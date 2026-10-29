# EC Signals: build spec (v1)

Paste this into a new Cursor repo as `SPEC.md` and build in the order of the milestones at the bottom.

## Goal

Collect qualitative signals about memory product lines (DRAM, HBM, NAND, SSD) from public filings of memory makers and their large customers, and log each one as a row in a CSV. Show the signals in a simple feed where users vote thumbs up or down, and log every vote. **v1 does no scoring.** Votes are collected so signals can be weighted later.

The end goal is to point a salesperson at a **memory product category or family**, for example "HBM3E looks tight" or "client SSD supply looks loose". Every signal is therefore tagged with a product family.

## What counts as a signal

A **signal** is a passage of 1–3 sentences from a filing that does all of the following:

1. **Names a memory product**: a specific family (e.g. HBM3E, DDR5, enterprise SSD) or memory in general ("DRAM", "memory").
2. **Says something about the supply picture** for it: availability, shortages or excess, allocation, lead times, capacity, yields, inventory, pricing, or demand outpacing or lagging supply.
3. **Carries a direction**: the passage is either positive or negative about that supply picture. Purely factual mentions with no direction ("we sell DDR5 modules") are not signals.

Each signal records two directions, because the same sentence reads differently to a buyer and to a seller:

- `supply_direction`: what the passage implies about availability. `tightening` (shortage, allocation, longer lead times, prices rising), `easing` (excess inventory, oversupply, prices falling, ample capacity), `stable`, or `unclear`.
- `sentiment`: the tone of the writer. `positive`, `negative` or `mixed`. An illustrative (not quoted) sentence like "HBM demand significantly exceeds our supply" from Micron is `tightening` with `positive` sentiment. The same fact from Super Micro is `tightening` with `negative` sentiment.

`supply_direction` is the field that will steer salespeople. `sentiment` is kept because it shows who is under pressure.

## Product taxonomy

The taxonomy lives in `config/product_taxonomy.yaml` and maps synonyms to a family. A passage that names several families produces one signal per family.

| category | family | example matching terms |
|---|---|---|
| DRAM | HBM | HBM, HBM3, HBM3E, HBM4, high-bandwidth memory |
| DRAM | DDR5 | DDR5, server DRAM, RDIMM, MRDIMM |
| DRAM | DDR4 | DDR4, legacy DRAM |
| DRAM | LPDDR | LPDDR4X, LPDDR5, LPDDR5X, mobile DRAM, LPCAMM |
| DRAM | GDDR | GDDR6, GDDR7, graphics memory |
| DRAM | DRAM_general | DRAM, memory (when it plainly means DRAM) |
| NAND | Data center SSD | enterprise SSD, data center SSD, eSSD, QLC SSD |
| NAND | Client SSD | client SSD, PC SSD |
| NAND | Mobile/embedded | UFS, eMMC, mobile NAND |
| NAND | NAND_general | NAND, flash |
| Other | CXL | CXL, memory expansion |
| Other | Memory_general | memory, memory and storage (no family given) |
| Compute | GPU | GPU, GPUs, graphics processing unit(s) |
| Compute | CPU | CPU, CPUs, microprocessor(s) |
| Compute | FPGA | FPGA, FPGAs, field-programmable gate array(s) |
| Compute | MCU | MCU, MCUs, microcontroller(s) |
| Analog | Power_IC | PMIC(s), power management IC(s), power IC(s) |
| Storage | HDD | HDD(s), hard disk drive(s), high-capacity HDDs |
| Storage | SSD | SSD(s), solid-state drive(s) (enterprise/client SSD stay under NAND) |
| Interconnect | Connector | electronic/electrical/fiber connectors; connector/interconnect shortage or lead-time phrases (avoid bare connector/interconnect) |
| Passives | Capacitor | ceramic/electrolytic capacitor phrasing |
| Passives | MLCC | MLCC(s), multilayer ceramic capacitor(s) |
| Passives | Resistor | chip resistor(s), resistor shortage(s) |

Korean synonyms go in the same file (for example 고대역폭메모리 for HBM, 낸드 for NAND, 디램 for DRAM). These are suggestions to review.

## Non-goals for v1

- No scoring, ranking or weighting model.
- No LLM summarisation. Extraction is rule-based so every row can be traced to exact source text. LLM extraction can be added later behind the same CSV schema.
- No hosted deployment or user accounts. Users are a name typed into the feed.

## Principle: every signal must be verifiable

Every row stores the exact excerpt, its character offsets in the cached source text, and the source URL. `verify.py` re-opens the cached source and checks the excerpt exists at those offsets. Any row that fails is a bug. This is the main guard against the tool inventing or mangling content.

## Companies (v1)

All companies live in `config/companies.yaml`. Adding one is a single entry, and CIKs and DART codes are looked up at runtime, never typed by hand.

| Company | Role | Source | Identifier in config |
|---|---|---|---|
| Micron | supplier | EDGAR (10-K, 10-Q, 8-K) | ticker `MU` |
| SK hynix | supplier | EDGAR (F-1, 6-K, in English) **and** DART (Korean) | ticker `SKHY`; KRX stock code `000660` |
| Samsung Electronics | supplier | DART (Korean) + English reports on Samsung's IR site | KRX stock code `005930` |
| Apple, Dell, HP Inc., HPE, Super Micro, Cisco, Nvidia, AMD, Qualcomm, Jabil | buyer | EDGAR (10-K, 8-K) | tickers `AAPL DELL HPQ HPE SMCI CSCO NVDA AMD QCOM JBL` |

Notes:
- Nvidia, AMD and Qualcomm design chips but don't build them. They're included deliberately (William agreed on 2026-10-20) because they are among the largest buyers of HBM and LPDDR, so their filings name memory constraints directly.
- Each buyer files 10-Ks on its own fiscal calendar. Store `fiscal_year_end` from the EDGAR submissions data, not by assumption.
- SK hynix's 6-Ks appear to be English versions of its Korean disclosures (inferred, not confirmed), so expect duplicates. Deduplicate by date plus normalised title. Prefer the English EDGAR 6-K and keep the DART receipt number as a cross-reference.
- **Company tiers** (`config/tiers.yaml`, field `tier` on each `companies.yaml` row): **Tier 1** = primary Signal map (household / core); **Tier 2** = Fortune 500–1000 electronics, datacenter, distribution, EMS peers; **Tier 3** = smaller listed / broker-dense. Maps are scoped by tier (default Tier 1); the feed can filter across tiers. Lower tiers are intentional — brokers often care more about Tier 2–3 than about Apple.

## Sources and how to fetch them

### EDGAR (no key)
- Ticker to CIK: `https://www.sec.gov/files/company_tickers.json`.
- Filing list: `https://data.sec.gov/submissions/CIK{cik:010d}.json` gives form, filingDate, accessionNumber, primaryDocument and, for 8-Ks, `items`.
- Documents: `https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{primaryDocument}`. For 8-Ks, also fetch exhibit 99.x (the press release) from the filing index.
- **Required:** send a `User-Agent` header of the form `"ECSignals your-name your-email"` (legacy `MemorySignals …` still accepted), and stay under 10 requests per second. SEC blocks clients that don't.
- Scope: every 10-K, 10-Q, 8-K (items 2.02, 2.06, 7.01 or 8.01) and 6-K filed inside the **lookback window** (see below).
- Sections to split out of 10-K/10-Q: Item 1 (Business), Item 1A (Risk Factors), Item 7 (MD&A). Keep the full text too, with section set to `other`.

### DART / OpenDART (free key)
- Get a key at opendart.fss.or.kr (email signup; the key is 40 characters). Put it in `.env` as `DART_API_KEY`, never in the repo.
- `GET https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=...` returns a ZIP containing `CORPCODE.xml`. Cache it, and map stock code to the 8-digit `corp_code`.
- `GET https://opendart.fss.or.kr/api/list.json` with `crtfc_key, corp_code, bgn_de, end_de, pblntf_ty, page_no, page_count` returns filings with `rcept_no`.
- Original document: `document.xml?crtfc_key=...&rcept_no=...` returns a **ZIP** (binary). Confirmed against the official OpenDART guide (apiId 2019003 / DS001): response is a Zip FILE; browsers that force a `.xml` download should be renamed to `.zip` before opening.
- Scope: periodic reports (business, half-year and quarterly reports) and major-event disclosures inside the lookback window.
- Language: store the Korean text as-is, with `language = ko`. Match against a Korean term list (below). Translation is a later milestone.

### Samsung IR site (English)
- Samsung posts English business reports as PDFs on its IR site, for example `images.samsung.com/is/content/samsung/assets/global/ir/docs/2025_4Q_Interim_Report.pdf`. Start with a hand-maintained list of PDF URLs in `config/ir_documents.yaml`. Extract the text with `pypdf` or `pdfplumber`.

## Lookback window

- Set in `config/settings.yaml` as `lookback_months: 12`. William chose 12 months on 2026-10-20 for the current market; revisit it if the market shifts significantly.
- Every run includes filings with a filing date in `[run_date − lookback_months, run_date]`.
- **Every row in signals.csv records `lookback_months`, `window_start` and `window_end`**, so a CSV opened months later still says which window produced it. Changing the setting never rewrites old rows.

## Extraction rules (v1)

The term lists in `config/product_taxonomy.yaml` and `config/supply_terms.yaml` are starting points to edit.

- Split the text into sentences, and treat a window of up to 3 sentences as one passage.
- A passage becomes a signal when it contains a **product** term from the taxonomy, a **supply topic** term, and at least one **direction** term. These are the three tests in "What counts as a signal".
- Supply topic terms: supply, shortage, constrained, allocation, lead time, capacity, yield, qualification, inventory, pricing/price, demand, sold out, bit growth, wafer starts.
- `supply_direction` terms:
  - tightening: shortage, constrained, tight, allocation, sold out, exceeds supply, extended lead times, price increase, undersupply
  - easing: excess, oversupply, inventory build, elevated inventory, price decline, softness, ample, write-down
  - Neither present: `stable` if a stability term appears ("balanced", "normalized"), otherwise `unclear`.
  - Both present: the closer one to the product term wins, and the row is flagged `direction_conflict = true` for review.
- `sentiment` terms: positive (strong, record, robust, improved, favorable, growth) and negative (headwind, challenging, adverse, weak, decline, risk, unable). Both present gives `mixed`.
- Korean terms (to review): 공급 부족 (supply shortage, tightening), 공급 과잉 (oversupply, easing), 재고 (inventory), 수율 (yield), 감산 (production cut, tightening), 가격 상승 (price increase, tightening), 가격 하락 (price decline, easing), 증설 (capacity expansion), 수요 강세 (strong demand).
- `topic` is the first matching group: `availability`, `pricing`, `inventory`, `capacity`, `yield_quality`, `demand`.
- Boilerplate risk factors repeat every year. De-dupe (see `DEDUPE.md`) collapses overlapping windows and exact/near repeats into one signal with multiple sightings. Flag `carried_from_prior_year` when the passage also appears in the previous year's 10-K. A 12-month window usually holds only one 10-K, so also fetch the previous 10-K. Use it only for this comparison, and never extract signals from it.
- Keyword rules will mislabel some direction and sentiment. Milestone 2 includes a hand check of those labels specifically. Votes in the feed will also show which rows were mislabelled.

## Data files

`data/sightings.csv` is append-only. `data/signals.csv` is rebuilt from sightings on every extract run (IDs are stable). Raw downloads are cached in `data/raw/` and never edited. See `DEDUPE.md` for matching rules.

### `data/signals.csv`

One row per distinct signal (rebuilt from sightings).

| column | example / note |
|---|---|
| signal_id | sha1 of `company\|product_family\|normalize(excerpt)` from the **first** filing, first 12 chars. `normalize`: NFKC, lowercase, curly→straight quotes, non-alnum runs→space, trim |
| company | Micron |
| ticker_or_code | MU / 000660 |
| role | supplier / buyer |
| source | EDGAR / DART / IR |
| form | 10-K, 8-K, 6-K, business report, IR-PDF (from first sighting) |
| filing_date | earliest filing_date among sightings (`first_seen`) |
| filing_id | EDGAR accession number or DART rcept_no (first sighting) |
| section | risk_factors, mdna, business, exhibit_99, other |
| product_category | DRAM / NAND / Other |
| product_family | HBM |
| topic | availability, pricing, inventory, capacity, yield_quality, demand |
| supply_direction | tightening / easing / stable / unclear |
| sentiment | positive / negative / mixed |
| direction_conflict | true / false |
| matched_terms | "product:HBM3E; topic:supply; dir:exceeds supply; sent:strong" |
| excerpt | exact text from the first sighting, max 1,200 characters |
| language | en / ko |
| char_start, char_end | offsets into the cached text file (first sighting) |
| raw_path | data/raw/... cached text the offsets refer to |
| source_url | link back to the original filing |
| first_seen | earliest `filing_date` among sightings |
| last_seen | latest `filing_date` among sightings |
| latest_period_end | `period_end` of the latest sighting (EDGAR `reportDate`) |
| times_seen | number of sightings |
| carried_from_prior_year | true when the passage also appears in the prior-year 10-K |
| lookback_months | 12 |
| window_start, window_end | 2025-10-20, 2026-10-20 |
| extractor_version | 0.2.0 |
| extracted_at | ISO timestamp |

### `data/sightings.csv`

One appearance of a signal in one filing. Append-only; skip existing `sighting_id` on re-run. `verify.py` checks every sighting at its offsets.

| column | note |
|---|---|
| sighting_id | sha1 of filing_id + char_start + char_end + product_family, first 12 chars |
| signal_id | the signal this appearance belongs to |
| filing_id, form, filing_date, section | as in signals.csv |
| period_end | period the filing covers; from EDGAR submissions `reportDate` |
| char_start, char_end, raw_path, source_url | where this appearance is, for verify |
| match | `first`, `exact`, or `near` |
| similarity | Jaccard for `near`, otherwise 1.0 |

### `data/votes.csv`

| column | note |
|---|---|
| vote_id | uuid4 |
| signal_id | links to signals.csv |
| user | name typed in the feed |
| vote | +1 (useful), -1 (not useful), or 0 (skip) |
| voted_at | ISO timestamp |

Votes are a separate file so several users never overwrite the signals file, and so later weighting has one row per user per vote. A changed vote is a new row, and the latest row per (user, signal) wins. `0` means the user skipped the card; skips do not count in 👍/👎 tallies. The feed hides any signal this profile has already rated or skipped.

## Feed UI

- Built with **FastAPI** (`app/server.py`) plus static HTML under `app/static/` (not Streamlit). Design references live in `docs/design/`. See `docs/FEED-BUILD.md` for the build sequence.
- API: `GET /api/signals` (each signal with its sightings list and vote tallies), `GET /api/votes?user=NAME`, `POST /api/votes` (append-only; file-locked). Votes: latest row per (user, signal) wins.
- Sidebar / filter rail: user name (required before voting), plus filters for product category, product family, supply direction, company, role and source.
- Main area: one card at a time showing company and role, form, date, product family, supply direction, sentiment, the excerpt with matched terms highlighted, and a link to the source. A line reading "Seen in N filings, latest YYYY-MM-DD" expands into the list of sightings, each linked to its filing. Buttons: 👍, 👎, Skip.
- Order: only signals this user hasn't rated or skipped, newest `last_seen` first. Skip persists as `vote=0` so a reload does not re-show those cards.
- Each vote is appended to `votes.csv` immediately. The card shows the running 👍/👎 counts from all users (skips excluded).
- **Family view** (second tab, the first step toward steering): a table with one row per product family showing counts of tightening, easing, stable and unclear **signals** (not sightings) in the window. It also shows how many come from suppliers versus buyers, and the date of the latest signal. Clicking a family filters the feed to it. These are plain counts, not a score. The column header shows the lookback window.

## Repo layout

```
config/settings.yaml  companies.yaml  product_taxonomy.yaml  supply_terms.yaml  ir_documents.yaml
src/fetch_edgar.py  fetch_dart.py  fetch_ir.py  sections.py  extract.py  dedupe.py  verify.py
app/server.py  app/static/
docs/design/  docs/FEED-BUILD.md
DEDUPE.md
data/raw/  data/signals.csv  data/sightings.csv  data/votes.csv
fixtures/signals_v010.csv
tests/
.env.example   (SEC_USER_AGENT, DART_API_KEY)
```

## Milestones (each one gets checked before moving on)

1. **EDGAR fetch for Micron only.** Done when Micron's latest 10-K is cached and its Item 1A/7 sections are split out. Check: open the cached text and confirm the Item 1A heading and first paragraph match the filing on sec.gov.
2. **Extraction and verify.** Done when `signals.csv` is produced for Micron and `verify.py` passes on 100% of rows. Check: read 20 random rows against the source by hand, and for each one note whether `product_family`, `supply_direction` and `sentiment` are right. Tune the term lists until most are right before moving on.
3. **All EDGAR companies.** Run the fetch for the 10 buyers plus SK hynix 6-Ks. Check: a per-company count of filings and signals; any company with 0 signals gets looked at.
4. **Feed and family view.** Done when two different user names can vote on the same signal, `votes.csv` shows both rows, and the card counts update. The family view's counts must match a manual count from `signals.csv` for one family.
5. **DART and Samsung IR.** Add an OpenDART key. Done when Korean signals are produced for Samsung and SK hynix, with verify passing.
6. **Later:** translation of Korean excerpts (stored in a new `excerpt_en` column, original kept), sweeping all public electronics companies by SIC code (3570–3679), and LLM-based extraction.

## Decisions log

- 2026-10-20: Company list confirmed, including Nvidia, AMD and Qualcomm.
- 2026-10-20: Lookback is 12 months for all filing types, recorded on every CSV row.
- 2026-10-20: A signal is positive or negative supply sentiment about a memory product or family (definition above).
- 2026-10-20: De-dupe per DEDUPE.md — stable `signal_id`, append-only `sightings.csv`, rebuild `signals.csv`; extractor 0.2.0. M4 proof votes cleared (old IDs obsolete).
- 2026-10-20: Feed UI moves from Streamlit to FastAPI + static HTML (`app/server.py`); Streamlit `app.py` kept as a legacy reference until the new UI is complete.
