"""W10 해외 종목 현재가 갱신 (stooq 무료 CSV). 실패한 종목은 이전 값 유지."""
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "public" / "data" / "w10.json"
URL = "https://stooq.com/q/l/?s={sym}.us&f=sd2t2ohlcv&h&e=csv"


def fetch(ticker):
    req = urllib.request.Request(URL.format(sym=ticker.lower()), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
    if not rows:
        return None
    row = rows[0]
    close, date = row.get("Close"), row.get("Date")
    if not close or close in ("N/D", "") or not date or date == "N/D":
        return None
    return float(close), date


def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    updated = 0
    dates = []
    for h in data.get("holdings", []):
        try:
            res = fetch(h["ticker"])
        except Exception as e:  # 네트워크 오류 등 → 이전 값 유지
            print(f"skip {h['ticker']}: {e}")
            continue
        if not res:
            print(f"skip {h['ticker']}: no data")
            continue
        price, date = res
        if date >= h.get("priceDate", ""):
            h["price"], h["priceDate"] = round(price, 2), date
            updated += 1
        dates.append(h["priceDate"])
    if dates:
        data["asof"] = max(dates)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"W10 quotes updated: {updated}/{len(data.get('holdings', []))}, asof {data.get('asof')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
