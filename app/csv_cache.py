"""Process-local CSV cache keyed by content digest (no paid infra)."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
from threading import Lock
from typing import Any


class CsvCache:
    """Thread-safe DictReader cache.

    Always reads file bytes (cheap) and skips CSV parse when the SHA-1 matches.
    Callers that append/overwrite in-process should still ``invalidate`` so
    concurrent readers do not briefly see a torn write.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        # path → (sha1 hex, rows)
        self._rows: dict[str, tuple[str, list[dict[str, str]]]] = {}

    def _key(self, path: Path) -> str:
        try:
            return str(path.resolve())
        except FileNotFoundError:
            return str(path)

    def read(self, path: Path) -> list[dict[str, str]]:
        key = self._key(path)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            with self._lock:
                self._rows.pop(key, None)
            return []
        if not data:
            return []

        digest = hashlib.sha1(data).hexdigest()
        with self._lock:
            hit = self._rows.get(key)
            if hit and hit[0] == digest:
                return hit[1]

        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
        with self._lock:
            self._rows[key] = (digest, rows)
        return rows

    def invalidate(self, path: Path) -> None:
        key = self._key(path)
        alt = str(path)
        with self._lock:
            self._rows.pop(key, None)
            self._rows.pop(alt, None)

    def fingerprint(self, *paths: Path) -> str:
        """Stable fingerprint for ETags (content digest)."""
        parts: list[str] = []
        for path in paths:
            key = self._key(path)
            with self._lock:
                hit = self._rows.get(key)
            if hit:
                parts.append(f"{path.name}:{hit[0]}")
                continue
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                parts.append(f"{path.name}:missing")
                continue
            if not data:
                parts.append(f"{path.name}:empty")
                continue
            digest = hashlib.sha1(data).hexdigest()
            parts.append(f"{path.name}:{digest}")
        return "|".join(parts)


class DerivedCache:
    def __init__(self) -> None:
        self._lock = Lock()
        self._store: dict[str, tuple[str, Any]] = {}

    def get_or_build(self, key: str, fingerprint: str, builder: Any) -> Any:
        with self._lock:
            hit = self._store.get(key)
            if hit and hit[0] == fingerprint:
                return hit[1]
        value = builder()
        with self._lock:
            self._store[key] = (fingerprint, value)
        return value

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            doomed = [k for k in self._store if k.startswith(prefix)]
            for k in doomed:
                del self._store[k]
