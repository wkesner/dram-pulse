"""Fill `excerpt_en` for non-English EC Signals rows.

Original `excerpt` is never modified. English rows keep `excerpt_en` blank.

Offline translator: Argos Translate (ko/zh/zt/ja/de/fr/… → en packages).

  pip install 'argostranslate>=1.9,<2'
  python -m src.translate_excerpts            # install needed *→en packages
  python -m src.translate_excerpts --dry-run

Re-run is idempotent: rows that already have excerpt_en are skipped unless
`--force` is set. After extract rebuilds, excerpt_en is preserved by signal_id
when the source excerpt is unchanged (see extract._merge_excerpt_en); re-run
this module if new non-English signals appear.

Feed UI: any language≠en card shows Show English. If `excerpt_en` is blank,
POST /api/translate fills it on demand (same Argos path) and persists it.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from src.dedupe import SIGNAL_COLUMNS, write_csv
from src.fetch_edgar import REPO_ROOT

DEFAULT_SIGNALS = REPO_ROOT / "data" / "signals.csv"

# Filing language tags → Argos from_code (to_code always "en").
# zh-TW / Traditional uses Argos "zt"; Simplified uses "zh".
_LANG_ALIASES: dict[str, str] = {
    "": "en",
    "en": "en",
    "english": "en",
    "ko": "ko",
    "korean": "ko",
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "chinese": "zh",
    "cn": "zh",
    "zh-tw": "zt",
    "zh-hant": "zt",
    "zh_tw": "zt",
    "zt": "zt",
    "ja": "ja",
    "jp": "ja",
    "japanese": "ja",
    "de": "de",
    "german": "de",
    "deutsch": "de",
    "fr": "fr",
    "french": "fr",
    "es": "es",
    "spanish": "es",
    "it": "it",
    "italian": "it",
    "pt": "pt",
    "portuguese": "pt",
    "nl": "nl",
    "dutch": "nl",
    "ru": "ru",
    "russian": "ru",
}

# Human labels for API / UI chips (Argos code → short display).
ARGOS_LABELS: dict[str, str] = {
    "ko": "ko",
    "zh": "zh",
    "zt": "zh-TW",
    "ja": "ja",
    "de": "de",
    "fr": "fr",
    "es": "es",
    "it": "it",
    "pt": "pt",
    "nl": "nl",
    "ru": "ru",
}

# Languages we actively cover for EC Signals sources (DART/CNINFO/MOPS/EDINET/UREG).
PRIMARY_SOURCE_LANGS = ("ko", "zh", "zt", "ja", "de")


def normalize_lang(lang: str) -> str:
    """Lower/strip a language tag (preserve zh-tw style hyphen)."""
    return (lang or "en").strip().lower().replace("_", "-")


def is_english(lang: str) -> bool:
    return argos_source_code(lang) == "en"


def argos_source_code(lang: str) -> str | None:
    """Map a filing language tag to an Argos from_code, or None if unknown.

    Returns ``\"en\"`` for English (no translation). Unknown tags return None.
    """
    key = normalize_lang(lang)
    if key in _LANG_ALIASES:
        return _LANG_ALIASES[key]
    # zh-anything traditional vs simplified heuristics
    if key.startswith("zh-tw") or key.startswith("zh-hant"):
        return "zt"
    if key.startswith("zh"):
        return "zh"
    if key in ARGOS_LABELS:
        return key
    return None


def display_lang(lang: str) -> str:
    """Short chip label for the feed (ko / zh / zh-TW / ja / de / …)."""
    code = argos_source_code(lang)
    if code is None:
        return normalize_lang(lang) or "??"
    if code == "en":
        return "en"
    return ARGOS_LABELS.get(code, code)


def _ensure_argos_import() -> None:
    try:
        import argostranslate.package  # noqa: F401
        import argostranslate.translate  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "argostranslate is required for offline *→en translation.\n"
            "  pip install 'argostranslate>=1.9,<2'\n"
            "Then re-run: python -m src.translate_excerpts"
        ) from exc


def ensure_argos_pair(source: str, *, target: str = "en", install: bool = True) -> None:
    """Ensure the Argos package for source→target is installed."""
    _ensure_argos_import()
    import argostranslate.package

    source = (source or "").strip().lower()
    target = (target or "en").strip().lower()
    if not source or source == "en":
        return

    installed = argostranslate.package.get_installed_packages()
    if any(p.from_code == source and p.to_code == target for p in installed):
        return
    if not install:
        raise SystemExit(
            f"Argos {source}→{target} package not installed. "
            "Re-run without --no-install."
        )
    print(
        f"Downloading Argos Translate package: {source} → {target}…",
        flush=True,
    )
    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    pkg = next(
        (p for p in available if p.from_code == source and p.to_code == target),
        None,
    )
    if pkg is None:
        raise SystemExit(f"No Argos package found for {source}→{target}")
    path = pkg.download()
    argostranslate.package.install_from_path(path)
    print(f"Installed Argos {source}→{target} package.", flush=True)


def ensure_argos_for_langs(
    langs: list[str] | set[str], *, install: bool = True
) -> list[str]:
    """Install Argos *→en packages for the given filing language tags.

    Returns the list of Argos from_codes that were ensured.
    """
    codes: list[str] = []
    seen: set[str] = set()
    for lang in langs:
        code = argos_source_code(lang)
        if not code or code == "en" or code in seen:
            continue
        seen.add(code)
        ensure_argos_pair(code, target="en", install=install)
        codes.append(code)
    return codes


# Back-compat alias used by extract._enrich_candidate_excerpt_en
def _ensure_argos_ko_en(*, install: bool = True) -> None:
    ensure_argos_pair("ko", target="en", install=install)


def translate_text(text: str, *, source: str = "ko", target: str = "en") -> str:
    import argostranslate.translate

    src = (source or "ko").strip().lower()
    tgt = (target or "en").strip().lower()
    if not text or not text.strip():
        return ""
    if src == "en" or src == tgt:
        return text.strip()
    out = argostranslate.translate.translate(text, src, tgt)
    return (out or "").strip()


def translate_excerpt(excerpt: str, language: str, *, install: bool = True) -> str:
    """Translate one excerpt to English using the filing language tag."""
    code = argos_source_code(language)
    if code is None:
        raise ValueError(f"unsupported language for translation: {language!r}")
    if code == "en":
        return ""
    ensure_argos_pair(code, target="en", install=install)
    return translate_text(excerpt, source=code, target="en")


def translate_signals(
    rows: list[dict[str, str]],
    *,
    force: bool = False,
    dry_run: bool = False,
    install: bool = True,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    stats = {
        "total": len(rows),
        "english": 0,
        "skipped_existing": 0,
        "translated": 0,
        "failed": 0,
        "unsupported_lang": 0,
    }
    # Pre-install packages for languages we will need (skip dry-run network).
    if not dry_run:
        need_langs = [
            (r.get("language") or "en")
            for r in rows
            if not is_english(r.get("language") or "en")
            and (force or not (r.get("excerpt_en") or "").strip())
            and (r.get("excerpt") or "").strip()
        ]
        if need_langs:
            try:
                ensure_argos_for_langs(need_langs, install=install)
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001
                print(f"WARN ensure_argos_for_langs: {exc}", file=sys.stderr)

    out: list[dict[str, str]] = []
    for row in rows:
        row = dict(row)
        lang = row.get("language") or "en"
        excerpt = row.get("excerpt") or ""
        if is_english(lang):
            row["excerpt_en"] = ""
            stats["english"] += 1
            out.append(row)
            continue
        code = argos_source_code(lang)
        if code is None:
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
                f"({row.get('ticker_or_code')}) {display_lang(lang)}→en "
                f"len={len(excerpt)}",
                flush=True,
            )
            stats["translated"] += 1
            out.append(row)
            continue
        try:
            row["excerpt_en"] = translate_text(excerpt, source=code, target="en")
            if not row["excerpt_en"]:
                stats["failed"] += 1
            else:
                stats["translated"] += 1
        except Exception as exc:  # noqa: BLE001 — keep batch going
            print(
                f"WARN translate failed {row.get('signal_id')} "
                f"({display_lang(lang)}): {exc}",
                file=sys.stderr,
            )
            row["excerpt_en"] = existing
            stats["failed"] += 1
        out.append(row)
    return out, stats


def persist_excerpt_en(
    signals_path: Path,
    signal_id: str,
    excerpt_en: str,
    *,
    columns: list[str] | None = None,
) -> bool:
    """Write excerpt_en for one signal_id. Returns True if a row was updated."""
    if not signals_path.is_file():
        return False
    with signals_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        fieldnames = list(rows[0].keys()) if rows else list(SIGNAL_COLUMNS)
    cols = columns or (
        list(SIGNAL_COLUMNS)
        if all(c in SIGNAL_COLUMNS for c in ("signal_id", "excerpt_en"))
        else fieldnames
    )
    if "excerpt_en" not in cols:
        cols = list(cols) + ["excerpt_en"]
    found = False
    for row in rows:
        if (row.get("signal_id") or "") == signal_id:
            row["excerpt_en"] = excerpt_en
            found = True
            break
    if not found:
        return False
    write_csv(signals_path, rows, cols)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fill excerpt_en for non-English signals "
            "(Argos Translate offline: ko/zh/ja/de/… → en)"
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
        help="Do not download missing Argos *→en packages",
    )
    args = parser.parse_args(argv)

    if not args.signals.is_file():
        raise SystemExit(f"signals file missing: {args.signals}")

    with args.signals.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    updated, stats = translate_signals(
        rows,
        force=args.force,
        dry_run=args.dry_run,
        install=not args.no_install,
    )
    print(
        "translate_excerpts: "
        f"total={stats['total']} english={stats['english']} "
        f"translated={stats['translated']} skipped_existing={stats['skipped_existing']} "
        f"unsupported_lang={stats['unsupported_lang']} failed={stats['failed']}"
    )
    if args.dry_run:
        return 0

    write_csv(args.signals, updated, SIGNAL_COLUMNS)
    print(f"Wrote {len(updated)} signals → {args.signals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
