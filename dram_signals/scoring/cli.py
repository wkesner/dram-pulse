"""CLI: score classify | run | examples | status."""

from __future__ import annotations

import argparse
import json
import logging

from dram_signals import config
from dram_signals.db import db_session
from dram_signals.scoring.pipeline import classify_all_raw, score_open_leads
from dram_signals.scoring.scorer import (
    score_worked_example_1,
    score_worked_example_2,
    score_worked_example_3,
)
from dram_signals.scoring.seed import seed_reference_tables

logger = logging.getLogger("dram_signals.scoring")


def cmd_seed(_args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        stats = seed_reference_tables(conn)
    print(json.dumps(stats, indent=2))
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        stats = classify_all_raw(
            conn,
            broker_id=args.broker_id,
            limit=args.limit,
        )
    print(json.dumps(stats, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        cstats = classify_all_raw(conn, broker_id=args.broker_id, limit=args.limit)
        sstats = score_open_leads(conn, broker_id=args.broker_id)
    print(json.dumps({"classify": cstats, "score": sstats}, indent=2))
    return 0


def cmd_examples(_args: argparse.Namespace) -> int:
    for label, fn in (
        ("example_1_supply_A", score_worked_example_1),
        ("example_2_demand_D", lambda: score_worked_example_2(market_allocation=False)),
        ("example_2_demand_C_alloc", lambda: score_worked_example_2(market_allocation=True)),
        ("example_3_demand_A", score_worked_example_3),
    ):
        out = fn()
        ls = out.lead_score
        print(
            f"{label}: total={ls.total if ls else None} "
            f"S={ls.signal_strength if ls else None} "
            f"F={ls.fit if ls else None} "
            f"T={ls.timing if ls else None} "
            f"tier={ls.tier if ls else None}"
        )
        print(f"  reasons: {out.reasons}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        seed_reference_tables(conn)
        typed = conn.execute("SELECT COUNT(*) AS n FROM typed_signals").fetchone()["n"]
        companies = conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"]
        snaps = conn.execute("SELECT COUNT(*) AS n FROM score_snapshots").fetchone()["n"]
        print(f"db: {config.DB_PATH}")
        print(f"companies: {companies}  typed_signals: {typed}  snapshots: {snaps}")
        tiers = conn.execute(
            """
            SELECT tier, COUNT(*) AS n FROM score_snapshots
            WHERE gated = 0 AND broker_id = ?
            GROUP BY tier ORDER BY tier
            """,
            (args.broker_id,),
        ).fetchall()
        if tiers:
            print("latest snapshot tiers: " + ", ".join(f"{r['tier']}={r['n']}" for r in tiers))
        top = conn.execute(
            """
            SELECT company_key, side, score, tier, reasons_text
            FROM score_snapshots
            WHERE gated = 0 AND broker_id = ?
            ORDER BY routed_at DESC, score DESC
            LIMIT 10
            """,
            (args.broker_id,),
        ).fetchall()
        if top:
            print("recent scores:")
            for r in top:
                print(
                    f"  [{r['tier']}] {r['score']:5.1f}  {r['side']:<6}  "
                    f"{r['company_key']:<28}  {r['reasons_text']}"
                )
    return 0


def add_score_subparser(sub: argparse._SubParsersAction) -> None:
    score = sub.add_parser("score", help="Lead scoring: classify, run, examples, status")
    score_sub = score.add_subparsers(dest="score_command", required=True)

    def add_broker(p: argparse.ArgumentParser) -> None:
        p.add_argument("--broker-id", default="default", help="Broker workspace id")

    seed_p = score_sub.add_parser("seed", help="Load regime / end-build / allocation YAML")
    seed_p.set_defaults(func=cmd_seed)

    classify_p = score_sub.add_parser("classify", help="Classify raw signals → typed_signals")
    add_broker(classify_p)
    classify_p.add_argument("--limit", type=int, default=None)
    classify_p.set_defaults(func=cmd_classify)

    run_p = score_sub.add_parser("run", help="Classify + score open leads + snapshot")
    add_broker(run_p)
    run_p.add_argument("--limit", type=int, default=None)
    run_p.set_defaults(func=cmd_run)

    ex = score_sub.add_parser("examples", help="Print section 4.6 worked examples")
    ex.set_defaults(func=cmd_examples)

    st = score_sub.add_parser("status", help="Show scoring table counts and recent snapshots")
    add_broker(st)
    st.set_defaults(func=cmd_status)
