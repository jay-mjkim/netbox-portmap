from django.urls import include, path
from utilities.urls import get_model_urls

from . import views

urlpatterns = (
    path("", views.WorkbenchView.as_view(), name="workbench"),
    path("devices/<int:pk>/", include(get_model_urls("dcim", "device"))),
)
