"""v1 scoring formula: Signal strength + Fit + Timing (section 4)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional, Sequence

from dram_signals.scoring.catalog import (
    BUYER_TYPE_REGIME_POINTS,
    REGIME_MULTIPLIER,
    SIGNAL_TYPES,
    confidence_band,
    tier_for_score,
)


@dataclass
class ScoredSignalInput:
    signal_type: str
    confidence: float
    age_days: float
    event_key: str
    half_life_override: Optional[float] = None  # e.g. D1 post-LTB: no decay


@dataclass
class SignalStrengthResult:
    score: float
    raw: float
    regime_multiplier: float
    values: list[float]
    details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FitInput:
    end_build_match: str  # core | adjacent | none
    buyer_type: str
    regime: str
    ability_points: float  # precomputed 0-6
    product_family_match: str  # trades | unknown | no
    relationship: str  # past_transaction | active_contact | none


@dataclass
class FitResult:
    score: float
    components: dict[str, float]


@dataclass
class TimingInput:
    deadline_points: float  # 0-10 from LTB / quarter-end rules
    market_movement_points: float  # 0-10 (highest of spread / 3d / 4w)
    market_allocation_active: bool = False
    side: str = "demand"


@dataclass
class TimingResult:
    score: float
    deadline: float
    market: float
    allocation_boost: float


@dataclass
class LeadScore:
    signal_strength: float
    fit: float
    timing: float
    total: float
    tier: str
    components: dict[str, Any] = field(default_factory=dict)


def signal_value(
    signal_type: str,
    confidence: float,
    age_days: float,
    *,
    half_life_override: Optional[float] = None,
) -> Optional[float]:
    """value_i = base × band × 0.5^(age/half_life). None if below review threshold."""
    typedef = SIGNAL_TYPES.get(signal_type)
    if not typedef or typedef.side == "market":
        return None
    band = confidence_band(confidence)
    if band is None:
        return None
    half = half_life_override if half_life_override is not None else typedef.half_life_days
    if half is None or half <= 0:
        decay = 1.0
    else:
        decay = 0.5 ** (age_days / half)
    value = typedef.base * band * decay
    # Drop once below 10% of base (~3.3 half-lives)
    if value < typedef.base * 0.1:
        return None
    return value


def combine_signal_values(values: Sequence[float]) -> float:
    """raw = top + 0.5×second + 0.25×third (extras ignored)."""
    ranked = sorted((v for v in values if v is not None), reverse=True)
    if not ranked:
        return 0.0
    raw = ranked[0]
    if len(ranked) > 1:
        raw += 0.5 * ranked[1]
    if len(ranked) > 2:
        raw += 0.25 * ranked[2]
    return raw


def score_signal_strength(
    signals: Sequence[ScoredSignalInput],
    *,
    side: str,
    regime: str,
) -> SignalStrengthResult:
    # Deduplicate by event_key keeping highest value
    best_by_event: dict[str, tuple[float, ScoredSignalInput]] = {}
    details: list[dict[str, Any]] = []
    for sig in signals:
        val = signal_value(
            sig.signal_type,
            sig.confidence,
            sig.age_days,
            half_life_override=sig.half_life_override,
        )
        if val is None:
            continue
        prev = best_by_event.get(sig.event_key)
        if prev is None or val > prev[0]:
            best_by_event[sig.event_key] = (val, sig)
            details.append(
                {
                    "signal_type": sig.signal_type,
                    "event_key": sig.event_key,
                    "value": round(val, 4),
                    "age_days": sig.age_days,
                    "confidence": sig.confidence,
                }
            )

    values = [v for v, _ in best_by_event.values()]
    # Keep details only for the winning events
    winning_keys = set(best_by_event)
    details = [d for d in details if d["event_key"] in winning_keys]
    # Deduplicate details to one per event (last write wins from loop — rebuild cleanly)
    details = [
        {
            "signal_type": sig.signal_type,
            "event_key": ek,
            "value": round(val, 4),
            "age_days": sig.age_days,
            "confidence": sig.confidence,
        }
        for ek, (val, sig) in best_by_event.items()
    ]
    details.sort(key=lambda d: d["value"], reverse=True)

    raw = combine_signal_values(values)
    mult = REGIME_MULTIPLIER.get((regime, side), 1.0)
    score = min(45.0, raw) * mult
    return SignalStrengthResult(
        score=score,
        raw=raw,
        regime_multiplier=mult,
        values=sorted(values, reverse=True),
        details=details,
    )


def score_fit(inp: FitInput) -> FitResult:
    end_pts = {"core": 10.0, "adjacent": 5.0, "none": 0.0}.get(inp.end_build_match, 0.0)
    buyer_table = BUYER_TYPE_REGIME_POINTS.get(inp.buyer_type, {})
    buyer_pts = float(buyer_table.get(inp.regime, 0))
    ability = max(0.0, min(6.0, inp.ability_points))
    fam_pts = {"trades": 5.0, "unknown": 2.0, "no": 0.0}.get(inp.product_family_match, 2.0)
    rel_pts = {
        "past_transaction": 6.0,
        "active_contact": 3.0,
        "none": 0.0,
    }.get(inp.relationship, 0.0)
    components = {
        "end_build_match": end_pts,
        "buyer_type_regime": buyer_pts,
        "ability": ability,
        "product_family_match": fam_pts,
        "relationship": rel_pts,
    }
    total = min(35.0, sum(components.values()))
    return FitResult(score=total, components=components)


def score_timing(inp: TimingInput) -> TimingResult:
    deadline = max(0.0, min(10.0, inp.deadline_points))
    market = max(0.0, min(10.0, inp.market_movement_points))
    boost = 0.0
    if inp.side == "demand" and inp.market_allocation_active:
        market_with_boost = min(10.0, market + 5.0)
        boost = market_with_boost - market
        market = market_with_boost
    total = min(20.0, deadline + market)
    return TimingResult(
        score=total,
        deadline=deadline,
        market=market,
        allocation_boost=boost,
    )


def combine_lead(
    strength: SignalStrengthResult,
    fit: FitResult,
    timing: TimingResult,
) -> LeadScore:
    total = strength.score + fit.score + timing.score
    return LeadScore(
        signal_strength=round(strength.score, 4),
        fit=round(fit.score, 4),
        timing=round(timing.score, 4),
        total=round(total, 4),
        tier=tier_for_score(total),
        components={
            "strength": {
                "raw": round(strength.raw, 4),
                "regime_multiplier": strength.regime_multiplier,
                "signals": strength.details,
            },
            "fit": fit.components,
            "timing": {
                "deadline": timing.deadline,
                "market": timing.market,
                "allocation_boost": timing.allocation_boost,
            },
        },
    )


# --- Timing helpers ---

def ltb_deadline_points(*, days_since_ltb: Optional[float] = None, days_until_ltb: Optional[float] = None) -> float:
    """EOL-linked deadline points (section 4.4)."""
    if days_since_ltb is not None:
        if 0 <= days_since_ltb <= 180:
            return 10.0
        return 0.0
    if days_until_ltb is not None:
        if days_until_ltb < 0:
            return 0.0
        if days_until_ltb <= 90:
            return 9.0
        if days_until_ltb <= 180:
            return 6.0
        return 3.0
    return 0.0


def quarter_end_deadline_points(days_until_quarter_end: float) -> float:
    if 0 <= days_until_quarter_end <= 30:
        return 6.0
    return 0.0


def spot_spread_points(high: float, low: float, average: float) -> float:
    if average <= 0:
        return 0.0
    spread = (high - low) / average
    if spread >= 0.50:
        return 10.0
    if spread >= 0.25:
        return 6.0
    if spread >= 0.10:
        return 3.0
    return 1.0


def spot_change_points(pct_abs: float, *, window: str) -> float:
    """Absolute % change → points. window in {3d, 4w}."""
    pct = abs(pct_abs)
    if window == "3d":
        if pct >= 0.20:
            return 10.0
        if pct >= 0.10:
            return 6.0
        return 0.0
    # 4w
    if pct >= 0.10:
        return 10.0
    if pct >= 0.05:
        return 6.0
    if pct >= 0.02:
        return 3.0
    return 1.0


def market_movement_points(
    *,
    spread_pts: float = 0.0,
    change_3d_pts: float = 0.0,
    change_4w_pts: float = 0.0,
) -> float:
    return max(spread_pts, change_3d_pts, change_4w_pts)


def ability_to_pay_points(
    *,
    side: str,
    gross_margin: Optional[float],
    costly_stopped_line: bool = False,
    stocks_short_family: Optional[bool] = None,
) -> float:
    if side == "supply":
        if stocks_short_family is True:
            return 6.0
        if stocks_short_family is False:
            return 0.0
        return 3.0
    # demand
    if gross_margin is None:
        pts = 3.0
    elif gross_margin >= 0.40:
        pts = 6.0
    elif gross_margin >= 0.25:
        pts = 4.0
    else:
        pts = 1.0
    if costly_stopped_line:
        pts = min(6.0, pts + 2.0)
    return pts


def parse_iso_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])
