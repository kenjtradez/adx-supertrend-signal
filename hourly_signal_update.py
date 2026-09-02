"""
ADX(14)>25 + Supertrend(10,3) trailing-exit system — hourly forward-test
signal logger, across 12 instruments.

Backtest (H1, 2016-2026): all 12 pairs profitable full-period, in-sample,
and out-of-sample, robust across parameter variants. See conversation
history / adx_supertrend_isoos.csv for the full validation.

Run this once per hour. It:
  1. Pulls the latest confirmed H1 bar for each of the 12 pairs
  2. Recomputes ADX(14) and Supertrend(10,3) over the trailing window
  3. Applies the trade-state machine per pair (entry on ST flip while
     ADX>25, trail the stop with ST, move to breakeven at 1R, exit on
     stop hit or ST flip back)
  4. Appends to the price log, updates state.json, alerts via Telegram
  5. Does NOT place any trades — logging only, for forward-test tracking

Requires env vars (GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

DATA SOURCE: Yahoo Finance (direct chart API, no key needed).
  FX pairs use the "=X" ticker (e.g. EURCHF=X) — standardized spot rates,
  should track your broker closely.
  Indices use ^DJI (US30), ^GDAXI (DE30), ^FTSE (UK100) — real index
  values, much closer to broker CFD quotes than a proxy ETF, but still
  not guaranteed identical. Verify against your own chart before trusting
  the exact levels.
"""
import os
import json
import requests
import pandas as pd
import numpy as np
from pathlib import Path

BASE = Path(__file__).parent
PRICE_LOG_PATH = BASE / "price_log.csv"
STATE_PATH = BASE / "state.json"

ADX_THRESH = 25
ST_PERIOD = 10
ST_MULT = 3.0
BREAKEVEN_AT_R = 1.0
MIN_HISTORY_BARS = 60  # need at least this many bars post-warmup for stable ADX/ST

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

YAHOO_SYMBOLS = {
    'EURCHF': 'EURCHF=X', 'EURCAD': 'EURCAD=X', 'EURJPY': 'EURJPY=X',
    'CADCHF': 'CADCHF=X', 'NZDCAD': 'NZDCAD=X', 'GBPCAD': 'GBPCAD=X',
    'AUDCHF': 'AUDCHF=X', 'NZDJPY': 'NZDJPY=X', 'GBPUSD': 'GBPUSD=X',
    'US30': '^DJI', 'DE30': '^GDAXI', 'UK100': '^FTSE',
}
PAIRS = list(YAHOO_SYMBOLS.keys())


def fetch_latest_h1_bars(symbol, n=5):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "5d", "interval": "60m"}
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    result = data.get("chart", {}).get("result")
    if not result:
        raise RuntimeError(f"Yahoo error for {symbol}: {data}")
    result = result[0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    bars = []
    for i in range(len(timestamps)):
        if quote["close"][i] is None:
            continue
        bars.append({
            "datetime": pd.to_datetime(timestamps[i], unit="s", utc=True),
            "open": quote["open"][i], "high": quote["high"][i],
            "low": quote["low"][i], "close": quote["close"][i],
        })
    return bars[-n:]


def wilder_smooth(series, n):
    result = np.full(len(series), np.nan)
    vals = series.values
    if len(vals) <= n:
        return pd.Series(result, index=series.index)
    result[n] = np.nansum(vals[1:n + 1])
    for i in range(n + 1, len(vals)):
        result[i] = result[i - 1] - (result[i - 1] / n) + vals[i]
    return pd.Series(result, index=series.index)


def compute_adx(df, n=14):
    high, low, close = df['high'], df['low'], df['close']
    up_move, down_move = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr_w = wilder_smooth(tr, n)
    plus_di = 100 * (wilder_smooth(plus_dm, n) / atr_w)
    minus_di = 100 * (wilder_smooth(minus_dm, n) / atr_w)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.rolling(n).mean()


def compute_supertrend(df, period=10, mult=3.0):
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift(1)).abs(),
                     (df['low'] - df['close'].shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().values
    hl2 = (high + low) / 2
    basic_upper, basic_lower = hl2 + mult * atr, hl2 - mult * atr
    n = len(df)
    final_upper, final_lower = np.full(n, np.nan), np.full(n, np.nan)
    supertrend, direction = np.full(n, np.nan), np.zeros(n)
    for i in range(n):
        if np.isnan(atr[i]):
            continue
        if i == 0 or np.isnan(final_upper[i - 1]):
            final_upper[i], final_lower[i] = basic_upper[i], basic_lower[i]
            direction[i] = 1
            supertrend[i] = final_lower[i]
            continue
        final_upper[i] = basic_upper[i] if (basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
        final_lower[i] = basic_lower[i] if (basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]) else final_lower[i - 1]
        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if close[i] > final_upper[i] else -1
        supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]
    return pd.Series(supertrend, index=df.index), pd.Series(direction, index=df.index)


def load_price_log():
    if PRICE_LOG_PATH.exists():
        return pd.read_csv(PRICE_LOG_PATH, parse_dates=["datetime"])
    raise FileNotFoundError(f"{PRICE_LOG_PATH} not found. Seed it before first run.")


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {p: {"state": 0, "entry_price": None, "stop_price": None, "risk": None, "moved_to_be": False} for p in PAIRS}


def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[warn] Telegram not configured. Message was:\n" + msg)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"})


def process_pair(pair, price_log, state):
    symbol = YAHOO_SYMBOLS[pair]
    bars = fetch_latest_h1_bars(symbol, n=5)
    if not bars:
        return None, price_log, "no new data"

    pair_log = price_log[price_log["pair"] == pair].sort_values("datetime")
    existing_dts = set(pair_log["datetime"])

    new_rows = [b for b in bars if b["datetime"] not in existing_dts]
    if not new_rows:
        return None, price_log, "no new bar"

    for b in new_rows:
        price_log = pd.concat([price_log, pd.DataFrame([{**b, "pair": pair}])], ignore_index=True)

    pair_df = price_log[price_log["pair"] == pair].sort_values("datetime").set_index("datetime")
    if len(pair_df) < MIN_HISTORY_BARS:
        return None, price_log, f"insufficient history ({len(pair_df)} bars)"

    adx = compute_adx(pair_df, 14)
    st, st_dir = compute_supertrend(pair_df, ST_PERIOD, ST_MULT)

    closes, highs, lows, opens = pair_df['close'].values, pair_df['high'].values, pair_df['low'].values, pair_df['open'].values
    st_vals, dir_vals, adx_vals = st.values, st_dir.values, adx.values

    p_state = state.get(pair, {"state": 0, "entry_price": None, "stop_price": None, "risk": None, "moved_to_be": False})
    cur_pos = p_state["state"]
    entry_price = p_state["entry_price"]
    stop_price = p_state["stop_price"]
    risk = p_state["risk"]
    moved_to_be = p_state["moved_to_be"]

    i = len(pair_df) - 1  # latest bar
    c, h, l = closes[i], highs[i], lows[i]
    prev_dir, cur_dir = dir_vals[i - 1], dir_vals[i]
    cur_st, cur_adx = st_vals[i], adx_vals[i]

    action = "HOLD" if cur_pos != 0 else "FLAT"

    if cur_pos == 0:
        if not np.isnan(cur_adx) and cur_adx > ADX_THRESH and not np.isnan(cur_st):
            if prev_dir == -1 and cur_dir == 1:
                cur_pos = 1
                entry_price = c
                stop_price = st_vals[i - 1]
                risk = entry_price - stop_price
                moved_to_be = False
                action = "ENTER LONG"
            elif prev_dir == 1 and cur_dir == -1:
                cur_pos = -1
                entry_price = c
                stop_price = st_vals[i - 1]
                risk = stop_price - entry_price
                moved_to_be = False
                action = "ENTER SHORT"
    elif cur_pos == 1:
        if not np.isnan(cur_st) and cur_st > stop_price:
            stop_price = cur_st
        if not moved_to_be and risk and risk > 0 and (c - entry_price) >= BREAKEVEN_AT_R * risk:
            stop_price = max(stop_price, entry_price)
            moved_to_be = True
            action = "HOLD LONG (moved to breakeven)"
        if l <= stop_price or cur_dir == -1:
            action = "EXIT LONG"
            cur_pos, entry_price, stop_price, risk, moved_to_be = 0, None, None, None, False
        else:
            if action == "HOLD":
                action = "HOLD LONG"
    elif cur_pos == -1:
        if not np.isnan(cur_st) and cur_st < stop_price:
            stop_price = cur_st
        if not moved_to_be and risk and risk > 0 and (entry_price - c) >= BREAKEVEN_AT_R * risk:
            stop_price = min(stop_price, entry_price)
            moved_to_be = True
            action = "HOLD SHORT (moved to breakeven)"
        if h >= stop_price or cur_dir == 1:
            action = "EXIT SHORT"
            cur_pos, entry_price, stop_price, risk, moved_to_be = 0, None, None, None, False
        else:
            if action == "HOLD":
                action = "HOLD SHORT"

    state[pair] = {"state": cur_pos, "entry_price": entry_price, "stop_price": stop_price,
                    "risk": risk, "moved_to_be": moved_to_be}

    detail = {
        "pair": pair, "close": c, "adx": None if np.isnan(cur_adx) else round(cur_adx, 1),
        "supertrend": None if np.isnan(cur_st) else round(cur_st, 5),
        "action": action, "position": cur_pos,
        "stop_price": round(stop_price, 5) if stop_price else None,
    }
    return detail, price_log, "ok"


def main():
    price_log = load_price_log()
    state = load_state()
    details = []

    for pair in PAIRS:
        try:
            detail, price_log, status = process_pair(pair, price_log, state)
            if detail:
                details.append(detail)
            else:
                details.append({"pair": pair, "action": f"SKIPPED ({status})"})
        except Exception as e:
            details.append({"pair": pair, "action": f"ERROR: {e}"})

    # trim price log to last ~1500 bars per pair to keep file size sane
    trimmed = []
    for pair in PAIRS:
        sub = price_log[price_log["pair"] == pair].sort_values("datetime").tail(1500)
        trimmed.append(sub)
    price_log = pd.concat(trimmed, ignore_index=True)

    price_log.to_csv(PRICE_LOG_PATH, index=False)
    STATE_PATH.write_text(json.dumps(state, indent=2))

    active_moves = [d for d in details if d.get("action", "").startswith(("ENTER", "EXIT")) or "breakeven" in d.get("action", "")]
    if active_moves:
        lines = [f"*ADX+Supertrend Signal Update*"]
        for d in active_moves:
            lines.append(f"{d['pair']}: *{d['action']}* @ {d.get('close', '?')}")
        lines.append("_(signal log only — no trades placed)_")
        send_telegram("\n".join(lines))
    else:
        print("No position changes this run.")

    for d in details:
        print(d)


if __name__ == "__main__":
    main()
