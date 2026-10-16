"""Micron obsolete part-catalog collector (public AEM JSON).

Fetches obsolete DRAM catalogs and upserts part families / variants with
lifecycle_status=obsolete. Temperature ranges are mapped to consumer /
industrial / automotive grades so packaging-grade twins stay distinct for
PPV and shortage analysis.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from dram_signals.catalog.service import (
    link_pcn_to_variant,
    register_pcn,
    upsert_variant_from_attrs,
)
from dram_signals.db import db_session, set_collector_state
from dram_signals import config
from dram_signals.http_client import HttpClient

logger = logging.getLogger(__name__)

SOURCE = "micron_obsolete"
MANUFACTURER = "micron"

# Catalog slug → human label. Paths match Micron's obsolete index.
DEFAULT_CATALOGS: tuple[tuple[str, str], ...] = (
    ("obsolete-ddr4-sdram", "DDR4 SDRAM"),
    ("obsolete-ddr3-sdram", "DDR3 SDRAM"),
    ("obsolete-lpddr4", "LPDDR4"),
    ("obsolete-lpddr5", "LPDDR3/LPDDR5 catalog bucket"),
    ("obsolete-gddr6", "GDDR6"),
)

JSON_TMPL = (
    "https://www.micron.com/products/obsolete/{slug}/part-catalog/"
    "_jcr_content.products.json/getpartcatalog/obsolete/{slug}/-/en_US"
)
PAGE_TMPL = "https://www.micron.com/products/obsolete/{slug}/part-catalog"


class MicronObsoleteCollector:
    def __init__(
        self,
        client: Optional[HttpClient] = None,
        catalogs: tuple[tuple[str, str], ...] = DEFAULT_CATALOGS,
    ) -> None:
        self.client = client or HttpClient()
        self.catalogs = catalogs

    def collect(self) -> dict[str, int]:
        stats = {"catalogs": 0, "parts": 0, "variants_upserted": 0}
        with db_session(config.DB_PATH) as conn:
            for slug, label in self.catalogs:
                try:
                    n = self._ingest_catalog(conn, slug, label)
                except Exception:
                    logger.exception("Micron obsolete catalog failed slug=%s", slug)
                    continue
                stats["catalogs"] += 1
                stats["parts"] += n
                stats["variants_upserted"] += n
            set_collector_state(
                conn,
                SOURCE,
                watermark=f"catalogs={stats['catalogs']};parts={stats['parts']}",
            )
        logger.info(
            "Micron obsolete ingest complete catalogs=%d parts=%d",
            stats["catalogs"],
            stats["parts"],
        )
        return stats

    def _ingest_catalog(self, conn: Any, slug: str, label: str) -> int:
        url = JSON_TMPL.format(slug=slug)
        page_url = PAGE_TMPL.format(slug=slug)
        payload = self.client.get_json(url)
        details = payload.get("details") or []
        logger.info("Micron obsolete slug=%s parts=%d", slug, len(details))

        notice_id = f"OBSOLETE-CATALOG-{slug.upper()}"
        pcn_id = register_pcn(
            conn,
            manufacturer=MANUFACTURER,
            notice_id=notice_id,
            notice_type="obsolete_catalog",
            title=f"Micron obsolete part catalog: {label}",
            url=page_url,
            raw_text=(
                f"Ingested {len(details)} obsolete part numbers from Micron "
                f"public catalog '{label}' ({slug})."
            ),
            source=SOURCE,
            external_id=slug,
        )
        # Refresh affected-part links for this notice.
        conn.execute("DELETE FROM pcn_affected_parts WHERE pcn_id = ?", (pcn_id,))

        count = 0
        for item in details:
            mpn = (item.get("part-number") or "").strip()
            if not mpn:
                continue
            attrs = {
                a.get("id"): a.get("value")
                for a in (item.get("attr") or [])
                if a.get("id")
            }
            variant_id = upsert_variant_from_attrs(
                conn,
                manufacturer=MANUFACTURER,
                mpn=mpn,
                attrs=attrs,
                source=SOURCE,
                source_url=page_url,
                lifecycle_status="obsolete",
            )
            family_row = conn.execute(
                "SELECT family_id FROM part_variants WHERE id = ?",
                (variant_id,),
            ).fetchone()
            link_pcn_to_variant(
                conn,
                pcn_id=pcn_id,
                variant_id=variant_id,
                family_id=int(family_row["family_id"]) if family_row else None,
                mpn=mpn.upper(),
                impact_notes="Listed on Micron obsolete part catalog",
            )
            count += 1
        return count
