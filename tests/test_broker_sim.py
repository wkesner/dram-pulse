"""Invariants for broker overlap, pricing, and trading fills."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from dram_signals.broker_sim.catalog import (
    Product,
    illustrative_market_price,
    load_products_from_seed,
    products_from_seed_dict,
)
from dram_signals.broker_sim.customers import build_customer_universe
from dram_signals.broker_sim.simulation import (
    BuyOffer,
    SellBid,
    _fill_buy,
    _fill_sell,
    _should_buy,
    _should_sell,
    format_summary,
    run_simulation,
)
from dram_signals.broker_sim.strategies import BROKER_SPECS, BrokerSpec
from dram_signals.broker_sim.simulation import BrokerState

REPO = Path(__file__).resolve().parents[1]
SEED_CATALOG = REPO / "data" / "seed_catalog.json"
COMPANIES = REPO / "config" / "companies.yaml"


def test_seed_catalog_loads_products() -> None:
    products = load_products_from_seed(SEED_CATALOG)
    assert len(products) >= 5
    skus = {p.sku for p in products}
    assert "MT40A1G8SA-062E:B" in skus
    assert all(p.base_market_price > 0 for p in products)


def test_market_price_grade_and_lifecycle_ordering() -> None:
    consumer = illustrative_market_price(
        technology="DDR4", density="8Gb", grade="consumer", lifecycle_status="active"
    )
    industrial = illustrative_market_price(
        technology="DDR4", density="8Gb", grade="industrial", lifecycle_status="active"
    )
    automotive = illustrative_market_price(
        technology="DDR4", density="8Gb", grade="automotive", lifecycle_status="active"
    )
    obsolete = illustrative_market_price(
        technology="DDR4", density="8Gb", grade="consumer", lifecycle_status="obsolete"
    )
    assert industrial > consumer
    assert automotive > industrial
    assert obsolete < consumer


def test_customer_overlap_is_partial_not_disjoint() -> None:
    rng = random.Random(42)
    book = build_customer_universe(
        ["apex", "harbor", "summit"],
        rng=rng,
        companies_path=COMPANIES,
    )
    stats = book.overlap_stats()
    assert stats["customers"] >= 10
    assert stats["on_two_brokers"] + stats["on_three_brokers"] > 0
    assert stats["exclusive"] > 0
    # Not a single shared book and not three disjoint books.
    assert stats["overlap_rate"] > 0.05
    assert stats["overlap_rate"] < 0.95
    apex = book.customers_for("apex")
    harbor = book.customers_for("harbor")
    summit = book.customers_for("summit")
    assert apex and harbor and summit
    assert apex != harbor
    assert len(apex & harbor) >= 1 or len(apex & summit) >= 1


def test_customer_assignment_is_seed_reproducible() -> None:
    a = build_customer_universe(
        ["apex", "harbor", "summit"],
        rng=random.Random(99),
        companies_path=COMPANIES,
    )
    b = build_customer_universe(
        ["apex", "harbor", "summit"],
        rng=random.Random(99),
        companies_path=COMPANIES,
    )
    assert a.membership == b.membership


def test_buy_requires_under_market() -> None:
    spec = BROKER_SPECS[0]
    state = BrokerState(spec=spec, cash=100_000)
    product = Product(
        sku="X",
        manufacturer="micron",
        family_code="X",
        technology="DDR4",
        density="8Gb",
        grade="consumer",
        lifecycle_status="active",
        base_market_price=4.0,
    )
    bad = BuyOffer(sku="X", qty=100, offer_price=4.0, market_price=4.0)
    assert _should_buy(spec, bad, product, state) is False
    assert _fill_buy(state, bad) is False
    assert state.units_bought == 0
    assert state.cash == 100_000

    good = BuyOffer(sku="X", qty=100, offer_price=3.5, market_price=4.0)
    # Apex min discount is 3%; 12.5% clears it.
    assert _should_buy(spec, good, product, state) is True
    assert _fill_buy(state, good) is True
    assert state.units_bought == 100
    assert state.cash == pytest.approx(100_000 - 350)
    assert state.units_on_hand("X") == 100


def test_sell_requires_at_or_above_market() -> None:
    spec = BROKER_SPECS[1]  # Harbor: min_sell_premium = 0
    state = BrokerState(spec=spec, cash=50_000)
    # Seed inventory
    from dram_signals.broker_sim.simulation import Lot

    state.inventory["X"] = [Lot(qty=200, unit_cost=3.0)]
    under = SellBid(
        sku="X", qty=50, bid_price=3.9, market_price=4.0, customer="Dell"
    )
    assert _should_sell(spec, under, state) == 0
    assert _fill_sell(state, under, 50) is False
    assert state.units_sold == 0

    ok = SellBid(
        sku="X", qty=50, bid_price=4.0, market_price=4.0, customer="Dell"
    )
    qty = _should_sell(spec, ok, state)
    assert qty == 50
    assert _fill_sell(state, ok, qty) is True
    assert state.units_sold == 50
    assert state.realized_pnl == pytest.approx(50 * (4.0 - 3.0))
    assert state.units_on_hand("X") == 150


def test_sell_cannot_exceed_inventory() -> None:
    spec = BROKER_SPECS[2]
    state = BrokerState(spec=spec, cash=10_000)
    from dram_signals.broker_sim.simulation import Lot

    state.inventory["Y"] = [Lot(qty=40, unit_cost=2.5)]
    bid = SellBid(
        sku="Y", qty=100, bid_price=5.0, market_price=4.5, customer="Apple"
    )
    qty = _should_sell(spec, bid, state)
    assert qty == 40
    assert _fill_sell(state, bid, qty) is True
    assert state.units_on_hand("Y") == 0
    assert state.cash == pytest.approx(10_000 + 40 * 5.0)


def test_harbor_rejects_shallow_discount() -> None:
    harbor = next(s for s in BROKER_SPECS if s.broker_id == "harbor")
    state = BrokerState(spec=harbor, cash=100_000)
    product = Product(
        sku="Z",
        manufacturer="samsung",
        family_code="Z",
        technology="DDR4",
        density="8Gb",
        grade="consumer",
        lifecycle_status="active",
        base_market_price=4.0,
    )
    shallow = BuyOffer(sku="Z", qty=100, offer_price=3.80, market_price=4.0)  # 5%
    assert _should_buy(harbor, shallow, product, state) is False
    deep = BuyOffer(sku="Z", qty=100, offer_price=3.40, market_price=4.0)  # 15%
    assert _should_buy(harbor, deep, product, state) is True


def test_simulation_reproducible_and_cash_nonnegative() -> None:
    a = run_simulation(days=15, seed=42, starting_cash=100_000)
    b = run_simulation(days=15, seed=42, starting_cash=100_000)
    assert format_summary(a) == format_summary(b)
    for state in a.brokers.values():
        assert state.cash >= 0
        assert state.units_on_hand() >= 0
        # Buys under market / sells at or above are structural; equity can move.
        assert state.units_bought >= 0
        assert state.units_sold >= 0
        assert state.units_sold <= state.units_bought  # no naked shorts


def test_simulation_has_three_named_brokers() -> None:
    result = run_simulation(days=5, seed=1)
    assert set(result.brokers) == {"apex", "harbor", "summit"}
    names = {s.spec.name for s in result.brokers.values()}
    assert "Apex Component Brokers" in names
    assert "Harbor Electronics Trading" in names
    assert "Summit Memory Partners" in names
    summary = result.summary()
    assert summary["winner"]
    assert summary["overlap"]["customers"] > 0


def test_products_from_minimal_seed() -> None:
    payload = {
        "families": [
            {
                "manufacturer": "micron",
                "family_code": "MTTEST",
                "technology": "DDR4",
                "density": "8Gb",
                "variants": [
                    {
                        "mpn": "MTTEST-1",
                        "grade": "consumer",
                        "lifecycle_status": "active",
                    }
                ],
            }
        ]
    }
    products = products_from_seed_dict(payload)
    assert len(products) == 1
    assert products[0].sku == "MTTEST-1"
    assert json.dumps(products[0].base_market_price)  # JSON-serializable float


def test_cli_main_runs(capsys) -> None:
    from dram_signals.broker_sim.cli import main

    assert main(["--days", "3", "--seed", "0"]) == 0
    out = capsys.readouterr().out
    assert "Apex Component Brokers" in out
    assert "Customer overlap" in out
