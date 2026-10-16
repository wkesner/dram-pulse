"""Lead scorer: gates → S + F + T → tier (section 4)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional, Sequence

from dram_signals.scoring.catalog import RUBRIC_VERSION, confidence_band
from dram_signals.scoring.formula import (
    FitInput,
    ScoredSignalInput,
    TimingInput,
    ability_to_pay_points,
    combine_lead,
    ltb_deadline_points,
    market_movement_points,
    quarter_end_deadline_points,
    score_fit,
    score_signal_strength,
    score_timing,
    spot_change_points,
    spot_spread_points,
)
from dram_signals.scoring.gates import GateResult, check_gates
from dram_signals.scoring.reasons import build_reasons


@dataclass
class LeadContext:
    company_key: str
    side: str
    product_family: str
    regime: str
    company: dict[str, Any]
    signals: Sequence[ScoredSignalInput]
    market_allocation_active: bool = False
    days_until_quarter_end: Optional[float] = None
    days_since_ltb: Optional[float] = None
    days_until_ltb: Optional[float] = None
    # Spot readings allowed for customer scores (licensed or broker_quote only)
    spot_high: Optional[float] = None
    spot_low: Optional[float] = None
    spot_average: Optional[float] = None
    spot_change_3d: Optional[float] = None  # fractional, e.g. 0.08
    spot_change_4w: Optional[float] = None
    spot_source_note: Optional[str] = None
    end_build_match: str = "none"  # core | adjacent | none
    costly_stopped_line: bool = False
    stocks_short_family: Optional[bool] = None


@dataclass
class ScoreOutcome:
    gated: bool
    gate: Optional[GateResult]
    lead_score: Optional[Any]
    reasons: str
    snapshot_id: str
    product_family: str
    regime: str


def score_lead(ctx: LeadContext) -> ScoreOutcome:
    snapshot_id = str(uuid.uuid4())
    # Confidence gate: at least one signal ≥ 0.6
    signals_ok = any(confidence_band(s.confidence) is not None for s in ctx.signals)
    entity_resolved = bool(ctx.company_key) and ctx.company_key != "unknown"
    gate = check_gates(
        ctx.company,
        signals_ok=signals_ok,
        entity_resolved=entity_resolved,
    )
    if gate.blocked:
        return ScoreOutcome(
            gated=True,
            gate=gate,
            lead_score=None,
            reasons=f"gated: {gate.reason}",
            snapshot_id=snapshot_id,
            product_family=ctx.product_family,
            regime=ctx.regime,
        )

    strength = score_signal_strength(ctx.signals, side=ctx.side, regime=ctx.regime)

    company = ctx.company
    ability = ability_to_pay_points(
        side=ctx.side,
        gross_margin=company.get("gross_margin"),
        costly_stopped_line=ctx.costly_stopped_line,
        stocks_short_family=ctx.stocks_short_family,
    )
    families = company.get("families_traded") or []
    if ctx.product_family in families:
        fam_match = "trades"
    elif not families:
        fam_match = "unknown"
    else:
        fam_match = "no"

    fit = score_fit(
        FitInput(
            end_build_match=ctx.end_build_match,
            buyer_type=company.get("buyer_type") or "mid_oem_ems",
            regime=ctx.regime,
            ability_points=ability,
            product_family_match=fam_match,
            relationship=company.get("relationship") or "none",
        )
    )

    deadline_pts = 0.0
    discovery_first = False
    if ctx.days_since_ltb is not None:
        deadline_pts = max(deadline_pts, ltb_deadline_points(days_since_ltb=ctx.days_since_ltb))
        if 0 <= ctx.days_since_ltb <= 180:
            discovery_first = True
    if ctx.days_until_ltb is not None:
        deadline_pts = max(deadline_pts, ltb_deadline_points(days_until_ltb=ctx.days_until_ltb))
    if ctx.side == "supply" and ctx.days_until_quarter_end is not None:
        deadline_pts = max(
            deadline_pts,
            quarter_end_deadline_points(ctx.days_until_quarter_end),
        )

    # Market movement: only from allowed readings (caller enforces licence)
    spread_pts = change_3d = change_4w = 0.0
    has_spot = False
    if (
        ctx.spot_high is not None
        and ctx.spot_low is not None
        and ctx.spot_average is not None
        and ctx.spot_average > 0
    ):
        spread_pts = spot_spread_points(ctx.spot_high, ctx.spot_low, ctx.spot_average)
        has_spot = True
    if ctx.spot_change_3d is not None:
        change_3d = spot_change_points(ctx.spot_change_3d, window="3d")
        has_spot = True
    if ctx.spot_change_4w is not None:
        change_4w = spot_change_points(ctx.spot_change_4w, window="4w")
        has_spot = True
    market_pts = market_movement_points(
        spread_pts=spread_pts,
        change_3d_pts=change_3d,
        change_4w_pts=change_4w,
    ) if has_spot else 0.0

    timing = score_timing(
        TimingInput(
            deadline_points=deadline_pts,
            market_movement_points=market_pts,
            market_allocation_active=ctx.market_allocation_active,
            side=ctx.side,
        )
    )

    lead = combine_lead(strength, fit, timing)
    reasons = build_reasons(
        strength_details=strength.details,
        timing=lead.components["timing"],
        product_family=ctx.product_family,
        spot_note=ctx.spot_source_note,
        discovery_first=discovery_first,
    )
    return ScoreOutcome(
        gated=False,
        gate=None,
        lead_score=lead,
        reasons=reasons,
        snapshot_id=snapshot_id,
        product_family=ctx.product_family,
        regime=ctx.regime,
    )


def score_worked_example_1() -> ScoreOutcome:
    """Section 4.6 example 1 — supply EMS, expected ~67.6 A."""
    company = {
        "buyer_type": "mid_oem_ems",
        "gross_margin": None,
        "families_traded": ["DDR4"],
        "relationship": "none",
        "end_builds": ["industrial", "networking_telecom"],
        "gate_flags": {},
    }
    ctx = LeadContext(
        company_key="example-ems",
        side="supply",
        product_family="DDR4",
        regime="shortage",
        company=company,
        signals=[
            ScoredSignalInput("S1", 0.9, 10.0, "evt-s1"),
            ScoredSignalInput("S6", 0.8, 5.0, "evt-s6"),
        ],
        days_until_quarter_end=20.0,
        spot_change_4w=0.01,  # <2%
        end_build_match="core",
        stocks_short_family=True,
        spot_source_note="DDR4 spot <2% in 4 weeks",
    )
    return score_lead(ctx)


def score_worked_example_2(*, market_allocation: bool = False) -> ScoreOutcome:
    """Section 4.6 example 2 — demand small OEM, D without alloc / C with."""
    company = {
        "buyer_type": "small",
        "gross_margin": None,
        "families_traded": ["DDR4"],
        "relationship": "none",
        "end_builds": ["industrial"],
        "gate_flags": {},
    }
    ctx = LeadContext(
        company_key="example-industrial-oem",
        side="demand",
        product_family="DDR4",
        regime="shortage",
        company=company,
        signals=[ScoredSignalInput("D3", 0.85, 7.0, "evt-d3")],
        market_allocation_active=market_allocation,
        spot_change_4w=0.01,
        end_build_match="adjacent",
        spot_source_note="DDR4 spot <2% in 4 weeks",
    )
    return score_lead(ctx)


def score_worked_example_3() -> ScoreOutcome:
    """Section 4.6 example 3 — demand mid OEM post-LTB, ~70.8 A."""
    company = {
        "buyer_type": "mid_oem_ems",
        "gross_margin": 0.30,
        "families_traded": ["DDR4"],
        "relationship": "active_contact",
        "end_builds": ["current_servers", "legacy_servers"],
        "gate_flags": {},
    }
    ctx = LeadContext(
        company_key="example-server-oem",
        side="demand",
        product_family="DDR4",
        regime="shortage",
        company=company,
        signals=[
            ScoredSignalInput("D1", 1.0, 30.0, "evt-d1", half_life_override=None),
        ],
        days_since_ltb=30.0,
        spot_change_4w=0.01,
        end_build_match="core",
        spot_source_note="DDR4 spot <2% in 4 weeks",
    )
    return score_lead(ctx)
