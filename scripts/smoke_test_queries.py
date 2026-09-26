"""
Run every SQL query the dashboard issues, against the real database.

Why this exists: the dashboard's Python logic is easy to test against
fixtures, but its SQL is not — and three separate releases shipped queries
that were fine in Python and wrong in Postgres (a numpy value rendered into
SQL text, a str list adapted to text[] and compared to a date column, and a
column that only the GitHub Action created). Each one was found by a user
looking at a red page.

Run this before pushing dashboard changes, or let the workflow run it:

    python scripts/smoke_test_queries.py

Keep the queries here in step with dashboard.py — they are deliberately
copied rather than imported, because importing dashboard.py would execute
Streamlit.
"""

import os
import sys
import traceback
from datetime import date, timedelta

from sqlalchemy import text

from db import get_engine, wait_for_db

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        results.append((PASS, name, detail or ""))
    except Exception as e:
        results.append((FAIL, name, f"{type(e).__name__}: {e}"))
        if os.environ.get("SMOKE_VERBOSE"):
            traceback.print_exc()


def main() -> int:
    engine = get_engine()
    wait_for_db(engine)

    with engine.connect() as conn:
        def one(sql, **params):
            return conn.execute(text(sql), params).fetchone()

        def rows(sql, **params):
            return conn.execute(text(sql), params).fetchall()

        # Anchor everything to a date that really exists.
        as_of = one("SELECT max(date)::text FROM daily_prices")[0]
        if not as_of:
            print("No price data in the database — nothing to test.")
            return 1
        print(f"Testing against latest trading date {as_of}\n")

        check("available_dates", lambda: str(len(
            rows("SELECT DISTINCT date::text FROM daily_prices ORDER BY 1"))) + " dates")

        check("load_day", lambda: str(len(rows(
            """SELECT date::text AS date, symbol, series, name, open, high, low,
                      close, prev_close, chg_pct, volume, value
               FROM daily_prices WHERE date = :d""", d=as_of))) + " rows")

        check("load_indices", lambda: str(len(rows(
            """SELECT date::text AS date, index_name AS index, close, chg_pct
               FROM indices_history ORDER BY index_name, date"""))) + " rows")

        check("load_shares", lambda: str(len(rows(
            "SELECT symbol, shares_outstanding FROM shares_outstanding"))) + " rows")

        check("load_sectors", lambda: str(len(rows(
            "SELECT symbol, sector FROM symbol_sector"))) + " rows")

        check("load_last_fetched", lambda: str(one(
            "SELECT value FROM meta WHERE key = 'last_fetched_utc'")))

        # The one that shipped broken: named binds, not "= ANY(:list)".
        def closes_on():
            ds = [as_of,
                  (date.fromisoformat(as_of) - timedelta(days=30)).isoformat()]
            names = [f"d{i}" for i in range(len(ds))]
            ph = ", ".join(f":{n}" for n in names)
            got = rows(f"""SELECT date::text AS date, symbol, close
                           FROM daily_prices WHERE date IN ({ph})""",
                       **dict(zip(names, ds)))
            return f"{len(got)} rows over {len(ds)} dates"
        check("load_closes_on (multi-date IN)", closes_on)

        check("load_52w", lambda: str(len(rows(
            """SELECT symbol, MAX(high) AS high_52w, MIN(low) AS low_52w
               FROM daily_prices
               WHERE date <= CAST(:d AS date)
                 AND date >  CAST(:d AS date) - INTERVAL '1 year'
               GROUP BY symbol""", d=as_of))) + " symbols")

        # Watchlist holding columns — added by the app, not only the workflow.
        check("watchlist_holdings", lambda: str(len(rows(
            """SELECT symbol, quantity, buy_price FROM watchlist
               WHERE list_type = :t ORDER BY added_at""", t="personal"))) + " rows")

        check("price_series (stock detail)", lambda: str(len(rows(
            """SELECT date::text AS date, close, volume
               FROM daily_prices WHERE symbol = :s ORDER BY date""",
            s="RELIANCE"))) + " bars")

        check("volume_average (scanners)", lambda: str(len(rows(
            """SELECT symbol, AVG(volume) AS avg_volume
               FROM daily_prices
               WHERE date <= CAST(:d AS date)
                 AND date >  CAST(:d AS date) - INTERVAL '20 days'
               GROUP BY symbol""", d=as_of))) + " symbols")

    width = max(len(n) for _, n, _ in results)
    failures = 0
    for status, name, detail in results:
        mark = "ok  " if status == PASS else "FAIL"
        print(f"  [{mark}] {name:<{width}}  {detail}")
        failures += status == FAIL

    print()
    if failures:
        print(f"{failures} of {len(results)} queries FAILED. "
              f"Set SMOKE_VERBOSE=1 for full tracebacks.")
        return 1
    print(f"All {len(results)} queries OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
