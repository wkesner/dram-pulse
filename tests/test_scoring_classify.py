"""Classification + entity resolution smoke tests."""

from __future__ import annotations

from dram_signals.db import connect, init_db
from dram_signals.scoring.classify import classify_raw_signal, infer_family
from dram_signals.scoring.entity import resolve_or_create, buyer_type_from_revenue
from dram_signals.scoring.seed import seed_reference_tables, get_regime, end_build_match_strength


def test_infer_family() -> None:
    assert infer_family("DDR4 inventory write-down") == "DDR4"
    assert infer_family("HBM3E shortage") == "HBM"


def test_classify_edgar_writedown() -> None:
    row = {
        "source": "edgar_8k",
        "company_name": "Acme EMS Inc",
        "raw_text": "We recorded an inventory write-down related to excess DRAM components.",
        "keyword": "inventory write-down",
        "timestamp": "2026-10-01T00:00:00+00:00",
        "url": "https://www.sec.gov/Archives/example",
    }
    found = classify_raw_signal(row, company_key="acme-ems")
    types = {c.signal_type for c in found}
    assert "S1" in types
    assert all(c.side == "supply" for c in found if c.signal_type == "S1")


def test_classify_job_allocation() -> None:
    row = {
        "source": "greenhouse",
        "company_name": "Small OEM",
        "raw_text": "Memory buyer responsible for DRAM allocation and shortage recovery.",
        "keyword": "buyer",
        "timestamp": "2026-10-10T00:00:00+00:00",
        "url": "https://boards.greenhouse.io/example/jobs/1",
    }
    found = classify_raw_signal(row, company_key="small-oem")
    assert any(c.signal_type == "D3" and c.side == "demand" for c in found)


def test_buyer_type_bands() -> None:
    assert buyer_type_from_revenue(1_200_000_000) == "mid_oem_ems"
    assert buyer_type_from_revenue(80_000_000) == "small"
    assert buyer_type_from_revenue(6_000_000_000) == "very_large_oem"


def test_seed_and_end_build_match(tmp_path) -> None:
    db = tmp_path / "s.db"
    conn = connect(db)
    init_db(conn)
    seed_reference_tables(conn)
    assert get_regime(conn, "DDR4") == "shortage"
    assert get_regime(conn, "HBM") == "shortage"
    assert end_build_match_strength(conn, "DDR4", ["industrial"]) == "core"
    assert end_build_match_strength(conn, "DDR4", ["pcs"]) == "none"
    key = resolve_or_create(
        conn,
        display_name="Test EMS",
        revenue_usd=1_200_000_000,
        end_builds=["industrial"],
        families_traded=["DDR4"],
    )
    assert key
    conn.close()
