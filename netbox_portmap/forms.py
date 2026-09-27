from dcim.models import Device, Rack, Site
from django import forms
from utilities.forms.fields import DynamicModelChoiceField


class WorkbenchForm(forms.Form):
    """Hub selection. Field names double as query parameters."""

    site = DynamicModelChoiceField(queryset=Site.objects.all(), required=False, label="Site")
    hub = DynamicModelChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Hub device",
        query_params={"site_id": "$site"},
    )
    rack = DynamicModelChoiceField(
        queryset=Rack.objects.all(),
        required=False,
        label="Shelf rack",
        query_params={"site_id": "$site"},
    )
