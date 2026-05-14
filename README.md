<div align="center">

# 📜 pergam

**Immutable parchment for what your AI builds.**

A tiny self-hostable service — or a hosted endpoint — that turns every HTML report, dashboard, or design doc your AI generates into a permanent, versioned URL your team can actually open.

<br>

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/github/v/tag/diesilveira/pergam?label=version&color=brightgreen)](https://github.com/diesilveira/pergam/releases/latest)
[![GitHub stars](https://img.shields.io/github/stars/diesilveira/pergam?style=flat&logo=github)](https://github.com/diesilveira/pergam/stargazers)

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Postgres](https://img.shields.io/badge/Postgres-15-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Cloudflare Workers](https://img.shields.io/badge/Cloudflare%20Workers-deployed-F38020?logo=cloudflare&logoColor=white)](https://api.pergam.dev/healthz)
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
- 🤖 **AI-native** — ships with a [Claude Code skill](skills/post-pergam/SKILL.md) so your agent can press "ship" and hand back a link without you babysitting `curl`.
- 🪞 **Built-in viewer** — sandboxed iframe with strict CSP. Mermaid + jsDelivr allowed; everything else blocked.
- 🏷️ **Typed taxonomy** — `plan`, `investigacion`, `informe`, `reporte`, `otro`. Filter the index by type, author, or substring.
- 🐳 **Self-hostable** — one Python file (~500 LOC), one Postgres table, one `docker compose up`.
- ☁️ **Also hosted** — [pergam.dev](https://pergam.dev) for ephemeral 72-hour shares. Zero setup, drag-and-drop.
- 🪶 **Tiny stack** — Python stdlib + `psycopg` for the server. Cloudflare Worker + KV for the hosted side. No framework, no build step.

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

### ☁️ Hosted (zero setup, 72h TTL)

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

The skill **auto-picks the backend** based on `$PERGAM_URL`:

- Unset / `https://api.pergam.dev` → hosted (Flow A, ephemeral)
- Local URL like `http://localhost:1111` → self-hosted (Flow B, versioned)

See [`skills/post-pergam/SKILL.md`](skills/post-pergam/SKILL.md) for the full contract — when to bump a version, the type taxonomy, and the HTML guidelines for what the AI should produce.

## 🧱 Architecture

```
┌─────────────┐      ┌──────────────────┐      ┌──────────────┐
│   Client    │ ───→ │      pergam      │ ───→ │   Postgres   │
│ (AI / curl) │      │ (Python · 500 LOC)│      │  (1 table)   │
└─────────────┘      └──────────────────┘      └──────────────┘
```

- **One Python process**, no framework — just `http.server` + `psycopg`.
- **One Postgres table**: `pergams (id, version, …)` with a composite key. Latest version per `id` resolved via a window function on read.
- **Strict CSP per response**: sandboxed iframe, `frame-ancestors` allowlist, no inline scripts on the viewer chrome.
- **Hosted side** (Cloudflare Worker + KV) is a separate ~200 LOC subset: token-based, TTL-bound, no versioning. See [`web/worker/`](web/worker/).

Full schema, request flow, and config knobs → [DOCS.md](DOCS.md).

## 📖 Docs

| Topic | Where |
| --- | --- |
| 🔌 Full API reference | [DOCS.md § API](DOCS.md) |
| 🕰️ Versioning model | [DOCS.md § Versioning](DOCS.md) |
| ⚙️ Configuration | [DOCS.md § Configuration](DOCS.md) |
| 🚢 Deployment guide | [DOCS.md § Deployment](DOCS.md) |
| 🤖 Claude Code skill | [`skills/post-pergam/SKILL.md`](skills/post-pergam/SKILL.md) |
| ☁️ Hosted Worker | [`web/worker/`](web/worker/) |
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
