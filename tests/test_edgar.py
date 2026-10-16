"""Unit tests for EDGAR collector helpers (no network)."""

from __future__ import annotations

from dram_signals.collectors.edgar import EdgarCollector


def test_parse_company_name_with_ticker_and_cik() -> None:
    name = EdgarCollector._parse_company_name(
        ["NOCERA, INC.  (NCRA)  (CIK 0001756180)"]
    )
    assert name == "NOCERA, INC."


def test_parse_company_name_multi_ticker() -> None:
    name = EdgarCollector._parse_company_name(
        ["Exascale Labs Holdings Inc.  (XLAB, XLABW)  (CIK 0002109869)"]
    )
    assert name == "Exascale Labs Holdings Inc."


def test_parse_company_name_cik_only() -> None:
    name = EdgarCollector._parse_company_name(
        ["SOME COMPANY LLC  (CIK 0001234567)"]
    )
    assert name == "SOME COMPANY LLC"


def test_document_url() -> None:
    url = EdgarCollector._document_url(
        "1756180",
        "0001683168-26-005878",
        "nocera_ex9902.htm",
    )
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/"
        "1756180/000168316826005878/nocera_ex9902.htm"
    )


def test_build_query_phrases() -> None:
    assert EdgarCollector._build_query("inventory write-down") == '"inventory write-down"'
    assert EdgarCollector._build_query("DRAM") == "DRAM"


def test_html_to_text_strips_tags() -> None:
    text = EdgarCollector._html_to_text(
        "<html><body><p>Inventory <b>write-down</b> noted.</p></body></html>"
    )
    assert "Inventory write-down noted." in text
    assert "<" not in text
