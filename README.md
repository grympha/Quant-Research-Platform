# XAUUSD Quant Research Platform

Phase 3 — Persistent Dataset Library, Research History, and Export Center.

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Streamlit (4-tab dashboard) |
| Backend API | FastAPI + Uvicorn |
| Database | SQLite (`data/quant.db`) |
| Data | Pandas + NumPy |
| Charts | Plotly |

## Project Structure

```
Quant-Research-Platform/
├── backend/
│   └── main.py              # FastAPI REST API v3.0.0
├── core/
│   ├── data_loader.py       # MT5 CSV parser + detect_timeframe() + load_from_dataset_id()
│   ├── backtest.py          # Sequential no-lookahead execution engine
│   ├── report.py            # Report & PASS/WATCHLIST/FAIL goal evaluator
│   ├── export.py            # CSV/JSON export builders + per-research exports
│   └── modules/
│       └── liquidity_sweep.py   # Liquidity Sweep strategy
├── database/
│   └── db.py                # SQLite schema + CRUD for all 7 tables
├── frontend/
│   └── app.py               # Streamlit 4-tab dashboard
├── data/                    # Auto-created at startup
│   ├── quant.db             # SQLite database
│   ├── uploads/             # Legacy uploaded files
│   └── exports/             # All CSV + JSON exports
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

## Dashboard Tabs

### Tab 1 — Run Analysis

Choose your data source:

| Source | When to use |
|--------|-------------|
| **Upload New Files** | First time using a dataset; files are stored in the library for reuse |
| **Use Stored Datasets** | Pick a dataset you already uploaded — no need to upload again |

**Upload flow:**
1. Drag & drop one or more MT5 OHLCV CSV files.
2. Timeframe is auto-detected from filename (`XAUUSD_H1_OHLCV.csv` → H1).
3. Override the timeframe via dropdown if needed.
4. Click **Upload & Store Files** — files are stored in SQLite and the Dataset Library.
5. Duplicate files are detected by SHA-256 hash; existing datasets are reused automatically.

**Analysis modes:**

| Mode | Description |
|------|-------------|
| Single Timeframe | One dataset; Liquidity Sweep runs directly on it |
| Multi-Timeframe | Assign roles (Trend / Structure / Entry) to different datasets |

### Tab 2 — OHLCV Dataset Library

Shows all stored datasets with:
- Dataset ID, Symbol, Timeframe, Filename, Row count, Date range, Upload date, Status

Actions per dataset:
- **Export CSV** — download candles in MT5 format
- **Delete** — removes dataset and all its candles (with confirmation)

### Tab 3 — Research History

Shows all completed analysis runs with key metrics:
- Research ID, Name, Date, Module, Mode, Timeframe(s)
- Trades, Win%, PF, Net R, Monthly%, DD%, Goal Status

Actions per run:
- **View Details** — expander showing full charts and metrics from the stored report
- **Export** — download Trade Log CSV, Monthly CSV, Summary CSV, or Full JSON
- **Re-run** — repeat analysis with same parameters and datasets, save as new run
- **Delete** — removes run and all associated trades / monthly records (with confirmation)

### Tab 4 — Export Center

Download exports for any dataset or research run without navigating to the individual record:
- **Dataset Exports** — pick dataset → download MT5-format CSV
- **Research Run Exports** — pick run → download Trade Log, Monthly Report, Summary, or Full JSON

## Uploaded & Stored Data

### How to upload and store OHLCV data

1. Go to **Run Analysis → Upload New Files**
2. Drop your CSV files; timeframes are auto-detected
3. Click **Upload & Store Files**
4. Files are stored in SQLite (`ohlcv_candles` table) and metadata in `datasets`
5. Duplicate files (same SHA-256 hash) are detected and the existing dataset is reused

### How to reuse stored datasets

1. Go to **Run Analysis → Use Stored Datasets**
2. Select one (Single TF) or up to three (Multi-TF: Trend / Structure / Entry)
3. Configure parameters in the sidebar
4. Click **Use Selected Datasets** then **Run Analysis**

## Database Tables

| Table | Purpose |
|-------|---------|
| `datasets` | OHLCV dataset metadata (symbol, timeframe, filename, SHA-256 hash, row count, date range) |
| `ohlcv_candles` | Raw candle storage — one row per candle, linked to `datasets` via CASCADE delete |
| `research_runs` | Every completed analysis run with all metrics and the full report JSON blob |
| `trade_logs` | Per-trade records for each research run, linked via CASCADE delete |
| `monthly_reports` | Monthly breakdown rows for each research run, linked via CASCADE delete |
| `uploads` | Legacy single-file upload metadata (backward compat) |
| `analyses` | Legacy analysis records (backward compat) |

### Key design decisions

- **Dedup by SHA-256**: uploading the same file twice returns the existing `dataset_id` instead of storing duplicates.
- **Candle dedup**: `UNIQUE(dataset_id, dt)` prevents duplicate candles within a dataset; `INSERT OR IGNORE` handles conflicts silently.
- **CASCADE delete**: deleting a dataset removes all its candles; deleting a research run removes all its trades and monthly records.
- **WAL mode**: SQLite journal mode set to WAL for better concurrent write performance.

## File Naming Convention

| Timeframe | Recommended Filename |
|-----------|---------------------|
| M1  | `XAUUSD_M1_OHLCV.csv` |
| M5  | `XAUUSD_M5_OHLCV.csv` |
| M15 | `XAUUSD_M15_OHLCV.csv` |
| M30 | `XAUUSD_M30_OHLCV.csv` |
| H1  | `XAUUSD_H1_OHLCV.csv` |
| H4  | `XAUUSD_H4_OHLCV.csv` |
| D1  | `XAUUSD_D1_OHLCV.csv` |

Files with unrecognised names show ⚠️ — assign the timeframe manually via the dropdown.

## Strategy: Liquidity Sweep

Detects candles that wick through a recent swing high/low and close back on the other side.

- **Bullish sweep**: wick below confirmed N-bar swing low, close above → BUY next candle open
- **Bearish sweep**: wick above confirmed N-bar swing high, close below → SELL next candle open
- **Stop**: wick extreme + 0.20 buffer
- **Target**: entry ± (stop_distance × RR)
- **No lookahead**: swing at bar *j* only confirmed once bar *j + N* has closed

## Target Goals

| Metric | PASS | WATCHLIST |
|--------|------|-----------|
| Monthly Return | 3% – 5% | 1.5%–3% or 5%–8% |
| Max Drawdown | < 4% | 4%–6% |
| Profit Factor | ≥ 1.5 | 1.2–1.49 |

## API Reference

### Dataset Library

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/datasets` | Upload CSV → hash-dedup → store candles → return `dataset_id` |
| GET | `/api/v1/datasets` | List all stored datasets |
| DELETE | `/api/v1/datasets/{id}` | Delete dataset and all its candles |
| GET | `/api/v1/datasets/{id}/export` | Download dataset as MT5-format CSV |

### Analysis

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/analyze` | Run strategy; accepts `dataset_id` or `dataset_ids` (stored) or `upload_id` (legacy) |

**Single TF (stored dataset):**
```json
{
  "dataset_id": "<dataset_id>",
  "analysis_mode": "single",
  "module": "liquidity_sweep",
  "timeframe": "H1",
  "risk_pct": 1.0,
  "rr": 2.0,
  "lookback": 5,
  "research_name": "XAUUSD H1 Jan 2024"
}
```

**Multi-TF (stored datasets):**
```json
{
  "dataset_ids": {
    "H4": "<dataset_id>",
    "H1": "<dataset_id>",
    "M15": "<dataset_id>"
  },
  "analysis_mode": "multi",
  "trend_tf": "H4",
  "structure_tf": "H1",
  "entry_tf": "M15",
  "module": "liquidity_sweep",
  "risk_pct": 1.0,
  "rr": 2.0,
  "lookback": 5
}
```

### Research History

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/research` | List all research runs |
| GET | `/api/v1/research/{id}` | Full research run detail |
| DELETE | `/api/v1/research/{id}` | Delete run (cascades trades + monthly) |
| GET | `/api/v1/research/{id}/trades` | Trade log for a run |
| GET | `/api/v1/research/{id}/monthly` | Monthly breakdown for a run |
| GET | `/api/v1/research/{id}/export/{fmt}` | fmt: `trade_log` \| `monthly` \| `summary` \| `report` |

## Exports

All export files are written to `data/exports/`:

| File | Contents |
|------|----------|
| `trade_log.csv` | All trades across all runs (appended) |
| `research_summary.csv` | One row per analysis run (appended) |
| `trade_log_{id}.csv` | Trade log for a specific research run |
| `monthly_{id}.csv` | Monthly breakdown for a specific run |
| `summary_{id}.csv` | Summary row for a specific run |
| `report_{id}.json` | Full research report JSON for a specific run |
