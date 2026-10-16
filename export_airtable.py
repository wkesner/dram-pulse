#!/usr/bin/env python3
"""Upsert SQLite signals into Airtable Companies + Signals.

Usage:
  AIRTABLE_TOKEN=pat... AIRTABLE_BASE_ID=app... \\
    python3 export_airtable.py [--dry-run] [--all] [--limit N]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dram_signals.exporters.airtable_export import export_to_airtable  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export signals to Airtable")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON; no API calls")
    parser.add_argument("--all", action="store_true", help="Re-send all rows; do not move watermark")
    parser.add_argument("--limit", type=int, default=None, help="Max signal rows")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    export_to_airtable(dry_run=args.dry_run, all_rows=args.all, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
