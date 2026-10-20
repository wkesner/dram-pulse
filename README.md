# Memory Signals

Qualitative memory-market signals from public filings (see [`SPEC.md`](SPEC.md)).

## Milestone 1 — Micron EDGAR 10-K

Fetch Micron's latest 10-K from SEC EDGAR, cache it under `data/raw/`, and split Item 1 (Business), Item 1A (Risk Factors), and Item 7 (MD&A). Full plain text is kept as `other`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Required by SEC fair-access. Copy .env.example → .env or export directly:
export SEC_USER_AGENT="MemorySignals Your Name you@example.com"

python -m src.fetch_edgar --ticker MU
```

Cached files land in `data/raw/MU/<accession_no_dashes>/`:

| file | contents |
|------|----------|
| `primary.html` | raw EDGAR primary document |
| `item1_business.txt` | Item 1 |
| `item1a_risk_factors.txt` | Item 1A |
| `item7_mdna.txt` | Item 7 |
| `other_full.txt` | full plain text |
| `meta.json` | accession, filing date, source URL, paths |

Config: `config/settings.yaml` (`lookback_months: 12`), `config/companies.yaml` (ticker `MU` only — CIK looked up at runtime).

## Milestone 2 — Extract + verify (Micron)

Rule-based extraction from cached Item 1 / 1A / 7 text. Term lists live in `config/product_taxonomy.yaml` and `config/supply_terms.yaml`.

```bash
export SEC_USER_AGENT="MemorySignals Your Name you@example.com"
# Fetches prior-year 10-K once (for carried_from_prior_year only; no signals extracted from it)
python3 -m src.extract --ticker MU
python3 -m src.verify
```

Output: `data/signals.csv`. `verify.py` checks every excerpt against `char_start`/`char_end` in the cached raw text (must be 100%).

## Milestone 3 — all EDGAR companies

```bash
export SEC_USER_AGENT="MemorySignals Your Name you@example.com"
python3 -m src.fetch_edgar --all
python3 -m src.extract --all
python3 -m src.verify
```

`config/companies.yaml` lists MU, SKHY, and buyers AAPL DELL HPQ HPE SMCI CSCO NVDA AMD QCOM JBL. Forms: 10-K, 10-Q, 8-K (items 2.02 / 2.06 / 7.01 / 8.01); SKHY also 6-K. CIKs and `fiscalYearEnd` come from EDGAR at runtime.

## Milestone 4 — Feed + family view

```bash
pip install -r requirements.txt
streamlit run app.py
```

- Sidebar: type your name (required before voting), then filter by product category / family / supply direction / company / role / source.
- **Feed** tab: one signal card at a time with highlighted matched terms, source link, 👍 / 👎 / Skip. Votes append to `data/votes.csv`; counts use each user’s latest vote.
- **Family view** tab: plain counts per product family (tightening / easing / stable / unclear, supplier vs buyer, latest filing date). Header shows the lookback window. Click a family to filter the feed.

---

# DRAM Signal Pipeline

Phase 1 collectors for a DRAM market signal-detection pipeline. Public sources are pulled into a local SQLite table of normalized records; a later classification step will tag demand-side vs supply-side. Collectors intentionally capture broadly at the source level — job collectors only apply the title keyword gate from the brief, not demand/supply labels.

## Record shape

| Column | Meaning |
|--------|---------|
| `source` | Collector id (`edgar_8k`, `greenhouse`, `lever`, …) |
| `timestamp` | Event / filing / posting time (ISO-8601 UTC) |
| `company_name` | Filing entity or employer |
| `raw_text` | Document / posting body |
| `url` | Canonical public URL |

## Current collectors

| Order | Source | Status |
|-------|--------|--------|
| 1 | SEC EDGAR 8-K (full-text search) | **Implemented** |
| 2 | Greenhouse / Lever / Workday / iCIMS job postings | **Implemented** |
| 3 | Parts catalog + Micron obsolete/PCN-EOL ingest | **Implemented** |
| 4 | Samsung family-status watcher | **Implemented** |
| 5 | Part-number decoder (offline) | **Implemented** |
| 6 | Distributor part master (Octopart / DigiKey / mock) | **Implemented** |
| 7 | Lead scorer (classify → S+F+T → tier) | **Implemented (v1)** |
| 8 | Trade press RSS (EE Times, DigiTimes) | Planned |

### EDGAR 8-K

Uses the public full-text search API at `https://efts.sec.gov/LATEST/search-index` with form type `8-K` and these keywords:

- `inventory write-down`
- `program cancellation`
- `supply constraint`
- `memory`
- `DRAM`

Matching documents are fetched from `sec.gov` Archives, HTML is stripped to plain text, and rows are upserted by `(source, accession:filename)`.

### Job boards (Greenhouse / Lever / Workday / iCIMS)

Public board APIs (no auth):

- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
- Lever: `GET https://api.lever.co/v0/postings/{token}?mode=json`
- Workday CXS: `POST {cxs_jobs_url}` then detail GET (Micron, Jabil, Flex, Magna, Valeo, Aptiv, NXP, ADI, Applied Materials, KLA, …)
- iCIMS: HTML search + per-job schema.org `JobPosting` JSON-LD (no public JSON API). Hosts like `careers-rambus.icims.com`.

Title must contain one of: **buyer**, **procurement**, **commodity manager**, **supply chain**. Matching posts store title + location + description as `raw_text`.

Board configs live in [`data/job_boards.json`](data/job_boards.json). Add tokens / CXS URLs / iCIMS hosts there as you discover more boards.

### Parts catalog, families / variants, PCN-EOL

Manufacturer PCN portals are mostly customer-gated. What ships here:

1. **Catalog model** — `part_families` + `part_variants` with first-class **grade** (`consumer` / `industrial` / `automotive` / `military`) and **package** / order-packaging fields. Same die family, different packaging-grade twins stay distinct for PPV and shortage work.
2. **Manual + seed registration** — register families/variants and attach PCN/EOL notices via CLI.
3. **Micron obsolete catalog collector** — public AEM JSON for obsolete DRAM catalogs; upserts variants as `obsolete` and links them to an `obsolete_catalog` notice. Temperature ranges map to grades (`0C…` → consumer, `-40C…` → industrial, `…125C` → automotive).

Samsung / SK hynix PCNs are not published as open feeds; register those notices manually (or from distributor PDFs) with `catalog pcn-add` until a public source appears.

### Samsung family-status watcher (Step 1)

Polls Samsung Semiconductor **family** pages only (from sitemap). Reads
`productStatus` / `productModelName` meta tags — never part pages (those
redirect to family shells with no part-level specs). Status changes and
first-seen EOL emit `signals` rows with `source=samsung_family_status`.

```bash
python -m dram_signals once --source samsung_family_status
# optional cap: DRAM_SIGNALS_SAMSUNG_FAMILY_MAX=10
```

### Part-number decoder (Step 2)

Offline pure functions — no network. Rules in [`data/partnum_rules.yaml`](data/partnum_rules.yaml)
(each rule declares its evidence `source`).

```bash
python -m dram_signals decode K4VAF325ZC-SC28
python -m dram_signals decode H5AN8G6NCJR-VKC
```

Decoded family joins to Step 1 status (e.g. a PN whose family is LPDDR4X
inherits family EOL).

### Distributor part master (Step 3)

Builds a `parts` table (not signals) from:

1. `data/part_seed.txt` (customer / purchase lists)
2. MPNs mined from existing `signals.raw_text`
3. Samsung sitemap part-slug MPNs

Enrichment order: **Octopart/Nexar** (`NEXAR_TOKEN` or `OCTOPART_TOKEN`) →
**DigiKey** (`DIGIKEY_CLIENT_ID` + `DIGIKEY_CLIENT_SECRET`) → **mock** heuristics.
Results are disk-cached under `data/part_master_cache/` for 7 days. Lifecycle
changes emit `source=part_lifecycle` signals.

```bash
# Works offline via mock provider
DRAM_SIGNALS_PART_MASTER_MOCK=1 python -m dram_signals once --source part_master
```

### Airtable export (hub for Clay)

Airtable is the shared public-signals hub; Clay enriches Companies only.
See [`docs/airtable-clay-sync.md`](docs/airtable-clay-sync.md).

```bash
export AIRTABLE_TOKEN=pat...          # data.records:read + write on one base
export AIRTABLE_BASE_ID=app...
python3 export_airtable.py --dry-run --limit 3
python3 export_airtable.py --limit 3  # real upsert; watermark on success
python3 export_airtable.py            # incremental via changed_at
python3 export_csv.py --out data/export_signals.csv   # Clay CSV fallback
```

Never overwrites human fields (Review, Side, …) or Clay enrichment fields.
`dramx_*` rows get Internal Only = true.

### Lead scoring (rubric v1)

Score **leads** (company × supply/demand), not raw documents. After hard gates:

`Signal strength (0–45) + Fit (0–35) + Timing (0–20) → 0–100 → tier A/B/C/D`

```bash
python -m dram_signals score seed          # regime, end-build map, market allocation
python -m dram_signals score examples      # section 4.6 worked examples
python -m dram_signals score run           # classify raw signals + score + snapshot
python -m dram_signals score status
```

Design index: [`docs/scoring-rubric.md`](docs/scoring-rubric.md). Hand tables live in `data/scoring/`. Spot prices from unlicensed free indexes stay **internal only**; customer Timing market-movement uses broker quotes / licensed feeds only.

Broker isolation: `broker_id` on companies, typed signals, purchase log, allocation letters, and snapshots. Public signals are shared; broker-entered data never crosses workspaces.

### Catalog host probes & robots.txt

Before any manufacturer catalog crawl, re-check robots paths:

```bash
export COLLECTOR_CONTACT=you@example.com
python scripts/probe_catalogs.py
```

Findings live in [`docs/robots-findings.md`](docs/robots-findings.md). Headline:

| Host | Canonical `/robots.txt` | Collector stance |
|------|-------------------------|------------------|
| `product.skhynix.com` | **Broken (HTTP 500)** — only a non-standard `/robot.txt` exists | Step 4b **on hold** |
| `www.dramexchange.com` | **Missing (HTTP 404)** | No path rules; still ToS-bound for Step 6 |
| `semiconductor.samsung.com` | **OK** — `/dram/` allowed | Family-status watcher OK |

```bash
# Seed example families with consumer / industrial / automotive twins
python -m dram_signals catalog seed

# Register your own family + packaging-grade variants
python -m dram_signals catalog family-add --manufacturer micron --code MT40A1G8SA \
  --technology DDR4 --density 8Gb --organization x8
python -m dram_signals catalog variant-add --manufacturer micron --family MT40A1G8SA \
  --mpn 'MT40A1G8SA-062E:B' --grade consumer --package TFBGA --temperature '0C to +95C'
python -m dram_signals catalog variant-add --manufacturer micron --family MT40A1G8SA \
  --mpn 'MT40A1G8SA-062E IT:B' --grade industrial --package TFBGA --temperature '-40C to +95C'

python -m dram_signals catalog show MT40A1G8SA
python -m dram_signals catalog variants --family MT40A1G8SA --grade industrial

# Ingest Micron obsolete catalogs into the catalog + PCN tables
python -m dram_signals once --source micron_obsolete

# Attach a real EOL/PCN when you have one
python -m dram_signals catalog pcn-add --manufacturer samsung --notice-id PCN-2025-001 \
  --type eol --title "DDR4 1Znm phase-out" --mpn K4A8G165WC-BCWE --family K4A8G165WC
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

SEC fair-access rules require a descriptive User-Agent with contact info:

```bash
export DRAM_SIGNALS_USER_AGENT="YourOrg DRAMSignals/0.1 (you@example.com)"
```

## Usage

Run everything once:

```bash
python -m dram_signals once
```

Run only job collectors, or a single ATS:

```bash
python -m dram_signals once --source jobs
python -m dram_signals once --source greenhouse
python -m dram_signals once --source lever
python -m dram_signals once --source workday
python -m dram_signals once --source icims
python -m dram_signals once --source micron_obsolete
python -m dram_signals once --source samsung_family_status
python -m dram_signals once --source part_master
python -m dram_signals once --source catalog_watch
python -m dram_signals once --source edgar --start 2026-09-01 --end 2026-10-16
python -m dram_signals decode K3KL2L20DM-JGCT
```

Schedule (default every 6 hours; runs immediately, then repeats):

```bash
python -m dram_signals schedule --source all --every-hours 6
```

Inspect the database:

```bash
python -m dram_signals status
sqlite3 data/signals.db "SELECT source, company_name, keyword FROM signals WHERE source IN ('greenhouse','lever');"
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DRAM_SIGNALS_USER_AGENT` | placeholder research UA | Required for SEC access |
| `DRAM_SIGNALS_DB` | `data/signals.db` | SQLite path |
| `DRAM_SIGNALS_DATA_DIR` | `data/` | Data directory |
| `DRAM_SIGNALS_BOARDS_FILE` | `data/job_boards.json` | Greenhouse/Lever board list |
| `DRAM_SIGNALS_EDGAR_LOOKBACK_DAYS` | `30` | Initial EDGAR lookback |
| `DRAM_SIGNALS_SCHEDULE_HOURS` | `6` | Schedule interval |
| `DRAM_SIGNALS_REQUEST_DELAY` | `0.15` | Seconds between HTTP calls |
| `DRAM_SIGNALS_CATALOG_REQUEST_DELAY` | `2.0` | Delay for Samsung / distributor hosts |
| `DRAM_SIGNALS_SAMSUNG_FAMILY_MAX` | unset | Cap family pages per run |
| `DRAM_SIGNALS_PART_MASTER_MAX` | unset | Cap part enrichments per run |
| `DRAM_SIGNALS_PART_MASTER_MOCK` | unset | Force mock lifecycle provider |
| `NEXAR_TOKEN` / `OCTOPART_TOKEN` | unset | Octopart/Nexar API token |
| `DIGIKEY_CLIENT_ID` / `DIGIKEY_CLIENT_SECRET` | unset | DigiKey Product Information v4 |
| `DRAM_SIGNALS_MAX_RAW_TEXT` | `100000` | Max chars stored per record |

## Layout

```
dram_signals/
  config.py                 # paths, keywords, knobs
  db.py                     # SQLite schema + upsert
  http_client.py            # throttled requests session
  boards.py                 # load data/job_boards.json
  job_titles.py             # buyer/procurement/… title gate
  collectors/edgar.py       # EDGAR 8-K
  collectors/greenhouse.py  # Greenhouse Job Board API
  collectors/lever.py       # Lever Postings API
  collectors/workday.py     # Workday CXS careers API
  collectors/icims.py           # iCIMS HTML + JSON-LD
  collectors/micron_obsolete.py # Micron obsolete part catalogs → catalog/PCN
  catalog/                      # families, variants, grades, PCN linkage
  __main__.py                   # once | schedule | status | catalog
data/job_boards.json            # curated ATS board tokens / CXS URLs / hosts
data/seed_catalog.json          # example families with grade twins
scripts/probe_catalogs.py       # Step 0 read-only robots + catalog probes
docs/robots-findings.md         # SK / DRAMeXchange / Samsung robots paths
data/partnum_rules.yaml         # Step 2 PN decode rules (sourced)
data/part_seed.txt              # Optional customer MPN list for Step 3
data/scoring/                   # regime, market_allocation, end_build_map YAML
dram_signals/scoring/           # classify, formula, scorer, snapshots, CLI
docs/scoring-rubric.md          # Engineering index for the lead rubric
```
