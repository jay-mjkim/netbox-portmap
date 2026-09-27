"""
Automatic port layout.

The workbench draws every cabled interface of a device as a tile. Without any
per-model drawing, tiles are placed from what the interface names already say:

* interfaces are grouped by name prefix and slot (``TenGigabitEthernet1/0/x``,
  ``TwentyFiveGigE1/1/x``, ``IB1/x``, ``eno``, ...), each group sorted by port
  number;
* a group of four or more numbered ports is laid out the way switch faceplates
  are: odd numbers on the top row, even numbers on the bottom row;
* smaller groups sit in a single row;
* management-only ports go last, after a gap.

This is deliberately generic -- it needs no model-specific data, so it works
for any device type in any NetBox -- and for the purpose of cabling it is
enough: what matters is telling ports apart and seeing which are free, not a
photo-accurate faceplate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_NAME_RE = re.compile(
    r"^(?P<prefix>[A-Za-z][A-Za-z\-_ ]*?)"  # letters: 'TenGigabitEthernet', 'eno', 'IB'
    r"(?P<slot>(?:\d+/)*\d+/)?"  # optional 'x/y/' slot path
    r"(?P<port>\d+)$"  # trailing port number
)


@dataclass
class Group:
    key: str
    label: str
    ports: list = field(default_factory=list)  # (number, port dict)


def _parse(name: str):
    m = _NAME_RE.match(name.strip())
    if not m:
        return name, "", None
    return m.group("prefix"), m.group("slot") or "", int(m.group("port"))


def arrange(ports: list[dict]) -> dict:
    """
    Place ports on a grid. Each port dict needs ``name`` and ``mgmt_only``; the
    function adds ``row``/``col`` (0-based) and ``group`` to every port and
    returns ``{"cols": <total columns>, "rows": <1|2>, "groups": [...]}``.
    """
    groups: dict[str, Group] = {}
    order: list[str] = []
    mgmt: list[dict] = []
    for p in ports:
        if p.get("mgmt_only"):
            mgmt.append(p)
            continue
        prefix, slot, number = _parse(p["name"])
        key = f"{prefix}|{slot}"
        if key not in groups:
            groups[key] = Group(key, f"{prefix}{slot}".rstrip("/"))
            order.append(key)
        groups[key].ports.append((number if number is not None else 0, p))

    col = 0
    out_groups = []
    for key in order:
        g = groups[key]
        g.ports.sort(key=lambda t: (t[0], t[1]["name"]))
        two_rows = len(g.ports) >= 4
        start = col
        for i, (_, p) in enumerate(g.ports):
            if two_rows:
                p["row"], p["col"] = i % 2, col + i // 2
            else:
                p["row"], p["col"] = 0, col + i
            p["group"] = g.label
        col += (len(g.ports) + 1) // 2 if two_rows else len(g.ports)
        out_groups.append({"label": g.label, "start": start, "end": col - 1, "count": len(g.ports)})
        col += 1  # gap between groups
    if mgmt:
        col += 0 if not order else 1
        start = col
        for i, p in enumerate(sorted(mgmt, key=lambda p: p["name"])):
            p["row"], p["col"], p["group"] = 0, col + i, "mgmt"
        col += len(mgmt)
        out_groups.append({"label": "mgmt", "start": start, "end": col - 1, "count": len(mgmt)})
    else:
        col -= 1  # no trailing gap
    rows = 2 if any(p.get("row") == 1 for p in ports) else 1
    return {"cols": max(col, 1), "rows": rows, "groups": out_groups}
