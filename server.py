#!/usr/bin/env python3
"""pergam server — postgres-backed, versioned, immutable.

Versioning model
----------------
Each logical pergam is identified by `id` (random 8-hex). It has one or more
`version` rows (monotonically increasing from 1). The public surface always
defaults to the latest version of an id.

POST /pergam contract
---------------------
Body JSON:
  {
    "title":   "...",            required
    "html":    "...",            required
    "type":    "plan|investigacion|informe|reporte|otro",  required
    "author":  "<email>",        required
    "id":      "abc12345"        optional — present → version++
  }
Returns: {"id": "...", "version": <int>, "view_url": "...", "title": "...",
          "type": "...", "author": "..."}

Endpoints
---------
  POST   /pergam               create new pergam or bump version
  GET    /                     index (latest version per id; ?type= ?author= ?q=)
  GET    /{id}/view            render latest version
  GET    /{id}/v{n}/view       render specific version
  GET    /{id}/raw             raw HTML (latest)
  GET    /{id}/v{n}/raw        raw HTML (specific)
  GET    /{id}/versions        JSON list of versions for an id
  GET    /healthz              liveness probe
  (DELETE removed — pergams are immutable)

Env (12-factor)
---------------
  HOST           bind host                     (default 0.0.0.0)
  PORT           bind port                     (default 1111)
  DATABASE_URL   postgres connection string    (required)
  PUBLIC_HOST    host for returned URLs        (default localhost)
  PUBLIC_PORT    port for returned URLs        (default PORT)
"""

from __future__ import annotations

import html as html_lib
import http.server
import json
import os
import re
import socketserver
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, parse_qs

import psycopg
from psycopg_pool import ConnectionPool

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 1111

_VALID_TYPES = {"plan", "investigacion", "informe", "reporte", "otro"}
_ID_RE = re.compile(r"^[a-f0-9]{6,16}$")
_VERSION_RE = re.compile(r"^v(\d+)$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


class PergamHandler(http.server.BaseHTTPRequestHandler):
    server_version = "pergam/2.0"
    pool: ConnectionPool

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write(f"[{_utc_now_iso()}] {self.address_string()} - {format % args}\n")

    # ----- POST -----
    def do_POST(self) -> None:
        if urlparse(self.path).path != "/pergam":
            self.send_error(404, "Not Found")
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            self.send_error(400, "Empty body")
            return

        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self.send_error(415, "Use application/json")
            return

        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_error(400, f"Invalid JSON: {exc}")
            return

        html = data.get("html", "")
        title = (data.get("title") or "Pergam").strip()
        pergam_type = (data.get("type") or "").strip().lower()
        author = (data.get("author") or "").strip()
        existing_id = (data.get("id") or "").strip()

        if not html or not html.strip():
            self.send_error(400, "Missing html")
            return
        if not author:
            self.send_error(400, "Missing author")
            return
        if pergam_type not in _VALID_TYPES:
            self.send_error(
                400,
                f"type must be one of {sorted(_VALID_TYPES)}",
            )
            return
        if existing_id and not _ID_RE.match(existing_id):
            self.send_error(400, "Invalid id format")
            return

        bytes_count = len(html.encode("utf-8"))

        with self.pool.connection() as conn:
            if existing_id:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM pergams WHERE id = %s",
                    (existing_id,),
                ).fetchone()
                next_version = (row[0] or 0) + 1
                pergam_id = existing_id
            else:
                next_version = 1
                pergam_id = _new_id()
                while conn.execute(
                    "SELECT 1 FROM pergams WHERE id = %s LIMIT 1", (pergam_id,)
                ).fetchone():
                    pergam_id = _new_id()

            conn.execute(
                """
                INSERT INTO pergams (id, version, title, html, type, author, bytes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (pergam_id, next_version, title, html, pergam_type, author, bytes_count),
            )

        public_host = os.environ.get("PUBLIC_HOST", "localhost")
        public_port = int(os.environ.get("PUBLIC_PORT", self.server.server_address[1]))
        scheme = "https" if str(public_port) == "443" else "http"
        host_str = public_host if str(public_port) in ("80", "443") else f"{public_host}:{public_port}"
        view_url = f"{scheme}://{host_str}/{pergam_id}/view"

        response = json.dumps({
            "id": pergam_id,
            "version": next_version,
            "view_url": view_url,
            "title": title,
            "type": pergam_type,
            "author": author,
        })
        self._send(201, "application/json", response.encode("utf-8"))

    # ----- GET -----
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.strip("/")
        query = parse_qs(parsed.query)

        if path == "":
            self._render_index(query)
            return
        if path == "healthz":
            self._send(200, "application/json", b'{"ok":true}')
            return

        parts = path.split("/")

        # /{id}/versions
        if len(parts) == 2 and parts[1] == "versions" and _ID_RE.match(parts[0]):
            self._render_versions_json(parts[0])
            return

        # /{id}/view or /{id}/raw  (latest)
        if len(parts) == 2 and parts[1] in ("view", "raw") and _ID_RE.match(parts[0]):
            self._serve_pergam(parts[0], None, raw=(parts[1] == "raw"))
            return

        # /{id}/v{n}/view or /{id}/v{n}/raw  (specific version)
        if len(parts) == 3 and parts[2] in ("view", "raw") and _ID_RE.match(parts[0]):
            m = _VERSION_RE.match(parts[1])
            if m:
                self._serve_pergam(parts[0], int(m.group(1)), raw=(parts[2] == "raw"))
                return

        self.send_error(404, "Not Found")

    # ----- DELETE intentionally not implemented (immutable) -----
    def do_DELETE(self) -> None:
        self.send_error(405, "Pergams are immutable")

    # --------------------------------------------------------------
    # rendering
    # --------------------------------------------------------------
    def _serve_pergam(self, pergam_id: str, version: int | None, *, raw: bool) -> None:
        with self.pool.connection() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT html, version FROM pergams WHERE id = %s ORDER BY version DESC LIMIT 1",
                    (pergam_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT html, version FROM pergams WHERE id = %s AND version = %s",
                    (pergam_id, version),
                ).fetchone()

        if not row:
            self.send_error(404, "Pergam not found")
            return

        html_text, _ver = row
        payload = html_text.encode("utf-8")
        ctype = "text/html; charset=utf-8" if not raw else "text/plain; charset=utf-8"
        self._send(200, ctype, payload)

    def _render_versions_json(self, pergam_id: str) -> None:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT version, title, type, author, bytes, created_at
                FROM pergams WHERE id = %s ORDER BY version DESC
                """,
                (pergam_id,),
            ).fetchall()

        if not rows:
            self.send_error(404, "Pergam not found")
            return

        out = [
            {
                "version": r[0],
                "title": r[1],
                "type": r[2],
                "author": r[3],
                "bytes": r[4],
                "created_at": r[5].isoformat(),
            }
            for r in rows
        ]
        self._send(200, "application/json", json.dumps(out).encode("utf-8"))

    def _render_index(self, query: dict[str, list[str]]) -> None:
        # filters
        type_filter = (query.get("type", [None])[0] or "").lower() or None
        author_filter = query.get("author", [None])[0] or None
        text_query = query.get("q", [None])[0] or None

        sql = """
            SELECT l.id, l.version, l.title, l.type, l.author, l.created_at, l.bytes,
                   (SELECT count(*) FROM pergams p WHERE p.id = l.id) AS total_versions
            FROM pergams_latest l
            WHERE 1=1
        """
        params: list[Any] = []
        if type_filter:
            sql += " AND l.type = %s"
            params.append(type_filter)
        if author_filter:
            sql += " AND l.author = %s"
            params.append(author_filter)
        if text_query:
            sql += " AND l.title ILIKE %s"
            params.append(f"%{text_query}%")
        sql += " ORDER BY l.created_at DESC LIMIT 500"

        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            type_counts = conn.execute(
                "SELECT type, count(*) FROM pergams_latest GROUP BY type ORDER BY 2 DESC, 1"
            ).fetchall()
            author_counts = conn.execute(
                "SELECT author, count(*) FROM pergams_latest GROUP BY author ORDER BY 2 DESC, 1"
            ).fetchall()
            total = conn.execute("SELECT count(*) FROM pergams_latest").fetchone()[0]

        # ---- filter pill URLs that preserve the OTHER active filters ----
        from urllib.parse import urlencode

        def link(*, override_type=None, override_author=None, override_q=None,
                 clear_type=False, clear_author=False, clear_q=False) -> str:
            qs: dict[str, str] = {}
            if not clear_type:
                t = override_type if override_type is not None else type_filter
                if t: qs["type"] = t
            if not clear_author:
                a = override_author if override_author is not None else author_filter
                if a: qs["author"] = a
            if not clear_q:
                q = override_q if override_q is not None else text_query
                if q: qs["q"] = q
            return "/?" + urlencode(qs) if qs else "/"

        def type_pill(t: str, c: int) -> str:
            active = (t == type_filter)
            cls = ' class="active"' if active else ""
            # active pill → clears that filter; inactive → applies it
            href = link(clear_type=True) if active else link(override_type=t)
            return f'<a href="{html_lib.escape(href)}"{cls}>{html_lib.escape(t)} <span class="cnt">{c}</span></a>'

        def author_pill(a: str, c: int) -> str:
            active = (a == author_filter)
            cls = ' class="active"' if active else ""
            href = link(clear_author=True) if active else link(override_author=a)
            short = a.split("@")[0] if "@" in a else a
            return f'<a href="{html_lib.escape(href)}"{cls} title="{html_lib.escape(a)}">{html_lib.escape(short)} <span class="cnt">{c}</span></a>'

        type_filter_links = " ".join(type_pill(t, c) for t, c in type_counts)
        author_filter_links = " ".join(author_pill(a, c) for a, c in author_counts)
        clear_link = (
            f'<a class="clear" href="/">clear all</a>'
            if (type_filter or author_filter or text_query) else ""
        )

        body_rows = []
        for r in rows:
            (pid, ver, title, ptype, author, created, _bytes, totalv) = r
            pergam_versions = (
                f'<details><summary>v{ver}</summary>'
                f'<a href="/{pid}/versions" class="muted">api</a> · '
                + " · ".join(
                    f'<a href="/{pid}/v{i}/view">v{i}</a>'
                    for i in range(totalv, 0, -1)
                )
                + "</details>"
            )
            author_href = html_lib.escape(link(override_author=author, clear_author=False))
            type_href = html_lib.escape(link(override_type=ptype, clear_type=False))
            body_rows.append(
                f"<tr>"
                f'<td><code>{html_lib.escape(pid)}</code></td>'
                f'<td><a href="/{pid}/view"><strong>{html_lib.escape(title)}</strong></a></td>'
                f'<td><a class="type t-{html_lib.escape(ptype)}" href="{type_href}" title="filter by type">{html_lib.escape(ptype)}</a></td>'
                f'<td class="muted"><a href="{author_href}" class="author-cell" title="filter by author">{html_lib.escape(author)}</a></td>'
                f"<td>{pergam_versions}</td>"
                f'<td class="muted">{created.strftime("%Y-%m-%d %H:%M")}</td>'
                f"</tr>"
            )
        body = "\n".join(body_rows) or '<tr><td colspan="6" class="muted">No pergams match.</td></tr>'

        showing = len(rows)
        showing_note = (
            f"showing {showing} of {total}"
            if (type_filter or author_filter or text_query) and showing != total
            else f"{total} pergams"
        )

        html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>pergam — index</title>
<style>
  :root {{
    --bg:#0d1117; --bg2:#161b22; --bg3:#1c2128;
    --text:#e6edf3; --muted:#8b949e; --accent:#58a6ff; --border:#30363d;
  }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         background:var(--bg); color:var(--text); margin:0; padding:2rem; font-size:14px; line-height:1.55; }}
  h1 {{ color:var(--accent); margin:0 0 .25rem; font-size:1.5rem; }}
  .stats {{ color:var(--muted); margin:0 0 1rem; font-size:.85rem; }}

  /* Filter bar layout */
  .toolbar {{ display:flex; gap:1rem; flex-wrap:wrap; align-items:flex-end; margin-bottom:1.25rem;
              padding-bottom:.85rem; border-bottom:1px solid var(--border); }}
  .filter-group {{ flex:1; min-width:240px; }}
  .filter-group .label {{ color:var(--muted); font-size:.7rem; text-transform:uppercase;
                          letter-spacing:.06em; margin-bottom:.3rem; font-weight:600; }}
  .pills {{ display:flex; gap:.35rem; flex-wrap:wrap; }}
  .pills a {{ display:inline-flex; align-items:center; gap:.35rem;
              padding:.25rem .65rem; border-radius:999px;
              border:1px solid var(--border); color:var(--muted);
              font-size:.78rem; text-decoration:none;
              background:var(--bg2); transition:all .12s ease; }}
  .pills a:hover {{ border-color:var(--accent); color:var(--accent); }}
  .pills a.active {{ border-color:var(--accent); color:var(--accent);
                      background:rgba(88,166,255,.12); font-weight:600; }}
  .pills a .cnt {{ color:var(--muted); font-family:ui-monospace,monospace; font-size:.72em; }}
  .pills a.active .cnt {{ color:var(--accent); }}

  .clear {{ display:inline-block; padding:.25rem .65rem; border-radius:999px;
            border:1px dashed var(--muted); color:var(--muted); font-size:.74rem;
            text-decoration:none; margin-left:.35rem; }}
  .clear:hover {{ color:#f85149; border-color:#f85149; }}

  .search-form {{ margin-left:auto; }}
  .search-form input {{ background:var(--bg2); color:var(--text); border:1px solid var(--border);
                        border-radius:6px; padding:.4rem .6rem; font-size:.85rem; min-width:240px; }}
  .search-form input:focus {{ outline:none; border-color:var(--accent); }}

  table {{ border-collapse: collapse; width:100%; }}
  th, td {{ padding:.5rem .75rem; border-bottom:1px solid var(--border); text-align:left; vertical-align:top; }}
  th {{ color:var(--muted); font-weight:600; font-size:.72rem; text-transform: uppercase; letter-spacing:.05em; }}
  tr:hover td {{ background: rgba(255,255,255,.02); }}
  a {{ color:var(--accent); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  code {{ background:var(--bg2); padding:.1rem .35rem; border-radius:3px; font-size:.85em; }}
  .muted {{ color:var(--muted); font-size:.85em; }}
  details summary {{ cursor:pointer; color:var(--muted); }}
  details[open] summary {{ color: var(--accent); }}
  details ul {{ list-style:none; padding-left:0; margin:.3rem 0 0; }}

  .type {{ display:inline-block; padding:.05rem .55rem; border-radius:999px; font-size:.7rem;
          border:1px solid; text-transform: uppercase; letter-spacing:.04em; }}
  .type:hover {{ text-decoration:none; opacity:.85; }}
  .t-plan         {{ color:#79c0ff; border-color:#79c0ff; }}
  .t-investigacion{{ color:#d2a8ff; border-color:#d2a8ff; }}
  .t-informe      {{ color:#7ee787; border-color:#7ee787; }}
  .t-reporte      {{ color:#ffa657; border-color:#ffa657; }}
  .t-otro         {{ color:#8b949e; border-color:#8b949e; }}

  .author-cell {{ color:var(--muted) !important; }}
  .author-cell:hover {{ color:var(--accent) !important; text-decoration:none; }}

  @media (max-width:780px) {{
    .toolbar {{ flex-direction:column; align-items:stretch; }}
    .search-form {{ margin-left:0; }}
    .search-form input {{ width:100%; }}
  }}
</style></head><body>
<h1>pergam</h1>
<div class="stats">{showing_note} · postgres-backed · immutable, versioned</div>

<div class="toolbar">
  <div class="filter-group">
    <div class="label">Type</div>
    <div class="pills">{type_filter_links}</div>
  </div>
  <div class="filter-group">
    <div class="label">Author</div>
    <div class="pills">{author_filter_links}</div>
  </div>
  <form class="search-form" method="get">
    <input type="search" name="q" placeholder="search title…" value="{html_lib.escape(text_query or '')}" autocomplete="off">
    <input type="hidden" name="type" value="{html_lib.escape(type_filter or '')}">
    <input type="hidden" name="author" value="{html_lib.escape(author_filter or '')}">
  </form>
  {clear_link}
</div>

<table>
  <thead><tr><th>ID</th><th>Title</th><th>Type</th><th>Author</th><th>Versions</th><th>Latest</th></tr></thead>
  <tbody>
    {body}
  </tbody>
</table>
</body></html>"""
        self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))

    # --------------------------------------------------------------
    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is required", file=sys.stderr)
        return 2

    host = os.environ.get("HOST", DEFAULT_HOST)
    port = int(os.environ.get("PORT", DEFAULT_PORT))

    pool = ConnectionPool(db_url, min_size=1, max_size=8, kwargs={"autocommit": True}, open=True)
    pool.wait(timeout=10.0)
    PergamHandler.pool = pool

    with ThreadingHTTPServer((host, port), PergamHandler) as httpd:
        print(
            f"pergam listening on http://{host}:{port}  (db connected)",
            flush=True,
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down", flush=True)
        finally:
            pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
