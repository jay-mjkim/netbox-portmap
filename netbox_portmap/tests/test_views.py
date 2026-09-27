from core.models import ObjectType
from django.test import TestCase
from django.urls import reverse
from users.models import ObjectPermission, User

from .test_api import build_fixture


class WorkbenchViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()
        cls.user = User.objects.create_user(username="admin", is_superuser=True)

    def setUp(self):
        self.client.force_login(self.user)

    def test_landing_without_hub(self):
        res = self.client.get(reverse("plugins:netbox_portmap:workbench"))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'id="pm-bench"')

    def test_workbench_with_hub(self):
        res = self.client.get(
            reverse("plugins:netbox_portmap:workbench"), {"hub": self.f.sw.pk, "rack": self.f.rack1.pk}
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="pm-bench"')
        self.assertContains(res, f"hubId: {self.f.sw.pk}")
        self.assertContains(res, "canEdit: true")
        self.assertContains(res, "workbench.js?v=")

    def test_device_tab(self):
        res = self.client.get(reverse("plugins:netbox_portmap:device_portmap", kwargs={"pk": self.f.srv1.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, f"hubId: {self.f.srv1.pk}")

    def test_read_only_user(self):
        viewer = User.objects.create_user(username="viewer")
        perm = ObjectPermission.objects.create(name="view", actions=["view"])
        perm.object_types.set(ObjectType.objects.filter(app_label="dcim", model__in=["device", "interface", "cable"]))
        perm.users.add(viewer)
        self.client.force_login(viewer)
        res = self.client.get(reverse("plugins:netbox_portmap:workbench"), {"hub": self.f.sw.pk})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "canEdit: false")

    def test_anonymous_redirects(self):
        self.client.logout()
        res = self.client.get(reverse("plugins:netbox_portmap:workbench"))
        self.assertEqual(res.status_code, 302)
