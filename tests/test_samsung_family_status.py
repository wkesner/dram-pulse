"""Tests for Samsung family-status watcher (fixtures, no live network)."""

from __future__ import annotations

import json
from pathlib import Path

from dram_signals.collectors.samsung_family_status import (
    SOURCE,
    extract_product_meta,
    family_urls_from_locs,
    normalize_status,
)
from dram_signals.db import connect, count_signals, init_db

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_meta_active() -> None:
    html = (FIXTURES / "samsung_gddr7.html").read_text(encoding="utf-8")
    meta = extract_product_meta(html)
    assert meta["productStatus"] == "Active"
    assert meta["productModelName"] == "GDDR7"
    assert normalize_status(meta["productStatus"], discontinued_banner=False) == "Active"


def test_extract_meta_eol_banner() -> None:
    html = (FIXTURES / "samsung_lpddr4x.html").read_text(encoding="utf-8")
    meta = extract_product_meta(html)
    assert meta["productStatus"] == "EOL"
    assert "This product has been discontinued" in html
    assert normalize_status(meta["productStatus"], discontinued_banner=True) == "EOL"


def test_family_urls_from_locs() -> None:
    locs = [
        "https://semiconductor.samsung.com/dram/gddr/gddr7/",
        "https://semiconductor.samsung.com/dram/gddr/gddr7/k4vaf325zc-sc28/",
        "https://semiconductor.samsung.com/about-us/",
        "https://semiconductor.samsung.com/mcp/emcp/emcp/",
    ]
    families = family_urls_from_locs(locs)
    assert "https://semiconductor.samsung.com/dram/gddr/gddr7/" in families
    assert "https://semiconductor.samsung.com/mcp/emcp/emcp/" in families
    # Part URL itself is not listed — only parent family
    assert all("/k4vaf" not in u for u in families)


def test_status_change_emits_signal(tmp_path, monkeypatch) -> None:
    import dram_signals.config as cfg
    from dram_signals.collectors import samsung_family_status as mod

    db = tmp_path / "s.db"
    monkeypatch.setattr(cfg, "DB_PATH", db)

    html_by_url = {
        "https://semiconductor.samsung.com/dram/gddr/gddr7/": (
            FIXTURES / "samsung_gddr7.html"
        ).read_text(encoding="utf-8"),
        "https://semiconductor.samsung.com/dram/lpddr/lpddr4x/": (
            FIXTURES / "samsung_lpddr4x.html"
        ).read_text(encoding="utf-8"),
    }

    class FakeClient:
        delay_seconds = 0
        timeout = 5

        def get_text(self, url: str, *, retries: int = 3) -> str:
            if "sitemap" in url:
                return (
                    '<?xml version="1.0"?>'
                    "<urlset>"
                    "<url><loc>https://semiconductor.samsung.com/dram/gddr/gddr7/</loc></url>"
                    "<url><loc>https://semiconductor.samsung.com/dram/lpddr/lpddr4x/</loc></url>"
                    "</urlset>"
                )
            return html_by_url[url]

    monkeypatch.setattr(mod, "robots_allowed", lambda url: True)

    collector = mod.SamsungFamilyStatusCollector(client=FakeClient())  # type: ignore[arg-type]
    stats = collector.collect()
    assert stats["families_checked"] == 2
    # First run: EOL first-sighting emits; Active first-sighting does not
    assert stats["status_changes"] == 1

    conn = connect(db)
    init_db(conn)
    rows = conn.execute(
        "SELECT family_url, status FROM samsung_family_state ORDER BY family_url"
    ).fetchall()
    assert len(rows) == 2
    by_url = {r["family_url"]: r["status"] for r in rows}
    assert by_url["https://semiconductor.samsung.com/dram/lpddr/lpddr4x/"] == "EOL"
    assert count_signals(conn, SOURCE) == 1
    sig = conn.execute(
        "SELECT raw_text, keyword FROM signals WHERE source = ?", (SOURCE,)
    ).fetchone()
    payload = json.loads(sig["raw_text"])
    assert payload["new_status"] == "EOL"
    assert sig["keyword"] == "first_eol"

    # Second run with flipped GDDR7 → EOL should emit a change signal
    html_by_url["https://semiconductor.samsung.com/dram/gddr/gddr7/"] = (
        FIXTURES / "samsung_lpddr4x.html"
    ).read_text(encoding="utf-8").replace("LPDDR4X", "GDDR7").replace(
        'content="EOL"', 'content="EOL"'
    )
    stats2 = collector.collect()
    assert stats2["status_changes"] >= 1
    assert count_signals(conn, SOURCE) >= 2
    conn.close()
