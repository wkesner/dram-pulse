# Memory Signals

Qualitative memory-market signals from public filings (see [`SPEC.md`](SPEC.md)).

## Remotes (Origin + GitHub)

- **`origin`** — Cursor Origin (`origin.cursor.com/git/william-kesner/dram-pulse`) — primary.
- **`github`** — public mirror (`https://github.com/wkesner/dram-pulse.git`).

Until GitHub credentials are available on the worker, push both explicitly:

```bash
git push -u origin HEAD
git push -u github HEAD
```

After `gh auth login` (or a PAT in the credential helper), you can enable dual-push so one `git push origin` updates both:

```bash
ORIGIN_URL=$(git remote get-url --push origin)   # keep existing Origin URL
git remote set-url --push --add origin "$ORIGIN_URL"
git remote set-url --push --add origin https://github.com/wkesner/dram-pulse.git
# Do not enable this until `git push github HEAD` succeeds — a failing GitHub
# push URL makes the whole `git push origin` fail.
```

Never commit `.env` or API keys.

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
python3 -m src.verify   # defaults to data/sightings.csv
# Fixture: python3 -m src.dedupe fixtures/signals_v010.csv
```

Output: append-only `data/sightings.csv` and rebuilt `data/signals.csv` (one row per signal; see `DEDUPE.md`). `verify.py` checks every sighting excerpt against `char_start`/`char_end` in the cached raw text (must be 100%).

## Milestone 3 — all EDGAR companies

```bash
export SEC_USER_AGENT="MemorySignals Your Name you@example.com"
python3 -m src.fetch_edgar --all
python3 -m src.extract --all
python3 -m src.verify
```

`config/companies.yaml` lists MU, SKHY, and buyers AAPL DELL HPQ HPE SMCI CSCO NVDA AMD QCOM JBL. Forms: 10-K, 10-Q, 8-K (items 2.02 / 2.06 / 7.01 / 8.01); SKHY also 6-K. CIKs and `fiscalYearEnd` come from EDGAR at runtime.

## Milestone 4 — Feed + family view

The feed UI is **FastAPI + static HTML** (not Streamlit). Design mockups: `docs/design/`. Build sequence: `docs/FEED-BUILD.md`.

```bash
pip install -r requirements.txt
uvicorn app.server:app --reload --port 8000
# open http://localhost:8000/  (static placeholder until Prompt 2+)
# API: GET /api/signals  GET /api/meta  GET /api/votes?user=NAME  POST /api/votes
```

Legacy Streamlit prototype (optional): `streamlit run app.py`.

- Name field required before voting; filters for product category / family / supply direction / company / role / source.
- **Feed**: stacked scroll of signal cards by default (toggle **Layout → One at a time** for the old queue). Sightings expander, 👍 / 👎 / Skip. Votes append to `data/votes.csv` (file-locked); tallies use each user’s latest vote.
- **Family view**: plain counts per product family (signals, not sightings). Header shows the lookback window.

Demo votes (invented users only; does not invent excerpts):

```bash
python3 scripts/simulate_votes.py --reset --cover 0.72 --seed 42
python3 -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

### Broker market simulation

Three fictional DRAM component brokers compete for virtual P&L on the shared
seed catalog (`data/seed_catalog.json`). They buy offers **under** market and
sell customer demand **at or above** market, with partially overlapping
customer books (buyers from `config/companies.yaml` plus invented EMS/ODM
names — not three disjoint books). Starting cash is **$250k** per broker.

| Broker | Personality |
|--------|-------------|
| Apex Component Brokers | Hungry buyer, patient seller, holds inventory |
| Harbor Electronics Trading | Deep discounts only, quick flips at ≥ market |
| Summit Memory Partners | Balanced volume, broadest customer affinity |

Each baseline has a **scored twin** (same strategy + $250k) with fast access to
a simple 0–100 **memory timing score** (shortage ≥60 / glut ≤40; inspired by
rubric S*/D* sides). Twins observe the latent score with **lag=1 day** by
default and nudge buy/sell thresholds; baselines are unscored. Market mid
drift is weakly biased by the true score so earlier information can matter.
See `dram_signals/broker_sim/memory_score.py`.

**Prices:** prefer the free MemoryIndex public board
(`GET https://memoryindex.io/api/public/v1/prices`, no key for a 10-contract
slice; optional `MEMORYINDEX_KEY`). Rows often cite DRAMeXchange / TrendForce
or Silicon Analysts. Free-tier license is **internal use only**. On network
failure, last-good cache at `data/broker_sim/memoryindex_cache.json`, then the
illustrative technology × density × grade model. Not used: DRAMeXchange HTML
scrape (ToS), Ornn/DigiKey spot (paid keys).

```bash
python3 -m scripts.simulate_brokers --days 30 --seed 42          # baselines + scored twins
python3 -m scripts.simulate_brokers --baseline-only --no-live
python3 -m scripts.simulate_brokers --score-lag 1 --json
pytest tests/test_broker_sim.py tests/test_broker_live_prices.py tests/test_broker_scored_twins.py
```

**GUI / graphs** (equity, P&L, cash, inventory; baseline vs scored). Port **8010**
avoids colliding with the feed server on 8000:

```bash
# Local GUI (single mode): explicitly enable the offline demo gate
BROKER_SIM_OPEN=1 uvicorn app.server:app --host 0.0.0.0 --port 8010
# open http://127.0.0.1:8010/brokers.html
# API (token gate): X-Broker-Sim-Token + GET /api/broker-sim?days=30&seed=42&live=false
# Clerk mode: Authorization: Bearer <session JWT> (BROKER_SIM_OPEN does not bypass)
```

Details: `dram_signals/broker_sim/` (`live_prices.py`, `memory_score.py`). Max `days=90`;
open single-tenant demo forces offline prices and caps at 30 days.

## Multi-org demo (Clerk)

### Whole-app login

**Clerk** (`AUTH_MODE=clerk`): every data API and the UI require a signed-in Clerk session **and** an active organization. Unauthenticated visitors see the sign-in gate; `/api/config` stays public so the browser can bootstrap Clerk.

**Single** (`AUTH_MODE=single`): open local demos by default (typed “Voting as” name). Set `APP_ACCESS_TOKEN` to require a shared secret for the whole app — the UI prompts for the token; APIs accept `Authorization: Bearer …` or `X-App-Token`.

### Clerk setup

Prefer the [Clerk CLI](https://clerk.com/docs/cli) (FastAPI is not an accountless `clerk init` framework — we use Clerk JS + backend JWT verify):

```bash
npx -y clerk@latest auth login
npx -y clerk@latest apps list --json          # if several apps, pick one id
npx -y clerk@latest link --app <application_id>
npx -y clerk@latest env pull --file .env      # publishable + secret only
npx -y clerk@latest enable orgs
# then ensure:
#   AUTH_MODE=clerk
#   CLERK_ISSUER=https://<frontend-api>.clerk.accounts.dev   # optional; derived from pk if omitted
#   CLERK_AUTHORIZED_PARTIES=http://127.0.0.1:8000,http://localhost:8000
#   OPERATOR_TOKEN=…   # optional
# Single-tenant shared-secret alternative:
#   AUTH_MODE=single
#   APP_ACCESS_TOKEN=…   # gates UI + /api/*
```

Dashboard still recommended: email verification + bot/CAPTCHA; Allowed origins `http://127.0.0.1:8000` and `http://localhost:8000`.

```bash
pip install -r requirements.txt
python3 -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Open the feed → sign in via Clerk → create/select an organization. The API seeds `data/orgs/{org_id}/` with a private copy of the base corpus. Votes never cross orgs. Anonymized events append to `data/aggregates/events.jsonl` (operator: `GET /api/operator/aggregates` + `X-Operator-Token`).

Isolation tests: `pytest tests/test_clerk_isolation.py`.


## Milestone 5 — OpenDART + Samsung IR

Korean periodic / major-event disclosures via OpenDART, plus Samsung English IR PDFs.

```bash
# Put the 40-character OpenDART key in gitignored .env (never commit it):
# DART_API_KEY=...
cp .env.example .env   # then edit

python3 -m src.fetch_dart          # Samsung 005930 + SK hynix 000660
python3 -m src.fetch_ir            # URLs in config/ir_documents.yaml
export SEC_USER_AGENT="MemorySignals Your Name you@example.com"
python3 -m src.extract --all --no-fetch-prior
python3 -m src.verify              # must be 100%
```

`document.xml` returns a **ZIP** (official OpenDART guide). Cached under `data/raw/{krx_code}/{rcept_no}/` with `meta.json` + `other_full.txt`. Korean rows use `language=ko` and Korean terms from `config/supply_terms.yaml` / `product_taxonomy.yaml`.

## Asia / UK filings (JP · CN · LSE)

Parallel fetch path for electronics-commodity issuers outside SEC/OpenDART.

| Market | Module | Auth | Status |
|--------|--------|------|--------|
| **China (CNINFO)** | `python -m src.fetch_cninfo` | none | **v1 live** — unofficial public JSON on the CSRC-designated site |
| **Taiwan (MOPS/TWSE)** | `python -m src.fetch_mops` | none | **v1 live** — official `openapi.twse.com.tw` |
| **Japan (EDINET)** | `python -m src.fetch_edinet` | `EDINET_API_KEY` | code ready; needs free key on the agent VM |
| **UK (Companies House)** | `python -m src.fetch_companies_house` | `COMPANIES_HOUSE_API_KEY` | code ready; idle until key is on the agent VM |
| **France (AMF Info‑financière)** | `python -m src.fetch_amf` | none | **stub** — free OAM JSON API; wire ISINs next |
| **Germany (UREG)** | `python -m src.fetch_ureg` | none | **v1 live** — Unternehmensregister search + IR PDF bodies (no captcha solver) |
| TDnet (JP timely) | — | — | use EDINET API (site scrape discouraged by ToS) |
| LSE RNS / LSEG | — | commercial | blocked without LSEG credentials |
| FCA National Storage Mechanism | — | browser only | free search/download in UI; public API ~late 2027 |
| AFM registers (NL) | — | portal | thin vs ASML/NXP already on EDGAR |

```bash
# China (no key) — SMIC / GigaDevice / Hua Hong / JCET / Luxshare / NAURA
python3 -m src.fetch_cninfo
python3 -m src.fetch_cninfo --ticker 603986

# Taiwan (no key) — TSMC / MediaTek / ASE / UMC / Winbond / Nanya / …
python3 -m src.fetch_mops
python3 -m src.fetch_mops --ticker 2330

# Japan — after EDINET_API_KEY is on this VM (.env or Cursor environment secret)
python3 -m src.fetch_edinet
python3 -m src.fetch_edinet --ticker 8035

# UK Companies House — after COMPANIES_HOUSE_API_KEY is on this VM
python3 -m src.fetch_companies_house
python3 -m src.fetch_companies_house --ticker RSW

# Germany — Unternehmensregister listing + IR PDF mirrors (IFX / WAF smoke)
python3 -m src.fetch_ureg
python3 -m src.fetch_ureg --ticker IFX
python3 -m src.fetch_ureg --list-only --ticker WAF

# Extract + verify (same dedupe path as EDGAR/DART)
export SEC_USER_AGENT="ECSignals Your Name you@example.com"
python3 -m src.extract --all --no-fetch-prior
python3 -m src.verify
```

Cache layout matches DART: `data/raw/{ticker}/{filing_id}/meta.json` + `other_full.txt`. Languages: `zh` (CNINFO), `zh-TW` (MOPS), `ja` (EDINET), `en` (CH), `de`/`en` (UREG). Chinese / Japanese starter terms live in `config/supply_terms.yaml` and `config/product_taxonomy.yaml`. German commodity terms land in a follow-on PR.

### Auth / rate limits / ToS (summary)

- **CNINFO**: no key. Soft-cap ~1 r/s. Unofficial `hisAnnouncement/query` + `static.cninfo.com.cn` PDFs; treat as best-effort and prefer any future official API.
- **MOPS/TWSE**: no key. Official OpenAPI (`t187ap04_L` material info + `t187ap05_L` monthly revenue). Snapshots are latest-period (not a historical range) — re-runs accumulate cache. Prefer OpenAPI over MOPS HTML (TWSE ToS discourages automated scrape of non-API pages). Soft ≤1 r/s.
- **UREG (Unternehmensregister)**: no key. Search-token + ACCOUNTING listing; prefer pub types 135/136/137. Document bodies often need captcha — follow IR/`config/ureg_documents.yaml` PDFs instead (no captcha solver; no BaFin MVP SOAP). Soft ≤0.5 r/s; robots.txt disallows `/de/search`.
- **EDINET**: free key (email + MFA). Prefer API over website scrape (ToS). No published hard RPS — stay ≤1 doc/s, cache, backoff.
- **Companies House**: free key; Basic auth; **600 req / 5 min** (HTTP 429). Document API for PDF / iXBRL.
- **LSE RNS**: LSEG Announcement API needs commercial user/accessKey; not free.
- **FCA NSM**: free human search/CSV export; no programmatic retrieval API yet.

### v2 exchange roadmap (secondary / regional)

Ranked for electronic-commodity supply signals + information imbalance. Prefer official open data. **Do not start these until Taiwan/JP/UK keys are green.**

| Rank | Market | Source | Key? | Why next | Caveats |
|------|--------|--------|------|----------|---------|
| 1 | **AMF Info‑financière (FR)** | `info-financiere.gouv.fr` explore API | No | Best free EU filing API after EDGAR dual-lists | FR/EN mix; wire ISINs (`fetch_amf` stub) |
| 2 | **BaFin / Unternehmensregister (DE)** | Portal publications | Free portal; limited API | High DE electronics density (Infineon, …) | German text; clunky register UX; no clean API |
| 3 | **AFM registers (NL)** | Financial-reporting HTML register | Free portal | NL names beyond ASML/NXP | Thin vs EDGAR dual-lists |
| 4 | **HKEX** | HKEXnews title search + PDF | No free official API | Dense electronics/ODM/foundry H-shares | Portal ToS / anti-bot |
| 5 | **SEDAR+ (Canada)** | CSA API / bulk | Free account for some feeds | Memory/packaging via TSX | API access tiers vary |
| 6 | **ASX (Australia)** | ASX announcements | Mostly free portal | Thin electronics; clean English | No rich open API |
| 7 | **India BSE/NSE** | BSE/NSE archives | Mixed free | Growing EMS/OSAT/design density | Endpoint stability uneven |

Already covered: **SEC EDGAR** (incl. EU dual-lists STM/NXPI/ASML), **Korea OpenDART**, **China CNINFO**, **Taiwan TWSE OpenAPI**, **Japan EDINET** (key), **UK Companies House** (key). AMF stubbed. Skip paid LSE RNS until LSEG credentials exist; revisit FCA NSM ~2027. Prefer EDGAR dual-lists over Infineon OTC `IFNNY` (no live 20-F).

**English toggle (SPEC later):** originals stay in `excerpt`. Fill `excerpt_en` offline with Argos Translate (`pip install 'argostranslate>=1.9,<2'`), then:

```bash
python3 -m src.translate_excerpts   # downloads ko→en package on first run
```

Feed cards with `language != en` (and a non-empty `excerpt_en`) show **Show English** / **Original**. English rows leave `excerpt_en` blank. Re-extract preserves existing translations by `signal_id`.

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
  broker_sim/                   # three overlapping DRAM brokers (P&L sim)
  __main__.py                   # once | schedule | status | catalog
data/job_boards.json            # curated ATS board tokens / CXS URLs / hosts
data/seed_catalog.json          # example families with grade twins
scripts/probe_catalogs.py       # Step 0 read-only robots + catalog probes
scripts/simulate_brokers.py     # CLI for broker market simulation
docs/robots-findings.md         # SK / DRAMeXchange / Samsung robots paths
data/partnum_rules.yaml         # Step 2 PN decode rules (sourced)
data/part_seed.txt              # Optional customer MPN list for Step 3
data/scoring/                   # regime, market_allocation, end_build_map YAML
dram_signals/scoring/           # classify, formula, scorer, snapshots, CLI
docs/scoring-rubric.md          # Engineering index for the lead rubric
```
