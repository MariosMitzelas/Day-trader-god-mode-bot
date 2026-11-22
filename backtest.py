"""Backtest harness."""
import argparse, json, math, sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev
from types import SimpleNamespace
from bot import CLOSE, OPEN, read_bars, replay, scan

def _load(csv_path, args):
    for (symbol, day), bars in read_bars(csv_path).items():
        regular = [b for b in bars if OPEN <= b.stamp.time() < CLOSE]
        premarket = [b for b in bars if not (OPEN <= b.stamp.time() < CLOSE)]
        for index, plan in scan(symbol, regular, premarket, args):
            outcome = replay(plan, regular[index + 1:], args)
            yield {"day": day.isoformat(), "symbol": symbol, "setup": plan.setup,
                   "signal_time": plan.signal_time, "shares": plan.shares,
                   "planned_risk_usd": plan.planned_risk_usd,
                   "volume_ratio": plan.volume_ratio, **outcome}

def _stats(trades):
    sim = [t for t in trades if t["status"] == "simulated"]
    if not sim: return {"trades": 0}
    pnls = [t["pnl_usd"] for t in sim]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p < 0]
    gw = sum(wins); gl = abs(sum(losses))
    return {"trades": len(sim), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(sim), 4),
            "net_pnl_usd": round(sum(pnls), 2)}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("csv"); p.add_argument("--fx", type=float, required=True)
    p.add_argument("--out"); a = p.parse_args()
    args = SimpleNamespace(equity=10000, cash=10000, fx=a.fx, risk=.01, cost=.04)
    trades = list(_load(a.csv, args))
    if not trades: sys.exit("No trades.")
    report = {"overall": _stats(trades), "trades": trades}
    if a.out: Path(a.out).write_text(json.dumps(report, indent=2))
    print(json.dumps({"summary": report["overall"]}, indent=2))

if __name__ == "__main__":
    main()
