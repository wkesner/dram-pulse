"""SQLite schema for scoring: typed signals, companies, leads, snapshots."""

SCORING_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    broker_id TEXT NOT NULL DEFAULT 'default',
    company_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    cik TEXT,
    domain TEXT,
    sic TEXT,
    revenue_usd REAL,
    gross_margin REAL,
    buyer_type TEXT,
    end_builds_json TEXT,
    families_traded_json TEXT,
    relationship TEXT DEFAULT 'none',
    gate_flags_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (broker_id, company_key)
);

CREATE INDEX IF NOT EXISTS idx_companies_cik ON companies(cik);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(display_name);

CREATE TABLE IF NOT EXISTS company_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_id TEXT NOT NULL DEFAULT 'default',
    company_key TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    source TEXT,
    UNIQUE(alias_norm, broker_id)
);

CREATE TABLE IF NOT EXISTS typed_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_signal_id INTEGER,
    company_key TEXT,
    side TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    product_family TEXT NOT NULL DEFAULT 'unknown',
    confidence REAL NOT NULL,
    event_key TEXT NOT NULL,
    event_at TEXT NOT NULL,
    deadline TEXT,
    source TEXT,
    url TEXT,
    raw_text TEXT,
    broker_id TEXT NOT NULL DEFAULT 'default',
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(event_key, signal_type, broker_id)
);

CREATE INDEX IF NOT EXISTS idx_typed_company_side
    ON typed_signals(company_key, side);
CREATE INDEX IF NOT EXISTS idx_typed_event
    ON typed_signals(event_key);

CREATE TABLE IF NOT EXISTS family_regime (
    product_family TEXT PRIMARY KEY,
    regime TEXT NOT NULL,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_allocation (
    product_family TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    noted_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS end_build_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_family TEXT NOT NULL,
    end_build TEXT NOT NULL,
    match_strength TEXT NOT NULL DEFAULT 'core',
    UNIQUE(product_family, end_build)
);

CREATE TABLE IF NOT EXISTS spot_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_family TEXT NOT NULL,
    grade TEXT,
    as_of TEXT NOT NULL,
    session TEXT,
    high REAL,
    low REAL,
    average REAL,
    source TEXT NOT NULL,
    licence_status TEXT NOT NULL,
    broker_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_spot_family_asof
    ON spot_prices(product_family, as_of);

CREATE TABLE IF NOT EXISTS purchase_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_id TEXT NOT NULL,
    customer_company_key TEXT NOT NULL,
    part_number TEXT NOT NULL,
    product_family TEXT,
    end_product TEXT,
    purchased_at TEXT,
    qty REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_purchase_broker_customer
    ON purchase_log(broker_id, customer_company_key);

CREATE TABLE IF NOT EXISTS allocation_letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_id TEXT NOT NULL,
    company_key TEXT NOT NULL,
    form TEXT NOT NULL,
    product_family TEXT,
    noted_at TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS score_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    company_key TEXT NOT NULL,
    side TEXT NOT NULL,
    product_family TEXT,
    score REAL NOT NULL,
    signal_strength REAL NOT NULL,
    fit REAL NOT NULL,
    timing REAL NOT NULL,
    components_json TEXT NOT NULL,
    signal_ids_json TEXT,
    regime TEXT,
    rubric_version TEXT NOT NULL,
    tier TEXT NOT NULL,
    exploration INTEGER NOT NULL DEFAULT 0,
    gated INTEGER NOT NULL DEFAULT 0,
    gate_reason TEXT,
    reasons_text TEXT,
    routed_to TEXT,
    routed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_broker_tier
    ON score_snapshots(broker_id, tier, routed_at);

CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reason TEXT NOT NULL,
    company_name TEXT,
    company_key TEXT,
    raw_signal_id INTEGER,
    details_json TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
"""
