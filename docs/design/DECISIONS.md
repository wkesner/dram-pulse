# Design decisions

One line per settled choice so later prompts don’t undo earlier ones.

- **Front end is FastAPI + static HTML**, not Streamlit — instant voting and chart clicks without a full script re-run; mockups port directly (`docs/FEED-BUILD.md`).
- **Three tabs:** Feed / Signal map / Families (mockup had two; FEED-BUILD adds Signal map).
- **Buyers are the lighter step** of the same hue as suppliers: `--tight-2` / `--ease-2` next to `--tight` / `--ease` (light and dark).
- **Tokens and fonts** (Newsreader, Public Sans, IBM Plex Mono) copied from `docs/design/feed_mockup.html`; theme follows `prefers-color-scheme` with an optional Theme toggle for QA.
- **Votes** append to `data/votes.csv` (file-locked); latest row per (user, signal) wins. `vote=0` is Skip; feed hides rated/skipped for that profile.
- **Signal map month chart counts sightings** by filing month (when filers said things); headlines and family/company bars count **signals**.
- **“Leave out risk factors”** drops `section == risk_factors` (signals for headlines/bars; sightings for the month chart).
- **Data notes** (`/api/meta`) are computed from `config/companies.yaml` + `data/signals.csv` — never hard-coded.
