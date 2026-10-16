"""Section 4.6 worked examples — arithmetic locked in Python."""

from __future__ import annotations

from dram_signals.scoring.formula import (
    ScoredSignalInput,
    signal_value,
    combine_signal_values,
)
from dram_signals.scoring.scorer import (
    score_worked_example_1,
    score_worked_example_2,
    score_worked_example_3,
)


def test_example1_component_arithmetic() -> None:
    s1 = signal_value("S1", 0.9, 10.0)
    s6 = signal_value("S6", 0.8, 5.0)
    assert s1 is not None and s6 is not None
    assert abs(s1 - 25.72) < 0.02
    assert abs(s6 - 11.71) < 0.02
    raw = combine_signal_values([s1, s6])
    assert abs(raw - (25.72 + 0.5 * 11.71)) < 0.05
    s = min(45.0, raw) * 1.00
    assert abs(s - 31.6) < 0.1


def test_example_1_supply_tier_a() -> None:
    out = score_worked_example_1()
    assert not out.gated
    ls = out.lead_score
    assert ls is not None
    assert abs(ls.signal_strength - 31.6) < 0.15
    assert abs(ls.fit - 29.0) < 0.01
    assert abs(ls.timing - 7.0) < 0.01
    assert abs(ls.total - 67.6) < 0.2
    assert ls.tier == "A"


def test_example_2_demand_without_allocation_tier_d() -> None:
    out = score_worked_example_2(market_allocation=False)
    ls = out.lead_score
    assert ls is not None
    assert abs(ls.signal_strength - 14.8) < 0.15
    assert abs(ls.fit - 18.0) < 0.01
    assert abs(ls.timing - 1.0) < 0.01
    assert abs(ls.total - 33.8) < 0.2
    assert ls.tier == "D"


def test_example_2_demand_with_allocation_tier_c() -> None:
    out = score_worked_example_2(market_allocation=True)
    ls = out.lead_score
    assert ls is not None
    assert abs(ls.timing - 6.0) < 0.01
    assert abs(ls.total - 38.8) < 0.2
    assert ls.tier == "C"


def test_example_3_demand_post_ltb_tier_a() -> None:
    out = score_worked_example_3()
    ls = out.lead_score
    assert ls is not None
    # D1: 35 × 0.85 = 29.75
    assert abs(ls.signal_strength - 29.75) < 0.05
    assert abs(ls.fit - 30.0) < 0.01
    assert abs(ls.timing - 11.0) < 0.01
    assert abs(ls.total - 70.8) < 0.2
    assert ls.tier == "A"
    assert "discovery" in out.reasons.lower()


def test_d1_no_decay() -> None:
    v = signal_value("D1", 1.0, 30.0)
    assert v == 35.0
