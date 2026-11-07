"""One-request, read-only LunarCrush free-tier probe. Never prints credentials or headers."""
import json
import os
import sys

import requests

API_URL = "https://lunarcrush.com/api4/public/coins/list/v1"
KEY = os.getenv("SOCIAL_API_KEY", "").strip()
TARGETS = {"BTC", "ETH", "LINK", "BNB"}

def main():
    if not KEY:
        print("RESULT=BLOCKED reason=SOCIAL_API_KEY secret ausente")
        return 0

    try:
        response = requests.get(
            API_URL,
            headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json"},
            timeout=20,
        )
    except requests.RequestException as exc:
        print(f"RESULT=NETWORK_ERROR type={type(exc).__name__}")
        return 0

    print(f"HTTP_STATUS={response.status_code}")
    if response.status_code in (401, 403):
        print("RESULT=AUTH_OR_PLAN_RESTRICTED; nenhum corpo ou credencial foi registrado")
        return 0
    if response.status_code == 429:
        print("RESULT=RATE_LIMITED")
        return 0
    if response.status_code != 200:
        print("RESULT=HTTP_ERROR; resposta omitida")
        return 0

    try:
        payload = response.json()
    except ValueError:
        print("RESULT=INVALID_JSON")
        return 0

    print("TOP_LEVEL_KEYS=" + ",".join(sorted(payload.keys())[:40]) if isinstance(payload, dict) else "TOP_LEVEL_TYPE=" + type(payload).__name__)
    rows = None
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("data", "coins", "result"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
            if isinstance(candidate, dict):
                for nested in ("data", "coins", "results"):
                    if isinstance(candidate.get(nested), list):
                        rows = candidate[nested]
                        break
                if rows is not None:
                    break

    if not rows:
        print("RESULT=NO_LIST_DATA")
        return 0

    print(f"ROW_COUNT={len(rows)}")
    matched = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol", "")).upper()
        if symbol not in TARGETS:
            continue
        matched += 1
        keys = sorted(row.keys())
        print(f"ASSET={symbol} FIELDS={','.join(keys[:80])}")
        for field in ("social_volume_24h", "sentiment", "social_contributors", "spam", "generated"):
            if field in row:
                value = row[field]
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)[:300]
                print(f"METRIC_{symbol}_{field}={value}")

    print(f"MATCHED_TARGET_ROWS={matched}")
    if matched == 0:
        print("RESULT=AUTHENTICATED_ENDPOINT_BUT_TARGETS_NOT_FOUND")
    else:
        print("RESULT=ENDPOINT_ACCESS_CONFIRMED; plan eligibility of each social field must be inferred from actual returned fields, not docs alone")
    return 0

if __name__ == "__main__":
    sys.exit(main())
