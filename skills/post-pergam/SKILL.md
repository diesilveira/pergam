---
name: post-pergam
description: Use when the user wants an HTML dashboard, report, comparison board, or any self-contained HTML artifact deployed somewhere viewable. POSTs the HTML to a `pergam` backend and returns a shareable URL. Supports two backends — the hosted **pergam.dev** Worker (public 1-year shares, default) and any self-hosted pergam server (versioned, immutable, typed). Triggered by phrases like "publish/post/deploy this as a pergam", "armá un pergam de esto", "show this as a viewable pergam", "actualizá el pergam", "v2 de", "iterá sobre", or any request to output HTML that should be browseable.
---

# post-pergam

Publish a self-contained HTML document to a [`pergam`](https://github.com/diesilveira/pergam) backend and return a shareable URL.

First pick the **server** ([§ Choosing the server](#choosing-the-server)), then the **backend** it runs. There are two, with different capabilities:

| Resolved server URL | Backend | Section |
| --- | --- | --- |
| `https://api.pergam.dev` (the built-in `hosted` server) | Hosted Worker | [§ Flow A](#flow-a--hosted-pergamdev) |
| Any other URL (e.g. `http://localhost:1111`, `https://pergam.example.com`) | Self-hosted server | [§ Flow B](#flow-b--self-hosted-pergam-server) |
| No server named, and the user mentions "share this", "quick link", "ephemeral" | Hosted | Flow A |
| No server named, and the user mentions "my local pergam", "via Docker", "versioning", "iterá sobre v1" | Self-hosted (`local` profile or `$PERGAM_URL`) | Flow B |

**When ambiguous, default to Flow A** — zero setup, works out of the box.

---

## Choosing the server

A user may have several pergam servers and name them: "publish it on **team**", "subilo al local". Named servers live in one JSON file, `~/.config/pergam/servers.json` (`$XDG_CONFIG_HOME/pergam/servers.json` when `XDG_CONFIG_HOME` is set; `$PERGAM_CONFIG` overrides the path):

```json
{
  "default": "local",
  "servers": {
    "local":  {"url": "http://localhost:1111"},
    "team":   {"url": "https://pergam.example.com", "ca": "~/.config/pergam/team-ca.pem"},
    "hosted": {"url": "https://api.pergam.dev"}
  }
}
```

- `servers` maps a name to its `url` and an optional `ca`: the PEM bundle of the private CA that signed that server's certificate, passed to curl as `--cacert`. `~` is expanded; a relative `ca` is read from the file's folder.
- `default` (optional) is the server used when the user names none.
- `hosted` (`https://api.pergam.dev`) exists even when the file does not list it.

**Resolution order** — the first that applies wins:

1. The server the user named in the request ("on team"), or a URL they gave. An unknown name is an error: list the known ones and ask, never guess.
2. `$PERGAM_URL`, with `$PERGAM_CA` as its optional CA bundle. Without a config file this is exactly the old behaviour.
3. The file's `default`.
4. `hosted`.

Resolve with the helper shipped next to this file (stdlib Python, no install), **in the same shell command** as the curl calls that use it, since each command starts a fresh shell:

```bash
eval "$(python3 <skill-dir>/pergam_profiles.py env team)" &&   # omit "team" when the user named no server
curl -sS ${PERGAM_CA:+--cacert "$PERGAM_CA"} "$PERGAM_URL/healthz"
```

`env` prints `PERGAM_SERVER`, `PERGAM_URL` and `PERGAM_CA` (empty when there is none) as shell assignments; `pergam_profiles.py list` shows every server with the default marked `*`. `<skill-dir>` is this skill's folder (`~/.claude/skills/post-pergam` for a user-level install). Every curl to a server with a CA bundle carries `--cacert "$PERGAM_CA"`; **never** fall back to `curl -k`: if verification fails, tell the user the `ca` is wrong or missing.

---

## Flow A — Hosted (pergam.dev)

Public **1-year** shares served by a Cloudflare Worker. No versioning. Max 1 MB HTML. URL pattern: `https://pergam.dev/s/<token>`.

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

4. **Report the `view_url`** to the user. Always mention "expires in a year" so they know it's not permanent.

### Flow A limitations

- **No versioning.** If the user says "v2", "iterá sobre", or "actualizá", you can't bump a version — post a fresh share and tell the user this backend doesn't keep history. If versioning matters, suggest they switch to Flow B (self-hosted).
- **1 MB max** per HTML payload. For long-form reports, trim inline CSS or switch to Flow B.
- **1-year TTL.** Hard expiry, then gone.
- **Rate limits:** 10 POSTs / hour / IP, 240 GETs / hour / IP.

---

## Flow B — Self-hosted pergam server

Use when the resolved server is a self-hosted instance (typically `http://localhost:1111` after `docker compose up -d` in the pergam repo). Below, `$PERGAM_URL` and `$PERGAM_CA` are the values [§ Choosing the server](#choosing-the-server) resolved; add `--cacert "$PERGAM_CA"` to each curl when it is set. Supports **versioning, immutability, types, authors**, and persistence.

1. **Ensure reachable:**
   ```bash
   curl -sS ${PERGAM_CA:+--cacert "$PERGAM_CA"} "$PERGAM_URL/healthz"    # -> {"ok":true}
   ```
   If `Connection refused`, ask the user to start it (`docker compose up -d` in their pergam dir) or confirm the URL.

2. **Decide: new pergam or new version?** See [§ Versioning (Flow B)](#versioning-flow-b).

3. **Pick `type`** from the server's accepted list (by default `plan | investigacion | informe | reporte | otro`). See [§ Pergam types (Flow B)](#pergam-types-flow-b).

4. **Determine `author`** from the Claude session's user email. Required, except on a server started with `TRUST_PROXY_USER=1` behind an authenticating proxy: there the author is the signed-in user (`X-Forwarded-User`, set by the proxy), the body's `author` is ignored and the response's `author` says who was recorded.

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
       # "tags": ["q3", "billing"],     # OPTIONAL: merged into the id's existing tags
       # "note": "adds the costs section",  # OPTIONAL: what this version changes (≤ 280 chars)
   }))
   PY
   ```

6. **Lint before publishing** — `POST /lint` checks the same payload without storing anything:
   ```bash
   curl -sS ${PERGAM_CA:+--cacert "$PERGAM_CA"} -X POST "$PERGAM_URL/lint" \
        -H "Content-Type: application/json" \
        --data-binary @/tmp/pergam.payload.json
   ```
   Response: `{"findings": [{"rule", "severity", "line", "column", "message"}, …], "errors": n, "warnings": n, "truncated": false}`. Fix every `error` in the HTML and lint again before posting: `external-resource` (something loaded from another server, which the sandbox blocks: inline it or use a `data:` URI), `missing-anchor` (a `#link` with no matching `id`: fix the TOC), `mermaid-empty` / `mermaid-unknown-diagram` (a diagram block that will not render). A `warning` (Mermaid from jsDelivr) is allowed, but switch it to the server's `/static/` path. A server older than `/lint` answers 404: skip this step there.

7. **POST to `/pergam`:**
   ```bash
   curl -sS ${PERGAM_CA:+--cacert "$PERGAM_CA"} -X POST "$PERGAM_URL/pergam" \
        -H "Content-Type: application/json" \
        --data-binary @/tmp/pergam.payload.json
   ```
   Response: `{"id":"abc12345", "version": 2, "view_url":"http://localhost:1111/abc12345/view", "title":"...", "type":"...", "author":"..."}`

8. **Report the `view_url` AND `version`** to the user. Mention "v{n}" if not v1, and the `tags` if you sent any.
   The `view_url` is built by the server from its `PUBLIC_URL` (or `PUBLIC_HOST`/`PUBLIC_PORT`); report it as returned instead of rebuilding it from `$PERGAM_URL`. It always shows the latest version; when the link must keep showing exactly this version, replace its trailing `/view` with `/v{n}/view` (that page keeps showing v{n} and says when a newer version exists).

### Versioning (Flow B)

| Signal in the user's request                                         | Action                                                                        |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| "armá un pergam nuevo de X", "publish a fresh report"                | **New `id`**. Omit `id` in the POST.                                          |
| "actualizá el pergam", "nueva versión", "v2", "siguiente versión"    | **Same `id`, version++**. Resolve `id` from context — usually the most recent `view_url` posted in this conversation. |
| "iterá sobre el plan anterior", "modificá el informe"                | Same as above. Read the current content first with `GET /{id}/text` (see [§ Reading](#reading-an-existing-pergam-flow-b)). |
| User mentions an explicit id (`6548a9b9`, `1eb85505`, …)             | **That `id`, version++**. Even if it's new to this conversation.              |
| Ambiguous and there is a recent pergam in the conversation           | Default to **new version** of the most recent.                                |
| Ambiguous and no recent pergam                                       | Default to **new `id`**.                                                      |

**How to find the "current" id**: search this conversation for the most recent `view_url` you (or another assistant message) emitted. The id is the 8-hex segment between the host and `/view`. If you can't find one and the user asked to "iterate", ask the user for the id rather than guess.

The server is permissive: posting with an `id` that doesn't exist yet is accepted as that id's `v1`.

**Notes**: when posting a new version (v2+), send a short `note` saying what changed, in the user's words when they gave them ("adds the costs section", "fixes the totals"). It shows in the view page's version selector and in `/{id}/versions`, and can't be edited later. Skip it for a v1 unless the user asked for one.

### Reading an existing pergam (Flow B)

When the user asks to read, summarise, quote, check or **iterate on** a pergam that already exists, read its text in **one call** instead of downloading the HTML:

```bash
curl -sS "$PERGAM_URL/abc12345/text"       # latest version, as Markdown
curl -sS "$PERGAM_URL/abc12345/v2/text"    # a specific version
```

- It returns only what a reader sees: headings as `#`, lists as `-`/`1.`, tables as pipe tables, code as fenced blocks; scripts and styles are dropped. **404** means the id (or version) doesn't exist on this server.
- To iterate, build the new version from that text plus the user's changes, then POST it with the same `id` (see [§ Versioning](#versioning-flow-b)). Fetch `/{id}/raw` only when the new version must keep the old markup or styling exactly.
- To find a pergam whose id you don't have, search: `curl -sS "$PERGAM_URL/api/pergams?q=<words>"` and pick from `pergams[].id` (each row has `title`, `author`, `created_at` and a `snippet` of the match).
- Search limits: words match exactly (no stemming, no accent folding: try `información` and `informacion` if unsure), only the first 100,000 characters of each pergam's text are searchable, and `q` is at most 200 characters. When a long pergam isn't found by a word from its end, look it up by title or author instead.

### Pergam types (Flow B)

Required in Flow B. The server only accepts the types in its `PERGAM_TYPES` setting; by default these five:

| Type            | When to pick it                                                       |
| --------------- | --------------------------------------------------------------------- |
| `plan`          | Step-by-step implementation plans, deploy plans, migration plans.     |
| `investigacion` | Research, evaluations, "we considered X/Y/Z", design exploration.     |
| `informe`       | Reviews, audits, post-mortems, status reports about a specific thing. |
| `reporte`       | Recurring dashboards, comparison boards, leaderboards, metrics views. |
| `otro`          | Anything that doesn't fit the above. Don't overuse.                   |

When iterating (new version of an existing pergam), **keep the same type as the previous version** unless the content shifts categorically.

If the user's server is configured with other types, a `400 type must be one of [...]` lists them: pick from that list and retry. Never invent a type the server didn't list.

### Tags (Flow B)

Optional. Send `tags` when the user names topics, a project, a quarter or a team ("tag it billing", "para el release de marzo"), or when the content obviously belongs to one; otherwise omit them. Two or three short tags beat many.

- The server normalises them (lowercase, hyphens, deduplicated, sorted) and returns the id's resulting `tags`.
- On a new version, `tags` are **merged** with the ones the id already has; omitting `tags` leaves them untouched. You don't need to resend old tags.
- Limits: 20 tags per id, 40 characters per tag, no control characters.

**Tagging, pinning or archiving an already-published pergam** ("tagueá el informe de ayer con billing", "archivá ese plan", "pin it"): don't post a new version. `PATCH` its metadata instead; this never creates a version.

```bash
curl -sS "$PERGAM_URL/abc12345/meta"      # -> {"id":"abc12345","tags":["q3"],"archived":false,"pinned":false}
curl -sS -X PATCH "$PERGAM_URL/abc12345/meta" \
     -H "Content-Type: application/json" \
     -d '{"tags":["q3","billing"]}'
```

- `PATCH` `tags` **replaces** the set: to add a tag, `GET /{id}/meta` first and send the old tags plus the new one. `[]` clears them.
- `archived: true` hides it from the index (still reachable by URL); `pinned: true` lists it first. Send only the fields you change.
- On a server that trusts its proxy for identity, only the owner (the author of v1) and its collaborators may version or `PATCH` a pergam; the owner manages the list:
  ```bash
  curl -sS -X PUT "$PERGAM_URL/abc12345/collaborators" -H "Content-Type: application/json" \
       -d '{"collaborators":["bo@example.com"]}'      # replaces the list; [] empties it
  ```
- Archiving and pinning belong to the id: a **new version of an archived pergam stays archived** and hidden from the index, and the POST response doesn't say so. When the user iterates on a pergam they archived, check `GET /{id}/meta` and, if it is archived, either tell them or `PATCH` `{"archived": false}` when they clearly want it visible again.

### Promoting a pergam to another server (Flow B)

"Pasalo al team", "promote abc12345 to team", "copy it to the team server": move a pergam with its **whole history** (same id, version numbers, dates, tags and notes) from one self-hosted server to another in one command, with the helper shipped next to this file:

```bash
python3 <skill-dir>/pergam_promote.py abc12345 --from local --to team
# -> promoted abc12345 from local to team: uploaded v1, v2, v3, v4, v5
#    https://pergam.example.com/abc12345/view
```

- `--from` and `--to` are server names from [§ Choosing the server](#choosing-the-server) (each with its `ca`, so no `-k`) or plain URLs. When the user names only the target, the source is the server the pergam was published on in this conversation, usually `local`.
- It exports from the source (`GET /{id}/export`) and, when the target already has the id, uploads **only the versions it lacks** (`POST /import`). Running it again prints `team already has every version of abc12345; nothing to do` and writes nothing. Long histories are uploaded 1,000 versions per request (the server takes at most 10,000 per import), oldest first, so a promote interrupted midway resumes on the next run.
- When a version the target already has differs (another pergam with the same id there, or a different history), it stops with exit status 1 and uploads nothing: tell the user, never work around it by posting the HTML as a new pergam.
- When the target trusts its proxy for identity (`TRUST_PROXY_USER`), it refuses with 403 an upload that would create the id with a v1 by someone other than the signed-in user, or that adds versions to a pergam the user neither owns nor collaborates on. The printed message names the rule (`v1 of abc12345 is by …, not …`): tell the user, and for an id already on the target, ask its owner to add them as a collaborator.
- The target records where the pergam came from (`origin`, shown in its view bar). The hosted backend keeps no versions, so promote works only between self-hosted servers.
- Report the printed `view_url` (the target's) and how many versions were uploaded.

### Flow B endpoints

| Method | Path                  | Description                                                          |
| ------ | --------------------- | -------------------------------------------------------------------- |
| POST   | `/pergam`             | Body `{title, html, type, author, id?, tags?, note?}`. Returns `{id, version, view_url, title, type, author}` (+ `tags`, `note` when sent). |
| GET    | `/`                   | HTML index, latest version per id, pinned first, paginated. Filters: `?type=plan&author=...&tag=...&q=words`; `q` searches titles and visible text (`"quoted phrase"` for exact order); `?archived=1` includes archived ones. |
| GET    | `/api/pergams`        | JSON index with the same filters as `/` (`type`, `author`, `tag`, `q`, `archived`, `cursor`): `{pergams, matching, next_cursor, counts, stats}`. Use it to find or count pergams instead of scraping `/`. |
| GET    | `/api/pergams/{id}`   | JSON: latest title/type/author, `tags`, `archived`, `pinned`, `origin` (server it was imported from, or `null`) and `versions` (newest first). |
| GET    | `/{id}/view`          | View page of the latest version: bar with title, type, tags, author, `v3 of 5` and a version selector, document in a frame. |
| GET    | `/{id}/v{n}/view`     | View page of a specific version; says so when a newer one exists.    |
| GET    | `/{id}/v{n}/content`  | The posted HTML as `text/html` plus the frame bridge tag (what the view frames). Use `/raw` for the exact bytes. |
| GET    | `/{id}/raw`           | Raw HTML (latest).                                                   |
| GET    | `/{id}/v{n}/raw`      | Raw HTML (specific).                                                 |
| GET    | `/{id}/text`          | Visible text of the latest version as Markdown (headings, lists, tables). Cheapest way to read a pergam. |
| GET    | `/{id}/v{n}/text`     | Same, for a specific version.                                        |
| GET    | `/{id}/diff/v{a}..v{b}` | HTML page with the text changes between two versions, per section. Link it when the user asks what changed. |
| GET    | `/{id}/versions`      | JSON: `[{version, title, type, author, bytes, created_at, note}, …]`. |
| GET    | `/{id}/meta`          | JSON: `{id, tags, archived, pinned}`.                                |
| PATCH  | `/{id}/meta`          | Body with any of `{tags, archived, pinned}`. Edits metadata, no new version. |
| GET    | `/{id}/collaborators` | JSON: `{id, owner, collaborators}`; `owner` is the author of v1. |
| PUT    | `/{id}/collaborators` | Body `{collaborators: [user, …]}`; replaces the list. Only the owner, on a server that trusts its proxy. |
| POST   | `/lint`               | Body `{html}` (other fields ignored). Returns `{findings, errors, warnings, truncated}`; stores nothing. Call it before every publish. |
| GET    | `/{id}/export`        | JSON: every version with its HTML, plus `meta`, to move the pergam to another server. |
| POST   | `/import`             | Body: an export document. Adds the versions this server lacks (same id, numbers, dates); **409** if one it has differs. |
| GET    | `/feed.atom`          | Atom feed of recent publications, `?tag=`/`?author=` to filter. Point the user here when they want to follow new pergams. |
| GET    | `/healthz`            | `{"ok":true}`.                                                       |
| HEAD   | any GET route         | Same status and headers as GET, no body (cheap existence check).     |
| DELETE | `/{id}`               | **405 Method Not Allowed** — pergams are immutable.                  |

---

## HTML guidelines

Shared between both flows.

- Complete document: `<!doctype html><html lang="...">...</html>`.
- Inline all CSS. **No external fonts.** **No external JS**: Mermaid comes from the server on Flow B and from jsDelivr on Flow A (see below).
- Both backends run the document sandboxed, in an opaque origin, under a strict CSP: no external images (use inline SVG or `data:` URIs), no `fetch`/XHR, no forms, no nested iframes, no `localStorage` or cookies. In-page anchors, links, inline scripts, printing and Mermaid keep working.
- Use CSS Grid or flexbox for the actual layout.
- Dark theme. Paleta from `polish-guide.md`: `bg:#0d1117`, `bg2:#161b22`, `text:#e6edf3`, `muted:#8b949e`, `border:#30363d`, accent `#58a6ff`, ok `#3fb950`, warn `#d29922`, critical `#f85149`.

### Two modes

**Short pergam** (3–10 cards, dashboard, PR list, comparison board):
- Single `<section class="grid">` with cards, no CDN.

**Long-form content** (specs, design docs, multi-section reports):
- Start from `template.html` (sibling file): gradient header, sticky TOC, numbered sections, Mermaid for diagrams (dark theme), syntax tokens, callouts, badges, HTTP-method-colored tables, responsive collapse, and print styles so Save as PDF gives a light single-column document without the TOC. Keep the `@media print` block.
- For details (when to use what, color tokens, Mermaid class defs, responsive rules), read `polish-guide.md`.

If content has 5+ logical sections, diagrams, or schemas → long-form template. Otherwise → short pergam.

## Diagrams

Replace ASCII art with Mermaid in long-form content. Flow B (self-hosted): `<script src="/static/mermaid/11.17.2/mermaid.min.js"></script>`, served by the pergam server itself, so the pergam makes no external request. Flow A (hosted): `<script src="https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js"></script>`, the only allowed external dependency. Same version on both, so diagrams render the same. Use `flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`. Always set `theme: 'dark'` and define `classDef` colors per diagram. Full snippet in `polish-guide.md`.

## Troubleshooting

### Flow A (hosted)

- **413 `payload too large`** → HTML > 1 MB. Strip whitespace, drop inline assets, or switch to Flow B.
- **429 `rate limit`** → hit the 10 POST/h cap. Wait an hour or switch to Flow B.
- **404 on `/share` POST** → check you're using `POST /share`, not `POST /pergam`. Flow A doesn't have `/pergam`.

### Flow B (self-hosted)

- **`Connection refused` on `/healthz`** → server not reachable. Ask the user to `docker compose up -d` in their pergam dir or confirm the server's URL (`pergam_profiles.py list`).
- **`SSL certificate problem` / `unable to get local issuer certificate`** → the server uses a private CA: set its `ca` in `~/.config/pergam/servers.json` (or `$PERGAM_CA`). Do not retry with `-k`.
- **`unknown server 'x'`** from `pergam_profiles.py` → the user named a server the file does not define; show them the list and the file path.
- **Container restarts in a loop, logs show "could not connect to server"** → the pergam server can't reach its Postgres. If using bundled compose, `docker compose restart` after the db is ready.
- **400 "type must be one of […]"** → the message lists the types this server accepts (`PERGAM_TYPES`); pick one of those and retry.
- **400 "Missing author"** → always pass `author`.
- **403 "… is neither the owner of … nor one of its collaborators"** → the server trusts its proxy for identity and the pergam belongs to someone else. Tell the user who owns it (the message says) and that the owner can add them with `PUT /{id}/collaborators`; never repost the HTML as a new pergam to get around it.
- **401 "Missing X-Forwarded-User"** → the server trusts its reverse proxy for identity and the request did not come through it signed in. Use the URL the proxy serves and sign in there; never set the header yourself.
- **405 on DELETE** → expected. Pergams are immutable. Post a new version to supersede, or `PATCH /{id}/meta` with `{"archived": true}` to hide it.
- **404 on `PATCH /{id}/meta`** → the id has no versions on this server; check the id.
