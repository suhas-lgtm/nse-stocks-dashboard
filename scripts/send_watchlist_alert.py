"""
WhatsApp alert for big movers in the personal watchlist.

Runs after the price fetch. Looks at the latest stored trading day, picks the
personal-watchlist stocks that moved at least ALERT_THRESHOLD_PCT in either
direction, and sends one WhatsApp message via CallMeBot.

Environment:
    DATABASE_URL          (required)
    CALLMEBOT_PHONE       (required) e.g. +919876543210
    CALLMEBOT_APIKEY      (required) from the CallMeBot signup WhatsApp reply
    ALERT_THRESHOLD_PCT   (optional) default 3.0

Deliberately forgiving: a missing configuration or an empty watchlist is not an
error, it just skips. The job's real work is collecting prices, and a messaging
problem should never make that look like it failed.

Usage:
    python scripts/send_watchlist_alert.py
"""

import os
import sys
import urllib.parse

import requests
from sqlalchemy import text

from db import get_engine

CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
DEFAULT_THRESHOLD = 3.0
MAX_LINES = 25  # keep the message readable if the whole list moves at once


def fetch_movers(engine, threshold: float):
    """(trading_date, [(symbol, close, chg_pct), ...]) sorted by biggest move."""
    sql = text("""
        WITH latest AS (SELECT max(date) AS d FROM daily_prices)
        SELECT d.date::text, d.symbol, d.close, d.chg_pct
        FROM daily_prices d, latest
        WHERE d.date = latest.d
          AND d.chg_pct IS NOT NULL
          AND abs(d.chg_pct) >= :threshold
          AND d.symbol IN (
              SELECT symbol FROM watchlist WHERE list_type = 'personal'
          )
        ORDER BY abs(d.chg_pct) DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"threshold": threshold}).fetchall()
        trading_date = conn.execute(
            text("SELECT max(date)::text FROM daily_prices")
        ).scalar()
    return trading_date, [(r[1], r[2], r[3]) for r in rows]


def already_sent_for(engine, trading_date: str) -> bool:
    """The job runs at 4pm and again at 11pm; only alert once per trading day."""
    with engine.connect() as conn:
        last = conn.execute(
            text("SELECT value FROM meta WHERE key = 'last_alert_date'")
        ).scalar()
    return last == trading_date


def mark_sent(engine, trading_date: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO meta (key, value) VALUES ('last_alert_date', :d)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""),
            {"d": trading_date},
        )


def build_message(trading_date: str, movers: list, threshold: float) -> str:
    from datetime import date
    pretty = date.fromisoformat(trading_date).strftime("%d %b %Y")
    lines = [f"NSE watchlist — {pretty}", f"Moves of {threshold:g}% or more", ""]
    for symbol, close, chg in movers[:MAX_LINES]:
        lines.append(f"{'+' if chg >= 0 else ''}{chg:.2f}%  {symbol}  ₹{close:,.2f}")
    if len(movers) > MAX_LINES:
        lines.append(f"...and {len(movers) - MAX_LINES} more")
    return "\n".join(lines)


def send(phone: str, apikey: str, message: str) -> None:
    params = {"phone": phone, "text": message, "apikey": apikey}
    url = f"{CALLMEBOT_URL}?{urllib.parse.urlencode(params)}"
    resp = requests.get(url, timeout=30)
    # CallMeBot answers with an HTML page; 200 plus no "error" is the success
    # signal it gives us.
    body = resp.text.strip()
    if resp.status_code != 200 or "error" in body.lower():
        raise RuntimeError(f"CallMeBot returned {resp.status_code}: {body[:300]}")


def main() -> int:
    # The message carries a rupee sign; Windows consoles default to cp1252 and
    # would crash on printing it.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    phone = os.environ.get("CALLMEBOT_PHONE")
    apikey = os.environ.get("CALLMEBOT_APIKEY")
    if not phone or not apikey:
        print("CALLMEBOT_PHONE / CALLMEBOT_APIKEY not set — skipping alert.")
        return 0

    threshold = float(os.environ.get("ALERT_THRESHOLD_PCT", DEFAULT_THRESHOLD))
    engine = get_engine()

    trading_date, movers = fetch_movers(engine, threshold)
    if trading_date is None:
        print("No price data yet — skipping alert.")
        return 0

    if already_sent_for(engine, trading_date):
        print(f"Alert for {trading_date} already sent — skipping.")
        return 0

    if not movers:
        print(f"No watchlist stock moved {threshold:g}% or more on {trading_date}.")
        mark_sent(engine, trading_date)
        return 0

    message = build_message(trading_date, movers, threshold)
    print(f"Sending {len(movers)} mover(s) for {trading_date}:")
    print(message)

    send(phone, apikey, message)
    mark_sent(engine, trading_date)
    print("WhatsApp alert sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
