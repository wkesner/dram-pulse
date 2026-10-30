"""Multi-language excerpt_en pipeline + on-demand translate API."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.server import create_app
from src.dedupe import SIGNAL_COLUMNS, write_csv
from src.translate_excerpts import (
    is_translatable_lang,
    normalize_source_lang,
    persist_excerpt_en,
    supported_argos_lang,
    translate_row_excerpt,
    translate_signals,
)


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("ko", "ko"),
        ("korean", "ko"),
        ("zh", "zh"),
        ("zh-CN", "zh"),
        ("zh-TW", "zt"),
        ("zh_tw", "zt"),
        ("ja", "ja"),
        ("jp", "ja"),
        ("japanese", "ja"),
        ("de", "de"),
        ("german", "de"),
        ("de-DE", "de"),
        ("en", None),
        ("", None),
        ("english", None),
        ("fr", None),
    ],
)
def test_normalize_source_lang(lang: str, expected: str | None):
    assert normalize_source_lang(lang) == expected


def test_translatable_vs_supported():
    assert is_translatable_lang("ko")
    assert is_translatable_lang("zh-TW")
    assert is_translatable_lang("de")
    assert is_translatable_lang("fr")  # foreign UI chip even if Argos lacks package
    assert not is_translatable_lang("en")
    assert supported_argos_lang("ko")
    assert supported_argos_lang("zh-TW")
    assert supported_argos_lang("UREG".lower()) is False  # not a language code
    assert supported_argos_lang("de")
    assert not supported_argos_lang("fr")


def _row(**kwargs: str) -> dict[str, str]:
    base = {
        "signal_id": "s1",
        "company": "Test Co",
        "ticker_or_code": "TST",
        "role": "supplier",
        "tier": "1",
        "source": "DART",
        "form": "DART",
        "filing_date": "2026-01-01",
        "filing_id": "1",
        "section": "full",
        "product_category": "Memory",
        "product_family": "DRAM_general",
        "topic": "capacity",
        "supply_direction": "tightening",
        "sentiment": "negative",
        "direction_conflict": "false",
        "matched_terms": "",
        "excerpt": "메모리 공급이 부족하다",
        "language": "ko",
        "excerpt_en": "",
        "char_start": "0",
        "char_end": "10",
        "raw_path": "",
        "source_url": "https://example.com",
        "first_seen": "",
        "last_seen": "",
        "latest_period_end": "",
        "times_seen": "1",
        "carried_from_prior_year": "false",
        "lookback_months": "12",
        "window_start": "",
        "window_end": "",
        "extractor_version": "test",
        "extracted_at": "",
    }
    base.update(kwargs)
    return base


def test_translate_signals_english_blank_and_skips_existing():
    rows = [
        _row(signal_id="en1", language="en", excerpt="English only", source="EDGAR"),
        _row(signal_id="ko1", language="ko", excerpt_en="Already English"),
        _row(signal_id="zh1", language="zh", excerpt="存储紧张", source="CNINFO"),
        _row(signal_id="de1", language="de", excerpt="Lieferengpass", source="UREG"),
        _row(signal_id="ja1", language="ja", excerpt="供給不足", source="EDINET"),
        _row(signal_id="tw1", language="zh-TW", excerpt="供給吃緊", source="MOPS"),
        _row(signal_id="fr1", language="fr", excerpt="pénurie", source="AMF"),
    ]
    with patch(
        "src.translate_excerpts.translate_text",
        side_effect=lambda text, source="ko", target="en": f"EN[{source}:{text}]",
    ):
        out, stats = translate_signals(rows, force=False, dry_run=False)

    by_id = {r["signal_id"]: r for r in out}
    assert by_id["en1"]["excerpt_en"] == ""
    assert by_id["ko1"]["excerpt_en"] == "Already English"
    assert by_id["zh1"]["excerpt_en"] == "EN[zh:存储紧张]"
    assert by_id["de1"]["excerpt_en"] == "EN[de:Lieferengpass]"
    assert by_id["ja1"]["excerpt_en"] == "EN[ja:供給不足]"
    assert by_id["tw1"]["excerpt_en"] == "EN[zt:供給吃緊]"
    assert by_id["fr1"]["excerpt_en"] == ""
    assert stats["english"] == 1
    assert stats["skipped_existing"] == 1
    assert stats["translated"] == 4
    assert stats["unsupported_lang"] == 1
    # Original excerpts never mutated.
    assert by_id["zh1"]["excerpt"] == "存储紧张"
    assert by_id["de1"]["excerpt"] == "Lieferengpass"


def test_translate_row_excerpt_uses_cache_and_rejects_english():
    cached = _row(excerpt_en="Cached EN")
    assert translate_row_excerpt(cached) == "Cached EN"

    with pytest.raises(ValueError, match="english_signal"):
        translate_row_excerpt(_row(language="en", excerpt="hi"))

    with patch(
        "src.translate_excerpts.ensure_argos_for_langs", return_value=["de"]
    ), patch(
        "src.translate_excerpts.translate_text", return_value="Shortage"
    ):
        en = translate_row_excerpt(
            _row(language="de", excerpt="Engpass", excerpt_en="", source="UREG")
        )
    assert en == "Shortage"


def test_persist_excerpt_en(tmp_path: Path):
    path = tmp_path / "signals.csv"
    rows = [
        _row(signal_id="ko1", language="ko"),
        _row(signal_id="en1", language="en", excerpt="EN", source="EDGAR"),
    ]
    write_csv(path, rows, SIGNAL_COLUMNS)
    assert persist_excerpt_en(path, "ko1", "Hello memory")
    with path.open(encoding="utf-8", newline="") as fh:
        saved = {r["signal_id"]: r for r in csv.DictReader(fh)}
    assert saved["ko1"]["excerpt_en"] == "Hello memory"
    # English must stay blank even if persist is called.
    assert not persist_excerpt_en(path, "en1", "should not stick")
    with path.open(encoding="utf-8", newline="") as fh:
        saved = {r["signal_id"]: r for r in csv.DictReader(fh)}
    assert saved["en1"]["excerpt_en"] == ""


def test_extract_source_language_defaults_cover_asia_and_ureg():
    """Source→language defaults include Asian filings + UREG/BaFin DE."""
    import src.extract as extract_mod

    text = Path(extract_mod.__file__).read_text(encoding="utf-8")
    mapping = {
        "DART": "ko",
        "CNINFO": "zh",
        "MOPS": "zh-TW",
        "TWSE": "zh-TW",
        "EDINET": "ja",
        "UREG": "de",
        "BAFIN_UREG": "de",
        "BAFIN": "de",
    }
    for src_name, lang in mapping.items():
        assert f'"{src_name}": "{lang}"' in text


@pytest.fixture()
def translate_client(tmp_path: Path) -> tuple[TestClient, Path]:
    signals = tmp_path / "signals.csv"
    sightings = tmp_path / "sightings.csv"
    votes = tmp_path / "votes.csv"
    companies = tmp_path / "companies.yaml"
    rows = [
        _row(
            signal_id="ko_cached",
            language="ko",
            excerpt="한글",
            excerpt_en="Korean cached",
            source="DART",
        ),
        _row(
            signal_id="zh_need",
            language="zh",
            excerpt="存储紧张",
            excerpt_en="",
            source="CNINFO",
        ),
        _row(
            signal_id="de_need",
            language="de",
            excerpt="Lieferengpass bei Speicher",
            excerpt_en="",
            source="UREG",
        ),
        _row(
            signal_id="en_row",
            language="en",
            excerpt="English filing text",
            excerpt_en="",
            source="EDGAR",
        ),
        _row(
            signal_id="fr_row",
            language="fr",
            excerpt="pénurie",
            excerpt_en="",
            source="AMF",
        ),
    ]
    write_csv(signals, rows, SIGNAL_COLUMNS)
    sightings.write_text(
        "sighting_id,signal_id,filing_date,form,section,match,source_url,excerpt\n",
        encoding="utf-8",
    )
    votes.write_text("vote_id,signal_id,user,vote,voted_at\n", encoding="utf-8")
    companies.write_text("companies: []\n", encoding="utf-8")
    app = create_app(
        signals_path=signals,
        sightings_path=sightings,
        votes_path=votes,
        companies_path=companies,
        mount_static=False,
        mode="single",
        broker_sim_open=True,
    )
    return TestClient(app), signals


def test_api_translate_cached_and_ondemand(translate_client):
    client, signals = translate_client

    r = client.post("/api/signals/ko_cached/translate")
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is True
    assert body["excerpt_en"] == "Korean cached"
    assert body["language"] == "ko"

    with patch(
        "src.translate_excerpts.ensure_argos_for_langs", return_value=["zh"]
    ), patch(
        "src.translate_excerpts.translate_text", return_value="Storage is tight"
    ):
        r = client.post("/api/signals/zh_need/translate")
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is False
    assert body["excerpt_en"] == "Storage is tight"
    assert body["language"] == "zh"

    # Persisted for subsequent calls.
    with signals.open(encoding="utf-8", newline="") as fh:
        saved = {row["signal_id"]: row for row in csv.DictReader(fh)}
    assert saved["zh_need"]["excerpt_en"] == "Storage is tight"
    assert saved["zh_need"]["excerpt"] == "存储紧张"

    r2 = client.post("/api/signals/zh_need/translate")
    assert r2.json()["cached"] is True


def test_api_translate_de_ureg(translate_client):
    client, _signals = translate_client
    with patch(
        "src.translate_excerpts.ensure_argos_for_langs", return_value=["de"]
    ), patch(
        "src.translate_excerpts.translate_text",
        return_value="Memory supply bottleneck",
    ):
        r = client.post("/api/signals/de_need/translate")
    assert r.status_code == 200
    assert r.json()["excerpt_en"] == "Memory supply bottleneck"
    assert r.json()["language"] == "de"


def test_api_translate_rejects_english_and_unknown(translate_client):
    client, _ = translate_client
    r = client.post("/api/signals/en_row/translate")
    assert r.status_code == 400
    assert "english" in r.json()["detail"].lower()

    r = client.post("/api/signals/fr_row/translate")
    assert r.status_code == 422

    r = client.post("/api/signals/missing/translate")
    assert r.status_code == 404


def test_api_signals_exposes_excerpt_en_for_foreign(translate_client):
    client, _ = translate_client
    body = client.get("/api/signals").json()
    by_id = {r["signal_id"]: r for r in body}
    assert by_id["ko_cached"]["language"] == "ko"
    assert by_id["ko_cached"]["excerpt_en"] == "Korean cached"
    assert by_id["en_row"]["excerpt_en"] == ""
    assert by_id["de_need"]["language"] == "de"
