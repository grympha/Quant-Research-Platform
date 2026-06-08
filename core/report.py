"""
Report Generator — Phase 2
============================
Converts a list of trade results into a full performance report with:
  - Standard metrics (win rate, profit factor, net R, drawdown)
  - Monthly breakdown (R per month → % return per month)
  - Per-metric PASS / WATCHLIST / FAIL rating
  - Composite goal_status: PASS | WATCHLIST | FAIL

Goal thresholds
───────────────
Monthly Return   PASS  3 % – 5 %      WATCHLIST  1.5 % – 3 % | 5 % – 8 %
Max Drawdown     PASS  < 4 %           WATCHLIST  4 % – 6 %
Profit Factor    PASS  ≥ 1.5           WATCHLIST  1.2 – 1.49
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import pandas as pd


# ── Goal definitions ──────────────────────────────────────────────────────────

GOALS: dict[str, Any] = {
    "monthly_return_min": 3.0,
    "monthly_return_max": 5.0,
    "max_drawdown_limit": 4.0,
    "profit_factor_min":  1.5,
}

_WATCHLIST_MONTHLY_LO  = 1.5   # %
_WATCHLIST_MONTHLY_HI  = 8.0   # % (above target but suspicious)
_WATCHLIST_DD          = 6.0   # %
_WATCHLIST_PF          = 1.2

_EMPTY_REPORT: dict[str, Any] = {
    "total_trades":      0,
    "open_trades":       0,
    "win_trades":        0,
    "loss_trades":       0,
    "win_rate":          0.0,
    "profit_factor":     0.0,
    "gross_profit_r":    0.0,
    "gross_loss_r":      0.0,
    "net_r":             0.0,
    "monthly_return":    0.0,
    "max_drawdown":      0.0,
    "goal_status":       "INSUFFICIENT DATA",
    "goal_detail":       {},
    "goals":             GOALS,
    "equity_curve":      [0.0],
    "monthly_breakdown": [],
}


# ── Public API ────────────────────────────────────────────────────────────────

def generate(
    trades:     list[dict],
    risk_pct:   float,
    start_date: str | None = None,
    end_date:   str | None = None,
) -> dict:
    """
    Build a full performance report.

    Parameters
    ----------
    trades     : Output of any module's run() (or backtest.execute).
    risk_pct   : Risk per trade as % of account (e.g. 1.0 = 1 %).
    start_date : First bar datetime string — used to normalise monthly return.
    end_date   : Last bar datetime string.

    Returns
    -------
    Dict containing all metrics, goal evaluation, equity curve, and monthly breakdown.
    """
    closed      = [t for t in trades if t["result"] in ("win", "loss")]
    open_trades = [t for t in trades if t["result"] == "open"]

    if not closed:
        return {**_EMPTY_REPORT, "open_trades": len(open_trades)}

    wins   = [t for t in closed if t["result"] == "win"]
    losses = [t for t in closed if t["result"] == "loss"]

    # ── Core metrics ──────────────────────────────────────────────────────────
    win_rate      = len(wins) / len(closed) * 100
    gross_profit  = sum(t["r_multiple"] for t in wins)
    gross_loss    = abs(sum(t["r_multiple"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else math.inf
    net_r         = sum(t["r_multiple"] for t in closed)

    # ── Equity curve (linear % of account) ───────────────────────────────────
    sorted_closed = sorted(closed, key=lambda t: (t["date"], t["time"]))
    equity: list[float] = [0.0]
    for t in sorted_closed:
        equity.append(round(equity[-1] + t["r_multiple"] * risk_pct, 4))

    # ── Max drawdown ─────────────────────────────────────────────────────────
    peak   = 0.0
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd

    # ── Monthly return ────────────────────────────────────────────────────────
    months         = _calc_months(sorted_closed, start_date, end_date)
    net_return_pct = net_r * risk_pct
    monthly_return = net_return_pct / months

    # ── Monthly breakdown ─────────────────────────────────────────────────────
    monthly_breakdown = _build_monthly_breakdown(sorted_closed, risk_pct)

    # ── Goal evaluation ───────────────────────────────────────────────────────
    pf_display = round(profit_factor, 2) if not math.isinf(profit_factor) else 999.0

    goal_detail = {
        "monthly_return": {
            "value":  round(monthly_return, 2),
            "target": f"{GOALS['monthly_return_min']}%–{GOALS['monthly_return_max']}%",
            "status": _rate_monthly_return(monthly_return),
        },
        "max_drawdown": {
            "value":  round(max_dd, 2),
            "target": f"< {GOALS['max_drawdown_limit']}%",
            "status": _rate_drawdown(max_dd),
        },
        "profit_factor": {
            "value":  pf_display,
            "target": f"> {GOALS['profit_factor_min']}",
            "status": _rate_profit_factor(profit_factor),
        },
    }

    statuses    = [g["status"] for g in goal_detail.values()]
    goal_status = _composite_status(statuses)

    return {
        "total_trades":      len(closed),
        "open_trades":       len(open_trades),
        "win_trades":        len(wins),
        "loss_trades":       len(losses),
        "win_rate":          round(win_rate, 1),
        "profit_factor":     pf_display,
        "gross_profit_r":    round(gross_profit, 2),
        "gross_loss_r":      round(gross_loss, 2),
        "net_r":             round(net_r, 2),
        "monthly_return":    round(monthly_return, 2),
        "max_drawdown":      round(max_dd, 2),
        "goal_status":       goal_status,
        "goal_detail":       goal_detail,
        "goals":             GOALS,
        "equity_curve":      equity,
        "monthly_breakdown": monthly_breakdown,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_monthly_breakdown(sorted_closed: list[dict], risk_pct: float) -> list[dict]:
    """Return a list of {month, trades, wins, losses, net_r, return_pct} dicts."""
    buckets: dict[str, dict] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "net_r": 0.0}
    )

    for t in sorted_closed:
        key = t["date"][:7]  # YYYY-MM
        b   = buckets[key]
        b["trades"] += 1
        b["net_r"]  += t["r_multiple"]
        if t["result"] == "win":
            b["wins"]   += 1
        else:
            b["losses"] += 1

    breakdown = []
    for month in sorted(buckets):
        b = buckets[month]
        breakdown.append({
            "month":      month,
            "trades":     b["trades"],
            "wins":       b["wins"],
            "losses":     b["losses"],
            "net_r":      round(b["net_r"], 2),
            "return_pct": round(b["net_r"] * risk_pct, 2),
        })

    return breakdown


def _calc_months(
    sorted_closed: list[dict],
    start_date: str | None,
    end_date:   str | None,
) -> float:
    """Estimate calendar months spanned by the backtest."""
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


def _rate_monthly_return(value: float) -> str:
    lo, hi = GOALS["monthly_return_min"], GOALS["monthly_return_max"]
    if lo <= value <= hi:
        return "PASS"
    if _WATCHLIST_MONTHLY_LO <= value < lo or hi < value <= _WATCHLIST_MONTHLY_HI:
        return "WATCHLIST"
    return "FAIL"


def _rate_drawdown(value: float) -> str:
    limit = GOALS["max_drawdown_limit"]
    if value < limit:
        return "PASS"
    if value < _WATCHLIST_DD:
        return "WATCHLIST"
    return "FAIL"


def _rate_profit_factor(value: float) -> str:
    if value >= GOALS["profit_factor_min"]:
        return "PASS"
    if value >= _WATCHLIST_PF:
        return "WATCHLIST"
    return "FAIL"


def _composite_status(statuses: list[str]) -> str:
    if all(s == "PASS" for s in statuses):
        return "PASS"
    if any(s == "FAIL" for s in statuses):
        return "FAIL"
    return "WATCHLIST"
