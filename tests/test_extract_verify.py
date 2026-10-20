"""Smoke tests for extract helpers and verify."""

from __future__ import annotations

from pathlib import Path

from src.dedupe import SIGNAL_COLUMNS, SIGHTING_COLUMNS
from src.extract import normalize_text, signal_id, split_sentences
from src.verify import verify_csv

REPO = Path(__file__).resolve().parents[1]


def test_signal_id_stable():
    a = signal_id("https://example.com/a", 10, 20, "HBM")
    b = signal_id("https://example.com/a", 10, 20, "HBM")
    c = signal_id("https://example.com/a", 10, 20, "DRAM_general")
    assert a == b
    assert len(a) == 12
    assert a != c


def test_split_sentences_basic():
    text = "Demand for HBM is strong. Supply is constrained. Prices rose."
    spans = split_sentences(text)
    assert len(spans) >= 2
    assert "Demand for HBM" in spans[0][2]


def test_normalize_text():
    assert normalize_text("  A\nB  ") == "a b"


def test_sightings_csv_verifies_if_present():
    csv_path = REPO / "data" / "sightings.csv"
    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        return
    total, passed, failures = verify_csv(csv_path)
    assert total > 0
    assert passed == total
    assert not failures
    header = csv_path.read_text(encoding="utf-8").splitlines()[0].split(",")
    for col in ("sighting_id", "signal_id", "char_start", "char_end", "raw_path", "match"):
        assert col in header


def test_signals_csv_has_dedupe_columns_if_present():
    csv_path = REPO / "data" / "signals.csv"
    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        return
    header = csv_path.read_text(encoding="utf-8").splitlines()[0].split(",")
    # After dedupe rebuild, header matches SIGNAL_COLUMNS.
    if "first_seen" in header:
        assert header == SIGNAL_COLUMNS
        assert "repeat_of" not in header
    else:
        # Pre-dedupe checkout — skip strict column assert until extract re-runs.
        assert "signal_id" in header
