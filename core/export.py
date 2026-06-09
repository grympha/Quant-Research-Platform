"""
CSV / JSON Export Utilities — Phase 2 + Persistent History.

All files are written under  data/exports/  so they are easy to find and
can be downloaded from the Streamlit Export Center.

Auto-appended files:
  data/exports/trade_log.csv        — one row per trade across all runs
  data/exports/research_summary.csv — one row per analysis run

Per-research exports (generated on demand):
  data/exports/trade_log_{research_id[:8]}.csv
  data/exports/monthly_{research_id[:8]}.csv
  data/exports/summary_{research_id[:8]}.csv
  data/exports/report_{research_id[:8]}.json
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

_EXPORT_DIR = Path("data/exports")
_TRADE_LOG  = _EXPORT_DIR / "trade_log.csv"
_SUMMARY    = _EXPORT_DIR / "research_summary.csv"

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
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Append rows; write header only if file is new/empty."""
    _ensure_dir()
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


# ── Append-mode exports (called after every analysis run) ─────────────────────

def save_trade_log(trades: list[dict], run_id: str) -> Path:
    rows = [
        {
            "run_id":       run_id,
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
    report: dict,
    run_id: str,
    filename: str,
    timeframe: str,
    module: str,
    risk_pct: float,
    rr: float,
    lookback: int,
    symbol: str = "XAUUSD",
) -> Path:
    row = {
        "run_id":         run_id,
        "run_date":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol":         symbol,
        "filename":       filename,
        "timeframe":      timeframe,
        "module":         module,
        "risk_pct":       risk_pct,
        "rr":             rr,
        "lookback":       lookback,
        "total_trades":   report.get("total_trades", 0),
        "win_trades":     report.get("win_trades", 0),
        "loss_trades":    report.get("loss_trades", 0),
        "win_rate":       report.get("win_rate", 0.0),
        "profit_factor":  report.get("profit_factor", 0.0),
        "net_r":          report.get("net_r", 0.0),
        "max_drawdown":   report.get("max_drawdown", 0.0),
        "monthly_return": report.get("monthly_return", 0.0),
        "goal_status":    report.get("goal_status", ""),
    }
    _write_csv(_SUMMARY, _SUMMARY_FIELDS, [row])
    return _SUMMARY.resolve()


# ── On-demand per-research exports ────────────────────────────────────────────

def build_trade_log_csv(trades: list[dict]) -> bytes:
    """Return trade log as UTF-8 CSV bytes (for Streamlit download_button)."""
    buf = io.StringIO()
    if trades:
        writer = csv.DictWriter(
            buf,
            fieldnames=[k for k in trades[0] if not k.startswith("_")],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(trades)
    return buf.getvalue().encode("utf-8")


def build_monthly_csv(monthly: list[dict]) -> bytes:
    buf = io.StringIO()
    if monthly:
        writer = csv.DictWriter(buf, fieldnames=list(monthly[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(monthly)
    return buf.getvalue().encode("utf-8")


def build_summary_csv(report: dict, meta: dict) -> bytes:
    """Single-row summary CSV with all key metrics."""
    row = {**meta, **{k: v for k, v in report.items() if not isinstance(v, (list, dict))}}
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(row.keys()), extrasaction="ignore")
    writer.writeheader()
    writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def build_report_json(result: dict) -> bytes:
    """Full analysis result as indented JSON bytes."""
    return json.dumps(result, indent=2, default=str).encode("utf-8")


def save_module_comparison(rows: list[dict], run_datetime: str = "") -> Path:
    """
    Append module comparison rows to data/exports/module_comparison.csv.
    Each call appends one block (one row per module) with a run timestamp.
    Returns the absolute path of the file.
    """
    _ensure_dir()
    path = _EXPORT_DIR / "module_comparison.csv"

    if not rows:
        return path.resolve()

    # Inject run_datetime into each row for traceability
    stamped = [{**r, "run_datetime": run_datetime} for r in rows]
    fields  = list(stamped[0].keys())

    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(stamped)

    return path.resolve()


def save_per_research_exports(research_id: str, result: dict) -> dict[str, Path]:
    """
    Write all per-research exports to data/exports/.
    Returns dict of {export_type: absolute_path}.
    """
    _ensure_dir()
    short = research_id[:8]
    trades  = result.get("trades", [])
    rpt     = result.get("report", {})
    monthly = rpt.get("monthly_breakdown", [])

    paths: dict[str, Path] = {}

    # Trade log
    p = _EXPORT_DIR / f"trade_log_{short}.csv"
    p.write_bytes(build_trade_log_csv(trades))
    paths["trade_log"] = p.resolve()

    # Monthly breakdown
    p = _EXPORT_DIR / f"monthly_{short}.csv"
    p.write_bytes(build_monthly_csv(monthly))
    paths["monthly"] = p.resolve()

    # Research summary
    meta = {
        "research_id": research_id,
        "module":      result.get("module"),
        "timeframe":   result.get("timeframe"),
        "mode":        result.get("analysis_mode"),
    }
    p = _EXPORT_DIR / f"summary_{short}.csv"
    p.write_bytes(build_summary_csv(rpt, meta))
    paths["summary"] = p.resolve()

    # Full JSON
    p = _EXPORT_DIR / f"report_{short}.json"
    p.write_bytes(build_report_json(result))
    paths["report_json"] = p.resolve()

    return paths
