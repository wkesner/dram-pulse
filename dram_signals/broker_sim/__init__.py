"""Simulated electronic-component DRAM brokers competing for virtual P&L.

Three baseline brokers share a DRAM catalog and overlapping customers. Each
has a **scored twin** with the same personality and $250k start cash, but
faster access to a simple memory timing score (see ``memory_score``).

Run::

    python3 -m scripts.simulate_brokers --days 30 --seed 42
    python3 -m scripts.simulate_brokers --baseline-only --no-live
    # GUI (port 8010 — avoids clash with the feed on 8000):
    uvicorn app.server:app --host 0.0.0.0 --port 8010
    # open http://127.0.0.1:8010/brokers.html

Prices prefer MemoryIndex public board; see ``live_prices`` for sources/limits.
"""

from __future__ import annotations

from dram_signals.broker_sim.catalog import Product, load_products_from_seed
from dram_signals.broker_sim.customers import CustomerBook, build_customer_universe
from dram_signals.broker_sim.live_prices import PriceBundle, resolve_market_prices
from dram_signals.broker_sim.memory_score import generate_score_path, observe_score
from dram_signals.broker_sim.simulation import (
    BrokerState,
    SimulationResult,
    run_simulation,
)
from dram_signals.broker_sim.strategies import (
    BASELINE_SPECS,
    BROKER_SPECS,
    BrokerSpec,
    all_broker_specs,
    make_scored_twin,
)

__all__ = [
    "BASELINE_SPECS",
    "BROKER_SPECS",
    "BrokerSpec",
    "BrokerState",
    "CustomerBook",
    "PriceBundle",
    "Product",
    "SimulationResult",
    "all_broker_specs",
    "build_customer_universe",
    "generate_score_path",
    "load_products_from_seed",
    "make_scored_twin",
    "observe_score",
    "resolve_market_prices",
    "run_simulation",
]
