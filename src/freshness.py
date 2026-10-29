"""Last-filing freshness status + polite stale-only EDGAR refresh.

Usage:
  python -m src.freshness status
  python -m src.freshness status --stale-days 45 --tier 1
  python -m src.freshness refresh --stale-days 45 --max-rps 1 --dry-run
  python -m src.freshness refresh --stale-days 45 --max-rps 1
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.fetch_edgar import (
    REPO_ROOT,
    Company,
    RateLimitedSession,
    fetch_company_filings,
    load_companies,
    load_yaml,
    resolve_user_agent,
)

DEFAULT_SETTINGS = REPO_ROOT / "config" / "settings.yaml"
DEFAULT_COMPANIES = REPO_ROOT / "config" / "companies.yaml"
DEFAULT_STALE_DAYS = 45


@dataclass
class FreshnessRow:
    ticker: str
    name: str
    tier: int
    last_filing_date: str
    last_form: str
    age_days: int | None
    n_filings: int
    fiscal_year_end: str
    stale: bool
    missing: bool


def _parse_iso_date(value: str) -> date | None:
    raw = (value or "").strip()[:10]
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _latest_meta(ticker_dir: Path) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_date = ""
    for meta_path in ticker_dir.glob("*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("prior_only"):
            continue
        fd = str(meta.get("filing_date") or "")
        if fd >= best_date:
            best_date = fd
            best = meta
    return best


def scan_freshness(
    companies: list[Company],
    raw_root: Path,
    *,
    stale_days: int,
    as_of: date | None = None,
) -> list[FreshnessRow]:
    today = as_of or datetime.now(timezone.utc).date()
    rows: list[FreshnessRow] = []
    for co in companies:
        if co.source.upper() != "EDGAR":
            continue
        ticker_dir = raw_root / co.ticker
        if not ticker_dir.is_dir():
            rows.append(
                FreshnessRow(
                    ticker=co.ticker,
                    name=co.name,
                    tier=co.tier,
                    last_filing_date="",
                    last_form="",
                    age_days=None,
                    n_filings=0,
                    fiscal_year_end="",
                    stale=True,
                    missing=True,
                )
            )
            continue
        n = sum(
            1
            for p in ticker_dir.glob("*/meta.json")
            if not json.loads(p.read_text(encoding="utf-8")).get("prior_only")
        )
        meta = _latest_meta(ticker_dir) or {}
        fd = str(meta.get("filing_date") or "")
        d = _parse_iso_date(fd)
        age = (today - d).days if d else None
        stale = age is None or age >= stale_days
        rows.append(
            FreshnessRow(
                ticker=co.ticker,
                name=co.name,
                tier=co.tier,
                last_filing_date=fd,
                last_form=str(meta.get("form") or ""),
                age_days=age,
                n_filings=n,
                fiscal_year_end=str(meta.get("fiscal_year_end") or ""),
                stale=stale,
                missing=False,
            )
        )
    rows.sort(key=lambda r: (not r.stale, -(r.age_days or 10**9), r.ticker))
    return rows


def print_status(rows: list[FreshnessRow], *, stale_days: int) -> None:
    stale = [r for r in rows if r.stale]
    print(
        f"EDGAR freshness (stale ≥ {stale_days}d): "
        f"{len(stale)}/{len(rows)} stale or missing"
    )
    print(
        f"{'TICKER':6} {'T':>1} {'AGE':>4} {'LAST':10} {'FORM':6} "
        f"{'N':>3} {'FYE':4} STATUS"
    )
    for r in rows:
        age = "—" if r.age_days is None else str(r.age_days)
        status = "MISSING" if r.missing else ("STALE" if r.stale else "ok")
        print(
            f"{r.ticker:6} {r.tier:>1} {age:>4} {r.last_filing_date or '—':10} "
            f"{r.last_form or '—':6} {r.n_filings:>3} {r.fiscal_year_end or '—':4} {status}"
        )


def write_calendar_csv(path: Path, rows: list[FreshnessRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "ticker",
                "name",
                "tier",
                "last_filing_date",
                "last_form",
                "age_days",
                "n_filings",
                "fiscal_year_end",
                "stale",
                "missing",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "ticker": r.ticker,
                    "name": r.name,
                    "tier": r.tier,
                    "last_filing_date": r.last_filing_date,
                    "last_form": r.last_form,
                    "age_days": "" if r.age_days is None else r.age_days,
                    "n_filings": r.n_filings,
                    "fiscal_year_end": r.fiscal_year_end,
                    "stale": r.stale,
                    "missing": r.missing,
                }
            )
    print(f"Wrote {path}")


def cmd_status(args: argparse.Namespace) -> int:
    settings = load_yaml(args.settings)
    companies = load_companies(args.companies)
    if args.tier is not None:
        companies = [c for c in companies if c.tier == args.tier]
    raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")
    rows = scan_freshness(companies, raw_root, stale_days=args.stale_days)
    print_status(rows, stale_days=args.stale_days)
    if args.out:
        write_calendar_csv(args.out, rows)
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    settings = load_yaml(args.settings)
    companies = load_companies(args.companies)
    edgar = [c for c in companies if c.source.upper() == "EDGAR"]
    if args.tier is not None:
        edgar = [c for c in edgar if c.tier == args.tier]
    if args.ticker:
        want = args.ticker.upper()
        edgar = [c for c in edgar if c.ticker == want]
        if not edgar:
            raise SystemExit(f"No EDGAR company {want}")
    raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")
    rows = scan_freshness(edgar, raw_root, stale_days=args.stale_days)
    by_ticker = {c.ticker: c for c in edgar}
    targets = [by_ticker[r.ticker] for r in rows if r.stale and r.ticker in by_ticker]
    print(
        f"Stale/missing targets: {len(targets)} "
        f"(stale_days={args.stale_days}, max_rps={args.max_rps})"
    )
    if args.dry_run:
        for c in targets:
            print(f"  would refresh {c.ticker}")
        return 0
    if not targets:
        print("Nothing to refresh.")
        return 0

    ua = resolve_user_agent()
    session = RateLimitedSession(ua, max_rps=min(max(args.max_rps, 0.2), 8.0))
    summary: list[dict[str, Any]] = []
    for company in targets:
        try:
            metas = fetch_company_filings(company, settings, session)
            summary.append({"ticker": company.ticker, "filings": len(metas), "ok": True})
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {company.ticker}: {exc}", file=sys.stderr)
            summary.append(
                {"ticker": company.ticker, "filings": 0, "ok": False, "error": str(exc)}
            )
    print(json.dumps({"refreshed": summary}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="EDGAR last-filing freshness status and stale-only refresh"
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--companies", type=Path, default=DEFAULT_COMPANIES)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print last-filing age per EDGAR company")
    p_status.add_argument(
        "--stale-days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        help=f"Mark stale when last filing is ≥ N days old (default {DEFAULT_STALE_DAYS})",
    )
    p_status.add_argument("--tier", type=int, choices=(1, 2, 3))
    p_status.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional CSV path (e.g. data/company_calendar.csv)",
    )
    p_status.set_defaults(func=cmd_status)

    p_refresh = sub.add_parser(
        "refresh", help="Polite EDGAR refresh for stale/missing tickers only"
    )
    p_refresh.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    p_refresh.add_argument("--tier", type=int, choices=(1, 2, 3))
    p_refresh.add_argument("--ticker")
    p_refresh.add_argument(
        "--max-rps",
        type=float,
        default=1.0,
        help="SEC rate limit (default 1.0 — stay polite)",
    )
    p_refresh.add_argument(
        "--dry-run",
        action="store_true",
        help="List stale tickers without calling SEC",
    )
    p_refresh.set_defaults(func=cmd_refresh)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
