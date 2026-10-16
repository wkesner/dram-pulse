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
| 2 | Greenhouse / Lever / Workday job postings | **Implemented** |
| 3 | Manufacturer PCN/EOL (Micron, Samsung, SK hynix) | Planned |
| 4 | Trade press RSS (EE Times, DigiTimes) | Planned |

### EDGAR 8-K

Uses the public full-text search API at `https://efts.sec.gov/LATEST/search-index` with form type `8-K` and these keywords:

- `inventory write-down`
- `program cancellation`
- `supply constraint`
- `memory`
- `DRAM`

Matching documents are fetched from `sec.gov` Archives, HTML is stripped to plain text, and rows are upserted by `(source, accession:filename)`.

### Job boards (Greenhouse / Lever / Workday)

Public board APIs (no auth):

- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
- Lever: `GET https://api.lever.co/v0/postings/{token}?mode=json`
- Workday CXS: `POST {cxs_jobs_url}` then detail GET (used for Micron, Jabil, Flex, Magna, Valeo, Aptiv, NXP, ADI, Applied Materials, KLA, …)

Title must contain one of: **buyer**, **procurement**, **commodity manager**, **supply chain**. Matching posts store title + location + description as `raw_text`.

Board configs live in [`data/job_boards.json`](data/job_boards.json). Micron is on Workday (not Greenhouse/Lever). Major EMS and automotive/electronics OEMs are included where a public ATS endpoint was found — add more tokens/URLs there as you discover them.

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
python -m dram_signals once --source edgar --start 2026-09-01 --end 2026-10-16
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
  __main__.py               # once | schedule | status
data/job_boards.json        # curated ATS board tokens / CXS URLs
```
