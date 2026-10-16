"""Map signal rows to stable Airtable Company Keys."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

# Micron files 8-Ks under this CIK — fold into vendor:micron
MICRON_CIK = "0000723125"

_VENDOR_NAMES: dict[str, str] = {
    "samsung": "vendor:samsung",
    "samsung electronics": "vendor:samsung",
    "samsung semiconductor": "vendor:samsung",
    "sk hynix": "vendor:skhynix",
    "skhynix": "vendor:skhynix",
    "sk-hynix": "vendor:skhynix",
    "micron": "vendor:micron",
    "micron technology": "vendor:micron",
}

_EDGAR_CIK_RE = re.compile(
    r"/Archives/edgar/data/(\d+)/",
    re.I,
)


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def extract_cik(url: str, metadata: Optional[dict[str, Any]] = None) -> Optional[str]:
    if metadata and metadata.get("cik"):
        return str(metadata["cik"]).zfill(10)
    if not url:
        return None
    m = _EDGAR_CIK_RE.search(url)
    if m:
        return m.group(1).zfill(10)
    return None


def company_key_and_role(
    *,
    company_name: str,
    source: str = "",
    url: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> tuple[str, str]:
    """Return (company_key, role) where role is prospect | manufacturer."""
    cik = extract_cik(url, metadata)
    if cik and cik == MICRON_CIK:
        return "vendor:micron", "manufacturer"

    norm = _norm_name(company_name)
    if norm in _VENDOR_NAMES:
        return _VENDOR_NAMES[norm], "manufacturer"
    # Prefix match for "Samsung Electronics Co., Ltd."
    for name, key in _VENDOR_NAMES.items():
        if norm.startswith(name + " ") or norm.startswith(name + ","):
            return key, "manufacturer"

    if cik:
        return f"cik:{cik}", "prospect"

    # Domain hint from non-SEC career URLs
    if url and "sec.gov" not in url:
        host = urlparse(url).netloc.lower()
        host = host.removeprefix("www.").removeprefix("careers-").removeprefix("boards.")
        if host and host not in {"greenhouse.io", "lever.co", "myworkdayjobs.com"}:
            # Only use clean company career hosts
            if "greenhouse.io" not in host and "lever.co" not in host:
                base = host.split(".")[0]
                if base and len(base) > 2:
                    pass  # keep name: for now; domain: later when Clay confirms

    slug = re.sub(r"[^a-z0-9]+", "-", norm).strip("-") or "unknown"
    return f"name:{slug}", "prospect"


def signal_key(source: str, external_id: str) -> str:
    return f"{source}:{external_id}"
