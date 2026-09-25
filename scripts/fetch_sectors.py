"""
Sector classification per symbol, straight from NSE.

NSE publishes its index constituent lists as plain CSVs with an "Industry"
column — the official classification, free, no auth and no rate limiting.
That is a much better source than Yahoo's .info endpoint, which needs one
request per symbol and rate-limits aggressively.

NIFTY Total Market (750 names) is the widest list NSE publishes and already
contains Nifty 500 + Microcap 250. The others are fetched anyway so that a
change to NSE's index structure can only add coverage, never lose it.

Everything outside those lists — small BE-series names in no index — has no
published sector and shows as "Unknown" in the dashboard. That is ~2,100 of
~2,900 symbols by count, but a small fraction by market value.

Sectors move only when NSE reshuffles an index, so this runs monthly
alongside refresh_symbol_master.py, not nightly.

Usage:
    python scripts/fetch_sectors.py
"""

import csv
import io
import sys
from pathlib import Path

import requests

from db import bulk_upsert, get_engine

ROOT = Path(__file__).resolve().parent.parent
MASTER_PATH = ROOT / "data" / "symbol_master.csv"

BASE_URL = "https://nsearchives.nseindia.com/content/indices/"
INDEX_LISTS = [
    "ind_niftytotalmarket_list.csv",   # widest: 750 names, includes the rest
    "ind_nifty500list.csv",
    "ind_niftymidcap150list.csv",
    "ind_niftysmallcap250list.csv",
    "ind_niftymicrocap250_list.csv",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "en-US,en;q=0.9",
}

# If NSE changes its files and we end up with almost nothing, fail loudly
# rather than wiping good sector data with an empty write.
MIN_EXPECTED_SYMBOLS = 400


def fetch_list(name: str) -> dict:
    url = BASE_URL + name
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"  {name}: HTTP {resp.status_code}, skipping")
        return {}
    out = {}
    for row in csv.DictReader(io.StringIO(resp.text)):
        symbol = (row.get("Symbol") or "").strip()
        sector = (row.get("Industry") or "").strip()
        if symbol and sector:
            out[symbol] = sector
    print(f"  {name}: {len(out)} symbols")
    return out


def main() -> int:
    print("Fetching NSE index constituent lists...")
    sectors: dict[str, str] = {}
    for name in INDEX_LISTS:
        # The widest list is fetched first; setdefault keeps its label if a
        # narrower list disagrees.
        for symbol, sector in fetch_list(name).items():
            sectors.setdefault(symbol, sector)

    print(f"\n{len(sectors)} symbols with a sector, "
          f"{len(set(sectors.values()))} distinct sectors.")

    if len(sectors) < MIN_EXPECTED_SYMBOLS:
        print(f"ABORTING: only {len(sectors)} symbols found, expected at least "
              f"{MIN_EXPECTED_SYMBOLS}. NSE's files may have moved or changed "
              f"format. Not overwriting existing sector data.")
        return 1

    # Only keep symbols we actually track, so the table stays aligned with the
    # dashboard's universe.
    if MASTER_PATH.exists():
        with MASTER_PATH.open(encoding="utf-8") as f:
            universe = {r["symbol"] for r in csv.DictReader(f)}
        before = len(sectors)
        sectors = {s: v for s, v in sectors.items() if s in universe}
        print(f"Matched {len(sectors)}/{len(universe)} tracked symbols "
              f"({before - len(sectors)} in NSE's lists but not in our master).")

    rows = [{"symbol": s, "sector": v} for s, v in sorted(sectors.items())]
    bulk_upsert(
        get_engine(), "symbol_sector", ["symbol", "sector"], rows,
        conflict_cols=["symbol"], update_cols=["sector"],
    )
    print(f"Wrote {len(rows)} sector rows to the database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
