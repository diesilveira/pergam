# pergam-share Worker

Cloudflare Worker backing `api.pergam.dev`'s 30-day share endpoint. Token-based,
TTL-bound, KV-backed — a ~200 LOC subset of the self-hosted pergam server.

## Setup

The KV namespace id is injected at build time from `PERGAM_KV_ID` so it stays out
of version control. `wrangler.toml` is **generated** from
[`wrangler.toml.tmpl`](wrangler.toml.tmpl) and is gitignored — edit the template,
not the generated file.

1. Copy the env template and fill in the KV id:

   ```bash
   cp .env.example .env
   ```

   Set `PERGAM_KV_ID` to your namespace id. To create one:

   ```bash
   wrangler kv namespace create pergam_shares
   ```

2. Deploy (regenerates `wrangler.toml` from `.env` first, via the `predeploy` hook):

   ```bash
   npm run deploy
   ```

`npm run dev` works the same way (a `predev` hook regenerates the config).
Run `npm run gen-config` on its own to (re)generate `wrangler.toml` without deploying.
