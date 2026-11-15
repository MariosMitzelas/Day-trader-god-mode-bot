"""Scan an explicit US equity watchlist using Alpaca minute bars."""
import argparse
import logging
import os
import re
import sqlite3
import time as clock
from datetime import datetime, time, timedelta
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

from bot import Bar, NY, scan, send_telegram

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"
log = logging.getLogger("scanner")


def config():
    symbols = tuple(dict.fromkeys(os.environ.get(
        "SYMBOLS", "AAPL,MSFT,NVDA,AMD,AMZN,META,TSLA"
    ).upper().replace(" ", "").split(",")))
    if len(symbols) > 50 or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", s) for s in symbols):
        raise ValueError("SYMBOLS must contain 1–50 comma-separated tickers.")
    args = SimpleNamespace(
        symbols=symbols, feed=os.environ.get("ALPACA_FEED", "iex"),
        equity=float(os.environ.get("EQUITY_EUR", "10000")),
        cash=float(os.environ.get("CASH_EUR", "10000")),
        fx=float(os.environ["USD_PER_EUR"]),
        risk=float(os.environ.get("RISK_FRACTION", ".01")),
        cost=float(os.environ.get("COST_USD_PER_SHARE", ".04")),
    )
    if (args.feed not in {"iex", "sip"}
            or not all(map(isfinite, (args.equity, args.cash, args.fx, args.risk, args.cost)))
            or not (0 < args.cash <= args.equity and args.fx > 0
                    and 0 < args.risk <= 1 and args.cost >= 0)):
        raise ValueError("Invalid feed, account settings, FX, or cost.")
    return args


def alpaca(url, **parameters):
    request = Request(f"{url}?{urlencode(parameters)}", headers={
        "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
    })
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def fetch_bars(args, now):
    params = dict(symbols=",".join(args.symbols), timeframe="1Min",
                  start=now.replace(hour=4, minute=0, second=0, microsecond=0).isoformat(),
                  end=now.isoformat(), feed=args.feed, adjustment="raw", limit=10000)
    by_symbol = {symbol: {} for symbol in args.symbols}
    while True:
        page = alpaca(DATA_URL, **params)
        for symbol, records in (page.get("bars") or {}).items():
            for row in records:
                stamp = datetime.fromisoformat(row["t"].replace("Z", "+00:00")).astimezone(NY)
                if stamp + timedelta(minutes=1) <= now:
                    by_symbol[symbol][stamp] = Bar(
                        stamp, row["o"], row["h"], row["l"], row["c"], row["v"]
                    )
        if not page.get("next_page_token"):
            return {symbol: sorted(bars.values(), key=lambda bar: bar.stamp)
                    for symbol, bars in by_symbol.items()}
        params["page_token"] = page["next_page_token"]


def recent_plans(by_symbol, start, end, now, args):
    candidates = []
    for symbol, bars in by_symbol.items():
        premarket = [bar for bar in bars if time(4) <= bar.stamp.time() < start]
        regular = [bar for bar in bars if start <= bar.stamp.time() < end]
        for _, plan in scan(symbol, regular, premarket, args):
            age = (now - datetime.fromisoformat(plan.signal_time)).total_seconds()
            if 0 <= age <= 90:
                candidates.append(plan)
    return sorted(candidates, key=lambda plan: (-plan.volume_ratio, plan.symbol, plan.setup))


def alert(plans, args):
    plan = plans[0]
    price_r = plan.max_entry - plan.stop
    tp1, tp2 = plan.shares // 2, plan.shares // 4
    runners = plan.shares - tp1 - tp2
    setups = " + ".join(item.setup for item in plans)
    stamp = datetime.fromisoformat(plan.signal_time).strftime("%H:%M ET")
    return (
        f"{plan.symbol} · {setups}\n"
        f"{stamp} | Feed {args.feed.upper()} | Όγκος {plan.volume_ratio:.2f}× της βάσης\n"
        f"Trigger ${plan.trigger:.2f} | Μέγιστη είσοδος ${plan.max_entry:.2f}\n"
        f"Stop ${plan.stop:.2f} | Έως {plan.shares} μετοχές\n"
        f"Εκτιμώμενο αρχικό ρίσκο €{plan.planned_risk_usd / args.fx:.2f}\n"
        f"Ενδεικτικά με εκτέλεση στη μέγιστη είσοδο:\n"
        f"TP1 ${plan.max_entry + price_r:.2f} · {tp1} μετοχές\n"
        f"TP2 ${plan.max_entry + 2 * price_r:.2f} · {tp2} μετοχές\n"
        f"Trailing για {runners} μετοχές μετά το TP1: κάτω από επιβεβαιωμένο χαμηλό 1 λεπτού.\n"
        "Επανυπολόγισε τους στόχους με την πραγματική εκτέλεση."
    )


def deliver(plans, db, args, dry_run):
    groups = {}
    for plan in plans:
        key = plan.symbol, plan.signal_time, plan.trigger, plan.stop, plan.max_entry
        groups.setdefault(key, []).append(plan)
    for key, matches in groups.items():
        identifier = repr(key)
        message = alert(matches, args)
        if dry_run:
            print(message)
        elif not db.execute("SELECT 1 FROM sent WHERE id = ?", (identifier,)).fetchone():
            send_telegram(message)
            with db:
                db.execute("INSERT INTO sent VALUES (?)", (identifier,))
            log.info("Alert delivered for %s (%s)", key[0], key[1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    cli = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = os.environ.get("STATE_DB", "alerts.sqlite3")
    try:
        args = config()
        for name in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
            if not os.environ.get(name):
                raise ValueError(f"Missing {name}")
        if not cli.dry_run and not all(os.environ.get(name) for name in
                                      ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")):
            raise ValueError("Missing Telegram settings.")
    except (KeyError, ValueError) as error:
        parser.exit(2, f"Configuration error: {error}\n")
    if not cli.dry_run:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(":memory:" if cli.dry_run else path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY)")
        cached_date, session = None, None
        log.info("Scanner started with %s symbols and %s feed.", len(args.symbols), args.feed)
        while True:
            now = datetime.now(NY)
            date = now.date().isoformat()
            try:
                if cached_date != date:
                    rows = alpaca(CALENDAR_URL, start=date, end=date)
                    session = (time.fromisoformat(rows[0]["open"]),
                               time.fromisoformat(rows[0]["close"])) if rows else None
                    cached_date = date
                    log.info("Market session %s: %s", date, session or "closed")
                if session and session[0] <= now.time() < session[1]:
                    # Wait a few seconds after each minute for the aggregate to arrive.
                    cutoff = (now - timedelta(seconds=5)).replace(second=0, microsecond=0)
                    bars = fetch_bars(args, cutoff)
                    plans = recent_plans(bars, *session, now, args)
                    deliver(plans, db, args, cli.dry_run)
                if cli.once:
                    return
            except (HTTPError, URLError, TimeoutError, ValueError) as error:
                log.error("Request failed (%s).", getattr(error, "code", type(error).__name__))
                if cli.once:
                    raise SystemExit(1) from None
            clock.sleep(30)


if __name__ == "__main__":
    main()
