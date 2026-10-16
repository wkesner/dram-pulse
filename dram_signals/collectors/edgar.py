"""SEC EDGAR 8-K full-text search collector.

Pulls filings matching DRAM-relevant keywords from the public EFTS API
(https://efts.sec.gov/LATEST/search-index), fetches matching documents,
and emits normalized SignalRecord rows. Broad capture — no demand/supply
classification here.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any, Optional

from bs4 import BeautifulSoup

from dram_signals import config
from dram_signals.db import SignalRecord
from dram_signals.http_client import HttpClient

logger = logging.getLogger(__name__)

SOURCE = "edgar_8k"
PAGE_SIZE = 100
MAX_OFFSET = 9900

# "ACME CORP  (TICK)  (CIK 0001234567)" → "ACME CORP"
# Also handles multi-ticker forms like "(XLAB, XLABW)".
_DISPLAY_NAME_RE = re.compile(
    r"^(?P<name>.*?)\s*(?:\([A-Z0-9.,\-\s]+\))?\s*(?:\(CIK\s+\d+\))?\s*$",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class EdgarCollector:
    """Collect 8-K hits from EDGAR full-text search for configured keywords."""

    def __init__(
        self,
        client: Optional[HttpClient] = None,
        keywords: tuple[str, ...] = config.EDGAR_KEYWORDS,
        lookback_days: int = config.EDGAR_DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self.client = client or HttpClient()
        self.keywords = keywords
        self.lookback_days = lookback_days

    def collect(
        self,
        *,
        start: Optional[date] = None,
        end: Optional[date] = None,
        fetch_documents: bool = True,
    ) -> list[SignalRecord]:
        end_dt = end or date.today()
        start_dt = start or (end_dt - timedelta(days=self.lookback_days))

        # Collect unique hits across keywords first, then fetch bodies once.
        # external_id -> (hit, merged keywords)
        pending: dict[str, tuple[dict[str, Any], str]] = {}

        for keyword in self.keywords:
            logger.info(
                "EDGAR search keyword=%r forms=%s range=%s..%s",
                keyword,
                config.EDGAR_FORMS,
                start_dt,
                end_dt,
            )
            hits = self._search_all(keyword, start_dt, end_dt)
            logger.info("EDGAR keyword=%r hits=%d", keyword, len(hits))

            for hit in hits:
                hit_id = hit.get("_id") or ""
                if ":" not in hit_id:
                    continue
                if hit_id not in pending:
                    pending[hit_id] = (hit, keyword)
                else:
                    existing_hit, merged_kw = pending[hit_id]
                    if keyword and keyword not in merged_kw.split("|"):
                        pending[hit_id] = (
                            existing_hit,
                            f"{merged_kw}|{keyword}" if merged_kw else keyword,
                        )

        records: list[SignalRecord] = []
        for hit, keywords in pending.values():
            record = self._hit_to_record(hit, keywords, fetch_documents=fetch_documents)
            if record is not None:
                records.append(record)

        records.sort(key=lambda r: r.timestamp, reverse=True)
        logger.info("EDGAR unique records=%d", len(records))
        return records

    def _search_all(self, keyword: str, start: date, end: date) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        offset = 0
        query = self._build_query(keyword)

        while offset <= MAX_OFFSET:
            payload = self.client.get_json(
                config.EDGAR_SEARCH_URL,
                params={
                    "q": query,
                    "forms": config.EDGAR_FORMS,
                    "dateRange": "custom",
                    "startdt": start.isoformat(),
                    "enddt": end.isoformat(),
                    "from": offset,
                },
            )
            page = payload.get("hits", {}).get("hits", [])
            total = payload.get("hits", {}).get("total", {})
            total_value = int(total.get("value", 0)) if isinstance(total, dict) else int(total or 0)

            hits.extend(page)
            logger.debug(
                "EDGAR page offset=%d got=%d total=%d",
                offset,
                len(page),
                total_value,
            )

            if not page or offset + PAGE_SIZE >= total_value:
                break
            offset += PAGE_SIZE

        return hits

    @staticmethod
    def _build_query(keyword: str) -> str:
        # Phrase search for multi-word terms; single tokens as-is.
        keyword = keyword.strip()
        if " " in keyword and not (keyword.startswith('"') and keyword.endswith('"')):
            return f'"{keyword}"'
        return keyword

    def _hit_to_record(
        self,
        hit: dict[str, Any],
        keyword: str,
        *,
        fetch_documents: bool,
    ) -> Optional[SignalRecord]:
        source = hit.get("_source") or {}
        hit_id = hit.get("_id") or ""
        if ":" not in hit_id:
            logger.warning("Skipping hit without document id: %s", hit_id)
            return None

        adsh, filename = hit_id.split(":", 1)
        ciks = source.get("ciks") or []
        if not ciks:
            logger.warning("Skipping hit without CIK: %s", hit_id)
            return None

        cik = str(ciks[0]).lstrip("0") or "0"
        company = self._parse_company_name(source.get("display_names") or [])
        file_date = source.get("file_date") or ""
        timestamp = self._to_timestamp(file_date)
        url = self._document_url(cik, adsh, filename)
        external_id = f"{adsh}:{filename}"

        if fetch_documents:
            try:
                raw_text = self._fetch_raw_text(url)
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s — storing metadata stub", url, exc)
                raw_text = self._metadata_stub(source, keyword, filename)
        else:
            raw_text = self._metadata_stub(source, keyword, filename)

        return SignalRecord(
            source=SOURCE,
            timestamp=timestamp,
            company_name=company,
            raw_text=raw_text,
            url=url,
            external_id=external_id,
            keyword=keyword,
        )

    def _fetch_raw_text(self, url: str) -> str:
        html = self.client.get_text(url)
        return self._html_to_text(html)[: config.MAX_RAW_TEXT_CHARS]

    @staticmethod
    def _html_to_text(html: str) -> str:
        # Prefer BeautifulSoup for HTML/SGML EDGAR wrappers; fall back to strip tags.
        try:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text(separator=" ")
        except Exception:
            text = _TAG_RE.sub(" ", html)
        text = unescape(text)
        return _WS_RE.sub(" ", text).strip()

    @staticmethod
    def _metadata_stub(source: dict[str, Any], keyword: str, filename: str) -> str:
        items = source.get("items") or []
        desc = source.get("file_description") or source.get("file_type") or filename
        return (
            f"[metadata only] keyword={keyword}; "
            f"form={source.get('form')}; items={','.join(items)}; "
            f"description={desc}"
        )

    @staticmethod
    def _parse_company_name(display_names: list[str]) -> str:
        if not display_names:
            return "Unknown"
        raw = display_names[0].strip()
        match = _DISPLAY_NAME_RE.match(raw)
        if match:
            name = match.group("name").strip(" -")
            if name:
                return name
        # Fallback: cut at first parenthetical
        return raw.split("(")[0].strip() or raw

    @staticmethod
    def _to_timestamp(file_date: str) -> str:
        """Normalize filing date to ISO-8601 UTC midnight when only a date is known."""
        if not file_date:
            return datetime.now(timezone.utc).isoformat()
        try:
            d = date.fromisoformat(file_date)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()
        except ValueError:
            return file_date

    @staticmethod
    def _document_url(cik: str, adsh: str, filename: str) -> str:
        adsh_nodash = adsh.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik}/{adsh_nodash}/{filename}"
        )
