"""CLI entry: python -m dram_signals [once|schedule|status]."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta

import schedule

from dram_signals import config
from dram_signals.collectors.edgar import SOURCE, EdgarCollector
from dram_signals.db import (
    count_signals,
    db_session,
    get_watermark,
    set_collector_state,
    upsert_signals,
)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def run_edgar_once(
    *,
    start: date | None,
    end: date | None,
    lookback_days: int | None,
    fetch_documents: bool,
) -> int:
    logger = logging.getLogger("dram_signals.run")

    with db_session(config.DB_PATH) as conn:
        watermark = get_watermark(conn, SOURCE)

    if start is None and watermark:
        # Inclusive overlap of 1 day to catch late-indexed filings
        try:
            start = date.fromisoformat(watermark[:10]) - timedelta(days=1)
        except ValueError:
            start = None

    collector = EdgarCollector(
        lookback_days=lookback_days or config.EDGAR_DEFAULT_LOOKBACK_DAYS,
    )
    records = collector.collect(start=start, end=end, fetch_documents=fetch_documents)

    with db_session(config.DB_PATH) as conn:
        inserted = upsert_signals(conn, records)
        newest = max((r.timestamp for r in records), default=None)
        set_collector_state(conn, SOURCE, watermark=newest)
        total = count_signals(conn, SOURCE)

    logger.info(
        "EDGAR run complete: fetched=%d inserted=%d total_in_db=%d db=%s",
        len(records),
        inserted,
        total,
        config.DB_PATH,
    )
    return inserted


def cmd_once(args: argparse.Namespace) -> int:
    run_edgar_once(
        start=args.start,
        end=args.end,
        lookback_days=args.lookback_days,
        fetch_documents=not args.metadata_only,
    )
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    logger = logging.getLogger("dram_signals.schedule")
    hours = args.every_hours or config.SCHEDULE_HOURS

    def job() -> None:
        try:
            run_edgar_once(
                start=None,
                end=None,
                lookback_days=args.lookback_days,
                fetch_documents=not args.metadata_only,
            )
        except Exception:
            logger.exception("Scheduled EDGAR run failed")

    logger.info("Scheduling EDGAR collector every %s hour(s); first run now", hours)
    job()
    schedule.every(hours).hours.do(job)

    while True:
        schedule.run_pending()
        time.sleep(30)


def cmd_status(_args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        total = count_signals(conn)
        edgar = count_signals(conn, SOURCE)
        watermark = get_watermark(conn, SOURCE)
        rows = conn.execute(
            """
            SELECT company_name, timestamp, keyword, url
            FROM signals
            WHERE source = ?
            ORDER BY timestamp DESC
            LIMIT 5
            """,
            (SOURCE,),
        ).fetchall()

    print(f"db: {config.DB_PATH}")
    print(f"signals total: {total}")
    print(f"signals edgar_8k: {edgar}")
    print(f"edgar watermark: {watermark or '(none)'}")
    if rows:
        print("latest:")
        for row in rows:
            print(
                f"  {row['timestamp'][:10]}  {row['company_name'][:40]:<40}  "
                f"kw={row['keyword']}  {row['url']}"
            )
    return 0


def _add_verbose(p: argparse.ArgumentParser) -> None:
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dram_signals",
        description="DRAM market signal collectors (Phase 1: EDGAR 8-K).",
    )
    _add_verbose(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    once = sub.add_parser("once", help="Run the EDGAR collector once and exit")
    _add_verbose(once)
    once.add_argument("--start", type=parse_date, help="Start date YYYY-MM-DD")
    once.add_argument("--end", type=parse_date, help="End date YYYY-MM-DD")
    once.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help=f"Lookback when no watermark (default {config.EDGAR_DEFAULT_LOOKBACK_DAYS})",
    )
    once.add_argument(
        "--metadata-only",
        action="store_true",
        help="Skip fetching filing bodies (search hits only)",
    )
    once.set_defaults(func=cmd_once)

    sched = sub.add_parser("schedule", help="Run on a repeating interval")
    _add_verbose(sched)
    sched.add_argument(
        "--every-hours",
        type=float,
        default=None,
        help=f"Interval hours (default {config.SCHEDULE_HOURS})",
    )
    sched.add_argument("--lookback-days", type=int, default=None)
    sched.add_argument("--metadata-only", action="store_true")
    sched.set_defaults(func=cmd_schedule)

    status = sub.add_parser("status", help="Show DB counts and recent rows")
    _add_verbose(status)
    status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
