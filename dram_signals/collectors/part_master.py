"""Step 3: part master from distributor APIs (Octopart/Nexar, DigiKey).

Seeds MPNs from Samsung sitemap part slugs, decoded PNs in signals.raw_text,
and optional seed files. Enrichment prefers Octopart, then DigiKey. Without
API credentials, a local mock provider returns lifecycle heuristics so the
pipeline stays runnable offline.

Lifecycle changes emit signals with source='part_lifecycle'.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol
from urllib.parse import urlparse

from dram_signals import config
from dram_signals.db import SignalRecord, db_session, set_collector_state, upsert_signals
from dram_signals.http_client import HttpClient
from dram_signals.partnums import decode, extract_candidate_mpns, normalize_pn
from dram_signals.robots_util import allowed as robots_allowed

logger = logging.getLogger(__name__)

SOURCE = "part_master"
LIFECYCLE_SIGNAL_SOURCE = "part_lifecycle"
CACHE_DIR = config.DATA_DIR / "part_master_cache"
CACHE_TTL_SECONDS = int(os.environ.get("DRAM_SIGNALS_PART_CACHE_TTL", str(7 * 24 * 3600)))
SAMSUNG_BASE = "https://semiconductor.samsung.com"
PART_PATH_RE = re.compile(r"^/(dram|mcp)/[^/]+/[^/]+/([a-z0-9-]+)/$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LifecycleProvider(Protocol):
    name: str

    def lookup(self, vendor: str, mpn: str) -> Optional[dict[str, Any]]:
        """Return {lifecycle_status, raw, ...} or None if unknown."""


class MockLifecycleProvider:
    """Offline fallback — no network. Maps family heuristics to lifecycle."""

    name = "mock"

    # Families commonly EOL on Samsung family pages (verified Step 0 / Step 1).
    EOL_FAMILIES = {"LPDDR4", "LPDDR4X", "DDR3", "GDDR5"}
    ACTIVE_FAMILIES = {"GDDR7", "LPDDR5X", "LPDDR5", "HBM", "DDR5", "GDDR6"}

    def lookup(self, vendor: str, mpn: str) -> Optional[dict[str, Any]]:
        decoded = decode(mpn)
        if not decoded:
            return {
                "lifecycle_status": "unknown",
                "raw": {"provider": self.name, "reason": "undecoded"},
            }
        family = str(decoded.get("family") or "").upper()
        if family in self.EOL_FAMILIES:
            status = "EOL"
        elif family in self.ACTIVE_FAMILIES:
            status = "Active"
        else:
            status = "unknown"
        return {
            "lifecycle_status": status,
            "raw": {
                "provider": self.name,
                "family": family,
                "vendor": vendor,
                "mpn": mpn,
            },
        }


class CachedProvider:
    """Disk-cache wrapper (7-day default) so API quotas survive re-runs."""

    def __init__(self, inner: LifecycleProvider, cache_dir: Path = CACHE_DIR) -> None:
        self.inner = inner
        self.name = f"cache:{inner.name}"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, vendor: str, mpn: str) -> Path:
        digest = hashlib.sha256(f"{self.inner.name}:{vendor}:{mpn}".encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def lookup(self, vendor: str, mpn: str) -> Optional[dict[str, Any]]:
        path = self._key(vendor, mpn)
        if path.exists():
            age = time.time() - path.stat().st_mtime
            if age < CACHE_TTL_SECONDS:
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    path.unlink(missing_ok=True)
        result = self.inner.lookup(vendor, mpn)
        if result is not None:
            path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
        return result


class OctopartProvider:
    """Nexar/Octopart GraphQL lookup. Requires NEXAR_TOKEN or OCTOPART_TOKEN."""

    name = "octopart"
    ENDPOINT = "https://api.nexar.com/graphql"

    def __init__(self, token: str, client: Optional[HttpClient] = None) -> None:
        self.token = token
        self.client = client or HttpClient(delay_seconds=2.0, timeout=30.0)

    def lookup(self, vendor: str, mpn: str) -> Optional[dict[str, Any]]:
        query = """
        query PartSearch($q: String!) {
          supSearch(q: $q, limit: 3) {
            results {
              part {
                mpn
                manufacturer { name }
                category { name }
                specs { attribute { name } value }
              }
            }
          }
        }
        """
        self.client._throttle()
        resp = self.client.session.post(
            self.ENDPOINT,
            json={"query": query, "variables": {"q": mpn}},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=self.client.timeout,
        )
        self.client._last_request_at = time.monotonic()
        if resp.status_code in {401, 403}:
            logger.error("Octopart/Nexar auth failed HTTP %s", resp.status_code)
            return None
        resp.raise_for_status()
        payload = resp.json()
        results = (
            ((payload.get("data") or {}).get("supSearch") or {}).get("results") or []
        )
        if not results:
            return {
                "lifecycle_status": "unknown",
                "raw": {"provider": self.name, "hit": False, "mpn": mpn},
            }
        part = (results[0] or {}).get("part") or {}
        specs = {
            (s.get("attribute") or {}).get("name"): s.get("value")
            for s in (part.get("specs") or [])
            if s
        }
        lifecycle = (
            specs.get("Lifecycle Status")
            or specs.get("LifecycleStatus")
            or specs.get("Part Lifecycle")
            or "unknown"
        )
        return {
            "lifecycle_status": _normalize_lifecycle(str(lifecycle)),
            "raw": {"provider": self.name, "part": part, "specs": specs},
        }


class DigiKeyProvider:
    """DigiKey Product Information v4 — needs DIGIKEY_CLIENT_ID + SECRET."""

    name = "digikey"
    TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
    SEARCH_URL = (
        "https://api.digikey.com/products/v4/search/{keyword}/productdetails"
    )

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        client: Optional[HttpClient] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = client or HttpClient(delay_seconds=2.0, timeout=30.0)
        self._access_token: Optional[str] = None
        self._token_expires_at = 0.0

    def _ensure_token(self) -> Optional[str]:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        self.client._throttle()
        resp = self.client.session.post(
            self.TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
            timeout=self.client.timeout,
        )
        self.client._last_request_at = time.monotonic()
        if resp.status_code >= 400:
            logger.error("DigiKey token failed HTTP %s", resp.status_code)
            return None
        data = resp.json()
        self._access_token = data.get("access_token")
        self._token_expires_at = time.time() + float(data.get("expires_in") or 600)
        return self._access_token

    def lookup(self, vendor: str, mpn: str) -> Optional[dict[str, Any]]:
        token = self._ensure_token()
        if not token:
            return None
        url = self.SEARCH_URL.format(keyword=mpn)
        self.client._throttle()
        resp = self.client.session.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "X-DIGIKEY-Client-Id": self.client_id,
                "Accept": "application/json",
            },
            timeout=self.client.timeout,
        )
        self.client._last_request_at = time.monotonic()
        if resp.status_code == 404:
            return {
                "lifecycle_status": "unknown",
                "raw": {"provider": self.name, "hit": False, "mpn": mpn},
            }
        if resp.status_code >= 400:
            logger.warning("DigiKey lookup HTTP %s for %s", resp.status_code, mpn)
            return None
        data = resp.json()
        product = data.get("Product") or data.get("product") or data
        status = (
            product.get("ProductStatus")
            or product.get("ProductStatusName")
            or (product.get("ProductStatusV2") or {}).get("Status")
            or "unknown"
        )
        return {
            "lifecycle_status": _normalize_lifecycle(str(status)),
            "raw": {"provider": self.name, "product": product},
        }


def _normalize_lifecycle(value: str) -> str:
    text = (value or "").strip().lower()
    if not text or text == "unknown":
        return "unknown"
    if "obsolete" in text:
        return "Obsolete"
    if "end of life" in text or text in {"eol", "end-of-life"}:
        return "EOL"
    if "nrnd" in text or "not recommended" in text:
        return "NRND"
    if "active" in text or "production" in text:
        return "Active"
    return value.strip()


def build_providers(client: Optional[HttpClient] = None) -> list[LifecycleProvider]:
    """Octopart → DigiKey → mock. Force mock with DRAM_SIGNALS_PART_MASTER_MOCK=1."""
    force_mock = os.environ.get("DRAM_SIGNALS_PART_MASTER_MOCK", "").lower() in {
        "1",
        "true",
        "yes",
    }
    providers: list[LifecycleProvider] = []
    if not force_mock:
        nexar = os.environ.get("NEXAR_TOKEN") or os.environ.get("OCTOPART_TOKEN")
        if nexar:
            providers.append(CachedProvider(OctopartProvider(nexar, client=client)))
        dk_id = os.environ.get("DIGIKEY_CLIENT_ID")
        dk_secret = os.environ.get("DIGIKEY_CLIENT_SECRET")
        if dk_id and dk_secret:
            providers.append(
                CachedProvider(DigiKeyProvider(dk_id, dk_secret, client=client))
            )
    providers.append(CachedProvider(MockLifecycleProvider()))
    return providers


def seed_mpns_from_samsung_sitemap(client: HttpClient) -> list[tuple[str, str]]:
    """Return [(vendor, mpn), ...] from Samsung part-like sitemap slugs."""
    out: list[tuple[str, str]] = []
    if not robots_allowed(f"{SAMSUNG_BASE}/sitemap.xml"):
        return out
    locs: list[str] = []
    for sm in (f"{SAMSUNG_BASE}/sitemap.xml", f"{SAMSUNG_BASE}/top_sitemap.xml"):
        try:
            text = client.get_text(sm)
        except Exception:
            logger.exception("sitemap fetch failed %s", sm)
            continue
        text = re.sub(r'\sxmlns="[^"]+"', "", text, count=1)
        root = ET.fromstring(text)
        for url_el in root.findall("url"):
            loc = url_el.findtext("loc")
            if loc:
                locs.append(loc)
    seen: set[str] = set()
    for loc in locs:
        path = urlparse(loc).path
        m = PART_PATH_RE.match(path)
        if not m:
            continue
        slug = m.group(2).upper()
        mpn = normalize_pn(slug)
        if mpn in seen:
            continue
        if not decode(mpn):
            # Still seed; decoder may be incomplete for some modules.
            pass
        seen.add(mpn)
        out.append(("samsung", mpn))
    return out


def seed_mpns_from_signals(conn: Any) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT raw_text FROM signals").fetchall()
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        for mpn in extract_candidate_mpns(row["raw_text"] or ""):
            decoded = decode(mpn)
            vendor = (decoded or {}).get("vendor") or "unknown"
            key = f"{vendor}:{mpn}"
            if key in seen:
                continue
            seen.add(key)
            found.append((str(vendor), mpn))
    return found


def seed_mpns_from_file(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "," in line:
            vendor, mpn = line.split(",", 1)
        elif "\t" in line:
            vendor, mpn = line.split("\t", 1)
        else:
            mpn = line
            decoded = decode(mpn)
            vendor = (decoded or {}).get("vendor") or "unknown"
        out.append((vendor.strip().lower(), normalize_pn(mpn)))
    return out


class PartMasterCollector:
    def __init__(
        self,
        client: Optional[HttpClient] = None,
        *,
        providers: Optional[list[LifecycleProvider]] = None,
        seed_file: Optional[Path] = None,
        max_parts: Optional[int] = None,
        skip_sitemap: bool = False,
    ) -> None:
        self.client = client or HttpClient(delay_seconds=2.0, timeout=30.0)
        self.providers = providers or build_providers(self.client)
        self.seed_file = seed_file or (
            Path(os.environ["DRAM_SIGNALS_PART_SEED"])
            if os.environ.get("DRAM_SIGNALS_PART_SEED")
            else config.DATA_DIR / "part_seed.txt"
        )
        self.max_parts = max_parts
        self.skip_sitemap = skip_sitemap

    def collect(self) -> dict[str, int]:
        stats = {
            "seeded": 0,
            "looked_up": 0,
            "lifecycle_changes": 0,
            "signals_inserted": 0,
            "providers": len(self.providers),
        }
        with db_session(config.DB_PATH) as conn:
            seeds = self._gather_seeds(conn)
            if self.max_parts is not None:
                seeds = seeds[: self.max_parts]
            stats["seeded"] = len(seeds)
            change_records: list[SignalRecord] = []

            for vendor, mpn in seeds:
                result = self._enrich(vendor, mpn)
                if result is None:
                    continue
                stats["looked_up"] += 1
                signal = self._upsert_part(conn, vendor, mpn, result)
                if signal:
                    change_records.append(signal)
                    stats["lifecycle_changes"] += 1

            if change_records:
                stats["signals_inserted"] = upsert_signals(conn, change_records)
            set_collector_state(conn, SOURCE, watermark=_now())

        logger.info(
            "Part master done seeded=%d looked_up=%d changes=%d",
            stats["seeded"],
            stats["looked_up"],
            stats["lifecycle_changes"],
        )
        return stats

    def _gather_seeds(self, conn: Any) -> list[tuple[str, str]]:
        combined: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add_all(items: list[tuple[str, str]]) -> None:
            for vendor, mpn in items:
                key = f"{vendor}:{mpn}"
                if key in seen:
                    continue
                seen.add(key)
                combined.append((vendor, mpn))

        add_all(seed_mpns_from_file(self.seed_file))
        add_all(seed_mpns_from_signals(conn))
        if not self.skip_sitemap:
            try:
                add_all(seed_mpns_from_samsung_sitemap(self.client))
            except Exception:
                logger.exception("Samsung sitemap seed failed")
        return combined

    def _enrich(self, vendor: str, mpn: str) -> Optional[dict[str, Any]]:
        for provider in self.providers:
            try:
                result = provider.lookup(vendor, mpn)
            except Exception:
                logger.exception("provider %s failed for %s", provider.name, mpn)
                continue
            if result is not None:
                result = dict(result)
                result["lifecycle_source"] = provider.name
                return result
        return None

    def _upsert_part(
        self,
        conn: Any,
        vendor: str,
        mpn: str,
        result: dict[str, Any],
    ) -> Optional[SignalRecord]:
        now = _now()
        decoded = decode(mpn) or {}
        lifecycle = result.get("lifecycle_status") or "unknown"
        lifecycle_source = result.get("lifecycle_source") or "unknown"
        sources = {
            "enrichment": result.get("raw") or {},
            "decoded": decoded,
        }
        prev = conn.execute(
            "SELECT lifecycle_status FROM parts WHERE vendor = ? AND mpn = ?",
            (vendor, mpn),
        ).fetchone()
        previous = prev["lifecycle_status"] if prev else None

        if prev:
            conn.execute(
                """
                UPDATE parts SET
                    decoded_json = ?,
                    lifecycle_status = ?,
                    lifecycle_source = ?,
                    last_seen_at = ?,
                    sources_json = ?
                WHERE vendor = ? AND mpn = ?
                """,
                (
                    json.dumps(decoded, sort_keys=True),
                    lifecycle,
                    lifecycle_source,
                    now,
                    json.dumps(sources, sort_keys=True),
                    vendor,
                    mpn,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO parts (
                    vendor, mpn, decoded_json, lifecycle_status, lifecycle_source,
                    first_seen_at, last_seen_at, sources_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vendor,
                    mpn,
                    json.dumps(decoded, sort_keys=True),
                    lifecycle,
                    lifecycle_source,
                    now,
                    now,
                    json.dumps(sources, sort_keys=True),
                ),
            )

        changed = previous is not None and previous != lifecycle
        first_notable = previous is None and lifecycle.upper() in {
            "EOL",
            "OBSOLETE",
            "NRND",
        }
        if not (changed or first_notable):
            return None

        if changed:
            summary = f"{vendor}:{mpn} lifecycle {previous} -> {lifecycle}"
            keyword = "lifecycle_change"
        else:
            summary = f"{vendor}:{mpn} first seen as {lifecycle}"
            keyword = "first_eol"

        return SignalRecord(
            source=LIFECYCLE_SIGNAL_SOURCE,
            timestamp=now,
            company_name=vendor.replace("_", " ").title(),
            raw_text=json.dumps(
                {
                    "summary": summary,
                    "old_status": previous,
                    "new_status": lifecycle,
                    "lifecycle_source": lifecycle_source,
                    "decoded": decoded,
                },
                sort_keys=True,
            ),
            url=f"part://{vendor}/{mpn}",
            external_id=f"{vendor}:{mpn}:{lifecycle}",
            keyword=keyword,
        )
