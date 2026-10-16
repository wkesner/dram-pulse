"""Thin Airtable REST client: upsert batches, throttle, 429 retry."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
MIN_INTERVAL_S = 0.25  # 4 req/s (limit is 5/s)


class AirtableError(RuntimeError):
    def __init__(self, message: str, *, failed_keys: Optional[list[str]] = None) -> None:
        super().__init__(message)
        self.failed_keys = failed_keys or []


class AirtableClient:
    def __init__(
        self,
        token: str,
        base_id: str,
        *,
        session: Optional[requests.Session] = None,
        api_base: str = "https://api.airtable.com/v0",
        rate_limit_wait_s: float = 30.0,
        min_interval_s: float = MIN_INTERVAL_S,
    ) -> None:
        self.token = token
        self.base_id = base_id
        self.api_base = api_base.rstrip("/")
        self.rate_limit_wait_s = rate_limit_wait_s
        self.min_interval_s = min_interval_s
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )
        self._last_request_at = 0.0
        self.api_calls = 0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)

    def _url(self, table: str) -> str:
        from urllib.parse import quote

        return f"{self.api_base}/{self.base_id}/{quote(table, safe='')}"

    def upsert(
        self,
        table: str,
        records: list[dict[str, Any]],
        *,
        merge_fields: list[str],
        typecast: bool = True,
    ) -> dict[str, Any]:
        """PATCH upsert in batches of 10. Returns aggregated created/updated ids."""
        created: list[str] = []
        updated: list[str] = []
        all_records: list[dict[str, Any]] = []

        for i in range(0, len(records), BATCH_SIZE):
            chunk = records[i : i + BATCH_SIZE]
            payload = {
                "performUpsert": {"fieldsToMergeOn": merge_fields},
                "records": chunk,
                "typecast": typecast,
            }
            data = self._request("PATCH", self._url(table), json_body=payload)
            created.extend(data.get("createdRecords") or [])
            updated.extend(data.get("updatedRecords") or [])
            all_records.extend(data.get("records") or [])

        return {
            "createdRecords": created,
            "updatedRecords": updated,
            "records": all_records,
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.session.request(
                    method, url, json=json_body, timeout=60
                )
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(2 ** attempt)
                continue
            self._last_request_at = time.monotonic()
            self.api_calls += 1
            if resp.status_code == 429:
                wait = self.rate_limit_wait_s
                logger.warning("Airtable 429 — sleep %ss (attempt %d/%d)", wait, attempt + 1, retries)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise AirtableError(
                    f"Airtable HTTP {resp.status_code}: {resp.text[:500]}"
                )
            return resp.json()
        raise AirtableError(f"Airtable request failed after retries: {last_exc}")
