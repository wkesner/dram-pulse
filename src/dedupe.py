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
_NUM_RE = re.compile(r"\d[\d,.]*%?")
_NON_ALNUM_RUN = re.compile(r"[^a-z0-9]+", re.IGNORECASE)

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
    "ticker_or_code",
    "role",
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


def _span(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["char_start"]), int(row["char_end"])


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    as_, ae = _span(a)
    bs, be = _span(b)
    return max(0, min(ae, be) - max(as_, bs)) > 0


def collapse_overlapping_windows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group by raw_path + product_family + supply_direction. Where windows
    overlap, keep the one with most matched terms, then longest, then earliest.
    Different directions overlapping are both kept (different groups).
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    passthrough: list[dict[str, Any]] = []
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
            # Prefer more matched terms, then longer span, then earlier start.
            cand_score = (
                _matched_term_count(row),
                int(row["char_end"]) - int(row["char_start"]),
                -int(row["char_start"]),
            )
            prev_score = (
                _matched_term_count(prev),
                int(prev["char_end"]) - int(prev["char_start"]),
                -int(prev["char_start"]),
            )
            if cand_score > prev_score:
                selected[conflict_idx] = row
            # else keep prev
        kept.extend(selected)
    return kept


def dedupe(
    rows: Iterable[dict[str, Any]],
    *,
    near_jaccard: float = NEAR_JACCARD,
    prior_year_norms: dict[str, set[str] | list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Pure function: candidate rows → (signals, sightings).

    `prior_year_norms` maps company → normalized prior-year 10-K bodies (or
    chunks) used for carried_from_prior_year. Optional.
    """
    candidates = collapse_overlapping_windows(list(rows))
    # Oldest filing first for exact/near assignment.
    candidates.sort(
        key=lambda r: (
            r.get("filing_date") or "",
            r.get("filing_id") or "",
            int(r.get("char_start") or 0),
            r.get("product_family") or "",
        )
    )

    # Per (company, family): list of (signal_id, first_excerpt, direction, sentiment, norms_seen)
    # norms_seen: list of excerpts already attributed to this signal (for near compare)
    open_signals: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    sightings: list[dict[str, Any]] = []
    # signal_id → accumulator of sightings / first row
    signal_acc: dict[str, dict[str, Any]] = {}

    for row in candidates:
        company = row.get("company") or ""
        family = row.get("product_family") or ""
        excerpt = row.get("excerpt") or ""
        direction = row.get("supply_direction") or ""
        sentiment = row.get("sentiment") or ""
        norm = normalize(excerpt)
        key = (company, family)

        match = "first"
        similarity = 1.0
        chosen_sid: str | None = None

        # Exact: normalized excerpt matches any text already seen for company+family
        for sig in open_signals[key]:
            if norm in sig["norms"] or any(normalize(e) == norm for e in sig["excerpts"]):
                match = "exact"
                similarity = 1.0
                chosen_sid = sig["signal_id"]
                break

        # Near
        if chosen_sid is None:
            best_sim = 0.0
            best_sid = None
            for sig in open_signals[key]:
                if sig["direction"] != direction or sig["sentiment"] != sentiment:
                    continue
                for prev_excerpt in sig["excerpts"]:
                    if not numbers_identical(excerpt, prev_excerpt):
                        continue
                    sim = jaccard(excerpt, prev_excerpt)
                    if sim >= near_jaccard and sim > best_sim:
                        best_sim = sim
                        best_sid = sig["signal_id"]
            if best_sid is not None:
                match = "near"
                similarity = best_sim
                chosen_sid = best_sid

        if chosen_sid is None:
            # New signal — ID from first-filing excerpt
            chosen_sid = stable_signal_id(company, family, excerpt)
            open_signals[key].append(
                {
                    "signal_id": chosen_sid,
                    "direction": direction,
                    "sentiment": sentiment,
                    "excerpts": [excerpt],
                    "norms": {norm},
                    "first_row": row,
                }
            )
            signal_acc[chosen_sid] = {
                "first_row": dict(row),
                "sightings": [],
            }
        else:
            # Attach to existing
            for sig in open_signals[key]:
                if sig["signal_id"] == chosen_sid:
                    sig["excerpts"].append(excerpt)
                    sig["norms"].add(norm)
                    break
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
            "ticker_or_code": row.get("ticker_or_code") or "",
            "role": row.get("role") or "",
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
        first = group_sorted[0]
        # Prefer the match=first sighting as canonical excerpt when present.
        first_match = next((g for g in group_sorted if g.get("match") == "first"), first)
        last = max(group_sorted, key=lambda r: (r.get("filing_date") or "", r.get("period_end") or ""))
        # latest_period_end: period_end of the latest sighting by filing_date
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

        # Use first_match excerpt for stable ID consistency
        signals.append(
            {
                "signal_id": sid,
                "company": company,
                "ticker_or_code": first_match.get("ticker_or_code") or "",
                "role": first_match.get("role") or "",
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
                "language": first_match.get("language") or "en",
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
