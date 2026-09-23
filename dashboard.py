"""
Base dashboard for NSE stocks — a first pass to verify the data pipeline works.
Layout/design will be revisited later; this just needs to show the data clearly.
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

IST = ZoneInfo("Asia/Kolkata")

ROOT = Path(__file__).resolve().parent
DAILY_DIR = ROOT / "data" / "daily"
LATEST_PATH = ROOT / "data" / "latest.csv"
INDICES_PATH = ROOT / "data" / "indices_history.csv"
SHARES_PATH = ROOT / "data" / "shares_outstanding.csv"
META_PATH = ROOT / "data" / "meta.json"

st.set_page_config(page_title="NSE Stocks Dashboard", layout="wide")

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


@st.cache_data(ttl=600)
def available_dates() -> list[str]:
    return sorted(p.stem for p in DAILY_DIR.glob("*.csv"))


@st.cache_data(ttl=600)
def load_day(date_str: str) -> pd.DataFrame:
    path = DAILY_DIR / f"{date_str}.csv"
    df = pd.read_csv(path)
    numeric_cols = ["open", "high", "low", "close", "prev_close", "chg_pct", "volume", "value"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=600)
def load_indices() -> pd.DataFrame:
    if not INDICES_PATH.exists():
        return pd.DataFrame(columns=["date", "index", "close", "chg_pct"])
    df = pd.read_csv(INDICES_PATH)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["chg_pct"] = pd.to_numeric(df["chg_pct"], errors="coerce")
    return df


@st.cache_data(ttl=600)
def load_shares() -> pd.DataFrame:
    if not SHARES_PATH.exists():
        return pd.DataFrame(columns=["symbol", "shares_outstanding"])
    df = pd.read_csv(SHARES_PATH)
    df["shares_outstanding"] = pd.to_numeric(df["shares_outstanding"], errors="coerce")
    return df


def cap_category(mc_cr) -> str:
    if pd.isna(mc_cr):
        return "Unknown"
    for name, lo, hi in CAP_BANDS:
        if lo <= mc_cr < hi or (hi == float("inf") and mc_cr >= lo):
            return name
    return "Unknown"


def with_market_cap(df: pd.DataFrame, shares_df: pd.DataFrame) -> pd.DataFrame:
    """Adds market_cap_cr and cap_category, computed as shares_outstanding x close."""
    merged = df.merge(shares_df, on="symbol", how="left")
    merged["market_cap_cr"] = (merged["shares_outstanding"] * merged["close"] / 1e7).round(1)
    merged["cap_category"] = merged["market_cap_cr"].apply(cap_category)
    return merged


def nearest_available(dates: list[str], target: str) -> str:
    """Latest date <= target, falling back to the earliest date if target is before everything."""
    earlier = [d for d in dates if d <= target]
    return earlier[-1] if earlier else dates[0]


POPULAR_INDICES = [
    "NIFTY 50", "SENSEX", "NIFTY BANK", "NIFTY IT", "NIFTY PHARMA",
    "NIFTY NEXT 50", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250", "NIFTY 100", "NIFTY 500",
]

st.title("NSE Stocks Dashboard")

if META_PATH.exists():
    try:
        fetched_utc = datetime.strptime(
            json.loads(META_PATH.read_text())["last_fetched_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=ZoneInfo("UTC"))
        st.caption(f"Last fetched {fetched_utc.astimezone(IST).strftime('%d %b %Y, %I:%M %p')} IST")
    except (KeyError, ValueError, json.JSONDecodeError):
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

# ---- Popular index strip (always visible) ----
indices_df = load_indices()
if not indices_df.empty:
    latest_idx_date = indices_df["date"].max()
    today_idx = indices_df[indices_df["date"] == latest_idx_date].set_index("index")
    shown = [name for name in POPULAR_INDICES if name in today_idx.index]
    idx_cols = st.columns(len(shown)) if shown else []
    for col, name in zip(idx_cols, shown):
        row = today_idx.loc[name]
        delta = f"{row['chg_pct']:+.2f}%" if pd.notna(row["chg_pct"]) else None
        col.metric(name.title(), f"{row['close']:,.2f}", delta)
    st.caption(f"Popular indices as of {latest_idx_date} · full list under the \"All Indices\" tab")
    st.divider()

tab_stocks, tab_returns, tab_indices = st.tabs(["Stocks", "Returns calculator", "All Indices"])

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
    search = st.text_input("Search symbol or name", "", key="stocks_search")

    filtered = df[df["series"].isin(selected_series) & df["cap_category"].isin(selected_caps)]
    if search:
        mask = (
            filtered["symbol"].str.contains(search, case=False, na=False)
            | filtered["name"].str.contains(search, case=False, na=False)
        )
        filtered = filtered[mask]

    sort_col = st.selectbox(
        "Sort by", ["chg_pct", "symbol", "close", "volume", "value", "market_cap_cr"], index=0, key="stocks_sort"
    )
    sort_desc = st.checkbox("Descending", value=True, key="stocks_sort_desc")
    filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    st.dataframe(
        filtered[["symbol", "name", "series", "cap_category", "market_cap_cr", "open", "high", "low",
                  "close", "prev_close", "chg_pct", "volume", "value"]]
        .rename(columns={"cap_category": "cap", "market_cap_cr": "mkt cap (₹cr)"}),
        use_container_width=True, hide_index=True, height=600,
    )
    st.caption(
        "Market cap = shares outstanding × close (shares refreshed monthly, see "
        "`scripts/fetch_shares_outstanding.py`). Bands: Large > ₹20,000cr · "
        "Mid ₹5,000-20,000cr · Small ₹500-5,000cr · Micro < ₹500cr. \"Unknown\" = "
        "no shares data on Yahoo (mostly very illiquid names)."
    )

    st.divider()
    top_gainers = df.sort_values("chg_pct", ascending=False).head(10)
    top_losers = df.sort_values("chg_pct", ascending=True).head(10)
    gcol, lcol = st.columns(2)
    with gcol:
        st.subheader("Top gainers")
        st.dataframe(top_gainers[["symbol", "series", "cap_category", "close", "chg_pct"]], hide_index=True, use_container_width=True)
    with lcol:
        st.subheader("Top losers")
        st.dataframe(top_losers[["symbol", "series", "cap_category", "close", "chg_pct"]], hide_index=True, use_container_width=True)

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

        st.caption(f"{len(ret_df):,} stocks with data on both dates")

        series_opt = sorted(ret_df["series"].dropna().unique().tolist())
        sel_series = st.multiselect("Series", series_opt, default=series_opt, key="ret_series")
        sel_caps = st.multiselect("Market cap", CAP_CATEGORIES, default=CAP_CATEGORIES, key="ret_cap")
        ret_search = st.text_input("Search symbol or name", "", key="ret_search")

        ret_filtered = ret_df[ret_df["series"].isin(sel_series) & ret_df["cap_category"].isin(sel_caps)]
        if ret_search:
            mask = (
                ret_filtered["symbol"].str.contains(ret_search, case=False, na=False)
                | ret_filtered["name"].str.contains(ret_search, case=False, na=False)
            )
            ret_filtered = ret_filtered[mask]

        ret_filtered = ret_filtered.sort_values("return_pct", ascending=False)
        st.dataframe(
            ret_filtered[["symbol", "name", "series", "cap_category", "market_cap_cr",
                          "close_start", "close_end", "return_pct"]]
            .rename(columns={
                "cap_category": "cap", "market_cap_cr": "mkt cap (₹cr)",
                "close_start": f"close ({from_str})", "close_end": f"close ({to_str})",
            }),
            use_container_width=True, hide_index=True, height=500,
        )

        st.divider()
        top_g = ret_df.sort_values("return_pct", ascending=False).head(10)
        top_l = ret_df.sort_values("return_pct", ascending=True).head(10)
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
