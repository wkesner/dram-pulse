"""API tests for Memory Signals FastAPI server (Prompt 1)."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.server import create_app

REPO = Path(__file__).resolve().parents[1]
LIVE_SIGNALS = REPO / "data" / "signals.csv"
LIVE_SIGHTINGS = REPO / "data" / "sightings.csv"


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Temp copy of fixture-shaped live CSVs + empty votes."""
    assert LIVE_SIGNALS.is_file(), "data/signals.csv required"
    assert LIVE_SIGHTINGS.is_file(), "data/sightings.csv required"
    shutil.copy(LIVE_SIGNALS, tmp_path / "signals.csv")
    shutil.copy(LIVE_SIGHTINGS, tmp_path / "sightings.csv")
    votes = tmp_path / "votes.csv"
    votes.write_text("vote_id,signal_id,user,vote,voted_at\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    app = create_app(
        signals_path=data_dir / "signals.csv",
        sightings_path=data_dir / "sightings.csv",
        votes_path=data_dir / "votes.csv",
        mount_static=False,
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
