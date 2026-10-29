"""
UK Companies House filing fetch — stub + ready path.

Official free REST API + Document API. Requires COMPANIES_HOUSE_API_KEY from:
  https://developer.company-information.service.gov.uk/

Auth: HTTP Basic with API key as username, blank password.
Rate limit: 600 requests / 5 minutes (HTTP 429 when exceeded).

LSE RNS (LSEG) needs commercial credentials; FCA National Storage Mechanism
has free browser search but no public retrieval API until ~late 2027.
Companies House is the official free programmatic UK path for filed accounts
and confirmation statements — useful for UK electronics issuers, though
supply-signal language is denser in RNS trading updates (blocked without LSEG).

Without a key this module exits with setup instructions.
"""

from __future__ import annotations

import argparse
import io
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

from src.envutil import load_dotenv
from src.fetch_edgar import (
    DEFAULT_COMPANIES,
    DEFAULT_SETTINGS,
    REPO_ROOT,
    load_companies,
    load_yaml,
    parse_filing_date,
    window_bounds,
)

USER_AGENT = "ECSignalsResearchBot/0.1 (local; contact: local-dev)"

# Filing history categories most likely to carry supply narrative.
DEFAULT_CATEGORIES = {"accounts", "annual-return", "confirmation-statement", "capital"}


def require_companies_house_api_key() -> str:
    load_dotenv()
    import os

    key = (os.environ.get("COMPANIES_HOUSE_API_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "COMPANIES_HOUSE_API_KEY is required for UK Companies House fetch.\n"
            "Register a free application at:\n"
            "  https://developer.company-information.service.gov.uk/\n"
            "Put the API key in gitignored .env (see .env.example).\n"
            "Note: LSE RNS / LSEG announcement feeds need separate commercial\n"
            "credentials; FCA NSM has no public retrieval API yet (~2027)."
        )
    return key


def _session(api_key: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    s.auth = (api_key, "")
    return s


class RateLimiter:
    def __init__(self, max_per_second: float) -> None:
        self.min_interval = 1.0 / max_per_second if max_per_second > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delay = self.min_interval - (now - self._last)
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


def _cfg(settings: dict[str, Any]) -> dict[str, Any]:
    return settings.get("companies_house") or {}


def _raw_root(settings: dict[str, Any]) -> Path:
    return REPO_ROOT / settings["paths"]["raw_dir"]


def list_filing_history(
    company_number: str,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
) -> list[dict[str, Any]]:
    cfg = _cfg(settings)
    base = cfg.get(
        "api_base", "https://api.company-information.service.gov.uk"
    ).rstrip("/")
    url = f"{base}/company/{company_number}/filing-history"
    out: list[dict[str, Any]] = []
    start = 0
    page_size = 100
    while True:
        limiter.wait()
        resp = session.get(
            url, params={"items_per_page": page_size, "start_index": start}, timeout=60
        )
        if resp.status_code == 401:
            raise SystemExit("COMPANIES_HOUSE_API_KEY rejected (HTTP 401).")
        if resp.status_code == 429:
            time.sleep(30)
            continue
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("items") or []
        out.extend(items)
        total = int(payload.get("total_count") or 0)
        start += len(items)
        if not items or start >= total:
            break
    return out


def _document_meta_and_content(
    links: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
) -> tuple[dict[str, Any], bytes, str]:
    """Follow filing-history document metadata → content (PDF or iXBRL)."""
    doc_meta_url = (links.get("document_metadata") or "").strip()
    if not doc_meta_url:
        raise RuntimeError("no document_metadata link")
    limiter.wait()
    meta_resp = session.get(doc_meta_url, timeout=60)
    meta_resp.raise_for_status()
    meta = meta_resp.json()
    content_url = (meta.get("links") or {}).get("document") or ""
    if not content_url:
        raise RuntimeError("no document content link")
    resources = meta.get("resources") or {}
    if "application/xhtml+xml" in resources:
        accept = "application/xhtml+xml"
    else:
        accept = "application/pdf"
    limiter.wait()
    content_resp = session.get(
        content_url, headers={"Accept": accept}, timeout=120, allow_redirects=True
    )
    content_resp.raise_for_status()
    return meta, content_resp.content, accept


def _bytes_to_text(data: bytes, content_type: str) -> str:
    if "xhtml" in content_type or "html" in content_type or "xml" in content_type:
        from bs4 import BeautifulSoup

        for enc in ("utf-8", "latin-1"):
            try:
                html = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            html = data.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text("\n", strip=True)
    # PDF
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages)


def fetch_company_ch(
    company: Any,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    *,
    force: bool = False,
) -> int:
    number = (getattr(company, "company_number", None) or "").strip()
    if not number:
        raise RuntimeError(
            f"{company.name}: companies.yaml needs company_number for Companies House"
        )
    lookback = int(settings.get("lookback_months", 12))
    w_start, w_end = window_bounds(lookback)
    categories = set(_cfg(settings).get("categories") or DEFAULT_CATEGORIES)
    rows = list_filing_history(number, settings, session, limiter)
    selected: list[dict[str, Any]] = []
    for row in rows:
        cat = str(row.get("category") or "").lower()
        if categories and cat and cat not in categories:
            continue
        filed = str(row.get("date") or "")[:10]
        if not filed:
            continue
        try:
            d = parse_filing_date(filed)
        except ValueError:
            continue
        if not (w_start <= d <= w_end):
            continue
        selected.append(row)

    ticker = str(company.ticker)
    print(
        f"{company.name} ({ticker}/{number}): {len(selected)} CH filings in "
        f"{w_start}..{w_end}"
    )
    saved = 0
    raw_root = _raw_root(settings)
    for row in sorted(selected, key=lambda r: r.get("date") or "", reverse=True):
        tx_id = str(row.get("transaction_id") or row.get("barcode") or "").strip()
        if not tx_id:
            continue
        dest = raw_root / ticker / tx_id
        meta_path = dest / "meta.json"
        text_path = dest / "other_full.txt"
        if meta_path.is_file() and text_path.is_file() and not force:
            continue
        links = row.get("links") or {}
        try:
            _doc_meta, body, ctype = _document_meta_and_content(links, session, limiter)
            text = _bytes_to_text(body, ctype)
            text = text.replace("\x00", "").strip()
            if not text:
                raise RuntimeError("empty text")
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {ticker}/{tx_id}: {exc}")
            continue
        dest.mkdir(parents=True, exist_ok=True)
        if "pdf" in ctype:
            (dest / "document.pdf").write_bytes(body)
        else:
            (dest / "document.xhtml").write_bytes(body)
        text_path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        description = str(row.get("description") or row.get("category") or "CH").strip()
        meta = {
            "company": company.name,
            "ticker": ticker,
            "role": company.role,
            "source": "CH",
            "form": description,
            "filing_date": str(row.get("date") or "")[:10],
            "accession_number": tx_id,
            "filing_id": tx_id,
            "company_number": number,
            "category": row.get("category") or "",
            "title": description,
            "source_url": (
                f"https://find-and-update.company-information.service.gov.uk"
                f"/company/{number}/filing-history"
            ),
            "lookback_months": lookback,
            "window_start": w_start.isoformat(),
            "window_end": w_end.isoformat(),
            "language": "en",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "paths": {
                "other_full": str(text_path.relative_to(REPO_ROOT)).replace("\\", "/")
            },
        }
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        saved += 1
        print(f"  saved CH {ticker}/{tx_id} ({description[:60]})")
    return saved


def run_fetch_companies_house(
    *, ticker: str | None = None, force: bool = False
) -> int:
    api_key = require_companies_house_api_key()
    settings = load_yaml(DEFAULT_SETTINGS)
    companies = [
        c for c in load_companies(DEFAULT_COMPANIES) if c.source.upper() in {"CH", "COMPANIES_HOUSE"}
    ]
    if ticker:
        want = ticker.upper()
        companies = [c for c in companies if c.ticker.upper() == want]
        if not companies:
            raise SystemExit(f"No Companies House company {ticker}")
    if not companies:
        print("No CH / COMPANIES_HOUSE companies in config/companies.yaml")
        return 0

    sess = _session(api_key)
    lim = RateLimiter(float(_cfg(settings).get("max_requests_per_second", 2)))
    total = 0
    for company in companies:
        print(f"Companies House: {company.name} ({company.company_number})")
        total += fetch_company_ch(company, settings, sess, lim, force=force)
    print(f"Companies House fetch complete: {total} new filing(s)")
    return total


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Fetch UK Companies House filings (requires COMPANIES_HOUSE_API_KEY)"
    )
    p.add_argument("--ticker", help="Config ticker (e.g. RSW)")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    run_fetch_companies_house(ticker=args.ticker, force=args.force)


if __name__ == "__main__":
    main()
