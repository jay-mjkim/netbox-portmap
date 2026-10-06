"""
The port map as a spreadsheet: one row per cable on the hub, in faceplate order.

The columns follow the port-map sheets the team keeps by hand (SRC · Cable ·
DST), with one difference that is the point of the download: the first column
is the cable's NetBox id, and each end carries its interface id, so a row in
the file can be found again in NetBox and a change made from the sheet lands
on the right cable rather than on "row 37".
"""

from __future__ import annotations

import re

from dcim.models import Cable, CableTermination, Device, Interface

from . import data, media, xlsx

SCOPES = ("hub", "rack", "site")

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


def _natural(name: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name or "")]


def rows_for(devices, absolute=lambda path: path) -> list[list]:
    """Every cable touching one of ``devices``, once each — a rack or a site on one sheet.

    A cable has no direction in NetBox, but a port map has: the sheets put the server on the
    left and the switch on the right. The end whose device has fewer cabled ports is written as
    SRC (a server has a handful, a switch has dozens); between equals the A end is SRC. Rows
    are ordered by the SRC device's rack and position, top of the rack first, then port.
    """
    devices = list(devices)
    by_pk = {d.pk: d for d in devices}
    interfaces = (
        Interface.objects.filter(device__in=devices, cable__isnull=False)
        .exclude(type__in=media.VIRTUAL_TYPES)
        .select_related("cable", "device")
    )
    cabled_count: dict[int, int] = {}
    for itf in interfaces:
        cabled_count[itf.device_id] = cabled_count.get(itf.device_id, 0) + 1
    cable_ids = {itf.cable_id for itf in interfaces}
    ends: dict[int, dict[str, Interface]] = {}
    far_ids = set()
    for term in (
        CableTermination.objects.filter(cable_id__in=cable_ids).select_related("termination_type").order_by("pk")
    ):
        if term.termination_type.model != "interface":
            continue
        ends.setdefault(term.cable_id, {}).setdefault(term.cable_end, term.termination_id)
        far_ids.add(term.termination_id)
    itf_by_id = Interface.objects.filter(pk__in=far_ids).select_related("device", "cable").in_bulk()
    far_devices = {i.device_id for i in itf_by_id.values()} - set(by_pk)
    for d in Device.objects.filter(pk__in=far_devices).select_related("site", "rack", "device_type", "role"):
        by_pk[d.pk] = d
    for itf in Interface.objects.filter(device_id__in=far_devices, cable__isnull=False):
        cabled_count[itf.device_id] = cabled_count.get(itf.device_id, 0) + 1
    summaries = {pk: data.device_summary(d) for pk, d in by_pk.items()}
    entries = []
    for side in ends.values():
        a, b = itf_by_id.get(side.get("A")), itf_by_id.get(side.get("B"))
        if a is None or b is None:
            continue
        src, dst = (a, b) if cabled_count.get(a.device_id, 0) <= cabled_count.get(b.device_id, 0) else (b, a)
        entries.append((src, dst))
    entries.sort(
        key=lambda e: (
            (summaries[e[0].device_id].get("rack") or ""),
            -(summaries[e[0].device_id].get("position") or 0),
            e[0].device.name or "",
            _natural(e[0].name),
        )
    )
    out = [HEADERS]
    for src, dst in entries:
        cable = src.cable
        hub, far = summaries[src.device_id], summaries[dst.device_id]
        out.append(
            [
                cable.pk,
                cable.status or "",
                hub.get("role") or "",
                _seat(hub),
                hub["name"],
                src.name,
                src.pk,
                _label(hub, src.name),
                cable.type or "",
                float(cable.length) if cable.length is not None else None,
                cable.length_unit or "",
                cable.label or "",
                "",
                "",
                far.get("role") or "",
                _seat(far),
                far["name"],
                dst.name,
                dst.pk,
                _label(far, dst.name),
                absolute(f"/dcim/cables/{cable.pk}/"),
            ]
        )
    return out


def workbook(device: Device, absolute=lambda path: path, scope: str = "hub") -> bytes:
    """``hub``: this device's cables in faceplate order. ``rack`` / ``site``: every cable on
    the hub's rack or site, one sheet, the port map the team keeps."""
    if scope == "hub":
        table = rows(device, absolute)
    elif scope == "rack":
        table = rows_for(Device.objects.filter(rack=device.rack) if device.rack_id else [device], absolute)
    else:
        table = rows_for(Device.objects.filter(site=device.site), absolute)
    colours(table)
    title = {
        "hub": "Port Map",
        "rack": f"Rack {device.rack.name}" if device.rack_id else "Port Map",
        "site": str(device.site),
    }[scope]
    return xlsx.workbook(title, table, widths=WIDTHS, group_row=GROUPS)


def filename(device: Device, today, scope: str = "hub") -> str:
    stem = {
        "hub": device.name or str(device.pk),
        "rack": device.rack.name if device.rack_id else (device.name or str(device.pk)),
        "site": device.site.name,
    }[scope]
    return f"{stem}_portmap_{today:%Y%m%d}.xlsx"
