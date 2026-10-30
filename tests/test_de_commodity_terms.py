"""German (de) commodity / supply term banks + fixture-window extract."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.extract import (
    REPO_ROOT,
    ExtractContext,
    _load_term_banks,
    extract_from_section,
    split_sentences,
)
from src.fetch_edgar import Company

FIXTURES = Path(__file__).parent / "fixtures" / "de_extract"


def _banks():
    return _load_term_banks(
        REPO_ROOT / "config" / "product_taxonomy.yaml",
        REPO_ROOT / "config" / "supply_terms.yaml",
    )


def test_german_product_terms_present() -> None:
    product_terms = {p.term for p in _banks()["products"]}
    assert "Leistungshalbleiter" in product_terms
    assert "DRAM-Bausteine" in product_terms or "Speichermodule" in product_terms
    assert "Mikrocontroller" in product_terms
    assert "Mehrschicht-Keramikkondensatoren" in product_terms
    assert "Siliziumwafer" in product_terms or "Waferfertigung" in product_terms
    assert "Grafikprozessoren" in product_terms
    # Bare noisy tokens deliberately omitted.
    assert "Speicher" not in product_terms
    assert "Kondensator" not in product_terms
    assert "Kondensatoren" not in product_terms


def test_german_supply_terms_merged() -> None:
    banks = _banks()
    tightening = " ".join(term for _label, term, _pat in banks["tightening"])
    easing = " ".join(term for _label, term, _pat in banks["easing"])
    topics = " ".join(term for _label, term, _pat in banks["topic_compiled"])
    assert "Lieferengpass" in tightening or "Lieferengpässe" in tightening
    assert "Verknappung" in tightening
    assert "Bestandsaufbau" in easing
    assert "Allokation" in topics or "Produktallokation" in topics
    assert "Lagerbestand" in topics or "Lagerbestände" in topics
    # Prefer compounds over bare legalese stems.
    topic_terms = {term for _label, term, _pat in banks["topic_compiled"]}
    assert "Lieferkette" not in topic_terms
    assert "Bestand" not in topic_terms
    assert "Kapazität" not in topic_terms


def test_german_sentence_split() -> None:
    text = (
        "Leistungshalbleiter waren von Lieferengpässen betroffen. "
        "Die Speichermodule verteuerten sich."
    )
    sents = split_sentences(text)
    assert len(sents) >= 2


def _extract_fixture(name: str, *, company: str, ticker: str) -> list[dict]:
    banks = _banks()
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    raw_dir = REPO_ROOT / "data" / "raw" / "_test_de"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = raw_dir / f"{path.stem}.txt"
    raw.write_text(text, encoding="utf-8")
    co = Company(
        name=company,
        role="supplier",
        source="UREG",
        ticker=ticker,
    )
    meta = {
        "source": "UREG",
        "source_url": f"https://www.unternehmensregister.de/example/{ticker}",
        "form": "Lagebericht",
        "filing_date": "2024-12-31",
        "accession_number": f"test-de-{path.stem}",
        "language": "de",
        "lookback_months": 12,
        "window_start": "2024-01-01",
        "window_end": "2024-12-31",
        "company": company,
        "ticker": ticker,
        "role": "supplier",
    }
    ctx = ExtractContext(
        company=co,
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
        return extract_from_section(ctx, "other", raw, text)
    finally:
        if raw.is_file():
            raw.unlink()
        if raw_dir.is_dir() and not any(raw_dir.iterdir()):
            raw_dir.rmdir()


def test_extract_infineon_leistungshalbleiter_fixture() -> None:
    rows = _extract_fixture(
        "infineon_leistungshalbleiter.txt",
        company="Infineon Technologies AG",
        ticker="IFX",
    )
    assert rows, "expected ≥1 German Power_IC / supply signal"
    assert all(r.get("language") == "de" for r in rows)
    assert all(r.get("source") == "UREG" for r in rows)
    families = {r["product_family"] for r in rows}
    assert "Power_IC" in families
    assert any(r.get("supply_direction") == "tightening" for r in rows)


def test_extract_memory_dram_fixture() -> None:
    rows = _extract_fixture(
        "memory_dram_engpass.txt",
        company="Test Memory AG",
        ticker="DRAMDE",
    )
    assert rows, "expected ≥1 German DRAM / NAND supply signal"
    assert all(r.get("language") == "de" for r in rows)
    families = {r["product_family"] for r in rows}
    assert families & {"DRAM_general", "Memory_general", "NAND_general"}
    assert any(r.get("supply_direction") == "tightening" for r in rows)


def test_extract_wafer_mlcc_fixture() -> None:
    rows = _extract_fixture(
        "wafer_mlcc_bestand.txt",
        company="Test Wafer AG",
        ticker="WAFDE",
    )
    assert rows, "expected ≥1 German wafer / MLCC signal"
    assert all(r.get("language") == "de" for r in rows)
    families = {r["product_family"] for r in rows}
    assert families & {"Capacitor", "Packaging"}
    directions = {r.get("supply_direction") for r in rows}
    assert directions & {"easing", "tightening"}
