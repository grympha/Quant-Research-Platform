import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "quant.db"


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS uploads (
                id          TEXT PRIMARY KEY,
                filename    TEXT NOT NULL,
                filepath    TEXT NOT NULL,
                rows        INTEGER,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analyses (
                id          TEXT PRIMARY KEY,
                upload_id   TEXT NOT NULL,
                module      TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                risk_pct    REAL NOT NULL,
                rr          REAL NOT NULL,
                result      TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (upload_id) REFERENCES uploads(id)
            );
        """)


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_upload(filename: str, filepath: str, rows: int) -> str:
    uid = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO uploads VALUES (?,?,?,?,?)",
            (uid, filename, filepath, rows, datetime.utcnow().isoformat()),
        )
    return uid


def get_upload_by_id(upload_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM uploads WHERE id = ?", (upload_id,)
        ).fetchone()
    return dict(row) if row else None


def save_analysis(
    upload_id: str,
    module: str,
    timeframe: str,
    risk_pct: float,
    rr: float,
    result: dict,
) -> str:
    uid = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?)",
            (
                uid,
                upload_id,
                module,
                timeframe,
                risk_pct,
                rr,
                json.dumps(result),
                datetime.utcnow().isoformat(),
            ),
        )
    return uid


def get_recent_analyses(limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.module, a.timeframe, a.risk_pct, a.rr,
                   a.result, a.created_at, u.filename
            FROM analyses a
            JOIN uploads u ON a.upload_id = u.id
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
