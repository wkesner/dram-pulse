# DRAM Signal Pipeline

Phase 1 collectors for a DRAM market signal-detection pipeline. Public sources are pulled into a local SQLite table of normalized records; a later classification step will tag demand-side vs supply-side. Collectors capture broadly and do not filter.

## Record shape

| Column | Meaning |
|--------|---------|
| `source` | Collector id (`edgar_8k`, later `greenhouse`, `lever`, …) |
| `timestamp` | Event / filing time (ISO-8601 UTC) |
| `company_name` | Filing entity or employer |
| `raw_text` | Document / posting body |
| `url` | Canonical public URL |

## Current collectors

| Order | Source | Status |
|-------|--------|--------|
| 1 | SEC EDGAR 8-K (full-text search) | **Implemented** |
| 2 | Greenhouse / Lever job postings | Planned |
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

Run once (default: last 30 days, or since the stored watermark):

```bash
python -m dram_signals once
```

Narrow the window or skip document downloads (metadata only):

```bash
python -m dram_signals once --start 2026-09-01 --end 2026-10-16
python -m dram_signals once --metadata-only --lookback-days 7
```

Run on a schedule (default every 6 hours; runs immediately, then repeats):

```bash
python -m dram_signals schedule --every-hours 6
```

Inspect the database:

```bash
python -m dram_signals status
sqlite3 data/signals.db "SELECT company_name, timestamp, keyword FROM signals LIMIT 10;"
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DRAM_SIGNALS_USER_AGENT` | placeholder research UA | Required for SEC access |
| `DRAM_SIGNALS_DB` | `data/signals.db` | SQLite path |
| `DRAM_SIGNALS_DATA_DIR` | `data/` | Data directory |
| `DRAM_SIGNALS_EDGAR_LOOKBACK_DAYS` | `30` | Initial lookback |
| `DRAM_SIGNALS_SCHEDULE_HOURS` | `6` | Schedule interval |
| `DRAM_SIGNALS_REQUEST_DELAY` | `0.15` | Seconds between HTTP calls |
| `DRAM_SIGNALS_MAX_RAW_TEXT` | `100000` | Max chars stored per record |

## Layout

```
dram_signals/
  config.py           # paths, keywords, knobs
  db.py               # SQLite schema + upsert
  http_client.py      # throttled requests session
  collectors/edgar.py # EDGAR 8-K collector
  __main__.py         # once | schedule | status
data/                 # created at runtime (gitignored DB)
```
