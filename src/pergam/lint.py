"""Static checks of a document before it is published, for `POST /lint`. Nothing is stored.

Each finding names its rule, a severity, and the line and column (both from 1)
of the tag it is about:

- `external-resource`: a tag that loads something from another server (script,
  stylesheet, image, frame, media, font or `@import` in CSS). The sandbox's CSP
  blocks it, so it is an error; scripts and styles from jsDelivr are allowed for
  documents published before `/static/` served Mermaid, so those are warnings.
- `missing-anchor`: a link to `#name` with no element whose `id` (or `<a name>`) is `name`.
- `mermaid-empty`: a `class="mermaid"` block with no diagram text.
- `mermaid-unknown-diagram`: one whose first statement is not a diagram type Mermaid knows.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from heapq import merge
from itertools import islice
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urlparse

MAX_FINDINGS = 500
LINT_MAX_BODY_BYTES = 2 * 1024 * 1024
ALLOWED_CDN_HOST = "cdn.jsdelivr.net"
MERMAID_DIAGRAMS = frozenset(
    {
        "architecture", "architecture-beta", "block", "block-beta", "C4Component", "C4Container", "C4Context",
        "C4Deployment", "C4Dynamic", "classDiagram", "classDiagram-v2", "erDiagram", "flowchart", "flowchart-elk",
        "gantt", "gitGraph", "graph", "info", "journey", "kanban", "mindmap", "packet", "packet-beta", "pie",
        "quadrantChart", "radar-beta", "requirementDiagram", "sankey", "sankey-beta", "sequenceDiagram",
        "stateDiagram", "stateDiagram-v2", "timeline", "treemap", "treemap-beta", "xychart", "xychart-beta", "zenuml",
    }
)
_RESOURCE_ATTRIBUTES = {
    "script": ("src",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "track": ("src",),
    "iframe": ("src",),
    "frame": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "image": ("href", "xlink:href"),
    "use": ("href", "xlink:href"),
    "feimage": ("href", "xlink:href"),
}
_LOADING_RELS = frozenset(
    {"stylesheet", "icon", "preload", "prefetch", "modulepreload", "manifest", "apple-touch-icon", "mask-icon",
     "preconnect", "dns-prefetch"}
)
_CDN_KINDS = frozenset({"script", "link"})
MAX_MERMAID_TEXT = 16_384
_CSS_URL = re.compile(
    r"""url\(\s*+(?:"([^"\n]{0,2048}+)"|'([^'\n]{0,2048}+)'|([^'"()\s]{1,2048}+))|@import\s++(?:"([^"\n]{0,2048}+)"|'([^'\n]{0,2048}+)')""",
    re.IGNORECASE,
)


@dataclass
class _MermaidBlock:
    tag: str
    line: int
    offset: int
    open_tags: int = 1
    parts: list[str] = field(default_factory=list)
    size: int = 0

    def add(self, data: str) -> None:
        """Keep the first `MAX_MERMAID_TEXT` characters: the diagram type is in its first statement."""
        if self.size < MAX_MERMAID_TEXT:
            self.parts.append(data[: MAX_MERMAID_TEXT - self.size])
            self.size += len(self.parts[-1])


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    line: int
    column: int
    message: str

    def to_json(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "line": self.line,
            "column": self.column,
            "message": self.message,
        }


@dataclass(frozen=True)
class LintResult:
    findings: list[Finding]
    truncated: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "findings": [finding.to_json() for finding in self.findings],
            "errors": sum(finding.severity == "error" for finding in self.findings),
            "warnings": sum(finding.severity == "warning" for finding in self.findings),
            "truncated": self.truncated,
        }


def _external_host(url: str) -> str | None:
    """The host of an absolute http(s) or scheme-relative URL, None for anything that stays local."""
    url = url.strip()
    if "//" not in url:
        return None
    absolute = "http:" + url if url.startswith("//") else url
    try:
        parsed = urlparse(absolute)
        if parsed.scheme.lower() not in ("http", "https"):
            return None
        return parsed.hostname or ""
    except ValueError:
        return ""


def _srcset_urls(value: str) -> list[str]:
    return [candidate.split()[0] for candidate in value.split(",") if candidate.strip()]


def _shown(url: str) -> str:
    return url if len(url) <= 200 else url[:199] + "…"


def _first_statement(text: str) -> str | None:
    """The first line Mermaid reads as its diagram, stripped, in one forward pass over the lines.

    Blank lines, `%%` comments, `%%{…}%%` directives (over several lines too) and a
    `---` front matter block before it are skipped, as Mermaid skips them.
    """
    lines = iter(text.splitlines())
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            closing = next((rest for rest in lines if rest.strip() == "---"), None)
            if closing is None:
                return None
        elif stripped.startswith("%%{") and "}%%" not in stripped:
            next((rest for rest in lines if "}%%" in rest), None)
        elif stripped and not stripped.startswith("%%"):
            return stripped
    return None


class _Linter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.findings: list[Finding] = []
        self.targets: set[str] = set()
        self.anchor_links: list[tuple[str, int, int]] = []
        self.mermaid: _MermaidBlock | None = None
        self.in_style = False

    def add(self, rule: str, severity: str, message: str, position: tuple[int, int] | None = None) -> None:
        """Keep at most one finding past `MAX_FINDINGS`: enough to tell that the list was cut."""
        if len(self.findings) > MAX_FINDINGS:
            return
        line, offset = position or self.getpos()
        self.findings.append(Finding(rule, severity, line, offset + 1, message))

    def external(self, tag: str, url: str, position: tuple[int, int] | None = None) -> None:
        host = _external_host(url)
        if host is None:
            return
        if host == ALLOWED_CDN_HOST and tag in _CDN_KINDS:
            message = f"<{tag}> loads {_shown(url)}: allowed, but a self-hosted server serves Mermaid from /static/"
            self.add("external-resource", "warning", message, position)
        else:
            message = f"<{tag}> loads {_shown(url)} from another server; the sandbox blocks it"
            self.add("external-resource", "error", message, position)

    def css(self, tag: str, text: str) -> None:
        for match in _CSS_URL.finditer(text):
            self.external(tag, next(group for group in match.groups() if group is not None))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        for name in ("id", "name" if tag == "a" else ""):
            if name and values.get(name):
                self.targets.add(values[name])
        for name in _RESOURCE_ATTRIBUTES.get(tag, ()):
            if name in values:
                urls = _srcset_urls(values[name]) if name == "srcset" else [values[name]]
                for url in urls:
                    self.external(tag, url)
        if tag == "link" and _LOADING_RELS & set(values.get("rel", "").lower().split()):
            self.external(tag, values.get("href", ""))
        if "style" in values:
            self.css(tag, values["style"])
        if tag == "a" and values.get("href", "").startswith("#"):
            line, offset = self.getpos()
            self.anchor_links.append((values["href"], line, offset))
        if self.mermaid is not None:
            self.mermaid.open_tags += tag == self.mermaid.tag
        elif "mermaid" in values.get("class", "").split():
            self.mermaid = _MermaidBlock(tag, *self.getpos())
        self.in_style = tag == "style"

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """A self-closed element holds no diagram text, so it never opens a Mermaid block."""
        mermaid = self.mermaid
        self.handle_starttag(tag, attrs)
        if mermaid is None:
            self.mermaid = None
        self.in_style = False

    def handle_endtag(self, tag: str) -> None:
        self.in_style = False
        if self.mermaid is None or tag != self.mermaid.tag:
            return
        self.mermaid.open_tags -= 1
        if self.mermaid.open_tags == 0:
            self.finish_mermaid()

    def handle_data(self, data: str) -> None:
        if self.in_style:
            self.css("style", data)
        if self.mermaid is not None:
            self.mermaid.add(data)

    def finish_mermaid(self) -> None:
        assert self.mermaid is not None
        block, self.mermaid = self.mermaid, None
        statement = _first_statement("".join(block.parts))
        if statement is None:
            self.add("mermaid-empty", "error", "Mermaid block has no diagram", (block.line, block.offset))
        elif statement.split()[0].rstrip(":;") not in MERMAID_DIAGRAMS:
            message = f"Mermaid block starts with {_shown(statement.split()[0])!r}, not a diagram type such as flowchart"
            self.add("mermaid-unknown-diagram", "error", message, (block.line, block.offset))

    def close(self) -> None:
        super().close()
        if self.mermaid is not None:
            self.finish_mermaid()

    def missing_anchors(self) -> Iterator[Finding]:
        """Links to an absent `#name`, in document order, made only as they are consumed."""
        for href, line, offset in self.anchor_links:
            name = unquote(href[1:])
            if name and name.lower() != "top" and name not in self.targets:
                yield Finding("missing-anchor", "error", line, offset + 1, f"link to {_shown(href)} has no element with id {name!r}")


def _position(finding: Finding) -> tuple[int, int]:
    return finding.line, finding.column


def lint_html(html: str) -> LintResult:
    """Findings in document order, at most `MAX_FINDINGS` of them.

    Findings met while parsing stop being kept one past the cap, and anchor
    findings are produced lazily and merged in, so memory stays bounded by the
    cap rather than by the number of problems in the document.
    """
    linter = _Linter()
    linter.feed(html)
    linter.close()
    parsed = sorted(linter.findings, key=_position)
    merged = list(islice(merge(parsed, linter.missing_anchors(), key=_position), MAX_FINDINGS + 1))
    return LintResult(merged[:MAX_FINDINGS], len(merged) > MAX_FINDINGS)
