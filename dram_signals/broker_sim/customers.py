"""Customer universe with seeded, partially overlapping broker books.

Assumptions:

* Seed buyers come from ``config/companies.yaml`` (``role: buyer``) plus a
  small set of invented EMS / ODM names so the book is large enough to show
  overlap statistics.
* Assignment is **not** three disjoint books. For each customer the RNG picks
  a membership pattern:

  - ~15% all three brokers
  - ~30% a random pair
  - ~55% a single random broker

* Optional per-broker affinity weights tilt which broker gets exclusive /
  pair slots (Summit tends broader, Harbor more selective).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPANIES = REPO_ROOT / "config" / "companies.yaml"

# Invented demo accounts — not real customers.
EXTRA_CUSTOMERS = [
    "Northwind EMS",
    "Cedar Board Assemblies",
    "Lakeview Industrial Controls",
    "Redwood Robotics",
    "Atlas Medical Devices",
    "Brightline Telecom OEMs",
    "Cascade Automotive Tier-2",
    "Ironclad Defense Systems",
    "PixelForge Gaming",
    "Meridian Storage Labs",
]


@dataclass(frozen=True)
class Customer:
    name: str
    ticker: Optional[str] = None
    source: str = "seed"


@dataclass
class CustomerBook:
    """Maps broker_id → frozenset of customer names, plus reverse index."""

    customers: list[Customer]
    membership: dict[str, frozenset[str]]  # broker_id -> customer names
    by_customer: dict[str, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.by_customer:
            rev: dict[str, set[str]] = {}
            for broker_id, names in self.membership.items():
                for name in names:
                    rev.setdefault(name, set()).add(broker_id)
            self.by_customer = {k: frozenset(v) for k, v in rev.items()}

    def customers_for(self, broker_id: str) -> frozenset[str]:
        return self.membership.get(broker_id, frozenset())

    def overlap_stats(self) -> dict[str, float | int]:
        """Summary of how many customers sit on 1 / 2 / 3 broker books."""
        counts = {1: 0, 2: 0, 3: 0}
        for brokers in self.by_customer.values():
            n = len(brokers)
            if n in counts:
                counts[n] += 1
        total = len(self.by_customer) or 1
        pairwise: dict[str, int] = {}
        broker_ids = sorted(self.membership)
        for i, a in enumerate(broker_ids):
            for b in broker_ids[i + 1 :]:
                shared = self.membership[a] & self.membership[b]
                pairwise[f"{a}|{b}"] = len(shared)
        return {
            "customers": len(self.by_customer),
            "exclusive": counts[1],
            "on_two_brokers": counts[2],
            "on_three_brokers": counts[3],
            "overlap_rate": round((counts[2] + counts[3]) / total, 4),
            "pairwise_shared": pairwise,  # type: ignore[dict-item]
        }


def load_buyer_names(path: Optional[Path] = None) -> list[Customer]:
    """Buyers from companies.yaml (role=buyer), de-duplicated by name."""
    companies_path = Path(path) if path else DEFAULT_COMPANIES
    data = yaml.safe_load(companies_path.read_text(encoding="utf-8")) or {}
    seen: set[str] = set()
    out: list[Customer] = []
    for row in data.get("companies") or []:
        if str(row.get("role") or "").lower() != "buyer":
            continue
        name = str(row.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        ticker = row.get("ticker")
        out.append(
            Customer(
                name=name,
                ticker=str(ticker) if ticker is not None else None,
                source="companies.yaml",
            )
        )
    return out


def build_customer_universe(
    broker_ids: Sequence[str],
    *,
    rng: random.Random,
    companies_path: Optional[Path] = None,
    extra: Optional[Sequence[str]] = None,
    p_all: float = 0.15,
    p_pair: float = 0.30,
    affinities: Optional[dict[str, float]] = None,
) -> CustomerBook:
    """Build a shared customer list and overlapping broker memberships.

    Parameters
    ----------
    p_all, p_pair:
        Probabilities for all-three and random-pair membership. Remainder is
        exclusive to one broker. Must satisfy ``p_all + p_pair <= 1``.
    affinities:
        Relative weights used when sampling which broker(s) get a customer.
        Defaults to equal weight.
    """
    if p_all < 0 or p_pair < 0 or p_all + p_pair > 1:
        raise ValueError("p_all and p_pair must be >= 0 and sum to <= 1")
    ids = list(broker_ids)
    if len(ids) < 2:
        raise ValueError("Need at least two brokers for an overlapping book")

    customers = load_buyer_names(companies_path)
    for name in extra if extra is not None else EXTRA_CUSTOMERS:
        customers.append(Customer(name=name, source="invented"))

    weights = {b: float((affinities or {}).get(b, 1.0)) for b in ids}
    membership: dict[str, set[str]] = {b: set() for b in ids}

    def _weighted_sample(k: int) -> list[str]:
        pool = list(ids)
        chosen: list[str] = []
        for _ in range(k):
            w = [weights[b] for b in pool]
            pick = rng.choices(pool, weights=w, k=1)[0]
            chosen.append(pick)
            pool.remove(pick)
        return chosen

    for cust in customers:
        roll = rng.random()
        if roll < p_all:
            chosen = list(ids)
        elif roll < p_all + p_pair:
            chosen = _weighted_sample(2)
        else:
            chosen = _weighted_sample(1)
        for b in chosen:
            membership[b].add(cust.name)

    return CustomerBook(
        customers=customers,
        membership={b: frozenset(v) for b, v in membership.items()},
    )
