# Day-trader-god-mode-bot

## EMA 9 / VWAP simulation

Set the GitHub Actions repository variable `EMA_VWAP_SIMULATION` to `1` to
enable Telegram alerts on normal scans. For a Docker deployment use the same
environment variable. The scanner covers the configured stock watchlist plus SPY.
The original 3m alerts report the crossover. No broker orders are submitted.

For SPY offline replay use Python 3.10 or newer:

```bash
python ema_vwap.py bars.csv
python ema_vwap.py bars.csv --quotes options.csv --fx 1.10 --fee 0.65
```

The first command reports signals. The second compares premium stops of 25%
and 30% on the same inputs. The exchange-rate and fee values above are examples.
The default budget is EUR 100 per position, adjustable with `--budget-eur`.
The simulation takes one standard 100-share contract at a time with the entire
premium and both fees inside that budget.

- Bars CSV columns: `timestamp,symbol,open,high,low,close,volume`. Use SPY
  one-minute bars timestamped at the start of the minute.
- Quotes CSV columns: `timestamp,contract,side,strike,expiry,bid,ask`. Use
  historical quotes for standard SPY contracts, CALL or PUT, expiry as
  YYYY-MM-DD, and prices in USD per share. Timestamps in both CSVs need an
  explicit timezone.
- EMA is seeded at the first complete 3-minute close each session. Session
  VWAP uses the OHLC4 of each complete 3-minute bar, weighted by volume.
  Signals use consecutive complete bars. IEX volume may differ from SIP.
- Replay uses the first eligible quote timestamp within one minute of the signal
  and selects the closest affordable strike at that timestamp, preferring the
  nearer expiry when strikes are equally distant. Entry uses ask and exit uses
  bid, based on supplied quotes. A premium stop can fill below its threshold.
  Exit also triggers when the underlying closes on the adverse side of EMA 9
  or in the final minute of the session. Exits without a usable quote are flagged.
- The offline session clock assumes a normal 09:30–16:00 New York session.
  Exclude early-close dates from offline replay. Live scanning uses Alpaca's
  market calendar. GitHub Actions can deliver signals after candle close.

## Intraday EMA 9 / VWAP Reclaim and Pullback

The same simulation toggle enables LONG and SHORT alerts for the watchlist and SPY
on completed 5m and 15m bars. The scanner reports a plan based on the underlying
stock or ETF. It does not track open positions or submit orders.

- The impulse must start at or after 09:45 ET on a normal session. The scan
  needs 14 prior consecutive same-session candles for RVOL and initial RSI/ATR.
  This delays the first possible 5m/15m alerts beyond the opening range.
- RVOL compares impulse volume to the prior 14 candles. LONG RSI is 55–68,
  mirrored to 32–45 for SHORT. The impulse body must be at least 0.5 ATR.
  Confluence means separation of at most 0.5 ATR or a fresh directional cross.
  The directional EMA slope on the impulse must be at least 0.05 ATR per bar.
- Wait for one to three candles of declining volume with an EMA or VWAP
  touch. A close through VWAP against the setup on at least baseline volume
  invalidates it. Entry is the reversal candle close, within 1.5 ATR of EMA 9.
- Stop is one tick beyond the pullback extreme. TP1 is 2R for half, with
  break-even and an EMA 9 close-based exit for the remainder. Skip signals with
  a completed-day SMA 20 or SMA 200 between entry and TP1. Symbols with fewer
  than 200 available daily bars skip this setup.
