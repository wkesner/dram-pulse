"""Pure part-number decoder (Step 2). No network I/O.

decode(pn) → dict | None with vendor, kind, family, density fields, confidence.
Rules live in data/partnum_rules.yaml; each rule declares its evidence source.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from dram_signals import config

RULES_PATH = Path(
    __import__("os").environ.get(
        "DRAM_SIGNALS_PARTNUM_RULES",
        str(config.DATA_DIR / "partnum_rules.yaml"),
    )
)

# Density tokens often appear as 8G / 16G / 1G8 / 4G4 inside the PN body.
_DENSITY_RE = re.compile(
    r"(?P<a>\d{1,3})G(?P<b>\d{0,2})(?P<org>x?\d{0,2})?",
    re.I,
)
_MODULE_CAP_RE = re.compile(r"(?P<cap>\d{1,3})G(?P<rest>[A-Z0-9])", re.I)

# Prefix characters that may appear in free text when mining signals.
PREFIX_CHARS = re.compile(
    r"\b("
    r"K4[ARFUZV]|K3[ULK]|KHB|KM[A-Z0-9]{0,2}|"
    r"M3(?:21|93|78|23)|M4(?:71|25)|"
    r"H5(?:AN|AG|CG|CN|GQ)|H56G|H9[HJ]|HMCG|HMA|"
    r"MT(?:40|53|62)"
    r")[A-Z0-9]{2,}(?:-[A-Z0-9:]+)?\b",
    re.I,
)


@lru_cache(maxsize=1)
def load_rules(path: str | None = None) -> list[dict[str, Any]]:
    rules_path = Path(path) if path else RULES_PATH
    with rules_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    rules = list(data.get("rules") or [])
    # Longest prefix first so M321 beats M3, K4A beats K4, etc.
    rules.sort(key=lambda r: len(str(r.get("prefix") or "")), reverse=True)
    return rules


def normalize_pn(pn: str) -> str:
    return (pn or "").strip().upper().replace(" ", "")


def _parse_density_fields(body: str, kind: str) -> dict[str, Any]:
    """Best-effort density / organization from the PN body after the prefix."""
    out: dict[str, Any] = {
        "density_gbit": None,
        "capacity_gb": None,
        "organization": None,
    }
    if not body:
        return out

    if kind == "module":
        # e.g. M321R4GA0EB0 → 4G often means 4GB module capacity (heuristic).
        m = re.search(r"(\d{1,3})G", body, re.I)
        if m:
            out["capacity_gb"] = int(m.group(1))
            out["confidence_note"] = "module_capacity_heuristic"
        return out

    # Component forms like 8G165 (8Gb, x16) or 4G4 (4Gb x4) or 1G8
    m = re.match(r"(\d{1,3})G(\d{0,3})", body, re.I)
    if not m:
        return out
    left = int(m.group(1))
    right = m.group(2)
    if right and len(right) >= 2:
        # Samsung-style: 8G165 → 8Gb, org from 16…
        out["density_gbit"] = left
        if right.startswith(("16", "8", "4", "32")):
            if right.startswith("16"):
                out["organization"] = "x16"
            elif right.startswith("32"):
                out["organization"] = "x32"
            elif right.startswith("8"):
                out["organization"] = "x8"
            elif right.startswith("4"):
                out["organization"] = "x4"
    elif right and len(right) == 1:
        # 4G4 / 1G8 style → density Gb, organization xN
        out["density_gbit"] = left
        out["organization"] = f"x{right}"
    else:
        out["density_gbit"] = left
    return out


def _split_speed_package(tail: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Split trailing -SPEED:REV / -SPEEDPKG into speed, package, die_rev."""
    if not tail:
        return None, None, None
    die_rev = None
    core = tail
    if ":" in core:
        core, die_rev = core.split(":", 1)
        die_rev = die_rev or None
    # Heuristic: leading alnum speed code, remainder package
    m = re.match(r"([A-Z0-9]{2,6})([A-Z]{0,4})$", core, re.I)
    if m:
        return m.group(1).upper(), (m.group(2).upper() or None), die_rev
    return core.upper(), None, die_rev


def decode(pn: str, *, rules_path: str | None = None) -> Optional[dict[str, Any]]:
    """Decode a part number. Returns None if no prefix rule matches."""
    norm = normalize_pn(pn)
    if not norm or len(norm) < 4:
        return None

    base, sep, suffix = norm.partition("-")
    rules = load_rules(rules_path)
    matched: Optional[dict[str, Any]] = None
    for rule in rules:
        prefix = str(rule.get("prefix") or "").upper()
        if prefix and base.startswith(prefix):
            matched = rule
            break
    if not matched:
        return None

    prefix = str(matched["prefix"]).upper()
    body = base[len(prefix) :]
    kind = str(matched.get("kind") or "component")
    density = _parse_density_fields(body, kind)
    speed_code, package_code, die_rev = _split_speed_package(suffix if sep else "")

    # Keep the rule's confidence for the prefix match; leave unparsed
    # density/org as null rather than guessing (unknown fields stay empty).
    confidence = str(matched.get("confidence") or "low")

    result: dict[str, Any] = {
        "vendor": matched.get("vendor"),
        "kind": kind,
        "family": matched.get("family"),
        "module_type": matched.get("module_type"),
        "density_gbit": density.get("density_gbit"),
        "capacity_gb": density.get("capacity_gb"),
        "organization": density.get("organization"),
        "speed_code": speed_code,
        "package_code": package_code,
        "die_rev": die_rev,
        "confidence": confidence,
        "rules_matched": [matched.get("id")],
        "rule_source": matched.get("source"),
        "mpn": norm,
        "prefix": prefix,
        "family_code": base,  # ordering base without speed bin
    }
    return result


def extract_candidate_mpns(text: str) -> list[str]:
    """Pull plausible memory MPNs from free text (signals, EDGAR, etc.)."""
    if not text:
        return []
    found = {normalize_pn(m.group(0)) for m in PREFIX_CHARS.finditer(text)}
    # Keep only those that actually decode.
    out: list[str] = []
    for mpn in sorted(found):
        if decode(mpn):
            out.append(mpn)
    return out


def clear_rules_cache() -> None:
    load_rules.cache_clear()
