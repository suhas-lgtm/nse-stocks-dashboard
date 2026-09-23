"""
Fetches shares outstanding per symbol from Yahoo Finance and writes
data/shares_outstanding.csv. This is NOT run nightly: shares outstanding
barely changes day to day (only on buybacks/splits/new issues), so this is
meant to run occasionally (e.g. monthly, alongside refresh_symbol_master.py).

Market cap = shares_outstanding x close, computed at read time from whatever
day's price you already have — no need to store market cap directly or
re-fetch it daily.

Unlike prices (bulk yf.download), Yahoo only exposes shares outstanding via
the per-ticker fast_info endpoint — there's no bulk equivalent, so this is
one request per symbol (~0.1-0.5s/symbol depending on Yahoo's mood; the full
~2,900-symbol universe took about 5 minutes in testing). It uses the same
canary-check + retry pattern as the price scripts, since this endpoint hits
the same Yahoo session fragility.

Usage:
    python scripts/fetch_shares_outstanding.py               # full universe
    python scripts/fetch_shares_outstanding.py --limit 50     # quick test
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
MASTER_PATH = ROOT / "data" / "symbol_master.csv"
OUT_PATH = ROOT / "data" / "shares_outstanding.csv"

CANARY_SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"]
CANARY_RETRY_ATTEMPTS = 3
CANARY_RETRY_BACKOFF_SECONDS = 30
PER_SYMBOL_RETRY_ATTEMPTS = 2
PER_SYMBOL_RETRY_BACKOFF_SECONDS = 5
PROGRESS_EVERY = 100


def get_shares(symbol: str):
    t = yf.Ticker(f"{symbol}.NS")
    for attempt in range(1, PER_SYMBOL_RETRY_ATTEMPTS + 1):
        try:
            shares = t.fast_info.get("shares")
            return shares
        except Exception:
            if attempt < PER_SYMBOL_RETRY_ATTEMPTS:
                time.sleep(PER_SYMBOL_RETRY_BACKOFF_SECONDS)
    return None


def check_canaries() -> bool:
    for attempt in range(1, CANARY_RETRY_ATTEMPTS + 1):
        ok = sum(1 for s in CANARY_SYMBOLS if get_shares(s))
        print(f"Canary check (attempt {attempt}/{CANARY_RETRY_ATTEMPTS}): {ok}/{len(CANARY_SYMBOLS)} ok")
        if ok == len(CANARY_SYMBOLS):
            return True
        if attempt < CANARY_RETRY_ATTEMPTS:
            time.sleep(CANARY_RETRY_BACKOFF_SECONDS)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="only fetch the first N symbols (for testing)")
    args = parser.parse_args()

    if not MASTER_PATH.exists():
        print(f"ERROR: {MASTER_PATH} not found. Run scripts/refresh_symbol_master.py first.")
        sys.exit(1)
    with MASTER_PATH.open(encoding="utf-8") as f:
        symbols = [row["symbol"] for row in csv.DictReader(f)]
    if args.limit:
        symbols = symbols[:args.limit]

    print(f"Fetching shares outstanding for {len(symbols)} symbols...")
    print("Checking canary symbols first...")
    if not check_canaries():
        print("ABORTING: canary symbols failed — Yahoo session looks broken. Try again later.")
        sys.exit(1)

    rows = []
    missing = []
    start = time.time()
    for i, symbol in enumerate(symbols, 1):
        shares = get_shares(symbol)
        if shares:
            rows.append({"symbol": symbol, "shares_outstanding": int(shares)})
        else:
            missing.append(symbol)
        if i % PROGRESS_EVERY == 0:
            elapsed = time.time() - start
            print(f"  {i}/{len(symbols)} done ({elapsed:.0f}s elapsed, {len(missing)} missing so far)")

    from db import bulk_upsert, get_engine

    bulk_upsert(
        get_engine(), "shares_outstanding", ["symbol", "shares_outstanding"], rows,
        conflict_cols=["symbol"], update_cols=["shares_outstanding"],
    )

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s: {len(rows)}/{len(symbols)} symbols with shares data.")
    print(f"Wrote {len(rows)} rows to the database")
    if missing:
        print(f"{len(missing)} symbols had no shares data (delisted/illiquid?): {missing[:20]}"
              f"{' ...' if len(missing) > 20 else ''}")


if __name__ == "__main__":
    main()
