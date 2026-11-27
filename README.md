<div align="center">

# 📜 pergam

**Immutable parchment for what your AI builds.**

A tiny self-hostable service — or a hosted endpoint — that turns every HTML report, dashboard, or design doc your AI generates into a permanent, versioned URL your team can actually open.

<br>

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/github/v/tag/diesilveira/pergam?label=version&color=brightgreen)](CHANGELOG.md)
[![GitHub stars](https://img.shields.io/github/stars/diesilveira/pergam?style=flat&logo=github)](https://github.com/diesilveira/pergam/stargazers)

[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Postgres](https://img.shields.io/badge/Postgres-15-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-skill-D97757?logo=anthropic&logoColor=white)](skills/post-pergam/SKILL.md)

[**Try it live →**](https://pergam.dev) · [**Docs**](DOCS.md) · [**Claude Code skill**](skills/post-pergam/SKILL.md) · [**API health**](https://api.pergam.dev/healthz)

<br>

<img src="https://pergam.dev/og.png" alt="pergam — terminal showing the post-pergam Claude Code skill producing a stable URL" width="780">

</div>

---

## 🤔 Why pergam

Your AI generates beautiful HTML — reports, dashboards, design docs, plans. Then those artifacts vanish into chat scrollback. You lose the URL, you lose the version, you lose the trail.

**pergam fixes that.** One prompt → one shareable, permanent URL. Versioned. Immutable. No accounts, no telemetry, no SaaS lock-in. Self-host it in a `docker compose up`, or use the hosted service for quick public links.

## ✨ Features

**Publish and version**

- 📦 **Publish HTML** with one `POST` and get a stable URL back.
- 🕰️ **Versioned** — every `id` keeps its full history, `v1`, `v2`, … reachable forever.
- 🔒 **Immutable** — a pergam is never edited or deleted, only superseded by a new version.
- 📝 **Version notes** — an optional one-line note says what each version changes.
- 🧹 **Lint** — `POST /lint` reports what the sandbox would break, with line and column, and stores nothing.
- 🤖 **AI-native** — a [Claude Code skill](skills/post-pergam/SKILL.md) publishes for you and hands back the link.

**Organise and find**

- 🏷️ **Types and tags** — a `type` per pergam from a configurable list (`PERGAM_TYPES`), plus free-form tags.
- 🎛️ **Filter dialog** — filter by several types, authors and tags and by status, with live counts.
- 📌 **Pinned row** — pin what matters as cards above the list (12 by default); archive what doesn't.
- 🔎 **Search** — titles and the visible text of every pergam, with highlighted snippets; press `/` anywhere.
- 📡 **Atom feed** — `/feed.atom` follows new publications, by tag or author.

**Read**

- 🧭 **View page** — the document at full width under one bar: version selector, Pin, History and a menu.
- 🕓 **History panel** — each version's note, author and date; older versions link to the current one.
- 🔀 **Diff** — `/{id}/diff/v2..v5` shows what changed between two versions, section by section.
- 🛡️ **Sandboxed viewer** — documents run in an opaque origin under a strict content policy, so a script cannot reach the API.
- 🌗 **Light and dark themes** — follows the system, or pick one; system fonts only.
- 📄 **Text view** — `/{id}/text` returns what a reader sees, as Markdown.

**Integrate and move**

- 🧩 **JSON API** — `/api/pergams` lists, filters, sorts and pages like the index; `/api/pergams/{id}` returns one pergam with its versions.
- 🚚 **Export and import** — `/{id}/export` and `POST /import` move a pergam, versions and all, to another server.
- 🔐 **Owners behind your proxy** — with `TRUST_PROXY_USER=1` the author comes from `X-Forwarded-User`, and only the owner and their collaborators can write.

**Run it**

- 🐳 **Self-hostable** — one Python process, one Postgres database, one `docker compose up`.
- 🪶 **Tiny stack** — Python stdlib and `psycopg`; no framework, no build step, no telemetry.
- ☁️ **Also hosted** — [pergam.dev](https://pergam.dev) for public 1-year shares, zero setup.

## ⚡ Quickstart

Two paths, depending on whether you want full history or just a quick public link.

### 🐳 Self-host (versioned, durable, MIT)

```bash
git clone https://github.com/diesilveira/pergam
cd pergam
docker compose up -d --build
curl http://localhost:1111/healthz       # → {"ok":true}
```

Publish your first pergam:

```bash
curl -sS -X POST http://localhost:1111/pergam \
  -H 'Content-Type: application/json' \
  -d '{"title":"Hello","html":"<h1>hi</h1>","type":"otro","author":"you@example.com"}'
```

You'll get a `view_url` back — open it in a browser. That's the whole loop. ✨

Prefer not to build? Run the published multi-arch image (`linux/amd64`, `linux/arm64`) with the same compose file:

```bash
PERGAM_IMAGE=ghcr.io/diesilveira/pergam:2 docker compose up -d
```

Tags are `2.0.0` (exact), `2.0`, `2` and `latest`. Pin `2.0.0` in production and upgrade on purpose. Without compose, [`docker run` works too](DOCS.md#published-image).

### ☁️ Hosted (zero setup, 1-year TTL)

Drag any `.html` onto [pergam.dev](https://pergam.dev) and you get a public link. No account, no key. Same thing via the API:

```bash
curl -sS -X POST https://api.pergam.dev/share \
  -H 'Content-Type: application/json' \
  -d '{"title":"Hello","html":"<h1>hi</h1>"}'
```

Response: `{"token": "…", "view_url": "https://pergam.dev/s/…", "expires_at": "…"}`.

## 🤖 Claude Code skill

Install the [`post-pergam`](skills/post-pergam/SKILL.md) skill once and any project gets a "publish this as a pergam" verb:

```bash
git clone https://github.com/diesilveira/pergam
cp -r pergam/skills/post-pergam ~/.claude/skills/
```

Inside any project, ask Claude:

> _"armá un pergam de X y publicalo"_

The skill **auto-picks the server and backend**: a server the user names ("publish it on team") from `~/.config/pergam/servers.json`, else `$PERGAM_URL`, else the file's `default`, else hosted:

- `https://api.pergam.dev` → hosted (Flow A, 1-year TTL)
- Any other URL, like `http://localhost:1111` → self-hosted (Flow B, versioned), with an optional CA bundle per server for private TLS

See [`skills/post-pergam/SKILL.md`](skills/post-pergam/SKILL.md) for the full contract — when to bump a version, the type taxonomy, and the HTML guidelines for what the AI should produce.

## 🧱 Architecture

```
┌─────────────┐      ┌──────────────────┐      ┌──────────────┐
│   Client    │ ───→ │      pergam      │ ───→ │   Postgres   │
│ (AI / curl) │      │ (Python, stdlib) │      │  (4 tables)  │
└─────────────┘      └──────────────────┘      └──────────────┘
```

- **One Python process**, no framework — `http.server` and `psycopg`. Pages, scripts and Mermaid are served by the process itself.
- **Four Postgres tables**: `pergams` (immutable, keyed by `id` and `version`), `pergam_meta` (tags, archived, pinned, origin, collaborators: what belongs to the id), `pergam_text` (each version's text and search document) and `pergam_note`. The latest version per `id` is read through the `pergams_latest` view.
- **Numbered migrations** in `db/migrations/` are applied when the server starts.
- **Framed documents**: the view page loads each version in a sandboxed iframe from `/{id}/v{n}/content` and talks to it only through `postMessage`.
- **Hosted side** is a separate ~200 LOC subset: token-based, TTL-bound, no versioning. See [`web/worker/`](web/worker/) for a reference deployment on Cloudflare Workers + KV.

Full schema, request flow, and config knobs → [DOCS.md](DOCS.md).

## ⬆️ Upgrading from 1.x

Migrations apply on their own at startup, but 2.0 changes how published HTML is served and renames the compose service. Read the [upgrade notes](CHANGELOG.md#upgrade-notes) in the [changelog](CHANGELOG.md) before you pull.

## 📖 Docs

| Topic | Where |
| --- | --- |
| 🔌 Full API reference | [DOCS.md § API](DOCS.md) |
| 🕰️ Versioning model | [DOCS.md § Versioning](DOCS.md) |
| ⚙️ Configuration | [DOCS.md § Configuration](DOCS.md) |
| 📋 Changelog and upgrade notes | [CHANGELOG.md](CHANGELOG.md) |
| 🚢 Deployment guide | [DOCS.md § Deployment](DOCS.md) |
| 🤖 Claude Code skill | [`skills/post-pergam/SKILL.md`](skills/post-pergam/SKILL.md) |
| ☁️ Hosted variant (example: Cloudflare) | [`web/worker/`](web/worker/) |
| 🌐 Landing source | [`web/public/`](web/public/) |

## 🤝 Contributing

Issues and PRs welcome. Open an issue first for non-trivial changes — happy to chat about it before you put time in.

If pergam is the shape of what you wanted, **starring the repo** is the highest-signal way to tell me. ⭐

## 📝 License

[MIT](LICENSE) — do whatever, no warranty.

---

<div align="center">

Built by [diesilveira.dev](https://diesilveira.dev) · Live at [pergam.dev](https://pergam.dev)

</div>
