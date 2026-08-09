#!/usr/bin/env python3
"""Refresh 2025 annual financial highlights from OpenDART.

Requires DART_API_KEY. Corporate-code XML and statement responses are processed
in memory only; no raw provider response or credential is committed.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "companies.json"
CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FINANCIAL_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"


def api_url(endpoint: str, key: str, parameters: dict[str, str]) -> str:
    encoded_key = key if "%" in key else quote(key, safe="")
    return f"{endpoint}?crtfc_key={encoded_key}&{urlencode(parameters)}"


def request_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "compound-asset-2045/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def corp_code_map(key: str) -> dict[str, str]:
    payload = request_bytes(api_url(CORP_CODE_URL, key, {}))
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        xml_payload = archive.read("CORPCODE.xml")
    root = ElementTree.fromstring(xml_payload)
    mapping: dict[str, str] = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if len(stock_code) == 6 and corp_code:
            mapping[stock_code] = corp_code
    return mapping


def parse_amount(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text == "-":
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def statement_rows(key: str, corp_code: str) -> list[dict[str, Any]]:
    url = api_url(
        FINANCIAL_URL,
        key,
        {"corp_code": corp_code, "bsns_year": "2025", "reprt_code": "11011", "fs_div": "CFS"},
    )
    payload = json.loads(request_bytes(url).decode("utf-8"))
    if not isinstance(payload, dict) or str(payload.get("status")) != "000":
        return []
    rows = payload.get("list", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def find_row(rows: list[dict[str, Any]], ids: tuple[str, ...], names: tuple[str, ...]) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("account_id", "")) in ids:
            return row
    patterns = [re.compile(pattern) for pattern in names]
    for row in rows:
        account_name = str(row.get("account_nm", "")).replace(" ", "")
        if any(pattern.fullmatch(account_name) for pattern in patterns):
            return row
    return None


def derived_financials(rows: list[dict[str, Any]], existing: dict[str, Any]) -> dict[str, Any]:
    result = dict(existing)
    revenue_row = find_row(rows, ("ifrs-full_Revenue",), (r"매출액", r"영업수익", r"수익\(매출액\)"))
    operating_row = find_row(rows, ("dart_OperatingIncomeLoss",), (r"영업이익", r"영업이익\(손실\)"))
    profit_row = find_row(
        rows,
        ("ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs-full_ProfitLoss"),
        (r"지배기업소유주지분당기순이익", r"당기순이익"),
    )
    liabilities_row = find_row(rows, ("ifrs-full_Liabilities",), (r"부채총계",))
    equity_row = find_row(rows, ("ifrs-full_EquityAttributableToOwnersOfParent", "ifrs-full_Equity"), (r"자본총계",))
    eps_row = find_row(rows, ("ifrs-full_BasicEarningsLossPerShare",), (r"기본주당이익", r"기본주당손익"))

    revenue = parse_amount(revenue_row.get("thstrm_amount")) if revenue_row else None
    operating = parse_amount(operating_row.get("thstrm_amount")) if operating_row else None
    profit = parse_amount(profit_row.get("thstrm_amount")) if profit_row else None
    liabilities = parse_amount(liabilities_row.get("thstrm_amount")) if liabilities_row else None
    equity = parse_amount(equity_row.get("thstrm_amount")) if equity_row else None
    prior_equity = parse_amount(equity_row.get("frmtrm_amount")) if equity_row else None
    eps = parse_amount(eps_row.get("thstrm_amount")) if eps_row else None

    if revenue and revenue > 0:
        result["revenue2025"] = int(round(revenue / 100_000_000))
        if operating is not None:
            result["operatingMargin2025"] = round(operating / revenue * 100, 2)
    if liabilities is not None and equity and equity > 0:
        result["debtRatio2025"] = round(liabilities / equity * 100, 2)
    if profit is not None and equity and equity > 0:
        average_equity = (equity + prior_equity) / 2 if prior_equity and prior_equity > 0 else equity
        result["roe2025"] = round(profit / average_equity * 100, 2)
    if eps is not None and eps > 0:
        result["eps2025"] = int(round(eps))
    return result


def atomic_write(value: dict[str, Any]) -> None:
    temporary = DATA_PATH.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(DATA_PATH)


def main() -> None:
    key = os.environ.get("DART_API_KEY")
    if not key:
        print("OpenDART refresh skipped: DART_API_KEY is not set; snapshot preserved")
        return

    with DATA_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    companies = data.get("companies", [])
    try:
        mapping = corp_code_map(key)
    except (HTTPError, URLError, TimeoutError, zipfile.BadZipFile, ElementTree.ParseError, KeyError):
        print("OpenDART refresh failed before company lookup; snapshot preserved")
        return

    fetched = 0
    updated = 0
    for company in companies:
        code = str(company.get("code", ""))
        corp_code = mapping.get(code)
        if not corp_code:
            continue
        try:
            rows = statement_rows(key, corp_code)
            if not rows:
                continue
            fetched += 1
            refreshed = derived_financials(rows, company.get("financials", {}))
            if refreshed != company.get("financials", {}):
                company["financials"] = refreshed
                updated += 1
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
            continue
        time.sleep(0.08)

    if not fetched:
        print("OpenDART refresh returned no valid company statements; snapshot preserved")
        return
    data["financialDataStatus"] = "verified_from_opendart_2025_annual"
    atomic_write(data)
    print(f"OpenDART refresh verified {fetched}/{len(companies)} companies and changed {updated}")


if __name__ == "__main__":
    main()
