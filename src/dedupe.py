"""De-dupe candidate extraction rows into stable signals + sightings.

See DEDUPE.md. Pure functions — no network I/O.

Usage (fixture counts):
  python -m src.dedupe fixtures/signals_v010.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

NEAR_JACCARD = 0.85
# Cross-source (e.g. DART ko via excerpt_en vs EDGAR en) is paraphrased / MT-padded.
# Use max(full, sentence) Jaccard; 0.30 catches real SK hynix pairs (~0.33 sent)
# while in-window false pairs stay ~0.10–0.18.
CROSS_SOURCE_NEAR_JACCARD = 0.30
CROSS_SOURCE_DATE_WINDOW_DAYS = 7
_NUM_RE = re.compile(r"\d[\d,.]*%?")
_NON_ALNUM_RUN = re.compile(r"[^a-z0-9]+", re.IGNORECASE)
_SENT_SPLIT = re.compile(r"[.!?]+")
_SOURCE_RANK = {"EDGAR": 0, "IR": 1, "DART": 2}

# When the same passage matches several families (e.g. DRAM + NAND + Memory_general),
# keep one signal and prefer a more specific family for the displayed card.
_FAMILY_SPECIFICITY = {
    "Memory_general": 0,
    "DRAM_general": 1,
    "NAND_general": 1,
    "DDR4": 2,
    "DDR5": 2,
    "LPDDR": 2,
    "GDDR": 2,
    "HBM": 3,
    "Data center SSD": 2,
    "Client SSD": 2,
    "Mobile/embedded": 2,
    "CXL": 2,
    "AI_accelerators": 2,
    "Packaging": 2,
    # Other electronic commodities (prefer named family over bare general).
    "GPU": 2,
    "CPU": 2,
    "FPGA": 2,
    "MCU": 2,
    "Power_IC": 2,
    "Capacitor": 2,
    "Resistor": 2,
}

# Curly / typographic quotes → straight.
_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2032": "'",
        "\u2033": '"',
    }
)

SIGHTING_COLUMNS = [
    "sighting_id",
    "signal_id",
    "old_signal_id",
    "filing_id",
    "form",
    "filing_date",
    "section",
    "period_end",
    "char_start",
    "char_end",
    "raw_path",
    "source_url",
    "match",
    "similarity",
    "company",
    "product_family",
    "product_category",
    "supply_direction",
    "sentiment",
    "direction_conflict",
    "matched_terms",
    "excerpt",
    "language",
    "excerpt_en",
    "ticker_or_code",
    "role",
    "tier",
    "source",
    "lookback_months",
    "window_start",
    "window_end",
    "extractor_version",
    "extracted_at",
]

SIGNAL_COLUMNS = [
    "signal_id",
    "company",
    "ticker_or_code",
    "role",
    "tier",
    "source",
    "form",
    "filing_date",
    "filing_id",
    "section",
    "product_category",
    "product_family",
    "topic",
    "supply_direction",
    "sentiment",
    "direction_conflict",
    "matched_terms",
    "excerpt",
    "language",
    "excerpt_en",
    "char_start",
    "char_end",
    "raw_path",
    "source_url",
    "first_seen",
    "last_seen",
    "latest_period_end",
    "times_seen",
    "carried_from_prior_year",
    "lookback_months",
    "window_start",
    "window_end",
    "extractor_version",
    "extracted_at",
]


def normalize(text: str) -> str:
    """NFKC, lower, straight quotes, non-alnum runs → space, trim."""
    s = unicodedata.normalize("NFKC", text or "")
    s = s.translate(_QUOTE_MAP)
    s = s.lower()
    s = _NON_ALNUM_RUN.sub(" ", s)
    return s.strip()


# Nested windows of the same English claim (short Memory_general inside a longer
# DRAM/NAND span) should exact-merge even when char offsets differ across filings.
_CONTAINMENT_MIN_CHARS = 80


def is_match_text_contained(a: str, b: str, *, min_chars: int = _CONTAINMENT_MIN_CHARS) -> bool:
    """True when one normalized match_text is a substantial substring of the other."""
    na, nb = normalize(a), normalize(b)
    if len(na) < min_chars or len(nb) < min_chars:
        return False
    return na in nb or nb in na


def is_english_lang(lang: str) -> bool:
    return (lang or "en").strip().lower() in {"", "en", "english"}


def match_text(row: dict[str, Any]) -> str:
    """Text used for exact/near compare. Prefer excerpt_en for non-English rows."""
    lang = row.get("language") or "en"
    en = (row.get("excerpt_en") or "").strip()
    if not is_english_lang(str(lang)) and en:
        return en
    return row.get("excerpt") or ""


def source_key(row: dict[str, Any]) -> str:
    return (row.get("source") or "").strip().upper()


def is_cross_source(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if source_key(a) != source_key(b):
        return True
    la = (a.get("language") or "en").strip().lower()
    lb = (b.get("language") or "en").strip().lower()
    return is_english_lang(la) != is_english_lang(lb)


def _parse_filing_date(value: str):
    from datetime import date

    raw = (value or "").strip()[:10]
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def within_filing_date_window(
    a: dict[str, Any], b: dict[str, Any], *, days: int = CROSS_SOURCE_DATE_WINDOW_DAYS
) -> bool:
    da = _parse_filing_date(str(a.get("filing_date") or ""))
    db = _parse_filing_date(str(b.get("filing_date") or ""))
    if da is None or db is None:
        return False
    return abs((da - db).days) <= days


def word_set(text: str) -> set[str]:
    norm = normalize(text)
    return {w for w in norm.split() if w}


def jaccard(a: str, b: str) -> float:
    sa, sb = word_set(a), word_set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text or "") if p.strip()]
    # Keep short fragments only when that is all we have.
    longish = [p for p in parts if len(word_set(p)) >= 4]
    return longish or parts or ([text] if (text or "").strip() else [])


def max_sentence_jaccard(a: str, b: str) -> float:
    """Best pairwise sentence Jaccard — tolerates MT padding around the claim."""
    sa, sb = _sentences(a), _sentences(b)
    if not sa or not sb:
        return 0.0
    best = 0.0
    for x in sa:
        for y in sb:
            sim = jaccard(x, y)
            if sim > best:
                best = sim
    return best


def cross_source_similarity(a: str, b: str) -> float:
    """Full-text Jaccard or best sentence Jaccard, whichever is higher."""
    return max(jaccard(a, b), max_sentence_jaccard(a, b))


def extract_numbers(text: str) -> list[str]:
    return _NUM_RE.findall(text or "")


def numbers_identical(a: str, b: str) -> bool:
    return extract_numbers(a) == extract_numbers(b)


def stable_signal_id(company: str, product_family: str, excerpt: str) -> str:
    payload = f"{company}|{product_family}|{normalize(excerpt)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def sighting_id(
    filing_id: str, char_start: int | str, char_end: int | str, product_family: str
) -> str:
    payload = f"{filing_id}{char_start}{char_end}{product_family}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _matched_term_count(row: dict[str, Any]) -> int:
    mt = (row.get("matched_terms") or "").strip()
    if not mt:
        return 0
    return len([p for p in mt.split(";") if p.strip()])


def family_specificity(family: str) -> int:
    """Higher = more specific product family (Memory_general is the catch-all)."""
    return _FAMILY_SPECIFICITY.get(family or "", 2)


def window_keep_score(row: dict[str, Any]) -> tuple:
    """Prefer richer matches, longer spans, more specific families, earlier start."""
    return (
        _matched_term_count(row),
        int(row["char_end"]) - int(row["char_start"]),
        family_specificity(str(row.get("product_family") or "")),
        -int(row["char_start"]),
        # Stable tie-break so DRAM vs NAND at equal specificity is deterministic.
        str(row.get("product_family") or ""),
    )


def _span(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["char_start"]), int(row["char_end"])


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    as_, ae = _span(a)
    bs, be = _span(b)
    return max(0, min(ae, be) - max(as_, bs)) > 0


def collapse_overlapping_windows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group by raw_path + product_family + supply_direction. Where windows
    overlap, keep one: most matched terms, then longest, then more specific
    family, then earliest. Different directions (or families) overlapping are
    kept in separate groups — cross-family exact merge happens later so each
    candidate remains a sighting under one signal_id.
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("raw_path") or "",
            row.get("product_family") or "",
            row.get("supply_direction") or "",
        )
        groups[key].append(row)

    kept: list[dict[str, Any]] = []
    for _key, group in groups.items():
        group = sorted(group, key=lambda r: (int(r["char_start"]), int(r["char_end"])))
        selected: list[dict[str, Any]] = []
        for row in group:
            conflict_idx = None
            for i, prev in enumerate(selected):
                if _overlaps(prev, row):
                    conflict_idx = i
                    break
            if conflict_idx is None:
                selected.append(row)
                continue
            prev = selected[conflict_idx]
            if window_keep_score(row) > window_keep_score(prev):
                selected[conflict_idx] = row
            # else keep prev
        kept.extend(selected)
    return kept


def dedupe(
    rows: Iterable[dict[str, Any]],
    *,
    near_jaccard: float = NEAR_JACCARD,
    cross_source_near_jaccard: float = CROSS_SOURCE_NEAR_JACCARD,
    prior_year_norms: dict[str, set[str] | list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Pure function: candidate rows → (signals, sightings).

    Exact match on company using `match_text` (excerpt_en for non-English when
    present), including across product_family — the same Silicon Labs shortage
    paragraph must not become three Feed cards (DRAM + NAND + Memory_general).
    Near match stays scoped to (company, product_family) except cross-source
    pairs (DART↔EDGAR/IR), which use a lower Jaccard threshold because
    translations paraphrase. Prefer English EDGAR as the canonical signal row
    at rebuild time (see `canonical_sighting`).

    `prior_year_norms` maps company → normalized prior-year 10-K bodies (or
    chunks) used for carried_from_prior_year. Optional.
    """
    candidates = collapse_overlapping_windows(list(rows))
    # Oldest filing first; within a day prefer EDGAR/IR English so DART attaches.
    # More specific families first so the surviving signal_id / card label is
    # DRAM/HBM rather than Memory_general when the same span was multi-tagged.
    candidates.sort(
        key=lambda r: (
            r.get("filing_date") or "",
            0 if is_english_lang(str(r.get("language") or "en")) else 1,
            _SOURCE_RANK.get(source_key(r), 9),
            r.get("filing_id") or "",
            int(r.get("char_start") or 0),
            -family_specificity(str(r.get("product_family") or "")),
            r.get("product_family") or "",
        )
    )

    # Per (company, family): open signals for near compare
    open_signals: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    # Flat per-company index for cross-family exact matches
    open_by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sightings: list[dict[str, Any]] = []
    # signal_id → accumulator of sightings / first row
    signal_acc: dict[str, dict[str, Any]] = {}

    def _attach_to_open(chosen_sid: str, excerpt: str, mtext: str, norm: str, row: dict) -> None:
        for sig in open_by_company[row.get("company") or ""]:
            if sig["signal_id"] == chosen_sid:
                sig["excerpts"].append(excerpt)
                sig["match_texts"].append(mtext)
                sig["norms"].add(norm)
                sig["rows"].append(row)
                break

    for row in candidates:
        company = row.get("company") or ""
        family = row.get("product_family") or ""
        excerpt = row.get("excerpt") or ""
        mtext = match_text(row)
        direction = row.get("supply_direction") or ""
        sentiment = row.get("sentiment") or ""
        norm = normalize(mtext)
        key = (company, family)

        match = "first"
        similarity = 1.0
        chosen_sid: str | None = None

        # Exact: same normalized claim already seen for this company (any family).
        # Cross-family nested windows (short Memory_general inside a longer NAND
        # span) also exact-merge; same-family nesting stays on overlap collapse /
        # near match so fixture restatements are not over-merged.
        for sig in open_by_company[company]:
            if sig["direction"] != direction:
                continue
            if norm in sig["norms"] or any(
                normalize(t) == norm for t in sig["match_texts"]
            ):
                match = "exact"
                similarity = 1.0
                chosen_sid = sig["signal_id"]
                break
            sig_family = str((sig.get("first_row") or {}).get("product_family") or "")
            if sig_family != family and any(
                is_match_text_contained(mtext, prev) for prev in sig["match_texts"]
            ):
                match = "exact"
                similarity = 1.0
                chosen_sid = sig["signal_id"]
                break

        # Near (same-source strict within family; cross-source paraphrased)
        if chosen_sid is None:
            best_sim = 0.0
            best_sid = None
            for sig in open_signals[key]:
                if sig["direction"] != direction:
                    continue
                cross = is_cross_source(row, sig["first_row"])
                # Same-source keeps sentiment; cross-source skips it — Argos /
                # keyword labels often disagree (mixed vs positive) on the same claim.
                if not cross and sig["sentiment"] != sentiment:
                    continue
                threshold = cross_source_near_jaccard if cross else near_jaccard
                for prev_text, prev_row in zip(sig["match_texts"], sig["rows"]):
                    if not cross:
                        if not numbers_identical(mtext, prev_text):
                            continue
                        sim = jaccard(mtext, prev_text)
                    else:
                        # Cross-source: proximate filings (SK hynix 6-K vs DART
                        # often a few days apart) + sentence-aware similarity.
                        if not within_filing_date_window(row, prev_row):
                            continue
                        sim = cross_source_similarity(mtext, prev_text)
                    if sim >= threshold and sim > best_sim:
                        best_sim = sim
                        best_sid = sig["signal_id"]
            if best_sid is not None:
                match = "near"
                similarity = best_sim
                chosen_sid = best_sid

        if chosen_sid is None:
            # New signal — ID from first-filing match text (English when present)
            id_excerpt = mtext or excerpt
            chosen_sid = stable_signal_id(company, family, id_excerpt)
            entry = {
                "signal_id": chosen_sid,
                "direction": direction,
                "sentiment": sentiment,
                "excerpts": [excerpt],
                "match_texts": [mtext],
                "norms": {norm},
                "rows": [row],
                "first_row": row,
            }
            open_signals[key].append(entry)
            open_by_company[company].append(entry)
            signal_acc[chosen_sid] = {
                "first_row": dict(row),
                "sightings": [],
            }
        else:
            _attach_to_open(chosen_sid, excerpt, mtext, norm, row)
            if chosen_sid not in signal_acc:
                # Shouldn't happen, but be safe
                signal_acc[chosen_sid] = {"first_row": dict(row), "sightings": []}

        sid = sighting_id(
            row.get("filing_id") or "",
            row.get("char_start") or 0,
            row.get("char_end") or 0,
            family,
        )
        sighting = {
            "sighting_id": sid,
            "signal_id": chosen_sid,
            "old_signal_id": row.get("signal_id") or row.get("old_signal_id") or "",
            "filing_id": row.get("filing_id") or "",
            "form": row.get("form") or "",
            "filing_date": row.get("filing_date") or "",
            "section": row.get("section") or "",
            "period_end": row.get("period_end") or "",
            "char_start": str(row.get("char_start") or ""),
            "char_end": str(row.get("char_end") or ""),
            "raw_path": row.get("raw_path") or "",
            "source_url": row.get("source_url") or "",
            "match": match,
            "similarity": f"{similarity:.4f}" if match == "near" else ("1.0" if match != "near" else f"{similarity:.4f}"),
            "company": company,
            "product_family": family,
            "product_category": row.get("product_category") or "",
            "supply_direction": direction,
            "sentiment": sentiment,
            "direction_conflict": row.get("direction_conflict") or "",
            "matched_terms": row.get("matched_terms") or "",
            "excerpt": excerpt,
            "language": row.get("language") or "en",
            "excerpt_en": row.get("excerpt_en") or "",
            "ticker_or_code": row.get("ticker_or_code") or "",
            "role": row.get("role") or "",
            "tier": str(row.get("tier") or ""),
            "source": row.get("source") or "",
            "lookback_months": row.get("lookback_months") or "",
            "window_start": row.get("window_start") or "",
            "window_end": row.get("window_end") or "",
            "extractor_version": row.get("extractor_version") or "",
            "extracted_at": row.get("extracted_at") or "",
            "topic": row.get("topic") or "",
        }
        if match != "near":
            sighting["similarity"] = "1.0"
        else:
            sighting["similarity"] = f"{similarity:.4f}"

        sightings.append(sighting)
        signal_acc[chosen_sid]["sightings"].append(sighting)

    signals = build_signals_from_sightings(
        sightings, signal_acc, prior_year_norms=prior_year_norms or {}
    )
    return signals, sightings


def canonical_sighting(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer English EDGAR (then IR), then a more specific product family."""

    def rank(g: dict[str, Any]) -> tuple:
        src = source_key(g)
        lang = str(g.get("language") or "en")
        lang_i = 0 if is_english_lang(lang) else 1
        src_i = _SOURCE_RANK.get(src, 9)
        match_i = 0 if g.get("match") == "first" else 1
        # Lower rank tuple wins: negate specificity so HBM/DRAM beat Memory_general.
        fam_i = -family_specificity(str(g.get("product_family") or ""))
        return (
            lang_i,
            src_i,
            match_i,
            fam_i,
            g.get("product_family") or "",
            g.get("filing_date") or "",
            g.get("filing_id") or "",
            int(g.get("char_start") or 0),
        )

    return min(group, key=rank)


def build_signals_from_sightings(
    sightings: list[dict[str, Any]],
    signal_acc: dict[str, dict[str, Any]] | None = None,
    *,
    prior_year_norms: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    """Rebuild one signal row per signal_id from its sightings."""
    prior_year_norms = prior_year_norms or {}
    by_sid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in sightings:
        by_sid[s["signal_id"]].append(s)

    signals: list[dict[str, Any]] = []
    for sid, group in by_sid.items():
        group_sorted = sorted(
            group,
            key=lambda r: (
                r.get("filing_date") or "",
                r.get("filing_id") or "",
                int(r.get("char_start") or 0),
            ),
        )
        # Prefer English EDGAR/IR over DART Korean when both sightings exist.
        first_match = canonical_sighting(group_sorted)
        latest_by_date = max(group_sorted, key=lambda r: r.get("filing_date") or "")
        dates = [g.get("filing_date") or "" for g in group_sorted if g.get("filing_date")]
        first_seen = min(dates) if dates else ""
        last_seen = max(dates) if dates else ""

        company = first_match.get("company") or ""
        carried = "false"
        prior_bodies = list(prior_year_norms.get(company) or [])
        for g in group_sorted:
            excerpt_norm = normalize(g.get("excerpt") or "")
            if not excerpt_norm or len(excerpt_norm) < 40:
                continue
            if any(excerpt_norm in body for body in prior_bodies):
                carried = "true"
                break

        # Canonical row supplies displayed excerpt/source; IDs stay on the group.
        lang = first_match.get("language") or "en"
        excerpt_en = first_match.get("excerpt_en") or ""
        if is_english_lang(str(lang)):
            excerpt_en = ""
        signals.append(
            {
                "signal_id": sid,
                "company": company,
                "ticker_or_code": first_match.get("ticker_or_code") or "",
                "role": first_match.get("role") or "",
                "tier": first_match.get("tier") or "",
                "source": first_match.get("source") or "",
                "form": first_match.get("form") or "",
                "filing_date": first_seen,
                "filing_id": first_match.get("filing_id") or "",
                "section": first_match.get("section") or "",
                "product_category": first_match.get("product_category") or "",
                "product_family": first_match.get("product_family") or "",
                "topic": first_match.get("topic") or "",
                "supply_direction": first_match.get("supply_direction") or "",
                "sentiment": first_match.get("sentiment") or "",
                "direction_conflict": first_match.get("direction_conflict") or "",
                "matched_terms": first_match.get("matched_terms") or "",
                "excerpt": first_match.get("excerpt") or "",
                "language": lang,
                # Filled by `python -m src.translate_excerpts`; blank for language=en.
                "excerpt_en": excerpt_en,
                "char_start": first_match.get("char_start") or "",
                "char_end": first_match.get("char_end") or "",
                "raw_path": first_match.get("raw_path") or "",
                "source_url": first_match.get("source_url") or "",
                "first_seen": first_seen,
                "last_seen": last_seen,
                "latest_period_end": latest_by_date.get("period_end") or "",
                "times_seen": str(len(group_sorted)),
                "carried_from_prior_year": carried,
                "lookback_months": first_match.get("lookback_months") or "",
                "window_start": first_match.get("window_start") or "",
                "window_end": first_match.get("window_end") or "",
                "extractor_version": first_match.get("extractor_version") or "",
                "extracted_at": first_match.get("extracted_at") or "",
            }
        )

    signals.sort(
        key=lambda r: (
            r.get("last_seen") or "",
            r.get("company") or "",
            r.get("product_family") or "",
            r.get("signal_id") or "",
        ),
        reverse=True,
    )
    return signals


def load_candidate_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})


def append_sightings(
    path: Path, new_sightings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Append sightings whose sighting_id is not already present. Returns all rows."""
    existing: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    if path.is_file() and path.stat().st_size > 0:
        with path.open(encoding="utf-8", newline="") as fh:
            existing = list(csv.DictReader(fh))
            seen_ids = {r.get("sighting_id", "") for r in existing}

    to_add = [s for s in new_sightings if s.get("sighting_id") not in seen_ids]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.is_file() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SIGHTING_COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in to_add:
            writer.writerow({k: row.get(k, "") for k in SIGHTING_COLUMNS})
    return existing + [{k: str(s.get(k, "")) for k in SIGHTING_COLUMNS} for s in to_add]


def count_match_types(sightings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"first": 0, "exact": 0, "near": 0}
    for s in sightings:
        m = s.get("match") or ""
        if m in counts:
            counts[m] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run dedupe on a candidate CSV (fixture)")
    parser.add_argument("csv", type=Path, help="Candidate rows CSV (e.g. fixtures/signals_v010.csv)")
    args = parser.parse_args(argv)
    rows = load_candidate_csv(args.csv)
    signals, sightings = dedupe(rows)
    counts = count_match_types(sightings)
    print(f"input_rows={len(rows)}")
    print(f"sightings={len(sightings)}")
    print(f"signals={len(signals)}")
    print(f"exact={counts['exact']} near={counts['near']} first={counts['first']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
