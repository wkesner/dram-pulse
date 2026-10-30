"""Fill `excerpt_en` for non-English EC Signals rows.

Original `excerpt` is never modified. English rows keep `excerpt_en` blank.

Offline translator: Argos Translate packages for Asian + German sources:

  ko → en   (DART)
  zh → en   (CNINFO simplified)
  zt → en   (MOPS / zh-TW traditional)
  ja → en   (EDINET)
  de → en   (UREG / BaFin DE)

  pip install 'argostranslate>=1.9,<2'
  python -m src.translate_excerpts            # install missing packages
  python -m src.translate_excerpts --dry-run

Re-run is idempotent: rows that already have excerpt_en are skipped unless
`--force` is set. After extract rebuilds, excerpt_en is preserved by signal_id
when the source excerpt is unchanged (see extract._merge_excerpt_en); re-run
this module if new non-English signals appear. The Feed can also fill
excerpt_en on demand via POST /api/signals/{id}/translate.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from src.dedupe import SIGNAL_COLUMNS, write_csv
from src.fetch_edgar import REPO_ROOT

DEFAULT_SIGNALS = REPO_ROOT / "data" / "signals.csv"

# CSV / meta language → Argos from_code (to_code is always en).
_LANG_TO_ARGOS: dict[str, str] = {
    "ko": "ko",
    "korean": "ko",
    "zh": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "zh-hans": "zh",
    "chinese": "zh",
    "cmn": "zh",
    "zh-tw": "zt",
    "zh_tw": "zt",
    "zh-hant": "zt",
    "zt": "zt",
    "ja": "ja",
    "jp": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "de": "de",
    "deu": "de",
    "ger": "de",
    "german": "de",
}

# Human labels for UI / errors (keyed by Argos code).
ARGOS_LANG_LABELS: dict[str, str] = {
    "ko": "Korean",
    "zh": "Chinese (Simplified)",
    "zt": "Chinese (Traditional)",
    "ja": "Japanese",
    "de": "German",
}

SUPPORTED_ARGOS_CODES = frozenset(ARGOS_LANG_LABELS)


def _is_english(lang: str) -> bool:
    return (lang or "en").strip().lower() in {"", "en", "english"}


def normalize_source_lang(lang: str) -> str | None:
    """Return Argos from_code for a non-English language, else None."""
    key = (lang or "").strip().lower().replace(" ", "")
    if not key or _is_english(key):
        return None
    # Normalize separators: zh_TW / zh.TW → zh-tw
    key = key.replace("_", "-").replace(".", "-")
    if key in _LANG_TO_ARGOS:
        return _LANG_TO_ARGOS[key]
    # Bare prefix: zh-anything without Hans → try zh; de-DE → de
    if "-" in key:
        base = key.split("-", 1)[0]
        if base in _LANG_TO_ARGOS:
            return _LANG_TO_ARGOS[base]
    return _LANG_TO_ARGOS.get(key)


def is_translatable_lang(lang: str) -> bool:
    """True when Feed should offer translate-to-English (any non-EN)."""
    return not _is_english(lang)


def supported_argos_lang(lang: str) -> bool:
    code = normalize_source_lang(lang)
    return code is not None and code in SUPPORTED_ARGOS_CODES


def _ensure_argos_package(from_code: str, *, install: bool = True) -> None:
    try:
        import argostranslate.package
        import argostranslate.translate  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "argostranslate is required for offline translation.\n"
            "  pip install 'argostranslate>=1.9,<2'\n"
            "Then re-run: python -m src.translate_excerpts"
        ) from exc

    if from_code not in SUPPORTED_ARGOS_CODES:
        raise SystemExit(f"Unsupported Argos source language: {from_code}")

    installed = argostranslate.package.get_installed_packages()
    if any(p.from_code == from_code and p.to_code == "en" for p in installed):
        return
    if not install:
        raise SystemExit(
            f"Argos {from_code}→en package not installed. Re-run without --no-install."
        )
    label = ARGOS_LANG_LABELS.get(from_code, from_code)
    print(f"Downloading Argos Translate package: {label} → English…", flush=True)
    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    pkg = next(
        (p for p in available if p.from_code == from_code and p.to_code == "en"),
        None,
    )
    if pkg is None:
        raise SystemExit(f"No Argos package found for {from_code}→en")
    path = pkg.download()
    argostranslate.package.install_from_path(path)
    print(f"Installed Argos {from_code}→en package.", flush=True)


def ensure_argos_for_langs(
    langs: set[str] | list[str],
    *,
    install: bool = True,
) -> list[str]:
    """Install Argos packages for the given CSV/meta language codes.

    Returns the Argos from_codes that were ensured.
    """
    codes: list[str] = []
    seen: set[str] = set()
    for lang in langs:
        code = normalize_source_lang(lang)
        if not code or code in seen:
            continue
        if code not in SUPPORTED_ARGOS_CODES:
            continue
        seen.add(code)
        _ensure_argos_package(code, install=install)
        codes.append(code)
    return codes


# Back-compat alias used by extract.py
def _ensure_argos_ko_en(*, install: bool = True) -> None:
    _ensure_argos_package("ko", install=install)


def translate_text(text: str, *, source: str = "ko", target: str = "en") -> str:
    """Translate `text` with Argos. `source` may be a CSV lang or Argos code.

    For traditional Chinese (`zt` / zh-TW), falls back to simplified `zh` if the
    zt package yields an empty string (MOPS/UMC path).
    """
    import argostranslate.translate

    from_code = normalize_source_lang(source) or (source or "").strip().lower()
    if not from_code:
        return ""
    if target.strip().lower() not in {"en", "english"}:
        raise ValueError(f"Only English target is supported (got {target!r})")
    out = (argostranslate.translate.translate(text, from_code, "en") or "").strip()
    if not out and from_code == "zt":
        # Traditional package missing / empty → try simplified model.
        try:
            _ensure_argos_package("zh", install=True)
        except SystemExit:
            return ""
        out = (argostranslate.translate.translate(text, "zh", "en") or "").strip()
    return out


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
        argos = normalize_source_lang(lang)
        if not argos or argos not in SUPPORTED_ARGOS_CODES:
            # Leave any prior excerpt_en; do not invent text for unknown langs.
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
                f"({row.get('ticker_or_code')}) lang={lang}→{argos} "
                f"len={len(excerpt)}",
                flush=True,
            )
            stats["translated"] += 1
            out.append(row)
            continue
        try:
            row["excerpt_en"] = translate_text(excerpt, source=argos, target="en")
            if not row["excerpt_en"]:
                stats["failed"] += 1
            else:
                stats["translated"] += 1
        except Exception as exc:  # noqa: BLE001 — keep batch going
            print(
                f"WARN translate failed {row.get('signal_id')} ({lang}): {exc}",
                file=sys.stderr,
            )
            row["excerpt_en"] = existing
            stats["failed"] += 1
        out.append(row)
    return out, stats


def translate_row_excerpt(
    row: dict[str, str],
    *,
    install: bool = True,
) -> str:
    """Return English for one signal row (existing excerpt_en or fresh MT).

    Raises ValueError for English / empty / unsupported language.
    Raises SystemExit / ImportError if Argos cannot be loaded.
    """
    lang = (row.get("language") or "en").strip().lower()
    if _is_english(lang):
        raise ValueError("english_signal")
    existing = (row.get("excerpt_en") or "").strip()
    if existing:
        return existing
    excerpt = (row.get("excerpt") or "").strip()
    if not excerpt:
        raise ValueError("empty_excerpt")
    argos = normalize_source_lang(lang)
    if not argos or argos not in SUPPORTED_ARGOS_CODES:
        raise ValueError(f"unsupported_lang:{lang}")
    ensure_argos_for_langs({lang}, install=install)
    en = translate_text(excerpt, source=argos, target="en")
    if not en:
        raise ValueError("translate_empty")
    return en


def persist_excerpt_en(
    signals_path: Path,
    signal_id: str,
    excerpt_en: str,
) -> bool:
    """Write excerpt_en for signal_id into signals.csv. Returns True if updated."""
    if not signals_path.is_file():
        return False
    with signals_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    found = False
    for row in rows:
        if row.get("signal_id") != signal_id:
            continue
        # English posts never store excerpt_en.
        if _is_english(row.get("language") or "en"):
            if (row.get("excerpt_en") or "").strip():
                row["excerpt_en"] = ""
                write_csv(signals_path, rows, SIGNAL_COLUMNS)
            return False
        row["excerpt_en"] = excerpt_en
        found = True
        break
    if not found:
        return False
    write_csv(signals_path, rows, SIGNAL_COLUMNS)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fill excerpt_en for non-English signals "
            "(Argos Translate: ko/zh/zt/ja/de → en)"
        )
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
        help="Do not download Argos packages if missing",
    )
    args = parser.parse_args(argv)

    if not args.signals.is_file():
        raise SystemExit(f"signals file missing: {args.signals}")

    with args.signals.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    need_langs: set[str] = set()
    for r in rows:
        lang = r.get("language") or "en"
        if _is_english(lang):
            continue
        if not args.force and (r.get("excerpt_en") or "").strip():
            continue
        if not (r.get("excerpt") or "").strip():
            continue
        if supported_argos_lang(lang):
            need_langs.add(lang)

    if need_langs and not args.dry_run:
        ensure_argos_for_langs(need_langs, install=not args.no_install)

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
