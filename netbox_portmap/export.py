"""
The port map as a spreadsheet, and the labels that go on the cables.

What is generic lives in the code: one row per cable with the NetBox cable id
first and the two interface ids last, so a row in the file can be found again
in NetBox and an edit lands on the right cable rather than on "row 37"; the
upstream end on the left; labels that name the device, the port, where it
sits and the cable's length.

What is a house convention lives in the ``export`` setting: the words in the
header row, how a rack is written on a label, how port names are shortened,
what a cable type is called, which sheet a label goes on. The defaults are
plain English and NetBox's own names; a site overrides what it keeps
differently (see README, "Export profile").
"""

from __future__ import annotations

import re

from dcim.choices import CableTypeChoices
from dcim.models import CableTermination, Device, Interface
from netbox.plugins import get_plugin_config

from . import data, media, xlsx

SCOPES = ("hub", "rack", "site")
KINDS = ("portmap", "labels")

# The sheet, column by column: NetBox id · A end · cable · B end · notes · NetBox ids and link.
DEFAULT_PROFILE = {
    "headers": [
        "Cable ID",
        "Role",
        "Rack U",
        "Device",
        "Port",
        "Direction",
        "Label A",
        "Type",
        "Length",
        "Span",
        "Role",
        "Rack U",
        "Device",
        "Port",
        "Label B",
        "Confirmed",
        "Side",
        "Planned",
        "Interface A",
        "Interface B",
        "NetBox",
    ],
    "group_labels": ["NetBox", "A end", "Cable", "B end", "Notes", "NetBox"],
    # The word written in the Direction column for every A -> B row; "" leaves it blank.
    "direction": "",
    # A rack on a label: "{rack} {position:g}U". ``seat_strip_prefix`` is a regex removed from
    # the rack name first ("^\\d+F-" turns 3F-03-18 into 03-18).
    "seat_format": "{rack} {position:g}U",
    "seat_strip_prefix": "",
    # A label: these parts, in this order, joined by the separator; empty parts are skipped.
    "label_fields": ["device", "port", "seat", "length"],
    "label_separator": ", ",
    # Port names on labels: [regex, replacement] pairs tried in order; the first match wins.
    "port_abbreviations": [],
    # Cable type -> the name the sheets use. Types not listed use NetBox's own label.
    "cable_type_labels": {},
    # Which label sheet a cable goes on, by the start of its NetBox type, in this order.
    "label_sheets": [["AOC", ["aoc"]], ["DAC", ["dac-"]], ["UTP", ["cat"]], ["Fiber", ["mmf", "smf"]]],
    "label_sheet_other": "Other",
    "label_headers": ["Label A", "Label B", "Length", "Cable ID"],
    "confirmed_mark": "✅",
    "planned_mark": "Planned",
    # Which end is written first: the lower tier. Regexes on the role name, upstream first; a
    # role that matches none is an end device (a server, a PDU) and goes last.
    "tiers": [r"firewall|\bfw\b|router", r"\bl3\b|spine|core", r"\bl2\b|leaf|access|\btor\b", r"switch"],
}
WIDTHS = [9, 20, 11, 18, 14, 12, 34, 20, 7, 7, 20, 11, 18, 14, 34, 10, 13, 9, 11, 11, 40]
LABEL_WIDTHS = [36, 36, 7, 9]
END_DEVICE = 9


def profile() -> dict:
    """The defaults with the deployment's overrides on top."""
    overrides = get_plugin_config("netbox_portmap", "export") or {}
    return {**DEFAULT_PROFILE, **{k: v for k, v in overrides.items() if v is not None}}


def tier_of(role: str | None, prof: dict) -> int:
    name = (role or "").lower()
    for tier, pattern in enumerate(prof["tiers"]):
        if re.search(pattern, name):
            return tier
    return END_DEVICE


def short_port(name: str, prof: dict) -> str:
    for pattern, repl in prof["port_abbreviations"]:
        if re.match(pattern, name or ""):
            return re.sub(pattern, repl, name)
    return name or ""


def _netbox_type_labels() -> dict:
    """NetBox's own names for cable types; CHOICES is grouped, so flatten it."""
    out = {}
    for value, label in CableTypeChoices.CHOICES:
        if isinstance(label, list | tuple):
            out.update({v: str(lbl) for v, lbl, *_ in label})
        else:
            out[value] = str(label)
    return out


def type_label(cable_type: str | None, prof: dict) -> str:
    key = cable_type or ""
    if key in prof["cable_type_labels"]:
        return prof["cable_type_labels"][key]
    return _netbox_type_labels().get(key, key)


def sheet_for(cable_type: str | None, prof: dict) -> str:
    key = cable_type or ""
    for name, prefixes in prof["label_sheets"]:
        if any(key.startswith(p) for p in prefixes):
            return name
    return prof["label_sheet_other"]


def _seat(summary: dict | None, prof: dict) -> str:
    if not summary or not summary.get("rack"):
        return ""
    rack = summary["rack"]
    if prof["seat_strip_prefix"]:
        rack = re.sub(prof["seat_strip_prefix"], "", rack)
    pos = summary.get("position")
    if pos is None:
        return rack
    return prof["seat_format"].format(rack=rack, position=pos)


def _label(summary: dict | None, port: str, length: str, prof: dict) -> str:
    if not summary:
        return ""
    values = {"device": summary.get("name", ""), "port": port, "seat": _seat(summary, prof), "length": length}
    return prof["label_separator"].join(v for v in (values.get(f, "") for f in prof["label_fields"]) if v)


def _length(cable) -> str:
    if cable.length is None:
        return ""
    return f"{float(cable.length):g}{(cable.length_unit or '').upper()}"


def _natural(name: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name or "")]


class Row:
    """One cable as a row, built once and read for either sheet."""

    def __init__(self, cable, src: Interface, dst: Interface, hub: dict, far: dict, prof: dict, absolute):
        sp, dp = short_port(src.name, prof), short_port(dst.name, prof)
        length = _length(cable)
        self.cable_type = cable.type or ""
        self.cells = [
            cable.pk,
            hub.get("role") or "",
            _seat(hub, prof),
            hub["name"],
            sp,
            prof["direction"],
            _label(hub, sp, length, prof),
            type_label(cable.type, prof),
            length,
            "",
            far.get("role") or "",
            _seat(far, prof),
            far["name"],
            dp,
            _label(far, dp, length, prof),
            prof["confirmed_mark"] if cable.status == "connected" else "",
            "",
            prof["planned_mark"] if cable.status == "planned" else "",
            src.pk,
            dst.pk,
            absolute(f"/dcim/cables/{cable.pk}/"),
        ]

    def label_cells(self) -> list:
        return [self.cells[6], self.cells[14], self.cells[8], self.cells[0]]


def _orient(a: Interface, b: Interface, summaries: dict, prof: dict) -> tuple[Interface, Interface]:
    """Which end is written first: a management port of its own cable; otherwise the upstream
    tier; a tie keeps NetBox's A end."""
    if a.mgmt_only != b.mgmt_only:
        return (a, b) if a.mgmt_only else (b, a)
    ta = tier_of(summaries[a.device_id].get("role"), prof)
    tb = tier_of(summaries[b.device_id].get("role"), prof)
    return (b, a) if tb < ta else (a, b)


def rows_for(devices, prof: dict, absolute=lambda path: path) -> list[Row]:
    """Every cable touching one of ``devices``, once each: upstream tier first, then the first
    end's rack and position (top of the rack first), then port."""
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
            pairs.append(_orient(a, b, summaries, prof))
    pairs.sort(
        key=lambda e: (
            tier_of(summaries[e[0].device_id].get("role"), prof),
            summaries[e[0].device_id].get("rack") or "",
            -(summaries[e[0].device_id].get("position") or 0),
            e[0].device.name or "",
            _natural(e[0].name),
        )
    )
    return [
        Row(src.cable, src, dst, summaries[src.device_id], summaries[dst.device_id], prof, absolute)
        for src, dst in pairs
    ]


def hub_rows(device: Device, prof: dict, absolute=lambda path: path) -> list[Row]:
    """This device's cables in the order the workbench draws its ports, the device first."""
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
        rows.append(Row(src.cable, src, far, hub, far_summaries[far.device_id], prof, absolute))
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


def workbook(device: Device, absolute=lambda path: path, scope: str = "hub", kind: str = "portmap") -> bytes:
    """``portmap``: one sheet, the hub (faceplate order) or every cable on its rack / site.
    ``labels``: the two label texts and the length per cable, one sheet per cable family."""
    prof = profile()
    rows = hub_rows(device, prof, absolute) if scope == "hub" else rows_for(_devices(device, scope), prof, absolute)
    if kind == "labels":
        sheets = []
        for name in [s[0] for s in prof["label_sheets"]] + [prof["label_sheet_other"]]:
            body = [r.label_cells() for r in rows if sheet_for(r.cable_type, prof) == name]
            if body:
                sheets.append(xlsx.Sheet(name, [prof["label_headers"]] + body, LABEL_WIDTHS))
        if not sheets:
            sheets.append(xlsx.Sheet("Labels", [prof["label_headers"]], LABEL_WIDTHS))
        return xlsx.workbook_sheets(sheets)
    groups = [(0, 0), (1, 6), (7, 9), (10, 14), (15, 17), (18, 20)]
    group_row = [(a, b, label) for (a, b), label in zip(groups, prof["group_labels"], strict=True)]
    return xlsx.workbook(
        _title(device, scope), [prof["headers"]] + [r.cells for r in rows], widths=WIDTHS, group_row=group_row
    )


def filename(device: Device, today, scope: str = "hub", kind: str = "portmap") -> str:
    return f"{_title(device, scope)}_{'labels' if kind == 'labels' else 'portmap'}_{today:%Y%m%d}.xlsx"
