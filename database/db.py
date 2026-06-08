"""
SQLite database helpers — XAUUSD Quant Research Platform

Tables
──────
uploads          legacy single-file upload metadata (compat)
analyses         legacy analysis records (compat)
datasets         OHLCV dataset metadata with SHA-256 dedup
ohlcv_candles    raw candle storage  (FK → datasets.dataset_id, CASCADE)
research_runs    persistent research history
trade_logs       per-trade records   (FK → research_runs.research_id, CASCADE)
monthly_reports  monthly breakdown   (FK → research_runs.research_id, CASCADE)

FK correctness
──────────────
FOREIGN KEY(research_id) REFERENCES research_runs(research_id)
FOREIGN KEY(dataset_id)  REFERENCES datasets(dataset_id)

init_db() inspects the live on-disk schema using PRAGMA foreign_key_list()
and automatically drops + recreates any table whose FK references the wrong
column (e.g. research_runs(id) from a stale migration).
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


# ── Connection context ────────────────────────────────────────────────────────

@contextmanager
def _connect():
    """
    SQLite connection with fully explicit transaction management.

    * isolation_level=None  — Python's sqlite3 never issues implicit
      BEGIN/COMMIT/ROLLBACK, eliminating any implicit-tx interference.
    * Explicit BEGIN before yield, COMMIT after, ROLLBACK on exception.
    * PRAGMA foreign_keys = ON set *before* BEGIN (required by SQLite docs).
    * PRAGMA defer_foreign_keys = ON set *inside* the transaction so all FK
      checks run at COMMIT time — parent rows are guaranteed to exist by then.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys  = ON")
    try:
        conn.execute("BEGIN")
        conn.execute("PRAGMA defer_foreign_keys = ON")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Schema introspection helpers ──────────────────────────────────────────────

def _tbl_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _tbl_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _tbl_fk_list(conn, table: str) -> list[dict]:
    """Return FK definitions for *table* as a list of dicts."""
    rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    return [dict(r) for r in rows]


# ── Research table DDL (shared by init + repair) ──────────────────────────────

_RESEARCH_RUNS_DDL = """
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
        win_rate          REAL    NOT NULL DEFAULT 0.0,
        profit_factor     REAL    NOT NULL DEFAULT 0.0,
        net_r             REAL    NOT NULL DEFAULT 0.0,
        monthly_return    REAL    NOT NULL DEFAULT 0.0,
        max_drawdown      REAL    NOT NULL DEFAULT 0.0,
        goal_status       TEXT    NOT NULL DEFAULT 'INSUFFICIENT DATA',
        full_report       TEXT,
        status            TEXT    NOT NULL DEFAULT 'completed',
        backtest_start    TEXT,
        backtest_end      TEXT,
        analysis_sub_mode TEXT    DEFAULT 'full_backtest'
    )
"""

_TRADE_LOGS_DDL = """
    CREATE TABLE IF NOT EXISTS trade_logs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        research_id TEXT    NOT NULL,
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
        bars_held   INTEGER,
        FOREIGN KEY(research_id) REFERENCES research_runs(research_id) ON DELETE CASCADE
    )
"""

_MONTHLY_REPORTS_DDL = """
    CREATE TABLE IF NOT EXISTS monthly_reports (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        research_id TEXT    NOT NULL,
        month       TEXT,
        trades      INTEGER,
        wins        INTEGER,
        losses      INTEGER,
        net_r       REAL,
        return_pct  REAL,
        FOREIGN KEY(research_id) REFERENCES research_runs(research_id) ON DELETE CASCADE
    )
"""


def _create_research_tables(conn) -> None:
    """CREATE IF NOT EXISTS all three research tables + their indexes."""
    conn.execute(_RESEARCH_RUNS_DDL)
    conn.execute(_TRADE_LOGS_DDL)
    conn.execute(_MONTHLY_REPORTS_DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tradelog_research ON trade_logs(research_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_monthly_research  ON monthly_reports(research_id)")


# ── FK schema validation & auto-repair ───────────────────────────────────────

def _check_and_repair_research_schema(conn) -> None:
    """
    Inspect the LIVE on-disk FK definitions for trade_logs and
    monthly_reports.  If any FK references the wrong column (e.g.
    research_runs.id instead of research_runs.research_id — a relic of an
    older migration), drop all three research tables and recreate them with
    the correct schema.

    Datasets and ohlcv_candles are never modified here.
    Root cause of the persistent IntegrityError: FOREIGN KEY constraint
    failed is precisely this kind of stale FK definition that CREATE TABLE
    IF NOT EXISTS never updates.
    """
    issues: list[str] = []

    # ── 1. research_runs must have 'research_id' as a column ──────────────────
    if _tbl_exists(conn, "research_runs"):
        cols = _tbl_columns(conn, "research_runs")
        if "research_id" not in cols:
            issues.append(
                f"research_runs is missing column 'research_id' — found: {sorted(cols)}"
            )
        else:
            print(f"[db] research_runs.research_id ✓  (columns: {sorted(cols)})", flush=True)

    # ── 2. trade_logs FK must reference research_runs(research_id) ────────────
    if not issues and _tbl_exists(conn, "trade_logs"):
        for fk in _tbl_fk_list(conn, "trade_logs"):
            ref_table = fk.get("table", "")
            ref_col   = fk.get("to")          # None → implicit PK reference
            from_col  = fk.get("from", "")
            display   = f"{ref_table}.{ref_col or '(PK)'}"
            print(f"[db] FK  trade_logs.{from_col} → {display}", flush=True)
            if ref_table == "research_runs":
                # None = implicit PK = research_id → OK
                # 'research_id' explicitly = OK
                # anything else = broken
                if ref_col is not None and ref_col != "research_id":
                    issues.append(
                        f"trade_logs.{from_col} FK → {display}  "
                        f"(expected research_runs.research_id)"
                    )

    # ── 3. monthly_reports FK must reference research_runs(research_id) ───────
    if not issues and _tbl_exists(conn, "monthly_reports"):
        for fk in _tbl_fk_list(conn, "monthly_reports"):
            ref_table = fk.get("table", "")
            ref_col   = fk.get("to")
            from_col  = fk.get("from", "")
            display   = f"{ref_table}.{ref_col or '(PK)'}"
            print(f"[db] FK  monthly_reports.{from_col} → {display}", flush=True)
            if ref_table == "research_runs":
                if ref_col is not None and ref_col != "research_id":
                    issues.append(
                        f"monthly_reports.{from_col} FK → {display}  "
                        f"(expected research_runs.research_id)"
                    )

    # ── Repair ────────────────────────────────────────────────────────────────
    if issues:
        print("[db] ─────────────────────────────────────────────────", flush=True)
        for msg in issues:
            print(f"[db] ⚠️  Schema issue: {msg}", flush=True)
        print(
            "[db] Dropping monthly_reports, trade_logs, research_runs "
            "and recreating with correct FK schema …",
            flush=True,
        )
        conn.execute("DROP TABLE IF EXISTS monthly_reports")
        conn.execute("DROP TABLE IF EXISTS trade_logs")
        conn.execute("DROP TABLE IF EXISTS research_runs")
        _create_research_tables(conn)
        print("[db] ✅ Research tables recreated with correct schema.", flush=True)
        print("[db] ─────────────────────────────────────────────────", flush=True)
    else:
        print("[db] ✅ FK schema check passed — no issues found.", flush=True)


# ── Schema init ───────────────────────────────────────────────────────────────

def init_db() -> None:
    """
    Idempotent schema setup.  Safe to call on every server start.

    Order:
      1. Legacy tables (uploads, analyses)
      2. Dataset tables (datasets, ohlcv_candles)
      3. Research tables (research_runs, trade_logs, monthly_reports)
      4. Indexes
      5. FK schema check + auto-repair   ← detects & fixes broken FK refs
      6. Column migrations               ← adds new columns to existing tables
    """
    with _connect() as conn:
        # ── Legacy ───────────────────────────────────────────────────────────
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

        # ── Datasets ─────────────────────────────────────────────────────────
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
                dataset_id TEXT    NOT NULL,
                dt         TEXT    NOT NULL,
                open       REAL    NOT NULL,
                high       REAL    NOT NULL,
                low        REAL    NOT NULL,
                close      REAL    NOT NULL,
                volume     REAL    NOT NULL,
                UNIQUE(dataset_id, dt),
                FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
            )
        """)

        # ── Research ─────────────────────────────────────────────────────────
        _create_research_tables(conn)

        # ── Indexes ───────────────────────────────────────────────────────────
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candles_dataset ON ohlcv_candles(dataset_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_datasets_hash    ON datasets(file_hash)"
        )

        # ── FK schema validation + auto-repair ────────────────────────────────
        # Inspects PRAGMA foreign_key_list() for each research table.
        # If any FK references the wrong column (stale schema from older code),
        # the three research tables are dropped and recreated with correct FKs.
        _check_and_repair_research_schema(conn)

        # ── Column migrations (safe for existing databases) ───────────────────
        for _sql in [
            "ALTER TABLE research_runs ADD COLUMN backtest_start    TEXT",
            "ALTER TABLE research_runs ADD COLUMN backtest_end      TEXT",
            "ALTER TABLE research_runs ADD COLUMN analysis_sub_mode TEXT DEFAULT 'full_backtest'",
        ]:
            try:
                conn.execute(_sql)
            except sqlite3.OperationalError:
                pass   # column already exists → no-op

    print("[db] init_db() complete.", flush=True)


# ── Legacy helpers ────────────────────────────────────────────────────────────

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
               FROM   analyses a
               LEFT JOIN uploads u ON a.upload_id = u.id
               ORDER  BY a.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Dataset functions ─────────────────────────────────────────────────────────

def dataset_exists(dataset_id: str) -> bool:
    """Return True if dataset_id is present and active in the datasets table."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM datasets WHERE dataset_id=? AND status='active'",
            (dataset_id,),
        ).fetchone()
    return row is not None


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
    print(f"[db] dataset_metadata saved: {did} ({timeframe} / {filename})", flush=True)
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
    inserted = cur.rowcount
    print(f"[db] ohlcv_candles inserted: {inserted} rows for dataset_id={dataset_id}", flush=True)
    return inserted


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


# ── Safe research-run helpers (used inside save_research_run_complete) ─────────

def create_research_run(conn, rid: str, **kw) -> str:
    """
    INSERT one row into research_runs using an existing connection *conn*
    (must already be inside a _connect() context / open transaction).

    Returns rid so callers can use it immediately for child-table inserts.
    """
    report = kw.get("report", {})
    conn.execute(
        """INSERT INTO research_runs (
            research_id, created_datetime, research_name,
            selected_module, symbol, timeframe_mode,
            timeframes_used, dataset_ids_used,
            risk_percent, reward_risk_ratio, lookback,
            total_trades, wins, losses, win_rate,
            profit_factor, net_r, monthly_return,
            max_drawdown, goal_status, full_report, status,
            backtest_start, backtest_end, analysis_sub_mode
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rid,
            _now(),
            kw.get("research_name") or f"Run {rid[:8]}",
            kw["selected_module"],
            kw.get("symbol", "XAUUSD"),
            kw["timeframe_mode"],
            json.dumps(kw.get("timeframes_used", [])),
            json.dumps(kw.get("dataset_ids_used", {})),
            kw["risk_percent"],
            kw["reward_risk_ratio"],
            kw["lookback"],
            report.get("total_trades", 0),
            report.get("win_trades", 0),
            report.get("loss_trades", 0),
            report.get("win_rate",        0.0),
            report.get("profit_factor",   0.0),
            report.get("net_r",           0.0),
            report.get("monthly_return",  0.0),
            report.get("max_drawdown",    0.0),
            report.get("goal_status", "INSUFFICIENT DATA"),
            json.dumps(report),
            "completed",
            kw.get("backtest_start"),
            kw.get("backtest_end"),
            kw.get("analysis_sub_mode", "full_backtest"),
        ),
    )
    print(f"[db] research_runs row inserted: {rid}", flush=True)
    return rid


def insert_trade_logs(conn, research_id: str, trades: list[dict]) -> int:
    """
    Bulk-INSERT trade_logs rows using an existing open connection *conn*.
    Skips gracefully when trades is empty or research_id is None.
    Returns the number of rows inserted.
    """
    if not trades or research_id is None:
        print(f"[db] trade_logs skipped (trades={len(trades) if trades else 0}, research_id={research_id})", flush=True)
        return 0
    conn.executemany(
        """INSERT INTO trade_logs
           (research_id, date, time, direction, swept_level, entry, sl, tp,
            exit_price, result, r_multiple, bars_held)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                research_id,
                t.get("date"), t.get("time"), t.get("direction"),
                t.get("swept_level"), t.get("entry"), t.get("sl"), t.get("tp"),
                t.get("exit_price"), t.get("result"),
                t.get("r_multiple"), t.get("bars_held"),
            )
            for t in trades
        ],
    )
    print(f"[db] trade_logs inserted: {len(trades)} rows (research_id={research_id})", flush=True)
    return len(trades)


def insert_monthly_reports(conn, research_id: str, monthly: list[dict]) -> int:
    """
    Bulk-INSERT monthly_reports rows using an existing open connection *conn*.
    Skips gracefully when monthly is empty or research_id is None.
    Returns the number of rows inserted.
    """
    if not monthly or research_id is None:
        print(f"[db] monthly_reports skipped (rows={len(monthly) if monthly else 0}, research_id={research_id})", flush=True)
        return 0
    conn.executemany(
        """INSERT INTO monthly_reports
           (research_id, month, trades, wins, losses, net_r, return_pct)
           VALUES (?,?,?,?,?,?,?)""",
        [
            (research_id, b["month"], b["trades"], b["wins"],
             b["losses"], b["net_r"], b["return_pct"])
            for b in monthly
        ],
    )
    print(f"[db] monthly_reports inserted: {len(monthly)} rows (research_id={research_id})", flush=True)
    return len(monthly)


# ── Research-run CRUD ─────────────────────────────────────────────────────────

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
    backtest_start: str | None = None,
    backtest_end: str | None = None,
    analysis_sub_mode: str = "full_backtest",
) -> str:
    """
    Persist a research run atomically.

    Transaction order (requirement):
      BEGIN
        INSERT research_runs          ← parent; research_id generated here
        INSERT trade_logs             ← child FK → research_runs.research_id
        INSERT monthly_reports        ← child FK → research_runs.research_id
      COMMIT   (FK checks run here via defer_foreign_keys)

    Any exception triggers ROLLBACK — no partial writes.
    """
    rid = str(uuid.uuid4())

    print("[db] ──────────────────────────────────────────────────", flush=True)
    print(f"[db] save_research_run_complete START  rid={rid}", flush=True)
    print(f"[db]   dataset_ids_used : {dataset_ids_used}", flush=True)
    print(f"[db]   analysis_sub_mode: {analysis_sub_mode}", flush=True)
    print(f"[db]   trades           : {len(trades)}", flush=True)
    print(f"[db]   monthly rows     : {len(monthly_breakdown)}", flush=True)

    with _connect() as conn:
        # Step 1 — parent: research_runs
        create_research_run(
            conn, rid,
            research_name    = research_name,
            selected_module  = selected_module,
            symbol           = symbol,
            timeframe_mode   = timeframe_mode,
            timeframes_used  = timeframes_used,
            dataset_ids_used = dataset_ids_used,
            risk_percent     = risk_percent,
            reward_risk_ratio= reward_risk_ratio,
            lookback         = lookback,
            report           = report,
            backtest_start   = backtest_start,
            backtest_end     = backtest_end,
            analysis_sub_mode= analysis_sub_mode,
        )

        # Step 2 — children: trade_logs
        n_trades = insert_trade_logs(conn, rid, trades)

        # Step 3 — children: monthly_reports
        n_monthly = insert_monthly_reports(conn, rid, monthly_breakdown)

    print(f"[db] save_research_run_complete END    rid={rid}  trades={n_trades}  monthly={n_monthly}", flush=True)
    print("[db] ──────────────────────────────────────────────────", flush=True)
    return rid


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
    """Legacy single-table insert (backward compat — no trade/monthly rows)."""
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
                report.get("win_trades",   0),
                report.get("loss_trades",  0),
                report.get("win_rate",     0.0),
                report.get("profit_factor",0.0),
                report.get("net_r",        0.0),
                report.get("monthly_return",0.0),
                report.get("max_drawdown", 0.0),
                report.get("goal_status",  "INSUFFICIENT DATA"),
                json.dumps(report),
                "completed",
            ),
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
    """Legacy standalone insert (kept for compat)."""
    with _connect() as conn:
        insert_trade_logs(conn, research_id, trades)


def get_trade_logs(research_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trade_logs WHERE research_id=? ORDER BY date, time",
            (research_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_monthly_reports(research_id: str, breakdown: list[dict]) -> None:
    """Legacy standalone insert (kept for compat)."""
    with _connect() as conn:
        insert_monthly_reports(conn, research_id, breakdown)


def get_monthly_reports(research_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM monthly_reports WHERE research_id=? ORDER BY month",
            (research_id,),
        ).fetchall()
    return [dict(r) for r in rows]
