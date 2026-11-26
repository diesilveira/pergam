#!/usr/bin/env python3
"""Named pergam servers ("hosted", "local", "team") read from one JSON file. Stdlib only.

The file is `$PERGAM_CONFIG` when set, else `$XDG_CONFIG_HOME/pergam/servers.json`,
else `~/.config/pergam/servers.json`:

    {
      "default": "local",
      "servers": {
        "local": {"url": "http://localhost:1111"},
        "team":  {"url": "https://pergam.example.com", "ca": "~/.config/pergam/team-ca.pem"}
      }
    }

`ca` is optional: a PEM bundle for a server whose certificate a private CA signed,
given to curl as `--cacert` (`~` is expanded, a relative path is read from the
file's folder). `hosted` (https://api.pergam.dev) exists even when the file does
not list it. The server to use resolves in this order:

1. the name the user gave ("publish it on team"), or an http(s) URL given instead;
2. `$PERGAM_URL` (with `$PERGAM_CA` as its optional CA bundle);
3. the file's `default`;
4. `hosted`.

Usage:
    pergam_profiles.py env [NAME]   shell assignments of PERGAM_SERVER, PERGAM_URL, PERGAM_CA, for eval
    pergam_profiles.py list         every profile, the default marked with *
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

HOSTED_URL = "https://api.pergam.dev"


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Server:
    name: str
    url: str
    ca: str | None = None


@dataclass(frozen=True)
class Profiles:
    servers: dict[str, Server]
    default: str | None = None


def config_path(env: Mapping[str, str]) -> Path:
    if env.get("PERGAM_CONFIG"):
        return Path(env["PERGAM_CONFIG"]).expanduser()
    base = Path(env["XDG_CONFIG_HOME"]).expanduser() if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "pergam" / "servers.json"


def _url(value: object, where: str) -> str:
    if not isinstance(value, str) or urlparse(value).scheme not in ("http", "https") or not urlparse(value).hostname:
        raise ProfileError(f"{where}: url must be an absolute http(s) URL")
    return value.rstrip("/")


def _ca(value: object, folder: Path, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ProfileError(f"{where}: ca must be a file path")
    path = Path(value).expanduser()
    path = path if path.is_absolute() else folder / path
    if not path.is_file():
        raise ProfileError(f"{where}: ca file not found: {path}")
    return str(path)


def load(path: Path) -> Profiles:
    """The profiles in `path` plus the built-in `hosted`; only `hosted` when the file does not exist."""
    builtin = {"hosted": Server("hosted", HOSTED_URL)}
    if not path.exists():
        return Profiles(builtin)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{path}: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("servers", {}), dict):
        raise ProfileError(f"{path}: expected an object with a \"servers\" object")
    servers = dict(builtin)
    for name, entry in data.get("servers", {}).items():
        where = f"{path}: servers.{name}"
        if not isinstance(entry, dict):
            raise ProfileError(f"{where} must be an object")
        servers[name] = Server(name, _url(entry.get("url"), where), _ca(entry.get("ca"), path.parent, where))
    default = data.get("default")
    if default is not None and default not in servers:
        raise ProfileError(f"{path}: default {default!r} is not one of {sorted(servers)}")
    return Profiles(servers, default)


def resolve(name: str | None, env: Mapping[str, str]) -> Server:
    """The server to talk to, in the order the module docstring lists; raises `ProfileError`."""
    if name and urlparse(name).scheme in ("http", "https"):
        return Server(name, _url(name, name))
    profiles = load(config_path(env))
    if name:
        if name not in profiles.servers:
            raise ProfileError(f"unknown server {name!r}; known: {', '.join(sorted(profiles.servers))}")
        return profiles.servers[name]
    if env.get("PERGAM_URL"):
        return Server("PERGAM_URL", _url(env["PERGAM_URL"], "PERGAM_URL"), env.get("PERGAM_CA") or None)
    return profiles.servers[profiles.default or "hosted"]


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["env"] and len(argv) <= 2:
            server = resolve(argv[1] if len(argv) == 2 else None, os.environ)
            for key, value in (("PERGAM_SERVER", server.name), ("PERGAM_URL", server.url), ("PERGAM_CA", server.ca or "")):
                print(f"{key}={shlex.quote(value)}")
            return 0
        if argv == ["list"]:
            profiles = load(config_path(os.environ))
            for name, server in sorted(profiles.servers.items()):
                mark = "*" if name == (profiles.default or "hosted") else " "
                print(f"{mark} {name}\t{server.url}" + (f"\tca={server.ca}" if server.ca else ""))
            return 0
    except ProfileError as exc:
        print(f"pergam_profiles: {exc}", file=sys.stderr)
        return 2
    print(__doc__.split("Usage:")[1].rstrip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
