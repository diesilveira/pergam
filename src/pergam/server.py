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
            (total_pergams, total_authors, total_versions, total_bytes,
             new_pergams_7d, new_versions_7d) = conn.execute(
                """
                SELECT
                  (SELECT count(*) FROM pergams_latest),
                  (SELECT count(DISTINCT author) FROM pergams_latest),
                  (SELECT count(*) FROM pergams),
                  (SELECT COALESCE(SUM(bytes), 0) FROM pergams),
                  (SELECT count(*) FROM pergams_latest WHERE created_at >= now() - interval '7 days'),
                  (SELECT count(*) FROM pergams         WHERE created_at >= now() - interval '7 days')
                """
            ).fetchone()

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

        # ---- humanise sizes & dates ----
        def human_bytes(n: int) -> tuple[str, str]:
            if n < 1024:           return (f"{n}", "B")
            if n < 1024 * 1024:    return (f"{n/1024:.0f}", "KB")
            if n < 1024**3:        return (f"{n/(1024*1024):.1f}", "MB")
            return (f"{n/(1024**3):.2f}", "GB")

        from datetime import datetime as _dt, timezone as _tz
        _now = _dt.now(_tz.utc)
        def human_age(ts) -> str:
            delta = _now - ts
            secs = int(delta.total_seconds())
            if secs < 60:      return "just now"
            if secs < 3600:    return f"{secs // 60}m ago"
            if secs < 86400:
                hrs = secs // 3600
                return "today" if hrs < 12 else f"{hrs}h ago"
            days = secs // 86400
            if days < 30:      return f"{days}d ago"
            if days < 365:     return f"{days // 30}mo ago"
            return f"{days // 365}y ago"

        # ---- avatar gradient picked deterministically from author string ----
        _avatar_grads = [
            ("#f85149", "#f0d28a"),  # red → yellow
            ("#5ee7ff", "#7aa2ff"),  # cyan → blue
            ("#f0d28a", "#f59f5a"),  # yellow → orange
            ("#56d364", "#5ee7ff"),  # green → cyan
            ("#b692ff", "#7aa2ff"),  # purple → blue
            ("#f59f5a", "#f85149"),  # orange → red
        ]
        def avatar(author: str) -> str:
            # simple deterministic hash
            h = 0
            for ch in author:
                h = (h * 131 + ord(ch)) & 0xFFFFFFFF
            a, b = _avatar_grads[h % len(_avatar_grads)]
            local = author.split("@")[0] if "@" in author else author
            initials = "".join(p[0] for p in re.split(r"[.\-_+ ]+", local) if p)[:2].upper() or "?"
            return (
                f'<span class="avatar" aria-hidden="true" '
                f'style="background:linear-gradient(135deg,{a},{b})">{html_lib.escape(initials)}</span>'
            )

        # ---- list rows ----
        # Each row is a <div> (not <a>) because it contains interactive
        # children (<details>, inner <a>s); the HTML parser would otherwise
        # reparent them out of an enclosing <a>. A click handler below
        # navigates to /{id}/view when the click is outside any inner anchor
        # or details element.
        body_rows = []
        for r in rows:
            (pid, ver, title, ptype, author, created, _bytes, totalv) = r
            author_href = html_lib.escape(link(override_author=author, clear_author=False))
            type_href = html_lib.escape(link(override_type=ptype, clear_type=False))
            short_author = author.split("@")[0] if "@" in author else author
            versions_block = (
                f'<details class="vers"><summary>v{ver}</summary>'
                f'<div class="vers__list">'
                + " · ".join(
                    f'<a href="/{pid}/v{i}/view">v{i}</a>'
                    for i in range(totalv, 0, -1)
                )
                + f' · <a href="/{pid}/versions" class="muted">json</a>'
                + "</div></details>"
            )
            body_rows.append(
                f'<div class="row" data-href="/{pid}/view" tabindex="0" role="link">'
                f'  <a class="row__title" href="/{pid}/view">{html_lib.escape(title)}</a>'
                f'  <code class="row__id">{html_lib.escape(pid)}</code>'
                f'  <a class="type t-{html_lib.escape(ptype)}" '
                f'     href="{type_href}" title="filter by type">{html_lib.escape(ptype)}</a>'
                f'  <a class="row__author" href="{author_href}" title="filter by author">'
                f'    {avatar(author)}<span class="row__author-name">{html_lib.escape(short_author)}</span>'
                f'  </a>'
                f'  <span class="row__version">{versions_block}</span>'
                f'  <time class="row__date" datetime="{created.isoformat()}" '
                f'        title="{created.strftime("%Y-%m-%d %H:%M UTC")}">{human_age(created)}</time>'
                f'</div>'
            )
        body = "\n".join(body_rows) or (
            '<div class="empty">No pergams match. '
            '<a href="https://github.com/diesilveira/pergam" target="_blank" rel="noopener">'
            'POST one to <code>/pergam</code></a> to get started.</div>'
        )

        showing = len(rows)
        filtered = bool(type_filter or author_filter or text_query)
        showing_note = (
            f"showing {showing} of {total_pergams}"
            if filtered and showing != total_pergams
            else f"{total_pergams} pergams · {total_authors} authors · {total_versions} versions"
        )

        # stat-card delta helper
        def delta(n: int) -> str:
            return f'<span class="stat__delta">▲{n}</span>' if n > 0 else ""

        bytes_val, bytes_unit = human_bytes(total_bytes)

        # topbar pieces
        public_host = os.environ.get("PUBLIC_HOST", "localhost")
        public_port = os.environ.get("PUBLIC_PORT", str(self.server.server_address[1]))
        host_display = public_host if public_port in ("80", "443") else f"{public_host}:{public_port}"

        html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>pergam · index</title>
<style>
  :root {{
    --bg:#0b0d12; --surface:#11151c; --surface-hi:#161b25; --border:#1f2632;
    --text:#e6edf3; --muted:#8b96a7; --muted-2:#6b7686;
    --accent:#5ee7ff; --accent-soft:rgba(94,231,255,.12);
    --ok:#56d364; --warn:#f0d28a; --danger:#f85149;
    --t-plan:#f0d28a; --t-investigacion:#b692ff; --t-informe:#56d364;
    --t-reporte:#5ee7ff; --t-otro:#8b96a7;
  }}
  * {{ box-sizing:border-box; }}
  html, body {{ background:var(--bg); color:var(--text); margin:0;
                font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                font-size:14px; line-height:1.5; }}
  a {{ color:inherit; text-decoration:none; }}
  code, .mono {{ font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace; }}

  /* ── topbar ─────────────────────────────────────────────────────────── */
  .topbar {{ display:flex; align-items:center; gap:.75rem; padding:.55rem 1rem;
             background:#0e1218; border-bottom:1px solid var(--border); position:sticky; top:0; z-index:10; }}
  .topbar__logo {{ display:flex; align-items:center; gap:.45rem; font-weight:600; }}
  .topbar__logo .blocks {{ color:var(--accent); font-family:'JetBrains Mono',monospace; font-weight:700; letter-spacing:-2px; }}
  .topbar__pill {{ font-size:.65rem; font-weight:700; letter-spacing:.08em;
                   color:var(--accent); background:var(--accent-soft);
                   border:1px solid rgba(94,231,255,.4); border-radius:999px;
                   padding:.15rem .55rem; }}
  .topbar__url {{ display:inline-flex; align-items:center; gap:.4rem;
                  background:var(--bg); border:1px solid var(--border); border-radius:5px;
                  padding:.25rem .55rem; font-size:.78rem; color:var(--text);
                  font-family:'JetBrains Mono',monospace; min-width:0; max-width:32ch;
                  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .topbar__dot {{ width:6px; height:6px; border-radius:50%; background:var(--ok); flex:0 0 6px;
                  box-shadow:0 0 6px rgba(86,211,100,.6); }}
  .topbar__nav {{ margin-left:auto; display:flex; align-items:center; gap:1rem; font-size:.82rem; }}
  .topbar__nav a {{ color:var(--muted-2); }}
  .topbar__nav a:hover {{ color:var(--text); }}
  .topbar__nav a.active {{ color:var(--accent); font-weight:600; }}

  /* ── layout ─────────────────────────────────────────────────────────── */
  .wrap {{ max-width:1200px; margin:0 auto; padding:1.5rem 1.25rem 3rem; }}
  .title-row h1 {{ margin:0; font-size:1.4rem; font-weight:700; letter-spacing:-.01em; }}
  .title-row .sub {{ color:var(--muted); font-size:.82rem; margin-top:.15rem; }}

  /* ── stat cards ─────────────────────────────────────────────────────── */
  .stats-grid {{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr));
                 gap:.75rem; margin:1.1rem 0 1.4rem; }}
  .stat {{ background:var(--surface); border:1px solid var(--border); border-radius:8px;
           padding:.7rem .85rem; position:relative; }}
  .stat__value {{ font-size:1.55rem; font-weight:700; letter-spacing:-.01em; line-height:1.1; }}
  .stat__value .unit {{ font-size:.85rem; color:var(--muted); font-weight:600; margin-left:.15rem; }}
  .stat__label {{ font-size:.62rem; font-weight:700; letter-spacing:.08em;
                  color:var(--muted); margin-top:.3rem; }}
  .stat__delta {{ position:absolute; top:.7rem; right:.85rem;
                  color:var(--ok); font-size:.7rem; font-weight:600; }}

  /* ── filters row ────────────────────────────────────────────────────── */
  .toolbar {{ display:flex; gap:1rem; flex-wrap:wrap; align-items:center;
              padding:.75rem .9rem; background:var(--surface); border:1px solid var(--border);
              border-radius:8px; margin-bottom:1rem; }}
  .filter-group {{ display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; min-width:0; }}
  .filter-group .label {{ color:var(--muted); font-size:.65rem; font-weight:700;
                          letter-spacing:.08em; text-transform:uppercase; }}
  .pills {{ display:flex; gap:.3rem; flex-wrap:wrap; }}
  .pills a {{ display:inline-flex; align-items:center; gap:.3rem;
              padding:.18rem .55rem; border-radius:999px;
              border:1px solid var(--border); color:var(--muted);
              background:var(--bg); font-size:.74rem; transition:all .12s ease; }}
  .pills a:hover {{ border-color:var(--accent); color:var(--accent); }}
  .pills a.active {{ border-color:var(--accent); color:var(--accent);
                     background:var(--accent-soft); font-weight:600; }}
  .pills a .cnt {{ color:var(--muted-2); font-family:'JetBrains Mono',monospace; font-size:.7em; }}
  .pills a.active .cnt {{ color:var(--accent); }}

  .clear {{ display:inline-block; padding:.18rem .55rem; border-radius:999px;
            border:1px dashed var(--muted-2); color:var(--muted); font-size:.7rem; }}
  .clear:hover {{ color:var(--danger); border-color:var(--danger); }}

  .search-form {{ margin-left:auto; }}
  .search-form input {{ background:var(--bg); color:var(--text); border:1px solid var(--border);
                        border-radius:6px; padding:.35rem .6rem; font-size:.8rem; min-width:240px;
                        font-family:inherit; }}
  .search-form input::placeholder {{ color:var(--muted-2); }}
  .search-form input:focus {{ outline:none; border-color:var(--accent); }}

  /* ── list panel ─────────────────────────────────────────────────────── */
  .list {{ background:var(--surface); border:1px solid var(--border); border-radius:8px;
           overflow:hidden; }}
  .row {{ display:grid;
          grid-template-columns: minmax(0,1fr) auto auto auto auto auto;
          align-items:center; gap:.9rem;
          padding:.7rem .95rem; border-bottom:1px solid var(--border);
          transition:background .12s ease; cursor:pointer; }}
  .row:last-child {{ border-bottom:none; }}
  .row:hover {{ background:var(--surface-hi); }}
  .row__title {{ font-weight:600; color:var(--text); min-width:0;
                 overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .row__id {{ color:var(--muted-2); font-size:.72rem; background:var(--bg);
              border:1px solid var(--border); border-radius:4px;
              padding:.1rem .4rem; }}

  .type {{ display:inline-block; padding:.1rem .55rem; border-radius:4px;
          font-family:'JetBrains Mono',monospace; font-size:.68rem; font-weight:600;
          letter-spacing:.02em; cursor:pointer; user-select:none; }}
  .t-plan          {{ color:var(--t-plan);          background:rgba(240,210,138,.1);  border:1px solid rgba(240,210,138,.4); }}
  .t-investigacion {{ color:var(--t-investigacion); background:rgba(182,146,255,.1);  border:1px solid rgba(182,146,255,.4); }}
  .t-informe       {{ color:var(--t-informe);       background:rgba(86,211,100,.1);   border:1px solid rgba(86,211,100,.4); }}
  .t-reporte       {{ color:var(--t-reporte);       background:rgba(94,231,255,.1);   border:1px solid rgba(94,231,255,.4); }}
  .t-otro          {{ color:var(--t-otro);          background:rgba(139,150,167,.1);  border:1px solid rgba(139,150,167,.4); }}

  .row__author {{ display:inline-flex; align-items:center; gap:.45rem;
                  color:var(--muted); font-size:.78rem; cursor:pointer; }}
  .row__author:hover {{ color:var(--text); }}
  .row__author-name {{ max-width:14ch; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .avatar {{ width:22px; height:22px; border-radius:50%;
             display:inline-flex; align-items:center; justify-content:center;
             font-family:'JetBrains Mono',monospace; font-size:.6rem; font-weight:700;
             color:#0b0d12; flex:0 0 22px; }}

  .row__version .vers > summary {{ cursor:pointer; list-style:none;
                                   color:var(--accent); font-family:'JetBrains Mono',monospace;
                                   font-size:.78rem; font-weight:700; }}
  .row__version .vers > summary::-webkit-details-marker {{ display:none; }}
  .row__version .vers__list {{ margin-top:.35rem; font-size:.72rem;
                                color:var(--muted); font-family:'JetBrains Mono',monospace; }}
  .row__version .vers__list a {{ color:var(--accent); }}
  .row__version .vers__list a:hover {{ text-decoration:underline; }}

  .row__date {{ color:var(--muted); font-size:.78rem; min-width:6ch; text-align:right; }}

  .empty {{ padding:2rem; text-align:center; color:var(--muted); font-size:.9rem; }}
  .empty a {{ color:var(--accent); }}
  .empty a:hover {{ text-decoration:underline; }}
  .empty code {{ background:var(--bg); border:1px solid var(--border);
                 padding:.05rem .35rem; border-radius:4px; font-size:.85em; }}

  /* ── responsive ─────────────────────────────────────────────────────── */
  @media (max-width: 900px) {{
    .stats-grid {{ grid-template-columns:repeat(2, minmax(0,1fr)); }}
    .row {{ grid-template-columns: 1fr auto;
            grid-template-areas:
              "title   date"
              "type    version"
              "author  id"; }}
    .row__title  {{ grid-area:title; }}
    .row__date   {{ grid-area:date; text-align:right; }}
    .type        {{ grid-area:type;  justify-self:start; }}
    .row__version{{ grid-area:version; justify-self:end; }}
    .row__author {{ grid-area:author; }}
    .row__id     {{ grid-area:id;    justify-self:end; }}
    .search-form {{ margin-left:0; width:100%; }}
    .search-form input {{ width:100%; min-width:0; }}
    .topbar__url {{ display:none; }}
  }}
  @media (max-width: 520px) {{
    .stats-grid {{ grid-template-columns:repeat(2, minmax(0,1fr)); }}
    .topbar__pill {{ display:none; }}
  }}
</style></head><body>

<header class="topbar">
  <span class="topbar__logo"><span class="blocks">▮▮</span> pergam</span>
  <span class="topbar__pill">SELF-HOSTED</span>
  <span class="topbar__url"><span class="topbar__dot"></span>{html_lib.escape(host_display)}</span>
  <nav class="topbar__nav">
    <a href="/" class="active">Index</a>
    <a href="https://github.com/diesilveira/pergam" target="_blank" rel="noopener">API</a>
  </nav>
</header>

<main class="wrap">
  <div class="title-row">
    <h1>Index</h1>
    <div class="sub">{showing_note}</div>
  </div>

  <section class="stats-grid" aria-label="Instance stats">
    <div class="stat">
      <div class="stat__value">{total_pergams}</div>
      <div class="stat__label">PERGAMS</div>
      {delta(new_pergams_7d)}
    </div>
    <div class="stat">
      <div class="stat__value">{total_authors}</div>
      <div class="stat__label">AUTHORS</div>
    </div>
    <div class="stat">
      <div class="stat__value">{total_versions}</div>
      <div class="stat__label">VERSIONS</div>
      {delta(new_versions_7d)}
    </div>
    <div class="stat">
      <div class="stat__value">{bytes_val}<span class="unit">{bytes_unit}</span></div>
      <div class="stat__label">PAYLOAD</div>
    </div>
  </section>

  <div class="toolbar">
    <div class="filter-group">
      <span class="label">Type</span>
      <div class="pills">{type_filter_links}</div>
    </div>
    <div class="filter-group">
      <span class="label">Author</span>
      <div class="pills">{author_filter_links}</div>
    </div>
    <form class="search-form" method="get">
      <input type="search" name="q" placeholder="search title…" value="{html_lib.escape(text_query or '')}" autocomplete="off">
      <input type="hidden" name="type" value="{html_lib.escape(type_filter or '')}">
      <input type="hidden" name="author" value="{html_lib.escape(author_filter or '')}">
    </form>
    {clear_link}
  </div>

  <section class="list" aria-label="Pergams">
    {body}
  </section>
</main>

<script>
  // Make the whole row navigable, but defer to inner <a> and <details>
  // so the type pill, author chip, and version dropdown still work.
  document.querySelectorAll('.row[data-href]').forEach(row => {{
    const go = () => window.location.assign(row.getAttribute('data-href'));
    row.addEventListener('click', ev => {{
      if (ev.target.closest('a, details, summary, input, button')) return;
      go();
    }});
    row.addEventListener('keydown', ev => {{
      if (ev.key === 'Enter' && ev.target === row) go();
    }});
  }});
</script>
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
