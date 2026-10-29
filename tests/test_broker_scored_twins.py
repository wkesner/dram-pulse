"""Scored twins: same personality, faster memory-score access."""

from __future__ import annotations

import random

from dram_signals.broker_sim.memory_score import (
    generate_score_path,
    nudge_from_score,
    observe_score,
)
from dram_signals.broker_sim.simulation import run_simulation
from dram_signals.broker_sim.strategies import (
    BASELINE_SPECS,
    all_broker_specs,
    make_scored_twin,
)


def test_twins_match_baseline_strategy_except_score_flags() -> None:
    for base in BASELINE_SPECS:
        twin = make_scored_twin(base, score_lag_days=1)
        assert twin.parent_id == base.broker_id
        assert twin.cohort == "scored"
        assert twin.has_memory_score is True
        assert twin.score_lag_days == 1
        assert twin.min_buy_discount == base.min_buy_discount
        assert twin.min_sell_premium == base.min_sell_premium
        assert twin.inventory_target_units == base.inventory_target_units
        assert twin.max_cash_fraction_per_buy == base.max_cash_fraction_per_buy
        assert twin.customer_affinity == base.customer_affinity
        assert twin.prefer_active == base.prefer_active
        assert twin.broker_id == f"{base.broker_id}_scored"


def test_all_specs_include_six_when_scored() -> None:
    specs = all_broker_specs(include_scored=True, score_lag_days=1)
    assert len(specs) == 6
    assert {s.broker_id for s in specs} == {
        "apex",
        "harbor",
        "summit",
        "apex_scored",
        "harbor_scored",
        "summit_scored",
    }


def test_observe_score_lag_and_baseline_none() -> None:
    rng = random.Random(7)
    path = generate_score_path(10, rng)
    assert observe_score(path, day=1, has_memory_score=True, score_lag_days=1) is None
    day5 = next(r.true_score for r in path if r.day == 5)
    assert observe_score(path, day=6, has_memory_score=True, score_lag_days=1) == day5
    assert observe_score(path, day=6, has_memory_score=False, score_lag_days=1) is None


def test_nudge_shortage_vs_glut() -> None:
    base_buy, base_sell = 0.06, 0.01
    tight = nudge_from_score(
        min_buy_discount=base_buy, min_sell_premium=base_sell, observed=80
    )
    loose = nudge_from_score(
        min_buy_discount=base_buy, min_sell_premium=base_sell, observed=20
    )
    none = nudge_from_score(
        min_buy_discount=base_buy, min_sell_premium=base_sell, observed=None
    )
    assert tight.min_buy_discount < base_buy
    assert tight.min_sell_premium > base_sell
    assert tight.inventory_headroom > 1.0
    assert loose.min_buy_discount > base_buy
    assert loose.min_sell_premium < base_sell
    assert none.min_buy_discount == base_buy


def test_scored_twins_share_parent_customers() -> None:
    result = run_simulation(
        days=5, seed=11, prefer_live_prices=False, include_scored=True
    )
    for parent in ("apex", "harbor", "summit"):
        twin = f"{parent}_scored"
        assert result.book.customers_for(parent) == result.book.customers_for(twin)


def test_simulation_with_scored_twins_and_comparisons() -> None:
    result = run_simulation(
        days=20,
        seed=42,
        prefer_live_prices=False,
        include_scored=True,
        score_lag_days=1,
        starting_cash=250_000.0,
    )
    assert len(result.brokers) == 6
    assert result.starting_cash == 250_000.0
    summary = result.summary()
    assert summary["include_scored"] is True
    assert len(summary["pair_comparisons"]) == 3
    assert summary["score_path"]
    # History carries true score + observed for twins
    day2 = summary["history"][1]
    assert "true_memory_score" in day2
    assert day2["brokers"]["apex"]["observed_score"] is None
    assert day2["brokers"]["apex_scored"]["observed_score"] is not None


def test_baseline_only_has_three() -> None:
    result = run_simulation(
        days=4, seed=2, prefer_live_prices=False, include_scored=False
    )
    assert set(result.brokers) == {"apex", "harbor", "summit"}
    assert result.summary()["pair_comparisons"] == []
