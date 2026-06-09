"""
frontend/services.py — Dual-mode service layer

Auto-detects deployment mode from environment:
  API_BASE_URL set   → "api" mode   (HTTP calls to FastAPI backend)
  API_BASE_URL unset → "streamlit_only" mode (direct Python/SQLite — no uvicorn needed)

All service functions raise SvcError on failure.
Callers do: try: result = svc.run_analysis(payload) except svc.SvcError as e: st.error(e.message)
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# ── Mode detection ─────────────────────────────────────────────────────────────
_API_BASE: str = os.environ.get("API_BASE_URL", "").rstrip("/")
RUN_MODE: str  = "api" if _API_BASE else "streamlit_only"

# ── Error class ────────────────────────────────────────────────────────────────
class SvcError(Exception):
    def __init__(self, message: str, status_code: int = 500, detail: dict | None = None):
        super().__init__(message)
        self.message     = message
        self.status_code = status_code
        self.detail      = detail or {}

# ── Imports (both modes need requests; local mode also imports project modules) ─
import requests as _http   # noqa: E402  (used in API mode and for exceptions)

if RUN_MODE == "streamlit_only":
    # Ensure project root is on sys.path so core/ and database/ are importable
    _root = str(Path(__file__).resolve().parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from core import data_loader as _dl
    from core import export as _exp
    from core import report as _rg
    from core.modules import break_retest as _br
    from core.modules import liquidity_sweep as _ls
    from database.db import (
        init_db                  as _init_db,
        list_datasets            as _list_datasets,
        get_dataset_by_id        as _get_dataset_by_id,
        get_dataset_by_hash      as _get_dataset_by_hash,
        save_dataset_metadata    as _save_dataset_metadata,
        save_dataset_candles     as _save_dataset_candles,
        delete_dataset           as _delete_dataset,
        get_dataset_candles      as _get_dataset_candles,
        dataset_exists           as _dataset_exists,
        save_upload              as _save_upload,
        get_db_health            as _get_db_health,
        reset_database           as _reset_db,
        list_research_runs       as _list_runs,
        get_research_run         as _get_run,
        delete_research_run      as _delete_run,
        get_trade_logs           as _get_trades,
        get_monthly_reports      as _get_monthly,
        save_research_run_complete as _save_run,
        save_analysis            as _save_analysis,
        list_goal_profiles       as _list_gp,
        get_goal_profile         as _get_gp,
        get_default_goal_profile as _get_default_gp,
        create_goal_profile      as _create_gp,
        update_goal_profile      as _update_gp,
        delete_goal_profile      as _delete_gp,
        set_default_goal_profile as _set_default_gp,
    )
    from core.data_loader import load_from_dataset_id as _load_from_dataset_id

    # Bootstrap on first import
    _init_db()
    Path("data/uploads").mkdir(parents=True, exist_ok=True)
    Path("data/exports").mkdir(parents=True, exist_ok=True)
    _UPLOAD_DIR = Path("data/uploads")


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

def health() -> dict:
    if RUN_MODE == "streamlit_only":
        return {"status": "ok", "version": "3.1.0", "mode": "streamlit_only"}
    r = _http.get(f"{_API_BASE}/health", timeout=2)
    r.raise_for_status()
    return r.json()


def db_health() -> dict:
    if RUN_MODE == "streamlit_only":
        try:
            return _get_db_health()
        except Exception as exc:
            return {"connection": f"error: {exc}"}
    try:
        r = _http.get(f"{_API_BASE}/api/v1/health/db", timeout=5)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def reset_db(confirm: str) -> dict:
    if RUN_MODE == "streamlit_only":
        if confirm != "RESET":
            raise SvcError("Pass confirm='RESET' to execute.", 400)
        try:
            _reset_db()
            _init_db()
            return {"status": "reset_complete"}
        except Exception as exc:
            raise SvcError(f"Reset failed: {exc}", 500)
    r = _http.post(f"{_API_BASE}/api/v1/admin/reset-db?confirm={confirm}", timeout=30)
    if r.status_code != 200:
        raise SvcError(r.text, r.status_code)
    return r.json()


# ══════════════════════════════════════════════════════════════════════════════
# DATASETS
# ══════════════════════════════════════════════════════════════════════════════

def list_datasets() -> list[dict]:
    if RUN_MODE == "streamlit_only":
        try:
            return _list_datasets()
        except Exception:
            return []
    try:
        r = _http.get(f"{_API_BASE}/api/v1/datasets", timeout=5)
        return r.json().get("datasets", []) if r.status_code == 200 else []
    except Exception:
        return []


def upload_dataset(file_bytes: bytes, filename: str, timeframe: str) -> dict:
    """
    Store a dataset. Returns the same dict structure as POST /api/v1/datasets.
    Raises SvcError(status_code=409, detail={...}) on duplicate.
    """
    if RUN_MODE == "api":
        r = _http.post(
            f"{_API_BASE}/api/v1/datasets",
            files={"file": (filename, file_bytes, "text/csv")},
            params={"timeframe": timeframe},
            timeout=60,
        )
        if r.status_code == 409:
            try:
                d = r.json().get("detail", {})
            except Exception:
                d = {}
            raise SvcError("duplicate", 409, detail=d)
        if r.status_code != 200:
            try:
                msg = r.json().get("detail", r.text)
                if isinstance(msg, dict):
                    msg = msg.get("message", str(msg))
            except Exception:
                msg = r.text
            raise SvcError(str(msg), r.status_code)
        return r.json()

    # ── Local mode ──────────────────────────────────────────────────────────
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    existing  = _get_dataset_by_hash(file_hash)
    if existing:
        raise SvcError("duplicate", 409, detail={
            "message":              "File already in library — reusing existing dataset.",
            "existing_dataset_id":  existing["dataset_id"],
            "existing_timeframe":   existing["timeframe"],
            "existing_filename":    existing["filename"],
        })

    try:
        df = _dl.load_csv(file_bytes)
    except ValueError as exc:
        raise SvcError(str(exc), 422)
    except Exception as exc:
        raise SvcError(f"CSV parse error: {exc}", 500)

    validation = _dl.validate_dataframe(df)
    if not validation["valid"]:
        raise SvcError(f"CSV validation failed: {validation['errors']}", 422)

    tf = timeframe or _dl.detect_timeframe(filename) or "UNKNOWN"
    dataset_id = _save_dataset_metadata(
        symbol="XAUUSD", timeframe=tf, filename=filename,
        file_hash=file_hash, total_rows=len(df),
        start_datetime=str(df.index[0]),
        end_datetime=str(df.index[-1]),
    )

    candles = [
        {
            "dt": str(idx),
            "open":   float(row["Open"]),
            "high":   float(row["High"]),
            "low":    float(row["Low"]),
            "close":  float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        for idx, row in df.iterrows()
    ]
    _save_dataset_candles(dataset_id, candles)

    dest = _UPLOAD_DIR / filename
    dest.write_bytes(file_bytes)
    _save_upload(filename, str(dest), len(df))

    return {
        "dataset_id": dataset_id,
        "timeframe":  tf,
        "symbol":     "XAUUSD",
        "filename":   filename,
        "rows":       len(df),
        "start":      str(df.index[0]),
        "end":        str(df.index[-1]),
        "validation": validation,
    }


def delete_dataset(dataset_id: str) -> bool:
    if RUN_MODE == "streamlit_only":
        try:
            return _delete_dataset(dataset_id)
        except Exception as exc:
            raise SvcError(str(exc))
    r = _http.delete(f"{_API_BASE}/api/v1/datasets/{dataset_id}", timeout=10)
    if r.status_code == 200:
        return True
    raise SvcError(r.text, r.status_code)


def export_dataset_csv(dataset_id: str) -> bytes:
    if RUN_MODE == "api":
        r = _http.get(f"{_API_BASE}/api/v1/datasets/{dataset_id}/export", timeout=30)
        if r.status_code == 200:
            return r.content
        raise SvcError(r.text, r.status_code)

    meta    = _get_dataset_by_id(dataset_id)
    if not meta:
        raise SvcError("Dataset not found.", 404)
    candles = _get_dataset_candles(dataset_id)
    if not candles:
        raise SvcError("No candles found.", 404)

    df = pd.DataFrame(candles)
    df["dt"] = pd.to_datetime(df["dt"])
    out = pd.DataFrame({
        "Date":   df["dt"].dt.strftime("%Y.%m.%d"),
        "Time":   df["dt"].dt.strftime("%H:%M"),
        "Open":   df["open"].values,
        "High":   df["high"].values,
        "Low":    df["low"].values,
        "Close":  df["close"].values,
        "Volume": df["volume"].values,
    })
    return out.to_csv(index=False).encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS — local pipeline helpers
# ══════════════════════════════════════════════════════════════════════════════

def _san_float(v: object) -> object:
    if isinstance(v, float):
        if math.isnan(v):  return 0.0
        if math.isinf(v):  return 999.0 if v > 0 else -999.0
    return v


def _san_report(rpt: dict) -> dict:
    out: dict = {}
    for k, v in rpt.items():
        if isinstance(v, float):
            out[k] = _san_float(v)
        elif isinstance(v, list):
            out[k] = [
                {ik: _san_float(iv) for ik, iv in item.items()}
                if isinstance(item, dict) else _san_float(item)
                for item in v
            ]
        elif isinstance(v, dict):
            out[k] = {ik: (_san_float(iv) if isinstance(iv, float) else iv) for ik, iv in v.items()}
        else:
            out[k] = v
    return out


def _local_run_analysis(payload: dict) -> dict:
    """Replicate main.py:_run_analysis() without FastAPI."""
    module           = payload.get("module", "liquidity_sweep")
    analysis_mode    = payload.get("analysis_mode", "single")
    trend_tf         = payload.get("trend_tf")
    structure_tf     = payload.get("structure_tf")
    entry_tf         = payload.get("entry_tf")
    risk_pct         = float(payload.get("risk_pct", 1.0))
    rr               = float(payload.get("rr", 2.0))
    lookback         = int(payload.get("lookback", 5))
    max_bars         = int(payload.get("max_bars", 200))
    breakout_buffer  = float(payload.get("breakout_buffer", 0.10))
    retest_tolerance = float(payload.get("retest_tolerance", 0.50))
    backtest_start   = payload.get("backtest_start")
    backtest_end     = payload.get("backtest_end")
    analysis_sub_mode = payload.get("analysis_sub_mode", "full_backtest")
    research_name    = payload.get("research_name")
    goal_profile_id  = payload.get("goal_profile_id")
    timeframe        = payload.get("timeframe", "H1")

    # ── Resolve data ─────────────────────────────────────────────────────────
    data_by_timeframe: dict[str, Any] = {}
    dataset_ids_used:  dict[str, str] = {}
    export_filename = "unknown"

    if analysis_mode == "multi":
        src = payload.get("dataset_ids", {})
        if not src:
            raise SvcError("dataset_ids required for multi-TF mode.", 400)
        for tf, did in src.items():
            if not _dataset_exists(did):
                raise SvcError(f"Dataset '{did}' not found for TF '{tf}'.", 404)
            try:
                data_by_timeframe[tf] = _load_from_dataset_id(did)
            except Exception as exc:
                raise SvcError(f"Failed to load dataset '{tf}': {exc}", 500)
            dataset_ids_used[tf] = did
        primary_tf = structure_tf or next(iter(data_by_timeframe))
        if primary_tf not in data_by_timeframe:
            raise SvcError(f"Structure TF '{primary_tf}' not in dataset_ids.", 400)
        export_filename = "multi_tf_" + "_".join(sorted(src.keys()))
    else:
        did = payload.get("dataset_id")
        if not did:
            raise SvcError("dataset_id required for single-TF mode.", 400)
        if not _dataset_exists(did):
            raise SvcError("Dataset not found.", 404)
        meta = _get_dataset_by_id(did)
        try:
            df_loaded = _load_from_dataset_id(did)
        except Exception as exc:
            raise SvcError(f"Failed to load dataset: {exc}", 500)
        data_by_timeframe[timeframe] = df_loaded
        primary_tf = timeframe
        dataset_ids_used[timeframe] = did
        export_filename = meta["filename"] if meta else did[:8]

    # ── Date filter ──────────────────────────────────────────────────────────
    if backtest_start or backtest_end:
        for _tf in list(data_by_timeframe.keys()):
            _df = data_by_timeframe[_tf]
            try:
                if backtest_start:
                    _df = _df[_df.index >= pd.Timestamp(backtest_start)]
                if backtest_end:
                    _df = _df[_df.index < pd.Timestamp(backtest_end) + pd.Timedelta(days=1)]
            except Exception as exc:
                raise SvcError(f"Invalid date range: {exc}", 400)
            if _df.empty:
                raise SvcError(
                    f"No data for '{_tf}' in range {backtest_start} → {backtest_end}.", 400
                )
            data_by_timeframe[_tf] = _df

    primary_df = data_by_timeframe[primary_tf]
    multi_data = data_by_timeframe if analysis_mode == "multi" else None

    # ── Dispatch module ──────────────────────────────────────────────────────
    if module == "liquidity_sweep":
        trades = _ls.run(
            primary_df, rr=rr, lookback=lookback, max_bars=max_bars,
            data_by_timeframe=multi_data, trend_tf=trend_tf,
            structure_tf=structure_tf, entry_tf=entry_tf,
        )
    elif module == "break_retest":
        trades = _br.run(
            primary_df, rr=rr, lookback=lookback, max_bars=max_bars,
            breakout_buffer=breakout_buffer, retest_tolerance=retest_tolerance,
            data_by_timeframe=multi_data, trend_tf=trend_tf,
            structure_tf=structure_tf, entry_tf=entry_tf,
        )
    else:
        raise SvcError(f"Unknown module: {module!r}", 400)

    # ── Goal profile ─────────────────────────────────────────────────────────
    goal_profile = _get_gp(goal_profile_id) if goal_profile_id else None
    if not goal_profile:
        goal_profile = _get_default_gp()

    goals_for_report = None
    if goal_profile:
        goals_for_report = {
            "monthly_return_target":   goal_profile["monthly_return_target"],
            "daily_drawdown_target":   goal_profile["daily_drawdown_target"],
            "monthly_drawdown_target": goal_profile["monthly_drawdown_target"],
            "profit_factor_target":    goal_profile["profit_factor_target"],
            "_profile_id":             goal_profile["id"],
            "_profile_name":           goal_profile["profile_name"],
        }

    rpt = _san_report(_rg.generate(
        trades, risk_pct,
        start_date=str(primary_df.index[0]),
        end_date=str(primary_df.index[-1]),
        goals=goals_for_report,
    ))

    clean_trades = [{k: v for k, v in t.items() if not k.startswith("_")} for t in trades]
    _gp_snapshot = json.dumps({
        k: v for k, v in (goals_for_report or {}).items() if not k.startswith("_")
    }) if goals_for_report else None

    research_id = _save_run(
        research_name        = research_name,
        selected_module      = module,
        symbol               = "XAUUSD",
        timeframe_mode       = analysis_mode,
        timeframes_used      = list(data_by_timeframe.keys()),
        dataset_ids_used     = dataset_ids_used,
        risk_percent         = risk_pct,
        reward_risk_ratio    = rr,
        lookback             = lookback,
        report               = rpt,
        trades               = clean_trades,
        monthly_breakdown    = rpt.get("monthly_breakdown", []),
        backtest_start       = backtest_start,
        backtest_end         = backtest_end,
        analysis_sub_mode    = analysis_sub_mode,
        goal_profile_id      = goal_profile["id"]           if goal_profile else None,
        goal_profile_name    = goal_profile["profile_name"] if goal_profile else None,
        goal_values_snapshot = _gp_snapshot,
    )

    _save_analysis(research_id, module, primary_tf, risk_pct, rr, rpt)
    trade_log_path = str(_exp.save_trade_log(clean_trades, run_id=research_id))
    summary_path   = str(_exp.save_research_summary(
        report=rpt, run_id=research_id, filename=export_filename,
        timeframe=primary_tf, module=module,
        risk_pct=risk_pct, rr=rr, lookback=lookback,
    ))

    result_payload: dict = {
        "research_id":       research_id,
        "analysis_id":       research_id,
        "symbol":            "XAUUSD",
        "module":            module,
        "analysis_mode":     analysis_mode,
        "analysis_sub_mode": analysis_sub_mode,
        "backtest_start":    backtest_start,
        "backtest_end":      backtest_end,
        "timeframe":         primary_tf,
        "timeframes_used":   list(data_by_timeframe.keys()),
        "structure_tf":      structure_tf,
        "entry_tf":          entry_tf,
        "trend_tf":          trend_tf,
        "goal_profile_id":   goal_profile["id"]           if goal_profile else None,
        "goal_profile_name": goal_profile["profile_name"] if goal_profile else None,
        "parameters": {
            "risk_pct":          risk_pct,
            "rr":                rr,
            "lookback":          lookback,
            "breakout_buffer":   breakout_buffer,
            "retest_tolerance":  retest_tolerance,
        },
        "trades":  clean_trades,
        "report":  rpt,
        "exports": {"trade_log": trade_log_path, "research_summary": summary_path},
    }

    per_run_paths = _exp.save_per_research_exports(research_id, result_payload)
    result_payload["exports"].update({k: str(v) for k, v in per_run_paths.items()})

    return result_payload


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS — public API
# ══════════════════════════════════════════════════════════════════════════════

def run_analysis(payload: dict) -> dict:
    if RUN_MODE == "streamlit_only":
        return _local_run_analysis(payload)
    try:
        r = _http.post(f"{_API_BASE}/api/v1/analyze", json=payload, timeout=120)
    except _http.exceptions.ConnectionError as exc:
        raise SvcError(f"Connection error: {exc}", 503)
    if r.status_code != 200:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise SvcError(str(detail), r.status_code)
    return r.json()


def run_comparison(payload: dict) -> dict:
    _MOD_DISPLAY = {"liquidity_sweep": "Liquidity Sweep", "break_retest": "Break & Retest"}

    if RUN_MODE == "streamlit_only":
        modules = payload.get("modules", ["liquidity_sweep", "break_retest"])
        cmp_name = payload.get("comparison_name", "Module Comparison")
        results: dict[str, dict] = {}
        errors:  dict[str, str]  = {}
        for mod in modules:
            mod_payload = {
                **payload,
                "module":        mod,
                "research_name": f"{cmp_name} [{_MOD_DISPLAY.get(mod, mod)}]",
            }
            try:
                results[mod] = _local_run_analysis(mod_payload)
            except SvcError as exc:
                errors[mod] = exc.message
            except Exception as exc:
                errors[mod] = f"{type(exc).__name__}: {exc}"

        comparison = []
        for mod in modules:
            if mod in results:
                rpt = results[mod].get("report", {})
                comparison.append({
                    "Module":            _MOD_DISPLAY.get(mod, mod),
                    "Module ID":         mod,
                    "Total Trades":      rpt.get("total_trades", 0),
                    "Wins":              rpt.get("win_trades", 0),
                    "Losses":            rpt.get("loss_trades", 0),
                    "Win Rate %":        rpt.get("win_rate", 0.0),
                    "Profit Factor":     rpt.get("profit_factor", 0.0),
                    "Net R":             rpt.get("net_r", 0.0),
                    "Monthly Return %":  rpt.get("monthly_return", 0.0),
                    "Max Drawdown %":    rpt.get("max_drawdown", 0.0),
                    "Goal Status":       rpt.get("goal_status", "—"),
                    "Research ID":       results[mod].get("research_id", ""),
                })
            else:
                comparison.append({
                    "Module":    _MOD_DISPLAY.get(mod, mod),
                    "Module ID": mod,
                    "Error":     errors.get(mod, "unknown error"),
                })

        run_dt   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cmp_path = str(_exp.save_module_comparison(comparison, run_datetime=run_dt))
        return {
            "comparison":   comparison,
            "results":      results,
            "errors":       errors,
            "export_path":  cmp_path,
            "run_datetime": run_dt,
        }

    try:
        r = _http.post(f"{_API_BASE}/api/v1/compare", json=payload, timeout=180)
    except _http.exceptions.ConnectionError as exc:
        raise SvcError(f"Connection error: {exc}", 503)
    if r.status_code != 200:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise SvcError(str(detail), r.status_code)
    return r.json()


# ══════════════════════════════════════════════════════════════════════════════
# RESEARCH HISTORY
# ══════════════════════════════════════════════════════════════════════════════

def list_research_runs(limit: int = 50) -> list[dict]:
    if RUN_MODE == "streamlit_only":
        try:
            return _list_runs(limit)
        except Exception:
            return []
    try:
        r = _http.get(f"{_API_BASE}/api/v1/research?limit={limit}", timeout=5)
        return r.json().get("research_runs", []) if r.status_code == 200 else []
    except Exception:
        return []


def get_research_run(research_id: str) -> dict | None:
    if RUN_MODE == "streamlit_only":
        return _get_run(research_id)
    try:
        r = _http.get(f"{_API_BASE}/api/v1/research/{research_id}", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def delete_research_run(research_id: str) -> bool:
    if RUN_MODE == "streamlit_only":
        return bool(_delete_run(research_id))
    r = _http.delete(f"{_API_BASE}/api/v1/research/{research_id}", timeout=10)
    return r.status_code == 200


def export_research(research_id: str, fmt: str) -> bytes:
    if RUN_MODE == "api":
        r = _http.get(f"{_API_BASE}/api/v1/research/{research_id}/export/{fmt}", timeout=15)
        if r.status_code == 200:
            return r.content
        raise SvcError(r.text, r.status_code)

    run = _get_run(research_id)
    if not run:
        raise SvcError("Research run not found.", 404)

    report_dict = json.loads(run["full_report"]) if run.get("full_report") else {}
    short       = research_id[:8]
    _MYT        = timezone(timedelta(hours=8))

    def _myt(dt_str: str) -> str:
        try:
            ts = pd.Timestamp(dt_str)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            return ts.tz_convert(_MYT).strftime("%d-%m-%Y %H:%M") + " MYT"
        except Exception:
            return dt_str

    if fmt == "trade_log":
        return _exp.build_trade_log_csv(_get_trades(research_id))

    if fmt == "monthly":
        return _exp.build_monthly_csv(_get_monthly(research_id))

    if fmt == "summary":
        meta = {
            "research_id":          research_id,
            "module":               run["selected_module"],
            "timeframe":            run["timeframe_mode"],
            "created_datetime_utc": run["created_datetime"],
            "created_datetime_myt": _myt(run["created_datetime"]),
            "goal_profile_name":    run.get("goal_profile_name") or "Legacy Goal Profile",
            "goal_values":          run.get("goal_values_snapshot") or "{}",
        }
        return _exp.build_summary_csv(report_dict, meta)

    if fmt == "report":
        result_full = {
            "research_id":          research_id,
            "research_name":        run.get("research_name"),
            "created":              run["created_datetime"],
            "created_datetime_myt": _myt(run["created_datetime"]),
            "module":               run["selected_module"],
            "symbol":               run["symbol"],
            "timeframe_mode":       run["timeframe_mode"],
            "timeframes":           json.loads(run["timeframes_used"]),
            "goal_profile_name":    run.get("goal_profile_name") or "Legacy Goal Profile",
            "goal_values":          json.loads(run["goal_values_snapshot"])
                                    if run.get("goal_values_snapshot") else {},
            "parameters": {
                "risk_pct": run["risk_percent"],
                "rr":       run["reward_risk_ratio"],
                "lookback": run["lookback"],
            },
            "report":  report_dict,
            "trades":  _get_trades(research_id),
            "monthly": _get_monthly(research_id),
        }
        return _exp.build_report_json(result_full)

    raise SvcError(f"Unknown export format '{fmt}'.", 400)


# ══════════════════════════════════════════════════════════════════════════════
# GOAL PROFILES
# ══════════════════════════════════════════════════════════════════════════════

def list_goal_profiles() -> list[dict]:
    if RUN_MODE == "streamlit_only":
        try:
            return _list_gp()
        except Exception:
            return []
    try:
        r = _http.get(f"{_API_BASE}/api/v1/goals/profiles", timeout=5)
        return r.json().get("profiles", []) if r.status_code == 200 else []
    except Exception:
        return []


def create_goal_profile(data: dict) -> dict:
    if RUN_MODE == "streamlit_only":
        try:
            pid = _create_gp(
                profile_name            = data["profile_name"],
                monthly_return_target   = data["monthly_return_target"],
                daily_drawdown_target   = data["daily_drawdown_target"],
                monthly_drawdown_target = data["monthly_drawdown_target"],
                profit_factor_target    = data["profit_factor_target"],
                is_default              = data.get("is_default", False),
            )
            return {"profile_id": pid, "message": f"Profile '{data['profile_name']}' created."}
        except Exception as exc:
            raise SvcError(str(exc), 400)
    r = _http.post(f"{_API_BASE}/api/v1/goals/profiles", json=data, timeout=10)
    if r.status_code != 200:
        try:
            msg = r.json().get("detail", r.text)
        except Exception:
            msg = r.text
        raise SvcError(str(msg), r.status_code)
    return r.json()


def update_goal_profile(profile_id: str, data: dict) -> dict:
    if RUN_MODE == "streamlit_only":
        ok = _update_gp(
            profile_id,
            profile_name            = data["profile_name"],
            monthly_return_target   = data["monthly_return_target"],
            daily_drawdown_target   = data["daily_drawdown_target"],
            monthly_drawdown_target = data["monthly_drawdown_target"],
            profit_factor_target    = data["profit_factor_target"],
        )
        if not ok:
            raise SvcError("Profile not found.", 404)
        return {"updated": profile_id}
    r = _http.put(f"{_API_BASE}/api/v1/goals/profiles/{profile_id}", json=data, timeout=10)
    if r.status_code != 200:
        try:
            msg = r.json().get("detail", r.text)
        except Exception:
            msg = r.text
        raise SvcError(str(msg), r.status_code)
    return r.json()


def delete_goal_profile(profile_id: str) -> bool:
    if RUN_MODE == "streamlit_only":
        try:
            ok = _delete_gp(profile_id)
            if not ok:
                raise SvcError("Profile not found.", 404)
            return True
        except ValueError as exc:
            raise SvcError(str(exc), 400)
    r = _http.delete(f"{_API_BASE}/api/v1/goals/profiles/{profile_id}", timeout=10)
    if r.status_code == 200:
        return True
    try:
        msg = r.json().get("detail", r.text)
    except Exception:
        msg = r.text
    raise SvcError(str(msg), r.status_code)


def set_default_goal_profile(profile_id: str) -> bool:
    if RUN_MODE == "streamlit_only":
        ok = _set_default_gp(profile_id)
        if not ok:
            raise SvcError("Profile not found.", 404)
        return True
    r = _http.post(f"{_API_BASE}/api/v1/goals/profiles/{profile_id}/set-default", timeout=10)
    if r.status_code == 200:
        return True
    raise SvcError(r.text, r.status_code)
