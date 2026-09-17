"""Provision only this study's cron-job.org jobs; never log credentials."""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

REPOSITORY = "david26496/taoyuan-flight-delay-study"
BASE = "https://api.cron-job.org"
EXPIRY = 20260922020000


def jobs(token):
    result = []
    specs = [
        ("TDX hourly", "collect_tdx.yml", [17], [-1], list(range(17, 23))),
        ("Taoyuan daily", "collect_taoyuan.yml", [7], [0, 6, 12, 18], list(range(17, 22))),
        ("Taoyuan final", "collect_taoyuan.yml", [7], [0], [22]),
    ]
    for label, workflow, minutes, hours, days in specs:
        result.append({
            "title": f"TPE study 202609 [{REPOSITORY}] {label}",
            "url": f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{workflow}/dispatches",
            "enabled": True, "saveResponses": False, "requestMethod": 1,
            "requestTimeout": 30, "redirectSuccess": False,
            "schedule": {"timezone": "Asia/Taipei", "expiresAt": EXPIRY,
                         "hours": hours, "minutes": minutes, "mdays": days,
                         "months": [9], "wdays": [-1]},
            "extendedData": {
                "headers": {"Authorization": f"Bearer {token}",
                            "Accept": "application/vnd.github+json",
                            "Content-Type": "application/json",
                            "X-GitHub-Api-Version": "2026-03-10"},
                "body": json.dumps({"ref": "main", "inputs": {"trigger_source": "cron-job.org"}}),
            },
        })
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(method, path, key, payload=None):
    request = urllib.request.Request(BASE + path,
        data=None if payload is None else json.dumps(payload).encode(), method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    # Mutations are not blindly retried: a lost create response could create duplicates.
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"cron-job.org {method} {path}: HTTP {exc.code}; response omitted") from None
    except (OSError, ValueError, urllib.error.URLError):
        raise RuntimeError(f"cron-job.org {method} {path}: request failed; response omitted") from None


def select_existing(existing, desired):
    matches = [job for job in existing if job.get("title") == desired["title"]]
    if len(matches) > 1 or (matches and matches[0].get("url") != desired["url"]):
        raise RuntimeError("Conflicting study job: inspect cron-job.org before proceeding")
    return matches[0] if matches else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["plan", "provision", "status"])
    args = parser.parse_args()
    if args.mode == "plan":
        for job in jobs("PLACEHOLDER"):
            print(json.dumps({k: job[k] for k in ("title", "url", "schedule")}, ensure_ascii=False))
        return
    key = os.environ.get("CRON_JOB_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing CRON_JOB_API_KEY secret")
    response = api("GET", "/jobs", key)
    if response.get("someFailed") or not isinstance(response.get("jobs"), list):
        raise RuntimeError("Incomplete job inventory; no changes made")
    token = os.environ.get("SCHEDULER_GITHUB_TOKEN", "").strip()
    if args.mode == "provision":
        if not token:
            raise RuntimeError("Missing SCHEDULER_GITHUB_TOKEN secret")
        if datetime.now(ZoneInfo("Asia/Taipei")) >= datetime(2026, 9, 22, 2, tzinfo=ZoneInfo("Asia/Taipei")):
            raise RuntimeError("Study has ended; refusing to enable jobs")
    planned = jobs(token)
    matched = [(job, select_existing(response["jobs"], job)) for job in planned]
    for desired, existing in matched:
        if args.mode == "provision":
            if existing:
                job_id = existing["jobId"]
                api("PATCH", f"/jobs/{job_id}", key, {"job": desired})
            else:
                job_id = api("PUT", "/jobs", key, {"job": desired})["jobId"]
            time.sleep(1.1)  # documented create limit: one per second
            actual = api("GET", f"/jobs/{job_id}", key)["jobDetails"]
            # Compare in memory; never print returned headers or request bodies.
            for field in ("url", "enabled", "schedule", "requestMethod", "extendedData", "saveResponses"):
                if actual.get(field) != desired[field]:
                    raise RuntimeError(f"Readback mismatch for job {job_id}, field {field}")
            print(json.dumps({"job_id": job_id, "title": desired["title"], "verified": True,
                              "next_execution": actual.get("nextExecution")}, ensure_ascii=False))
        elif existing:
            job_id = existing["jobId"]
            history = api("GET", f"/jobs/{job_id}/history", key)
            print(json.dumps({"job_id": job_id, "title": desired["title"],
                "enabled": existing.get("enabled"), "next_execution": existing.get("nextExecution"),
                "history": [{k: h.get(k) for k in ("date", "datePlanned", "jitter", "httpStatus", "status")}
                            for h in history.get("history", [])[:5]]}, ensure_ascii=False))
        else:
            print(json.dumps({"title": desired["title"], "status": "not configured"}))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, TypeError) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "Unexpected API schema; response omitted", file=sys.stderr)
        sys.exit(1)
