"""
The port map as a spreadsheet: one row per cable on the hub, in faceplate order.

The columns follow the port-map sheets the team keeps by hand (SRC · Cable ·
DST), with one difference that is the point of the download: the first column
is the cable's NetBox id, and each end carries its interface id, so a row in
the file can be found again in NetBox and a change made from the sheet lands
on the right cable rather than on "row 37".
"""

from __future__ import annotations

from dcim.models import Cable, Device

from . import data, xlsx

GROUPS = [(0, 1, "NetBox"), (2, 7, "SRC"), (8, 13, "Cable"), (14, 19, "DST"), (20, 20, "")]
HEADERS = [
    "Cable ID",
    "상태",
    "종류",
    "실장",
    "Hostname",
    "Port",
    "Interface ID",
    "Label(상)",
    "Cable 타입",
    "길이",
    "단위",
    "Label",
    "색",
    "구간",
    "종류",
    "실장",
    "Hostname",
    "Port",
    "Interface ID",
    "Label(하)",
    "NetBox",
]
WIDTHS = [10, 11, 14, 12, 20, 24, 12, 34, 20, 7, 6, 18, 9, 8, 14, 12, 20, 24, 12, 34, 44]


def _seat(summary: dict | None) -> str:
    """Where the device sits, the way the sheets say it: ``03-18 42U``."""
    if not summary or not summary.get("rack"):
        return ""
    rack = summary["rack"]
    pos = summary.get("position")
    return f"{rack} {int(pos)}U" if pos is not None else rack


def _label(summary: dict | None, port: str) -> str:
    """``ICN-CON-FW-01, x1, 03-18 42U`` — what is printed on the cable's tag."""
    if not summary:
        return ""
    return ", ".join(part for part in (summary.get("name", ""), port, _seat(summary)) if part)


def rows(device: Device, absolute=lambda path: path) -> list[list]:
    """Header rows then one row per cable, ordered as the workbench draws the ports."""
    ports = data.device_ports(device)
    hub = ports["device"]
    cabled = [p for p in ports["ports"] if p["cable"]]
    cabled.sort(key=lambda p: (p.get("col", 0), p.get("row", 0)))
    peer_ids = {p["peer"]["device_id"] for p in cabled if p["peer"] and p["peer"].get("device_id")}
    peers = {
        d.pk: data.device_summary(d)
        for d in Device.objects.filter(pk__in=peer_ids).select_related("site", "rack", "device_type", "role")
    }
    out = [HEADERS]
    for p in cabled:
        peer = p["peer"] or {}
        far = peers.get(peer.get("device_id"))
        far_port = peer.get("name", "")
        out.append(
            [
                p["cable"],
                p["cable_status"] or "",
                hub.get("role") or "",
                _seat(hub),
                hub["name"],
                p["name"],
                p["id"],
                _label(hub, p["name"]),
                p["cable_type"] or "",
                p["cable_length"],
                p["cable_unit"] or "",
                p["cable_label"] or "",
                "",
                "",
                (far or {}).get("role") or "",
                _seat(far),
                (far or {}).get("name") or peer.get("device") or "",
                far_port,
                peer.get("id") if peer.get("kind") == "interface" else "",
                _label(far, far_port) if far else far_port,
                absolute(f"/dcim/cables/{p['cable']}/"),
            ]
        )
    return out


def colours(table: list[list]) -> None:
    """Fill the colour and the span columns, which need the cable rows themselves."""
    by_id = {c.pk: c for c in Cable.objects.filter(pk__in=[r[0] for r in table[1:]])}
    for row in table[1:]:
        cable = by_id.get(row[0])
        if cable is None:
            continue
        row[12] = cable.color or ""
        row[13] = f"{row[9]:g}{row[10]}".upper() if row[9] is not None else ""


def workbook(device: Device, absolute=lambda path: path) -> bytes:
    table = rows(device, absolute)
    colours(table)
    return xlsx.workbook("Port Map", table, widths=WIDTHS, group_row=GROUPS)


def filename(device: Device, today) -> str:
    return f"{device.name or device.pk}_portmap_{today:%Y%m%d}.xlsx"
