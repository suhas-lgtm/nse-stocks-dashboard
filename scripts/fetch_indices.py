"""
Fetches every Indian market index available on Yahoo Finance under the tickers
below and upserts them into data/indices_history.csv. Because this always
pulls a full year and merges by (date, index), the same script both backfills
history on first run and does the nightly top-up — no separate backfill mode
needed, unlike the stock pipeline.

Usage:
    python scripts/fetch_indices.py
"""

import csv
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 20

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "indices_history.csv"

# Every Indian index we found available on Yahoo Finance (checked manually —
# yfinance has no "list all indices" API). About half of these (mostly the
# ^CNXxxx sector indices) only ever return today's level, no history — Yahoo
# just doesn't carry it. Those show a single day and can't be used in the
# returns calculator across a date range.
INDICES = {
    "NIFTY 50": "^NSEI",
    "NIFTY BANK": "^NSEBANK",
    "NIFTY NEXT 50": "^NSMIDCP",
    "NIFTY MIDCAP 150": "NIFTYMIDCAP150.NS",
    "NIFTY SMALLCAP 250": "NIFTYSMLCAP250.NS",
    "NIFTY SMALLCAP 100": "^CNXSC",
    "NIFTY 100": "^CNX100",
    "NIFTY 200": "^CNX200",
    "NIFTY 500": "^CRSLDX",
    "NIFTY IT": "^CNXIT",
    "NIFTY AUTO": "^CNXAUTO",
    "NIFTY FMCG": "^CNXFMCG",
    "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTY METAL": "^CNXMETAL",
    "NIFTY REALTY": "^CNXREALTY",
    "NIFTY ENERGY": "^CNXENERGY",
    "NIFTY PSU BANK": "^CNXPSUBANK",
    "NIFTY PSE": "^CNXPSE",
    "NIFTY INFRA": "^CNXINFRA",
    "NIFTY MEDIA": "^CNXMEDIA",
    "NIFTY FIN SERVICE": "NIFTY_FIN_SERVICE.NS",
    "NIFTY COMMODITIES": "^CNXCMDT",
    "NIFTY CONSUMPTION": "^CNXCONSUM",
    "NIFTY SERVICES SECTOR": "^CNXSERVICE",
    "SENSEX": "^BSESN",
    "INDIA VIX": "^INDIAVIX",
}

# The subset shown as the "popular" strip at the top of the dashboard — all
# have full 1-year history, so their sparklines actually mean something.
POPULAR_INDICES = [
    "NIFTY 50", "SENSEX", "NIFTY BANK", "NIFTY IT", "NIFTY PHARMA",
    "NIFTY NEXT 50", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250", "NIFTY 100", "NIFTY 500",
]

OUT_COLUMNS = ["date", "index", "close", "chg_pct"]


def fetch_all(tickers):
    for attempt in range(1, MAX_RETRIES + 1):
        data = yf.download(tickers, period="1y", group_by="ticker", progress=False, threads=True, auto_adjust=False)
        # A broken Yahoo session fails every ticker at once (see fetch_yahoo_prices.py) —
        # treat "all empty" as a retry-worthy glitch, not real data gaps for 5 major indices.
        any_ok = any(
            ticker in data and not data[ticker]["Close"].dropna().empty
            for ticker in tickers if ticker in data
        )
        if any_ok or attempt == MAX_RETRIES:
            return data
        print(f"All indices came back empty (attempt {attempt}/{MAX_RETRIES}), "
              f"retrying in {RETRY_DELAY_SECONDS}s...")
        time.sleep(RETRY_DELAY_SECONDS)
    return data


def main():
    tickers = list(INDICES.values())
    data = fetch_all(tickers)

    new_rows = []
    for name, ticker in INDICES.items():
        try:
            sub = data[ticker].dropna(subset=["Close"])
        except KeyError:
            print(f"WARNING: no data at all for {name} ({ticker})")
            continue
        if sub.empty:
            print(f"WARNING: empty series for {name} ({ticker})")
            continue
        closes = sub["Close"]
        for i in range(len(closes)):
            close = float(closes.iloc[i])
            prev = float(closes.iloc[i - 1]) if i > 0 else None
            chg_pct = round((close - prev) / prev * 100, 2) if prev else ""
            new_rows.append({
                "date": closes.index[i].date().isoformat(),
                "index": name,
                "close": round(close, 2),
                "chg_pct": chg_pct,
            })
        print(f"{name}: {len(closes)} day(s), latest {closes.index[-1].date()} = {closes.iloc[-1]:.2f}")

    if not new_rows:
        print("ERROR: fetched 0 index rows.")
        sys.exit(1)

    new_df = pd.DataFrame(new_rows)
    if OUT_PATH.exists():
        existing = pd.read_csv(OUT_PATH, dtype={"date": str})
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.drop_duplicates(subset=["date", "index"], keep="last")
    combined = combined.sort_values(["index", "date"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False, columns=OUT_COLUMNS)
    print(f"Wrote {len(combined)} total rows to {OUT_PATH}")


if __name__ == "__main__":
    main()
