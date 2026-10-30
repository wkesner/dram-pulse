"""Unternehmensregister (UREG) fetch — parse fixtures, no live network in CI."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.fetch_edgar import Company, REPO_ROOT, load_companies
from src.fetch_ureg import (
    filter_publications,
    parse_company_euid,
    parse_search_html,
    preferred_pub_types,
    resolve_body_url,
    search_name,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def test_companies_yaml_has_ureg_smoke_names() -> None:
    cos = load_companies(REPO_ROOT / "config" / "companies.yaml")
    ureg = [c for c in cos if c.source.upper() == "UREG"]
    by_ticker = {c.ticker: c for c in ureg}
    assert "IFX" in by_ticker
    assert "WAF" in by_ticker
    assert by_ticker["IFX"].euid == "DED2601V.HRB126492"
    assert by_ticker["IFX"].isin == "DE0006231004"
    assert by_ticker["WAF"].company_name == "Siltronic AG"
    assert "135" in by_ticker["IFX"].forms


def test_parse_infineon_fixture_filters_hinweis_and_hgb() -> None:
    html = (FIXTURES / "ureg_infineon_search.html").read_text(encoding="utf-8")
    pubs = parse_search_html(html)
    assert parse_company_euid(html) == "DED2601V.HRB126492"
    types = {p["publication_type"] for p in pubs}
    assert "135" in types
    assert "136" in types
    assert "121" in types
    assert "86" in types

    filtered = filter_publications(
        pubs,
        types={"135", "136", "137"},
        window_start=date(2025, 1, 1),
        window_end=date(2026, 12, 31),
    )
    titles = [p["title"] for p in filtered]
    assert any("Jahresabschluss" in t for t in titles)
    assert any("Halbjahresfinanzbericht" in t for t in titles)
    assert not any(t.startswith("Hinweis") for t in titles)
    assert not any("264" in t for t in titles)
    # umlaut decode from \\u00e4
    assert any("Geschäftsjahr" in t for t in titles)


def test_parse_siltronic_fixture() -> None:
    html = (FIXTURES / "ureg_siltronic_search.html").read_text(encoding="utf-8")
    pubs = parse_search_html(html)
    assert parse_company_euid(html) == "DED2601V.HRB150884"
    filtered = filter_publications(
        pubs,
        types={"135", "136", "137"},
        window_start=date(2025, 1, 1),
        window_end=date(2026, 12, 31),
    )
    assert any(p["publication_type"] == "135" for p in filtered)
    assert any("Siltronic" in (p.get("company_name") or "") for p in pubs)


def test_resolve_body_url_prefers_ureg_documents_for_jahresabschluss() -> None:
    company = Company(
        name="Infineon Technologies",
        role="supplier",
        source="UREG",
        ticker="IFX",
        forms=("135", "136", "137"),
        company_name="Infineon Technologies AG",
        euid="DED2601V.HRB126492",
    )
    pub = {
        "publication_type": "135",
        "title": "Jahresabschluss zum Geschäftsjahr vom 01.10.2024 bis zum 30.09.2025",
        "source_date": "2026-03-11",
        "source_name": "Unternehmensregister",
    }
    overrides = [
        {
            "ticker": "IFX",
            "publication_type": "135",
            "title_contains": "Jahresabschluss",
            "filing_date": "2026-03-11",
            "url": "https://example.test/ifx-ja.pdf",
        }
    ]
    settings = {"ureg": {"follow_external_source_urls": True}}
    url, provenance = resolve_body_url(pub, company, settings, overrides)
    assert provenance == "ureg_documents"
    assert url and url.endswith("ifx-ja.pdf")


def test_resolve_body_url_uses_direct_pdf_source_name() -> None:
    company = Company(
        name="Demo",
        role="supplier",
        source="UREG",
        ticker="DEMO",
        forms=("136",),
    )
    pub = {
        "publication_type": "136",
        "title": "Halbjahresfinanzbericht",
        "source_date": "2026-05-06",
        "source_name": "https://ir.example.test/reports/h1-2026.pdf",
    }
    url, provenance = resolve_body_url(pub, company, {"ureg": {}}, [])
    assert provenance == "source_name"
    assert url.endswith("h1-2026.pdf")


def test_preferred_pub_types_and_search_name() -> None:
    c = Company(
        name="Infineon",
        role="supplier",
        source="UREG",
        ticker="IFX",
        forms=("135", "136"),
        company_name="Infineon Technologies AG",
    )
    assert search_name(c) == "Infineon Technologies AG"
    assert preferred_pub_types(c, {"ureg": {}}) == {"135", "136"}


def test_fetch_one_publication_writes_cache(tmp_path: Path) -> None:
    from src.fetch_ureg import fetch_one_publication

    # Minimal one-page PDF bytes (empty content object — pypdf may yield little text).
    # Use a tiny real PDF from pypdf writer if available; else skip extract via mock.
    company = Company(
        name="Infineon Technologies",
        role="supplier",
        source="UREG",
        ticker="IFX",
        forms=("135",),
        company_name="Infineon Technologies AG",
    )
    pub = {
        "publication_type": "135",
        "title": "Jahresabschluss zum Geschäftsjahr vom 01.10.2024 bis zum 30.09.2025",
        "source_date": "2026-03-11",
        "source_name": "Unternehmensregister",
        "esef_pub": False,
    }
    overrides = [
        {
            "ticker": "IFX",
            "publication_type": "135",
            "title_contains": "Jahresabschluss",
            "filing_date": "2026-03-11",
            "url": "https://example.test/ja.pdf",
            "language": "de",
        }
    ]
    settings = {
        "lookback_months": 12,
        "paths": {"raw_dir": str(tmp_path)},
        "ureg": {"follow_external_source_urls": True},
    }
    pdf_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.content = pdf_bytes
    resp.headers = {"Content-Type": "application/pdf"}
    resp.raise_for_status = MagicMock()
    sess.get.return_value = resp
    lim = MagicMock()

    with patch("src.fetch_ureg._pdf_to_text", return_value="Lieferkette und Kapazität Test.\n"):
        with patch("src.fetch_ureg._raw_root", return_value=tmp_path):
            with patch("src.fetch_ureg.REPO_ROOT", tmp_path):
                # relative_to(REPO_ROOT) needs dest under tmp_path == REPO_ROOT
                ok = fetch_one_publication(
                    company,
                    pub,
                    settings,
                    sess,
                    lim,
                    overrides,
                    window_start=date(2025, 1, 1),
                    window_end=date(2026, 12, 31),
                    force=True,
                )
    assert ok is True
    # filing id folder under IFX/
    kids = list((tmp_path / "IFX").iterdir())
    assert kids
    meta = (kids[0] / "meta.json").read_text(encoding="utf-8")
    assert '"source": "UREG"' in meta
    assert "Lieferkette" in (kids[0] / "other_full.txt").read_text(encoding="utf-8")
