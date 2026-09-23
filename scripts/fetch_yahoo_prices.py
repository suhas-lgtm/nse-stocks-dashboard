"""
Nightly price refresh — reads data/symbol_master.csv (built by
refresh_symbol_master.py) and pulls the latest daily OHLCV for every symbol
from Yahoo Finance in small batches. This is the only script the nightly
GitHub Action runs; it never talks to NSE.

Yahoo Finance has a known failure mode where a broken session (crumb/cookie)
causes every subsequent request to fail instantly and silently — yfinance
reports it as "possibly delisted" even for large, obviously-listed stocks.
To avoid quietly writing garbage data on a bad night, this script:
  1. Checks a handful of "canary" symbols (large, always-listed stocks) first.
     If canaries fail, the whole run aborts before touching any data files.
  2. Fetches in small batches (default 50) with pauses between them.
  3. Retries any batch whose failure rate looks abnormal (real delistings are
     rare and isolated; a systemic Yahoo/session issue fails a large chunk of
     a batch at once).
  4. Aborts the whole run (no files written, non-zero exit so the GitHub
     Action shows as failed) if the overall missing rate is still too high
     after retries — instead of committing an incomplete snapshot.

Usage:
    python scripts/fetch_yahoo_prices.py
"""

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
MASTER_PATH = ROOT / "data" / "symbol_master.csv"
DAILY_DIR = ROOT / "data" / "daily"
LATEST_PATH = ROOT / "data" / "latest.csv"
META_PATH = ROOT / "data" / "meta.json"

OUT_COLUMNS = [
    "date", "symbol", "series", "isin", "name",
    "open", "high", "low", "close", "prev_close", "chg_pct",
    "volume", "value",
]

# Large, highly liquid stocks that should essentially never come back empty.
# If these fail, treat it as a Yahoo-side/session problem, not real data gaps.
CANARY_SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"]

BATCH_SIZE = 50
BATCH_PAUSE_SECONDS = 8
BATCH_FAILURE_RATE_RETRY_THRESHOLD = 0.15   # retry a batch if >15% of it is missing
MAX_BATCH_RETRIES = 3
BATCH_RETRY_BACKOFF_SECONDS = 20

CANARY_RETRY_ATTEMPTS = 3
CANARY_RETRY_BACKOFF_SECONDS = 60

# Abort the whole run (write nothing) if more than this fraction of the
# universe is missing after all retries.
MAX_ACCEPTABLE_MISSING_RATE = 0.03


def load_symbol_master() -> list[dict]:
    if not MASTER_PATH.exists():
        print(f"ERROR: {MASTER_PATH} not found. Run scripts/refresh_symbol_master.py first.")
        sys.exit(1)
    with MASTER_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch(tickers: list[str]) -> pd.DataFrame:
    return yf.download(
        tickers, period="5d", group_by="ticker",
        progress=False, threads=True, auto_adjust=False,
    )


def check_canaries() -> bool:
    tickers = [f"{s}.NS" for s in CANARY_SYMBOLS]
    for attempt in range(1, CANARY_RETRY_ATTEMPTS + 1):
        data = fetch(tickers)
        ok = 0
        for s in CANARY_SYMBOLS:
            try:
                if not data[f"{s}.NS"]["Close"].dropna().empty:
                    ok += 1
            except Exception:
                pass
        print(f"Canary check (attempt {attempt}/{CANARY_RETRY_ATTEMPTS}): {ok}/{len(CANARY_SYMBOLS)} ok")
        if ok == len(CANARY_SYMBOLS):
            return True
        if attempt < CANARY_RETRY_ATTEMPTS:
            print(f"Canary symbols failed (Yahoo session likely broken); "
                  f"retrying in {CANARY_RETRY_BACKOFF_SECONDS}s...")
            time.sleep(CANARY_RETRY_BACKOFF_SECONDS)
    return False


def extract_rows(data: pd.DataFrame, batch_rows: list[dict], single: bool) -> tuple[list[dict], list[str]]:
    rows, missing = [], []
    for m in batch_rows:
        symbol, series, isin, name = m["symbol"], m["series"], m["isin"], m["name"]
        ticker = f"{symbol}.NS"
        try:
            sub = data if single else data[ticker]
            sub = sub.dropna(subset=["Close"])
        except KeyError:
            sub = None
        if sub is None or sub.empty:
            missing.append(symbol)
            continue

        last = sub.iloc[-1]
        prev_close = sub.iloc[-2]["Close"] if len(sub) >= 2 else None
        close = last["Close"]
        chg_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else ""

        rows.append({
            "date": sub.index[-1].date().isoformat(),
            "symbol": symbol,
            "series": series,
            "isin": isin,
            "name": name,
            "open": round(float(last["Open"]), 2),
            "high": round(float(last["High"]), 2),
            "low": round(float(last["Low"]), 2),
            "close": round(float(close), 2),
            "prev_close": round(float(prev_close), 2) if prev_close else "",
            "chg_pct": chg_pct,
            "volume": int(last["Volume"]) if pd.notna(last["Volume"]) else "",
            # Approximation (close * volume) — Yahoo doesn't expose NSE's
            # official traded turnover figure.
            "value": round(float(close) * float(last["Volume"]), 2) if pd.notna(last["Volume"]) else "",
        })
    return rows, missing


def fetch_batch_with_retries(batch: list[dict]) -> tuple[list[dict], list[str]]:
    symbols = [m["symbol"] for m in batch]
    single = len(symbols) == 1
    rows, missing = [], symbols

    for attempt in range(1, MAX_BATCH_RETRIES + 1):
        data = fetch([f"{s}.NS" for s in symbols])
        rows, missing = extract_rows(data, batch, single)
        failure_rate = len(missing) / len(batch)
        if failure_rate <= BATCH_FAILURE_RATE_RETRY_THRESHOLD:
            return rows, missing
        if attempt < MAX_BATCH_RETRIES:
            print(f"  batch failure rate {failure_rate:.0%} looks abnormal "
                  f"(attempt {attempt}/{MAX_BATCH_RETRIES}), retrying in "
                  f"{BATCH_RETRY_BACKOFF_SECONDS}s...")
            time.sleep(BATCH_RETRY_BACKOFF_SECONDS)
            # re-fetch only the ones still missing next loop by narrowing batch
            batch = [m for m in batch if m["symbol"] in missing]
            symbols = [m["symbol"] for m in batch]
            single = len(symbols) == 1
        else:
            print(f"  batch still {failure_rate:.0%} missing after {MAX_BATCH_RETRIES} attempts, giving up on these.")

    return rows, missing


def write_rows_to_db(rows: list[dict]) -> None:
    """Upsert into daily_prices, plus stamp meta.last_fetched_utc."""
    from datetime import datetime, timezone

    from db import bulk_upsert, get_engine
    from sqlalchemy import text

    engine = get_engine()
    # Deduplicate on (date, symbol): a stock whose latest bar lagged can appear
    # under two dates in one run, and Postgres refuses an upsert that touches
    # the same row twice.
    deduped = {(r["date"], r["symbol"]): r for r in rows}
    payload = [
        {k: (None if v == "" else v) for k, v in r.items()}
        for r in deduped.values()
    ]

    cols = ["date", "symbol", "series", "name", "open", "high", "low", "close",
            "prev_close", "chg_pct", "volume", "value"]
    bulk_upsert(
        engine, "daily_prices", cols, payload,
        conflict_cols=["date", "symbol"],
        update_cols=[c for c in cols if c not in ("date", "symbol")],
    )

    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO meta (key, value) VALUES ('last_fetched_utc', :v)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""),
            {"v": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        )


def main():
    master_rows = load_symbol_master()
    print(f"Loaded {len(master_rows)} symbols from {MASTER_PATH}")

    print("Checking canary symbols before starting the full run...")
    if not check_canaries():
        print(
            "ABORTING: canary symbols failed after retries — Yahoo Finance session "
            "looks broken right now. Not touching any data files. This run will "
            "show as failed; try again later or rerun manually."
        )
        sys.exit(1)

    all_rows = []
    all_missing = []
    for i in range(0, len(master_rows), BATCH_SIZE):
        batch = master_rows[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(master_rows) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"Batch {batch_num}/{total_batches} ({len(batch)} symbols)...")
        rows, missing = fetch_batch_with_retries(batch)
        all_rows.extend(rows)
        all_missing.extend(missing)
        if i + BATCH_SIZE < len(master_rows):
            time.sleep(BATCH_PAUSE_SECONDS)

    missing_rate = len(all_missing) / len(master_rows)
    print(f"\nDone: {len(all_rows)} ok, {len(all_missing)} missing ({missing_rate:.1%})")

    if missing_rate > MAX_ACCEPTABLE_MISSING_RATE:
        print(
            f"ABORTING: {missing_rate:.1%} of the universe is missing, above the "
            f"{MAX_ACCEPTABLE_MISSING_RATE:.0%} threshold — this looks systemic, not "
            f"isolated delistings. Not writing/committing this run's data."
        )
        print(f"Missing symbols (first 30): {all_missing[:30]}")
        sys.exit(1)

    if not all_rows:
        print("ERROR: 0 rows fetched.")
        sys.exit(1)

    dates = [r["date"] for r in all_rows]
    target_date_str = max(set(dates), key=dates.count)

    all_rows.sort(key=lambda r: (r["series"], r["symbol"]))
    write_rows_to_db(all_rows)

    by_series = {}
    for r in all_rows:
        by_series[r["series"]] = by_series.get(r["series"], 0) + 1

    print(f"Wrote {len(all_rows)} rows to the database (latest date {target_date_str})")
    print("Breakdown by series:", by_series)
    if all_missing:
        print(f"{len(all_missing)} symbols missing (likely genuinely delisted/suspended): {all_missing}")


if __name__ == "__main__":
    main()
