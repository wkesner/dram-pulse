# Hosting (EC Signals)

Public HTTPS deploy for the FastAPI feed + Clerk static UI.

**Full host comparison, Clerk prod domain steps, secrets, and Origin/GitHub notes:**  
Project doc `docs/hosting-options.md` (Cursor Project store).

## Prefer: Fly.io (~$4–8/mo)

```bash
fly apps create ec-signals          # once; name must match fly.toml
fly volumes create ecsignals_data --size 1 --region iad
fly secrets set AUTH_MODE=clerk \
  CLERK_PUBLISHABLE_KEY=pk_live_… \
  CLERK_SECRET_KEY=sk_live_… \
  CLERK_ISSUER=https://… \
  CLERK_AUTHORIZED_PARTIES=https://ec-signals.fly.dev,https://app.yourdomain.com \
  OPERATOR_TOKEN=… BROKER_SIM_TOKEN=…
fly deploy
```

- Image: `Dockerfile` + `requirements-web.txt` (no `data/raw/`).
- Volume `/data` holds `orgs/`, `aggregates/`, `votes.csv` via `docker/entrypoint.sh`.
- HTTPS via Fly; set Clerk production domain + allowlists before sharing.

## Alternatives

| Host | Config | Notes |
|---|---|---|
| Render | `render.yaml` | Free = spin-down, no disk; add paid disk at `/data` for votes |
| Railway | Dockerfile | Hobby + volume; connect **GitHub** mirror |

## Dual remotes

Deploy platforms that use GitHub apps need `github.com/wkesner/dram-pulse`. Dual-push feature branches / `main` from a VM with `gh` auth (see Project `internal/github-dual-push.md`). Origin remains the Cursor PR source of truth.
