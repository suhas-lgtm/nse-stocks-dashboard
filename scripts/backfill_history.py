"""
One-time (or occasional) backfill: pulls ~1 year of daily history for every
symbol in data/symbol_master.csv from Yahoo Finance and writes one CSV per
trading day to data/daily/, in the same format the nightly fetch produces —
so the dashboard's date picker can browse any date once this has run.

Reuses the same defensive pattern as fetch_yahoo_prices.py (canary check,
small batches, retry on abnormal batch failure rates) since Yahoo's session
can break identically here. Takes roughly 10-15 minutes for ~2,900 symbols.

Usage:
    python scripts/backfill_history.py
    python scripts/backfill_history.py --period 6mo   # shorter window
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
MASTER_PATH = ROOT / "data" / "symbol_master.csv"
DAILY_DIR = ROOT / "data" / "daily"
LATEST_PATH = ROOT / "data" / "latest.csv"

OUT_COLUMNS = [
    "date", "symbol", "series", "isin", "name",
    "open", "high", "low", "close", "prev_close", "chg_pct",
    "volume", "value",
]

CANARY_SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"]
BATCH_SIZE = 50
BATCH_PAUSE_SECONDS = 8
BATCH_FAILURE_RATE_RETRY_THRESHOLD = 0.15
MAX_BATCH_RETRIES = 3
BATCH_RETRY_BACKOFF_SECONDS = 20
CANARY_RETRY_ATTEMPTS = 3
CANARY_RETRY_BACKOFF_SECONDS = 60


def load_symbol_master() -> list[dict]:
    if not MASTER_PATH.exists():
        print(f"ERROR: {MASTER_PATH} not found. Run scripts/refresh_symbol_master.py first.")
        sys.exit(1)
    with MASTER_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch(tickers: list[str], period: str) -> pd.DataFrame:
    return yf.download(
        tickers, period=period, group_by="ticker",
        progress=False, threads=True, auto_adjust=False,
    )


def check_canaries(period: str) -> bool:
    tickers = [f"{s}.NS" for s in CANARY_SYMBOLS]
    for attempt in range(1, CANARY_RETRY_ATTEMPTS + 1):
        data = fetch(tickers, period)
        ok = sum(
            1 for s in CANARY_SYMBOLS
            if f"{s}.NS" in data and not data[f"{s}.NS"]["Close"].dropna().empty
        )
        print(f"Canary check (attempt {attempt}/{CANARY_RETRY_ATTEMPTS}): {ok}/{len(CANARY_SYMBOLS)} ok")
        if ok == len(CANARY_SYMBOLS):
            return True
        if attempt < CANARY_RETRY_ATTEMPTS:
            time.sleep(CANARY_RETRY_BACKOFF_SECONDS)
    return False


def extract_symbol_history(sub: pd.DataFrame, m: dict) -> list[dict]:
    sub = sub.dropna(subset=["Close"])
    rows = []
    for i in range(len(sub)):
        close = float(sub.iloc[i]["Close"])
        prev_close = float(sub.iloc[i - 1]["Close"]) if i > 0 else None
        chg_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else ""
        row = sub.iloc[i]
        rows.append({
            "date": sub.index[i].date().isoformat(),
            "symbol": m["symbol"],
            "series": m["series"],
            "isin": m["isin"],
            "name": m["name"],
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(close, 2),
            "prev_close": round(prev_close, 2) if prev_close else "",
            "chg_pct": chg_pct,
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else "",
            "value": round(close * float(row["Volume"]), 2) if pd.notna(row["Volume"]) else "",
        })
    return rows


def fetch_batch_with_retries(batch: list[dict], period: str) -> tuple[dict, list[str]]:
    """Returns ({symbol: [row, ...]}, [missing_symbols])."""
    symbols = [m["symbol"] for m in batch]
    by_symbol, missing = {}, symbols

    for attempt in range(1, MAX_BATCH_RETRIES + 1):
        data = fetch([f"{s}.NS" for s in symbols], period)
        by_symbol, missing = {}, []
        single = len(symbols) == 1
        for m in batch:
            ticker = f"{m['symbol']}.NS"
            try:
                sub = data if single else data[ticker]
            except KeyError:
                sub = None
            rows = extract_symbol_history(sub, m) if sub is not None else []
            if rows:
                by_symbol[m["symbol"]] = rows
            else:
                missing.append(m["symbol"])

        failure_rate = len(missing) / len(batch)
        if failure_rate <= BATCH_FAILURE_RATE_RETRY_THRESHOLD:
            return by_symbol, missing
        if attempt < MAX_BATCH_RETRIES:
            print(f"  batch failure rate {failure_rate:.0%} looks abnormal "
                  f"(attempt {attempt}/{MAX_BATCH_RETRIES}), retrying in "
                  f"{BATCH_RETRY_BACKOFF_SECONDS}s...")
            time.sleep(BATCH_RETRY_BACKOFF_SECONDS)
            batch = [m for m in batch if m["symbol"] in missing]
            symbols = [m["symbol"] for m in batch]
        else:
            print(f"  batch still {failure_rate:.0%} missing after {MAX_BATCH_RETRIES} attempts, giving up on these.")

    return by_symbol, missing


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", type=str, default="1y", help="yfinance period, e.g. 1y, 6mo (default: 1y)")
    args = parser.parse_args()

    master_rows = load_symbol_master()
    print(f"Loaded {len(master_rows)} symbols from {MASTER_PATH}")

    print("Checking canary symbols before starting the backfill...")
    if not check_canaries(args.period):
        print("ABORTING: canary symbols failed after retries — Yahoo session looks broken. Try again later.")
        sys.exit(1)

    by_date: dict[str, list[dict]] = {}
    all_missing = []

    total_batches = (len(master_rows) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(master_rows), BATCH_SIZE):
        batch = master_rows[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"Batch {batch_num}/{total_batches} ({len(batch)} symbols)...")
        by_symbol, missing = fetch_batch_with_retries(batch, args.period)
        for symbol, rows in by_symbol.items():
            for row in rows:
                by_date.setdefault(row["date"], []).append(row)
        all_missing.extend(missing)
        if i + BATCH_SIZE < len(master_rows):
            time.sleep(BATCH_PAUSE_SECONDS)

    if not by_date:
        print("ERROR: 0 rows fetched across the whole run.")
        sys.exit(1)

    dates = sorted(by_date.keys())
    for d in dates:
        rows = sorted(by_date[d], key=lambda r: (r["series"], r["symbol"]))
        write_csv(rows, DAILY_DIR / f"{d}.csv")

    latest_date = dates[-1]
    write_csv(sorted(by_date[latest_date], key=lambda r: (r["series"], r["symbol"])), LATEST_PATH)

    symbols_covered = len({m["symbol"] for rows in by_date.values() for m in rows})
    print(f"\nDone: {len(dates)} trading days written ({dates[0]} -> {latest_date}), "
          f"{len(master_rows) - len(set(all_missing))}/{len(master_rows)} symbols with data.")
    print(f"Updated {LATEST_PATH}")
    if all_missing:
        uniq_missing = sorted(set(all_missing))
        print(f"{len(uniq_missing)} symbols never returned data: {uniq_missing[:30]}"
              f"{' ...' if len(uniq_missing) > 30 else ''}")


if __name__ == "__main__":
    main()
