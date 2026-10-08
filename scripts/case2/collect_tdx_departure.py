"""Archive verified TPE Departure pages; no Arrival requests or transformations."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collection_common import (CollectionError, ROOT, START, END, atomic_new,
                               in_window, now, reject_empty_or_html, request_bytes)
from collect_tdx import access_token  # Authentication only; never calls Arrival collect().

ENDPOINT = 'https://tdx.transportdata.tw/api/basic/v2/Air/FIDS/Airport/Departure/TPE'
PAGE_SIZE = 1000
MAX_PAGES = 100
MAX_RUN_BYTES = 64 * 1024 * 1024
DEPARTURE_FIELDS = {'ScheduleDepartureTime', 'ActualDepartureTime',
                    'EstimatedDepartureTime', 'DepartureRemark', 'DepartureRemarkEn'}


class RunLog:
    def __init__(self, root):
        self.root = Path(root)
        self.id = uuid.uuid4().hex
        self.pages = 0
        self.total_rows = 0
        self.snapshots = []
        self.last_http = {}

    def emit(self, event, **fields):
        if event == 'http_attempt':
            self.last_http = {k: fields.get(k) for k in ('http_status', 'content_type')}
            fields['endpoint_identifier'] = fields.get('source_url', ENDPOINT)
            fields['success'] = fields.get('http_status') == 200 and not fields.get('error')
            fields['error_message'] = fields.get('error')
        record = dict(collection_timestamp=now().isoformat(), timezone='Asia/Taipei',
                      source='TDX', direction='DEPARTURE', airport='TPE', endpoint_identifier=ENDPOINT,
                      run_id=self.id, event=event, http_status=None, content_type=None,
                      raw_file_path=None, file_size=None, sha256=None,
                      parsed_record_count=None, success=None, error_message=None,
                      pages=self.pages, total_rows=self.total_rows,
                      pagination_termination='pending',
                      github_run_id=os.getenv('GITHUB_RUN_ID'),
                      github_run_attempt=os.getenv('GITHUB_RUN_ATTEMPT'),
                      github_sha=os.getenv('GITHUB_SHA'),
                      trigger_source=os.getenv('COLLECTION_TRIGGER', 'local'))
        record.update(fields)
        path = self.root / 'logs/case2/tdx' / f'{now():%Y%m%d_%H%M%S_%f}_{self.id}_{uuid.uuid4().hex}.json'
        atomic_new(path, (json.dumps(record, sort_keys=True) + '\n').encode())
        print(json.dumps(record, sort_keys=True), flush=True)


def validate(body, headers):
    reject_empty_or_html(body, headers)
    try:
        rows = json.loads(body)
    except (ValueError, UnicodeError):
        raise CollectionError('invalid JSON response') from None
    required = {'FlightDate', 'FlightNumber', 'AirlineID', 'DepartureAirportID',
                'ArrivalAirportID', 'UpdateTime'}
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise CollectionError('unexpected Departure page shape or row count')
    if any(not isinstance(row, dict) or not required.issubset(row)
           or row['DepartureAirportID'] != 'TPE' for row in rows):
        raise CollectionError('unexpected TPE FIDSDeparture schema')
    # Official Departure time/status fields are optional/nullable. Do not reject
    # individual missing values, but an entire page lacking direction-specific
    # fields is not sufficient evidence of the expected Departure response.
    if rows and not any(DEPARTURE_FIELDS.intersection(row) for row in rows):
        raise CollectionError('page lacks all Departure time/status fields')
    return rows


def collect(log):
    token = access_token(log)
    skip, size, seen = 0, 0, set()
    pending = []  # Validate complete pagination before publishing any raw page.
    for page in range(1, MAX_PAGES + 1):
        url = ENDPOINT + '?' + urlencode({'$format': 'JSON', '$top': PAGE_SIZE, '$skip': skip})
        body, headers = request_bytes(url, log, headers={
            'Accept': 'application/json', 'Authorization': f'Bearer {token}'})
        at = now()
        log.pages = page
        rows = validate(body, headers)
        digest = hashlib.sha256(body).hexdigest()
        # Detect repeated pages even when the server changes whitespace/key order.
        fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if rows and fingerprint in seen:
            raise CollectionError('repeated page: API may be ignoring $skip')
        seen.add(fingerprint)
        size += len(body)
        if size > MAX_RUN_BYTES:
            raise CollectionError('response budget exceeded; incomplete collection')
        pending.append((page, skip, body, headers, at, len(rows), digest, url))
        log.total_rows += len(rows)
        if not rows:
            if not log.total_rows:
                raise CollectionError('empty first page; no flight observations')
            break
        skip += len(rows)  # A short page is not termination evidence.
    else:
        raise CollectionError('pagination limit reached; incomplete collection')

    for page, skip, body, headers, at, count, digest, url in pending:
        relative = Path('raw/case2/tdx/departure') / f'tdx_departure_{at:%Y%m%d_%H%M%S_%f}_{log.id}_p{page:03d}.json'
        path = log.root / relative
        atomic_new(path, body)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise CollectionError('persisted SHA256 verification failed')
        log.snapshots.append(relative.as_posix())
        log.emit('snapshot_saved', collection_timestamp=at.isoformat(),
                 endpoint_identifier=url, http_status=200,
                 content_type=headers.get('content-type'), raw_file_path=relative.as_posix(),
                 file_size=len(body), sha256=digest, parsed_record_count=count,
                 success=True, page=page, skip=skip, pagination_termination='empty_page',
                 field_presence={key: sum(key in row for row in json.loads(body))
                                 for key in sorted(DEPARTURE_FIELDS | {'AcType', 'IsCargo'})})
    log.emit('collection_complete', success=True, pagination_termination='empty_page',
             snapshots=log.snapshots, parsed_record_count=log.total_rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, default=ROOT,
                        help='Staging root for raw/case2 and logs/case2; default repository root')
    args = parser.parse_args(argv)
    log = RunLog(args.output_root.resolve())
    if not in_window(now()):
        log.emit('collection_skipped', success=False, error_message='outside inherited collection window',
                 window_start=START.isoformat(), window_end=END.isoformat(), pagination_termination='not_started')
        return 1
    try:
        collect(log)
        return 0
    except (CollectionError, OSError, ValueError) as exc:
        log.emit('collection_failed', success=False,
                 error_message=str(exc) if isinstance(exc, CollectionError) else type(exc).__name__,
                 pagination_termination='incomplete', snapshots=log.snapshots, **log.last_http)
        return 1


if __name__ == '__main__':
    sys.exit(main())
