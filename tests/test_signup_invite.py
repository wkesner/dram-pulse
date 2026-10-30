"""SIGNUP_INVITE_KEY gate for Clerk account creation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.clerk_auth import (
    check_signup_invite_key,
    mint_signup_invite_ticket,
    verify_signup_invite_ticket,
)
from app.server import create_app

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def clerk_invite_client(tmp_path: Path):
    signals = tmp_path / "signals.csv"
    sightings = tmp_path / "sightings.csv"
    votes = tmp_path / "votes.csv"
    signals.write_text(
        (REPO / "data" / "signals.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    sightings.write_text(
        (REPO / "data" / "sightings.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    votes.write_text("vote_id,signal_id,user,vote,voted_at\n", encoding="utf-8")
    app = create_app(
        signals_path=signals,
        sightings_path=sightings,
        votes_path=votes,
        companies_path=REPO / "config" / "companies.yaml",
        mount_static=False,
        mode="clerk",
        orgs_root=tmp_path / "orgs",
        aggregates_path=tmp_path / "agg.jsonl",
        session_resolver=None,
        operator_token="x",
        invite_key="invite-secret-xyz",
        app_token="",
    )
    return TestClient(app)


def test_config_flags_signup_invite_without_leaking_key(clerk_invite_client: TestClient):
    cfg = clerk_invite_client.get("/api/config").json()
    assert cfg["requires_signup_invite"] is True
    assert "invite-secret" not in str(cfg).lower()
    assert "SIGNUP_INVITE_KEY" not in str(cfg)
    assert cfg.get("signup_invite_key") in (None, "")


def test_verify_invite_rejects_bad_key(clerk_invite_client: TestClient):
    resp = clerk_invite_client.post(
        "/api/signup/verify-invite", json={"key": "wrong"}
    )
    assert resp.status_code == 401


def test_verify_invite_mints_and_reuses_ticket(clerk_invite_client: TestClient):
    ok = clerk_invite_client.post(
        "/api/signup/verify-invite", json={"key": "invite-secret-xyz"}
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["ticket"]
    assert body["reused"] is False

    again = clerk_invite_client.post(
        "/api/signup/verify-invite", json={"ticket": body["ticket"]}
    )
    assert again.status_code == 200
    assert again.json()["ok"] is True
    assert again.json()["reused"] is True


def test_verify_invite_rejects_garbage_ticket(clerk_invite_client: TestClient):
    resp = clerk_invite_client.post(
        "/api/signup/verify-invite", json={"ticket": "v1.1.abc.deadbeef"}
    )
    assert resp.status_code == 401


def test_invite_helpers_roundtrip():
    key = "roundtrip-key-99"
    assert check_signup_invite_key(key, invite_key=key)
    assert not check_signup_invite_key("nope", invite_key=key)
    ticket = mint_signup_invite_ticket(key, ttl_seconds=120)
    assert verify_signup_invite_ticket(ticket, invite_key=key)
    assert not verify_signup_invite_ticket(ticket + "x", invite_key=key)


def test_single_mode_has_no_invite_endpoint(tmp_path: Path):
    app = create_app(
        signals_path=REPO / "data" / "signals.csv",
        sightings_path=REPO / "data" / "sightings.csv",
        votes_path=tmp_path / "votes.csv",
        companies_path=REPO / "config" / "companies.yaml",
        mount_static=False,
        mode="single",
        invite_key="should-not-matter",
        broker_sim_open=True,
    )
    client = TestClient(app)
    cfg = client.get("/api/config").json()
    assert cfg["requires_signup_invite"] is False
    assert client.post("/api/signup/verify-invite", json={"key": "x"}).status_code == 404
