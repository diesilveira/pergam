"""HTTP routing and request validation on top of `http.server`."""

from __future__ import annotations

import http.server
import json
import re
import sys
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from pergam.access import Access, CollaboratorError, Forbidden, parse_collaborators
from pergam.api import index_json, pergam_json
from pergam.cursor import CursorError, IndexCursor
from pergam.diff import Section, TooLargeToDiff, check_size, diff_sections
from pergam.diff_page import render_diff
from pergam.error_page import render_error, varies_by_accept, wants_error_page
from pergam.feed import CONTENT_TYPE as ATOM_CONTENT_TYPE
from pergam.feed import render_feed
from pergam.identity import PROXY_USER_HEADER, IdentityError, Writer, parse_author, single_proxy_user
from pergam.ids import ID_RE
from pergam.index_page import render_index
from pergam.lint import LINT_CONCURRENCY, LINT_MAX_BODY_BYTES, lint_html
from pergam.limits import DEFAULT_MAX_BODY_MB, MIB
from pergam.meta import MetaError, parse_meta_change
from pergam.notes import NoteError, parse_note
from pergam.public_url import PublicAddress
from pergam.search import QueryError, parse_query
from pergam.store import ImportConflict, IndexFilters, NewPergam, PergamStore
from pergam.tags import TagError, normalise_tag, parse_tags
from pergam.taxonomy import DEFAULT_TYPES, normalise_type
from pergam.text import extract_text
from pergam.transfer import TransferError, export_json, parse_import
from pergam.static_files import FILES as STATIC_FILES
from pergam.view_page import FRAME_POLICY, FramePolicy, framed, render_view

VERSION_RE = re.compile(r"^v(\d+)$")
RANGE_RE = re.compile(r"^v([1-9]\d*)\.\.v([1-9]\d*)$")
CONTENT_TYPES = {
    "content": "text/html; charset=utf-8",
    "raw": "text/plain; charset=utf-8",
    "text": "text/markdown; charset=utf-8",
}
LATEST_KINDS = frozenset({"raw", "text"})
NO_STORE = "no-store"
IMMUTABLE = "public, max-age=31536000, immutable"
DERIVED = "public, max-age=300"
NOSNIFF = (("X-Content-Type-Options", "nosniff"),)


_LOG_ESCAPES = {code: f"\\x{code:02x}" for code in (*range(0x20), *range(0x7F, 0xA0))} | {ord("\\"): "\\\\"}


def escape_log(text: str) -> str:
    """`text` with C0 and C1 control characters as `\\xNN` and backslashes doubled, as `http.server` logs them."""
    return text.translate(_LOG_ESCAPES)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _first(query: dict[str, list[str]], key: str) -> str | None:
    return query.get(key, [None])[0] or None


def parse_filters(query: dict[str, list[str]]) -> IndexFilters:
    """Index filters from a query string, shared by `/` and `/api/pergams`; raises `CursorError` or `QueryError`."""
    token = _first(query, "cursor")
    return IndexFilters(
        type=normalise_type(_first(query, "type") or "") or None,
        author=_first(query, "author"),
        tag=normalise_tag(_first(query, "tag") or "") or None,
        q=parse_query(_first(query, "q")),
        archived=_first(query, "archived") in ("1", "true"),
        cursor=IndexCursor.decode(token) if token else None,
    )


class PergamHandler(http.server.BaseHTTPRequestHandler):
    server_version = "pergam/2.0"
    store: PergamStore
    public: PublicAddress
    types: tuple[str, ...] = DEFAULT_TYPES
    max_body_bytes: int = DEFAULT_MAX_BODY_MB * MIB
    trust_proxy_user: bool = False
    frame: FramePolicy = FRAME_POLICY
    lint_slots = threading.BoundedSemaphore(LINT_CONCURRENCY)
    vary_accept = False

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        line = escape_log(format % args)
        sys.stderr.write(f"[{_utc_now_iso()}] {self.address_string()} - {line}\n")

    def send_response(self, code: int, message: str | None = None) -> None:
        """The status line always carries the standard reason phrase.

        `send_error` messages can name users and ids, which belong only in the
        HTML-escaped body: in a header line a stored CR LF would split the
        response, and a character outside Latin-1 would drop the connection.
        """
        super().send_response(code)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """The standard error response, with the standard library's body swapped for a page when a browser asks.

        Only the body differs: status line, headers and logging stay the library's, so
        API clients, `HEAD` and cache rules see no change (see `error_page.wants_error_page`).
        Where the body depends on `Accept`, the response says so with `Vary: Accept`.
        """
        headers = getattr(self, "headers", None)
        path = urlparse(getattr(self, "path", "")).path
        if headers is None or not varies_by_accept(self.command, path):
            super().send_error(code, message, explain)
            return
        self.vary_accept = True
        try:
            if wants_error_page(self.command, path, headers.get("Accept")):
                shown = message if message is not None else self.responses.get(code, ("Error",))[0]
                host = self.public.display if hasattr(self, "public") else None
                self.error_message_format = render_error(code, shown, host).replace("%", "%%")
            super().send_error(code, message, explain)
        finally:
            self.vary_accept = False
            self.__dict__.pop("error_message_format", None)

    def end_headers(self) -> None:
        if self.vary_accept:
            self.send_header("Vary", "Accept")
        super().end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/pergam":
            self._publish()
        elif path == "/import":
            self._import()
        elif path == "/lint":
            self._lint()
        else:
            self.send_error(404, "Not Found")

    def _writer(self) -> Writer | None:
        """The writer of this request, or None after answering 401/400 for a missing or malformed proxy user."""
        if not self.trust_proxy_user:
            return Writer()
        try:
            return Writer(single_proxy_user(self.headers.get_all(PROXY_USER_HEADER) or []))
        except IdentityError as exc:
            self.send_error(exc.status, str(exc))
            return None

    def _publish(self) -> None:
        writer = self._writer()
        if writer is None:
            return
        data = self._read_json_object()
        if data is None:
            return

        html = data.get("html", "")
        title = (data.get("title") or "Pergam").strip()
        pergam_type = normalise_type(data.get("type") or "")
        try:
            body_author = parse_author(data.get("author"))
        except ValueError as exc:
            self.send_error(400, str(exc))
            return
        author = writer.user or body_author
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
            note = parse_note(data.get("note"))
            tags = None if data.get("tags") is None else parse_tags(data["tags"])
            published = self.store.publish(
                NewPergam(title=title, html=html, type=pergam_type, author=author, tags=tags, note=note),
                existing_id or None,
                writer.user,
            )
        except (NoteError, TagError) as exc:
            self.send_error(400, str(exc))
            return
        except Forbidden as exc:
            self.send_error(403, str(exc))
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
        if note:
            response["note"] = note
        self._send(201, "application/json", json.dumps(response).encode("utf-8"))

    def _lint(self) -> None:
        """Findings for the body's `html`; other fields are ignored, so a publish payload can be linted as is.

        Parsing takes many times the body in memory, and `/lint` is anonymous, so at
        most `LINT_CONCURRENCY` run at once; a request beyond that is 429 at once.
        """
        if not self.lint_slots.acquire(blocking=False):
            self.send_error(429, "Too many lints at once, retry in a moment")
            return
        try:
            self._lint_body()
        finally:
            self.lint_slots.release()

    def _lint_body(self) -> None:
        data = self._read_json_object(LINT_MAX_BODY_BYTES)
        if data is None:
            return
        html = data.get("html")
        if not isinstance(html, str) or not html.strip():
            self.send_error(400, "Missing html")
            return
        own = tuple(origin for origin in (self.public.origin, self.headers.get("Host")) if origin)
        self._send_json(lint_html(html, own).to_json())

    def _import(self) -> None:
        """201 when any version was written, 200 when every one was already here, 409 on a conflict."""
        writer = self._writer()
        if writer is None:
            return
        data = self._read_json_object()
        if data is None:
            return
        try:
            result = self.store.import_pergam(parse_import(data, self.types), writer.user)
        except (TransferError, TagError) as exc:
            self.send_error(400, str(exc))
            return
        except Forbidden as exc:
            self.send_error(403, str(exc))
            return
        except ImportConflict as exc:
            self.send_error(409, str(exc))
            return
        pergam_id = data["id"]
        body = {
            "id": pergam_id,
            "imported": result.imported,
            "skipped": result.skipped,
            "origin": result.origin.to_json() if result.origin else None,
            "view_url": self.public.view_url(pergam_id),
        }
        self._send_json(body, 201 if result.imported else 200)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.strip("/")

        if path == "":
            self._render_index(parse_qs(parsed.query))
            return
        if path == "feed.atom":
            self._render_feed(parse_qs(parsed.query))
            return
        if path == "api/pergams":
            self._render_index_json(parse_qs(parsed.query))
            return
        static = STATIC_FILES.get(parsed.path)
        if static is not None:
            cache_control = IMMUTABLE if static.immutable_for(_first(parse_qs(parsed.query), "v")) else DERIVED
            self._send(200, static.content_type, static.body, cache_control=cache_control, headers=NOSNIFF)
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
            if parts[1] == "collaborators":
                self._render_collaborators(parts[0])
                return
            if parts[1] == "export":
                self._render_export(parts[0])
                return
            if parts[1] == "view":
                self._render_view(parts[0], None)
                return
            if parts[1] in LATEST_KINDS:
                self._serve_pergam(parts[0], None, parts[1])
                return
        if len(parts) == 3 and parts[1] == "diff" and ID_RE.match(parts[0]):
            self._render_diff(parts[0], parts[2])
            return
        if len(parts) == 3 and ID_RE.match(parts[0]):
            match = VERSION_RE.match(parts[1])
            if match and parts[2] == "view":
                self._render_view(parts[0], int(match.group(1)))
                return
            if match and parts[2] in CONTENT_TYPES:
                self._serve_pergam(parts[0], int(match.group(1)), parts[2])
                return

        self.send_error(404, "Not Found")

    def do_PATCH(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 2 or parts[1] != "meta" or not ID_RE.match(parts[0]):
            self.send_error(404, "Not Found")
            return
        writer = self._writer()
        if writer is None:
            return
        data = self._read_json_object()
        if data is None:
            return
        try:
            meta = self.store.update_meta(parts[0], parse_meta_change(data), writer.user)
        except MetaError as exc:
            self.send_error(400, str(exc))
            return
        except Forbidden as exc:
            self.send_error(403, str(exc))
            return
        if meta is None:
            self.send_error(404, "Pergam not found")
            return
        self._send(200, "application/json", json.dumps(meta.to_json(parts[0])).encode("utf-8"))

    def do_PUT(self) -> None:
        """`PUT /{id}/collaborators` replaces the list, by the owner only.

        Without trusted identity there is no owner to check, so the list cannot be
        edited at all (409): otherwise anyone could seed it before the flag is on.
        """
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 2 or parts[1] != "collaborators" or not ID_RE.match(parts[0]):
            self.send_error(404, "Not Found")
            return
        if not self.trust_proxy_user:
            self.send_error(409, "Collaborators can only be edited when the server trusts its proxy (TRUST_PROXY_USER)")
            return
        writer = self._writer()
        if writer is None:
            return
        data = self._read_json_object()
        if data is None:
            return
        try:
            access = self.store.set_collaborators(parts[0], parse_collaborators(data), writer.user or "")
        except CollaboratorError as exc:
            self.send_error(400, str(exc))
            return
        except Forbidden as exc:
            self.send_error(403, str(exc))
            return
        if access is None:
            self.send_error(404, "Pergam not found")
            return
        self._send_json(access.to_json(parts[0]))

    def do_HEAD(self) -> None:
        """Same status and headers as GET; `_send` and `send_error` skip the body."""
        self.do_GET()

    def do_DELETE(self) -> None:
        self.send_error(405, "Pergams are immutable")

    def _serve_pergam(self, pergam_id: str, version: int | None, kind: str) -> None:
        """`kind` is `content` (the HTML plus the frame bridge), `raw` (the HTML as posted) or `text` (Markdown).

        Only `/raw` is the stored bytes alone, so only it is cached for good; `/content`
        carries the bridge tag and the frame's headers, which may change with an upgrade.
        """
        html_text = self.store.html(pergam_id, version)
        if html_text is None:
            self.send_error(404, "Pergam not found")
            return
        if kind == "text":
            body = extract_text(html_text).markdown
        elif kind == "content":
            body = framed(html_text)
        else:
            body = html_text
        cache_control = NO_STORE if version is None else IMMUTABLE if kind == "raw" else DERIVED
        headers = self.frame.content_headers if kind == "content" else NOSNIFF
        self._send(200, CONTENT_TYPES[kind], body.encode("utf-8"), cache_control=cache_control, headers=headers)

    def _render_view(self, pergam_id: str, version: int | None) -> None:
        """The view page names how many versions exist, so it changes with every new one: never cached."""
        versions = self.store.versions(pergam_id)
        meta = self.store.meta(pergam_id) if versions else None
        shown = versions[0].version if versions and version is None else version
        if meta is None or all(info.version != shown for info in versions):
            self.send_error(404, "Pergam not found")
            return
        access = self.store.access(pergam_id)
        page = render_view(pergam_id, versions, meta, shown, self.frame, access=access, editable=self._offers_edits(access))
        self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))

    def _offers_edits(self, access: Access | None) -> bool:
        """Whether the view page shows the pin, archive and tag controls, which `PATCH /{id}/meta`.

        Without trusted identity anyone who reaches the server may already PATCH, so they
        are shown. With it they are shown to the owner and the collaborators, and to a
        request whose user is unknown, which the page then tells why the server refused.
        """
        if not self.trust_proxy_user or access is None:
            return True
        try:
            user = single_proxy_user(self.headers.get_all(PROXY_USER_HEADER) or [])
        except IdentityError:
            return True
        return access.may_edit(user)

    def _render_diff(self, pergam_id: str, version_range: str) -> None:
        """`v{a}..v{b}` in either order, `b` read as the newer side; 400 when malformed, 404 when unknown."""
        match = RANGE_RE.match(version_range)
        if not match:
            self.send_error(400, "Range must look like v2..v5")
            return
        versions = self.store.versions(pergam_id)
        by_number = {info.version: info for info in versions}
        old, new = (by_number.get(int(number)) for number in match.groups())
        if old is None or new is None:
            self.send_error(404, "Pergam or version not found")
            return
        sections = self._diff(pergam_id, old.version, new.version, old.bytes, new.bytes)
        page = render_diff(pergam_id, old, new, sections, versions)
        self._send(200, "text/html; charset=utf-8", page.encode("utf-8"), cache_control=DERIVED)

    def _diff(self, pergam_id: str, old: int, new: int, *html_bytes: int) -> list[Section] | TooLargeToDiff:
        """The sections, or why the versions are too large to compare; oversized HTML is never read."""
        try:
            check_size(*html_bytes)
            return diff_sections(*(self.store.html(pergam_id, version) or "" for version in (old, new)))
        except TooLargeToDiff as exc:
            return exc

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
                "note": v.note,
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

    def _render_collaborators(self, pergam_id: str) -> None:
        access = self.store.access(pergam_id)
        if access is None:
            self.send_error(404, "Pergam not found")
            return
        self._send_json(access.to_json(pergam_id))

    def _render_export(self, pergam_id: str) -> None:
        versions = self.store.export(pergam_id)
        meta = self.store.meta(pergam_id) if versions else None
        if meta is None:
            self.send_error(404, "Pergam not found")
            return
        self._send_json(export_json(pergam_id, versions, meta, self.public))

    def _read_json_object(self, limit: int | None = None) -> dict[str, Any] | None:
        """The request's JSON object body, at most `limit` bytes (default `max_body_bytes`), or None after answering 400/413/415."""
        limit = min(limit or self.max_body_bytes, self.max_body_bytes)
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return None
        if length <= 0:
            self.send_error(400, "Empty body")
            return None
        if length > limit:
            self.send_error(413, f"Body larger than {limit // MIB} MB")
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
        """Parsed filters, or None after answering 400 for a malformed cursor or an overlong `q`."""
        try:
            return parse_filters(query)
        except CursorError:
            self.send_error(400, "Invalid cursor")
        except QueryError as exc:
            self.send_error(400, str(exc))
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

    def _render_feed(self, query: dict[str, list[str]]) -> None:
        tag = normalise_tag(_first(query, "tag") or "") or None
        author = (_first(query, "author") or "").strip() or None
        body = render_feed(self.store.feed(tag, author), self.public, tag, author)
        self._send(200, ATOM_CONTENT_TYPE, body)

    def _send_json(self, body: object, status: int = 200) -> None:
        self._send(status, "application/json", json.dumps(body).encode("utf-8"))

    def _send(
        self,
        status: int,
        content_type: str,
        payload: bytes,
        cache_control: str = NO_STORE,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Version-pinned content never changes, so those routes pass `IMMUTABLE`.

        Content derived from pinned versions (`/text`, `/diff`) changes when the
        extractor improves, so it gets the short `DERIVED` lifetime instead.
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
