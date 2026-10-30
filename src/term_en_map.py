"""Map foreign matched_terms → English term-bank equivalents for Feed highlights.

Matched terms on KO/ZH/JA/DE rows are source-language. When the Feed shows
`excerpt_en`, highlight using English phrases from the same product family /
supply topic (plus a small gloss table for common CJK supply phrases that
Argos tends to emit).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.fetch_edgar import REPO_ROOT
from src.extract import load_supply_terms, load_yaml

DEFAULT_TAXONOMY = REPO_ROOT / "config" / "product_taxonomy.yaml"
DEFAULT_SUPPLY = REPO_ROOT / "config" / "supply_terms.yaml"

# Latin / ASCII-ish token (allows spaces, hyphens, digits) — treated as already-EN.
_LATINISH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\s\-/&.']*[A-Za-z0-9]%?$")

# Curated glosses so Argos English often lights up (term-bank sense, not free invent).
# Keys include both simplified and traditional forms used in CNINFO / MOPS.
_CJK_GLOSS: dict[str, list[str]] = {
    # Packaging / foundry products
    "约当晶圆": ["wafer equivalents", "wafer equivalent", "equivalent wafers", "wafers"],
    "約當晶圓": ["wafer equivalents", "wafer equivalent", "equivalent wafers", "wafers"],
    "晶圆代工": ["wafer foundry", "foundry", "wafer fabrication"],
    "晶圓代工": ["wafer foundry", "foundry", "wafer fabrication"],
    "晶圆制造": ["wafer manufacturing", "wafer fabrication"],
    "晶圓製造": ["wafer manufacturing", "wafer fabrication"],
    "晶圆厂": ["wafer fab", "fab", "foundry"],
    "晶圓廠": ["wafer fab", "fab", "foundry"],
    "晶圆": ["wafer", "wafers"],
    "晶圓": ["wafer", "wafers"],
    "封装产能": ["packaging capacity"],
    "封裝產能": ["packaging capacity"],
    "代工产能": ["foundry capacity", "packaging capacity"],
    "代工產能": ["foundry capacity", "packaging capacity"],
    "存储芯片": ["memory chips", "memory"],
    "存储器": ["memory"],
    "闪存": ["flash memory", "flash"],
    "显存": ["graphics memory"],
    "顯存": ["graphics memory"],
    # Capacity / utilization
    "产能利用率": ["capacity utilization", "capacity utilization rate", "utilization", "capacity"],
    "產能利用率": ["capacity utilization", "capacity utilization rate", "utilization", "capacity"],
    "产能满载": ["full capacity", "fully loaded", "at full capacity", "capacity"],
    "產能滿載": ["full capacity", "fully loaded", "at full capacity", "capacity"],
    "产能紧张": ["tight capacity", "capacity constrained", "capacity"],
    "產能緊張": ["tight capacity", "capacity constrained", "capacity"],
    "产能扩张": ["capacity expansion", "capacity expansions"],
    "產能擴張": ["capacity expansion", "capacity expansions"],
    "产能扩充": ["capacity expansion"],
    "產能擴充": ["capacity expansion"],
    "产能": ["capacity"],
    "產能": ["capacity"],
    # Supply / shortage
    "供不应求": ["shortage", "supply shortage", "exceeds supply", "undersupply"],
    "供不應求": ["shortage", "supply shortage", "exceeds supply", "undersupply"],
    "供应短缺": ["supply shortage", "shortage"],
    "供應短缺": ["supply shortage", "shortage"],
    "供应紧张": ["tight supply", "supply shortage", "shortage"],
    "供應緊張": ["tight supply", "supply shortage", "shortage"],
    "供应不足": ["short supply", "shortage"],
    "供應不足": ["short supply", "shortage"],
    "短缺": ["shortage"],
    "缺货": ["shortage", "sold out"],
    "缺貨": ["shortage", "sold out"],
    "供应": ["supply"],
    "供應": ["supply"],
    # Pricing
    "价格上涨": ["price increase", "price increases", "rising prices"],
    "價格上漲": ["price increase", "price increases", "rising prices"],
    "价格上升": ["price increase", "rising prices"],
    "價格上升": ["price increase", "rising prices"],
    "价格上行": ["price increase", "rising prices"],
    "價格上行": ["price increase", "rising prices"],
    "价格": ["price", "prices"],
    "價格": ["price", "prices"],
    # Demand / sentiment fragments that appear in matched_terms
    "增长": ["growth", "demand growth"],
    "成長": ["growth", "demand growth"],
    "需求": ["demand"],
}


def _is_latinish(term: str) -> bool:
    t = (term or "").strip()
    if not t:
        return False
    # Pure ASCII product codes / English phrases
    if _LATINISH.match(t):
        return True
    # Mostly latin characters (e.g. "NAND flash")
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if ord(c) < 128) / len(letters) >= 0.7


def parse_matched_terms(matched_terms: str) -> list[str]:
    """Extract term strings from `product:X; topic:Y; dir:Z`."""
    out: list[str] = []
    for part in str(matched_terms or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            term = part.split(":", 1)[1].strip()
        else:
            term = part
        if term:
            out.append(term)
    return out


def _latin_terms(terms: list[str]) -> list[str]:
    return [t for t in terms if _is_latinish(t)]


@lru_cache(maxsize=4)
def _build_foreign_to_en(
    taxonomy_path: str,
    supply_path: str,
) -> dict[str, list[str]]:
    """foreign_term → preferred English equivalents (longest first)."""
    tax = Path(taxonomy_path)
    sup = Path(supply_path)
    mapping: dict[str, list[str]] = {}

    def add(src: str, ens: list[str]) -> None:
        src = (src or "").strip()
        if not src or _is_latinish(src):
            return
        bucket = mapping.setdefault(src, [])
        seen = set(bucket)
        for en in ens:
            en = (en or "").strip()
            if not en or en in seen or not _is_latinish(en):
                continue
            bucket.append(en)
            seen.add(en)

    # Product taxonomy: same family → English siblings.
    data = load_yaml(tax) if tax.is_file() else {}
    for _category, families in (data.get("categories") or {}).items():
        for family, term_list in (families or {}).items():
            terms = [str(t) for t in (term_list or []) if t]
            ens = _latin_terms(terms)
            # Family label as weak fallback (DDR5 → "DDR5" already latin).
            if family and _is_latinish(family.replace("_", " ")):
                ens = ens + [family.replace("_", " ")]
            for t in terms:
                if not _is_latinish(t):
                    add(t, ens)

    supply = load_supply_terms(sup) if sup.is_file() else {}
    en_topics = supply.get("topics") or {}
    en_dir = supply.get("direction") or {}
    en_sent = supply.get("sentiment") or {}

    for lang_key in ("korean", "chinese", "japanese", "german"):
        block = supply.get(lang_key) or {}
        for label, foreign_terms in (block.get("topics") or {}).items():
            ens = _latin_terms([str(x) for x in (en_topics.get(label) or [])])
            for ft in foreign_terms or []:
                add(str(ft), ens)
        for label, foreign_terms in (block.get("direction") or {}).items():
            ens = _latin_terms([str(x) for x in (en_dir.get(label) or [])])
            for ft in foreign_terms or []:
                add(str(ft), ens)
        for label, foreign_terms in (block.get("sentiment") or {}).items():
            ens = _latin_terms([str(x) for x in (en_sent.get(label) or [])])
            for ft in foreign_terms or []:
                add(str(ft), ens)

    # Curated glosses (append; bank terms already preferred first).
    for src, ens in _CJK_GLOSS.items():
        add(src, ens)

    # Longest-first within each list for highlight priority.
    for src, ens in mapping.items():
        mapping[src] = sorted(set(ens), key=len, reverse=True)
    return mapping


def english_equivalents_for_term(
    term: str,
    *,
    taxonomy_path: Path | None = None,
    supply_path: Path | None = None,
) -> list[str]:
    term = (term or "").strip()
    if not term:
        return []
    if _is_latinish(term):
        return [term]
    path_t = str(taxonomy_path or DEFAULT_TAXONOMY)
    path_s = str(supply_path or DEFAULT_SUPPLY)
    return list(_build_foreign_to_en(path_t, path_s).get(term, []))


def matched_terms_en(
    matched_terms: str,
    *,
    taxonomy_path: Path | None = None,
    supply_path: Path | None = None,
    excerpt_en: str = "",
) -> list[str]:
    """English highlight phrases for a signal's matched_terms.

    When `excerpt_en` is provided, phrases that actually occur in the English
    text are ordered first (still keep others for partial MT overlap).
    """
    path_t = taxonomy_path or DEFAULT_TAXONOMY
    path_s = supply_path or DEFAULT_SUPPLY
    seen: set[str] = set()
    out: list[str] = []
    for term in parse_matched_terms(matched_terms):
        cands = english_equivalents_for_term(
            term, taxonomy_path=path_t, supply_path=path_s
        )
        if not cands and _is_latinish(term):
            cands = [term]
        for en in cands:
            key = en.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(en)
    if excerpt_en:
        low = excerpt_en.lower()
        present = [t for t in out if t.lower() in low]
        absent = [t for t in out if t.lower() not in low]
        # Prefer phrases that will actually paint, then the rest.
        out = present + absent
    # Drop very short tokens (mirror UI highlight filter length > 2).
    return [t for t in out if len(t) > 2]


def enrich_signal_matched_terms_en(
    row: dict[str, Any],
    *,
    taxonomy_path: Path | None = None,
    supply_path: Path | None = None,
) -> list[str]:
    return matched_terms_en(
        str(row.get("matched_terms") or ""),
        taxonomy_path=taxonomy_path,
        supply_path=supply_path,
        excerpt_en=str(row.get("excerpt_en") or ""),
    )
