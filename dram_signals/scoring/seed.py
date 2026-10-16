"""Load hand-maintained scoring tables from data/scoring/*.yaml."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from dram_signals import config

SCORING_DATA = Path(
    __import__("os").environ.get(
        "DRAM_SIGNALS_SCORING_DATA",
        str(config.DATA_DIR / "scoring"),
    )
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_yaml(name: str) -> dict[str, Any]:
    path = SCORING_DATA / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def seed_reference_tables(conn: sqlite3.Connection) -> dict[str, int]:
    stats = {"regime": 0, "market_allocation": 0, "end_build_map": 0}
    now = _now()

    regime = _load_yaml("regime.yaml").get("families") or {}
    for family, meta in regime.items():
        conn.execute(
            """
            INSERT INTO family_regime (product_family, regime, notes, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(product_family) DO UPDATE SET
                regime = excluded.regime,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (family, meta.get("regime", "balanced"), meta.get("notes"), now),
        )
        stats["regime"] += 1

    alloc = _load_yaml("market_allocation.yaml").get("families") or {}
    for family, meta in alloc.items():
        conn.execute(
            """
            INSERT INTO market_allocation (product_family, active, source, noted_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(product_family) DO UPDATE SET
                active = excluded.active,
                source = excluded.source,
                noted_at = excluded.noted_at,
                updated_at = excluded.updated_at
            """,
            (
                family,
                1 if meta.get("active") else 0,
                meta.get("source"),
                meta.get("noted_at"),
                now,
            ),
        )
        stats["market_allocation"] += 1

    builds = _load_yaml("end_build_map.yaml").get("families") or {}
    conn.execute("DELETE FROM end_build_map")
    for family, rows in builds.items():
        for row in rows or []:
            conn.execute(
                """
                INSERT INTO end_build_map (product_family, end_build, match_strength)
                VALUES (?, ?, ?)
                """,
                (family, row["end_build"], row.get("match_strength", "core")),
            )
            stats["end_build_map"] += 1

    return stats


def get_regime(conn: sqlite3.Connection, product_family: str) -> str:
    row = conn.execute(
        "SELECT regime FROM family_regime WHERE product_family = ?",
        (product_family,),
    ).fetchone()
    if row:
        return str(row["regime"])
    row = conn.execute(
        "SELECT regime FROM family_regime WHERE product_family = 'unknown'"
    ).fetchone()
    return str(row["regime"]) if row else "balanced"


def market_allocation_active(conn: sqlite3.Connection, product_family: str) -> bool:
    row = conn.execute(
        "SELECT active FROM market_allocation WHERE product_family = ?",
        (product_family,),
    ).fetchone()
    return bool(row and row["active"])


def end_build_match_strength(
    conn: sqlite3.Connection,
    product_family: str,
    company_end_builds: list[str],
) -> str:
    """Return core | adjacent | none for the best overlap."""
    if not company_end_builds:
        return "none"
    rows = conn.execute(
        """
        SELECT end_build, match_strength FROM end_build_map
        WHERE product_family = ?
        """,
        (product_family,),
    ).fetchall()
    company_set = {b.lower().replace(" ", "_") for b in company_end_builds}
    best = "none"
    rank = {"none": 0, "adjacent": 1, "core": 2}
    for row in rows:
        eb = str(row["end_build"]).lower()
        if eb in company_set:
            strength = str(row["match_strength"])
            if rank.get(strength, 0) > rank[best]:
                best = strength
    return best
