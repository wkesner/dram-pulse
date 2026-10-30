"""
AMF Info-financière (France) disclosure stub — Europe free-portal path.

Official free API (OAM / Transparency Directive storage):
  https://www.info-financiere.gouv.fr/api/explore/v2.0
  Docs: https://www.data.gouv.fr/dataservices/api-info-financiere
  Soft limit: ~10_000 calls / IP / day. No API key.

Ranked #1 among native EU portals for EC Signals after EDGAR dual-lists
(STM/NXPI/ASML already on EDGAR). BaFin/Unternehmensregister and AFM (NL)
are portal-first without a comparable public filing API — see
internal note europe-filings-portals.md.

This module is intentionally a stub: roster ISINs + list/download land in a
follow-on PR. Running it prints the ranked Europe path and exits non-zero
so CI / agents do not pretend France is live yet.
"""

from __future__ import annotations

import argparse
import sys

_HELP = """\
AMF Info-financière fetch is stubbed (Europe free-portal path).

Why AMF first among native EU portals:
  1. Free JSON API (no key) — info-financiere.gouv.fr explore v2
  2. Regulated issuer filings (annual / semi / permanent info)
  3. Complements EDGAR dual-lists (STM already indexed via SEC)

Not yet implemented here:
  - companies.yaml source: AMF + ISIN / AMF entity ids
  - list documents for configured issuers → PDF/HTML → other_full.txt

Next portals (no stub yet):
  2. BaFin + Unternehmensregister (DE) — high electronics density, portal UX
  3. AFM financial-reporting register (NL) — thin vs ASML/NXP on EDGAR

Do not chase UK Companies House confirmation statements for Europe insight.
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="AMF Info-financière stub (France OAM filings)"
    )
    p.add_argument(
        "--ticker",
        help="Reserved for future ISIN / local ticker (unused in stub)",
    )
    p.parse_args(argv)
    print(_HELP, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
