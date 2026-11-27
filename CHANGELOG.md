# Changelog

All notable changes to the self-hosted server are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/). The hosted service ([pergam.dev](https://pergam.dev)) did not change in 2.0.

## [2.0.0] - 2026-11-27

2.0 turns the 1.x server, which published and listed documents, into one for living with them: organise, find, read and compare versions, and move pergams between servers. It also frames every published document in a sandbox. Running 1.x? Read [Upgrade notes](#upgrade-notes) first.

### Organise

- Types come from a configurable list (`PERGAM_TYPES`); tags are free-form and belong to the pergam, not to a version.
- Pin a pergam to show it as a card above the index (12 at most by default, `PERGAM_MAX_PINNED`); the most recently pinned comes first.
- Archive a pergam to take it out of the index. Archived pergams stay reachable by URL, and archiving also unpins.
- `PATCH /{id}/meta` edits tags, `archived` and `pinned` without creating a version.
- `POST /pergam` accepts optional `tags` (merged into the pergam's tags) and `note`.

### Find and read

- Search (`?q=`) matches titles and the visible text of every pergam, with highlighted snippets. The search field is in every page's top bar, and `/` focuses it.
- A filter dialog filters by several types, authors and tags and by status, with live counts. Filters combine, and the index sorts by date or title, 100 pergams per page with "Load more".
- Index rows carry a one-line summary; a switch shows it under each title.
- `/{id}/text` and `/{id}/v{n}/text` return what a reader sees as Markdown.
- `GET /feed.atom` follows new publications, filtered by tag, author or status.
- `GET /api/pergams`, `GET /api/pergams/{id}` and `GET /facets` return the index, one pergam and the filter counts as JSON.
- Mermaid 11.17.2 is served by the server at `/static/mermaid/11.17.2/mermaid.min.js`, so diagrams render with no external request.
- `POST /lint` reports what the sandbox would break (external resources, broken TOC anchors, malformed Mermaid blocks) with line and column, and stores nothing.

### Versions

- Every version can carry a one-line note (`note`, up to 280 characters).
- The view page opens each pergam at full width under one bar: version selector, Pin, History and a menu. The History panel lists every version with its note, author and date; older versions say when they were published and link to the current one.
- `/{id}/diff/v2..v5` compares two versions section by section, with pickers for the pair and a Sections list.

### Across servers

- `GET /{id}/export` returns a pergam whole (every version, its HTML and metadata) as one JSON document, and `POST /import` recreates it on another server with the same id, version numbers and dates. Importing the same document twice changes nothing, and a pergam remembers the server it came from (`origin`).
- The Claude Code skill keeps named servers in `~/.config/pergam/servers.json`, publishes to the one you name, and promotes a pergam from one server to another in one command (`pergam_promote.py`).

### Trust

- Published HTML runs sandboxed: framed in an opaque origin, under a content policy that blocks every request to the server's API. A script inside a pergam cannot read the index, publish, patch or submit a form. See [Upgrade notes](#upgrade-notes) for what that changes for documents.
- With `TRUST_PROXY_USER=1` the author of every write comes from `X-Forwarded-User`, set by an authenticating reverse proxy. Each pergam then has an owner (the author of v1) and a list of collaborators, and only they can add versions or edit it. Off by default.
- Every page the server renders refuses to be framed by another origin.
- Request bodies are capped (`PERGAM_MAX_BODY_MB`), and `POST /lint` is limited to 2 MB and two at a time.
- Errors are shown as a page to browsers; other clients get the same responses as before.

### Interface

- One visual system across the index, view, diff, filter and error pages, with light and dark themes: system by default, or chosen from the top bar and remembered. System fonts only; nothing is loaded from elsewhere.
- Every control works with the keyboard and has a visible focus ring; the pages are usable on a phone, and the filter dialog becomes a full-screen sheet there.
- The pages work without script: the filter button opens a page, menus are `<details>` dropdowns and "Load more" is a link. Script adds the dialog and live counts, the history panel and the pin, archive and tag controls.

### Changed

- Each release is published as a multi-arch image (`linux/amd64`, `linux/arm64`) at `ghcr.io/diesilveira/pergam`, tagged `<version>`, `<major>.<minor>`, `<major>` and `latest`. The bundled compose file runs it when `PERGAM_IMAGE` is set, and still builds from source when it is not. See [Published image](DOCS.md#published-image).
- `GET /{id}/view` and `GET /{id}/v{n}/view` return the view page, with the document in a frame, and no longer the document itself. `/{id}/raw` and `/{id}/v{n}/raw` still return the posted bytes as `text/plain`.
- The bundled compose service is now `pergam-local` (container `pergam-local`), and the database schema is created by migrations, not loaded from `db/schema.sql`.
- The image is based on `python:3.14-slim`; `psycopg` is 3.3.

### Upgrade notes

For anyone running 1.x. The `POST /pergam` contract is unchanged: `{title, html, type, author, id?}` still returns `{id, version, view_url, title, type, author}`. `tags` and `note` are optional additions, and the response carries them only when the request sent them. Clients and the skill that worked against 1.x keep working.

**Migrations.** Migrations `001` to `008` apply by themselves, in order, when the server starts, and the server listens only after they succeed. They are additive: none drops or rewrites an existing row, and the 1.x tables, indexes and view stay as they are. `001` records the 1.x schema without running DDL; `002` to `008` add `pergam_meta`, `pergam_text`, `pergam_note` and columns on them. The applied ones are listed in `schema_migrations`. A failed migration rolls back, logs `migrations failed` and exits non-zero. Back up the database before the first start all the same.

**Search index.** After the migrations the server fills the text of versions published before 2.0 in the background, 50 at a time. The server answers meanwhile, and until a version is indexed, search still finds it by title.

**Database privileges.** The role in `DATABASE_URL` needs:

- With the bundled compose database, nothing: the role owns its database.
- With an external database that 1.x initialised, a role that is not the owner needs `SELECT, INSERT` on `pergams`, `SELECT` on `pergams_latest`, and `CREATE` on the schema (it creates `schema_migrations`, `pergam_meta`, `pergam_text` and `pergam_note`), plus ownership of the tables it later alters (`pergam_meta`, `pergam_text`), which it has when it created them. Without `CREATE`, the owner runs the files in `db/migrations/` once and grants `SELECT, INSERT, UPDATE` on `pergam_meta` and `pergam_text` and `SELECT, INSERT` on `pergam_note` and `schema_migrations`. [DOCS.md](DOCS.md#database-privileges) lists them migration by migration.

**Bundled compose.** The service `app` (container `pergam`) is now `pergam-local`. Start 2.0 with `docker compose up -d --build --remove-orphans`, so the old container is replaced and does not keep holding the port. The `db_data` volume and the data in it carry over; a mount of `db/schema.sql` is gone. If you reference the service or container by name in scripts, update them.

**Published HTML is sandboxed.** Every document is framed with `sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-modals allow-top-navigation-by-user-activation"` (no `allow-same-origin`), and `/{id}/v{n}/content` repeats that sandbox in a `Content-Security-Policy` with these sources: scripts inline, from the server and from `https://cdn.jsdelivr.net`; styles inline and from jsDelivr; images `data:` only; fonts `data:` and jsDelivr; `connect-src` jsDelivr only; no forms, no `<base>`, no nested frames. Documents already published are not rewritten, but some will look or behave differently:

- Images and fonts from any other host no longer load. Inline them as `data:` URIs.
- Scripts and stylesheets from hosts other than the server and jsDelivr no longer load.
- `fetch` and XHR to anything but jsDelivr are blocked, and so are forms, nested frames and cookies.
- `localStorage` and `sessionStorage` do not persist. A small stand-in keeps documents that use them without a guard working, but its contents are gone on every load.
- A document cannot navigate the whole window without a click, and relative URLs resolve against `/{id}/v{n}/`, not the view URL.
- Mermaid from `https://cdn.jsdelivr.net` keeps rendering, and so do inline scripts and styles. New documents should load the server's copy from `/static/mermaid/`.

`POST /lint` tells you which of these a document hits before you publish it. Posted bytes are untouched: `/raw` and `/export` return exactly what was posted. In 1.x a document was served as the page itself, on the server's own origin; in 2.0 it never runs there. `/{id}/v{n}/content`, the route the view page frames, is cached for five minutes (`public, max-age=300`).

**Archived pergams and `?archived=1`.** The index shows active pergams by default. `?state=active|archived|all` chooses the status: `state=archived` lists only archived pergams and `state=all` both. `/?archived=1` is answered with a **301** to the same URL with `state=all`, so links that used it keep their meaning; on `/api/pergams` it reads as `state=all` with no redirect. Any other `state` is **400**.

**New environment variables.** None is required; the compose file passes each one through.

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `PUBLIC_URL` | unset | Base of returned `view_url`s, e.g. `https://pergam.example.com`. Wins over `PUBLIC_HOST` and `PUBLIC_PORT`. Set it behind a reverse proxy: the content policy names this origin. |
| `PERGAM_TYPES` | `plan,investigacion,informe,reporte,otro` | Accepted `type` values, comma separated. Removing one stops new posts only; stored pergams keep theirs. |
| `PERGAM_MAX_BODY_MB` | `256` | Largest request body in MB; a larger `Content-Length` is **413**. |
| `TRUST_PROXY_USER` | off | `1` takes the author from `X-Forwarded-User` and enforces owners. Only behind a proxy that sets the header and drops client copies, with pergam reachable through it alone. |
| `PERGAM_MAX_PINNED` | `12` | Most pergams pinned at once; `0` turns pinning off. |

The server refuses to start on a value it cannot read, such as a `PUBLIC_URL` that is not an absolute `http(s)` URL or a `TRUST_PROXY_USER` that is neither on nor off.

**Browsers.** The interface uses `light-dark()` and `color-mix()`: Chrome 123, Firefox 120 or Safari 17.5 and later. Older browsers show the pages without colours. Documents keep their own look in any browser.

[2.0.0]: https://github.com/diesilveira/pergam/compare/v1.0.0...v2.0.0
