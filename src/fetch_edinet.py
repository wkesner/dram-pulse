"""
EDINET (Japan FSA) disclosure fetch — stub + ready path.

Official free API (v2). Requires EDINET_API_KEY from:
  https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1

ToS highlights (see EDINET terms / PDL1.0):
  - Prefer the API; website scraping is prohibited when the API can serve the data.
  - No published hard RPS; keep ≤ ~1 doc/sec and avoid burst downloads.
  - FSA may throttle under load; use backoff + cache.

Without a key this module exits with setup instructions (does not block the PR).
When EDINET_API_KEY is set, fetches document list by date window and downloads
type=5 CSV ZIPs (text) for configured JP issuers.
"""

from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
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
    window_bounds,
)

USER_AGENT = "ECSignalsResearchBot/0.1 (local; contact: local-dev)"

# Annual / semi-annual / quarterly / extraordinary report type codes (common).
DEFAULT_DOC_TYPES = {"120", "130", "140", "150", "160", "035"}


def require_edinet_api_key() -> str:
    load_dotenv()
    import os

    key = (os.environ.get("EDINET_API_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "EDINET_API_KEY is required for Japan EDINET fetch.\n"
            "Register + issue a free key at:\n"
            "  https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1\n"
            "Put it in gitignored .env (see .env.example). Never commit the key.\n"
            "TDnet timely disclosures are not covered by this API; EDINET is the\n"
            "official free programmatic path for securities reports."
        )
    return key


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
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
    return settings.get("edinet") or {}


def _raw_root(settings: dict[str, Any]) -> Path:
    return REPO_ROOT / settings["paths"]["raw_dir"]


def _sec_code(company: Any) -> str:
    """EDINET secCode is 5 half-width chars (4-digit TSE code + trailing 0)."""
    raw = str(company.ticker).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 4:
        return digits + "0"
    if len(digits) == 5:
        return digits
    return digits.zfill(5)[:5]


def list_documents_for_date(
    api_key: str,
    day: date,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
) -> list[dict[str, Any]]:
    cfg = _cfg(settings)
    base = cfg.get("api_base", "https://api.edinet-fsa.go.jp/api/v2").rstrip("/")
    url = f"{base}/documents.json"
    limiter.wait()
    resp = session.get(
        url,
        params={
            "date": day.isoformat(),
            "type": 2,
            "Subscription-Key": api_key,
        },
        timeout=60,
    )
    if resp.status_code == 401:
        raise SystemExit("EDINET_API_KEY rejected (HTTP 401). Re-issue the key.")
    resp.raise_for_status()
    payload = resp.json()
    return list(payload.get("results") or [])


def download_document_csv_zip(
    api_key: str,
    doc_id: str,
    dest_dir: Path,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
) -> tuple[Path, list[str]]:
    cfg = _cfg(settings)
    base = cfg.get("api_base", "https://api.edinet-fsa.go.jp/api/v2").rstrip("/")
    url = f"{base}/documents/{doc_id}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    limiter.wait()
    resp = session.get(
        url,
        params={"type": 5, "Subscription-Key": api_key},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.content
    if data[:2] != b"PK":
        raise RuntimeError(f"EDINET document {doc_id} is not a ZIP ({len(data)} bytes)")
    zip_path = dest_dir / "document.zip"
    zip_path.write_bytes(data)
    names: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            safe = Path(name).name
            if not safe or safe.endswith("/"):
                continue
            target = dest_dir / safe
            target.write_bytes(zf.read(name))
            names.append(safe)
    return zip_path, names


def _zip_members_to_text(dest_dir: Path, names: list[str]) -> str:
    chunks: list[str] = []
    for name in names:
        path = dest_dir / name
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".csv", ".txt", ".xml", ".htm", ".html"}:
            continue
        raw = path.read_bytes()
        for enc in ("utf-8", "cp932", "shift_jis", "utf-16", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        text = text.replace("\x00", "").strip()
        if text:
            chunks.append(text)
    return "\n\n".join(chunks)


def fetch_company_edinet(
    company: Any,
    api_key: str,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    *,
    force: bool = False,
) -> int:
    """Scan lookback calendar days for this issuer's secCode / edinet_code."""
    lookback = int(settings.get("lookback_months", 12))
    w_start, w_end = window_bounds(lookback)
    want_sec = _sec_code(company)
    want_edinet = (getattr(company, "edinet_code", None) or "").strip().upper()
    doc_types = set(_cfg(settings).get("doc_type_codes") or DEFAULT_DOC_TYPES)
    ticker = str(company.ticker)
    raw_root = _raw_root(settings)

    # Walk day-by-day (EDINET list API is date-scoped). Cap to reduce keyless-dev cost.
    max_days = int(_cfg(settings).get("max_scan_days", 120))
    days: list[date] = []
    cur = w_end
    while cur >= w_start and len(days) < max_days:
        days.append(cur)
        cur -= timedelta(days=1)

    matched: dict[str, dict[str, Any]] = {}
    for day in days:
        try:
            rows = list_documents_for_date(api_key, day, settings, session, limiter)
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: EDINET list {day}: {exc}")
            continue
        for row in rows:
            sec = str(row.get("secCode") or "").strip()
            edinet = str(row.get("edinetCode") or "").strip().upper()
            doc_type = str(row.get("docTypeCode") or "").strip()
            if doc_types and doc_type and doc_type not in doc_types:
                continue
            if want_edinet and edinet != want_edinet:
                continue
            if not want_edinet and sec != want_sec:
                continue
            doc_id = str(row.get("docID") or "").strip()
            if doc_id:
                matched[doc_id] = row

    print(
        f"{company.name} ({ticker}): {len(matched)} EDINET docs in "
        f"{w_start}..{w_end} (scanned {len(days)} days)"
    )
    saved = 0
    for doc_id, row in sorted(
        matched.items(), key=lambda kv: str(kv[1].get("submitDateTime") or ""), reverse=True
    ):
        dest = raw_root / ticker / doc_id
        meta_path = dest / "meta.json"
        text_path = dest / "other_full.txt"
        if meta_path.is_file() and text_path.is_file() and not force:
            continue
        try:
            _zip, names = download_document_csv_zip(
                api_key, doc_id, dest, settings, session, limiter
            )
            text = _zip_members_to_text(dest, names)
            if not text.strip():
                raise RuntimeError("empty text after ZIP extract")
            text_path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
            for name in names:
                member = dest / name
                if member.is_file() and member.name not in {
                    "meta.json",
                    "other_full.txt",
                    "document.zip",
                }:
                    member.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {ticker}/{doc_id}: {exc}")
            continue

        submit = str(row.get("submitDateTime") or "")[:10]
        title = str(row.get("docDescription") or row.get("filerName") or "EDINET").strip()
        meta = {
            "company": company.name,
            "ticker": ticker,
            "role": company.role,
            "source": "EDINET",
            "form": title,
            "filing_date": submit,
            "accession_number": doc_id,
            "filing_id": doc_id,
            "edinet_code": row.get("edinetCode") or want_edinet,
            "sec_code": row.get("secCode") or want_sec,
            "doc_type_code": row.get("docTypeCode") or "",
            "title": title,
            "source_url": (
                f"https://disclosure2.edinet-fsa.go.jp/WEBSITE/download?"
                f"docid={doc_id}"
            ),
            "lookback_months": lookback,
            "window_start": w_start.isoformat(),
            "window_end": w_end.isoformat(),
            "language": "ja",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "paths": {
                "other_full": str(text_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "document_zip": str((dest / "document.zip").relative_to(REPO_ROOT)).replace(
                    "\\", "/"
                ),
            },
        }
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        saved += 1
        print(f"  saved EDINET {ticker}/{doc_id} ({title[:60]})")
    return saved


def run_fetch_edinet(*, ticker: str | None = None, force: bool = False) -> int:
    api_key = require_edinet_api_key()
    settings = load_yaml(DEFAULT_SETTINGS)
    companies = [c for c in load_companies(DEFAULT_COMPANIES) if c.source.upper() == "EDINET"]
    if ticker:
        want = ticker.upper()
        companies = [c for c in companies if c.ticker.upper() == want or c.ticker == ticker]
        if not companies:
            raise SystemExit(f"No EDINET company {ticker} in config/companies.yaml")
    if not companies:
        print("No EDINET companies in config/companies.yaml")
        return 0

    sess = _session()
    lim = RateLimiter(float(_cfg(settings).get("max_requests_per_second", 1)))
    total = 0
    for company in companies:
        print(f"EDINET disclosures: {company.name} ({company.ticker})")
        total += fetch_company_edinet(
            company, api_key, settings, sess, lim, force=force
        )
    print(f"EDINET fetch complete: {total} new disclosure(s)")
    return total


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Fetch EDINET disclosures (requires EDINET_API_KEY)")
    p.add_argument("--ticker", help="Single TSE code (e.g. 8035)")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    run_fetch_edinet(ticker=args.ticker, force=args.force)


if __name__ == "__main__":
    main()
