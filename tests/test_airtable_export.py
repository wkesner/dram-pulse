"""Offline Airtable exporter tests (brief section 3.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dram_signals.db import (
    SignalRecord,
    connect,
    get_export_watermark,
    init_db,
    upsert_signals,
)
from dram_signals.exporters.airtable_client import AirtableClient
from dram_signals.exporters.airtable_export import (
    COMPANY_FIELDS,
    SIGNAL_FIELDS,
    TABLES,
    export_to_airtable,
)
from dram_signals.exporters.company_keys import company_key_and_role, signal_key
from tests.fake_airtable import STATE, start_fake_airtable


@pytest.fixture()
def fake_api():
    server, api_base = start_fake_airtable()
    yield api_base
    server.shutdown()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = connect(path)
    init_db(conn)
    conn.commit()
    conn.close()
    return path


def _seed(db_path: Path, n: int = 2, *, source: str = "edgar_8k") -> None:
    conn = connect(db_path)
    init_db(conn)
    records = []
    for i in range(n):
        cik = 1000000 + i  # distinct CIKs → distinct Company Keys
        records.append(
            SignalRecord(
                source=source,
                timestamp=f"2026-10-{10+i:02d}T00:00:00+00:00",
                company_name=f"Acme Corp {i}",
                raw_text=f"inventory write-down DRAM filing {i}",
                url=f"https://www.sec.gov/Archives/edgar/data/{cik}/0001/a{i}.htm",
                external_id=f"0001:a{i}.htm",
                keyword="inventory write-down",
            )
        )
    upsert_signals(conn, records)
    conn.commit()
    conn.close()


def _client(api_base: str) -> AirtableClient:
    return AirtableClient(
        "tok",
        "appTEST",
        api_base=api_base,
        rate_limit_wait_s=0.01,
        min_interval_s=0.0,
    )


def test_company_key_micron_cik() -> None:
    key, role = company_key_and_role(
        company_name="Micron Technology Inc",
        url="https://www.sec.gov/Archives/edgar/data/723125/0001/x.htm",
    )
    assert key == "vendor:micron"
    assert role == "manufacturer"


def test_company_key_samsung_name() -> None:
    key, role = company_key_and_role(company_name="Samsung Electronics")
    assert key == "vendor:samsung"
    assert role == "manufacturer"


def test_changed_at_moves_only_on_content_change(db) -> None:
    conn = connect(db)
    init_db(conn)
    r = SignalRecord(
        source="edgar_8k",
        timestamp="2026-10-01T00:00:00+00:00",
        company_name="Acme",
        raw_text="hello",
        url="https://example.com/1",
        external_id="e1",
        keyword="memory",
    )
    assert upsert_signals(conn, [r]) == 1
    row = conn.execute("SELECT changed_at, content_hash FROM signals").fetchone()
    first_changed = row["changed_at"]
    # Identical rerun
    assert upsert_signals(conn, [r]) == 0
    row2 = conn.execute("SELECT changed_at FROM signals").fetchone()
    assert row2["changed_at"] == first_changed
    # Extra term / content change
    r2 = SignalRecord(
        source="edgar_8k",
        timestamp="2026-10-01T00:00:00+00:00",
        company_name="Acme",
        raw_text="hello world",
        url="https://example.com/1",
        external_id="e1",
        keyword="memory|DRAM",
    )
    assert upsert_signals(conn, [r2]) == 1
    row3 = conn.execute("SELECT changed_at, keyword FROM signals").fetchone()
    assert row3["changed_at"] >= first_changed
    assert "DRAM" in (row3["keyword"] or "")
    conn.close()


def test_first_export_creates_companies_then_signals(db, fake_api) -> None:
    _seed(db, 2)
    client = _client(fake_api)
    stats = export_to_airtable(
        token="tok",
        base_id="appTEST",
        client=client,
        db_path=db,
    )
    assert stats["companies_upserted"] == 2
    assert stats["signals_created"] == 2
    assert stats["api_calls"] >= 2
    assert TABLES["companies"] in STATE.tables
    assert TABLES["signals"] in STATE.tables
    # Signals link to company record ids
    for rec in STATE.tables[TABLES["signals"]].values():
        company_links = rec["fields"][SIGNAL_FIELDS["company"]]
        assert isinstance(company_links, list) and company_links[0].startswith("recFAKE")
    assert get_export_watermark(connect(db), "airtable") is not None


def test_rerun_no_changes_zero_calls(db, fake_api) -> None:
    _seed(db, 2)
    client = _client(fake_api)
    export_to_airtable(token="tok", base_id="appTEST", client=client, db_path=db)
    calls_after_first = client.api_calls
    STATE.requests.clear()
    client2 = _client(fake_api)
    stats = export_to_airtable(
        token="tok", base_id="appTEST", client=client2, db_path=db
    )
    assert stats["exported"] == 0
    assert client2.api_calls == 0
    assert STATE.requests == []
    assert calls_after_first >= 2


def test_changed_row_updates_in_place(db, fake_api) -> None:
    _seed(db, 1)
    client = _client(fake_api)
    export_to_airtable(token="tok", base_id="appTEST", client=client, db_path=db)
    # Mutate content
    conn = connect(db)
    init_db(conn)
    upsert_signals(
        conn,
        [
            SignalRecord(
                source="edgar_8k",
                timestamp="2026-10-10T00:00:00+00:00",
                company_name="Acme Corp 0",
                raw_text="inventory write-down DRAM filing 0 UPDATED",
                url="https://www.sec.gov/Archives/edgar/data/1000000/0001/a0.htm",
                external_id="0001:a0.htm",
                keyword="inventory write-down|supply constraint",
            )
        ],
    )
    conn.commit()
    conn.close()
    n_before = len(STATE.tables[TABLES["signals"]])
    export_to_airtable(token="tok", base_id="appTEST", client=_client(fake_api), db_path=db)
    assert len(STATE.tables[TABLES["signals"]]) == n_before  # no duplicate
    sk = signal_key("edgar_8k", "0001:a0.htm")
    excerpt = STATE.tables[TABLES["signals"]][sk]["fields"][SIGNAL_FIELDS["excerpt"]]
    assert "UPDATED" in excerpt


def test_human_owned_fields_not_overwritten(db, fake_api) -> None:
    _seed(db, 1)
    export_to_airtable(token="tok", base_id="appTEST", client=_client(fake_api), db_path=db)
    # Simulate human setting Review / Enrich?
    sk = signal_key("edgar_8k", "0001:a0.htm")
    STATE.tables[TABLES["signals"]][sk]["fields"]["Review"] = "Relevant"
    # Company enrich gate
    ck = list(STATE.tables[TABLES["companies"]].keys())[0]
    STATE.tables[TABLES["companies"]][ck]["fields"]["Enrich?"] = True
    # Force re-export
    conn = connect(db)
    conn.execute("UPDATE exports SET exported_until = '1970-01-01'")
    conn.commit()
    conn.close()
    export_to_airtable(token="tok", base_id="appTEST", client=_client(fake_api), db_path=db)
    assert STATE.tables[TABLES["signals"]][sk]["fields"]["Review"] == "Relevant"
    assert STATE.tables[TABLES["companies"]][ck]["fields"]["Enrich?"] is True


def test_eleven_rows_two_batches(db, fake_api) -> None:
    _seed(db, 11)
    export_to_airtable(token="tok", base_id="appTEST", client=_client(fake_api), db_path=db)
    signal_reqs = [r for r in STATE.requests if r["table"] == TABLES["signals"]]
    assert len(signal_reqs) == 2
    assert signal_reqs[0]["n"] == 10
    assert signal_reqs[1]["n"] == 1


def test_429_retried(db, fake_api) -> None:
    _seed(db, 1)
    STATE.force_429_once = True
    stats = export_to_airtable(
        token="tok", base_id="appTEST", client=_client(fake_api), db_path=db
    )
    assert stats["signals_created"] == 1


def test_dramx_internal_only(db, fake_api) -> None:
    conn = connect(db)
    init_db(conn)
    upsert_signals(
        conn,
        [
            SignalRecord(
                source="dramx_spot",
                timestamp="2026-10-16T00:00:00+00:00",
                company_name="Market",
                raw_text="DDR4 spot internal",
                url="https://www.dramexchange.com/",
                external_id="ddr4-2026-10-16",
                keyword="spot",
            )
        ],
    )
    conn.commit()
    conn.close()
    export_to_airtable(token="tok", base_id="appTEST", client=_client(fake_api), db_path=db)
    sk = signal_key("dramx_spot", "ddr4-2026-10-16")
    assert STATE.tables[TABLES["signals"]][sk]["fields"][SIGNAL_FIELDS["internal_only"]] is True


def test_partial_failure_leaves_watermark(db, fake_api) -> None:
    _seed(db, 2)
    # First succeed companies, fail signals
    STATE.fail_on_table = TABLES["signals"]
    with pytest.raises(SystemExit):
        export_to_airtable(
            token="tok", base_id="appTEST", client=_client(fake_api), db_path=db
        )
    conn = connect(db)
    init_db(conn)
    assert get_export_watermark(conn, "airtable") is None
    conn.close()
