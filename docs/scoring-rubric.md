# Lead scoring rubric (v1)

Score **leads**, not documents. A lead is one `company × side` (demand: may need DRAM; supply: may have excess). Signals are evidence on a lead. After hard gates:

**Signal strength (0–45) + Fit (0–35) + Timing (0–20) = 0–100 → tier A/B/C/D**

v1 weights are hand-set (`rubric_version=v1`). Every routed lead is snapshotted; CRM outcomes feed Stage 1+ learning (pooled outcome rates across brokers; broker-entered data never crosses workspaces).

Bloomberg-terminal product rule: every paying broker sees the same public signals; nobody is told who else is looking. Broker-entered data (allocation letters, purchase logs, relationships, fit settings) never affects another broker’s scores.

Full design: see product brief (sections 1–8). This file is the engineering index for `dram_signals.scoring`.

## Build order (section 7)

| # | Piece | Status |
|---|-------|--------|
| 1 | Classification → typed signals | **v1** |
| 2 | Entity resolution + end builds | **v1** (name/SIC; Clay later) |
| 3 | Regime / market_allocation / end_build_map / spot_prices | **v1** (hand YAML + spot table) |
| 4 | Gross margin (SEC XBRL) | stub defaults |
| 5 | Scorer + worked examples as tests | **v1** |
| 6 | `score_snapshots` + CRM push | snapshots **v1**; CRM later |
| 7 | Broker-private D0 + purchase log | schema + API stub |
| 8 | CRM outcome reader | later |
| 9 | Weekly report | later |

## Spot licensing

DRAMeXchange / TrendForce free tables are **internal only** until written commercial consent. Customer-facing scores use **broker quotes** (and licensed indexes when available). Market-movement Timing is **0** when no allowed reading exists for that family.

## Worked examples (section 4.6)

Locked as unit tests in `tests/test_scoring_examples.py`.
