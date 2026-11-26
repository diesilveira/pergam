"""Files served under `/static/`: the pages' stylesheet, theme, shortcut and index scripts, the view and diff enhancements (`pages.js`), the frame bridge and the vendored Mermaid with its licence.

`storage-shim.js` lives here too but is not served: `/content` inlines it, without
its leading comment, as `STORAGE_SHIM`.

Mermaid is `dist/mermaid.min.js` of the npm package at `MERMAID_VERSION`, byte for
byte (sha256 `581ed7d7…5390eb8`), next to the package's `LICENSE`. A file whose
path names its version never changes, so it is cached for good; the bridge keeps
its path across upgrades, so it gets a short lifetime. Only the files listed here
are served, read once at import.
"""

from __future__ import annotations

import hashlib
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
PAGES_SCRIPT_PATH = "/static/pages.js"
SHORTCUTS_SCRIPT_PATH = "/static/shortcuts.js"
INDEX_SCRIPT_PATH = "/static/index.js"
STORAGE_SHIM = re.sub(
    r"\A/\*.*?\*/\s*", "", (STATIC_ROOT / "storage-shim.js").read_text(encoding="utf-8"), count=1, flags=re.DOTALL
).strip()


@dataclass(frozen=True)
class StaticFile:
    body: bytes
    content_type: str
    versioned: bool

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.body).hexdigest()[:12]

    def immutable_for(self, requested_digest: str | None) -> bool:
        """Cached for good: the path names a version, or the URL names this exact content."""
        return self.versioned or requested_digest == self.digest


def _load(relative: str, content_type: str, versioned: bool) -> StaticFile:
    return StaticFile((STATIC_ROOT / relative).read_bytes(), content_type, versioned)


FILES: dict[str, StaticFile] = {
    STYLESHEET_PATH: _load("pergam.css", "text/css; charset=utf-8", versioned=False),
    THEME_SCRIPT_PATH: _load("theme.js", "text/javascript; charset=utf-8", versioned=False),
    PAGES_SCRIPT_PATH: _load("pages.js", "text/javascript; charset=utf-8", versioned=False),
    SHORTCUTS_SCRIPT_PATH: _load("shortcuts.js", "text/javascript; charset=utf-8", versioned=False),
    INDEX_SCRIPT_PATH: _load("index.js", "text/javascript; charset=utf-8", versioned=False),
    BRIDGE_PATH: _load("frame-bridge.js", "text/javascript; charset=utf-8", versioned=False),
    MERMAID_PATH: _load(f"mermaid/{MERMAID_VERSION}/mermaid.min.js", "text/javascript; charset=utf-8", versioned=True),
    f"/static/mermaid/{MERMAID_VERSION}/LICENSE": _load(
        f"mermaid/{MERMAID_VERSION}/LICENSE", "text/plain; charset=utf-8", versioned=True
    ),
}


def hashed_url(path: str) -> str:
    """`path` with `?v=<content digest>`: that URL never changes content, so it is cached for good.

    Pages are never cached and carry this URL, so an upgrade changes it and readers
    never pair new markup with an old stylesheet. The plain path stays served, briefly cached.
    """
    return f"{path}?v={FILES[path].digest}"
