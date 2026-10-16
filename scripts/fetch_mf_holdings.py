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

class RateLimited(RuntimeError):
    """The API asked us to slow down."""


# Four, not eight: the API rate-limits at bulk volume and extra parallelism
# only buys more 429s.
DEFAULT_WORKERS = 4
RATE_LIMIT_RETRIES = 6
RATE_LIMIT_BACKOFF = 20    # seconds, multiplied by the attempt number
REQUEST_TIMEOUT = 30
RETRIES = 2
RETRY_BACKOFF = 2
PAUSE_EVERY = 500          # requests
PAUSE_SECONDS = 1
PROGRESS_EVERY = 250

MONTH_NAMES = [m.lower() for m in calendar.month_name[1:]]


def api_key() -> str:
    key = os.environ.get("ADVISORKHOJ_KEY")
    if not key:
        print("ERROR: ADVISORKHOJ_KEY is not set. Put the Advisorkhoj API key "
              "in that environment variable (a GitHub secret in CI).")
        sys.exit(1)
    return key


def get_json(url: str, params: dict) -> dict:
    """GET with backoff. 429 is handled separately and patiently.

    Advisorkhoj rate-limits hard at bulk volume — a six-month run across ~1,160
    schemes is ~7,000 requests and gets 429s partway. Treating that like any
    other error just burns the remaining attempts and loses the scheme, so a
    429 waits much longer and gets more tries.
    """
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = RATE_LIMIT_BACKOFF * attempt
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = max(wait, int(retry_after))
                last = "HTTP 429 (rate limited)"
                if attempt < RATE_LIMIT_RETRIES:
                    time.sleep(wait)
                    continue
                raise RateLimited(f"{url}: still rate limited after "
                                  f"{RATE_LIMIT_RETRIES} attempts")
            last = f"HTTP {resp.status_code}"
        except RateLimited:
            raise
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


def month_end(year: int, month_name: str) -> str:
    """The date a portfolio for that month is published under (its last day)."""
    m = MONTH_NAMES.index(month_name) + 1
    return f"{year:04d}-{m:02d}-{calendar.monthrange(year, m)[1]:02d}"


def already_stored(engine, dates: list[str]) -> set:
    """(scheme_id, portfolio_date) pairs we already hold, so a re-run resumes
    instead of refetching everything. A fetch this long will be interrupted."""
    from sqlalchemy import text
    if not dates:
        return set()
    names = [f"d{i}" for i in range(len(dates))]
    ph = ", ".join(f":{n}" for n in names)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""SELECT scheme_id, portfolio_date::text FROM mf_holdings
                     WHERE portfolio_date IN ({ph})"""),
            dict(zip(names, dates))).fetchall()
    return {(r[0], r[1]) for r in rows}


def parse_date(ddmmyyyy: str) -> str | None:
    try:
        d, m, y = ddmmyyyy.split("-")
        return f"{y}-{m}-{d}"
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6,
                    help="how many months WITH DATA to collect (default 6)")
    ap.add_argument("--max-lookback", type=int, default=14,
                    help="how far back to look for them (default 14 months)")
    ap.add_argument("--categories", nargs="*", default=None,
                    help="specific categories instead of all equity-ish ones")
    ap.add_argument("--limit-schemes", type=int, default=None,
                    help="only the first N schemes, for a quick test run")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help=f"parallel requests (default {DEFAULT_WORKERS})")
    ap.add_argument("--refetch", action="store_true",
                    help="refetch scheme-months already stored")
    args = ap.parse_args()

    key = api_key()
    engine = get_engine()
    wait_for_db(engine)
    create_schema(engine)

    schemes = list_schemes(key, args.categories)
    if args.limit_schemes:
        schemes = schemes[:args.limit_schemes]
    # Candidates, not targets: portfolios publish in arrears, so the most
    # recent complete month usually has nothing yet. Mid-October asked every
    # scheme for September, got status 400 from all of them, and stored
    # nothing while reporting success. Walk back until --months months have
    # actually produced data.
    months = recent_months(args.max_lookback)
    print(f"{len(schemes):,} schemes. Collecting {args.months} month(s) "
          f"with published data, looking back as far as "
          f"{months[-1][1]} {months[-1][0]}.")
    print()

    # --- scheme ids -------------------------------------------------------
    bulk_upsert(engine, "mf_schemes", ["scheme", "category"],
                [{"scheme": s, "category": c} for s, c in schemes],
                conflict_cols=["scheme"], update_cols=["category"])
    from sqlalchemy import text
    with engine.connect() as conn:
        scheme_ids = dict(conn.execute(
            text("SELECT scheme, scheme_id FROM mf_schemes")).fetchall())

    # --- fetch, one month at a time ---------------------------------------
    # Written per month rather than once at the end: this runs for a long time
    # against a throttled API, and an interruption used to discard everything.
    month_dates = [month_end(y, m) for y, m in months]
    stored = set() if args.refetch else already_stored(engine, month_dates)
    if stored:
        print(f"{len(stored):,} scheme-months already stored, skipping those.\n")

    grand_holdings = grand_failed = 0
    months_seen: list[str] = []

    for (year, month_name), pdate in zip(months, month_dates):
        if len(months_seen) >= args.months:
            break
        todo = [s for s, _c in schemes
                if args.refetch or (scheme_ids.get(s), pdate) not in stored]
        if not todo:
            print(f"{month_name} {year}: already stored, counts towards the target")
            months_seen.append(pdate)
            continue

        print(f"{month_name} {year} ({pdate}): {len(todo):,} schemes to fetch")
        holdings: list[dict] = []
        instruments: dict[str, dict] = {}
        done = empty = failed = 0

        def work(scheme):
            try:
                return scheme, fetch_portfolio(key, scheme, year, month_name), None
            except Exception as e:
                return scheme, [], e

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for scheme, rows, err in pool.map(work, todo):
                done += 1
                if err is not None:
                    failed += 1
                elif not rows:
                    empty += 1
                for r in rows:
                    rdate = parse_date(r.get("portfolio_date") or "")
                    isin = (r.get("isin") or "").strip()
                    sid = scheme_ids.get(scheme)
                    if not (rdate and isin and sid):
                        continue
                    holdings.append({
                        "portfolio_date": rdate, "scheme_id": sid, "isin": isin,
                        "value_lakh": r.get("value"),
                        "holdings_pct": r.get("holdings"),
                    })
                    instruments.setdefault(isin, {
                        "isin": isin, "instrument": r.get("instrument"),
                        "sector": r.get("sector"), "industry": r.get("industry"),
                    })
                if done % PROGRESS_EVERY == 0:
                    print(f"    {done:,}/{len(todo):,} schemes, "
                          f"{len(holdings):,} holdings", flush=True)
                if done % PAUSE_EVERY == 0:
                    time.sleep(PAUSE_SECONDS)

        if not holdings:
            # Normal for a recent month: nothing published yet. Not an error,
            # and it does not count towards the target.
            print(f"  nothing published for {month_name} {year} "
                  f"({failed:,} request failures) - looking further back")
            grand_failed += failed
            continue

        # The same (date, scheme, isin) can appear twice when a fund lists a
        # stock under two descriptions; Postgres refuses an upsert touching one
        # row twice, so collapse before writing.
        deduped = {(h["portfolio_date"], h["scheme_id"], h["isin"]): h
                   for h in holdings}
        bulk_upsert(engine, "mf_instruments",
                    ["isin", "instrument", "sector", "industry"],
                    list(instruments.values()), conflict_cols=["isin"],
                    update_cols=["instrument", "sector", "industry"])
        bulk_upsert(engine, "mf_holdings",
                    ["portfolio_date", "scheme_id", "isin", "value_lakh",
                     "holdings_pct"],
                    list(deduped.values()),
                    conflict_cols=["portfolio_date", "scheme_id", "isin"],
                    update_cols=["value_lakh", "holdings_pct"])
        grand_holdings += len(deduped)
        grand_failed += failed
        months_seen.append(pdate)
        print(f"  wrote {len(deduped):,} holdings "
              f"({done - empty - failed:,} schemes reported, {empty:,} had no "
              f"portfolio, {failed:,} failed)\n", flush=True)

    if not months_seen:
        print("Nothing new fetched.")
        return 0

    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text(
            """INSERT INTO meta (key, value) VALUES ('mf_holdings_updated_utc', :v)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""),
            {"v": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    print(f"Wrote {grand_holdings:,} holdings across {len(months_seen)} "
          f"month-end(s): {', '.join(months_seen)}")
    if grand_failed:
        print(f"{grand_failed:,} requests failed. Re-run to pick them up — "
              f"months already stored are skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
