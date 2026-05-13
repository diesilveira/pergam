# pergam — web deploy

The public face of pergam: a static landing/gallery on **Cloudflare Pages** + a
tiny **Cloudflare Worker** for the 72-hour share endpoint. Everything lives in
one Cloudflare account.

```
pergam.dev          ─→  Cloudflare Pages (this folder's `public/`)
api.pergam.dev      ─→  Cloudflare Worker (this folder's `worker/`)
```

The self-host story (Docker + Postgres) lives in the parent repo. **This
subfolder is the public-share path only.**

## Manual deploy steps (~50 min total)

### 1 · Buy the domain · ~5 min · ≈ USD 12/year

Any registrar works, but **Cloudflare Registrar** is cheapest (at-cost) and
makes step 4 effectively a no-op. If you use another registrar, you'll point
nameservers at Cloudflare in step 3.

### 2 · Deploy the Worker · ~10 min

```bash
cd ~/proyectos/pergam/web/worker
npm install
npx wrangler login                                  # browser auth
npx wrangler kv:namespace create pergam_shares
# Output: id = "abc123def456..."
# Paste the id into wrangler.toml under [[kv_namespaces]].id
npx wrangler deploy
```

Test:

```bash
curl https://pergam-share.<you>.workers.dev/healthz
# -> {"ok":true}
```

### 3 · Connect domain to Cloudflare · ~5–10 min

If you bought via CF Registrar, **skip this step**.

Otherwise: in the CF dashboard → Add site → `pergam.dev`. CF gives you two
nameservers — point your registrar's NS records at them. Propagation: 5–30 min.

### 4 · Custom route for the Worker · ~5 min

```bash
# Edit wrangler.toml, uncomment the routes block:
# routes = [
#   { pattern = "api.pergam.dev/*", custom_domain = true }
# ]

npx wrangler deploy
# CF auto-creates the DNS record for api.pergam.dev
curl https://api.pergam.dev/healthz
# -> {"ok":true}
```

### 5 · Deploy the Pages site · ~10 min

Two ways. Pick A if you haven't pushed the repo to GitHub yet; pick B if you
want git-driven auto-deploys.

#### A · Direct upload via Wrangler (no GitHub push required)

```bash
cd ~/proyectos/pergam/web
npx wrangler pages project create pergam --production-branch=main
npx wrangler pages deploy public --project-name=pergam
```

Output: a preview URL like `https://abc1234.pergam.pages.dev`. Open it,
drag-drop a `.html`, verify it works.

#### B · Git integration (auto-deploys on push)

1. Push your repo to GitHub: `gh repo create diesilveira/pergam --public --push`
2. CF dashboard → Workers & Pages → Create application → Pages → Connect to Git
3. Pick the `pergam` repo
4. Build settings:
   - Build command: *(leave blank)*
   - Build output directory: `web/public`
   - Root directory: `/` (default)
5. Save & deploy. Every push to `main` redeploys.

### 6 · Attach `pergam.dev` to the Pages project · ~5 min

CF dashboard → Workers & Pages → `pergam` → Custom domains → `Set up a custom domain`
→ enter `pergam.dev`. CF auto-configures DNS (since the zone is in CF). TLS
provisions in 1–5 min.

For the `www` subdomain, repeat with `www.pergam.dev` or add a redirect rule.

### 7 · Smoke test E2E · ~5 min

- Open `https://pergam.dev` → landing renders
- Drag-drop a small `.html` → modal shows `pergam.dev/s/<token>` URL
- Open that URL in a new tab → grid renders inside an iframe with banner above
- `curl https://api.pergam.dev/healthz` → `{"ok":true}`
- "Make your own" link returns to landing

### 8 · Point the Claude skill at the public service · ~1 min · optional

```bash
# Add to ~/.zshrc or ~/.bashrc:
export PERGAM_URL=https://api.pergam.dev
```

The skill will POST to that URL instead of `http://localhost:1111`.

## What each piece does

| Path                              | Served by      | Purpose                                       |
| --------------------------------- | -------------- | --------------------------------------------- |
| `pergam.dev/`                     | CF Pages       | Landing page · drag-drop upload · gallery     |
| `pergam.dev/s/<token>`            | CF Pages       | Rewrites to `view.html?t=<token>` (200, same path) |
| `pergam.dev/library/*.html`       | CF Pages       | Example grids linked from the landing         |
| `api.pergam.dev/share`            | CF Worker      | POST → store HTML in KV with 72h TTL          |
| `api.pergam.dev/s/<token>`        | CF Worker      | GET → HTML with strict CSP for safe rendering |
| `api.pergam.dev/s/<token>/meta`   | CF Worker      | GET → JSON metadata (title, expires_at, …)    |
| `api.pergam.dev/s/<token>/raw`    | CF Worker      | GET → raw HTML for download                   |
| `api.pergam.dev/healthz`          | CF Worker      | Liveness probe                                |

## Rate limits & body cap

Hardcoded in `worker/src/index.ts`:

- `POST /share`: 10 per IP per hour
- `GET /s/*`: 240 per IP per hour
- Body size: 512 KB

Adjust the constants at the top of `index.ts` and redeploy.

## CF free-tier sizing

Cloudflare Workers free tier (as of 2026): 100,000 requests/day, 10 ms CPU
per request. Workers KV free tier: 1 GB storage, 100,000 reads/day, 1,000
writes/day. CF Pages free tier: unlimited bandwidth + unlimited requests for
static content, 500 builds/month.

At ~1,000 shares/day with ~20 views each = ~21k Worker requests + 21k KV
reads + 1k writes per day. Comfortably free. Pages is effectively unmetered
for our static assets.

## Local dev

```bash
# Worker (port 8787)
cd worker && npx wrangler dev

# Pages dev (port 8788)
cd .. && npx wrangler pages dev public

# Hit the editor at http://localhost:8788 and the Worker at http://localhost:8787.
# Override the API base for local dev by setting in browser devtools console:
#   window.PERGAM_API = "http://localhost:8787"
# (or edit app.js temporarily during dev)
```

## Tearing down

```bash
cd worker && npx wrangler delete                       # remove the Worker
cd ..     && npx wrangler pages project delete pergam  # remove the Pages site
```

Domain stays yours, can be repointed elsewhere.

## Why Cloudflare Pages over Netlify or GitHub Pages?

- **Same ecosystem as the Worker** — one dashboard, one API token, one bill (free).
- **`_redirects` / `_headers` files** — supports the same Netlify-style syntax
  for URL rewrites and security headers. GitHub Pages doesn't have these and
  would force either hash-routing URLs (`pergam.dev/#s/abc`) or a 404-shim
  workaround.
- **Unmetered bandwidth + builds** — Pages free tier is more generous than
  Netlify's at the OSS-demo scale.
- **CF DNS already there** — if your zone is on CF (step 3), custom-domain
  attachment is one click.
