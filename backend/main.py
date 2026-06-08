"""
XAUUSD Quant Research Platform — FastAPI Backend  (Phase 2)
============================================================
Endpoints:
  GET  /health
  GET  /api/v1/modules
  POST /api/v1/upload        Upload + validate MT5 CSV
  POST /api/v1/analyze       Run module → report + export CSVs
  GET  /api/v1/history       Recent analyses from SQLite
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core import data_loader, export
from core import report as report_gen
from core.modules import liquidity_sweep
from database.db import (
    get_recent_analyses,
    get_upload_by_id,
    init_db,
    save_analysis,
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
            "Bearish candle sweeping a prior high → SELL. "
            "Bullish candle sweeping a prior low → BUY. "
            "Entry at next candle open, SL beyond wick, TP at RR target."
        ),
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="XAUUSD Quant Research Platform",
    description="Phase 2 — Real Liquidity Sweep backtesting API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request schema ─────────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    upload_id: str
    module:    str   = "liquidity_sweep"
    timeframe: str   = "H1"
    risk_pct:  float = Field(default=1.0,  ge=0.1,  le=10.0)
    rr:        float = Field(default=2.0,  ge=0.5,  le=10.0)
    lookback:  int   = Field(default=5,    ge=2,    le=20)
    max_bars:  int   = Field(default=200,  ge=10,   le=500)


# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/api/v1/modules", tags=["Modules"])
def list_modules():
    return {"modules": MODULES}


@app.post("/api/v1/upload", tags=["Data"])
async def upload_csv(file: UploadFile = File(...)):
    """Single-file upload (kept for backward compatibility)."""
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted.")

    content = await file.read()

    try:
        df = data_loader.load_csv(io.BytesIO(content))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"CSV parse error: {exc}")

    validation = data_loader.validate_dataframe(df)
    if not validation["valid"]:
        raise HTTPException(
            status_code=422,
            detail={"message": "CSV failed validation", "errors": validation["errors"]},
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
        "preview":    (
            df.head(5)
            .reset_index()
            .rename(columns={"datetime": "Datetime"})
            .to_dict(orient="records")
        ),
    }


@app.post("/api/v1/upload-multiple", tags=["Data"])
async def upload_multiple_csv(files: list[UploadFile] = File(...)):
    """
    Accept 1–N CSV files, parse each, combine into one sorted dataset,
    validate, persist to disk, and return a single upload_id for analysis.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    per_file: list[dict] = []
    dfs: list = []

    for file in files:
        if not file.filename.lower().endswith(".csv"):
            raise HTTPException(
                status_code=400,
                detail=f"'{file.filename}' is not a .csv file.",
            )
        content = await file.read()
        try:
            df_i = data_loader.load_csv(io.BytesIO(content))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{file.filename}: {exc}")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{file.filename}: parse error — {exc}")

        per_file.append({
            "filename": file.filename,
            "rows":     len(df_i),
            "start":    str(df_i.index[0]),
            "end":      str(df_i.index[-1]),
        })
        dfs.append(df_i)

    # Merge all files into one sorted, deduplicated DataFrame
    combined = data_loader.combine_dataframes(dfs)

    validation = data_loader.validate_dataframe(combined)
    if not validation["valid"]:
        raise HTTPException(
            status_code=422,
            detail={"message": "Combined dataset failed validation",
                    "errors": validation["errors"]},
        )

    # Persist as a re-loadable MT5 CSV
    start_tag = combined.index[0].strftime("%Y%m%d")
    end_tag   = combined.index[-1].strftime("%Y%m%d")
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
        "preview":    (
            combined.head(5)
            .reset_index()
            .rename(columns={"datetime": "Datetime"})
            .to_dict(orient="records")
        ),
    }


@app.post("/api/v1/analyze", tags=["Analysis"])
def analyze(req: AnalyzeRequest):
    upload = get_upload_by_id(req.upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found.")

    try:
        df = data_loader.load_csv(upload["filepath"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to reload data: {exc}")

    # ── Run strategy ───────────────────────────────────────────────────────────
    if req.module == "liquidity_sweep":
        trades = liquidity_sweep.run(
            df,
            rr=req.rr,
            lookback=req.lookback,
            max_bars=req.max_bars,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown module: {req.module!r}")

    # ── Generate report ────────────────────────────────────────────────────────
    rpt = report_gen.generate(
        trades,
        req.risk_pct,
        start_date=str(df.index[0]),
        end_date=str(df.index[-1]),
    )

    # ── Persist to SQLite ──────────────────────────────────────────────────────
    analysis_id = save_analysis(
        req.upload_id, req.module, req.timeframe, req.risk_pct, req.rr, rpt
    )

    # ── Export CSVs ────────────────────────────────────────────────────────────
    trade_log_path    = str(export.save_trade_log(trades, run_id=analysis_id))
    summary_path      = str(export.save_research_summary(
        report=rpt,
        run_id=analysis_id,
        filename=upload["filename"],
        timeframe=req.timeframe,
        module=req.module,
        risk_pct=req.risk_pct,
        rr=req.rr,
        lookback=req.lookback,
    ))

    # Strip internal bar-index fields before returning
    clean_trades = [
        {k: v for k, v in t.items() if not k.startswith("_")}
        for t in trades
    ]

    return {
        "analysis_id": analysis_id,
        "symbol":      "XAUUSD",
        "module":      req.module,
        "timeframe":   req.timeframe,
        "parameters":  {
            "risk_pct": req.risk_pct,
            "rr":       req.rr,
            "lookback": req.lookback,
        },
        "trades":      clean_trades,
        "report":      rpt,
        "exports":     {
            "trade_log":         trade_log_path,
            "research_summary":  summary_path,
        },
    }


@app.get("/api/v1/history", tags=["Analysis"])
def history(limit: int = 10):
    return {"analyses": get_recent_analyses(limit)}
