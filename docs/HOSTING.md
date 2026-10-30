# Hosting (EC Signals)

Public HTTPS deploy for the FastAPI feed + Clerk static UI.

**Full host comparison, Clerk prod domain steps, secrets, and Origin/GitHub notes:**  
Project doc `docs/hosting-options.md` (Cursor Project store).

## Prefer: Fly.io (~$4–8/mo)

```bash
fly apps create ec-signals          # once; name must match fly.toml
fly volumes create ecsignals_data --size 1 --region iad
# Demo on *.fly.dev → Development instance keys (pk_test_/sk_test_), not pk_live_
fly secrets set AUTH_MODE=clerk \
  CLERK_PUBLISHABLE_KEY=pk_test_… \
  CLERK_SECRET_KEY=sk_test_… \
  CLERK_ISSUER=https://….clerk.accounts.dev \
  CLERK_AUTHORIZED_PARTIES=https://ec-signals.fly.dev \
  OPERATOR_TOKEN=… BROKER_SIM_TOKEN=…
fly deploy
```

- Image: `Dockerfile` + `requirements-web.txt` (no `data/raw/`).
- Volume `/data` holds `orgs/`, `aggregates/`, `votes.csv` via `docker/entrypoint.sh`.
- HTTPS via Fly.

### Clerk + `*.fly.dev`

Clerk **production Domains refuse `*.fly.dev`**. For https://ec-signals.fly.dev:

1. Use the **Development** instance (`pk_test_` / `sk_test_` — already on this app).
2. Dashboard → **Configure → Paths** on **Dev**: allow redirect/origin `https://ec-signals.fly.dev`.
3. Do not add fly.dev under Production → Domains.

Production Clerk (`pk_live_`) needs a **custom domain** you own (`fly certs add …` + Clerk DNS), then swap secrets / authorized parties.

## Alternatives

| Host | Config | Notes |
|---|---|---|
| Render | `render.yaml` | Free = spin-down, no disk; add paid disk at `/data` for votes |
| Railway | Dockerfile | Hobby + volume; connect **GitHub** mirror |

## Dual remotes

Deploy platforms that use GitHub apps need `github.com/wkesner/dram-pulse`. Dual-push feature branches / `main` from a VM with `gh` auth (see Project `internal/github-dual-push.md`). Origin remains the Cursor PR source of truth.
