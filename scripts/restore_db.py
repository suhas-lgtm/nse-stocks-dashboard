"""
Load a backup_db.py snapshot into a database.

Use after an accident, or to move provider — point DATABASE_URL at the new
database and run this against a backup folder. Every write is an upsert on the
table's primary key, so it is safe to re-run and will not duplicate rows.

    python scripts/restore_db.py "C:/Users/suhas/Backups/nse-stocks-dashboard/2026-09-27_1000"
"""

import csv
import sys
import time
from pathlib import Path

from db import bulk_upsert, create_schema, get_engine, wait_for_db

# table -> primary key columns
KEYS = {
    "watchlist": ["list_type", "symbol"],
    "meta": ["key"],
    "symbol_sector": ["symbol"],
    "shares_outstanding": ["symbol"],
    "indices_history": ["date", "index_name"],
    "daily_prices": ["date", "symbol"],
}

# daily_prices is ~650k rows; holding all of it as dicts before the first
# write would need most of a gigabyte, so stream it in chunks.
CHUNK_ROWS = 20_000


def _write_chunk(engine, table, columns, rows, key_cols, update_cols) -> int:
    """One upsert per chunk, retried once — a long-haul connection to a
    serverless database drops often enough on a restore this size."""
    for attempt in (1, 2):
        try:
            bulk_upsert(engine, table, columns, rows,
                        conflict_cols=key_cols, update_cols=update_cols)
            return len(rows)
        except Exception:
            if attempt == 2:
                raise
            print(f"    chunk failed, retrying in 5s...")
            time.sleep(5)
    return 0


def restore_table(engine, table: str, path: Path, key_cols: list[str]) -> int:
    total = 0
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        if not columns:
            return 0
        update_cols = [c for c in columns if c not in key_cols] or key_cols

        chunk = []
        for record in reader:
            chunk.append({k: (None if v == "" else v) for k, v in record.items()})
            if len(chunk) >= CHUNK_ROWS:
                total += _write_chunk(engine, table, columns, chunk,
                                      key_cols, update_cols)
                print(f"    {total:,} rows so far...", flush=True)
                chunk = []
        if chunk:
            total += _write_chunk(engine, table, columns, chunk,
                                  key_cols, update_cols)
    return total


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    folder = Path(sys.argv[1])
    if not folder.is_dir():
        print(f"No such folder: {folder}")
        return 1

    engine = get_engine()
    wait_for_db(engine)
    print("Creating schema if needed...")
    create_schema(engine)

    grand_total = 0
    for table, key_cols in KEYS.items():
        path = folder / f"{table}.csv"
        if not path.exists():
            print(f"  {table:<20} (no file, skipped)")
            continue
        n = restore_table(engine, table, path, key_cols)
        grand_total += n
        print(f"  {table:<20} {n:>9,} rows restored")

    print(f"\nRestore complete — {grand_total:,} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
