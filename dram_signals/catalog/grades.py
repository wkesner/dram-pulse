"""Market-grade inference for DRAM variants.

Industrial / automotive / consumer parts often share a die family and differ
mainly by temperature qualification and package marking. PPV and shortage
work needs those grades as first-class fields — an industrial TFBGA may still
be buyable when the consumer twin is on allocation.
"""

from __future__ import annotations

import re

GRADES = ("consumer", "industrial", "automotive", "military", "unknown")

# Micron-style cues: AAT/AIT markers in the MPN, and temp windows.
_AUTOMOTIVE_HINT = re.compile(
    r"automotive|aec[\s-]?q|\baat\b|\bait\b|105\s*c|125\s*c",
    re.I,
)
_INDUSTRIAL_HINT = re.compile(r"industrial|\bit\b|-40\s*c", re.I)
_CONSUMER_HINT = re.compile(r"^0\s*c|\b0c\s+to|commercial|consumer", re.I)
_MIL_HINT = re.compile(r"military|mil[\s-]?spec|-55\s*c", re.I)


def infer_grade(temperature_range: str | None, *, hints: str | None = None) -> str:
    """Map temperature / free-text hints (often the MPN) to a catalog grade."""
    blob = " ".join(x for x in (temperature_range, hints) if x).strip()
    if not blob:
        return "unknown"
    if _MIL_HINT.search(blob):
        return "military"
    # Check automotive before industrial: both use -40C floors; AAT/105C are auto.
    if _AUTOMOTIVE_HINT.search(blob):
        return "automotive"
    if _INDUSTRIAL_HINT.search(blob):
        return "industrial"
    if _CONSUMER_HINT.search(blob):
        return "consumer"
    return "unknown"


def derive_family_code(mpn: str) -> str:
    """Best-effort family code from an ordering MPN.

    Examples:
      MT40A4G4VA-062E:B → MT40A4G4VA
      K4A8G165WC-BCTD → K4A8G165WC
    """
    base = (mpn or "").strip().upper()
    if not base:
        return ""
    # Drop revision suffixes after colon first.
    base = base.split(":", 1)[0]
    # Speed / temp bin usually after first hyphen for Micron/Samsung-style PNs.
    if "-" in base:
        head, rest = base.split("-", 1)
        # Keep hyphenated density tokens like rare forms; default to head.
        if len(head) >= 6:
            return head
        return f"{head}-{rest.split('-', 1)[0]}"
    return base
