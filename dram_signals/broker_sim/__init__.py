"""Simulated electronic-component DRAM brokers competing for virtual P&L.

Three named brokers share a DRAM catalog and a partially overlapping customer
universe. Each day they see buy-side offers (under market) and sell-side demand
(at or above market) and trade according to distinct strategies with the same
profit objective: buy under market, sell at/above market.

Run::

    python3 -m scripts.simulate_brokers --days 30 --seed 42
    python3 -m dram_signals.broker_sim --days 30 --seed 42 --live
    # GUI (port 8010 — avoids clash with the feed on 8000):
    uvicorn app.server:app --reload --port 8010
    # open http://127.0.0.1:8010/brokers.html

Prices prefer MemoryIndex public board; see ``live_prices`` for sources/limits.
"""

from __future__ import annotations

from dram_signals.broker_sim.catalog import Product, load_products_from_seed
from dram_signals.broker_sim.customers import CustomerBook, build_customer_universe
from dram_signals.broker_sim.live_prices import PriceBundle, resolve_market_prices
from dram_signals.broker_sim.simulation import (
    BrokerState,
    SimulationResult,
    run_simulation,
)
from dram_signals.broker_sim.strategies import BROKER_SPECS, BrokerSpec

__all__ = [
    "BROKER_SPECS",
    "BrokerSpec",
    "BrokerState",
    "CustomerBook",
    "PriceBundle",
    "Product",
    "SimulationResult",
    "build_customer_universe",
    "load_products_from_seed",
    "resolve_market_prices",
    "run_simulation",
]
