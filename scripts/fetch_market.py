#!/usr/bin/env python3
"""Refresh Korean closing prices from the free data.go.kr stock-price API.

Required secret (either name is accepted):
  STOCK_API_SERVICE_KEY or DATA_GO_KR_API_KEY

Optional:
  STOCK_API_ENDPOINT

The script deliberately exits successfully and preserves the verified snapshot
when credentials are absent, the provider is unavailable, or no valid rows are
returned. Raw responses and credentials are never persisted.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "companies.json"
DEFAULT_ENDPOINT = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
SOURCE_LABEL = "공공데이터포털 금융위원회 주식시세정보"


def load_data() -> dict[str, Any]:
    with DATA_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or not isinstance(value.get("companies"), list):
        raise ValueError("data/companies.json has an invalid shape")
    return value


def atomic_write(value: dict[str, Any]) -> None:
    temporary = DATA_PATH.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(DATA_PATH)


def service_url(endpoint: str, service_key: str, parameters: dict[str, str]) -> str:
    # data.go.kr commonly distributes an already percent-encoded key. Preserve
    # percent escapes in that case; otherwise encode the raw key exactly once.
    encoded_key = service_key if "%" in service_key else quote(service_key, safe="")
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}serviceKey={encoded_key}&{urlencode(parameters)}"


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "compound-asset-2045/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        payload = response.read()
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("provider response was not an object")
    return decoded


def response_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("response")
    if not isinstance(response, dict):
        return []
    header = response.get("header")
    if isinstance(header, dict) and str(header.get("resultCode", "00")) not in {"00", "0"}:
        return []
    body = response.get("body")
    if not isinstance(body, dict):
        return []
    items_container = body.get("items")
    if not isinstance(items_container, dict):
        return []
    items = items_container.get("item", [])
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def normalized_date(value: Any) -> str | None:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def normalized_price(value: Any) -> int | None:
    try:
        number = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def fetch_latest_for_code(endpoint: str, key: str, code: str, begin: str, end: str) -> tuple[int, str] | None:
    parameters = {
        "resultType": "json",
        "pageNo": "1",
        "numOfRows": "100",
        "beginBasDt": begin,
        "endBasDt": end,
        "likeSrtnCd": code,
    }
    url = service_url(endpoint, key, parameters)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            candidates = []
            for item in response_items(fetch_json(url)):
                returned_code = str(item.get("srtnCd", "")).strip()
                if returned_code and not returned_code.endswith(code):
                    continue
                date = normalized_date(item.get("basDt"))
                price = normalized_price(item.get("clpr"))
                if date and price:
                    candidates.append((date, price))
            if not candidates:
                return None
            date, price = max(candidates, key=lambda pair: pair[0])
            return price, date
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(1)
    if last_error:
        return None
    return None


def main() -> None:
    service_key = os.environ.get("STOCK_API_SERVICE_KEY") or os.environ.get("DATA_GO_KR_API_KEY")
    if not service_key:
        print("market refresh skipped: no API key; verified snapshot preserved")
        return

    endpoint = os.environ.get("STOCK_API_ENDPOINT", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    data = load_data()
    companies = data["companies"]
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst).date()
    begin = (today - timedelta(days=14)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    updates: dict[str, tuple[int, str]] = {}
    for company in companies:
        code = str(company.get("code", ""))
        if len(code) != 6 or not code.isdigit():
            continue
        result = fetch_latest_for_code(endpoint, service_key, code, begin, end)
        if result:
            updates[code] = result
        time.sleep(0.05)

    if not updates:
        print("market refresh returned no valid rows; verified snapshot preserved")
        return

    for company in companies:
        code = str(company.get("code", ""))
        if code not in updates:
            continue
        price, date = updates[code]
        company["currentPrice"] = price
        company["priceBasisDate"] = date
        company["priceSource"] = SOURCE_LABEL

    dates = [date for _, date in updates.values()]
    data["priceBasisDate"] = max(dates)
    data["marketDataStatus"] = "updated_from_data_go_kr" if len(updates) == len(companies) else "partially_updated_from_data_go_kr"
    atomic_write(data)
    print(f"market refresh updated {len(updates)}/{len(companies)} companies; snapshot preserved for the remainder")


if __name__ == "__main__":
    main()
