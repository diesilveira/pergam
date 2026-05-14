# pergam

> Immutable parchment for what your AI builds.

`pergam` is a tiny, self-hostable HTTP service that **stores and serves HTML pergams** — dashboards, reports, design docs, comparison boards, anything you can express as a self-contained HTML page. Every pergam is content-addressable by `(id, version)`, born immutable, and viewable at a stable URL.

It's designed to be the place an AI agent presses "ship" and gets back a URL your team will actually open. Pair it with the companion [Claude Code skill](#claude-code-skill) and one prompt becomes one shareable artifact — "che, te paso un pergam".

## Why

LLM coding sessions generate a stream of useful HTML artifacts — PR review boards, status dashboards, evaluation reports, spec docs with Mermaid diagrams — and most of them die in chat scrollback. The alternatives (pasting into Notion, manually exporting to a wiki, screenshot-sharing) are too much friction for content the AI produced in seconds.

pergam is the smallest possible "publish HTML, get URL, share" service: one stdlib Python file, one Docker compose, one Postgres table, immutable history.

## Quick start

```bash
git clone https://github.com/<you>/pergam
cd pergam
docker compose up -d --build
curl http://localhost:1111/healthz
# -> {"ok":true}
open http://localhost:1111/
```

That's it. The compose file bundles Postgres so you don't need anything else.

Publish a first pergam:

```bash
curl -sS -X POST http://localhost:1111/pergam \
  -H 'Content-Type: application/json' \
  -d '{
    "title":  "Hello",
    "html":   "<!doctype html><h1>Hello, pergam</h1>",
    "type":   "otro",
    "author": "you@example.com"
  }'
# -> {"id":"a1b2c3d4","version":1,"view_url":"http://localhost:1111/a1b2c3d4/view"}
```

Open the `view_url` in a browser.

## Endpoints

| Method   | Path                  | Description                                                          |
| -------- | --------------------- | -------------------------------------------------------------------- |
| `POST`   | `/pergam`             | Body `{title, html, type, author, id?}`. Returns `{id, version, view_url, title, type, author}`. Omit `id` for a new pergam; include it to bump the version of an existing one. |
| `GET`    | `/`                   | HTML index. Filters: `?type=<type>`, `?author=<email>`, `?q=<title-substring>`. |
| `GET`    | `/{id}/view`          | Render the latest version.                                           |
| `GET`    | `/{id}/v{n}/view`     | Render a specific version.                                           |
| `GET`    | `/{id}/raw`           | Raw HTML source (latest).                                            |
| `GET`    | `/{id}/v{n}/raw`      | Raw HTML source (specific version).                                  |
| `GET`    | `/{id}/versions`      | JSON: `[{version, title, type, author, bytes, created_at}, …]`.      |
| `GET`    | `/healthz`            | Liveness probe.                                                      |
| `DELETE` | `/{id}`               | **405 Method Not Allowed** — pergams are immutable.                  |

## Versioning model

Every pergam has a logical `id` (random 8-hex) and a monotonically increasing `version`. The public surface defaults to the latest version of an id:

- `POST /pergam` with no `id` → new pergam, `version=1`
- `POST /pergam` with an existing `id` → same id, `version + 1`
- `GET /{id}/view` → latest version
- `GET /{id}/v3/view` → version 3 specifically

Pergams cannot be deleted. To "supersede" a pergam, post a new version with the same id.

## Pergam types

The `type` field is required and free-form `text`, but a recommended taxonomy keeps filters useful:

| Type            | When to pick it                                                       |
| --------------- | --------------------------------------------------------------------- |
| `plan`          | Step-by-step implementation plans, deploy plans, migration plans.     |
| `investigacion` | Research, evaluations, comparing alternatives.                        |
| `informe`       | Reviews, audits, post-mortems, status reports.                        |
| `reporte`       | Recurring dashboards, comparison boards, leaderboards.                |
| `otro`          | Anything that doesn't fit the above.                                  |

## Configuration

App env vars follow 12-factor conventions (no `PERGAM_*` prefix inside the container — the container *is* pergam):

| Env var             | Default                | Description                                  |
| ------------------- | ---------------------- | -------------------------------------------- |
| `DATABASE_URL`      | bundled `db` service   | Postgres connection string.                  |
| `HOST`              | `0.0.0.0`              | Bind host inside the container.              |
| `PORT`              | `1111`                 | Bind port inside the container.              |
| `PUBLIC_HOST`       | `localhost`            | Host used in returned `view_url`s.           |
| `PUBLIC_PORT`       | `1111`                 | Port used in returned `view_url`s.           |
| `POSTGRES_PASSWORD` | `pergam`               | Password for the bundled `db` service.       |
| `PERGAM_PORT`       | `1111`                 | Host-side published port (compose only).     |

See `.env.example` for the full set.

## Project layout

```
pergam/
├── server.py            ~300 LOC stdlib http.server + psycopg
├── Dockerfile           python:3.13-slim, runs as uid 10001
├── docker-compose.yml   app + bundled postgres + healthchecks
├── schema.sql           postgres init script (runs on first boot)
├── Makefile             up / down / restart / logs / ps / health / clean
├── requirements.txt     psycopg[binary], psycopg-pool
├── migrate_from_fs.py   import legacy ./data/*.html files
├── .env.example
└── skills/
    └── post-pergam/     Claude Code skill (see below)
```

## Claude Code skill

`skills/post-pergam/` contains a [Claude Code](https://claude.com/claude-code) skill that knows how to talk to pergam. To install it:

```bash
# user-level (available in every project)
cp -r skills/post-pergam ~/.claude/skills/

# OR project-level (committed to a repo, available only there)
cp -r skills/post-pergam <your-repo>/.claude/skills/
```

The skill reads the user's email from the Claude Code session, picks a `type` from context, and decides whether to publish as a new pergam or a new version of an existing one. See `skills/post-pergam/SKILL.md` for the full contract.

## Deployment

For a single internal team, the easiest path is:

1. Run `docker compose up -d` on a small VM behind your VPN.
2. Point a DNS record (e.g. `pergam.internal`) at it.
3. Optionally put [Caddy](https://caddyserver.com/) in front for TLS and auth — pergam itself has no auth and assumes the network is a trust boundary.

For larger teams, swap `tls internal` for an OIDC sidecar (e.g. `oauth2-proxy`) so per-user attribution flows in via `X-Forwarded-User`.

## Make targets

```
make up        # docker compose up -d --build
make down      # docker compose down
make restart   # docker compose restart
make logs      # tail logs from both services
make ps        # show service status
make health    # curl /healthz
make clean     # stop everything and wipe the bundled db volume
```

## Privacy

No telemetry. No external network calls. The server only reads/writes its own Postgres. Embedded HTML may pull external resources (Mermaid CDN, fonts) if the publisher includes them — that's outside pergam's control.

## Contributing

Open an issue first if you want to propose a non-trivial change. PRs welcome for bug fixes, performance improvements, additional type-taxonomy recommendations, and skill enhancements.

## License

MIT. See [LICENSE](LICENSE).
