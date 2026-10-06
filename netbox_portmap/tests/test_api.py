from types import SimpleNamespace

from core.models import ObjectType
from dcim.models import Cable, Device, DeviceRole, DeviceType, Interface, Manufacturer, Rack, Site
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from users.models import ObjectPermission
from utilities.testing import APITestCase


def build_fixture():
    """A switch, two servers (one in another rack) and a 0U PDU; one existing cable."""
    site = Site.objects.create(name="Site 1", slug="site-1")
    mfr = Manufacturer.objects.create(name="Mfr", slug="mfr")
    sw_type = DeviceType.objects.create(manufacturer=mfr, model="Switch", slug="switch", u_height=1)
    srv_type = DeviceType.objects.create(manufacturer=mfr, model="Server", slug="server", u_height=2)
    pdu_type = DeviceType.objects.create(manufacturer=mfr, model="PDU", slug="pdu", u_height=0)
    role = DeviceRole.objects.create(name="Role", slug="role")
    f = SimpleNamespace()
    f.rack1 = Rack.objects.create(site=site, name="R-01")
    f.rack2 = Rack.objects.create(site=site, name="R-03")
    f.sw = Device.objects.create(
        name="sw1", site=site, rack=f.rack1, position=40, face="front", device_type=sw_type, role=role
    )
    f.srv1 = Device.objects.create(
        name="srv1", site=site, rack=f.rack1, position=10, face="front", device_type=srv_type, role=role
    )
    f.srv2 = Device.objects.create(
        name="srv2", site=site, rack=f.rack2, position=10, face="front", device_type=srv_type, role=role
    )
    f.pdu = Device.objects.create(name="pdu1", site=site, rack=f.rack1, device_type=pdu_type, role=role)
    f.sw_ports = [
        Interface.objects.create(device=f.sw, name=f"GigabitEthernet1/0/{i}", type="1000base-t") for i in range(1, 9)
    ]
    f.sw_sfp = Interface.objects.create(device=f.sw, name="TenGigabitEthernet1/1/1", type="10gbase-x-sfpp")
    f.sw_mgmt = Interface.objects.create(device=f.sw, name="mgmt0", type="1000base-t", mgmt_only=True)
    f.sw_lag = Interface.objects.create(device=f.sw, name="Port-channel1", type="lag")
    f.srv1_eno0 = Interface.objects.create(device=f.srv1, name="eno0", type="1000base-t")
    f.srv1_eno1 = Interface.objects.create(device=f.srv1, name="eno1", type="1000base-t")
    f.srv1_ib0 = Interface.objects.create(device=f.srv1, name="ib0", type="infiniband-hdr")
    f.srv2_eno0 = Interface.objects.create(device=f.srv2, name="eno0", type="10gbase-t")
    f.srv2_sfp = Interface.objects.create(device=f.srv2, name="ens1f0", type="10gbase-x-sfpp")
    f.cable = Cable(a_terminations=[f.sw_ports[0]], b_terminations=[f.srv1_eno0], type="cat6", status="connected")
    f.cable.full_clean()
    f.cable.save()
    return f


VIEW_PERMS = ("dcim.view_device", "dcim.view_interface", "dcim.view_cable", "dcim.view_rack")
CABLE_PERMS = ("dcim.add_cable", "dcim.change_cable", "dcim.delete_cable")


class ReadEndpointsTest(APITestCase):
    user_permissions = VIEW_PERMS

    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()

    def test_ports(self):
        url = reverse("plugins-api:netbox_portmap-api:portmap-device-ports", kwargs={"pk": self.f.sw.pk})
        res = self.client.get(url, **self.header)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["device"]["name"], "sw1")
        names = [p["name"] for p in body["ports"]]
        self.assertNotIn("Port-channel1", names)  # virtual interfaces are not shown
        self.assertIn("mgmt0", names)
        self.assertEqual(body["grid"]["rows"], 2)
        first = next(p for p in body["ports"] if p["name"] == "GigabitEthernet1/0/1")
        self.assertEqual(first["family"], "copper")
        self.assertEqual(first["cable"], self.f.cable.pk)
        self.assertEqual(first["peer"]["device"], "srv1")
        self.assertEqual(first["peer"]["id"], self.f.srv1_eno0.pk)
        self.assertEqual(first["cable_type"], "cat6")
        self.assertIn("row", first)
        self.assertIn("col", first)

    def test_peers(self):
        url = reverse("plugins-api:netbox_portmap-api:portmap-device-peers", kwargs={"pk": self.f.sw.pk})
        body = self.client.get(url, **self.header).json()
        self.assertEqual([p["name"] for p in body["peers"]], ["srv1"])
        self.assertEqual(body["peers"][0]["cables"], 1)

    def test_rack_devices_exclude_zero_u(self):
        url = reverse("plugins-api:netbox_portmap-api:portmap-rack-devices", kwargs={"pk": self.f.rack1.pk})
        body = self.client.get(url, **self.header).json()
        self.assertEqual([d["name"] for d in body["devices"]], ["sw1", "srv1"])  # top of rack first, no PDU

    def test_ports_query_count_does_not_grow_with_cables(self):
        url = reverse("plugins-api:netbox_portmap-api:portmap-device-ports", kwargs={"pk": self.f.sw.pk})
        with CaptureQueriesContext(connection) as one:
            self.client.get(url, **self.header)
        extra = []
        for i, port in enumerate(self.f.sw_ports[1:5]):
            peer = Interface.objects.create(device=self.f.srv2, name=f"eth{i}", type="1000base-t")
            cable = Cable(a_terminations=[port], b_terminations=[peer], type="cat6")
            cable.full_clean()
            cable.save()
            extra.append(peer)
        with CaptureQueriesContext(connection) as five:
            body = self.client.get(url, **self.header).json()
        # Peers are resolved in bulk: four more cables must not mean a dozen more queries.
        self.assertLessEqual(len(five), len(one) + 2)
        by_name = {p["name"]: p for p in body["ports"]}
        self.assertEqual(by_name["GigabitEthernet1/0/2"]["peer"]["device"], "srv2")
        self.assertEqual(by_name["GigabitEthernet1/0/2"]["peer"]["id"], extra[0].pk)
        self.assertEqual(by_name["GigabitEthernet1/0/1"]["peer"]["device"], "srv1")
        self.assertIsNone(by_name["GigabitEthernet1/0/6"]["peer"])

    def test_ports_requires_auth(self):
        url = reverse("plugins-api:netbox_portmap-api:portmap-device-ports", kwargs={"pk": self.f.sw.pk})
        self.assertIn(self.client.get(url).status_code, (401, 403))


class RestrictedPeersTest(APITestCase):
    """A user who may not see a device must not learn its name or position from the peer list."""

    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()
        cable = Cable(a_terminations=[cls.f.sw_ports[1]], b_terminations=[cls.f.srv2_eno0], type="cat6")
        cable.full_clean()
        cable.save()

    def setUp(self):
        super().setUp()
        perm = ObjectPermission.objects.create(
            name="view all but srv2", actions=["view"], constraints={"name__in": ["sw1", "srv1"]}
        )
        perm.object_types.set([ObjectType.objects.get(app_label="dcim", model="device")])
        perm.users.add(self.user)
        for model in ("interface", "cable"):
            p = ObjectPermission.objects.create(name=f"view {model}", actions=["view"])
            p.object_types.set([ObjectType.objects.get(app_label="dcim", model=model)])
            p.users.add(self.user)

    def test_peers_hide_devices_the_user_cannot_view(self):
        url = reverse("plugins-api:netbox_portmap-api:portmap-device-peers", kwargs={"pk": self.f.sw.pk})
        res = self.client.get(url, **self.header)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([p["name"] for p in res.json()["peers"]], ["srv1"])


class CheckTest(APITestCase):
    user_permissions = VIEW_PERMS

    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()
        cls.url = reverse("plugins-api:netbox_portmap-api:portmap-check-list")

    def check(self, **payload):
        res = self.client.post(self.url, payload, format="json", **self.header)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_ok_with_suggestions(self):
        v = self.check(a=self.f.sw_ports[1].pk, b=self.f.srv1_eno1.pk)
        self.assertEqual(v["level"], "ok")
        self.assertEqual(v["cable_type"], "cat6")
        self.assertEqual((v["length"], v["length_unit"]), (3, "m"))  # same rack

    def test_length_across_racks(self):
        v = self.check(a=self.f.sw_ports[1].pk, b=self.f.srv2_eno0.pk)
        self.assertEqual(v["level"], "warn")  # 1G <-> 10G
        self.assertEqual(v["length"], 5)  # |1-3| * 0.6 + 3 = 4.2 -> 5

    def test_media_mismatch_blocks(self):
        v = self.check(a=self.f.sw_ports[1].pk, b=self.f.srv2_sfp.pk)
        self.assertEqual(v["level"], "block")

    def test_same_device_blocks(self):
        self.assertEqual(self.check(a=self.f.sw_ports[1].pk, b=self.f.sw_ports[2].pk)["level"], "block")

    def test_cabled_port_blocks_unless_freed(self):
        v = self.check(a=self.f.sw_ports[0].pk, b=self.f.srv1_eno1.pk)
        self.assertEqual(v["level"], "block")
        self.assertIn("already cabled", v["reason"])
        v = self.check(a=self.f.sw_ports[0].pk, b=self.f.srv1_eno1.pk, free_cables=[self.f.cable.pk])
        self.assertEqual(v["level"], "ok")

    def test_batch(self):
        targets = [self.f.srv1_eno0.pk, self.f.srv1_eno1.pk, self.f.srv1_ib0.pk, self.f.sw_ports[2].pk]
        body = self.check(a=self.f.sw_ports[1].pk, targets=targets)
        results = {int(k): v["level"] for k, v in body["results"].items()}
        self.assertEqual(
            results,
            {
                self.f.srv1_eno0.pk: "block",  # cabled
                self.f.srv1_eno1.pk: "ok",
                self.f.srv1_ib0.pk: "block",  # copper vs qsfp
                self.f.sw_ports[2].pk: "block",  # same device
            },
        )

    def test_requires_target(self):
        res = self.client.post(self.url, {"a": self.f.sw_ports[1].pk}, format="json", **self.header)
        self.assertEqual(res.status_code, 400)


class CommitTest(APITestCase):
    user_permissions = VIEW_PERMS + CABLE_PERMS

    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()
        cls.url = reverse("plugins-api:netbox_portmap-api:portmap-commit-list")

    def commit(self, payload, expect=201):
        res = self.client.post(self.url, payload, format="json", **self.header)
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def test_create_with_defaults(self):
        body = self.commit({"create": [{"a": self.f.sw_ports[1].pk, "b": self.f.srv1_eno1.pk, "ref": "n1"}]})
        self.assertTrue(body["applied"])
        self.assertEqual(body["created"][0]["ref"], "n1")
        cable = Cable.objects.get(pk=body["created"][0]["cable"]["id"])
        self.assertEqual(cable.type, "cat6")
        self.assertEqual(cable.status, "planned")
        self.assertEqual(float(cable.length), 3.0)
        self.assertEqual(cable.length_unit, "m")
        ends = {t.pk for t in cable.a_terminations} | {t.pk for t in cable.b_terminations}
        self.assertEqual(ends, {self.f.sw_ports[1].pk, self.f.srv1_eno1.pk})

    def test_create_with_explicit_fields(self):
        body = self.commit(
            {
                "create": [
                    {
                        "a": self.f.sw_sfp.pk,
                        "b": self.f.srv2_sfp.pk,
                        "type": "mmf-om3",
                        "status": "connected",
                        "label": "SW1 Te1/1/1 - SRV2",
                        "length": 10,
                        "length_unit": "m",
                        "ref": "x",
                    }
                ]
            }
        )
        cable = Cable.objects.get(pk=body["created"][0]["cable"]["id"])
        self.assertEqual(
            (cable.type, cable.status, cable.label, float(cable.length)),
            ("mmf-om3", "connected", "SW1 Te1/1/1 - SRV2", 10.0),
        )

    def test_update_and_delete(self):
        new = Cable(a_terminations=[self.f.sw_ports[3]], b_terminations=[self.f.srv1_eno1], type="cat6")
        new.full_clean()
        new.save()
        body = self.commit(
            {
                "update": [{"id": new.pk, "type": "cat6a", "label": "L", "length": 5, "length_unit": "m"}],
                "delete": [self.f.cable.pk],
            }
        )
        self.assertEqual((body["updated"], body["deleted"]), (1, 1))
        new.refresh_from_db()
        self.assertEqual((new.type, new.label, float(new.length)), ("cat6a", "L", 5.0))
        self.assertFalse(Cable.objects.filter(pk=self.f.cable.pk).exists())

    def test_delete_then_reuse_port(self):
        body = self.commit(
            {
                "delete": [self.f.cable.pk],
                "create": [{"a": self.f.sw_ports[0].pk, "b": self.f.srv2_eno0.pk, "ref": "r"}],
            }
        )
        self.assertTrue(body["applied"])
        port = Interface.objects.get(pk=self.f.sw_ports[0].pk)
        self.assertIsNotNone(port.cable_id)
        self.assertNotEqual(port.cable_id, self.f.cable.pk)

    def test_validation_error_names_the_item(self):
        body = self.commit({"update": [{"id": self.f.cable.pk, "length": 5, "length_unit": ""}]}, expect=400)
        self.assertFalse(body["applied"])
        self.assertEqual(body["errors"][0]["op"], "update")
        self.assertEqual(body["errors"][0]["id"], self.f.cable.pk)
        self.assertIn("unit", body["errors"][0]["error"])  # a readable message, not a dict repr
        self.assertNotIn("{", body["errors"][0]["error"])
        self.f.cable.refresh_from_db()
        self.assertIsNone(self.f.cable.length)

    def test_all_or_nothing(self):
        before = Cable.objects.count()
        body = self.commit(
            {
                "delete": [self.f.cable.pk],
                "create": [
                    {"a": self.f.sw_ports[1].pk, "b": self.f.srv1_eno1.pk, "ref": "good"},
                    {"a": self.f.sw_ports[2].pk, "b": self.f.srv2_sfp.pk, "ref": "bad"},  # copper vs SFP
                ],
            },
            expect=400,
        )
        self.assertFalse(body["applied"])
        self.assertEqual([e["ref"] for e in body["errors"]], ["bad"])
        self.assertIn("media mismatch", body["errors"][0]["error"])
        self.assertEqual(Cable.objects.count(), before)  # the delete was rolled back too
        self.assertTrue(Cable.objects.filter(pk=self.f.cable.pk).exists())


class CommitPermissionTest(APITestCase):
    """change_cable alone gets past DRF's model permission; add/delete are checked explicitly."""

    user_permissions = VIEW_PERMS + ("dcim.change_cable",)

    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()
        cls.url = reverse("plugins-api:netbox_portmap-api:portmap-commit-list")

    def test_create_needs_add_cable(self):
        res = self.client.post(
            self.url, {"create": [{"a": self.f.sw_ports[1].pk, "b": self.f.srv1_eno1.pk}]}, format="json", **self.header
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("dcim.add_cable", res.json()["detail"])
        self.assertEqual(Cable.objects.count(), 1)

    def test_delete_needs_delete_cable(self):
        res = self.client.post(self.url, {"delete": [self.f.cable.pk]}, format="json", **self.header)
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Cable.objects.filter(pk=self.f.cable.pk).exists())

    def test_update_allowed_with_change_cable(self):
        res = self.client.post(
            self.url, {"update": [{"id": self.f.cable.pk, "label": "x"}]}, format="json", **self.header
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Cable.objects.get(pk=self.f.cable.pk).label, "x")


class ViewOnlyCommitTest(APITestCase):
    user_permissions = VIEW_PERMS

    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()

    def test_rejected(self):
        url = reverse("plugins-api:netbox_portmap-api:portmap-commit-list")
        res = self.client.post(url, {"update": [{"id": self.f.cable.pk, "label": "x"}]}, format="json", **self.header)
        self.assertEqual(res.status_code, 403)


class ExportTest(APITestCase):
    """The port map as a file: the first column is the cable's NetBox id."""

    user_permissions = VIEW_PERMS

    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()

    def sheet(self, res):
        import re
        import zipfile
        from io import BytesIO

        xml = zipfile.ZipFile(BytesIO(res.content)).read("xl/worksheets/sheet1.xml").decode()
        rows = []
        for row in re.findall(r"<row [^>]*>(.*?)</row>", xml):
            cells = {}
            for ref, body in re.findall(r'<c r="([A-Z]+)\d+"[^>]*?(?:/>|>(.*?)</c>)', row):
                text = re.search(r"<t[^>]*>(.*?)</t>", body or "")
                value = re.search(r"<v>(.*?)</v>", body or "")
                cells[ref] = text.group(1) if text else (value.group(1) if value else "")
            rows.append(cells)
        return rows

    def test_the_first_column_is_the_netbox_cable_id(self):
        url = reverse("plugins-api:netbox_portmap-api:portmap-device-export", kwargs={"pk": self.f.sw.pk})
        res = self.client.get(url, **self.header)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("sw1_portmap_", res["Content-Disposition"])
        group, header, *body = self.sheet(res)
        self.assertEqual((group["A"], group["C"], group["I"], group["O"]), ("NetBox", "SRC", "Cable", "DST"))
        self.assertEqual((header["A"], header["G"], header["S"]), ("Cable ID", "Interface ID", "Interface ID"))
        [row] = body
        self.assertEqual(row["A"], str(self.f.cable.pk))
        self.assertEqual((row["E"], row["F"], row["G"]), ("sw1", "GigabitEthernet1/0/1", str(self.f.sw_ports[0].pk)))
        self.assertEqual((row["Q"], row["R"], row["S"]), ("srv1", "eno0", str(self.f.srv1_eno0.pk)))
        self.assertEqual((row["D"], row["P"]), ("R-01 40U", "R-01 10U"))
        self.assertEqual(row["H"], "sw1, GigabitEthernet1/0/1, R-01 40U")
        self.assertEqual(row["I"], "cat6")
        self.assertTrue(row["U"].endswith(f"/dcim/cables/{self.f.cable.pk}/"))


class ExportScopeTest(APITestCase):
    """A rack or a site on one sheet: every cable once, the server end on the left."""

    user_permissions = VIEW_PERMS

    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()
        cable = Cable(a_terminations=[cls.f.sw_ports[1]], b_terminations=[cls.f.srv2_eno0], type="cat6a")
        cable.full_clean()
        cable.save()
        cls.second = cable

    def export(self, scope):
        url = reverse("plugins-api:netbox_portmap-api:portmap-device-export", kwargs={"pk": self.f.sw.pk})
        return self.client.get(url, {"scope": scope}, **self.header)

    def test_site_lists_every_cable_once_with_the_server_as_src(self):
        res = self.export("site")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Site 1_portmap_", res["Content-Disposition"].replace("%20", " "))
        rows = ExportTest.sheet(self, res)[2:]
        self.assertEqual(sorted(r["A"] for r in rows), sorted([str(self.f.cable.pk), str(self.second.pk)]))
        # The server has one cable, the switch two: the server is SRC on every row.
        self.assertEqual({r["E"] for r in rows}, {"srv1", "srv2"})
        self.assertEqual({r["Q"] for r in rows}, {"sw1"})
        # Rack R-01 before R-03.
        self.assertEqual([r["E"] for r in rows], ["srv1", "srv2"])

    def test_rack_keeps_to_the_rack(self):
        rows = ExportTest.sheet(self, self.export("rack"))[2:]
        # srv2 sits in R-03, but its cable reaches sw1 in R-01, so it is on the rack's sheet.
        self.assertEqual(len(rows), 2)
        rows = ExportTest.sheet(
            self,
            self.client.get(
                reverse("plugins-api:netbox_portmap-api:portmap-device-export", kwargs={"pk": self.f.srv2.pk}),
                {"scope": "rack"},
                **self.header,
            ),
        )[2:]
        self.assertEqual([r["A"] for r in rows], [str(self.second.pk)])

    def test_an_unknown_scope_is_refused(self):
        self.assertEqual(self.export("building").status_code, 400)


class ExportRestrictedTest(APITestCase):
    """The file says no more than the screen: a hub the user may not view is not exported."""

    @classmethod
    def setUpTestData(cls):
        cls.f = build_fixture()

    def setUp(self):
        super().setUp()
        perm = ObjectPermission.objects.create(name="other site", actions=["view"], constraints={"site__name": "x"})
        perm.object_types.set([ObjectType.objects.get(app_label="dcim", model="device")])
        perm.users.add(self.user)
        for model in ("interface", "cable"):
            p = ObjectPermission.objects.create(name=f"view {model}", actions=["view"])
            p.object_types.set([ObjectType.objects.get(app_label="dcim", model=model)])
            p.users.add(self.user)

    def test_a_device_the_user_may_not_see_is_not_exported(self):
        url = reverse("plugins-api:netbox_portmap-api:portmap-device-export", kwargs={"pk": self.f.sw.pk})
        self.assertEqual(self.client.get(url, **self.header).status_code, 404)
