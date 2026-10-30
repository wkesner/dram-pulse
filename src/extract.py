"""Rule-based signal extraction for Memory Signals (Milestone 2).

Usage:
  export SEC_USER_AGENT="MemorySignals Your Name you@example.com"
  python -m src.extract --ticker MU
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.dedupe import (
    SIGNAL_COLUMNS,
    append_sightings,
    build_signals_from_sightings,
    canonical_language,
    dedupe,
    is_english_lang,
    merge_group,
    normalize as normalize_excerpt,
    window_keep_score,
    write_csv as write_dedupe_csv,
)
from src.fetch_edgar import (
    REPO_ROOT,
    Company,
    RateLimitedSession,
    iter_recent_filings,
    load_companies,
    load_yaml,
    lookup_cik,
    parse_filing_date,
    resolve_user_agent,
    window_bounds,
)

EXTRACTOR_VERSION = "0.3.1"
SIGHTINGS_PATH = REPO_ROOT / "data" / "sightings.csv"

SECTION_FILES = {
    "business": "item1_business.txt",
    "risk_factors": "item1a_risk_factors.txt",
    "mdna": "item7_mdna.txt",
}

# For 8-K / 6-K we extract from full text + any exhibit 99 files.
PERIODIC_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}

# Sentence split: keep abbreviations from shredding too aggressively.
_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z\"'(\[])|(?<=\n)\s*(?=[A-Z][a-z])"
)
# Korean / CJK: split on .!?。 and blank lines (no Latin capital cue).
_SENTENCE_SPLIT_KO = re.compile(r"(?<=[.!?。])\s+|(?:\n\s*)+")
_HANGUL = re.compile(r"[\uac00-\ud7a3]")
_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7a3]")
_WS = re.compile(r"\s+")

# Full-document sources (no Item 1/1A/7 split) — parallel to DART/IR.
FULL_DOC_SOURCES = {
    "DART",
    "IR",
    "CNINFO",
    "EDINET",
    "CH",
    "COMPANIES_HOUSE",
    "MOPS",
    "TWSE",
}


@dataclass(frozen=True)
class TermHit:
    kind: str
    label: str
    term: str
    start: int
    end: int


@dataclass(frozen=True)
class ProductTerm:
    category: str
    family: str
    term: str
    pattern: re.Pattern[str]


def _compile_term(term: str) -> re.Pattern[str]:
    """Case-insensitive word-boundary match; allow flexible whitespace/hyphens/commas."""
    parts = re.split(r"[\s,\-]+", term.strip())
    body = r"[\s,\-]+".join(re.escape(p) for p in parts if p)
    # Alphanumeric boundaries so "DRAM" does not match inside longer tokens,
    # while still allowing "HBM3E" and Korean terms.
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def load_taxonomy(path: Path) -> list[ProductTerm]:
    data = load_yaml(path)
    terms: list[ProductTerm] = []
    for category, families in (data.get("categories") or {}).items():
        for family, term_list in (families or {}).items():
            for term in term_list or []:
                terms.append(
                    ProductTerm(
                        category=category,
                        family=family,
                        term=term,
                        pattern=_compile_term(term),
                    )
                )
    # Prefer longer terms so HBM3E wins over HBM, etc.
    terms.sort(key=lambda t: len(t.term), reverse=True)
    return terms


def load_supply_terms(path: Path) -> dict[str, Any]:
    return load_yaml(path)


def compile_term_map(mapping: dict[str, list[str]]) -> list[tuple[str, str, re.Pattern[str]]]:
    compiled: list[tuple[str, str, re.Pattern[str]]] = []
    for label, terms in mapping.items():
        for term in sorted(terms or [], key=len, reverse=True):
            compiled.append((label, term, _compile_term(term)))
    compiled.sort(key=lambda t: len(t[1]), reverse=True)
    return compiled


def normalize_text(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def _text_is_korean(text: str) -> bool:
    sample = text[:8000]
    if not sample:
        return False
    return len(_HANGUL.findall(sample)) >= max(20, len(sample) // 40)


def _text_is_cjk(text: str) -> bool:
    """True when Hangul / Han / Kana dominate — use CJK sentence splitter."""
    sample = text[:8000]
    if not sample:
        return False
    return len(_CJK.findall(sample)) >= max(20, len(sample) // 40)


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, sentence) spans into `text`."""
    splitter = _SENTENCE_SPLIT_KO if _text_is_cjk(text) else _SENTENCE_SPLIT
    chunks: list[tuple[int, int, str]] = []
    for block in re.finditer(r"\S(?:.|\n)*?(?=\n\s*\n|\Z)", text):
        block_text = block.group(0)
        base = block.start()
        parts = splitter.split(block_text)
        cursor = 0
        for part in parts:
            if not part or not part.strip():
                m = re.match(r"\s*", block_text[cursor:])
                cursor += len(m.group(0)) if m else 0
                continue
            idx = block_text.find(part, cursor)
            if idx < 0:
                idx = cursor
            start = base + idx
            end = start + len(part)
            stripped = part.strip()
            if stripped:
                lead = len(part) - len(part.lstrip())
                trail = len(part) - len(part.rstrip())
                chunks.append((start + lead, end - trail, stripped))
            cursor = idx + len(part)
    if not chunks and text.strip():
        s = text.strip()
        start = text.find(s)
        chunks.append((start, start + len(s), s))
    return chunks


def find_hits(
    text: str,
    compiled: list[tuple[str, str, re.Pattern[str]]],
    kind: str,
) -> list[TermHit]:
    hits: list[TermHit] = []
    occupied: list[tuple[int, int]] = []
    for label, term, pattern in compiled:
        for m in pattern.finditer(text):
            span = (m.start(), m.end())
            if any(not (span[1] <= a or span[0] >= b) for a, b in occupied):
                continue
            occupied.append(span)
            hits.append(TermHit(kind=kind, label=label, term=term, start=m.start(), end=m.end()))
    return hits


def find_product_hits(text: str, products: list[ProductTerm]) -> list[TermHit]:
    hits: list[TermHit] = []
    occupied: list[tuple[int, int]] = []
    for product in products:
        for m in product.pattern.finditer(text):
            span = (m.start(), m.end())
            if any(not (span[1] <= a or span[0] >= b) for a, b in occupied):
                continue
            occupied.append(span)
            hits.append(
                TermHit(
                    kind="product",
                    label=f"{product.category}/{product.family}",
                    term=product.term,
                    start=m.start(),
                    end=m.end(),
                )
            )
    return hits


def first_topic(
    topic_compiled: list[tuple[str, str, re.Pattern[str]]], text: str
) -> TermHit | None:
    # Topic group order is preserved in supply_terms.yaml; within a group longer first.
    # Walk groups in file order: rebuild group order from compiled labels' first appearance.
    group_order: list[str] = []
    for label, _term, _pat in topic_compiled:
        if label not in group_order:
            group_order.append(label)
    for group in group_order:
        for label, term, pattern in topic_compiled:
            if label != group:
                continue
            m = pattern.search(text)
            if m:
                return TermHit(kind="topic", label=label, term=term, start=m.start(), end=m.end())
    return None


def pick_direction(
    text: str,
    product_pos: int,
    tightening: list[tuple[str, str, re.Pattern[str]]],
    easing: list[tuple[str, str, re.Pattern[str]]],
    stable: list[tuple[str, str, re.Pattern[str]]],
) -> tuple[str, bool, list[TermHit]]:
    """Return (supply_direction, direction_conflict, matched direction hits)."""
    t_hits = [
        TermHit("dir", "tightening", term, m.start(), m.end())
        for _label, term, pat in tightening
        for m in pat.finditer(text)
    ]
    e_hits = [
        TermHit("dir", "easing", term, m.start(), m.end())
        for _label, term, pat in easing
        for m in pat.finditer(text)
    ]
    if t_hits and e_hits:
        # Closer to product term wins.
        def dist(h: TermHit) -> int:
            return min(abs(h.start - product_pos), abs(h.end - product_pos))

        best_t = min(t_hits, key=dist)
        best_e = min(e_hits, key=dist)
        if dist(best_t) <= dist(best_e):
            return "tightening", True, [best_t, best_e]
        return "easing", True, [best_e, best_t]
    if t_hits:
        return "tightening", False, [min(t_hits, key=lambda h: h.start)]
    if e_hits:
        return "easing", False, [min(e_hits, key=lambda h: h.start)]
    for _label, term, pat in stable:
        m = pat.search(text)
        if m:
            hit = TermHit("dir", "stable", term, m.start(), m.end())
            return "stable", False, [hit]
    return "unclear", False, []


def pick_sentiment(
    text: str,
    positive: list[tuple[str, str, re.Pattern[str]]],
    negative: list[tuple[str, str, re.Pattern[str]]],
) -> tuple[str, list[TermHit]]:
    p_hits = [
        TermHit("sent", "positive", term, m.start(), m.end())
        for _label, term, pat in positive
        for m in [pat.search(text)]
        if m
    ]
    n_hits = [
        TermHit("sent", "negative", term, m.start(), m.end())
        for _label, term, pat in negative
        for m in [pat.search(text)]
        if m
    ]
    # Deduplicate by term
    if p_hits and n_hits:
        return "mixed", [p_hits[0], n_hits[0]]
    if p_hits:
        return "positive", [p_hits[0]]
    if n_hits:
        return "negative", [n_hits[0]]
    return "mixed", []


def signal_id(source_url: str, char_start: int, char_end: int, product_family: str) -> str:
    # SPEC hashes url+offsets; include family so multi-family passages stay unique.
    payload = f"{source_url}{char_start}{char_end}|{product_family}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def prior_match_id(source_url: str, char_start: int, char_end: int) -> str:
    payload = f"{source_url}{char_start}{char_end}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def passage_windows(
    sentences: list[tuple[int, int, str]], max_n: int = 3
) -> Iterable[tuple[int, int, str, int, int]]:
    """Yield (sent_i, width, text, char_start, char_end) for windows up to max_n."""
    n = len(sentences)
    for i in range(n):
        for width in range(1, max_n + 1):
            if i + width > n:
                break
            span = sentences[i : i + width]
            start = span[0][0]
            end = span[-1][1]
            text = " ".join(s[2] for s in span)
            yield i, width, text, start, end


def find_prior_repeat(
    excerpt: str,
    prior_texts: list[tuple[str, str]],
) -> str:
    """If normalised excerpt appears in a prior filing text, return a synthetic id."""
    needle = normalize_text(excerpt)
    if len(needle) < 40:
        return ""
    for prior_url, prior_body in prior_texts:
        hay = normalize_text(prior_body)
        idx = hay.find(needle)
        if idx < 0:
            # Try a shortened core (first 200 chars of normalised excerpt)
            core = needle[:200]
            idx = hay.find(core)
            if idx < 0:
                continue
            end = idx + len(core)
        else:
            end = idx + len(needle)
        return prior_match_id(prior_url, idx, end)
    return ""


@dataclass
class ExtractContext:
    company: Company
    meta: dict[str, Any]
    products: list[ProductTerm]
    topic_compiled: list[tuple[str, str, re.Pattern[str]]]
    tightening: list[tuple[str, str, re.Pattern[str]]]
    easing: list[tuple[str, str, re.Pattern[str]]]
    stable: list[tuple[str, str, re.Pattern[str]]]
    positive: list[tuple[str, str, re.Pattern[str]]]
    negative: list[tuple[str, str, re.Pattern[str]]]
    prior_texts: list[tuple[str, str]]
    extracted_at: str


def extract_from_section(
    ctx: ExtractContext,
    section: str,
    raw_path: Path,
    text: str,
) -> list[dict[str, Any]]:
    sentences = split_sentences(text)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()

    # Prefer shortest qualifying window per (start_sentence, family).
    best: dict[tuple[int, str], tuple[int, dict[str, Any]]] = {}

    for sent_i, width, passage, char_start, char_end in passage_windows(sentences, 3):
        product_hits = find_product_hits(passage, ctx.products)
        if not product_hits:
            continue
        topic_hit = first_topic(ctx.topic_compiled, passage)
        if topic_hit is None:
            continue

        # Unique families in this passage (first hit per family).
        families: dict[str, TermHit] = {}
        for hit in product_hits:
            _cat, fam = hit.label.split("/", 1)
            if fam not in families:
                families[fam] = hit

        for fam, phit in families.items():
            category = phit.label.split("/", 1)[0]
            direction, conflict, dir_hits = pick_direction(
                passage,
                phit.start,
                ctx.tightening,
                ctx.easing,
                ctx.stable,
            )
            # Gate: require tightening or easing (or stable) direction term.
            if direction == "unclear" or not dir_hits:
                continue

            # Product mention must be near a direction or topic hit so we do not
            # tag a family that only co-occurs in a long 3-sentence window.
            def _near(hit: TermHit, limit: int = 160) -> bool:
                return min(abs(hit.start - phit.start), abs(hit.end - phit.end)) <= limit

            if not any(_near(h) for h in dir_hits) and not _near(topic_hit):
                continue

            # Materials / input shortages are not component-product supply signals.
            if re.search(r"\brare earth\b|\braw materials?\b|\bminerals?\b", passage, re.I):
                if fam in {
                    "Memory_general",
                    "DRAM_general",
                    "NAND_general",
                    "GPU",
                    "CPU",
                    "FPGA",
                    "MCU",
                    "Power_IC",
                    "Capacitor",
                    "Resistor",
                }:
                    continue
            # Legal / IP boilerplate naming "FPGA" / "microcontroller" is not a supply signal.
            if re.search(
                r"\bpatent(s|ed)?\b|\btrademark(s)?\b|\blicens(e|es|ing)\b|\binfringe",
                passage,
                re.I,
            ):
                if fam in {"GPU", "CPU", "FPGA", "MCU", "Power_IC"}:
                    continue
            # Competition-regulator inquiries that mention allocating GPU supply are not
            # commodity-tightness signals; same for debt/credit facility boilerplate.
            if fam in {"GPU", "CPU", "FPGA", "MCU", "Power_IC", "Capacitor", "Resistor"}:
                if re.search(
                    r"competition regulators?|antitrust|requests for information from|"
                    r"regulatory (?:inquir|investigat)",
                    passage,
                    re.I,
                ):
                    continue
                if re.search(
                    r"\b(?:credit facility|term loan|DDTL|indenture|revolving credit|"
                    r"depreciable cost|loans? (?:available|may be drawn)|"
                    r"loans?(?:\s+\w+){0,3}\s+are constrained)\b",
                    passage,
                    re.I,
                ):
                    continue


            # Accounting policy text mentioning write-downs is not a market signal.
            if re.search(
                r"net realizable value|lower of cost|GAAP provides|unit of account",
                passage,
                re.I,
            ):
                continue

            # Direction must be near the product mention (topic alone is not enough).
            if not any(_near(h, 180) for h in dir_hits):
                continue

            sentiment, sent_hits = pick_sentiment(passage, ctx.positive, ctx.negative)

            excerpt = text[char_start:char_end]
            if len(excerpt) > 1200:
                excerpt = excerpt[:1200]
                char_end = char_start + len(excerpt)

            key = (sent_i, fam)
            row = _build_row(
                ctx,
                section=section,
                raw_path=raw_path,
                excerpt=excerpt,
                char_start=char_start,
                char_end=char_end,
                category=category,
                family=fam,
                topic=topic_hit.label,
                direction=direction,
                conflict=conflict,
                sentiment=sentiment,
                product_hit=phit,
                topic_hit=topic_hit,
                dir_hits=dir_hits,
                sent_hits=sent_hits,
            )
            prev = best.get(key)
            if prev is None or width < prev[0]:
                best[key] = (width, row)

    candidates = [row for _width, row in best.values()]
    # Collapse heavily overlapping windows within a merge group (Memory together;
    # Compute/Analog/Passives only with themselves) so DRAM+NAND+Memory_general
    # stay one card while a co-listed Capacitor/MCU still surfaces.
    candidates.sort(
        key=lambda r: (
            int(r["char_start"]),
            int(r["char_end"]),
            r["product_family"],
        )
    )
    kept: list[dict[str, Any]] = []
    for row in candidates:
        start, end = int(row["char_start"]), int(row["char_end"])
        direction = row.get("supply_direction") or ""
        row_group = merge_group(str(row.get("product_category") or ""))
        overlap_idx = None
        for i, prev in enumerate(kept):
            if (prev.get("supply_direction") or "") != direction:
                continue
            if merge_group(str(prev.get("product_category") or "")) != row_group:
                continue
            ps, pe = int(prev["char_start"]), int(prev["char_end"])
            overlap = max(0, min(end, pe) - max(start, ps))
            span = max(end - start, pe - ps, 1)
            shorter = max(min(end - start, pe - ps), 1)
            # Heavy overlap OR almost-contained nested window of the same claim.
            if overlap / span >= 0.55 or overlap / shorter >= 0.85:
                overlap_idx = i
                break
        if overlap_idx is None:
            kept.append(row)
            continue
        prev = kept[overlap_idx]
        if window_keep_score(row) > window_keep_score(prev):
            kept[overlap_idx] = row

    for row in kept:
        dedupe_key = (int(row["char_start"]), int(row["char_end"]), row["product_family"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append(row)
    return rows


def _build_row(
    ctx: ExtractContext,
    *,
    section: str,
    raw_path: Path,
    excerpt: str,
    char_start: int,
    char_end: int,
    category: str,
    family: str,
    topic: str,
    direction: str,
    conflict: bool,
    sentiment: str,
    product_hit: TermHit,
    topic_hit: TermHit,
    dir_hits: list[TermHit],
    sent_hits: list[TermHit],
) -> dict[str, Any]:
    meta = ctx.meta
    source_url = meta["source_url"]
    raw_abs = raw_path if raw_path.is_absolute() else (REPO_ROOT / raw_path)
    rel_raw = str(raw_abs.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    # Candidate id (pre-dedupe); final signal_id comes from src.dedupe.
    sid = signal_id(source_url, char_start, char_end, family)
    matched = [
        f"product:{product_hit.term}",
        f"topic:{topic_hit.term}",
    ]
    if dir_hits:
        matched.append(f"dir:{dir_hits[0].term}")
    if sent_hits:
        matched.append(f"sent:{sent_hits[0].term}")
    language = str(meta.get("language") or "").strip().lower()
    if not language:
        src_u = str(meta.get("source") or ctx.company.source).upper()
        language = {
            "DART": "ko",
            "CNINFO": "zh",
            "MOPS": "zh-TW",
            "TWSE": "zh-TW",
            "EDINET": "ja",
        }.get(src_u, "en")
    source = str(meta.get("source") or ctx.company.source)
    filing_id = str(
        meta.get("filing_id") or meta.get("accession_number") or meta.get("rcept_no") or ""
    )
    return {
        "signal_id": sid,
        "company": meta.get("company") or ctx.company.name,
        "ticker_or_code": meta.get("ticker") or ctx.company.ticker,
        "role": meta.get("role") or ctx.company.role,
        "tier": str(meta.get("tier") or getattr(ctx.company, "tier", "") or ""),
        "source": source,
        "form": meta.get("form", "10-K"),
        "filing_date": meta.get("filing_date", ""),
        "filing_id": filing_id,
        "section": section,
        "product_category": category,
        "product_family": family,
        "topic": topic,
        "supply_direction": direction,
        "sentiment": sentiment,
        "direction_conflict": "true" if conflict else "false",
        "matched_terms": "; ".join(matched),
        "excerpt": excerpt.replace("\n", " ").strip(),
        "language": language,
        "char_start": str(char_start),
        "char_end": str(char_end),
        "raw_path": rel_raw,
        "source_url": source_url,
        "period_end": meta.get("period_end") or meta.get("report_date") or "",
        "lookback_months": str(meta.get("lookback_months", "")),
        "window_start": meta.get("window_start", ""),
        "window_end": meta.get("window_end", ""),
        "extractor_version": EXTRACTOR_VERSION,
        "extracted_at": ctx.extracted_at,
    }


def list_metas_for_ticker(
    raw_root: Path, ticker: str, *, include_prior: bool = False
) -> list[tuple[Path, dict[str, Any]]]:
    ticker_dir = raw_root / ticker.upper()
    if not ticker_dir.is_dir():
        return []
    metas: list[tuple[Path, dict[str, Any]]] = []
    for child in ticker_dir.iterdir():
        meta_path = child / "meta.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("prior_only") and not include_prior:
            continue
        metas.append((child, meta))
    metas.sort(
        key=lambda m: (m[1].get("filing_date", ""), m[1].get("accession_number", "")),
        reverse=True,
    )
    return metas


def latest_meta_for_ticker(raw_root: Path, ticker: str) -> tuple[Path, dict[str, Any]]:
    pool = list_metas_for_ticker(raw_root, ticker)
    if not pool:
        raise FileNotFoundError(f"No cached filings under {raw_root / ticker}")
    return pool[0]


def latest_10k_meta(
    raw_root: Path, ticker: str
) -> tuple[Path, dict[str, Any]] | None:
    for path, meta in list_metas_for_ticker(raw_root, ticker):
        if str(meta.get("form", "")).upper() in {"10-K", "10-K/A"}:
            return path, meta
    return None


def fetch_prior_10k_if_needed(
    company: Company,
    settings: dict[str, Any],
    current_meta: dict[str, Any],
    session: RateLimitedSession,
) -> dict[str, Any] | None:
    """Fetch the previous 10-K for repeat_of comparison only (no signal extraction)."""
    edgar = settings["edgar"]
    raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")
    cik = current_meta.get("cik") or lookup_cik(
        session, edgar["company_tickers_url"], company.ticker
    )
    submissions_url = edgar["submissions_url_template"].format(cik=int(cik))
    submissions = session.get(submissions_url).json()
    filings = [
        row
        for row in iter_recent_filings(submissions)
        if row["form"] in ("10-K", "10-K/A")
    ]
    filings.sort(key=lambda r: r["filingDate"], reverse=True)
    current_acc = current_meta["accession_number"]
    prior = None
    for row in filings:
        if row["accessionNumber"] == current_acc:
            continue
        if parse_filing_date(row["filingDate"]) < parse_filing_date(
            current_meta["filing_date"]
        ):
            prior = row
            break
    if prior is None:
        return None

    # Reuse fetch_company_10k machinery by temporarily selecting that accession:
    # call lower-level cache write via a one-off fetch.
    from src.fetch_edgar import archive_url, cache_dir, write_sections
    from src.sections import split_filing

    url = archive_url(
        cik,
        prior["accessionNumber"],
        prior["primaryDocument"],
        edgar["archives_url_template"],
    )
    dest = cache_dir(raw_root, company.ticker, prior["accessionNumber"])
    meta_path = dest / "meta.json"
    if meta_path.is_file() and (dest / "other_full.txt").is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["prior_only"] = True
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return meta

    print(f"Fetching prior 10-K {prior['accessionNumber']} for repeat_of only…")
    html = session.get(url).text
    sections = split_filing(html)
    paths = write_sections(dest, html, sections)
    lookback = int(settings["lookback_months"])
    # Window fields on prior meta are informational only.
    w_start, w_end = window_bounds(lookback)
    meta = {
        "company": company.name,
        "ticker": company.ticker,
        "cik": cik if isinstance(cik, str) else f"{int(cik):010d}",
        "form": prior["form"],
        "filing_date": prior["filingDate"],
        "accession_number": prior["accessionNumber"],
        "primary_document": prior["primaryDocument"],
        "source_url": url,
        "lookback_months": lookback,
        "window_start": w_start.isoformat(),
        "window_end": w_end.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "prior_only": True,
        "sections_present": sorted(k for k in sections) ,
        "paths": {k: str(v.relative_to(REPO_ROOT)) for k, v in paths.items()},
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def _merge_term_lists(*groups: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for term in group or []:
            if term not in seen:
                seen.add(term)
                out.append(term)
    return out


def _load_term_banks(
    taxonomy_path: Path, supply_path: Path
) -> dict[str, Any]:
    products = load_taxonomy(taxonomy_path)
    supply = load_supply_terms(supply_path)
    ko = supply.get("korean") or {}
    zh = supply.get("chinese") or {}
    ja = supply.get("japanese") or {}
    ko_topics = ko.get("topics") or {}
    zh_topics = zh.get("topics") or {}
    ja_topics = ja.get("topics") or {}
    ko_dir = ko.get("direction") or {}
    zh_dir = zh.get("direction") or {}
    ja_dir = ja.get("direction") or {}
    ko_sent = ko.get("sentiment") or {}
    zh_sent = zh.get("sentiment") or {}
    ja_sent = ja.get("sentiment") or {}

    topics: dict[str, list[str]] = {}
    for label, terms in (supply.get("topics") or {}).items():
        merged = _merge_term_lists(terms, ko_topics.get(label))
        merged = _merge_term_lists(merged, zh_topics.get(label))
        merged = _merge_term_lists(merged, ja_topics.get(label))
        topics[label] = merged
    for extra_topics in (ko_topics, zh_topics, ja_topics):
        for label, terms in extra_topics.items():
            if label not in topics:
                topics[label] = list(terms or [])

    direction = supply.get("direction") or {}
    sentiment = supply.get("sentiment") or {}
    return {
        "products": products,
        "topic_compiled": compile_term_map(topics),
        "tightening": compile_term_map(
            {
                "tightening": _merge_term_lists(
                    _merge_term_lists(
                        _merge_term_lists(
                            direction.get("tightening"), ko_dir.get("tightening")
                        ),
                        zh_dir.get("tightening"),
                    ),
                    ja_dir.get("tightening"),
                )
            }
        ),
        "easing": compile_term_map(
            {
                "easing": _merge_term_lists(
                    _merge_term_lists(
                        _merge_term_lists(
                            direction.get("easing"), ko_dir.get("easing")
                        ),
                        zh_dir.get("easing"),
                    ),
                    ja_dir.get("easing"),
                )
            }
        ),
        "stable": compile_term_map(
            {
                "stable": _merge_term_lists(
                    _merge_term_lists(
                        _merge_term_lists(
                            direction.get("stable"), ko_dir.get("stable")
                        ),
                        zh_dir.get("stable"),
                    ),
                    ja_dir.get("stable"),
                )
            }
        ),
        "positive": compile_term_map(
            {
                "positive": _merge_term_lists(
                    _merge_term_lists(
                        _merge_term_lists(
                            sentiment.get("positive"), ko_sent.get("positive")
                        ),
                        zh_sent.get("positive"),
                    ),
                    ja_sent.get("positive"),
                )
            }
        ),
        "negative": compile_term_map(
            {
                "negative": _merge_term_lists(
                    _merge_term_lists(
                        _merge_term_lists(
                            sentiment.get("negative"), ko_sent.get("negative")
                        ),
                        zh_sent.get("negative"),
                    ),
                    ja_sent.get("negative"),
                )
            }
        ),
    }


def _prior_texts_for_company(
    company: Company,
    settings: dict[str, Any],
    raw_root: Path,
    *,
    fetch_prior: bool,
    session: RateLimitedSession | None,
) -> list[tuple[str, str]]:
    prior_texts: list[tuple[str, str]] = []
    tenk = latest_10k_meta(raw_root, company.ticker)
    if tenk is None:
        return prior_texts
    _path, meta = tenk
    if fetch_prior:
        if session is None:
            ua = resolve_user_agent()
            max_rps = float(settings.get("edgar", {}).get("max_requests_per_second", 10))
            session = RateLimitedSession(ua, max_rps=min(max_rps, 8.0))
        prior_meta = fetch_prior_10k_if_needed(company, settings, meta, session)
    else:
        # Use any already-cached prior_only 10-K
        priors = [
            m
            for m in list_metas_for_ticker(raw_root, company.ticker, include_prior=True)
            if m[1].get("prior_only")
        ]
        prior_meta = priors[0][1] if priors else None

    if not prior_meta:
        return prior_texts
    prior_dir = raw_root / company.ticker / prior_meta["accession_number"].replace("-", "")
    for fname in SECTION_FILES.values():
        p = prior_dir / fname
        if p.is_file():
            prior_texts.append((prior_meta["source_url"], p.read_text(encoding="utf-8")))
    other = prior_dir / "other_full.txt"
    if other.is_file():
        prior_texts.append((prior_meta["source_url"], other.read_text(encoding="utf-8")))
    return prior_texts


def extract_filing(
    company: Company,
    cache_dir_path: Path,
    meta: dict[str, Any],
    banks: dict[str, Any],
    prior_texts: list[tuple[str, str]],
    extracted_at: str,
) -> list[dict[str, Any]]:
    ctx = ExtractContext(
        company=company,
        meta=meta,
        products=banks["products"],
        topic_compiled=banks["topic_compiled"],
        tightening=banks["tightening"],
        easing=banks["easing"],
        stable=banks["stable"],
        positive=banks["positive"],
        negative=banks["negative"],
        prior_texts=prior_texts,
        extracted_at=extracted_at,
    )
    rows: list[dict[str, Any]] = []
    form = str(meta.get("form", "")).upper()
    source = str(meta.get("source") or ctx.company.source).upper()

    # DART / IR / CNINFO / EDINET / CH: full-document text only (no Item split).
    if source in FULL_DOC_SOURCES or form in {"IR-PDF", "DART", "CNINFO", "EDINET"}:
        other = cache_dir_path / "other_full.txt"
        if other.is_file():
            rows.extend(
                extract_from_section(
                    ctx, "other", other, other.read_text(encoding="utf-8")
                )
            )
        return rows

    if form in PERIODIC_FORMS:
        for section, fname in SECTION_FILES.items():
            path = cache_dir_path / fname
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            rows.extend(extract_from_section(ctx, section, path, text))
    else:
        # 8-K / 6-K / other: full text + exhibit 99s
        other = cache_dir_path / "other_full.txt"
        if other.is_file():
            rows.extend(
                extract_from_section(
                    ctx, "other", other, other.read_text(encoding="utf-8")
                )
            )
        for path in sorted(cache_dir_path.glob("exhibit_99_*.txt")):
            rows.extend(
                extract_from_section(
                    ctx, "exhibit_99", path, path.read_text(encoding="utf-8")
                )
            )
    return rows


def _company_for_ticker(companies_path: Path, ticker: str) -> Company:
    """Prefer EDGAR entry when multiple sources share a ticker; else first match."""
    want = ticker.upper()
    matches = [c for c in load_companies(companies_path) if c.ticker == want]
    if not matches:
        raise SystemExit(f"Ticker {ticker} not in {companies_path}")
    for preferred in (
        "EDGAR",
        "DART",
        "IR",
        "CNINFO",
        "MOPS",
        "TWSE",
        "EDINET",
        "CH",
        "COMPANIES_HOUSE",
    ):
        for c in matches:
            if c.source.upper() == preferred:
                return c
    return matches[0]


def extract_ticker(
    ticker: str,
    *,
    settings: dict[str, Any],
    companies_path: Path,
    taxonomy_path: Path,
    supply_path: Path,
    out_path: Path | None = None,
    fetch_prior: bool = True,
    write: bool = True,
    session: RateLimitedSession | None = None,
) -> list[dict[str, Any]]:
    company = _company_for_ticker(companies_path, ticker)
    raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")
    filings = list_metas_for_ticker(raw_root, ticker)
    if not filings:
        print(f"Warning: no cached filings for {ticker}", file=sys.stderr)
        return []

    banks = _load_term_banks(taxonomy_path, supply_path)
    # Prior-year 10-K repeat detection is EDGAR-only.
    prior_texts: list[tuple[str, str]] = []
    if company.source.upper() == "EDGAR":
        prior_texts = _prior_texts_for_company(
            company, settings, raw_root, fetch_prior=fetch_prior, session=session
        )
    extracted_at = datetime.now(timezone.utc).isoformat()

    rows: list[dict[str, Any]] = []
    for cache_dir_path, meta in filings:
        rows.extend(
            extract_filing(
                company, cache_dir_path, meta, banks, prior_texts, extracted_at
            )
        )

    section_order = {
        "business": 0,
        "risk_factors": 1,
        "mdna": 2,
        "exhibit_99": 3,
        "other": 4,
    }
    rows.sort(
        key=lambda r: (
            r.get("filing_date", ""),
            section_order.get(r["section"], 9),
            int(r["char_start"]),
            r["product_family"],
        ),
        reverse=True,
    )

    if write and out_path is not None:
        write_signals_csv(out_path, rows, append=False)
    return rows


def read_signals_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_signals_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Rewrite signals.csv from deduped signal rows (not append-only)."""
    write_dedupe_csv(path, rows, SIGNAL_COLUMNS)


def prior_year_norms_by_company(
    companies: list[Company],
    settings: dict[str, Any],
    raw_root: Path,
    *,
    fetch_prior: bool,
    session: RateLimitedSession | None,
) -> dict[str, list[str]]:
    """Normalized prior-year 10-K bodies per company (for carried_from_prior_year)."""
    out: dict[str, list[str]] = {}
    for company in companies:
        texts = _prior_texts_for_company(
            company, settings, raw_root, fetch_prior=fetch_prior, session=session
        )
        bodies: list[str] = []
        for _url, body in texts:
            norm_body = normalize_excerpt(body)
            if norm_body:
                bodies.append(norm_body)
        if bodies:
            out[company.name] = bodies
    return out


def backfill_period_ends(
    companies: list[Company],
    settings: dict[str, Any],
    session: RateLimitedSession,
) -> None:
    """Ensure every cached meta.json has period_end from EDGAR reportDate."""
    edgar = settings["edgar"]
    raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")
    for company in companies:
        try:
            cik = lookup_cik(session, edgar["company_tickers_url"], company.ticker)
            submissions_url = edgar["submissions_url_template"].format(cik=int(cik))
            submissions = session.get(submissions_url).json()
        except LookupError as exc:
            print(f"  skip period_end backfill {company.ticker}: {exc}", file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001 — SEC outage / 403
            print(f"  skip period_end backfill {company.ticker}: {exc}", file=sys.stderr)
            continue
        by_acc = {
            row["accessionNumber"]: row.get("reportDate") or ""
            for row in iter_recent_filings(submissions)
        }
        ticker_dir = raw_root / company.ticker
        if not ticker_dir.is_dir():
            continue
        for child in ticker_dir.iterdir():
            meta_path = child / "meta.json"
            if not meta_path.is_file():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            acc = meta.get("accession_number") or ""
            report = by_acc.get(acc) or ""
            if report and meta.get("period_end") != report:
                meta["period_end"] = report
                meta["report_date"] = report
                meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def _prior_excerpt_en_by_id(signals_path: Path) -> dict[str, str]:
    """Keep prior English translations across extract rebuilds (stable signal_id)."""
    if not signals_path.is_file() or signals_path.stat().st_size == 0:
        return {}
    out: dict[str, str] = {}
    with signals_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            sid = (row.get("signal_id") or "").strip()
            en = (row.get("excerpt_en") or "").strip()
            if sid and en:
                out[sid] = en
    return out


def _prior_excerpt_en_by_excerpt(*csv_paths: Path) -> dict[str, str]:
    """Map normalized non-English excerpt → prior excerpt_en (MT is non-deterministic)."""
    out: dict[str, str] = {}
    for path in csv_paths:
        if not path or not path.is_file() or path.stat().st_size == 0:
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if is_english_lang(str(row.get("language") or "en")):
                    continue
                en = (row.get("excerpt_en") or "").strip()
                ex = (row.get("excerpt") or "").strip()
                if not en or not ex:
                    continue
                key = normalize_excerpt(ex)
                if key and key not in out:
                    out[key] = en
    return out


def _merge_excerpt_en(
    signals: list[dict[str, Any]], prior: dict[str, str]
) -> list[dict[str, Any]]:
    for row in signals:
        if is_english_lang(str(row.get("language") or "en")):
            row["excerpt_en"] = ""
            continue
        sid = row.get("signal_id") or ""
        existing = (row.get("excerpt_en") or "").strip()
        if not existing and sid in prior:
            row["excerpt_en"] = prior[sid]
        elif "excerpt_en" not in row:
            row["excerpt_en"] = ""
    return signals


def _enrich_candidate_excerpt_en(
    candidates: list[dict[str, Any]],
    *,
    prior_by_excerpt: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Attach excerpt_en on non-English candidates so cross-language dedupe can match.

    Prefer a prior translation for the same original excerpt so rebuilds stay
    deterministic (Argos output drifts enough to change near-merge IDs).
    Auto-translate is wired for Korean today; ZH/JA/DE reuse prior excerpt_en.
    """
    prior_by_excerpt = prior_by_excerpt or {}
    need_ko: list[dict[str, Any]] = []
    for c in candidates:
        if is_english_lang(str(c.get("language") or "en")):
            continue
        if (c.get("excerpt_en") or "").strip():
            continue
        ex = (c.get("excerpt") or "").strip()
        if not ex:
            continue
        cached = prior_by_excerpt.get(normalize_excerpt(ex), "")
        if cached:
            c["excerpt_en"] = cached
            continue
        if canonical_language(str(c.get("language") or "")) == "ko":
            need_ko.append(c)
    if not need_ko:
        return candidates
    try:
        from src.translate_excerpts import _ensure_argos_ko_en, translate_text
    except Exception:
        return candidates
    try:
        _ensure_argos_ko_en(install=True)
    except SystemExit:
        return candidates
    # Deduplicate Argos calls by normalized excerpt within this batch.
    batch: dict[str, str] = {}
    for c in need_ko:
        key = normalize_excerpt(c.get("excerpt") or "")
        if key in batch:
            c["excerpt_en"] = batch[key]
            continue
        try:
            en = translate_text(c.get("excerpt") or "", source="ko", target="en")
            batch[key] = en
            c["excerpt_en"] = en
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: excerpt_en enrich failed: {exc}", file=sys.stderr)
    return candidates


def finalize_candidates(
    candidates: list[dict[str, Any]],
    *,
    signals_path: Path,
    sightings_path: Path,
    prior_year_norms: dict[str, set[str]],
    rebuild_sightings: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run dedupe, write sightings (append-only) and rebuild signals.csv."""
    prior_en = _prior_excerpt_en_by_id(signals_path)
    prior_by_excerpt = _prior_excerpt_en_by_excerpt(signals_path, sightings_path)
    # Cross-language near-match compares excerpt_en to English excerpts.
    candidates = _enrich_candidate_excerpt_en(
        list(candidates), prior_by_excerpt=prior_by_excerpt
    )
    signals, sightings = dedupe(candidates, prior_year_norms=prior_year_norms)

    if rebuild_sightings:
        # Fresh extract rebuilds sightings file from this run's candidates so
        # IDs stay consistent with the just-produced signals. Idempotent re-runs
        # that only append use append_sightings instead.
        if sightings_path.is_file():
            sightings_path.unlink()
        append_sightings(sightings_path, sightings)
        # Rebuild signals from the written sightings file.
        with sightings_path.open(encoding="utf-8", newline="") as fh:
            all_sightings = list(csv.DictReader(fh))
        signals = build_signals_from_sightings(
            all_sightings, prior_year_norms=prior_year_norms
        )
    else:
        append_sightings(sightings_path, sightings)
        with sightings_path.open(encoding="utf-8", newline="") as fh:
            all_sightings = list(csv.DictReader(fh))
        signals = build_signals_from_sightings(
            all_sightings, prior_year_norms=prior_year_norms
        )

    signals = _merge_excerpt_en(signals, prior_en)
    write_signals_csv(signals_path, signals)
    return signals, sightings


def extract_all(
    *,
    settings: dict[str, Any],
    companies_path: Path,
    taxonomy_path: Path,
    supply_path: Path,
    out_path: Path,
    sightings_path: Path = SIGHTINGS_PATH,
    fetch_prior: bool = True,
) -> list[dict[str, Any]]:
    all_companies = load_companies(companies_path)
    edgar_companies = [c for c in all_companies if c.source.upper() == "EDGAR"]
    # Unique tickers across EDGAR / DART / IR (Samsung DART+IR share 005930).
    tickers: list[str] = []
    seen: set[str] = set()
    for c in all_companies:
        if c.ticker not in seen:
            seen.add(c.ticker)
            tickers.append(c.ticker)

    ua = resolve_user_agent()
    max_rps = float(settings.get("edgar", {}).get("max_requests_per_second", 10))
    session = RateLimitedSession(ua, max_rps=min(max_rps, 8.0))
    raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")

    print("Backfilling period_end (EDGAR reportDate)…")
    try:
        backfill_period_ends(edgar_companies, settings, session)
    except Exception as exc:  # noqa: BLE001 — offline / SEC 403: use cached metas
        print(f"  period_end backfill skipped: {exc}", file=sys.stderr)

    try:
        prior_norms = prior_year_norms_by_company(
            edgar_companies,
            settings,
            raw_root,
            fetch_prior=fetch_prior,
            session=session,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  prior-year norms skipped: {exc}", file=sys.stderr)
        prior_norms = prior_year_norms_by_company(
            edgar_companies,
            settings,
            raw_root,
            fetch_prior=False,
            session=None,
        )

    all_candidates: list[dict[str, Any]] = []
    for ticker in tickers:
        rows = extract_ticker(
            ticker,
            settings=settings,
            companies_path=companies_path,
            taxonomy_path=taxonomy_path,
            supply_path=supply_path,
            out_path=None,
            fetch_prior=False,  # already gathered prior norms above
            write=False,
            session=session,
        )
        print(f"  {ticker}: {len(rows)} candidate rows")
        all_candidates.extend(rows)

    signals, sightings = finalize_candidates(
        all_candidates,
        signals_path=out_path,
        sightings_path=sightings_path,
        prior_year_norms=prior_norms,
        rebuild_sightings=True,
    )
    print(
        f"dedupe: {len(all_candidates)} candidates → "
        f"{len(sightings)} sightings → {len(signals)} signals"
    )
    return signals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract Memory Signals from cached filings")
    parser.add_argument("--ticker", help="Single ticker (default: all EDGAR companies)")
    parser.add_argument("--all", action="store_true", help="Extract all EDGAR companies")
    parser.add_argument("--settings", type=Path, default=REPO_ROOT / "config" / "settings.yaml")
    parser.add_argument("--companies", type=Path, default=REPO_ROOT / "config" / "companies.yaml")
    parser.add_argument(
        "--taxonomy", type=Path, default=REPO_ROOT / "config" / "product_taxonomy.yaml"
    )
    parser.add_argument(
        "--supply-terms", type=Path, default=REPO_ROOT / "config" / "supply_terms.yaml"
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "signals.csv")
    parser.add_argument(
        "--sightings", type=Path, default=SIGHTINGS_PATH, help="Append-only sightings CSV"
    )
    parser.add_argument(
        "--no-fetch-prior",
        action="store_true",
        help="Skip fetching the prior-year 10-K for carried_from_prior_year",
    )
    args = parser.parse_args(argv)

    settings = load_yaml(args.settings)
    # Always run the full EDGAR set through dedupe so signals.csv is consistent.
    # --ticker still supported as a filter on candidates, but we rebuild globally.
    if args.ticker and not args.all:
        print(
            "Note: single-ticker extract still rebuilds signals.csv via dedupe "
            "for that ticker's candidates only.",
            file=sys.stderr,
        )
        companies = [
            c
            for c in load_companies(args.companies)
            if c.source.upper() == "EDGAR" and c.ticker == args.ticker.upper()
        ]
        if not companies:
            raise SystemExit(f"No EDGAR company {args.ticker}")
        ua = resolve_user_agent()
        max_rps = float(settings.get("edgar", {}).get("max_requests_per_second", 10))
        session = RateLimitedSession(ua, max_rps=min(max_rps, 8.0))
        raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")
        backfill_period_ends(companies, settings, session)
        prior_norms = prior_year_norms_by_company(
            companies,
            settings,
            raw_root,
            fetch_prior=not args.no_fetch_prior,
            session=session,
        )
        candidates = extract_ticker(
            args.ticker.upper(),
            settings=settings,
            companies_path=args.companies,
            taxonomy_path=args.taxonomy,
            supply_path=args.supply_terms,
            write=False,
            fetch_prior=False,
            session=session,
        )
        signals, _sightings = finalize_candidates(
            candidates,
            signals_path=args.out,
            sightings_path=args.sightings,
            prior_year_norms=prior_norms,
            rebuild_sightings=False,
        )
    else:
        signals = extract_all(
            settings=settings,
            companies_path=args.companies,
            taxonomy_path=args.taxonomy,
            supply_path=args.supply_terms,
            out_path=args.out,
            sightings_path=args.sightings,
            fetch_prior=not args.no_fetch_prior,
        )
    print(f"Wrote {len(signals)} signals → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
