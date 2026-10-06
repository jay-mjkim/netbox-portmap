"""
The port map as a spreadsheet, and the labels that go on the cables.

The columns follow the port-map sheets the team keeps by hand (SRC · Cable ·
DST · Comment), with one difference that is the point of the download: the
first column is the cable's NetBox id, and each end carries its interface id.
A row in the file can then be found again in NetBox, and a change made from
the sheet lands on the right cable rather than on "row 37" — which moves the
moment somebody inserts a line above it.

Direction and sides follow the sheets' convention: the upstream end is SRC
(firewall over L3 over L2 and leaf over everything else), a management port
is always the SRC of its own cable, and a link is written as "Down" from SRC
to DST. Ties — a stack cable between two L3 switches — keep NetBox's A end as
SRC. Labels read ``HOST, PORT, RACK U`` with the rack's floor prefix dropped,
the way they are printed, and end with the cable's length.
"""

from __future__ import annotations

import re

from dcim.models import CableTermination, Device, Interface
from netbox.plugins import get_plugin_config

from . import data, media, xlsx

SCOPES = ("hub", "rack", "site")
KINDS = ("portmap", "labels")

GROUPS = [(0, 0, "NetBox"), (1, 6, "SRC"), (7, 9, "Cable"), (10, 14, "DST"), (15, 17, "Comment"), (18, 20, "NetBox")]
HEADERS = [
    "Cable ID",
    "종류",
    "실장",
    "Hostname",
    "Port",
    "Up/Down Link",
    "Label(상)",
    "Cable 타입",
    "길이",
    "구간",
    "종류",
    "실장",
    "Hostname",
    "Port",
    "Label(하)",
    "Confirmed",
    "좌/상 or 우/하",
    "Planned",
    "SRC Itf ID",
    "DST Itf ID",
    "NetBox",
]
WIDTHS = [9, 20, 11, 18, 12, 12, 32, 20, 7, 7, 20, 11, 18, 12, 32, 10, 13, 9, 10, 10, 40]
LABEL_HEADERS = ["Label(상)", "Label(하)", "길이", "Cable ID"]
LABEL_WIDTHS = [34, 34, 7, 9]

# What the sheets call a cable type. Anything not listed falls back to NetBox's own value.
TYPE_LABELS = {
    "aoc": "AOC HDR 200G",
    "dac-active": "DAC HDR 200G",
    "dac-passive": "DAC HDR 200G",
    "cat6": "UTP CAT 6",
    "cat6a": "UTP CAT 6a",
    "mmf-om2": "LC-LC Mutimode 1G",
    "mmf-om3": "LC-LC Mutimode 10G",
    "mmf-om4": "LC-LC Mutimode 10G",
    "smf": "LC-LC Singlemode 10G",
    "smf-os2": "LC-LC Singlemode 10G",
    "": "Stack Cable",
}
# Which label sheet a cable goes on, by the start of its NetBox type.
FAMILIES = (("AOC", ("aoc",)), ("DAC", ("dac-",)), ("UTP", ("cat",)), ("LC", ("mmf", "smf")))
OTHER = "기타"

# Upstream first. A role whose name matches none of these is an end device: a server, a PDU.
TIERS = ((r"firewall|\bfw\b|router", 0), (r"\bl3\b|spine|core", 1), (r"\bl2\b|leaf|access|\btor\b", 2), (r"switch", 3))
END_DEVICE = 9
_CISCO = r"^(?:GigabitEthernet|TenGigabitEthernet|TwentyFiveGigE|FortyGigabitEthernet|HundredGigE)\d+/"
PORT_SHORT = (
    (re.compile(_CISCO + r"0/(\d+)$"), r"\1"),
    (re.compile(_CISCO + r"1/(\d+)$"), r"+\1"),
    (re.compile(r"^port(\d+)$"), r"\1"),
)


def tier_of(role: str | None) -> int:
    name = (role or "").lower()
    for pattern, tier in TIERS:
        if re.search(pattern, name):
            return tier
    return END_DEVICE


def short_port(name: str) -> str:
    """``GigabitEthernet1/0/7`` -> ``7``, ``TenGigabitEthernet1/1/2`` -> ``+2``, ``port17`` -> ``17``."""
    for pattern, repl in PORT_SHORT:
        if pattern.match(name or ""):
            return pattern.sub(repl, name)
    return name or ""


def type_label(cable_type: str | None) -> str:
    key = cable_type or ""
    labels = get_plugin_config("netbox_portmap", "cable_type_labels") or {}
    return labels.get(key, TYPE_LABELS.get(key, key))


def family_of(cable_type: str | None) -> str:
    key = cable_type or ""
    for family, prefixes in FAMILIES:
        if any(key.startswith(p) for p in prefixes):
            return family
    return OTHER


def _seat(summary: dict | None) -> str:
    """Where the device sits, the way the sheets say it: ``03-18 42U`` — the rack with its
    floor prefix dropped (``3F-03-18`` is written ``03-18`` on a label)."""
    if not summary or not summary.get("rack"):
        return ""
    rack = summary["rack"]
    strip = get_plugin_config("netbox_portmap", "seat_strip_prefix")
    if strip:
        rack = re.sub(strip, "", rack)
    pos = summary.get("position")
    return f"{rack} {pos:g}U" if pos is not None else rack


def _label(summary: dict | None, port: str, length: str = "") -> str:
    """``ICN-CON-FW-01, x1, 03-18 42U, 5M`` — what is printed on the cable's tag: the device,
    the port, where it sits, and the cable's length so the right reel is picked up."""
    if not summary:
        return ""
    return ", ".join(part for part in (summary.get("name", ""), port, _seat(summary), length) if part)


def _length(cable) -> str:
    if cable.length is None:
        return ""
    return f"{float(cable.length):g}{(cable.length_unit or '').upper()}"


def _natural(name: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name or "")]


def _row(cable, src: Interface, dst: Interface, hub: dict, far: dict, direction: str, absolute) -> list:
    sp, dp = short_port(src.name), short_port(dst.name)
    length = _length(cable)
    return [
        cable.pk,
        hub.get("role") or "",
        _seat(hub),
        hub["name"],
        sp,
        direction,
        _label(hub, sp, length),
        type_label(cable.type),
        length,
        "",
        far.get("role") or "",
        _seat(far),
        far["name"],
        dp,
        _label(far, dp, length),
        "✅" if cable.status == "connected" else "",
        "",
        "Planned" if cable.status == "planned" else "",
        src.pk,
        dst.pk,
        absolute(f"/dcim/cables/{cable.pk}/"),
    ]


def _orient(a: Interface, b: Interface, summaries: dict) -> tuple[Interface, Interface]:
    """Which end is SRC: a management port of its own cable; otherwise the upstream tier;
    a tie keeps the A end."""
    if a.mgmt_only != b.mgmt_only:
        return (a, b) if a.mgmt_only else (b, a)
    ta, tb = tier_of(summaries[a.device_id].get("role")), tier_of(summaries[b.device_id].get("role"))
    return (b, a) if tb < ta else (a, b)


def entries_for(devices, absolute=lambda path: path) -> list[list]:
    """Every cable touching one of ``devices``, once each, as port-map rows: upstream tier
    first, then the SRC device's rack and position (top of the rack first), then port."""
    devices = list(devices)
    by_pk = {d.pk: d for d in devices}
    interfaces = (
        Interface.objects.filter(device__in=devices, cable__isnull=False)
        .exclude(type__in=media.VIRTUAL_TYPES)
        .select_related("cable", "device")
    )
    cable_ids = {itf.cable_id for itf in interfaces}
    ends: dict[int, dict[str, int]] = {}
    far_ids = set()
    terminations = CableTermination.objects.filter(cable_id__in=cable_ids).select_related("termination_type")
    for term in terminations.order_by("pk"):
        if term.termination_type.model != "interface":
            continue
        ends.setdefault(term.cable_id, {}).setdefault(term.cable_end, term.termination_id)
        far_ids.add(term.termination_id)
    itf_by_id = Interface.objects.filter(pk__in=far_ids).select_related("device", "cable").in_bulk()
    far_devices = {i.device_id for i in itf_by_id.values()} - set(by_pk)
    for d in Device.objects.filter(pk__in=far_devices).select_related("site", "rack", "device_type", "role"):
        by_pk[d.pk] = d
    summaries = {pk: data.device_summary(d) for pk, d in by_pk.items()}
    pairs = []
    for side in ends.values():
        a, b = itf_by_id.get(side.get("A")), itf_by_id.get(side.get("B"))
        if a is not None and b is not None:
            pairs.append(_orient(a, b, summaries))
    pairs.sort(
        key=lambda e: (
            tier_of(summaries[e[0].device_id].get("role")),
            summaries[e[0].device_id].get("rack") or "",
            -(summaries[e[0].device_id].get("position") or 0),
            e[0].device.name or "",
            _natural(e[0].name),
        )
    )
    return [
        _row(src.cable, src, dst, summaries[src.device_id], summaries[dst.device_id], "Down", absolute)
        for src, dst in pairs
    ]


def hub_rows(device: Device, absolute=lambda path: path) -> list[list]:
    """This device's cables in the order the workbench draws its ports, the device as SRC."""
    ports = data.device_ports(device)
    cabled = sorted((p for p in ports["ports"] if p["cable"]), key=lambda p: (p.get("col", 0), p.get("row", 0)))
    itfs = Interface.objects.filter(pk__in=[p["id"] for p in cabled]).select_related("cable", "device").in_bulk()
    peer_ids = {p["peer"]["id"] for p in cabled if p["peer"] and p["peer"].get("kind") == "interface"}
    peers = (
        Interface.objects.filter(pk__in=peer_ids)
        .select_related("device", "device__site", "device__rack", "device__role", "device__device_type")
        .in_bulk()
    )
    hub = ports["device"]
    far_summaries = {d.pk: data.device_summary(d) for d in {i.device for i in peers.values()}}
    rows = []
    for p in cabled:
        far = peers.get((p["peer"] or {}).get("id"))
        if far is None:
            continue
        src = itfs[p["id"]]
        rows.append(_row(src.cable, src, far, hub, far_summaries[far.device_id], "Down", absolute))
    return rows


def _devices(device: Device, scope: str):
    if scope == "rack":
        return Device.objects.filter(rack=device.rack) if device.rack_id else [device]
    return Device.objects.filter(site=device.site)


def _title(device: Device, scope: str) -> str:
    if scope == "rack" and device.rack_id:
        return device.rack.name
    if scope == "site":
        return device.site.name
    return device.name or str(device.pk)


def _family_of_row(row: list) -> str:
    """Rows carry the sheet's type label; map it back for the label sheets."""
    label = row[7]
    labels = {**TYPE_LABELS, **(get_plugin_config("netbox_portmap", "cable_type_labels") or {})}
    for key, value in labels.items():
        if value == label:
            return family_of(key)
    return family_of(label)


def workbook(device: Device, absolute=lambda path: path, scope: str = "hub", kind: str = "portmap") -> bytes:
    """``portmap``: one sheet, the hub (faceplate order) or every cable on its rack / site.
    ``labels``: the two label texts and the length per cable, one sheet per cable family."""
    rows = hub_rows(device, absolute) if scope == "hub" else entries_for(_devices(device, scope), absolute)
    if kind == "labels":
        sheets = []
        for family in [f for f, _ in FAMILIES] + [OTHER]:
            body = [[r[6], r[14], r[8], r[0]] for r in rows if _family_of_row(r) == family]
            if body:
                sheets.append(xlsx.Sheet(family, [LABEL_HEADERS] + body, LABEL_WIDTHS))
        if not sheets:
            sheets.append(xlsx.Sheet("Labels", [LABEL_HEADERS], LABEL_WIDTHS))
        return xlsx.workbook_sheets(sheets)
    return xlsx.workbook(_title(device, scope), [HEADERS] + rows, widths=WIDTHS, group_row=GROUPS)


def filename(device: Device, today, scope: str = "hub", kind: str = "portmap") -> str:
    return f"{_title(device, scope)}_{'labels' if kind == 'labels' else 'portmap'}_{today:%Y%m%d}.xlsx"
