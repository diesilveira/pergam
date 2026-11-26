"""Visible text of a pergam's HTML, for `GET /{id}/text` and for search.

Only the stdlib `html.parser`, and one parse renders it twice: as simple
Markdown for `/text`, and as plain lines (the same blocks without Markdown
syntax) for the search index and its snippets. The contents of `script`,
`style`, `template`, `noscript` and `head` are dropped. In Markdown, headings
become `#` lines, list items `-` or `1.` lines, tables pipe tables (first row as
header) and `pre` fenced blocks; everything else is paragraphs of
whitespace-collapsed text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

SKIPPED = frozenset({"script", "style", "template", "noscript", "head", "title"})
HEADINGS = {f"h{level}": level for level in range(1, 7)}
BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "details", "dialog", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "header", "hgroup", "hr", "main", "nav",
        "p", "section", "summary",
    }
)
CELLS = frozenset({"td", "th"})

_WHITESPACE = re.compile(r"\s+")


def collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


@dataclass
class _Table:
    rows: list[list[str]] = field(default_factory=list)
    cell: list[str] | None = None
    caption: list[str] | None = None

    def close_cell(self) -> None:
        if self.cell is not None:
            if not self.rows:
                self.rows.append([])
            self.rows[-1].append(collapse("".join(self.cell)))
            self.cell = None

    def open_cell(self) -> None:
        self.close_cell()
        if not self.rows:
            self.rows.append([])
        self.cell = []

    def open_row(self) -> None:
        self.close_cell()
        self.rows.append([])

    def markdown(self) -> str:
        rows = self._rows()
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        lines = [_table_line(row + [""] * (width - len(row))) for row in rows]
        return "\n".join([lines[0], _table_line(["---"] * width), *lines[1:]])

    def plain(self) -> str:
        return "\n".join(" ".join(cell for cell in row if cell) for row in self._rows())

    def _rows(self) -> list[list[str]]:
        return [row for row in self.rows if any(row)]


def _table_line(cells: list[str]) -> str:
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


def _fence(code: str) -> str:
    longest = max((len(run) for run in re.findall(r"`{3,}", code)), default=2)
    return "`" * (longest + 1)


@dataclass(frozen=True)
class PageText:
    markdown: str
    plain: str


@dataclass(frozen=True)
class _Block:
    markdown: str
    plain: str
    list_group: int = 0


class _TextBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self.list_group = 0
        self.inline: list[str] = []
        self.skip_depth = 0
        self.heading = 0
        self.lists: list[list[int]] = []
        self.marker: str | None = None
        self.pre: list[str] | None = None
        self.pre_depth = 0
        self.table: _Table | None = None
        self.table_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIPPED:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if self.table_depth and tag not in ("table", "caption", "tr", *CELLS):
            self._write(" ")
            return
        if tag == "table":
            self._start_table()
        elif tag == "caption" and self.table:
            self.table.caption = []
        elif tag == "tr" and self.table_depth == 1 and self.table:
            self.table.open_row()
        elif tag in CELLS and self.table_depth == 1 and self.table:
            self.table.open_cell()
        elif tag in ("tr", *CELLS):
            self._write(" ")
        elif tag == "pre":
            self._flush()
            if self.pre is None:
                self.pre = []
            self.pre_depth += 1
        elif self.pre is not None:
            if tag == "br":
                self.pre.append("\n")
        elif tag in HEADINGS:
            self._flush()
            self.heading = HEADINGS[tag]
        elif tag in ("ul", "ol"):
            self._flush()
            if not self.lists:
                self.list_group += 1
            self.lists.append([1 if tag == "ol" else 0, 0])
        elif tag == "li":
            self._flush()
            self.marker = self._next_marker()
        elif tag in BLOCKS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag == "table":
            self._end_table()
        elif tag == "caption" and self.table and self.table.caption is not None:
            caption = collapse("".join(self.table.caption))
            self._emit(caption, caption)
            self.table.caption = None
        elif self.table_depth:
            if tag in CELLS and self.table_depth == 1 and self.table:
                self.table.close_cell()
        elif tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
            if self.pre_depth == 0 and self.pre is not None:
                self._end_pre()
        elif self.pre is not None:
            return
        elif tag in HEADINGS:
            self._flush()
            self.heading = 0
        elif tag in ("ul", "ol"):
            self._flush()
            if self.lists:
                self.lists.pop()
        elif tag == "li" or tag in BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self._write(data)

    def close(self) -> None:
        super().close()
        if self.table:
            self.table_depth = 1
            self._end_table()
        if self.pre is not None:
            self._end_pre()
        self._flush()

    def page_text(self) -> PageText:
        out: list[str] = []
        previous_group = 0
        for block in self.blocks:
            if out:
                out.append("\n" if block.list_group and block.list_group == previous_group else "\n\n")
            out.append(block.markdown)
            previous_group = block.list_group
        markdown = "".join(out) + "\n" if out else ""
        return PageText(markdown=markdown, plain="\n".join(block.plain for block in self.blocks))

    def _write(self, text: str) -> None:
        if self.table:
            if self.table.cell is not None:
                self.table.cell.append(text)
            elif self.table.caption is not None:
                self.table.caption.append(text)
        elif self.pre is not None:
            self.pre.append(text)
        else:
            self.inline.append(text)

    def _next_marker(self) -> str:
        if not self.lists:
            return "- "
        current = self.lists[-1]
        current[1] += 1
        return f"{current[1]}. " if current[0] else "- "

    def _indent(self) -> str:
        return "  " * max(0, len(self.lists) - 1)

    def _flush(self) -> None:
        text = collapse("".join(self.inline))
        self.inline = []
        if not text:
            return
        if self.heading:
            self._emit("#" * self.heading + " " + text, text)
        elif self.lists or self.marker:
            marker, self.marker = self.marker, None
            line = self._indent() + (marker or "  ") + text
            self.blocks.append(_Block(line, text, self.list_group if self.lists else 0))
        else:
            self._emit(text, text)

    def _emit(self, markdown: str, plain: str) -> None:
        if markdown:
            self.blocks.append(_Block(markdown, plain))

    def _start_table(self) -> None:
        if self.table_depth == 0:
            self._flush()
            self.table = _Table()
        else:
            self._write(" ")
        self.table_depth += 1

    def _end_table(self) -> None:
        if self.table_depth == 0:
            return
        self.table_depth -= 1
        if self.table_depth == 0 and self.table:
            self.table.close_cell()
            table, self.table = self.table, None
            self._emit(table.markdown(), table.plain())

    def _end_pre(self) -> None:
        code = "".join(self.pre or []).removeprefix("\r").removeprefix("\n").rstrip()
        self.pre = None
        self.pre_depth = 0
        if code.strip():
            fence = _fence(code)
            self._emit(f"{fence}\n{code}\n{fence}", code)


def extract_text(html: str) -> PageText:
    """The visible text of `html`. `markdown` ends in a newline; both are empty when nothing is visible."""
    builder = _TextBuilder()
    builder.feed(html)
    builder.close()
    return builder.page_text()
