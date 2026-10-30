"""Named broker companies and their trading personalities.

Baseline trio (Apex / Harbor / Summit) share the objective: buy under market,
sell at/above market. Each has a **scored twin** with the same personality and
$250k start cash, but with fast memory-score access (see ``memory_score``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class BrokerSpec:
    """Static personality for one simulated broker company."""

    broker_id: str
    name: str
    personality: str
    # Buy if offer_price <= market * (1 - min_buy_discount).
    min_buy_discount: float
    # Sell if bid_price >= market * (1 + min_sell_premium).
    min_sell_premium: float
    # Soft inventory cap in units across all SKUs.
    inventory_target_units: int
    # Max fraction of cash to spend on a single buy fill.
    max_cash_fraction_per_buy: float
    # Relative weight when assigning overlapping customers.
    customer_affinity: float
    # Prefer active-lifecycle SKUs when True (skip nrnd/obsolete unless deep).
    prefer_active: bool
    # Memory-score access (scored twins only).
    has_memory_score: bool = False
    # Days of lag on the latent score (1 = see yesterday's true score).
    score_lag_days: int = 1
    # "baseline" or "scored".
    cohort: str = "baseline"
    # For scored twins: id of the unscored original.
    parent_id: Optional[str] = None


# Distinct fictional companies — not real brokers. Unscored baselines.
BASELINE_SPECS: tuple[BrokerSpec, ...] = (
    BrokerSpec(
        broker_id="apex",
        name="Apex Component Brokers",
        personality=(
            "Hungry buyer: thin discounts OK, patient seller, holds inventory "
            "to wait for a premium bid. No memory-score feed."
        ),
        min_buy_discount=0.03,
        min_sell_premium=0.02,
        inventory_target_units=8_000,
        max_cash_fraction_per_buy=0.35,
        customer_affinity=1.0,
        prefer_active=False,
        has_memory_score=False,
        cohort="baseline",
    ),
    BrokerSpec(
        broker_id="harbor",
        name="Harbor Electronics Trading",
        personality=(
            "Selective / cash-conservative: only deep discounts, flips inventory "
            "at any price at or above market. No memory-score feed."
        ),
        min_buy_discount=0.12,
        min_sell_premium=0.0,
        inventory_target_units=2_500,
        max_cash_fraction_per_buy=0.15,
        customer_affinity=0.7,
        prefer_active=True,
        has_memory_score=False,
        cohort="baseline",
    ),
    BrokerSpec(
        broker_id="summit",
        name="Summit Memory Partners",
        personality=(
            "Volume / balanced: moderate discounts, sells near market, broadest "
            "customer coverage, prefers active lifecycle SKUs. No memory-score feed."
        ),
        min_buy_discount=0.06,
        min_sell_premium=0.005,
        inventory_target_units=5_000,
        max_cash_fraction_per_buy=0.25,
        customer_affinity=1.35,
        prefer_active=True,
        has_memory_score=False,
        cohort="baseline",
    ),
)

# Back-compat alias used throughout the package.
BROKER_SPECS: tuple[BrokerSpec, ...] = BASELINE_SPECS

DEFAULT_SCORED_LAG_DAYS = 1


def make_scored_twin(
    base: BrokerSpec,
    *,
    score_lag_days: int = DEFAULT_SCORED_LAG_DAYS,
) -> BrokerSpec:
    """Clone a baseline broker with fast memory-score access."""
    if base.cohort != "baseline" or base.parent_id is not None:
        raise ValueError(f"Expected a baseline spec, got {base.broker_id}")
    return replace(
        base,
        broker_id=f"{base.broker_id}_scored",
        name=f"{base.name} (scored)",
        personality=(
            f"{base.personality} Twin with memory-score access "
            f"(lag={score_lag_days} day(s))."
        ),
        has_memory_score=True,
        score_lag_days=score_lag_days,
        cohort="scored",
        parent_id=base.broker_id,
    )


def scored_twin_specs(
    *,
    score_lag_days: int = DEFAULT_SCORED_LAG_DAYS,
    baselines: tuple[BrokerSpec, ...] = BASELINE_SPECS,
) -> tuple[BrokerSpec, ...]:
    return tuple(make_scored_twin(b, score_lag_days=score_lag_days) for b in baselines)


def all_broker_specs(
    *,
    include_scored: bool = True,
    score_lag_days: int = DEFAULT_SCORED_LAG_DAYS,
) -> tuple[BrokerSpec, ...]:
    if not include_scored:
        return BASELINE_SPECS
    return BASELINE_SPECS + scored_twin_specs(score_lag_days=score_lag_days)


def specs_by_id(
    specs: tuple[BrokerSpec, ...] | None = None,
) -> dict[str, BrokerSpec]:
    return {s.broker_id: s for s in (specs or all_broker_specs())}
