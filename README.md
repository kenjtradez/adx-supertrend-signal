# ADX(14)>25 + Supertrend(10,3) — Forward Test, 12 Pairs

Trend-following system: enters on a Supertrend flip while ADX confirms
a trending regime, trails the stop with the Supertrend line, moves to
breakeven at 1R. This is the strongest-validated strategy from the
whole analysis — profitable full-period, in-sample, AND out-of-sample
on all 12 pairs, and robust across 9 parameter variants (ADX threshold,
Supertrend period/multiplier, breakeven rule).

Pairs: EURCHF, EURCAD, EURJPY, CADCHF, NZDCAD, GBPCAD, AUDCHF, NZDJPY,
GBPUSD, US30, DE30, UK100.

**This is a signal logger only. It places no trades.**

## Fresh start

`price_log.csv` is seeded with ~1000 hourly bars per pair (needed for
ADX/Supertrend to stabilize), but `state.json` starts every pair at
flat (position 0, no open trade). The first real entry for each pair
is decided live, going forward — same principle as the NAS100 and
EURGBP forward tests.

## How it works, per pair, per hour

1. Pull the latest confirmed H1 bar.
2. Recompute ADX(14) and Supertrend(10,3) over the trailing window.
3. If flat: enter long on a bullish Supertrend flip (if ADX>25), short
   on a bearish flip (if ADX>25).
4. If in a trade: trail the stop with the Supertrend line, move to
   breakeven once price has moved 1R in your favor, exit if price hits
   the stop or Supertrend flips against you.
5. Log the bar, update state, alert on Telegram if anything changed
   (entry, exit, or breakeven move) — stays quiet on hours with no
   action, so you're not getting pinged every hour for nothing.

## Setup

1. Push this folder as a new GitHub repo.
2. Add secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (reuse existing).
3. Settings → Actions → General → Workflow permissions → Read and write → Save.
4. Actions tab → run "ADX+Supertrend Signal Log (12 pairs)" manually once to confirm it's green.
5. From then on it runs automatically every hour, weekdays.

## Data source & caveats

Yahoo Finance (no key needed):
- FX pairs use the standard `=X` ticker (e.g. `EURCHF=X`) — spot rates
  are fairly standardized across venues, should track your broker closely.
- Indices use `^DJI` (US30), `^GDAXI` (DE30), `^FTSE` (UK100) — real
  index values, closer to broker CFD quotes than a proxy ETF would be,
  but not guaranteed identical. Spot-check against your own chart.

Yahoo's free hourly data typically only goes back ~2 years on request,
which is why the seed file uses your own historical data instead — the
live script only needs the last handful of bars each run to detect new
data, then relies on the growing log for indicator history.

## Files

- `hourly_signal_update.py` — the hourly job
- `price_log.csv` — persistent OHLC log per pair (trimmed to last ~1500 bars/pair to keep it manageable)
- `state.json` — live trade state per pair (position, entry, stop, breakeven flag) — reset to flat
- `.github/workflows/adx_supertrend_signal.yml` — the cron job
