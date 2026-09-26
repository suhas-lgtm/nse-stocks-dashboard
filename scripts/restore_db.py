"""
Load a backup_db.py snapshot back into a database.

Use after a Neon accident, or to move to a different provider — point
DATABASE_URL at the new database and run this against the backup folder.

    python scripts/restore_db.py "C:/Users/suhas/Backups/nse-stocks-dashboard/2026-09-26_2230"

Every write is an upsert on the table's primary key, so this is safe to re-run
and will not duplicate rows.
"""

import csv
import sys
from pathlib import Path

from db import bulk_upsert, create_schema, get_engine, wait_for_db

# table -> (primary key columns)
KEYS = {
    "watchlist": ["list_type", "symbol"],
    "meta": ["key"],
    "symbol_sector": ["symbol"],
    "shares_outstanding": ["symbol"],
    "indices_history": ["date", "index_name"],
    "daily_prices": ["date", "symbol"],
}


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
    create_schema(engine)

    for table, key_cols in KEYS.items():
        path = folder / f"{table}.csv"
        if not path.exists():
            print(f"  {table:<20} (no file, skipped)")
            continue
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames or []
            rows = [{k: (None if v == "" else v) for k, v in r.items()}
                    for r in reader]
        if not rows:
            print(f"  {table:<20} (empty)")
            continue
        update_cols = [c for c in columns if c not in key_cols] or key_cols
        bulk_upsert(engine, table, columns, rows,
                    conflict_cols=key_cols, update_cols=update_cols)
        print(f"  {table:<20} {len(rows):>8,} rows restored")

    print("\nRestore complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
