"""Fill `excerpt_en` for non-English EC Signals rows (SPEC later).

Original `excerpt` is never modified. English rows keep `excerpt_en` blank.

Offline translator: Argos Translate (ko→en package).

  pip install 'argostranslate>=1.9,<2'
  python -m src.translate_excerpts            # install ko-en package if needed
  python -m src.translate_excerpts --dry-run

Re-run is idempotent: rows that already have excerpt_en are skipped unless
`--force` is set. After extract rebuilds, excerpt_en is preserved by signal_id
when the Korean excerpt is unchanged (see extract._merge_excerpt_en); re-run
this module if new ko signals appear.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from src.dedupe import SIGNAL_COLUMNS, write_csv
from src.fetch_edgar import REPO_ROOT

DEFAULT_SIGNALS = REPO_ROOT / "data" / "signals.csv"


def _is_english(lang: str) -> bool:
    return (lang or "en").strip().lower() in {"", "en", "english"}


def _ensure_argos_ko_en(*, install: bool = True) -> None:
    try:
        import argostranslate.package
        import argostranslate.translate  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "argostranslate is required for offline ko→en translation.\n"
            "  pip install 'argostranslate>=1.9,<2'\n"
            "Then re-run: python -m src.translate_excerpts"
        ) from exc

    installed = argostranslate.package.get_installed_packages()
    if any(p.from_code == "ko" and p.to_code == "en" for p in installed):
        return
    if not install:
        raise SystemExit(
            "Argos ko→en package not installed. Re-run without --no-install."
        )
    print("Downloading Argos Translate package: Korean → English…", flush=True)
    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    pkg = next(
        (p for p in available if p.from_code == "ko" and p.to_code == "en"),
        None,
    )
    if pkg is None:
        raise SystemExit("No Argos package found for ko→en")
    path = pkg.download()
    argostranslate.package.install_from_path(path)
    print("Installed Argos ko→en package.", flush=True)


def translate_text(text: str, *, source: str = "ko", target: str = "en") -> str:
    import argostranslate.translate

    out = argostranslate.translate.translate(text, source, target)
    return (out or "").strip()


def translate_signals(
    rows: list[dict[str, str]],
    *,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    stats = {
        "total": len(rows),
        "english": 0,
        "skipped_existing": 0,
        "translated": 0,
        "failed": 0,
        "unsupported_lang": 0,
    }
    out: list[dict[str, str]] = []
    for row in rows:
        row = dict(row)
        lang = (row.get("language") or "en").strip().lower()
        excerpt = row.get("excerpt") or ""
        if _is_english(lang):
            row["excerpt_en"] = ""
            stats["english"] += 1
            out.append(row)
            continue
        if lang not in {"ko", "korean"}:
            # Only Korean is wired offline today; leave blank rather than invent text.
            if not (row.get("excerpt_en") or "").strip():
                row["excerpt_en"] = ""
            stats["unsupported_lang"] += 1
            out.append(row)
            continue
        existing = (row.get("excerpt_en") or "").strip()
        if existing and not force:
            stats["skipped_existing"] += 1
            out.append(row)
            continue
        if not excerpt.strip():
            row["excerpt_en"] = ""
            stats["failed"] += 1
            out.append(row)
            continue
        if dry_run:
            print(
                f"[dry-run] would translate {row.get('signal_id')} "
                f"({row.get('ticker_or_code')}) len={len(excerpt)}",
                flush=True,
            )
            stats["translated"] += 1
            out.append(row)
            continue
        try:
            row["excerpt_en"] = translate_text(excerpt, source="ko", target="en")
            if not row["excerpt_en"]:
                stats["failed"] += 1
            else:
                stats["translated"] += 1
        except Exception as exc:  # noqa: BLE001 — keep batch going
            print(
                f"WARN translate failed {row.get('signal_id')}: {exc}",
                file=sys.stderr,
            )
            row["excerpt_en"] = existing
            stats["failed"] += 1
        out.append(row)
    return out, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fill excerpt_en for non-English signals (Argos Translate, offline)"
    )
    parser.add_argument(
        "--signals",
        type=Path,
        default=DEFAULT_SIGNALS,
        help="Path to signals.csv",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-translate rows that already have excerpt_en",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List rows that would be translated; do not write",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Do not download the Argos ko→en package if missing",
    )
    args = parser.parse_args(argv)

    if not args.signals.is_file():
        raise SystemExit(f"signals file missing: {args.signals}")

    with args.signals.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    need = any(
        not _is_english(r.get("language") or "en")
        and (args.force or not (r.get("excerpt_en") or "").strip())
        for r in rows
    )
    if need and not args.dry_run:
        _ensure_argos_ko_en(install=not args.no_install)
    elif need and args.dry_run:
        pass
    elif not need:
        # Still normalize english blanks / column presence
        pass
    else:
        _ensure_argos_ko_en(install=not args.no_install)

    updated, stats = translate_signals(rows, force=args.force, dry_run=args.dry_run)
    print(
        "translate_excerpts: "
        f"total={stats['total']} english={stats['english']} "
        f"translated={stats['translated']} skipped_existing={stats['skipped_existing']} "
        f"unsupported_lang={stats['unsupported_lang']} failed={stats['failed']}"
    )
    if args.dry_run:
        return 0

    # Ensure SIGNAL_COLUMNS order (adds excerpt_en if older CSV lacked it).
    write_csv(args.signals, updated, SIGNAL_COLUMNS)
    print(f"Wrote {len(updated)} signals → {args.signals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
