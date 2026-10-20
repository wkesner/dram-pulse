"""Data helpers for the Memory Signals Streamlit feed (Milestone 4)."""

from __future__ import annotations

import csv
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNALS_PATH = REPO_ROOT / "data" / "signals.csv"
VOTES_PATH = REPO_ROOT / "data" / "votes.csv"

VOTE_COLUMNS = ["vote_id", "signal_id", "user", "vote", "voted_at"]


def load_signals(path: Path = SIGNALS_PATH) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_votes(path: Path = VOTES_PATH) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def append_vote(
    signal_id: str,
    user: str,
    vote: int,
    *,
    path: Path = VOTES_PATH,
) -> dict[str, str]:
    """Append one vote row (+1 or -1). Changed votes are new rows."""
    if vote not in (1, -1):
        raise ValueError("vote must be +1 or -1")
    user = user.strip()
    if not user:
        raise ValueError("user is required")
    row = {
        "vote_id": str(uuid.uuid4()),
        "signal_id": signal_id,
        "user": user,
        "vote": str(vote),
        "voted_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.is_file() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=VOTE_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return row


def latest_votes_by_user_signal(
    votes: list[dict[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Latest row per (user, signal_id) wins."""
    latest: dict[tuple[str, str], dict[str, str]] = {}
    for row in votes:
        key = (row.get("user", ""), row.get("signal_id", ""))
        prev = latest.get(key)
        if prev is None or (row.get("voted_at") or "") >= (prev.get("voted_at") or ""):
            latest[key] = row
    return latest


def vote_counts_for_signal(
    signal_id: str, votes: list[dict[str, str]]
) -> tuple[int, int]:
    """Return (thumbs_up, thumbs_down) from each user's latest vote."""
    latest = latest_votes_by_user_signal(votes)
    up = down = 0
    for (user, sid), row in latest.items():
        if sid != signal_id or not user:
            continue
        try:
            v = int(row.get("vote", 0))
        except ValueError:
            continue
        if v == 1:
            up += 1
        elif v == -1:
            down += 1
    return up, down


def user_has_voted(
    user: str, signal_id: str, votes: list[dict[str, str]]
) -> bool:
    latest = latest_votes_by_user_signal(votes)
    return (user.strip(), signal_id) in latest


def filter_signals(
    signals: list[dict[str, str]],
    *,
    product_category: str | None = None,
    product_family: str | None = None,
    supply_direction: str | None = None,
    company: str | None = None,
    role: str | None = None,
    source: str | None = None,
) -> list[dict[str, str]]:
    out = signals
    checks = {
        "product_category": product_category,
        "product_family": product_family,
        "supply_direction": supply_direction,
        "company": company,
        "role": role,
        "source": source,
    }
    for key, value in checks.items():
        if value and value != "(all)":
            out = [r for r in out if (r.get(key) or "") == value]
    return out


def _date_sort_key(date_str: str) -> tuple[int, int, int]:
    """Ascending sort of this key puts newest dates first."""
    parts = (date_str or "0000-00-00").split("-")
    try:
        y, m, d = (int(parts[0]), int(parts[1]), int(parts[2]))
        return (-y, -m, -d)
    except (ValueError, IndexError):
        return (0, 0, 0)


def order_feed(
    signals: list[dict[str, str]],
    user: str,
    votes: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Unvoted by this user first, newest filing first, repeat_of last."""
    user = user.strip()

    def key(row: dict[str, str]) -> tuple:
        voted = 1 if user and user_has_voted(user, row["signal_id"], votes) else 0
        is_repeat = 1 if (row.get("repeat_of") or "").strip() else 0
        return (voted, is_repeat, _date_sort_key(row.get("filing_date") or ""))

    return sorted(signals, key=key)


def lookback_label(signals: list[dict[str, str]]) -> str:
    if not signals:
        return "lookback n/a"
    months = signals[0].get("lookback_months") or "?"
    starts = {r.get("window_start") for r in signals if r.get("window_start")}
    ends = {r.get("window_end") for r in signals if r.get("window_end")}
    start = sorted(starts)[0] if starts else "?"
    end = sorted(ends)[-1] if ends else "?"
    return f"{months}-month window ({start} → {end})"


def family_table(signals: list[dict[str, str]]) -> list[dict[str, Any]]:
    """One row per product family with direction / role counts and latest date."""
    by_family: dict[str, list[dict[str, str]]] = {}
    for row in signals:
        fam = row.get("product_family") or "(unknown)"
        by_family.setdefault(fam, []).append(row)

    rows: list[dict[str, Any]] = []
    for family in sorted(by_family):
        group = by_family[family]
        dirs = {"tightening": 0, "easing": 0, "stable": 0, "unclear": 0}
        suppliers = buyers = 0
        latest = ""
        category = group[0].get("product_category") or ""
        for r in group:
            d = r.get("supply_direction") or "unclear"
            if d not in dirs:
                d = "unclear"
            dirs[d] += 1
            role = (r.get("role") or "").lower()
            if role == "supplier":
                suppliers += 1
            elif role == "buyer":
                buyers += 1
            fd = r.get("filing_date") or ""
            if fd > latest:
                latest = fd
        rows.append(
            {
                "product_family": family,
                "product_category": category,
                "tightening": dirs["tightening"],
                "easing": dirs["easing"],
                "stable": dirs["stable"],
                "unclear": dirs["unclear"],
                "total": len(group),
                "supplier_signals": suppliers,
                "buyer_signals": buyers,
                "latest_filing_date": latest,
            }
        )
    return rows


def parse_matched_terms(matched_terms: str) -> list[str]:
    """Extract term strings from 'product:X; topic:Y; dir:Z'."""
    terms: list[str] = []
    for part in (matched_terms or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            terms.append(part.split(":", 1)[1].strip())
        else:
            terms.append(part)
    terms.sort(key=len, reverse=True)
    return [t for t in terms if t]


def highlight_excerpt(excerpt: str, matched_terms: str) -> str:
    """Return HTML with matched terms wrapped in <mark>."""
    terms = parse_matched_terms(matched_terms)
    if not terms:
        return _escape(excerpt)

    pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    out: list[str] = []
    last = 0
    for m in pattern.finditer(excerpt):
        out.append(_escape(excerpt[last : m.start()]))
        out.append(f"<mark>{_escape(m.group(0))}</mark>")
        last = m.end()
    out.append(_escape(excerpt[last:]))
    return "".join(out)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
