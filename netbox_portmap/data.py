"""Read side: what the workbench shows for a device."""

from __future__ import annotations

from dcim.models import Cable, Device, Interface

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


def _peer_of(interface: Interface):
    """The far end of the interface's cable, if it is another device's interface."""
    peers = interface.link_peers
    if not peers:
        return None
    peer = peers[0]
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


def device_ports(device: Device) -> dict:
    """Device summary plus its cabling-relevant interfaces, laid out on a grid."""
    ports = []
    qs = (
        Interface.objects.filter(device=device)
        .exclude(type__in=media.VIRTUAL_TYPES)
        .select_related("cable")
        .order_by("name")
    )
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
                "peer": _peer_of(itf),
                "url": itf.get_absolute_url(),
            }
        )
    grid = layout.arrange(ports)
    return {"device": device_summary(device), "ports": ports, "grid": grid}


def peer_devices(device: Device) -> list[dict]:
    """Devices this one has cables to, most-connected first."""
    counts: dict[int, int] = {}
    for itf in Interface.objects.filter(device=device, cable__isnull=False).select_related("cable"):
        for peer in itf.link_peers:
            did = getattr(peer, "device_id", None)
            if did and did != device.pk:
                counts[did] = counts.get(did, 0) + 1
    devices = Device.objects.filter(pk__in=counts).select_related("site", "rack", "device_type", "role")
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
