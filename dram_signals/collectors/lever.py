"""Lever public Postings API collector.

GET https://api.lever.co/v0/postings/{site}?mode=json

Keeps postings whose titles mention buyer / procurement / commodity manager /
supply chain. Site slugs live in data/job_boards.json.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from dram_signals import config
from dram_signals.boards import Board, boards_for
from dram_signals.db import SignalRecord
from dram_signals.http_client import HttpClient
from dram_signals.job_titles import matching_title_keyword
from dram_signals.textutil import html_to_text

logger = logging.getLogger(__name__)

SOURCE = "lever"
API_TMPL = "https://api.lever.co/v0/postings/{token}"
PAGE_SIZE = 100


class LeverCollector:
    def __init__(
        self,
        client: Optional[HttpClient] = None,
        boards: Optional[list[Board]] = None,
    ) -> None:
        self.client = client or HttpClient()
        self.boards = boards if boards is not None else boards_for("lever")

    def collect(self) -> list[SignalRecord]:
        records: list[SignalRecord] = []
        for board in self.boards:
            try:
                records.extend(self._collect_board(board))
            except Exception:
                logger.exception(
                    "Lever board failed token=%s company=%s",
                    board.token,
                    board.company_name,
                )
        records.sort(key=lambda r: r.timestamp, reverse=True)
        logger.info("Lever unique matching jobs=%d", len(records))
        return records

    def _collect_board(self, board: Board) -> list[SignalRecord]:
        postings = self._fetch_all(board.token)
        logger.info(
            "Lever token=%s company=%s postings=%d",
            board.token,
            board.company_name,
            len(postings),
        )

        out: list[SignalRecord] = []
        for posting in postings:
            record = self._posting_to_record(board, posting)
            if record is not None:
                out.append(record)
        logger.info("Lever token=%s title matches=%d", board.token, len(out))
        return out

    def _fetch_all(self, token: str) -> list[dict[str, Any]]:
        url = API_TMPL.format(token=quote(token, safe=""))
        all_rows: list[dict[str, Any]] = []
        skip = 0
        while True:
            page = self.client.get_json(
                url,
                params={"mode": "json", "limit": PAGE_SIZE, "skip": skip},
            )
            if not isinstance(page, list):
                logger.warning("Lever unexpected payload for %s: %r", token, type(page))
                break
            all_rows.extend(page)
            if len(page) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
            if skip > 5000:
                logger.warning("Lever pagination cap reached for %s", token)
                break
        return all_rows

    def _posting_to_record(
        self,
        board: Board,
        posting: dict[str, Any],
    ) -> Optional[SignalRecord]:
        title = (posting.get("text") or "").strip()
        keyword = matching_title_keyword(title)
        if keyword is None:
            return None

        posting_id = posting.get("id")
        if not posting_id:
            return None

        url = (posting.get("hostedUrl") or posting.get("applyUrl") or "").strip()
        if not url:
            url = f"https://jobs.lever.co/{board.token}/{posting_id}"

        categories = posting.get("categories") or {}
        location = ""
        if isinstance(categories, dict):
            location = (categories.get("location") or "").strip()

        body = (
            posting.get("descriptionPlain")
            or posting.get("descriptionBodyPlain")
            or html_to_text(posting.get("description") or "")
        )
        raw_text = self._compose_raw_text(title, location, body or "")

        return SignalRecord(
            source=SOURCE,
            timestamp=self._timestamp(posting),
            company_name=board.company_name,
            raw_text=raw_text[: config.MAX_RAW_TEXT_CHARS],
            url=url,
            external_id=f"{board.token}:{posting_id}",
            keyword=keyword,
        )

    @staticmethod
    def _compose_raw_text(title: str, location: str, body: str) -> str:
        parts = [f"Title: {title}"]
        if location:
            parts.append(f"Location: {location}")
        if body:
            parts.append(body.strip())
        return "\n\n".join(parts)

    @staticmethod
    def _timestamp(posting: dict[str, Any]) -> str:
        created = posting.get("createdAt")
        if isinstance(created, (int, float)):
            # Lever returns epoch milliseconds.
            seconds = created / 1000.0 if created > 1e12 else float(created)
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        return datetime.now(timezone.utc).isoformat()
