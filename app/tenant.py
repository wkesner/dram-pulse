"""Org-scoped corpus seeding and anonymized aggregate events."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_SIGNALS = REPO_ROOT / "data" / "signals.csv"
DEFAULT_BASE_SIGHTINGS = REPO_ROOT / "data" / "sightings.csv"
DEFAULT_ORGS_ROOT = REPO_ROOT / "data" / "orgs"
DEFAULT_AGGREGATES = REPO_ROOT / "data" / "aggregates" / "events.jsonl"

_ORG_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def assert_safe_org_id(org_id: str) -> str:
    oid = (org_id or "").strip()
    if not oid or not _ORG_ID_RE.match(oid):
        raise ValueError("invalid org_id")
    return oid


def org_dir(orgs_root: Path, org_id: str) -> Path:
    return orgs_root / assert_safe_org_id(org_id)


def org_paths(orgs_root: Path, org_id: str) -> dict[str, Path]:
    root = org_dir(orgs_root, org_id)
    return {
        "root": root,
        "signals": root / "signals.csv",
        "sightings": root / "sightings.csv",
        "votes": root / "votes.csv",
    }


def ensure_org_corpus(
    org_id: str,
    *,
    orgs_root: Path = DEFAULT_ORGS_ROOT,
    base_signals: Path = DEFAULT_BASE_SIGNALS,
    base_sightings: Path = DEFAULT_BASE_SIGHTINGS,
) -> dict[str, Path]:
    """
    Copy the base Memory Signals corpus into data/orgs/{org_id}/ on first use.
    Idempotent: existing files are left alone.
    """
    paths = org_paths(orgs_root, org_id)
    paths["root"].mkdir(parents=True, exist_ok=True)
    if not paths["signals"].is_file():
        if not base_signals.is_file():
            raise FileNotFoundError(f"base signals missing: {base_signals}")
        shutil.copy2(base_signals, paths["signals"])
    if not paths["sightings"].is_file():
        if not base_sightings.is_file():
            raise FileNotFoundError(f"base sightings missing: {base_sightings}")
        shutil.copy2(base_sightings, paths["sightings"])
    if not paths["votes"].is_file():
        paths["votes"].write_text(
            "vote_id,org_id,user_id,user,signal_id,vote,voted_at\n",
            encoding="utf-8",
        )
    return paths


def signal_fingerprint(signal_id: str, direction: str = "") -> str:
    """Stable anonymized id — not reversible to org or user."""
    payload = f"{signal_id}|{direction or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def append_aggregate_event(
    path: Path,
    *,
    signal_id: str,
    direction: str,
    vote: int,
) -> dict[str, Any]:
    """Append one anonymized event (no org_id / user_id / email)."""
    event = {
        "signal_fingerprint": signal_fingerprint(signal_id, direction),
        "direction": direction or "",
        "vote": int(vote),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")
    return event


def read_aggregate_events(path: Path, *, limit: int = 500) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
