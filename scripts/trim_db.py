"""
Shrink the database and stop it growing.

Storage was 268 MB of a 500 MB free tier, climbing ~30 MB a month as each
trading day adds ~2,900 rows. That runs out in about seven months. This does
three things, all reversible from a backup_db.py snapshot:

  1. Drops idx_daily_prices_date. The primary key is already a B-tree on
     (date, symbol); date is its leading column, so the PK serves every
     "WHERE date = ..." query by itself. The separate index has been storing a
     second copy of every date for nothing.

  2. Drops daily_prices.value. It is close x volume, and nothing reads it —
     every "value" in dashboard.py is the portfolio column (quantity x close),
     not this one.

  3. Deletes rows older than the retention window, and keeps deleting them on
     each run, so storage plateaus instead of climbing.

Postgres does not hand space back on DELETE or DROP COLUMN — it marks the
space reusable. Pass --vacuum to rewrite the table and actually return it,
which needs a few minutes and briefly locks daily_prices.

    python scripts/trim_db.py                  # report only, changes nothing
    python scripts/trim_db.py --apply
    python scripts/trim_db.py --apply --vacuum

Take a backup first: python scripts/backup_db.py
"""

import argparse
import sys

from sqlalchemy import text

from db import get_engine, wait_for_db

# A year plus a buffer: the dashboard's 1Y return looks back 365 days and
# resolves to the nearest stored trading day, so cutting at exactly 365 would
# leave that column blank for the oldest dates.
DEFAULT_RETENTION_DAYS = 400

REDUNDANT_INDEXES = ["idx_daily_prices_date"]
UNUSED_COLUMNS = [("daily_prices", "value")]


def report(conn) -> None:
    print("Current sizes")
    print("-" * 52)
    rows = conn.execute(text("""
        SELECT relname,
               pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
               pg_size_pretty(pg_relation_size(c.oid))       AS heap,
               pg_size_pretty(pg_indexes_size(c.oid))        AS indexes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY pg_total_relation_size(c.oid) DESC
    """)).fetchall()
    print(f"  {'table':<22}{'total':>10}{'data':>10}{'indexes':>10}")
    for r in rows:
        print(f"  {r[0]:<22}{r[1]:>10}{r[2]:>10}{r[3]:>10}")

    print("\nIndexes on daily_prices")
    print("-" * 52)
    for r in conn.execute(text("""
        SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid))
        FROM pg_stat_user_indexes WHERE relname = 'daily_prices'
        ORDER BY pg_relation_size(indexrelid) DESC
    """)).fetchall():
        flag = "  <- redundant" if r[0] in REDUNDANT_INDEXES else ""
        print(f"  {r[0]:<34}{r[1]:>8}{flag}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually make the changes (default: report only)")
    ap.add_argument("--vacuum", action="store_true",
                    help="rewrite daily_prices to return freed space to the "
                         "filesystem; locks the table for a few minutes")
    ap.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    args = ap.parse_args()

    engine = get_engine()
    wait_for_db(engine)

    with engine.connect() as conn:
        report(conn)

        cutoff = conn.execute(text(
            "SELECT (CURRENT_DATE - CAST(:n AS integer))::text"),
            {"n": args.retention_days}).scalar()
        old = conn.execute(text(
            "SELECT count(*) FROM daily_prices WHERE date < CAST(:c AS date)"),
            {"c": cutoff}).scalar()
        total = conn.execute(text("SELECT count(*) FROM daily_prices")).scalar()

    print(f"\nRetention: keeping {args.retention_days} days (from {cutoff})")
    print(f"  {old:,} of {total:,} rows are older than that")

    if not args.apply:
        print("\nReport only — nothing changed. Re-run with --apply to proceed.")
        print("Take a backup first: python scripts/backup_db.py")
        return 0

    print("\nApplying:")
    with engine.begin() as conn:
        for idx in REDUNDANT_INDEXES:
            conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))
            print(f"  dropped index {idx}")
        for table, column in UNUSED_COLUMNS:
            conn.execute(text(
                f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}"))
            print(f"  dropped column {table}.{column}")
        if old:
            deleted = conn.execute(text(
                "DELETE FROM daily_prices WHERE date < CAST(:c AS date)"),
                {"c": cutoff}).rowcount
            print(f"  deleted {deleted:,} rows older than {cutoff}")

    if args.vacuum:
        print("\n  rewriting daily_prices to reclaim space (this takes a while)...")
        # VACUUM FULL cannot run inside a transaction block.
        raw = engine.raw_connection()
        try:
            raw.set_isolation_level(0)
            with raw.cursor() as cur:
                cur.execute("VACUUM FULL ANALYZE daily_prices")
        finally:
            raw.close()
        print("  done")
    else:
        print("\n  Space is now reusable but not returned to the filesystem.")
        print("  Re-run with --vacuum to actually shrink it.")

    with engine.connect() as conn:
        print()
        report(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
