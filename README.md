# NSE Stocks Dashboard

Daily-refreshed data and a base dashboard for NSE main-board stocks (EQ, BE, BZ
series), built the same way as the existing US ETF/Yahoo Finance dashboard.

## Data source

**Yahoo Finance** (`.NS` tickers), via the `yfinance` library — same source as
the US ETF dashboard.

Yahoo has no "list all NSE stocks" API, so the list of symbols to track still
comes from NSE's own daily Bhavcopy (`scripts/refresh_symbol_master.py`) — but
that only needs to run occasionally (new listings are infrequent), not every
night. **The nightly job never talks to NSE**; it only reads the symbol list
and pulls prices from Yahoo.

Yahoo Finance does **not** carry NSE Emerge (SME) stocks — series SM and ST
are therefore not included. Tracked series:

| Series | Meaning |
|---|---|
| EQ | Regular equity |
| BE | Trade-to-trade equity (must take delivery, no intraday) |
| BZ | Trade-to-trade, Z-group (surveillance) |

## Where the data lives

Prices, indices, share counts and watchlists live in **Postgres** (a free Neon
database), not in this repo. The connection string is supplied as
`DATABASE_URL` — a GitHub Actions secret for the fetch jobs, and a Streamlit
secret for the app. It is never committed.

This replaced an earlier setup that committed CSVs to git on every fetch. That
worked, but every commit triggered a Streamlit redeploy, which restarted the
app and wiped in-memory state (watchlists) twice a day, and the repo grew
~70MB/year. With the database, fresh data appears without any redeploy.

| Table | Holds |
|---|---|
| `daily_prices` | one row per (date, symbol) — OHLC, change %, volume |
| `indices_history` | one row per (date, index) — level and day change % |
| `shares_outstanding` | share count per symbol, for market cap |
| `watchlist` | the Personal watchlist, persisted across devices/restarts |
| `meta` | `last_fetched_utc`, shown in the app header |

The CSVs under `data/` are the pre-migration snapshot. They're no longer read
or written — `scripts/migrate_csv_to_db.py` loaded them into Postgres once.
`data/symbol_master.csv` is the exception: it stays a file (small, changes
monthly) because the price fetch reads it to know which symbols to pull.

## Layout

```
scripts/db.py                        # engine, schema, and the batched upsert helper
scripts/migrate_csv_to_db.py         # one-time: loaded the existing CSVs into Postgres
scripts/refresh_symbol_master.py     # monthly: rebuilds data/symbol_master.csv from NSE
scripts/fetch_yahoo_prices.py        # 4pm + 11pm: pulls prices from Yahoo -> daily_prices
scripts/fetch_indices.py             # 4pm + 11pm: pulls 26 Indian indices -> indices_history
scripts/fetch_shares_outstanding.py  # monthly: share counts -> shares_outstanding
scripts/backfill_history.py          # one-time: ~1y of history for every stock
dashboard.py                         # Streamlit app: Stocks / Returns / All Indices / Watchlist
.github/workflows/nightly_fetch.yml           # 4pm + 11pm IST price + index refresh
.github/workflows/monthly_symbol_refresh.yml  # monthly symbol list + share counts
```

### A note on bulk writes

`db.bulk_upsert` exists because the obvious approach is a trap: SQLAlchemy's
`executemany` sends one round trip per row. Against a US-hosted database that's
~250ms each, so a 2,900-row nightly write took **14 minutes**. Batching the rows
with `psycopg2.extras.execute_values` brought the same write down to **16
seconds**. Use that helper for any multi-row write.

### Historical dates, returns calculator & indices

`dashboard.py` has three tabs:

- **Stocks** — date picker (any date with an archived file in `data/daily/`)
  showing that day's full table, filters, and top gainers/losers.
- **Returns calculator** — pick two dates (e.g. Aug 1 → Aug 4) and see every
  stock's % return between them, sortable, with best/worst-return panels.
- **All Indices** — every Indian index found on Yahoo Finance (26 total,
  checked by hand — there's no index-listing API): a summary table, a
  returns calculator (same two-date UX as the stock one, but one sortable
  table across all indices with history at once), and a single-index chart
  picker below. A "popular" strip of 10 well-known indices (NIFTY 50, Sensex,
  Bank, IT, Pharma, Next 50, Midcap 150, Smallcap 250, 100, 500) sits at the
  top of every tab for a quick glance.

The date range only goes as far back as `data/daily/` has files — run
`scripts/backfill_history.py` once (takes ~10-15 min for ~2,900 symbols) to
populate roughly the last year; after that, each nightly run adds one more
day automatically.

About half the 26 indices (mostly the narrower `^CNXxxx` sector ones — Auto,
FMCG, Metal, Realty, Energy, PSU Bank, PSE, Infra, Media, Fin Service,
Commodities, Consumption, Services Sector) only ever return **today's**
level on Yahoo, never history — so they can't be charted or used in the
returns calculator, only shown as a current snapshot. This is a Yahoo data
gap, not something the pipeline can fix.

### Market cap / equity value

Yahoo Finance does have market cap, but not through the same bulk endpoint as
prices — it's a separate per-stock lookup (`fast_info`), so it can't be
folded into the nightly price fetch without ~2,900 extra individual requests.
Since shares outstanding barely changes day to day (only on buybacks, splits,
new issues), the efficient approach is: fetch shares outstanding occasionally
(`scripts/fetch_shares_outstanding.py`, ~5 minutes for the full universe, runs
monthly alongside the symbol master refresh) and compute
`market_cap = shares_outstanding × close` at read time using whichever day's
price you already have — no daily re-fetch needed. Coverage: 2,450/2,922
stocks (84%) have shares data on Yahoo; the rest (mostly very illiquid names,
e.g. `AARNAV`) show as "Unknown" cap.

This is now wired into both the Stocks and Returns Calculator tabs as a
"Mkt Cap (₹cr)" column, a "Cap" category column, and a filter. Bands used
(as specified, not SEBI's official large/mid/small-cap ranking which is
based on rank order among all listed companies, not fixed rupee cutoffs):

| Category | Market cap |
|---|---|
| Large | > ₹20,000 cr |
| Mid | ₹5,000 – 20,000 cr |
| Small | ₹500 – 5,000 cr |
| Micro | < ₹500 cr |
| Unknown | no shares data on Yahoo |

Note: `value` in the price data is an approximation (`close × volume`), not
NSE's official traded turnover — Yahoo doesn't expose that figure.

### Why the fetch script looks defensive

Yahoo Finance has a known failure mode: its crumb/cookie session can break
mid-run, after which *every* request fails instantly, even for stocks like
RELIANCE or TCS — yfinance reports this identically to a real delisting, so a
naive script would silently commit an empty or badly wrong snapshot some
nights. `fetch_yahoo_prices.py` guards against this:

- Checks 6 always-listed "canary" stocks first; aborts (writing nothing) if
  they fail, since that means the whole session is broken, not that 2,900
  stocks got delisted overnight.
- Fetches in batches of 50 with pauses between them (large single requests to
  ~3,000 tickers were the trigger for the session breaking during testing).
- Retries any batch with an abnormally high failure rate.
- Aborts the whole run (no files touched, non-zero exit so GitHub Actions
  shows it as failed) if more than 3% of the universe is still missing after
  retries.

In testing, a full run took about 13 minutes and returned 2,921/2,922 symbols
(only one genuine gap). A failed run does not overwrite yesterday's data —
worst case the dashboard is a day stale, not wrong.

## Running locally

```
python -m pip install -r requirements.txt
python scripts/refresh_symbol_master.py   # one-time: build the symbol list
python scripts/fetch_yahoo_prices.py      # fetch today's prices
python scripts/fetch_indices.py           # fetch index levels (NIFTY 50, Bank, ...)
python scripts/backfill_history.py        # one-time: ~1y of history, for the date picker (~10-15 min)
streamlit run dashboard.py                # view the dashboard at localhost:8501
```

## Automated nightly refresh

`.github/workflows/nightly_fetch.yml` runs on GitHub Actions every night at
23:00 IST (17:30 UTC), pulls that day's prices and index levels from Yahoo
Finance for every symbol in `data/symbol_master.csv`, and commits the new
data back into `data/`. On weekends/holidays Yahoo simply returns no new bar
and nothing changes, so it exits quietly.

`.github/workflows/monthly_symbol_refresh.yml` runs once a month to catch new
NSE listings/delistings (`data/symbol_master.csv`, from NSE's bhavcopy — the
only workflow that talks to NSE) and refresh shares outstanding
(`data/shares_outstanding.csv`, from Yahoo).

Both also have a manual "Run workflow" button on GitHub (Actions tab).

This requires the repo to be pushed to GitHub — see next steps below.

## Deploying the dashboard online (free)

1. Push this repo to GitHub (see "Next steps").
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub.
3. "New app" → pick this repo → main file path `dashboard.py` → Deploy.
4. Because the nightly GitHub Action commits new data to the repo, Streamlit
   Community Cloud auto-redeploys with the new data each time it detects a
   push — no extra steps needed after the first deploy.

## Next steps (not done yet)

1. Create a GitHub account (any email) and a **public** repo (Streamlit
   Community Cloud's free tier needs a public repo) named e.g.
   `nse-stocks-dashboard`.
2. Share the repo URL — the project will be pushed to it and the Actions
   workflows will start running automatically on the schedules above.
3. Deploy on Streamlit Community Cloud per the steps above.
4. Dashboard design/features beyond this base version (styling, charts,
   sector data, historical trends) — later, as planned.

## Note on the claude.ai preview link vs. this repo

The preview artifact shared during development uses a curated subset (top 750
stocks by turnover, ~10.7MB) for historical dates/returns, not the full
2,921-stock universe — a static page has a hard size ceiling that the full
year of full-market history exceeds. `dashboard.py` in this repo has no such
limit; once hosted, its date picker and returns calculator cover every
tracked stock, every date.
