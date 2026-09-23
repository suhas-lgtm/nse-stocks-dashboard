"""
One-time migration: loads the existing CSV data into Postgres.

Run this once, after setting DATABASE_URL, to seed the database with the year
of history already in data/. Safe to re-run — every insert is an upsert, so
re-running just overwrites rows with the same key.

Usage:
    set DATABASE_URL=postgresql://...   (Windows)
    python scripts/migrate_csv_to_db.py
"""

import csv
import io
import json
import sys
import time
from pathlib import Path

from db import create_schema, get_engine

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "data" / "daily"
INDICES_PATH = ROOT / "data" / "indices_history.csv"
SHARES_PATH = ROOT / "data" / "shares_outstanding.csv"
META_PATH = ROOT / "data" / "meta.json"

DAILY_COLUMNS = ["date", "symbol", "series", "name", "open", "high", "low",
                 "close", "prev_close", "chg_pct", "volume", "value"]


def _copy_rows(raw_conn, table: str, columns: list[str], rows: list[list]) -> None:
    """Bulk load via COPY — far faster than row-by-row inserts over a network."""
    if not rows:
        return
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    buf.seek(0)
    with raw_conn.cursor() as cur:
        cur.copy_expert(
            f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv)", buf
        )


def _blank_to_none(v):
    return None if v == "" else v


CHUNK_SIZE = 10  # files per transaction — small enough to survive a dropped connection


def _already_loaded_dates(engine) -> set:
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT date::text FROM daily_prices")).fetchall()
    return {r[0] for r in rows}


def _load_chunk(engine, files: list) -> int:
    """One fresh connection + transaction per chunk, so a dropped connection
    only costs us that chunk (which the caller retries) instead of the run."""
    rows = []
    for fp in files:
        with fp.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append([_blank_to_none(r.get(c, "")) for c in DAILY_COLUMNS])
    if not rows:
        return 0

    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            # A regular (not TEMP) staging table: TEMP tables die with the
            # session, and Neon's free tier drops sessions mid-load.
            cur.execute("DROP TABLE IF EXISTS _stage_daily")
            cur.execute("CREATE TABLE _stage_daily (LIKE daily_prices)")
        _copy_rows(raw, "_stage_daily", DAILY_COLUMNS, rows)
        with raw.cursor() as cur:
            # DISTINCT ON: a stock whose latest bar lagged a day gets written
            # into the next day's file too, so the same (date, symbol) can show
            # up in two files. Postgres refuses an upsert that touches the same
            # row twice, so collapse duplicates to one row first.
            cur.execute(f"""
                INSERT INTO daily_prices ({', '.join(DAILY_COLUMNS)})
                SELECT DISTINCT ON (date, symbol) {', '.join(DAILY_COLUMNS)}
                FROM _stage_daily
                ORDER BY date, symbol
                ON CONFLICT (date, symbol) DO UPDATE SET
                    series = EXCLUDED.series, name = EXCLUDED.name,
                    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                    close = EXCLUDED.close, prev_close = EXCLUDED.prev_close,
                    chg_pct = EXCLUDED.chg_pct, volume = EXCLUDED.volume,
                    value = EXCLUDED.value
            """)
            cur.execute("DROP TABLE IF EXISTS _stage_daily")
        raw.commit()
        return len(rows)
    finally:
        try:
            raw.close()
        except Exception:
            pass


def migrate_daily(engine) -> None:
    files = sorted(DAILY_DIR.glob("*.csv"))
    done_dates = _already_loaded_dates(engine)
    pending = [f for f in files if f.stem not in done_dates]
    print(f"{len(files)} daily files; {len(files) - len(pending)} already loaded, "
          f"{len(pending)} to go.")

    total = 0
    for i in range(0, len(pending), CHUNK_SIZE):
        chunk = pending[i:i + CHUNK_SIZE]
        for attempt in range(1, 4):
            try:
                total += _load_chunk(engine, chunk)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"  chunk {chunk[0].stem}..{chunk[-1].stem} failed "
                      f"({type(e).__name__}), retry {attempt}/3...")
                time.sleep(5)
        print(f"  {min(i + CHUNK_SIZE, len(pending))}/{len(pending)} files, "
              f"{total:,} rows loaded", flush=True)
    print(f"Loaded {total:,} daily price rows.")


def migrate_indices(engine) -> None:
    if not INDICES_PATH.exists():
        print("No indices_history.csv, skipping.")
        return
    rows = []
    with INDICES_PATH.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([r["date"], r["index"], _blank_to_none(r["close"]),
                         _blank_to_none(r["chg_pct"])])
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("CREATE TEMP TABLE _stage_idx (LIKE indices_history)")
        _copy_rows(raw, "_stage_idx", ["date", "index_name", "close", "chg_pct"], rows)
        with raw.cursor() as cur:
            cur.execute("""
                INSERT INTO indices_history (date, index_name, close, chg_pct)
                SELECT DISTINCT ON (date, index_name) date, index_name, close, chg_pct
                FROM _stage_idx
                ORDER BY date, index_name
                ON CONFLICT (date, index_name) DO UPDATE SET
                    close = EXCLUDED.close, chg_pct = EXCLUDED.chg_pct
            """)
        raw.commit()
        print(f"Loaded {len(rows):,} index rows.")
    finally:
        raw.close()


def migrate_shares(engine) -> None:
    if not SHARES_PATH.exists():
        print("No shares_outstanding.csv, skipping.")
        return
    rows = []
    with SHARES_PATH.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([r["symbol"], _blank_to_none(r["shares_outstanding"])])
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("CREATE TEMP TABLE _stage_shares (LIKE shares_outstanding)")
        _copy_rows(raw, "_stage_shares", ["symbol", "shares_outstanding"], rows)
        with raw.cursor() as cur:
            cur.execute("""
                INSERT INTO shares_outstanding (symbol, shares_outstanding)
                SELECT DISTINCT ON (symbol) symbol, shares_outstanding
                FROM _stage_shares
                ORDER BY symbol
                ON CONFLICT (symbol) DO UPDATE SET
                    shares_outstanding = EXCLUDED.shares_outstanding
            """)
        raw.commit()
        print(f"Loaded {len(rows):,} shares-outstanding rows.")
    finally:
        raw.close()


def migrate_meta(engine) -> None:
    if not META_PATH.exists():
        return
    from sqlalchemy import text
    meta = json.loads(META_PATH.read_text())
    with engine.begin() as conn:
        for k, v in meta.items():
            conn.execute(
                text("""INSERT INTO meta (key, value) VALUES (:k, :v)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""),
                {"k": k, "v": str(v)},
            )
    print(f"Loaded {len(meta)} meta key(s).")


def main():
    engine = get_engine()
    print("Creating schema (if not already there)...")
    create_schema(engine)
    migrate_daily(engine)
    migrate_indices(engine)
    migrate_shares(engine)
    migrate_meta(engine)
    print("\nMigration complete.")


if __name__ == "__main__":
    sys.exit(main())
