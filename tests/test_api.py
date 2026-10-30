"""API tests for Memory Signals FastAPI server (Prompt 1 / 6)."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.server import compute_notes, create_app

REPO = Path(__file__).resolve().parents[1]
LIVE_SIGNALS = REPO / "data" / "signals.csv"
LIVE_SIGHTINGS = REPO / "data" / "sightings.csv"
LIVE_COMPANIES = REPO / "config" / "companies.yaml"


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Temp copy of fixture-shaped live CSVs + empty votes."""
    assert LIVE_SIGNALS.is_file(), "data/signals.csv required"
    assert LIVE_SIGHTINGS.is_file(), "data/sightings.csv required"
    shutil.copy(LIVE_SIGNALS, tmp_path / "signals.csv")
    shutil.copy(LIVE_SIGHTINGS, tmp_path / "sightings.csv")
    shutil.copy(LIVE_COMPANIES, tmp_path / "companies.yaml")
    votes = tmp_path / "votes.csv"
    votes.write_text("vote_id,signal_id,user,vote,voted_at\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    # Explicit single-tenant mode so AUTH_MODE=clerk in a local .env
    # does not break the classic typed-name API tests.
    # broker_sim_open enables the local offline demo gate for /api/broker-sim.
    app = create_app(
        signals_path=data_dir / "signals.csv",
        sightings_path=data_dir / "sightings.csv",
        votes_path=data_dir / "votes.csv",
        companies_path=data_dir / "companies.yaml",
        mount_static=False,
        mode="single",
        broker_sim_open=True,
    )
    return TestClient(app)


def test_get_signals_length_and_shape(client: TestClient, data_dir: Path):
    with (data_dir / "signals.csv").open(encoding="utf-8", newline="") as fh:
        expected_n = sum(1 for _ in csv.DictReader(fh))
    resp = client.get("/api/signals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == expected_n
    assert expected_n > 0
    row = body[0]
    assert "signal_id" in row
    assert "sightings" in row and isinstance(row["sightings"], list)
    assert "votes" in row and row["votes"] == {"up": 0, "down": 0}
    if row["sightings"]:
        s = row["sightings"][0]
        for key in ("filing_date", "form", "section", "match", "source_url", "excerpt"):
            assert key in s


def test_get_signals_attaches_sightings(client: TestClient):
    body = client.get("/api/signals").json()
    multi = [r for r in body if int(r.get("times_seen") or 0) > 1]
    assert multi, "expected at least one multi-sighting signal"
    assert len(multi[0]["sightings"]) == int(multi[0]["times_seen"])


def test_post_vote_and_tallies(client: TestClient, data_dir: Path):
    signals = client.get("/api/signals").json()
    sid = signals[0]["signal_id"]

    r1 = client.post("/api/votes", json={"signal_id": sid, "user": "Alice", "vote": 1})
    assert r1.status_code == 200
    assert r1.json()["signal_id"] == sid
    assert r1.json()["user"] == "Alice"
    assert r1.json()["vote"] == "1"
    assert r1.json()["vote_id"]
    assert r1.json()["voted_at"]

    r2 = client.post("/api/votes", json={"signal_id": sid, "user": "Bob", "vote": -1})
    assert r2.status_code == 200

    body = client.get("/api/signals").json()
    row = next(r for r in body if r["signal_id"] == sid)
    assert row["votes"] == {"up": 1, "down": 1}

    # Alice changes mind → latest wins
    client.post("/api/votes", json={"signal_id": sid, "user": "Alice", "vote": -1})
    body = client.get("/api/signals").json()
    row = next(r for r in body if r["signal_id"] == sid)
    assert row["votes"] == {"up": 0, "down": 2}

    with (data_dir / "votes.csv").open(encoding="utf-8", newline="") as fh:
        votes = list(csv.DictReader(fh))
    assert len(votes) == 3


def test_get_votes_for_user(client: TestClient):
    signals = client.get("/api/signals").json()
    sid_a = signals[0]["signal_id"]
    sid_b = signals[1]["signal_id"]
    client.post("/api/votes", json={"signal_id": sid_a, "user": "Carol", "vote": 1})
    client.post("/api/votes", json={"signal_id": sid_b, "user": "Carol", "vote": -1})
    client.post("/api/votes", json={"signal_id": sid_a, "user": "Carol", "vote": -1})

    resp = client.get("/api/votes", params={"user": "Carol"})
    assert resp.status_code == 200
    data = resp.json()
    assert data[sid_a] == -1
    assert data[sid_b] == -1
    assert len(data) == 2


def test_post_vote_rejects_unknown_signal(client: TestClient):
    resp = client.post(
        "/api/votes",
        json={"signal_id": "notarealsignal", "user": "Alice", "vote": 1},
    )
    assert resp.status_code == 400


def test_post_vote_rejects_empty_user(client: TestClient):
    sid = client.get("/api/signals").json()[0]["signal_id"]
    resp = client.post("/api/votes", json={"signal_id": sid, "user": "   ", "vote": 1})
    assert resp.status_code == 400


def test_post_vote_rejects_invalid_vote_value(client: TestClient):
    sid = client.get("/api/signals").json()[0]["signal_id"]
    resp = client.post("/api/votes", json={"signal_id": sid, "user": "Alice", "vote": 2})
    assert resp.status_code == 422


def test_skip_persists_and_does_not_tally(client: TestClient, data_dir: Path):
    """vote=0 is Skip: stored for the profile, ignored in 👍/👎 tallies."""
    signals = client.get("/api/signals").json()
    sid = signals[0]["signal_id"]

    r = client.post("/api/votes", json={"signal_id": sid, "user": "Dana", "vote": 0})
    assert r.status_code == 200
    assert r.json()["vote"] == "0"

    mine = client.get("/api/votes", params={"user": "Dana"}).json()
    assert mine[sid] == 0

    body = client.get("/api/signals").json()
    row = next(r for r in body if r["signal_id"] == sid)
    assert row["votes"] == {"up": 0, "down": 0}

    # Another user can still rate; skip does not inflate tallies.
    client.post("/api/votes", json={"signal_id": sid, "user": "Eli", "vote": 1})
    body = client.get("/api/signals").json()
    row = next(r for r in body if r["signal_id"] == sid)
    assert row["votes"] == {"up": 1, "down": 0}

    with (data_dir / "votes.csv").open(encoding="utf-8", newline="") as fh:
        votes = list(csv.DictReader(fh))
    assert any(v["user"] == "Dana" and v["vote"] == "0" for v in votes)


def test_meta_exposes_tiers_and_signals_have_tier(client: TestClient, data_dir: Path):
    meta = client.get("/api/meta").json()
    assert "tiers" in meta and len(meta["tiers"]) == 3
    assert meta["default_map_tier"] == 1
    assert meta["company_roster"]
    assert {c["tier"] for c in meta["company_roster"]} <= {1, 2, 3}

    signals = client.get("/api/signals").json()
    assert signals
    assert all(str(s.get("tier")) in {"1", "2", "3"} for s in signals)


def test_meta_notes_match_signals(client: TestClient, data_dir: Path):
    signals = list(csv.DictReader((data_dir / "signals.csv").open(encoding="utf-8")))
    meta = client.get("/api/meta").json()
    notes = meta["notes"]
    assert notes["total"] == len(signals)
    assert notes["general_rows"] == sum(
        1 for r in signals if r.get("product_family") == "Memory_general"
    )
    buyers = [r for r in signals if r.get("role") == "buyer"]
    assert notes["buyer_rows"] == len(buyers)
    assert notes["buyer_specific"] == sum(
        1 for r in buyers if r.get("product_family") != "Memory_general"
    )
    assert notes["conflict_rows"] == sum(
        1
        for r in signals
        if str(r.get("direction_conflict", "")).lower() in {"true", "1"}
    )
    # Live data: Jabil is listed but has no signals.
    assert "Jabil" in notes["missing_companies"]
    assert "Jabil" in notes["missing"]


def test_meta_lists_stub_company_with_zero_signals(data_dir: Path):
    """Prompt 6 check: a company with no filings appears in missing — without
    committing a fake company to config/companies.yaml."""
    companies_path = data_dir / "companies.yaml"
    data = yaml.safe_load(companies_path.read_text(encoding="utf-8"))
    data["companies"].append(
        {
            "name": "Acme Stub Co",
            "role": "buyer",
            "source": "EDGAR",
            "ticker": "ACME",
        }
    )
    companies_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    app = create_app(
        signals_path=data_dir / "signals.csv",
        sightings_path=data_dir / "sightings.csv",
        votes_path=data_dir / "votes.csv",
        companies_path=companies_path,
        mount_static=False,
        mode="single",
    )
    client = TestClient(app)
    notes = client.get("/api/meta").json()["notes"]
    assert "Acme Stub Co" in notes["missing_companies"]
    assert "Acme Stub Co" in notes["missing"]

    # Pure function also lists the stub.
    signals = list(csv.DictReader((data_dir / "signals.csv").open(encoding="utf-8")))
    names = [c["name"] for c in data["companies"]]
    computed = compute_notes(signals, names)
    assert "Acme Stub Co" in computed["missing_companies"]


def test_compute_notes_join_missing_labels():
    notes = compute_notes(
        [{"company": "A", "role": "supplier", "product_family": "HBM", "source": "EDGAR", "direction_conflict": "false"}],
        ["A", "B", "C", "D"],
    )
    assert notes["missing"] == "B, C, and D"
    assert notes["missing_companies"] == ["B", "C", "D"]


def test_broker_sim_api_offline(client: TestClient):
    resp = client.get(
        "/api/broker-sim",
        params={
            "days": 3,
            "seed": 9,
            "live": "false",
            "starting_cash": 250000,
            "include_scored": "false",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 3
    assert body["starting_cash"] == 250000
    assert len(body["brokers"]) == 3
    assert len(body["history"]) == 3
    assert body["prices"]["source"] == "illustrative"
    assert "equity" in body["history"][0]["brokers"]["apex"]


def test_broker_sim_api_with_scored_twins(client: TestClient):
    resp = client.get(
        "/api/broker-sim",
        params={
            "days": 4,
            "seed": 3,
            "live": "false",
            "include_scored": "true",
            "score_lag": 1,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["brokers"]) == 6
    assert len(body["pair_comparisons"]) == 3
    assert body["include_scored"] is True
    assert "apex_scored" in body["history"][0]["brokers"]


def test_broker_sim_disabled_without_open_or_token(data_dir: Path):
    app = create_app(
        signals_path=data_dir / "signals.csv",
        sightings_path=data_dir / "sightings.csv",
        votes_path=data_dir / "votes.csv",
        companies_path=data_dir / "companies.yaml",
        mount_static=False,
        mode="single",
        broker_sim_open=False,
        broker_sim_token="",
    )
    client = TestClient(app)
    resp = client.get("/api/broker-sim", params={"days": 2, "live": "false"})
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()


def test_broker_sim_requires_valid_token(data_dir: Path):
    app = create_app(
        signals_path=data_dir / "signals.csv",
        sightings_path=data_dir / "sightings.csv",
        votes_path=data_dir / "votes.csv",
        companies_path=data_dir / "companies.yaml",
        mount_static=False,
        mode="single",
        broker_sim_open=False,
        broker_sim_token="sim-secret",
    )
    client = TestClient(app)
    denied = client.get("/api/broker-sim", params={"days": 2, "live": "false"})
    assert denied.status_code == 401
    bad = client.get(
        "/api/broker-sim",
        params={"days": 2, "live": "false"},
        headers={"X-Broker-Sim-Token": "wrong"},
    )
    assert bad.status_code == 401
    ok = client.get(
        "/api/broker-sim",
        params={"days": 2, "live": "false", "include_scored": "false"},
        headers={"X-Broker-Sim-Token": "sim-secret"},
    )
    assert ok.status_code == 200
    assert ok.json()["days"] == 2


def test_broker_sim_open_mode_forces_offline_and_caps_days(data_dir: Path):
    app = create_app(
        signals_path=data_dir / "signals.csv",
        sightings_path=data_dir / "sightings.csv",
        votes_path=data_dir / "votes.csv",
        companies_path=data_dir / "companies.yaml",
        mount_static=False,
        mode="single",
        broker_sim_open=True,
        broker_sim_token="",
    )
    client = TestClient(app)
    # Open gate ignores live=true (no MemoryIndex fetch) and caps days at 30.
    resp = client.get(
        "/api/broker-sim",
        params={"days": 45, "live": "true", "include_scored": "false", "seed": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 30
    assert body["prices"]["source"] == "illustrative"


def test_broker_sim_rejects_days_over_cap(client: TestClient):
    resp = client.get("/api/broker-sim", params={"days": 365, "live": "false"})
    assert resp.status_code == 422


def test_config_reports_open_single_mode(client: TestClient):
    cfg = client.get("/api/config").json()
    assert cfg["auth_mode"] == "single"
    assert cfg["requires_auth"] is False
    assert cfg["requires_app_token"] is False


def test_app_access_token_gates_all_data_apis(data_dir: Path):
    app = create_app(
        signals_path=data_dir / "signals.csv",
        sightings_path=data_dir / "sightings.csv",
        votes_path=data_dir / "votes.csv",
        companies_path=data_dir / "companies.yaml",
        mount_static=False,
        mode="single",
        app_token="app-secret",
        broker_sim_open=False,
        broker_sim_token="",
    )
    client = TestClient(app)

    cfg = client.get("/api/config").json()
    assert cfg["requires_auth"] is True
    assert cfg["requires_app_token"] is True

    assert client.get("/api/signals").status_code == 401
    assert client.get("/api/meta").status_code == 401
    assert client.get("/api/votes", params={"user": "A"}).status_code == 401
    assert client.get("/api/broker-sim", params={"days": 2}).status_code == 401

    bad = client.get("/api/signals", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401

    ok_headers = {"Authorization": "Bearer app-secret"}
    assert client.get("/api/signals", headers=ok_headers).status_code == 200
    assert client.get("/api/meta", headers=ok_headers).status_code == 200
    # X-App-Token is an accepted alternate header.
    assert (
        client.get("/api/signals", headers={"X-App-Token": "app-secret"}).status_code
        == 200
    )
    # Authenticated callers unlock broker-sim without BROKER_SIM_OPEN/TOKEN.
    sim = client.get(
        "/api/broker-sim",
        params={"days": 2, "live": "false", "include_scored": "false"},
        headers=ok_headers,
    )
    assert sim.status_code == 200
    assert sim.json()["days"] == 2
