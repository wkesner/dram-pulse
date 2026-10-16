"""End-to-end: seed tables → classify raw signals → score leads → snapshot."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from dram_signals.scoring.classify import Classification, classify_raw_signal
from dram_signals.scoring.entity import get_company, resolve_or_create
from dram_signals.scoring.formula import ScoredSignalInput
from dram_signals.scoring.scorer import LeadContext, score_lead
from dram_signals.scoring.seed import (
    end_build_match_strength,
    get_regime,
    market_allocation_active,
    seed_reference_tables,
)
from dram_signals.scoring.snapshots import write_snapshot

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_days(event_at: str, as_of: Optional[datetime] = None) -> float:
    as_of = as_of or datetime.now(timezone.utc)
    try:
        ts = datetime.fromisoformat(event_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (as_of - ts).total_seconds() / 86400.0)


def classify_all_raw(
    conn: sqlite3.Connection,
    *,
    broker_id: str = "default",
    limit: Optional[int] = None,
) -> dict[str, int]:
    """Classify raw `signals` rows into `typed_signals` + resolve companies."""
    seed_reference_tables(conn)
    sql = "SELECT id, source, timestamp, company_name, raw_text, url, keyword FROM signals ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    stats = {"raw": 0, "typed": 0, "review": 0, "companies": 0}
    seen_companies: set[str] = set()

    for row in rows:
        stats["raw"] += 1
        name = row["company_name"]
        key = resolve_or_create(conn, display_name=name, broker_id=broker_id)
        if key not in seen_companies:
            seen_companies.add(key)
            stats["companies"] += 1

        classifications = classify_raw_signal(dict(row), company_key=key)
        if not classifications:
            continue
        for c in classifications:
            if c.confidence < 0.6:
                conn.execute(
                    """
                    INSERT INTO review_queue (reason, company_name, company_key, raw_signal_id, details_json, created_at)
                    VALUES ('low_confidence', ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        key,
                        row["id"],
                        json.dumps({"signal_type": c.signal_type, "confidence": c.confidence}),
                        _now(),
                    ),
                )
                stats["review"] += 1
                continue
            _upsert_typed(conn, row, key, c, broker_id=broker_id)
            stats["typed"] += 1
    return stats


def _upsert_typed(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    company_key: str,
    c: Classification,
    *,
    broker_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO typed_signals (
            raw_signal_id, company_key, side, signal_type, product_family,
            confidence, event_key, event_at, deadline, source, url, raw_text,
            broker_id, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_key, signal_type, broker_id) DO UPDATE SET
            confidence = MAX(typed_signals.confidence, excluded.confidence),
            raw_signal_id = excluded.raw_signal_id
        """,
        (
            row["id"],
            company_key,
            c.side,
            c.signal_type,
            c.product_family,
            c.confidence,
            c.event_key,
            c.event_at,
            c.deadline,
            row["source"],
            row["url"],
            (row["raw_text"] or "")[:5000],
            broker_id,
            json.dumps(c.metadata or {}),
            _now(),
        ),
    )


def score_open_leads(
    conn: sqlite3.Connection,
    *,
    broker_id: str = "default",
    as_of: Optional[datetime] = None,
) -> dict[str, int]:
    """Score every company×side with typed signals; write snapshots."""
    seed_reference_tables(conn)
    rows = conn.execute(
        """
        SELECT * FROM typed_signals
        WHERE broker_id = ? AND company_key IS NOT NULL
        ORDER BY company_key, side, event_at
        """,
        (broker_id,),
    ).fetchall()

    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(row["company_key"], row["side"])].append(row)

    stats = {"leads": 0, "scored": 0, "gated": 0, "tier_A": 0, "tier_B": 0, "tier_C": 0, "tier_D": 0}

    for (company_key, side), sigs in grouped.items():
        stats["leads"] += 1
        company = get_company(conn, company_key) or {
            "buyer_type": "mid_oem_ems",
            "families_traded": [],
            "relationship": "none",
            "end_builds": [],
            "gate_flags": {},
            "gross_margin": None,
        }
        # Dominant family = most common among signals
        families = [s["product_family"] for s in sigs if s["product_family"] != "unknown"]
        product_family = max(set(families), key=families.count) if families else "unknown"
        regime = get_regime(conn, product_family)
        alloc = market_allocation_active(conn, product_family)
        match = end_build_match_strength(conn, product_family, company.get("end_builds") or [])

        scored_inputs = [
            ScoredSignalInput(
                signal_type=s["signal_type"],
                confidence=float(s["confidence"]),
                age_days=_age_days(s["event_at"], as_of),
                event_key=s["event_key"],
            )
            for s in sigs
        ]
        # Spot: only broker quotes / licensed rows for this broker
        spot = _latest_allowed_spot(conn, product_family, broker_id=broker_id)
        ctx = LeadContext(
            company_key=company_key,
            side=side,
            product_family=product_family,
            regime=regime,
            company=company,
            signals=scored_inputs,
            market_allocation_active=alloc,
            end_build_match=match,
            stocks_short_family=(
                True
                if side == "supply" and product_family in (company.get("families_traded") or [])
                else None
            ),
            spot_change_4w=spot.get("change_4w") if spot else None,
            spot_high=spot.get("high") if spot else None,
            spot_low=spot.get("low") if spot else None,
            spot_average=spot.get("average") if spot else None,
            spot_source_note=spot.get("note") if spot else None,
        )
        outcome = score_lead(ctx)
        write_snapshot(
            conn,
            outcome,
            broker_id=broker_id,
            company_key=company_key,
            side=side,
            signal_ids=[s["id"] for s in sigs],
        )
        if outcome.gated:
            stats["gated"] += 1
        else:
            stats["scored"] += 1
            tier = outcome.lead_score.tier if outcome.lead_score else "D"
            stats[f"tier_{tier}"] = stats.get(f"tier_{tier}", 0) + 1

    return stats


def _latest_allowed_spot(
    conn: sqlite3.Connection,
    product_family: str,
    *,
    broker_id: str,
) -> Optional[dict[str, Any]]:
    """Customer-facing spot: broker_quote for this broker, or licence_status=licensed.

    internal_only readings (e.g. unlicensed DRAMeXchange scrape) are ignored.
    """
    row = conn.execute(
        """
        SELECT * FROM spot_prices
        WHERE product_family = ?
          AND (
            licence_status = 'licensed'
            OR (licence_status = 'broker_quote' AND broker_id = ?)
          )
        ORDER BY as_of DESC
        LIMIT 1
        """,
        (product_family, broker_id),
    ).fetchone()
    if not row:
        return None
    note = None
    if row["high"] is not None and row["low"] is not None and row["average"]:
        spread = (row["high"] - row["low"]) / row["average"]
        note = (
            f"{product_family} trading ${row['low']:.2f}-${row['high']:.2f} "
            f"({spread:.0%} spread; {row['source']})"
        )
    return {
        "high": row["high"],
        "low": row["low"],
        "average": row["average"],
        "change_4w": None,  # computed from history when enough rows exist
        "note": note,
    }
