"""Lead scoring: classify → gates → S+F+T → tier + snapshots."""

from dram_signals.scoring.catalog import RUBRIC_VERSION, SIGNAL_TYPES, tier_for_score
from dram_signals.scoring.formula import (
    combine_lead,
    score_fit,
    score_signal_strength,
    score_timing,
    signal_value,
)
from dram_signals.scoring.scorer import (
    score_lead,
    score_worked_example_1,
    score_worked_example_2,
    score_worked_example_3,
)

__all__ = [
    "RUBRIC_VERSION",
    "SIGNAL_TYPES",
    "tier_for_score",
    "signal_value",
    "score_signal_strength",
    "score_fit",
    "score_timing",
    "combine_lead",
    "score_lead",
    "score_worked_example_1",
    "score_worked_example_2",
    "score_worked_example_3",
]
