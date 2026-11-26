"""Full-text search over titles and extracted bodies, and the snippet shown for a match.

Search documents use the `simple` configuration: words are lowercased, never
stemmed, so matching is the same for every language. Only the first
`INDEXED_BODY_CHARS` characters of a body are indexed, which keeps a document
well under Postgres' 1 MB `tsvector` limit whatever the pergam's size. Snippets
come from `ts_headline` with private-use sentinels as highlight markers, so the
page can escape the whole snippet and only then turn the sentinels into `<mark>`.

`ts_headline` re-parses the text it is given, so it only runs for rows whose
body (weight B of the stored document) matches, first over the opening
`EARLY_HEADLINE_CHARS` and over the whole indexed prefix only when that has no
hit. `q` is capped at `MAX_QUERY_CHARS`.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

INDEXED_BODY_CHARS = 100_000
MAX_QUERY_CHARS = 200
EARLY_HEADLINE_CHARS = 8_000
MARK_START, MARK_STOP = "", ""
SENTINELS = MARK_START + MARK_STOP
HEADLINE_OPTIONS = (
    f'StartSel="{MARK_START}", StopSel="{MARK_STOP}", '
    'MaxWords=30, MinWords=12, MaxFragments=2, FragmentDelimiter=" … "'
)

DOCUMENT_SQL = (
    "setweight(to_tsvector('simple', p.title), 'A')"
    f" || setweight(to_tsvector('simple', left(%(body)s, {INDEXED_BODY_CHARS})), 'B')"
)

INSERT_TEXT = f"""
    INSERT INTO pergam_text (id, version, body, document, extractor)
    SELECT p.id, p.version, %(body)s, {DOCUMENT_SQL}, %(extractor)s
    FROM pergams p WHERE p.id = %(id)s AND p.version = %(version)s
    ON CONFLICT (id, version) DO UPDATE
        SET body = EXCLUDED.body, document = EXCLUDED.document, extractor = EXCLUDED.extractor
        WHERE pergam_text.extractor < EXCLUDED.extractor
"""

MATCHES_SQL = "(l.title ILIKE %(q_like)s OR t.document @@ websearch_to_tsquery('simple', %(q)s))"

_HEADLINE_OF = (
    "ts_headline('simple', translate(left(t.body, {chars}), %(sentinels)s, ''),"
    " websearch_to_tsquery('simple', %(q)s), %(headline_options)s)"
)

HEADLINE_SQL = f"""
    CASE WHEN ts_filter(t.document, '{{b}}') @@ websearch_to_tsquery('simple', %(q)s) THEN (
        SELECT CASE WHEN strpos(early.headline, %(mark_start)s) > 0 THEN early.headline
                    ELSE {_HEADLINE_OF.format(chars=INDEXED_BODY_CHARS)} END
        FROM (SELECT {_HEADLINE_OF.format(chars=EARLY_HEADLINE_CHARS)} AS headline) early
    ) END
"""


class QueryError(ValueError):
    pass


def parse_query(raw: str | None) -> str | None:
    """`q` trimmed, None when blank; raises `QueryError` above `MAX_QUERY_CHARS`."""
    q = (raw or "").strip()
    if len(q) > MAX_QUERY_CHARS:
        raise QueryError(f"q must be at most {MAX_QUERY_CHARS} characters")
    return q or None


def search_params(q: str) -> dict[str, str]:
    return {
        "q": q,
        "q_like": f"%{q}%",
        "sentinels": SENTINELS,
        "mark_start": MARK_START,
        "headline_options": HEADLINE_OPTIONS,
    }


@dataclass(frozen=True)
class Snippet:
    """A `ts_headline` fragment of a body that matched, with sentinel-marked hits."""

    headline: str

    @classmethod
    def of(cls, headline: str | None) -> Snippet | None:
        """None unless the body itself matched (a title-only match has no highlighted words)."""
        return cls(" ".join(headline.split())) if headline and MARK_START in headline else None

    def html(self) -> str:
        return html.escape(self.headline).replace(MARK_START, "<mark>").replace(MARK_STOP, "</mark>")

    def text(self) -> str:
        return self.headline.replace(MARK_START, "").replace(MARK_STOP, "")
