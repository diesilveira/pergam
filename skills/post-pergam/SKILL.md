---
name: post-pergam
description: Use when the user wants an HTML dashboard, report, comparison board, or any self-contained HTML artifact deployed somewhere viewable. POSTs the HTML to a running `pergam` server and returns a `http://localhost:1111/{id}/view` URL (or whatever the configured public URL is). Supports versioning — same id with version++ when the user iterates on a previous pergam. Triggered by phrases like "publish/post/deploy this as a pergam", "armá un pergam de esto", "show this as a viewable pergam", "actualizá el pergam", "v2 de", "iterá sobre", or any request to output HTML that should be browseable.
---

# post-pergam

Publish a self-contained HTML document to a running [`pergam`](https://github.com/diesilveira/pergam) server and return a shareable URL. Pergams are **versioned and immutable**: every POST either creates a new logical pergam (random 8-hex `id`, `version=1`) or bumps the version of an existing one (same `id`, `version` incremented).

## Steps

1. **Ensure the pergam server is reachable.** Default is `http://localhost:1111`. If the user runs pergam locally, `cd <pergam-dir> && docker compose up -d`. If they point it at a remote URL (env var `PERGAM_URL` or similar), use that instead. Check health:
   ```bash
   curl -sS http://localhost:1111/healthz   # -> {"ok":true}
   ```
   If unreachable, ask the user where pergam is running rather than guessing.

2. **Decide: new pergam or new version?** See [§ Versioning](#versioning).

3. **Pick `type`** from `plan | investigacion | informe | reporte | otro`. See [§ Pergam types](#pergam-types).

4. **Determine `author`** from the Claude Code session's user email (exposed in your context as the user's email). Always pass it — the server requires it.

5. **Build the HTML** as a complete, self-contained document (see HTML guidelines below). Write it to a temp file so the JSON payload escapes cleanly — do NOT try to embed multi-line HTML directly in `curl -d`.
   ```bash
   python3 - <<'PY'
   import json, pathlib
   html = pathlib.Path("/tmp/pergam.html").read_text()
   pathlib.Path("/tmp/pergam.payload.json").write_text(json.dumps({
       "title":  "My pergam title",
       "html":   html,
       "type":   "plan",                # required
       "author": "you@example.com",     # required: the session's user email
       # "id":   "abc12345",            # OPTIONAL: present → new version of this id
   }))
   PY
   ```

6. **POST it:**
   ```bash
   curl -sS -X POST http://localhost:1111/pergam \
        -H "Content-Type: application/json" \
        --data-binary @/tmp/pergam.payload.json
   ```
   Response: `{"id":"abc12345", "version": 2, "view_url":"http://localhost:1111/abc12345/view", "title":"...", "type":"...", "author":"..."}`

7. **Report the `view_url` AND `version`** to the user as the final answer. Mention "v{n}" if it's not the first version, so they understand it stacked on top of the previous one.

## Versioning

The skill should decide which mode to use **before** posting:

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

## Pergam types

Required. Pick one based on what the content actually is. Free-form `text` on the server, but stick to this taxonomy so filters stay clean:

| Type            | When to pick it                                                       |
| --------------- | --------------------------------------------------------------------- |
| `plan`          | Step-by-step implementation plans, deploy plans, migration plans.     |
| `investigacion` | Research, evaluations, "we considered X/Y/Z", design exploration.     |
| `informe`       | Reviews, audits, post-mortems, status reports about a specific thing. |
| `reporte`       | Recurring dashboards, comparison boards, leaderboards, metrics views. |
| `otro`          | Anything that doesn't fit the above. Don't overuse.                   |

When iterating (new version of an existing pergam), **keep the same type as the previous version** unless the content shifts categorically.

## HTML guidelines

- Complete document: `<!doctype html><html lang="...">...</html>`.
- Inline all CSS. **No external fonts.** **No external JS** except Mermaid (see below).
- Use CSS Grid or flexbox for the actual layout.
- Dark theme. Paleta from `polish-guide.md`: `bg:#0d1117`, `bg2:#161b22`, `text:#e6edf3`, `muted:#8b949e`, `border:#30363d`, accent `#58a6ff`, ok `#3fb950`, warn `#d29922`, critical `#f85149`.

## Two modes

**Short pergam** (3–10 cards, dashboard, PR list, comparison board):
- Single `<section class="grid">` with cards, no CDN.

**Long-form content** (specs, design docs, multi-section reports):
- Start from `template.html` (sibling file): gradient header, sticky TOC, numbered sections, Mermaid for diagrams (dark theme), syntax tokens, callouts, badges, HTTP-method-colored tables, responsive collapse.
- For details (when to use what, color tokens, Mermaid class defs, responsive rules), read `polish-guide.md`.

If content has 5+ logical sections, diagrams, or schemas → long-form template. Otherwise → short pergam.

## Diagrams

Replace ASCII art with Mermaid in long-form content. Only allowed external dependency (`https://cdn.jsdelivr.net/npm/mermaid@11.4.1`). Use `flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`. Always set `theme: 'dark'` and define `classDef` colors per diagram. Full snippet in `polish-guide.md`.

## Endpoints

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

## Troubleshooting

- **`Connection refused` on `/healthz`** → the pergam server isn't reachable. Ask the user to start it (`docker compose up -d` in their pergam directory) or to confirm the URL.
- **Container restarts in a loop, logs show "could not connect to server"** → the pergam server can't reach its Postgres. Common cause: the database hostname in `DATABASE_URL` is unreachable from inside the container. If using the bundled compose, `docker compose restart` after the db is ready.
- **400 "type must be one of …"** → fix the payload to use one of `plan | investigacion | informe | reporte | otro`.
- **400 "Missing author"** → always pass `author` (Claude session email).
- **405 on DELETE** → expected. Pergams are immutable. Post a new version to supersede.
