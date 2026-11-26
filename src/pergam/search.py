"""Full-text search over titles and extracted bodies, and the snippet shown for a match.

Search documents use the `simple` configuration: words are lowercased, never
stemmed, so matching is the same for every language. Only the first
`INDEXED_BODY_CHARS` characters of a body are indexed, which keeps a document
well under Postgres' 1 MB `tsvector` limit whatever the pergam's size. Snippets
come from `ts_headline` with private-use sentinels as highlight markers, so the
page can escape the whole snippet and only then turn the sentinels into `<mark>`.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

INDEXED_BODY_CHARS = 100_000
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
    INSERT INTO pergam_text (id, version, body, document)
    SELECT p.id, p.version, %(body)s, {DOCUMENT_SQL}
    FROM pergams p WHERE p.id = %(id)s AND p.version = %(version)s
    ON CONFLICT (id, version) DO NOTHING
"""

MATCHES_SQL = "(l.title ILIKE %(q_like)s OR t.document @@ websearch_to_tsquery('simple', %(q)s))"

HEADLINE_SQL = (
    "ts_headline('simple', translate(t.body, %(sentinels)s, ''),"
    " websearch_to_tsquery('simple', %(q)s), %(headline_options)s)"
)


def search_params(q: str) -> dict[str, str]:
    return {"q": q, "q_like": f"%{q}%", "sentinels": SENTINELS, "headline_options": HEADLINE_OPTIONS}


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
