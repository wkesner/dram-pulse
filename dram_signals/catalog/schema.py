"""SQLite schema for the parts catalog and PCN/EOL notices."""

CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS manufacturers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS part_families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id),
    family_code TEXT NOT NULL,
    technology TEXT,
    density TEXT,
    organization TEXT,
    description TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(manufacturer_id, family_code)
);

CREATE INDEX IF NOT EXISTS idx_part_families_code
    ON part_families(family_code);

CREATE TABLE IF NOT EXISTS part_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES part_families(id),
    mpn TEXT NOT NULL UNIQUE,
    package TEXT,
    package_detail TEXT,
    grade TEXT NOT NULL,
    temperature_range TEXT,
    speed_grade TEXT,
    voltage TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'unknown',
    order_packaging TEXT,
    attributes_json TEXT,
    source TEXT,
    source_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_part_variants_family
    ON part_variants(family_id);

CREATE INDEX IF NOT EXISTS idx_part_variants_grade
    ON part_variants(grade);

CREATE INDEX IF NOT EXISTS idx_part_variants_lifecycle
    ON part_variants(lifecycle_status);

CREATE TABLE IF NOT EXISTS pcn_notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id),
    notice_id TEXT NOT NULL,
    notice_type TEXT NOT NULL,
    title TEXT,
    published_at TEXT,
    effective_at TEXT,
    last_order_at TEXT,
    last_ship_at TEXT,
    url TEXT,
    raw_text TEXT,
    source TEXT,
    external_id TEXT,
    collected_at TEXT NOT NULL,
    UNIQUE(manufacturer_id, notice_id)
);

CREATE INDEX IF NOT EXISTS idx_pcn_notices_type
    ON pcn_notices(notice_type);

CREATE TABLE IF NOT EXISTS pcn_affected_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pcn_id INTEGER NOT NULL REFERENCES pcn_notices(id) ON DELETE CASCADE,
    family_id INTEGER REFERENCES part_families(id),
    variant_id INTEGER REFERENCES part_variants(id),
    mpn TEXT,
    mpn_pattern TEXT,
    impact_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_pcn_affected_pcn
    ON pcn_affected_parts(pcn_id);

CREATE INDEX IF NOT EXISTS idx_pcn_affected_variant
    ON pcn_affected_parts(variant_id);
"""
