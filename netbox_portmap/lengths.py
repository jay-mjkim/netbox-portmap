"""Cable length suggestion from where the two devices sit."""

from __future__ import annotations

import math
import re

from netbox.plugins import get_plugin_config

_NUM_RE = re.compile(r"(\d+)(?!.*\d)")  # last number in a rack name


def _rack_index(rack) -> int | None:
    """A rack's position along its row, from the trailing number of its name.

    Racks are named by position in almost every DC ("A-07", "3F-03-12"); the
    trailing number is the best generic proxy for distance along a row.
    """
    if rack is None:
        return None
    m = _NUM_RE.search(rack.name or "")
    return int(m.group(1)) if m else None


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
        ia, ib = _rack_index(ra), _rack_index(rb)
        if ia is None or ib is None or ra.location_id != rb.location_id:
            return None
        raw = abs(ia - ib) * float(model.get("rack_pitch", 0.6)) + float(model.get("vertical", 3.0))
    sizes = sorted(model.get("sizes") or [])
    length = next((s for s in sizes if s >= raw), sizes[-1] if sizes else math.ceil(raw))
    return length, model.get("unit", "m")
