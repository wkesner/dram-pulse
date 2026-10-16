# Robots.txt findings (catalog / spot-price hosts)

Last verified: 2026-10-16 via `scripts/probe_catalogs.py`  
User-Agent: `DRAMSignalPipeline-Probe/0.1 (+contact:…)` · 15s timeout · no retries

## Short answers

| Host | Canonical `/robots.txt`? | What we use |
|------|--------------------------|-------------|
| `product.skhynix.com` | **No — broken (HTTP 500 HTML)** | Non-standard `/robot.txt` exists and says `Allow: /`, but it is **not** the Robot Exclusion Protocol path. Corporate `www.skhynix.com/robots.txt` does **not** cover the product host. |
| `www.dramexchange.com` | **No file (HTTP 404)** | No crawl rules declared. Parent/sister hosts have their own files that do **not** apply here. |
| `semiconductor.samsung.com` | **Yes (HTTP 200)** | `Allow: /` with narrow Disallows; `/dram/` allowed. |

---

## SK hynix product site (`product.skhynix.com`)

### Can we find the robots file?

**Not a working canonical one.** Crawlers (and `urllib.robotparser`) only look at:

```text
https://product.skhynix.com/robots.txt
```

That URL returns **HTTP 500** with an HTML error page (`Error | SK hynix`), for both our collector UA and a normal browser UA. The Internet Archive CDX history (2021–2022) shows the same path as HTML 404/500 — never a `text/plain` robots document.

### What *does* exist

| URL | Status | Notes |
|-----|--------|-------|
| `https://product.skhynix.com/robots.txt` | **500** | Canonical path — **broken**. Treat as “no readable robots.txt”. |
| `https://product.skhynix.com/robot.txt` | **200** `text/plain` | **Typo / non-standard** filename (singular “robot”). Body: `User-agent: *` / empty `Disallow:` / `Allow: /`. **Not** consulted by standard robots clients. |
| `https://www.skhynix.com/robots.txt` | **200** | Corporate site only: `Disallow: /etc`, `Allow: /`. **Does not govern** `product.skhynix.com`. |
| `https://product.skhynix.com/sitemap.xml` | **200** | Single URL (`/`), lastmod 2021-06-01 — no product part inventory. |
| `https://product.skhynix.com/home.go` | **200** | Plain `requests` works (~94KB HTML); browser not required for the homepage alone. |

### Policy for collectors

- Do **not** invent allow/deny from `/robot.txt` for Robot Exclusion Protocol compliance.
- Step 4b (SK hynix catalog crawl) stays **on hold** until either:
  1. Canonical `/robots.txt` starts serving real rules, or
  2. We get explicit written permission, or
  3. Product owners confirm `/robot.txt` is intentional (unusual).
- SK hynix data for now: part-number decoder + distributor/API part master only.

---

## DRAMeXchange (`www.dramexchange.com`)

### Can we find the robots file?

**No file is published** on the English spot-price host.

| URL | Status | Notes |
|-----|--------|-------|
| `https://www.dramexchange.com/robots.txt` | **404** | Redirects to ASP.NET `Error/Error404?aspxerrorpath=/robots.txt`. |
| `https://dramexchange.com/robots.txt` | **404** | Same. |
| `https://www.dramexchange.com/` | **200** | Homepage with DRAM Spot tables is reachable. |

### Related hosts (do **not** substitute)

| URL | Status | Relevance |
|-----|--------|-----------|
| `https://www.dramx.com/robots.txt` | **200** | Different Chinese site (`全球半导体观察…`). Disallows `/Common/`, `/Article/`, `/Member/`, etc. **Not** the English DRAMeXchange spot homepage. |
| `https://www.trendforce.com/robots.txt` | **200** | Parent company. Disallows `/api/*`, `/search`, `/searchNews`, … — **does not apply** to `dramexchange.com`. |

### Policy for collectors

- Missing robots.txt ⇒ no path restrictions declared (default allow under robots convention).
- Step 6 still bound by DRAMeXchange/TrendForce **Terms of Use** (internal logging only until licensed). Attribute stored rows: `Source: DRAMeXchange/TrendForce`.

---

## Samsung Semiconductor (`semiconductor.samsung.com`)

Canonical file works:

```text
https://semiconductor.samsung.com/robots.txt
```

- `User-agent: *` · `Allow: /`
- Disallow: `/ehs/`, `/parking/`, `/events/nanum-week/`, a few `/china/about-us/` paths, …
- Sitemaps: `/sitemap.xml`, `/top_sitemap.xml`, `/kr/sitemap.xml`, `/kr/top_sitemap.xml`
- `/dram/` and `/mcp/` are **allowed** — suitable for the family-status watcher.

Product meta on family pages uses names like `productStatus`, `productModelName` (not `meta-product*` prefixes). Part URLs in the sitemap **redirect to family pages** and do not contain the part slug in the body.

---

## Re-run

```bash
export COLLECTOR_CONTACT=you@example.com
python scripts/probe_catalogs.py
```

The probe prints every robots URL above (including SK hynix `/robot.txt` and DRAMeXchange sister hosts) so this document can be re-verified anytime.
