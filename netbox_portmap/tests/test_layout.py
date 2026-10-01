from django.test import SimpleTestCase

from netbox_portmap import layout


def ports(*names, mgmt=()):
    return [{"name": n, "mgmt_only": n in mgmt} for n in names]


class ArrangeTest(SimpleTestCase):
    def test_switch_faceplate_two_rows(self):
        ps = ports(*[f"GigabitEthernet1/0/{i}" for i in range(1, 9)])
        grid = layout.arrange(ps)
        self.assertEqual(grid["rows"], 2)
        self.assertEqual(grid["cols"], 4)
        by_name = {p["name"]: p for p in ps}
        self.assertEqual((by_name["GigabitEthernet1/0/1"]["row"], by_name["GigabitEthernet1/0/1"]["col"]), (0, 0))
        self.assertEqual((by_name["GigabitEthernet1/0/2"]["row"], by_name["GigabitEthernet1/0/2"]["col"]), (1, 0))
        self.assertEqual((by_name["GigabitEthernet1/0/8"]["row"], by_name["GigabitEthernet1/0/8"]["col"]), (1, 3))
        self.assertEqual(grid["groups"][0]["label"], "GigabitEthernet1/0")

    def test_numeric_sort_not_lexical(self):
        ps = ports("eth10", "eth2", "eth1")
        layout.arrange(ps)
        self.assertEqual([p["col"] for p in ps], [2, 1, 0])

    def test_groups_separated_by_gap(self):
        ps = ports("eno0", "eno1", "ib0", "ib1")
        grid = layout.arrange(ps)
        self.assertEqual(grid["rows"], 1)
        self.assertEqual([g["start"] for g in grid["groups"]], [0, 3])
        self.assertEqual(grid["cols"], 5)

    def test_mgmt_last(self):
        ps = ports("mgmt0", "eno0", "eno1", mgmt=("mgmt0",))
        grid = layout.arrange(ps)
        mgmt = next(p for p in ps if p["name"] == "mgmt0")
        self.assertEqual(mgmt["group"], "mgmt")
        self.assertEqual(mgmt["col"], grid["cols"] - 1)
        self.assertGreater(mgmt["col"], max(p["col"] for p in ps if p["name"] != "mgmt0") + 1)

    def test_slot_paths_split_groups(self):
        ps = ports("TenGigabitEthernet1/1/1", "TenGigabitEthernet1/1/2", "TwentyFiveGigE1/1/1", "TwentyFiveGigE1/1/2")
        grid = layout.arrange(ps)
        self.assertEqual([g["label"] for g in grid["groups"]], ["TenGigabitEthernet1/1", "TwentyFiveGigE1/1"])

    def test_unparseable_names_still_placed(self):
        ps = ports("uplink", "wan")
        grid = layout.arrange(ps)
        self.assertEqual({p["col"] for p in ps}, {0, 2})
        self.assertEqual(grid["cols"], 3)

    def test_numeric_names_form_one_group(self):
        ps = ports("1", "2", "3", "4", "1/1/1", "1/1/2")
        grid = layout.arrange(ps)
        self.assertEqual([g["label"] for g in grid["groups"]], ["ports", "1/1"])
        self.assertEqual([g["count"] for g in grid["groups"]], [4, 2])

    def test_empty(self):
        self.assertEqual(layout.arrange([]), {"cols": 1, "rows": 1, "groups": []})
