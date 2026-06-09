# XAUUSD Quant Research Platform

**Phase 1.5 — Stability & Validation**

A persistent backtesting platform for the Liquidity Sweep strategy on XAUUSD.
Stores OHLCV datasets and research history in SQLite so every run is reusable.

---

## Stack

| Layer    | Technology                        |
|----------|-----------------------------------|
| Backend  | FastAPI + Uvicorn (port 8000)     |
| Frontend | Streamlit (port 8501)             |
| Database | SQLite (WAL mode, FK enforced)    |
| Data     | Pandas, MT5 OHLCV CSV             |
| Charts   | Plotly                            |

---

## Quick Start

```bash
# Terminal 1 — Backend
cd "d:\Quant Research Platform\Quant-Research-Platform"
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — Frontend
cd "d:\Quant Research Platform\Quant-Research-Platform"
python -m streamlit run frontend/app.py
```

Open `http://localhost:8501` in your browser.

---

## How to Upload OHLCV Files

1. Go to **Run Analysis** → **Upload New Files**.
2. Drop one or more MT5 OHLCV CSV files.
   - Expected columns: `Date, Time, Open, High, Low, Close, Volume`
   - Expected date format: `2024.01.02`
3. Timeframe is auto-detected from the filename (e.g. `XAUUSD_H1_OHLCV.csv` → H1).
   Override if detection fails.
4. Click **Upload & Store Files**.
   - Duplicate files are detected by SHA-256 hash — re-uploading reuses the existing dataset.
   - Candles are deduplicated by `(dataset_id, datetime)`.
5. Uploaded datasets persist in SQLite across server restarts.

---

## How to Reuse Stored Datasets

1. Go to **Run Analysis** → **Use Stored Datasets**.
2. Select **Single Timeframe** or **Multi-Timeframe** mode.
3. Pick datasets from the dropdown (shows TF, filename, rows, date range).
4. Click **Use Selected Datasets**.
5. Continue to Step 2 (date range) and Step 3 (run).

---

## How to Run Analysis

1. Complete data source selection (upload or stored datasets).
2. **Step 2 — Date Range & Analysis Mode**
   - Default: use full dataset range.
   - Uncheck **Use Full Dataset Range** to enter a custom window.
   - The selected range must be inside the available dataset range.
   - Choose analysis mode:
     - **Full Backtest** — single run, all metrics.
     - **Yearly Analysis** — year-by-year breakdown in Monthly tab.
     - **Monthly Analysis** — monthly detail.
     - **Walk Forward** — split into In-Sample (training) + Out-of-Sample (validation).
3. **Step 3 — Configure & Run**
   - Set sidebar parameters: Risk %, RR, Swing Strength.
   - Optionally name the run.
   - Click **▶ Run Analysis**.
4. Results appear in Step 4 with 5 sub-tabs:
   - **Equity Curve** — cumulative return + pie chart + metrics table
   - **Drawdown** — drawdown curve with goal limit line
   - **Monthly** — bar chart; year-by-year table for Yearly mode
   - **Trades** — full trade log
   - **Goals** — PASS/WATCHLIST/FAIL for each target metric

### Goal Targets

| Metric         | PASS           | WATCHLIST           |
|----------------|----------------|---------------------|
| Monthly Return | 3 % – 5 %      | 1.5 %–3 % or 5%–8% |
| Max Drawdown   | < 4 %          | 4 % – 6 %           |
| Profit Factor  | >= 1.5         | 1.2 – 1.49          |

---

## How to View Research History

1. Go to **Research History** tab.
2. The table shows every completed run: name, date, mode, TF(s), analysis type, date range, metrics, goal status.
3. Select a run from the dropdown.
4. **View details** — expander shows the full equity curve, drawdown, monthly breakdown, and trade log.
5. **Re-run** — expander shows original parameters; click **Re-run now** to re-run with the same settings.
6. **Delete** — permanently removes the run and all linked trade/monthly records (CASCADE).

---

## How to Export Results

### From Research History tab
Select a run, then click any of the 4 download buttons:
- **Trade Log CSV** — all trades for that run
- **Monthly CSV** — monthly breakdown
- **Summary CSV** — single-row metrics summary
- **Full Report JSON** — complete result including equity curve

### From Export Center tab
- **OHLCV Dataset Exports** — download any stored dataset as MT5-formatted CSV.
- **Research Run Exports** — same 4 export formats, pick any run.
- **Cumulative Log Files** (auto-appended every run):
  - `data/exports/trade_log.csv`
  - `data/exports/research_summary.csv`

Per-run files are also written to `data/exports/` after each analysis:
`trade_log_{id}.csv`, `monthly_{id}.csv`, `summary_{id}.csv`, `report_{id}.json`

---

## Platform Health Check

Go to **Platform Health** tab to see:

| Section        | What it shows                                           |
|----------------|---------------------------------------------------------|
| Database       | File path, size, connection status, FK enforcement      |
| Table Counts   | Row counts for all 5 tables                             |
| Latest Run     | Most recent research run summary                        |
| Export Folder  | File count and total size under `data/exports/`         |
| Validation     | Automated checklist of all key platform checks          |

---

## How to Reset Database (Development Only)

Use this to start completely fresh during development. **All data will be deleted.**

**Via Streamlit:**
1. Go to **Platform Health** tab.
2. Scroll to **Reset Database — Development Only**.
3. Check the confirmation checkbox.
4. Type `RESET` in the text box.
5. Click **Reset Database**.

**Via API:**
```
POST http://localhost:8000/api/v1/admin/reset-db?confirm=RESET
```

After reset, all tables are recreated with the correct schema.
The FK schema check runs automatically on startup to detect and repair stale schemas.

---

## Database Schema

```
datasets         — dataset metadata (one row per uploaded file)
ohlcv_candles    — raw candle storage  FK -> datasets.dataset_id  CASCADE
research_runs    — analysis history
trade_logs       — per-trade records   FK -> research_runs.research_id  CASCADE
monthly_reports  — monthly breakdown   FK -> research_runs.research_id  CASCADE
```

Foreign keys are enforced with `PRAGMA foreign_keys = ON`.
FK schema is validated on every startup via `PRAGMA foreign_key_list()` and auto-repaired if stale.

---

## Directory Layout

```
backend/
  main.py               FastAPI app, all API endpoints
core/
  data_loader.py        MT5 CSV parser, validator, TF detector
  report.py             Performance report generator
  export.py             CSV/JSON export utilities
  modules/
    liquidity_sweep.py  Liquidity Sweep backtest engine
database/
  db.py                 SQLite helpers, schema init, health/reset
frontend/
  app.py                Streamlit dashboard (5 tabs)
data/
  quant.db              SQLite database (auto-created)
  exports/              All CSV/JSON export files
  uploads/              Legacy uploaded CSV files
```

---

## Known Limitations

- Walk Forward uses two separate API calls (IS + OOS); not a single atomic operation.
- Multi-TF analysis requires all TFs to share a common date range.
- No live market data connection — manual MT5 CSV export required.
- Weekend/holiday gaps in MT5 data produce warnings (harmless, analysis proceeds).
- Database reset is unprotected beyond the `?confirm=RESET` parameter — add auth before any production deployment.
