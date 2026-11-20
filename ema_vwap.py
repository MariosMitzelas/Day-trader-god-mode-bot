"""Three-minute EMA/VWAP signals and offline SPY options replay."""
import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from math import isfinite

from bot import CLOSE, NY, OPEN, read_bars

MINUTE = timedelta(minutes=1)
WINDOW = 3 * MINUTE


def indicators(bars, session_open):
    """Use complete, session-aligned buckets and an OHLC4 volume-weighted proxy."""
    buckets = defaultdict(list)
    for bar in bars:
        anchor = datetime.combine(bar.stamp.date(), session_open, NY)
        index = (bar.stamp - anchor) // WINDOW
        buckets[anchor + index * WINDOW].append(bar)
    ema = previous = previous_end = session = None
    weighted = volume = 0
    for start, group in sorted(buckets.items()):
        if session != start.date():
            ema = previous = previous_end = None
            weighted = volume = 0
            session = start.date()
        if [bar.stamp for bar in group] != [start + i * MINUTE for i in range(3)]:
            continue
        close = group[-1].close
        typical = (group[0].open + max(b.high for b in group)
                   + min(b.low for b in group) + close) / 4
        bucket_volume = sum(b.volume for b in group)
        weighted += typical * bucket_volume
        volume += bucket_volume
        ema = close if ema is None else ema + .2 * (close - ema)
        if not volume:
            continue
        vwap = weighted / volume
        delta = ema - vwap
        end = start + WINDOW
        side = None
        if previous_end == start:
            if previous <= 0 < delta:
                side = "CALL"
            elif previous >= 0 > delta:
                side = "PUT"
        yield end, close, ema, vwap, side
        previous, previous_end = delta, end


def read_quotes(path):
    quotes = defaultdict(list)
    with open(path, encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if stamp.utcoffset() is None:
                raise ValueError("Quote timestamps must include a timezone.")
            stamp = stamp.astimezone(NY)
            side, contract = row["side"].upper(), row["contract"]
            strike, bid, ask = map(float, (row["strike"], row["bid"], row["ask"]))
            expiry = date.fromisoformat(row["expiry"])
            if (not contract or side not in {"CALL", "PUT"}
                    or not all(map(isfinite, (strike, bid, ask)))
                    or not (strike > 0 and 0 <= bid <= ask and ask > 0)):
                raise ValueError(f"Invalid option quote at {stamp.isoformat()}.")
            if expiry >= stamp.date() and OPEN <= stamp.time() < CLOSE:
                quotes[stamp.date()].append((stamp, contract, side, strike, expiry, bid, ask))
    return {day: sorted(rows) for day, rows in quotes.items()}


def replay(points, quotes, budget, fee, stop_fraction):
    results = []
    available_after = None
    for index, (stamp, close, _, _, side) in enumerate(points):
        if (not side or stamp.time() >= (datetime.combine(stamp.date(), CLOSE) - WINDOW).time()
                or (available_after and stamp <= available_after)):
            continue
        candidates = {}
        for q in quotes:
            if q[0] > stamp + MINUTE:
                break
            if (q[0] > stamp and q[2] == side and q[5] > 0
                    and q[6] * 100 + 2 * fee <= budget):
                candidates.setdefault(q[0], []).append(q)
        result = {"signal": stamp.isoformat(), "side": side, "status": "no_eligible_quote"}
        results.append(result)
        if not candidates:
            continue
        entry = min(candidates[min(candidates)], key=lambda q: (abs(q[3] - close), q[4], q[6], q[1]))
        entered, contract, _, _, _, _, ask = entry
        result.update(contract=contract, entered=entered.isoformat(), entry_ask=ask,
                      status="unpriced_exit")
        exit_signal = next((end for end, price, ema, _, _ in points[index + 1:]
                            if (side == "CALL" and price < ema)
                            or (side == "PUT" and price > ema)), None)
        # Use only quotes observed after entry. Reserve the final minute for flattening.
        flatten = datetime.combine(stamp.date(), CLOSE, NY) - MINUTE
        deadline = min(exit_signal or flatten, flatten)
        for observed, quoted_contract, _, _, _, bid, _ in quotes:
            if observed <= entered or quoted_contract != contract:
                continue
            if observed > deadline + MINUTE:
                break
            if observed >= deadline or bid <= ask * (1 - stop_fraction):
                reason = ("EMA" if exit_signal == deadline else "session_close") if observed >= deadline else "premium_stop"
                result.update(status="simulated", exited=observed.isoformat(),
                              exit_bid=bid, reason=reason, pnl_usd=round((bid - ask) * 100 - 2 * fee, 2))
                available_after = observed
                break
        else:
            # An unpriced open position makes the rest of this session ambiguous.
            break
        if result["status"] == "unpriced_exit":
            break
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bars")
    parser.add_argument("--quotes", help="Historical standard SPY option bid/ask CSV")
    parser.add_argument("--fx", type=float, help="USD per EUR, required with --quotes")
    parser.add_argument("--budget-eur", type=float, default=100)
    parser.add_argument("--fee", type=float, default=0, help="USD per contract per side")
    args = parser.parse_args()
    try:
        if args.quotes and (args.fx is None or not all(map(isfinite, (args.fx, args.budget_eur, args.fee)))
                            or min(args.fx, args.budget_eur) <= 0 or args.fee < 0):
            raise ValueError("Provide positive FX and budget and a nonnegative fee.")
        quotes = read_quotes(args.quotes) if args.quotes else {}
        sessions = {}
        for (symbol, day), bars in sorted(read_bars(args.bars).items()):
            if symbol != "SPY":
                continue
            points = list(indicators([b for b in bars if OPEN <= b.stamp.time() < CLOSE], OPEN))
            if args.quotes:
                sessions[str(day)] = {
                    str(stop): replay(points, quotes.get(day, []), args.fx * args.budget_eur, args.fee, stop)
                    for stop in (.25, .30)
                }
            else:
                sessions[str(day)] = [{"signal": end.isoformat(), "side": side,
                                       "close": close, "ema9": ema, "vwap": vwap}
                                      for end, close, ema, vwap, side in points if side]
        if not sessions:
            raise ValueError("The bars CSV contains no SPY sessions.")
        print(json.dumps(sessions, indent=2))
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
