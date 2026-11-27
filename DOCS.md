# pergam — docs

Full reference for the pergam HTTP service: endpoints, versioning model, type taxonomy, configuration, deployment, and the Claude Code skill.

For a quick intro and `docker compose up`, see the [README](README.md).

## Endpoints

| Method   | Path                  | Description                                                          |
| -------- | --------------------- | -------------------------------------------------------------------- |
| `POST`   | `/pergam`             | Body `{title, html, type, author, id?, tags?, note?}`; `author` must not contain control characters (**400**). Returns `{id, version, view_url, title, type, author}`, plus `tags` and `note` when the request sent them. Omit `id` for a new pergam; include it to bump the version of an existing one. `tags` are merged into the id's tags (see [Tags and metadata](#tags-and-metadata)); `note` describes this version (see [Version notes](#version-notes)). |
| `GET`    | `/`                   | HTML index, 100 pergams per page. Filters, combinable: `?type=<type>`, `?author=<email>`, `?tag=<tag>` (each may repeat: any of the values), `?q=<search>` (title or text), `?state=active\|archived\|all`; order: `?sort=recent` (default) or `?sort=title`. `?archived=1` redirects (**301**) to `state=all`. See [Index](#index), [Filters](#filters) and [Search](#search). |
| `GET`    | `/filters`            | The filter form as a page, for the same query as `/`: what the Filters button opens without script. See [Filters](#filters). |
| `GET`    | `/filters/form`       | The filter dialog's form alone (an HTML fragment), for the same query as `/`: what the index's dialog loads on first open. |
| `GET`    | `/api/pergams`        | JSON index: the same filters, status, sort, pages and counts as `/`. See [JSON API](#json-api). |
| `GET`    | `/facets`             | JSON: `{total, facets: {type, author, tag, state}}` for the same query as `/`, the filter dialog's live counts. See [JSON API](#json-api). |
| `GET`    | `/api/pergams/{id}`   | JSON: metadata, tags, `origin` and every version of one pergam. **404** for an unknown id. |
| `GET`    | `/{id}/view`          | View page of the latest version: a bar with title, type, tags, versions and actions, a history and details panel, and the document in a frame. See [View](#view). |
| `GET`    | `/{id}/v{n}/view`     | View page of a specific version; tells when a newer one exists.      |
| `GET`    | `/{id}/v{n}/content`  | The posted HTML with the frame bridge tag added (`text/html`, sandboxed by its CSP), what the view page frames. Version-pinned only. See [View](#view) and [Sandbox](#sandbox). |
| `GET`    | `/{id}/raw`           | Raw HTML source (latest).                                            |
| `GET`    | `/{id}/v{n}/raw`      | Raw HTML source (specific version).                                  |
| `GET`    | `/{id}/text`          | Visible text of the latest version as Markdown (`text/markdown`). See [Text](#text). |
| `GET`    | `/{id}/v{n}/text`     | Visible text of a specific version as Markdown.                      |
| `GET`    | `/{id}/diff/v{a}..v{b}` | HTML page comparing the text of two versions, section by section. See [Diff](#diff). |
| `GET`    | `/{id}/versions`      | JSON: `[{version, title, type, author, bytes, created_at, note}, …]`, newest first; `note` is `null` when the version has none. |
| `GET`    | `/{id}/meta`          | JSON: `{id, tags, archived, pinned}`. **404** for an unknown id.     |
| `PATCH`  | `/{id}/meta`          | Body with any of `{tags, archived, pinned}`; returns the resulting `{id, tags, archived, pinned}`. Never creates a version. **404** unknown id, **400** malformed body, **409** a pin the server refuses (see [Pinning](#pinning)). |
| `GET`    | `/{id}/collaborators` | JSON: `{id, owner, collaborators}`; `owner` is the author of v1. **404** for an unknown id. See [Ownership](#ownership). |
| `PUT`    | `/{id}/collaborators` | Body `{collaborators: [user, …]}`; replaces the list and returns `{id, owner, collaborators}`. Only the owner, and only with `TRUST_PROXY_USER` on (**409** otherwise). See [Ownership](#ownership). |
| `POST`   | `/lint`               | Body `{html}`. Returns the problems that would break the document once published, each with its line and column; stores nothing. See [Lint](#lint). |
| `GET`    | `/{id}/export`        | JSON document with every version of one pergam, its HTML and its metadata. See [Export and import](#export-and-import). **404** for an unknown id. |
| `POST`   | `/import`             | Body: an export document. Recreates the versions this server lacks, keeping id, version numbers and `created_at`. Returns `{id, imported, skipped, origin, view_url}`. **409** when a version already here differs. See [Export and import](#export-and-import). |
| `GET`    | `/feed.atom`          | Atom 1.0 feed of the 50 most recent publications; `?tag=<tag>`, `?author=<email>`, `?state=`. See [Feed](#feed). |
| `GET`    | `/static/frame-bridge.js` | The frame bridge script (`text/javascript`, cached five minutes). |
| `GET`    | `/static/mermaid/11.17.2/mermaid.min.js` | Mermaid 11.17.2, vendored (`text/javascript`, cached for good), next to its `LICENSE`. See [Mermaid](#mermaid). |
| `GET`    | `/healthz`            | Liveness probe.                                                      |
| `HEAD`   | any `GET` route       | Same status and headers as `GET`, without the body.                  |
| `DELETE` | `/{id}`               | **405 Method Not Allowed** — pergams are immutable.                  |

## Versioning model

Every pergam has a logical `id` (random 8-hex) and a monotonically increasing `version`. The public surface defaults to the latest version of an id:

- `POST /pergam` with no `id` → new pergam, `version=1`
- `POST /pergam` with an existing `id` → same id, `version + 1`
- `GET /{id}/view` → latest version
- `GET /{id}/v3/view` → version 3 specifically

Version numbers are assigned atomically: concurrent posts to the same `id` are serialized and receive consecutive versions, none of them fails. Each version's `created_at` is taken once its post holds the id, and is never earlier than the previous version's, so dates follow version order.

Caching: a version-pinned raw route (`/{id}/v{n}/raw`) never changes, so it is sent with `Cache-Control: public, max-age=31536000, immutable`. Routes derived from a pinned version can change with an upgrade, so they are cached for five minutes only (`public, max-age=300`): `/{id}/v{n}/content`, which adds the server's frame bridge and sandbox headers to those bytes, `/{id}/v{n}/text`, built by the text extractor, and `/{id}/diff/v{a}..v{b}`, built from the same extraction. Everything else (both view pages, which count the versions, latest `/{id}/raw` and `/{id}/text`, the index, JSON routes, the feed) is `no-store`.

Pergams cannot be deleted. To "supersede" a pergam, post a new version with the same id; to hide it, archive it with `PATCH /{id}/meta`.

### Version notes

`note` on `POST /pergam` is an optional one-line description of what this version changes (`"adds the costs section"`). It is stored with the version and, like the version, never changes. Whitespace is collapsed; a blank note is the same as none; a note that is not a string, holds a control character other than whitespace or is longer than 280 characters is **400** and publishes nothing. Notes are returned by `/{id}/versions` and `/api/pergams/{id}` (`null` for versions without one) and shown in the view page's version selector.

## Identity

By default the `author` of a version is whatever `POST /pergam` sends: pergam has no accounts and assumes the network is a trust boundary. Behind a reverse proxy that authenticates users (see [Deployment](#deployment)), set `TRUST_PROXY_USER=1` and the author comes from the proxy instead:

- every `POST /pergam` takes its author from the `X-Forwarded-User` header, trimmed; the body's `author` is ignored, may be left out, and the response's `author` is the header's value, so nobody can publish under someone else's name;
- a write without the header, or with a blank one, is **401** and writes nothing; one that carries the header twice is **400**, so a client's copy left in front of the proxy's never counts; one longer than 320 characters or holding a control character is **400**. The header is read as UTF-8;
- the same header names the writer of `PATCH /{id}/meta`, `PUT /{id}/collaborators` and `POST /import`, with the same **401** and **400**, and decides what each may change (see [Ownership](#ownership));
- reads (`GET`, `HEAD`) never need it.

Only turn it on when the proxy sets `X-Forwarded-User` on every request itself, replacing (not appending to) any copy a client sends, and pergam is reachable through that proxy alone: pergam trusts the header as it arrives. With the flag off (the default) the header is never read and nothing changes. A value of `TRUST_PROXY_USER` that is neither `1/true/yes/on` nor `0/false/no/off` stops the server at startup.

### Ownership

With `TRUST_PROXY_USER` on, each pergam has an **owner**, the author of its v1, which never changes, and a list of **collaborators** the owner manages:

| Write | Who may |
| ----- | ------- |
| `POST /pergam` with the `id` of an existing pergam (a new version) | the owner or a collaborator |
| `PATCH /{id}/meta` | the owner or a collaborator |
| `PUT /{id}/collaborators` | the owner |
| `POST /import` into an id this server already has | the owner or a collaborator, writing only versions whose `author` is themselves, the owner or a collaborator |
| `POST /import` that creates the id | anyone, writing only versions whose `author` is themselves; they become the owner |
| `POST /pergam` without `id`, or with an id this server does not have | anyone; they become the owner |

Anyone else gets **403** and nothing is written; the HTML body's message (never the status line, which always carries the standard reason) names the rule that failed (`eve@example.com is neither the owner of abc12345 (ana@example.com) nor one of its collaborators`, `v2 of abc12345 is by bo@example.com: an import writes only versions by the signed-in user (eve@example.com)`). The check runs under the same per-id lock as the write, so a collaborator removed meanwhile cannot slip a version in.

```bash
curl -sS https://pergam.example.com/abc12345/collaborators
# → {"id":"abc12345","owner":"ana@example.com","collaborators":[]}
curl -sS -X PUT https://pergam.example.com/abc12345/collaborators \
     -H 'Content-Type: application/json' -d '{"collaborators":["bo@example.com"]}'
# → {"id":"abc12345","owner":"ana@example.com","collaborators":["bo@example.com"]}
```

- `PUT` replaces the whole list; `[]` empties it. Each collaborator is a user as the proxy names them (compared exactly), trimmed, deduplicated and sorted, at most 320 characters without control characters, at most 100 per pergam; any other body, or a field other than `collaborators`, is **400**.
- An import checks the `author` of every version it writes, not only v1, so nobody can record versions under the name of someone with no say over the pergam. Versions this server already has are only compared, not written, so they are not checked. To promote a history with several authors, the owner on the target first adds the others as collaborators.
- The owner of a pergam published before the flag was turned on is whatever `author` its v1 was posted with; when that is not the name the proxy now sends, nobody can add versions to it until the owner name and the proxy agree. Plan the switch so authors already match the proxy's names (usually the email).
- With the flag off nothing is enforced: anyone may version, patch and import. The list cannot be edited then (`PUT` is **409**), since there is no owner to check and a list seeded before the flag is turned on would take effect with it; `GET` still answers.
- A build of this server from before that rule accepted `PUT` with the flag off. On an instance that ran one, turning the flag on should start from empty lists: `UPDATE pergam_meta SET collaborators = NULL;` first.

## View

`/{id}/view` and `/{id}/v{n}/view` are pergam's own page around the document:

- **A calm bar** with a breadcrumb (`Index / title`) and a version selector (`v3 of 5`: every version, newest first, with its date and [note](#version-notes)), then **Pin** (a toggle, see [Tags and metadata](#tags-and-metadata)), **History**, which shows or hides the side panel, and a **⋯** menu with everything else: **Compare with v{n-1}** (`/{id}/diff/v{n-1}..v{n}`; absent on v1), **Text** (`/{id}/v{n}/text`), **Print** (once the bridge answers), **Archive** and **Edit tags**, then **Raw HTML** (`/raw`), **Export (JSON)** (`/{id}/export`, downloaded) and **Open document** (`/content` alone). The type and the tags are not in the bar: they are in the panel's details.
- **A notice** on any version that is not the latest: when it was published, a link to the current one (`/{id}/view`) and one to what changed since (`/{id}/diff/v{n}..v{latest}`).
- **The document** in a full-height, sandboxed iframe loaded from `/{id}/v{n}/content`, always version-pinned (see [Sandbox](#sandbox)).
- **A side panel**: the history (every version with its note, author and how long ago it was published, the exact time on hover; the version shown is marked; **Compare with current** shows under it when it is an earlier one, and under the others on hover or when focus is in them, so it stays reachable with `Tab`), then the details: type (its badge), tags, author (the owner, the author of v1), [collaborators](#ownership), origin (the server it was [imported](#export-and-import) from, or "published here") and the id.

The panel is part of the page, so it works without script, below the document. With script (`/static/pages.js`, loaded in `<head>` like `theme.js`) it starts closed, so a first visit shows the document at full width, and the **History** button opens it beside the document. In a long history the panel scrolls to the version shown. On a wide screen (over 860 px) the reader's choice is remembered in `localStorage` under `pergam-history-panel` and applied before first paint from then on; on a narrow one the panel always starts closed and opens over the document, with a close button and `Esc`. The version selector and the **⋯** menu are `<details>` dropdowns, so they open with the keyboard and without script; with script `Esc` or a click elsewhere closes them, a click inside the document included, and a menu entry that acts in place (**Print**, **Archive**) closes its menu and gives focus back to **⋯**, so the status line it updates stays in view. Every control is reachable with `Tab`, has a visible focus ring and an accessible name; icons are inline SVG hidden from assistive technology. The bar keeps one row at every width: the title shortens, and the search is a collapsed field, its icon until it is focused (see [Search field](#pages-and-theme)). At 640 px and below the breadcrumb's `Index /` and the pergam name give way to the logo, **Pin** shows its star alone (still named "Pin") and the version selector shows `v3` (the rest of "v3 of 5" stays for screen readers, so its name starts with what it shows). The tag editor opens over the bar.

`/raw` returns the posted bytes untouched, as `text/plain`. `/content` returns them with two tags added right after a leading `<!doctype>` (or first when there is none, so a document that ends inside `<title>` or `<textarea>` cannot swallow them as text): a small inline script, the [storage stand-in](#sandbox), which runs before any script of the document, and `<script src="/static/frame-bridge.js" defer></script>`, the **frame bridge**, which talks to the view page with `postMessage` only, never reaching into the other side's DOM or location, so it works inside the sandbox. `/raw` and `/{id}/export` never carry it. Through it:

- **Anchors**: in-document links (`#section`) scroll the document, and the view URL follows them (`/{id}/view#section`, replaced, not a new history entry). A fragment on the view URL, at load or changed later, scrolls the document without adding history.
- **Links** that leave the document and have no `target` of their own (and no `<base target>`) open in the whole window, not inside the frame. Links with a `target`, downloads, and clicks with a modifier key keep their usual behaviour.
- **Title**: the tab shows the document's `<title>`, then the version.
- **Print**: the **Print** button, or Ctrl/⌘+P anywhere on the page, prints the whole document rather than the visible part of the frame. The button appears only once the bridge has answered.

What changes for a document inside the frame, compared with opening `/content` on its own:

- `window.location` is `/{id}/v{n}/content`, not the view URL, and `window.top !== window`. A fragment the reader follows still shows in the view URL through the bridge.
- Relative URLs resolve against `/{id}/v{n}/` (`href="notes"` is `/{id}/v{n}/notes`, a 404): link other pergams and assets by absolute path or full URL.
- The frame is the viewport: `100vh`, `position: fixed` and `sticky`, and `matchMedia` measure the frame, below the bar.
- The tab title comes from the document's `<title>`; without one it stays the pergam's title.
- The bridge needs script to run: a document whose own CSP forbids `/static/frame-bridge.js` keeps its links inside the frame and shows no print button.

The view page treats every message as untrusted data: it accepts only those from its own frame whose origin is its own or `null` (the sandbox's), only strings, at most 2,048 characters, and a fragment only when it starts with `#`; nothing is ever inserted as HTML. `pages.js` never listens to messages, so nothing the framed document sends reaches it. Unknown ids or versions are **404**.

### Sandbox

A published document never runs with the server's privileges, framed or opened on its own at `/content`:

- The iframe is `sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-modals allow-top-navigation-by-user-activation"`, without `allow-same-origin`, and every `/content` response repeats those tokens in the `sandbox` directive of its `Content-Security-Policy`. The document runs in an opaque origin (`origin` is `"null"`): no cookies or `localStorage` of this server, and every request it makes to the API is cross-origin.
- The rest of that CSP is the hosted viewer's: `default-src 'none'`; scripts inline, from this server and from `https://cdn.jsdelivr.net`; styles inline and from jsDelivr; images only `data:`; fonts `data:` and jsDelivr; `connect-src` only jsDelivr; `form-action 'none'`, `base-uri 'none'`; framed only by this server (`frame-ancestors`). "This server" is `'self'` plus its public origin by name (from `PUBLIC_URL`, or `PUBLIC_HOST`/`PUBLIC_PORT`), for browsers that do not match `'self'` from an opaque origin. `/content` also sends `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`; `/raw` is `text/plain` with `nosniff`, so it never renders.
- So a script inside a pergam cannot read the index, post a version, patch metadata, submit a form, beacon or embed a frame against the API: the browser blocks the request before it leaves.
- What still works: inline scripts and styles, [Mermaid](#mermaid) from `/static/` or jsDelivr, in-page anchors, links that leave the document (on a click), links that open a new tab (as an ordinary page outside the sandbox), printing, `data:` images.
- `localStorage` and `sessionStorage` throw in an opaque origin, so `/content` starts with a small inline script that, when they do, puts an in-memory stand-in with the same methods (`getItem`, `setItem`, `removeItem`, `clear`, `key`, `length`, and named properties) in their place. Documents that use them without a guard, for a theme toggle or a remembered tab, keep working, but nothing persists: the stand-in is empty again on every load.
- What a document loses: images, fonts, scripts and styles from anywhere else, `fetch` and XHR except to jsDelivr, forms, nested frames, cookies, storage that outlives the page, and navigating the whole window without a click. The skill keeps documents within these limits and [`POST /lint`](#lint) reports what would break.

`/content` is cached for five minutes (`public, max-age=300`), not for good like `/raw`, because the bridge tag and these headers are the server's and may change with an upgrade.

**Upgrading from a build without the sandbox:** earlier builds served `/{id}/v{n}/content` unsandboxed with a one-year `immutable` cache, so a browser that opened one may keep that copy and run it on the server's origin when it opens that URL directly again. Inside the view page the iframe's own `sandbox` attribute still applies to such a copy. The URLs stay the same; on an instance that served those builds to readers who may be targeted, ask them to clear the site's cached data, or put the instance on a new host name.

### Mermaid

The server ships Mermaid 11.17.2 (`dist/mermaid.min.js` of the npm package, unchanged, MIT, with its licence at `/static/mermaid/11.17.2/LICENSE`) and serves it at `/static/mermaid/11.17.2/mermaid.min.js`, cached for good since the path names the version. A pergam that loads it from there renders its diagrams with no external request, and the skill's template does so on a self-hosted server. Pergams published earlier load Mermaid from `https://cdn.jsdelivr.net`, which the sandbox's CSP still allows, as the hosted viewer does, so they keep rendering. Published documents name that versioned path, so an upgrade adds the new version next to it and keeps shipping the old one.

## Lint

`POST /lint` checks a document before it is published and writes nothing: no version, no metadata, no text. The body is `{"html": "…"}`; other fields are ignored, so the payload of a `POST /pergam` can be linted as is. A missing or blank `html` is **400**; the body must be `application/json` and at most 2 MB (**413** above that, whatever `PERGAM_MAX_BODY_MB` says), which keeps an anonymous lint cheap. Parsing takes many times the body in memory, so at most two lints run at once; another one arriving meanwhile is **429** at once and can be retried a moment later. Only the first 16,384 characters of each Mermaid block are read, enough for its diagram type; a block whose front matter, directives or comments run past them is not reported either way. It needs no `X-Forwarded-User`, even with `TRUST_PROXY_USER` on.

```bash
curl -sS -X POST http://localhost:1111/lint -H 'Content-Type: application/json' \
     -d '{"html":"<a href=\"#costs\">costs</a><img src=\"https://img.example.com/a.png\">"}'
# → {"findings": [
#      {"rule": "missing-anchor", "severity": "error", "line": 1, "column": 1, "message": "link to #costs has no element with id 'costs'"},
#      {"rule": "external-resource", "severity": "error", "line": 1, "column": 27, "message": "<img> loads https://img.example.com/a.png from another server; the sandbox blocks it"}
#    ], "errors": 2, "warnings": 0, "truncated": false}
```

Findings come in document order, at most 500 (`truncated` says when there were more); `line` and `column` (both from 1) point at the tag concerned. The checks are static, on the markup alone:

| Rule | Severity | Reported when |
| ---- | -------- | ------------- |
| `external-resource` | error | A tag loads from another server: `<script src>`, a loading `<link>` (stylesheet, icon, preload, …), `<img src/srcset>`, `<source>`, `<video>`/`<audio>`/`<track>`, `<iframe>`, `<embed>`, `<object>`, SVG `<image>`/`<use>`, or `url(…)`/`@import` in a `<style>` or `style` attribute. The [sandbox](#sandbox) blocks it. A `<script>` from this server's own origin (its public URL or the `Host` the lint request came to) is allowed, so it is not reported. |
| `external-resource` | warning | A `<script>` or `<link>` from `cdn.jsdelivr.net`, which the sandbox allows for older pergams; prefer the server's [Mermaid](#mermaid). |
| `missing-anchor` | error | `<a href="#name">` with no element whose `id` is `name` (or `<a name="name">`); `#` and `#top` are fine. |
| `mermaid-empty` | error | A `class="mermaid"` element with no diagram text (comments, directives and front matter do not count). |
| `mermaid-unknown-diagram` | error | Its first statement does not start with a diagram type the server's Mermaid detects (`flowchart`, `graph`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`, `gantt`, `pie`, `xychart-beta`, `architecture-beta`, … the same prefixes Mermaid's own detectors use). |

## Diff

`GET /{id}/diff/v2..v5` compares what a reader sees in two versions, without opening two tabs:

- Both versions go through the same extraction as [`/text`](#text), so markup-only changes (classes, wrappers, styles) do not show. Blank lines are ignored outside code blocks.
- The Markdown lines are compared with Python's `difflib` and grouped by section: every heading opens one, text before the first heading forms its own. A heading changed on its own counts as the same section renamed.
- Changed sections are open, with additions (`+`, green) and removals (`−`, red); a replaced line is shown next to its replacement with the changed words marked. Runs of unchanged lines are cut to two lines around each change. Unchanged sections are collapsed.
- The bar has a breadcrumb (`Index / title / Changes`), two version pickers, a **Sections** button, the summary (`+12 −6 lines · 3 of 5 sections changed`) and a button to open the newer version. Each picker is a dropdown of links to the same comparison with that side set to another version (`/{id}/diff/v{k}..v{b}` or `/{id}/diff/v{a}..v{k}`), so choosing a pair never means editing the URL and works without script.
- The bar keeps one row at every width, with the search collapsed to its icon until focused. At 900 px and below it holds the logo, `Changes`, the pickers, the search, **Sections** and the theme toggle; the summary moves above the sections, and the two version cards there open either version.
- **Sections** opens a list of every section with its additions and removals (`+2 −2`, `+12 new`, `−3 removed`, `unchanged`), each linking to it (`#sec-3`). It starts collapsed and is a `<details>` dropdown, so it works without script; with script choosing a section closes the list, opens that section when it is collapsed (also when it is followed again after the section was closed) and moves focus to it. Without sections to list (too large to compare, or no text on either side) there is no Sections button. The pickers read `v1` and are named "v1, older version" and "v3, newer version".
- The changes sit in one centred column at most 860 px wide.
- Above the sections, both versions with their [notes](#version-notes), each linking to its view page.

The range is `v{a}..v{b}`, the newer side on the right; `a` may be larger than `b` (read backwards) or equal (no differences). A range that is not `v{a}..v{b}` with positive numbers without leading zeros is **400**; an unknown id or version is **404**.

The work is bounded, so a diff answers in well under a second whatever the input:

- Text both versions share at the start and at the end is matched without cost. Lines (and words) that occur exactly once in each version anchor the comparison, as in patience diff, so a large document with scattered edits is compared gap by gap. Within a gap, lines or words that repeat more than 8 times on the newer side never anchor a match (they still extend one).
- Matching is metered. A replaced line whose word comparison would overrun its share, or any once the request has spent its total, is marked as a whole line instead of word by word.
- A version over 2 MB of HTML or 20,000 lines of text, or line matching that overruns its budget, gets a **too large to diff** page (still `200`, same bar and pickers, no Sections button) with links to both versions' `/text`.

## Export and import

`GET /{id}/export` returns one pergam whole, as one JSON document, to recreate it on another server:

```json
{
  "format_version": 1,
  "id": "abc12345",
  "server": "http://localhost:1111",
  "meta": {"tags": ["q3"], "archived": false, "pinned": false, "pinned_at": null},
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
  - for an id new to this server, its `tags`, `archived` and `pinned` from `meta`, and `origin`: the document's `server` and `id`. A pin follows the [pinning](#pinning) rules without failing the import: an archived pergam, or one past this server's cap, arrives unpinned, and the response says why in `pin_refused`. `meta.pinned_at` (optional, `null` or ISO 8601 with a UTC offset, in the same range as `created_at`) keeps the card's place, but never later than the import itself, so a clock ahead on the other server cannot put a card above pins made here; an export without it, from an older server, is pinned as of the import. For an id it already has, `meta.tags` are merged into its tags as with `POST /pergam`, and `archived`, `pinned` and `origin` stay as they are.
- The response is **201** when anything was written, **200** when every version was skipped: `{"id": "abc12345", "imported": [4, 5], "skipped": [1, 2, 3], "origin": {"url": "http://localhost:1111", "id": "abc12345"}, "view_url": "…"}`, plus `pin_refused` (the reason, as a `PATCH` would give it) when the document asked for a pin this server did not keep.
- Validation follows `POST /pergam` for `type` (trimmed, lowercased, one of this server's [types](#pergam-types)), `author` (trimmed, non-empty) and `note` (see [Version notes](#version-notes)), and is stricter elsewhere, since an export always carries every field:
  - `title` and `html` must be non-empty strings and are stored exactly as sent: `title` is not trimmed and has no `"Pergam"` default;
  - `title`, `html` and `author` must not contain NUL, and the `author` of a version the import writes no other control character either (as on `POST /pergam`); versions already here are only compared, so an export whose older authors predate that rule still imports;
  - `format_version` must be 1 and the id 6 to 16 lowercase hex characters;
  - `version`s must be distinct integers from 1 to 2147483647, at most 10,000 of them per import (a longer history goes in several imports, each carrying the next versions);
  - `created_at` must be ISO 8601 with a UTC offset, between 1970-01-01 and five minutes past the server's clock (the allowed clock skew between servers); it is stored in UTC. Dates need not grow with the version number: older histories, or ones across a clock step, can date a version before the previous one, and they import as they are;
  - `server` (optional) must be an absolute `http(s)` URL with a valid host and port, without credentials, query or fragment.
  Anything else is **400** and writes nothing. `bytes` is recomputed from the HTML and unknown fields are ignored.
- The body may be as large as `PERGAM_MAX_BODY_MB` (see [Configuration](#configuration)).
- With `TRUST_PROXY_USER` on, an import is a write like any other: it needs `X-Forwarded-User` and follows the [ownership](#ownership) rules (**403** otherwise, nothing written).

`origin` is shown in the view page's bar ("imported from localhost:1111/abc12345", linking to the original) and returned by `GET /api/pergams/{id}` (`null` when the pergam was not imported). It cannot be edited. The skill's [promote helper](skills/post-pergam/SKILL.md#promoting-a-pergam-to-another-server-flow-b), `skills/post-pergam/pergam_promote.py` (stdlib only), combines both in one command between two named servers and uploads only the versions the target lacks: `python3 skills/post-pergam/pergam_promote.py abc12345 --from local --to team`.

## Tags and metadata

Tags, `archived` and `pinned` belong to the logical `id`, not to a version, and live in `pergam_meta`. Changing them never creates a version. An id without a metadata row reads as no tags, not archived, not pinned.

- Tags are free-form and normalised: trimmed, lowercased, runs of whitespace, `_` and `-` collapsed to one `-`, empties dropped, duplicates removed, stored sorted. `" Q3  Review_notes "` → `q3-review-notes`.
- At most 20 tags per id, each at most 40 characters after normalisation, with no control characters (tabs and newlines are separators and become `-`); otherwise the request gets **400** and nothing is written.
- `POST /pergam` with `tags` **merges** them into the id's existing tags; posting without `tags` (or `"tags": null`) leaves them as they are.
- A new version never changes `archived` or `pinned`: posting to an archived id keeps it archived (and out of the index).
- `PATCH /{id}/meta` edits metadata of an id that already has versions. Fields it omits keep their value; `tags` **replaces** the whole set (`[]` clears it); `archived` and `pinned` must be JSON booleans. Unknown fields, an empty object or a wrong type are **400**, and so is `{"archived": true, "pinned": true}`. The response is always the resulting state.

### Pinning

Pinned pergams are the cards above the index list, so an instance keeps few of them:

- **Cap**: at most `PERGAM_MAX_PINNED` pergams (default 12) are pinned at once; `0` turns pinning off. Pinning one more is **409**, with the reason in the body (the status line is always the standard `Conflict`), and the whole `PATCH` changes nothing, tags included. Re-sending `pinned: true` for a pinned pergam, unpinning and every other change always work. Every new pin is counted under one instance-wide lock, so concurrent pins never pass the cap. An instance that already holds more pins than the cap (from before it, or a lowered cap) keeps them; it cannot pin more until it is back under.
- **Archiving unpins**: archiving a pinned pergam also unpins it, in the same update, keeping its tags; unarchiving does not pin it again. Pinning an archived pergam is **409** (`unarchive it first`). A row left both archived and pinned by an earlier build reads as not pinned everywhere (`/meta`, the API, the index order and cursor, export), is never a card and never counts toward the cap; the next write to it unpins it.
- **Order**: the cards show the most recently pinned first, from `pinned_at`, set when a pergam goes from unpinned to pinned, cleared when it is unpinned, and untouched when a pinned pergam is patched or gets a new version. Pergams pinned before `pinned_at` existed have none and follow the dated ones, newest version first. The list below and the API's order and pages do not change with it.
- `GET /api/pergams` reports `stats.pinned` (the pins that count) and `stats.max_pinned`, and the index shows "n of max pinned" next to the cards.

```bash
curl -sS -X PATCH http://localhost:1111/abc12345/meta \
  -H 'Content-Type: application/json' \
  -d '{"tags":["billing","q3"],"pinned":true}'
# → {"id":"abc12345","tags":["billing","q3"],"archived":false,"pinned":true}
```

**From the view page.** **Pin** is a toggle button in the bar and **Archive** a toggle in the **⋯** menu (`aria-pressed` shows the state); **Edit tags**, also in the menu, turns the tags into a field over the bar (comma-separated; Enter saves, Esc cancels), filled from a fresh `GET /{id}/meta` so it starts from the current tags. They call `PATCH /{id}/meta` with `fetch` from `/static/pages.js` and show what the server answered: the new state, or why nothing changed (**403**: not the owner or a collaborator; **401**: the proxy sent no user; **409** on Pin: the server's [pinned limit](#tags-and-metadata) is reached or the pergam is archived, with the server's own reason (read as text from the error body), or a plain one when it gives none; **400**: a change the server refused, naming the tag limits when tags were sent). Both buttons always show the state the server answered with, so archiving a pinned pergam, which unpins it, shows it unpinned and says "Archived and unpinned". While a pergam is archived, **Pin** is unavailable (`aria-disabled`, with a description saying to unarchive it first). The answer goes into a `role="status"` region that is always on the page, emptied before each request, so screen readers announce every outcome, the same one twice included. Without script they are absent. When identity is trusted (`TRUST_PROXY_USER`) the page offers them only to the owner and the collaborators, and to a request whose user the server cannot tell (it then gets the 401); with the flag off anyone who can reach the server may already `PATCH`, so everyone gets them.

They cannot be driven by anything but the reader: the published document's script runs sandboxed in an opaque origin, its CSP forbids any connection to the server, the view page acts on nothing it sends beyond a fragment, a title and a print request, and no script on the page listens for messages. `PATCH /{id}/meta` takes only `Content-Type: application/json`, which an HTML form on another site cannot send, and the server answers no CORS preflight, so another site's script cannot send it either. Nor can another site frame the page and trick a click onto them: every page refuses to be framed by any origin but its own (see [Pages and theme](#pages-and-theme)).

## Index

`GET /` lists the latest version of every pergam, in one centred column.

- **Layout**: the heading ("All pergams", "Archived pergams" when only archived ones are listed), one toolbar row (the Filters button and the summary of active filters on the left, the sort menu on the right), the pinned pergams as a row of cards, then the list. A row is one line: title, type, the author's avatar and how long ago the latest version was published, at every width (on a phone a long type name is cut). The title link covers the whole row; the type and the author stay links that add that value to the filters. The feed and the JSON of the current list are links in the footer; the top bar keeps the search field and the theme toggle. Instance numbers (pergams, authors, versions, payload) are in the API's `stats`, not on the page.
- **Sort menu**: Recent or Title A–Z (links, so it works without script), and a "Show summaries" switch that adds a second line to each row (the summary, or on a search the highlighted snippet, and the id's tags) plus the version and the author's name. The choice is remembered in `localStorage` under `pergam-summaries` and applied before first paint; without script the switch is hidden and rows stay on one line. A search snippet always shows, since it says why the row matched.
- **Filters** (see [Filters](#filters)): `type`, `author` and `tag` may repeat; within one a pergam matches any of the values, and different filters, the status and `q` (title or text, see [Search](#search)) combine with AND. `tag` is normalised like stored tags, so `?tag=Q3` matches `q3`.
- **Status**: `?state=active` (the default, also when the parameter is left out or blank) lists active pergams, `state=archived` only archived ones, `state=all` both, archived rows then carrying a muted "Archived" badge. Any other value is **400**. The old `?archived=1` (`true`) meant both: `/` answers it with a **301** to the same URL with `state=all` in its place (an explicit `state` wins, `archived=0` just goes away, and of several `archived` only the first non-blank one counts, as before), so old links keep their meaning. Archived pergams stay reachable by URL whatever the status. Archiving belongs to the id, so a new version of an archived pergam stays archived and hidden, and the `201` does not say so; un-archive it with `PATCH /{id}/meta` `{"archived": false}`.
- **Order**: `?sort=recent` (the default): pinned first, then by the latest version's `created_at` (newest first), then by id. `?sort=title`: by title, case-insensitive (Postgres `lower(title)` in the database's collation), then by id. Both orders are total, so paging is stable. Any other `sort` is **400**. Filter, sort and clear links keep the sort and start again from the first page.
- **Pinned cards**: on the first page, the pinned pergams that match the filters and the search (archived ones never) show as one row of cards above the list, most recently pinned first (see [Pinning](#pinning)), with their summary and "n of max pinned" next to the heading; the list leaves them out on every page, so each pergam shows once. The row scrolls sideways natively, with snap points, by touch, trackpad or Tab (a focused card scrolls into view); script adds previous and next buttons that move it by one view of cards and are disabled at the ends, never advancing on their own. Cards keep a fixed width, so the next one peeks in at the edge: three or more show at 1280 px, one and a peek at 360 px. It is a plain labelled list, not a carousel widget, and scrolls instantly under `prefers-reduced-motion`. At most 100 cards are shown; pins past those, possible only on an instance over the cap, stay in the list, marked `pinned`.
- **Date groups**: sorted by recent, the list is grouped under Today, This week, This month and Earlier, from the latest version's `created_at` in UTC: the same day, the same ISO week (Monday first), the same calendar month, anything before. Sorted by title, it is one list.
- **Summary**: the start of the latest version's extracted text (see [Search](#search)): lines that only repeat the title are skipped, the rest joined into one line and cut at the first sentence end at least 20 characters in (so `Dr.` or `Version 2.` near the start do not end it), or at a word before 160 characters with `…`. It is plain text, escaped like everything else on the page. A version whose text is not extracted yet, or is empty, has no summary rather than a wrong one.
- **Pages** hold 100 pergams. "Load more" carries an opaque `cursor` (the sort key of the last row) plus the active filters and sort; with script it appends the next page in place (and moves the focus to its first row), without it it is a link to that page, which then offers "← first page". While the data does not change, following the links visits every matching pergam exactly once, in either order, under any status and filters. A pergam that gets a new version or is pinned, unpinned, archived or retagged mid-crawl changes its sort key or match, so it can move to a page already seen (skipped) or one still ahead (seen twice). A cursor is tied to its order: a malformed one, or one issued by the other order, is **400**.
- **Empty results** say what was looked for and offer a way out: "No pergams match these filters." (or "… “roadmap” with these filters.", or "Nothing is archived yet.") with "Clear filters", which keeps the search and the sort, and "Edit filters"; a search alone offers "Clear search"; an empty instance says how to publish. The summary of active filters stays above.

## Filters

The Filters button opens one dialog with four sections: Type, Author, Tags (each "Any of": a pergam matches any selected value) and Status (Active, Archived, All). The button shows how many filters are active: one per selected value, plus one for a status other than Active; the search and the sort never count, and its accessible name says so ("Filters, 3 active").

- **Summary**: with any filter active, the toolbar shows "23 of 312" (the matches, of every pergam in the status: the active ones, the archived ones, or all of them), one chip per value ("Type: plan", "Tag: infra", "Author: ana", "Archived only", "Active and archived"), whose × is a link to the same list without that value, and "Clear all", which drops every filter and keeps the search and the sort. Past five chips the first four show and "+N more" opens the dialog. On a plain search the count shows alone. Below 600 px the chips wrap under the toolbar.
- **Dialog**: the page carries it empty; its form comes from `/filters/form` (the same partial as the `/filters` page, for the page's query) the first time it opens, while the button is marked busy, so the index stays small however many authors and tags there are; a "Load more" request (sent with `X-Pergam-Load-More: 1`) gets the page without it. Only the form of an HTML answer is taken; if the request fails or brings no form, the button follows its link to `/filters`. Choices are a draft until the main button, which says what it will show ("Show 23 pergams"), is pressed; it then navigates once, replacing the dialog's history entry, to the URL the form describes, and the new page moves the focus to the summary and announces the number. Each change asks [`GET /facets`](#json-api) for the draft (after 150 ms) and updates every count and the button in place, keeping the old number while it loads; with no result the button is disabled ("No matches") with "No pergams match. Try removing a filter."; if the request fails it says "Show results". Escape, the close button, a click on the backdrop and the browser's back button all close it and discard the draft (opening it pushed a history entry); the first Escape in a find field only clears that field, and Enter there never applies. Reloading with the dialog open opens it again, so the back button still closes it. Each section's "Clear" and the footer's "Clear all" reset the draft without touching the search.
- **Values**: types in the server's configured order, authors and tags most used first; selected values come first when the dialog opens, and nothing moves while it is open. Each value shows what selecting it would list (see Counts); a value with no result is dimmed, moved to the end, announced as unavailable and cannot be ticked, unless it is already selected. Authors show their eight most used and tags their fifteen before "Show all", which expands in place (all of them when that would hide three or fewer), and a find field appears past twelve authors or fifteen tags. A section with nothing to choose is left out unless a value of it is selected: Type and Author when every pergam has the same one, Tags when no pergam has a tag.
- **Counts** are facets over every pergam the status allows, not only the current page: each facet applies `q`, the status and every other filter, but not its own values, so each value shows how many pergams selecting it alone would list, and the other values of a filter stay listed. With `?type=plan&tag=ops` the type counts are the `ops` pergams of each type, the tag counts the `plan` pergams with each tag. The Status counts apply `q` and every filter. Values come from every pergam the server has, whatever its status, so a value with no match shows at zero rather than disappearing. They take a fixed number of queries, however many values are selected.
- **Keyboard**: the button opens the dialog with the focus on the first selected value, or the first type; Tab and Space change every section (arrow keys move within Status), Enter applies, Escape cancels and returns the focus to the button.
- **Phone**: at 600 px and below the dialog fills the screen and slides up, with 40 px chips, one column of authors and 44 px buttons.
- **Without script** the Filters button (and "Edit filters", "+N more") is a link to `/filters` with the same query: a page with the same form, every value shown, counts for the current URL, "Show results" (a GET form to `/`) and "Cancel" back to the list. The dialog, the live counts, folding, find fields and dimmed values need script; the page never marks a value unavailable, since nothing there could stop it being ticked.

URLs list the keys in the order the form submits them and the values of each key sorted by code point, so one selection has one URL whether it comes from the dialog, a chip or a link: `/?type=plan&type=reporte&author=ana%40example.com&tag=infra&state=archived&q=roadmap&sort=title`. Every older single-value URL works unchanged. A filter takes at most 50 values (**400** above that); values are bound as SQL parameters, never spliced into a query.

## Pages and theme

The index, view and diff pages share one stylesheet, `/static/pergam.css`, and two scripts, `/static/theme.js` and `/static/shortcuts.js`; the index adds `/static/index.js` (the sort menu and its "Show summaries" switch, "Load more", the pinned row's buttons) and `/static/filters.js` (the filter dialog), and the view and diff pages add `/static/pages.js`, their progressive enhancements (the history panel, dropdowns, the pin, archive and tag controls). Every script they add is a same-origin static file; the only inline script is the view page's frame bridge listener (see [View](#view)), and `/content` inlines the [storage stand-in](#sandbox). Pages link them as `…?v=<content digest>` (`layout.HEAD_ASSETS`, `static_files.hashed_url`) and that exact URL is served `public, max-age=31536000, immutable`; pages themselves are never cached, so an upgrade changes the URLs and readers never pair new markup with an old stylesheet. The plain paths stay served, with `max-age=300`. They use system fonts only and load nothing from elsewhere. The colour tokens use `light-dark()` and `color-mix()`, so the floor is a browser from 2024: Chrome 123, Firefox 120, Safari 17.5; older ones show the pages without colours. The published document inside the view page keeps its own look.

**Framing.** Every page the server renders (index, view, diff, and errors on their routes, page or not) is sent with `Content-Security-Policy: frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN`, so only this server may frame it and another site cannot overlay its controls (clickjacking). `/content` keeps its own policy (see [Sandbox](#sandbox)), and JSON, feed, text, static and write responses carry neither header.

**Themes.** Every colour is a token holding a `light-dark(light, dark)` pair, resolved by the element's colour scheme: the system's (`prefers-color-scheme`) unless the reader has chosen. The top bar's toggle cycles **system, light, dark** and back: system is no stored value and no `data-theme`; light and dark are stored in `localStorage` under `pergam-theme` and set `<html data-theme>`. `theme.js` runs synchronously in `<head>`, before the body is parsed, so a remembered choice is on `<html>` before first paint, and other open tabs follow a change. The toggle's icon (one inline SVG per mode, from `pergam.icons`, the current one shown by `data-theme-mode`) and its `aria-label` name the current mode and the next; the search field's icon is an inline SVG too. Every icon a page draws (top bar, the index's pinned row, sort menu and filter dialog, the view and diff bars) comes from `pergam.icons`, inline SVG in `currentColor` and hidden from assistive technology, never a text glyph; every page loads each script once. Without script the system scheme applies and the toggle stays hidden. Text tokens meet WCAG AA (4.5:1) on `--bg`, `--surface` and `--raised` in both themes, and on the diff and highlight backgrounds, which tests check.

**Tokens** (defined once, on `:root`, in `pergam.css`):

| Group | Names |
| ----- | ----- |
| Surfaces | `--bg` page, `--surface` cards and bars, `--raised` hover and fields, `--border`, `--border-strong`, `--doc-bg` (the frame behind a published document, white in both themes), `--backdrop` (behind a modal dialog) |
| Text | `--text`, `--soft`, `--muted`, `--accent`, `--accent-soft` (tint), `--on-accent` (text on an `--accent` fill) |
| Status | `--ok`, `--danger`, `--warn-bg` with `--warn-text`, `--add-bg`/`--add-mark`, `--del-bg`/`--del-mark`, `--mark-bg` |
| Type, space, shape | `--sans`, `--mono`, `--radius`, `--radius-sm`, `--space-1` to `--space-5`, `--shadow` |

**Components** (shared classes; a page uses them as they are and adds layout in its own CSS):

| Class | What it is |
| ----- | ---------- |
| `.topbar`, `.logo`, `.topbar__host`, `.topbar__actions`, `.topbar__nav` | the top bar, see below; `.topbar__nav a[aria-current]` marks the current page |
| `.btn`, `.btn--primary`, `.icon-btn` | buttons (`<button>` or `<a>`); icon buttons are 32 px squares and need an `aria-label` |
| `.badge` | a pergam type; set `style="--type-rgb:R,G,B"` from `taxonomy.type_rgb(type)` (the colour still derives from the type name) and the stylesheet darkens or lightens it per theme; use `<a class="badge">` for a filter link |
| `.tag` | a tag pill, `<a>` or `<span>` |
| `.avatar` | author initials on a hue: `style="--h:0-359"`, see `index_page.avatar` |
| `.ver` | a version chip (`v3`), monospace accent |
| `.crumbs`, `.crumbs__sep` | a breadcrumb: muted links separated by `/`, the current page last (a heading) |
| `.dropdown`, `.dropdown__toggle`, `.dropdown__menu`, `.dropdown__menu--end` | a `<details>` dropdown: the `<summary>` is the toggle, the menu opens below it (right-aligned with `--end`, across the screen at phone width) |
| `.type-dot` | a small swatch of a type's colour (`style="--type-rgb:R,G,B"`, as on `.badge`) |
| `.input`, `.kbd`, `.card`, `.plus`, `.minus`, `.muted`, `.sr-only` | search field, key cap, bordered surface, diff counts, secondary text, text for screen readers only; `.plus` and `.minus` are the diff's added and removed counts and live in the shared sheet only so the diff colours stay in one place |

**The top bar** is rendered in one place, `pergam.layout.top_bar(host=None, middle="", actions="", search=None)`: the logo linking to `/`, the optional server `host` (plain text, escaped there), then `middle` (grows with the bar), then the search field, then `actions` and the theme toggle at the right edge. `middle` and `actions` are HTML the caller has escaped. A page puts `layout.HEAD_ASSETS` (colour-scheme meta, `theme.js`, the stylesheet link, `shortcuts.js`) in `<head>` and the bar first in `<body>`; its template fills `${head_assets}` and `${top_bar}`.

**Search field.** Every page's top bar carries the same search form (`.topbar__search`, `role="search"`, labelled "Search title and text"), which submits `q` to the index, so a search from a view or diff page lands on the index with the query applied. `search` is a `layout.SearchBox(query, keep, compact=True, collapsed=False)`: the index passes the current `q` and the filters a new search keeps (`type`, `author`, `tag`, `archived`, `sort`, as hidden fields) and `compact=False`; the view and diff pages pass `collapsed=True`, a field that is an icon at every width because their bar is already full; other pages pass nothing and get an empty compact field. Below 640 px the index's field takes its own row of the bar, while a compact one shrinks to its search icon, so the bar around a document keeps its rows: the icon is a `<label>` for the field, so a tap (or `/`, or Tab) focuses it with or without script, and the focused field opens over the bar until it loses focus.

**Shortcuts** (`shortcuts.js`, one listener on the document): `/` focuses the search field and selects its text; `Esc` in the field clears it, and a second `Esc` leaves it. They never fire while the focus is in an input, a text area, a select or editable content, nor with Ctrl, ⌘ or Alt held, and the published document's frame keeps its own keys.

**Page-local helpers.** The view and diff pages still define a few classes in their own CSS, around the shared top bar's `middle` and `actions`: `.bar__title` (the page's `<h1>`, both pages), `.versions__*` (the rows of a version dropdown, both), `.bar__summary`, `.pickers` and `.toc__*` (diff), and `.bar__links`, `.bar__origin`, `.bar__print`, `.btn--pin`, `.dropdown__item`, `.meta-controls` and `.tag-form` (view). Icons come from `pergam.icons` as inline SVG (`.icon`, shared). They are layout around shared components, not shared components, and a page that needs them copies them for now; the next step is to promote the ones a third page uses.

**Inline styles.** Badge and avatar hues are inline `style="--type-rgb:…"` and `style="--h:…"` custom properties. If server pages ever get a Content-Security-Policy, it needs `style-src 'unsafe-inline'` (or these become classes); the pages' own CSS is inline too.

**Page stylesheets** (`templates/*.css`, inlined in their page) hold layout and may only refer to tokens with `var(--name)`: no colour literal, no font name. A test fails on either, and on any inline `style` that is not `--h` or `--type-rgb`.

## Errors in a browser

A request that prefers `text/html` (a browser: `Accept: text/html,…`) and is a `GET` or `HEAD` on a page route gets the error as a page in the product's look: the status, a plain explanation, the server's message (escaped) and a way out (the index, and a search box). Everything else keeps the standard library's error response unchanged: `curl` and other clients (`Accept: */*` or none), `fetch`, every non-`GET`, and every route that does not serve a page whatever it accepts: JSON (`/api/…`, `/{id}/versions`, `/meta`, `/collaborators`, `/export`), the feed, `/healthz`, and the plain text and Markdown of a pergam (`/{id}/raw`, `/{id}/text` and their `/v{n}/` forms). Status line, headers and cache rules are the same either way (no `Cache-Control`, `Connection: close`, and the [framing](#pages-and-theme) headers on page routes), and the status line never carries user-controlled text. Because the body of a `GET` or `HEAD` error on a page route depends on `Accept`, those responses, page or not, carry `Vary: Accept`; errors of the other routes do not.

## JSON API

`GET /api/pergams` is the index as JSON. It takes the same query parameters as `/` (`type`, `author` and `tag`, each repeatable, `q`, `state`, `sort`, `cursor`; `archived=1` reads as `state=all` here, without a redirect) and comes from the same query, so for the same parameters it lists the pergams the HTML index lists, 100 per page. In the `recent` order the page shows them in the same order (pinned cards first); in the `title` order the JSON keeps pinned pergams in their title position, where the page lifts them into cards.

```json
{
  "pergams": [
    {"id": "abc12345", "version": 2, "title": "Release notes", "type": "plan", "author": "you@example.com",
     "created_at": "2025-03-04T05:06:07+00:00", "bytes": 5120, "versions": 2, "tags": ["q3"],
     "archived": false, "pinned": false, "pinned_at": null, "view_url": "http://localhost:1111/abc12345/view",
     "text_url": "http://localhost:1111/abc12345/text", "snippet": null,
     "summary": "What ships in this release and what moves to the next one."}
  ],
  "sort": "recent",
  "state": "active",
  "matching": 1,
  "next_cursor": null,
  "archived_count": 0,
  "counts": {"types": [{"type": "plan", "count": 1}], "authors": [{"author": "you@example.com", "count": 1}],
             "tags": [{"tag": "q3", "count": 1}]},
  "stats": {"pergams": 1, "authors": 1, "versions": 2, "bytes": 9000, "new_pergams_7d": 1, "new_versions_7d": 2,
            "pinned": 0, "max_pinned": 12}
}
```

- Each row describes the latest version; `versions` is how many the id has. `snippet` is the matching text fragment (plain text) when `q` matched the body, else `null`. `summary` is the row's one-line summary (plain text, see [Index](#index)); it is left out, not `null`, while the version's text is not extracted yet or when it is empty.
- `pinned_at` is when the pergam was pinned (ISO 8601), `null` when it is not pinned or was pinned before the server recorded it.
- `sort` and `state` are the order and the status used (any other `?sort=` or `?state=` is **400**).
- `matching` counts every pergam that matches the filters, across pages. `next_cursor` is `null` on the last page; otherwise pass it back as `?cursor=` with the same filters and sort. A malformed cursor, or one from the other sort, is **400**.
- `counts` and `archived_count` are the index's facet counts: each applies `q`, the status and every other filter but not its own values (see [Filters](#filters)), and lists only values with at least one match; `archived_count` is how many archived pergams match the rest. `stats` covers the whole instance.

`GET /facets` takes the same parameters (a `cursor`, if sent, must be valid, **400** otherwise, but changes nothing) and returns only the counts, for the filter dialog:

```json
{"total": 23,
 "facets": {"type": [{"value": "plan", "count": 12}, {"value": "runbook", "count": 0}],
            "author": [{"value": "ana@example.com", "count": 23}],
            "tag": [{"value": "infra", "count": 18}],
            "state": {"active": 23, "archived": 6, "all": 29}}}
```

`total` is what the parameters list; every value any pergam has is listed, zero counts included, with the selected ones even when nothing has them; types follow the server's configured order, then any other stored type, and authors and tags go from most to least used. It is `no-store` and answers errors as JSON routes do.

`GET /api/pergams/{id}` returns one pergam: the latest version's `version`, `title`, `type`, `author`, `created_at`, `bytes`, `note`, `view_url` and `text_url`, the id's `tags`, `archived`, `pinned`, `pinned_at` and `origin` (`{url, id}` of the server it was [imported](#export-and-import) from, else `null`), and `versions`, newest first, each with `version`, `title`, `type`, `author`, `bytes`, `created_at`, `note` and its pinned `view_url` and `text_url` (`/{id}/v{n}/view`, `/{id}/v{n}/text`). An unknown id is **404**.

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
- **Filters**: `?tag=` (normalised like stored tags) keeps entries whose id carries that tag; `?author=` keeps versions posted by that author; both combine with AND. `?state=` works as on the index: archived pergams are left out by default, `state=archived` keeps only them and `state=all` both; it is part of the feed's own URL.
- The feed's `id` and `rel="self"` link are its own URL including the filters, so each filtered feed is a separate subscription. URLs are built from `PUBLIC_URL` (or `PUBLIC_HOST`/`PUBLIC_PORT`), like `view_url`.

## Search

`?q=` matches a pergam when its latest version's title contains `q` (case-insensitive substring, as before) **or** its title or text matches `q` as a full-text query.

- **Text** is the visible text of the HTML, extracted at publish time by the same parse that serves `GET /{id}/text`, stored as plain lines (the same blocks without Markdown syntax, so snippets show no `#` or `|`). The contents of `script`, `style`, `template`, `noscript` and `head` are never indexed.
- **Query syntax** is Postgres' web-search syntax: words are ANDed, `"quoted phrase"` matches the words in order, `or` and `-excluded` work. Matching is language-neutral (`simple` configuration): case-insensitive, no stemming, so `region` does not match `regions`, and no accent folding, so `informacion` does not match `información`. Title words weigh more than body words.
- **Snippet**: when the text matched, the row shows the matching fragment with the hits highlighted, in place of its summary. A title-only match shows the summary.
- **Filters on a search**: the filter dialog counts within the results, each facet without its own values (see [Filters](#filters)), so the type counts add up to the results until a type is chosen, and another type can still be picked; submitting the search keeps the active filters. The search field sits in every page's top bar (`/` focuses it, see [Pages and theme](#pages-and-theme)).
- `q` is at most 200 characters (after trimming); longer is **400**, on `/` and on `/api/pergams`.
- **Order and paging** are the same as without `q`, in either sort, so the cursor works unchanged.
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
| `PERGAM_MAX_PINNED` | `12`                 | Most pergams pinned at once; `0` turns pinning off (see [Pinning](#pinning)). |
| `PERGAM_MAX_BODY_MB` | `256`                | Largest request body, in MB, for `POST` and `PATCH`. A larger declared `Content-Length` is **413** and nothing is read; a `Content-Length` that is not a number is **400**. The default leaves room for a multi-version `POST /import`. |
| `TRUST_PROXY_USER`  | off                    | `1` takes the author of every write from `X-Forwarded-User`, set by an authenticating reverse proxy (see [Identity](#identity)). |
| `POSTGRES_PASSWORD` | `pergam`               | Password for the bundled `db` service.       |
| `PERGAM_PORT`       | `1111`                 | Host-side published port (compose only).     |
| `PERGAM_IMAGE`      | `pergam:latest`        | Image the compose service runs (compose only). Unset builds it from the checkout; set it to run a [published image](#published-image). |
| `MIGRATIONS_DIR`    | `/app/db/migrations`   | Directory of `NNN_*.sql` migrations (the image sets it; from a checkout it defaults to `db/migrations`). |

See `.env.example` for the full set. The server refuses to start when `PUBLIC_URL` is not an absolute `http(s)` URL, carries credentials, a query or fragment marker (`?`, `#`) or a non-numeric port, when `PUBLIC_PORT` is not a number, when `PERGAM_MAX_BODY_MB` is not a positive integer, when `PERGAM_MAX_PINNED` is not a whole number of 0 or more in plain ASCII digits (no sign), or when `TRUST_PROXY_USER` is not a recognised on/off value.

## Schema migrations

The schema lives in numbered files, `db/migrations/NNN_description.sql`, shipped inside the image. On every start the server applies the pending ones before it listens:

- Applied migrations are recorded in `schema_migrations (version, name, applied_at)`.
- Each migration runs in its own transaction: a failure rolls it back completely, the server logs `migrations failed` and exits non-zero.
- A Postgres advisory lock serializes servers that start at the same time against one database.

Migrations are additive and safe on a populated database: they never drop or rewrite existing rows, and any backfill is idempotent. `001_initial_schema.sql` is the schema the bundled compose used to load through `docker-entrypoint-initdb.d`. When `pergams` already exists it runs no DDL at all, so existing instances upgrade in place with every row, index and view untouched and only `schema_migrations` gains a row. `002_pergam_meta.sql` adds the `pergam_meta (id, tags, archived, pinned)` table and writes no rows. `003_pergam_text.sql` adds `pergam_text (id, version, body, document)`: the extracted text and the `tsvector` (with a GIN index) of every version, filled on publish and, for older versions, by the server's background backfill (see [Search](#search)); `pergams` keeps its schema and rows. `004_pergam_text_extractor.sql` adds `pergam_text.extractor` (existing rows read as version 1) so the backfill can re-extract text written by an older extractor; it rewrites no rows. `005_pergam_note.sql` adds `pergam_note (id, version, note)`, one row per version posted with a note; it writes no rows, so versions published before it have no note. `006_pergam_origin.sql` adds `pergam_meta.origin_url` and `origin_id` (both nullable, existing rows read as no origin); it rewrites no rows. `007_pergam_collaborators.sql` adds `pergam_meta.collaborators` (nullable, existing rows read as no collaborators); it rewrites no rows. `008_pergam_pinned_at.sql` adds `pergam_meta.pinned_at` (nullable, existing pins read as pinned at an unknown time); it rewrites no rows, not even ones left both archived and pinned. To add a change, create the next number and restart the server.

### Database privileges

The `DATABASE_URL` role needs:

| Database state | Privileges |
| -------------- | ---------- |
| Fresh (no `pergams` yet) | `CREATE` on the schema: the role creates and owns the tables. The bundled compose user owns its database, so nothing to do there. |
| Initialised by the old `schema.sql`, role not the owner | `SELECT, INSERT` on `pergams`, `SELECT` on `pergams_latest`, plus either `CREATE` on the schema (to create `schema_migrations` once) or a `schema_migrations` table created by the owner with `SELECT, INSERT` granted to the role. |

A later migration that changes the schema needs the privileges that change requires (usually ownership of the table), and says so in its file. `002_pergam_meta.sql` creates `pergam_meta`, so it needs `CREATE` on the schema; alternatively the owner runs that file once and grants `SELECT, INSERT, UPDATE` on `pergam_meta` to the role, and the migration then runs no DDL. `003_pergam_text.sql` works the same way with `pergam_text`, for which the role needs `SELECT, INSERT, UPDATE`. `004_pergam_text_extractor.sql` alters `pergam_text`, so it needs ownership of that table (the role owns it when it ran `003` itself); alternatively the owner runs that file once too, and the migration then runs no DDL. `005_pergam_note.sql` creates `pergam_note` like `002` and `003` do, so it needs `CREATE` on the schema, or the owner runs it once and grants `SELECT, INSERT` on `pergam_note` to the role. `006_pergam_origin.sql` alters `pergam_meta` like `004` alters `pergam_text`: it needs ownership of that table (the role owns it when it ran `002` itself), or the owner runs that file once and the migration then runs no DDL. `007_pergam_collaborators.sql` and `008_pergam_pinned_at.sql` alter `pergam_meta` the same way, with the same privileges.

## Project layout

```
pergam/
├── src/pergam/              stdlib http.server + psycopg, no framework
│   ├── server.py            entry point: migrations, pool, HTTP server
│   ├── routes.py            HTTP routing and request validation
│   ├── store.py             data access (all SQL)
│   ├── layout.py            head assets and the top bar shared by every page
│   ├── icons.py             inline SVG icons for every page
│   ├── error_page.py        error responses as a page, for browsers only
│   ├── index_page.py        HTML index renderer
│   ├── index_links.py       canonical index URLs and author helpers
│   ├── filters_ui.py        Filters button, active-filter chips, the filter form (dialog and /filters page)
│   ├── states.py            the status filter (active, archived, all)
│   ├── view_page.py         view page: bar, frame policy and the framed document
│   ├── view_panel.py        view page's side panel: history and details
│   ├── diff.py              text diff of two versions, grouped by section
│   ├── diff_page.py         diff page renderer
│   ├── api.py               JSON bodies of /api/pergams
│   ├── feed.py              Atom feed of recent publications
│   ├── templates/           index, view, diff and error pages (HTML + layout CSS)
│   ├── static/              pergam.css, theme.js and shortcuts.js (every page), index.js and filters.js (index), pages.js (view and diff), frame-bridge.js (added to /content), vendored Mermaid
│   ├── static_files.py      the files served under /static/
│   ├── identity.py          the writer, from X-Forwarded-User when trusted
│   ├── access.py            owners, collaborators and the 403 rules
│   ├── lint.py              static checks of a document for POST /lint
│   ├── public_url.py        host/URL used in view_url and on the index
│   ├── limits.py            largest request body (PERGAM_MAX_BODY_MB)
│   ├── pins.py              cap on pinned pergams (PERGAM_MAX_PINNED)
│   ├── taxonomy.py          accepted types and badge colours
│   ├── tags.py              tag normalisation and limits
│   ├── meta.py              per-id metadata and PATCH validation
│   ├── transfer.py          export document and import validation
│   ├── ids.py               shape of a pergam id
│   ├── text.py              visible text of a pergam as Markdown (search and /text)
│   ├── search.py            full-text search SQL and snippets
│   ├── summary.py           one-line summary of a pergam from its extracted text
│   ├── backfill.py          text of versions without text or with stale text
│   ├── cursor.py            sort orders and their keyset cursors for index pagination
│   └── migrate.py           numbered migrations runner
├── db/
│   └── migrations/          NNN_*.sql, applied at server startup
├── tests/                   pytest suite (real server + throwaway Postgres)
├── scripts/
│   └── migrate_from_fs.py   import legacy ./data/*.html files
├── skills/post-pergam/      Claude Code skill, server profiles and promote helper (see below)
├── web/                     public landing + example hosted worker (Cloudflare)
├── .github/workflows/       CI: test suite + image build on every pull request; release-image.yml publishes the image on a version tag
├── Dockerfile               python:3.14-slim, runs as uid 10001
├── docker-compose.yml       app + bundled postgres + healthchecks
├── Makefile                 up / down / restart / logs / ps / health / clean / test
├── CHANGELOG.md             what changed in each release, and upgrade notes
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

For larger teams, swap `tls internal` for an OIDC sidecar (e.g. `oauth2-proxy`) that sets `X-Forwarded-User`, and start pergam with `TRUST_PROXY_USER=1` so authors come from that header (see [Identity](#identity)). Publish pergam's port only to the proxy, never directly, or anyone could send the header themselves.

### Published image

Every release is published as a multi-arch image (`linux/amd64`, `linux/arm64`) at `ghcr.io/diesilveira/pergam`, built from the repository's `Dockerfile`. Tags for release `2.0.0`: `2.0.0`, `2.0`, `2` and `latest`. Use the exact version in production, or `2` / `2.0` to follow minor and patch releases. The image carries the OCI labels `source`, `version`, `revision` and `licenses`.

With compose, set `PERGAM_IMAGE` (in `.env` or the environment) and start without `--build`:

```bash
PERGAM_IMAGE=ghcr.io/diesilveira/pergam:2.0.0 docker compose up -d     # pulls the image
PERGAM_IMAGE=ghcr.io/diesilveira/pergam:2.0.0 docker compose pull && docker compose up -d   # later: move to a newer image
```

Compose pulls an image it does not have; `--build` (and `make up`) builds from source and tags the result with that name instead. With `PERGAM_IMAGE` unset nothing changes: the service builds `pergam:latest` from the checkout.

Without compose, run the image against any Postgres, with the same environment variables (see [Configuration](#configuration)). The server applies its migrations on start:

```bash
docker network create pergam
docker run -d --name pergam-db --network pergam \
  -e POSTGRES_DB=pergam -e POSTGRES_USER=pergam -e POSTGRES_PASSWORD=CHANGE_ME \
  -v pergam_db:/var/lib/postgresql/data postgres:15-alpine
docker run -d --name pergam --network pergam -p 1111:1111 \
  -e DATABASE_URL=postgresql://pergam:CHANGE_ME@pergam-db:5432/pergam \
  -e PUBLIC_URL=http://localhost:1111 \
  ghcr.io/diesilveira/pergam:2.0.0
curl http://localhost:1111/healthz   # → {"ok":true}
```

The container runs as uid 10001 and needs nothing else mounted.

### Releasing

The version lives in one place, `__version__` in `src/pergam/__init__.py`; the HTTP `Server` header (`pergam/<version> Python/…`) comes from it. To release: bump `__version__`, add the version to [CHANGELOG.md](CHANGELOG.md), merge, then push the tag `v<version>` (`v2.0.0`). The `release-image` workflow runs on that tag only: it fails unless the tag is `vMAJOR.MINOR.PATCH` and equals `v` + `__version__`, runs the test suite, then builds and pushes the image with the built-in `GITHUB_TOKEN`. It never runs on pull requests; the `docker-build` job in `test.yml` builds the image on every pull request without pushing it.

## Testing

```bash
make test
```

That creates `.venv`, installs `requirements-dev.txt` and runs `pytest`. The suite boots the real server (`python3 src/pergam/server.py`) against a throwaway Postgres: by default it starts a `postgres:15-alpine` container named `pergam-test-*` on a random local port with no volume, and removes it at the end. Set `TEST_DATABASE_URL` to an admin connection string to use an existing server instead (CI does this with a `services: postgres` container). Either way every run works in its own `pergam_test_*` database, dropped afterwards; the bundled compose database is never touched.

`tests/test_frame_bridge.py` drives the view page and its frame bridge in a real browser through Playwright (a test-only dependency), with the installed Google Chrome or a Playwright Chromium, with the frame sandboxed as served, including that a published script cannot read or write through the API, framed or opened alone. `tests/test_view_browser.py` covers the view page's bar, menus and history panel (remembered state, full width when closed, phone width, keyboard, contrast in both themes), `tests/test_view_meta_browser.py` pinning, archiving and tag editing from it, including that a published script cannot pin or archive anything, and `tests/test_diff_page.py` the diff page's pickers, section list and the legibility of its word marks in both themes. `tests/test_ui_browser.py` does the same for the themes: both colour schemes, the toggle and its remembered choice (applied before first paint), keyboard use, phone width and the legibility of every type badge colour; `tests/test_index_browser.py` for the index and search: the shortcuts, searching from a view or diff page, filter counts on a search, one-line rows and the "Show summaries" switch, the sort menu, "Load more" in both orders, keyboard focus, the compact search around a document, the pinned row's buttons, focus and reduced motion, and phone width in both themes; `tests/test_filter_dialog_browser.py` for the filter dialog: the draft and live count, applying, every way of cancelling, dimmed values, folding and find fields, the keyboard, the phone sheet and the page without script. Without Playwright or a browser those tests are skipped, except when `CI` is set (GitHub Actions sets it): there they fail.

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

No telemetry. No external network calls. The server only reads/writes its own Postgres. A self-hosted pergam's CSP lets a document load resources only from the server itself and from jsDelivr (Mermaid in pergams published before the server served it). That does not stop a document from sending the reader elsewhere: it can navigate its own frame to any URL, and its links go anywhere on a click or in a new tab, carrying whatever it puts in those URLs. What it can put there is only what it holds itself, since it cannot read the API or this server's cookies.
