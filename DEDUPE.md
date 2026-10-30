# De-dupe signals at extraction (instructions for Cursor)

Add this to the repo next to `SPEC.md` and build it as part of milestone 2. It changes how `extract.py` writes rows, so do it before anyone votes. Votes attach to `signal_id`, and this change alters how that ID is made.

## Why

The first Micron-plus-buyers run (`signals.csv`, extractor 0.1.0, 188 rows) contains only 69 distinct signals. The duplicates come from three places:

| Source of duplicates | Rows it adds | Example |
|---|---|---|
| Overlapping 3-sentence windows in one document | 64 | The same sentence about DRAM pricing appears in 2–3 windows in Micron's 10-K |
| The same passage repeated in later filings | 39 | Micron risk factors copied from each 10-Q into the next |
| The same passage lightly reworded in a later filing | 16 | "In the first quarter of 2026, we continued to benefit…" then "In the third quarter of 2026, …" |

These counts come from running `dedupe_reference.py` on the fixture. Duplicates inflate counts in the family view and make users vote on the same thing many times.

## What to build

### 1. Stable signal IDs

Replace the `signal_id` rule in `SPEC.md`:

```
signal_id = sha1(f"{company}|{product_family}|{normalize(excerpt)}")[:12]
```

The excerpt used is the one from the **first** filing the passage appeared in. With this rule, re-running extraction produces the same IDs, so votes stay attached.

`normalize`: NFKC, lowercase, curly quotes changed to straight ones, then every run of characters that aren't letters or digits collapsed to one space, then trimmed.

### 2. A new append-only file, `data/sightings.csv`

Each sighting is one appearance of a signal in one filing. `verify.py` checks every sighting's excerpt at its offsets, just as it checks signals today.

| column | note |
|---|---|
| sighting_id | sha1 of filing_id + char_start + char_end + product_family, first 12 chars |
| signal_id | the signal this appearance belongs to |
| filing_id, form, filing_date, section | as in signals.csv |
| period_end | the period the filing covers. From EDGAR `reportDate` in the submissions JSON (confirm the field name against a real response). For DART, use the report period |
| char_start, char_end, raw_path, source_url | where this appearance is, for verify |
| match | `first`, `exact` or `near` |
| similarity | Jaccard score for `near`, otherwise 1.0 |

Before appending, skip any sighting whose `sighting_id` is already in the file, so a re-run adds nothing new.

### 3. `data/signals.csv` becomes one row per signal

Rebuild it from `sightings.csv` on every run. It isn't appended to any more, and its IDs are stable, so rebuilding is safe. Keep every current column, and add:

- `first_seen`, `last_seen`: the earliest and latest `filing_date` among the signal's sightings
- `latest_period_end`: the `period_end` of the latest sighting
- `times_seen`: the number of sightings
- `carried_from_prior_year`: true when the passage also appears in the previous year's 10-K. This replaces `repeat_of`, since `repeat_of` pointed to IDs that never exist in the file.

Recency weighting (later, not v1) should use `last_seen`. A passage that is still being repeated is still current.

### 4. Matching rules, in order

Write these as a pure function over candidate rows, `dedupe(rows) -> (signals, sightings)`, so it can be tested without network access. `dedupe_reference.py` is a working version to port.

1. **Overlapping windows, one document.** Group candidate windows by `raw_path`, `product_family` and `supply_direction`. Where windows overlap, keep one: the window with the most matched terms, then the longest, then more specific family, then the earliest. Windows with **different** directions are kept even if they overlap. In Micron's 10-K, a window saying HBM demand may outrun supply overlaps one saying HBM demand may weaken. Both are real signals. (Extract also collapses heavily overlapping windows **across** families so a span is not emitted three times as DRAM + NAND + Memory_general.)
2. **Exact repeat.** Process rows oldest filing first. Compare `match_text` = `excerpt_en` when `language` is non-English (ko / zh / ja / de) and `excerpt_en` is present, else `excerpt`. If the normalized `match_text` is identical for the same `company` and `supply_direction`, it's a sighting (`match = exact`) **across every product family and source** — Ambarella MCU + Power_IC + Memory_general on one span become one card. Nested-window **containment** (≥80 char substring) still exact-merges only within a **merge group** (all Memory categories together, or the same non-memory category) so a soft Memory⊂Capacitor overlap on a later filing stays two signals. Same language + different filing date of an exact clone → **keep both as sightings** under one `signal_id` (`times_seen` grows). The rebuilt card prefers a more specific family over `Memory_general`.
3. **Near repeat (same language, same source).** For the same `company` and `product_family`, it's a sighting (`match = near`) only if **all** of these hold:
   - the word-set Jaccard similarity to any text already seen for that signal is at least **0.85**
   - `supply_direction` and `sentiment` are the same
   - the numbers in the passage (`\d[\d,.]*%?`) are identical

   The numbers rule is what stops "revenue increased 12%" being merged into "revenue decreased 5%". The Qualcomm pair in the tests below has a similarity of 0.90 and must stay apart.
4. Anything else is a new signal (`match = first`).
5. **Cross-language / cross-source (EN ↔ KO / ZH / JA / DE, any portal).** Same `company`, **any** `product_family`. Near match when `source` differs **or** `canonical_language` differs uses `max(full Jaccard, best sentence Jaccard)` ≥ **0.30**, **same `supply_direction` only** (sentiment is skipped — keyword/MT labels often disagree on the same claim), and filing dates within **7 days** (SK hynix English 6-K often lands a few days after the DART report). Numbers need not be identical across languages. When both exist, the rebuilt signal row **prefers English EDGAR** (then IR, then other) for displayed excerpt/`source`; both filings remain as sightings under one `signal_id`.

### 5. Feed changes

- Show one card per signal, with a line reading "Seen in N filings, latest YYYY-MM-DD". This expands into the list of sightings, each linked to its filing.
- Order: unvoted first, then newest `last_seen` first.
- The family view counts signals, not sightings.

## Tests (add to `tests/test_dedupe.py`)

The fixture is `fixtures/signals_v010.csv`, a copy of the 0.1.0 run. Map each old row to its new signal through the sighting's `old_signal_id`, the fixture row's ID.

| Test | Expected |
|---|---|
| Totals | 188 rows → 124 sightings → 57 signals; 55 exact, 12 near |
| Micron's restated quarterly sentence merges | old IDs `1f0beca2cc6f`, `6ddf3af33877`, `0e6411d58623`, `db2a072beb62` share one new signal |
| Qualcomm increase versus decrease stays apart | `8732382a3a08` and `11282417d705` are different signals |
| Conflicting HBM windows both survive | `5270a139e733` (tightening) and `6726809579d0` (easing) are both kept |
| Re-run is idempotent | running twice adds 0 sightings and gives the same signal IDs |
| verify passes | 100% of sightings verify at their offsets |

Run `python dedupe_reference.py fixtures/signals_v010.csv` to reproduce the expected numbers. Once the real extractor works on raw text, the totals for a fresh run will differ from the fixture. The fixture tests should not change.

## Hand check after building

Read 10 random `near` merges and 10 random signals that have no sightings beyond the first. Note any merge that joined two different claims, and any obvious repeat that was missed. Then tune `NEAR_JACCARD`, but only if a mistake shows up. The 0.85 threshold was checked by reading the 0.80–0.90 pairs in the fixture. It has not been tested on other companies' filings yet.
