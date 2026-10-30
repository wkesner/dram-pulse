"""EC Signals FastAPI server — CSV-backed signals / votes API.

Auth modes:
  AUTH_MODE=single (default) — typed name votes, shared data/signals.csv
  AUTH_MODE=clerk — Clerk session JWT + org isolation under data/orgs/{org_id}/

Run:
  scripts/run_feed.sh
  # or: uvicorn app.server:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import csv
import fcntl
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.clerk_auth import (
    ClerkConfigError,
    ClerkSession,
    SessionResolver,
    auth_mode,
    clerk_authorized_parties,
    clerk_configured,
    clerk_issuer,
    clerk_publishable_key,
    verify_clerk_session_token,
)
from app.csv_cache import CsvCache, DerivedCache
from app.rate_limit import ConcurrencyGate, RateLimiter
from app.tenant import (
    DEFAULT_AGGREGATES,
    DEFAULT_ORGS_ROOT,
    append_aggregate_event,
    ensure_org_corpus,
    read_aggregate_events,
)
from src.envutil import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SIGNALS = REPO_ROOT / "data" / "signals.csv"
DEFAULT_SIGHTINGS = REPO_ROOT / "data" / "sightings.csv"
DEFAULT_VOTES = REPO_ROOT / "data" / "votes.csv"
DEFAULT_COMPANIES = REPO_ROOT / "config" / "companies.yaml"
DEFAULT_TIERS = REPO_ROOT / "config" / "tiers.yaml"
STATIC_DIR = Path(__file__).resolve().parent / "static"

VOTE_COLUMNS = ["vote_id", "signal_id", "user", "vote", "voted_at"]
ORG_VOTE_COLUMNS = [
    "vote_id",
    "org_id",
    "user_id",
    "user",
    "signal_id",
    "vote",
    "voted_at",
]
SIGHTING_API_FIELDS = (
    "filing_date",
    "form",
    "section",
    "match",
    "source_url",
    "excerpt",
)
# Meta sightings keep chart/list structure without shipping excerpt text twice.
SIGHTING_META_FIELDS = (
    "filing_date",
    "form",
    "section",
    "match",
    "source_url",
)
SightingsMode = Literal["full", "meta", "none"]

# Expensive GET /api/broker-sim caps (blocking MemoryIndex fetch + multi-day sim).
BROKER_SIM_MAX_DAYS = 90
BROKER_SIM_OPEN_MAX_DAYS = 30

# ~10 concurrent users: high per-client read budget; separate keys so one client
# cannot starve another. Global gate caps simultaneous CSV→JSON builds.
SIGNALS_READ_MAX = 180
SIGNALS_READ_WINDOW_S = 60.0
SIGNALS_BUILD_CONCURRENCY = 12
STATIC_ASSET_MAX_AGE = 300


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


class VoteIn(BaseModel):
    signal_id: str
    user: str = ""
    # 1 = useful, -1 = not useful, 0 = skip (hide from this profile's feed)
    vote: Literal[1, -1, 0]


class TranslateIn(BaseModel):
    """On-demand English translation for a non-English signal excerpt."""

    signal_id: str = Field(min_length=1)


_CSV = CsvCache()
_DERIVED = DerivedCache()


def _read_csv(path: Path) -> list[dict[str, str]]:
    return _CSV.read(path)


def _etag_for(*paths: Path, extra: str = "") -> str:
    raw = _CSV.fingerprint(*paths) + "|" + extra
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return f'"{digest}"'


class _StaticCacheMiddleware(BaseHTTPMiddleware):
    """Add Cache-Control for static UI assets (HTML revalidates)."""

    def __init__(self, app: ASGIApp, *, max_age: int = STATIC_ASSET_MAX_AGE) -> None:
        super().__init__(app)
        self.max_age = max_age

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/"):
            return response
        if path.endswith((".js", ".css")):
            response.headers.setdefault(
                "Cache-Control", f"public, max-age={self.max_age}"
            )
        elif path.endswith(".html") or path == "/" or path.endswith("/"):
            response.headers.setdefault(
                "Cache-Control", "no-cache, must-revalidate"
            )
        return response


def _latest_votes_by_user_signal(
    votes: list[dict[str, str]],
    *,
    user_key: str = "user",
) -> dict[tuple[str, str], dict[str, str]]:
    latest: dict[tuple[str, str], dict[str, str]] = {}
    for row in votes:
        key = (row.get(user_key, "") or row.get("user", ""), row.get("signal_id", ""))
        prev = latest.get(key)
        if prev is None or (row.get("voted_at") or "") >= (prev.get("voted_at") or ""):
            latest[key] = row
    return latest


def _vote_tallies(
    votes: list[dict[str, str]],
    *,
    user_key: str = "user",
) -> dict[str, dict[str, int]]:
    """signal_id → {up, down} from each user's latest vote."""
    tallies: dict[str, dict[str, int]] = {}
    for (user, sid), row in _latest_votes_by_user_signal(votes, user_key=user_key).items():
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
    *,
    fields: tuple[str, ...] = SIGHTING_API_FIELDS,
) -> dict[str, list[dict[str, str]]]:
    by_sid: dict[str, list[dict[str, str]]] = {}
    for row in sightings:
        sid = row.get("signal_id") or ""
        if not sid:
            continue
        by_sid.setdefault(sid, []).append({k: row.get(k) or "" for k in fields})
    for group in by_sid.values():
        group.sort(key=lambda r: r.get("filing_date") or "", reverse=True)
    return by_sid


def _known_signal_ids(signals_path: Path) -> set[str]:
    return {r.get("signal_id", "") for r in _read_csv(signals_path) if r.get("signal_id")}


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _load_company_names(companies_path: Path) -> list[str]:
    """Return company names from config/companies.yaml (order preserved)."""
    companies = _load_yaml_mapping(companies_path).get("companies") or []
    names: list[str] = []
    for row in companies:
        name = (row or {}).get("name") or ""
        if name:
            names.append(str(name))
    return names


def _load_tier_catalog(tiers_path: Path) -> list[dict[str, Any]]:
    rows = _load_yaml_mapping(tiers_path).get("tiers") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            tid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "id": tid,
                "label": row.get("label") or f"Tier {tid}",
                "short": row.get("short") or f"T{tid}",
                "map_default": bool(row.get("map_default")),
                "summary": row.get("summary") or "",
            }
        )
    return out


def _company_tier_lookup(companies_path: Path) -> dict[str, int]:
    """ticker / name → tier for enriching signal rows that predate the column."""
    companies = _load_yaml_mapping(companies_path).get("companies") or []
    lookup: dict[str, int] = {}
    for row in companies:
        if not isinstance(row, dict):
            continue
        try:
            tier = int(row.get("tier", 1))
        except (TypeError, ValueError):
            tier = 1
        if tier not in {1, 2, 3}:
            tier = 1
        keys = [
            str(row.get("name") or "").strip(),
            str(row.get("ticker") or "").strip(),
            str(row.get("krx_code") or "").strip(),
        ]
        for key in keys:
            if not key:
                continue
            for variant in {key, key.upper()}:
                prev = lookup.get(variant)
                if prev is None or tier < prev:
                    lookup[variant] = tier
    return lookup


def _resolve_tier(row: dict[str, Any], lookup: dict[str, int]) -> str:
    raw = str(row.get("tier") or "").strip()
    if raw in {"1", "2", "3"}:
        return raw
    for key in (
        row.get("ticker_or_code") or "",
        row.get("company") or "",
    ):
        k = str(key).strip()
        if k in lookup:
            return str(lookup[k])
        if k.upper() in lookup:
            return str(lookup[k.upper()])
    return "1"


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
    org_id: str = "",
    user_id: str = "",
    columns: list[str] | None = None,
) -> dict[str, str]:
    cols = columns or (ORG_VOTE_COLUMNS if org_id else VOTE_COLUMNS)
    row = {
        "vote_id": str(uuid.uuid4()),
        "signal_id": signal_id,
        "user": user,
        "vote": str(vote),
        "voted_at": datetime.now(timezone.utc).isoformat(),
    }
    if "org_id" in cols:
        row["org_id"] = org_id
    if "user_id" in cols:
        row["user_id"] = user_id
    votes_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = votes_path.with_suffix(votes_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        write_header = not votes_path.is_file() or votes_path.stat().st_size == 0
        with votes_path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in cols})
            fh.flush()
    _CSV.invalidate(votes_path)
    _DERIVED.invalidate_prefix("tallies:")
    return row


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


def create_app(
    *,
    signals_path: Path | None = None,
    sightings_path: Path | None = None,
    votes_path: Path | None = None,
    companies_path: Path | None = None,
    tiers_path: Path | None = None,
    static_dir: Path | None = None,
    mount_static: bool = True,
    mode: str | None = None,
    orgs_root: Path | None = None,
    aggregates_path: Path | None = None,
    session_resolver: SessionResolver | None = None,
    operator_token: str | None = None,
    broker_sim_token: str | None = None,
    broker_sim_open: bool | None = None,
) -> FastAPI:
    load_dotenv()
    signals_path = signals_path or DEFAULT_SIGNALS
    sightings_path = sightings_path or DEFAULT_SIGHTINGS
    votes_path = votes_path or DEFAULT_VOTES
    companies_path = companies_path or DEFAULT_COMPANIES
    tiers_path = tiers_path or DEFAULT_TIERS
    static_dir = static_dir or STATIC_DIR
    orgs_root = orgs_root or DEFAULT_ORGS_ROOT
    aggregates_path = aggregates_path or DEFAULT_AGGREGATES
    tier_lookup = _company_tier_lookup(companies_path)
    tier_catalog = _load_tier_catalog(tiers_path)
    mode = (mode or auth_mode()).lower()
    if mode not in {"single", "clerk"}:
        mode = "single"
    op_token = (
        operator_token
        if operator_token is not None
        else (os.environ.get("OPERATOR_TOKEN") or "").strip()
    )
    sim_token = (
        broker_sim_token
        if broker_sim_token is not None
        else (os.environ.get("BROKER_SIM_TOKEN") or "").strip()
    )
    sim_open = (
        bool(broker_sim_open) if broker_sim_open is not None else _env_flag("BROKER_SIM_OPEN")
    )

    vote_limiter = RateLimiter(max_calls=60, window_seconds=60.0)
    bootstrap_limiter = RateLimiter(max_calls=10, window_seconds=60.0)
    broker_sim_limiter = RateLimiter(max_calls=10, window_seconds=60.0)
    translate_limiter = RateLimiter(max_calls=30, window_seconds=60.0)
    # Per-client read budgets (not a shared pool) — 10 users stay under the ceiling.
    signals_read_limiter = RateLimiter(
        max_calls=SIGNALS_READ_MAX, window_seconds=SIGNALS_READ_WINDOW_S
    )
    signals_build_gate = ConcurrencyGate(SIGNALS_BUILD_CONCURRENCY)

    app = FastAPI(title="EC Signals", version="0.4.0")
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(_StaticCacheMiddleware)
    app.state.auth_mode = mode
    app.state.orgs_root = orgs_root

    def resolve_session(
        authorization: str | None = Header(default=None),
    ) -> ClerkSession:
        if session_resolver is not None:
            return session_resolver(_bearer_token(authorization))
        if not clerk_configured():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Clerk is not configured. Set CLERK_PUBLISHABLE_KEY, "
                    "CLERK_SECRET_KEY, and CLERK_ISSUER in .env "
                    "(see .env.example and docs/MULTI-ORG-DEMO.md)."
                ),
            )
        token = _bearer_token(authorization)
        try:
            return verify_clerk_session_token(token or "")
        except ClerkConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    def require_clerk_session(
        session: ClerkSession = Depends(resolve_session),
    ) -> ClerkSession:
        return session

    def org_corpus(session: ClerkSession) -> dict[str, Path]:
        try:
            return ensure_org_corpus(
                session.org_id,
                orgs_root=orgs_root,
                base_signals=signals_path,
                base_sightings=sightings_path,
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return {
            "auth_mode": mode,
            "clerk_configured": clerk_configured() if mode == "clerk" else False,
            "clerk_publishable_key": clerk_publishable_key() if mode == "clerk" else "",
            "clerk_issuer": clerk_issuer() if mode == "clerk" else "",
            "authorized_parties": clerk_authorized_parties() if mode == "clerk" else [],
            "requires_org": mode == "clerk",
        }

    @app.get("/api/me")
    def get_me(session: ClerkSession = Depends(require_clerk_session)) -> dict[str, Any]:
        if mode != "clerk":
            raise HTTPException(status_code=404, detail="clerk mode only")
        return {
            "user_id": session.user_id,
            "org_id": session.org_id,
            "org_role": session.org_role,
            "org_slug": session.org_slug,
            "display_name": session.display_name,
        }

    @app.post("/api/orgs/bootstrap")
    def bootstrap_org(
        request: Request,
        session: ClerkSession = Depends(require_clerk_session),
    ) -> dict[str, Any]:
        """Seed this org's private corpus copy (idempotent)."""
        if mode != "clerk":
            raise HTTPException(status_code=404, detail="clerk mode only")
        ip = request.client.host if request.client else "unknown"
        if not bootstrap_limiter.allow(f"boot:{session.user_id}:{ip}"):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        paths = org_corpus(session)

        def _rel(p: Path) -> str:
            try:
                return str(p.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
            except ValueError:
                return str(p)

        return {
            "org_id": session.org_id,
            "signals": _rel(paths["signals"]),
            "sightings": _rel(paths["sightings"]),
            "votes": _rel(paths["votes"]),
            "seeded": True,
        }

    def _signals_payload(
        sig_path: Path,
        sight_path: Path,
        votes_file: Path,
        *,
        user_key: str,
        sightings_mode: SightingsMode = "meta",
    ) -> list[dict[str, Any]]:
        signals = _read_csv(sig_path)
        fp_sight = _CSV.fingerprint(sight_path)
        if sightings_mode == "full":
            fields = SIGHTING_API_FIELDS
        elif sightings_mode == "meta":
            fields = SIGHTING_META_FIELDS
        else:
            fields = ()
        if fields:
            sightings = _DERIVED.get_or_build(
                f"sight:{sight_path}:{sightings_mode}",
                fp_sight,
                lambda: _sightings_by_signal(_read_csv(sight_path), fields=fields),
            )
        else:
            sightings = {}
        tallies = _DERIVED.get_or_build(
            f"tallies:{votes_file}:{user_key}",
            _CSV.fingerprint(votes_file),
            lambda: _vote_tallies(_read_csv(votes_file), user_key=user_key),
        )
        out: list[dict[str, Any]] = []
        for row in signals:
            sid = row.get("signal_id") or ""
            payload = dict(row)
            payload["tier"] = _resolve_tier(row, tier_lookup)
            if sightings_mode != "none":
                payload["sightings"] = sightings.get(sid, [])
            else:
                payload["sightings"] = []
            payload["votes"] = tallies.get(sid, {"up": 0, "down": 0})
            out.append(payload)
        return out

    def _client_key(request: Request, authorization: str | None) -> str:
        ip = request.client.host if request.client else "unknown"
        if mode == "clerk" and authorization:
            try:
                session = resolve_session(authorization)
                return f"{session.org_id}:{session.user_id}:{ip}"
            except HTTPException:
                return f"anon:{ip}"
        return f"anon:{ip}"

    def _maybe_not_modified(
        request: Request, etag: str
    ) -> Response | None:
        inm = request.headers.get("if-none-match")
        if inm and etag in {p.strip() for p in inm.split(",")}:
            return Response(status_code=304, headers={"ETag": etag})
        return None

    @app.get("/api/signals")
    async def get_signals(
        request: Request,
        response: Response,
        authorization: str | None = Header(default=None),
        sightings: SightingsMode = Query(
            default="meta",
            description=(
                "full = nested sightings with excerpts; "
                "meta = dates/forms/urls only (default); "
                "none = omit nested sightings"
            ),
        ),
    ) -> Any:
        ck = _client_key(request, authorization)
        if not signals_read_limiter.allow(f"sig:{ck}"):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        signals_read_limiter.prune()

        if mode == "clerk":
            session = resolve_session(authorization)
            paths = org_corpus(session)
            sig_p, sight_p, votes_p = (
                paths["signals"],
                paths["sightings"],
                paths["votes"],
            )
            user_key = "user_id"
        else:
            sig_p, sight_p, votes_p = signals_path, sightings_path, votes_path
            user_key = "user"

        etag = _etag_for(sig_p, sight_p, votes_p, extra=f"sightings={sightings}")
        not_mod = _maybe_not_modified(request, etag)
        if not_mod is not None:
            return not_mod

        async with signals_build_gate:
            payload = await asyncio.to_thread(
                _signals_payload,
                sig_p,
                sight_p,
                votes_p,
                user_key=user_key,
                sightings_mode=sightings,
            )
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "private, no-cache"
        return payload

    @app.get("/api/signals/{signal_id}/sightings")
    async def get_signal_sightings(
        signal_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Full sightings (with excerpts) for one signal — card expand path."""
        ck = _client_key(request, authorization)
        if not signals_read_limiter.allow(f"sight:{ck}"):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        if mode == "clerk":
            session = resolve_session(authorization)
            paths = org_corpus(session)
            sig_p, sight_p = paths["signals"], paths["sightings"]
        else:
            sig_p, sight_p = signals_path, sightings_path
        if signal_id not in _known_signal_ids(sig_p):
            raise HTTPException(status_code=404, detail="unknown signal_id")

        def _load() -> list[dict[str, str]]:
            by = _sightings_by_signal(_read_csv(sight_p), fields=SIGHTING_API_FIELDS)
            return by.get(signal_id, [])

        rows = await asyncio.to_thread(_load)
        return {"signal_id": signal_id, "sightings": rows}

    @app.get("/api/meta")
    async def get_meta(
        request: Request,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> Any:
        """Coverage notes for the Signal map (companies.yaml + signals.csv)."""
        if mode == "clerk":
            session = resolve_session(authorization)
            paths = org_corpus(session)
            sig_p = paths["signals"]
        else:
            sig_p = signals_path
        etag = _etag_for(sig_p, companies_path, tiers_path, extra="meta")
        not_mod = _maybe_not_modified(request, etag)
        if not_mod is not None:
            return not_mod

        def _build() -> dict[str, Any]:
            signals = _read_csv(sig_p)
            names = _load_company_names(companies_path)
            notes = compute_notes(signals, names)
            companies = _load_yaml_mapping(companies_path).get("companies") or []
            roster: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in companies:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "")
                ticker = str(row.get("ticker") or "")
                key = f"{name}|{ticker}"
                if not name or key in seen:
                    continue
                seen.add(key)
                try:
                    tier = int(row.get("tier", 1))
                except (TypeError, ValueError):
                    tier = 1
                roster.append(
                    {
                        "name": name,
                        "ticker": ticker,
                        "role": row.get("role") or "",
                        "source": row.get("source") or "",
                        "tier": tier if tier in {1, 2, 3} else 1,
                    }
                )
            default_map_tier = next(
                (t["id"] for t in tier_catalog if t.get("map_default")), 1
            )
            return {
                "notes": notes,
                "companies": names,
                "tiers": tier_catalog,
                "company_roster": roster,
                "default_map_tier": default_map_tier,
            }

        body = await asyncio.to_thread(_build)
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "private, no-cache"
        return body

    def _tally_for_signal(
        votes_file: Path, signal_id: str, *, user_key: str
    ) -> dict[str, int]:
        tallies = _vote_tallies(_read_csv(votes_file), user_key=user_key)
        return tallies.get(signal_id, {"up": 0, "down": 0})

    @app.get("/api/votes")
    async def get_votes(
        user: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ) -> dict[str, int]:
        """Latest vote per signal_id for this user. Values are 1, -1, or 0 (skip)."""

        def _load_clerk(session: ClerkSession) -> dict[str, int]:
            paths = org_corpus(session)
            latest = _latest_votes_by_user_signal(
                _read_csv(paths["votes"]), user_key="user_id"
            )
            result: dict[str, int] = {}
            for (uid, sid), row in latest.items():
                if uid != session.user_id or not sid:
                    continue
                # Hard isolation: ignore rows that somehow lack/mismatch org_id
                if (row.get("org_id") or "") != session.org_id:
                    continue
                try:
                    result[sid] = int(row["vote"])
                except (KeyError, ValueError):
                    continue
            return result

        def _load_single(name: str) -> dict[str, int]:
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

        if mode == "clerk":
            session = resolve_session(authorization)
            return await asyncio.to_thread(_load_clerk, session)

        name = user.strip()
        if not name:
            raise HTTPException(status_code=400, detail="user is required")
        return await asyncio.to_thread(_load_single, name)

    @app.post("/api/votes")
    async def post_vote(
        body: VoteIn,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        def _finish(
            votes_file: Path,
            *,
            user_key: str,
            row: dict[str, str],
        ) -> dict[str, Any]:
            tallies = _tally_for_signal(
                votes_file, body.signal_id, user_key=user_key
            )
            out: dict[str, Any] = dict(row)
            out["votes"] = tallies
            return out

        if mode == "clerk":
            session = resolve_session(authorization)
            ip = request.client.host if request.client else "unknown"
            if not vote_limiter.allow(f"vote:{session.org_id}:{session.user_id}:{ip}"):
                raise HTTPException(status_code=429, detail="rate limit exceeded")
            paths = org_corpus(session)

            def _clerk_vote() -> dict[str, Any]:
                if body.signal_id not in _known_signal_ids(paths["signals"]):
                    raise HTTPException(status_code=400, detail="unknown signal_id")
                direction = ""
                for crow in _read_csv(paths["signals"]):
                    if crow.get("signal_id") == body.signal_id:
                        direction = crow.get("supply_direction") or ""
                        break
                row = _append_vote_locked(
                    paths["votes"],
                    signal_id=body.signal_id,
                    user=session.display_name,
                    vote=body.vote,
                    org_id=session.org_id,
                    user_id=session.user_id,
                    columns=ORG_VOTE_COLUMNS,
                )
                # Skips are per-profile UX only — omit from anonymized usefulness aggregates.
                if body.vote in (1, -1):
                    append_aggregate_event(
                        aggregates_path,
                        signal_id=body.signal_id,
                        direction=direction,
                        vote=body.vote,
                    )
                return _finish(paths["votes"], user_key="user_id", row=row)

            return await asyncio.to_thread(_clerk_vote)

        user = body.user.strip()
        if not user:
            raise HTTPException(status_code=400, detail="user is required")

        def _single_vote() -> dict[str, Any]:
            if body.signal_id not in _known_signal_ids(signals_path):
                raise HTTPException(status_code=400, detail="unknown signal_id")
            row = _append_vote_locked(
                votes_path,
                signal_id=body.signal_id,
                user=user,
                vote=body.vote,
            )
            return _finish(votes_path, user_key="user", row=row)

        return await asyncio.to_thread(_single_vote)

    def _translate_signal_row(sig_path: Path, signal_id: str) -> dict[str, Any]:
        """Return English excerpt for a non-EN signal; persist when newly translated."""
        from src.dedupe import SIGNAL_COLUMNS
        from src.translate_excerpts import (
            display_lang,
            is_english,
            persist_excerpt_en,
            translate_excerpt,
        )

        rows = _read_csv(sig_path)
        row = next((r for r in rows if (r.get("signal_id") or "") == signal_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown signal_id")
        lang = row.get("language") or "en"
        if is_english(lang):
            raise HTTPException(
                status_code=400,
                detail="English signals have no translation toggle",
            )
        existing = (row.get("excerpt_en") or "").strip()
        if existing:
            return {
                "signal_id": signal_id,
                "language": lang,
                "language_label": display_lang(lang),
                "excerpt_en": existing,
                "cached": True,
            }
        excerpt = (row.get("excerpt") or "").strip()
        if not excerpt:
            raise HTTPException(status_code=400, detail="empty excerpt")
        try:
            excerpt_en = translate_excerpt(excerpt, lang, install=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SystemExit as exc:
            raise HTTPException(
                status_code=503,
                detail=str(exc) or "translation package unavailable",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"translation failed: {exc}",
            ) from exc
        if not excerpt_en:
            raise HTTPException(status_code=503, detail="translation returned empty")
        lock_path = sig_path.with_suffix(sig_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            # Re-check under lock in case a concurrent request filled it.
            again = _read_csv(sig_path)
            current = next(
                (r for r in again if (r.get("signal_id") or "") == signal_id),
                None,
            )
            if current is not None and (current.get("excerpt_en") or "").strip():
                excerpt_en = (current.get("excerpt_en") or "").strip()
            else:
                persist_excerpt_en(
                    sig_path,
                    signal_id,
                    excerpt_en,
                    columns=list(SIGNAL_COLUMNS),
                )
                _CSV.invalidate(sig_path)
        return {
            "signal_id": signal_id,
            "language": lang,
            "language_label": display_lang(lang),
            "excerpt_en": excerpt_en,
            "cached": False,
        }

    @app.post("/api/translate")
    def post_translate(
        body: TranslateIn,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Fill or return excerpt_en for a non-English signal (Argos offline MT)."""
        ip = request.client.host if request.client else "unknown"
        if mode == "clerk":
            session = resolve_session(authorization)
            if not translate_limiter.allow(
                f"tr:{session.org_id}:{session.user_id}:{ip}"
            ):
                raise HTTPException(status_code=429, detail="rate limit exceeded")
            paths = org_corpus(session)
            return _translate_signal_row(paths["signals"], body.signal_id.strip())

        if not translate_limiter.allow(f"tr:anon:{ip}"):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        return _translate_signal_row(signals_path, body.signal_id.strip())

    @app.get("/api/operator/aggregates")
    def operator_aggregates(
        x_operator_token: str | None = Header(default=None),
        limit: int = Query(default=200, ge=1, le=5000),
    ) -> dict[str, Any]:
        """Anonymized vote events across orgs (operator only)."""
        if not op_token:
            raise HTTPException(
                status_code=503,
                detail="OPERATOR_TOKEN not configured on server",
            )
        if not x_operator_token or x_operator_token != op_token:
            raise HTTPException(status_code=401, detail="invalid operator token")
        events = read_aggregate_events(aggregates_path, limit=limit)
        return {"events": events, "count": len(events)}

    def _authorize_broker_sim(
        request: Request,
        authorization: str | None,
        x_broker_sim_token: str | None,
    ) -> tuple[str, bool]:
        """Return (rate_limit_key, allow_expensive) after auth gates.

        allow_expensive=True → live prices and full BROKER_SIM_MAX_DAYS.
        Open single-tenant demo forces offline + shorter days.
        """
        ip = request.client.host if request.client else "unknown"
        if mode == "clerk":
            session = resolve_session(authorization)
            return f"broker:{session.org_id}:{session.user_id}:{ip}", True

        provided = (x_broker_sim_token or "").strip()
        if sim_token:
            # compare_digest requires equal-length strings; mismatch → 401, not 500.
            ok = len(provided) == len(sim_token) and secrets.compare_digest(
                provided, sim_token
            )
            if not ok:
                raise HTTPException(status_code=401, detail="invalid broker sim token")
            return f"broker:token:{ip}", True
        if sim_open:
            return f"broker:open:{ip}", False
        raise HTTPException(
            status_code=503,
            detail=(
                "Broker sim disabled in single mode. Set BROKER_SIM_OPEN=1 for a "
                "local offline demo, or BROKER_SIM_TOKEN and send X-Broker-Sim-Token."
            ),
        )

    @app.get("/api/broker-sim")
    async def get_broker_sim(
        request: Request,
        days: int = Query(30, ge=1, le=BROKER_SIM_MAX_DAYS),
        seed: int = Query(42),
        live: bool = Query(
            False, description="Prefer MemoryIndex live/public prices (auth required)"
        ),
        starting_cash: float = Query(250_000.0, gt=0),
        include_scored: bool = Query(
            True, description="Include scored twins with fast memory-score access"
        ),
        score_lag: int = Query(1, ge=0, le=30, description="Score lag days for twins"),
        authorization: str | None = Header(default=None),
        x_broker_sim_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Run the broker market sim (baseline ± scored twins) + history.

        Clerk mode requires a session JWT. Single mode requires BROKER_SIM_TOKEN
        (X-Broker-Sim-Token) or an explicit BROKER_SIM_OPEN=1 local demo gate.
        Work runs off the event loop via asyncio.to_thread.
        """
        rate_key, allow_expensive = _authorize_broker_sim(
            request, authorization, x_broker_sim_token
        )
        if not broker_sim_limiter.allow(rate_key):
            raise HTTPException(status_code=429, detail="rate limit exceeded")

        effective_days = days if allow_expensive else min(days, BROKER_SIM_OPEN_MAX_DAYS)
        effective_live = bool(live) if allow_expensive else False

        from dram_signals.broker_sim.simulation import run_simulation

        result = await asyncio.to_thread(
            run_simulation,
            days=effective_days,
            seed=seed,
            starting_cash=starting_cash,
            companies_path=companies_path,
            prefer_live_prices=effective_live,
            include_scored=include_scored,
            score_lag_days=score_lag,
        )
        return result.summary()

    if mount_static and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
