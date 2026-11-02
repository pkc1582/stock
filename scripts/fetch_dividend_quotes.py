#!/usr/bin/env python3
"""Refresh closing prices for the D10 dividend-growth track.

Primary source: Yahoo Finance (see fetch_market.py's fetch_yahoo_snapshot).
Each D10 code keeps its own basis date — a code that fails this run simply
keeps its previously verified price; a complete, same-date snapshot across
all 10 codes is no longer required to write a partial update.

Fallback source (only when Yahoo returns zero usable quotes): the data.go.kr
helpers in fetch_market.py, using the same secrets (STOCK_API_SERVICE_KEY or
DATA_GO_KR_API_KEY). That path keeps the original all-codes-same-date rule.

Codes come from ``data/dividend-track.json``. The snapshot is written to
``data/dividend-quotes.json`` with a per-code basis date.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_market as fm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TRACK_PATH = ROOT / "data" / "dividend-track.json"
QUOTES_PATH = ROOT / "data" / "dividend-quotes.json"


def load_codes() -> list[str]:
    with TRACK_PATH.open("r", encoding="utf-8") as handle:
        track = json.load(handle)
    codes = [str(item["code"]) for item in track.get("companies", [])]
    if not codes or len(set(codes)) != len(codes):
        raise fm.MarketRefreshError("data/dividend-track.json has an invalid code list")
    for code in codes:
        if fm.normalized_code(code) != code:
            raise fm.MarketRefreshError(f"invalid stock code in dividend track: {code}")
    return codes


def load_existing_quotes() -> dict[str, dict[str, Any]]:
    if not QUOTES_PATH.exists():
        return {}
    with QUOTES_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    quotes = value.get("quotes")
    if not isinstance(quotes, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for code, entry in quotes.items():
        if isinstance(entry, dict) and "price" in entry and "basisDate" in entry:
            result[str(code)] = {"price": entry["price"], "basisDate": entry["basisDate"]}
        elif isinstance(entry, (int, float)) and not isinstance(entry, bool):
            # Pre-migration shape: a flat price shared a single top-level basisDate.
            result[str(code)] = {"price": entry, "basisDate": value.get("basisDate", "")}
    return result


def write_snapshot(quotes: dict[str, dict[str, Any]], source: str) -> None:
    snapshot = {
        "schemaVersion": 2,
        "source": source,
        "quotes": quotes,
    }
    tmp = QUOTES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(QUOTES_PATH)


def refresh_via_yahoo(codes: list[str]) -> dict[str, tuple[int, str]]:
    deadline = time.monotonic() + fm.YAHOO_TOTAL_HTTP_BUDGET_SECONDS
    return fm.fetch_yahoo_snapshot(codes, deadline)


def refresh_via_data_go_kr(endpoint: str, key: str, codes: list[str]) -> dict[str, Any] | None:
    """All-or-nothing data.go.kr fallback: a complete same-date snapshot across
    every D10 code, or nothing (kept from the original implementation)."""

    today = fm.current_kst_date()
    deadline = time.monotonic() + fm.TOTAL_HTTP_BUDGET_SECONDS
    budget = fm.ApiCallBudget(fm.LOOKBACK_DAYS * len(codes) * fm.MAX_HTTP_ATTEMPTS_PER_REQUEST)
    for offset in range(1, fm.LOOKBACK_DAYS + 1):
        candidate = today - timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        iso = candidate.isoformat()
        quotes = fm.fetch_snapshot_quotes(
            endpoint, key, candidate.strftime("%Y%m%d"), codes, deadline, budget
        )
        if set(quotes) == set(codes):
            return {code: {"price": quotes[code], "basisDate": iso} for code in codes}
    raise fm.MarketRefreshError("no complete D10 quote snapshot in the lookback window")


def main() -> int:
    try:
        codes = load_codes()
    except (OSError, json.JSONDecodeError, fm.MarketRefreshError) as error:
        print(f"dividend quote refresh failed; previous snapshot preserved: {error}", file=sys.stderr)
        return 1

    existing = load_existing_quotes()

    try:
        yahoo_quotes = refresh_via_yahoo(codes)
    except fm.MarketRefreshError as error:
        print(f"Yahoo Finance refresh failed; falling back to data.go.kr: {error}")
        yahoo_quotes = {}

    if yahoo_quotes:
        merged = dict(existing)
        updated = 0
        for code, (price, basis_date) in yahoo_quotes.items():
            previous = merged.get(code, {})
            previous_date = fm.normalized_date(previous.get("basisDate")) or ""
            if basis_date < previous_date:
                continue
            if previous.get("price") != price or previous.get("basisDate") != basis_date:
                updated += 1
            merged[code] = {"price": price, "basisDate": basis_date}
        if updated:
            write_snapshot(merged, fm.YAHOO_SOURCE_LABEL)
        print(f"dividend quotes via Yahoo Finance updated {updated}/{len(codes)} codes"
              + ("" if updated else "; no file changes"))
        return 0

    print("Yahoo Finance returned no usable D10 quotes; falling back to data.go.kr")

    key = os.environ.get("STOCK_API_SERVICE_KEY") or os.environ.get("DATA_GO_KR_API_KEY")
    if not key:
        print("dividend quote refresh skipped: no fallback API key; previous snapshot preserved")
        return 0
    endpoint = os.environ.get("STOCK_API_ENDPOINT", fm.DEFAULT_ENDPOINT).strip() or fm.DEFAULT_ENDPOINT
    try:
        snapshot = refresh_via_data_go_kr(endpoint, key, codes)
        write_snapshot(snapshot, fm.SOURCE_LABEL)
        basis = next(iter(snapshot.values()))["basisDate"]
        print(f"dividend quotes (data.go.kr fallback) updated {len(snapshot)} codes for {basis}")
        return 0
    except (fm.MarketRefreshError, OSError, json.JSONDecodeError) as error:
        print(f"dividend quote refresh failed; previous snapshot preserved: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
