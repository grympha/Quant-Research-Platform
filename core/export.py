"""
CSV Export Utilities — Phase 2
================================
Saves backtest results to two files:

  data/trade_log.csv        — one row per trade (appended each run)
  data/research_summary.csv — one row per analysis run (appended)
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR   = Path("data")
_TRADE_LOG  = _DATA_DIR / "trade_log.csv"
_SUMMARY    = _DATA_DIR / "research_summary.csv"

_TRADE_FIELDS = [
    "run_id", "date", "time", "direction", "swept_level",
    "entry", "sl", "tp", "exit_price",
    "result", "r_multiple", "bars_held",
]

_SUMMARY_FIELDS = [
    "run_id", "run_date", "symbol", "filename", "timeframe",
    "module", "risk_pct", "rr", "lookback",
    "total_trades", "win_trades", "loss_trades",
    "win_rate", "profit_factor", "net_r",
    "max_drawdown", "monthly_return", "goal_status",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Append rows to a CSV, writing the header only if the file is new/empty."""
    _ensure_dir()
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


# ── Public API ────────────────────────────────────────────────────────────────

def save_trade_log(trades: list[dict], run_id: str) -> Path:
    """
    Append all trades from this run to data/trade_log.csv.

    Parameters
    ----------
    trades : Trade list from liquidity_sweep.run() / backtest.execute().
    run_id : UUID of the analysis (links rows to research_summary).

    Returns
    -------
    Absolute path to the file.
    """
    rows = [
        {
            "run_id": run_id,
            "date":         t.get("date", ""),
            "time":         t.get("time", ""),
            "direction":    t.get("direction", ""),
            "swept_level":  t.get("swept_level", ""),
            "entry":        t.get("entry", ""),
            "sl":           t.get("sl", ""),
            "tp":           t.get("tp", ""),
            "exit_price":   t.get("exit_price", ""),
            "result":       t.get("result", ""),
            "r_multiple":   t.get("r_multiple", ""),
            "bars_held":    t.get("bars_held", ""),
        }
        for t in trades
    ]
    _write_csv(_TRADE_LOG, _TRADE_FIELDS, rows)
    return _TRADE_LOG.resolve()


def save_research_summary(
    report:    dict,
    run_id:    str,
    filename:  str,
    timeframe: str,
    module:    str,
    risk_pct:  float,
    rr:        float,
    lookback:  int,
    symbol:    str = "XAUUSD",
) -> Path:
    """
    Append one summary row to data/research_summary.csv.

    Returns
    -------
    Absolute path to the file.
    """
    row = {
        "run_id":        run_id,
        "run_date":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol":        symbol,
        "filename":      filename,
        "timeframe":     timeframe,
        "module":        module,
        "risk_pct":      risk_pct,
        "rr":            rr,
        "lookback":      lookback,
        "total_trades":  report.get("total_trades", 0),
        "win_trades":    report.get("win_trades", 0),
        "loss_trades":   report.get("loss_trades", 0),
        "win_rate":      report.get("win_rate", 0.0),
        "profit_factor": report.get("profit_factor", 0.0),
        "net_r":         report.get("net_r", 0.0),
        "max_drawdown":  report.get("max_drawdown", 0.0),
        "monthly_return":report.get("monthly_return", 0.0),
        "goal_status":   report.get("goal_status", ""),
    }
    _write_csv(_SUMMARY, _SUMMARY_FIELDS, [row])
    return _SUMMARY.resolve()
