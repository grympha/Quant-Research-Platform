"""
Backtest Execution Engine — Phase 2
=====================================
Executes pre-computed signals against OHLCV data bar by bar.

Rules:
- One trade open at a time (no concurrent positions).
- Entry filled at the OPEN of the candle after the signal candle.
- SL/TP adjusted from actual fill price (handles entry gaps).
- On ambiguous candles where both TP and SL are in range:
    → SL is assumed hit first (conservative backtesting assumption).
- If max_bars is reached with no exit: trade closed at last bar's Close,
  result recorded as 'open' with unrealised R.
- After a trade closes, the next signal must start on a later bar
  (no overlapping signals inside the same trade window).
"""

from __future__ import annotations

import pandas as pd


def execute(
    df: pd.DataFrame,
    signals: list[dict],
    max_trade_bars: int = 200,
) -> list[dict]:
    """
    Execute signals sequentially against the OHLCV DataFrame.

    Parameters
    ----------
    df             : Full OHLCV DataFrame (DatetimeIndex).
    signals        : Signal list from liquidity_sweep.generate_signals().
    max_trade_bars : Max candles to hold before force-closing at market.

    Returns
    -------
    List of trade result dicts.
    """
    trades: list[dict] = []
    last_exit_bar: int = -1  # prevents new entry before previous trade is closed

    for sig in sorted(signals, key=lambda s: s["signal_bar"]):
        signal_bar = sig["signal_bar"]
        entry_bar = signal_bar + 1

        if entry_bar <= last_exit_bar:
            continue
        if entry_bar >= len(df):
            continue

        fill_price = float(df["Open"].iloc[entry_bar])
        direction = sig["direction"]  # 'BUY' | 'SELL'
        sl_dist = sig["sl_dist"]      # original distance from signal-close to SL
        rr = sig["rr"]

        # Recalculate SL/TP from actual fill price
        if direction == "BUY":
            sl = round(fill_price - sl_dist, 2)
            tp = round(fill_price + sl_dist * rr, 2)
        else:
            sl = round(fill_price + sl_dist, 2)
            tp = round(fill_price - sl_dist * rr, 2)

        # Sanity guard
        if direction == "BUY" and sl >= fill_price:
            continue
        if direction == "SELL" and sl <= fill_price:
            continue

        result, exit_price, exit_bar = _simulate(
            df, entry_bar, fill_price, sl, tp, direction, max_trade_bars
        )

        r_mult = _calc_r(fill_price, sl, exit_price, direction)

        ts = df.index[entry_bar]
        try:
            bar_date = str(ts.date())
            bar_time = str(ts.time())
        except Exception:
            bar_date = str(ts)[:10]
            bar_time = str(ts)[11:19] if len(str(ts)) > 10 else "00:00:00"

        trades.append({
            "date":        bar_date,
            "time":        bar_time,
            "direction":   direction,
            "swept_level": round(sig["swept_level"], 2),
            "entry":       round(fill_price, 2),
            "sl":          round(sl, 2),
            "tp":          round(tp, 2),
            "exit_price":  round(exit_price, 2),
            "result":      result,
            "r_multiple":  round(r_mult, 2),
            "bars_held":   exit_bar - entry_bar,
            # internal references (stripped before export)
            "_signal_bar": signal_bar,
            "_entry_bar":  entry_bar,
            "_exit_bar":   exit_bar,
        })

        last_exit_bar = exit_bar

    return trades


def _simulate(
    df: pd.DataFrame,
    entry_bar: int,
    entry: float,
    sl: float,
    tp: float,
    direction: str,
    max_bars: int,
) -> tuple[str, float, int]:
    """
    Walk forward from entry_bar.
    Returns (result, exit_price, exit_bar_index).
    SL is checked before TP on every candle (conservative).
    """
    end = min(entry_bar + max_bars, len(df))

    for i in range(entry_bar, end):
        hi = float(df["High"].iloc[i])
        lo = float(df["Low"].iloc[i])

        if direction == "BUY":
            if lo <= sl:
                return "loss", sl, i
            if hi >= tp:
                return "win", tp, i
        else:  # SELL
            if hi >= sl:
                return "loss", sl, i
            if lo <= tp:
                return "win", tp, i

    # Max bars hit — close at last Close, mark as 'open'
    last_close = float(df["Close"].iloc[end - 1])
    return "open", last_close, end - 1


def _calc_r(entry: float, sl: float, exit_price: float, direction: str) -> float:
    """Return R multiple relative to the entry→SL distance."""
    sl_dist = abs(entry - sl)
    if sl_dist < 1e-8:
        return 0.0
    if direction == "BUY":
        return (exit_price - entry) / sl_dist
    return (entry - exit_price) / sl_dist
