"""
Builds/refreshes data/symbol_master.csv — the reference list of NSE symbols this
dashboard tracks, with their series (EQ/BE/BZ) and company name.

Yahoo Finance has no "list all NSE stocks" endpoint, so this list has to come
from NSE itself. Unlike prices, the symbol list barely changes day to day (new
listings/delistings are occasional), so this script is meant to be run
manually/periodically (e.g. monthly) — NOT every night. The nightly price
refresh (scripts/fetch_yahoo_prices.py) reads this file and never talks to NSE.

Source: NSE's own end-of-day Bhavcopy (single file, all series in one shot):
https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip

Usage:
    python scripts/refresh_symbol_master.py                     # latest available day
    python scripts/refresh_symbol_master.py --date 2026-09-18   # specific day
"""

import argparse
import csv
import io
import sys
import zipfile
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")

BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# Main-board stock series only. SM/ST (NSE Emerge SME) are excluded because
# Yahoo Finance doesn't carry price data for them — see README.
DEFAULT_SERIES = {"EQ", "BE", "BZ"}

ROOT = Path(__file__).resolve().parent.parent
MASTER_PATH = ROOT / "data" / "symbol_master.csv"


def fetch_bhavcopy_csv(target_date: date) -> str:
    date_str = target_date.strftime("%Y%m%d")
    url = BHAVCOPY_URL.format(date_str=date_str)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 404:
        raise FileNotFoundError(f"No bhavcopy for {target_date}")
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        with zf.open(names[0]) as f:
            return f.read().decode("utf-8-sig")


def build_master(raw_csv: str, series_keep: set[str]) -> list[dict]:
    reader = csv.DictReader(io.StringIO(raw_csv))
    rows = []
    for row in reader:
        series = (row.get("SctySrs") or "").strip()
        if series not in series_keep:
            continue
        rows.append({
            "symbol": (row.get("TckrSymb") or "").strip(),
            "series": series,
            "isin": (row.get("ISIN") or "").strip(),
            "name": (row.get("FinInstrmNm") or "").strip(),
        })
    rows.sort(key=lambda r: (r["series"], r["symbol"]))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (default: most recent trading day)")
    parser.add_argument("--series", type=str, default=None, help="Comma-separated series (default: EQ,BE,BZ)")
    args = parser.parse_args()

    series_keep = (
        {s.strip().upper() for s in args.series.split(",")} if args.series else DEFAULT_SERIES
    )

    if args.date:
        candidates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    else:
        today = datetime.now(IST).date()
        candidates = [today - timedelta(days=i) for i in range(7)]

    raw_csv = None
    used_date = None
    for d in candidates:
        try:
            raw_csv = fetch_bhavcopy_csv(d)
            used_date = d
            break
        except FileNotFoundError:
            continue

    if raw_csv is None:
        print(f"Could not find a bhavcopy in the last {len(candidates)} day(s).")
        sys.exit(1)

    rows = build_master(raw_csv, series_keep)
    if not rows:
        print("WARNING: 0 symbols matched the series filter.")
        sys.exit(1)

    MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MASTER_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "series", "isin", "name"])
        writer.writeheader()
        writer.writerows(rows)

    by_series = {}
    for r in rows:
        by_series[r["series"]] = by_series.get(r["series"], 0) + 1
    print(f"Built symbol master from {used_date}: {len(rows)} symbols -> {MASTER_PATH}")
    print("Breakdown by series:", by_series)


if __name__ == "__main__":
    main()
