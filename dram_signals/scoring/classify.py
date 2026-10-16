"""Classify raw collector signals into typed signals (section 2 / 7.1)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from dram_signals.partnums import decode
from dram_signals.scoring.catalog import SIGNAL_TYPES

# Keyword → (signal_type, side, family_hint)
_EDGAR_RULES: list[tuple[re.Pattern[str], str, Optional[str]]] = [
    (re.compile(r"inventory\s+write[- ]?down|excess\s+and\s+obsolete|\bE&O\b", re.I), "S1", None),
    (re.compile(r"program\s+cancell|product\s+cancell|lost\s+(?:a\s+)?(?:major\s+)?customer", re.I), "S2", None),
    (re.compile(r"restructur|plant\s+clos|Item\s+2\.05|Item\s+1\.03|bankrupt", re.I), "S3", None),
    (re.compile(r"guidance\s+(?:cut|lower|reduc)|inventory\s+correction|weak\s+demand", re.I), "S4", None),
    (re.compile(r"acquisit(?:ion|ions)\s+(?:clos|complet)|Item\s+2\.01", re.I), "S7", None),
    (re.compile(r"supply\s+constraint|memory\s+shortage|allocation|unable\s+to\s+(?:source|secure)", re.I), "D2", None),
    (re.compile(r"material\s+(?:definitive\s+)?agreement|Item\s+1\.01", re.I), "D6", None),
]

_JOB_SUPPLY = re.compile(
    r"excess\s+inventory|asset\s+recovery|liquidat|surplus\s+(?:inventory|stock)",
    re.I,
)
_JOB_DEMAND_ALLOC = re.compile(r"\ballocation\b|\bshortage\b", re.I)
_JOB_BUYER = re.compile(
    r"memory|DRAM|commodity\s+manager|buyer|procurement|supply\s+chain",
    re.I,
)
_JOB_NPI = re.compile(r"\bNPI\b|new\s+product|production\s+ramp|ramp[- ]up", re.I)

_FAMILY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bHBM\d*[A-Z]?\b", re.I), "HBM"),
    (re.compile(r"\bGDDR\d*\b", re.I), "GDDR"),
    (re.compile(r"\bLPDDR5\b", re.I), "LPDDR5"),
    (re.compile(r"\bLPDDR4\b", re.I), "LPDDR4"),
    (re.compile(r"\bDDR5\b", re.I), "DDR5"),
    (re.compile(r"\bDDR4\b", re.I), "DDR4"),
    (re.compile(r"\bDDR3\b", re.I), "DDR4"),  # treat legacy DDR3 demand under DDR4 regime bucket
]


@dataclass
class Classification:
    side: str
    signal_type: str
    product_family: str
    confidence: float
    event_key: str
    event_at: str
    deadline: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def infer_family(text: str, keyword: str | None = None) -> str:
    blob = f"{keyword or ''} {text or ''}"
    for pat, fam in _FAMILY_PATTERNS:
        if pat.search(blob):
            return fam
    # Try decode any MPN in text
    from dram_signals.partnums import extract_candidate_mpns

    for mpn in extract_candidate_mpns(text or ""):
        decoded = decode(mpn)
        if decoded and decoded.get("family"):
            fam = str(decoded["family"]).upper()
            if fam.startswith("LPDDR5"):
                return "LPDDR5"
            if fam.startswith("LPDDR4"):
                return "LPDDR4"
            if fam.startswith("GDDR"):
                return "GDDR"
            if fam.startswith("HBM"):
                return "HBM"
            if fam in {"DDR4", "DDR5"}:
                return fam
    return "unknown"


def make_event_key(company_key: str, signal_type: str, event_at: str, url: str = "") -> str:
    day = (event_at or "")[:10]
    raw = f"{company_key}|{signal_type}|{day}|{urlparse_path(url)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def urlparse_path(url: str) -> str:
    from urllib.parse import urlparse

    if not url:
        return ""
    p = urlparse(url)
    return f"{p.netloc}{p.path}"


def classify_raw_signal(row: dict[str, Any], *, company_key: Optional[str] = None) -> list[Classification]:
    """Return zero or more typed classifications for one raw signals row.

    One document can yield one signal per side.
    """
    source = (row.get("source") or "").lower()
    text = row.get("raw_text") or ""
    keyword = row.get("keyword") or ""
    company = row.get("company_name") or "unknown"
    ts = row.get("timestamp") or _now()
    url = row.get("url") or ""
    key = company_key or _slug(company)

    results: list[Classification] = []

    if source == "edgar_8k":
        results.extend(_classify_edgar(text, keyword, key, ts, url))
    elif source in {"greenhouse", "lever", "workday", "icims"}:
        results.extend(_classify_job(text, keyword, key, ts, url))
    elif source in {"samsung_family_status", "part_lifecycle", "micron_obsolete"}:
        results.extend(_classify_lifecycle(source, text, keyword, key, ts, url))
    else:
        # Generic text pass
        results.extend(_classify_edgar(text, keyword, key, ts, url))

    # Deduplicate by (side, signal_type) keeping highest confidence
    best: dict[tuple[str, str], Classification] = {}
    for c in results:
        k = (c.side, c.signal_type)
        if k not in best or c.confidence > best[k].confidence:
            best[k] = c
    return list(best.values())


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "unknown"


def _classify_edgar(
    text: str, keyword: str, company_key: str, ts: str, url: str
) -> list[Classification]:
    blob = f"{keyword}\n{text}"
    family = infer_family(blob, keyword)
    found: list[Classification] = []
    for pat, code, _ in _EDGAR_RULES:
        if pat.search(blob):
            typedef = SIGNAL_TYPES[code]
            conf = 0.85 if keyword and pat.search(keyword) else 0.75
            # Memory write-downs need memory/component cue for S1
            if code == "S1" and not re.search(
                r"memory|DRAM|component|semiconductor|inventory", blob, re.I
            ):
                conf *= 0.9
            found.append(
                Classification(
                    side=typedef.side,
                    signal_type=code,
                    product_family=family,
                    confidence=min(1.0, conf),
                    event_key=make_event_key(company_key, code, ts, url),
                    event_at=ts,
                    metadata={"matcher": pat.pattern[:80]},
                )
            )
    return found


def _classify_job(
    text: str, keyword: str, company_key: str, ts: str, url: str
) -> list[Classification]:
    blob = f"{keyword}\n{text}"
    family = infer_family(blob, keyword)
    out: list[Classification] = []
    if _JOB_SUPPLY.search(blob):
        out.append(
            Classification(
                side="supply",
                signal_type="S5",
                product_family=family,
                confidence=0.8,
                event_key=make_event_key(company_key, "S5", ts, url),
                event_at=ts,
            )
        )
    if _JOB_BUYER.search(blob) and _JOB_DEMAND_ALLOC.search(blob):
        out.append(
            Classification(
                side="demand",
                signal_type="D3",
                product_family=family,
                confidence=0.85,
                event_key=make_event_key(company_key, "D3", ts, url),
                event_at=ts,
            )
        )
    elif _JOB_NPI.search(blob) and _JOB_BUYER.search(blob):
        out.append(
            Classification(
                side="demand",
                signal_type="D4",
                product_family=family,
                confidence=0.75,
                event_at=ts,
                event_key=make_event_key(company_key, "D4", ts, url),
            )
        )
    return out


def _classify_lifecycle(
    source: str, text: str, keyword: str, company_key: str, ts: str, url: str
) -> list[Classification]:
    family = infer_family(text, keyword)
    try:
        payload = json.loads(text) if text.strip().startswith("{") else {}
    except json.JSONDecodeError:
        payload = {}
    status = str(payload.get("new_status") or keyword or "").upper()
    if status in {"EOL", "OBSOLETE"} or "eol" in (keyword or "").lower():
        # Family-level EOL without account link → Timing/regime, not a company lead.
        # Still emit a typed market-adjacent note as D1 only when company_key is real.
        if company_key and company_key not in {"samsung", "samsung-electronics", "unknown"}:
            return [
                Classification(
                    side="demand",
                    signal_type="D1",
                    product_family=family if family != "unknown" else _family_from_model(payload),
                    confidence=0.9,
                    event_key=make_event_key(company_key, "D1", ts, url),
                    event_at=ts,
                    metadata={"kind": "linked_eol", "source": source},
                )
            ]
    return []


def _family_from_model(payload: dict[str, Any]) -> str:
    model = str(payload.get("meta", {}).get("productModelName") or payload.get("summary") or "")
    return infer_family(model)
