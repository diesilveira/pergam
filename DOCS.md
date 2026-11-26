# pergam — docs

Full reference for the pergam HTTP service: endpoints, versioning model, type taxonomy, configuration, deployment, and the Claude Code skill.

For a quick intro and `docker compose up`, see the [README](README.md).

## Endpoints

| Method   | Path                  | Description                                                          |
| -------- | --------------------- | -------------------------------------------------------------------- |
| `POST`   | `/pergam`             | Body `{title, html, type, author, id?, tags?, note?}`. Returns `{id, version, view_url, title, type, author}`, plus `tags` and `note` when the request sent them. Omit `id` for a new pergam; include it to bump the version of an existing one. `tags` are merged into the id's tags (see [Tags and metadata](#tags-and-metadata)); `note` describes this version (see [Version notes](#version-notes)). |
| `GET`    | `/`                   | HTML index, 100 pergams per page. Filters, combinable: `?type=<type>`, `?author=<email>`, `?tag=<tag>`, `?q=<search>` (title or text), `?archived=1`. See [Index](#index) and [Search](#search). |
| `GET`    | `/api/pergams`        | JSON index: the same filters, order, pages and counts as `/`. See [JSON API](#json-api). |
| `GET`    | `/api/pergams/{id}`   | JSON: metadata, tags, `origin` and every version of one pergam. **404** for an unknown id. |
| `GET`    | `/{id}/view`          | View page of the latest version: a bar with title, type, tags, author and versions, and the document in a frame. See [View](#view). |
| `GET`    | `/{id}/v{n}/view`     | View page of a specific version; tells when a newer one exists.      |
| `GET`    | `/{id}/v{n}/content`  | The posted HTML followed by the frame bridge tag (`text/html`), what the view page frames. Version-pinned only. See [View](#view). |
| `GET`    | `/{id}/raw`           | Raw HTML source (latest).                                            |
| `GET`    | `/{id}/v{n}/raw`      | Raw HTML source (specific version).                                  |
| `GET`    | `/{id}/text`          | Visible text of the latest version as Markdown (`text/markdown`). See [Text](#text). |
| `GET`    | `/{id}/v{n}/text`     | Visible text of a specific version as Markdown.                      |
| `GET`    | `/{id}/diff/v{a}..v{b}` | HTML page comparing the text of two versions, section by section. See [Diff](#diff). |
| `GET`    | `/{id}/versions`      | JSON: `[{version, title, type, author, bytes, created_at, note}, …]`, newest first; `note` is `null` when the version has none. |
| `GET`    | `/{id}/meta`          | JSON: `{id, tags, archived, pinned}`. **404** for an unknown id.     |
| `PATCH`  | `/{id}/meta`          | Body with any of `{tags, archived, pinned}`; returns the resulting `{id, tags, archived, pinned}`. Never creates a version. **404** unknown id, **400** malformed body. |
| `GET`    | `/{id}/export`        | JSON document with every version of one pergam, its HTML and its metadata. See [Export and import](#export-and-import). **404** for an unknown id. |
| `POST`   | `/import`             | Body: an export document. Recreates the versions this server lacks, keeping id, version numbers and `created_at`. Returns `{id, imported, skipped, origin, view_url}`. **409** when a version already here differs. See [Export and import](#export-and-import). |
| `GET`    | `/feed.atom`          | Atom 1.0 feed of the 50 most recent publications; `?tag=<tag>`, `?author=<email>`. See [Feed](#feed). |
| `GET`    | `/static/frame-bridge.js` | The frame bridge script (`text/javascript`, cached five minutes). |
| `GET`    | `/healthz`            | Liveness probe.                                                      |
| `HEAD`   | any `GET` route       | Same status and headers as `GET`, without the body.                  |
| `DELETE` | `/{id}`               | **405 Method Not Allowed** — pergams are immutable.                  |

## Versioning model

Every pergam has a logical `id` (random 8-hex) and a monotonically increasing `version`. The public surface defaults to the latest version of an id:

- `POST /pergam` with no `id` → new pergam, `version=1`
- `POST /pergam` with an existing `id` → same id, `version + 1`
- `GET /{id}/view` → latest version
- `GET /{id}/v3/view` → version 3 specifically

Version numbers are assigned atomically: concurrent posts to the same `id` are serialized and receive consecutive versions, none of them fails.

Caching: a version-pinned content route (`/{id}/v{n}/content`, `/{id}/v{n}/raw`) never changes, so it is sent with `Cache-Control: public, max-age=31536000, immutable`. `/{id}/v{n}/text` is derived from that version by the text extractor, which can improve with an upgrade, so it is cached for five minutes only (`public, max-age=300`), and so is `/{id}/diff/v{a}..v{b}`, built from the same extraction. Everything else (both view pages, which count the versions, latest `/{id}/raw` and `/{id}/text`, the index, JSON routes, the feed) is `no-store`.

Pergams cannot be deleted. To "supersede" a pergam, post a new version with the same id; to hide it, archive it with `PATCH /{id}/meta`.

### Version notes

`note` on `POST /pergam` is an optional one-line description of what this version changes (`"adds the costs section"`). It is stored with the version and, like the version, never changes. Whitespace is collapsed; a blank note is the same as none; a note that is not a string, holds a control character other than whitespace or is longer than 280 characters is **400** and publishes nothing. Notes are returned by `/{id}/versions` and `/api/pergams/{id}` (`null` for versions without one) and shown in the view page's version selector.

## View

`/{id}/view` and `/{id}/v{n}/view` are pergam's own page around the document:

- **A slim bar** with the version's title, type, the id's tags, the author, the server it was [imported](#export-and-import) from when it was, `v3 of 5`, a version selector (every version, newest first, with its date and [note](#version-notes)), links to the index, the version's `raw` HTML, its `text` and the document alone (`open document`), and a **print** button.
- **A notice** on any version that is not the latest, linking to the current one (`/{id}/view`).
- **The document** in a full-height iframe loaded from `/{id}/v{n}/content`, always version-pinned, so the browser caches it for good.

`/raw` returns the posted bytes untouched, as `text/plain`. `/content` returns them followed by one tag, `<script src="/static/frame-bridge.js"></script>`: the **frame bridge**, which talks to the view page with `postMessage` only, never reaching into the other side's DOM or location, so it keeps working once the frame is sandboxed. Through it:

- **Anchors**: in-document links (`#section`) scroll the document, and the view URL follows them (`/{id}/view#section`, replaced, not a new history entry). A fragment on the view URL, at load or changed later, scrolls the document without adding history.
- **Links** that leave the document and have no `target` of their own (and no `<base target>`) open in the whole window, not inside the frame. Links with a `target`, downloads, and clicks with a modifier key keep their usual behaviour.
- **Title**: the tab shows the document's `<title>`, then the version.
- **Print**: the print button, or Ctrl/⌘+P anywhere on the page, prints the whole document rather than the visible part of the frame. The button appears only once the bridge has answered.

What changes for a document inside the frame, compared with opening `/content` on its own:

- `window.location` is `/{id}/v{n}/content`, not the view URL, and `window.top !== window`. A fragment the reader follows still shows in the view URL through the bridge.
- Relative URLs resolve against `/{id}/v{n}/` (`href="notes"` is `/{id}/v{n}/notes`, a 404): link other pergams and assets by absolute path or full URL.
- The frame is the viewport: `100vh`, `position: fixed` and `sticky`, and `matchMedia` measure the frame, below the bar.
- The tab title comes from the document's `<title>`; without one it stays the pergam's title.
- The bridge needs script to run: a document whose own CSP forbids `/static/frame-bridge.js`, or whose markup leaves an element such as `<script>` or a comment unclosed at the end, keeps its links inside the frame and shows no print button.

The view page treats every message as untrusted data: it accepts only those from its own frame, only strings, at most 2,048 characters, and a fragment only when it starts with `#`; nothing is ever inserted as HTML. The bar links to the version's changes (`/{id}/diff/v{n-1}..v{n}`) and the notice on an earlier version to what changed since (`/{id}/diff/v{n}..v{latest}`). Unknown ids or versions are **404**. The frame is not sandboxed yet: published scripts run on the server's origin as they did before the view page.

## Diff

`GET /{id}/diff/v2..v5` compares what a reader sees in two versions, without opening two tabs:

- Both versions go through the same extraction as [`/text`](#text), so markup-only changes (classes, wrappers, styles) do not show. Blank lines are ignored outside code blocks.
- The Markdown lines are compared with Python's `difflib` and grouped by section: every heading opens one, text before the first heading forms its own. A heading changed on its own counts as the same section renamed.
- Changed sections are open, with additions (`+`, green) and removals (`−`, red); a replaced line is shown next to its replacement with the changed words marked. Runs of unchanged lines are cut to two lines around each change. Unchanged sections are collapsed.
- The header shows both versions with their [notes](#version-notes) and links to each one's view page.

The range is `v{a}..v{b}`, the newer side on the right; `a` may be larger than `b` (read backwards) or equal (no differences). A range that is not `v{a}..v{b}` with positive numbers without leading zeros is **400**; an unknown id or version is **404**.

The work is bounded, so a diff answers in well under a second whatever the input:

- Text both versions share at the start and at the end is matched without cost; lines or words that repeat more than 8 times on the newer side never anchor a match (they still extend one).
- Matching is metered. A replaced line whose word comparison would overrun its share, or any once the request has spent its total, is marked as a whole line instead of word by word.
- A version over 2 MB of HTML or 20,000 lines of text, or line matching that overruns its budget, gets a **too large to diff** page (still `200`) with links to both versions' `/text`.

## Export and import

`GET /{id}/export` returns one pergam whole, as one JSON document, to recreate it on another server:

```json
{
  "format_version": 1,
  "id": "abc12345",
  "server": "http://localhost:1111",
  "meta": {"tags": ["q3"], "archived": false, "pinned": false},
  "versions": [
    {"version": 1, "title": "Plan", "html": "<!doctype html>…", "type": "plan", "author": "you@example.com",
     "note": null, "bytes": 5120, "created_at": "2025-03-04T05:06:07.123456+00:00"}
  ]
}
```

- `versions` holds every version, oldest first, with the HTML exactly as posted (what `/{id}/v{n}/raw` returns, never `/content` with the frame bridge appended). `server` is this server's `PUBLIC_URL` (or `PUBLIC_HOST`/`PUBLIC_PORT`).
- `POST /import` takes that document, or the same document with only some of its versions, and writes, in one transaction:
  - every version this server does not have yet, with its version number, `created_at`, note and text (searchable at once);
  - nothing for a version it already has with the same `title`, `html`, `type`, `author` and `note` (`created_at` and `bytes` are not compared): it is listed as skipped. Importing the same document twice is a no-op;
  - **409** and nothing at all when any version it already has differs, or when the result would leave a version number missing (a document with v3 only, for an id this server does not have);
  - for an id new to this server, its `tags`, `archived` and `pinned` from `meta`, and `origin`: the document's `server` and `id`. For an id it already has, `meta.tags` are merged into its tags as with `POST /pergam`, and `archived`, `pinned` and `origin` stay as they are.
- The response is **201** when anything was written, **200** when every version was skipped: `{"id": "abc12345", "imported": [4, 5], "skipped": [1, 2, 3], "origin": {"url": "http://localhost:1111", "id": "abc12345"}, "view_url": "…"}`.
- Validation follows `POST /pergam` for `type` (trimmed, lowercased, one of this server's [types](#pergam-types)), `author` (trimmed, non-empty) and `note` (see [Version notes](#version-notes)), and is stricter elsewhere, since an export always carries every field:
  - `title` and `html` must be non-empty strings and are stored exactly as sent: `title` is not trimmed and has no `"Pergam"` default;
  - `title`, `html` and `author` must not contain NUL;
  - `format_version` must be 1 and the id 6 to 16 lowercase hex characters;
  - `version`s must be distinct integers from 1 to 2147483647, at most 10,000 of them per import (a longer history goes in several imports, each carrying the next versions);
  - `created_at` must be ISO 8601 with a UTC offset, between 1970-01-01 and five minutes past the server's clock; it is stored in UTC and must not decrease from one version to the next (a document whose versions go back in time is **400**; one whose versions would be dated before a version already here is **409**);
  - `server` (optional) must be an absolute `http(s)` URL with a valid host and port, without credentials, query or fragment.
  Anything else is **400** and writes nothing. `bytes` is recomputed from the HTML and unknown fields are ignored.
- The body may be as large as `PERGAM_MAX_BODY_MB` (see [Configuration](#configuration)).

`origin` is shown in the view page's bar ("imported from localhost:1111/abc12345", linking to the original) and returned by `GET /api/pergams/{id}` (`null` when the pergam was not imported). It cannot be edited. The skill's [promote helper](skills/post-pergam/SKILL.md#promoting-a-pergam-to-another-server-flow-b), `skills/post-pergam/pergam_promote.py` (stdlib only), combines both in one command between two named servers and uploads only the versions the target lacks: `python3 skills/post-pergam/pergam_promote.py abc12345 --from local --to team`.

## Tags and metadata

Tags, `archived` and `pinned` belong to the logical `id`, not to a version, and live in `pergam_meta`. Changing them never creates a version. An id without a metadata row reads as no tags, not archived, not pinned.

- Tags are free-form and normalised: trimmed, lowercased, runs of whitespace, `_` and `-` collapsed to one `-`, empties dropped, duplicates removed, stored sorted. `" Q3  Review_notes "` → `q3-review-notes`.
- At most 20 tags per id, each at most 40 characters after normalisation, with no control characters (tabs and newlines are separators and become `-`); otherwise the request gets **400** and nothing is written.
- `POST /pergam` with `tags` **merges** them into the id's existing tags; posting without `tags` (or `"tags": null`) leaves them as they are.
- A new version never changes `archived` or `pinned`: posting to an archived id keeps it archived (and out of the index).
- `PATCH /{id}/meta` edits metadata of an id that already has versions. Fields it omits keep their value; `tags` **replaces** the whole set (`[]` clears it); `archived` and `pinned` must be JSON booleans. Unknown fields, an empty object or a wrong type are **400**.

```bash
curl -sS -X PATCH http://localhost:1111/abc12345/meta \
  -H 'Content-Type: application/json' \
  -d '{"tags":["billing","q3"],"pinned":true}'
# → {"id":"abc12345","tags":["billing","q3"],"archived":false,"pinned":true}
```

## Index

`GET /` lists the latest version of every pergam.

- **Filters** combine with AND: `type`, `author`, `tag` (normalised like stored tags, so `?tag=Q3` matches `q3`) and `q` (title or text, see [Search](#search)). Clicking a type, author or tag pill sets that filter and keeps the others; clicking the active one clears it.
- **Archived** pergams are left out unless `?archived=1`, which lists them alongside the rest, marked `archived`. They stay reachable by URL either way. Archiving belongs to the id, so a new version of an archived pergam stays archived and hidden, and the `201` does not say so; un-archive it with `PATCH /{id}/meta` `{"archived": false}`.
- **Order**: pinned first, then by the latest version's `created_at` (newest first), then by id. The order is total, so paging is stable.
- **Pages** hold 100 pergams. The "next →" link carries an opaque `cursor` (the sort key of the last row) plus the active filters. While the data does not change, following the links visits every matching pergam exactly once. A pergam that gets a new version or is pinned, unpinned, archived or retagged mid-crawl changes its sort key or match, so it can move to a page already seen (skipped) or one still ahead (seen twice). A malformed `cursor` is **400**.
- Pill counts (type, author, tag) and the count under the title cover every visible pergam, not only the current page: hidden archived pergams are left out of them and the title line says how many are hidden. The stats cards cover the whole instance, archived included. "clear all" drops every filter, `archived=1` included.

## JSON API

`GET /api/pergams` is the index as JSON. It takes the same query parameters as `/` (`type`, `author`, `tag`, `q`, `archived`, `cursor`) and comes from the same query, so for the same parameters it lists exactly the pergams the HTML index lists, in the same order, 100 per page.

```json
{
  "pergams": [
    {"id": "abc12345", "version": 2, "title": "Release notes", "type": "plan", "author": "you@example.com",
     "created_at": "2025-03-04T05:06:07+00:00", "bytes": 5120, "versions": 2, "tags": ["q3"],
     "archived": false, "pinned": false, "view_url": "http://localhost:1111/abc12345/view",
     "text_url": "http://localhost:1111/abc12345/text", "snippet": null}
  ],
  "matching": 1,
  "next_cursor": null,
  "archived_count": 0,
  "counts": {"types": [{"type": "plan", "count": 1}], "authors": [{"author": "you@example.com", "count": 1}],
             "tags": [{"tag": "q3", "count": 1}]},
  "stats": {"pergams": 1, "authors": 1, "versions": 2, "bytes": 9000, "new_pergams_7d": 1, "new_versions_7d": 2}
}
```

- Each row describes the latest version; `versions` is how many the id has. `snippet` is the matching text fragment (plain text) when `q` matched the body, else `null`.
- `matching` counts every pergam that matches the filters, across pages. `next_cursor` is `null` on the last page; otherwise pass it back as `?cursor=` with the same filters. A malformed cursor is **400**.
- `counts` and `stats` are the index's pills and cards: counts cover every visible pergam, stats the whole instance.

`GET /api/pergams/{id}` returns one pergam: the latest version's `version`, `title`, `type`, `author`, `created_at`, `bytes`, `note`, `view_url` and `text_url`, the id's `tags`, `archived`, `pinned` and `origin` (`{url, id}` of the server it was [imported](#export-and-import) from, else `null`), and `versions`, newest first, each with `version`, `title`, `type`, `author`, `bytes`, `created_at`, `note` and its pinned `view_url` and `text_url` (`/{id}/v{n}/view`, `/{id}/v{n}/text`). An unknown id is **404**.

Both are `Cache-Control: no-store`.

## Text

`GET /{id}/text` (latest) and `GET /{id}/v{n}/text` return what a reader sees on the page, as Markdown, for agents and scripts that need the content without the markup. It is the same extraction the search index uses:

- what a browser does not render is dropped: `script`, `style`, `title`, `iframe` fallback, a closed `noscript` or `template`, and the `head`, which ends at `</head>`, `<body>` or the first content that cannot be in a head (its end tag is optional). An unclosed `noscript` or `template` is kept as content instead of hiding the rest of the page;
- `h1`–`h6` become `#`–`######` lines, `ul`/`ol` items `-` or `1.` lines (nested lists indented two spaces), `pre` a fenced code block with its whitespace kept (a block element such as `<p>` ends an unclosed `pre`);
- a `table` becomes a pipe table whose first row is the header (cells padded to the widest row, `|` escaped as `\|`, nested tables flattened into their cell), its `caption` a paragraph before it;
- everything else is paragraphs, whitespace collapsed and entities decoded. Links, images and inline styling are reduced to their text. A paragraph or list item that starts with a Markdown marker (`#`, `>`, `*`, `+`, `-`, `1.`, `1)`, a code fence) gets it backslash-escaped, so it stays text.

The body is `text/markdown; charset=utf-8` and ends with a newline, or is empty when the page has no visible text. Unknown ids or versions are **404**.

```bash
curl -sS http://localhost:1111/abc12345/text
```

## Feed

`GET /feed.atom` is an Atom 1.0 (RFC 4287) feed, `application/atom+xml`, of the 50 most recent publications. The index page advertises it, so feed readers find it from `/`.

- **Every published version is an entry**, newest first: a new version of an id appears as a new entry titled `Title (v2)`. Its `id` and `alternate` link are the version's immutable `/{id}/v{n}/view` URL, with a second `alternate` link (`text/markdown`) to `/{id}/v{n}/text`.
- Each entry has the version's `author` (as `<name>`), `published`/`updated` (its `created_at`), a `category` for its type and one per tag, and a `summary`: the start of its text, or `<type> by <author>` when it has none.
- **Filters**: `?tag=` (normalised like stored tags) keeps entries whose id carries that tag; `?author=` keeps versions posted by that author; both combine with AND. Archived pergams are left out.
- The feed's `id` and `rel="self"` link are its own URL including the filters, so each filtered feed is a separate subscription. URLs are built from `PUBLIC_URL` (or `PUBLIC_HOST`/`PUBLIC_PORT`), like `view_url`.

## Search

`?q=` matches a pergam when its latest version's title contains `q` (case-insensitive substring, as before) **or** its title or text matches `q` as a full-text query.

- **Text** is the visible text of the HTML, extracted at publish time by the same parse that serves `GET /{id}/text`, stored as plain lines (the same blocks without Markdown syntax, so snippets show no `#` or `|`). The contents of `script`, `style`, `template`, `noscript` and `head` are never indexed.
- **Query syntax** is Postgres' web-search syntax: words are ANDed, `"quoted phrase"` matches the words in order, `or` and `-excluded` work. Matching is language-neutral (`simple` configuration): case-insensitive, no stemming, so `region` does not match `regions`, and no accent folding, so `informacion` does not match `información`. Title words weigh more than body words.
- **Snippet**: when the text matched, the row shows the matching fragment with the hits highlighted. A title-only match shows no snippet.
- `q` is at most 200 characters (after trimming); longer is **400**, on `/` and on `/api/pergams`.
- **Order and paging** are the same as without `q` (pinned, newest, id), so the cursor works unchanged.
- Only the first 100,000 characters of a version's text are indexed; the rest is still served by `/{id}/text`.

Versions published before search existed are indexed by the server itself: after applying migrations it backfills them in the background, in batches of 50, each committed on its own. Every text row records the extractor version that wrote it, and the same backfill re-extracts rows written by an older extractor, so an upgrade that improves extraction also refreshes text that is already stored. The server answers requests meanwhile, and until a version is indexed `q` still finds it by title. Stopping the server mid-way is safe: the next start resumes with what is missing or stale. A document the extractor fails on is logged (`text extraction failed for <id> v<n>`) and stored with empty text, so it is still found by title and never stops the run or its publish. The backfill only reads `pergams`; the log says `text backfill: filled N versions` when it did any work.

## Pergam types

`type` is required and must be one of the server's accepted types. They come from `PERGAM_TYPES` (comma separated, trimmed and lowercased); unset or blank means the five defaults:

| Type            | When to pick it                                                       |
| --------------- | --------------------------------------------------------------------- |
| `plan`          | Step-by-step implementation plans, deploy plans, migration plans.     |
| `investigacion` | Research, evaluations, comparing alternatives.                        |
| `informe`       | Reviews, audits, post-mortems, status reports.                        |
| `reporte`       | Recurring dashboards, comparison boards, leaderboards.                |
| `otro`          | Anything that doesn't fit the above.                                  |

The posted `type` is trimmed and lowercased; anything else is **400** `type must be one of [...]`, listing what this server accepts. Removing a type from `PERGAM_TYPES` only stops new posts: pergams already stored with it still render, list and filter. Each badge colour is derived from the type name (the five defaults keep their fixed colours), so adding a type needs no CSS change.

## Configuration

App env vars follow 12-factor conventions; only the pergam-specific knobs carry a `PERGAM_*` prefix:

| Env var             | Default                | Description                                  |
| ------------------- | ---------------------- | -------------------------------------------- |
| `DATABASE_URL`      | bundled `db` service   | Postgres connection string.                  |
| `HOST`              | `0.0.0.0`              | Bind host inside the container.              |
| `PORT`              | `1111`                 | Bind port inside the container.              |
| `PUBLIC_URL`        | unset                  | Base of returned `view_url`s, e.g. `https://docs.example.com` → `https://docs.example.com/{id}/view`. Takes precedence over `PUBLIC_HOST`/`PUBLIC_PORT`. |
| `PUBLIC_HOST`       | `localhost`            | Host used in returned `view_url`s when `PUBLIC_URL` is unset. |
| `PUBLIC_PORT`       | `PORT`                 | Port used in returned `view_url`s when `PUBLIC_URL` is unset; `443` means `https`, `80`/`443` are omitted from the URL. |
| `PERGAM_TYPES`      | `plan,investigacion,informe,reporte,otro` | Accepted `type` values, comma separated (see [Pergam types](#pergam-types)). |
| `PERGAM_MAX_BODY_MB` | `256`                | Largest request body, in MB, for `POST` and `PATCH`. A larger declared `Content-Length` is **413** and nothing is read; a `Content-Length` that is not a number is **400**. The default leaves room for a multi-version `POST /import`. |
| `POSTGRES_PASSWORD` | `pergam`               | Password for the bundled `db` service.       |
| `PERGAM_PORT`       | `1111`                 | Host-side published port (compose only).     |
| `MIGRATIONS_DIR`    | `/app/db/migrations`   | Directory of `NNN_*.sql` migrations (the image sets it; from a checkout it defaults to `db/migrations`). |

See `.env.example` for the full set. The server refuses to start when `PUBLIC_URL` is not an absolute `http(s)` URL, carries credentials, a query or fragment marker (`?`, `#`) or a non-numeric port, when `PUBLIC_PORT` is not a number, or when `PERGAM_MAX_BODY_MB` is not a positive integer.

## Schema migrations

The schema lives in numbered files, `db/migrations/NNN_description.sql`, shipped inside the image. On every start the server applies the pending ones before it listens:

- Applied migrations are recorded in `schema_migrations (version, name, applied_at)`.
- Each migration runs in its own transaction: a failure rolls it back completely, the server logs `migrations failed` and exits non-zero.
- A Postgres advisory lock serializes servers that start at the same time against one database.

Migrations are additive and safe on a populated database: they never drop or rewrite existing rows, and any backfill is idempotent. `001_initial_schema.sql` is the schema the bundled compose used to load through `docker-entrypoint-initdb.d`. When `pergams` already exists it runs no DDL at all, so existing instances upgrade in place with every row, index and view untouched and only `schema_migrations` gains a row. `002_pergam_meta.sql` adds the `pergam_meta (id, tags, archived, pinned)` table and writes no rows. `003_pergam_text.sql` adds `pergam_text (id, version, body, document)`: the extracted text and the `tsvector` (with a GIN index) of every version, filled on publish and, for older versions, by the server's background backfill (see [Search](#search)); `pergams` keeps its schema and rows. `004_pergam_text_extractor.sql` adds `pergam_text.extractor` (existing rows read as version 1) so the backfill can re-extract text written by an older extractor; it rewrites no rows. `005_pergam_note.sql` adds `pergam_note (id, version, note)`, one row per version posted with a note; it writes no rows, so versions published before it have no note. `006_pergam_origin.sql` adds `pergam_meta.origin_url` and `origin_id` (both nullable, existing rows read as no origin); it rewrites no rows. To add a change, create the next number and restart the server.

### Database privileges

The `DATABASE_URL` role needs:

| Database state | Privileges |
| -------------- | ---------- |
| Fresh (no `pergams` yet) | `CREATE` on the schema: the role creates and owns the tables. The bundled compose user owns its database, so nothing to do there. |
| Initialised by the old `schema.sql`, role not the owner | `SELECT, INSERT` on `pergams`, `SELECT` on `pergams_latest`, plus either `CREATE` on the schema (to create `schema_migrations` once) or a `schema_migrations` table created by the owner with `SELECT, INSERT` granted to the role. |

A later migration that changes the schema needs the privileges that change requires (usually ownership of the table), and says so in its file. `002_pergam_meta.sql` creates `pergam_meta`, so it needs `CREATE` on the schema; alternatively the owner runs that file once and grants `SELECT, INSERT, UPDATE` on `pergam_meta` to the role, and the migration then runs no DDL. `003_pergam_text.sql` works the same way with `pergam_text`, for which the role needs `SELECT, INSERT, UPDATE`. `004_pergam_text_extractor.sql` alters `pergam_text`, so it needs ownership of that table (the role owns it when it ran `003` itself); alternatively the owner runs that file once too, and the migration then runs no DDL. `005_pergam_note.sql` creates `pergam_note` like `002` and `003` do, so it needs `CREATE` on the schema, or the owner runs it once and grants `SELECT, INSERT` on `pergam_note` to the role. `006_pergam_origin.sql` alters `pergam_meta` like `004` alters `pergam_text`: it needs ownership of that table (the role owns it when it ran `002` itself), or the owner runs that file once and the migration then runs no DDL.

## Project layout

```
pergam/
├── src/pergam/              stdlib http.server + psycopg, no framework
│   ├── server.py            entry point: migrations, pool, HTTP server
│   ├── routes.py            HTTP routing and request validation
│   ├── store.py             data access (all SQL)
│   ├── index_page.py        HTML index renderer
│   ├── view_page.py         view page: version bar around the framed document
│   ├── diff.py              text diff of two versions, grouped by section
│   ├── diff_page.py         diff page renderer
│   ├── api.py               JSON bodies of /api/pergams
│   ├── feed.py              Atom feed of recent publications
│   ├── templates/           index, view and diff pages (HTML + CSS)
│   ├── static/              frame-bridge.js, appended to /content
│   ├── public_url.py        host/URL used in view_url and on the index
│   ├── limits.py            largest request body (PERGAM_MAX_BODY_MB)
│   ├── taxonomy.py          accepted types and badge colours
│   ├── tags.py              tag normalisation and limits
│   ├── meta.py              per-id metadata and PATCH validation
│   ├── transfer.py          export document and import validation
│   ├── ids.py               shape of a pergam id
│   ├── text.py              visible text of a pergam as Markdown (search and /text)
│   ├── search.py            full-text search SQL and snippets
│   ├── backfill.py          text of versions without text or with stale text
│   ├── cursor.py            keyset cursor for index pagination
│   └── migrate.py           numbered migrations runner
├── db/
│   └── migrations/          NNN_*.sql, applied at server startup
├── tests/                   pytest suite (real server + throwaway Postgres)
├── scripts/
│   └── migrate_from_fs.py   import legacy ./data/*.html files
├── skills/post-pergam/      Claude Code skill, server profiles and promote helper (see below)
├── web/                     public landing + example hosted worker (Cloudflare)
├── .github/workflows/       CI: test suite + image build on every pull request
├── Dockerfile               python:3.14-slim, runs as uid 10001
├── docker-compose.yml       app + bundled postgres + healthchecks
├── Makefile                 up / down / restart / logs / ps / health / clean / test
├── requirements.txt         psycopg[binary], psycopg-pool
├── requirements-dev.txt     + pytest (tests only)
└── .env.example
```

## Claude Code skill

`skills/post-pergam/` contains a [Claude Code](https://claude.com/claude-code) skill that knows how to talk to pergam. To install it:

```bash
# user-level (available in every project)
cp -r skills/post-pergam ~/.claude/skills/

# OR project-level (committed to a repo, available only there)
cp -r skills/post-pergam <your-repo>/.claude/skills/
```

The skill reads the user's email from the Claude Code session, picks a `type` from context, and decides whether to publish as a new pergam or a new version of an existing one. It talks to named servers (`hosted`, `local`, `team`, …) defined in `~/.config/pergam/servers.json`, each with its URL and an optional CA bundle for a private TLS certificate; with no file, `$PERGAM_URL` works as before. `skills/post-pergam/pergam_profiles.py` (stdlib only) resolves the name the user gave, then `$PERGAM_URL`, then the file's `default`, then the hosted backend. See `skills/post-pergam/SKILL.md` for the full contract.

## Deployment

For a single internal team, the easiest path is:

1. Run `docker compose up -d` on a small VM behind your VPN.
2. Point a DNS record (e.g. `pergam.internal`) at it.
3. Optionally put [Caddy](https://caddyserver.com/) in front for TLS and auth — pergam itself has no auth and assumes the network is a trust boundary.

For larger teams, swap `tls internal` for an OIDC sidecar (e.g. `oauth2-proxy`) so per-user attribution flows in via `X-Forwarded-User`.

## Testing

```bash
make test
```

That creates `.venv`, installs `requirements-dev.txt` and runs `pytest`. The suite boots the real server (`python3 src/pergam/server.py`) against a throwaway Postgres: by default it starts a `postgres:15-alpine` container named `pergam-test-*` on a random local port with no volume, and removes it at the end. Set `TEST_DATABASE_URL` to an admin connection string to use an existing server instead (CI does this with a `services: postgres` container). Either way every run works in its own `pergam_test_*` database, dropped afterwards; the bundled compose database is never touched.

`tests/test_frame_bridge.py` drives the view page and its frame bridge in a real browser through Playwright (a test-only dependency), with the installed Google Chrome or a Playwright Chromium, each case plain and with the frame sandboxed. Without Playwright or a browser those tests are skipped.

## Make targets

```
make up        # docker compose up -d --build
make down      # docker compose down
make restart   # docker compose restart
make logs      # tail logs from both services
make ps        # show service status
make health    # curl /healthz
make clean     # stop everything and wipe the bundled db volume
make test      # run the test suite (see Testing)
```

## Privacy

No telemetry. No external network calls. The server only reads/writes its own Postgres. Embedded HTML may pull external resources (Mermaid CDN, fonts) if the publisher includes them — that's outside pergam's control.
