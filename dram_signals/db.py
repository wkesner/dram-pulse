"""SQLite persistence for normalized signal records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

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
    first_seen_at TEXT,
    changed_at TEXT,
    content_hash TEXT,
    matched_terms TEXT,
    metadata_json TEXT,
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

CREATE TABLE IF NOT EXISTS exports (
    destination TEXT PRIMARY KEY,
    exported_until TEXT,
    last_run_at TEXT,
    last_stats_json TEXT
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
    matched_terms: Optional[tuple[str, ...]] = None
    metadata: Optional[dict[str, Any]] = None


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate_signals(conn: sqlite3.Connection) -> None:
    """Add change-tracking columns to existing DBs (section 3.2)."""
    cols = _table_columns(conn, "signals")
    alters = [
        ("first_seen_at", "TEXT"),
        ("changed_at", "TEXT"),
        ("content_hash", "TEXT"),
        ("matched_terms", "TEXT"),
        ("metadata_json", "TEXT"),
    ]
    for name, typ in alters:
        if name not in cols:
            conn.execute(f"ALTER TABLE signals ADD COLUMN {name} {typ}")

    # Backfill first_seen_at / changed_at from collected_at
    conn.execute(
        """
        UPDATE signals
        SET first_seen_at = COALESCE(first_seen_at, collected_at),
            changed_at = COALESCE(changed_at, collected_at)
        WHERE first_seen_at IS NULL OR changed_at IS NULL
        """
    )
    # Backfill content_hash for rows that never got one
    missing = conn.execute(
        "SELECT id, source, timestamp, company_name, raw_text, url, external_id, keyword, matched_terms, metadata_json "
        "FROM signals WHERE content_hash IS NULL OR content_hash = ''"
    ).fetchall()
    for row in missing:
        h = content_hash_for(
            timestamp=row["timestamp"],
            company_name=row["company_name"],
            raw_text=row["raw_text"],
            url=row["url"],
            keyword=row["keyword"],
            matched_terms=row["matched_terms"],
            metadata_json=row["metadata_json"],
        )
        conn.execute("UPDATE signals SET content_hash = ? WHERE id = ?", (h, row["id"]))

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_signals_changed_at ON signals(changed_at)"
    )
    # exports table for destinations that lack CREATE IF NOT EXISTS from old DBs
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exports (
            destination TEXT PRIMARY KEY,
            exported_until TEXT,
            last_run_at TEXT,
            last_stats_json TEXT
        )
        """
    )


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate_signals(conn)
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash_for(
    *,
    timestamp: str,
    company_name: str,
    raw_text: str,
    url: str,
    keyword: Optional[str] = None,
    matched_terms: Optional[str] = None,
    metadata_json: Optional[str] = None,
) -> str:
    blob = "\n".join(
        [
            timestamp or "",
            company_name or "",
            raw_text or "",
            url or "",
            keyword or "",
            matched_terms or "",
            metadata_json or "",
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _terms_json(record: SignalRecord) -> Optional[str]:
    if record.matched_terms:
        return json.dumps(list(record.matched_terms), sort_keys=True)
    if record.keyword:
        # Collectors historically store a single keyword string; support "a|b" merges.
        parts = [p.strip() for p in re_split_terms(record.keyword) if p.strip()]
        return json.dumps(parts, sort_keys=True) if parts else None
    return None


def re_split_terms(keyword: str) -> list[str]:
    import re

    return re.split(r"[|,;/]+", keyword)


def upsert_signals(conn: sqlite3.Connection, records: list[SignalRecord]) -> int:
    """Insert new records; update when content changes. Returns rows inserted or changed."""
    if not records:
        return 0

    now = _now()
    changed = 0
    for r in records:
        meta_json = json.dumps(r.metadata, sort_keys=True) if r.metadata else None
        terms = _terms_json(r)
        digest = content_hash_for(
            timestamp=r.timestamp,
            company_name=r.company_name,
            raw_text=r.raw_text,
            url=r.url,
            keyword=r.keyword,
            matched_terms=terms,
            metadata_json=meta_json,
        )
        existing = conn.execute(
            "SELECT id, content_hash FROM signals WHERE source = ? AND external_id = ?",
            (r.source, r.external_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO signals (
                    source, timestamp, company_name, raw_text, url,
                    external_id, keyword, collected_at, first_seen_at, changed_at,
                    content_hash, matched_terms, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.source,
                    r.timestamp,
                    r.company_name,
                    r.raw_text,
                    r.url,
                    r.external_id,
                    r.keyword,
                    now,
                    now,
                    now,
                    digest,
                    terms,
                    meta_json,
                ),
            )
            changed += 1
            continue

        if existing["content_hash"] == digest:
            # Seen again with identical content — do not bump changed_at.
            continue

        conn.execute(
            """
            UPDATE signals SET
                timestamp = ?,
                company_name = ?,
                raw_text = ?,
                url = ?,
                keyword = ?,
                matched_terms = ?,
                metadata_json = ?,
                content_hash = ?,
                changed_at = ?,
                collected_at = ?
            WHERE id = ?
            """,
            (
                r.timestamp,
                r.company_name,
                r.raw_text,
                r.url,
                r.keyword,
                terms,
                meta_json,
                digest,
                now,
                now,
                existing["id"],
            ),
        )
        changed += 1
    return changed


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
    now = _now()
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


def get_export_watermark(conn: sqlite3.Connection, destination: str) -> Optional[str]:
    row = conn.execute(
        "SELECT exported_until FROM exports WHERE destination = ?",
        (destination,),
    ).fetchone()
    return row["exported_until"] if row else None


def set_export_watermark(
    conn: sqlite3.Connection,
    destination: str,
    exported_until: str,
    *,
    stats: Optional[dict[str, Any]] = None,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO exports (destination, exported_until, last_run_at, last_stats_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(destination) DO UPDATE SET
            exported_until = excluded.exported_until,
            last_run_at = excluded.last_run_at,
            last_stats_json = excluded.last_stats_json
        """,
        (destination, exported_until, now, json.dumps(stats or {})),
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
