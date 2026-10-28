"""Org isolation tests for AUTH_MODE=clerk (no live Clerk keys required)."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.clerk_auth import make_test_session
from app.server import create_app

REPO = Path(__file__).resolve().parents[1]
LIVE_SIGNALS = REPO / "data" / "signals.csv"
LIVE_SIGHTINGS = REPO / "data" / "sightings.csv"
LIVE_COMPANIES = REPO / "config" / "companies.yaml"


@pytest.fixture()
def clerk_env(tmp_path: Path):
    shutil.copy(LIVE_SIGNALS, tmp_path / "signals.csv")
    shutil.copy(LIVE_SIGHTINGS, tmp_path / "sightings.csv")
    shutil.copy(LIVE_COMPANIES, tmp_path / "companies.yaml")
    orgs = tmp_path / "orgs"
    aggregates = tmp_path / "aggregates" / "events.jsonl"
    return {
        "signals": tmp_path / "signals.csv",
        "sightings": tmp_path / "sightings.csv",
        "companies": tmp_path / "companies.yaml",
        "orgs": orgs,
        "aggregates": aggregates,
        "votes_single": tmp_path / "votes.csv",
    }


def _client(clerk_env, org_id: str, user_id: str, display: str = ""):
    session = make_test_session(
        user_id=user_id, org_id=org_id, display_name=display or user_id
    )

    def resolver(_token: str | None):
        return session

    app = create_app(
        signals_path=clerk_env["signals"],
        sightings_path=clerk_env["sightings"],
        votes_path=clerk_env["votes_single"],
        companies_path=clerk_env["companies"],
        mount_static=False,
        mode="clerk",
        orgs_root=clerk_env["orgs"],
        aggregates_path=clerk_env["aggregates"],
        session_resolver=resolver,
        operator_token="test-operator",
    )
    return TestClient(app), session


def test_org_a_votes_invisible_to_org_b(clerk_env):
    client_a, sess_a = _client(clerk_env, "org_alpha", "user_a", "Alice")
    client_b, sess_b = _client(clerk_env, "org_beta", "user_b", "Bob")

    signals = client_a.get("/api/signals").json()
    assert len(signals) > 0
    sid = signals[0]["signal_id"]

    r = client_a.post("/api/votes", json={"signal_id": sid, "vote": 1})
    assert r.status_code == 200
    assert r.json()["org_id"] == "org_alpha"
    assert r.json()["user_id"] == "user_a"

    # Org A sees its tally
    row_a = next(s for s in client_a.get("/api/signals").json() if s["signal_id"] == sid)
    assert row_a["votes"] == {"up": 1, "down": 0}

    # Org B gets its own seeded corpus with zero tallies for that signal
    row_b = next(s for s in client_b.get("/api/signals").json() if s["signal_id"] == sid)
    assert row_b["votes"] == {"up": 0, "down": 0}

    # User votes endpoint is org-scoped
    assert client_a.get("/api/votes").json().get(sid) == 1
    assert client_b.get("/api/votes").json() == {}

    # Separate vote files on disk
    votes_a = clerk_env["orgs"] / "org_alpha" / "votes.csv"
    votes_b = clerk_env["orgs"] / "org_beta" / "votes.csv"
    assert votes_a.is_file()
    # Org B may not have voted yet — bootstrap via signals creates empty votes
    client_b.get("/api/signals")
    assert votes_b.is_file()
    with votes_a.open(encoding="utf-8", newline="") as fh:
        rows_a = list(csv.DictReader(fh))
    assert len(rows_a) == 1
    assert rows_a[0]["org_id"] == "org_alpha"
    with votes_b.open(encoding="utf-8", newline="") as fh:
        rows_b = list(csv.DictReader(fh))
    assert rows_b == []


def test_aggregate_events_have_no_org_or_user_pii(clerk_env):
    client_a, _ = _client(clerk_env, "org_alpha", "user_a")
    sid = client_a.get("/api/signals").json()[0]["signal_id"]
    client_a.post("/api/votes", json={"signal_id": sid, "vote": -1})

    raw = clerk_env["aggregates"].read_text(encoding="utf-8").strip()
    event = json.loads(raw)
    assert set(event.keys()) == {"signal_fingerprint", "direction", "vote", "ts"}
    assert "org" not in json.dumps(event).lower()
    assert "user_a" not in json.dumps(event)
    assert "org_alpha" not in json.dumps(event)

    resp = client_a.get(
        "/api/operator/aggregates", headers={"X-Operator-Token": "test-operator"}
    )
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


def test_clerk_mode_rejects_missing_auth(clerk_env):
    app = create_app(
        signals_path=clerk_env["signals"],
        sightings_path=clerk_env["sightings"],
        votes_path=clerk_env["votes_single"],
        companies_path=clerk_env["companies"],
        mount_static=False,
        mode="clerk",
        orgs_root=clerk_env["orgs"],
        aggregates_path=clerk_env["aggregates"],
        session_resolver=None,
        operator_token="x",
    )
    client = TestClient(app)
    # No keys + no resolver → 503 configuration error
    resp = client.get("/api/signals")
    assert resp.status_code == 503


def test_bootstrap_seeds_corpus(clerk_env):
    client, sess = _client(clerk_env, "org_gamma", "user_g")
    resp = client.post("/api/orgs/bootstrap")
    assert resp.status_code == 200
    assert resp.json()["org_id"] == "org_gamma"
    assert (clerk_env["orgs"] / "org_gamma" / "signals.csv").is_file()
    assert (clerk_env["orgs"] / "org_gamma" / "sightings.csv").is_file()
