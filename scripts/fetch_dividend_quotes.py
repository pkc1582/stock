#!/usr/bin/env python3
"""Refresh closing prices for the D10 dividend-growth track.

Reuses the data.go.kr helpers from ``fetch_market.py`` and the same secrets
(STOCK_API_SERVICE_KEY or DATA_GO_KR_API_KEY). Codes come from
``data/dividend-track.json``. A snapshot is written to
``data/dividend-quotes.json`` only when every D10 code has a quote on the
same basis date; otherwise the previous snapshot is preserved.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
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


def existing_basis_date() -> str:
    if not QUOTES_PATH.exists():
        return "0000-00-00"
    with QUOTES_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return fm.normalized_date(value.get("basisDate")) or "0000-00-00"


def refresh(endpoint: str, key: str, today: date) -> tuple[dict[str, Any] | None, str]:
    codes = load_codes()
    previous = existing_basis_date()
    deadline = time.monotonic() + fm.TOTAL_HTTP_BUDGET_SECONDS
    budget = fm.ApiCallBudget(fm.LOOKBACK_DAYS * len(codes) * fm.MAX_HTTP_ATTEMPTS_PER_REQUEST)
    for offset in range(1, fm.LOOKBACK_DAYS + 1):
        candidate = today - timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        iso = candidate.isoformat()
        if iso < previous:
            break
        if iso == previous:
            return None, iso
        quotes = fm.fetch_snapshot_quotes(
            endpoint, key, candidate.strftime("%Y%m%d"), codes, deadline, budget
        )
        if set(quotes) == set(codes):
            return {
                "schemaVersion": 1,
                "basisDate": iso,
                "source": fm.SOURCE_LABEL,
                "quotes": {code: quotes[code] for code in codes},
            }, iso
    raise fm.MarketRefreshError("no complete D10 quote snapshot in the lookback window")


def main() -> int:
    key = os.environ.get("STOCK_API_SERVICE_KEY") or os.environ.get("DATA_GO_KR_API_KEY")
    if not key:
        print("dividend quote refresh skipped: no API key; previous snapshot preserved")
        return 0
    endpoint = os.environ.get("STOCK_API_ENDPOINT", fm.DEFAULT_ENDPOINT).strip() or fm.DEFAULT_ENDPOINT
    try:
        snapshot, basis = refresh(endpoint, key, fm.current_kst_date())
        if snapshot is None:
            print(f"dividend quotes already current for {basis}")
            return 0
        tmp = QUOTES_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(QUOTES_PATH)
        print(f"dividend quotes updated {len(snapshot['quotes'])} codes for {basis}")
        return 0
    except (fm.MarketRefreshError, OSError, json.JSONDecodeError) as error:
        print(f"dividend quote refresh failed; previous snapshot preserved: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
