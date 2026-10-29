"""Memory Signals FastAPI server — CSV-backed signals / votes API.

Run:
  uvicorn app.server:app --reload --port 8000
"""

from __future__ import annotations

import csv
import fcntl
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SIGNALS = REPO_ROOT / "data" / "signals.csv"
DEFAULT_SIGHTINGS = REPO_ROOT / "data" / "sightings.csv"
DEFAULT_VOTES = REPO_ROOT / "data" / "votes.csv"
DEFAULT_COMPANIES = REPO_ROOT / "config" / "companies.yaml"
STATIC_DIR = Path(__file__).resolve().parent / "static"

VOTE_COLUMNS = ["vote_id", "signal_id", "user", "vote", "voted_at"]
SIGHTING_API_FIELDS = (
    "filing_date",
    "form",
    "section",
    "match",
    "source_url",
    "excerpt",
)


class VoteIn(BaseModel):
    signal_id: str
    user: str
    vote: Literal[1, -1]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _latest_votes_by_user_signal(
    votes: list[dict[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    latest: dict[tuple[str, str], dict[str, str]] = {}
    for row in votes:
        key = (row.get("user", ""), row.get("signal_id", ""))
        prev = latest.get(key)
        if prev is None or (row.get("voted_at") or "") >= (prev.get("voted_at") or ""):
            latest[key] = row
    return latest


def _vote_tallies(votes: list[dict[str, str]]) -> dict[str, dict[str, int]]:
    """signal_id → {up, down} from each user's latest vote."""
    tallies: dict[str, dict[str, int]] = {}
    for (user, sid), row in _latest_votes_by_user_signal(votes).items():
        if not user or not sid:
            continue
        try:
            v = int(row.get("vote", 0))
        except ValueError:
            continue
        bucket = tallies.setdefault(sid, {"up": 0, "down": 0})
        if v == 1:
            bucket["up"] += 1
        elif v == -1:
            bucket["down"] += 1
    return tallies


def _sightings_by_signal(
    sightings: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    by_sid: dict[str, list[dict[str, str]]] = {}
    for row in sightings:
        sid = row.get("signal_id") or ""
        if not sid:
            continue
        by_sid.setdefault(sid, []).append(
            {k: row.get(k) or "" for k in SIGHTING_API_FIELDS}
        )
    for group in by_sid.values():
        group.sort(key=lambda r: r.get("filing_date") or "", reverse=True)
    return by_sid


def _known_signal_ids(signals_path: Path) -> set[str]:
    return {r.get("signal_id", "") for r in _read_csv(signals_path) if r.get("signal_id")}


def _load_company_names(companies_path: Path) -> list[str]:
    """Return company names from config/companies.yaml (order preserved)."""
    if not companies_path.is_file():
        return []
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required for /api/meta") from exc
    data = yaml.safe_load(companies_path.read_text(encoding="utf-8")) or {}
    companies = data.get("companies") or []
    names: list[str] = []
    for row in companies:
        name = (row or {}).get("name") or ""
        if name:
            names.append(str(name))
    return names


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def compute_notes(
    signals: list[dict[str, Any]],
    company_names: list[str],
) -> dict[str, Any]:
    """
    Dynamic coverage notes for the Signal map (Prompt 6).

    All counts come from the provided signals + company list — never hard-coded.
    """
    present = {r.get("company") or "" for r in signals}
    missing = [c for c in company_names if c not in present]
    buyers = [r for r in signals if (r.get("role") or "").lower() == "buyer"]
    sources = sorted(
        {r.get("source") or "" for r in signals if (r.get("source") or "").strip()}
    )
    general = sum(
        1 for r in signals if (r.get("product_family") or "") == "Memory_general"
    )
    buyer_specific = sum(
        1 for r in buyers if (r.get("product_family") or "") != "Memory_general"
    )
    conflicts = sum(1 for r in signals if _is_truthy(r.get("direction_conflict")))

    if not missing:
        missing_label = "None of the chosen companies"
    elif len(missing) == 1:
        missing_label = missing[0]
    elif len(missing) == 2:
        missing_label = f"{missing[0]} and {missing[1]}"
    else:
        missing_label = ", ".join(missing[:-1]) + f", and {missing[-1]}"

    return {
        "total": len(signals),
        "companies_listed": len(company_names),
        "missing_companies": missing,
        "missing": missing_label,
        "sources": ", ".join(sources) if sources else "none",
        "general_rows": general,
        "buyer_rows": len(buyers),
        "buyer_specific": buyer_specific,
        "conflict_rows": conflicts,
    }


def _append_vote_locked(
    votes_path: Path,
    *,
    signal_id: str,
    user: str,
    vote: int,
) -> dict[str, str]:
    row = {
        "vote_id": str(uuid.uuid4()),
        "signal_id": signal_id,
        "user": user,
        "vote": str(vote),
        "voted_at": datetime.now(timezone.utc).isoformat(),
    }
    votes_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = votes_path.with_suffix(votes_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        write_header = not votes_path.is_file() or votes_path.stat().st_size == 0
        with votes_path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=VOTE_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
            fh.flush()
    return row


def create_app(
    *,
    signals_path: Path | None = None,
    sightings_path: Path | None = None,
    votes_path: Path | None = None,
    companies_path: Path | None = None,
    static_dir: Path | None = None,
    mount_static: bool = True,
) -> FastAPI:
    signals_path = signals_path or DEFAULT_SIGNALS
    sightings_path = sightings_path or DEFAULT_SIGHTINGS
    votes_path = votes_path or DEFAULT_VOTES
    companies_path = companies_path or DEFAULT_COMPANIES
    static_dir = static_dir or STATIC_DIR

    app = FastAPI(title="Memory Signals", version="0.2.0")

    @app.get("/api/signals")
    def get_signals() -> list[dict[str, Any]]:
        signals = _read_csv(signals_path)
        sightings = _sightings_by_signal(_read_csv(sightings_path))
        tallies = _vote_tallies(_read_csv(votes_path))
        out: list[dict[str, Any]] = []
        for row in signals:
            sid = row.get("signal_id") or ""
            payload = dict(row)
            payload["sightings"] = sightings.get(sid, [])
            payload["votes"] = tallies.get(sid, {"up": 0, "down": 0})
            out.append(payload)
        return out

    @app.get("/api/meta")
    def get_meta() -> dict[str, Any]:
        """Coverage notes for the Signal map (companies.yaml + signals.csv)."""
        signals = _read_csv(signals_path)
        names = _load_company_names(companies_path)
        notes = compute_notes(signals, names)
        return {"notes": notes, "companies": names}

    @app.get("/api/votes")
    def get_votes(user: str = Query(..., min_length=1)) -> dict[str, int]:
        """Latest vote per signal_id for this user. Values are 1 or -1."""
        name = user.strip()
        if not name:
            raise HTTPException(status_code=400, detail="user is required")
        latest = _latest_votes_by_user_signal(_read_csv(votes_path))
        result: dict[str, int] = {}
        for (u, sid), row in latest.items():
            if u != name or not sid:
                continue
            try:
                result[sid] = int(row["vote"])
            except (KeyError, ValueError):
                continue
        return result

    @app.post("/api/votes")
    def post_vote(body: VoteIn) -> dict[str, str]:
        user = body.user.strip()
        if not user:
            raise HTTPException(status_code=400, detail="user is required")
        if body.signal_id not in _known_signal_ids(signals_path):
            raise HTTPException(status_code=400, detail="unknown signal_id")
        row = _append_vote_locked(
            votes_path,
            signal_id=body.signal_id,
            user=user,
            vote=body.vote,
        )
        return row

    @app.get("/api/broker-sim")
    def get_broker_sim(
        days: int = Query(30, ge=1, le=365),
        seed: int = Query(42),
        live: bool = Query(True, description="Prefer MemoryIndex live/public prices"),
        starting_cash: float = Query(250_000.0, gt=0),
    ) -> dict[str, Any]:
        """Run the three-broker DRAM market sim and return summary + history."""
        from dram_signals.broker_sim.simulation import run_simulation

        result = run_simulation(
            days=days,
            seed=seed,
            starting_cash=starting_cash,
            companies_path=companies_path,
            prefer_live_prices=live,
        )
        return result.summary()

    if mount_static and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
