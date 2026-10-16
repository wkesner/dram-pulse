"""SQLite persistence for normalized signal records."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from dram_signals.catalog.schema import CATALOG_SCHEMA
from dram_signals.scoring.schema import SCORING_SCHEMA


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    company_name TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    url TEXT NOT NULL,
    external_id TEXT NOT NULL,
    keyword TEXT,
    collected_at TEXT NOT NULL,
    UNIQUE(source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_signals_source_ts
    ON signals(source, timestamp);

CREATE INDEX IF NOT EXISTS idx_signals_company
    ON signals(company_name);

CREATE TABLE IF NOT EXISTS collector_state (
    source TEXT PRIMARY KEY,
    last_run_at TEXT,
    watermark TEXT
);
""" + CATALOG_SCHEMA + SCORING_SCHEMA


@dataclass(frozen=True)
class SignalRecord:
    """Normalized collector output row."""

    source: str
    timestamp: str
    company_name: str
    raw_text: str
    url: str
    external_id: str
    keyword: Optional[str] = None


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def db_session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_signals(conn: sqlite3.Connection, records: list[SignalRecord]) -> int:
    """Insert new records; skip duplicates on (source, external_id). Returns insert count."""
    if not records:
        return 0

    collected_at = datetime.now(timezone.utc).isoformat()
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO signals (
            source, timestamp, company_name, raw_text, url,
            external_id, keyword, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r.source,
                r.timestamp,
                r.company_name,
                r.raw_text,
                r.url,
                r.external_id,
                r.keyword,
                collected_at,
            )
            for r in records
        ],
    )
    return conn.total_changes - before


def get_watermark(conn: sqlite3.Connection, source: str) -> Optional[str]:
    row = conn.execute(
        "SELECT watermark FROM collector_state WHERE source = ?",
        (source,),
    ).fetchone()
    return row["watermark"] if row else None


def set_collector_state(
    conn: sqlite3.Connection,
    source: str,
    *,
    watermark: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO collector_state (source, last_run_at, watermark)
        VALUES (?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            last_run_at = excluded.last_run_at,
            watermark = COALESCE(excluded.watermark, collector_state.watermark)
        """,
        (source, now, watermark),
    )


def count_signals(conn: sqlite3.Connection, source: Optional[str] = None) -> int:
    if source:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM signals WHERE source = ?",
            (source,),
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()
    return int(row["n"])
