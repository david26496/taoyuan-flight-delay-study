"""Byte-preserving HTTP, bounded retries, immutable files and Taipei windows."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 9, 17, tzinfo=TZ)
RESEARCH_END = datetime(2026, 9, 22, tzinfo=TZ)  # exclusive
END = datetime(2026, 9, 22, 2, tzinfo=TZ)  # inclusive
TIMEOUT = 30
MAX_ATTEMPTS = 4
RETRY_STATUSES = {408, 416, 423, 429, 500, 502, 503, 504}


def now():
    return datetime.now(TZ)


def in_window(at):
    if at.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return START <= at.astimezone(TZ) <= END


class CollectionError(Exception):
    pass


class WindowClosed(CollectionError):
    pass


def check_window():
    if not in_window(now()):
        raise WindowClosed("outside collection window")


def atomic_new(path, body):
    """Publish a complete file with an atomic, no-clobber hard link."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # fails if the destination already exists
    finally:
        os.unlink(temporary)


def save_snapshot(source, prefix, extension, body):
    # Wait for an actual new second rather than inventing a filename timestamp.
    for _ in range(4):
        at = now()
        check_window()
        path = ROOT / "raw" / source / f"{prefix}_{at:%Y%m%d_%H%M%S}.{extension}"
        try:
            atomic_new(path, body)
            return path.relative_to(ROOT).as_posix(), at.isoformat()
        except FileExistsError:
            time.sleep(1.05)
    raise CollectionError("snapshot filename collision after bounded wait")


class RunLog:
    def __init__(self, source):
        self.source = source
        self.id = uuid.uuid4().hex
        self.snapshots = []

    def emit(self, event, **fields):
        record = {"event": event, "collected_at": now().isoformat(),
                  "source": self.source, "run_id": self.id,
                  "github_run_id": os.getenv("GITHUB_RUN_ID"),
                  "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"), **fields}
        text = json.dumps(record, ensure_ascii=False, sort_keys=True)
        print(text, flush=True)
        path = ROOT / "logs" / self.source / f"{now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex}.json"
        atomic_new(path, (text + "\n").encode("utf-8"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # Do not forward authentication headers or token POST bodies to another URL.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect())


def request_bytes(url, log, *, data=None, headers=None, sensitive=False):
    request_headers = {"User-Agent": "tpe-arrival-research/1.0",
                       "Accept-Encoding": "identity", **(headers or {})}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        check_window()
        started = now().isoformat()
        status, body, response_headers, error = None, None, {}, None
        transport_error = False
        try:
            request = urllib.request.Request(url, data=data, headers=request_headers)
            with OPENER.open(request, timeout=TIMEOUT) as response:
                status = response.status
                response_headers = dict(response.headers.items())
                body = response.read()  # urllib does not decode Content-Encoding
        except urllib.error.HTTPError as exc:
            status = exc.code
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            with exc:
                # Never log or persist the token endpoint's response body.
                try:
                    body = exc.read()
                except (OSError, http.client.HTTPException):
                    body = None
            error = f"HTTP {status}"
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            # Exception text can contain request details. Only log its class.
            error = type(exc).__name__
            transport_error = True
        normalized = {k.lower(): v for k, v in response_headers.items()}
        if status == 200 and error is None:
            length = normalized.get("content-length")
            if length is not None and (not length.isdigit() or int(length) != len(body)):
                error = "content-length mismatch"
        log.emit("http_attempt", source_url=url, request_started_at=started,
                 http_status=status, attempt=attempt,
                 response_size=len(body) if body is not None else None,
                 sha256=hashlib.sha256(body).hexdigest() if body is not None and not sensitive else None,
                 content_type=normalized.get("content-type"),
                 content_encoding=normalized.get("content-encoding"),
                 last_modified=normalized.get("last-modified"),
                 error=error)
        if status == 200 and error is None:
            if normalized.get("content-encoding", "identity").lower() not in ("", "identity"):
                raise CollectionError("unexpected compression despite Accept-Encoding: identity")
            return body, normalized
        retryable = transport_error or status in RETRY_STATUSES or error == "content-length mismatch"
        if not retryable or attempt == MAX_ATTEMPTS:
            raise CollectionError(f"HTTP request failed: status={status}, error={error}")
        delay = 2 ** attempt  # 2, 4, 8 seconds; no unbounded retry
        retry_after = normalized.get("retry-after", "")
        if retry_after.isdigit():
            delay = min(60, max(delay, int(retry_after)))
        time.sleep(delay)
    raise AssertionError("unreachable")


def reject_empty_or_html(body, headers):
    if not body or not body.strip():
        raise CollectionError("empty HTTP response")
    media = headers.get("content-type", "").lower()
    # Ignore BOM/NULs only in this inspection copy. Preserve original bytes.
    probe = body[:8192].replace(b"\x00", b"").lower()
    if "html" in media or re.search(rb"<!doctype\s+html|<html\b|<head\b|<body\b", probe):
        raise CollectionError("HTML page rejected")


def run_collector(source, collect):
    if not in_window(now()):
        print("SKIPPED: outside 2026-09-17 00:00 through 2026-09-22 02:00 Asia/Taipei")
        return 0  # no credentials, HTTP, log directories or raw files
    log = RunLog(source)
    try:
        result = collect(log)
        if result is not False:
            log.emit("collection_complete", snapshots=log.snapshots)
        return 0
    except WindowClosed:
        log.emit("window_closed", snapshots=log.snapshots)
        return 1 if log.snapshots else 0
    except (CollectionError, OSError, ValueError) as exc:
        # CollectionError messages are controlled by us, never a token body.
        message = str(exc) if isinstance(exc, CollectionError) else type(exc).__name__
        log.emit("collection_failed", error=message, snapshots=log.snapshots)
        return 1
