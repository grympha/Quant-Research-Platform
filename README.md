# XAUUSD Quant Research Platform

Phase 1 — Liquidity Sweep backtesting on MT5 OHLCV data.

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
│   └── main.py          # FastAPI REST API
├── core/
│   ├── data_loader.py   # MT5 CSV parser
│   ├── report.py        # Report & goal evaluator
│   └── modules/
│       └── liquidity_sweep.py   # Phase 1 strategy
├── database/
│   └── db.py            # SQLite helper
├── frontend/
│   └── app.py           # Streamlit dashboard
├── data/                # Auto-created: uploads + quant.db
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
API docs available at: http://localhost:8000/docs

### 3. Start the Streamlit dashboard (Terminal 2)
```bash
streamlit run frontend/app.py
```
Dashboard available at: http://localhost:8501

## CSV Format (MT5 Export)

```
Date,Time,Open,High,Low,Close,Volume
2024.01.02,01:00,2063.45,2064.12,2062.80,2063.90,342
```

Export from MT5: File → Save As → CSV

## Strategy: Liquidity Sweep

Detects candles that wick through a recent swing high/low then close back on the other side (stop-hunt / liquidity grab).

- **Bullish sweep**: wick below prior N-bar low, close above → Long entry
- **Bearish sweep**: wick above prior N-bar high, close below → Short entry
- **Stop**: wick extreme + small buffer
- **Target**: entry ± (stop_distance × RR)

## Target Goals

| Metric | Target |
|--------|--------|
| Monthly Return | 3% – 5% |
| Max Drawdown | < 4% |
| Profit Factor | > 1.5 |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Health check |
| GET | /api/v1/modules | List available modules |
| POST | /api/v1/upload | Upload MT5 CSV |
| POST | /api/v1/analyze | Run analysis |
| GET | /api/v1/history | Recent analyses |
