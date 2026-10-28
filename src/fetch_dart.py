"""
OpenDART disclosure fetch (Milestone 5).

Official endpoints (guide DS001 / document download apiId 2019003):
  - corpCode.xml → ZIP of CORPCODE.xml (stock_code → corp_code)
  - list.json → recent disclosures (pblntf_ty A/B)
  - document.xml → ZIP of disclosure body (binary ZIP; not XML)

Never prints DART_API_KEY. Cache: data/raw/{krx_code}/{rcept_no}/meta.json + other_full.txt
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from src.envutil import require_dart_api_key
from src.fetch_edgar import (
    DEFAULT_COMPANIES,
    DEFAULT_SETTINGS,
    REPO_ROOT,
    load_companies,
    load_yaml,
    window_bounds,
)

USER_AGENT = "MemorySignalsResearchBot/0.1 (local; contact: local-dev)"


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


def _dart_cfg(settings: dict[str, Any]) -> dict[str, Any]:
    return settings.get("dart") or {}


def _raw_root(settings: dict[str, Any]) -> Path:
    return REPO_ROOT / settings["paths"]["raw_dir"]


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _rcept_dt_to_iso(rcept_dt: str) -> str:
    s = (rcept_dt or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def fetch_corp_code_map(
    api_key: str,
    settings: dict[str, Any],
    session: requests.Session | None = None,
    limiter: RateLimiter | None = None,
    force: bool = False,
) -> dict[str, str]:
    """Return mapping stock_code (6-digit) → corp_code. Cache CORPCODE.xml under data/raw/_dart/."""
    sess = session or _session()
    dart = _dart_cfg(settings)
    cache_dir = _raw_root(settings) / "_dart"
    cache_xml = cache_dir / "CORPCODE.xml"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if force or not cache_xml.is_file():
        lim = limiter or RateLimiter(float(dart.get("max_requests_per_second", 2)))
        lim.wait()
        resp = sess.get(dart["corp_code_url"], params={"crtfc_key": api_key}, timeout=120)
        resp.raise_for_status()
        data = resp.content
        if data[:2] != b"PK":
            raise RuntimeError(
                f"corpCode.xml did not return a ZIP ({len(data)} bytes). "
                "Check DART_API_KEY / OpenDART status."
            )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            member = next(
                (n for n in names if n.upper().endswith("CORPCODE.XML")), names[0]
            )
            cache_xml.write_bytes(zf.read(member))

    tree = ET.parse(cache_xml)
    mapping: dict[str, str] = {}
    for el in tree.getroot().findall("list"):
        stock = (el.findtext("stock_code") or "").strip()
        corp = (el.findtext("corp_code") or "").strip()
        if stock and corp:
            mapping[stock] = corp
    return mapping


def list_disclosures(
    api_key: str,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    pblntf_ty: str,
    settings: dict[str, Any],
    session: requests.Session | None = None,
    limiter: RateLimiter | None = None,
) -> list[dict[str, Any]]:
    """Paginate list.json for one disclosure type. Dates are YYYYMMDD."""
    sess = session or _session()
    dart = _dart_cfg(settings)
    lim = limiter or RateLimiter(float(dart.get("max_requests_per_second", 2)))
    url = dart["list_url"]
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        lim.wait()
        resp = sess.get(
            url,
            params={
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "pblntf_ty": pblntf_ty,
                "page_no": page,
                "page_count": 100,
            },
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        status = str(payload.get("status", ""))
        if status == "013":
            break
        if status != "000":
            raise RuntimeError(
                f"OpenDART list.json status={status} message={payload.get('message')!r}"
            )
        out.extend(payload.get("list") or [])
        total_page = int(payload.get("total_page") or 1)
        if page >= total_page:
            break
        page += 1
    return out


def download_document_zip(
    api_key: str,
    rcept_no: str,
    dest_dir: Path,
    settings: dict[str, Any],
    session: requests.Session | None = None,
    limiter: RateLimiter | None = None,
) -> tuple[Path, list[str]]:
    """Download document.xml (ZIP), save as document.zip, extract members. Returns (zip_path, names)."""
    sess = session or _session()
    dart = _dart_cfg(settings)
    lim = limiter or RateLimiter(float(dart.get("max_requests_per_second", 2)))
    dest_dir.mkdir(parents=True, exist_ok=True)
    lim.wait()
    resp = sess.get(
        dart["document_url"],
        params={"crtfc_key": api_key, "rcept_no": rcept_no},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.content
    if data[:2] != b"PK":
        raise RuntimeError(
            f"document.xml for {rcept_no} is not a ZIP ({len(data)} bytes)"
        )
    zip_path = dest_dir / "document.zip"
    zip_path.write_bytes(data)
    written: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            safe = Path(name).name
            if not safe or safe.endswith("/"):
                continue
            # Avoid clobbering meta/text we write ourselves
            if safe.lower() in {"meta.json", "other_full.txt", "document.zip"}:
                safe = f"dart_{safe}"
            target = dest_dir / safe
            target.write_bytes(zf.read(name))
            written.append(safe)
    return zip_path, written


def _bytes_to_text(data: bytes, suffix: str) -> str:
    suf = suffix.lower()
    if suf in {".htm", ".html", ".xhtml"}:
        # DART often uses EUC-KR / cp949
        for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
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
    if suf in {".xml", ".xsd"}:
        for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
            try:
                xml_text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            xml_text = data.decode("utf-8", errors="replace")
        # Strip tags lightly; keep text nodes
        soup = BeautifulSoup(xml_text, "lxml-xml")
        text = soup.get_text("\n", strip=True)
        if len(text) < 40:
            text = re.sub(r"<[^>]+>", " ", xml_text)
            text = re.sub(r"\s+", " ", text).strip()
        return text
    if suf == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    # plain / unknown
    for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def dart_files_to_text(dest_dir: Path, filenames: list[str]) -> str:
    """Prefer longest HTML/XML body from extracted ZIP members."""
    candidates: list[tuple[int, str]] = []
    for name in filenames:
        path = dest_dir / name
        if not path.is_file():
            continue
        suf = path.suffix.lower()
        if suf not in {".htm", ".html", ".xhtml", ".xml", ".txt", ".pdf"}:
            continue
        try:
            text = _bytes_to_text(path.read_bytes(), suf)
        except Exception:  # noqa: BLE001
            continue
        text = text.replace("\x00", "").strip()
        if text:
            candidates.append((len(text), text))
    if not candidates:
        return ""
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def fetch_company_dart(
    company: Any,
    api_key: str,
    settings: dict[str, Any],
    corp_map: dict[str, str],
    session: requests.Session,
    limiter: RateLimiter,
    force: bool = False,
) -> int:
    stock = str(company.krx_code or company.ticker or "").zfill(6)
    corp_code = corp_map.get(stock)
    if not corp_code:
        raise RuntimeError(
            f"No OpenDART corp_code for stock_code={stock} ({company.name})"
        )

    lookback = int(settings.get("lookback_months", 12))
    w_start, w_end = window_bounds(lookback)
    bgn_de, end_de = _yyyymmdd(w_start), _yyyymmdd(w_end)

    types = list((_dart_cfg(settings).get("disclosure_types") or ["A", "B"]))
    seen: dict[str, dict[str, Any]] = {}
    for ty in types:
        for row in list_disclosures(
            api_key, corp_code, bgn_de, end_de, ty, settings, session, limiter
        ):
            rcept = str(row.get("rcept_no") or "").strip()
            if rcept:
                seen[rcept] = row

    raw_root = _raw_root(settings)
    saved = 0
    for rcept_no, row in sorted(
        seen.items(), key=lambda kv: kv[1].get("rcept_dt") or "", reverse=True
    ):
        dest = raw_root / stock / rcept_no
        meta_path = dest / "meta.json"
        text_path = dest / "other_full.txt"
        if meta_path.is_file() and text_path.is_file() and not force:
            continue
        try:
            _zip_path, files = download_document_zip(
                api_key, rcept_no, dest, settings, session, limiter
            )
            text = dart_files_to_text(dest, files)
            if not text.strip():
                raise RuntimeError("empty text after ZIP extract")
            text_path.write_text(text, encoding="utf-8")
            # Drop bulky ZIP members; document.zip + other_full.txt are enough to re-verify.
            for name in files:
                member = dest / name
                if member.is_file() and member.name not in {
                    "meta.json",
                    "other_full.txt",
                    "document.zip",
                }:
                    member.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {stock}/{rcept_no}: {exc}")
            continue

        report_nm = str(row.get("report_nm") or "").strip()
        meta = {
            "company": company.name,
            "ticker": stock,
            "role": company.role,
            "source": "DART",
            "form": report_nm or "DART",
            "filing_date": _rcept_dt_to_iso(str(row.get("rcept_dt") or "")),
            "accession_number": rcept_no,
            "filing_id": rcept_no,
            "corp_code": corp_code,
            "title": report_nm,
            "pblntf_ty": row.get("pblntf_ty") or "",
            "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
            "lookback_months": lookback,
            "window_start": w_start.isoformat(),
            "window_end": w_end.isoformat(),
            "language": "ko",
            "files": files,
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
        print(f"  saved DART {stock}/{rcept_no} ({report_nm[:60]})")
    return saved


def run_fetch_dart(force: bool = False) -> int:
    api_key = require_dart_api_key()
    settings = load_yaml(DEFAULT_SETTINGS)
    companies = [c for c in load_companies(DEFAULT_COMPANIES) if c.source.upper() == "DART"]
    if not companies:
        print("No DART companies in config/companies.yaml")
        return 0

    sess = _session()
    lim = RateLimiter(float(_dart_cfg(settings).get("max_requests_per_second", 2)))
    print("Fetching OpenDART corpCode map…")
    corp_map = fetch_corp_code_map(api_key, settings, sess, lim, force=force)
    print(f"  {len(corp_map)} stock codes indexed")

    total = 0
    for company in companies:
        print(f"DART disclosures: {company.name} ({company.krx_code or company.ticker})")
        total += fetch_company_dart(
            company, api_key, settings, corp_map, sess, lim, force=force
        )
    print(f"DART fetch complete: {total} new disclosure(s)")
    return total


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Fetch OpenDART disclosures for DART companies")
    p.add_argument("--force", action="store_true", help="Re-download even if cached")
    args = p.parse_args(argv)
    run_fetch_dart(force=args.force)


if __name__ == "__main__":
    main()
