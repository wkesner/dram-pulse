"""Milestone 5: Korean term banks + DART/IR language tagging."""

from __future__ import annotations

from pathlib import Path

from src.extract import (
    REPO_ROOT,
    _load_term_banks,
    _text_is_korean,
    extract_from_section,
    ExtractContext,
    split_sentences,
)
from src.fetch_edgar import Company
from datetime import datetime, timezone


def test_korean_terms_merged_into_banks() -> None:
    banks = _load_term_banks(
        REPO_ROOT / "config" / "product_taxonomy.yaml",
        REPO_ROOT / "config" / "supply_terms.yaml",
    )
    # Direction bank must include a Korean tightening phrase.
    blob = " ".join(term for _label, term, _pat in banks["tightening"])
    assert "공급 부족" in blob or "가격 상승" in blob
    # Product taxonomy includes Korean DRAM/NAND synonyms.
    product_terms = {p.term for p in banks["products"]}
    assert "디램" in product_terms
    assert "낸드" in product_terms


def test_korean_sentence_split() -> None:
    text = "DRAM 가격이 상승하였습니다. 낸드 재고는 감소했습니다."
    assert _text_is_korean(text)
    sents = split_sentences(text)
    assert len(sents) >= 2


def test_extract_korean_passage_tagged_ko() -> None:
    banks = _load_term_banks(
        REPO_ROOT / "config" / "product_taxonomy.yaml",
        REPO_ROOT / "config" / "supply_terms.yaml",
    )
    text = (
        "2분기는 AI 인프라 투자 확대에 따른 수요 강세와 제한된 공급 환경 지속으로 "
        "가격 상승세가 이어졌습니다. DRAM과 NAND 모두 전 분기에 이어 큰 폭의 "
        "가격 상승을 기록했습니다."
    )
    # raw_path must live under the repo root (signals store repo-relative paths).
    raw_dir = REPO_ROOT / "data" / "raw" / "_test_dart"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = raw_dir / "other_full.txt"
    raw.write_text(text, encoding="utf-8")
    company = Company(
        name="SK hynix",
        role="supplier",
        source="DART",
        ticker="000660",
        krx_code="000660",
    )
    meta = {
        "source": "DART",
        "source_url": "https://dart.fss.or.kr/example",
        "form": "반기보고서",
        "filing_date": "2026-08-14",
        "accession_number": "20260814003509",
        "language": "ko",
        "lookback_months": 12,
        "window_start": "2025-10-28",
        "window_end": "2026-10-28",
        "company": "SK hynix",
        "ticker": "000660",
        "role": "supplier",
    }
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
        prior_texts=[],
        extracted_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        rows = extract_from_section(ctx, "other", raw, text)
        assert rows, "expected at least one Korean DART signal"
        assert all(r["language"] == "ko" for r in rows)
        assert all(r["source"] == "DART" for r in rows)
        families = {r["product_family"] for r in rows}
        assert families & {"DRAM_general", "NAND_general", "Memory_general"}
    finally:
        if raw.is_file():
            raw.unlink()
        if raw_dir.is_dir() and not any(raw_dir.iterdir()):
            raw_dir.rmdir()
