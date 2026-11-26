from __future__ import annotations

import json
import re
import socket
import time
from pathlib import Path

import psycopg
import pytest

import pergam
from pergam.lint import LINT_CONCURRENCY, LINT_MAX_BODY_BYTES, MAX_FINDINGS, MAX_MERMAID_TEXT, MERMAID_DIAGRAMS, _Linter, authorities, lint_html
from pergam.static_files import MERMAID_VERSION
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
        "flowchart-elk TD\n A-->B",
        "requirementDiagram\n requirement r { id: 1 }",
        "xychart-beta\n bar [1, 2]",
        "architecture-beta\n service api(server)[API]",
        "venn-beta\n set A",
        "treeView-beta\n root",
        "ishikawa-beta\n Problem",
        "C4Context\n Person(a, \"A\")",
    ],
    ids=["directive", "multiline-directive", "frontmatter", "comment", "pie", "nested-markup", "elk", "requirement",
         "xychart", "architecture", "venn", "tree-view", "ishikawa", "c4"],
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


def test_findings_past_the_cap_are_not_kept_while_parsing() -> None:
    linter = _Linter()
    linter.feed('<img src="https://img.example.com/a.png">' * (MAX_FINDINGS * 20))
    linter.close()

    assert len(linter.findings) == MAX_FINDINGS + 1


def test_capped_findings_are_the_first_in_document_order_across_rules() -> None:
    html = '<a href="#gone">x</a>\n<img src="https://img.example.com/a.png">\n' * MAX_FINDINGS

    result = lint_html(html)

    assert result.truncated
    assert [(f.rule, f.line) for f in result.findings[:4]] == [
        ("missing-anchor", 1), ("external-resource", 2), ("missing-anchor", 3), ("external-resource", 4)
    ]
    assert result.findings[-1].line == MAX_FINDINGS


def test_every_diagram_type_is_one_the_vendored_mermaid_detects() -> None:
    source = (Path(pergam.__file__).parent / "static" / "mermaid" / MERMAID_VERSION / "mermaid.min.js").read_text()
    detectors = re.findall(r"/\^\\s\*([^/]{1,120})/", source)
    detected = {word for body in detectors for word in re.findall(r"[A-Za-z][A-Za-z0-9-]*", body)}

    assert set(MERMAID_DIAGRAMS) <= detected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://Docs.Example.com", {"docs.example.com:443"}),
        ("http://localhost:1111/x.js", {"localhost:1111"}),
        ("//docs.example.com/x.js", {"docs.example.com:80", "docs.example.com:443"}),
        ("docs.example.com", {"docs.example.com:80", "docs.example.com:443"}),
        ("127.0.0.1:8080", {"127.0.0.1:8080"}),
        ("http://[broken", set()),
    ],
)
def test_authorities(url: str, expected: set[str]) -> None:
    assert authorities(url) == expected


def test_scripts_from_this_server_are_not_external_but_its_images_are() -> None:
    html = (
        '<script src="https://docs.example.com/static/mermaid/11.17.2/mermaid.min.js"></script>\n'
        '<script src="http://127.0.0.1:8080/static/frame-bridge.js"></script>\n'
        '<img src="https://docs.example.com/logo.png">\n'
        '<script src="https://other.example.com/x.js"></script>'
    )

    result = lint_html(html, ("https://docs.example.com", "127.0.0.1:8080"))

    assert [(f.rule, f.line) for f in result.findings] == [("external-resource", 3), ("external-resource", 4)]


def test_lint_endpoint_knows_its_own_origin(client: Client) -> None:
    html = f'<script src="http://127.0.0.1:{client.port}/static/frame-bridge.js"></script>'

    assert client.post_json({"html": html}, "/lint").json()["findings"] == []


@pytest.mark.parametrize(
    "opening",
    [
        "---\n" + "title: long\n" * (MAX_MERMAID_TEXT // 12 + 1) + "---\nflowchart TB\n A-->B",
        "%%{init: {\n" + "  'x': 1,\n" * (MAX_MERMAID_TEXT // 9 + 1) + "}}%%\nflowchart TB\n A-->B",
        "%% " + "x" * (MAX_MERMAID_TEXT - 10) + "\nflowchart TB\n A-->B",
    ],
    ids=["front-matter", "directive", "comment"],
)
def test_a_block_whose_opening_outruns_the_inspected_prefix_is_not_reported(opening: str) -> None:
    assert found(f'<pre class="mermaid">{opening}</pre>') == []


def test_a_short_unknown_block_past_a_long_body_is_still_reported() -> None:
    assert found('<pre class="mermaid">grahp TD\n' + "A-->B\n" * MAX_MERMAID_TEXT + "</pre>") == [
        ("mermaid-unknown-diagram", "error", 1, 1)
    ]


def held_lint(port: int) -> socket.socket:
    """A `/lint` request whose body never finishes arriving, so it holds its slot."""
    held = socket.create_connection(("127.0.0.1", port), timeout=10)
    held.sendall(b"POST /lint HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
    return held


def test_lints_beyond_the_concurrency_cap_get_429(client: Client) -> None:
    held = [held_lint(client.port) for _ in range(LINT_CONCURRENCY)]
    try:
        deadline = time.monotonic() + 5
        while (status := client.post_json({"html": "<p>x</p>"}, "/lint").status) != 429 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert status == 429
    finally:
        for connection in held:
            connection.close()

    deadline = time.monotonic() + 5
    while (status := client.post_json({"html": "<p>x</p>"}, "/lint").status) != 200 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert status == 200
