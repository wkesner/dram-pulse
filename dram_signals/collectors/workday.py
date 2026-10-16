"""Workday public CXS careers collector.

POST {cxs_jobs_url} with JSON {"limit","offset","searchText"} then fetch
job detail pages for title matches. Used for Micron, major EMS, and OEMs
that do not publish Greenhouse/Lever boards.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from dram_signals import config
from dram_signals.boards import Board, boards_for
from dram_signals.db import SignalRecord
from dram_signals.http_client import HttpClient
from dram_signals.job_titles import matching_title_keyword
from dram_signals.textutil import html_to_text

logger = logging.getLogger(__name__)

SOURCE = "workday"
PAGE_SIZE = 20
# Cap pages per search term so large EMS boards (Jabil/Flex) stay tractable.
MAX_OFFSET_PER_TERM = 200
# Workday full-text search terms that surface title-relevant roles.
SEARCH_TERMS = (
    "buyer",
    "procurement",
    "commodity manager",
    "supply chain",
)


class WorkdayCollector:
    def __init__(
        self,
        client: Optional[HttpClient] = None,
        boards: Optional[list[Board]] = None,
    ) -> None:
        self.client = client or HttpClient()
        self.boards = boards if boards is not None else boards_for("workday")

    def collect(self) -> list[SignalRecord]:
        records: list[SignalRecord] = []
        for board in self.boards:
            try:
                records.extend(self._collect_board(board))
            except Exception:
                logger.exception(
                    "Workday board failed token=%s company=%s",
                    board.token,
                    board.company_name,
                )
        records.sort(key=lambda r: r.timestamp, reverse=True)
        logger.info("Workday unique matching jobs=%d", len(records))
        return records

    def _collect_board(self, board: Board) -> list[SignalRecord]:
        if not board.cxs_jobs_url:
            logger.warning("Workday board %s missing cxs_jobs_url", board.token)
            return []

        # Deduplicate title matches across search terms by externalPath.
        matches: dict[str, tuple[dict[str, Any], str]] = {}
        for term in SEARCH_TERMS:
            for posting in self._search(board.cxs_jobs_url, term):
                path = posting.get("externalPath")
                title = (posting.get("title") or "").strip()
                keyword = matching_title_keyword(title)
                if not path or keyword is None:
                    continue
                if path not in matches:
                    matches[path] = (posting, keyword)

        logger.info(
            "Workday token=%s company=%s title matches=%d",
            board.token,
            board.company_name,
            len(matches),
        )

        out: list[SignalRecord] = []
        for path, (posting, keyword) in matches.items():
            try:
                record = self._to_record(board, posting, keyword)
            except Exception:
                logger.exception(
                    "Workday detail failed token=%s path=%s",
                    board.token,
                    path,
                )
                continue
            if record is not None:
                out.append(record)

        logger.info("Workday token=%s records=%d", board.token, len(out))
        return out

    def _search(self, jobs_url: str, search_text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        total = None
        while True:
            payload = self.client.post_json(
                jobs_url,
                {
                    "appliedFacets": {},
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": search_text,
                },
            )
            page = payload.get("jobPostings") or []
            if total is None:
                total = int(payload.get("total") or 0)
                logger.debug(
                    "Workday search=%r total=%d url=%s",
                    search_text,
                    total,
                    jobs_url,
                )
            rows.extend(page)
            offset += PAGE_SIZE
            if not page or offset >= total or offset >= MAX_OFFSET_PER_TERM:
                break
        return rows

    def _to_record(
        self,
        board: Board,
        posting: dict[str, Any],
        keyword: str,
    ) -> Optional[SignalRecord]:
        path = posting.get("externalPath") or ""
        detail_url = self._detail_api_url(board.cxs_jobs_url or "", path)
        detail = self.client.get_json(detail_url)
        info = detail.get("jobPostingInfo") or {}

        title = (info.get("title") or posting.get("title") or "").strip()
        job_id = (
            info.get("id")
            or info.get("jobReqId")
            or (posting.get("bulletFields") or [None])[0]
            or path
        )
        public_url = (info.get("externalUrl") or "").strip()
        if not public_url:
            public_url = self._public_job_url(board.cxs_jobs_url or "", path)

        location = (info.get("location") or posting.get("locationsText") or "").strip()
        body = html_to_text(info.get("jobDescription") or "")
        raw_text = self._compose_raw_text(title, location, body)

        return SignalRecord(
            source=SOURCE,
            timestamp=self._timestamp(info, posting),
            company_name=board.company_name,
            raw_text=raw_text[: config.MAX_RAW_TEXT_CHARS],
            url=public_url,
            external_id=f"{board.token}:{job_id}",
            keyword=keyword,
        )

    @staticmethod
    def _detail_api_url(jobs_url: str, external_path: str) -> str:
        # .../wday/cxs/{tenant}/{site}/jobs → .../wday/cxs/{tenant}/{site}{path}
        base = jobs_url[: -len("/jobs")] if jobs_url.endswith("/jobs") else jobs_url
        return urljoin(base + "/", external_path.lstrip("/"))

    @staticmethod
    def _public_job_url(jobs_url: str, external_path: str) -> str:
        parts = urlsplit(jobs_url)
        # /wday/cxs/{tenant}/{site}/jobs → /{site}{externalPath}
        segs = [s for s in parts.path.split("/") if s]
        site = segs[3] if len(segs) >= 4 else "External"
        return f"{parts.scheme}://{parts.netloc}/{site}{external_path}"

    @staticmethod
    def _compose_raw_text(title: str, location: str, body: str) -> str:
        parts = [f"Title: {title}"]
        if location:
            parts.append(f"Location: {location}")
        if body:
            parts.append(body)
        return "\n\n".join(parts)

    @staticmethod
    def _timestamp(info: dict[str, Any], posting: dict[str, Any]) -> str:
        for key in ("startDate", "posted"):
            value = info.get(key)
            if isinstance(value, str) and value:
                try:
                    # startDate is YYYY-MM-DD; posted may be ISO.
                    if len(value) == 10:
                        dt = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
                    else:
                        text = value.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(text)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc).isoformat()
                except ValueError:
                    continue
        return datetime.now(timezone.utc).isoformat()
