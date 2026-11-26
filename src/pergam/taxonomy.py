"""The allowed pergam types and the colour of each type badge.

`PERGAM_TYPES` (comma separated) lists the types `POST /pergam` accepts; unset or
blank means the five defaults. Stored types outside the list keep rendering, so a
badge colour is derived from the type name alone: the defaults keep their
historic colours, any other name gets a stable hue from a CRC32 of its name.
"""

from __future__ import annotations

import colorsys
import zlib
from collections.abc import Mapping

DEFAULT_TYPES = ("plan", "investigacion", "informe", "reporte", "otro")

_DEFAULT_COLOURS: dict[str, tuple[int, int, int]] = {
    "plan": (240, 210, 138),
    "investigacion": (182, 146, 255),
    "informe": (86, 211, 100),
    "reporte": (94, 231, 255),
    "otro": (139, 150, 167),
}


def normalise_type(raw: str) -> str:
    return raw.strip().lower()


def parse_types(raw: str | None) -> tuple[str, ...]:
    names = [normalise_type(part) for part in (raw or "").split(",")]
    unique = tuple(dict.fromkeys(name for name in names if name))
    return unique or DEFAULT_TYPES


def allowed_types(env: Mapping[str, str]) -> tuple[str, ...]:
    return parse_types(env.get("PERGAM_TYPES"))


def type_rgb(name: str) -> tuple[int, int, int]:
    if name in _DEFAULT_COLOURS:
        return _DEFAULT_COLOURS[name]
    hue = zlib.crc32(name.encode("utf-8")) % 360 / 360
    red, green, blue = colorsys.hls_to_rgb(hue, 0.72, 0.8)
    return round(red * 255), round(green * 255), round(blue * 255)
