# 📜 pergam

> Immutable parchment for what your AI builds.

A tiny self-hostable service that stores HTML "pergams" — dashboards, reports, plans, anything self-contained — and serves them at a stable, versioned URL.

One prompt → one shareable link. 🔗

## 🚀 Quick start

```bash
git clone https://github.com/diesilveira/pergam
cd pergam
docker compose up -d --build
open http://localhost:1111/
```

Publish your first pergam:

```bash
curl -sS -X POST http://localhost:1111/pergam \
  -H 'Content-Type: application/json' \
  -d '{"title":"Hello","html":"<h1>hi</h1>","type":"otro","author":"you@example.com"}'
```

You'll get a `view_url` back — open it in a browser. That's the whole loop. ✨

## 💡 What it does

- 📦 **Publish HTML** via one POST, get a stable URL.
- 🔒 **Immutable** — pergams can't be deleted, only superseded with a new version.
- 🕰️ **Versioned** — every `id` keeps its full history.
- 🤖 **AI-native** — ships with a [Claude Code](https://claude.com/claude-code) skill so an agent can press "ship" and hand back a link.
- 🏠 **Self-hostable** — one Python file, one Postgres table, one `docker compose up`.

## 📖 Docs

Full API, configuration, versioning model and deployment guide → [**DOCS.md**](DOCS.md).

Claude Code skill → [`skills/post-pergam/SKILL.md`](skills/post-pergam/SKILL.md).

## 🤝 Contributing

Issues and PRs welcome. Open an issue first for non-trivial changes.

## 📝 License

MIT — see [LICENSE](LICENSE).
