"""Structural perf helpers for ~10 concurrent feed users (no paid infra)."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.csv_cache import CsvCache
from app.rate_limit import RateLimiter
from app.server import create_app

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    shutil.copy(REPO / "data" / "signals.csv", tmp_path / "signals.csv")
    shutil.copy(REPO / "data" / "sightings.csv", tmp_path / "sightings.csv")
    shutil.copy(REPO / "config" / "companies.yaml", tmp_path / "companies.yaml")
    (tmp_path / "votes.csv").write_text(
        "vote_id,signal_id,user,vote,voted_at\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    app = create_app(
        signals_path=data_dir / "signals.csv",
        sightings_path=data_dir / "sightings.csv",
        votes_path=data_dir / "votes.csv",
        companies_path=data_dir / "companies.yaml",
        mount_static=True,
        mode="single",
        broker_sim_open=True,
    )
    return TestClient(app)


def test_csv_cache_hits_same_mtime(tmp_path: Path):
    path = tmp_path / "t.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    cache = CsvCache()
    a = cache.read(path)
    b = cache.read(path)
    assert a == b == [{"a": "1", "b": "2"}]
    assert a is b  # same cached list object
    path.write_text("a,b\n3,4\n", encoding="utf-8")
    c = cache.read(path)
    assert c == [{"a": "3", "b": "4"}]
    assert c is not a


def test_rate_limiter_is_per_key_not_shared():
    lim = RateLimiter(max_calls=2, window_seconds=60.0)
    assert lim.allow("alice")
    assert lim.allow("alice")
    assert not lim.allow("alice")
    # Bob still has a full independent budget.
    assert lim.allow("bob")
    assert lim.allow("bob")
    assert not lim.allow("bob")


def test_meta_payload_smaller_than_full(client: TestClient):
    meta = client.get("/api/signals", params={"sightings": "meta"})
    full = client.get("/api/signals", params={"sightings": "full"})
    assert meta.status_code == 200 and full.status_code == 200
    assert len(meta.content) < len(full.content)
    none = client.get("/api/signals", params={"sightings": "none"})
    assert none.status_code == 200
    assert all(r.get("sightings") == [] for r in none.json())


def test_vote_invalidates_and_etag_changes(client: TestClient, data_dir: Path):
    first = client.get("/api/signals")
    etag1 = first.headers["etag"]
    sid = first.json()[0]["signal_id"]
    client.post("/api/votes", json={"signal_id": sid, "user": "Pat", "vote": 1})
    second = client.get("/api/signals")
    assert second.headers["etag"] != etag1
    with (data_dir / "votes.csv").open(encoding="utf-8", newline="") as fh:
        assert list(csv.DictReader(fh))


def test_static_js_has_cache_control(client: TestClient):
    resp = client.get("/app.js")
    assert resp.status_code == 200
    assert "max-age" in (resp.headers.get("cache-control") or "")
