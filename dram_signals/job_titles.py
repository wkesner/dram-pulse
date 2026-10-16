"""Title filters for job-board collectors."""

from __future__ import annotations

import re
from typing import Optional

# Phrases from the Phase 1 brief. Matched against job titles only
# (description body is still captured broadly once a title matches).
TITLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bbuyer\b",
        r"\bprocurement\b",
        r"\bcommodity\s+manager\b",
        r"\bsupply\s+chain\b",
    )
)


def matching_title_keyword(title: str) -> Optional[str]:
    """Return the first matching keyword label, or None if the title is out of scope."""
    if not title:
        return None
    for pattern in TITLE_PATTERNS:
        match = pattern.search(title)
        if match:
            return match.group(0).lower()
    return None
