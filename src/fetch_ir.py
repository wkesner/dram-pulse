"""
Samsung IR English PDF fetch (Milestone 5).

Reads hand-maintained URLs from config/ir_documents.yaml, downloads PDFs,
extracts text with pypdf into other_full.txt, and writes meta.json compatible
with src.extract.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from pypdf import PdfReader

from src.fetch_edgar import REPO_ROOT, load_yaml, window_bounds

USER_AGENT = "MemorySignalsResearchBot/0.1 (local; contact: local-dev)"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/pdf,*/*",
        }
    )
    return s


def load_ir_documents(path: Path | None = None) -> list[dict[str, Any]]:
    settings = load_yaml(REPO_ROOT / "config" / "settings.yaml")
    rel = (settings.get("paths") or {}).get("ir_documents", "config/ir_documents.yaml")
    doc_path = path or (REPO_ROOT / rel)
    data = load_yaml(doc_path)
    docs = data.get("documents") or []
    if not isinstance(docs, list):
        raise ValueError(f"Expected documents list in {doc_path}")
    return docs


def _slug_for_doc(doc: dict[str, Any]) -> str:
    filing_id = str(doc.get("filing_id") or "").strip()
    if filing_id:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", filing_id)
    url = str(doc.get("url") or "")
    name = Path(urlparse(url).path).name or "document.pdf"
    stem = Path(name).stem
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"IR_{stem}_{digest}"


def pdf_to_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        if text.strip():
            parts.append(text)
    # Normalize form-feed / odd whitespace but keep paragraph breaks for offsets.
    body = "\n\n".join(parts)
    body = body.replace("\x00", "")
    return body


def fetch_one_ir_document(
    doc: dict[str, Any],
    settings: dict[str, Any],
    session: requests.Session,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    url = str(doc.get("url") or "").strip()
    if not url:
        return None
    code = str(doc.get("ticker_or_code") or doc.get("krx_code") or "005930").zfill(6)
    slug = _slug_for_doc(doc)
    raw_root = REPO_ROOT / settings.get("paths", {}).get("raw_dir", "data/raw")
    dest = raw_root / code / slug
    meta_path = dest / "meta.json"
    text_path = dest / "other_full.txt"
    pdf_path = dest / "document.pdf"

    if meta_path.is_file() and text_path.is_file() and not force:
        return json.loads(meta_path.read_text(encoding="utf-8"))

    dest.mkdir(parents=True, exist_ok=True)
    print(f"  GET {url}")
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "pdf" not in ctype and not resp.content.startswith(b"%PDF"):
        raise RuntimeError(
            f"IR URL did not return a PDF ({len(resp.content)} bytes, "
            f"content-type={ctype!r}): {url}"
        )
    pdf_path.write_bytes(resp.content)
    text = pdf_to_text(pdf_path)
    if not text.strip():
        raise RuntimeError(f"No extractable text from PDF: {url}")
    text_path.write_text(text, encoding="utf-8")

    lookback = int(settings.get("lookback_months", 12))
    w_start, w_end = window_bounds(lookback)
    filing_date = str(doc.get("filing_date") or "")
    # Skip docs outside the lookback window (still cached if we downloaded).
    if filing_date:
        try:
            fd = date.fromisoformat(filing_date[:10])
            if fd < w_start or fd > w_end:
                print(f"  note: {slug} filing_date {filing_date} outside lookback window")
        except ValueError:
            pass

    meta = {
        "company": doc.get("company") or "Samsung Electronics",
        "ticker": code,
        "role": doc.get("role") or "supplier",
        "source": "IR",
        "form": doc.get("form") or "IR-PDF",
        "filing_date": filing_date,
        "accession_number": slug,
        "filing_id": slug,
        "title": doc.get("title") or "",
        "period_end": doc.get("period_end") or "",
        "source_url": url,
        "lookback_months": lookback,
        "window_start": w_start.isoformat(),
        "window_end": w_end.isoformat(),
        "language": "en",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "paths": {
            "pdf": str(pdf_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "other_full": str(text_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  saved IR {code}/{slug} ({len(text)} chars)")
    return meta


def run_fetch_ir(*, force: bool = False) -> int:
    settings = load_yaml(REPO_ROOT / "config" / "settings.yaml")
    docs = load_ir_documents()
    if not docs:
        print("No IR documents in config/ir_documents.yaml")
        return 0
    sess = _session()
    saved = 0
    for doc in docs:
        try:
            meta = fetch_one_ir_document(doc, settings, sess, force=force)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip IR {doc.get('url')}: {exc}")
            time.sleep(0.5)
            continue
        if meta:
            saved += 1
        time.sleep(0.4)
    print(f"IR fetch complete: {saved} document(s)")
    return saved


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Fetch Samsung IR English PDFs")
    p.add_argument("--force", action="store_true", help="Re-download even if cached")
    args = p.parse_args(argv)
    run_fetch_ir(force=args.force)


if __name__ == "__main__":
    main()
