"""Entity resolution → company_key with end builds attached (section 7.2)."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

# SIC → end-build starter guesses (coarse; Clay/industry data later)
SIC_END_BUILDS: dict[str, list[str]] = {
    "3571": ["current_servers", "pcs"],  # Electronic computers
    "3577": ["current_servers", "ai_servers"],
    "3661": ["networking_telecom"],
    "3663": ["networking_telecom"],
    "3674": ["embedded", "industrial"],  # Semiconductors
    "3672": ["pcs", "embedded"],
    "3711": ["automotive", "automotive_infotainment"],
    "3714": ["automotive"],
    "3841": ["medical"],  # costly stopped-line industries tracked separately
    "3812": ["aerospace_defense", "hpc"],
    "7372": ["pcs"],
}

REVENUE_BUYER_TYPE = (
    (5_000_000_000, "very_large_oem"),
    (250_000_000, "mid_oem_ems"),
    (20_000_000, "small"),
    (0, "very_small"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\b(inc|corp|corporation|ltd|llc|co|company|plc|sa|ag|nv)\b\.?", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slug_key(name: str) -> str:
    return normalize_name(name).replace(" ", "-") or "unknown"


def buyer_type_from_revenue(revenue_usd: Optional[float], *, hyperscaler: bool = False) -> str:
    if hyperscaler:
        return "hyperscaler"
    if revenue_usd is None:
        return "mid_oem_ems"
    for threshold, label in REVENUE_BUYER_TYPE:
        if revenue_usd >= threshold:
            return label
    return "very_small"


def end_builds_from_sic(sic: Optional[str]) -> list[str]:
    if not sic:
        return []
    code = str(sic).strip()[:4]
    return list(SIC_END_BUILDS.get(code, []))


def resolve_or_create(
    conn: sqlite3.Connection,
    *,
    display_name: str,
    broker_id: str = "default",
    cik: Optional[str] = None,
    domain: Optional[str] = None,
    sic: Optional[str] = None,
    revenue_usd: Optional[float] = None,
    gross_margin: Optional[float] = None,
    buyer_type: Optional[str] = None,
    end_builds: Optional[list[str]] = None,
    families_traded: Optional[list[str]] = None,
    relationship: str = "none",
    gate_flags: Optional[dict[str, Any]] = None,
) -> str:
    """Return company_key, creating/updating the company row."""
    norm = normalize_name(display_name)
    existing = None
    if cik:
        existing = conn.execute(
            "SELECT company_key FROM companies WHERE cik = ? AND broker_id = ?",
            (cik, broker_id),
        ).fetchone()
    if not existing:
        row = conn.execute(
            """
            SELECT company_key FROM company_aliases
            WHERE alias_norm = ? AND broker_id = ?
            """,
            (norm, broker_id),
        ).fetchone()
        if row:
            existing = row

    builds = end_builds if end_builds is not None else end_builds_from_sic(sic)
    btype = buyer_type or buyer_type_from_revenue(revenue_usd)
    now = _now()

    if existing:
        key = existing["company_key"] if isinstance(existing, sqlite3.Row) else existing[0]
        conn.execute(
            """
            UPDATE companies SET
                display_name = COALESCE(?, display_name),
                cik = COALESCE(?, cik),
                domain = COALESCE(?, domain),
                sic = COALESCE(?, sic),
                revenue_usd = COALESCE(?, revenue_usd),
                gross_margin = COALESCE(?, gross_margin),
                buyer_type = COALESCE(?, buyer_type),
                end_builds_json = COALESCE(?, end_builds_json),
                families_traded_json = COALESCE(?, families_traded_json),
                relationship = COALESCE(?, relationship),
                gate_flags_json = COALESCE(?, gate_flags_json),
                updated_at = ?
            WHERE company_key = ? AND broker_id = ?
            """,
            (
                display_name,
                cik,
                domain,
                sic,
                revenue_usd,
                gross_margin,
                btype,
                json.dumps(builds) if builds else None,
                json.dumps(families_traded) if families_traded is not None else None,
                relationship if relationship != "none" else None,
                json.dumps(gate_flags) if gate_flags else None,
                now,
                key,
                broker_id,
            ),
        )
        return str(key)

    key = slug_key(display_name)
    # Avoid collisions
    base = key
    n = 2
    while conn.execute(
        "SELECT 1 FROM companies WHERE company_key = ?", (key,)
    ).fetchone():
        key = f"{base}-{n}"
        n += 1

    conn.execute(
        """
        INSERT INTO companies (
            company_key, display_name, cik, domain, sic, revenue_usd, gross_margin,
            buyer_type, end_builds_json, families_traded_json, relationship,
            gate_flags_json, broker_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            display_name,
            cik,
            domain,
            sic,
            revenue_usd,
            gross_margin,
            btype,
            json.dumps(builds),
            json.dumps(families_traded or []),
            relationship,
            json.dumps(gate_flags or {}),
            broker_id,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO company_aliases (company_key, alias, alias_norm, source, broker_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (key, display_name, norm, "resolve", broker_id),
    )
    return key


def get_company(conn: sqlite3.Connection, company_key: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM companies WHERE company_key = ?", (company_key,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["end_builds"] = json.loads(d.pop("end_builds_json") or "[]")
    d["families_traded"] = json.loads(d.pop("families_traded_json") or "[]")
    d["gate_flags"] = json.loads(d.pop("gate_flags_json") or "{}")
    return d
