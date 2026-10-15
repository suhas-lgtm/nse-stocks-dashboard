"""
Mutual fund equity holdings, month by month, from Advisorkhoj.

Fund houses publish a full portfolio once a month. Comparing consecutive
months shows what fund managers actually bought and sold — which is a very
different signal from price action, and the reason this exists.

Only asset_class == "Equity" rows are stored. Debt and cash holdings are real
but irrelevant to a stocks dashboard, and skipping them cuts the row count by
roughly a third.

Holdings join to the NSE universe on ISIN, which data/symbol_master.csv
already carries — that is what turns "HDFC BANK LIMITED EQ NEW FV RE. 1/-"
into HDFCBANK.

Needs ADVISORKHOJ_KEY in the environment (a GitHub secret in CI).

    python scripts/fetch_mf_holdings.py                  # last 6 months
    python scripts/fetch_mf_holdings.py --months 12
    python scripts/fetch_mf_holdings.py --categories "Equity: Flexi Cap"
    python scripts/fetch_mf_holdings.py --months 1 --limit-schemes 20   # smoke run
"""

import argparse
import calendar
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests

from db import bulk_upsert, create_schema, get_engine, wait_for_db

BASE = "https://mfapi.advisorkhoj.com/"
CATEGORIES_URL = BASE + "getAllSchemeCategories"
SCHEMES_URL = BASE + "getAllSchemesCommonNamesbyCategory"
PORTFOLIO_URL = BASE + "getMutualfundHistoricalPortfolio"

# Categories whose portfolios are mostly listed Indian equity. Debt and
# overseas funds hold little or nothing that maps to an NSE symbol.
CATEGORY_PREFIXES = ("Equity", "Hybrid", "ELSS", "Index", "Retirement",
                     "Childrens", "Fund of Funds-Domestic-Equity")

WORKERS = 8
REQUEST_TIMEOUT = 60
RETRIES = 3
RETRY_BACKOFF = 5
PAUSE_EVERY = 200          # requests
PAUSE_SECONDS = 2

MONTH_NAMES = [m.lower() for m in calendar.month_name[1:]]


def api_key() -> str:
    key = os.environ.get("ADVISORKHOJ_KEY")
    if not key:
        print("ERROR: ADVISORKHOJ_KEY is not set. Put the Advisorkhoj API key "
              "in that environment variable (a GitHub secret in CI).")
        sys.exit(1)
    return key


def get_json(url: str, params: dict) -> dict:
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            last = f"HTTP {resp.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
    raise RuntimeError(f"{url} failed after {RETRIES} attempts: {last}")


def recent_months(n: int) -> list[tuple[int, str]]:
    """The n most recent complete months, newest first.

    The current month is skipped: a portfolio is published after the month
    ends, so asking for it only ever returns status 400.
    """
    today = date.today()
    year, month = today.year, today.month
    out = []
    for _ in range(n):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        out.append((year, MONTH_NAMES[month - 1]))
    return out


def list_schemes(key: str, categories: list[str] | None) -> list[tuple[str, str]]:
    """[(scheme, category)] for every category we care about."""
    if categories is None:
        all_cats = get_json(CATEGORIES_URL, {"key": key}).get("list") or []
        categories = [c for c in all_cats if c.startswith(CATEGORY_PREFIXES)]
        print(f"{len(categories)} of {len(all_cats)} categories selected")

    pairs: list[tuple[str, str]] = []
    seen = set()
    for cat in categories:
        data = get_json(SCHEMES_URL, {"key": key, "category": cat})
        for name in data.get("list") or []:
            if name not in seen:
                seen.add(name)
                pairs.append((name, cat))
    return pairs


def fetch_portfolio(key: str, scheme: str, year: int, month: str) -> list[dict]:
    """Equity holdings for one scheme-month, or [] when none is published."""
    data = get_json(PORTFOLIO_URL, {
        "scheme_amfi_common": scheme, "year": year, "month": month, "key": key})
    # status 400 with an empty list simply means "no portfolio for that month",
    # which is normal for recent months and for funds that launched later.
    rows = data.get("historicalSchemePortfolioList") or []
    return [r for r in rows if (r.get("asset_class") or "") == "Equity"]


def parse_date(ddmmyyyy: str) -> str | None:
    try:
        d, m, y = ddmmyyyy.split("-")
        return f"{y}-{m}-{d}"
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6,
                    help="how many complete months back to fetch (default 6)")
    ap.add_argument("--categories", nargs="*", default=None,
                    help="specific categories instead of all equity-ish ones")
    ap.add_argument("--limit-schemes", type=int, default=None,
                    help="only the first N schemes, for a quick test run")
    args = ap.parse_args()

    key = api_key()
    engine = get_engine()
    wait_for_db(engine)
    create_schema(engine)

    schemes = list_schemes(key, args.categories)
    if args.limit_schemes:
        schemes = schemes[:args.limit_schemes]
    months = recent_months(args.months)
    print(f"{len(schemes):,} schemes x {len(months)} months "
          f"({months[-1][1]} {months[-1][0]} to {months[0][1]} {months[0][0]}) "
          f"= {len(schemes) * len(months):,} requests\n")

    # --- scheme ids -------------------------------------------------------
    bulk_upsert(engine, "mf_schemes", ["scheme", "category"],
                [{"scheme": s, "category": c} for s, c in schemes],
                conflict_cols=["scheme"], update_cols=["category"])
    from sqlalchemy import text
    with engine.connect() as conn:
        scheme_ids = dict(conn.execute(
            text("SELECT scheme, scheme_id FROM mf_schemes")).fetchall())

    # --- fetch ------------------------------------------------------------
    jobs = [(s, y, m) for (s, _c) in schemes for (y, m) in months]
    holdings: list[dict] = []
    instruments: dict[str, dict] = {}
    done = empty = failed = 0

    def work(job):
        scheme, year, month = job
        try:
            return scheme, fetch_portfolio(key, scheme, year, month), None
        except Exception as e:
            return scheme, [], e

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for scheme, rows, err in pool.map(work, jobs):
            done += 1
            if err is not None:
                failed += 1
            elif not rows:
                empty += 1
            for r in rows:
                pdate = parse_date(r.get("portfolio_date") or "")
                isin = (r.get("isin") or "").strip()
                sid = scheme_ids.get(scheme)
                if not (pdate and isin and sid):
                    continue
                holdings.append({
                    "portfolio_date": pdate, "scheme_id": sid, "isin": isin,
                    "value_lakh": r.get("value"),
                    "holdings_pct": r.get("holdings"),
                })
                instruments.setdefault(isin, {
                    "isin": isin, "instrument": r.get("instrument"),
                    "sector": r.get("sector"), "industry": r.get("industry"),
                })
            if done % PAUSE_EVERY == 0:
                print(f"  {done:,}/{len(jobs):,} requests, "
                      f"{len(holdings):,} holdings so far", flush=True)
                time.sleep(PAUSE_SECONDS)

    print(f"\n{done:,} requests: {done - empty - failed:,} with data, "
          f"{empty:,} with no published portfolio, {failed:,} failed")

    if not holdings:
        print("ERROR: no holdings fetched — not writing anything.")
        return 1

    # Same (date, scheme, isin) can appear twice when a fund lists a stock
    # under two instrument descriptions; Postgres refuses an upsert that
    # touches one row twice, so collapse first.
    deduped = {(h["portfolio_date"], h["scheme_id"], h["isin"]): h
               for h in holdings}

    bulk_upsert(engine, "mf_instruments",
                ["isin", "instrument", "sector", "industry"],
                list(instruments.values()), conflict_cols=["isin"],
                update_cols=["instrument", "sector", "industry"])
    bulk_upsert(engine, "mf_holdings",
                ["portfolio_date", "scheme_id", "isin", "value_lakh", "holdings_pct"],
                list(deduped.values()),
                conflict_cols=["portfolio_date", "scheme_id", "isin"],
                update_cols=["value_lakh", "holdings_pct"])

    with engine.begin() as conn:
        conn.execute(text(
            """INSERT INTO meta (key, value) VALUES ('mf_holdings_updated_utc', :v)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""),
            {"v": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    months_seen = sorted({h["portfolio_date"] for h in deduped.values()})
    print(f"\nWrote {len(deduped):,} holdings across {len(instruments):,} stocks "
          f"and {len(months_seen)} month-ends ({months_seen[0]} to {months_seen[-1]}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
