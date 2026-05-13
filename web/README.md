# pergam — web deploy

The public face of pergam: a static landing/gallery on Netlify + a tiny
Cloudflare Worker for the 72-hour share endpoint.

```
pergam.dev          ─→  Netlify (this folder's `public/`)
api.pergam.dev      ─→  Cloudflare Worker (this folder's `worker/`)
```

The self-host story (Docker + Postgres) lives in the parent repo. **This
subfolder is the public-share path only.**

## Manual deploy steps

### 1 · Buy the domain (one time)

Any registrar works. We use `pergam.dev` in these examples. ≈ USD 12/year.

### 2 · Create the Cloudflare Worker

```bash
cd worker
npm install
npx wrangler login                                 # browser auth
npx wrangler kv:namespace create pergam_shares     # copy the id it prints
# Paste the id into wrangler.toml under [[kv_namespaces]].id
npx wrangler deploy
```

Note the URL it gives you — something like
`https://pergam-share.<you>.workers.dev`. Test:

```bash
curl https://pergam-share.<you>.workers.dev/healthz
# -> {"ok":true}
```

Optional: route under your domain.

```bash
# In Cloudflare dashboard, add api.pergam.dev to your zone, then:
npx wrangler deploy --routes api.pergam.dev/*
```

### 3 · Create the Netlify site

```bash
cd ..   # back to web/
npx netlify-cli init                               # link to your GH or drag-drop the public/ folder
# Publish directory: public
# Build command: (leave blank — static)
```

If you didn't bind a custom Worker domain, edit `public/app.js` and set:

```js
const API = "https://pergam-share.<you>.workers.dev";
```

Then `netlify deploy --prod`.

### 4 · Point pergam.dev at Netlify

In your DNS provider:

```
pergam.dev        CNAME  apex-loadbalancer.netlify.com.
www.pergam.dev    CNAME  apex-loadbalancer.netlify.com.
```

(or use Netlify DNS — `netlify-cli` will walk you through it.)

In Netlify dashboard → Domain settings → add `pergam.dev`. TLS is automatic.

### 5 · Update the Claude Code skill (optional)

If you want the skill to publish to your public service by default:

```bash
# Add to ~/.zshrc or ~/.bashrc:
export PERGAM_URL=https://api.pergam.dev
```

The skill will POST to that URL instead of `http://localhost:1111`.

## What each piece does

| Path                              | Served by  | Purpose                                       |
| --------------------------------- | ---------- | --------------------------------------------- |
| `pergam.dev/`                     | Netlify    | Landing page · drag-drop upload · gallery     |
| `pergam.dev/s/<token>`            | Netlify    | Rewrites to `view.html?t=<token>` (200, same path) |
| `pergam.dev/library/*.html`       | Netlify    | Example grids (linked from the landing)       |
| `api.pergam.dev/share`            | Worker     | POST → store HTML in KV with 72h TTL          |
| `api.pergam.dev/s/<token>`        | Worker     | GET → HTML with strict CSP for safe rendering |
| `api.pergam.dev/s/<token>/meta`   | Worker     | GET → JSON metadata (title, expires_at, …)    |
| `api.pergam.dev/s/<token>/raw`    | Worker     | GET → raw HTML for download                   |
| `api.pergam.dev/healthz`          | Worker     | Liveness probe                                |

## Rate limits & body cap

Hardcoded in `worker/src/index.ts`:

- `POST /share`: 10 per IP per hour
- `GET /s/*`: 240 per IP per hour
- Body size: 512 KB

Adjust the constants at the top of `index.ts` and redeploy.

## Worker free-tier sizing

Cloudflare Workers free tier (as of 2026): 100,000 requests/day, 10ms CPU per
request. Workers KV free tier: 1 GB storage, 100,000 reads/day, 1,000 writes/day.

Each share = 1 KV write (POST) + N KV reads (GET per view) + 1 small counter
write (rate limit). At ~1,000 shares/day with ~20 views each, you sit at
~21k requests + ~21k KV reads + ~1k writes per day. Comfortably free.

## Local dev

```bash
# Worker (port 8787)
cd worker && npx wrangler dev

# Netlify dev (port 8888 by default)
cd .. && npx netlify-cli dev

# Hit the editor at http://localhost:8888 and the Worker at http://localhost:8787
# Override the API base for local dev:
window.PERGAM_API = "http://localhost:8787";
```

## Tearing down

```bash
cd worker && npx wrangler delete
cd .. && netlify sites:delete <site-id>
```

Domain stays yours.
