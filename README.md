# XAUUSD Quant Research Platform

Phase 2 + Multi-Timeframe — Real Liquidity Sweep backtesting on MT5 OHLCV data.

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Streamlit |
| Backend API | FastAPI + Uvicorn |
| Database | SQLite (via built-in `sqlite3`) |
| Data | Pandas + NumPy |
| Charts | Plotly |

## Project Structure

```
Quant-Research-Platform/
├── backend/
│   └── main.py              # FastAPI REST API (single + multi-TF endpoints)
├── core/
│   ├── data_loader.py       # MT5 CSV parser + detect_timeframe()
│   ├── backtest.py          # Sequential no-lookahead execution engine
│   ├── report.py            # Report & PASS/WATCHLIST/FAIL goal evaluator
│   ├── export.py            # CSV trade-log + research-summary exports
│   └── modules/
│       └── liquidity_sweep.py   # Liquidity Sweep strategy (Phase 2)
├── database/
│   └── db.py                # SQLite helper
├── frontend/
│   └── app.py               # Streamlit dashboard
├── data/                    # Auto-created: uploads/, quant.db, CSV exports
├── requirements.txt
└── README.md
```

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Start the FastAPI backend (Terminal 1)
```bash
uvicorn backend.main:app --reload
```
API docs: http://localhost:8000/docs

### 3. Start the Streamlit dashboard (Terminal 2)
```bash
streamlit run frontend/app.py
```
Dashboard: http://localhost:8501

## CSV Format (MT5 Export)

```
Date,Time,Open,High,Low,Close,Volume
2024.01.02,01:00,2063.45,2064.12,2062.80,2063.90,342
```

Export from MT5: **File → Save As → CSV**

## Multi-Timeframe Upload

The platform supports uploading multiple timeframes in one session.

### File Naming Convention

Name your CSV files using the MT5 export format so timeframes are auto-detected:

| Timeframe | Expected Filename |
|-----------|------------------|
| M1 | `XAUUSD_M1_OHLCV.csv` |
| M5 | `XAUUSD_M5_OHLCV.csv` |
| M15 | `XAUUSD_M15_OHLCV.csv` |
| M30 | `XAUUSD_M30_OHLCV.csv` |
| H1 | `XAUUSD_H1_OHLCV.csv` |
| H4 | `XAUUSD_H4_OHLCV.csv` |
| D1 | `XAUUSD_D1_OHLCV.csv` |

Files with unrecognised names show a ⚠️ icon in the upload table — assign them a timeframe manually via the dropdown.

### Upload Steps

1. Drag and drop **one or more** CSV files onto the upload area.
2. The dashboard shows a **detected-timeframe table** — override any auto-detected values if needed.
3. Click **Upload & Validate Files** — each file is validated independently and stored.
4. A status table confirms which timeframes are ready (✅ OK / ⚠️ warnings / ❌ error).

### Analysis Modes

| Mode | How it works |
|------|-------------|
| **Single Timeframe** | Choose one uploaded timeframe; Liquidity Sweep runs on it. (Backward compatible with single-file uploads.) |
| **Multi-Timeframe** | Assign roles to your uploaded timeframes — Trend TF (optional), Structure TF (required for Liquidity Sweep), Entry TF (optional). |

### Multi-TF Role Guide

| Role | Purpose | Recommended |
|------|---------|-------------|
| Trend TF | Broad market direction context | H4, D1 |
| Structure TF *(required)* | Swing detection + Liquidity Sweep | H1, M15 |
| Entry TF | Fine entry refinement | M1, M5 |

### Missing Timeframe Errors

If a required timeframe is not uploaded, the dashboard shows a clear error before running:

```
❌ Missing H1 data. Please upload XAUUSD_H1_OHLCV.csv.
```

## Strategy: Liquidity Sweep (Phase 2)

Detects candles that wick through a recent swing high/low and close back on the other side (stop-hunt / liquidity grab).

- **Bullish sweep**: wick below confirmed N-bar swing low, close above → BUY entry next candle open
- **Bearish sweep**: wick above confirmed N-bar swing high, close below → SELL entry next candle open
- **Stop**: wick extreme + 0.20 buffer
- **Target**: entry ± (stop_distance × RR)
- **No lookahead**: a swing at bar *j* is only confirmed once bar *j + N* has closed

## Target Goals

| Metric | PASS | WATCHLIST |
|--------|------|-----------|
| Monthly Return | 3% – 5% | 1.5%–3% or 5%–8% |
| Max Drawdown | < 4% | 4%–6% |
| Profit Factor | ≥ 1.5 | 1.2–1.49 |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Health check |
| GET | /api/v1/modules | List available modules |
| POST | /api/v1/upload | Upload + validate single MT5 CSV |
| POST | /api/v1/upload-multiple | Merge multiple CSVs into one dataset |
| POST | /api/v1/analyze | Run analysis (single-TF or multi-TF mode) |
| GET | /api/v1/history | Recent analyses (SQLite) |

### `/api/v1/analyze` — Multi-TF request body

```json
{
  "analysis_mode": "multi",
  "timeframe_uploads": {
    "H1":  "<upload_id>",
    "M15": "<upload_id>"
  },
  "trend_tf":     "H4",
  "structure_tf": "H1",
  "entry_tf":     "M15",
  "module":       "liquidity_sweep",
  "risk_pct":     1.0,
  "rr":           2.0,
  "lookback":     5
}
```

### `/api/v1/analyze` — Single-TF request body (backward compat)

```json
{
  "upload_id":     "<upload_id>",
  "analysis_mode": "single",
  "module":        "liquidity_sweep",
  "timeframe":     "H1",
  "risk_pct":      1.0,
  "rr":            2.0,
  "lookback":      5
}
```

## Exported CSVs

After each analysis run:

| File | Contents |
|------|----------|
| `data/trade_log.csv` | One row per trade (appended per run) |
| `data/research_summary.csv` | One row per analysis run summary |
