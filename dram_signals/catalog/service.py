"""CRUD helpers for manufacturers, families, variants, and PCN linkage."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from dram_signals.catalog.grades import derive_family_code, infer_grade

KNOWN_MANUFACTURERS = {
    "micron": "Micron Technology",
    "samsung": "Samsung Semiconductor",
    "sk_hynix": "SK hynix",
    "nanya": "Nanya Technology",
    "cxmt": "CXMT",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_manufacturer(conn: sqlite3.Connection, name_or_code: str) -> int:
    raw = name_or_code.strip()
    code = raw.lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "skhynix": "sk_hynix",
        "sk-hynix": "sk_hynix",
        "hynix": "sk_hynix",
        "micron_technology": "micron",
        "samsung_semiconductor": "samsung",
    }
    code = aliases.get(code, code)
    name = KNOWN_MANUFACTURERS.get(code, raw)
    if code not in KNOWN_MANUFACTURERS and " " in raw:
        # Prefer a stable code from the provided display name.
        code = raw.lower().replace(" ", "_")

    row = conn.execute(
        "SELECT id FROM manufacturers WHERE code = ? OR name = ?",
        (code, name),
    ).fetchone()
    if row:
        return int(row["id"])

    cur = conn.execute(
        "INSERT INTO manufacturers (name, code) VALUES (?, ?)",
        (name, code),
    )
    return int(cur.lastrowid)


def register_family(
    conn: sqlite3.Connection,
    *,
    manufacturer: str,
    family_code: str,
    technology: Optional[str] = None,
    density: Optional[str] = None,
    organization: Optional[str] = None,
    description: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    mfr_id = ensure_manufacturer(conn, manufacturer)
    code = family_code.strip().upper()
    now = _now()
    existing = conn.execute(
        """
        SELECT id FROM part_families
        WHERE manufacturer_id = ? AND family_code = ?
        """,
        (mfr_id, code),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE part_families SET
                technology = COALESCE(?, technology),
                density = COALESCE(?, density),
                organization = COALESCE(?, organization),
                description = COALESCE(?, description),
                notes = COALESCE(?, notes),
                updated_at = ?
            WHERE id = ?
            """,
            (technology, density, organization, description, notes, now, existing["id"]),
        )
        return int(existing["id"])

    cur = conn.execute(
        """
        INSERT INTO part_families (
            manufacturer_id, family_code, technology, density, organization,
            description, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mfr_id,
            code,
            technology,
            density,
            organization,
            description,
            notes,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def register_variant(
    conn: sqlite3.Connection,
    *,
    manufacturer: str,
    family_code: str,
    mpn: str,
    grade: Optional[str] = None,
    package: Optional[str] = None,
    package_detail: Optional[str] = None,
    temperature_range: Optional[str] = None,
    speed_grade: Optional[str] = None,
    voltage: Optional[str] = None,
    lifecycle_status: str = "unknown",
    order_packaging: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
    source: Optional[str] = None,
    source_url: Optional[str] = None,
    technology: Optional[str] = None,
    density: Optional[str] = None,
    organization: Optional[str] = None,
    family_description: Optional[str] = None,
) -> int:
    family_id = register_family(
        conn,
        manufacturer=manufacturer,
        family_code=family_code,
        technology=technology,
        density=density,
        organization=organization,
        description=family_description,
    )
    resolved_grade = (grade or infer_grade(temperature_range) or "unknown").lower()
    if resolved_grade not in {
        "consumer",
        "industrial",
        "automotive",
        "military",
        "unknown",
    }:
        resolved_grade = "unknown"

    now = _now()
    mpn_norm = mpn.strip().upper()
    attrs_json = json.dumps(attributes, sort_keys=True) if attributes else None
    existing = conn.execute(
        "SELECT id FROM part_variants WHERE mpn = ?",
        (mpn_norm,),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE part_variants SET
                family_id = ?,
                package = COALESCE(?, package),
                package_detail = COALESCE(?, package_detail),
                grade = ?,
                temperature_range = COALESCE(?, temperature_range),
                speed_grade = COALESCE(?, speed_grade),
                voltage = COALESCE(?, voltage),
                lifecycle_status = ?,
                order_packaging = COALESCE(?, order_packaging),
                attributes_json = COALESCE(?, attributes_json),
                source = COALESCE(?, source),
                source_url = COALESCE(?, source_url),
                updated_at = ?
            WHERE id = ?
            """,
            (
                family_id,
                package,
                package_detail,
                resolved_grade,
                temperature_range,
                speed_grade,
                voltage,
                lifecycle_status,
                order_packaging,
                attrs_json,
                source,
                source_url,
                now,
                existing["id"],
            ),
        )
        return int(existing["id"])

    cur = conn.execute(
        """
        INSERT INTO part_variants (
            family_id, mpn, package, package_detail, grade, temperature_range,
            speed_grade, voltage, lifecycle_status, order_packaging,
            attributes_json, source, source_url, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            family_id,
            mpn_norm,
            package,
            package_detail,
            resolved_grade,
            temperature_range,
            speed_grade,
            voltage,
            lifecycle_status,
            order_packaging,
            attrs_json,
            source,
            source_url,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def upsert_variant_from_attrs(
    conn: sqlite3.Connection,
    *,
    manufacturer: str,
    mpn: str,
    attrs: dict[str, Any],
    source: str,
    source_url: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
) -> int:
    """Register/update a variant from a flat attribute map (Micron catalog style)."""
    family_code = derive_family_code(mpn)
    temp = attrs.get("temperature") or attrs.get("operating_temperature")
    grade = infer_grade(str(temp) if temp else None, hints=mpn)
    status = lifecycle_status or _normalize_lifecycle(attrs.get("production_status"))
    package = attrs.get("package_type") or attrs.get("package")
    package_detail = " / ".join(
        str(attrs[k])
        for k in ("pin_count", "package_dimension")
        if attrs.get(k)
    ) or None
    order_packaging = None
    if attrs.get("tr_qty") or attrs.get("dp_qty"):
        bits = []
        if attrs.get("tr_qty"):
            bits.append(f"tape_reel:{attrs['tr_qty']}")
        if attrs.get("dp_qty"):
            bits.append(f"dry_pack:{attrs['dp_qty']}")
        order_packaging = ", ".join(bits)

    return register_variant(
        conn,
        manufacturer=manufacturer,
        family_code=family_code,
        mpn=mpn,
        grade=grade,
        package=package,
        package_detail=package_detail,
        temperature_range=str(temp) if temp else None,
        speed_grade=attrs.get("transfers_per_second") or attrs.get("clock_speed"),
        voltage=attrs.get("io_voltage"),
        lifecycle_status=status,
        order_packaging=order_packaging,
        attributes=attrs,
        source=source,
        source_url=source_url,
        technology=attrs.get("functional_technology"),
        density=attrs.get("usable_density"),
        organization=attrs.get("data_width") or attrs.get("configuration"),
    )


def _normalize_lifecycle(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if "obsolete" in text:
        return "obsolete"
    if text in {"eol", "end of life", "end-of-life"}:
        return "eol"
    if "nrnd" in text or "not recommended" in text:
        return "nrnd"
    if text in {"active", "production", "volume production"}:
        return "active"
    return text.replace(" ", "_")


def register_pcn(
    conn: sqlite3.Connection,
    *,
    manufacturer: str,
    notice_id: str,
    notice_type: str,
    title: Optional[str] = None,
    published_at: Optional[str] = None,
    effective_at: Optional[str] = None,
    last_order_at: Optional[str] = None,
    last_ship_at: Optional[str] = None,
    url: Optional[str] = None,
    raw_text: Optional[str] = None,
    source: Optional[str] = None,
    external_id: Optional[str] = None,
) -> int:
    mfr_id = ensure_manufacturer(conn, manufacturer)
    now = _now()
    existing = conn.execute(
        """
        SELECT id FROM pcn_notices
        WHERE manufacturer_id = ? AND notice_id = ?
        """,
        (mfr_id, notice_id),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE pcn_notices SET
                notice_type = ?,
                title = COALESCE(?, title),
                published_at = COALESCE(?, published_at),
                effective_at = COALESCE(?, effective_at),
                last_order_at = COALESCE(?, last_order_at),
                last_ship_at = COALESCE(?, last_ship_at),
                url = COALESCE(?, url),
                raw_text = COALESCE(?, raw_text),
                source = COALESCE(?, source),
                external_id = COALESCE(?, external_id),
                collected_at = ?
            WHERE id = ?
            """,
            (
                notice_type,
                title,
                published_at,
                effective_at,
                last_order_at,
                last_ship_at,
                url,
                raw_text,
                source,
                external_id,
                now,
                existing["id"],
            ),
        )
        return int(existing["id"])

    cur = conn.execute(
        """
        INSERT INTO pcn_notices (
            manufacturer_id, notice_id, notice_type, title, published_at,
            effective_at, last_order_at, last_ship_at, url, raw_text,
            source, external_id, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mfr_id,
            notice_id,
            notice_type,
            title,
            published_at,
            effective_at,
            last_order_at,
            last_ship_at,
            url,
            raw_text,
            source,
            external_id,
            now,
        ),
    )
    return int(cur.lastrowid)


def link_pcn_to_variant(
    conn: sqlite3.Connection,
    *,
    pcn_id: int,
    variant_id: Optional[int] = None,
    family_id: Optional[int] = None,
    mpn: Optional[str] = None,
    mpn_pattern: Optional[str] = None,
    impact_notes: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO pcn_affected_parts (
            pcn_id, family_id, variant_id, mpn, mpn_pattern, impact_notes
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (pcn_id, family_id, variant_id, mpn, mpn_pattern, impact_notes),
    )


def get_family(conn: sqlite3.Connection, family_code: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.*, m.name AS manufacturer_name, m.code AS manufacturer_code
        FROM part_families f
        JOIN manufacturers m ON m.id = f.manufacturer_id
        WHERE upper(f.family_code) = upper(?)
        """,
        (family_code,),
    ).fetchone()


def list_families(
    conn: sqlite3.Connection,
    *,
    manufacturer: Optional[str] = None,
) -> list[sqlite3.Row]:
    if manufacturer:
        mfr_id = ensure_manufacturer(conn, manufacturer)
        return list(
            conn.execute(
                """
                SELECT f.*, m.name AS manufacturer_name,
                       (SELECT COUNT(*) FROM part_variants v WHERE v.family_id = f.id) AS variant_count
                FROM part_families f
                JOIN manufacturers m ON m.id = f.manufacturer_id
                WHERE f.manufacturer_id = ?
                ORDER BY f.family_code
                """,
                (mfr_id,),
            ).fetchall()
        )
    return list(
        conn.execute(
            """
            SELECT f.*, m.name AS manufacturer_name,
                   (SELECT COUNT(*) FROM part_variants v WHERE v.family_id = f.id) AS variant_count
            FROM part_families f
            JOIN manufacturers m ON m.id = f.manufacturer_id
            ORDER BY m.name, f.family_code
            """
        ).fetchall()
    )


def list_variants(
    conn: sqlite3.Connection,
    *,
    family_code: Optional[str] = None,
    grade: Optional[str] = None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if family_code:
        clauses.append("upper(f.family_code) = upper(?)")
        params.append(family_code)
    if grade:
        clauses.append("v.grade = ?")
        params.append(grade.lower())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return list(
        conn.execute(
            f"""
            SELECT v.*, f.family_code, f.technology, m.name AS manufacturer_name
            FROM part_variants v
            JOIN part_families f ON f.id = v.family_id
            JOIN manufacturers m ON m.id = f.manufacturer_id
            {where}
            ORDER BY f.family_code, v.grade, v.mpn
            """,
            params,
        ).fetchall()
    )
