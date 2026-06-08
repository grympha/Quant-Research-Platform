"""
Liquidity Sweep Module — Phase 1
=================================
Detects stop-hunt / liquidity-grab candles on XAUUSD:

  Bullish sweep : wick below the prior N-bar low, candle closes back above → Long
  Bearish sweep : wick above the prior N-bar high, candle closes back below → Short

Entry  : close of the sweep candle (next bar open approximation)
Stop   : wick extreme ± tiny buffer (1 pip = 0.10 on XAUUSD)
Target : entry ± stop_distance × RR

Simulation walks forward bar-by-bar to determine win / loss / open.
Overlapping signals are suppressed so each trade zone is used only once.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

_PIP = 0.10  # XAUUSD minimum buffer (1 pip)


# ── Signal detection ──────────────────────────────────────────────────────────

def _classify(df: pd.DataFrame, i: int, lookback: int) -> Literal["bullish", "bearish", "none"]:
    """Return sweep direction for candle at index i, or 'none'."""
    if i < lookback:
        return "none"

    window = df.iloc[i - lookback : i]
    c = df.iloc[i]

    prior_low = window["Low"].min()
    prior_high = window["High"].max()

    # Bearish sweep takes priority (wick above prior high, close below)
    if c["High"] > prior_high and c["Close"] < prior_high:
        return "bearish"

    # Bullish sweep (wick below prior low, close above)
    if c["Low"] < prior_low and c["Close"] > prior_low:
        return "bullish"

    return "none"


# ── Trade simulation ──────────────────────────────────────────────────────────

def _simulate(
    df: pd.DataFrame,
    start_i: int,
    entry: float,
    stop: float,
    target: float,
    direction: str,
    max_bars: int,
) -> tuple[str, float, int]:
    """
    Walk forward from start_i.
    Returns (result, r_multiple, bars_held).
    """
    stop_dist = abs(entry - stop)
    if stop_dist == 0:
        return "open", 0.0, 0

    end_i = min(start_i + max_bars, len(df))

    for i in range(start_i, end_i):
        h = df["High"].iloc[i]
        lo = df["Low"].iloc[i]

        if direction == "long":
            if lo <= stop:
                return "loss", -1.0, i - start_i + 1
            if h >= target:
                r = abs(target - entry) / stop_dist
                return "win", round(r, 2), i - start_i + 1
        else:
            if h >= stop:
                return "loss", -1.0, i - start_i + 1
            if lo <= target:
                r = abs(entry - target) / stop_dist
                return "win", round(r, 2), i - start_i + 1

    # Still open — mark with current unrealised R
    last_close = df["Close"].iloc[end_i - 1]
    if direction == "long":
        r = (last_close - entry) / stop_dist
    else:
        r = (entry - last_close) / stop_dist

    return "open", round(r, 2), max_bars


# ── Public entry point ────────────────────────────────────────────────────────

def run(df: pd.DataFrame, rr: float = 2.0, lookback: int = 20, max_bars: int = 100) -> list[dict]:
    """
    Detect liquidity sweeps and simulate trades.

    Parameters
    ----------
    df       : OHLCV DataFrame with DatetimeIndex (from data_loader.load_csv)
    rr       : Risk-to-reward ratio for targets
    lookback : Number of preceding bars used to identify the swing level
    max_bars : Maximum bars to hold before closing at market

    Returns
    -------
    List of trade dicts, one per detected setup.
    """
    trades: list[dict] = []
    skip_until: int = 0  # suppress overlapping signals within an active trade

    for i in range(lookback, len(df) - 1):
        if i < skip_until:
            continue

        direction = _classify(df, i, lookback)
        if direction == "none":
            continue

        c = df.iloc[i]
        entry = round(float(c["Close"]), 2)

        if direction == "long":
            stop = round(float(c["Low"]) - _PIP, 2)
            stop_dist = entry - stop
            if stop_dist <= 0:
                continue
            target = round(entry + stop_dist * rr, 2)
        else:
            stop = round(float(c["High"]) + _PIP, 2)
            stop_dist = stop - entry
            if stop_dist <= 0:
                continue
            target = round(entry - stop_dist * rr, 2)

        result, r_mult, bars = _simulate(
            df, i + 1, entry, stop, target, direction, max_bars
        )

        # Resolved exit price
        if result == "win":
            exit_price = target
        elif result == "loss":
            exit_price = stop
        else:
            exit_price = round(float(df["Close"].iloc[min(i + bars, len(df) - 1)]), 2)

        trades.append(
            {
                "date": str(df.index[i].date()),
                "time": str(df.index[i].time()),
                "direction": direction,
                "entry": entry,
                "stop": stop,
                "target": target,
                "exit_price": exit_price,
                "result": result,
                "r_multiple": r_mult,
                "bars_held": bars,
            }
        )

        # Don't stack signals during this trade's lifetime
        skip_until = i + bars + 1

    return trades
