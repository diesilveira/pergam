<div align="center">

# 📜 pergam

**Immutable parchment for what your AI builds.**

A tiny self-hostable service — or a hosted endpoint — that turns every HTML report, dashboard, or design doc your AI generates into a permanent, versioned URL your team can actually open.

<br>

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/github/v/tag/diesilveira/pergam?label=version&color=brightgreen)](https://github.com/diesilveira/pergam/releases/latest)
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

- 📦 **Publish HTML** via one `POST`, get a stable URL back.
- 🕰️ **Versioned** — every logical `id` keeps full history. `v1`, `v2`, … all reachable forever.
- 🔒 **Immutable** — pergams can't be edited or deleted, only superseded with a new version.
- 🧭 **Visible versions** — every pergam opens under a slim bar with its title, type, tags, author and `v3 of 5`, a version selector with each version's note, and a notice on older versions linking to the current one. `/{id}/diff/v2..v5` shows what changed between two versions, section by section.
- 🤖 **AI-native** — ships with a [Claude Code skill](skills/post-pergam/SKILL.md) so your agent can press "ship" and hand back a link without you babysitting `curl`.
- 🪞 **Built-in viewer** — on [pergam.dev](https://pergam.dev), a sandboxed iframe with strict CSP: Mermaid + jsDelivr allowed, everything else blocked. A self-hosted server frames each version under its version bar but does not sandbox it yet: published scripts run on the server's origin.
- 🏷️ **Typed and tagged** — a `type` per pergam from a configurable list (`PERGAM_TYPES`, five by default) plus free-form tags. Filter the index by type, author or tag; pin what matters, archive what doesn't.
- 🔎 **Searchable** — `?q=` searches titles and the visible text of every pergam, with highlighted snippets.
- 📡 **Atom feed** — `/feed.atom` follows new publications, filtered by tag or author.
- 🚚 **Portable** — `/{id}/export` and `POST /import` move a pergam to another server with its id, versions, dates, tags and notes; the skill promotes one between named servers in one command.
- 🧩 **JSON API** — `/api/pergams` lists, filters, pages and counts like the index; `/api/pergams/{id}` returns one pergam with its versions.
- 🔐 **Owners behind your proxy** — with `TRUST_PROXY_USER=1` the author comes from an authenticating reverse proxy (`X-Forwarded-User`), and only a pergam's owner and the collaborators they list can add versions or edit it.
- 🐳 **Self-hostable** — a few small Python modules, one Postgres database, one `docker compose up`.
- ☁️ **Also hosted** — [pergam.dev](https://pergam.dev) for public 1-year shares. Zero setup, drag-and-drop.
- 🪶 **Tiny stack** — Python stdlib + `psycopg` for the server. A tiny edge worker for the hosted variant. No framework, no build step.

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

- **One Python process**, no framework — just `http.server` + `psycopg`.
- **Four Postgres tables**: immutable `pergams (id, version, …)` with a composite key, `pergam_meta (id, tags, archived, pinned, origin, collaborators)` for what belongs to the id rather than to a version, `pergam_text` with each version's extracted text and full-text search document, and `pergam_note` with the optional note of each version. Latest version per `id` resolved on read through the `pergams_latest` view.
- **Framed documents**: the view page loads each version in an iframe from `/{id}/v{n}/content` and talks to it only through a `postMessage` bridge, so the frame can be sandboxed later without changing the page. It is not sandboxed yet; the hosted viewer is (sandboxed iframe, strict CSP, `frame-ancestors` allowlist).
- **Hosted side** is a separate ~200 LOC subset: token-based, TTL-bound, no versioning. See [`web/worker/`](web/worker/) for a reference deployment on Cloudflare Workers + KV.

Full schema, request flow, and config knobs → [DOCS.md](DOCS.md).

## 📖 Docs

| Topic | Where |
| --- | --- |
| 🔌 Full API reference | [DOCS.md § API](DOCS.md) |
| 🕰️ Versioning model | [DOCS.md § Versioning](DOCS.md) |
| ⚙️ Configuration | [DOCS.md § Configuration](DOCS.md) |
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

Built by [diesilveira.dev](https://diesilveira.dev) · Live at [pergam.dev](https://pergam.dev) · v1.0.0

</div>
