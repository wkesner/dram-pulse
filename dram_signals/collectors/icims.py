"""iCIMS public careers-site collector.

iCIMS has no public job JSON API. Career portals expose HTML search pages
and per-job pages with schema.org JobPosting JSON-LD:

  GET https://{host}/jobs/search?ss=1&in_iframe=1&pr={page}
  GET https://{host}/jobs/{id}/job?in_iframe=1

Hosts live in data/job_boards.json under the ``icims`` key.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from bs4 import BeautifulSoup

from dram_signals import config
from dram_signals.boards import Board, boards_for
from dram_signals.db import SignalRecord
from dram_signals.http_client import HttpClient
from dram_signals.job_titles import matching_title_keyword
from dram_signals.textutil import html_to_text

logger = logging.getLogger(__name__)

SOURCE = "icims"
_JOB_HREF_RE = re.compile(r"/jobs/(\d+)/", re.IGNORECASE)
_TITLE_PREFIX_RE = re.compile(r"^(?:Job\s+)?Title\s+", re.IGNORECASE)
_PAGE_OF_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)


class IcimsCollector:
    def __init__(
        self,
        client: Optional[HttpClient] = None,
        boards: Optional[list[Board]] = None,
    ) -> None:
        self.client = client or HttpClient()
        self.boards = boards if boards is not None else boards_for("icims")

    def collect(self) -> list[SignalRecord]:
        records: list[SignalRecord] = []
        for board in self.boards:
            try:
                records.extend(self._collect_board(board))
            except Exception:
                logger.exception(
                    "iCIMS board failed token=%s host=%s",
                    board.token,
                    board.host,
                )
        records.sort(key=lambda r: r.timestamp, reverse=True)
        logger.info("iCIMS unique matching jobs=%d", len(records))
        return records

    def _collect_board(self, board: Board) -> list[SignalRecord]:
        host = board.host
        if not host:
            logger.warning("iCIMS board %s missing host", board.token)
            return []

        listings = self._list_jobs(host)
        logger.info(
            "iCIMS host=%s company=%s listed=%d",
            host,
            board.company_name,
            len(listings),
        )

        out: list[SignalRecord] = []
        for job_id, list_title in listings.items():
            keyword = matching_title_keyword(list_title)
            if keyword is None:
                continue
            try:
                record = self._fetch_record(board, job_id, list_title, keyword)
            except Exception:
                logger.exception("iCIMS detail failed host=%s id=%s", host, job_id)
                continue
            if record is not None:
                out.append(record)

        logger.info("iCIMS host=%s title matches=%d", host, len(out))
        return out

    def _list_jobs(self, host: str) -> dict[str, str]:
        """Return job_id -> title from paginated search HTML."""
        found: dict[str, str] = {}
        for page in range(0, 200):
            url = f"https://{host}/jobs/search?ss=1&in_iframe=1"
            if page:
                url += f"&pr={page}"
            html = self.client.get_text(url)
            before = len(found)
            soup = BeautifulSoup(html, "lxml")
            for anchor in soup.select("a[href*='/jobs/']"):
                href = anchor.get("href") or ""
                match = _JOB_HREF_RE.search(href)
                if not match:
                    continue
                job_id = match.group(1)
                title = _TITLE_PREFIX_RE.sub("", anchor.get_text(" ", strip=True)).strip()
                if not title:
                    # Fall back to slug in URL: /jobs/123/senior-buyer/job
                    slug = href.rstrip("/").split("/")
                    if len(slug) >= 2 and slug[-1] == "job":
                        title = slug[-2].replace("-", " ")
                if title and job_id not in found:
                    found[job_id] = title

            pager = _PAGE_OF_RE.search(html)
            if pager and int(pager.group(1)) >= int(pager.group(2)):
                break
            if len(found) == before:
                break
        return found

    def _fetch_record(
        self,
        board: Board,
        job_id: str,
        list_title: str,
        keyword: str,
    ) -> Optional[SignalRecord]:
        host = board.host or ""
        detail_url = f"https://{host}/jobs/{job_id}/job?in_iframe=1"
        html = self.client.get_text(detail_url)
        posting = self._parse_job_posting(html)
        if posting is None:
            logger.warning("iCIMS missing JobPosting JSON-LD host=%s id=%s", host, job_id)
            return None

        title = (posting.get("title") or list_title).strip()
        # Prefer the detail-page title for the gate (list HTML can be noisy).
        keyword = matching_title_keyword(title)
        if keyword is None:
            return None

        company = (
            ((posting.get("hiringOrganization") or {}).get("name"))
            or board.company_name
        ).strip()
        public_url = (posting.get("url") or "").strip()
        if not public_url:
            public_url = f"https://{host}/jobs/{job_id}/job"

        location = self._format_location(posting.get("jobLocation"))
        body = html_to_text(posting.get("description") or "")
        raw_text = self._compose_raw_text(title, location, body)

        return SignalRecord(
            source=SOURCE,
            timestamp=self._timestamp(posting.get("datePosted")),
            company_name=company,
            raw_text=raw_text[: config.MAX_RAW_TEXT_CHARS],
            url=public_url,
            external_id=f"{board.token}:{job_id}",
            keyword=keyword,
        )

    @staticmethod
    def _parse_job_posting(html: str) -> Optional[dict[str, Any]]:
        for match in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    return item
        return None

    @staticmethod
    def _format_location(job_location: Any) -> str:
        if not job_location:
            return ""
        places = job_location if isinstance(job_location, list) else [job_location]
        parts: list[str] = []
        for place in places:
            if not isinstance(place, dict):
                continue
            address = place.get("address") or {}
            if not isinstance(address, dict):
                continue
            chunks = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ]
            label = ", ".join(
                str(c) for c in chunks if c and str(c).upper() != "UNAVAILABLE"
            )
            if label:
                parts.append(label)
        return "; ".join(parts)

    @staticmethod
    def _compose_raw_text(title: str, location: str, body: str) -> str:
        parts = [f"Title: {title}"]
        if location:
            parts.append(f"Location: {location}")
        if body:
            parts.append(body)
        return "\n\n".join(parts)

    @staticmethod
    def _timestamp(date_posted: Any) -> str:
        if isinstance(date_posted, str) and date_posted:
            text = date_posted.strip().replace("Z", "+00:00")
            try:
                # Prefer date portion; iCIMS times can be unstable.
                if "T" in text:
                    dt = datetime.fromisoformat(text)
                else:
                    dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).isoformat()
            except ValueError:
                pass
        return datetime.now(timezone.utc).isoformat()
