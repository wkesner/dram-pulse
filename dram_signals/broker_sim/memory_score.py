"""Simple memory timing score for scored broker twins.

Purpose
-------
Simulate how **faster access to memory-market information** changes trading
outcomes. Baseline brokers (Apex / Harbor / Summit) trade on strategy alone.
Each has a twin with the same personality and $250k start cash, but they can
read a simple 0–100 **memory score** with low lag.

Score model (intentionally small)
---------------------------------
Not a full lead scorer. Inspired by ``dram_signals.scoring`` regimes and
signal sides (supply vs demand heat):

* Each day a latent ``true_score`` ∈ [0, 100] follows a seeded mean-reverting
  walk, with occasional bumps from synthetic S* (supply/glut) and D*
  (demand/shortage) events — codes borrowed from the rubric catalog.
* **≥ 60** → shortage lean (prices tend to drift up).
* **≤ 40** → glut lean (prices tend to drift down).
* **41–59** → balanced / no strong tilt.

Observation lag
---------------
* Baseline: no score (``observed = None``).
* Scored twin: sees ``true_score`` from ``score_lag_days`` ago (default **1**).
  So they act on yesterday's reading while the market already embeds today's
  latent pressure — still faster than having no score at all.

Trading use
-----------
Observed score nudges the twin's buy-discount / sell-premium thresholds
(and a soft inventory headroom) so they buy earlier into shortage and sell
faster into glut. Market mid drift is weakly biased by the *true* score so
the information is actually valuable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

# Rubric-inspired event bumps (not full S+F+T scoring).
_DEMAND_EVENTS = (
    ("D0", 8.0),   # allocation
    ("D2", 6.0),   # shortage hurting shipments
    ("D3", 4.0),   # memory buyer hiring
    ("D5", 3.0),   # ramp / expansion
)
_SUPPLY_EVENTS = (
    ("S1", -7.0),  # inventory write-down
    ("S4", -5.0),  # guidance cut / correction
    ("S6", -4.0),  # production cut / glut
    ("S5", -3.0),  # excess inventory jobs
)


@dataclass(frozen=True)
class ScoreReading:
    """One day's latent score plus optional event note."""

    day: int
    true_score: float
    event: Optional[str] = None  # e.g. "D2" / "S1"


@dataclass(frozen=True)
class ThresholdNudge:
    """Effective thresholds after applying an observed memory score."""

    min_buy_discount: float
    min_sell_premium: float
    inventory_headroom: float  # multiplier on inventory_target_units


def generate_score_path(
    days: int,
    rng: random.Random,
    *,
    start: float = 50.0,
    reversion: float = 0.12,
    vol: float = 4.5,
    event_prob: float = 0.18,
) -> list[ScoreReading]:
    """Seeded latent memory-score path for ``days`` simulation days (1..days)."""
    if days < 1:
        return []
    score = float(start)
    out: list[ScoreReading] = []
    for day in range(1, days + 1):
        event: Optional[str] = None
        bump = 0.0
        if rng.random() < event_prob:
            if rng.random() < 0.55:
                event, bump = rng.choice(_DEMAND_EVENTS)
            else:
                event, bump = rng.choice(_SUPPLY_EVENTS)
        shock = rng.gauss(0.0, vol)
        score = score + reversion * (50.0 - score) + shock + bump
        score = max(0.0, min(100.0, score))
        out.append(ScoreReading(day=day, true_score=round(score, 2), event=event))
    return out


def observe_score(
    path: list[ScoreReading],
    *,
    day: int,
    has_memory_score: bool,
    score_lag_days: int,
) -> Optional[float]:
    """Return the score a broker sees on ``day`` (1-indexed), or None."""
    if not has_memory_score:
        return None
    lag = max(0, int(score_lag_days))
    target = day - lag
    if target < 1:
        return None
    for reading in path:
        if reading.day == target:
            return reading.true_score
    return None


def true_score_on(path: list[ScoreReading], day: int) -> float:
    for reading in path:
        if reading.day == day:
            return reading.true_score
    return 50.0


def drift_bias_from_score(true_score: float, *, scale: float = 0.006) -> float:
    """Additive daily drift bias from latent score (shortage → positive)."""
    return ((true_score - 50.0) / 50.0) * scale


def nudge_from_score(
    *,
    min_buy_discount: float,
    min_sell_premium: float,
    observed: Optional[float],
) -> ThresholdNudge:
    """Map an observed 0–100 score into threshold nudges.

    Shortage (≥60): buy more eagerly (lower discount bar), hold for premium,
    allow more inventory. Glut (≤40): opposite. Mid band: unchanged.
    """
    if observed is None:
        return ThresholdNudge(min_buy_discount, min_sell_premium, 1.0)
    if observed >= 60:
        # Strength scales gently above 60.
        strength = min(1.0, (observed - 60.0) / 25.0)
        return ThresholdNudge(
            min_buy_discount=max(0.01, min_buy_discount - 0.04 * strength),
            min_sell_premium=min_sell_premium + 0.025 * strength,
            inventory_headroom=1.0 + 0.35 * strength,
        )
    if observed <= 40:
        strength = min(1.0, (40.0 - observed) / 25.0)
        return ThresholdNudge(
            min_buy_discount=min(0.25, min_buy_discount + 0.04 * strength),
            min_sell_premium=max(0.0, min_sell_premium - 0.02 * strength),
            inventory_headroom=max(0.55, 1.0 - 0.35 * strength),
        )
    return ThresholdNudge(min_buy_discount, min_sell_premium, 1.0)


def regime_label(score: float) -> str:
    if score >= 60:
        return "shortage"
    if score <= 40:
        return "glut"
    return "balanced"
