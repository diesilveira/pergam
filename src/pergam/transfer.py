"""The document of `GET /{id}/export`, and its validation as the body of `POST /import`.

One JSON object carries a pergam whole: `format_version`, its `id`, the `server`
that exported it, its `meta` and every version with its HTML, oldest first. An
import may carry only some versions (what a promote sends when the target
already has the others); the server keeps their numbers and `created_at`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from pergam.ids import ID_RE
from pergam.meta import Meta, Origin
from pergam.notes import NoteError, parse_note
from pergam.public_url import PublicAddress
from pergam.tags import TagError, parse_tags
from pergam.taxonomy import normalise_type

FORMAT_VERSION = 1
MAX_SERVER_URL_LENGTH = 2048
EARLIEST_CREATED_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)
CLOCK_SKEW = timedelta(minutes=5)
MAX_VERSION = 2**31 - 1
MAX_IMPORT_VERSIONS = 10_000


class TransferError(ValueError):
    pass


@dataclass(frozen=True)
class ExportedVersion:
    version: int
    title: str
    html: str
    type: str
    author: str
    note: str | None
    bytes: int
    created_at: datetime

    def same_content(self, other: ExportedVersion) -> bool:
        """Equal title, HTML, type, author and note; `created_at` and `bytes` are not compared."""
        return (self.title, self.html, self.type, self.author, self.note) == (
            other.title,
            other.html,
            other.type,
            other.author,
            other.note,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "title": self.title,
            "html": self.html,
            "type": self.type,
            "author": self.author,
            "note": self.note,
            "bytes": self.bytes,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class ImportDocument:
    id: str
    origin: Origin | None
    tags: list[str]
    archived: bool
    pinned: bool
    versions: list[ExportedVersion]


def export_json(pergam_id: str, versions: list[ExportedVersion], meta: Meta, public: PublicAddress) -> dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "id": pergam_id,
        "server": public.base_url,
        "meta": {"tags": meta.tags, "archived": meta.archived, "pinned": meta.pinned},
        "versions": [version.to_json() for version in versions],
    }


def parse_server_url(value: object) -> str:
    """An absolute http(s) URL without credentials, query or fragment, trailing `/` dropped."""
    if not isinstance(value, str) or len(value) > MAX_SERVER_URL_LENGTH:
        raise TransferError("server must be a URL string")
    try:
        parsed = urlparse(value)
        parsed.port
    except ValueError:
        raise TransferError("server must be an absolute http(s) URL with a valid host and port") from None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.netloc.endswith(":")
        or "@" in value
        or any(char in value for char in "?#")
        or any(char.isspace() or ord(char) < 0x20 for char in value)
    ):
        raise TransferError("server must be an absolute http(s) URL without credentials, query or fragment")
    return value.rstrip("/")


def _text(entry: dict[str, Any], field: str, label: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TransferError(f"{label}: {field} must be a non-empty string")
    if "\x00" in value:
        raise TransferError(f"{label}: {field} must not contain NUL")
    return value


def _created_at(entry: dict[str, Any], label: str, now: datetime) -> datetime:
    """The timestamp in UTC, between 1970 and `now` plus `CLOCK_SKEW`.

    Dates outside that window cannot come from a real publish: a far future one
    would sit on top of the index and feed for good, and one past year 9999 in
    UTC is stored but cannot be read back by the driver.
    """
    value = entry.get("created_at")
    try:
        created_at = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        created_at = None
    if created_at is None or created_at.tzinfo is None:
        raise TransferError(f"{label}: created_at must be an ISO 8601 timestamp with a UTC offset")
    try:
        created_at = created_at.astimezone(timezone.utc)
    except OverflowError:
        created_at = None
    if created_at is None or created_at < EARLIEST_CREATED_AT:
        raise TransferError(f"{label}: created_at must be between 1970-01-01 and now (UTC)")
    if created_at > now + CLOCK_SKEW:
        minutes = int(CLOCK_SKEW.total_seconds() // 60)
        raise TransferError(
            f"{label}: created_at {created_at.isoformat()} is more than {minutes} minutes ahead of this server's clock"
            f" ({now.isoformat(timespec='seconds')}); check for clock skew between the two servers"
        )
    return created_at


def _version(entry: object, index: int, types: tuple[str, ...], now: datetime) -> ExportedVersion:
    if not isinstance(entry, dict):
        raise TransferError(f"versions[{index}] must be an object")
    number = entry.get("version")
    if not isinstance(number, int) or isinstance(number, bool) or not 0 < number <= MAX_VERSION:
        raise TransferError(f"versions[{index}]: version must be an integer from 1 to {MAX_VERSION}")
    label = f"v{number}"
    html = _text(entry, "html", label)
    pergam_type = normalise_type(_text(entry, "type", label))
    if pergam_type not in types:
        raise TransferError(f"{label}: type must be one of {sorted(types)}")
    try:
        note = parse_note(entry.get("note"))
    except NoteError as exc:
        raise TransferError(f"{label}: {exc}") from None
    return ExportedVersion(
        version=number,
        title=_text(entry, "title", label),
        html=html,
        type=pergam_type,
        author=_text(entry, "author", label).strip(),
        note=note,
        bytes=len(html.encode("utf-8")),
        created_at=_created_at(entry, label, now),
    )


def _meta(value: object) -> tuple[list[str], bool, bool]:
    if value is None:
        return [], False, False
    if not isinstance(value, dict):
        raise TransferError("meta must be an object")
    for flag in ("archived", "pinned"):
        if not isinstance(value.get(flag, False), bool):
            raise TransferError(f"meta.{flag} must be true or false")
    try:
        tags = parse_tags(value.get("tags", []))
    except TagError as exc:
        raise TransferError(f"meta: {exc}") from None
    return tags, value.get("archived", False), value.get("pinned", False)


def parse_import(data: dict[str, Any], types: tuple[str, ...], now: datetime | None = None) -> ImportDocument:
    """Validate a `POST /import` body against this server's accepted types; raises `TransferError`.

    `bytes` is recomputed from each version's HTML, unknown fields are ignored.
    Dates are not required to grow with the version number: histories written
    before publish stamped dates after the per-id lock, or across a clock step,
    can have a version dated before the previous one.
    """
    if data.get("format_version") != FORMAT_VERSION:
        raise TransferError(f"format_version must be {FORMAT_VERSION}")
    pergam_id = data.get("id")
    if not isinstance(pergam_id, str) or not ID_RE.match(pergam_id):
        raise TransferError("id must be 6 to 16 lowercase hex characters")
    entries = data.get("versions")
    if not isinstance(entries, list) or not entries:
        raise TransferError("versions must be a non-empty array")
    if len(entries) > MAX_IMPORT_VERSIONS:
        raise TransferError(f"an import carries at most {MAX_IMPORT_VERSIONS} versions")
    now = now or datetime.now(timezone.utc)
    versions = sorted((_version(entry, i, types, now) for i, entry in enumerate(entries)), key=lambda v: v.version)
    repeated = next((b for a, b in zip(versions, versions[1:]) if a.version == b.version), None)
    if repeated:
        raise TransferError(f"v{repeated.version} appears more than once")
    server = data.get("server")
    origin = None if server is None else Origin(parse_server_url(server), pergam_id)
    tags, archived, pinned = _meta(data.get("meta"))
    return ImportDocument(pergam_id, origin, tags, archived, pinned, versions)
