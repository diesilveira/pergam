"""Files served under `/static/`: the pages' stylesheet and theme script, the frame bridge and the vendored Mermaid with its licence.

`storage-shim.js` lives here too but is not served: `/content` inlines it, without
its leading comment, as `STORAGE_SHIM`.

Mermaid is `dist/mermaid.min.js` of the npm package at `MERMAID_VERSION`, byte for
byte (sha256 `581ed7d7…5390eb8`), next to the package's `LICENSE`. A file whose
path names its version never changes, so it is cached for good; the bridge keeps
its path across upgrades, so it gets a short lifetime. Only the files listed here
are served, read once at import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

STATIC_ROOT = Path(__file__).resolve().parent / "static"

MERMAID_VERSION = "11.17.2"
MERMAID_PATH = f"/static/mermaid/{MERMAID_VERSION}/mermaid.min.js"
MERMAID_CDN_URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
BRIDGE_PATH = "/static/frame-bridge.js"
STYLESHEET_PATH = "/static/pergam.css"
THEME_SCRIPT_PATH = "/static/theme.js"
STORAGE_SHIM = re.sub(
    r"\A/\*.*?\*/\s*", "", (STATIC_ROOT / "storage-shim.js").read_text(encoding="utf-8"), count=1, flags=re.DOTALL
).strip()


@dataclass(frozen=True)
class StaticFile:
    body: bytes
    content_type: str
    versioned: bool


def _load(relative: str, content_type: str, versioned: bool) -> StaticFile:
    return StaticFile((STATIC_ROOT / relative).read_bytes(), content_type, versioned)


FILES: dict[str, StaticFile] = {
    STYLESHEET_PATH: _load("pergam.css", "text/css; charset=utf-8", versioned=False),
    THEME_SCRIPT_PATH: _load("theme.js", "text/javascript; charset=utf-8", versioned=False),
    BRIDGE_PATH: _load("frame-bridge.js", "text/javascript; charset=utf-8", versioned=False),
    MERMAID_PATH: _load(f"mermaid/{MERMAID_VERSION}/mermaid.min.js", "text/javascript; charset=utf-8", versioned=True),
    f"/static/mermaid/{MERMAID_VERSION}/LICENSE": _load(
        f"mermaid/{MERMAID_VERSION}/LICENSE", "text/plain; charset=utf-8", versioned=True
    ),
}
