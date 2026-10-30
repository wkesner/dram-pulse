"""
Unternehmensregister (UREG) German issuer filings — EC Signals DE path.

Public portal (no API key). Flow:
  GET /api/search-token
  GET /de/search?companyName=…&formType=ACCOUNTING&searchToken=…

Prefer publication types 135 / 136 / 137 (Jahresabschluss, Halbjahresfinanzbericht,
Zwischenmitteilung). Skip register extracts, HGB exemptions, thin “Hinweis” pointers,
and hinterlegte (paid) deposits.

Document bodies on ureg often require FoxCaptcha. When listing sourceName is an
http(s) PDF, download it; otherwise match config/ureg_documents.yaml IR mirrors.
Never automate captcha / BaFin MVP SOAP.

Cache: data/raw/{ticker}/{filing_id}/meta.json + other_full.txt (+ document.pdf gitignored)
Source tag: UREG
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

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

DEFAULT_PUB_TYPES = ("135", "136", "137")
SKIP_TITLE_PREFIXES = ("Hinweis auf ", "Hinweis auf")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html, application/pdf, */*",
            "Accept-Language": "de,en;q=0.8",
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


def _ureg_cfg(settings: dict[str, Any]) -> dict[str, Any]:
    return settings.get("ureg") or {}


def _raw_root(settings: dict[str, Any]) -> Path:
    return REPO_ROOT / settings["paths"]["raw_dir"]


def _base_url(settings: dict[str, Any]) -> str:
    return str(_ureg_cfg(settings).get("base_url") or "https://www.unternehmensregister.de").rstrip(
        "/"
    )


def search_name(company: Any) -> str:
    return (
        getattr(company, "company_name", None)
        or getattr(company, "name", None)
        or ""
    ).strip()


def preferred_pub_types(company: Any, settings: dict[str, Any]) -> set[str]:
    cfg_types = [str(t) for t in (_ureg_cfg(settings).get("publication_types") or DEFAULT_PUB_TYPES)]
    forms = tuple(str(f) for f in (getattr(company, "forms", ()) or ()))
    # Company.forms may still be EDGAR defaults if misconfigured — ignore non-numeric.
    numeric = [f for f in forms if f.isdigit()]
    if numeric:
        return set(numeric)
    return set(cfg_types)


def get_search_token(
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
) -> str:
    cfg = _ureg_cfg(settings)
    path = cfg.get("search_token_path") or "/api/search-token"
    url = _base_url(settings) + path
    last_err: Exception | None = None
    for attempt in range(4):
        limiter.wait()
        try:
            resp = session.get(url, timeout=60)
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(min(2**attempt, 16) + 0.5)
            continue
        if resp.status_code in {403, 423, 429, 451, 502, 503, 504} and attempt < 3:
            # 451 = portal "Unavailable For Legal Reasons" / access-denied soft ban.
            wait = 20.0 * (attempt + 1) if resp.status_code in {403, 451, 429} else min(
                2 ** (attempt + 1), 32
            )
            print(
                f"UREG search-token HTTP {resp.status_code}; retry in {wait:.0f}s "
                f"({attempt + 1}/4)",
                flush=True,
            )
            time.sleep(wait + 0.5)
            continue
        resp.raise_for_status()
        payload = resp.json()
        token = str(payload.get("token") or "").strip()
        if not token:
            raise RuntimeError(f"UREG search-token missing token: {payload!r}")
        return token
    if last_err is not None:
        raise last_err
    raise RuntimeError(f"Failed to GET UREG search token from {url}")


def _unescape_rsc(html: str) -> str:
    # Next.js RSC often embeds JSON with \" / \\uXXXX escapes inside script payloads.
    text = html.replace('\\"', '"')
    # Collapse double-escaped unicode first (\\\\u00e4 → \\u00e4), then decode.
    text = text.replace("\\\\u", "\\u")
    text = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda m: chr(int(m.group(1), 16)),
        text,
    )
    return text


def parse_search_html(html: str) -> list[dict[str, Any]]:
    """Extract publicationDto rows from ureg search SSR/RSC HTML."""
    text = _unescape_rsc(html)
    pubs: list[dict[str, Any]] = []
    for m in re.finditer(r'"publicationDto"\s*:\s*\{', text):
        start = m.end() - 1
        depth = 0
        end = None
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            continue
        block = text[start:end]
        def g(pat: str, default: str | None = None) -> str | None:
            mm = re.search(pat, block)
            return mm.group(1) if mm else default

        title = g(r'"title"\s*:\s*"([^"]*)"')
        source_name = g(r'"sourceName"\s*:\s*"([^"]*)"', "") or ""
        pub = {
            "publication_type": g(r'"publicationType"\s*:\s*\{\s*"id"\s*:\s*(\d+)'),
            "title": title or "",
            "source_date": g(r'"sourceDate"\s*:\s*"([^"]*)"', "") or "",
            "source_name": source_name,
            "esef_pub": g(r'"esefPub"\s*:\s*(true|false)', "false") == "true",
            "xml_pub": g(r'"xmlPub"\s*:\s*(true|false)', "false") == "true",
            "deposit": g(r'"deposit"\s*:\s*(true|false)', "false") == "true",
            "has_pdf": g(r'"hasPdf"\s*:\s*(true|false)', "false") == "true",
            "company_name": g(r'"companyNameAtTimeOfPublication"\s*:\s*"([^"]*)"', "") or "",
            "company_location": g(r'"companyLocation"\s*:\s*"([^"]*)"', "") or "",
        }
        if pub["title"] or pub["publication_type"]:
            pubs.append(pub)

    # Deduplicate by type+date+title
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for p in pubs:
        key = (p.get("publication_type") or "", p.get("source_date") or "", p.get("title") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def parse_company_euid(html: str) -> str | None:
    text = _unescape_rsc(html)
    m = re.search(r'"euid"\s*:\s*"([A-Z0-9]+\.[A-Za-zÖ]{2,3}\d+)"', text)
    return m.group(1) if m else None


def filter_publications(
    pubs: list[dict[str, Any]],
    *,
    types: set[str],
    window_start: date,
    window_end: date,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in pubs:
        if p.get("deposit"):
            continue
        ptype = str(p.get("publication_type") or "")
        if types and ptype not in types:
            continue
        title = str(p.get("title") or "")
        if any(title.startswith(pref) for pref in SKIP_TITLE_PREFIXES):
            continue
        raw_date = str(p.get("source_date") or "")[:10]
        if raw_date:
            try:
                fd = date.fromisoformat(raw_date)
            except ValueError:
                fd = None
            if fd is not None and (fd < window_start or fd > window_end):
                continue
        out.append(p)
    return out


def search_publications(
    company: Any,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    *,
    token: str | None = None,
    fixture_html: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Return (publications, raw_html). Uses fixture_html when provided (tests)."""
    if fixture_html is not None:
        return parse_search_html(fixture_html), fixture_html

    tok = token or get_search_token(settings, session, limiter)
    cfg = _ureg_cfg(settings)
    form_type = str(cfg.get("default_form_type") or "ACCOUNTING")
    params: dict[str, str] = {
        "formType": form_type,
        "searchToken": tok,
    }
    euid = (getattr(company, "euid", None) or "").strip()
    name = search_name(company)
    if euid:
        params["companyEuid"] = euid
    if name:
        params["companyName"] = name
    if not euid and not name:
        raise ValueError(f"UREG company {company.ticker} needs company_name or euid")

    path = cfg.get("search_path") or "/de/search"
    url = f"{_base_url(settings)}{path}?{urlencode(params)}"
    last_err: Exception | None = None
    html = ""
    for attempt in range(4):
        limiter.wait()
        try:
            resp = session.get(url, timeout=90)
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(min(2**attempt, 16) + 0.5)
            continue
        if resp.status_code in {403, 423, 429, 451, 502, 503, 504} and attempt < 3:
            time.sleep(min(2 ** (attempt + 1), 32) + 1.0)
            # Refresh token after access-denied bursts.
            tok = get_search_token(settings, session, limiter)
            params["searchToken"] = tok
            url = f"{_base_url(settings)}{path}?{urlencode(params)}"
            continue
        resp.raise_for_status()
        html = resp.text
        break
    else:
        if last_err is not None:
            raise last_err
        raise RuntimeError(f"UREG search failed for {company.ticker}")

    return parse_search_html(html), html


def load_ureg_documents(settings: dict[str, Any]) -> list[dict[str, Any]]:
    rel = (
        (_ureg_cfg(settings).get("documents_path"))
        or (settings.get("paths") or {}).get("ureg_documents")
        or "config/ureg_documents.yaml"
    )
    path = REPO_ROOT / rel
    if not path.is_file():
        return []
    data = load_yaml(path)
    docs = data.get("documents") or []
    return docs if isinstance(docs, list) else []


def _is_direct_pdf_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or ".pdf?" in path or "%2epdf" in path.lower()


def resolve_body_url(
    pub: dict[str, Any],
    company: Any,
    settings: dict[str, Any],
    overrides: list[dict[str, Any]],
) -> tuple[str | None, str]:
    """Return (url, provenance) where provenance is source_name|ureg_documents|none."""
    cfg = _ureg_cfg(settings)
    follow = bool(cfg.get("follow_external_source_urls", True))
    source = str(pub.get("source_name") or "").strip()
    if follow and source.lower().startswith("http") and _is_direct_pdf_url(source):
        return source, "source_name"

    ticker = str(company.ticker).upper()
    ptype = str(pub.get("publication_type") or "")
    title = str(pub.get("title") or "")
    pub_date = str(pub.get("source_date") or "")[:10]

    candidates: list[dict[str, Any]] = []
    for doc in overrides:
        if str(doc.get("ticker") or "").upper() != ticker:
            continue
        if str(doc.get("publication_type") or "") != ptype:
            continue
        needle = str(doc.get("title_contains") or "").strip()
        if needle and needle.lower() not in title.lower():
            continue
        candidates.append(doc)

    if not candidates:
        # Looser: same ticker+type only.
        candidates = [
            d
            for d in overrides
            if str(d.get("ticker") or "").upper() == ticker
            and str(d.get("publication_type") or "") == ptype
        ]

    if not candidates:
        return None, "none"

    def score(doc: dict[str, Any]) -> tuple[int, int]:
        d_date = str(doc.get("filing_date") or "")[:10]
        date_score = 0
        if d_date and pub_date:
            try:
                delta = abs((date.fromisoformat(d_date) - date.fromisoformat(pub_date)).days)
                date_score = -delta
            except ValueError:
                date_score = -9999
        needle = str(doc.get("title_contains") or "")
        title_score = 1 if needle and needle.lower() in title.lower() else 0
        return (title_score, date_score)

    best = sorted(candidates, key=score, reverse=True)[0]
    url = str(best.get("url") or "").strip()
    return (url or None), ("ureg_documents" if url else "none")


def filing_id_for(pub: dict[str, Any], body_url: str) -> str:
    digest = hashlib.sha1(
        f"{pub.get('publication_type')}|{pub.get('source_date')}|{pub.get('title')}|{body_url}".encode(
            "utf-8"
        )
    ).hexdigest()[:10]
    ptype = pub.get("publication_type") or "pub"
    day = (pub.get("source_date") or "unknown")[:10].replace("-", "")
    return f"UREG_{ptype}_{day}_{digest}"


def _pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts).replace("\x00", "")


def _guess_language(pub: dict[str, Any], override_lang: str | None, text: str) -> str:
    if override_lang:
        return override_lang
    title = str(pub.get("title") or "")
    if re.search(r"\b(Release|Annual Report|according to Article)\b", title):
        return "en"
    # Heuristic on body sample
    sample = text[:4000]
    de_hits = len(re.findall(r"\b(der|die|und|für|Geschäftsjahr|Gesellschaft)\b", sample, re.I))
    en_hits = len(re.findall(r"\b(the|and|for|company|fiscal)\b", sample, re.I))
    return "de" if de_hits >= en_hits else "en"


def fetch_one_publication(
    company: Any,
    pub: dict[str, Any],
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    overrides: list[dict[str, Any]],
    *,
    window_start: date,
    window_end: date,
    force: bool = False,
) -> bool:
    body_url, provenance = resolve_body_url(pub, company, settings, overrides)
    if not body_url:
        print(
            f"  skip {company.ticker} type={pub.get('publication_type')} "
            f"{(pub.get('title') or '')[:50]!r}: no IR/PDF body "
            f"(captcha-gated or index-only sourceName)"
        )
        return False

    fid = filing_id_for(pub, body_url)
    ticker = str(company.ticker).upper()
    dest = _raw_root(settings) / ticker / fid
    meta_path = dest / "meta.json"
    text_path = dest / "other_full.txt"
    if meta_path.is_file() and text_path.is_file() and not force:
        return False

    limiter.wait()
    resp = session.get(body_url, timeout=120)
    resp.raise_for_status()
    data = resp.content
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "pdf" not in ctype and not data.startswith(b"%PDF"):
        print(
            f"  skip {ticker}/{fid}: body is not PDF "
            f"({ctype!r}, {len(data)} bytes, provenance={provenance})"
        )
        return False

    try:
        text = _pdf_to_text(data)
    except Exception as exc:  # noqa: BLE001
        print(f"  skip {ticker}/{fid}: PDF extract failed ({exc})")
        return False
    text = text.strip()
    if not text:
        print(f"  skip {ticker}/{fid}: empty text")
        return False

    dest.mkdir(parents=True, exist_ok=True)
    pdf_path = dest / "document.pdf"
    pdf_path.write_bytes(data)
    text_path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")

    override_lang = None
    for doc in overrides:
        if str(doc.get("url") or "").strip() == body_url:
            override_lang = str(doc.get("language") or "") or None
            break
    language = _guess_language(pub, override_lang, text)
    lookback = int(settings.get("lookback_months", 12))
    title = str(pub.get("title") or "")
    meta = {
        "company": company.name,
        "ticker": ticker,
        "role": company.role,
        "source": "UREG",
        "form": title or f"UREG-{pub.get('publication_type')}",
        "filing_date": str(pub.get("source_date") or "")[:10],
        "accession_number": fid,
        "filing_id": fid,
        "title": title,
        "publication_type": str(pub.get("publication_type") or ""),
        "euid": getattr(company, "euid", None) or "",
        "lei": getattr(company, "lei", None) or "",
        "isin": getattr(company, "isin", None) or "",
        "ureg_source_name": str(pub.get("source_name") or ""),
        "body_provenance": provenance,
        "source_url": body_url,
        "esef_pub": bool(pub.get("esef_pub")),
        "lookback_months": lookback,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "language": language,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "paths": {
            "other_full": str(text_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "document_pdf": str(pdf_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  saved UREG {ticker}/{fid} ({title[:70]}) via {provenance}")
    return True


def pubs_from_documents(
    company: Any,
    overrides: list[dict[str, Any]],
    types: set[str],
) -> list[dict[str, Any]]:
    """Synthesize publication rows from ureg_documents.yaml (offline / 451 fallback)."""
    ticker = str(company.ticker).upper()
    out: list[dict[str, Any]] = []
    for doc in overrides:
        if str(doc.get("ticker") or "").upper() != ticker:
            continue
        ptype = str(doc.get("publication_type") or "")
        if types and ptype not in types:
            continue
        out.append(
            {
                "publication_type": ptype,
                "title": str(doc.get("title") or doc.get("title_contains") or f"UREG-{ptype}"),
                "source_date": str(doc.get("filing_date") or "")[:10],
                "source_name": str(doc.get("url") or ""),
                "esef_pub": False,
                "xml_pub": False,
                "deposit": False,
                "has_pdf": True,
                "company_name": search_name(company),
                "company_location": "",
            }
        )
    return out


def fetch_company_ureg(
    company: Any,
    settings: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    *,
    force: bool = False,
    fixture_html: str | None = None,
    list_only: bool = False,
    documents_only: bool = False,
) -> int:
    lookback = int(settings.get("lookback_months", 12))
    w_start, w_end = window_bounds(lookback)
    types = preferred_pub_types(company, settings)
    overrides = load_ureg_documents(settings)
    pubs: list[dict[str, Any]] = []
    html = ""

    if documents_only:
        pubs = pubs_from_documents(company, overrides, types)
        print(f"  documents-only: {len(pubs)} override row(s) for {company.ticker}")
    else:
        try:
            pubs, html = search_publications(
                company, settings, session, limiter, fixture_html=fixture_html
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ureg search failed ({exc}); falling back to ureg_documents.yaml")
            pubs = pubs_from_documents(company, overrides, types)

    # Cache raw search HTML under data/raw/_ureg/ (gitignored).
    if html and fixture_html is None:
        cache_dir = _raw_root(settings) / "_ureg"
        cache_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (cache_dir / f"{company.ticker}_{stamp}.html").write_text(html, encoding="utf-8")
        euid = parse_company_euid(html)
        if euid and not getattr(company, "euid", None):
            print(f"  note: discovered euid {euid} for {company.ticker}")

    filtered = filter_publications(pubs, types=types, window_start=w_start, window_end=w_end)
    # Documents-only / override rows may sit just outside lookback — still allow
    # explicit ureg_documents filings for smoke.
    if not filtered and pubs and (documents_only or fixture_html is not None):
        filtered = [p for p in pubs if str(p.get("publication_type") or "") in types]
    print(
        f"{company.name} ({company.ticker}): {len(filtered)} UREG pubs in "
        f"{w_start}..{w_end} (of {len(pubs)} parsed; types={sorted(types)})"
    )
    for p in filtered[:12]:
        print(
            f"  - {p.get('source_date')} t={p.get('publication_type')} "
            f"{(p.get('title') or '')[:80]}"
        )
    if list_only:
        return 0

    saved = 0
    for pub in sorted(filtered, key=lambda r: str(r.get("source_date") or ""), reverse=True):
        if fetch_one_publication(
            company,
            pub,
            settings,
            session,
            limiter,
            overrides,
            window_start=w_start,
            window_end=w_end,
            force=force,
        ):
            saved += 1
    return saved


def run_fetch_ureg(
    *,
    ticker: str | None = None,
    force: bool = False,
    fixture_html_path: Path | None = None,
    list_only: bool = False,
    documents_only: bool = False,
) -> int:
    settings = load_yaml(DEFAULT_SETTINGS)
    companies = [
        c
        for c in load_companies(DEFAULT_COMPANIES)
        if c.source.upper() in {"UREG", "BAFIN_UREG"}
    ]
    if ticker:
        want = ticker.upper()
        companies = [c for c in companies if c.ticker == want]
        if not companies:
            raise SystemExit(f"No UREG company {ticker} in config/companies.yaml")
    if not companies:
        print("No UREG companies in config/companies.yaml")
        return 0

    fixture_html = None
    if fixture_html_path is not None:
        fixture_html = fixture_html_path.read_text(encoding="utf-8")

    sess = _session()
    lim = RateLimiter(float(_ureg_cfg(settings).get("max_requests_per_second", 0.5)))
    total = 0
    for company in companies:
        print(f"UREG disclosures: {company.name} ({company.ticker})")
        total += fetch_company_ureg(
            company,
            settings,
            sess,
            lim,
            force=force,
            fixture_html=fixture_html,
            list_only=list_only,
            documents_only=documents_only,
        )
    print(f"UREG fetch complete: {total} new disclosure(s)")
    return total


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Fetch Unternehmensregister (UREG) German filings via search + IR PDFs"
    )
    p.add_argument("--ticker", help="Single ticker (e.g. IFX, WAF)")
    p.add_argument("--force", action="store_true", help="Re-download even if cached")
    p.add_argument(
        "--list-only",
        action="store_true",
        help="Search and print matching pubs; do not download bodies",
    )
    p.add_argument(
        "--documents-only",
        action="store_true",
        help="Skip ureg search; fetch bodies listed in config/ureg_documents.yaml",
    )
    p.add_argument(
        "--fixture-html",
        type=Path,
        help="Parse this search HTML instead of calling ureg (tests / offline)",
    )
    args = p.parse_args(argv)
    run_fetch_ureg(
        ticker=args.ticker,
        force=args.force,
        fixture_html_path=args.fixture_html,
        list_only=args.list_only,
        documents_only=args.documents_only,
    )


if __name__ == "__main__":
    main()
