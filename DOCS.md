# pergam — docs

Full reference for the pergam HTTP service: endpoints, versioning model, type taxonomy, configuration, deployment, and the Claude Code skill.

For a quick intro and `docker compose up`, see the [README](README.md).

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
