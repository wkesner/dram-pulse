"""
EDINET (Japan FSA) disclosure fetch.

Official free API (v2). Requires EDINET_API_KEY from:
  https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1

ToS highlights (see EDINET terms / PDL1.0):
  - Prefer the API; website scraping is prohibited when the API can serve the data.
  - No published hard RPS; keep ≤ ~1 doc/sec and avoid burst downloads.
  - FSA may throttle under load; use backoff + cache.

List API is date-scoped — this module scans the lookback calendar once and
matches all configured EDINET issuers from each day's results (not per-company).
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
DEFAULT_DOC_TYPES = {"120", "130", "140", "150", "160"}

_KEY_HELP = """\
EDINET_API_KEY is required for Japan EDINET fetch.

Register + issue a free key at:
  https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1

Where to put it for this Cloud Agent VM (local .env on your laptop is not enough):
  1. Cursor Dashboard → Cloud Agents → this environment → Secrets
     add name exactly: EDINET_API_KEY
     (then start a new agent turn / restart so the secret is injected), OR
  2. On the agent VM create gitignored /workspace/.env with:
       EDINET_API_KEY=<your-key>
     (never commit .env)

TDnet timely disclosures are not covered by this API; EDINET is the official
free programmatic path for securities reports.
"""


def require_edinet_api_key() -> str:
    load_dotenv()
    import os

    key = (os.environ.get("EDINET_API_KEY") or "").strip()
    if not key:
        raise SystemExit(_KEY_HELP)
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
    last_err: Exception | None = None
    for attempt in range(4):
        limiter.wait()
        try:
            resp = session.get(
                url,
                params={
                    "date": day.isoformat(),
                    "type": 2,
                    "Subscription-Key": api_key,
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(min(2**attempt, 16) + 0.5)
            continue
        if resp.status_code == 401:
            raise SystemExit("EDINET_API_KEY rejected (HTTP 401). Re-issue the key.")
        if resp.status_code in {429, 502, 503, 504} and attempt < 3:
            time.sleep(min(2**attempt, 16) + 0.5)
            continue
        resp.raise_for_status()
        payload = resp.json()
        return list(payload.get("results") or [])
    if last_err is not None:
        raise last_err
    raise RuntimeError(f"EDINET list failed for {day}")


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
    last_err: Exception | None = None
    data = b""
    for attempt in range(4):
        limiter.wait()
        try:
            resp = session.get(
                url,
                params={"type": 5, "Subscription-Key": api_key},
                timeout=120,
            )
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(min(2**attempt, 16) + 0.5)
            continue
        if resp.status_code in {429, 502, 503, 504} and attempt < 3:
            time.sleep(min(2**attempt, 16) + 0.5)
            continue
        resp.raise_for_status()
        data = resp.content
        break
    else:
        if last_err is not None:
            raise last_err
        raise RuntimeError(f"EDINET document download failed for {doc_id}")

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


def _save_document(
    company: Any,
    row: dict[str, Any],
    api_key: str,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    *,
    window_start: date,
    window_end: date,
    force: bool = False,
) -> bool:
    doc_id = str(row.get("docID") or "").strip()
    if not doc_id:
        return False
    ticker = str(company.ticker)
    dest = _raw_root(settings) / ticker / doc_id
    meta_path = dest / "meta.json"
    text_path = dest / "other_full.txt"
    if meta_path.is_file() and text_path.is_file() and not force:
        return False
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
        return False

    lookback = int(settings.get("lookback_months", 12))
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
        "edinet_code": row.get("edinetCode") or getattr(company, "edinet_code", "") or "",
        "sec_code": row.get("secCode") or _sec_code(company),
        "doc_type_code": row.get("docTypeCode") or "",
        "title": title,
        "source_url": (
            f"https://disclosure2.edinet-fsa.go.jp/WEBSITE/download?docid={doc_id}"
        ),
        "lookback_months": lookback,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
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
    print(f"  saved EDINET {ticker}/{doc_id} ({title[:60]})")
    return True


def _company_matchers(
    companies: list[Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (by_sec_code, by_edinet_code) maps to Company."""
    by_sec: dict[str, Any] = {}
    by_edinet: dict[str, Any] = {}
    for company in companies:
        by_sec[_sec_code(company)] = company
        code = (getattr(company, "edinet_code", None) or "").strip().upper()
        if code:
            by_edinet[code] = company
    return by_sec, by_edinet


def scan_window_for_companies(
    companies: list[Any],
    api_key: str,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
) -> dict[str, list[tuple[Any, dict[str, Any]]]]:
    """One calendar scan → map ticker → [(company, row), ...]."""
    lookback = int(settings.get("lookback_months", 12))
    w_start, w_end = window_bounds(lookback)
    max_days = int(_cfg(settings).get("max_scan_days", 370))
    doc_types = set(_cfg(settings).get("doc_type_codes") or DEFAULT_DOC_TYPES)
    by_sec, by_edinet = _company_matchers(companies)

    days: list[date] = []
    cur = w_end
    while cur >= w_start and len(days) < max_days:
        days.append(cur)
        cur -= timedelta(days=1)

    matched: dict[str, dict[str, tuple[Any, dict[str, Any]]]] = {
        str(c.ticker): {} for c in companies
    }
    print(
        f"EDINET calendar scan: {len(days)} days ({w_start}..{w_end}) "
        f"for {len(companies)} issuer(s)…"
    )
    for i, day in enumerate(days, start=1):
        try:
            rows = list_documents_for_date(api_key, day, settings, session, limiter)
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: EDINET list {day}: {exc}")
            continue
        for row in rows:
            doc_type = str(row.get("docTypeCode") or "").strip()
            if doc_types and doc_type and doc_type not in doc_types:
                continue
            edinet = str(row.get("edinetCode") or "").strip().upper()
            sec = str(row.get("secCode") or "").strip()
            company = None
            if edinet and edinet in by_edinet:
                company = by_edinet[edinet]
            elif sec and sec in by_sec:
                company = by_sec[sec]
            if company is None:
                continue
            doc_id = str(row.get("docID") or "").strip()
            if not doc_id:
                continue
            matched[str(company.ticker)][doc_id] = (company, row)
        if i % 30 == 0 or i == len(days):
            hits = sum(len(v) for v in matched.values())
            print(f"  … scanned {i}/{len(days)} days, {hits} matching doc(s) so far")

    return {
        ticker: list(docs.values()) for ticker, docs in matched.items() if docs
    }


def run_fetch_edinet(*, ticker: str | None = None, force: bool = False) -> int:
    api_key = require_edinet_api_key()
    settings = load_yaml(DEFAULT_SETTINGS)
    companies = [c for c in load_companies(DEFAULT_COMPANIES) if c.source.upper() == "EDINET"]
    if ticker:
        want = ticker.zfill(4) if ticker.isdigit() else ticker.upper()
        companies = [
            c
            for c in companies
            if c.ticker == want or c.ticker == ticker or c.ticker.upper() == want
        ]
        if not companies:
            raise SystemExit(f"No EDINET company {ticker} in config/companies.yaml")
    if not companies:
        print("No EDINET companies in config/companies.yaml")
        return 0

    lookback = int(settings.get("lookback_months", 12))
    w_start, w_end = window_bounds(lookback)
    sess = _session()
    lim = RateLimiter(float(_cfg(settings).get("max_requests_per_second", 1)))

    by_ticker = scan_window_for_companies(companies, api_key, settings, sess, lim)
    total = 0
    for company in companies:
        rows = by_ticker.get(str(company.ticker), [])
        print(f"EDINET disclosures: {company.name} ({company.ticker}): {len(rows)} doc(s)")
        for company_row, row in sorted(
            rows, key=lambda pair: str(pair[1].get("submitDateTime") or ""), reverse=True
        ):
            if _save_document(
                company_row,
                row,
                api_key,
                settings,
                sess,
                lim,
                window_start=w_start,
                window_end=w_end,
                force=force,
            ):
                total += 1
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
