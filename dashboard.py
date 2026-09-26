"""
Base dashboard for NSE stocks — a first pass to verify the data pipeline works.
Layout/design will be revisited later; this just needs to show the data clearly.
"""

import hashlib
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

IST = ZoneInfo("Asia/Kolkata")

# Same password/hash as the claude.ai preview's Personal Watchlist, for consistency.
# A deterrent against casual edits from anyone with the link, not real security.
WATCHLIST_PASSWORD_HASH = "7a759a779365ae885791dfa59c42d0a86ed7f7d078c793db022cceab9514c136"

# page_icon takes a path; assets/logo.png is the same pulse mark the MF
# Research Center uses as its favicon, so browser tabs match across both.
_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png")
st.set_page_config(page_title="NSE Research Center — Armstrong Capital",
                   layout="wide",
                   page_icon=_ICON if os.path.exists(_ICON) else "📈")

st.markdown("""
<style>
/* ── Armstrong Capital design system ─────────────────────────────────────
   Tokens lifted from the MF Research Center project (site/src/index.css) so
   the two dashboards read as one product. Keep in step if the house palette
   changes there. */
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Inter:wght@300;400;500;600;700&display=swap');

:root {
    --bg-base:   #0B1120;
    --bg-card:   #111A2E;
    --bg-raised: #18233C;
    --line:      #24314F;
    --text-hi:   #F1F5FB;
    --text-mid:  #9FB0CC;
    --text-low:  #5E6F8F;
    --equity:    #3B82F6;
    --accent-a:  #22D3EE;
    --accent-b:  #F472B6;
    --gain:      #34D399;
    --loss:      #F87171;
    --font-display: 'Space Grotesk', sans-serif;
    --font-ui:      'Inter', sans-serif;
    --radius-card:  10px;
    --shadow-card:  0 4px 24px 0 rgba(0,0,0,0.45);
    --transition:   140ms cubic-bezier(0.4,0,0.2,1);
}

[data-testid="stAppViewContainer"] { background: var(--bg-base); }
[data-testid="stHeader"] { background: transparent; }
html, body, [data-testid="stAppViewContainer"] * { font-family: var(--font-ui); }
h1, h2, h3, h4 { font-family: var(--font-display) !important; }

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg-base); }
::-webkit-scrollbar-thumb { background: var(--line); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-low); }

/* Cards — metrics reuse the .card treatment */
[data-testid="stMetric"] {
    background: var(--bg-card);
    border: 1px solid var(--line);
    border-radius: var(--radius-card);
    box-shadow: var(--shadow-card);
    padding: 14px 16px 10px;
}
[data-testid="stMetricValue"] {
    font-family: var(--font-display);
    font-variant-numeric: tabular-nums;
}
[data-testid="stMetricLabel"] { color: var(--text-mid); }

/* Section headers get the accent bar from the MF dashboard */
h3 {
    display: flex; align-items: center; gap: 0.5rem;
    padding-bottom: 0.75rem; margin-bottom: 1rem;
    border-bottom: 1px solid var(--line);
}
h3::before {
    content: ''; width: 4px; height: 1.25rem; border-radius: 2px; flex-shrink: 0;
    background: linear-gradient(180deg, var(--accent-a), var(--accent-b));
}

.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--line); }
.stTabs [data-baseweb="tab"] {
    color: var(--text-mid); font-weight: 600; font-family: var(--font-display);
}
.stTabs [aria-selected="true"] { color: var(--text-hi) !important; }

[data-testid="stDataFrame"] {
    border: 1px solid var(--line);
    border-radius: var(--radius-card);
    overflow: hidden;
}

/* ── Gradient hero header ─────────────────────────────────────────────── */
.ac-hero {
    position: relative;
    background: linear-gradient(120deg, #0B1120 0%, #16224A 50%, #1E3A8A 100%);
    border: 1px solid var(--line);
    border-radius: var(--radius-card);
    padding: 14px 18px;
    margin-bottom: 14px;
    display: flex; align-items: center; gap: 14px;
}
.ac-hero::after {
    content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent, var(--accent-a), transparent);
    opacity: 0.6; pointer-events: none;
}
/* The logo sits on a white chip, as in the MF header — the mark is dark and
   would disappear against the navy gradient otherwise. */
.ac-logo-chip {
    background: #fff; border-radius: 6px; padding: 2px 6px; height: 40px;
    display: flex; align-items: center; justify-content: center; flex: none;
    border: 1px solid rgba(255,255,255,0.3);
    box-shadow: 0 1px 6px rgba(0,0,0,0.18);
}
.ac-logo-chip img { height: 28px; width: auto; object-fit: contain; display: block; }
.ac-title {
    font-family: var(--font-display); font-weight: 700; font-size: 20px;
    letter-spacing: 0.03em; color: #fff; margin: 0; line-height: 1.25;
}
.ac-subtitle {
    font-size: 11px; color: rgba(255,255,255,0.5);
    letter-spacing: 0.03em; margin: 2px 0 0;
}
.ac-badge {
    display: inline-flex; align-items: center; padding: 0.125rem 0.5rem;
    border-radius: 4px; font-size: 0.625rem; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase;
    background: rgba(251,146,60,0.15); color: #FB923C;
    border: 1px solid rgba(251,146,60,0.3); margin-left: auto;
}

/* ── Index tiles ──────────────────────────────────────────────────────── */
.nse-idx-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
.nse-idx-tile {
    flex: 1 1 160px; min-width: 150px;
    background: var(--bg-card);
    border: 1px solid var(--line);
    border-radius: var(--radius-card);
    padding: 12px 14px;
    box-shadow: var(--shadow-card);
    transition: transform var(--transition), box-shadow var(--transition);
}
.nse-idx-tile:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}
.nse-idx-name {
    font-size: 10.5px; font-weight: 700; letter-spacing: 0.06em;
    color: var(--text-mid); text-transform: uppercase;
}
.nse-idx-value {
    font-size: 18px; font-weight: 600; font-family: var(--font-display);
    font-variant-numeric: tabular-nums; color: var(--text-hi);
}
.nse-idx-delta {
    font-size: 12px; font-weight: 600; font-family: var(--font-display);
    font-variant-numeric: tabular-nums; margin-left: 6px;
}
.nse-up { color: var(--gain); }
.nse-down { color: var(--loss); }
.nse-flat { color: var(--text-low); }
</style>
""", unsafe_allow_html=True)

# Market cap bands (₹ crore) — as specified for this dashboard. Note this is a
# simpler/looser cut than SEBI's official large/mid/small-cap classification
# (which ranks the top 100 / next 150 / rest by market cap rather than using
# fixed rupee cutoffs), so numbers won't match SEBI-labeled fund categories.
CAP_BANDS = [
    ("Large", 20000, float("inf")),
    ("Mid", 5000, 20000),
    ("Small", 500, 5000),
    ("Micro", 0, 500),
]
CAP_CATEGORIES = [b[0] for b in CAP_BANDS]


@st.cache_resource
def get_db():
    """One engine per app process. Reads the URL from Streamlit secrets in the
    cloud, or DATABASE_URL locally."""
    url = None
    try:
        url = st.secrets["DATABASE_URL"]
    except Exception:
        url = os.environ.get("DATABASE_URL")
    if not url:
        st.error(
            "No database configured. Add DATABASE_URL to this app's Streamlit "
            "secrets (Manage app → Settings → Secrets)."
        )
        st.stop()
    # Same driver pin as scripts/db.py — SQLAlchemy 2.1 defaults a bare
    # postgresql:// URL to psycopg v3, which isn't installed here.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url, pool_pre_ping=True)


@st.cache_data(ttl=600)
def available_dates() -> list[str]:
    with get_db().connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT date::text FROM daily_prices ORDER BY 1")
        ).fetchall()
    return [r[0] for r in rows]


@st.cache_data(ttl=600)
def load_day(date_str: str) -> pd.DataFrame:
    return pd.read_sql(
        text("""SELECT date::text AS date, symbol, series, name, open, high, low,
                       close, prev_close, chg_pct, volume, value
                FROM daily_prices WHERE date = :d"""),
        get_db(), params={"d": date_str},
    )


@st.cache_data(ttl=600)
def load_indices() -> pd.DataFrame:
    df = pd.read_sql(
        text("""SELECT date::text AS date, index_name AS index, close, chg_pct
                FROM indices_history ORDER BY index_name, date"""),
        get_db(),
    )
    return df


@st.cache_data(ttl=600)
def load_shares() -> pd.DataFrame:
    return pd.read_sql(
        text("SELECT symbol, shares_outstanding FROM shares_outstanding"), get_db()
    )


# Lookback windows for the multi-period return columns, in calendar days.
RETURN_PERIODS = [("1W", 7), ("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365)]


@st.cache_data(ttl=600)
def load_closes_on(date_strs: tuple[str, ...]) -> pd.DataFrame:
    """Closing prices for a handful of specific dates, as symbol x date.

    One query for all the reference dates rather than one per period — the
    dates are a tiny IN list and daily_prices is indexed on date.
    """
    if not date_strs:
        return pd.DataFrame()
    df = pd.read_sql(
        text("""SELECT date::text AS date, symbol, close
                FROM daily_prices WHERE date = ANY(:ds)"""),
        get_db(), params={"ds": list(date_strs)},
    )
    return df.pivot_table(index="symbol", columns="date", values="close")


@st.cache_data(ttl=600)
def load_52w(as_of: str) -> pd.DataFrame:
    """52-week high/low per symbol, over the year ending on as_of.

    Uses the stored intraday high/low rather than closes, which is what a
    "52-week high" conventionally means.
    """
    return pd.read_sql(
        text("""SELECT symbol,
                       MAX(high) AS high_52w,
                       MIN(low)  AS low_52w
                FROM daily_prices
                WHERE date <= :d AND date > (:d::date - INTERVAL '1 year')
                GROUP BY symbol"""),
        get_db(), params={"d": as_of},
    )


@st.cache_data(ttl=600)
def load_sectors() -> pd.DataFrame:
    """Sector per symbol. Returns an empty frame if the table isn't there yet,
    so a deploy that lands before the first sector fetch degrades to showing
    everything as "Unknown" instead of taking the whole dashboard down."""
    try:
        return pd.read_sql(text("SELECT symbol, sector FROM symbol_sector"), get_db())
    except Exception:
        return pd.DataFrame({"symbol": pd.Series(dtype="object"),
                             "sector": pd.Series(dtype="object")})


@st.cache_data(ttl=600)
def load_last_fetched() -> str | None:
    with get_db().connect() as conn:
        row = conn.execute(
            text("SELECT value FROM meta WHERE key = 'last_fetched_utc'")
        ).fetchone()
    return row[0] if row else None


# --- Watchlist storage (the reason we moved to a database: these now survive
# --- app restarts, redeploys, and are the same on every device.
def watchlist_symbols(list_type: str) -> list[str]:
    with get_db().connect() as conn:
        rows = conn.execute(
            text("SELECT symbol FROM watchlist WHERE list_type = :t ORDER BY added_at"),
            {"t": list_type},
        ).fetchall()
    return [r[0] for r in rows]


def watchlist_holdings(list_type: str) -> dict:
    """{symbol: {"quantity": x, "buy_price": y}} in the order stocks were added.
    Either field may be None — a name can be watched without being held."""
    with get_db().connect() as conn:
        rows = conn.execute(
            text("""SELECT symbol, quantity, buy_price FROM watchlist
                    WHERE list_type = :t ORDER BY added_at"""),
            {"t": list_type},
        ).fetchall()
    return {r[0]: {"quantity": r[1], "buy_price": r[2]} for r in rows}


def watchlist_add(list_type: str, symbol: str,
                  quantity=None, buy_price=None) -> None:
    with get_db().begin() as conn:
        conn.execute(
            text("""INSERT INTO watchlist (list_type, symbol, quantity, buy_price)
                    VALUES (:t, :s, :q, :p)
                    ON CONFLICT (list_type, symbol) DO UPDATE SET
                        quantity = EXCLUDED.quantity,
                        buy_price = EXCLUDED.buy_price"""),
            {"t": list_type, "s": symbol, "q": quantity, "p": buy_price},
        )


def watchlist_remove(list_type: str, symbol: str) -> None:
    with get_db().begin() as conn:
        conn.execute(
            text("DELETE FROM watchlist WHERE list_type = :t AND symbol = :s"),
            {"t": list_type, "s": symbol},
        )


def cap_category(mc_cr) -> str:
    if pd.isna(mc_cr):
        return "Unknown"
    for name, lo, hi in CAP_BANDS:
        if lo <= mc_cr < hi or (hi == float("inf") and mc_cr >= lo):
            return name
    return "Unknown"


UNKNOWN_SECTOR = "Unknown"


def with_sector(df: pd.DataFrame, sectors_df: pd.DataFrame) -> pd.DataFrame:
    """Adds NSE's sector label. Symbols outside NSE's index lists get "Unknown"
    — mostly small BE-series names, which NSE doesn't classify anywhere."""
    merged = df.merge(sectors_df, on="symbol", how="left")
    merged["sector"] = merged["sector"].fillna(UNKNOWN_SECTOR)
    return merged


def with_market_cap(df: pd.DataFrame, shares_df: pd.DataFrame) -> pd.DataFrame:
    """Adds market_cap_cr and cap_category, computed as shares_outstanding x close."""
    merged = df.merge(shares_df, on="symbol", how="left")
    merged["market_cap_cr"] = (merged["shares_outstanding"] * merged["close"] / 1e7).round(1)
    merged["cap_category"] = merged["market_cap_cr"].apply(cap_category)
    return merged


def with_period_returns(df: pd.DataFrame, as_of: str, all_dates: list[str]) -> pd.DataFrame:
    """Adds ret_1W ... ret_1Y, each vs the nearest trading day on or before
    (as_of - period). Periods with no history that far back come out NaN."""
    as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
    earliest = all_dates[0]

    ref: dict[str, str] = {}
    for label, days in RETURN_PERIODS:
        target = (as_of_date - timedelta(days=days)).isoformat()
        if target < earliest:
            continue  # not enough history stored for this window
        ref[label] = nearest_available(all_dates, target)

    closes = load_closes_on(tuple(sorted(set(ref.values()))))
    out = df.copy()
    for label, ref_date in ref.items():
        if closes.empty or ref_date not in closes.columns:
            continue
        base = out["symbol"].map(closes[ref_date])
        out[f"ret_{label}"] = ((out["close"] - base) / base * 100).round(2)
    return out


def with_52w(df: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """Adds high_52w, low_52w and off_high_pct (how far below the 52w high,
    as a negative number; 0 means sitting at the high)."""
    merged = df.merge(load_52w(as_of), on="symbol", how="left")
    merged["off_high_pct"] = (
        (merged["close"] - merged["high_52w"]) / merged["high_52w"] * 100
    ).round(2)
    return merged


def sector_options(df: pd.DataFrame) -> list[str]:
    """Sector names for a filter, with "Unknown" pushed to the end."""
    present = sorted(set(df["sector"].dropna()) - {UNKNOWN_SECTOR})
    return present + ([UNKNOWN_SECTOR] if (df["sector"] == UNKNOWN_SECTOR).any() else [])


def nearest_available(dates: list[str], target: str) -> str:
    """Latest date <= target, falling back to the earliest date if target is before everything."""
    earlier = [d for d in dates if d <= target]
    return earlier[-1] if earlier else dates[0]


POPULAR_INDICES = [
    "NIFTY 50", "SENSEX", "NIFTY BANK", "NIFTY IT", "NIFTY PHARMA",
    "NIFTY NEXT 50", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250", "NIFTY 100", "NIFTY 500",
]

# The logo is inlined as a data URI: Streamlit serves no static files from the
# app directory, so a plain <img src="assets/logo.jpg"> would 404.
@st.cache_data
def _logo_data_uri() -> str:
    import base64
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.jpg")
    try:
        with open(path, "rb") as f:
            return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    except OSError:
        return ""


_logo = _logo_data_uri()
_logo_html = (
    f'<div class="ac-logo-chip"><img src="{_logo}" alt="Armstrong Capital"></div>'
    if _logo else ""
)
st.markdown(f"""
<div class="ac-hero">
  {_logo_html}
  <div>
    <p class="ac-title">NSE Research Center</p>
    <p class="ac-subtitle">Every Stock, Every Close, Every Day &middot; EQ, BE and BZ series</p>
  </div>
  <span class="ac-badge">Internal</span>
</div>
""", unsafe_allow_html=True)

_last_fetched = load_last_fetched()
if _last_fetched:
    try:
        fetched_utc = datetime.strptime(_last_fetched, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=ZoneInfo("UTC")
        )
        st.caption(f"Last fetched {fetched_utc.astimezone(IST).strftime('%d %b %Y, %I:%M %p')} IST")
    except ValueError:
        pass

dates = available_dates()
if not dates:
    st.error(
        "No data found yet. Run `python scripts/refresh_symbol_master.py` then "
        "`python scripts/fetch_yahoo_prices.py` (or `backfill_history.py` for history) first."
    )
    st.stop()

min_date = datetime.strptime(dates[0], "%Y-%m-%d").date()
max_date = datetime.strptime(dates[-1], "%Y-%m-%d").date()
shares_df = load_shares()
sectors_df = load_sectors()

# ---- Popular index strip (always visible) ----
indices_df = load_indices()
if not indices_df.empty:
    latest_idx_date = indices_df["date"].max()
    today_idx = indices_df[indices_df["date"] == latest_idx_date].set_index("index")
    shown = [name for name in POPULAR_INDICES if name in today_idx.index]

    tiles_html = []
    for name in shown:
        row = today_idx.loc[name]
        chg = row["chg_pct"]
        if pd.notna(chg):
            direction = "nse-up" if chg > 0 else ("nse-down" if chg < 0 else "nse-flat")
            arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "")
            sign = "+" if chg > 0 else ""
            delta_html = f'<span class="nse-idx-delta {direction}">{arrow} {sign}{chg:.2f}%</span>'
        else:
            delta_html = ""
        tiles_html.append(
            f'<div class="nse-idx-tile"><div class="nse-idx-name">{name}</div>'
            f'<div><span class="nse-idx-value">{row["close"]:,.2f}</span>{delta_html}</div></div>'
        )
    st.markdown(f'<div class="nse-idx-row">{"".join(tiles_html)}</div>', unsafe_allow_html=True)
    st.caption(f"Popular indices as of {latest_idx_date} · full list under the \"All Indices\" tab")
    st.divider()

tab_stocks, tab_returns, tab_indices, tab_watchlist = st.tabs(
    ["Stocks", "Returns calculator", "All Indices", "Watchlist"]
)

# ============================================================ Tab 1: Stocks
with tab_stocks:
    selected_date = st.date_input(
        "Date", value=max_date, min_value=min_date, max_value=max_date, key="stocks_date",
    )
    selected_date_str = selected_date.strftime("%Y-%m-%d")
    if selected_date_str not in dates:
        fallback = nearest_available(dates, selected_date_str)
        st.caption(f"No trading data for {selected_date}; showing {fallback} instead.")
        selected_date_str = fallback

    df = load_day(selected_date_str)
    df = with_market_cap(df, shares_df)
    df = with_sector(df, sectors_df)
    as_of_for_history = df["date"].iloc[0] if not df.empty else selected_date_str
    df = with_period_returns(df, as_of_for_history, dates)
    df = with_52w(df, as_of_for_history)
    as_of = df["date"].iloc[0] if not df.empty else selected_date_str
    st.caption(f"Data as of {as_of} · Source: Yahoo Finance · {len(df):,} securities")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total securities", f"{len(df):,}")
    col2.metric("Advancers", int((df["chg_pct"] > 0).sum()))
    col3.metric("Decliners", int((df["chg_pct"] < 0).sum()))
    col4.metric("Unchanged", int((df["chg_pct"] == 0).sum()))

    st.divider()

    series_options = sorted(df["series"].dropna().unique().tolist())
    selected_series = st.multiselect("Series", series_options, default=series_options, key="stocks_series")
    selected_caps = st.multiselect("Market cap", CAP_CATEGORIES, default=CAP_CATEGORIES, key="stocks_cap")
    sector_opts = sector_options(df)
    selected_sectors = st.multiselect("Sector", sector_opts, default=sector_opts, key="stocks_sector")
    # Exact market-cap range, for when the four bands are too coarse (e.g.
    # "between 8,000 and 40,000 cr"). Blank means no limit in that direction.
    mc1, mc2 = st.columns(2)
    mc_min = mc1.number_input(
        "Min market cap (₹cr)", min_value=0.0, value=None, step=1000.0,
        placeholder="no minimum", key="stocks_mc_min",
    )
    mc_max = mc2.number_input(
        "Max market cap (₹cr)", min_value=0.0, value=None, step=1000.0,
        placeholder="no maximum", key="stocks_mc_max",
    )
    near_high = st.checkbox(
        "Only stocks within 5% of their 52-week high", value=False, key="stocks_near_high"
    )
    search = st.text_input("Search symbol or name", "", key="stocks_search")

    filtered = df[
        df["series"].isin(selected_series)
        & df["cap_category"].isin(selected_caps)
        & df["sector"].isin(selected_sectors)
    ]

    if mc_min is not None and mc_max is not None and mc_min > mc_max:
        st.warning("Min market cap is above max — no stocks can match.")
    # NaN fails both comparisons, so setting either bound drops the stocks with
    # no shares data. That's the right default: you asked for a cap range, and
    # we don't know theirs.
    if mc_min is not None:
        filtered = filtered[filtered["market_cap_cr"] >= mc_min]
    if mc_max is not None:
        filtered = filtered[filtered["market_cap_cr"] <= mc_max]
    if near_high:
        filtered = filtered[filtered["off_high_pct"] >= -5]
    if mc_min is not None or mc_max is not None:
        lo = f"₹{mc_min:,.0f}cr" if mc_min is not None else "any"
        hi = f"₹{mc_max:,.0f}cr" if mc_max is not None else "any"
        st.caption(
            f"Market cap between {lo} and {hi} — {len(filtered):,} stocks. "
            "Stocks with unknown market cap are excluded while a bound is set."
        )
    if search:
        mask = (
            filtered["symbol"].str.contains(search, case=False, na=False)
            | filtered["name"].str.contains(search, case=False, na=False)
        )
        filtered = filtered[mask]

    sort_col = st.selectbox(
        "Sort by",
        [c for c in ["chg_pct", "symbol", "close", "market_cap_cr",
                     "ret_1W", "ret_1M", "ret_3M", "ret_6M", "ret_1Y", "off_high_pct"]
         if c in filtered.columns],
        index=0, key="stocks_sort"
    )
    sort_desc = st.checkbox("Descending", value=True, key="stocks_sort_desc")
    filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    st.dataframe(
        filtered[[c for c in
                  ["symbol", "name", "series", "sector", "cap_category", "market_cap_cr",
                   "close", "prev_close", "chg_pct",
                   "ret_1W", "ret_1M", "ret_3M", "ret_6M", "ret_1Y",
                   "high_52w", "low_52w", "off_high_pct"]
                  if c in filtered.columns]]
        .rename(columns={
            "cap_category": "cap", "market_cap_cr": "mkt cap (₹cr)",
            "close": "current price", "prev_close": "previous close", "chg_pct": "chg %",
            "ret_1W": "1W %", "ret_1M": "1M %", "ret_3M": "3M %",
            "ret_6M": "6M %", "ret_1Y": "1Y %",
            "high_52w": "52w high", "low_52w": "52w low", "off_high_pct": "off high %",
        }),
        use_container_width=True, hide_index=True, height=600,
    )
    st.caption(
        "Market cap = shares outstanding × close (shares refreshed monthly, see "
        "`scripts/fetch_shares_outstanding.py`). Bands: Large > ₹20,000cr · "
        "Mid ₹5,000-20,000cr · Small ₹500-5,000cr · Micro < ₹500cr. \"Unknown\" = "
        "no shares data on Yahoo (mostly very illiquid names)."
    )

    st.divider()
    # Gainers/losers honour the filters above, so narrowing to one sector shows
    # that sector's movers rather than the whole market's.
    top_gainers = filtered.sort_values("chg_pct", ascending=False).head(10)
    top_losers = filtered.sort_values("chg_pct", ascending=True).head(10)
    gcol, lcol = st.columns(2)
    with gcol:
        st.subheader("Top gainers")
        st.dataframe(top_gainers[["symbol", "sector", "cap_category", "close", "chg_pct"]], hide_index=True, use_container_width=True)
    with lcol:
        st.subheader("Top losers")
        st.dataframe(top_losers[["symbol", "sector", "cap_category", "close", "chg_pct"]], hide_index=True, use_container_width=True)

# ==================================================== Tab 2: Returns calculator
with tab_returns:
    st.caption("Pick two dates to see each stock's return between them (e.g. Aug 1 → Aug 4).")
    c1, c2 = st.columns(2)
    ret_from = c1.date_input("From", value=min_date, min_value=min_date, max_value=max_date, key="ret_from")
    ret_to = c2.date_input("To", value=max_date, min_value=min_date, max_value=max_date, key="ret_to")

    if ret_from > ret_to:
        st.error("'From' date must be on or before 'To' date.")
    else:
        from_str = nearest_available(dates, ret_from.strftime("%Y-%m-%d"))
        to_str = nearest_available(dates, ret_to.strftime("%Y-%m-%d"))
        if from_str != ret_from.strftime("%Y-%m-%d") or to_str != ret_to.strftime("%Y-%m-%d"):
            st.caption(f"Using nearest trading days: {from_str} → {to_str}")

        df_start = load_day(from_str).set_index("symbol")
        df_end = load_day(to_str).set_index("symbol")
        common = df_start.index.intersection(df_end.index)

        ret_df = pd.DataFrame({
            "symbol": common,
            "name": df_end.loc[common, "name"].values,
            "series": df_end.loc[common, "series"].values,
            "close_start": df_start.loc[common, "close"].values,
            "close_end": df_end.loc[common, "close"].values,
        })
        ret_df["return_pct"] = round(
            (ret_df["close_end"] - ret_df["close_start"]) / ret_df["close_start"] * 100, 2
        )
        # Cap category uses the "to" date's market cap.
        ret_df = ret_df.merge(shares_df, on="symbol", how="left")
        ret_df["market_cap_cr"] = (ret_df["shares_outstanding"] * ret_df["close_end"] / 1e7).round(1)
        ret_df["cap_category"] = ret_df["market_cap_cr"].apply(cap_category)
        ret_df = with_sector(ret_df, sectors_df)

        # Yahoo's raw feed has occasional bad ticks for thinly-traded ETFs/funds
        # (e.g. a gold or index fund showing a ~100x jump between two days that
        # never happened) — checked "Adj Close" too, it has the same bad value,
        # so this isn't a stock-split adjustment issue, it's a genuine data
        # glitch. |return| > 300% is a generous cutoff (a real microcap can
        # legitimately rally hard) that still catches the clearly-broken ones.
        IMPLAUSIBLE_THRESHOLD = 300
        ret_df["implausible"] = ret_df["return_pct"].abs() > IMPLAUSIBLE_THRESHOLD

        st.caption(f"{len(ret_df):,} stocks with data on both dates")
        hide_implausible = st.checkbox(
            f"Hide implausible returns (>{IMPLAUSIBLE_THRESHOLD}% — usually bad data "
            "for thinly-traded ETFs/funds, not a real move)",
            value=True, key="ret_hide_implausible",
        )
        ret_clean = ret_df[~ret_df["implausible"]] if hide_implausible else ret_df
        if hide_implausible and ret_df["implausible"].any():
            st.caption(f"{ret_df['implausible'].sum()} stock(s) hidden as implausible.")

        series_opt = sorted(ret_df["series"].dropna().unique().tolist())
        sel_series = st.multiselect("Series", series_opt, default=series_opt, key="ret_series")
        sel_caps = st.multiselect("Market cap", CAP_CATEGORIES, default=CAP_CATEGORIES, key="ret_cap")
        ret_sector_opts = sector_options(ret_df)
        sel_sectors = st.multiselect("Sector", ret_sector_opts, default=ret_sector_opts, key="ret_sector")
        ret_search = st.text_input("Search symbol or name", "", key="ret_search")

        ret_filtered = ret_clean[
            ret_clean["series"].isin(sel_series)
            & ret_clean["cap_category"].isin(sel_caps)
            & ret_clean["sector"].isin(sel_sectors)
        ]
        if ret_search:
            mask = (
                ret_filtered["symbol"].str.contains(ret_search, case=False, na=False)
                | ret_filtered["name"].str.contains(ret_search, case=False, na=False)
            )
            ret_filtered = ret_filtered[mask]

        ret_filtered = ret_filtered.sort_values("return_pct", ascending=False)
        st.dataframe(
            ret_filtered[["symbol", "name", "series", "sector", "cap_category", "market_cap_cr",
                          "close_start", "close_end", "return_pct"]]
            .rename(columns={
                "cap_category": "cap", "market_cap_cr": "mkt cap (₹cr)",
                "close_start": f"close ({from_str})", "close_end": f"close ({to_str})",
            }),
            use_container_width=True, hide_index=True, height=500,
        )

        st.divider()
        top_g = ret_clean.sort_values("return_pct", ascending=False).head(10)
        top_l = ret_clean.sort_values("return_pct", ascending=True).head(10)
        gcol, lcol = st.columns(2)
        with gcol:
            st.subheader(f"Best returns ({from_str} → {to_str})")
            st.dataframe(top_g[["symbol", "series", "cap_category", "return_pct"]], hide_index=True, use_container_width=True)
        with lcol:
            st.subheader(f"Worst returns ({from_str} → {to_str})")
            st.dataframe(top_l[["symbol", "series", "cap_category", "return_pct"]], hide_index=True, use_container_width=True)

# ==================================================== Tab 3: All Indices
with tab_indices:
    if indices_df.empty:
        st.info("No index data yet. Run `python scripts/fetch_indices.py`.")
    else:
        st.caption("Every Indian market index found on Yahoo Finance (checked manually — yfinance has no index-listing API).")
        latest_idx_date = indices_df["date"].max()
        hist_counts = indices_df.groupby("index")["date"].nunique()
        summary = indices_df[indices_df["date"] == latest_idx_date].copy()
        summary["days_of_history"] = summary["index"].map(hist_counts)
        summary["has_history"] = summary["days_of_history"] > 1
        summary = summary.sort_values("index")
        st.dataframe(
            summary[["index", "close", "chg_pct", "has_history", "days_of_history"]]
            .rename(columns={"close": "level", "chg_pct": "day chg %", "days_of_history": "days of history"}),
            use_container_width=True, hide_index=True, height=400,
        )
        st.caption(
            "`has_history = False` means Yahoo only ever returns today's level for that index "
            "(no historical series) — it can't be used in the returns calculator below."
        )

        st.divider()
        st.subheader("Index returns between two dates")
        idx_with_history = sorted(hist_counts[hist_counts > 1].index.tolist())

        c1, c2 = st.columns(2)
        idx_from = c1.date_input("From", value=min_date, min_value=min_date, max_value=max_date, key="idx_from")
        idx_to = c2.date_input("To", value=max_date, min_value=min_date, max_value=max_date, key="idx_to")

        if idx_from > idx_to:
            st.error("'From' date must be on or before 'To' date.")
        else:
            idx_rows = []
            for name in idx_with_history:
                sub = indices_df[indices_df["index"] == name].sort_values("date")
                idx_dates = sub["date"].tolist()
                f_str = nearest_available(idx_dates, idx_from.strftime("%Y-%m-%d"))
                t_str = nearest_available(idx_dates, idx_to.strftime("%Y-%m-%d"))
                start_close = sub.loc[sub["date"] == f_str, "close"].iloc[0]
                end_close = sub.loc[sub["date"] == t_str, "close"].iloc[0]
                idx_rows.append({
                    "index": name,
                    "close_from": start_close,
                    "close_to": end_close,
                    "return_pct": round((end_close - start_close) / start_close * 100, 2),
                    "from_date": f_str, "to_date": t_str,
                })
            idx_ret_df = pd.DataFrame(idx_rows).sort_values("return_pct", ascending=False)
            st.dataframe(
                idx_ret_df[["index", "close_from", "close_to", "return_pct", "from_date", "to_date"]],
                use_container_width=True, hide_index=True, height=450,
            )

        st.divider()
        st.subheader("Chart a single index")
        chosen = st.selectbox("Index", idx_with_history, key="idx_choice")
        sub = indices_df[indices_df["index"] == chosen].sort_values("date")
        st.line_chart(sub.set_index("date")["close"])

# ==================================================== Tab 4: Watchlist
with tab_watchlist:
    today_df = with_sector(with_market_cap(load_day(dates[-1]), shares_df), sectors_df)
    sector_by_symbol = dict(zip(today_df["symbol"], today_df["sector"]))
    symbol_lookup = today_df.set_index("symbol")[["name", "series"]].to_dict("index")
    symbol_options = sorted(f"{s} — {info['name']}" for s, info in symbol_lookup.items())

    def _watchlist_table(holdings: dict) -> pd.DataFrame:
        symbols = list(holdings)
        rows = today_df[today_df["symbol"].isin(symbols)].copy()
        # Keep watchlist order rather than whatever order the merge produced.
        rows["_order"] = rows["symbol"].apply(symbols.index)
        rows = rows.sort_values("_order").drop(columns="_order")
        cols = ["symbol", "name", "sector", "cap_category", "market_cap_cr",
                "close", "chg_pct"]
        rows = rows[cols]

        qty = rows["symbol"].map(lambda s: holdings[s].get("quantity"))
        buy = rows["symbol"].map(lambda s: holdings[s].get("buy_price"))
        # Only show the portfolio columns once at least one holding is entered,
        # so a plain watchlist stays a plain watchlist.
        if qty.notna().any():
            rows["qty"] = qty
            rows["buy price"] = buy
            rows["invested"] = (qty * buy).round(2)
            rows["value"] = (qty * rows["close"]).round(2)
            rows["P&L"] = (rows["value"] - rows["invested"]).round(2)
            rows["P&L %"] = ((rows["value"] / rows["invested"] - 1) * 100).round(2)
            total_value = rows["value"].sum()
            if total_value:
                rows["weight %"] = (rows["value"] / total_value * 100).round(2)
        return rows

    # Personal list lives in the database (survives restarts, same on every
    # device). Common list stays per-visitor and temporary, as specified.
    def _get_holdings(state_key: str, persistent: bool) -> dict:
        if persistent:
            return watchlist_holdings(state_key)
        current = st.session_state.setdefault(state_key, {})
        if isinstance(current, list):  # older session shape
            current = {sym: {"quantity": None, "buy_price": None} for sym in current}
            st.session_state[state_key] = current
        return current

    def _get_symbols(state_key: str, persistent: bool) -> list[str]:
        return list(_get_holdings(state_key, persistent))

    def _render_watchlist(state_key: str, key_prefix: str, persistent: bool):
        holdings = _get_holdings(state_key, persistent)
        symbols = list(holdings)
        if not symbols:
            st.caption("Empty — add a stock below.")
            return
        table = _watchlist_table(holdings)
        opts = sector_options(table)
        if len(opts) > 1:
            chosen_sectors = st.multiselect(
                "Sector", opts, default=opts, key=f"{key_prefix}_sector"
            )
            table = table[table["sector"].isin(chosen_sectors)]
        st.dataframe(
            table.rename(columns={"cap_category": "cap", "market_cap_cr": "mkt cap (₹cr)"}),
            use_container_width=True, hide_index=True,
        )
        if not table.empty:
            by_sector = (table.groupby("sector")["chg_pct"].agg(["count", "mean"])
                         .rename(columns={"count": "stocks", "mean": "avg chg %"})
                         .round(2).sort_values("avg chg %", ascending=False))
            st.caption("By sector")
            st.dataframe(by_sector, use_container_width=True)

        if "value" in table.columns and table["value"].notna().any():
            invested = table["invested"].sum()
            value = table["value"].sum()
            pnl = value - invested
            m1, m2, m3 = st.columns(3)
            m1.metric("Invested", f"₹{invested:,.0f}")
            m2.metric("Current value", f"₹{value:,.0f}")
            m3.metric("P&L", f"₹{pnl:,.0f}",
                      f"{(value / invested - 1) * 100:.2f}%" if invested else None)

        with st.expander("Set quantity / buy price"):
            edit_sym = st.selectbox("Stock", symbols, key=f"{key_prefix}_hold_sym")
            cur = holdings.get(edit_sym, {})
            e1, e2 = st.columns(2)
            new_qty = e1.number_input(
                "Quantity", min_value=0.0, value=cur.get("quantity"),
                step=1.0, placeholder="not held", key=f"{key_prefix}_hold_qty",
            )
            new_price = e2.number_input(
                "Buy price (₹)", min_value=0.0, value=cur.get("buy_price"),
                step=1.0, placeholder="not set", key=f"{key_prefix}_hold_price",
            )
            if st.button("Save holding", key=f"{key_prefix}_hold_save"):
                if persistent:
                    watchlist_add(state_key, edit_sym, new_qty, new_price)
                else:
                    holdings[edit_sym] = {"quantity": new_qty, "buy_price": new_price}
                st.rerun()

        remove_sym = st.selectbox(
            "Remove a stock", ["—"] + symbols, key=f"{key_prefix}_remove_select"
        )
        if remove_sym != "—" and st.button("Remove", key=f"{key_prefix}_remove_btn"):
            if persistent:
                watchlist_remove(state_key, remove_sym)
            else:
                holdings.pop(remove_sym, None)
            st.rerun()

    def _add_stock_ui(state_key: str, key_prefix: str, persistent: bool):
        choice = st.selectbox(
            "Add a stock", ["—"] + symbol_options, key=f"{key_prefix}_add_select"
        )
        if choice != "—" and st.button("Add", key=f"{key_prefix}_add_btn"):
            symbol = choice.split(" — ")[0]
            if persistent:
                watchlist_add(state_key, symbol)
            else:
                holdings = _get_holdings(state_key, persistent)
                holdings.setdefault(symbol, {"quantity": None, "buy_price": None})
            st.rerun()

    def _watchlist_returns_calculator(state_key: str, key_prefix: str, persistent: bool):
        symbols = _get_symbols(state_key, persistent)
        st.markdown("**Returns calculator**")
        if not symbols:
            st.caption("Add stocks above to calculate returns.")
            return
        c1, c2 = st.columns(2)
        w_from = c1.date_input("From", value=min_date, min_value=min_date, max_value=max_date, key=f"{key_prefix}_ret_from")
        w_to = c2.date_input("To", value=max_date, min_value=min_date, max_value=max_date, key=f"{key_prefix}_ret_to")
        if w_from > w_to:
            st.error("'From' date must be on or before 'To' date.")
            return

        from_str = nearest_available(dates, w_from.strftime("%Y-%m-%d"))
        to_str = nearest_available(dates, w_to.strftime("%Y-%m-%d"))
        if from_str != w_from.strftime("%Y-%m-%d") or to_str != w_to.strftime("%Y-%m-%d"):
            st.caption(f"Using nearest trading days: {from_str} → {to_str}")

        df_start = load_day(from_str).set_index("symbol")
        df_end = load_day(to_str).set_index("symbol")
        present = [s for s in symbols if s in df_start.index and s in df_end.index]
        if not present:
            st.caption("None of these stocks have data on both dates.")
            return

        w_ret = pd.DataFrame({
            "symbol": present,
            "name": df_end.loc[present, "name"].values,
            "sector": [sector_by_symbol.get(sym, UNKNOWN_SECTOR) for sym in present],
            "close_start": df_start.loc[present, "close"].values,
            "close_end": df_end.loc[present, "close"].values,
        })
        w_ret["return_pct"] = round(
            (w_ret["close_end"] - w_ret["close_start"]) / w_ret["close_start"] * 100, 2
        )
        # Same data-glitch guard as the main Returns calculator tab.
        w_ret = w_ret[w_ret["return_pct"].abs() <= 300]
        w_ret = w_ret.sort_values("return_pct", ascending=False)

        st.dataframe(
            w_ret.rename(columns={
                "close_start": f"close ({from_str})", "close_end": f"close ({to_str})",
                "return_pct": "return %",
            }),
            use_container_width=True, hide_index=True,
        )

    st.subheader("Personal Watchlist")
    st.caption(
        "Password-protected and saved permanently in the database — it's the same "
        "list on every device and survives app restarts."
    )

    unlocked = st.session_state.setdefault("personal_watchlist_unlocked", False)
    if not unlocked:
        pw = st.text_input("Password", type="password", key="personal_watchlist_pw")
        if st.button("Unlock", key="personal_watchlist_unlock_btn"):
            if hashlib.sha256(pw.encode("utf-8")).hexdigest() == WATCHLIST_PASSWORD_HASH:
                st.session_state["personal_watchlist_unlocked"] = True
                st.rerun()
            else:
                st.error("Wrong password.")
    else:
        if st.button("Lock", key="personal_watchlist_lock_btn"):
            st.session_state["personal_watchlist_unlocked"] = False
            st.rerun()
        _render_watchlist("personal", "pw", persistent=True)
        _add_stock_ui("personal", "pw", persistent=True)
        st.divider()
        _watchlist_returns_calculator("personal", "pw", persistent=True)

    st.divider()
    st.subheader("Common Watchlist")
    st.caption(
        "Open to anyone — add a portfolio to check it right now. Nothing here is "
        "saved; it resets when the page reloads."
    )
    _render_watchlist("common_watchlist", "cw", persistent=False)
    _add_stock_ui("common_watchlist", "cw", persistent=False)
    st.divider()
    _watchlist_returns_calculator("common_watchlist", "cw", persistent=False)
