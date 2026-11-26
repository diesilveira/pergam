"""Who may change a pergam when identity is trusted (`TRUST_PROXY_USER`).

The owner is the author of v1, which never changes. The owner and the
collaborators the owner lists may add versions and edit metadata; only the
owner edits the list. Without trusted identity nothing here is enforced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pergam.identity import IdentityError, parse_user

MAX_COLLABORATORS = 100


class CollaboratorError(ValueError):
    pass


class Forbidden(Exception):
    """The writer may not make this change; the message names the rule that failed."""


@dataclass(frozen=True)
class Access:
    owner: str
    collaborators: list[str]

    def may_edit(self, user: str) -> bool:
        return user == self.owner or user in self.collaborators

    def to_json(self, pergam_id: str) -> dict[str, Any]:
        return {"id": pergam_id, "owner": self.owner, "collaborators": self.collaborators}


def require_editor(access: Access, user: str, pergam_id: str) -> None:
    if not access.may_edit(user):
        raise Forbidden(f"{user} is neither the owner of {pergam_id} ({access.owner}) nor one of its collaborators")


def require_owner(access: Access, user: str, pergam_id: str) -> None:
    if user != access.owner:
        raise Forbidden(f"only the owner of {pergam_id} ({access.owner}) edits its collaborators, not {user}")


def require_import_authors(pergam_id: str, written: list[tuple[int, str]], user: str, access: Access | None) -> None:
    """Every `(version, author)` an import writes must be by `user`, or by the owner or a collaborator of an id already here.

    A trusted user may not record versions under the name of someone with no say
    over the pergam, as with `POST /pergam`; versions already here are not written,
    so they are not checked.
    """
    allowed = {user} if access is None else {user, access.owner, *access.collaborators}
    stranger = next(((version, author) for version, author in written if author not in allowed), None)
    if stranger is None:
        return
    who = "the signed-in user" if access is None else "the signed-in user, the owner or a collaborator"
    raise Forbidden(f"v{stranger[0]} of {pergam_id} is by {stranger[1]}: an import writes only versions by {who} ({user})")


def parse_collaborators(data: dict[str, Any]) -> list[str]:
    """The `collaborators` of a `PUT /{id}/collaborators` body, trimmed, deduplicated and sorted.

    Each one is a user as the proxy names it, so it follows the `X-Forwarded-User` rules.
    """
    unknown = sorted(set(data) - {"collaborators"})
    if unknown:
        raise CollaboratorError(f"unknown field {unknown[0]!r}; allowed: collaborators")
    value = data.get("collaborators")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CollaboratorError("collaborators must be an array of strings")
    try:
        users = sorted({parse_user(item) for item in value})
    except IdentityError:
        raise CollaboratorError(
            "each collaborator must be a non-empty string of at most 320 characters without control characters"
        ) from None
    if len(users) > MAX_COLLABORATORS:
        raise CollaboratorError(f"a pergam can have at most {MAX_COLLABORATORS} collaborators")
    return users
