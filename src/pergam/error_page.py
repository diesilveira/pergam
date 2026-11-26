"""Errors as a page for browsers, from `templates/error.html` and `error.css`.

`wants_error_page` decides who gets the page: a `GET` or `HEAD` that prefers
`text/html` (what a browser sends) on a route that is not JSON or a feed. Everyone
else keeps the standard library's error response, so `curl`, `fetch` and the API
clients see exactly what they saw before. The status line is never touched here and
user-controlled text reaches only the escaped body.
"""

from __future__ import annotations

import html as html_lib
import re
from http import HTTPStatus
from pathlib import Path
from string import Template

from pergam.ids import ID_RE
from pergam.layout import HEAD_ASSETS, top_bar

_TEMPLATES = Path(__file__).resolve().parent / "templates"
_PAGE = Template((_TEMPLATES / "error.html").read_text(encoding="utf-8").rstrip("\n"))
_CSS = (_TEMPLATES / "error.css").read_text(encoding="utf-8")

PAGE_METHODS = frozenset({"GET", "HEAD"})
_MACHINE_SUFFIXES = frozenset({"versions", "meta", "collaborators", "export"})

_COPY: dict[int, tuple[str, str]] = {
    400: ("Bad request", "The address or one of its parameters is not valid."),
    403: ("Not allowed", "You are not allowed to do that. Only a pergam's owner and collaborators can change it."),
    404: (
        "Not found",
        "Nothing is published at this address. The link may be mistyped, or the pergam may live on another server.",
    ),
    405: ("Method not allowed", "This address does not accept that kind of request."),
    413: ("Too large", "What was sent is larger than this server accepts."),
}
_CLIENT_ERROR = ("Request not accepted", "The server could not accept this request.")
_SERVER_ERROR = ("Something went wrong", "The server hit a problem handling this request. Try again in a moment.")

esc = html_lib.escape


def _quality(media_range: str) -> tuple[str, float]:
    name, *params = (part.strip() for part in media_range.split(";"))
    for param in params:
        key, _, value = param.partition("=")
        if key.strip().lower() == "q":
            try:
                return name.lower(), float(value)
            except ValueError:
                return name.lower(), 0.0
    return name.lower(), 1.0


def prefers_html(accept: str | None) -> bool:
    """True when `Accept` names `text/html` explicitly and ranks it at least as high as any other type.

    `*/*` alone, as `curl` and `fetch` send, never counts: only a client that asks for HTML gets it.
    """
    qualities = [_quality(part) for part in (accept or "").split(",") if part.strip()]
    html_quality = max((q for name, q in qualities if name == "text/html"), default=0.0)
    return html_quality > 0 and html_quality >= max(q for _, q in qualities)


def is_machine_route(path: str) -> bool:
    """Routes that answer with JSON, a feed or a health check, never with a page."""
    parts = path.strip("/").split("/")
    if parts[0] in {"api", "healthz", "feed.atom"}:
        return True
    return len(parts) == 2 and ID_RE.match(parts[0]) is not None and parts[1] in _MACHINE_SUFFIXES


def wants_error_page(method: str | None, path: str, accept: str | None) -> bool:
    return method in PAGE_METHODS and not is_machine_route(path) and prefers_html(accept)


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def render_error(status: int, message: str, host: str | None) -> str:
    """The page for `status`; `message` is the server's own one-line reason, escaped here."""
    phrase = _phrase(status)
    heading, explanation = _COPY.get(status) or (_SERVER_ERROR if status >= 500 else _CLIENT_ERROR)
    detail = f'<p class="error__detail"><code>{esc(message)}</code></p>' if message != phrase else ""
    return _PAGE.substitute(
        head_assets=HEAD_ASSETS,
        css=_CSS,
        top_bar=top_bar(host=host),
        page_title=esc(f"{status} {heading} · pergam"),
        code_line=esc(f"{status} · {phrase.upper()}"),
        heading=esc(heading),
        explanation=esc(explanation),
        detail=detail,
    )
