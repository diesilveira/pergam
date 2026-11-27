from __future__ import annotations

import pytest

from pergam.text import extract_text


def extract_markdown(html: str) -> str:
    return extract_text(html).markdown


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


def test_plain_rendering_has_the_same_blocks_without_markdown_syntax() -> None:
    html = (
        "<h2>Status</h2><ul><li>east</li><li>west</li></ul>"
        "<table><tr><th>Region</th><th>State</th></tr><tr><td>east</td><td></td><td>done</td></tr></table>"
        "<pre>x = 1</pre>"
    )

    assert extract_text(html).plain == "Status\neast\nwest\nRegion State\neast done\nx = 1"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<head><title>T</title><body><h1>Hello</h1>", "# Hello\n"),
        ("<html><head><meta charset=utf-8><title>T</title><h1>No body tag</h1><p>para</p>", "# No body tag\n\npara\n"),
        ("<head><style>a{}</style>Loose text<p>p</p>", "Loose text\n\np\n"),
        ("<html><body><p>x</p><head>late</head>", "x\n\nlate\n"),
    ],
    ids=["no-head-end-tag", "no-body-tag", "text-in-head", "head-after-body"],
)
def test_head_ends_implicitly(html: str, expected: str) -> None:
    assert extract_markdown(html) == expected


def test_closed_noscript_and_template_are_dropped() -> None:
    html = "<noscript>hidden<p>also hidden</p></noscript><template><template>x</template><p>tpl</p></template><p>shown</p>"

    assert extract_markdown(html) == "shown\n"


def test_unclosed_noscript_is_replayed_as_content() -> None:
    assert extract_markdown("<p>before</p><noscript>enable js<p>Body text</p>") == "before\n\nenable js\n\nBody text\n"


def test_block_element_ends_an_unclosed_pre() -> None:
    html = "<pre>code\n  x<p>after pre</p><h2>Next</h2>"

    assert extract_markdown(html) == "```\ncode\n  x\n```\n\nafter pre\n\n## Next\n"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p># not a heading</p>", "\\# not a heading\n"),
        ("<p>1. not a list</p>", "1\\. not a list\n"),
        ("<p>2) neither</p>", "2\\) neither\n"),
        ("<p>- dash</p>", "\\- dash\n"),
        ("<p>&gt; quote</p>", "\\> quote\n"),
        ("<p>```fence</p>", "\\```fence\n"),
        ("<ul><li>* star</li></ul>", "- \\* star\n"),
        ("<p>10 items</p>", "10 items\n"),
    ],
)
def test_leading_markdown_markers_are_escaped(html: str, expected: str) -> None:
    assert extract_markdown(html) == expected


def test_plain_rendering_is_not_escaped() -> None:
    assert extract_text("<p># tag</p>").plain == "# tag"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>a</p><div hidden>secret <b>x</b></div><p>b</p>", "a\nb"),
        ('<p>a</p><div hidden="until-found">secret</div><p>b</p>', "a\nb"),
        ('<p>a <span aria-hidden="true">icon</span> b</p>', "a b"),
        ('<p>a <span aria-hidden="false">shown</span></p>', "a shown"),
        ('<p style="display: none !important">gone</p><p style="color:red">kept</p>', "kept"),
        ('<p style="color:red; VISIBILITY:hidden">gone</p><p>kept</p>', "kept"),
        ('<p style="display:none-ish">kept</p>', "kept"),
        ("<p>Pick <select><option>one<option>two</select> now</p>", "Pick now"),
        ("<input list=l><datalist id=l><option value=a>alpha</datalist><p>after</p>", "after"),
    ],
    ids=["hidden", "until-found", "aria-hidden", "aria-visible", "display-none", "visibility", "not-none", "select", "datalist"],
)
def test_hidden_content_is_not_extracted(html: str, expected: str) -> None:
    assert extract_text(html).plain == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>a <img hidden src=x> b</p><p>c</p>", "a b\nc"),
        ("<p hidden>gone<p>visible", "visible"),
        ("<ul><li hidden>gone<li>shown</ul>", "shown"),
        ("<div><p hidden>gone</div><p>after</p>", "after"),
        ("<div hidden><div>x</div>still hidden</div><p>after</p>", "after"),
        ("<p>before</p><div hidden>never closed<p>rest", "before"),
    ],
    ids=["void", "omitted-p-end", "omitted-li-end", "unmatched-end", "nested-same-tag", "never-closed"],
)
def test_a_hidden_region_ends_where_a_browser_ends_it(html: str, expected: str) -> None:
    assert extract_text(html).plain == expected


def test_hidden_content_stays_out_of_markdown_too() -> None:
    assert extract_markdown('<h1>Title</h1><ul><li>one</li><li aria-hidden="true">two</li></ul>') == "# Title\n\n- one\n"
