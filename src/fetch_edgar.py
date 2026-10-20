"""Fetch EDGAR filings for Memory Signals.

Usage:
  export SEC_USER_AGENT="MemorySignals Your Name you@example.com"
  python -m src.fetch_edgar --ticker MU
  python -m src.fetch_edgar --all
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
    html_to_text,
    split_filing,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = REPO_ROOT / "config" / "settings.yaml"
DEFAULT_COMPANIES = REPO_ROOT / "config" / "companies.yaml"

DEFAULT_FORMS = ("10-K", "10-Q", "8-K")
AMENDMENT_OK = {"10-K/A", "10-Q/A", "8-K/A", "6-K/A"}
# 8-K items that carry memory/supply signal content per SPEC.
EIGHT_K_ITEMS = {"2.02", "2.06", "7.01", "8.01"}


@dataclass
class Company:
    name: str
    role: str
    source: str
    ticker: str
    krx_code: str | None = None
    forms: tuple[str, ...] = DEFAULT_FORMS


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
        forms = row.get("forms")
        if forms:
            form_tuple = tuple(str(f).upper() for f in forms)
        else:
            form_tuple = DEFAULT_FORMS
        out.append(
            Company(
                name=row["name"],
                role=row["role"],
                source=row["source"],
                ticker=str(row["ticker"]).upper(),
                krx_code=str(row["krx_code"]) if row.get("krx_code") else None,
                forms=form_tuple,
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


def iter_recent_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten recent filings into dict rows (includes items for 8-Ks)."""
    recent = submissions.get("filings", {}).get("recent", {})
    rows: list[dict[str, Any]] = []
    forms = recent.get("form") or []
    items_list = recent.get("items") or [""] * len(forms)
    primary_docs = recent.get("primaryDocument") or [""] * len(forms)
    primary_docs_desc = recent.get("primaryDocDescription") or [""] * len(forms)
    for i, form in enumerate(forms):
        items_raw = items_list[i] if i < len(items_list) else ""
        rows.append(
            {
                "form": form,
                "filingDate": recent["filingDate"][i],
                "accessionNumber": recent["accessionNumber"][i],
                "primaryDocument": primary_docs[i] if i < len(primary_docs) else "",
                "primaryDocDescription": (
                    primary_docs_desc[i] if i < len(primary_docs_desc) else ""
                ),
                "items": items_raw or "",
            }
        )
    return rows


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def form_base(form: str) -> str:
    return form.upper().replace("/A", "")


def allowed_forms_for_company(company: Company) -> set[str]:
    bases = {form_base(f) for f in company.forms}
    out: set[str] = set()
    for base in bases:
        out.add(base)
        out.add(f"{base}/A")
    return out


def eight_k_items_match(items_field: str) -> bool:
    """True if any SPEC 8-K item appears in the submissions items string."""
    if not items_field:
        return False
    # Items are often comma-separated: "2.02,9.01"
    parts = {p.strip() for p in re.split(r"[,;]", items_field) if p.strip()}
    return bool(parts & EIGHT_K_ITEMS)


def select_filings_in_window(
    filings: list[dict[str, Any]],
    company: Company,
    window_start: date,
    window_end: date,
) -> list[dict[str, Any]]:
    allowed = allowed_forms_for_company(company)
    selected: list[dict[str, Any]] = []
    for row in filings:
        form = row["form"]
        if form not in allowed:
            continue
        filed = parse_filing_date(row["filingDate"])
        if not (window_start <= filed <= window_end):
            continue
        if form_base(form) == "8-K" and not eight_k_items_match(str(row.get("items") or "")):
            continue
        selected.append(row)

    # Deduplicate SK hynix 6-Ks by filing date + normalised title.
    if "6-K" in {form_base(f) for f in company.forms}:
        selected = dedupe_six_k(selected)
    selected.sort(key=lambda r: (r["filingDate"], r["accessionNumber"]), reverse=True)
    return selected


def dedupe_six_k(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    # Prefer non-amendment, then earlier accession (stable).
    ordered = sorted(
        filings,
        key=lambda r: (
            0 if form_base(r["form"]) == "6-K" and not r["form"].endswith("/A") else 1,
            r["filingDate"],
            r["accessionNumber"],
        ),
    )
    for row in ordered:
        if form_base(row["form"]) != "6-K":
            out.append(row)
            continue
        title = normalize_title(str(row.get("primaryDocDescription") or row.get("primaryDocument") or ""))
        key = (row["filingDate"], title)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def archive_url(cik: str, accession: str, primary_document: str, template: str) -> str:
    cik_int = str(int(cik))
    accession_no_dashes = accession.replace("-", "")
    return template.format(
        cik=cik_int,
        accession_no_dashes=accession_no_dashes,
        primary_document=primary_document,
    )


def index_json_url(cik: str, accession: str) -> str:
    """Directory listing for a filing (contains exhibit filenames)."""
    cik_int = str(int(cik))
    acc = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/index.json"


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


def _is_exhibit_99_candidate(name: str, primary_document: str = "") -> bool:
    lower = name.lower()
    primary = (primary_document or "").lower()
    if not lower.endswith((".htm", ".html", ".txt")):
        return False
    if "index" in lower:
        return False
    if lower.endswith(".txt") and re.match(r"\d{10}-", lower):
        return False
    if primary and lower == primary:
        return False
    # Classic exhibit 99 naming.
    if (
        re.search(r"ex[-_]?99\d*", lower)
        or re.search(r"exhibit[-_]?99", lower)
        or re.search(r"99\d*\.htm", lower)
    ):
        return True
    # Some issuers (e.g. Nvidia) name press releases without "99".
    if re.search(r"(?:^|[^a-z])pr\.htm|press.?release|cfocommentary|earnings", lower):
        return True
    return False


def fetch_exhibit_99s(
    session: RateLimitedSession,
    cik: str,
    accession: str,
    archives_template: str,
    dest: Path,
    *,
    primary_document: str = "",
) -> list[dict[str, str]]:
    """Download exhibit 99.x / press-release documents from the filing index."""
    exhibits: list[dict[str, str]] = []
    try:
        idx = session.get(index_json_url(cik, accession)).json()
    except Exception as exc:  # noqa: BLE001 — index missing is non-fatal
        print(f"  warning: no index.json for {accession}: {exc}", file=sys.stderr)
        return exhibits

    items = idx.get("directory", {}).get("item") or []
    for item in items:
        name = str(item.get("name") or "")
        if not name or not _is_exhibit_99_candidate(name, primary_document):
            continue
        url = archive_url(cik, accession, name, archives_template)
        try:
            body = session.get(url).text
        except Exception as exc:  # noqa: BLE001
            print(f"  warning: exhibit fetch failed {name}: {exc}", file=sys.stderr)
            continue
        text = html_to_text(body) if "<" in body[:500] else body
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
        out_name = f"exhibit_99_{safe}.txt"
        dest_abs = dest if dest.is_absolute() else (REPO_ROOT / dest)
        dest_abs.mkdir(parents=True, exist_ok=True)
        path = dest_abs / out_name
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        raw_path = dest_abs / f"exhibit_99_{safe}.raw"
        raw_path.write_text(body, encoding="utf-8")
        exhibits.append(
            {
                "name": name,
                "path": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "source_url": url,
            }
        )
    return exhibits


def fetch_one_filing(
    company: Company,
    cik: str,
    filing: dict[str, Any],
    settings: dict[str, Any],
    session: RateLimitedSession,
    *,
    fiscal_year_end: str | None,
    window_start: date,
    window_end: date,
    prior_only: bool = False,
) -> dict[str, Any]:
    edgar = settings["edgar"]
    raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")
    accession = filing["accessionNumber"]
    dest = cache_dir(raw_root, company.ticker, accession)
    meta_path = dest / "meta.json"

    if meta_path.is_file() and (dest / "other_full.txt").is_file() and not prior_only:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return meta

    url = archive_url(
        cik,
        accession,
        filing["primaryDocument"],
        edgar["archives_url_template"],
    )
    print(f"  fetching {filing['form']} {accession} ({filing['filingDate']})…")
    html = session.get(url).text
    form = filing["form"]
    base = form_base(form)

    if base in {"10-K", "10-Q"}:
        sections = split_filing(html)
    else:
        text = html_to_text(html) if "<" in html[:500] else html
        if not text.endswith("\n"):
            text += "\n"
        sections = {SECTION_OTHER: text}

    paths = write_sections(dest, html, sections)
    exhibits: list[dict[str, str]] = []
    if base == "8-K":
        exhibits = fetch_exhibit_99s(
            session,
            cik,
            accession,
            edgar["archives_url_template"],
            dest,
            primary_document=filing.get("primaryDocument") or "",
        )

    meta = {
        "company": company.name,
        "ticker": company.ticker,
        "cik": cik if isinstance(cik, str) else f"{int(cik):010d}",
        "form": form,
        "filing_date": filing["filingDate"],
        "accession_number": accession,
        "primary_document": filing["primaryDocument"],
        "primary_doc_description": filing.get("primaryDocDescription") or "",
        "items": filing.get("items") or "",
        "source_url": url,
        "fiscal_year_end": fiscal_year_end or "",
        "lookback_months": int(settings["lookback_months"]),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "prior_only": prior_only,
        "sections_present": sorted(sections.keys()),
        "exhibits": exhibits,
        "paths": {k: str(v.relative_to(REPO_ROOT)) for k, v in paths.items()},
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


# Back-compat alias used by M1 / extract prior-10K helper.
def select_latest_10k(
    filings: list[dict[str, Any]],
    window_start: date,
    window_end: date,
) -> dict[str, Any] | None:
    candidates = [
        row
        for row in filings
        if row["form"] in ("10-K", "10-K/A")
        and window_start <= parse_filing_date(row["filingDate"]) <= window_end
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: r["filingDate"], reverse=True)
    return candidates[0]


def fetch_company_10k(
    company: Company,
    settings: dict[str, Any],
    session: RateLimitedSession,
    *,
    run_date: date | None = None,
) -> dict[str, Any]:
    """M1 helper: fetch only the latest 10-K in the lookback window."""
    metas = fetch_company_filings(
        company, settings, session, run_date=run_date, forms_override=("10-K",)
    )
    if not metas:
        raise RuntimeError(f"No 10-K for {company.ticker} in lookback window")
    return metas[0]


def fetch_company_filings(
    company: Company,
    settings: dict[str, Any],
    session: RateLimitedSession,
    *,
    run_date: date | None = None,
    forms_override: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Fetch all in-window EDGAR filings for one company. Returns meta list."""
    edgar = settings["edgar"]
    lookback = int(settings["lookback_months"])
    window_start, window_end = window_bounds(lookback, run_date)

    cik = lookup_cik(session, edgar["company_tickers_url"], company.ticker)
    submissions_url = edgar["submissions_url_template"].format(cik=int(cik))
    submissions = session.get(submissions_url).json()
    fiscal_year_end = str(submissions.get("fiscalYearEnd") or "")

    working = company
    if forms_override is not None:
        working = Company(
            name=company.name,
            role=company.role,
            source=company.source,
            ticker=company.ticker,
            krx_code=company.krx_code,
            forms=forms_override,
        )

    selected = select_filings_in_window(
        iter_recent_filings(submissions), working, window_start, window_end
    )
    print(
        f"{company.name} ({company.ticker}): {len(selected)} filings in "
        f"{window_start}..{window_end} (fiscalYearEnd={fiscal_year_end or 'n/a'})"
    )

    metas: list[dict[str, Any]] = []
    for filing in selected:
        meta = fetch_one_filing(
            company,
            cik,
            filing,
            settings,
            session,
            fiscal_year_end=fiscal_year_end,
            window_start=window_start,
            window_end=window_end,
        )
        metas.append(meta)
    return metas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch EDGAR filings for Memory Signals")
    parser.add_argument("--ticker", help="Single ticker to fetch")
    parser.add_argument("--all", action="store_true", help="Fetch all EDGAR companies")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--companies", type=Path, default=DEFAULT_COMPANIES)
    args = parser.parse_args(argv)

    if not args.all and not args.ticker:
        parser.error("Provide --ticker or --all")

    settings = load_yaml(args.settings)
    companies = load_companies(args.companies)
    edgar_cos = [c for c in companies if c.source.upper() == "EDGAR"]
    if args.ticker:
        ticker = args.ticker.upper()
        edgar_cos = [c for c in edgar_cos if c.ticker == ticker]
        if not edgar_cos:
            raise SystemExit(f"Ticker {ticker} not in {args.companies} as EDGAR")

    user_agent = resolve_user_agent()
    max_rps = float(settings.get("edgar", {}).get("max_requests_per_second", 10))
    session = RateLimitedSession(user_agent, max_rps=min(max_rps, 8.0))

    summary: list[dict[str, Any]] = []
    for company in edgar_cos:
        metas = fetch_company_filings(company, settings, session)
        summary.append({"ticker": company.ticker, "filings": len(metas)})

    print(json.dumps({"fetched": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
