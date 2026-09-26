"""
Render dashboard.py headlessly against fixture data.

Streamlit needs a browser and the app needs a database, so nothing about the
page was testable before this — three releases in a row shipped errors that a
user found by looking at a red page.

This stubs Streamlit and pandas.read_sql, then executes dashboard.py top to
bottom exactly as Streamlit would. It catches the class of bug that comes from
referencing a column that isn't there, or maths that breaks on a NaN, across
every tab at once. It does NOT check SQL correctness — the queries are stubbed
— so run scripts/smoke_test_queries.py against a real database as well.

    python tests/render_dashboard.py                        # default controls
    FAKE_ST_AGGRESSIVE=1 python tests/render_dashboard.py   # filters engaged

Exits non-zero if the page raises, warns, or renders nothing.
"""

import os
import random
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.chdir(ROOT)
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@fixture/db")

import fake_streamlit as fst  # noqa: E402

sys.modules["streamlit"] = fst
fst.session_state["personal_watchlist_unlocked"] = True

random.seed(7)
np.random.seed(7)

SYMS = [f"SYM{i:03d}" for i in range(60)] + ["RELIANCE", "TCS"]
SECTORS = ["Financial Services", "Information Technology", "Healthcare", "Power"]
DATES = [(date(2026, 9, 25) - timedelta(days=d)).isoformat() for d in range(400, -1, -1)]


def make_day(d):
    n = len(SYMS)
    close = np.round(np.random.uniform(20, 3000, n), 2)
    prev = np.round(close * np.random.uniform(0.9, 1.1, n), 2)
    vol = np.random.randint(0, 5_000_000, n).astype(float)
    vol[:3] = 0  # illiquid names, to exercise the volume guards
    return pd.DataFrame({
        "date": d, "symbol": SYMS, "series": ["EQ"] * n,
        "name": [f"{s} Limited" for s in SYMS],
        "open": np.round(prev * np.random.uniform(0.95, 1.08, n), 2),
        "high": np.round(close * 1.05, 2), "low": np.round(close * 0.95, 2),
        "close": close, "prev_close": prev,
        "chg_pct": np.round((close - prev) / prev * 100, 2),
        "volume": vol, "value": close * vol,
    })


def fake_read_sql(sql, con, params=None):
    q = " ".join(str(sql).split())
    if "FROM shares_outstanding" in q:
        return pd.DataFrame({
            "symbol": SYMS,
            "shares_outstanding": np.random.randint(int(1e6), int(1e10),
                                                    len(SYMS), dtype=np.int64)})
    if "FROM symbol_sector" in q:
        return pd.DataFrame({"symbol": SYMS,
                             "sector": [random.choice(SECTORS) for _ in SYMS]})
    if "FROM indices_history" in q:
        return pd.DataFrame([
            {"date": d, "index": ix, "close": 20000 + i * 3, "chg_pct": 0.4}
            for i, d in enumerate(DATES[-30:]) for ix in ("NIFTY 50", "NIFTY BANK")])
    if "MAX(high)" in q:
        return pd.DataFrame({"symbol": SYMS,
                             "high_52w": np.random.uniform(2000, 4000, len(SYMS)),
                             "low_52w": np.random.uniform(5, 50, len(SYMS))})
    if "AVG(volume)" in q:
        return pd.DataFrame({"symbol": SYMS,
                             "avg_volume": np.random.uniform(1e4, 1e6, len(SYMS))})
    if "WHERE symbol = :s" in q:
        n = 60
        return pd.DataFrame({"date": DATES[-n:],
                             "open": np.random.uniform(90, 110, n),
                             "high": np.random.uniform(100, 120, n),
                             "low": np.random.uniform(80, 95, n),
                             "close": np.random.uniform(90, 110, n),
                             "volume": np.random.uniform(1e4, 1e6, n)})
    if "WHERE date IN" in q:
        frames = [pd.DataFrame({"date": d, "symbol": SYMS,
                                "close": np.random.uniform(20, 3000, len(SYMS))})
                  for d in (params or {}).values()]
        return pd.concat(frames) if frames else pd.DataFrame()
    if "WHERE date = :d" in q:
        return make_day(params["d"])
    raise AssertionError("unstubbed query: " + q[:140])


pd.read_sql = fake_read_sql


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        q = " ".join(str(stmt).split())
        if "DISTINCT date::text" in q:
            return FakeResult([(d,) for d in DATES])
        if "FROM meta" in q:
            return FakeResult([("2026-09-25T17:30:00Z",)])
        if "FROM watchlist" in q and "quantity" in q:
            return FakeResult([("RELIANCE", 100.0, 1200.0), ("TCS", None, None)])
        if "SELECT symbol FROM watchlist" in q:
            return FakeResult([("RELIANCE",), ("TCS",)])
        return FakeResult([])


class FakeEngine:
    def connect(self):
        return FakeConn()

    def begin(self):
        return FakeConn()


import sqlalchemy  # noqa: E402

sqlalchemy.create_engine = lambda *a, **k: FakeEngine()


def main() -> int:
    src = open(os.path.join(ROOT, "dashboard.py"), encoding="utf-8").read()
    ns = {"__name__": "__main__", "__file__": os.path.join(ROOT, "dashboard.py")}
    try:
        exec(compile(src, "dashboard.py", "exec"), ns)
    except SystemExit as e:
        print("page stopped early:", e)
        return 1

    tables = fst.CALLS.count("dataframe")
    print(f"rendered {tables} tables, "
          f"{fst.CALLS.count('metric')} metrics, "
          f"{fst.CALLS.count('line_chart') + fst.CALLS.count('bar_chart')} charts, "
          f"{fst.CALLS.count('download_button')} downloads")

    if fst.WARNINGS:
        print("\nWARNINGS RAISED:")
        for w in fst.WARNINGS:
            print("  -", w[:300])
        return 1
    if tables == 0:
        print("nothing rendered")
        return 1
    print("OK - no warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
