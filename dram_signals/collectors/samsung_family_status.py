"""Step 1: Samsung Semiconductor family-page status watcher.

Family pages expose productStatus / productModelName meta tags. Part URLs
redirect to family shells and must not be scraped for part-level specs.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from dram_signals import config
from dram_signals.db import SignalRecord, db_session, set_collector_state, upsert_signals
from dram_signals.http_client import HttpClient
from dram_signals.robots_util import allowed as robots_allowed

logger = logging.getLogger(__name__)

SOURCE = "samsung_family_status"
COMPANY = "Samsung Electronics"
BASE = "https://semiconductor.samsung.com"
SITEMAPS = (
    f"{BASE}/sitemap.xml",
    f"{BASE}/top_sitemap.xml",
)

# Family pages: /dram/<cat>/<family>/  or /mcp/<cat>/<family>/
FAMILY_PATH_RE = re.compile(r"^/(dram|mcp)/[^/]+/[^/]+/$")
# Part-like: one extra slug — used only to derive parent family URLs
PART_PATH_RE = re.compile(r"^/(dram|mcp)/[^/]+/[^/]+/[a-z0-9-]+/$")

META_NAME_CONTENT = re.compile(
    r'<meta[^>]+name=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.I,
)
META_CONTENT_NAME = re.compile(
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']([^"\']+)["\']',
    re.I,
)
DISCONTINUED_BANNER = "This product has been discontinued"

# Polite rate for Samsung HTML (overrides default SEC-oriented delay).
SAMSUNG_DELAY_SECONDS = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_ns(text: str) -> str:
    return re.sub(r'\sxmlns="[^"]+"', "", text, count=1)


def parse_sitemap_locs(xml_text: str) -> list[str]:
    root = ET.fromstring(_strip_ns(xml_text))
    locs: list[str] = []
    tag = root.tag.lower()
    if tag.endswith("sitemapindex"):
        for sm in root.findall("sitemap"):
            loc = sm.findtext("loc")
            if loc:
                locs.append(loc.strip())
    else:
        for url_el in root.findall("url"):
            loc = url_el.findtext("loc")
            if loc:
                locs.append(loc.strip())
    return locs


def family_urls_from_locs(locs: list[str]) -> list[str]:
    """Build the family URL set from sitemap locs (direct + derived from parts)."""
    families: set[str] = set()
    for loc in locs:
        if not loc.startswith(BASE):
            continue
        path = urlparse(loc).path
        if FAMILY_PATH_RE.match(path):
            families.add(f"{BASE}{path}")
        elif PART_PATH_RE.match(path):
            # Drop last segment → family URL
            parent = path.rstrip("/").rsplit("/", 1)[0] + "/"
            if FAMILY_PATH_RE.match(parent):
                families.add(f"{BASE}{parent}")
    return sorted(families)


def extract_product_meta(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, content in META_NAME_CONTENT.findall(html):
        found[name] = content
    for content, name in META_CONTENT_NAME.findall(html):
        found.setdefault(name, content)
    keys = (
        "productStatus",
        "productModelName",
        "productCategory",
        "productSubCategory",
        "datePublished",
        "consumerProductYN",
    )
    return {k: found[k] for k in keys if k in found}


def normalize_status(raw: str | None, *, discontinued_banner: bool) -> str:
    text = (raw or "").strip()
    if not text and discontinued_banner:
        return "EOL"
    if not text:
        return "unknown"
    upper = text.upper()
    aliases = {
        "ACTIVE": "Active",
        "EOL": "EOL",
        "END OF LIFE": "EOL",
        "END-OF-LIFE": "EOL",
        "NRND": "NRND",
        "DISCONTINUED": "EOL",
        "OBSOLETE": "Obsolete",
    }
    return aliases.get(upper, text)


class SamsungFamilyStatusCollector:
    def __init__(
        self,
        client: Optional[HttpClient] = None,
        *,
        max_families: Optional[int] = None,
    ) -> None:
        self.client = client or HttpClient(
            delay_seconds=SAMSUNG_DELAY_SECONDS,
            timeout=30.0,
        )
        self.max_families = max_families

    def collect(self) -> dict[str, int]:
        stats = {
            "families_listed": 0,
            "families_checked": 0,
            "status_changes": 0,
            "signals_inserted": 0,
            "skipped_robots": 0,
            "errors": 0,
        }
        if not robots_allowed(f"{BASE}/dram/"):
            logger.error("robots.txt disallows /dram/ — aborting Samsung family watcher")
            return stats

        locs = self._load_sitemap_locs()
        families = family_urls_from_locs(locs)
        if self.max_families is not None:
            families = families[: self.max_families]
        stats["families_listed"] = len(families)
        logger.info("Samsung family URLs to check: %d", len(families))

        change_records: list[SignalRecord] = []
        with db_session(config.DB_PATH) as conn:
            for url in families:
                if not robots_allowed(url):
                    stats["skipped_robots"] += 1
                    continue
                try:
                    record = self._check_family(conn, url)
                except Exception:
                    logger.exception("Samsung family check failed url=%s", url)
                    stats["errors"] += 1
                    continue
                stats["families_checked"] += 1
                if record:
                    change_records.append(record)
                    stats["status_changes"] += 1

            if change_records:
                stats["signals_inserted"] = upsert_signals(conn, change_records)
            set_collector_state(
                conn,
                SOURCE,
                watermark=_now(),
            )

        logger.info(
            "Samsung family status done listed=%d checked=%d changes=%d errors=%d",
            stats["families_listed"],
            stats["families_checked"],
            stats["status_changes"],
            stats["errors"],
        )
        return stats

    def _load_sitemap_locs(self) -> list[str]:
        locs: list[str] = []
        for sm_url in SITEMAPS:
            if not robots_allowed(sm_url):
                logger.warning("robots disallows sitemap %s", sm_url)
                continue
            try:
                text = self.client.get_text(sm_url)
            except Exception:
                logger.exception("Failed to fetch sitemap %s", sm_url)
                continue
            child_locs = parse_sitemap_locs(text)
            # If this entry is a sitemap index, fetch children once.
            if child_locs and child_locs[0].endswith(".xml") and "sitemap" in child_locs[0].lower():
                for child in child_locs:
                    if not robots_allowed(child):
                        continue
                    try:
                        locs.extend(parse_sitemap_locs(self.client.get_text(child)))
                    except Exception:
                        logger.exception("Failed child sitemap %s", child)
            else:
                locs.extend(child_locs)
        return locs

    def _check_family(self, conn: Any, url: str) -> Optional[SignalRecord]:
        html = self.client.get_text(url)
        meta = extract_product_meta(html)
        banner = DISCONTINUED_BANNER in html
        status = normalize_status(meta.get("productStatus"), discontinued_banner=banner)
        model = meta.get("productModelName") or urlparse(url).path.rstrip("/").split("/")[-1].upper()
        checked_at = _now()

        prev = conn.execute(
            "SELECT status, model_name FROM samsung_family_state WHERE family_url = ?",
            (url,),
        ).fetchone()
        previous_status = prev["status"] if prev else None

        conn.execute(
            """
            INSERT INTO samsung_family_state (
                family_url, status, model_name, category, subcategory,
                date_published, discontinued_banner, checked_at, previous_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(family_url) DO UPDATE SET
                status = excluded.status,
                model_name = excluded.model_name,
                category = excluded.category,
                subcategory = excluded.subcategory,
                date_published = excluded.date_published,
                discontinued_banner = excluded.discontinued_banner,
                checked_at = excluded.checked_at,
                previous_status = samsung_family_state.status
            """,
            (
                url,
                status,
                model,
                meta.get("productCategory"),
                meta.get("productSubCategory"),
                meta.get("datePublished"),
                1 if banner else 0,
                checked_at,
                previous_status,
            ),
        )

        # Emit a signal on first sighting of EOL/Obsolete, or any status change.
        first_seen = previous_status is None
        changed = previous_status is not None and previous_status != status
        notable_first = first_seen and status.upper() in {"EOL", "OBSOLETE", "NRND"}
        if not (changed or notable_first):
            return None

        if changed:
            summary = f"{model} changed {previous_status} -> {status}"
            keyword = "status_change"
        else:
            summary = f"{model} first seen as {status}"
            keyword = "first_eol"

        import json

        raw = json.dumps(
            {
                "summary": summary,
                "meta": meta,
                "discontinued_banner": banner,
                "old_status": previous_status,
                "new_status": status,
                "family_url": url,
            },
            sort_keys=True,
        )
        date_token = (meta.get("datePublished") or checked_at)[:10]
        return SignalRecord(
            source=SOURCE,
            timestamp=checked_at,
            company_name=COMPANY,
            raw_text=raw,
            url=url,
            external_id=f"{url}|{status}|{date_token}",
            keyword=keyword,
        )
