"""Cable length suggestion from where the two devices sit."""

from __future__ import annotations

import math
import re

from netbox.plugins import get_plugin_config

_NUM_RE = re.compile(r"(\d+)(?!.*\d)")  # last number in a rack name


def _rack_position(rack) -> tuple[str, int] | None:
    """A rack's row and position along it, from its name.

    Racks are named by position in almost every DC ("A-07", "3F-03-12"): the
    trailing number is the position along the row and everything before it names
    the row. Only racks in the same row can be compared this way.
    """
    if rack is None:
        return None
    name = rack.name or ""
    m = _NUM_RE.search(name)
    if not m:
        return None
    return name[: m.start(1)], int(m.group(1))


def suggest(a_device, b_device):
    """Return (length, unit) or None when nothing sensible can be said."""
    model = get_plugin_config("netbox_portmap", "length_model")
    if not model:
        return None
    ra, rb = a_device.rack, b_device.rack
    if ra is None or rb is None:
        return None
    if ra.pk == rb.pk:
        raw = float(model.get("same_rack", 3.0))
    else:
        pa, pb = _rack_position(ra), _rack_position(rb)
        # Different rows ("3F-03-12" vs "3F-04-12"): the distance is not along a row and
        # cannot be read from the names. No suggestion beats a confidently short one.
        if pa is None or pb is None or pa[0] != pb[0] or ra.location_id != rb.location_id:
            return None
        raw = abs(pa[1] - pb[1]) * float(model.get("rack_pitch", 0.6)) + float(model.get("vertical", 3.0))
    sizes = sorted(model.get("sizes") or [])
    length = next((s for s in sizes if s >= raw), sizes[-1] if sizes else math.ceil(raw))
    return length, model.get("unit", "m")
