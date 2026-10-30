"""
Taiwan MOPS / TWSE OpenAPI disclosure fetch (no API key).

Official open data (prefer over HTML scrape — TWSE ToS discourages bots on
non-API pages):
  GET https://openapi.twse.com.tw/v1/opendata/t187ap04_L  → daily material info
  GET https://openapi.twse.com.tw/v1/opendata/t187ap05_L  → monthly revenue snapshot

No registration. No published hard RPS — stay ≤1 r/s, cache, backoff.
Dates are ROC calendar (民國); we convert to Gregorian for meta.filing_date.

Cache: data/raw/{ticker}/{filing_id}/meta.json + other_full.txt
Source tag: MOPS  language: zh-TW
"""

from __future__ import annotations

import argparse
import hashlib
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


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
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


def _cfg(settings: dict[str, Any]) -> dict[str, Any]:
    return settings.get("mops") or settings.get("twse") or {}


def _raw_root(settings: dict[str, Any]) -> Path:
    return REPO_ROOT / settings["paths"]["raw_dir"]


def _norm_row(row: dict[str, Any]) -> dict[str, str]:
    """Strip trailing spaces from TWSE Chinese field names / values."""
    out: dict[str, str] = {}
    for key, value in row.items():
        k = str(key).strip()
        if value is None:
            out[k] = ""
        elif isinstance(value, str):
            out[k] = value.strip()
        else:
            out[k] = str(value).strip()
    return out


def roc_to_iso(roc: str) -> str:
    """Convert ROC YYYMMDD (e.g. 1151028) → Gregorian ISO date."""
    s = re.sub(r"\D", "", (roc or "").strip())
    if len(s) == 7:  # YYYMMDD
        year = int(s[0:3]) + 1911
        return f"{year:04d}-{s[3:5]}-{s[5:7]}"
    if len(s) == 6:  # YYMMDD legacy
        year = int(s[0:2]) + 1911
        return f"{year:04d}-{s[2:4]}-{s[4:6]}"
    if len(s) == 5:  # YYYMM (monthly)
        year = int(s[0:3]) + 1911
        return f"{year:04d}-{s[3:5]}-01"
    return ""


def _get_json(
    url: str,
    session: requests.Session,
    limiter: RateLimiter,
) -> list[dict[str, Any]]:
    last_err: Exception | None = None
    for attempt in range(4):
        limiter.wait()
        try:
            resp = session.get(url, timeout=90)
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(min(2**attempt, 16) + 0.5)
            continue
        if resp.status_code in {429, 502, 503, 504} and attempt < 3:
            time.sleep(min(2**attempt, 16) + 0.5)
            continue
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected JSON array from {url}, got {type(payload)}")
        return payload
    if last_err is not None:
        raise last_err
    raise RuntimeError(f"Failed GET {url}")


def fetch_material_info(
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
) -> list[dict[str, str]]:
    cfg = _cfg(settings)
    base = cfg.get("api_base", "https://openapi.twse.com.tw/v1").rstrip("/")
    path = cfg.get("material_info_path", "/opendata/t187ap04_L")
    rows = _get_json(f"{base}{path}", session, limiter)
    return [_norm_row(r) for r in rows]


def fetch_monthly_revenue(
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
) -> list[dict[str, str]]:
    cfg = _cfg(settings)
    base = cfg.get("api_base", "https://openapi.twse.com.tw/v1").rstrip("/")
    path = cfg.get("monthly_revenue_path", "/opendata/t187ap05_L")
    rows = _get_json(f"{base}{path}", session, limiter)
    return [_norm_row(r) for r in rows]


def _filing_id(code: str, announce_date: str, announce_time: str, subject: str) -> str:
    digest = hashlib.sha1(f"{code}|{announce_date}|{announce_time}|{subject}".encode()).hexdigest()[:10]
    safe_date = re.sub(r"\D", "", announce_date) or "0000000"
    safe_time = re.sub(r"\D", "", announce_time) or "000000"
    return f"{safe_date}_{safe_time}_{digest}"


def _material_body(row: dict[str, str]) -> str:
    parts = [
        f"主旨：{row.get('主旨') or ''}",
        f"符合條款：{row.get('符合條款') or ''}",
        f"事實發生日：{row.get('事實發生日') or ''}",
        "",
        row.get("說明") or "",
    ]
    return "\n".join(parts).strip() + "\n"


def _revenue_body(row: dict[str, str]) -> str:
    """Render monthly revenue snapshot as plain text for rule extract."""
    ym = row.get("資料年月") or ""
    lines = [
        f"公司：{row.get('公司名稱') or ''}（{row.get('公司代號') or ''}）",
        f"資料年月：{ym}",
        f"當月營收：{row.get('營業收入-當月營收') or ''}",
        f"上月營收：{row.get('營業收入-上月營收') or ''}",
        f"去年當月營收：{row.get('營業收入-去年當月營收') or ''}",
        f"上月比較增減(%)：{row.get('營業收入-上月比較增減(%)') or ''}",
        f"去年同月增減(%)：{row.get('營業收入-去年同月增減(%)') or ''}",
        f"當月累計營收：{row.get('累計營業收入-當月累計營收') or ''}",
        f"去年累計營收：{row.get('累計營業收入-去年累計營收') or ''}",
        f"前期比較增減(%)：{row.get('累計營業收入-前期比較增減(%)') or ''}",
        f"備註：{row.get('備註') or ''}",
    ]
    return "\n".join(lines).strip() + "\n"


def _in_window(filing_iso: str, w_start: date, w_end: date) -> bool:
    if not filing_iso:
        return True  # keep undated snapshot rows
    try:
        d = date.fromisoformat(filing_iso[:10])
    except ValueError:
        return True
    return w_start <= d <= w_end


def _save(
    company: Any,
    filing_id: str,
    *,
    form: str,
    title: str,
    filing_date: str,
    body: str,
    source_url: str,
    settings: dict[str, Any],
    window_start: date,
    window_end: date,
    extra_meta: dict[str, Any] | None = None,
    force: bool = False,
) -> bool:
    ticker = str(company.ticker).zfill(4) if str(company.ticker).isdigit() else str(company.ticker)
    dest = _raw_root(settings) / ticker / filing_id
    meta_path = dest / "meta.json"
    text_path = dest / "other_full.txt"
    if meta_path.is_file() and text_path.is_file() and not force:
        return False
    if not body.strip():
        return False
    dest.mkdir(parents=True, exist_ok=True)
    text_path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    lookback = int(settings.get("lookback_months", 12))
    meta: dict[str, Any] = {
        "company": company.name,
        "ticker": ticker,
        "role": company.role,
        "source": "MOPS",
        "form": form,
        "filing_date": filing_date,
        "accession_number": filing_id,
        "filing_id": filing_id,
        "title": title,
        "source_url": source_url,
        "lookback_months": lookback,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "language": "zh-TW",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "paths": {
            "other_full": str(text_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        },
    }
    if extra_meta:
        meta.update(extra_meta)
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"  saved MOPS {ticker}/{filing_id} ({title[:60]})")
    return True


def run_fetch_mops(
    *,
    ticker: str | None = None,
    force: bool = False,
    include_revenue: bool = True,
) -> int:
    settings = load_yaml(DEFAULT_SETTINGS)
    companies = [c for c in load_companies(DEFAULT_COMPANIES) if c.source.upper() == "MOPS"]
    if ticker:
        want = ticker.zfill(4) if ticker.isdigit() else ticker.upper()
        companies = [c for c in companies if c.ticker == want or c.ticker == ticker]
        if not companies:
            raise SystemExit(f"No MOPS company {ticker} in config/companies.yaml")
    if not companies:
        print("No MOPS companies in config/companies.yaml")
        return 0

    by_code = {str(c.ticker).zfill(4): c for c in companies}
    lookback = int(settings.get("lookback_months", 12))
    w_start, w_end = window_bounds(lookback)
    sess = _session()
    lim = RateLimiter(float(_cfg(settings).get("max_requests_per_second", 1)))
    cfg = _cfg(settings)
    material_url = (
        cfg.get("api_base", "https://openapi.twse.com.tw/v1").rstrip("/")
        + cfg.get("material_info_path", "/opendata/t187ap04_L")
    )

    print(f"MOPS/TWSE material info snapshot → filter {len(by_code)} issuer(s)…")
    material = fetch_material_info(settings, sess, lim)
    print(f"  {len(material)} material rows in snapshot")

    saved = 0
    per_co: dict[str, int] = {code: 0 for code in by_code}
    for row in material:
        code = (row.get("公司代號") or "").zfill(4)
        company = by_code.get(code)
        if company is None:
            continue
        announce = row.get("發言日期") or ""
        filing_date = roc_to_iso(announce)
        if filing_date and not _in_window(filing_date, w_start, w_end):
            continue
        subject = row.get("主旨") or "重大訊息"
        fid = _filing_id(code, announce, row.get("發言時間") or "", subject)
        if _save(
            company,
            fid,
            form=subject,
            title=subject,
            filing_date=filing_date or date.today().isoformat(),
            body=_material_body(row),
            source_url=material_url,
            settings=settings,
            window_start=w_start,
            window_end=w_end,
            extra_meta={
                "clause": row.get("符合條款") or "",
                "fact_date_roc": row.get("事實發生日") or "",
                "announce_time": row.get("發言時間") or "",
                "feed": "t187ap04_L",
            },
            force=force,
        ):
            saved += 1
            per_co[code] = per_co.get(code, 0) + 1

    if include_revenue and bool(_cfg(settings).get("fetch_monthly_revenue", True)):
        print("MOPS/TWSE monthly revenue snapshot…")
        revenue = fetch_monthly_revenue(settings, sess, lim)
        rev_url = (
            cfg.get("api_base", "https://openapi.twse.com.tw/v1").rstrip("/")
            + cfg.get("monthly_revenue_path", "/opendata/t187ap05_L")
        )
        print(f"  {len(revenue)} revenue rows in snapshot")
        for row in revenue:
            code = (row.get("公司代號") or "").zfill(4)
            company = by_code.get(code)
            if company is None:
                continue
            ym = row.get("資料年月") or ""
            filing_date = roc_to_iso(ym)
            if filing_date and not _in_window(filing_date, w_start, w_end):
                continue
            title = f"月營收 {ym}"
            fid = f"rev_{re.sub(r'[^0-9]', '', ym) or 'unknown'}"
            if _save(
                company,
                fid,
                form="月營收",
                title=title,
                filing_date=filing_date or date.today().isoformat(),
                body=_revenue_body(row),
                source_url=rev_url,
                settings=settings,
                window_start=w_start,
                window_end=w_end,
                extra_meta={"feed": "t187ap05_L", "data_ym_roc": ym},
                force=force,
            ):
                saved += 1
                per_co[code] = per_co.get(code, 0) + 1

    for code, company in by_code.items():
        print(f"  {company.name} ({code}): {per_co.get(code, 0)} cached this run")
    print(f"MOPS fetch complete: {saved} new disclosure(s)")
    return saved


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Fetch Taiwan MOPS/TWSE OpenAPI disclosures (no API key)"
    )
    p.add_argument("--ticker", help="Single TWSE code (e.g. 2330)")
    p.add_argument("--force", action="store_true")
    p.add_argument(
        "--no-revenue",
        action="store_true",
        help="Skip monthly revenue snapshot (t187ap05_L)",
    )
    args = p.parse_args(argv)
    run_fetch_mops(
        ticker=args.ticker,
        force=args.force,
        include_revenue=not args.no_revenue,
    )


if __name__ == "__main__":
    main()
