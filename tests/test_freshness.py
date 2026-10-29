"""Freshness scan unit tests (no network)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.fetch_edgar import Company
from src.freshness import scan_freshness


def test_scan_marks_missing_and_stale(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    mu = raw / "MU" / "acc1"
    mu.mkdir(parents=True)
    (mu / "meta.json").write_text(
        json.dumps(
            {
                "filing_date": "2026-09-01",
                "form": "10-Q",
                "fiscal_year_end": "0903",
                "prior_only": False,
            }
        ),
        encoding="utf-8",
    )
    companies = [
        Company(name="Micron", role="supplier", source="EDGAR", ticker="MU", tier=1),
        Company(name="Ghost", role="supplier", source="EDGAR", ticker="GHOST", tier=2),
    ]
    rows = scan_freshness(
        companies, raw, stale_days=45, as_of=date(2026, 10, 28)
    )
    by = {r.ticker: r for r in rows}
    assert by["MU"].stale is True  # 57 days old
    assert by["MU"].age_days == 57
    assert by["MU"].n_filings == 1
    assert by["GHOST"].missing is True
    assert by["GHOST"].stale is True


def test_fresh_within_window(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    d = raw / "NVDA" / "acc1"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
        json.dumps(
            {
                "filing_date": "2026-10-20",
                "form": "8-K",
                "fiscal_year_end": "0126",
                "prior_only": False,
            }
        ),
        encoding="utf-8",
    )
    companies = [
        Company(name="Nvidia", role="buyer", source="EDGAR", ticker="NVDA", tier=1),
    ]
    rows = scan_freshness(
        companies, raw, stale_days=45, as_of=date(2026, 10, 28)
    )
    assert rows[0].stale is False
    assert rows[0].age_days == 8
