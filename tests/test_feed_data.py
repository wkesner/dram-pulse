"""Tests for Memory Signals feed helpers."""

from __future__ import annotations

from pathlib import Path

from src.feed_data import (
    append_vote,
    family_table,
    filter_signals,
    load_signals,
    load_votes,
    order_feed,
    vote_counts_for_signal,
)


def test_two_users_vote_same_signal(tmp_path: Path):
    votes_path = tmp_path / "votes.csv"
    sid = "abc123def456"
    append_vote(sid, "Alice", 1, path=votes_path)
    append_vote(sid, "Bob", -1, path=votes_path)
    votes = load_votes(votes_path)
    assert len(votes) == 2
    users = {r["user"] for r in votes}
    assert users == {"Alice", "Bob"}
    up, down = vote_counts_for_signal(sid, votes)
    assert up == 1
    assert down == 1
    # Alice changes mind → new row; latest wins
    append_vote(sid, "Alice", -1, path=votes_path)
    votes = load_votes(votes_path)
    assert len(votes) == 3
    up, down = vote_counts_for_signal(sid, votes)
    assert up == 0
    assert down == 2


def test_order_feed_unvoted_first_newest_last_seen():
    signals = [
        {
            "signal_id": "a",
            "filing_date": "2026-01-01",
            "last_seen": "2026-01-01",
        },
        {
            "signal_id": "b",
            "filing_date": "2026-06-01",
            "last_seen": "2026-06-01",
        },
        {
            "signal_id": "c",
            "filing_date": "2026-03-01",
            "last_seen": "2026-09-01",
        },
    ]
    votes = [
        {
            "vote_id": "1",
            "signal_id": "b",
            "user": "Alice",
            "vote": "1",
            "voted_at": "2026-10-01T00:00:00+00:00",
        }
    ]
    ordered = order_feed(signals, "Alice", votes)
    # Unvoted first (a, c), then voted (b). Among unvoted, newest last_seen first → c then a.
    assert [r["signal_id"] for r in ordered] == ["c", "a", "b"]


def test_family_table_matches_signals_csv():
    signals = load_signals()
    if not signals:
        return
    # Pick the family with the most rows for a stable check.
    from collections import Counter

    fam = Counter(r["product_family"] for r in signals).most_common(1)[0][0]
    subset = [r for r in signals if r["product_family"] == fam]
    table = {r["product_family"]: r for r in family_table(signals)}
    row = table[fam]
    assert row["total"] == len(subset)
    assert row["tightening"] == sum(
        1 for r in subset if r["supply_direction"] == "tightening"
    )
    assert row["easing"] == sum(1 for r in subset if r["supply_direction"] == "easing")
    assert row["stable"] == sum(1 for r in subset if r["supply_direction"] == "stable")
    assert row["unclear"] == sum(
        1 for r in subset if r["supply_direction"] not in {"tightening", "easing", "stable"}
    )
    assert row["supplier_signals"] == sum(1 for r in subset if r["role"] == "supplier")
    assert row["buyer_signals"] == sum(1 for r in subset if r["role"] == "buyer")
    assert row["latest_filing_date"] == max(
        (r.get("last_seen") or r["filing_date"]) for r in subset
    )


def test_filter_signals():
    signals = [
        {"company": "Micron", "role": "supplier", "product_family": "HBM", "product_category": "DRAM", "supply_direction": "tightening", "source": "EDGAR"},
        {"company": "Apple", "role": "buyer", "product_family": "HBM", "product_category": "DRAM", "supply_direction": "easing", "source": "EDGAR"},
    ]
    out = filter_signals(signals, role="buyer")
    assert len(out) == 1
    assert out[0]["company"] == "Apple"
