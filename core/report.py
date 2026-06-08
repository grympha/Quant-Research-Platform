"""
Report Generator
================
Takes a list of trade dicts (from any module) and produces a standardised
performance report that is checked against the platform's target goals.

Goals
-----
  Monthly Return : 3 % – 5 %
  Max Drawdown   : < 4 %
  Profit Factor  : > 1.5
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

# ── Goal thresholds ────────────────────────────────────────────────────────────
GOALS: dict[str, Any] = {
    "monthly_return_min": 3.0,
    "monthly_return_max": 5.0,
    "max_drawdown_limit": 4.0,
    "profit_factor_min": 1.5,
}

_EMPTY = {
    "total_trades": 0,
    "open_trades": 0,
    "win_trades": 0,
    "loss_trades": 0,
    "win_rate": 0.0,
    "profit_factor": 0.0,
    "gross_profit_r": 0.0,
    "gross_loss_r": 0.0,
    "net_r": 0.0,
    "monthly_return": 0.0,
    "max_drawdown": 0.0,
    "goal_status": "INSUFFICIENT DATA",
    "goal_detail": {},
    "goals": GOALS,
    "equity_curve": [0.0],
}


def generate(
    trades: list[dict],
    risk_pct: float,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """
    Parameters
    ----------
    trades     : Output of any module's run() function.
    risk_pct   : Risk per trade as a % of account (e.g. 1.0 = 1 %).
    start_date : First bar datetime string (used for monthly normalisation).
    end_date   : Last bar datetime string.

    Returns
    -------
    Dict with all metrics, goal evaluation, and equity curve list.
    """
    closed = [t for t in trades if t["result"] in ("win", "loss")]
    open_trades = [t for t in trades if t["result"] == "open"]

    if not closed:
        return {**_EMPTY, "open_trades": len(open_trades)}

    wins = [t for t in closed if t["result"] == "win"]
    losses = [t for t in closed if t["result"] == "loss"]

    win_rate = len(wins) / len(closed) * 100
    gross_profit_r = sum(t["r_multiple"] for t in wins)
    gross_loss_r = abs(sum(t["r_multiple"] for t in losses))
    profit_factor = (
        gross_profit_r / gross_loss_r if gross_loss_r > 0 else math.inf
    )
    net_r = sum(t["r_multiple"] for t in closed)

    # ── Equity curve (linear % of account) ───────────────────────────────────
    equity: list[float] = [0.0]
    sorted_closed = sorted(closed, key=lambda x: (x["date"], x["time"]))
    for t in sorted_closed:
        equity.append(round(equity[-1] + t["r_multiple"] * risk_pct, 4))

    # ── Max drawdown ─────────────────────────────────────────────────────────
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd

    # ── Monthly return ────────────────────────────────────────────────────────
    months = _calc_months(sorted_closed, start_date, end_date)
    net_return_pct = net_r * risk_pct
    monthly_return = net_return_pct / months

    # ── Goal evaluation ───────────────────────────────────────────────────────
    goal_detail = {
        "monthly_return": {
            "value": round(monthly_return, 2),
            "target": f"{GOALS['monthly_return_min']}%–{GOALS['monthly_return_max']}%",
            "passed": GOALS["monthly_return_min"] <= monthly_return <= GOALS["monthly_return_max"],
        },
        "max_drawdown": {
            "value": round(max_dd, 2),
            "target": f"< {GOALS['max_drawdown_limit']}%",
            "passed": max_dd < GOALS["max_drawdown_limit"],
        },
        "profit_factor": {
            "value": round(profit_factor, 2),
            "target": f"> {GOALS['profit_factor_min']}",
            "passed": profit_factor >= GOALS["profit_factor_min"],
        },
    }
    all_passed = all(g["passed"] for g in goal_detail.values())
    goal_status = "PASS" if all_passed else "FAIL"

    return {
        "total_trades": len(closed),
        "open_trades": len(open_trades),
        "win_trades": len(wins),
        "loss_trades": len(losses),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if not math.isinf(profit_factor) else 999.0,
        "gross_profit_r": round(gross_profit_r, 2),
        "gross_loss_r": round(gross_loss_r, 2),
        "net_r": round(net_r, 2),
        "monthly_return": round(monthly_return, 2),
        "max_drawdown": round(max_dd, 2),
        "goal_status": goal_status,
        "goal_detail": goal_detail,
        "goals": GOALS,
        "equity_curve": equity,
    }


def _calc_months(
    sorted_closed: list[dict],
    start_date: str | None,
    end_date: str | None,
) -> float:
    """Estimate the number of calendar months covered by the data."""
    try:
        if start_date and end_date:
            delta = pd.Timestamp(end_date) - pd.Timestamp(start_date)
            return max(delta.days / 30.44, 1.0)
        if len(sorted_closed) >= 2:
            delta = (
                pd.Timestamp(sorted_closed[-1]["date"])
                - pd.Timestamp(sorted_closed[0]["date"])
            )
            return max(delta.days / 30.44, 1.0)
    except Exception:
        pass
    return 1.0
