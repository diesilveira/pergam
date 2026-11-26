"""The shape of a pergam id: 6 to 16 lowercase hex characters (new ones are 8)."""

from __future__ import annotations

import re

ID_RE = re.compile(r"^[a-f0-9]{6,16}$")
