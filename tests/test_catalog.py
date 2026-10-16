"""Tests for parts catalog families, grades, and variants."""

from __future__ import annotations

from dram_signals.catalog.grades import derive_family_code, infer_grade
from dram_signals.catalog.service import (
    get_family,
    list_variants,
    register_family,
    register_variant,
    upsert_variant_from_attrs,
)
from dram_signals.db import connect, init_db


def test_infer_grade_from_temperature() -> None:
    assert infer_grade("0C to +95C") == "consumer"
    assert infer_grade("-40C to +95C") == "industrial"
    assert infer_grade("-40C to +125C") == "automotive"
    assert infer_grade("-40C to +105C") == "automotive"
    assert infer_grade("-55C to +125C") == "military"
    assert infer_grade("-40C to +95C", hints="MT40A1G8SA-062E AAT:E") == "automotive"
    assert infer_grade(None) == "unknown"


def test_derive_family_code() -> None:
    assert derive_family_code("MT40A4G4VA-062E:B") == "MT40A4G4VA"
    assert derive_family_code("K4A8G165WC-BCTD") == "K4A8G165WC"


def test_register_family_and_grade_variants(tmp_path) -> None:
    db = tmp_path / "c.db"
    conn = connect(db)
    init_db(conn)

    register_family(
        conn,
        manufacturer="micron",
        family_code="MT40A1G8SA",
        technology="DDR4",
        density="8Gb",
        organization="x8",
        description="seed family",
    )
    register_variant(
        conn,
        manufacturer="micron",
        family_code="MT40A1G8SA",
        mpn="MT40A1G8SA-062E:B",
        grade="consumer",
        package="TFBGA",
        temperature_range="0C to +95C",
        lifecycle_status="active",
    )
    register_variant(
        conn,
        manufacturer="micron",
        family_code="MT40A1G8SA",
        mpn="MT40A1G8SA-062E IT:B",
        temperature_range="-40C to +95C",
        package="TFBGA",
        lifecycle_status="active",
    )

    family = get_family(conn, "MT40A1G8SA")
    assert family is not None
    assert family["manufacturer_code"] == "micron"

    variants = list_variants(conn, family_code="MT40A1G8SA")
    grades = {v["grade"] for v in variants}
    assert grades == {"consumer", "industrial"}
    industrial = [v for v in variants if v["grade"] == "industrial"][0]
    assert industrial["package"] == "TFBGA"
    conn.close()


def test_upsert_from_micron_attrs(tmp_path) -> None:
    db = tmp_path / "c.db"
    conn = connect(db)
    init_db(conn)
    upsert_variant_from_attrs(
        conn,
        manufacturer="micron",
        mpn="MT40A4G4VA-062E:B",
        attrs={
            "temperature": "-40C to +95C",
            "package_type": "TFBGA",
            "pin_count": "78-ball",
            "functional_technology": "DDR4",
            "usable_density": "16Gb",
            "data_width": "x4",
            "transfers_per_second": "3200MTPS",
            "production_status": "Obsolete",
            "tr_qty": "3000",
        },
        source="micron_obsolete",
        lifecycle_status="obsolete",
    )
    variants = list_variants(conn, family_code="MT40A4G4VA")
    assert len(variants) == 1
    assert variants[0]["grade"] == "industrial"
    assert variants[0]["lifecycle_status"] == "obsolete"
    assert variants[0]["order_packaging"] and "tape_reel" in variants[0]["order_packaging"]
    conn.close()
