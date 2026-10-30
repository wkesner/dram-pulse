"""Multi-language excerpt_en pipeline (KO/ZH/JA/DE + aliases)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.dedupe import SIGNAL_COLUMNS, write_csv
from src.translate_excerpts import (
    PRIMARY_SOURCE_LANGS,
    argos_source_code,
    display_lang,
    is_english,
    persist_excerpt_en,
    translate_excerpt,
    translate_signals,
    translate_text,
)


@pytest.mark.parametrize(
    "lang,code",
    [
        ("en", "en"),
        ("EN", "en"),
        ("english", "en"),
        ("ko", "ko"),
        ("korean", "ko"),
        ("zh", "zh"),
        ("zh-CN", "zh"),
        ("zh-tw", "zt"),
        ("zh-TW", "zt"),
        ("zh-Hant", "zt"),
        ("ja", "ja"),
        ("jp", "ja"),
        ("japanese", "ja"),
        ("de", "de"),
        ("german", "de"),
        ("deutsch", "de"),
        ("fr", "fr"),
        ("UREG-not-a-lang", None),
    ],
)
def test_argos_source_code_aliases(lang: str, code: str | None):
    assert argos_source_code(lang) == code


def test_primary_source_langs_cover_asia_and_de():
    # DART / CNINFO / MOPS / EDINET / UREG
    assert set(PRIMARY_SOURCE_LANGS) >= {"ko", "zh", "zt", "ja", "de"}


@pytest.mark.parametrize(
    "lang,label",
    [
        ("ko", "ko"),
        ("zh", "zh"),
        ("zh-TW", "zh-TW"),
        ("ja", "ja"),
        ("de", "de"),
    ],
)
def test_display_lang(lang: str, label: str):
    assert display_lang(lang) == label


def test_is_english():
    assert is_english("en")
    assert is_english("")
    assert not is_english("ko")
    assert not is_english("de")
    assert not is_english("zh-TW")


def _row(**kwargs: str) -> dict[str, str]:
    base = {c: "" for c in SIGNAL_COLUMNS}
    base.update(kwargs)
    return base


def test_translate_signals_skips_english_and_translates_foreign(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_translate(text: str, *, source: str = "ko", target: str = "en") -> str:
        calls.append((source, text[:12]))
        return f"EN[{source}]:{text}"

    monkeypatch.setattr(
        "src.translate_excerpts.translate_text", fake_translate
    )
    monkeypatch.setattr(
        "src.translate_excerpts.ensure_argos_for_langs",
        lambda langs, install=True: [],
    )

    rows = [
        _row(signal_id="e1", language="en", excerpt="English DRAM supply"),
        _row(signal_id="k1", language="ko", excerpt="한국어 DRAM"),
        _row(signal_id="z1", language="zh", excerpt="中文 DRAM"),
        _row(signal_id="t1", language="zh-TW", excerpt="繁體 DRAM"),
        _row(signal_id="j1", language="ja", excerpt="日本語 DRAM"),
        _row(signal_id="d1", language="de", excerpt="Deutsch DRAM"),
        _row(
            signal_id="k2",
            language="ko",
            excerpt="이미 번역",
            excerpt_en="Already EN",
        ),
    ]
    out, stats = translate_signals(rows, force=False, dry_run=False, install=False)
    by_id = {r["signal_id"]: r for r in out}

    assert by_id["e1"]["excerpt_en"] == ""
    assert by_id["e1"]["excerpt"] == "English DRAM supply"
    assert by_id["k1"]["excerpt_en"].startswith("EN[ko]:")
    assert by_id["z1"]["excerpt_en"].startswith("EN[zh]:")
    assert by_id["t1"]["excerpt_en"].startswith("EN[zt]:")
    assert by_id["j1"]["excerpt_en"].startswith("EN[ja]:")
    assert by_id["d1"]["excerpt_en"].startswith("EN[de]:")
    assert by_id["k2"]["excerpt_en"] == "Already EN"
    # Originals unchanged
    assert by_id["k1"]["excerpt"] == "한국어 DRAM"
    assert by_id["d1"]["excerpt"] == "Deutsch DRAM"

    assert stats["english"] == 1
    assert stats["translated"] == 5
    assert stats["skipped_existing"] == 1
    assert {c[0] for c in calls} == {"ko", "zh", "zt", "ja", "de"}


def test_translate_signals_dry_run_does_not_call_argos(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("should not translate on dry-run")

    monkeypatch.setattr("src.translate_excerpts.translate_text", boom)
    rows = [_row(signal_id="d1", language="de", excerpt="Halbleiter DRAM")]
    out, stats = translate_signals(rows, dry_run=True, install=False)
    assert out[0]["excerpt_en"] == ""
    assert stats["translated"] == 1


def test_translate_excerpt_rejects_english():
    assert translate_excerpt("hello", "en", install=False) == ""


def test_translate_excerpt_unsupported():
    with pytest.raises(ValueError, match="unsupported"):
        translate_excerpt("x", "xx-unknown", install=False)


def test_persist_excerpt_en(tmp_path: Path, monkeypatch):
    path = tmp_path / "signals.csv"
    rows = [
        _row(signal_id="a", language="zh", excerpt="中文", excerpt_en=""),
        _row(signal_id="b", language="en", excerpt="EN", excerpt_en=""),
    ]
    write_csv(path, rows, SIGNAL_COLUMNS)
    assert persist_excerpt_en(path, "a", "Chinese DRAM")
    with path.open(encoding="utf-8", newline="") as fh:
        loaded = {r["signal_id"]: r for r in csv.DictReader(fh)}
    assert loaded["a"]["excerpt_en"] == "Chinese DRAM"
    assert loaded["a"]["excerpt"] == "中文"
    assert loaded["b"]["excerpt_en"] == ""
    assert not persist_excerpt_en(path, "missing", "x")


def test_translate_text_passthrough_empty():
    assert translate_text("", source="de", target="en") == ""


def test_extract_source_language_defaults_include_ureg_asia():
    """UREG→de and Asia portals stay wired for translate coverage."""
    import src.extract as extract_mod

    src = Path(extract_mod.__file__).read_text(encoding="utf-8")
    assert '"UREG": "de"' in src
    assert '"BAFIN": "de"' in src
    assert '"BAFIN_UREG": "de"' in src
    assert '"AMF": "fr"' in src
    assert '"EDINET": "ja"' in src
    assert '"CNINFO": "zh"' in src
    assert '"MOPS": "zh-TW"' in src
    assert argos_source_code("de") == "de"
    assert argos_source_code("zh-TW") == "zt"
    assert argos_source_code("ja") == "ja"
