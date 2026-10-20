"""Fetch EDGAR filings for Memory Signals (Milestone 1: Micron 10-K).

Usage:
  export SEC_USER_AGENT="MemorySignals Your Name you@example.com"
  python -m src.fetch_edgar --ticker MU
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

from src.sections import (
    SECTION_BUSINESS,
    SECTION_MDNA,
    SECTION_OTHER,
    SECTION_RISK_FACTORS,
    split_filing,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = REPO_ROOT / "config" / "settings.yaml"
DEFAULT_COMPANIES = REPO_ROOT / "config" / "companies.yaml"

# Forms we care about in later milestones; M1 only caches 10-K.
M1_FORMS = ("10-K", "10-K/A")


@dataclass
class Company:
    name: str
    role: str
    source: str
    ticker: str


class RateLimitedSession:
    """requests.Session that stays under SEC's fair-access rate."""

    def __init__(self, user_agent: str, max_rps: float = 10.0) -> None:
        if max_rps <= 0:
            raise ValueError("max_rps must be positive")
        self.min_interval = 1.0 / max_rps
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json, text/html, */*",
            }
        )

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def get(self, url: str, *, retries: int = 4, timeout: float = 60.0) -> requests.Response:
        last_err: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=timeout)
                self._last = time.monotonic()
            except requests.RequestException as exc:
                last_err = exc
                time.sleep(min(2**attempt, 8))
                continue

            if resp.status_code in {403, 429, 502, 503, 504} and attempt + 1 < retries:
                wait = min(2**attempt, 16) + 0.5
                print(
                    f"HTTP {resp.status_code} for {url} — retry in {wait:.1f}s "
                    f"({attempt + 1}/{retries})",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp

        if last_err is not None:
            raise last_err
        raise RuntimeError(f"Failed to GET {url}")


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def load_companies(path: Path) -> list[Company]:
    data = load_yaml(path)
    out: list[Company] = []
    for row in data.get("companies") or []:
        out.append(
            Company(
                name=row["name"],
                role=row["role"],
                source=row["source"],
                ticker=str(row["ticker"]).upper(),
            )
        )
    return out


def resolve_user_agent() -> str:
    ua = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua:
        raise SystemExit(
            "SEC_USER_AGENT is required. Set it to "
            '"MemorySignals your-name your-email" (see .env.example).'
        )
    if "MemorySignals" not in ua:
        print(
            "Warning: SEC_USER_AGENT should start with MemorySignals …",
            file=sys.stderr,
        )
    return ua


def lookup_cik(session: RateLimitedSession, tickers_url: str, ticker: str) -> str:
    """Return zero-padded 10-digit CIK for ticker via company_tickers.json."""
    resp = session.get(tickers_url)
    payload = resp.json()
    want = ticker.upper()
    for entry in payload.values():
        if str(entry.get("ticker", "")).upper() == want:
            return f"{int(entry['cik_str']):010d}"
    raise LookupError(f"Ticker {ticker!r} not found in company_tickers.json")


def parse_filing_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def window_bounds(lookback_months: int, run_date: date | None = None) -> tuple[date, date]:
    end = run_date or date.today()
    # Approximate calendar months without pulling dateutil.
    year = end.year
    month = end.month - lookback_months
    while month <= 0:
        month += 12
        year -= 1
    day = min(end.day, _days_in_month(year, month))
    start = date(year, month, day)
    return start, end


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def iter_recent_filings(submissions: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten recent + optional older filings into dict rows."""
    recent = submissions.get("filings", {}).get("recent", {})
    rows: list[dict[str, str]] = []
    forms = recent.get("form") or []
    for i, form in enumerate(forms):
        rows.append(
            {
                "form": form,
                "filingDate": recent["filingDate"][i],
                "accessionNumber": recent["accessionNumber"][i],
                "primaryDocument": recent["primaryDocument"][i],
            }
        )
    return rows


def select_latest_10k(
    filings: list[dict[str, str]],
    window_start: date,
    window_end: date,
) -> dict[str, str] | None:
    """Latest 10-K (or 10-K/A) with filingDate inside the lookback window."""
    candidates: list[dict[str, str]] = []
    for row in filings:
        if row["form"] not in M1_FORMS:
            continue
        filed = parse_filing_date(row["filingDate"])
        if window_start <= filed <= window_end:
            candidates.append(row)
    if not candidates:
        return None
    candidates.sort(key=lambda r: r["filingDate"], reverse=True)
    return candidates[0]


def archive_url(cik: str, accession: str, primary_document: str, template: str) -> str:
    cik_int = str(int(cik))  # Archives paths drop leading zeros
    accession_no_dashes = accession.replace("-", "")
    return template.format(
        cik=cik_int,
        accession_no_dashes=accession_no_dashes,
        primary_document=primary_document,
    )


def cache_dir(raw_root: Path, ticker: str, accession: str) -> Path:
    return raw_root / ticker.upper() / accession.replace("-", "")


def write_sections(dest: Path, html: str, plain_sections: dict[str, str]) -> dict[str, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    raw_html_path = dest / "primary.html"
    raw_html_path.write_text(html, encoding="utf-8")
    paths["primary_html"] = raw_html_path

    mapping = {
        SECTION_BUSINESS: "item1_business.txt",
        SECTION_RISK_FACTORS: "item1a_risk_factors.txt",
        SECTION_MDNA: "item7_mdna.txt",
        SECTION_OTHER: "other_full.txt",
    }
    for key, filename in mapping.items():
        body = plain_sections.get(key)
        if body is None:
            continue
        path = dest / filename
        path.write_text(body, encoding="utf-8")
        paths[key] = path
    return paths


def fetch_company_10k(
    company: Company,
    settings: dict[str, Any],
    session: RateLimitedSession,
    *,
    run_date: date | None = None,
) -> dict[str, Any]:
    edgar = settings["edgar"]
    lookback = int(settings["lookback_months"])
    raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")
    window_start, window_end = window_bounds(lookback, run_date)

    cik = lookup_cik(session, edgar["company_tickers_url"], company.ticker)
    submissions_url = edgar["submissions_url_template"].format(cik=int(cik))
    submissions = session.get(submissions_url).json()

    filing = select_latest_10k(iter_recent_filings(submissions), window_start, window_end)
    if filing is None:
        raise RuntimeError(
            f"No 10-K for {company.ticker} in lookback window "
            f"{window_start.isoformat()} .. {window_end.isoformat()}"
        )

    url = archive_url(
        cik,
        filing["accessionNumber"],
        filing["primaryDocument"],
        edgar["archives_url_template"],
    )
    html = session.get(url).text
    sections = split_filing(html)
    dest = cache_dir(raw_root, company.ticker, filing["accessionNumber"])
    paths = write_sections(dest, html, sections)

    meta = {
        "company": company.name,
        "ticker": company.ticker,
        "cik": cik,
        "form": filing["form"],
        "filing_date": filing["filingDate"],
        "accession_number": filing["accessionNumber"],
        "primary_document": filing["primaryDocument"],
        "source_url": url,
        "lookback_months": lookback,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sections_present": sorted(k for k in sections if k != SECTION_OTHER) + [SECTION_OTHER],
        "paths": {k: str(v.relative_to(REPO_ROOT)) for k, v in paths.items()},
    }
    meta_path = dest / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch EDGAR filings for Memory Signals")
    parser.add_argument("--ticker", default="MU", help="Ticker to fetch (default: MU)")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--companies", type=Path, default=DEFAULT_COMPANIES)
    args = parser.parse_args(argv)

    settings = load_yaml(args.settings)
    companies = {c.ticker: c for c in load_companies(args.companies)}
    ticker = args.ticker.upper()
    if ticker not in companies:
        raise SystemExit(f"Ticker {ticker} not in {args.companies}")
    company = companies[ticker]
    if company.source.upper() != "EDGAR":
        raise SystemExit(f"{ticker} source is {company.source}, not EDGAR")

    user_agent = resolve_user_agent()
    max_rps = float(settings.get("edgar", {}).get("max_requests_per_second", 10))
    # Stay comfortably under the hard cap.
    session = RateLimitedSession(user_agent, max_rps=min(max_rps, 8.0))

    print(f"Fetching latest 10-K for {company.name} ({ticker})…")
    meta = fetch_company_10k(company, settings, session)
    print(json.dumps(meta, indent=2))
    missing = [
        s
        for s in (SECTION_BUSINESS, SECTION_RISK_FACTORS, SECTION_MDNA)
        if s not in meta["paths"]
    ]
    if missing:
        print(f"Warning: missing sections: {missing}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
