"""
Dump every table to CSV, for a local backup outside OneDrive.

Most of this project is recoverable: the code and the historical CSVs are on
GitHub, and prices can be re-fetched from Yahoo. Three things cannot be:

  * prices for any day after the CSVs in data/daily/ stop
  * the sector classification, if NSE changes its index files
  * the watchlist and its holdings, which exist nowhere but the database

Neon's free tier has no backups, so one bad DELETE or a lapsed account loses
them. This writes plain CSVs that restore_db.py can read back.

    python scripts/backup_db.py
    python scripts/backup_db.py --out "C:/Users/suhas/Backups/nse"

Output goes to a dated folder, so repeated runs keep their own snapshot.
"""

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from db import get_engine, wait_for_db

# Small tables first: if a run is interrupted, the irreplaceable ones are
# already on disk.
TABLES = ["watchlist", "meta", "symbol_sector", "shares_outstanding",
          "indices_history", "daily_prices"]

DEFAULT_OUT = Path.home() / "Backups" / "nse-stocks-dashboard"


def dump_table(conn, table: str, path: Path) -> int:
    result = conn.execution_options(stream_results=True).execute(
        text(f"SELECT * FROM {table}"))
    columns = list(result.keys())
    rows = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        while True:
            chunk = result.fetchmany(10_000)
            if not chunk:
                break
            writer.writerows(chunk)
            rows += len(chunk)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"backup root (default: {DEFAULT_OUT})")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    if "onedrive" in str(out_dir).lower():
        print("WARNING: this backup path is inside OneDrive, so it will be "
              "synced to the cloud. Pass --out to choose somewhere local.\n")

    engine = get_engine()
    wait_for_db(engine)

    total = 0
    with engine.connect() as conn:
        for table in TABLES:
            path = out_dir / f"{table}.csv"
            try:
                n = dump_table(conn, table, path)
                size = path.stat().st_size
                print(f"  {table:<20} {n:>8,} rows  {size/1024:>8,.0f} KB")
                total += n
            except Exception as e:
                print(f"  {table:<20} FAILED: {type(e).__name__}: {e}")

    print(f"\n{total:,} rows written to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
