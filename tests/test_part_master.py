"""Tests for part-master seeding + mock enrichment (no live APIs)."""

from __future__ import annotations

import json

from dram_signals.collectors.part_master import (
    LIFECYCLE_SIGNAL_SOURCE,
    MockLifecycleProvider,
    PartMasterCollector,
)
from dram_signals.db import connect, count_signals, init_db
from dram_signals.partnums import decode


def test_mock_provider_eol_family() -> None:
    mock = MockLifecycleProvider()
    r = mock.lookup("samsung", "K4UBE3D4AA-MGCL")
    assert r is not None
    assert r["lifecycle_status"] == "EOL"  # LPDDR4X


def test_mock_provider_active_family() -> None:
    mock = MockLifecycleProvider()
    r = mock.lookup("samsung", "K4VAF325ZC-SC28")
    assert r is not None
    assert r["lifecycle_status"] == "Active"  # GDDR7


def test_part_master_upsert_and_signal(tmp_path, monkeypatch) -> None:
    import dram_signals.config as cfg

    db = tmp_path / "p.db"
    monkeypatch.setattr(cfg, "DB_PATH", db)
    seed = tmp_path / "seed.txt"
    seed.write_text(
        "samsung,K4UBE3D4AA-MGCL\nsamsung,K4VAF325ZC-SC28\n",
        encoding="utf-8",
    )

    collector = PartMasterCollector(
        providers=[MockLifecycleProvider()],
        seed_file=seed,
        skip_sitemap=True,
    )
    stats = collector.collect()
    assert stats["seeded"] == 2
    assert stats["looked_up"] == 2
    # EOL first sighting emits; Active first sighting does not
    assert stats["lifecycle_changes"] == 1

    conn = connect(db)
    init_db(conn)
    parts = conn.execute(
        "SELECT vendor, mpn, lifecycle_status FROM parts ORDER BY mpn"
    ).fetchall()
    assert len(parts) == 2
    eol = [p for p in parts if p["mpn"].startswith("K4U")][0]
    assert eol["lifecycle_status"] == "EOL"
    decoded = decode(eol["mpn"])
    assert decoded and decoded["family"] == "LPDDR4X"

    assert count_signals(conn, LIFECYCLE_SIGNAL_SOURCE) == 1
    sig = conn.execute(
        "SELECT raw_text FROM signals WHERE source = ?",
        (LIFECYCLE_SIGNAL_SOURCE,),
    ).fetchone()
    payload = json.loads(sig["raw_text"])
    assert payload["new_status"] == "EOL"

    # Flip Active → EOL on second run via a custom provider
    class FlipProvider:
        name = "flip"

        def lookup(self, vendor: str, mpn: str):
            return {"lifecycle_status": "EOL", "raw": {"provider": "flip"}}

    collector2 = PartMasterCollector(
        providers=[FlipProvider()],
        seed_file=seed,
        skip_sitemap=True,
    )
    stats2 = collector2.collect()
    assert stats2["lifecycle_changes"] >= 1
    assert count_signals(conn, LIFECYCLE_SIGNAL_SOURCE) >= 2
    conn.close()
