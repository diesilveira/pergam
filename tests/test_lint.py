from __future__ import annotations

import json
import time

import psycopg
import pytest

from pergam.lint import LINT_MAX_BODY_BYTES, MAX_FINDINGS, lint_html
from support import Client

TABLES = ("pergams", "pergam_meta", "pergam_text", "pergam_note")


def found(html: str) -> list[tuple[str, str, int, int]]:
    return [(f.rule, f.severity, f.line, f.column) for f in lint_html(html).findings]


def test_clean_document_has_no_findings() -> None:
    html = """<!doctype html><title>t</title><nav><a href="#a">a</a> <a href="#top">top</a> <a href="#">x</a></nav>
<h2 id="a">A</h2><img src="data:image/png;base64,AA" alt=""><a href="https://example.com/">out</a>
<script src="/static/mermaid/11.17.2/mermaid.min.js"></script>
<pre class="mermaid">
flowchart TB
  A --&gt; B
</pre>"""

    assert found(html) == []


@pytest.mark.parametrize(
    ("html", "column"),
    [
        ('<script src="https://cdn.example.com/x.js"></script>', 1),
        ('<link rel="stylesheet" href="//fonts.example.com/a.css">', 1),
        ('<p><img src="HTTPS://img.example.com/a.png"></p>', 4),
        ('<img srcset="a.png 1x, https://img.example.com/b.png 2x">', 1),
        ('<iframe src="https://example.com/"></iframe>', 1),
        ('<video poster="https://example.com/p.jpg"></video>', 1),
        ('<svg><image href="https://example.com/i.svg"/></svg>', 6),
        ('<div style="background: url(\'https://example.com/b.png\')"></div>', 1),
        ("<style>@import 'https://example.com/a.css';</style>", 8),
        ('<img src="http://[broken">', 1),
    ],
    ids=["script", "stylesheet", "image", "srcset", "frame", "poster", "svg-image", "style-attribute", "import", "bad-url"],
)
def test_external_resources_are_errors_with_their_location(html: str, column: int) -> None:
    assert found(html) == [("external-resource", "error", 1, column)]


def test_jsdelivr_scripts_are_a_warning_but_its_images_an_error() -> None:
    html = (
        '<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"></script>\n'
        '<img src="https://cdn.jsdelivr.net/x.png">'
    )

    assert found(html) == [("external-resource", "warning", 1, 1), ("external-resource", "error", 2, 1)]


def test_links_and_non_loading_rels_are_not_resources() -> None:
    html = '<a href="https://example.com/">x</a><link rel="canonical" href="https://example.com/"><form action="https://example.com/"></form>'

    assert found(html) == []


def test_anchor_without_a_target_is_reported_where_the_link_is() -> None:
    html = '<nav>\n  <a href="#costs">costs</a> <a href="#r%C3%A9sum%C3%A9">résumé</a> <a href="#named">n</a>\n</nav>\n<a name="named"></a><h2 id="résumé">R</h2>'

    result = lint_html(html)

    assert [(f.rule, f.line, f.column) for f in result.findings] == [("missing-anchor", 2, 3)]
    assert "#costs" in result.findings[0].message


@pytest.mark.parametrize(
    ("block", "rule"),
    [
        ('<pre class="mermaid">\n  \n</pre>', "mermaid-empty"),
        ('<div class="diagram mermaid">%% only a comment\n</div>', "mermaid-empty"),
        ('<pre class="mermaid">grahp TD\n A--&gt;B</pre>', "mermaid-unknown-diagram"),
        ('<pre class="mermaid">A --&gt; B</pre>', "mermaid-unknown-diagram"),
    ],
    ids=["blank", "comment-only", "typo", "no-keyword"],
)
def test_malformed_mermaid_blocks(block: str, rule: str) -> None:
    assert found("<p>intro</p>\n" + block) == [(rule, "error", 2, 1)]


@pytest.mark.parametrize(
    "diagram",
    [
        "%%{init: {'theme': 'dark'}}%%\nsequenceDiagram\n  A->>B: hi",
        "%%{\n  init: {'theme': 'dark'}\n}%%\nerDiagram\n  A ||--o{ B : has",
        "---\ntitle: Flow\n---\nflowchart LR\n  A-->B",
        "%% a comment\nstateDiagram-v2\n  [*] --> A",
        "pie title Pets\n  \"Dogs\" : 3",
        "<div><span>graph</span> TD; A-->B</div>",
    ],
    ids=["directive", "multiline-directive", "frontmatter", "comment", "pie", "nested-markup"],
)
def test_well_formed_mermaid_blocks(diagram: str) -> None:
    assert found(f'<div class="mermaid">{diagram}</div>') == []


def test_unclosed_mermaid_block_is_still_checked() -> None:
    assert found('<pre class="mermaid">\n') == [("mermaid-empty", "error", 1, 1)]


def test_findings_are_capped() -> None:
    result = lint_html('<a href="#nowhere">x</a>' * (MAX_FINDINGS + 1))

    assert len(result.findings) == MAX_FINDINGS
    assert result.to_json()["truncated"] is True


def test_lint_endpoint_reports_findings_and_writes_nothing(client: Client, db: psycopg.Connection) -> None:
    pid = client.publish()["id"]
    before = [db.execute(f"SELECT count(*) FROM {table}").fetchone() for table in TABLES]
    payload = {"title": "x", "type": "otro", "author": "you@example.com", "id": pid,
               "html": '<a href="#gone">x</a>\n<img src="https://img.example.com/a.png">'}

    resp = client.post_json(payload, "/lint")

    assert resp.status == 200
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json() == {
        "findings": [
            {"rule": "missing-anchor", "severity": "error", "line": 1, "column": 1,
             "message": "link to #gone has no element with id 'gone'"},
            {"rule": "external-resource", "severity": "error", "line": 2, "column": 1,
             "message": "<img> loads https://img.example.com/a.png from another server; the sandbox blocks it"},
        ],
        "errors": 2,
        "warnings": 0,
        "truncated": False,
    }
    assert [db.execute(f"SELECT count(*) FROM {table}").fetchone() for table in TABLES] == before


@pytest.mark.parametrize("payload", [{}, {"html": "  "}, {"html": 42}], ids=["missing", "blank", "not-a-string"])
def test_lint_needs_html(client: Client, payload: dict) -> None:
    assert client.post_json(payload, "/lint").status == 400


def test_lint_needs_no_trusted_user(trusting: Client) -> None:
    resp = trusting.post_json({"html": "<p>x</p>"}, "/lint")

    assert resp.status == 200
    assert resp.json()["findings"] == []


PATHOLOGICAL = LINT_MAX_BODY_BYTES - 1024


@pytest.mark.parametrize(
    "html",
    [
        '<pre class="mermaid">' + "%%{" * (PATHOLOGICAL // 3),
        '<pre class="mermaid">' + "%%{\n" * (PATHOLOGICAL // 4),
        '<pre class="mermaid">---\n' + "x\n" * (PATHOLOGICAL // 2),
        "<style>" + "url(" * (PATHOLOGICAL // 4),
        "<style>" + "url(" + " " * PATHOLOGICAL,
        "<style>" + "@import " * (PATHOLOGICAL // 8),
        '<div style="' + "url(" * (PATHOLOGICAL // 4) + '">',
        '<img srcset="' + "a," * (PATHOLOGICAL // 2) + '">',
    ],
    ids=["directives", "directive-lines", "front-matter", "css-urls", "css-spaces", "imports", "style-attribute", "srcset"],
)
def test_lint_work_stays_linear_up_to_its_body_limit(html: str) -> None:
    started = time.monotonic()

    lint_html(html)

    assert time.monotonic() - started < 1.5


def test_mermaid_text_past_its_cap_is_not_kept() -> None:
    block = '<pre class="mermaid">flowchart TB\n' + "A-->B\n" * 100_000 + "</pre>"

    assert found(block) == []


def test_lint_has_its_own_small_body_limit(client: Client) -> None:
    headers = {"Content-Type": "application/json", "Content-Length": str(LINT_MAX_BODY_BYTES + 1)}

    resp = client.request("POST", "/lint", json.dumps({"html": "<p>x</p>"}).encode(), headers)

    assert resp.status == 413
