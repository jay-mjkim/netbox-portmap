"""
Workbench API.

    GET  devices/<id>/ports/        the device, its ports on a grid, cables and peers
    GET  devices/<id>/peers/        devices it has cables to (the initial spoke list)
    GET  racks/<id>/devices/        racked devices of a rack (the shelf)
    POST check/                     {"a": <interface id>, "b": <interface id>}
                                    -> compatibility verdict + suggested type/length
    POST commit/                    {"create": [...], "update": [...], "delete": [...]}
                                    -> applied in one transaction, all or nothing

The UI keeps its changes client-side and sends them once with ``commit``, so a
half-finished session never leaves partial cabling behind.
"""

from __future__ import annotations

from dcim.models import Cable, Device, Interface, Rack
from django.db import transaction
from django.shortcuts import get_object_or_404
from netbox.api.authentication import TokenPermissions
from netbox.plugins import get_plugin_config
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .. import data, lengths, media


class ReadOnlyPermissions(TokenPermissions):
    """Read endpoints: dcim.view_device / view_interface as NetBox would require."""

    perms_map = {**TokenPermissions.perms_map, "POST": ["%(app_label)s.view_%(model_name)s"]}


class DeviceViewSet(viewsets.ViewSet):
    queryset = Device.objects.none()
    permission_classes = [ReadOnlyPermissions]
    schema = None

    def _device(self, request, pk):
        return get_object_or_404(
            Device.objects.restrict(request.user, "view").select_related("site", "rack", "device_type", "role"),
            pk=pk,
        )

    @action(detail=True, methods=["get"])
    def ports(self, request, pk=None):
        return Response(data.device_ports(self._device(request, pk)))

    @action(detail=True, methods=["get"])
    def peers(self, request, pk=None):
        return Response({"peers": data.peer_devices(self._device(request, pk))})


class RackViewSet(viewsets.ViewSet):
    queryset = Rack.objects.none()
    permission_classes = [ReadOnlyPermissions]
    schema = None

    @action(detail=True, methods=["get"])
    def devices(self, request, pk=None):
        rack = get_object_or_404(Rack.objects.restrict(request.user, "view"), pk=pk)
        devices = (
            Device.objects.restrict(request.user, "view")
            .filter(rack=rack)
            .exclude(device_type__u_height=0)
            .select_related("site", "rack", "device_type", "role")
            .order_by("-position", "name")
        )
        return Response(
            {"rack": {"id": rack.pk, "name": rack.name}, "devices": [data.device_summary(d) for d in devices]}
        )


class CheckSerializer(serializers.Serializer):
    a = serializers.IntegerField()
    # One target ("b") or many ("targets"): arming a port checks every visible port at once.
    b = serializers.IntegerField(required=False)
    targets = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    # Cables the client has marked for deletion; their ports count as free.
    free_cables = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)

    def validate(self, attrs):
        if "b" not in attrs and not attrs.get("targets"):
            raise serializers.ValidationError("Provide 'b' or 'targets'.")
        return attrs


class CheckViewSet(viewsets.ViewSet):
    """Compatibility of two interfaces plus suggested cable type and length."""

    queryset = Interface.objects.none()
    permission_classes = [ReadOnlyPermissions]
    schema = None

    def create(self, request):
        s = CheckSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        qs = Interface.objects.restrict(request.user, "view").select_related("device__rack", "cable")
        a = get_object_or_404(qs, pk=v["a"])
        free = set(v["free_cables"])
        if "b" in v:
            b = get_object_or_404(qs, pk=v["b"])
            return Response(_check(a, b, free))
        results = {b.pk: _check(a, b, free) for b in qs.filter(pk__in=v["targets"])}
        return Response({"results": results})


def _blocked(reason: str) -> dict:
    return {"level": "block", "reason": reason, "cable_type": None, "length": None, "length_unit": None}


def _check(a: Interface, b: Interface, free: set | None = None) -> dict:
    free = free or set()
    if a.pk == b.pk:
        return _blocked("Same port")
    if a.device_id == b.device_id:
        return _blocked("Same device")
    if a.cable_id and a.cable_id not in free:
        return _blocked(f"{a.device.name} {a.name} is already cabled (cable #{a.cable_id})")
    if b.cable_id and b.cable_id not in free:
        return _blocked(f"{b.device.name} {b.name} is already cabled (cable #{b.cable_id})")
    verdict = media.check(a.type, b.type, a.mgmt_only, b.mgmt_only)
    length = lengths.suggest(a.device, b.device) if verdict.allowed else None
    return {
        "level": verdict.level,
        "reason": verdict.reason,
        "cable_type": verdict.cable_type,
        "length": length[0] if length else None,
        "length_unit": length[1] if length else None,
    }


class CableCreateSerializer(serializers.Serializer):
    a = serializers.IntegerField()
    b = serializers.IntegerField()
    type = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.CharField(required=False, allow_blank=True, default="")
    label = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)
    color = serializers.CharField(required=False, allow_blank=True, default="", max_length=6)
    length = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, allow_null=True)
    length_unit = serializers.CharField(required=False, allow_blank=True, default="")
    ref = serializers.CharField(required=False, allow_blank=True, default="")  # client-side id echoed back


class CableUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    type = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    label = serializers.CharField(required=False, allow_blank=True, max_length=100)
    color = serializers.CharField(required=False, allow_blank=True, max_length=6)
    length = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, allow_null=True)
    length_unit = serializers.CharField(required=False, allow_blank=True)


class CommitSerializer(serializers.Serializer):
    create = serializers.ListField(child=CableCreateSerializer(), required=False, default=list)
    update = serializers.ListField(child=CableUpdateSerializer(), required=False, default=list)
    delete = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)


class CommitPermissions(TokenPermissions):
    perms_map = {**TokenPermissions.perms_map, "POST": ["%(app_label)s.change_%(model_name)s"]}


class CommitViewSet(viewsets.ViewSet):
    queryset = Cable.objects.none()
    permission_classes = [IsAuthenticated, CommitPermissions]
    schema = None

    def create(self, request):
        s = CommitSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        payload = s.validated_data
        user = request.user
        if payload["create"] and not user.has_perm("dcim.add_cable"):
            return Response({"detail": "Missing permission: dcim.add_cable"}, status=status.HTTP_403_FORBIDDEN)
        if payload["delete"] and not user.has_perm("dcim.delete_cable"):
            return Response({"detail": "Missing permission: dcim.delete_cable"}, status=status.HTTP_403_FORBIDDEN)
        default_status = get_plugin_config("netbox_portmap", "cable_status") or "planned"
        created, errors = [], []
        try:
            with transaction.atomic():
                # Deletes first: a port freed here may be reused by a create below.
                if payload["delete"]:
                    qs = Cable.objects.restrict(user, "delete").filter(pk__in=payload["delete"])
                    found = {c.pk for c in qs}
                    for pk in payload["delete"]:
                        if pk not in found:
                            errors.append({"op": "delete", "id": pk, "error": "not found or not permitted"})
                    for c in qs:
                        c.delete()
                for item in payload["update"]:
                    cable = Cable.objects.restrict(user, "change").filter(pk=item["id"]).first()
                    if cable is None:
                        errors.append({"op": "update", "id": item["id"], "error": "not found or not permitted"})
                        continue
                    for f in ("type", "status", "label", "color", "length", "length_unit"):
                        if f in item:
                            setattr(
                                cable,
                                f,
                                item[f] if item[f] != "" or f in ("label", "color", "type", "length_unit") else None,
                            )
                    cable.full_clean()
                    cable.save()
                ifaces = Interface.objects.restrict(user, "view").select_related("device__rack")
                for item in payload["create"]:
                    a = ifaces.filter(pk=item["a"]).first()
                    b = ifaces.filter(pk=item["b"]).first()
                    if a is None or b is None:
                        errors.append({"op": "create", "ref": item["ref"], "error": "interface not found"})
                        continue
                    # Re-evaluate on the server: the client may be stale.
                    a.refresh_from_db(fields=["cable"])
                    b.refresh_from_db(fields=["cable"])
                    verdict = _check(a, b)
                    if verdict["level"] == "block":
                        errors.append({"op": "create", "ref": item["ref"], "error": verdict["reason"]})
                        continue
                    cable = Cable(
                        a_terminations=[a],
                        b_terminations=[b],
                        type=item.get("type") or verdict.get("cable_type") or "",
                        status=item.get("status") or default_status,
                        label=item.get("label") or "",
                        color=item.get("color") or "",
                        length=item.get("length") if item.get("length") is not None else verdict.get("length"),
                        length_unit=item.get("length_unit") or verdict.get("length_unit") or "",
                    )
                    cable.full_clean()
                    cable.save()
                    created.append({"ref": item["ref"], "cable": data.cable_summary(cable)})
                if errors:
                    raise _Rollback()
        except _Rollback:
            return Response({"applied": False, "errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # validation errors from Cable.full_clean, etc.
            return Response(
                {"applied": False, "errors": [{"op": "commit", "error": str(exc)}]}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            {"applied": True, "created": created, "updated": len(payload["update"]), "deleted": len(payload["delete"])},
            status=status.HTTP_201_CREATED,
        )


class _Rollback(Exception):
    """Raised inside the transaction to roll everything back when any item failed."""
