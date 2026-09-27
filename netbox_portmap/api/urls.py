from netbox.api.routers import NetBoxRouter

from . import views

router = NetBoxRouter()
router.register("devices", views.DeviceViewSet, basename="portmap-device")
router.register("racks", views.RackViewSet, basename="portmap-rack")
router.register("check", views.CheckViewSet, basename="portmap-check")
router.register("commit", views.CommitViewSet, basename="portmap-commit")

urlpatterns = router.urls
