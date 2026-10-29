"""Named broker companies and their trading personalities.

All three share the same objective: buy under market, sell at/above market.
They differ in discount thresholds, sell patience, inventory appetite, and
customer-book affinity (used when building overlapping books).
"""

from __future__ import annotations

from dataclasses import dataclass


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


# Distinct fictional companies — not real brokers.
BROKER_SPECS: tuple[BrokerSpec, ...] = (
    BrokerSpec(
        broker_id="apex",
        name="Apex Component Brokers",
        personality=(
            "Hungry buyer: thin discounts OK, patient seller, holds inventory "
            "to wait for a premium bid."
        ),
        min_buy_discount=0.03,
        min_sell_premium=0.02,
        inventory_target_units=8_000,
        max_cash_fraction_per_buy=0.35,
        customer_affinity=1.0,
        prefer_active=False,
    ),
    BrokerSpec(
        broker_id="harbor",
        name="Harbor Electronics Trading",
        personality=(
            "Selective / cash-conservative: only deep discounts, flips inventory "
            "at any price at or above market."
        ),
        min_buy_discount=0.12,
        min_sell_premium=0.0,
        inventory_target_units=2_500,
        max_cash_fraction_per_buy=0.15,
        customer_affinity=0.7,
        prefer_active=True,
    ),
    BrokerSpec(
        broker_id="summit",
        name="Summit Memory Partners",
        personality=(
            "Volume / balanced: moderate discounts, sells near market, broadest "
            "customer coverage, prefers active lifecycle SKUs."
        ),
        min_buy_discount=0.06,
        min_sell_premium=0.005,
        inventory_target_units=5_000,
        max_cash_fraction_per_buy=0.25,
        customer_affinity=1.35,
        prefer_active=True,
    ),
)


def specs_by_id() -> dict[str, BrokerSpec]:
    return {s.broker_id: s for s in BROKER_SPECS}
