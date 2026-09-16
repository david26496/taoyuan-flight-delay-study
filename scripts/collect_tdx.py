"""Archive all TPE arrival pages exactly as served; never persist credentials."""
import hashlib
import json
import os
import sys
from urllib.parse import urlencode
from collection_common import (
    CollectionError, reject_empty_or_html, request_bytes, run_collector, save_snapshot,
)

TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
ENDPOINT = "https://tdx.transportdata.tw/api/basic/v2/Air/FIDS/Airport/Arrival/TPE"
PAGE_SIZE = 1000
MAX_PAGES = 100


def access_token(log):
    credentials = {name: os.getenv(name, "").strip() for name in ("TDX_CLIENT_ID", "TDX_CLIENT_SECRET")}
    if not all(credentials.values()):
        raise CollectionError("missing TDX_CLIENT_ID or TDX_CLIENT_SECRET environment variable")
    form = urlencode({"grant_type": "client_credentials",
                      "client_id": credentials["TDX_CLIENT_ID"],
                      "client_secret": credentials["TDX_CLIENT_SECRET"]}).encode("ascii")
    body, _ = request_bytes(TOKEN_URL, log, data=form,
                            headers={"Content-Type": "application/x-www-form-urlencoded"}, sensitive=True)
    try:
        obj = json.loads(body)
        token = obj.get("access_token")
        if not isinstance(token, str) or not token or obj.get("token_type", "").lower() != "bearer":
            raise ValueError
    except (ValueError, AttributeError, UnicodeError):
        raise CollectionError("invalid token response (body intentionally omitted)") from None
    return token


def validate(body, headers):
    reject_empty_or_html(body, headers)
    try:
        rows = json.loads(body)
    except (ValueError, UnicodeError):
        raise CollectionError("invalid JSON response") from None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise CollectionError("expected FIDSArrival JSON array")
    # Validate identity, not optional timestamps: missing/null times stay untouched.
    required = {"FlightDate", "FlightNumber", "AirlineID", "DepartureAirportID", "ArrivalAirportID", "UpdateTime"}
    if any(not required.issubset(row) or row["ArrivalAirportID"] != "TPE" for row in rows):
        raise CollectionError("unexpected TPE FIDSArrival schema")
    return rows


def collect(log):
    token = access_token(log)  # held in memory and reused for every page/retry
    skip, seen = 0, set()
    for page in range(1, MAX_PAGES + 1):
        # No $select, flight-date filter, cargo filter, cleaning or codeshare removal.
        url = ENDPOINT + "?" + urlencode({"$format": "JSON", "$top": PAGE_SIZE, "$skip": skip})
        body, headers = request_bytes(url, log, headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
        rows = validate(body, headers)
        digest = hashlib.sha256(body).hexdigest()
        if rows and digest in seen:
            raise CollectionError("repeated page: API may be ignoring $skip")
        seen.add(digest)
        path, collected_at = save_snapshot("tdx", "arrival_TPE", "json", body)
        log.snapshots.append(path)
        log.emit("snapshot_saved", source_url=url, http_status=200,
                 collected_at=collected_at, path=path, response_size=len(body), sha256=digest,
                 page=page, skip=skip, row_count=len(rows))
        if not rows:
            return  # keep the original [] page as evidence of pagination termination
        # Continue even after a short page in case the server imposes a lower cap.
        skip += len(rows)
    raise CollectionError("pagination limit reached; collection is incomplete")


if __name__ == "__main__":
    sys.exit(run_collector("tdx", collect))
