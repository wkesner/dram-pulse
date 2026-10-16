"""Persist score snapshots at routing time (section 5.1)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from dram_signals.scoring.catalog import RUBRIC_VERSION
from dram_signals.scoring.scorer import ScoreOutcome


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_snapshot(
    conn: sqlite3.Connection,
    outcome: ScoreOutcome,
    *,
    broker_id: str,
    company_key: str,
    side: str,
    routed_to: Optional[str] = None,
    exploration: bool = False,
    signal_ids: Optional[list[Any]] = None,
) -> str:
    now = _now()
    if outcome.gated or outcome.lead_score is None:
        conn.execute(
            """
            INSERT INTO score_snapshots (
                snapshot_id, broker_id, company_key, side, product_family,
                score, signal_strength, fit, timing, components_json,
                signal_ids_json, regime, rubric_version, tier, exploration,
                gated, gate_reason, reasons_text, routed_to, routed_at
            ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?, ?, ?, 'D', ?, 1, ?, ?, ?, ?)
            """,
            (
                outcome.snapshot_id,
                broker_id,
                company_key,
                side,
                outcome.product_family,
                json.dumps({"gate": outcome.gate.reason if outcome.gate else None}),
                json.dumps(signal_ids or []),
                outcome.regime,
                RUBRIC_VERSION,
                1 if exploration else 0,
                outcome.gate.reason if outcome.gate else None,
                outcome.reasons,
                routed_to,
                now,
            ),
        )
        return outcome.snapshot_id

    ls = outcome.lead_score
    conn.execute(
        """
        INSERT INTO score_snapshots (
            snapshot_id, broker_id, company_key, side, product_family,
            score, signal_strength, fit, timing, components_json,
            signal_ids_json, regime, rubric_version, tier, exploration,
            gated, gate_reason, reasons_text, routed_to, routed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?)
        """,
        (
            outcome.snapshot_id,
            broker_id,
            company_key,
            side,
            outcome.product_family,
            ls.total,
            ls.signal_strength,
            ls.fit,
            ls.timing,
            json.dumps(ls.components, sort_keys=True),
            json.dumps(signal_ids or []),
            outcome.regime,
            RUBRIC_VERSION,
            ls.tier,
            1 if exploration else 0,
            outcome.reasons,
            routed_to,
            now,
        ),
    )
    return outcome.snapshot_id
