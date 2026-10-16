"""Shared configuration for collectors."""

from __future__ import annotations

import os
from pathlib import Path

# Project paths
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DRAM_SIGNALS_DATA_DIR", ROOT_DIR / "data"))
DB_PATH = Path(os.environ.get("DRAM_SIGNALS_DB", DATA_DIR / "signals.db"))

# SEC requires a descriptive User-Agent with contact info.
# Override via DRAM_SIGNALS_USER_AGENT in production.
USER_AGENT = os.environ.get(
    "DRAM_SIGNALS_USER_AGENT",
    "DRAMSignalPipeline/0.1 (research; contact: research@example.com)",
)

# Default lookback when no prior watermark exists
EDGAR_DEFAULT_LOOKBACK_DAYS = int(os.environ.get("DRAM_SIGNALS_EDGAR_LOOKBACK_DAYS", "30"))

# Max characters stored per record (keeps SQLite lean; full docs stay on sec.gov)
MAX_RAW_TEXT_CHARS = int(os.environ.get("DRAM_SIGNALS_MAX_RAW_TEXT", "100000"))

# SEC fair-access guidance: keep under 10 requests/second
REQUEST_DELAY_SECONDS = float(os.environ.get("DRAM_SIGNALS_REQUEST_DELAY", "0.15"))

# Schedule interval for the long-running runner (hours)
SCHEDULE_HOURS = float(os.environ.get("DRAM_SIGNALS_SCHEDULE_HOURS", "6"))

# Samsung family watcher / part master polite delay (seconds per host)
CATALOG_REQUEST_DELAY_SECONDS = float(
    os.environ.get("DRAM_SIGNALS_CATALOG_REQUEST_DELAY", "2.0")
)

# Cap Samsung family pages per run (None / 0 = all from sitemap)
SAMSUNG_FAMILY_MAX = int(os.environ.get("DRAM_SIGNALS_SAMSUNG_FAMILY_MAX", "0")) or None

# Cap part-master enrichments per run (None / 0 = all seeds)
PART_MASTER_MAX = int(os.environ.get("DRAM_SIGNALS_PART_MASTER_MAX", "0")) or None

# EDGAR full-text search
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_FORMS = "8-K"
EDGAR_KEYWORDS = (
    "inventory write-down",
    "program cancellation",
    "supply constraint",
    "memory",
    "DRAM",
)
