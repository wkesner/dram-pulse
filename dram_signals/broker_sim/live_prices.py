"""Live / public DRAM spot context for the broker simulation.

Primary source (no API key required for a free slice):

* ``GET https://memoryindex.io/api/public/v1/prices``
  — MemoryIndex public board (10 contracts without a key; delayed one session).
  License string from the payload: free tier is **internal use only**.
  Attribution: ``Data: MemoryIndex.io``; many rows cite DRAMeXchange / TrendForce
  or Silicon Analysts in ``source`` / ``source_url``.

Optional:

* ``MEMORYINDEX_KEY`` / ``MEMORYINDEX_API_KEY`` — bearer token for a larger board
  if William provisions one (same endpoint).

Not used here (limits):

* DRAMeXchange HTML scrape — ToS-bound; repo policy keeps unlicensed index rows
  internal-only for scoring; we prefer the MemoryIndex JSON board instead.
* Ornn / DigiKey / Nexar — need paid keys; DigiKey/Nexar are already used for
  part-master lifecycle, not commodity spot.

Fallback chain:

1. Live MemoryIndex HTTP fetch
2. On-disk cache under ``data/broker_sim/memoryindex_cache.json`` (last good)
3. Illustrative technology×density×grade model in ``catalog.py``

Family die spots are mapped onto seed SKUs; grade / lifecycle multipliers from
the illustrative model still scale the die mid (consumer = 1.0×).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from dram_signals.broker_sim.catalog import Product, _GRADE_MULT, _LIFECYCLE_MULT

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = REPO_ROOT / "data" / "broker_sim" / "memoryindex_cache.json"
MEMORYINDEX_URL = "https://memoryindex.io/api/public/v1/prices"
DEFAULT_TIMEOUT = 20.0


@dataclass(frozen=True)
class SpotQuote:
    """One commodity spot reading (usually USD / die)."""

    contract_id: str
    ticker: str
    name: str
    segment: str
    spot_usd: float
    unit: str
    as_of: Optional[str] = None
    chg_24h_pct: Optional[float] = None
    chg_30d_pct: Optional[float] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    basis: Optional[str] = None


@dataclass
class PriceBundle:
    """Resolved per-SKU mids plus provenance for the run."""

    prices: dict[str, float]
    quotes: list[SpotQuote] = field(default_factory=list)
    source: str = "illustrative"
    detail: str = ""
    session_utc: Optional[str] = None
    license_note: Optional[str] = None
    attribution: Optional[str] = None
    fetched_at: Optional[str] = None
    matched_skus: int = 0
    unmatched_skus: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "detail": self.detail,
            "session_utc": self.session_utc,
            "license_note": self.license_note,
            "attribution": self.attribution,
            "fetched_at": self.fetched_at,
            "matched_skus": self.matched_skus,
            "unmatched_skus": self.unmatched_skus,
            "quotes": [
                {
                    "contract_id": q.contract_id,
                    "ticker": q.ticker,
                    "name": q.name,
                    "segment": q.segment,
                    "spot_usd": q.spot_usd,
                    "unit": q.unit,
                    "as_of": q.as_of,
                    "chg_24h_pct": q.chg_24h_pct,
                    "chg_30d_pct": q.chg_30d_pct,
                    "source": q.source,
                    "source_url": q.source_url,
                }
                for q in self.quotes
            ],
            "sku_prices": {k: round(v, 4) for k, v in self.prices.items()},
        }


def _api_key() -> Optional[str]:
    return (
        os.environ.get("MEMORYINDEX_KEY")
        or os.environ.get("MEMORYINDEX_API_KEY")
        or None
    )


def _density_gb(density: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*[Gg]b", density or "")
    return int(m.group(1)) if m else None


def _normalize_tech(technology: str) -> str:
    t = (technology or "").upper().replace(" ", "")
    for key in ("HBM3E", "HBM3", "HBM4", "HBM", "DDR5", "DDR4", "GDDR7", "GDDR6", "GDDR", "LPDDR"):
        if key in t:
            return key
    return t or "DDR4"


def parse_memoryindex_payload(payload: dict[str, Any]) -> tuple[list[SpotQuote], dict[str, Any]]:
    """Parse MemoryIndex JSON into SpotQuote rows + meta."""
    quotes: list[SpotQuote] = []
    for row in payload.get("memory") or []:
        try:
            spot = float(row["spot_usd"])
        except (KeyError, TypeError, ValueError):
            continue
        quotes.append(
            SpotQuote(
                contract_id=str(row.get("id") or ""),
                ticker=str(row.get("ticker") or ""),
                name=str(row.get("name") or ""),
                segment=str(row.get("segment") or ""),
                spot_usd=spot,
                unit=str(row.get("unit") or "USD"),
                as_of=str(row["as_of"]) if row.get("as_of") is not None else None,
                chg_24h_pct=_maybe_float(row.get("chg_24h_pct")),
                chg_30d_pct=_maybe_float(row.get("chg_30d_pct")),
                source=str(row["source"]) if row.get("source") is not None else None,
                source_url=str(row["source_url"]) if row.get("source_url") is not None else None,
                basis=str(row["basis"]) if row.get("basis") is not None else None,
            )
        )
    meta = {
        "session_utc": payload.get("session_utc"),
        "license": payload.get("license"),
        "attribution": payload.get("attribution"),
        "delayed_sessions": payload.get("delayed_sessions"),
        "counts": payload.get("counts"),
    }
    return quotes, meta


def _maybe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_memoryindex(
    *,
    url: str = MEMORYINDEX_URL,
    timeout: float = DEFAULT_TIMEOUT,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    """HTTP GET the MemoryIndex public prices board."""
    headers = {
        "User-Agent": "dram-pulse-broker-sim/0.1 (+https://github.com/william-kesner/dram-pulse)",
        "Accept": "application/json",
    }
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    sess = session or requests.Session()
    resp = sess.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("MemoryIndex response was not a JSON object")
    return data


def load_cache(path: Path = DEFAULT_CACHE_PATH) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("broker_sim price cache unreadable: %s", exc)
        return None


def save_cache(payload: dict[str, Any], path: Path = DEFAULT_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")


def match_quote_for_product(product: Product, quotes: list[SpotQuote]) -> Optional[SpotQuote]:
    """Best commodity contract for a catalog SKU (tech + density; die/stack only)."""
    tech = _normalize_tech(product.technology)
    gb = _density_gb(product.density)
    die_quotes = [
        q
        for q in quotes
        if "die" in (q.unit or "").lower()
        or "stack" in (q.unit or "").lower()
        or q.segment.upper() in {"DRAM", "HBM"}
    ]
    # Prefer die / stack over module / drive.
    pool = die_quotes or quotes

    def score(q: SpotQuote) -> tuple[int, int]:
        blob = f"{q.contract_id} {q.ticker} {q.name}".upper()
        tech_hit = 1 if tech and tech in blob.replace(" ", "") else 0
        # HBM special-case: any HBM* matches HBM family
        if tech.startswith("HBM") and "HBM" in blob:
            tech_hit = 1
        dens_hit = 0
        if gb is not None:
            if re.search(rf"(?:^|[^0-9]){gb}\s*G(?:B)?(?:[^A-Z0-9]|$)", blob):
                dens_hit = 2
            elif f"{gb}G" in blob.replace(" ", ""):
                dens_hit = 2
        # Penalize eTT / module / drive when we want finished consumer die.
        penalty = 0
        if "ETT" in blob:
            penalty -= 1
        if "MODULE" in (q.unit or "").upper() or "RDIMM" in blob or "DIMM" in blob:
            penalty -= 2
        if "DRIVE" in (q.unit or "").upper() or "SSD" in blob:
            penalty -= 3
        return (tech_hit + dens_hit + penalty, tech_hit)

    ranked = sorted(pool, key=score, reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    s = score(best)
    if s[1] < 1:
        return None
    # Require density match when we know density and tech is DDR*
    if tech.startswith("DDR") and gb is not None and s[0] < 3:
        return None
    return best


def apply_grade_lifecycle(die_mid: float, *, grade: str, lifecycle_status: str) -> float:
    g = _GRADE_MULT.get((grade or "unknown").lower(), 1.10)
    life = _LIFECYCLE_MULT.get((lifecycle_status or "active").lower(), 1.0)
    return round(die_mid * g * life, 4)


def prices_from_quotes(
    products: list[Product],
    quotes: list[SpotQuote],
    *,
    meta: Optional[dict[str, Any]] = None,
    source: str = "memoryindex",
    detail: str = "",
) -> PriceBundle:
    meta = meta or {}
    prices: dict[str, float] = {}
    matched = 0
    used_quotes: dict[str, SpotQuote] = {}
    for product in products:
        quote = match_quote_for_product(product, quotes)
        if quote is None:
            prices[product.sku] = product.base_market_price
            continue
        prices[product.sku] = apply_grade_lifecycle(
            quote.spot_usd,
            grade=product.grade,
            lifecycle_status=product.lifecycle_status,
        )
        matched += 1
        used_quotes[quote.contract_id] = quote
    unmatched = len(products) - matched
    if matched == 0:
        source = "illustrative"
        detail = detail or "no MemoryIndex contracts matched seed SKUs"
    return PriceBundle(
        prices=prices,
        quotes=list(used_quotes.values()),
        source=source,
        detail=detail,
        session_utc=str(meta.get("session_utc")) if meta.get("session_utc") else None,
        license_note=str(meta.get("license")) if meta.get("license") else None,
        attribution=str(meta.get("attribution")) if meta.get("attribution") else None,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        matched_skus=matched,
        unmatched_skus=unmatched,
    )


def illustrative_bundle(products: list[Product]) -> PriceBundle:
    return PriceBundle(
        prices={p.sku: p.base_market_price for p in products},
        quotes=[],
        source="illustrative",
        detail=(
            "technology × density × grade × lifecycle model "
            "(see dram_signals.broker_sim.catalog)"
        ),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        matched_skus=0,
        unmatched_skus=len(products),
    )


def resolve_market_prices(
    products: list[Product],
    *,
    prefer_live: bool = True,
    cache_path: Path = DEFAULT_CACHE_PATH,
    fixture_payload: Optional[dict[str, Any]] = None,
    session: Optional[requests.Session] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> PriceBundle:
    """Resolve per-SKU mids: live → cache → illustrative."""
    if not prefer_live and fixture_payload is None:
        return illustrative_bundle(products)

    payload: Optional[dict[str, Any]] = fixture_payload
    detail = ""
    source = "memoryindex"

    if payload is None:
        try:
            payload = fetch_memoryindex(timeout=timeout, session=session)
            try:
                save_cache(payload, cache_path)
            except OSError as exc:
                logger.warning("could not write price cache: %s", exc)
            detail = f"live fetch {MEMORYINDEX_URL}"
            source = "memoryindex_live"
        except (requests.RequestException, OSError, ValueError, json.JSONDecodeError) as exc:
            # OSError covers builtin ConnectionError / timeout when mocked offline.
            logger.info("MemoryIndex live fetch failed (%s); trying cache", exc)
            cached = load_cache(cache_path)
            if cached and isinstance(cached.get("payload"), dict):
                payload = cached["payload"]
                detail = (
                    f"cache fallback after live error: {exc}; "
                    f"cached_at={cached.get('cached_at')}"
                )
                source = "memoryindex_cache"
            else:
                bundle = illustrative_bundle(products)
                bundle.detail = f"live+cache unavailable ({exc}); {bundle.detail}"
                return bundle

    quotes, meta = parse_memoryindex_payload(payload)
    if not quotes:
        bundle = illustrative_bundle(products)
        bundle.detail = "MemoryIndex payload had no usable memory quotes; " + bundle.detail
        return bundle

    return prices_from_quotes(
        products,
        quotes,
        meta=meta,
        source=source,
        detail=detail or "MemoryIndex board",
    )
