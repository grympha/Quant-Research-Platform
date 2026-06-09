"""
Break & Retest Module
=====================
No-lookahead Break & Retest strategy.

──────────────────────────────────────────────────────────────────────────────
BUY SETUP
  1. Confirmed swing HIGH at level R (resistance).
  2. A candle closes ABOVE R + breakout_buffer  →  breakout active.
  3. On a later bar: Low touches R (within retest_tolerance),
     candle is bullish (Close > Open), Close > R  →  confirmation.
  4. Entry: next candle Open.
  5. SL: confirmation candle Low − sl_buffer.
  6. TP: entry + (entry − SL) × rr.

SELL SETUP
  1. Confirmed swing LOW at level S (support).
  2. A candle closes BELOW S − breakout_buffer  →  breakout active.
  3. On a later bar: High touches S (within retest_tolerance),
     candle is bearish (Close < Open), Close < S  →  confirmation.
  4. Entry: next candle Open.
  5. SL: confirmation candle High + sl_buffer.
  6. TP: entry − (SL − entry) × rr.

No-lookahead guarantee
  • A swing at bar j is confirmed at bar j + swing_n.
    Signals can only reference swings with j + swing_n ≤ i.
  • Breakout detected at bar i using Close[i] vs confirmed level.
  • Retest/confirmation detected at bar i using OHLC[i].
  • Entry fills at Open of bar i+1.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pandas as pd

from core import backtest

# ── Module-level constants ────────────────────────────────────────────────────

_SL_BUFFER: float    = 0.20   # price units beyond retest wick for SL
_MAX_RETEST_BARS: int = 30    # a breakout expires if no retest within this many bars
_MAX_ACTIVE: int      = 12    # max active breakouts tracked simultaneously


# ── Swing point detection (same algorithm as liquidity_sweep) ─────────────────

def _find_swings(
    df: pd.DataFrame, n: int
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """
    Symmetric n-bar swing detection.
    Bar j is a swing HIGH if High[j] is the maximum in [j-n, j+n].
    Bar j is a swing LOW  if Low[j]  is the minimum in [j-n, j+n].

    Returns (swing_highs, swing_lows) as (bar_index, price) tuples.
    """
    highs  = df["High"].to_numpy()
    lows   = df["Low"].to_numpy()
    n_bars = len(df)

    swing_highs: list[tuple[int, float]] = []
    swing_lows:  list[tuple[int, float]] = []

    for j in range(n, n_bars - n):
        wh = highs[j - n : j + n + 1]
        wl = lows[j - n : j + n + 1]
        if highs[j] >= wh.max() - 1e-8:
            swing_highs.append((j, float(highs[j])))
        if lows[j] <= wl.min() + 1e-8:
            swing_lows.append((j, float(lows[j])))

    return swing_highs, swing_lows


# ── Signal generation ─────────────────────────────────────────────────────────

def generate_signals(
    df: pd.DataFrame,
    rr: float,
    swing_n: int,
    breakout_buffer: float = 0.10,
    retest_tolerance: float = 0.50,
    max_recent_swings: int = 8,
) -> list[dict]:
    """
    Scan each bar for Break & Retest setups.

    Parameters
    ----------
    df                : OHLCV DataFrame (DatetimeIndex).
    rr                : Risk-reward ratio for TP.
    swing_n           : Bars on each side to confirm a swing point.
    breakout_buffer   : Minimum price distance the close must exceed the level
                        to register as a breakout (XAUUSD price units, ≈ pips).
    retest_tolerance  : Maximum distance between the retest candle's wick and
                        the broken level for the touch to count.
    max_recent_swings : How many of the most-recent confirmed swings to monitor.

    Returns
    -------
    List of signal dicts for core.backtest.execute().
    """
    all_highs, all_lows = _find_swings(df, swing_n)

    # active_breakouts: list of dicts
    # keys: direction, level, breakout_bar; expired is added when invalidated/used
    active_breakouts: list[dict] = []

    signals: list[dict] = []
    last_signal_bar: int = -1
    first_valid: int = swing_n * 2   # need enough bars for swings to be confirmed

    for i in range(first_valid, len(df) - 1):
        c_open  = float(df["Open"].iloc[i])
        c_high  = float(df["High"].iloc[i])
        c_low   = float(df["Low"].iloc[i])
        c_close = float(df["Close"].iloc[i])

        # Confirmed swings at bar i: swing at j is usable when j + swing_n <= i
        confirmed_highs = [(j, lvl) for j, lvl in all_highs if j + swing_n <= i]
        confirmed_lows  = [(j, lvl) for j, lvl in all_lows  if j + swing_n <= i]
        recent_highs    = confirmed_highs[-max_recent_swings:]
        recent_lows     = confirmed_lows[-max_recent_swings:]

        # ── Step 1 — detect new breakouts at bar i ────────────────────────────

        # BUY breakout: candle closes above resistance + buffer
        for _j, level in reversed(recent_highs):
            if c_close > level + breakout_buffer:
                if not any(
                    b["direction"] == "BUY" and abs(b["level"] - level) < 0.01
                    for b in active_breakouts
                ):
                    active_breakouts.append({
                        "direction":    "BUY",
                        "level":        level,
                        "breakout_bar": i,
                    })
                break

        # SELL breakout: candle closes below support − buffer
        for _j, level in reversed(recent_lows):
            if c_close < level - breakout_buffer:
                if not any(
                    b["direction"] == "SELL" and abs(b["level"] - level) < 0.01
                    for b in active_breakouts
                ):
                    active_breakouts.append({
                        "direction":    "SELL",
                        "level":        level,
                        "breakout_bar": i,
                    })
                break

        # Cap active breakouts to avoid runaway growth
        if len(active_breakouts) > _MAX_ACTIVE:
            active_breakouts = active_breakouts[-_MAX_ACTIVE:]

        # ── Step 2 — check active breakouts for retest + confirmation ─────────
        signal: dict | None = None

        if i > last_signal_bar:
            for bo in active_breakouts:
                if bo.get("expired"):
                    continue

                level     = bo["level"]
                b_bar     = bo["breakout_bar"]

                if bo["direction"] == "BUY":
                    # Invalidate: price closes back below the broken level
                    if c_close < level - retest_tolerance:
                        bo["expired"] = True
                        continue

                    # Retest + confirmation on bar i:
                    #   wick touches level, bullish candle, close above level,
                    #   must be a different bar from the breakout bar
                    if (i > b_bar
                            and c_low <= level + retest_tolerance
                            and c_close > c_open
                            and c_close > level):
                        sl_raw  = c_low - _SL_BUFFER
                        sl_dist = c_close - sl_raw
                        if sl_dist <= 0:
                            continue
                        signal = {
                            "signal_bar":  i,
                            "direction":   "BUY",
                            "swept_level": round(level, 2),
                            "close_price": round(c_close, 2),
                            "sl_dist":     round(sl_dist, 2),
                            "rr":          rr,
                        }
                        bo["expired"] = True
                        break

                else:  # SELL
                    # Invalidate: price closes back above the broken level
                    if c_close > level + retest_tolerance:
                        bo["expired"] = True
                        continue

                    # Retest + confirmation on bar i:
                    #   wick touches level, bearish candle, close below level
                    if (i > b_bar
                            and c_high >= level - retest_tolerance
                            and c_close < c_open
                            and c_close < level):
                        sl_raw  = c_high + _SL_BUFFER
                        sl_dist = sl_raw - c_close
                        if sl_dist <= 0:
                            continue
                        signal = {
                            "signal_bar":  i,
                            "direction":   "SELL",
                            "swept_level": round(level, 2),
                            "close_price": round(c_close, 2),
                            "sl_dist":     round(sl_dist, 2),
                            "rr":          rr,
                        }
                        bo["expired"] = True
                        break

        # ── Step 3 — expire stale breakouts ───────────────────────────────────
        active_breakouts = [
            b for b in active_breakouts
            if not b.get("expired") and (i - b["breakout_bar"]) <= _MAX_RETEST_BARS
        ]

        if signal:
            signals.append(signal)
            last_signal_bar = i

    return signals


# ── Public entry point ────────────────────────────────────────────────────────

def run(
    df: pd.DataFrame,
    rr: float = 2.0,
    lookback: int = 5,
    max_bars: int = 200,
    breakout_buffer: float = 0.10,
    retest_tolerance: float = 0.50,
    data_by_timeframe: dict | None = None,
    trend_tf: str | None = None,
    structure_tf: str | None = None,
    entry_tf: str | None = None,
) -> list[dict]:
    """
    Full Break & Retest pipeline: detect setups → execute via backtest engine.

    Parameters
    ----------
    df                : Primary OHLCV DataFrame (DatetimeIndex).
    rr                : Risk-reward ratio.
    lookback          : Bars on each side to confirm a swing (swing_n).
    max_bars          : Max candles to hold a trade before force-closing.
    breakout_buffer   : Min price distance for a valid breakout close.
    retest_tolerance  : Price tolerance for retest wick-touch.
    data_by_timeframe : {tf: df} dict — available for future multi-TF filter.
    trend_tf          : TF key used as trend context (future use).
    structure_tf      : TF key used for structure (= df in single-TF mode).
    entry_tf          : TF key for refined entry (future use).

    Returns
    -------
    List of trade result dicts (same schema as liquidity_sweep.run()).
    """
    min_required = lookback * 4 + 2
    if len(df) < min_required:
        return []

    signals = generate_signals(
        df,
        rr=rr,
        swing_n=lookback,
        breakout_buffer=breakout_buffer,
        retest_tolerance=retest_tolerance,
    )

    if not signals:
        return []

    return backtest.execute(df, signals, max_trade_bars=max_bars)
