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
    values = [tuple(r.get(c) for c in columns) for r in rows]

    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            execute_values(cur, sql, values, page_size=page_size)
        raw.commit()
    finally:
        raw.close()
    return len(rows)
