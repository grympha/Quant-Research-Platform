"""
XAUUSD Quant Research Platform — FastAPI Backend
=================================================
Phase 1 endpoints:
  GET  /health
  GET  /api/v1/modules
  POST /api/v1/upload        — upload MT5 CSV
  POST /api/v1/analyze       — run a module + return full report
  GET  /api/v1/history       — recent analyses from SQLite
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core import data_loader
from core.modules import liquidity_sweep
from core import report as report_gen
from database.db import (
    get_recent_analyses,
    get_upload_by_id,
    init_db,
    save_analysis,
    save_upload,
)

UPLOAD_DIR = Path("data/uploads")

# ── Registry of available modules ─────────────────────────────────────────────
MODULES: list[dict] = [
    {
        "id": "liquidity_sweep",
        "name": "Liquidity Sweep",
        "description": (
            "Detects candles that wick through a prior swing high/low then reverse — "
            "classic stop-hunt / liquidity-grab entries."
        ),
        "phase": 1,
    },
]


# ── Startup ────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="XAUUSD Quant Research Platform",
    description="Phase 1 — Liquidity Sweep backtesting API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ──────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    upload_id: str
    module: str = "liquidity_sweep"
    timeframe: str = "H1"
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0)
    rr: float = Field(default=2.0, ge=0.5, le=10.0)
    lookback: int = Field(default=20, ge=5, le=100)
    max_bars: int = Field(default=100, ge=10, le=500)


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/v1/modules", tags=["Modules"])
def list_modules():
    return {"modules": MODULES}


@app.post("/api/v1/upload", tags=["Data"])
async def upload_csv(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted.")

    content = await file.read()

    try:
        df = data_loader.load_csv(io.BytesIO(content))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"CSV parse error: {exc}")

    dest = UPLOAD_DIR / file.filename
    dest.write_bytes(content)

    upload_id = save_upload(file.filename, str(dest), len(df))

    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "rows": len(df),
        "start": str(df.index[0]),
        "end": str(df.index[-1]),
        "preview": df.head(5).reset_index().rename(columns={"datetime": "Datetime"}).to_dict(orient="records"),
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

    if req.module == "liquidity_sweep":
        trades = liquidity_sweep.run(
            df, rr=req.rr, lookback=req.lookback, max_bars=req.max_bars
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown module: {req.module!r}")

    rpt = report_gen.generate(
        trades,
        req.risk_pct,
        start_date=str(df.index[0]),
        end_date=str(df.index[-1]),
    )

    analysis_id = save_analysis(
        req.upload_id, req.module, req.timeframe, req.risk_pct, req.rr, rpt
    )

    return {
        "analysis_id": analysis_id,
        "symbol": "XAUUSD",
        "module": req.module,
        "timeframe": req.timeframe,
        "parameters": {
            "risk_pct": req.risk_pct,
            "rr": req.rr,
            "lookback": req.lookback,
        },
        "trades": trades,
        "report": rpt,
    }


@app.get("/api/v1/history", tags=["Analysis"])
def history(limit: int = 10):
    return {"analyses": get_recent_analyses(limit)}
