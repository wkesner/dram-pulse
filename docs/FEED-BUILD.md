# Building the feed and signal map in Cursor

This is how I would direct Cursor to rebuild the two mockups as the real app, and how to iterate on the design with it. Work through the prompts below one at a time, in order. Each one ends with a check. Don't start the next prompt until that check passes.

Before starting, copy the `reference/` folder from this directory into the repo as `docs/design/`.

| File | What it is |
|---|---|
| `feed_mockup.html` | The feed and Families tab: layout, colors, fonts, card behavior. Its data placeholder is `__DATA__`. |
| `signal_map_dashboard.html` | The signal map's chart code: diverging family bars, month columns, company bars. It runs inside a dashboard runtime, so the `dash.*` calls must be replaced. The `ms.bars` and `ms.months` functions are plain d3 and port directly. |
| `feed-desktop-light.png`, `feed-desktop-dark.png`, `feed-phone-light.png`, `families-desktop-light.png` | What "done" looks like |

## One change from SPEC.md: the front end

SPEC.md says Streamlit. For these designs I'd use a small **FastAPI** server that reads and writes the CSVs, plus **one static HTML page** with plain JavaScript and d3 from a CDN. There's no build step.

The reasons:
- Streamlit re-runs the whole script on every click, so instant voting and click-a-bar-to-filter are awkward.
- The cards and charts would end up as custom HTML components inside Streamlit anyway.
- The mockups are already HTML, so Cursor can port them instead of reinventing them.

If you'd rather stay on Streamlit, tell Cursor that in prompt 1. Prompts 3 to 6 then become "match this screenshot as closely as Streamlit allows".

## The prompts

Paste each block into Cursor's agent chat. Attach the screenshot named in the prompt; Cursor accepts pasted images.

### Prompt 1: the API

```
Read SPEC.md and DEDUPE.md. Build app/server.py with FastAPI:
- GET /api/signals: every row of data/signals.csv as JSON. For each signal, attach its sightings from data/sightings.csv as a list (filing_date, form, section, match, source_url, excerpt). Also attach the vote tallies (latest vote per user wins: up, down).
- GET /api/votes?user=NAME: that user's latest vote per signal_id.
- POST /api/votes {signal_id, user, vote: 1|-1}: append one row to data/votes.csv (vote_id uuid4, voted_at ISO UTC). Reject an unknown signal_id or an empty user with a 400 error.
- Serve app/static/ at /.
Use a file lock when appending to votes.csv. Write tests in tests/test_api.py with a temp copy of the fixture CSVs.
```

**Check:** the tests pass, and `curl localhost:8000/api/signals | jq length` gives the same number as `python -c "import pandas as pd; print(len(pd.read_csv('data/signals.csv')))"`.

### Prompt 2: the page shell and design tokens

```
Create app/static/index.html, styles.css and app.js. Copy the :root color tokens, the dark-mode blocks and the three Google Fonts (Newsreader, Public Sans, IBM Plex Mono) exactly from docs/design/feed_mockup.html. Build only the frame: the top bar with the title, the Feed / Signal map / Families tabs and the "Voting as" name field; the filter rail on the left that stacks on top below 760px; an empty main area. Match docs/design/feed-desktop-light.png for spacing and type. Save the user name in localStorage.
```

**Check:** open the page at desktop and phone width, in light and dark mode. Nothing scrolls sideways, and the frame matches the screenshot.

### Prompt 3: the feed card

```
Port the feed card from docs/design/feed_mockup.html (renderCard, highlight, fragment, latest) into app.js, reading data from /api/signals instead of the embedded JSON. Votes go to POST /api/votes. Keep:
- matched terms highlighted with <mark>, built with DOM nodes, never innerHTML
- "Open filing at this passage" using the #:~:text= fragment (first five words, last five words)
- "Seen in N filings, latest DATE" expanding to each sighting, each with its own passage link
- Useful / Not useful / Skip, with running counts from all users
- order: unvoted first, then newest last_seen
Filters in the rail: category, family, direction, company, role, section.
```

**Check:**
- Vote on a card under two different names. `data/votes.csv` gains two rows, and the counts on the card update.
- "Open filing at this passage" lands on the highlighted passage for one 10-K row and one 8-K press-release row.

### Prompt 4: the Families tab

```
Port renderFamilies from docs/design/feed_mockup.html: one row per product family with tightening, easing, supplier and buyer counts, small inline bars, and the latest last_seen date. Clicking a row opens the Feed tab filtered to that family. Counts are signals, not sightings. Show the lookback window in the header. Match docs/design/families-desktop-light.png.
```

**Check:** pick one family. Its tightening count matches `pandas: df[(df.product_family=='DRAM_general') & (df.supply_direction=='tightening')].shape[0]`.

### Prompt 5: the signal map

```
Add a Signal map tab. Port ms.bars and ms.months from docs/design/signal_map_dashboard.html (they are plain d3 v7). Replace dash.data/dash.params with your own state object and data from /api/signals. Build:
1. A row of four headline numbers: signals, tightening (with % of signals), easing, companies.
2. "Supply direction by product family": diverging horizontal bars, easing to the left of zero, tightening to the right, each split into supplier (darker) and buyer (lighter). Both sides use one scale.
3. "Signals by filing month": tightening above the baseline, easing below, one column per month of the lookback window, including empty months. Count sightings by filing month, since this chart shows when filers said things.
4. "Who is saying it": the same diverging bars, one row per company, suppliers first.
5. A "Leave out risk factors" toggle above the charts.
Clicking a family bar filters every card on the tab; clicking it again clears the filter. Each chart gets a hover tooltip that stays inside the card. Colors: tightening uses the --tight token, easing uses --ease, and the buyer segments use lighter steps of the same hues (add --tight-2 and --ease-2 tokens, defined for light and dark).
```

**Check:** with no filters, the headline numbers match pandas counts from signals.csv. With "Leave out risk factors" on, the month chart's total equals the number of sightings where section != risk_factors.

### Prompt 6: a data notes card

```
Under the signal map, add "What to know about this data": which companies in config/companies.yaml have no signals; how many signals are Memory_general only; how many buyer signals name a specific family; how many signals have direction_conflict = true. Compute every number from the data, never hard-code it.
```

**Check:** add a company with no filings to `companies.yaml`, and it appears in the "no signals" line.

## Iterating on the design with Cursor

- **One visual change per prompt.** "Make the excerpt 17px and the card padding 22px" works. "Make it look better" produces churn.
- **Show, then describe.** Paste a screenshot of what you see, circle the problem in any image editor, and say what should change. Cursor reads images better than descriptions of layouts.
- **Let it look at its own work.** If your Cursor version has the built-in browser for the agent, ask it to open `localhost:8000`, take a screenshot at 1200px and 400px wide, compare it to the reference PNG, and list the differences before it changes code. If it doesn't have one, take the screenshots yourself and paste them in.
- **Keep the tokens in one place.** Every color and font lives in the `:root` block in `styles.css`. When you change the look, ask Cursor to change only the tokens. Then light and dark mode stay in step.
- **Check dark mode and phone width after every visual change.** These are the two things that break silently.
- **Keep a `docs/design/DECISIONS.md`.** Add one line each time you settle something ("buyers are the lighter step of the same hue"), and point Cursor at it in new chats so it doesn't undo earlier choices.
- **Commit before each prompt.** If a design change goes sideways, `git checkout .` is faster than talking Cursor back.

## Data shapes the front end expects

```json
{
  "signal_id": "017a605d1f87",
  "company": "Micron", "role": "supplier",
  "product_category": "DRAM", "product_family": "DRAM_general",
  "supply_direction": "tightening", "sentiment": "mixed", "section": "mdna",
  "matched_terms": "product:DRAM; topic:average selling prices; dir:constrained",
  "excerpt": "...",
  "first_seen": "2026-03-19", "last_seen": "2026-10-09", "times_seen": 3,
  "sightings": [{"filing_date": "2026-03-19", "form": "10-Q", "section": "mdna", "match": "first", "source_url": "https://www.sec.gov/...", "excerpt": "..."}],
  "votes": {"up": 2, "down": 0}
}
```
