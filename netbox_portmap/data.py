"""Read side: what the workbench shows for a device."""

from __future__ import annotations

from dcim.models import Cable, CableTermination, Device, Interface

from . import layout, media

INTERFACE_FIELDS = ("device", "cable")


def device_summary(device: Device) -> dict:
    return {
        "id": device.pk,
        "name": device.name or str(device),
        "url": device.get_absolute_url(),
        "site": device.site.name if device.site_id else None,
        "rack": device.rack.name if device.rack_id else None,
        "position": float(device.position) if device.position is not None else None,
        "face": device.face or None,
        "device_type": str(device.device_type),
        "role": str(device.role) if device.role_id else None,
    }


def _describe_peer(peer) -> dict:
    if not isinstance(peer, Interface):
        return {"kind": peer._meta.model_name, "name": str(peer), "device_id": getattr(peer, "device_id", None)}
    return {
        "kind": "interface",
        "id": peer.pk,
        "name": peer.name,
        "device_id": peer.device_id,
        "device": peer.device.name,
        "url": peer.get_absolute_url(),
    }


def _peers_of(interfaces) -> dict[int, dict]:
    """Far end of every interface's cable, keyed by interface id.

    ``Interface.link_peers`` costs two or three queries per port; a 48-port hub
    would issue well over a hundred. The far ends are read here in a handful of
    queries instead. Cables with a cable profile (breakouts) keep NetBox's own
    resolution, which maps positions.
    """
    by_cable = {itf.cable_id: itf for itf in interfaces if itf.cable_id}
    if not by_cable:
        return {}
    out: dict[int, dict] = {}
    simple = {pk: itf for pk, itf in by_cable.items() if not getattr(itf.cable, "profile", None)}
    for itf in by_cable.values():
        if itf.cable_id not in simple:
            peers = itf.link_peers
            if peers:
                out[itf.pk] = _describe_peer(peers[0])
    far: dict[int, CableTermination] = {}
    for term in CableTermination.objects.filter(cable_id__in=simple).select_related("termination_type").order_by("pk"):
        itf = simple[term.cable_id]
        if term.cable_end != itf.cable_end and term.cable_id not in far:
            far[term.cable_id] = term
    # Interfaces are the common case: fetch them in one query with their devices.
    itf_ct = {t.termination_type_id for t in far.values() if t.termination_type.model == "interface"}
    interfaces_by_id = Interface.objects.select_related("device").in_bulk(
        [t.termination_id for t in far.values() if t.termination_type_id in itf_ct]
    )
    for cable_id, term in far.items():
        if term.termination_type_id in itf_ct:
            peer = interfaces_by_id.get(term.termination_id)
        else:
            peer = term.termination
        if peer is not None:
            out[simple[cable_id].pk] = _describe_peer(peer)
    return out


def device_ports(device: Device) -> dict:
    """Device summary plus its cabling-relevant interfaces, laid out on a grid."""
    ports = []
    qs = list(
        Interface.objects.filter(device=device)
        .exclude(type__in=media.VIRTUAL_TYPES)
        .select_related("cable")
        .order_by("name")
    )
    peers = _peers_of(qs)
    for itf in qs:
        ports.append(
            {
                "id": itf.pk,
                "name": itf.name,
                "type": itf.type,
                "family": media.family_of(itf.type),
                "form_factor": media.form_factor(itf.type),
                "speed": media.speed_of(itf.type),
                "mgmt_only": itf.mgmt_only,
                "enabled": itf.enabled,
                "cable": itf.cable_id,
                "cable_type": itf.cable.type if itf.cable_id else None,
                "cable_length": float(itf.cable.length) if itf.cable_id and itf.cable.length is not None else None,
                "cable_unit": itf.cable.length_unit if itf.cable_id else None,
                "cable_label": itf.cable.label if itf.cable_id else "",
                "cable_status": itf.cable.status if itf.cable_id else None,
                "peer": peers.get(itf.pk),
                "url": itf.get_absolute_url(),
            }
        )
    grid = layout.arrange(ports)
    return {"device": device_summary(device), "ports": ports, "grid": grid}


def peer_devices(device: Device, user=None) -> list[dict]:
    """Devices this one has cables to, most-connected first.

    With ``user`` the list is limited to devices that user may view: the names
    and rack positions of devices a user cannot see must not leak through here.
    """
    counts: dict[int, int] = {}
    interfaces = list(Interface.objects.filter(device=device, cable__isnull=False).select_related("cable"))
    for peer in _peers_of(interfaces).values():
        did = peer.get("device_id")
        if did and did != device.pk:
            counts[did] = counts.get(did, 0) + 1
    devices = Device.objects.all() if user is None else Device.objects.restrict(user, "view")
    devices = devices.filter(pk__in=counts).select_related("site", "rack", "device_type", "role")
    out = [dict(device_summary(d), cables=counts[d.pk]) for d in devices]
    out.sort(key=lambda d: (-d["cables"], d["rack"] or "", -(d["position"] or 0)))
    return out


def cable_summary(cable: Cable) -> dict:
    return {
        "id": cable.pk,
        "type": cable.type,
        "status": cable.status,
        "label": cable.label,
        "length": float(cable.length) if cable.length is not None else None,
        "length_unit": cable.length_unit,
        "color": cable.color,
        "url": cable.get_absolute_url(),
    }
