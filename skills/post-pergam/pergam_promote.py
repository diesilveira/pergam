#!/usr/bin/env python3
"""Copy a pergam with its whole history from one pergam server to another. Stdlib only.

    pergam_promote.py ID --from local --to team

`--from` and `--to` are server names from the profiles file (see
`pergam_profiles.py`) or http(s) URLs. The pergam keeps its id, version numbers,
dates, tags and notes, and the target records where it came from. When the
target already has the id, only the versions it lacks are uploaded; a version it
has with different content stops the promote before anything is uploaded, and
the server refuses such an import anyway. Running it again is a no-op.

Exit status: 0 promoted or nothing to do, 1 refused or failed, 2 bad arguments or profiles.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from pergam_profiles import HOSTED_URL, ProfileError, Server, resolve

COMPARED = ("title", "html", "type", "author", "note")
TIMEOUT = 120


class PromoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class Promoted:
    uploaded: list[int]
    already_there: list[int]
    view_url: str


def _send(server: Server, request: urllib.request.Request) -> dict[str, Any]:
    try:
        context = ssl.create_default_context(cafile=server.ca) if server.ca else None
    except (ssl.SSLError, OSError) as exc:
        raise PromoteError(f"{server.name}: cannot load the CA bundle {server.ca}: {exc}") from None
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _message(exc.read().decode("utf-8", "replace"))
        raise PromoteError(f"{server.name}: {request.get_method()} {request.selector} answered {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise PromoteError(f"{server.name}: cannot reach {server.url}: {exc}") from None


def _get(server: Server, path: str) -> dict[str, Any] | None:
    """The JSON at `path`, or None when the server answers 404."""
    try:
        return _send(server, urllib.request.Request(f"{server.url}{path}"))
    except PromoteError as exc:
        if isinstance(exc.__cause__, urllib.error.HTTPError) and exc.__cause__.code == 404:
            return None
        raise


def _post(server: Server, path: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    return _send(server, urllib.request.Request(f"{server.url}{path}", data, {"Content-Type": "application/json"}))


def _message(error_page: str) -> str:
    """The message of an `http.server` error page, or the body as is."""
    start = error_page.find("<p>Message: ")
    if start < 0:
        return error_page.strip()[:300]
    return html.unescape(error_page[start + len("<p>Message: ") : error_page.find("</p>", start)]).rstrip(".")


def _numbers(versions: list[int]) -> str:
    return ", ".join(f"v{n}" for n in versions)


def missing_versions(source: dict[str, Any], target: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[int]]:
    """Source versions the target lacks, and the numbers it already has; raises when one it has differs."""
    present = {v["version"]: v for v in (target or {}).get("versions", [])}
    differing = [
        v["version"]
        for v in source["versions"]
        if v["version"] in present and any(v.get(k) != present[v["version"]].get(k) for k in COMPARED)
    ]
    if differing:
        raise PromoteError(f"{_numbers(differing)} already exist on the target with different content; nothing uploaded")
    missing = [v for v in source["versions"] if v["version"] not in present]
    return missing, sorted(n for n in present if n in {v["version"] for v in source["versions"]})


def promote(pergam_id: str, source: Server, target: Server) -> Promoted:
    if HOSTED_URL in (source.url, target.url):
        raise PromoteError("the hosted backend keeps no versions; promote works between self-hosted servers")
    if source.url == target.url:
        raise PromoteError(f"{source.name} and {target.name} are the same server")
    path = f"/{quote(pergam_id, safe='')}/export"
    exported = _get(source, path)
    if exported is None:
        raise PromoteError(f"{pergam_id} not found on {source.name}")
    missing, already_there = missing_versions(exported, _get(target, path))
    if not missing:
        described = _get(target, f"/api/pergams/{quote(pergam_id, safe='')}")
        return Promoted([], already_there, described["view_url"] if described else f"{target.url}/{pergam_id}/view")
    answer = _post(target, "/import", {**exported, "versions": missing})
    return Promoted(answer["imported"], already_there, answer["view_url"])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Copy a pergam with its history to another pergam server.")
    parser.add_argument("id", help="the pergam id, e.g. abc12345")
    parser.add_argument("--from", dest="source", required=True, help="source server: profile name or URL")
    parser.add_argument("--to", dest="target", required=True, help="target server: profile name or URL")
    args = parser.parse_args(argv)
    try:
        source, target = resolve(args.source, os.environ), resolve(args.target, os.environ)
    except ProfileError as exc:
        print(f"pergam_promote: {exc}", file=sys.stderr)
        return 2
    try:
        result = promote(args.id, source, target)
    except PromoteError as exc:
        print(f"pergam_promote: {exc}", file=sys.stderr)
        return 1
    if result.uploaded:
        kept = f" ({_numbers(result.already_there)} already there)" if result.already_there else ""
        print(f"promoted {args.id} from {source.name} to {target.name}: uploaded {_numbers(result.uploaded)}{kept}")
    else:
        print(f"{target.name} already has every version of {args.id}; nothing to do")
    print(result.view_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
