"""Signal catalog: base points and half-lives (rubric section 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SignalTypeDef:
    code: str
    side: str  # supply | demand | market
    name: str
    base: float
    half_life_days: Optional[float]  # None = no decay (e. for D1 until LTB handling)
    sources: tuple[str, ...] = ()


# Supply (may have excess)
S1 = SignalTypeDef("S1", "supply", "Inventory write-down / E&O naming memory", 30, 45)
S2 = SignalTypeDef("S2", "supply", "Program/product cancellation, major customer loss", 28, 45)
S3 = SignalTypeDef("S3", "supply", "Restructuring / plant closure / bankruptcy", 26, 60)
S4 = SignalTypeDef("S4", "supply", "Guidance cut citing inventory correction", 20, 30)
S5 = SignalTypeDef("S5", "supply", "Job post: excess inventory / asset recovery", 18, 21)
S6 = SignalTypeDef("S6", "supply", "Production cut or inventory glut", 15, 14)
S7 = SignalTypeDef("S7", "supply", "Acquisition closed", 12, 60)

# Demand (may need DRAM)
D0 = SignalTypeDef("D0", "demand", "Company on allocation (letter or verbal)", 35, 30)
D1 = SignalTypeDef("D1", "demand", "EOL / LTB on a part this account uses", 35, None)
D2 = SignalTypeDef("D2", "demand", "Memory shortage hurting shipments", 32, 30)
D3 = SignalTypeDef("D3", "demand", "Job post: memory buyer / allocation / shortage", 22, 21)
D7 = SignalTypeDef("D7", "demand", "PCN forcing requalification on a part they use", 18, 60)
D6 = SignalTypeDef("D6", "demand", "Material agreement for large hardware build", 16, 45)
D4 = SignalTypeDef("D4", "demand", "Hardware NPI / production-ramp hiring", 14, 21)
D5 = SignalTypeDef("D5", "demand", "Product launch / ramp / capacity expansion", 12, 14)

# Market-level (feeds regime / Timing, not lead S directly)
M1 = SignalTypeDef("M1", "market", "Market-level family allocation", 0, 30)

SIGNAL_TYPES: dict[str, SignalTypeDef] = {
    t.code: t
    for t in (
        S1, S2, S3, S4, S5, S6, S7,
        D0, D1, D2, D3, D4, D5, D6, D7,
        M1,
    )
}

PRODUCT_FAMILIES = (
    "DDR4",
    "DDR5",
    "LPDDR4",
    "LPDDR5",
    "GDDR",
    "HBM",
    "unknown",
)

RUBRIC_VERSION = "v1"

# Regime multipliers: shortage prizes inventory; glut prizes buyers
REGIME_MULTIPLIER = {
    ("shortage", "supply"): 1.00,
    ("shortage", "demand"): 0.85,
    ("balanced", "supply"): 1.00,
    ("balanced", "demand"): 1.00,
    ("glut", "supply"): 0.85,
    ("glut", "demand"): 1.00,
}

# Buyer type × regime fit points (section 4.3)
BUYER_TYPE_REGIME_POINTS = {
    # buyer_type: {regime: points}
    "hyperscaler": {"shortage": 5, "balanced": 8, "glut": 8},
    "very_large_oem": {"shortage": 8, "balanced": 4, "glut": 3},
    "mid_oem_ems": {"shortage": 8, "balanced": 6, "glut": 6},
    "small": {"shortage": 5, "balanced": 5, "glut": 5},
    "very_small": {"shortage": 2, "balanced": 2, "glut": 2},
}

TIER_BANDS = (
    ("A", 65, 100),
    ("B", 50, 64),
    ("C", 35, 49),
    ("D", 0, 34),
)


def tier_for_score(score: float) -> str:
    if score >= 65:
        return "A"
    if score >= 50:
        return "B"
    if score >= 35:
        return "C"
    return "D"


def confidence_band(confidence: float) -> Optional[float]:
    """Return band multiplier, or None if held for review (<0.6)."""
    if confidence >= 0.8:
        return 1.0
    if confidence >= 0.6:
        return 0.8
    return None
