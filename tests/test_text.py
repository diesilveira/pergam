from __future__ import annotations

import pytest

from pergam.text import extract_markdown


def test_skips_invisible_elements() -> None:
    html = (
        "<html><head><title>Tab title</title><style>.secret-style{}</style>"
        "<script>var secretScript = 1</script></head><body>"
        "<script type='module'>secretModule()</script><noscript>secret-noscript</noscript>"
        "<template><p>secret-template</p></template><p>visible</p></body></html>"
    )

    assert extract_markdown(html) == "visible\n"


def test_headings_and_paragraphs() -> None:
    html = "<h1>Main  title</h1><p>Hello &amp; <b>bold</b>\n world</p><h3>Sub</h3><div>tail</div>"

    assert extract_markdown(html) == "# Main title\n\nHello & bold world\n\n### Sub\n\ntail\n"


def test_lists_keep_markers_nesting_and_order() -> None:
    html = "<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul><ol><li>first<li>second</ol>"

    assert extract_markdown(html) == "- one\n- two\n  - nested\n\n1. first\n2. second\n"


def test_table_becomes_a_pipe_table_with_the_first_row_as_header() -> None:
    html = (
        "<table><caption>Results</caption>"
        "<thead><tr><th>Name</th><th>a|b</th></tr></thead>"
        "<tbody><tr><td>x</td><td><p>1</p><p>2</p></td><td>extra</td></tr></tbody></table>"
    )

    assert extract_markdown(html) == (
        "Results\n\n| Name | a\\|b |  |\n| --- | --- | --- |\n| x | 1 2 | extra |\n"
    )


def test_pre_becomes_a_fenced_block_with_whitespace_kept() -> None:
    html = "<pre><code>def f():\n    return 1\n</code></pre><pre>uses ``` inside</pre>"

    assert extract_markdown(html) == "```\ndef f():\n    return 1\n```\n\n````\nuses ``` inside\n````\n"


@pytest.mark.parametrize(
    "html",
    ["", "<script>only()</script>", "<div>   </div>", "<table><tr><td></td></tr></table>"],
)
def test_nothing_visible_is_empty(html: str) -> None:
    assert extract_markdown(html) == ""


def test_unclosed_elements_still_flush() -> None:
    assert extract_markdown("<p>one<p>two<table><tr><td>cell") == "one\n\ntwo\n\n| cell |\n| --- |\n"


def test_nested_table_is_flattened_into_its_cell() -> None:
    html = "<table><tr><td>outer<table><tr><td>inner</td></tr></table></td></tr></table>"

    assert extract_markdown(html) == "| outer inner |\n| --- |\n"
