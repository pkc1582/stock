"""시세 기준일 신선도 점검. G20/D10 기준일이 3영업일 넘게 지나면 실패시킨다. W10은 경고만 출력한다."""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LATEST_PATH = ROOT / "public" / "data" / "latest.json"
W10_PATH = ROOT / "public" / "data" / "w10.json"
MAX_BUSINESS_DAYS_STALE = 3


def business_days_between(start, end):
    """start(기준일)와 end(오늘) 사이에 지난 영업일(평일) 수. 당일은 0."""
    if start >= end:
        return 0
    count = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return count


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def latest_price_basis_date(latest):
    dates = [parse_date(c.get("priceBasisDate")) for c in latest.get("companies", [])]
    dates = [d for d in dates if d]
    return max(dates) if dates else None


def main():
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()

    if not LATEST_PATH.exists():
        print(f"::error::{LATEST_PATH} not found")
        return 1

    latest = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    g20_date = latest_price_basis_date(latest)
    if not g20_date:
        print("::error::G20 priceBasisDate not found in latest.json")
        return 1

    stale_days = business_days_between(g20_date, today)
    if stale_days > MAX_BUSINESS_DAYS_STALE:
        print(f"::error::G20 price data is stale: priceBasisDate={g20_date} is {stale_days} business days old (limit {MAX_BUSINESS_DAYS_STALE})")
        return 1
    print(f"fresh: {g20_date} (G20, {stale_days} business days old)")

    if W10_PATH.exists():
        w10 = json.loads(W10_PATH.read_text(encoding="utf-8"))
        w10_date = parse_date(w10.get("asof"))
        if w10_date:
            w10_stale_days = business_days_between(w10_date, today)
            if w10_stale_days > MAX_BUSINESS_DAYS_STALE:
                print(f"::warning::W10 price data is stale: asof={w10_date} is {w10_stale_days} business days old (limit {MAX_BUSINESS_DAYS_STALE})")
            else:
                print(f"fresh: {w10_date} (W10, {w10_stale_days} business days old)")
        else:
            print("::warning::W10 asof date missing or unparsable")

    return 0


if __name__ == "__main__":
    sys.exit(main())
