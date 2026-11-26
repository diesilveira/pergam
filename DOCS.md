# pergam — docs

Full reference for the pergam HTTP service: endpoints, versioning model, type taxonomy, configuration, deployment, and the Claude Code skill.

For a quick intro and `docker compose up`, see the [README](README.md).

## Endpoints

| Method   | Path                  | Description                                                          |
| -------- | --------------------- | -------------------------------------------------------------------- |
| `POST`   | `/pergam`             | Body `{title, html, type, author, id?, tags?}`. Returns `{id, version, view_url, title, type, author}`, plus `tags` when the request sent them. Omit `id` for a new pergam; include it to bump the version of an existing one. `tags` are merged into the id's tags (see [Tags and metadata](#tags-and-metadata)). |
| `GET`    | `/`                   | HTML index. Filters: `?type=<type>`, `?author=<email>`, `?q=<title-substring>`. |
| `GET`    | `/{id}/view`          | Render the latest version.                                           |
| `GET`    | `/{id}/v{n}/view`     | Render a specific version.                                           |
| `GET`    | `/{id}/raw`           | Raw HTML source (latest).                                            |
| `GET`    | `/{id}/v{n}/raw`      | Raw HTML source (specific version).                                  |
| `GET`    | `/{id}/versions`      | JSON: `[{version, title, type, author, bytes, created_at}, …]`.      |
| `GET`    | `/{id}/meta`          | JSON: `{id, tags, archived, pinned}`. **404** for an unknown id.     |
| `PATCH`  | `/{id}/meta`          | Body with any of `{tags, archived, pinned}`; returns the resulting `{id, tags, archived, pinned}`. Never creates a version. **404** unknown id, **400** malformed body. |
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

Caching: a version-pinned route (`/{id}/v{n}/view`, `/{id}/v{n}/raw`) never changes, so it is sent with `Cache-Control: public, max-age=31536000, immutable`. Everything else (latest `/{id}/view` and `/{id}/raw`, the index, JSON routes) is `no-store`.

Pergams cannot be deleted. To "supersede" a pergam, post a new version with the same id; to hide it, archive it with `PATCH /{id}/meta`.

## Tags and metadata

Tags, `archived` and `pinned` belong to the logical `id`, not to a version, and live in `pergam_meta`. Changing them never creates a version. An id without a metadata row reads as no tags, not archived, not pinned.

- Tags are free-form and normalised: trimmed, lowercased, runs of whitespace, `_` and `-` collapsed to one `-`, empties dropped, duplicates removed, stored sorted. `" Q3  Review_notes "` → `q3-review-notes`.
- At most 20 tags per id, each at most 40 characters after normalisation; beyond that the request gets **400** and nothing is written.
- `POST /pergam` with `tags` **merges** them into the id's existing tags; posting without `tags` (or `"tags": null`) leaves them as they are.
- `PATCH /{id}/meta` edits metadata of an id that already has versions. Fields it omits keep their value; `tags` **replaces** the whole set (`[]` clears it); `archived` and `pinned` must be JSON booleans. Unknown fields, an empty object or a wrong type are **400**.

```bash
curl -sS -X PATCH http://localhost:1111/abc12345/meta \
  -H 'Content-Type: application/json' \
  -d '{"tags":["billing","q3"],"pinned":true}'
# → {"id":"abc12345","tags":["billing","q3"],"archived":false,"pinned":true}
```

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
| `PUBLIC_URL`        | unset                  | Base of returned `view_url`s, e.g. `https://docs.example.com` → `https://docs.example.com/{id}/view`. Takes precedence over `PUBLIC_HOST`/`PUBLIC_PORT`. |
| `PUBLIC_HOST`       | `localhost`            | Host used in returned `view_url`s when `PUBLIC_URL` is unset. |
| `PUBLIC_PORT`       | `PORT`                 | Port used in returned `view_url`s when `PUBLIC_URL` is unset; `443` means `https`, `80`/`443` are omitted from the URL. |
| `POSTGRES_PASSWORD` | `pergam`               | Password for the bundled `db` service.       |
| `PERGAM_PORT`       | `1111`                 | Host-side published port (compose only).     |
| `MIGRATIONS_DIR`    | `/app/db/migrations`   | Directory of `NNN_*.sql` migrations (the image sets it; from a checkout it defaults to `db/migrations`). |

See `.env.example` for the full set. The server refuses to start when `PUBLIC_URL` is not an absolute `http(s)` URL, carries credentials, a query or fragment marker (`?`, `#`) or a non-numeric port, or when `PUBLIC_PORT` is not a number.

## Schema migrations

The schema lives in numbered files, `db/migrations/NNN_description.sql`, shipped inside the image. On every start the server applies the pending ones before it listens:

- Applied migrations are recorded in `schema_migrations (version, name, applied_at)`.
- Each migration runs in its own transaction: a failure rolls it back completely, the server logs `migrations failed` and exits non-zero.
- A Postgres advisory lock serializes servers that start at the same time against one database.

Migrations are additive and safe on a populated database: they never drop or rewrite existing rows, and any backfill is idempotent. `001_initial_schema.sql` is the schema the bundled compose used to load through `docker-entrypoint-initdb.d`. When `pergams` already exists it runs no DDL at all, so existing instances upgrade in place with every row, index and view untouched and only `schema_migrations` gains a row. `002_pergam_meta.sql` adds the `pergam_meta (id, tags, archived, pinned)` table and writes no rows. To add a change, create the next number and restart the server.

### Database privileges

The `DATABASE_URL` role needs:

| Database state | Privileges |
| -------------- | ---------- |
| Fresh (no `pergams` yet) | `CREATE` on the schema: the role creates and owns the tables. The bundled compose user owns its database, so nothing to do there. |
| Initialised by the old `schema.sql`, role not the owner | `SELECT, INSERT` on `pergams`, `SELECT` on `pergams_latest`, plus either `CREATE` on the schema (to create `schema_migrations` once) or a `schema_migrations` table created by the owner with `SELECT, INSERT` granted to the role. |

A later migration that changes the schema needs the privileges that change requires (usually ownership of the table), and says so in its file. `002_pergam_meta.sql` creates `pergam_meta`, so it needs `CREATE` on the schema; alternatively the owner runs that file once and grants `SELECT, INSERT, UPDATE` on `pergam_meta` to the role, and the migration then runs no DDL.

## Project layout

```
pergam/
├── src/pergam/              stdlib http.server + psycopg, no framework
│   ├── server.py            entry point: migrations, pool, HTTP server
│   ├── routes.py            HTTP routing and request validation
│   ├── store.py             data access (all SQL)
│   ├── index_page.py        HTML index renderer
│   ├── templates/           index.html + index.css
│   ├── public_url.py        host/URL used in view_url and on the index
│   ├── tags.py              tag normalisation and limits
│   ├── meta.py              per-id metadata and PATCH validation
│   └── migrate.py           numbered migrations runner
├── db/
│   └── migrations/          NNN_*.sql, applied at server startup
├── tests/                   pytest suite (real server + throwaway Postgres)
├── scripts/
│   └── migrate_from_fs.py   import legacy ./data/*.html files
├── skills/post-pergam/      Claude Code skill (see below)
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

The skill reads the user's email from the Claude Code session, picks a `type` from context, and decides whether to publish as a new pergam or a new version of an existing one. See `skills/post-pergam/SKILL.md` for the full contract.

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
