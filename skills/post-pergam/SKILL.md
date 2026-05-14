---
name: post-pergam
description: Use when the user wants an HTML dashboard, report, comparison board, or any self-contained HTML artifact deployed somewhere viewable. POSTs the HTML to a `pergam` backend and returns a shareable URL. Supports two backends — the hosted **pergam.dev** Worker (ephemeral 72-hour shares, default) and any self-hosted pergam server (versioned, immutable, typed). Triggered by phrases like "publish/post/deploy this as a pergam", "armá un pergam de esto", "show this as a viewable pergam", "actualizá el pergam", "v2 de", "iterá sobre", or any request to output HTML that should be browseable.
---

# post-pergam

Publish a self-contained HTML document to a [`pergam`](https://github.com/diesilveira/pergam) backend and return a shareable URL.

There are **two backends** with different capabilities. Pick the right one before posting:

| Signal | Backend | Section |
| --- | --- | --- |
| `$PERGAM_URL` unset, OR set to `https://api.pergam.dev` | Hosted Worker | [§ Flow A](#flow-a--hosted-pergamdev) |
| `$PERGAM_URL` set to a local URL (e.g. `http://localhost:1111`) or any other host | Self-hosted server | [§ Flow B](#flow-b--self-hosted-pergam-server) |
| User mentions "share this", "quick link", "ephemeral" | Hosted | Flow A |
| User mentions "my local pergam", "via Docker", "versioning", "iterá sobre v1" | Self-hosted | Flow B |

**When ambiguous, default to Flow A** — zero setup, works out of the box.

---

## Flow A — Hosted (pergam.dev)

Ephemeral **72-hour** shares served by a Cloudflare Worker. No versioning. Max 128 KB HTML. URL pattern: `https://pergam.dev/s/<token>`.

1. **(Optional) health check:**
   ```bash
   curl -sS https://api.pergam.dev/healthz    # -> {"ok":true}
   ```

2. **Build the HTML** as a complete, self-contained document (see [§ HTML guidelines](#html-guidelines)). Write to a temp file:
   ```bash
   python3 - <<'PY'
   import json, pathlib
   html = pathlib.Path("/tmp/pergam.html").read_text()
   pathlib.Path("/tmp/pergam.payload.json").write_text(json.dumps({
       "title":  "My pergam title",
       "html":   html,
       "type":   "plan",                # optional, free-form; kept for parity with Flow B
       "author": "you@example.com",     # optional but recommended — Claude session email
   }))
   PY
   ```

3. **POST to `/share`:**
   ```bash
   curl -sS -X POST https://api.pergam.dev/share \
        -H "Content-Type: application/json" \
        --data-binary @/tmp/pergam.payload.json
   ```
   Response: `{"token":"abc123…","view_url":"https://pergam.dev/s/abc123…","expires_at":"2026-…","bytes":12345}`

4. **Report the `view_url`** to the user. Always mention "expires in 72h" so they know it's ephemeral.

### Flow A limitations

- **No versioning.** If the user says "v2", "iterá sobre", or "actualizá", you can't bump a version — post a fresh share and tell the user this backend doesn't keep history. If versioning matters, suggest they switch to Flow B (self-hosted).
- **128 KB max** per HTML payload. For long-form reports, trim inline CSS or switch to Flow B.
- **72h TTL.** Hard expiry, then gone.
- **Rate limits:** 10 POSTs / hour / IP, 240 GETs / hour / IP.

---

## Flow B — Self-hosted pergam server

Use when `$PERGAM_URL` points to a self-hosted instance (typically `http://localhost:1111` after `docker compose up -d` in the pergam repo). Supports **versioning, immutability, types, authors**, and persistence.

1. **Ensure reachable:**
   ```bash
   curl -sS "$PERGAM_URL/healthz"    # -> {"ok":true}
   ```
   If `Connection refused`, ask the user to start it (`docker compose up -d` in their pergam dir) or confirm the URL.

2. **Decide: new pergam or new version?** See [§ Versioning (Flow B)](#versioning-flow-b).

3. **Pick `type`** from `plan | investigacion | informe | reporte | otro`. See [§ Pergam types (Flow B)](#pergam-types-flow-b).

4. **Determine `author`** from the Claude session's user email. Required.

5. **Build the HTML** (see [§ HTML guidelines](#html-guidelines)). Write to temp file:
   ```bash
   python3 - <<'PY'
   import json, pathlib
   html = pathlib.Path("/tmp/pergam.html").read_text()
   pathlib.Path("/tmp/pergam.payload.json").write_text(json.dumps({
       "title":  "My pergam title",
       "html":   html,
       "type":   "plan",                # required
       "author": "you@example.com",     # required
       # "id":   "abc12345",            # OPTIONAL: present → new version of this id
   }))
   PY
   ```

6. **POST to `/pergam`:**
   ```bash
   curl -sS -X POST "$PERGAM_URL/pergam" \
        -H "Content-Type: application/json" \
        --data-binary @/tmp/pergam.payload.json
   ```
   Response: `{"id":"abc12345", "version": 2, "view_url":"http://localhost:1111/abc12345/view", "title":"...", "type":"...", "author":"..."}`

7. **Report the `view_url` AND `version`** to the user. Mention "v{n}" if not v1.

### Versioning (Flow B)

| Signal in the user's request                                         | Action                                                                        |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| "armá un pergam nuevo de X", "publish a fresh report"                | **New `id`**. Omit `id` in the POST.                                          |
| "actualizá el pergam", "nueva versión", "v2", "siguiente versión"    | **Same `id`, version++**. Resolve `id` from context — usually the most recent `view_url` posted in this conversation. |
| "iterá sobre el plan anterior", "modificá el informe"                | Same as above.                                                                |
| User mentions an explicit id (`6548a9b9`, `1eb85505`, …)             | **That `id`, version++**. Even if it's new to this conversation.              |
| Ambiguous and there is a recent pergam in the conversation           | Default to **new version** of the most recent.                                |
| Ambiguous and no recent pergam                                       | Default to **new `id`**.                                                      |

**How to find the "current" id**: search this conversation for the most recent `view_url` you (or another assistant message) emitted. The id is the 8-hex segment between the host and `/view`. If you can't find one and the user asked to "iterate", ask the user for the id rather than guess.

The server is permissive: posting with an `id` that doesn't exist yet is accepted as that id's `v1`.

### Pergam types (Flow B)

Required in Flow B. Free-form `text` on the server, but stick to this taxonomy so filters stay clean:

| Type            | When to pick it                                                       |
| --------------- | --------------------------------------------------------------------- |
| `plan`          | Step-by-step implementation plans, deploy plans, migration plans.     |
| `investigacion` | Research, evaluations, "we considered X/Y/Z", design exploration.     |
| `informe`       | Reviews, audits, post-mortems, status reports about a specific thing. |
| `reporte`       | Recurring dashboards, comparison boards, leaderboards, metrics views. |
| `otro`          | Anything that doesn't fit the above. Don't overuse.                   |

When iterating (new version of an existing pergam), **keep the same type as the previous version** unless the content shifts categorically.

### Flow B endpoints

| Method | Path                  | Description                                                          |
| ------ | --------------------- | -------------------------------------------------------------------- |
| POST   | `/pergam`             | Body `{title, html, type, author, id?}`. Returns `{id, version, view_url, title, type, author}`. |
| GET    | `/`                   | HTML index, latest version per id. Filters: `?type=plan&author=...&q=substr`. |
| GET    | `/{id}/view`          | Render latest version.                                               |
| GET    | `/{id}/v{n}/view`     | Render specific version.                                             |
| GET    | `/{id}/raw`           | Raw HTML (latest).                                                   |
| GET    | `/{id}/v{n}/raw`      | Raw HTML (specific).                                                 |
| GET    | `/{id}/versions`      | JSON: `[{version, title, type, author, bytes, created_at}, …]`.      |
| GET    | `/healthz`            | `{"ok":true}`.                                                       |
| DELETE | `/{id}`               | **405 Method Not Allowed** — pergams are immutable.                  |

---

## HTML guidelines

Shared between both flows.

- Complete document: `<!doctype html><html lang="...">...</html>`.
- Inline all CSS. **No external fonts.** **No external JS** except Mermaid (see below).
- Use CSS Grid or flexbox for the actual layout.
- Dark theme. Paleta from `polish-guide.md`: `bg:#0d1117`, `bg2:#161b22`, `text:#e6edf3`, `muted:#8b949e`, `border:#30363d`, accent `#58a6ff`, ok `#3fb950`, warn `#d29922`, critical `#f85149`.

### Two modes

**Short pergam** (3–10 cards, dashboard, PR list, comparison board):
- Single `<section class="grid">` with cards, no CDN.

**Long-form content** (specs, design docs, multi-section reports):
- Start from `template.html` (sibling file): gradient header, sticky TOC, numbered sections, Mermaid for diagrams (dark theme), syntax tokens, callouts, badges, HTTP-method-colored tables, responsive collapse.
- For details (when to use what, color tokens, Mermaid class defs, responsive rules), read `polish-guide.md`.

If content has 5+ logical sections, diagrams, or schemas → long-form template. Otherwise → short pergam.

## Diagrams

Replace ASCII art with Mermaid in long-form content. Only allowed external dependency (`https://cdn.jsdelivr.net/npm/mermaid@11.4.1`). Use `flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`. Always set `theme: 'dark'` and define `classDef` colors per diagram. Full snippet in `polish-guide.md`.

## Troubleshooting

### Flow A (hosted)

- **413 `payload too large`** → HTML > 128 KB. Strip whitespace, drop inline assets, or switch to Flow B.
- **429 `rate limit`** → hit the 10 POST/h cap. Wait an hour or switch to Flow B.
- **404 on `/share` POST** → check you're using `POST /share`, not `POST /pergam`. Flow A doesn't have `/pergam`.

### Flow B (self-hosted)

- **`Connection refused` on `/healthz`** → server not reachable. Ask the user to `docker compose up -d` in their pergam dir or confirm `PERGAM_URL`.
- **Container restarts in a loop, logs show "could not connect to server"** → the pergam server can't reach its Postgres. If using bundled compose, `docker compose restart` after the db is ready.
- **400 "type must be one of …"** → fix the payload to use `plan | investigacion | informe | reporte | otro`.
- **400 "Missing author"** → always pass `author`.
- **405 on DELETE** → expected. Pergams are immutable. Post a new version to supersede.
