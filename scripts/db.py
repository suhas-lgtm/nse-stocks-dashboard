"""
Shared database connection + schema for the NSE dashboard.

The connection string comes from the DATABASE_URL environment variable (used by
the GitHub Action and local scripts). The Streamlit app reads it from
st.secrets instead — see dashboard.py — because Streamlit Cloud has no env vars.

Never hardcode the URL here: it contains a password.
"""

import os

from sqlalchemy import create_engine, text

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS daily_prices (
        date        DATE   NOT NULL,
        symbol      TEXT   NOT NULL,
        series      TEXT,
        name        TEXT,
        open        DOUBLE PRECISION,
        high        DOUBLE PRECISION,
        low         DOUBLE PRECISION,
        close       DOUBLE PRECISION,
        prev_close  DOUBLE PRECISION,
        chg_pct     DOUBLE PRECISION,
        volume      BIGINT,
        value       DOUBLE PRECISION,
        PRIMARY KEY (date, symbol)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON daily_prices (date)",
    "CREATE INDEX IF NOT EXISTS idx_daily_prices_symbol ON daily_prices (symbol)",
    """
    CREATE TABLE IF NOT EXISTS indices_history (
        date        DATE NOT NULL,
        index_name  TEXT NOT NULL,
        close       DOUBLE PRECISION,
        chg_pct     DOUBLE PRECISION,
        PRIMARY KEY (date, index_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shares_outstanding (
        symbol              TEXT PRIMARY KEY,
        shares_outstanding  BIGINT
    )
    """,
    # Watchlists finally get real persistence here: they survive app restarts,
    # redeploys, and are shared across every device/browser.
    """
    CREATE TABLE IF NOT EXISTS watchlist (
        list_type  TEXT NOT NULL,
        symbol     TEXT NOT NULL,
        added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (list_type, symbol)
    )
    """,
    # NSE's own sector classification, keyed by symbol. Kept in its own table
    # rather than a column on daily_prices: it changes a few times a year, not
    # daily, so repeating it on every one of ~645k price rows would be waste.
    """
    CREATE TABLE IF NOT EXISTS symbol_sector (
        symbol  TEXT PRIMARY KEY,
        sector  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key    TEXT PRIMARY KEY,
        value  TEXT
    )
    """,
]


def get_engine(url: str | None = None):
    """SQLAlchemy engine. Pass a url explicitly, else read DATABASE_URL."""
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it as an environment variable "
            "(locally / in GitHub Actions secrets) or in Streamlit secrets."
        )
    # Neon/Supabase hand out postgres:// URLs; SQLAlchemy wants postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    # pool_pre_ping avoids stale-connection errors when the serverless DB
    # wakes back up from idle.
    return create_engine(url, pool_pre_ping=True)


def create_schema(engine) -> None:
    with engine.begin() as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(text(stmt))


def recompute_daily_derived(engine, since: str | None = None) -> int:
    """Recompute prev_close and chg_pct for daily_prices from the stored series.

    Do NOT trust the fetch window for this. yfinance returns a rolling window
    that sometimes skips a trading day for a given symbol, so taking "the
    second-to-last row I just fetched" silently compares against the wrong day
    (this produced wrong change % for ~1,750 of 2,600 stocks). The database
    knows the real previous trading day per symbol; LAG() uses it.

    `since` limits which rows get rewritten (the window still scans all history
    so the boundary row is correct). Pass None for a full rebuild.
    """
    sql = """
        WITH ordered AS (
            SELECT date, symbol,
                   LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS prev
            FROM daily_prices
        )
        UPDATE daily_prices d
        SET prev_close = o.prev,
            chg_pct = CASE
                WHEN o.prev IS NOT NULL AND o.prev <> 0
                THEN ROUND((((d.close - o.prev) / o.prev) * 100)::numeric, 2)
                ELSE NULL
            END
        FROM ordered o
        WHERE d.date = o.date AND d.symbol = o.symbol
    """
    params = {}
    if since:
        sql += " AND d.date >= :since"
        params["since"] = since
    with engine.begin() as conn:
        return conn.execute(text(sql), params).rowcount


def _as_python(v):
    """Unwrap numpy scalars into plain Python values.

    psycopg2 adapts int/float/str, but not numpy's np.float64 / np.int64. Those
    silently render into the SQL as their repr ("np.float64(0.42)"), which
    Postgres parses as schema.function and rejects with InvalidSchemaName. The
    callers should hand over clean values, but one missed float() cost a whole
    nightly run, so normalise here too.
    """
    return v.item() if hasattr(v, "item") else v


def bulk_upsert(engine, table: str, columns: list[str], rows: list[dict],
                conflict_cols: list[str], update_cols: list[str],
                page_size: int = 1000) -> int:
    """Upsert many rows in few round trips.

    The naive path (executemany) sends one statement per row, which is ~250ms
    each over a long-haul connection — 2,900 rows took 14 minutes. execute_values
    packs them into a handful of statements instead, turning that into seconds.
    Returns the number of rows sent.
    """
    if not rows:
        return 0
    from psycopg2.extras import execute_values

    col_list = ", ".join(columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES %s "
        f"ON CONFLICT ({', '.join(conflict_cols)}) DO UPDATE SET {updates}"
    )
    values = [tuple(_as_python(r.get(c)) for c in columns) for r in rows]

    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            execute_values(cur, sql, values, page_size=page_size)
        raw.commit()
    finally:
        raw.close()
    return len(rows)
