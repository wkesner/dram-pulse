"""Parts catalog: families, variants, and PCN/EOL linkage."""

from dram_signals.catalog.grades import GRADES, infer_grade
from dram_signals.catalog.service import (
    ensure_manufacturer,
    get_family,
    link_pcn_to_variant,
    list_families,
    list_variants,
    register_family,
    register_pcn,
    register_variant,
    upsert_variant_from_attrs,
)

__all__ = [
    "GRADES",
    "infer_grade",
    "ensure_manufacturer",
    "get_family",
    "link_pcn_to_variant",
    "list_families",
    "list_variants",
    "register_family",
    "register_pcn",
    "register_variant",
    "upsert_variant_from_attrs",
]
