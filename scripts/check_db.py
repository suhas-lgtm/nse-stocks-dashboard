"""
Preflight check: can we actually reach the database?

Run this before the long fetch so a bad/missing DATABASE_URL fails in seconds
instead of after a ten-minute download. Prints nothing secret — the password is
masked before anything is echoed.

Usage:
    python scripts/check_db.py
"""

import os
import re
import sys

from db import create_schema, get_engine


def masked(url: str) -> str:
    """postgresql://user:SECRET@host/db  ->  postgresql://user:***@host/db"""
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url)


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: DATABASE_URL is not set.")
        print("  In GitHub: Settings -> Secrets and variables -> Actions -> "
              "the secret must be named exactly DATABASE_URL (all caps).")
        return 1

    print(f"DATABASE_URL is set: {masked(url)}")

    from sqlalchemy import text

    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Connection OK.")
    except Exception as e:
        print(f"FAIL: could not connect: {type(e).__name__}: {e}")
        return 1

    # Make sure every table exists before the fetch tries to write to it.
    create_schema(engine)

    with engine.connect() as conn:
        for table in ("daily_prices", "indices_history", "shares_outstanding",
                      "watchlist"):
            n = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            print(f"  {table}: {n:,} rows")
        latest = conn.execute(
            text("SELECT max(date)::text FROM daily_prices")
        ).scalar()
        print(f"  latest stored trading date: {latest}")

    print("Database is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
