# Signals → Airtable → Clay

**Decision (2026-10-16):** Airtable is the hub; Clay is an enrichment worker; our scorer does the math.

```
collectors → SQLite → export_airtable.py → Airtable ◀─▶ Clay (Companies only)
                              ▲                │
                     pull_airtable.py ◀────────┘  (labels + enrichment)
                              │
                          score.py → score_snapshots → export Leads
```

## Why Airtable hub (not straight into Clay)

| Concern | Clay direct | Airtable hub |
|---------|-------------|--------------|
| Automated intake | Webhooks/HTTP on Growth (~$446/mo) | Airtable API on every plan |
| Dedupe on rerun | Webhook 50k cap | Upsert on Signal Key |
| Enrichment spend | One Clay row per signal | One company row; Enrich? gate |
| Human review | Weak | Views + Review select |
| Rubric secrecy | Formulas visible | Scorer stays in our service |

## Data isolation

This base holds **public signals only** (Bloomberg-terminal shared view). Broker-entered data (purchase logs, allocation letters, quotes, outcomes) never goes here. In production each broker gets their own Clay workspace and CRM.

`dramx_*` / DRAMeXchange-derived rows are tagged **Internal Only** and excluded from the Clay view (TrendForce terms 6.2).

## Plans

- Airtable Free for first test (1k records, 1k API calls/mo) → Team when live EDGAR fills the base.
- Clay Free to prototype → Launch for scheduled daily pulls. Confirm in first Clay session: Airtable source scheduling + Upsert Record on Launch [unverified].

## Tables

See product brief section 2. Exporter upserts **Companies** and **Signals** only; never overwrites human fields (Review, Side, Product Family, Signal Type, Enrich?) or Clay-owned enrichment fields.

## CLI

```bash
export AIRTABLE_TOKEN=pat...
export AIRTABLE_BASE_ID=app...
python3 export_airtable.py --dry-run --limit 3
python3 export_airtable.py --limit 3
python3 export_airtable.py          # incremental via changed_at watermark
```

CSV fallback (Clay Launch manual upload): `python3 export_csv.py`.

## Section 5 (pull + score loop)

Handed off after this exporter passes its live check. Rubric math already lives in `dram_signals.scoring`; `pull_airtable.py` will feed labels back into SQLite.
