import json

from dcim.choices import CableLengthUnitChoices, CableTypeChoices, LinkStatusChoices
from dcim.models import Device
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import render
from django.views.generic import View
from netbox.plugins import get_plugin_config
from utilities.views import ViewTab, register_model_view

from .forms import WorkbenchForm


def _choices(choice_set):
    """Flatten a NetBox ChoiceSet into [{"group": str|None, "options": [[value, label], ...]}]."""
    out = []
    for item in choice_set.CHOICES:  # (value, label[, color]) or (group, [(value, label[, color]), ...])
        value, label = item[0], item[1]
        if isinstance(label, list | tuple):
            out.append({"group": str(value), "options": [[opt[0], str(opt[1])] for opt in label]})
        else:
            out.append({"group": None, "options": [[value, str(label)]]})
    return out


def _client_config():
    return {
        "cable_types": _choices(CableTypeChoices),
        "statuses": _choices(LinkStatusChoices),
        "length_units": _choices(CableLengthUnitChoices),
        "default_status": get_plugin_config("netbox_portmap", "cable_status") or LinkStatusChoices.STATUS_PLANNED,
    }


class WorkbenchView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    The hub/spoke workbench: ``?hub=<device id>`` picks the hub, everything
    else happens in the browser against the plugin API.
    """

    permission_required = ("dcim.view_device", "dcim.view_interface", "dcim.view_cable")
    template_name = "netbox_portmap/workbench.html"

    def get(self, request):
        form = WorkbenchForm(request.GET or None)
        hub = None
        hub_id = request.GET.get("hub")
        if hub_id and hub_id.isdigit():
            hub = Device.objects.restrict(request.user, "view").filter(pk=hub_id).first()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "hub": hub,
                "can_edit": request.user.has_perms(("dcim.add_cable", "dcim.change_cable", "dcim.delete_cable")),
                "pm_config": json.dumps(_client_config()),
            },
        )


@register_model_view(Device, name="portmap", path="portmap")
class DevicePortMapView(WorkbenchView):
    """Same workbench, reached from a device page with that device as the hub."""

    tab = ViewTab(label="Port Map", permission="dcim.view_cable", weight=1200)

    def get(self, request, pk):
        request.GET = request.GET.copy()
        request.GET["hub"] = str(pk)
        if hub := Device.objects.filter(pk=pk).select_related("site").first():
            request.GET.setdefault("site", str(hub.site_id))
        return super().get(request)
