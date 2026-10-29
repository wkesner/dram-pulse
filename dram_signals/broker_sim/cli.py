"""CLI for the DRAM broker market simulation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dram_signals.broker_sim.simulation import format_summary, run_simulation


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Simulate three DRAM component brokers buying under market and "
            "selling at/above market with overlapping customer books."
        )
    )
    p.add_argument("--days", type=int, default=30, help="Simulation days (default 30)")
    p.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    p.add_argument(
        "--starting-cash",
        type=float,
        default=250_000.0,
        help="Cash per broker at day 0 (default 250000; confirmed)",
    )
    p.add_argument(
        "--seed-catalog",
        type=Path,
        default=None,
        help="Path to seed_catalog.json (default data/seed_catalog.json)",
    )
    p.add_argument(
        "--companies",
        type=Path,
        default=None,
        help="Path to companies.yaml for buyer names",
    )
    p.add_argument("--offers-per-day", type=int, default=6)
    p.add_argument("--bids-per-day", type=int, default=8)
    prices = p.add_mutually_exclusive_group()
    prices.add_argument(
        "--live",
        dest="prefer_live",
        action="store_true",
        default=True,
        help="Prefer MemoryIndex live/public prices (default)",
    )
    prices.add_argument(
        "--no-live",
        dest="prefer_live",
        action="store_false",
        help="Force illustrative mid model (offline / reproducible baselines)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable summary JSON instead of the table",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.days < 1:
        raise SystemExit("--days must be >= 1")
    result = run_simulation(
        days=args.days,
        seed=args.seed,
        starting_cash=args.starting_cash,
        seed_catalog=args.seed_catalog,
        companies_path=args.companies,
        offers_per_day=args.offers_per_day,
        bids_per_day=args.bids_per_day,
        prefer_live_prices=args.prefer_live,
    )
    if args.json:
        print(json.dumps(result.summary(), indent=2, default=str))
    else:
        print(format_summary(result))
    return 0
