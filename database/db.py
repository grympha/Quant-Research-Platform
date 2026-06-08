"""
SQLite database helpers — Phase 2 + Dataset Storage & Research History.

Tables
──────
uploads          legacy single-file upload metadata (kept for compat)
analyses         legacy analysis records (kept for compat)
datasets         OHLCV dataset metadata with SHA-256 dedup
ohlcv_candles    raw candle storage (per dataset, ON DELETE CASCADE)
research_runs    persistent research history with full metrics
trade_logs       per-trade records (linked to research_runs)
monthly_reports  monthly breakdown (linked to research_runs)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent.parent / "data" / "quant.db"


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    with _connect() as conn:
        # ── Legacy tables (kept for backward compat) ──────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS uploads (
                id          TEXT PRIMARY KEY,
                filename    TEXT NOT NULL,
                filepath    TEXT NOT NULL,
                rows        INTEGER,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id          TEXT PRIMARY KEY,
                upload_id   TEXT NOT NULL,
                module      TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                risk_pct    REAL NOT NULL,
                rr          REAL NOT NULL,
                result      TEXT,
                created_at  TEXT NOT NULL
            )
        """)

        # ── Dataset storage ───────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS datasets (
                dataset_id      TEXT PRIMARY KEY,
                symbol          TEXT NOT NULL DEFAULT 'XAUUSD',
                timeframe       TEXT NOT NULL,
                filename        TEXT NOT NULL,
                file_hash       TEXT NOT NULL UNIQUE,
                total_rows      INTEGER NOT NULL,
                start_datetime  TEXT NOT NULL,
                end_datetime    TEXT NOT NULL,
                upload_datetime TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'active'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_candles (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
                dt         TEXT NOT NULL,
                open       REAL NOT NULL,
                high       REAL NOT NULL,
                low        REAL NOT NULL,
                close      REAL NOT NULL,
                volume     REAL NOT NULL,
                UNIQUE(dataset_id, dt)
            )
        """)

        # ── Research history ──────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_runs (
                research_id       TEXT PRIMARY KEY,
                created_datetime  TEXT NOT NULL,
                research_name     TEXT,
                selected_module   TEXT NOT NULL,
                symbol            TEXT NOT NULL DEFAULT 'XAUUSD',
                timeframe_mode    TEXT NOT NULL,
                timeframes_used   TEXT NOT NULL,
                dataset_ids_used  TEXT NOT NULL,
                risk_percent      REAL NOT NULL,
                reward_risk_ratio REAL NOT NULL,
                lookback          INTEGER NOT NULL DEFAULT 5,
                total_trades      INTEGER NOT NULL DEFAULT 0,
                wins              INTEGER NOT NULL DEFAULT 0,
                losses            INTEGER NOT NULL DEFAULT 0,
                win_rate          REAL NOT NULL DEFAULT 0.0,
                profit_factor     REAL NOT NULL DEFAULT 0.0,
                net_r             REAL NOT NULL DEFAULT 0.0,
                monthly_return    REAL NOT NULL DEFAULT 0.0,
                max_drawdown      REAL NOT NULL DEFAULT 0.0,
                goal_status       TEXT NOT NULL DEFAULT 'INSUFFICIENT DATA',
                full_report       TEXT,
                status            TEXT NOT NULL DEFAULT 'completed'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                research_id TEXT NOT NULL REFERENCES research_runs(research_id) ON DELETE CASCADE,
                date        TEXT,
                time        TEXT,
                direction   TEXT,
                swept_level REAL,
                entry       REAL,
                sl          REAL,
                tp          REAL,
                exit_price  REAL,
                result      TEXT,
                r_multiple  REAL,
                bars_held   INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                research_id TEXT NOT NULL REFERENCES research_runs(research_id) ON DELETE CASCADE,
                month       TEXT,
                trades      INTEGER,
                wins        INTEGER,
                losses      INTEGER,
                net_r       REAL,
                return_pct  REAL
            )
        """)

        # Indexes for common query patterns
        conn.execute("CREATE INDEX IF NOT EXISTS idx_candles_dataset  ON ohlcv_candles(dataset_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tradelog_research ON trade_logs(research_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_monthly_research  ON monthly_reports(research_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_datasets_hash     ON datasets(file_hash)")


# ── Legacy helpers (unchanged for backward compat) ────────────────────────────

def save_upload(filename: str, filepath: str, rows: int) -> str:
    uid = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO uploads VALUES (?,?,?,?,?)",
            (uid, filename, filepath, rows, _now()),
        )
    return uid


def get_upload_by_id(upload_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
    return dict(row) if row else None


def save_analysis(upload_id, module, timeframe, risk_pct, rr, result) -> str:
    uid = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?)",
            (uid, upload_id, module, timeframe, risk_pct, rr, json.dumps(result), _now()),
        )
    return uid


def get_recent_analyses(limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT a.id, a.module, a.timeframe, a.risk_pct, a.rr,
                      a.result, a.created_at, u.filename
               FROM analyses a
               LEFT JOIN uploads u ON a.upload_id = u.id
               ORDER BY a.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Dataset functions ─────────────────────────────────────────────────────────

def get_dataset_by_hash(file_hash: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM datasets WHERE file_hash=?", (file_hash,)
        ).fetchone()
    return dict(row) if row else None


def get_dataset_by_id(dataset_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM datasets WHERE dataset_id=?", (dataset_id,)
        ).fetchone()
    return dict(row) if row else None


def save_dataset_metadata(
    symbol: str,
    timeframe: str,
    filename: str,
    file_hash: str,
    total_rows: int,
    start_datetime: str,
    end_datetime: str,
) -> str:
    did = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """INSERT INTO datasets
               (dataset_id, symbol, timeframe, filename, file_hash,
                total_rows, start_datetime, end_datetime, upload_datetime, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (did, symbol, timeframe, filename, file_hash,
             total_rows, start_datetime, end_datetime, _now(), "active"),
        )
    return did


def save_dataset_candles(dataset_id: str, candles: list[dict[str, Any]]) -> int:
    """Bulk-insert candles; returns number of rows actually inserted."""
    rows = [
        (dataset_id, c["dt"], c["open"], c["high"], c["low"], c["close"], c["volume"])
        for c in candles
    ]
    with _connect() as conn:
        cur = conn.executemany(
            "INSERT OR IGNORE INTO ohlcv_candles "
            "(dataset_id, dt, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    return cur.rowcount


def get_dataset_candles(dataset_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT dt, open, high, low, close, volume "
            "FROM ohlcv_candles WHERE dataset_id=? ORDER BY dt",
            (dataset_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_datasets() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM datasets WHERE status='active' ORDER BY upload_datetime DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_dataset(dataset_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM datasets WHERE dataset_id=?", (dataset_id,))
    return cur.rowcount > 0


# ── Research run functions ────────────────────────────────────────────────────

def save_research_run(
    research_name: str | None,
    selected_module: str,
    symbol: str,
    timeframe_mode: str,
    timeframes_used: list[str],
    dataset_ids_used: dict,
    risk_percent: float,
    reward_risk_ratio: float,
    lookback: int,
    report: dict,
) -> str:
    rid = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """INSERT INTO research_runs (
                research_id, created_datetime, research_name,
                selected_module, symbol, timeframe_mode,
                timeframes_used, dataset_ids_used,
                risk_percent, reward_risk_ratio, lookback,
                total_trades, wins, losses, win_rate,
                profit_factor, net_r, monthly_return,
                max_drawdown, goal_status, full_report, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid, _now(),
                research_name or f"Run {rid[:8]}",
                selected_module, symbol, timeframe_mode,
                json.dumps(timeframes_used),
                json.dumps(dataset_ids_used),
                risk_percent, reward_risk_ratio, lookback,
                report.get("total_trades", 0),
                report.get("win_trades", 0),
                report.get("loss_trades", 0),
                report.get("win_rate", 0.0),
                report.get("profit_factor", 0.0),
                report.get("net_r", 0.0),
                report.get("monthly_return", 0.0),
                report.get("max_drawdown", 0.0),
                report.get("goal_status", "INSUFFICIENT DATA"),
                json.dumps(report),
                "completed",
            ),
        )
    return rid


def save_research_run_complete(
    research_name: str | None,
    selected_module: str,
    symbol: str,
    timeframe_mode: str,
    timeframes_used: list[str],
    dataset_ids_used: dict,
    risk_percent: float,
    reward_risk_ratio: float,
    lookback: int,
    report: dict,
    trades: list[dict],
    monthly_breakdown: list[dict],
) -> str:
    """
    Save research_run + trade_logs + monthly_reports in one atomic transaction.

    Avoids FK constraint failures that occur when the three operations use
    separate connections — the parent row must exist in the same transaction
    before SQLite validates the child FK references.
    """
    rid = str(uuid.uuid4())
    with _connect() as conn:
        # 1. Parent row
        conn.execute(
            """INSERT INTO research_runs (
                research_id, created_datetime, research_name,
                selected_module, symbol, timeframe_mode,
                timeframes_used, dataset_ids_used,
                risk_percent, reward_risk_ratio, lookback,
                total_trades, wins, losses, win_rate,
                profit_factor, net_r, monthly_return,
                max_drawdown, goal_status, full_report, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid, _now(),
                research_name or f"Run {rid[:8]}",
                selected_module, symbol, timeframe_mode,
                json.dumps(timeframes_used),
                json.dumps(dataset_ids_used),
                risk_percent, reward_risk_ratio, lookback,
                report.get("total_trades", 0),
                report.get("win_trades", 0),
                report.get("loss_trades", 0),
                report.get("win_rate", 0.0),
                report.get("profit_factor", 0.0),
                report.get("net_r", 0.0),
                report.get("monthly_return", 0.0),
                report.get("max_drawdown", 0.0),
                report.get("goal_status", "INSUFFICIENT DATA"),
                json.dumps(report),
                "completed",
            ),
        )

        # 2. Trade logs (FK: research_id → research_runs — parent row above already in tx)
        if trades:
            conn.executemany(
                """INSERT INTO trade_logs
                   (research_id, date, time, direction, swept_level, entry, sl, tp,
                    exit_price, result, r_multiple, bars_held)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        rid,
                        t.get("date"), t.get("time"), t.get("direction"),
                        t.get("swept_level"), t.get("entry"), t.get("sl"), t.get("tp"),
                        t.get("exit_price"), t.get("result"),
                        t.get("r_multiple"), t.get("bars_held"),
                    )
                    for t in trades
                ],
            )

        # 3. Monthly breakdown (FK: research_id → research_runs)
        if monthly_breakdown:
            conn.executemany(
                "INSERT INTO monthly_reports "
                "(research_id, month, trades, wins, losses, net_r, return_pct) "
                "VALUES (?,?,?,?,?,?,?)",
                [
                    (rid, b["month"], b["trades"], b["wins"],
                     b["losses"], b["net_r"], b["return_pct"])
                    for b in monthly_breakdown
                ],
            )

    return rid


def list_research_runs(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM research_runs ORDER BY created_datetime DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_research_run(research_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM research_runs WHERE research_id=?", (research_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_research_run(research_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM research_runs WHERE research_id=?", (research_id,)
        )
    return cur.rowcount > 0


def save_trade_logs(research_id: str, trades: list[dict]) -> None:
    rows = [
        (
            research_id,
            t.get("date"), t.get("time"), t.get("direction"),
            t.get("swept_level"), t.get("entry"), t.get("sl"), t.get("tp"),
            t.get("exit_price"), t.get("result"),
            t.get("r_multiple"), t.get("bars_held"),
        )
        for t in trades
    ]
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO trade_logs
               (research_id, date, time, direction, swept_level, entry, sl, tp,
                exit_price, result, r_multiple, bars_held)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )


def get_trade_logs(research_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trade_logs WHERE research_id=? ORDER BY date, time",
            (research_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_monthly_reports(research_id: str, breakdown: list[dict]) -> None:
    rows = [
        (research_id, b["month"], b["trades"], b["wins"],
         b["losses"], b["net_r"], b["return_pct"])
        for b in breakdown
    ]
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO monthly_reports "
            "(research_id, month, trades, wins, losses, net_r, return_pct) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def get_monthly_reports(research_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM monthly_reports WHERE research_id=? ORDER BY month",
            (research_id,),
        ).fetchall()
    return [dict(r) for r in rows]
