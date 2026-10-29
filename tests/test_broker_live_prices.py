"""Live / cached MemoryIndex price resolution for broker sim."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dram_signals.broker_sim.catalog import load_products_from_seed
from dram_signals.broker_sim.live_prices import (
    apply_grade_lifecycle,
    match_quote_for_product,
    parse_memoryindex_payload,
    prices_from_quotes,
    resolve_market_prices,
)
from dram_signals.broker_sim.simulation import run_simulation

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "broker_sim" / "memoryindex_prices.json"
SEED = REPO / "data" / "seed_catalog.json"


@pytest.fixture()
def fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_memoryindex_fixture(fixture_payload) -> None:
    quotes, meta = parse_memoryindex_payload(fixture_payload)
    assert len(quotes) >= 5
    assert meta.get("attribution")
    assert any(q.contract_id == "ddr4-8g" for q in quotes)
    ddr4 = next(q for q in quotes if q.contract_id == "ddr4-8g")
    assert ddr4.spot_usd > 1


def test_match_ddr4_8gb_seed_skus(fixture_payload) -> None:
    quotes, _ = parse_memoryindex_payload(fixture_payload)
    products = load_products_from_seed(SEED)
    consumer = next(p for p in products if p.grade == "consumer")
    q = match_quote_for_product(consumer, quotes)
    assert q is not None
    assert "ddr4" in q.contract_id
    assert "8" in q.contract_id


def test_grade_multiplier_on_live_die(fixture_payload) -> None:
    quotes, meta = parse_memoryindex_payload(fixture_payload)
    products = load_products_from_seed(SEED)
    bundle = prices_from_quotes(products, quotes, meta=meta, source="fixture")
    assert bundle.matched_skus == len(products)
    assert bundle.source == "fixture"
    # Industrial should price above its family's consumer twin when same die mid.
    families: dict[str, list] = {}
    for p in products:
        families.setdefault(p.family_code, []).append(p)
    for _family, members in families.items():
        grades = {m.grade: m for m in members}
        if "consumer" in grades and "industrial" in grades:
            assert bundle.prices[grades["industrial"].sku] > bundle.prices[grades["consumer"].sku]
            die = match_quote_for_product(grades["consumer"], quotes).spot_usd
            assert bundle.prices[grades["consumer"].sku] == apply_grade_lifecycle(
                die, grade="consumer", lifecycle_status=grades["consumer"].lifecycle_status
            )


def test_resolve_uses_fixture_without_network(fixture_payload, tmp_path) -> None:
    products = load_products_from_seed(SEED)
    bundle = resolve_market_prices(
        products,
        prefer_live=True,
        cache_path=tmp_path / "cache.json",
        fixture_payload=fixture_payload,
    )
    assert bundle.source == "memoryindex"
    assert bundle.matched_skus >= 1
    assert all(v > 10 for v in bundle.prices.values())  # live mids >> illustrative ~$3


def test_resolve_falls_back_illustrative_when_offline(tmp_path, monkeypatch) -> None:
    products = load_products_from_seed(SEED)

    def boom(*_a, **_k):
        raise ConnectionError("offline")

    monkeypatch.setattr(
        "dram_signals.broker_sim.live_prices.fetch_memoryindex",
        boom,
    )
    bundle = resolve_market_prices(
        products,
        prefer_live=True,
        cache_path=tmp_path / "missing.json",
    )
    assert bundle.source == "illustrative"
    assert "unavailable" in bundle.detail


def test_simulation_with_fixture_prices(fixture_payload) -> None:
    products = load_products_from_seed(SEED)
    quotes, meta = parse_memoryindex_payload(fixture_payload)
    bundle = prices_from_quotes(products, quotes, meta=meta, source="fixture")
    result = run_simulation(
        days=5,
        seed=42,
        prefer_live_prices=False,
        price_bundle=bundle,
    )
    assert result.price_bundle is bundle
    summary = result.summary()
    assert summary["prices"]["matched_skus"] >= 1
    assert len(summary["history"]) == 5
