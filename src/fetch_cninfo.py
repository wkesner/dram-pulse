"""
CNINFO (巨潮资讯) A-share disclosure fetch — v1 Asia/UK filings path.

Unofficial but widely used public JSON endpoints on the CSRC-designated
disclosure site (cninfo.com.cn). No API key. Prefer official free APIs when
they exist; CNINFO is the most viable zero-key China path for EC Signals v1.

Endpoints used:
  POST /new/information/topSearch/query     → stock code → orgId
  POST /new/hisAnnouncement/query           → announcement list
  GET  https://static.cninfo.com.cn/{path}  → PDF body

Cache: data/raw/{ticker}/{announcement_id}/meta.json + other_full.txt
(+ document.pdf, gitignored). Rate-limit politely; never hammer the site.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

from src.fetch_edgar import (
    DEFAULT_COMPANIES,
    DEFAULT_SETTINGS,
    REPO_ROOT,
    load_companies,
    load_yaml,
    window_bounds,
)

USER_AGENT = "ECSignalsResearchBot/0.1 (local; contact: local-dev)"

# Periodic / earnings-adjacent categories (cninfo category codes).
DEFAULT_CATEGORIES = (
    "category_ndbg_szsh",  # annual
    "category_bndbg_szsh",  # semi-annual
    "category_yjygjxz",  # earnings forecast / express
    "category_yjkb_szsh",  # earnings flash
)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.cninfo.com.cn/",
            "Origin": "https://www.cninfo.com.cn",
        }
    )
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


def _cninfo_cfg(settings: dict[str, Any]) -> dict[str, Any]:
    return settings.get("cninfo") or {}


def _raw_root(settings: dict[str, Any]) -> Path:
    return REPO_ROOT / settings["paths"]["raw_dir"]


def _ms_to_iso(ms: Any) -> str:
    try:
        value = int(ms)
    except (TypeError, ValueError):
        return ""
    # CNINFO announcementTime is Beijing-local calendar day as epoch ms.
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).date().isoformat()


def resolve_org_id(
    stock_code: str,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    *,
    force: bool = False,
) -> str:
    """Resolve 6-digit A-share code → cninfo orgId (cached under data/raw/_cninfo/)."""
    code = str(stock_code).zfill(6)
    cache_dir = _raw_root(settings) / "_cninfo"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "org_ids.json"
    mapping: dict[str, str] = {}
    if cache_path.is_file() and not force:
        try:
            mapping = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            mapping = {}
    if code in mapping and mapping[code]:
        return mapping[code]

    cfg = _cninfo_cfg(settings)
    url = cfg.get(
        "top_search_url",
        "https://www.cninfo.com.cn/new/information/topSearch/query",
    )
    limiter.wait()
    resp = session.post(url, data={"keyWord": code, "maxNum": 10}, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected topSearch payload for {code}: {type(payload)}")
    hit = next((row for row in payload if str(row.get("code") or "") == code), None)
    if hit is None and payload:
        hit = payload[0]
    org_id = str((hit or {}).get("orgId") or "").strip()
    if not org_id:
        raise RuntimeError(f"No cninfo orgId for stock code {code}")
    mapping[code] = org_id
    cache_path.write_text(json.dumps(mapping, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return org_id


def list_announcements(
    stock_code: str,
    org_id: str,
    window_start: date,
    window_end: date,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    *,
    exchange: str | None = None,
) -> list[dict[str, Any]]:
    """Paginate hisAnnouncement/query for one issuer. pageSize capped at 30."""
    cfg = _cninfo_cfg(settings)
    url = cfg.get(
        "query_url",
        "https://www.cninfo.com.cn/new/hisAnnouncement/query",
    )
    categories = list(cfg.get("categories") or DEFAULT_CATEGORIES)
    se_date = f"{window_start.isoformat()}~{window_end.isoformat()}"
    stock = f"{str(stock_code).zfill(6)},{org_id}"
    out: list[dict[str, Any]] = []
    page = 1
    category_blob = ";".join(categories) if categories else ""
    col = (exchange or cfg.get("default_column") or "sse").lower()
    if col not in {"sse", "szse"}:
        col = "sse"
    while page <= 40:
        data = {
            "pageNum": str(page),
            "pageSize": "30",
            "column": col,
            "tabName": "fulltext",
            "plate": "",
            "stock": stock,
            "searchkey": "",
            "secid": "",
            "category": category_blob,
            "trade": "",
            "seDate": se_date,
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        resp = None
        for attempt in range(4):
            limiter.wait()
            resp = session.post(url, data=data, timeout=90)
            if resp.status_code in {429, 502, 503, 504} and attempt < 3:
                time.sleep(min(2 ** attempt, 16) + 0.5)
                continue
            break
        assert resp is not None
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("announcements") or []
        if not rows:
            break
        out.extend(rows)
        total_pages = int(payload.get("totalpages") or 1)
        has_more = bool(payload.get("hasMore"))
        if page >= total_pages or not has_more:
            break
        page += 1
    # Deduplicate by announcementId (cninfo sometimes doubles titles).
    seen: dict[str, dict[str, Any]] = {}
    for row in out:
        aid = str(row.get("announcementId") or "").strip()
        if aid:
            seen[aid] = row
    return list(seen.values())


def _pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def fetch_one_announcement(
    company: Any,
    row: dict[str, Any],
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    *,
    window_start: date,
    window_end: date,
    force: bool = False,
) -> bool:
    cfg = _cninfo_cfg(settings)
    static_base = cfg.get("static_base_url", "https://static.cninfo.com.cn").rstrip("/")
    adjunct = str(row.get("adjunctUrl") or "").lstrip("/")
    if not adjunct:
        return False
    aid = str(row.get("announcementId") or "").strip()
    if not aid:
        # Fall back to URL stem.
        aid = Path(adjunct).stem
    ticker = str(company.ticker).zfill(6) if str(company.ticker).isdigit() else str(company.ticker)
    dest = _raw_root(settings) / ticker / aid
    meta_path = dest / "meta.json"
    text_path = dest / "other_full.txt"
    if meta_path.is_file() and text_path.is_file() and not force:
        return False

    url = f"{static_base}/{adjunct}"
    limiter.wait()
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    data = resp.content
    dest.mkdir(parents=True, exist_ok=True)
    pdf_path = dest / "document.pdf"
    pdf_path.write_bytes(data)
    try:
        text = _pdf_to_text(data)
    except Exception as exc:  # noqa: BLE001
        print(f"  skip {ticker}/{aid}: PDF extract failed ({exc})")
        return False
    text = text.replace("\x00", "").strip()
    if not text:
        print(f"  skip {ticker}/{aid}: empty text (likely scanned PDF)")
        return False
    text_path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")

    title = str(row.get("announcementTitle") or "").strip()
    filing_date = _ms_to_iso(row.get("announcementTime"))
    lookback = int(settings.get("lookback_months", 12))
    meta = {
        "company": company.name,
        "ticker": ticker,
        "role": company.role,
        "source": "CNINFO",
        "form": title or "CNINFO",
        "filing_date": filing_date,
        "accession_number": aid,
        "filing_id": aid,
        "title": title,
        "org_id": str(row.get("orgId") or ""),
        "exchange": getattr(company, "exchange", None) or "",
        "adjunct_url": adjunct,
        "source_url": url,
        "lookback_months": lookback,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "language": "zh",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "paths": {
            "other_full": str(text_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "document_pdf": str(pdf_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        },
    }
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"  saved CNINFO {ticker}/{aid} ({title[:60]})")
    return True


def fetch_company_cninfo(
    company: Any,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    *,
    force: bool = False,
) -> int:
    stock = str(company.ticker).zfill(6)
    org_id = resolve_org_id(stock, settings, session, limiter, force=force)
    lookback = int(settings.get("lookback_months", 12))
    w_start, w_end = window_bounds(lookback)
    exchange = (getattr(company, "exchange", None) or "").strip().lower() or None

    rows = list_announcements(
        stock,
        org_id,
        w_start,
        w_end,
        settings,
        session,
        limiter,
        exchange=exchange,
    )
    # Title filter: drop pure HK dual-list housekeeping if present.
    filtered: list[dict[str, Any]] = []
    for row in rows:
        title = str(row.get("announcementTitle") or "")
        if re.search(r"港股公告|翌日披露|证券变动月报表", title):
            continue
        filtered.append(row)
    print(
        f"{company.name} ({stock}): {len(filtered)} CNINFO filings in "
        f"{w_start}..{w_end} (of {len(rows)} raw)"
    )
    saved = 0
    for row in sorted(
        filtered, key=lambda r: int(r.get("announcementTime") or 0), reverse=True
    ):
        if fetch_one_announcement(
            company,
            row,
            settings,
            session,
            limiter,
            window_start=w_start,
            window_end=w_end,
            force=force,
        ):
            saved += 1
    return saved


def run_fetch_cninfo(
    *,
    ticker: str | None = None,
    force: bool = False,
) -> int:
    settings = load_yaml(DEFAULT_SETTINGS)
    companies = [c for c in load_companies(DEFAULT_COMPANIES) if c.source.upper() == "CNINFO"]
    if ticker:
        want = ticker.zfill(6) if ticker.isdigit() else ticker.upper()
        companies = [c for c in companies if c.ticker == want]
        if not companies:
            raise SystemExit(f"No CNINFO company {ticker} in config/companies.yaml")
    if not companies:
        print("No CNINFO companies in config/companies.yaml")
        return 0

    sess = _session()
    lim = RateLimiter(float(_cninfo_cfg(settings).get("max_requests_per_second", 1)))
    total = 0
    for company in companies:
        print(f"CNINFO disclosures: {company.name} ({company.ticker})")
        total += fetch_company_cninfo(company, settings, sess, lim, force=force)
    print(f"CNINFO fetch complete: {total} new disclosure(s)")
    return total


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Fetch CNINFO A-share disclosures")
    p.add_argument("--ticker", help="Single 6-digit A-share code")
    p.add_argument("--force", action="store_true", help="Re-download even if cached")
    args = p.parse_args(argv)
    run_fetch_cninfo(ticker=args.ticker, force=args.force)


if __name__ == "__main__":
    main()
