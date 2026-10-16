"""Greenhouse public Job Board API collector.

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

Keeps postings whose titles mention buyer / procurement / commodity manager /
supply chain. Board tokens live in data/job_boards.json.
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

SOURCE = "greenhouse"
API_TMPL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseCollector:
    def __init__(
        self,
        client: Optional[HttpClient] = None,
        boards: Optional[list[Board]] = None,
    ) -> None:
        self.client = client or HttpClient()
        self.boards = boards if boards is not None else boards_for("greenhouse")

    def collect(self) -> list[SignalRecord]:
        records: list[SignalRecord] = []
        for board in self.boards:
            try:
                records.extend(self._collect_board(board))
            except Exception:
                logger.exception(
                    "Greenhouse board failed token=%s company=%s",
                    board.token,
                    board.company_name,
                )
        records.sort(key=lambda r: r.timestamp, reverse=True)
        logger.info("Greenhouse unique matching jobs=%d", len(records))
        return records

    def _collect_board(self, board: Board) -> list[SignalRecord]:
        url = API_TMPL.format(token=quote(board.token, safe=""))
        payload = self.client.get_json(url, params={"content": "true"})
        jobs = payload.get("jobs") or []
        logger.info(
            "Greenhouse token=%s company=%s jobs=%d",
            board.token,
            board.company_name,
            len(jobs),
        )

        out: list[SignalRecord] = []
        for job in jobs:
            record = self._job_to_record(board, job)
            if record is not None:
                out.append(record)
        logger.info(
            "Greenhouse token=%s title matches=%d",
            board.token,
            len(out),
        )
        return out

    def _job_to_record(self, board: Board, job: dict[str, Any]) -> Optional[SignalRecord]:
        title = (job.get("title") or "").strip()
        keyword = matching_title_keyword(title)
        if keyword is None:
            return None

        job_id = job.get("id")
        if job_id is None:
            return None

        company = (job.get("company_name") or board.company_name or board.token).strip()
        absolute_url = (job.get("absolute_url") or "").strip()
        if not absolute_url:
            absolute_url = (
                f"https://job-boards.greenhouse.io/{board.token}/jobs/{job_id}"
            )

        location = ""
        loc = job.get("location") or {}
        if isinstance(loc, dict):
            location = (loc.get("name") or "").strip()

        body = html_to_text(job.get("content") or "")
        raw_text = self._compose_raw_text(title, location, body)

        return SignalRecord(
            source=SOURCE,
            timestamp=self._timestamp(job),
            company_name=company,
            raw_text=raw_text[: config.MAX_RAW_TEXT_CHARS],
            url=absolute_url,
            external_id=f"{board.token}:{job_id}",
            keyword=keyword,
        )

    @staticmethod
    def _compose_raw_text(title: str, location: str, body: str) -> str:
        parts = [f"Title: {title}"]
        if location:
            parts.append(f"Location: {location}")
        if body:
            parts.append(body)
        return "\n\n".join(parts)

    @staticmethod
    def _timestamp(job: dict[str, Any]) -> str:
        for key in ("updated_at", "first_published"):
            value = job.get(key)
            if not value:
                continue
            parsed = GreenhouseCollector._parse_iso(value)
            if parsed:
                return parsed
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_iso(value: str) -> Optional[str]:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
