#!/usr/bin/env python3
"""CSV fallback for Clay Launch manual upload (Companies + Signals flattened).

Usage:
  python3 export_csv.py [--out path] [--limit N] [--since ISO]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dram_signals import config  # noqa: E402
from dram_signals.db import db_session, get_export_watermark  # noqa: E402
from dram_signals.exporters.airtable_export import (  # noqa: E402
    _excerpt,
    _is_internal,
    _matched_terms,
    _parse_metadata,
    load_changed_rows,
)
from dram_signals.exporters.company_keys import company_key_and_role, signal_key  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export signals to CSV for Clay upload")
    p.add_argument("--out", type=Path, default=Path("data/export_signals.csv"))
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--all", action="store_true")
    args = p.parse_args(argv)

    with db_session(config.DB_PATH) as conn:
        wm = None if args.all else get_export_watermark(conn, "csv")
        rows = load_changed_rows(conn, watermark=wm, limit=args.limit, all_rows=args.all)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "signal_key",
        "source",
        "signal_date",
        "company_key",
        "company_name",
        "role",
        "matched_terms",
        "excerpt",
        "url",
        "internal_only",
    ]
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            meta = _parse_metadata(row)
            ck, role = company_key_and_role(
                company_name=row["company_name"],
                source=row["source"],
                url=row["url"] or "",
                metadata=meta,
            )
            w.writerow(
                {
                    "signal_key": signal_key(row["source"], row["external_id"]),
                    "source": row["source"],
                    "signal_date": (row["timestamp"] or "")[:10],
                    "company_key": ck,
                    "company_name": row["company_name"],
                    "role": role,
                    "matched_terms": "|".join(_matched_terms(row)),
                    "excerpt": _excerpt(row, meta).replace("\n", " ")[:2000],
                    "url": row["url"] or "",
                    "internal_only": "true" if _is_internal(row["source"]) else "false",
                }
            )
    print(f"wrote {len(rows)} rows → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
