"""
Liquidity Sweep Module — Phase 2
==================================
Real swing-high / swing-low detection with rejection-candle confirmation.
No lookahead: a swing at bar j is only "confirmed" once bar j + swing_n has closed.

──────────────────────────────────────────────────────────────────
Signal logic
──────────────────────────────────────────────────────────────────
BEARISH SWEEP  (sweep of a prior swing HIGH → SELL)
  1. A confirmed swing high at level H exists.
  2. Current candle's High > H  (the level is taken out — liquidity swept).
  3. Current candle's Close < H (price rejects back below the level).
  4. Current candle is bearish  (Close < Open) — body confirms rejection.
  → Entry SELL at next candle Open.
  → SL  = sweep candle High + buffer.
  → TP  = entry − (SL − entry) × RR.

BULLISH SWEEP  (sweep of a prior swing LOW → BUY)
  1. A confirmed swing low at level L exists.
  2. Current candle's Low < L   (level taken out).
  3. Current candle's Close > L (price rejects back above the level).
  4. Current candle is bullish  (Close > Open) — body confirms rejection.
  → Entry BUY at next candle Open.
  → SL  = sweep candle Low − buffer.
  → TP  = entry + (entry − SL) × RR.
──────────────────────────────────────────────────────────────────

Execution is handled by core.backtest (no lookahead simulation).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core import backtest

_BUFFER: float = 0.20   # SL buffer beyond sweep wick (≈ 2 XAUUSD pips)
_MIN_SWEEP: float = 0.10  # minimum pip distance the wick must exceed the level


# ── Swing point detection ─────────────────────────────────────────────────────

def _find_swings(
    df: pd.DataFrame, n: int
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """
    Pre-compute all swing highs and lows using a symmetric n-bar window.

    A bar at index j is a swing HIGH if its High is >= every High in [j-n, j+n].
    A bar at index j is a swing LOW  if its Low  is <= every Low  in [j-n, j+n].

    Bars in [0, n-1] and [len-n, len-1] cannot be swings (window incomplete).

    Returns (swing_highs, swing_lows) as lists of (bar_index, price).
    """
    highs_arr = df["High"].to_numpy()
    lows_arr  = df["Low"].to_numpy()
    n_bars    = len(df)

    swing_highs: list[tuple[int, float]] = []
    swing_lows:  list[tuple[int, float]] = []

    for j in range(n, n_bars - n):
        window_h = highs_arr[j - n : j + n + 1]
        window_l = lows_arr[j - n : j + n + 1]

        if highs_arr[j] >= window_h.max() - 1e-8:
            swing_highs.append((j, float(highs_arr[j])))

        if lows_arr[j] <= window_l.min() + 1e-8:
            swing_lows.append((j, float(lows_arr[j])))

    return swing_highs, swing_lows


# ── Signal generation (no lookahead) ─────────────────────────────────────────

def generate_signals(
    df: pd.DataFrame,
    rr: float,
    swing_n: int,
    max_recent_swings: int = 8,
) -> list[dict]:
    """
    Scan each candle for a liquidity sweep + rejection setup.
    Only uses information available at the time of the signal candle.

    Parameters
    ----------
    df                : OHLCV DataFrame (DatetimeIndex).
    rr                : Risk-reward ratio for TP placement.
    swing_n           : Bars on each side needed to confirm a swing point.
    max_recent_swings : Look back at this many of the most-recent confirmed swings.

    Returns
    -------
    List of signal dicts consumed by core.backtest.execute().
    """
    all_highs, all_lows = _find_swings(df, swing_n)
    signals: list[dict] = []

    # The earliest bar at which any confirmed swing exists
    first_valid = swing_n * 2
    last_signal_bar = -1  # throttle: skip adjacent bars with signals

    for i in range(first_valid, len(df) - 1):
        if i == last_signal_bar:
            continue

        # A swing at bar j is confirmed at bar j + swing_n (i.e. j ≤ i - swing_n)
        confirmed_highs = [(j, lvl) for j, lvl in all_highs if j + swing_n <= i]
        confirmed_lows  = [(j, lvl) for j, lvl in all_lows  if j + swing_n <= i]

        recent_highs = confirmed_highs[-max_recent_swings:]
        recent_lows  = confirmed_lows[-max_recent_swings:]

        c_open  = float(df["Open"].iloc[i])
        c_high  = float(df["High"].iloc[i])
        c_low   = float(df["Low"].iloc[i])
        c_close = float(df["Close"].iloc[i])

        signal: dict | None = None

        # ── BEARISH: sweep of prior swing high ────────────────────────────
        for _j, swing_level in reversed(recent_highs):
            if c_high - swing_level < _MIN_SWEEP:
                continue          # wick too small — not a meaningful sweep
            if c_close >= swing_level:
                continue          # no rejection — price still above the level
            if c_close >= c_open:
                continue          # candle is bullish — body doesn't confirm

            sl_raw  = c_high + _BUFFER
            sl_dist = sl_raw - c_close
            if sl_dist <= 0:
                continue

            signal = {
                "signal_bar":  i,
                "direction":   "SELL",
                "swept_level": swing_level,
                "sweep_size":  round(c_high - swing_level, 2),
                "close_price": c_close,
                "sl_dist":     round(sl_dist, 2),
                "rr":          rr,
            }
            break   # use the most-recent qualifying swing high

        # ── BULLISH: sweep of prior swing low ─────────────────────────────
        if signal is None:
            for _j, swing_level in reversed(recent_lows):
                if swing_level - c_low < _MIN_SWEEP:
                    continue
                if c_close <= swing_level:
                    continue      # no rejection — price still below the level
                if c_close <= c_open:
                    continue      # bearish candle body — doesn't confirm

                sl_raw  = c_low - _BUFFER
                sl_dist = c_close - sl_raw
                if sl_dist <= 0:
                    continue

                signal = {
                    "signal_bar":  i,
                    "direction":   "BUY",
                    "swept_level": swing_level,
                    "sweep_size":  round(swing_level - c_low, 2),
                    "close_price": c_close,
                    "sl_dist":     round(sl_dist, 2),
                    "rr":          rr,
                }
                break

        if signal:
            signals.append(signal)
            last_signal_bar = i   # throttle consecutive signals

    return signals


# ── Public entry point ────────────────────────────────────────────────────────

def run(
    df: pd.DataFrame,
    rr: float = 2.0,
    lookback: int = 5,
    max_bars: int = 200,
) -> list[dict]:
    """
    Full pipeline: detect sweeps → execute via backtest engine → return trades.

    Parameters
    ----------
    df       : OHLCV DataFrame from data_loader.load_csv().
    rr       : Risk-reward ratio (e.g. 2.0 = 1:2).
    lookback : Bars on each side used to confirm a swing point (swing_n).
               Smaller = more sensitive swings; larger = only major pivots.
    max_bars : Maximum candles to hold a trade before force-closing.

    Returns
    -------
    List of trade result dicts (see core.backtest.execute for schema).
    """
    min_required = lookback * 4 + 2
    if len(df) < min_required:
        return []

    signals = generate_signals(df, rr=rr, swing_n=lookback)

    if not signals:
        return []

    trades = backtest.execute(df, signals, max_trade_bars=max_bars)
    return trades
