"""Export SQLite signals → Airtable Companies + Signals (upsert)."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from dram_signals import config
from dram_signals.db import (
    db_session,
    get_export_watermark,
    set_export_watermark,
)
from dram_signals.exporters.airtable_client import AirtableClient, AirtableError
from dram_signals.exporters.company_keys import company_key_and_role, signal_key

logger = logging.getLogger(__name__)

DESTINATION = "airtable"
EXCERPT_MAX = 10_000
DETAILS_MAX = 10_000

# Field / table names — rename in Airtable = one-line change here.
TABLES = {
    "companies": "Companies",
    "signals": "Signals",
}

COMPANY_FIELDS = {
    "key": "Company Key",
    "name": "Name",
    "cik": "CIK",
    "ticker": "Ticker",
    "sic": "SIC",
    "location": "Location",
    "role": "Role",
}

SIGNAL_FIELDS = {
    "key": "Signal Key",
    "id": "Signal ID",
    "source": "Source",
    "date": "Signal Date",
    "company": "Company",
    "company_name_raw": "Company Name (raw)",
    "matched_terms": "Matched Terms",
    "form": "Form / Doc Type / 8-K Items",
    "excerpt": "Excerpt",
    "details": "Details",
    "url": "URL",
    "first_seen": "First Seen",
    "last_changed": "Last Changed",
    "internal_only": "Internal Only",
}

# Human / Clay owned — never sent by exporter
NEVER_SEND_SIGNAL = {"Review", "Side", "Product Family", "Signal Type", "Deadline", "Same Event As"}
NEVER_SEND_COMPANY = {
    "Enrich?",
    "Domain",
    "LinkedIn URL",
    "Industry",
    "Employees",
    "HQ Country",
    "Revenue Band",
    "Enriched At",
    "Clay Status",
    "End Builds",
    "Buyer Type",
    "Gross Margin %",
    "Competitor",
    "Do Not Contact",
    "Screened",
}


def _parse_metadata(row: Any) -> dict[str, Any]:
    raw = row["metadata_json"] if "metadata_json" in row.keys() else None
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    # Lifecycle collectors store JSON in raw_text
    text = row["raw_text"] or ""
    if text.lstrip().startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
    return {}


def _matched_terms(row: Any) -> list[str]:
    raw = row["matched_terms"] if "matched_terms" in row.keys() else None
    if raw:
        try:
            terms = json.loads(raw)
            if isinstance(terms, list):
                return [str(t).strip().strip('"') for t in terms if str(t).strip()]
        except json.JSONDecodeError:
            pass
    kw = row["keyword"] or ""
    if not kw:
        return []
    return [p.strip().strip('"') for p in re.split(r"[|,;/]+", kw) if p.strip()]


def _excerpt(row: Any, meta: dict[str, Any]) -> str:
    if meta.get("summary"):
        text = str(meta["summary"])
    else:
        text = row["raw_text"] or ""
        if text.lstrip().startswith("{"):
            # Prefer a short JSON summary line
            text = json.dumps({k: meta[k] for k in list(meta)[:8]}, sort_keys=True) if meta else text[:600]
        else:
            text = text[:600]
    return text[:EXCERPT_MAX]


def _details(meta: dict[str, Any], source: str) -> str:
    keys_prefer = (
        "old_status",
        "new_status",
        "family_url",
        "product_family",
        "mpn",
        "lifecycle_source",
        "form",
        "items",
        "file_type",
        "summary",
        "discontinued_banner",
    )
    lines: list[str] = []
    seen: set[str] = set()
    for k in keys_prefer:
        if k in meta and meta[k] is not None:
            lines.append(f"{k}: {meta[k]}")
            seen.add(k)
    # Nested meta from Samsung
    nested = meta.get("meta")
    if isinstance(nested, dict):
        for k, v in nested.items():
            lines.append(f"{k}: {v}")
    for k, v in meta.items():
        if k in seen or k in {"meta", "decoded", "raw"} or isinstance(v, (dict, list)):
            continue
        if len(lines) >= 20:
            break
        lines.append(f"{k}: {v}")
    return "\n".join(lines)[:DETAILS_MAX]


def _form_doc(meta: dict[str, Any], source: str) -> str:
    bits = []
    for k in ("form", "file_type", "items"):
        if meta.get(k):
            bits.append(f"{k}={meta[k]}")
    if source == "edgar_8k" and not bits:
        bits.append("form=8-K")
    return "; ".join(bits)


def _date_only(ts: str) -> Optional[str]:
    if not ts:
        return None
    return ts[:10]


def _is_internal(source: str) -> bool:
    return source.startswith("dramx_")


def build_company_fields(row: Any, meta: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    key, role = company_key_and_role(
        company_name=row["company_name"],
        source=row["source"],
        url=row["url"] or "",
        metadata=meta,
    )
    from dram_signals.exporters.company_keys import extract_cik

    cik = extract_cik(row["url"] or "", meta)
    fields = {
        COMPANY_FIELDS["key"]: key,
        COMPANY_FIELDS["name"]: row["company_name"],
    }
    # Role: set for manufacturers (must stay manufacturer). Omit for prospects so
    # a human-edited Role on an existing company row is not overwritten (brief 3.3).
    if role == "manufacturer":
        fields[COMPANY_FIELDS["role"]] = role
    if cik:
        fields[COMPANY_FIELDS["cik"]] = cik
    if meta.get("ticker"):
        fields[COMPANY_FIELDS["ticker"]] = meta["ticker"]
    if meta.get("sic"):
        fields[COMPANY_FIELDS["sic"]] = str(meta["sic"])
    if meta.get("location"):
        fields[COMPANY_FIELDS["location"]] = meta["location"]
    return key, role, fields


def build_signal_fields(
    row: Any,
    *,
    company_record_id: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    sk = signal_key(row["source"], row["external_id"])
    fields: dict[str, Any] = {
        SIGNAL_FIELDS["key"]: sk,
        SIGNAL_FIELDS["id"]: row["id"],
        SIGNAL_FIELDS["source"]: row["source"],
        SIGNAL_FIELDS["date"]: _date_only(row["timestamp"]),
        SIGNAL_FIELDS["company"]: [company_record_id],
        SIGNAL_FIELDS["company_name_raw"]: row["company_name"],
        SIGNAL_FIELDS["matched_terms"]: _matched_terms(row),
        SIGNAL_FIELDS["form"]: _form_doc(meta, row["source"]),
        SIGNAL_FIELDS["excerpt"]: _excerpt(row, meta),
        SIGNAL_FIELDS["details"]: _details(meta, row["source"]),
        SIGNAL_FIELDS["url"]: row["url"] or None,
        SIGNAL_FIELDS["first_seen"]: row["first_seen_at"] or row["collected_at"],
        SIGNAL_FIELDS["last_changed"]: row["changed_at"] or row["collected_at"],
        SIGNAL_FIELDS["internal_only"]: _is_internal(row["source"]),
    }
    # Drop empty strings that would wipe
    return {k: v for k, v in fields.items() if v is not None and v != ""}


def load_changed_rows(
    conn: Any,
    *,
    watermark: Optional[str],
    limit: Optional[int] = None,
    all_rows: bool = False,
) -> list[Any]:
    if all_rows:
        sql = "SELECT * FROM signals ORDER BY changed_at ASC, id ASC"
        params: tuple[Any, ...] = ()
    elif watermark:
        sql = (
            "SELECT * FROM signals WHERE changed_at > ? "
            "ORDER BY changed_at ASC, id ASC"
        )
        params = (watermark,)
    else:
        sql = "SELECT * FROM signals ORDER BY changed_at ASC, id ASC"
        params = ()
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(sql, params).fetchall())


def export_to_airtable(
    *,
    token: Optional[str] = None,
    base_id: Optional[str] = None,
    dry_run: bool = False,
    all_rows: bool = False,
    limit: Optional[int] = None,
    client: Optional[AirtableClient] = None,
    db_path: Optional[Any] = None,
) -> dict[str, Any]:
    token = token or os.environ.get("AIRTABLE_TOKEN", "")
    base_id = base_id or os.environ.get("AIRTABLE_BASE_ID", "")
    path = db_path or config.DB_PATH

    stats: dict[str, Any] = {
        "companies_upserted": 0,
        "signals_created": 0,
        "signals_updated": 0,
        "api_calls": 0,
        "exported": 0,
        "dry_run": dry_run,
    }

    with db_session(path) as conn:
        watermark = None if all_rows else get_export_watermark(conn, DESTINATION)
        rows = load_changed_rows(conn, watermark=watermark, limit=limit, all_rows=all_rows)
        stats["exported"] = len(rows)
        if not rows:
            print("exported 0")
            print(
                f"companies_upserted=0 signals_created=0 signals_updated=0 api_calls=0"
            )
            return stats

        # Group companies
        company_payloads: dict[str, dict[str, Any]] = {}
        row_meta: list[tuple[Any, dict[str, Any], str]] = []
        for row in rows:
            meta = _parse_metadata(row)
            key, role, fields = build_company_fields(row, meta)
            # Prefer manufacturer role if any signal says so; never send Enrich?
            if key not in company_payloads or role == "manufacturer":
                company_payloads[key] = {"fields": fields}
            row_meta.append((row, meta, key))

        company_records = list(company_payloads.values())
        signal_records_preview: list[dict[str, Any]] = []

        if dry_run:
            # Build signal field previews with placeholder company ids
            for row, meta, key in row_meta:
                fields = build_signal_fields(row, company_record_id=f"recPLACEHOLDER:{key}", meta=meta)
                signal_records_preview.append({"fields": fields})
            payload = {
                "companies": company_records[:20],
                "signals": signal_records_preview[:20],
                "counts": {
                    "companies": len(company_records),
                    "signals": len(signal_records_preview),
                },
            }
            print(json.dumps(payload, indent=2, default=str))
            print(
                f"companies_upserted={len(company_records)} "
                f"signals_created=0 signals_updated=0 api_calls=0 (dry-run)"
            )
            stats["companies_upserted"] = len(company_records)
            return stats

        if not token or not base_id:
            raise SystemExit(
                "AIRTABLE_TOKEN and AIRTABLE_BASE_ID are required (unless --dry-run)"
            )

        client = client or AirtableClient(token, base_id)
        try:
            company_result = client.upsert(
                TABLES["companies"],
                company_records,
                merge_fields=[COMPANY_FIELDS["key"]],
            )
        except AirtableError as exc:
            logger.error("Company upsert failed: %s", exc)
            raise SystemExit(1) from exc

        # Map Company Key → record id from response
        key_to_id: dict[str, str] = {}
        for rec in company_result.get("records") or []:
            fields = rec.get("fields") or {}
            ck = fields.get(COMPANY_FIELDS["key"])
            rid = rec.get("id")
            if ck and rid:
                key_to_id[str(ck)] = str(rid)

        missing = [k for k in company_payloads if k not in key_to_id]
        if missing:
            logger.error("Upsert response missing company ids for: %s", missing[:10])
            raise SystemExit(1)

        signal_records = []
        signal_keys_order: list[str] = []
        for row, meta, key in row_meta:
            fields = build_signal_fields(row, company_record_id=key_to_id[key], meta=meta)
            signal_records.append({"fields": fields})
            signal_keys_order.append(fields[SIGNAL_FIELDS["key"]])

        try:
            signal_result = client.upsert(
                TABLES["signals"],
                signal_records,
                merge_fields=[SIGNAL_FIELDS["key"]],
            )
        except AirtableError as exc:
            logger.error("Signal upsert failed: %s", exc)
            # Watermark unchanged
            raise SystemExit(1) from exc

        created = len(signal_result.get("createdRecords") or [])
        updated = len(signal_result.get("updatedRecords") or [])
        stats["companies_upserted"] = len(company_payloads)
        stats["signals_created"] = created
        stats["signals_updated"] = updated
        stats["api_calls"] = client.api_calls

        # Advance watermark only after full success (and not --all unless we want to)
        max_changed = max(r["changed_at"] or r["collected_at"] for r in rows)
        if not all_rows:
            set_export_watermark(conn, DESTINATION, max_changed, stats=stats)

        print(
            f"companies_upserted={stats['companies_upserted']} "
            f"signals_created={stats['signals_created']} "
            f"signals_updated={stats['signals_updated']} "
            f"api_calls={stats['api_calls']}"
        )
        return stats
