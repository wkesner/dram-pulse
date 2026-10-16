"""Shared HTTP client with SEC-friendly defaults."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from dram_signals import config

logger = logging.getLogger(__name__)


class HttpClient:
    def __init__(
        self,
        user_agent: str = config.USER_AGENT,
        delay_seconds: float = config.REQUEST_DELAY_SECONDS,
        timeout: float = 60.0,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    def get_json(self, url: str, params: Optional[dict[str, Any]] = None) -> Any:
        self._throttle()
        response = self.session.get(url, params=params, timeout=self.timeout)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        return response.json()

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        self._throttle()
        response = self.session.post(
            url,
            params=params,
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self.timeout,
        )
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str, *, retries: int = 3) -> str:
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            response = self.session.get(url, timeout=self.timeout)
            self._last_request_at = time.monotonic()
            if response.status_code in {429, 502, 503, 504} and attempt + 1 < retries:
                wait = self.delay_seconds * (2**attempt) + 0.5
                logger.warning(
                    "HTTP %s for %s — retry in %.1fs (%d/%d)",
                    response.status_code,
                    url,
                    wait,
                    attempt + 1,
                    retries,
                )
                time.sleep(wait)
                continue
            try:
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - re-raised after loop
                last_exc = exc
                if attempt + 1 < retries:
                    time.sleep(self.delay_seconds * (2**attempt) + 0.5)
                    continue
                raise
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        assert last_exc is not None
        raise last_exc
