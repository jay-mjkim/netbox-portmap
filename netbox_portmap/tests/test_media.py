from django.conf import settings
from django.test import SimpleTestCase, override_settings

from netbox_portmap import media


def plugin_settings(**overrides):
    """override_settings() for this plugin only, keeping the other plugins' config."""
    cfg = dict(settings.PLUGINS_CONFIG)
    cfg["netbox_portmap"] = {**cfg.get("netbox_portmap", {}), **overrides}
    return override_settings(PLUGINS_CONFIG=cfg)


class FamilyTest(SimpleTestCase):
    def test_families(self):
        cases = {
            "1000base-t": "copper",
            "10gbase-t": "copper",
            "2.5gbase-t": "copper",
            "1000base-x-sfp": "sfp",
            "10gbase-x-sfpp": "sfp",
            "25gbase-x-sfp28": "sfp",
            "40gbase-x-qsfpp": "qsfp",
            "100gbase-x-qsfp28": "qsfp",
            "200gbase-x-qsfp56": "qsfp",
            "400gbase-x-qsfpdd": "qsfp",
            "400gbase-x-osfp": "qsfp",
            "infiniband-hdr": "qsfp",
            "cisco-stackwise-480": "stack",
            "virtual": "virtual",
            "lag": "virtual",
            "other": "other",
        }
        for itype, family in cases.items():
            with self.subTest(itype):
                self.assertEqual(media.family_of(itype), family)

    def test_speed(self):
        self.assertEqual(media.speed_of("10gbase-t"), 10000)
        self.assertEqual(media.speed_of("100base-tx"), 100)
        self.assertEqual(media.speed_of("infiniband-hdr"), 200000)
        self.assertIsNone(media.speed_of("other"))

    def test_form_factor(self):
        self.assertEqual(media.form_factor("10gbase-t"), "10G-T")
        self.assertEqual(media.form_factor("25gbase-x-sfp28"), "SFP28")
        self.assertEqual(media.form_factor("10gbase-x-sfpp"), "SFP+")
        self.assertEqual(media.form_factor("200gbase-x-qsfp56"), "QSFP56")
        self.assertEqual(media.form_factor("infiniband-hdr"), "IB")


class CheckTest(SimpleTestCase):
    def test_same_family_ok(self):
        v = media.check("10gbase-t", "10gbase-t")
        self.assertEqual(v.level, "ok")
        self.assertEqual(v.cable_type, "cat6a")

    def test_gigabit_copper_suggests_cat6(self):
        self.assertEqual(media.check("1000base-t", "1000base-t").cable_type, "cat6")

    def test_media_mismatch_blocks(self):
        v = media.check("10gbase-t", "10gbase-x-sfpp")
        self.assertEqual(v.level, "block")
        self.assertIn("media mismatch", v.reason)
        self.assertFalse(v.allowed)

    def test_speed_mismatch_warns(self):
        v = media.check("1000base-t", "10gbase-t")
        self.assertEqual(v.level, "warn")
        self.assertIn("speed mismatch", v.reason)

    def test_mgmt_to_data_warns(self):
        v = media.check("1000base-t", "1000base-t", a_mgmt=True)
        self.assertEqual(v.level, "warn")
        self.assertIn("management", v.reason)

    def test_virtual_blocks(self):
        self.assertEqual(media.check("virtual", "1000base-t").level, "block")

    def test_unclassified_warns(self):
        self.assertEqual(media.check("other", "other").level, "warn")

    def test_policy_override(self):
        with plugin_settings(compatibility={"copper:sfp": "warn"}):
            v = media.check("10gbase-t", "10gbase-x-sfpp")
        self.assertEqual(v.level, "warn")
        self.assertIn("site policy", v.reason)

    def test_warnings_can_be_disabled(self):
        with plugin_settings(warn_speed_mismatch=False, warn_mgmt_to_data=False):
            self.assertEqual(media.check("1000base-t", "10gbase-t", a_mgmt=True).level, "ok")
