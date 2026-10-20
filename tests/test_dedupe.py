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

# Expected from DEDUPE.md / dedupe_reference.py on the 0.1.0 fixture.
EXPECT_INPUT = 188
EXPECT_SIGHTINGS = 124
EXPECT_SIGNALS = 69
EXPECT_EXACT = 39
EXPECT_NEAR = 16

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
