"""CLI entry: python -m dram_signals [once|schedule|status]."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta

import schedule

from dram_signals import config
from dram_signals.collectors.edgar import SOURCE as EDGAR_SOURCE
from dram_signals.collectors.edgar import EdgarCollector
from dram_signals.collectors.greenhouse import SOURCE as GREENHOUSE_SOURCE
from dram_signals.collectors.greenhouse import GreenhouseCollector
from dram_signals.collectors.lever import SOURCE as LEVER_SOURCE
from dram_signals.collectors.lever import LeverCollector
from dram_signals.collectors.icims import SOURCE as ICIMS_SOURCE
from dram_signals.collectors.icims import IcimsCollector
from dram_signals.collectors.workday import SOURCE as WORKDAY_SOURCE
from dram_signals.collectors.workday import WorkdayCollector
from dram_signals.db import (
    SignalRecord,
    count_signals,
    db_session,
    get_watermark,
    set_collector_state,
    upsert_signals,
)

logger = logging.getLogger("dram_signals.run")

ALL_SOURCES = ("edgar", "greenhouse", "lever", "workday", "icims")
SOURCE_ALIASES = {
    "edgar": ("edgar",),
    "edgar_8k": ("edgar",),
    "greenhouse": ("greenhouse",),
    "lever": ("lever",),
    "workday": ("workday",),
    "icims": ("icims",),
    "jobs": ("greenhouse", "lever", "workday", "icims"),
    "all": ALL_SOURCES,
}


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def resolve_sources(name: str) -> tuple[str, ...]:
    try:
        return SOURCE_ALIASES[name]
    except KeyError as exc:
        raise argparse.ArgumentTypeError(
            f"unknown source {name!r}; choose from {', '.join(SOURCE_ALIASES)}"
        ) from exc


def _persist(source: str, records: list[SignalRecord]) -> int:
    with db_session(config.DB_PATH) as conn:
        inserted = upsert_signals(conn, records)
        newest = max((r.timestamp for r in records), default=None)
        set_collector_state(conn, source, watermark=newest)
        total = count_signals(conn, source)
    logger.info(
        "%s run complete: fetched=%d inserted=%d total_in_db=%d db=%s",
        source,
        len(records),
        inserted,
        total,
        config.DB_PATH,
    )
    return inserted


def run_edgar_once(
    *,
    start: date | None,
    end: date | None,
    lookback_days: int | None,
    fetch_documents: bool,
) -> int:
    with db_session(config.DB_PATH) as conn:
        watermark = get_watermark(conn, EDGAR_SOURCE)

    if start is None and watermark:
        try:
            start = date.fromisoformat(watermark[:10]) - timedelta(days=1)
        except ValueError:
            start = None

    collector = EdgarCollector(
        lookback_days=lookback_days or config.EDGAR_DEFAULT_LOOKBACK_DAYS,
    )
    records = collector.collect(start=start, end=end, fetch_documents=fetch_documents)
    return _persist(EDGAR_SOURCE, records)


def run_greenhouse_once() -> int:
    return _persist(GREENHOUSE_SOURCE, GreenhouseCollector().collect())


def run_lever_once() -> int:
    return _persist(LEVER_SOURCE, LeverCollector().collect())


def run_workday_once() -> int:
    return _persist(WORKDAY_SOURCE, WorkdayCollector().collect())


def run_icims_once() -> int:
    return _persist(ICIMS_SOURCE, IcimsCollector().collect())


def run_sources(
    sources: tuple[str, ...],
    *,
    start: date | None = None,
    end: date | None = None,
    lookback_days: int | None = None,
    fetch_documents: bool = True,
) -> None:
    for source in sources:
        if source == "edgar":
            run_edgar_once(
                start=start,
                end=end,
                lookback_days=lookback_days,
                fetch_documents=fetch_documents,
            )
        elif source == "greenhouse":
            run_greenhouse_once()
        elif source == "lever":
            run_lever_once()
        elif source == "workday":
            run_workday_once()
        elif source == "icims":
            run_icims_once()
        else:
            raise ValueError(f"unsupported source: {source}")


def cmd_once(args: argparse.Namespace) -> int:
    run_sources(
        args.sources,
        start=args.start,
        end=args.end,
        lookback_days=args.lookback_days,
        fetch_documents=not args.metadata_only,
    )
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    sched_logger = logging.getLogger("dram_signals.schedule")
    hours = args.every_hours or config.SCHEDULE_HOURS

    def job() -> None:
        try:
            run_sources(
                args.sources,
                start=None,
                end=None,
                lookback_days=args.lookback_days,
                fetch_documents=not args.metadata_only,
            )
        except Exception:
            sched_logger.exception("Scheduled collector run failed")

    sched_logger.info(
        "Scheduling sources=%s every %s hour(s); first run now",
        ",".join(args.sources),
        hours,
    )
    job()
    schedule.every(hours).hours.do(job)

    while True:
        schedule.run_pending()
        time.sleep(30)


def cmd_status(_args: argparse.Namespace) -> int:
    source_ids = (
        EDGAR_SOURCE,
        GREENHOUSE_SOURCE,
        LEVER_SOURCE,
        WORKDAY_SOURCE,
        ICIMS_SOURCE,
    )
    with db_session(config.DB_PATH) as conn:
        total = count_signals(conn)
        print(f"db: {config.DB_PATH}")
        print(f"signals total: {total}")
        for source in source_ids:
            n = count_signals(conn, source)
            watermark = get_watermark(conn, source)
            print(f"signals {source}: {n}  watermark={watermark or '(none)'}")

        rows = conn.execute(
            """
            SELECT source, company_name, timestamp, keyword, url
            FROM signals
            ORDER BY collected_at DESC, timestamp DESC
            LIMIT 10
            """
        ).fetchall()

    if rows:
        print("latest:")
        for row in rows:
            print(
                f"  [{row['source']}] {row['timestamp'][:10]}  "
                f"{row['company_name'][:36]:<36}  "
                f"kw={row['keyword']}  {row['url']}"
            )
    return 0


def _add_verbose(p: argparse.ArgumentParser) -> None:
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")


def _add_source_arg(p: argparse.ArgumentParser, default: str = "all") -> None:
    p.add_argument(
        "--source",
        dest="sources",
        type=resolve_sources,
        default=resolve_sources(default),
        help=(
            "Collector(s) to run: edgar, greenhouse, lever, workday, icims, jobs "
            f"(all ATS), all (default: {default})"
        ),
    )


def _add_edgar_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", type=parse_date, help="EDGAR start date YYYY-MM-DD")
    p.add_argument("--end", type=parse_date, help="EDGAR end date YYYY-MM-DD")
    p.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help=f"EDGAR lookback when no watermark (default {config.EDGAR_DEFAULT_LOOKBACK_DAYS})",
    )
    p.add_argument(
        "--metadata-only",
        action="store_true",
        help="EDGAR: skip fetching filing bodies (search hits only)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dram_signals",
        description="DRAM market signal collectors (EDGAR + ATS job boards).",
    )
    _add_verbose(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    once = sub.add_parser("once", help="Run selected collectors once and exit")
    _add_verbose(once)
    _add_source_arg(once, default="all")
    _add_edgar_opts(once)
    once.set_defaults(func=cmd_once)

    sched = sub.add_parser("schedule", help="Run selected collectors on an interval")
    _add_verbose(sched)
    _add_source_arg(sched, default="all")
    _add_edgar_opts(sched)
    sched.add_argument(
        "--every-hours",
        type=float,
        default=None,
        help=f"Interval hours (default {config.SCHEDULE_HOURS})",
    )
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
