"""Plain-language reasons for CRM / rep display (no weights)."""

from __future__ import annotations

from typing import Any, Optional


def build_reasons(
    *,
    strength_details: list[dict[str, Any]],
    timing: dict[str, Any],
    product_family: str,
    spot_note: Optional[str] = None,
    discovery_first: bool = False,
    max_reasons: int = 3,
) -> str:
    parts: list[str] = []
    for d in strength_details[:2]:
        code = d.get("signal_type", "?")
        age = d.get("age_days")
        age_s = f" {int(age)}d ago" if age is not None else ""
        parts.append(f"{code}{age_s}")
    if discovery_first:
        parts.append(
            f"{product_family} part past LTB — expect a disorganized buyer, lead with discovery"
        )
    elif spot_note:
        parts.append(spot_note)
    elif timing.get("deadline", 0) >= 6:
        parts.append(f"deadline pressure on {product_family}")
    if timing.get("allocation_boost", 0) > 0:
        parts.append(f"{product_family} market allocation active")
    return "; ".join(parts[:max_reasons])
