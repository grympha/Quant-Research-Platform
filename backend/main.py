"""
XAUUSD Quant Research Platform — FastAPI Backend (Phase 2 + Dataset Library)
=============================================================================
Endpoints
─────────
GET  /health
GET  /api/v1/modules

─── Dataset Library ───────────────────────────────────────────────────────────
POST   /api/v1/datasets              Upload CSV → hash-dedup → store candles
GET    /api/v1/datasets              List all stored datasets
DELETE /api/v1/datasets/{id}         Delete dataset + all its candles
GET    /api/v1/datasets/{id}/export  Export dataset candles as MT5 CSV

─── Analysis ──────────────────────────────────────────────────────────────────
POST /api/v1/analyze   Run strategy; accepts dataset_id(s) OR upload_id(s)
GET  /api/v1/history   Legacy analysis history (backward compat)

─── Research History ──────────────────────────────────────────────────────────
GET    /api/v1/research              List all research runs
GET    /api/v1/research/{id}         Get full research run detail
DELETE /api/v1/research/{id}         Delete research run
GET    /api/v1/research/{id}/trades  Trade log for a run
GET    /api/v1/research/{id}/monthly Monthly breakdown for a run
GET    /api/v1/research/{id}/export/{fmt}  Export (trade_log|monthly|summary|report)

─── Legacy upload (kept for backward compat) ──────────────────────────────────
POST /api/v1/upload          Single-file upload (file-based, not stored in DB)
POST /api/v1/upload-multiple Merge multiple CSVs (file-based)
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core import data_loader, export
from core import report as report_gen
from core.modules import liquidity_sweep
from database import db
from database.db import (
    delete_dataset,
    delete_research_run,
    get_dataset_by_hash,
    get_dataset_by_id,
    get_dataset_candles,
    get_monthly_reports,
    get_recent_analyses,
    get_research_run,
    get_trade_logs,
    get_upload_by_id,
    init_db,
    list_datasets,
    list_research_runs,
    save_analysis,
    save_dataset_candles,
    save_dataset_metadata,
    save_monthly_reports,
    save_research_run,
    save_research_run_complete,
    save_trade_logs,
    save_upload,
)

UPLOAD_DIR = Path("data/uploads")

MODULES: list[dict] = [
    {
        "id":          "liquidity_sweep",
        "name":        "Liquidity Sweep",
        "phase":       2,
        "description": (
            "Detects confirmed swing-high/low levels swept by a rejection candle. "
            "Entry at next candle open, SL beyond wick, TP at RR target."
        ),
    },
]


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    Path("data/exports").mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="XAUUSD Quant Research Platform",
    description="Phase 2 + Dataset Library — Persistent OHLCV storage & research history",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request schemas ───────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    # ── New: stored dataset sources ────────────────────────────────────────────
    dataset_id:  Optional[str]            = None   # single TF, stored
    dataset_ids: Optional[dict[str, str]] = None   # multi TF, stored  {tf: dataset_id}

    # ── Legacy: file-based sources (backward compat) ───────────────────────────
    upload_id:         Optional[str]            = None   # single TF, old upload
    timeframe_uploads: Optional[dict[str, str]] = None   # multi TF, old uploads

    # ── Mode + role selectors ─────────────────────────────────────────────────
    analysis_mode: str           = "single"
    trend_tf:      Optional[str] = None
    structure_tf:  Optional[str] = None
    entry_tf:      Optional[str] = None

    # ── Common parameters ──────────────────────────────────────────────────────
    module:        str   = "liquidity_sweep"
    timeframe:     str   = "H1"
    risk_pct:      float = Field(default=1.0,  ge=0.1, le=10.0)
    rr:            float = Field(default=2.0,  ge=0.5, le=10.0)
    lookback:      int   = Field(default=5,    ge=2,   le=20)
    max_bars:      int   = Field(default=200,  ge=10,  le=500)
    research_name: Optional[str] = None


# ── System ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "version": "3.0.0"}


@app.get("/api/v1/modules", tags=["Modules"])
def list_modules():
    return {"modules": MODULES}


# ── Dataset Library ───────────────────────────────────────────────────────────

@app.post("/api/v1/datasets", tags=["Dataset Library"])
async def upload_dataset(
    file: UploadFile = File(...),
    symbol: str = "XAUUSD",
    timeframe: str = "",
):
    """
    Upload a single MT5 OHLCV CSV file.

    - SHA-256 hash is computed; duplicate files are rejected.
    - Timeframe is auto-detected from filename when not supplied.
    - Candles are stored in SQLite (ohlcv_candles table).
    - Returns dataset_id for use in analysis requests.
    """
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted.")

    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()

    # ── Duplicate check ────────────────────────────────────────────────────────
    existing = get_dataset_by_hash(file_hash)
    if existing:
        raise HTTPException(
            409,
            detail={
                "message": (
                    "This OHLCV file already exists in storage. "
                    "Please select the existing dataset instead."
                ),
                "existing_dataset_id": existing["dataset_id"],
                "existing_timeframe":  existing["timeframe"],
                "existing_filename":   existing["filename"],
            },
        )

    # ── Parse & validate ───────────────────────────────────────────────────────
    try:
        df = data_loader.load_csv(io.BytesIO(content))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"CSV parse error: {exc}")

    validation = data_loader.validate_dataframe(df)
    if not validation["valid"]:
        raise HTTPException(
            422,
            {"message": "CSV failed validation", "errors": validation["errors"]},
        )

    # ── Detect timeframe ───────────────────────────────────────────────────────
    tf = timeframe or data_loader.detect_timeframe(file.filename) or "UNKNOWN"

    # ── Store metadata ─────────────────────────────────────────────────────────
    dataset_id = save_dataset_metadata(
        symbol=symbol,
        timeframe=tf,
        filename=file.filename,
        file_hash=file_hash,
        total_rows=len(df),
        start_datetime=str(df.index[0]),
        end_datetime=str(df.index[-1]),
    )

    # ── Store candles ──────────────────────────────────────────────────────────
    candles = [
        {
            "dt":     str(idx),
            "open":   float(row["Open"]),
            "high":   float(row["High"]),
            "low":    float(row["Low"]),
            "close":  float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        for idx, row in df.iterrows()
    ]
    save_dataset_candles(dataset_id, candles)

    # Also persist the file to disk (keeps /api/v1/upload backward compat working)
    dest = UPLOAD_DIR / file.filename
    dest.write_bytes(content)
    save_upload(file.filename, str(dest), len(df))

    return {
        "dataset_id": dataset_id,
        "timeframe":  tf,
        "symbol":     symbol,
        "filename":   file.filename,
        "rows":       len(df),
        "start":      str(df.index[0]),
        "end":        str(df.index[-1]),
        "validation": validation,
    }


@app.get("/api/v1/datasets", tags=["Dataset Library"])
def get_datasets():
    return {"datasets": list_datasets()}


@app.delete("/api/v1/datasets/{dataset_id}", tags=["Dataset Library"])
def remove_dataset(dataset_id: str):
    if not delete_dataset(dataset_id):
        raise HTTPException(404, "Dataset not found.")
    return {"deleted": dataset_id}


@app.get("/api/v1/datasets/{dataset_id}/export", tags=["Dataset Library"])
def export_dataset(dataset_id: str):
    """Return the dataset as an MT5-formatted CSV (Date,Time,Open,High,Low,Close,Volume)."""
    meta = get_dataset_by_id(dataset_id)
    if not meta:
        raise HTTPException(404, "Dataset not found.")

    candles = get_dataset_candles(dataset_id)
    if not candles:
        raise HTTPException(404, "No candles found for this dataset.")

    import pandas as pd
    df = pd.DataFrame(candles)
    df["dt"] = pd.to_datetime(df["dt"])
    out = {
        "Date":   df["dt"].dt.strftime("%Y.%m.%d"),
        "Time":   df["dt"].dt.strftime("%H:%M"),
        "Open":   df["open"],
        "High":   df["high"],
        "Low":    df["low"],
        "Close":  df["close"],
        "Volume": df["volume"],
    }
    import io as _io
    buf = _io.StringIO()
    pd.DataFrame(out).to_csv(buf, index=False)

    fname = f"{meta['symbol']}_{meta['timeframe']}_{dataset_id[:8]}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitise_float(v: object) -> object:
    """Replace nan/inf with JSON-safe values."""
    if isinstance(v, float):
        if math.isnan(v):
            return 0.0
        if math.isinf(v):
            return 999.0 if v > 0 else -999.0
    return v


def _sanitise_report(rpt: dict) -> dict:
    """Walk the report dict and replace any non-finite floats."""
    out: dict = {}
    for k, v in rpt.items():
        if isinstance(v, float):
            out[k] = _sanitise_float(v)
        elif isinstance(v, list):
            sanitised_list = []
            for item in v:
                if isinstance(item, dict):
                    sanitised_list.append({ik: _sanitise_float(iv) for ik, iv in item.items()})
                else:
                    sanitised_list.append(_sanitise_float(item))
            out[k] = sanitised_list
        elif isinstance(v, dict):
            out[k] = {ik: (_sanitise_float(iv) if isinstance(iv, float) else iv)
                      for ik, iv in v.items()}
        else:
            out[k] = v
    return out


# ── Analysis ──────────────────────────────────────────────────────────────────

@app.post("/api/v1/analyze", tags=["Analysis"])
def analyze(req: AnalyzeRequest):
    """
    Run a strategy module.

    Data source priority (first match wins):
      1. dataset_id  / dataset_ids   — load from SQLite ohlcv_candles
      2. upload_id   / timeframe_uploads — load from uploaded file (legacy)
    """
    data_by_timeframe: dict = {}
    primary_tf        = req.timeframe
    dataset_ids_used: dict[str, str] = {}
    export_filename   = "unknown"

    # ── Resolve data source ────────────────────────────────────────────────────
    use_stored = bool(req.dataset_id or req.dataset_ids)

    if use_stored:
        # ── Stored dataset(s) ──────────────────────────────────────────────────
        if req.analysis_mode == "multi":
            src = req.dataset_ids or {}
            if not src:
                raise HTTPException(400, "dataset_ids required for multi-TF mode.")
            for tf, did in src.items():
                meta = get_dataset_by_id(did)
                if not meta:
                    raise HTTPException(404, f"Dataset not found for TF '{tf}' (id={did}).")
                try:
                    data_by_timeframe[tf] = data_loader.load_from_dataset_id(did)
                except Exception as exc:
                    raise HTTPException(500, f"Failed to load dataset '{tf}': {exc}")
                dataset_ids_used[tf] = did

            primary_tf = req.structure_tf or next(iter(data_by_timeframe))
            if primary_tf not in data_by_timeframe:
                raise HTTPException(
                    400,
                    f"Structure TF '{primary_tf}' not in provided dataset_ids "
                    f"(available: {list(data_by_timeframe.keys())}).",
                )
            export_filename = "multi_tf_" + "_".join(sorted(src.keys()))

        else:  # single stored
            if not req.dataset_id:
                raise HTTPException(400, "dataset_id required for single-TF mode.")
            meta = get_dataset_by_id(req.dataset_id)
            if not meta:
                raise HTTPException(404, "Dataset not found.")
            try:
                df = data_loader.load_from_dataset_id(req.dataset_id)
            except Exception as exc:
                raise HTTPException(500, f"Failed to load dataset: {exc}")
            data_by_timeframe[req.timeframe] = df
            primary_tf = req.timeframe
            dataset_ids_used[req.timeframe] = req.dataset_id
            export_filename = meta["filename"]

    else:
        # ── Legacy file-based source ───────────────────────────────────────────
        if req.analysis_mode == "multi":
            src = req.timeframe_uploads or {}
            if not src:
                raise HTTPException(400, "timeframe_uploads required for multi-TF mode.")
            for tf, uid in src.items():
                upload = get_upload_by_id(uid)
                if not upload:
                    raise HTTPException(404, f"Upload not found for TF '{tf}'.")
                try:
                    data_by_timeframe[tf] = data_loader.load_csv(upload["filepath"])
                except Exception as exc:
                    raise HTTPException(500, f"Failed to load '{tf}' data: {exc}")
            primary_tf = req.structure_tf or next(iter(data_by_timeframe))
            if primary_tf not in data_by_timeframe:
                raise HTTPException(400, f"Structure TF '{primary_tf}' not in uploads.")
            export_filename = "multi_tf_" + "_".join(sorted(src.keys()))

        else:  # single legacy
            if not req.upload_id:
                raise HTTPException(400, "upload_id required for single-TF mode.")
            upload = get_upload_by_id(req.upload_id)
            if not upload:
                raise HTTPException(404, "Upload not found.")
            try:
                df = data_loader.load_csv(upload["filepath"])
            except Exception as exc:
                raise HTTPException(500, f"Failed to reload data: {exc}")
            data_by_timeframe[req.timeframe] = df
            primary_tf    = req.timeframe
            export_filename = upload["filename"]

    primary_df = data_by_timeframe[primary_tf]

    try:
        # ── Run strategy ───────────────────────────────────────────────────────
        if req.module == "liquidity_sweep":
            trades = liquidity_sweep.run(
                primary_df,
                rr=req.rr,
                lookback=req.lookback,
                max_bars=req.max_bars,
                data_by_timeframe=data_by_timeframe if req.analysis_mode == "multi" else None,
                trend_tf=req.trend_tf,
                structure_tf=req.structure_tf,
                entry_tf=req.entry_tf,
            )
        else:
            raise HTTPException(400, f"Unknown module: {req.module!r}")

        # ── Generate report ────────────────────────────────────────────────────
        rpt = report_gen.generate(
            trades,
            req.risk_pct,
            start_date=str(primary_df.index[0]),
            end_date=str(primary_df.index[-1]),
        )

        # Sanitise: replace any non-finite floats before JSON serialisation
        rpt = _sanitise_report(rpt)

        # ── Persist research run + trades + monthly in one atomic transaction ──
        clean_trades = [
            {k: v for k, v in t.items() if not k.startswith("_")}
            for t in trades
        ]

        research_id = save_research_run_complete(
            research_name=req.research_name,
            selected_module=req.module,
            symbol="XAUUSD",
            timeframe_mode=req.analysis_mode,
            timeframes_used=list(data_by_timeframe.keys()),
            dataset_ids_used=dataset_ids_used,
            risk_percent=req.risk_pct,
            reward_risk_ratio=req.rr,
            lookback=req.lookback,
            report=rpt,
            trades=clean_trades,
            monthly_breakdown=rpt.get("monthly_breakdown", []),
        )

        # ── Legacy analysis record (backward compat with /api/v1/history) ─────
        legacy_uid = (
            req.upload_id
            or (next(iter(req.timeframe_uploads.values())) if req.timeframe_uploads else None)
            or research_id
        )
        legacy_aid = save_analysis(legacy_uid, req.module, primary_tf, req.risk_pct, req.rr, rpt)

        # ── Append-mode CSV exports ────────────────────────────────────────────
        trade_log_path = str(export.save_trade_log(clean_trades, run_id=research_id))
        summary_path   = str(export.save_research_summary(
            report=rpt, run_id=research_id, filename=export_filename,
            timeframe=primary_tf, module=req.module,
            risk_pct=req.risk_pct, rr=req.rr, lookback=req.lookback,
        ))

        # ── Per-research file exports ──────────────────────────────────────────
        result_payload = {
            "research_id":     research_id,
            "analysis_id":     legacy_aid,
            "symbol":          "XAUUSD",
            "module":          req.module,
            "analysis_mode":   req.analysis_mode,
            "timeframe":       primary_tf,
            "timeframes_used": list(data_by_timeframe.keys()),
            "structure_tf":    req.structure_tf,
            "entry_tf":        req.entry_tf,
            "trend_tf":        req.trend_tf,
            "parameters":      {"risk_pct": req.risk_pct, "rr": req.rr, "lookback": req.lookback},
            "trades":          clean_trades,
            "report":          rpt,
            "exports":         {"trade_log": trade_log_path, "research_summary": summary_path},
        }
        per_run_paths = export.save_per_research_exports(research_id, result_payload)
        result_payload["exports"].update({k: str(v) for k, v in per_run_paths.items()})

        return result_payload

    except HTTPException:
        raise  # re-raise expected HTTP errors as-is
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[analyze] UNHANDLED ERROR:\n{tb}", flush=True)
        raise HTTPException(500, detail=f"{type(exc).__name__}: {exc}")


# ── Research History ──────────────────────────────────────────────────────────

@app.get("/api/v1/research", tags=["Research History"])
def get_research_list(limit: int = 100):
    return {"research_runs": list_research_runs(limit)}


@app.get("/api/v1/research/{research_id}", tags=["Research History"])
def get_research_detail(research_id: str):
    run = get_research_run(research_id)
    if not run:
        raise HTTPException(404, "Research run not found.")
    return run


@app.delete("/api/v1/research/{research_id}", tags=["Research History"])
def remove_research_run(research_id: str):
    if not delete_research_run(research_id):
        raise HTTPException(404, "Research run not found.")
    return {"deleted": research_id}


@app.get("/api/v1/research/{research_id}/trades", tags=["Research History"])
def get_research_trades(research_id: str):
    if not get_research_run(research_id):
        raise HTTPException(404, "Research run not found.")
    return {"trades": get_trade_logs(research_id)}


@app.get("/api/v1/research/{research_id}/monthly", tags=["Research History"])
def get_research_monthly(research_id: str):
    if not get_research_run(research_id):
        raise HTTPException(404, "Research run not found.")
    return {"monthly": get_monthly_reports(research_id)}


@app.get("/api/v1/research/{research_id}/export/{fmt}", tags=["Research History"])
def export_research(research_id: str, fmt: str):
    """
    Export a research run.
    fmt: trade_log | monthly | summary | report
    """
    run = get_research_run(research_id)
    if not run:
        raise HTTPException(404, "Research run not found.")

    short = research_id[:8]
    report_dict = json.loads(run["full_report"]) if run.get("full_report") else {}

    if fmt == "trade_log":
        trades = get_trade_logs(research_id)
        data   = export.build_trade_log_csv(trades)
        return Response(
            content=data, media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="trade_log_{short}.csv"'},
        )

    if fmt == "monthly":
        monthly = get_monthly_reports(research_id)
        data    = export.build_monthly_csv(monthly)
        return Response(
            content=data, media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="monthly_{short}.csv"'},
        )

    if fmt == "summary":
        meta = {
            "research_id": research_id,
            "module":      run["selected_module"],
            "timeframe":   run["timeframe_mode"],
        }
        data = export.build_summary_csv(report_dict, meta)
        return Response(
            content=data, media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="summary_{short}.csv"'},
        )

    if fmt == "report":
        result_full = {
            "research_id":    research_id,
            "research_name":  run.get("research_name"),
            "created":        run["created_datetime"],
            "module":         run["selected_module"],
            "symbol":         run["symbol"],
            "timeframe_mode": run["timeframe_mode"],
            "timeframes":     json.loads(run["timeframes_used"]),
            "parameters": {
                "risk_pct": run["risk_percent"],
                "rr":       run["reward_risk_ratio"],
                "lookback": run["lookback"],
            },
            "report": report_dict,
            "trades": get_trade_logs(research_id),
            "monthly": get_monthly_reports(research_id),
        }
        data = export.build_report_json(result_full)
        return Response(
            content=data, media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="report_{short}.json"'},
        )

    raise HTTPException(400, f"Unknown export format '{fmt}'. Use: trade_log, monthly, summary, report.")


# ── Legacy endpoints (backward compat) ────────────────────────────────────────

@app.get("/api/v1/history", tags=["Analysis"])
def history(limit: int = 10):
    return {"analyses": get_recent_analyses(limit)}


@app.post("/api/v1/upload", tags=["Data"])
async def upload_csv_legacy(file: UploadFile = File(...)):
    """Legacy single-file upload — file stored to disk only, not in SQLite datasets."""
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted.")
    content = await file.read()
    try:
        df = data_loader.load_csv(io.BytesIO(content))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"CSV parse error: {exc}")

    validation = data_loader.validate_dataframe(df)
    if not validation["valid"]:
        raise HTTPException(
            422,
            {"message": "CSV failed validation", "errors": validation["errors"]},
        )
    dest = UPLOAD_DIR / file.filename
    dest.write_bytes(content)
    upload_id = save_upload(file.filename, str(dest), len(df))
    return {
        "upload_id":  upload_id,
        "filename":   file.filename,
        "rows":       len(df),
        "file_count": 1,
        "files":      [{"filename": file.filename, "rows": len(df),
                        "start": str(df.index[0]), "end": str(df.index[-1])}],
        "start":      str(df.index[0]),
        "end":        str(df.index[-1]),
        "validation": validation,
        "preview":    df.head(5).reset_index().rename(columns={"datetime": "Datetime"}).to_dict("records"),
    }


@app.post("/api/v1/upload-multiple", tags=["Data"])
async def upload_multiple_legacy(files: list[UploadFile] = File(...)):
    """Legacy multi-file upload — merges files, stores to disk only."""
    if not files:
        raise HTTPException(400, "No files provided.")
    per_file, dfs = [], []
    for file in files:
        if not file.filename.lower().endswith(".csv"):
            raise HTTPException(400, f"'{file.filename}' is not a .csv file.")
        content = await file.read()
        try:
            df_i = data_loader.load_csv(io.BytesIO(content))
        except ValueError as exc:
            raise HTTPException(422, f"{file.filename}: {exc}")
        per_file.append({"filename": file.filename, "rows": len(df_i),
                         "start": str(df_i.index[0]), "end": str(df_i.index[-1])})
        dfs.append(df_i)

    combined = data_loader.combine_dataframes(dfs)
    validation = data_loader.validate_dataframe(combined)
    if not validation["valid"]:
        raise HTTPException(422, {"message": "Combined dataset failed validation",
                                  "errors": validation["errors"]})

    start_tag    = combined.index[0].strftime("%Y%m%d")
    end_tag      = combined.index[-1].strftime("%Y%m%d")
    combined_name = f"combined_{len(files)}files_{start_tag}_{end_tag}.csv"
    dest = UPLOAD_DIR / combined_name
    data_loader.save_dataframe(combined, dest)
    upload_id = save_upload(combined_name, str(dest), len(combined))

    return {
        "upload_id":  upload_id,
        "filename":   combined_name,
        "rows":       len(combined),
        "file_count": len(files),
        "files":      per_file,
        "start":      str(combined.index[0]),
        "end":        str(combined.index[-1]),
        "validation": validation,
        "preview":    combined.head(5).reset_index().rename(columns={"datetime": "Datetime"}).to_dict("records"),
    }
