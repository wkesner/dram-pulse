"""Verify signals.csv excerpts against cached raw text offsets.

Usage:
  python -m src.verify
  python -m src.verify --csv data/signals.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from src.fetch_edgar import REPO_ROOT


def verify_row(row: dict[str, str], repo_root: Path = REPO_ROOT) -> tuple[bool, str]:
    raw_path = row.get("raw_path") or ""
    excerpt = row.get("excerpt") or ""
    try:
        start = int(row["char_start"])
        end = int(row["char_end"])
    except (KeyError, ValueError) as exc:
        return False, f"bad offsets: {exc}"

    path = repo_root / raw_path
    if not path.is_file():
        return False, f"missing raw file: {raw_path}"

    text = path.read_text(encoding="utf-8")
    if start < 0 or end > len(text) or start > end:
        return False, f"offsets out of range: {start}:{end} vs len={len(text)}"

    slice_ = text[start:end]
    # Excerpts are stored with newlines flattened to spaces.
    expected = excerpt
    actual_flat = " ".join(slice_.split())
    expected_flat = " ".join(expected.split())
    if actual_flat != expected_flat and slice_ != excerpt:
        # Allow exact slice match OR whitespace-normalized match.
        if actual_flat != expected_flat:
            return (
                False,
                f"excerpt mismatch at {start}:{end}: "
                f"file={actual_flat[:80]!r} csv={expected_flat[:80]!r}",
            )
    return True, "ok"


def verify_csv(csv_path: Path, repo_root: Path = REPO_ROOT) -> tuple[int, int, list[str]]:
    failures: list[str] = []
    total = 0
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=2):
            total += 1
            ok, msg = verify_row(row, repo_root)
            if not ok:
                sid = row.get("signal_id", "?")
                failures.append(f"line {i} id={sid}: {msg}")
    passed = total - len(failures)
    return total, passed, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Memory Signals CSV offsets")
    parser.add_argument("--csv", type=Path, default=REPO_ROOT / "data" / "signals.csv")
    args = parser.parse_args(argv)

    if not args.csv.is_file():
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        return 2

    total, passed, failures = verify_csv(args.csv)
    rate = (100.0 * passed / total) if total else 0.0
    print(f"verify: {passed}/{total} passed ({rate:.1f}%)")
    for line in failures[:50]:
        print(f"  FAIL {line}", file=sys.stderr)
    if len(failures) > 50:
        print(f"  … {len(failures) - 50} more", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
