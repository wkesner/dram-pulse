"""Fixture tests for de-dupe (DEDUPE.md). Totals must not drift."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.dedupe import (
    SIGHTING_COLUMNS,
    append_sightings,
    build_signals_from_sightings,
    count_match_types,
    dedupe,
    load_candidate_csv,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "fixtures" / "signals_v010.csv"

# Expected on the 0.1.0 fixture after cross-family exact + nested-window merge
# (same claim tagged DRAM + NAND + Memory_general, or a short span contained in
# a longer cross-family span → one signal, multiple sightings).
EXPECT_INPUT = 188
EXPECT_SIGHTINGS = 124
EXPECT_SIGNALS = 57
EXPECT_EXACT = 55
EXPECT_NEAR = 12

MICRON_RESTATE = ["1f0beca2cc6f", "6ddf3af33877", "0e6411d58623", "db2a072beb62"]
QUALCOMM_PAIR = ("8732382a3a08", "11282417d705")
HBM_CONFLICT = ("5270a139e733", "6726809579d0")


@pytest.fixture(scope="module")
def fixture_rows() -> list[dict[str, str]]:
    assert FIXTURE.is_file(), f"missing fixture {FIXTURE}"
    rows = load_candidate_csv(FIXTURE)
    assert len(rows) == EXPECT_INPUT
    return rows


@pytest.fixture(scope="module")
def deduped(fixture_rows: list[dict[str, str]]):
    return dedupe(fixture_rows)


def test_totals(deduped):
    signals, sightings = deduped
    assert len(sightings) == EXPECT_SIGHTINGS
    assert len(signals) == EXPECT_SIGNALS
    counts = count_match_types(sightings)
    assert counts["exact"] == EXPECT_EXACT
    assert counts["near"] == EXPECT_NEAR
    assert counts["first"] == EXPECT_SIGNALS


def _sid_by_old(sightings: list[dict], old_id: str) -> str:
    for s in sightings:
        if s.get("old_signal_id") == old_id:
            return s["signal_id"]
    raise AssertionError(f"old_signal_id {old_id} not found in sightings")


def test_micron_restated_quarterly_merges(deduped):
    _signals, sightings = deduped
    new_ids = {_sid_by_old(sightings, oid) for oid in MICRON_RESTATE}
    assert len(new_ids) == 1


def test_qualcomm_increase_vs_decrease_apart(deduped):
    _signals, sightings = deduped
    a = _sid_by_old(sightings, QUALCOMM_PAIR[0])
    b = _sid_by_old(sightings, QUALCOMM_PAIR[1])
    assert a != b


def test_conflicting_hbm_windows_both_kept(deduped):
    _signals, sightings = deduped
    a = _sid_by_old(sightings, HBM_CONFLICT[0])
    b = _sid_by_old(sightings, HBM_CONFLICT[1])
    assert a != b
    # Both directions should appear as first (or at least as distinct signals).
    dirs = {
        s["supply_direction"]
        for s in sightings
        if s["signal_id"] in {a, b} and s.get("match") == "first"
    }
    assert "tightening" in dirs
    assert "easing" in dirs


def test_rerun_idempotent(fixture_rows, tmp_path: Path):
    signals1, sightings1 = dedupe(fixture_rows)
    path = tmp_path / "sightings.csv"
    append_sightings(path, sightings1)
    before = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    # Second append of the same sightings adds nothing.
    all_rows = append_sightings(path, sightings1)
    assert len(all_rows) == len(before) == EXPECT_SIGHTINGS
    signals2, sightings2 = dedupe(fixture_rows)
    assert {s["signal_id"] for s in signals1} == {s["signal_id"] for s in signals2}
    assert {s["sighting_id"] for s in sightings1} == {s["sighting_id"] for s in sightings2}
    # Rebuild from written file matches.
    rebuilt = build_signals_from_sightings(before)
    assert {s["signal_id"] for s in rebuilt} == {s["signal_id"] for s in signals1}


def test_sighting_columns_include_old_signal_id(deduped):
    _signals, sightings = deduped
    assert "old_signal_id" in SIGHTING_COLUMNS
    assert all(s.get("old_signal_id") for s in sightings)


def _sk_cross_base(**overrides) -> dict:
    row = {
        "company": "SK hynix",
        "ticker_or_code": "SKHY",
        "role": "supplier",
        "source": "EDGAR",
        "language": "en",
        "form": "6-K",
        "filing_date": "2026-08-18",
        "filing_id": "edgar-1",
        "section": "other",
        "product_category": "DRAM",
        "product_family": "DRAM_general",
        "supply_direction": "tightening",
        "sentiment": "mixed",
        "direction_conflict": "false",
        "matched_terms": "product:DRAM",
        "excerpt": (
            "Both DRAM and NAND flash experienced significant further price increases "
            "following those in the previous quarter, and AI-related products such as "
            "server DRAM and eSSDs led this trend."
        ),
        "excerpt_en": "",
        "char_start": "10",
        "char_end": "120",
        "raw_path": "data/raw/SKHY/a/other_full.txt",
        "source_url": "https://example.com/edgar",
        "lookback_months": "12",
        "window_start": "2025-10-28",
        "window_end": "2026-10-28",
        "extractor_version": "0.3.1",
        "extracted_at": "2026-10-28T00:00:00+00:00",
        "topic": "pricing",
        "period_end": "2026-06-30",
        "signal_id": "old-edgar",
    }
    row.update(overrides)
    return row


def test_cross_source_dart_edgar_near_merges_prefer_edgar():
    """DART Korean (via excerpt_en) near-matches EDGAR English; card prefers EDGAR.

    Mirrors the live SK hynix pair: Argos pads the translation, sentiment labels
    disagree (mixed vs positive), but the core sentence is the same claim.
    """
    from src.dedupe import canonical_sighting

    edgar = _sk_cross_base()
    dart = _sk_cross_base(
        ticker_or_code="000660",
        source="DART",
        language="ko",
        form="반기보고서 (2026.06)",
        filing_date="2026-08-14",
        filing_id="dart-1",
        sentiment="positive",
        excerpt="디램과 낸드 모두 전 분기에 이어 큰 폭의 가격 상승을 기록했으며",
        excerpt_en=(
            "The second quarter of the sales was KRW 2 trillion and KRW 2 trillion "
            "were KRW 2 trillion in the demand tax and limited supply environment. "
            "Both DRAM and NAND have recorded a large width price rise, and AI "
            "related products such as server DRAM and enterprise SSD have led these "
            "flows. Our second quarter sales increased by 51% compared to the "
            "previous quarter."
        ),
        char_start="20",
        char_end="100",
        raw_path="data/raw/000660/b/other_full.txt",
        source_url="https://example.com/dart",
        signal_id="old-dart",
    )
    signals, sightings = dedupe([dart, edgar])
    assert len(signals) == 1
    assert len(sightings) == 2
    assert {s["source"] for s in sightings} == {"EDGAR", "DART"}
    assert {s["match"] for s in sightings} == {"first", "near"}
    canon = canonical_sighting(sightings)
    assert canon["source"] == "EDGAR"
    assert signals[0]["source"] == "EDGAR"
    assert signals[0]["language"] == "en"
    assert "DRAM" in signals[0]["excerpt"]


def test_cross_source_requires_date_window():
    """Far-apart DART/EDGAR paraphrases must not merge."""
    edgar = _sk_cross_base(filing_id="edgar-2", raw_path="data/raw/SKHY/c/other_full.txt")
    dart = _sk_cross_base(
        ticker_or_code="000660",
        source="DART",
        language="ko",
        filing_date="2025-11-14",
        filing_id="dart-old",
        sentiment="positive",
        excerpt="디램 출하",
        excerpt_en=(
            "Both DRAM and NAND have recorded a large width price rise, and AI "
            "related products such as server DRAM and enterprise SSD have led these flows."
        ),
        char_start="5",
        char_end="40",
        raw_path="data/raw/000660/old/other_full.txt",
        source_url="https://example.com/dart-old",
        signal_id="old-d2",
    )
    signals, sightings = dedupe([dart, edgar])
    assert len(signals) == 2
    assert len(sightings) == 2


def test_cross_source_keeps_direction_apart():
    """Same claim text across sources must not merge when supply_direction differs."""
    edgar = _sk_cross_base(supply_direction="tightening")
    dart = _sk_cross_base(
        ticker_or_code="000660",
        source="DART",
        language="ko",
        filing_date="2026-08-14",
        filing_id="dart-ease",
        supply_direction="easing",
        sentiment="positive",
        excerpt="가격",
        excerpt_en=(
            "Both DRAM and NAND have recorded a large width price rise, and AI "
            "related products such as server DRAM and enterprise SSD have led these flows."
        ),
        char_start="30",
        char_end="80",
        raw_path="data/raw/000660/ease/other_full.txt",
        source_url="https://example.com/dart-ease",
        signal_id="old-ease",
    )
    signals, sightings = dedupe([dart, edgar])
    assert len(signals) == 2
    assert len(sightings) == 2


def test_cross_family_exact_merges_same_span():
    """Same Silicon Labs shortage paragraph tagged under three families → one card."""
    excerpt = (
        "Our business, financial condition, and results of operations could be "
        "materially and adversely affected by global or industry-specific shortages "
        "of memory components."
    )
    base = {
        "company": "Silicon Labs",
        "ticker_or_code": "SLAB",
        "role": "buyer",
        "source": "EDGAR",
        "language": "en",
        "form": "10-K",
        "filing_date": "2026-02-10",
        "filing_id": "slab-1",
        "section": "risk_factors",
        "supply_direction": "tightening",
        "sentiment": "negative",
        "direction_conflict": "false",
        "excerpt": excerpt,
        "excerpt_en": "",
        "char_start": "22374",
        "char_end": "22983",
        "raw_path": "data/raw/SLAB/a/risk.txt",
        "source_url": "https://example.com/slab",
        "lookback_months": "12",
        "window_start": "2025-01-01",
        "window_end": "2026-01-01",
        "extractor_version": "0.3.1",
        "extracted_at": "2026-10-28T00:00:00+00:00",
        "topic": "pricing",
        "period_end": "2026-01-03",
    }
    rows = []
    for fam, cat, term, oid in [
        ("Memory_general", "Other", "memory components", "old-mem"),
        ("DRAM_general", "DRAM", "DRAM", "old-dram"),
        ("NAND_general", "NAND", "NAND", "old-nand"),
    ]:
        rows.append(
            {
                **base,
                "product_family": fam,
                "product_category": cat,
                "matched_terms": f"product:{term}",
                "signal_id": oid,
            }
        )
    signals, sightings = dedupe(rows)
    assert len(signals) == 1
    assert len(sightings) == 3
    assert {s["match"] for s in sightings} == {"first", "exact"}
    assert sum(1 for s in sightings if s["match"] == "exact") == 2
    # Prefer a specific family over Memory_general on the card.
    assert signals[0]["product_family"] in {"DRAM_general", "NAND_general"}


def test_nested_window_exact_merges_into_parent():
    """Shorter Memory_general span contained in a longer NAND shortage claim → one card."""
    long_excerpt = (
        "Our business, financial condition, and results of operations could be "
        "materially and adversely affected by global or industry-specific shortages "
        "of memory components or other key components necessary for our customers’ "
        "products. The memory market has in the past experienced supply imbalances, "
        "capacity constraints, and pricing volatility, and the industry is currently "
        "experiencing a global shortage of certain memory components as a result of "
        "AI-driven demand."
    )
    nested = (
        "The memory market has in the past experienced supply imbalances, capacity "
        "constraints, and pricing volatility, and the industry is currently "
        "experiencing a global shortage of certain memory components as a result of "
        "AI-driven demand."
    )
    base = {
        "company": "Silicon Labs",
        "ticker_or_code": "SLAB",
        "role": "buyer",
        "source": "EDGAR",
        "language": "en",
        "form": "10-K",
        "filing_date": "2026-02-10",
        "filing_id": "slab-1",
        "section": "risk_factors",
        "supply_direction": "tightening",
        "sentiment": "negative",
        "direction_conflict": "false",
        "excerpt_en": "",
        "raw_path": "data/raw/SLAB/a/risk.txt",
        "source_url": "https://example.com/slab",
        "lookback_months": "12",
        "window_start": "2025-01-01",
        "window_end": "2026-01-01",
        "extractor_version": "0.3.1",
        "extracted_at": "2026-10-28T00:00:00+00:00",
        "topic": "pricing",
        "period_end": "2026-01-03",
    }
    parent = {
        **base,
        "product_family": "NAND_general",
        "product_category": "NAND",
        "matched_terms": "product:NAND",
        "excerpt": long_excerpt,
        "char_start": "22374",
        "char_end": "22983",
        "signal_id": "old-parent",
    }
    child = {
        **base,
        "product_family": "Memory_general",
        "product_category": "Other",
        "matched_terms": "product:memory components",
        "excerpt": nested,
        "char_start": "22750",
        "char_end": "22983",
        "signal_id": "old-nested",
    }
    signals, sightings = dedupe([parent, child])
    assert len(signals) == 1
    assert len(sightings) == 2
    assert {s["match"] for s in sightings} == {"first", "exact"}
    assert signals[0]["product_family"] == "NAND_general"


def _multi_lang_base(**overrides) -> dict:
    row = {
        "company": "Acme Semi",
        "ticker_or_code": "ACME",
        "role": "supplier",
        "source": "EDGAR",
        "language": "en",
        "form": "20-F",
        "filing_date": "2026-08-15",
        "filing_id": "acme-en",
        "section": "other",
        "product_category": "DRAM",
        "product_family": "DRAM_general",
        "supply_direction": "tightening",
        "sentiment": "mixed",
        "direction_conflict": "false",
        "matched_terms": "product:DRAM",
        "excerpt": (
            "Both DRAM and NAND flash experienced significant further price increases "
            "following those in the previous quarter, and AI-related products such as "
            "server DRAM and eSSDs led this trend."
        ),
        "excerpt_en": "",
        "char_start": "10",
        "char_end": "120",
        "raw_path": "data/raw/ACME/en/other_full.txt",
        "source_url": "https://example.com/en",
        "lookback_months": "12",
        "window_start": "2025-10-28",
        "window_end": "2026-10-28",
        "extractor_version": "0.4.0",
        "extracted_at": "2026-10-30T00:00:00+00:00",
        "topic": "pricing",
        "period_end": "2026-06-30",
        "signal_id": "old-en",
    }
    row.update(overrides)
    return row


def test_canonical_language_aliases():
    from src.dedupe import canonical_language, is_cross_language

    assert canonical_language("EN") == "en"
    assert canonical_language("korean") == "ko"
    assert canonical_language("zh-CN") == "zh"
    assert canonical_language("zh-TW") == "zh"
    assert canonical_language("jp") == "ja"
    assert canonical_language("German") == "de"
    a = _multi_lang_base(language="ko")
    b = _multi_lang_base(language="zh")
    assert is_cross_language(a, b)


@pytest.mark.parametrize(
    "lang,source,filing_id,raw",
    [
        ("ko", "DART", "dart-ko", "data/raw/ACME/ko/other_full.txt"),
        ("zh", "CNINFO", "cn-zh", "data/raw/ACME/zh/other_full.txt"),
        ("ja", "EDINET", "ja-1", "data/raw/ACME/ja/other_full.txt"),
        ("de", "BAFIN", "de-1", "data/raw/ACME/de/other_full.txt"),
    ],
)
def test_cross_language_near_merges_each_lang(lang, source, filing_id, raw):
    """EN ↔ KO/ZH/JA/DE near-merge via excerpt_en within the date window."""
    edgar = _multi_lang_base()
    other = _multi_lang_base(
        source=source,
        language=lang,
        filing_date="2026-08-12",
        filing_id=filing_id,
        sentiment="positive",
        excerpt=f"native-{lang}-claim",
        excerpt_en=(
            "The second quarter of the sales was KRW 2 trillion and KRW 2 trillion "
            "were KRW 2 trillion in the demand tax and limited supply environment. "
            "Both DRAM and NAND have recorded a large width price rise, and AI "
            "related products such as server DRAM and enterprise SSD have led these "
            "flows. Our second quarter sales increased by 51% compared to the "
            "previous quarter."
        ),
        char_start="20",
        char_end="100",
        raw_path=raw,
        source_url=f"https://example.com/{lang}",
        signal_id=f"old-{lang}",
        product_family="NAND_general",
        product_category="NAND",
    )
    signals, sightings = dedupe([other, edgar])
    assert len(signals) == 1, f"{lang} should near-merge across families"
    assert len(sightings) == 2
    assert {s["match"] for s in sightings} == {"first", "near"}
    assert signals[0]["language"] == "en"


def test_same_language_different_filing_date_keeps_both_sightings():
    """Exact restatement: same language, different dates → one signal, both sightings."""
    first = _multi_lang_base(
        filing_date="2026-02-10",
        filing_id="en-q1",
        raw_path="data/raw/ACME/q1/other_full.txt",
        signal_id="old-q1",
    )
    later = _multi_lang_base(
        filing_date="2026-08-10",
        filing_id="en-q3",
        char_start="40",
        char_end="150",
        raw_path="data/raw/ACME/q3/other_full.txt",
        signal_id="old-q3",
    )
    signals, sightings = dedupe([first, later])
    assert len(signals) == 1
    assert len(sightings) == 2
    assert {s["match"] for s in sightings} == {"first", "exact"}
    assert {s["filing_date"] for s in sightings} == {"2026-02-10", "2026-08-10"}
    assert signals[0]["times_seen"] == "2"


def test_cross_family_exact_collapses_across_merge_groups():
    """Identical Ambarella-style span tagged MCU + Power_IC + Memory → one card."""
    excerpt = (
        "Our customers purchase components used in the manufacture of their products "
        "from various sources of supply, often involving several specialized components, "
        "including lenses, sensors, microcontrollers, power management integrated "
        "circuits (PMICs), Wi-Fi chips, and memory chips. Any supply shortage of these "
        "components could materially and adversely affect our business."
    )
    base = {
        "company": "Ambarella",
        "ticker_or_code": "AMBA",
        "role": "buyer",
        "source": "EDGAR",
        "language": "en",
        "form": "10-K",
        "filing_date": "2026-03-23",
        "filing_id": "amba-1",
        "section": "risk_factors",
        "supply_direction": "tightening",
        "sentiment": "negative",
        "direction_conflict": "false",
        "excerpt": excerpt,
        "excerpt_en": "",
        "char_start": "1000",
        "char_end": "1400",
        "raw_path": "data/raw/AMBA/a/risk.txt",
        "source_url": "https://example.com/amba",
        "lookback_months": "12",
        "window_start": "2025-01-01",
        "window_end": "2026-01-01",
        "extractor_version": "0.4.0",
        "extracted_at": "2026-10-30T00:00:00+00:00",
        "topic": "pricing",
        "period_end": "2026-01-31",
    }
    rows = [
        {
            **base,
            "product_family": "MCU",
            "product_category": "Compute",
            "matched_terms": "product:microcontrollers",
            "signal_id": "old-mcu",
        },
        {
            **base,
            "product_family": "Power_IC",
            "product_category": "Analog",
            "matched_terms": "product:PMICs",
            "signal_id": "old-pmic",
        },
        {
            **base,
            "product_family": "Memory_general",
            "product_category": "Other",
            "matched_terms": "product:memory chips",
            "signal_id": "old-mem",
        },
    ]
    signals, sightings = dedupe(rows)
    assert len(signals) == 1
    assert len(sightings) == 3
    assert sum(1 for s in sightings if s["match"] == "exact") == 2


def test_cross_group_containment_different_date_keeps_both():
    """Soft Memory⊂Capacitor overlap on a later filing must not swallow Passives."""
    long_excerpt = (
        "Similarly, certain non-proprietary materials or components such as memory, "
        "printed circuit boards (PCBs), interposers, substrates and capacitors used in "
        "the manufacture of our products are currently available from only a limited "
        "number of suppliers. For example, there is currently an industry-wide memory "
        "shortage as the demand for such components has outpaced supply."
    )
    short_excerpt = (
        "Similarly, certain non-proprietary materials or components such as memory, "
        "printed circuit boards (PCBs), interposers, substrates and capacitors used in "
        "the manufacture of our products are currently available from only a limited "
        "number of suppliers."
    )
    memory = _multi_lang_base(
        company="AMD",
        ticker_or_code="AMD",
        role="buyer",
        filing_date="2026-02-04",
        filing_id="amd-mem",
        product_family="Memory_general",
        product_category="Other",
        excerpt=long_excerpt,
        char_start="100",
        char_end="400",
        raw_path="data/raw/AMD/mem/risk.txt",
        signal_id="old-mem",
        matched_terms="product:memory",
    )
    caps = _multi_lang_base(
        company="AMD",
        ticker_or_code="AMD",
        role="buyer",
        filing_date="2025-11-05",
        filing_id="amd-cap",
        product_family="Capacitor",
        product_category="Passives",
        excerpt=short_excerpt,
        char_start="50",
        char_end="250",
        raw_path="data/raw/AMD/cap/risk.txt",
        signal_id="old-cap",
        matched_terms="product:capacitors",
    )
    signals, sightings = dedupe([caps, memory])
    assert len(signals) == 2
    assert len(sightings) == 2
    assert {s["product_family"] for s in signals} == {"Memory_general", "Capacitor"}
