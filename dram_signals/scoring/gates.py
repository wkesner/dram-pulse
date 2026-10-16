"""Hard gates — checked before scoring (section 4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class GateResult:
    blocked: bool
    reason: Optional[str] = None
    action: Optional[str] = None  # block | exclude | review | notify_owner


def check_gates(
    company: dict[str, Any],
    *,
    signals_ok: bool,
    entity_resolved: bool,
) -> GateResult:
    flags = company.get("gate_flags") or {}
    if isinstance(flags, str):
        import json

        flags = json.loads(flags) if flags else {}

    if flags.get("denied_party") or flags.get("ofac") or flags.get("bis_entity"):
        return GateResult(True, "denied_party", "block")
    if flags.get("counterfeit") or flags.get("erai"):
        return GateResult(True, "counterfeit_bad_actor", "block")
    if flags.get("competitor"):
        return GateResult(True, "competitor", "exclude")
    if flags.get("do_not_contact"):
        return GateResult(True, "do_not_contact", "block")
    if flags.get("open_deal_owner"):
        return GateResult(True, "open_deal_owned", "notify_owner")
    if not entity_resolved:
        return GateResult(True, "entity_unresolved", "review")
    if not signals_ok:
        return GateResult(True, "all_signals_low_confidence", "review")
    return GateResult(False)
