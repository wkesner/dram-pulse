"""Shared DRAM product catalog and illustrative market prices.

* SKUs come from ``data/seed_catalog.json`` (manufacturer family variants).
* ``illustrative_market_price`` is the **offline fallback** when live/
  cached MemoryIndex quotes are unavailable (see ``live_prices``):
  - Base by technology: DDR4 → $3.20, else $4.00 (illustrative).
  - Density scale: parse Gb from density string (8Gb → 1.0×, 16Gb → 1.8×).
  - Grade premium: consumer 1.0×, industrial 1.25×, automotive 1.55×,
    military 1.9×, unknown 1.1×.
  - NRND / obsolete lifecycle softens price by 15% / 35%.
* When live quotes resolve, the simulator uses those die mids × the same
  grade/lifecycle multipliers. Prices are shared across brokers; daily
  drift is applied in the simulator around the chosen bases.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_CATALOG = REPO_ROOT / "data" / "seed_catalog.json"

_TECH_BASE = {
    "DDR4": 3.20,
    "DDR5": 5.50,
    "LPDDR": 4.00,
    "LPDDR4": 3.80,
    "LPDDR5": 5.00,
    "GDDR": 6.00,
    "HBM": 40.00,
}

_GRADE_MULT = {
    "consumer": 1.0,
    "industrial": 1.25,
    "automotive": 1.55,
    "military": 1.90,
    "unknown": 1.10,
}

_LIFECYCLE_MULT = {
    "active": 1.0,
    "nrnd": 0.85,
    "obsolete": 0.65,
    "eol": 0.70,
}


@dataclass(frozen=True)
class Product:
    """One tradeable DRAM SKU."""

    sku: str
    manufacturer: str
    family_code: str
    technology: str
    density: str
    grade: str
    lifecycle_status: str
    base_market_price: float

    @property
    def mpn(self) -> str:
        return self.sku


def _density_scale(density: str) -> float:
    m = re.search(r"(\d+)\s*[Gg]b", density or "")
    if not m:
        return 1.0
    gb = int(m.group(1))
    # Anchor at 8Gb.
    return max(0.5, gb / 8.0) ** 0.85


def illustrative_market_price(
    *,
    technology: str,
    density: str,
    grade: str,
    lifecycle_status: str = "active",
) -> float:
    """Deterministic USD/unit spot used as the shared market mid."""
    tech = (technology or "DDR4").upper()
    base = _TECH_BASE.get(tech, 4.00)
    for key, val in _TECH_BASE.items():
        if key in tech:
            base = val
            break
    g = _GRADE_MULT.get((grade or "unknown").lower(), 1.10)
    life = _LIFECYCLE_MULT.get((lifecycle_status or "active").lower(), 1.0)
    price = base * _density_scale(density) * g * life
    return round(price, 4)


def products_from_seed_dict(payload: dict[str, Any]) -> list[Product]:
    """Build products from an already-loaded seed_catalog structure."""
    out: list[Product] = []
    for family in payload.get("families") or []:
        manufacturer = str(family.get("manufacturer") or "unknown")
        family_code = str(family.get("family_code") or "")
        technology = str(family.get("technology") or "DDR4")
        density = str(family.get("density") or "8Gb")
        for variant in family.get("variants") or []:
            mpn = str(variant.get("mpn") or "").strip()
            if not mpn:
                continue
            grade = str(variant.get("grade") or "unknown")
            life = str(variant.get("lifecycle_status") or "active")
            price = illustrative_market_price(
                technology=technology,
                density=density,
                grade=grade,
                lifecycle_status=life,
            )
            out.append(
                Product(
                    sku=mpn,
                    manufacturer=manufacturer,
                    family_code=family_code,
                    technology=technology,
                    density=density,
                    grade=grade,
                    lifecycle_status=life,
                    base_market_price=price,
                )
            )
    return out


def load_products_from_seed(path: Optional[Path] = None) -> list[Product]:
    """Load the shared DRAM catalog from seed JSON."""
    seed_path = Path(path) if path else DEFAULT_SEED_CATALOG
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    products = products_from_seed_dict(payload)
    if not products:
        raise ValueError(f"No products found in {seed_path}")
    return products


def product_index(products: Iterable[Product]) -> dict[str, Product]:
    return {p.sku: p for p in products}
