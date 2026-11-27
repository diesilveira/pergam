"""What must agree with `pergam.__version__` before a release tag is pushed."""

from __future__ import annotations

import re
from pathlib import Path

from pergam import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_changelog_has_an_entry_for_the_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    entry = re.search(rf"^## \[{re.escape(__version__)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$", changelog, re.MULTILINE)
    assert entry, f"CHANGELOG.md has no '## [{__version__}] - YYYY-MM-DD' entry"
    assert re.search(rf"^\[{re.escape(__version__)}\]: https://", changelog, re.MULTILINE), "no compare link for the entry"


def test_landing_page_names_the_version() -> None:
    page = (ROOT / "web" / "public" / "index.html").read_text(encoding="utf-8")
    major_minor = ".".join(__version__.split(".")[:2])
    assert f'"softwareVersion": "{__version__}"' in page
    assert f"MIT · v{major_minor} · self-hostable" in page
