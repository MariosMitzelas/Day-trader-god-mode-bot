"""Long-only US equity scanner and minute-bar replay."""
import argparse
import csv
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from math import floor, isfinite
from statistics import mean
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16)
CENT = .01


@dataclass(frozen=True)
class Bar:
    stamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Plan:
    symbol: str
    setup: str
    signal_time: str
    trigger: float
    max_entry: float
    stop: float
    shares: int
    planned_risk_usd: float
    volume_ratio: float


def read_bars(path):
    sessions = defaultdict(list)
    with open(path, encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if stamp.utcoffset() is None or stamp.second or stamp.microsecond:
                raise ValueError("Use timezone-aware timestamps at the start of each minute.")
            stamp = stamp.astimezone(NY)
            symbol = row["symbol"].strip().upper()
            prices = [float(row[key]) for key in ("open", "high", "low", "close")]
            bar = Bar(stamp, *prices, int(row["volume"]))
            if (not symbol or not all(isfinite(p) and p > 0 for p in prices)
                    or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close)
                    or bar.volume < 0):
                raise ValueError(f"Invalid OHLCV for {symbol} at {stamp}")
            sessions[symbol, stamp.date()].append(bar)
    for bars in sessions.values():
        bars.sort(key=lambda bar: bar.stamp)
        if len({bar.stamp for bar in bars}) != len(bars):
            raise ValueError("Duplicate minute for one symbol.")
    return sessions


def setup_candidates(previous, signal, premarket_high):
    base = previous[-3:]
    impulse = previous[-6:-3]
    if len(impulse) < 3 or mean(b.volume for b in base) <= 0:
        return
    volume_ratio = signal.volume / mean(b.volume for b in base)
    if volume_ratio < 1.5 or signal.close * signal.volume < 100_000:
        return
    if any(b.stamp - a.stamp != timedelta(minutes=1)
           for a, b in zip(previous[-6:], previous[-5:] + [signal])):
        return
    base_low = min(b.low for b in base)
    base_high = max(b.high for b in base)
    if (premarket_high and base_high <= premarket_high < signal.close
            and base_high >= premarket_high * .995):
        yield "Premarket high breakout", premarket_high, base_low, volume_ratio
    peak = max(b.high for b in impulse)
    rise = peak - impulse[0].open
    if (rise / impulse[0].open >= .008 and base_high <= peak
            and 0 < peak - base_low <= rise / 2 and signal.close > base_high
            and mean(b.volume for b in base) < mean(b.volume for b in impulse)):
        yield "Bull flag pullback", base_high, base_low, volume_ratio


def scan(symbol, regular, premarket, args):
    premarket_high = max((b.high for b in premarket), default=None)
    found = set()
    for index in range(6, len(regular)):
        bar = regular[index]
        for name, trigger, low, ratio in setup_candidates(regular[:index], bar, premarket_high):
            if name in found:
                continue
            stop = round(low - CENT, 2)
            entry_cap = min(trigger + .25 * (trigger - stop), trigger * 1.005)
            max_entry = floor(entry_cap * 100 + 1e-9) / 100
            if stop <= 0 or bar.close > max_entry or max_entry <= stop:
                continue
            risk = max_entry - stop + args.cost
            shares = min(floor(args.equity * args.fx * args.risk / risk),
                         floor(args.cash * args.fx / (max_entry + args.cost)))
            if shares >= 1:
                found.add(name)
                yield index, Plan(symbol, name, (bar.stamp + timedelta(minutes=1)).isoformat(),
                                  trigger, max_entry, stop, shares, round(shares * risk, 2),
                                  round(ratio, 2))


def replay(plan, future, args):
    if not future:
        return {"status": "no_next_bar"}
    first = future[0]
    if first.stamp.isoformat() != plan.signal_time:
        return {"status": "missing_next_minute"}
    entry = round(first.open + args.cost / 2, 4)
    if not plan.trigger <= entry <= plan.max_entry:
        return {"status": "entry_outside_range"}
    initial_r = entry - plan.stop
    stop, left = plan.stop, plan.shares
    targets = [(entry + initial_r, plan.shares // 2),
               (entry + 2 * initial_r, plan.shares // 4)]
    exits, trailing, recent = [], [], []
    for bar in future:
        if recent and bar.stamp - recent[-1].stamp != timedelta(minutes=1):
            return {"status": "incomplete_data", "entry": entry, "exits": exits, "trail": trailing}
        if bar.low <= stop:
            exits.append(("stop", left, min(bar.open, stop) - args.cost / 2))
            left = 0
            break
        while targets and bar.high >= targets[0][0]:
            level, quantity = targets.pop(0)
            if quantity:
                exits.append(("TP1" if len(targets) else "TP2",
                              quantity, level - args.cost / 2))
                left -= quantity
        recent.append(bar)
        if len(recent) >= 3 and len(targets) < 2 and plan.shares // 2:
            a, b, c = recent[-3:]
            candidate = round(b.low - CENT, 2)
            if b.low < min(a.low, c.low) and stop < candidate < c.close:
                stop = candidate
                trailing.append({"effective_from": (c.stamp + timedelta(minutes=1)).isoformat(), "stop": stop})
    if left and future[-1].stamp.time() != time(15, 59):
        return {"status": "incomplete_session", "entry": entry, "exits": exits, "trail": trailing}
    if left:
        exits.append(("session_close", left, future[-1].close - args.cost / 2))
    pnl = sum(quantity * (price - entry) for _, quantity, price in exits)
    return {"status": "simulated", "entry": entry, "exits": exits, "trail": trailing,
            "initial_risk_usd": round(plan.shares * (initial_r + args.cost / 2), 2),
            "pnl_usd": round(pnl, 2)}


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID locally.")
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode()
    request = Request(f"https://api.telegram.org/bot{token}/sendMessage", payload,
                      {"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            if not json.load(response).get("ok"):
                raise ValueError("Telegram rejected the message.")
    except (URLError, TimeoutError):
        raise ValueError("Telegram request failed. Check credentials and connectivity.") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?")
    parser.add_argument("--equity", type=float, default=10000, help="Account equity in EUR")
    parser.add_argument("--cash", type=float, default=10000, help="Available cash in EUR")
    parser.add_argument("--fx", type=float, help="USD per EUR for the scenario")
    parser.add_argument("--risk", type=float, default=.01, help="Fraction of equity risked")
    parser.add_argument("--cost", type=float, default=.04,
                        help="Estimated round-trip cost in USD per share")
    parser.add_argument("--telegram-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.telegram_test:
            send_telegram("Day Trader Bot · Δοκιμαστική ειδοποίηση")
            print("Telegram test message sent.")
            return
        if (not args.csv or args.fx is None
                or not all(map(isfinite, (args.equity, args.cash, args.fx, args.risk, args.cost)))
                or not (0 < args.cash <= args.equity and args.fx > 0
                        and 0 < args.risk <= 1 and args.cost >= 0)):
            raise ValueError("Provide CSV, positive --fx, valid equity/cash, risk and cost.")
        results = []
        for (symbol, _), bars in read_bars(args.csv).items():
            regular = [b for b in bars if OPEN <= b.stamp.time() < CLOSE]
            premarket = [b for b in bars if time(4) <= b.stamp.time() < OPEN]
            for index, plan in scan(symbol, regular, premarket, args):
                outcome = replay(plan, regular[index + 1:], args)
                results.append({"plan": asdict(plan), "outcome": outcome})
        results.sort(key=lambda row: (row["plan"]["signal_time"],
                                      -row["plan"]["volume_ratio"], row["plan"]["symbol"]))
        print(json.dumps(results, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
