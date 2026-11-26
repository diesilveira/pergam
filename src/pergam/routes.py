"""HTTP routing and request validation on top of `http.server`."""

from __future__ import annotations

import http.server
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from pergam.api import index_json, pergam_json
from pergam.cursor import CursorError, IndexCursor
from pergam.index_page import render_index
from pergam.meta import MetaError, parse_meta_change
from pergam.public_url import PublicAddress
from pergam.store import IndexFilters, NewPergam, PergamStore
from pergam.tags import TagError, normalise_tag, parse_tags
from pergam.taxonomy import DEFAULT_TYPES, normalise_type

ID_RE = re.compile(r"^[a-f0-9]{6,16}$")
VERSION_RE = re.compile(r"^v(\d+)$")
NO_STORE = "no-store"
IMMUTABLE = "public, max-age=31536000, immutable"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _first(query: dict[str, list[str]], key: str) -> str | None:
    return query.get(key, [None])[0] or None


def parse_filters(query: dict[str, list[str]]) -> IndexFilters:
    """Index filters from a query string, shared by `/` and `/api/pergams`; raises `CursorError`."""
    token = _first(query, "cursor")
    return IndexFilters(
        type=normalise_type(_first(query, "type") or "") or None,
        author=_first(query, "author"),
        tag=normalise_tag(_first(query, "tag") or "") or None,
        q=(_first(query, "q") or "").strip() or None,
        archived=_first(query, "archived") in ("1", "true"),
        cursor=IndexCursor.decode(token) if token else None,
    )


class PergamHandler(http.server.BaseHTTPRequestHandler):
    server_version = "pergam/2.0"
    store: PergamStore
    public: PublicAddress
    types: tuple[str, ...] = DEFAULT_TYPES

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write(f"[{_utc_now_iso()}] {self.address_string()} - {format % args}\n")

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/pergam":
            self.send_error(404, "Not Found")
            return
        data = self._read_json_object()
        if data is None:
            return

        html = data.get("html", "")
        title = (data.get("title") or "Pergam").strip()
        pergam_type = normalise_type(data.get("type") or "")
        author = (data.get("author") or "").strip()
        existing_id = (data.get("id") or "").strip()

        if not html or not html.strip():
            self.send_error(400, "Missing html")
            return
        if not author:
            self.send_error(400, "Missing author")
            return
        if pergam_type not in self.types:
            self.send_error(400, f"type must be one of {sorted(self.types)}")
            return
        if existing_id and not ID_RE.match(existing_id):
            self.send_error(400, "Invalid id format")
            return

        try:
            tags = None if data.get("tags") is None else parse_tags(data["tags"])
            published = self.store.publish(
                NewPergam(title=title, html=html, type=pergam_type, author=author, tags=tags), existing_id or None
            )
        except TagError as exc:
            self.send_error(400, str(exc))
            return
        response: dict[str, object] = {
            "id": published.id,
            "version": published.version,
            "view_url": self.public.view_url(published.id),
            "title": title,
            "type": pergam_type,
            "author": author,
        }
        if published.tags is not None:
            response["tags"] = published.tags
        self._send(201, "application/json", json.dumps(response).encode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.strip("/")

        if path == "":
            self._render_index(parse_qs(parsed.query))
            return
        if path == "api/pergams":
            self._render_index_json(parse_qs(parsed.query))
            return
        if path == "healthz":
            self._send(200, "application/json", b'{"ok":true}')
            return

        parts = path.split("/")
        if len(parts) == 3 and parts[:2] == ["api", "pergams"] and ID_RE.match(parts[2]):
            self._render_pergam_json(parts[2])
            return
        if len(parts) == 2 and ID_RE.match(parts[0]):
            if parts[1] == "versions":
                self._render_versions_json(parts[0])
                return
            if parts[1] == "meta":
                self._render_meta_json(parts[0])
                return
            if parts[1] in ("view", "raw"):
                self._serve_pergam(parts[0], None, raw=parts[1] == "raw")
                return
        if len(parts) == 3 and parts[2] in ("view", "raw") and ID_RE.match(parts[0]):
            match = VERSION_RE.match(parts[1])
            if match:
                self._serve_pergam(parts[0], int(match.group(1)), raw=parts[2] == "raw")
                return

        self.send_error(404, "Not Found")

    def do_PATCH(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 2 or parts[1] != "meta" or not ID_RE.match(parts[0]):
            self.send_error(404, "Not Found")
            return
        data = self._read_json_object()
        if data is None:
            return
        try:
            change = parse_meta_change(data)
        except MetaError as exc:
            self.send_error(400, str(exc))
            return
        meta = self.store.update_meta(parts[0], change)
        if meta is None:
            self.send_error(404, "Pergam not found")
            return
        self._send(200, "application/json", json.dumps(meta.to_json(parts[0])).encode("utf-8"))

    def do_HEAD(self) -> None:
        """Same status and headers as GET; `_send` and `send_error` skip the body."""
        self.do_GET()

    def do_DELETE(self) -> None:
        self.send_error(405, "Pergams are immutable")

    def _serve_pergam(self, pergam_id: str, version: int | None, *, raw: bool) -> None:
        html_text = self.store.html(pergam_id, version)
        if html_text is None:
            self.send_error(404, "Pergam not found")
            return
        ctype = "text/plain; charset=utf-8" if raw else "text/html; charset=utf-8"
        self._send(200, ctype, html_text.encode("utf-8"), cache_control=NO_STORE if version is None else IMMUTABLE)

    def _render_versions_json(self, pergam_id: str) -> None:
        versions = self.store.versions(pergam_id)
        if not versions:
            self.send_error(404, "Pergam not found")
            return
        out = [
            {
                "version": v.version,
                "title": v.title,
                "type": v.type,
                "author": v.author,
                "bytes": v.bytes,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ]
        self._send(200, "application/json", json.dumps(out).encode("utf-8"))

    def _render_meta_json(self, pergam_id: str) -> None:
        meta = self.store.meta(pergam_id)
        if meta is None:
            self.send_error(404, "Pergam not found")
            return
        self._send(200, "application/json", json.dumps(meta.to_json(pergam_id)).encode("utf-8"))

    def _read_json_object(self) -> dict[str, Any] | None:
        """The request's JSON object body, or None after answering 400/415."""
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            self.send_error(400, "Empty body")
            return None
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self.send_error(415, "Use application/json")
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_error(400, f"Invalid JSON: {exc}")
            return None
        if not isinstance(data, dict):
            self.send_error(400, "JSON body must be an object")
            return None
        return data

    def _filters(self, query: dict[str, list[str]]) -> IndexFilters | None:
        """Parsed filters, or None after answering 400 for a malformed cursor."""
        try:
            return parse_filters(query)
        except CursorError:
            self.send_error(400, "Invalid cursor")
            return None

    def _render_index(self, query: dict[str, list[str]]) -> None:
        filters = self._filters(query)
        if filters is None:
            return
        page = render_index(self.store.index(filters), filters, self.public.display)
        self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))

    def _render_index_json(self, query: dict[str, list[str]]) -> None:
        filters = self._filters(query)
        if filters is None:
            return
        self._send_json(index_json(self.store.index(filters), self.public))

    def _render_pergam_json(self, pergam_id: str) -> None:
        versions = self.store.versions(pergam_id)
        meta = self.store.meta(pergam_id) if versions else None
        if meta is None:
            self.send_error(404, "Pergam not found")
            return
        self._send_json(pergam_json(pergam_id, versions, meta, self.public))

    def _send_json(self, body: object, status: int = 200) -> None:
        self._send(status, "application/json", json.dumps(body).encode("utf-8"))

    def _send(self, status: int, content_type: str, payload: bytes, cache_control: str = NO_STORE) -> None:
        """Version-pinned content never changes, so only those routes pass `IMMUTABLE`."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
