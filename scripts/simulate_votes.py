#!/usr/bin/env python3
"""Simulate several users voting on Memory Signals.

Appends to data/votes.csv using the same locked writer as POST /api/votes.
Does not invent excerpts — only votes on existing signal_ids.

Usage:
  python3 -m scripts.simulate_votes
  python3 -m scripts.simulate_votes --reset          # wipe simulated users' prior rows first
  python3 -m scripts.simulate_votes --cover 0.7 --seed 42
  python3 -m scripts.simulate_votes --api http://127.0.0.1:8000   # POST via HTTP instead
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.server import (  # noqa: E402
    DEFAULT_SIGNALS,
    DEFAULT_VOTES,
    VOTE_COLUMNS,
    _append_vote_locked,
    _read_csv,
)

# Invented personas for demos — not real people.
DEFAULT_USERS = [
    "Ava Chen",
    "Marcus Webb",
    "Priya Nair",
    "Jordan Lee",
    "Sam Ortiz",
    "Riley Park",
]


def _signal_ids(path: Path) -> list[str]:
    rows = _read_csv(path)
    return [r["signal_id"] for r in rows if r.get("signal_id")]


def _strip_users(votes_path: Path, users: set[str]) -> int:
    """Remove existing rows for the simulated users (keeps Alice/Bob fixtures)."""
    if not votes_path.is_file():
        return 0
    rows = _read_csv(votes_path)
    kept = [r for r in rows if (r.get("user") or "") not in users]
    removed = len(rows) - len(kept)
    with votes_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=VOTE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in kept:
            writer.writerow({k: row.get(k, "") for k in VOTE_COLUMNS})
    return removed


def _cast(
    *,
    signal_id: str,
    user: str,
    vote: int,
    votes_path: Path,
    api_base: str | None,
) -> None:
    if api_base:
        resp = requests.post(
            f"{api_base.rstrip('/')}/api/votes",
            json={"signal_id": signal_id, "user": user, "vote": vote},
            timeout=30,
        )
        resp.raise_for_status()
        return
    _append_vote_locked(votes_path, signal_id=signal_id, user=user, vote=vote)


def simulate(
    *,
    signals_path: Path = DEFAULT_SIGNALS,
    votes_path: Path = DEFAULT_VOTES,
    users: list[str] | None = None,
    cover: float = 0.72,
    seed: int = 42,
    reset: bool = False,
    api_base: str | None = None,
    min_voters: int = 1,
    max_voters: int = 4,
) -> dict[str, int]:
    rng = random.Random(seed)
    users = list(users or DEFAULT_USERS)
    ids = _signal_ids(signals_path)
    if not ids:
        raise SystemExit(f"No signal_ids in {signals_path}")

    if reset and not api_base:
        removed = _strip_users(votes_path, set(users))
        print(f"reset: removed {removed} prior row(s) for simulated users")

    n_cover = max(1, min(len(ids), int(round(len(ids) * cover))))
    covered = rng.sample(ids, n_cover)
    # Leave the rest completely unvoted by these users.
    written = 0
    up = down = 0
    for sid in covered:
        n_voters = rng.randint(min_voters, min(max_voters, len(users)))
        voters = rng.sample(users, n_voters)
        # Bias slightly toward Useful so the feed looks constructive.
        for user in voters:
            vote = 1 if rng.random() < 0.62 else -1
            _cast(
                signal_id=sid,
                user=user,
                vote=vote,
                votes_path=votes_path,
                api_base=api_base,
            )
            written += 1
            if vote == 1:
                up += 1
            else:
                down += 1

    return {
        "signals": len(ids),
        "covered": len(covered),
        "unvoted_signals": len(ids) - len(covered),
        "votes_written": written,
        "up": up,
        "down": down,
        "users": len(users),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    p.add_argument("--votes", type=Path, default=DEFAULT_VOTES)
    p.add_argument("--cover", type=float, default=0.72, help="Fraction of signals that get ≥1 vote")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--reset", action="store_true", help="Drop prior rows for simulated users first")
    p.add_argument(
        "--api",
        default="",
        help="If set (e.g. http://127.0.0.1:8000), POST votes through the live API",
    )
    args = p.parse_args(argv)
    if not (0 < args.cover <= 1):
        raise SystemExit("--cover must be in (0, 1]")

    stats = simulate(
        signals_path=args.signals,
        votes_path=args.votes,
        cover=args.cover,
        seed=args.seed,
        reset=args.reset,
        api_base=args.api or None,
    )
    print(
        f"simulate_votes: {stats['votes_written']} votes "
        f"({stats['up']} up / {stats['down']} down) from {stats['users']} users; "
        f"covered {stats['covered']}/{stats['signals']} signals "
        f"({stats['unvoted_signals']} left unvoted)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
