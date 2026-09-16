"""Archive the airport's complete legacy feed without decoding it."""
import hashlib
import re
import sys
from collection_common import (
    ROOT, RESEARCH_END, CollectionError, now, reject_empty_or_html,
    request_bytes, run_collector, save_snapshot,
)

SOURCE_URL = "https://www.taoyuan-airport.com/uploads/flightx/a_flight_v4.txt"


def validate(body, headers):
    reject_empty_or_html(body, headers)
    # Inspect ASCII delimiters/terminal/direction; no decoding or rewriting.
    rows = [row for row in body.splitlines() if row.strip()]
    if not rows or any(not re.match(rb"^[12],[AD],", row) or row.count(b",") < 13 for row in rows):
        raise CollectionError("unexpected a_flight_v4 record structure")


def collect(log):
    if now() >= RESEARCH_END and any((ROOT / "raw" / "taoyuan").glob("a_flight_20260922_*.txt")):
        print("SKIPPED: September 22 supplementary snapshot already exists")
        return False
    body, headers = request_bytes(SOURCE_URL, log)
    validate(body, headers)
    path, collected_at = save_snapshot("taoyuan", "a_flight", "txt", body)
    log.snapshots.append(path)
    log.emit("snapshot_saved", source_url=SOURCE_URL, http_status=200,
             collected_at=collected_at, path=path, response_size=len(body),
             sha256=hashlib.sha256(body).hexdigest())


if __name__ == "__main__":
    sys.exit(run_collector("taoyuan", collect))
