from types import SimpleNamespace

from django.test import SimpleTestCase

from netbox_portmap import lengths

from .test_media import plugin_settings


def device(rack_name, pk=1, location=1):
    rack = SimpleNamespace(pk=pk, name=rack_name, location_id=location) if rack_name else None
    return SimpleNamespace(rack=rack)


class SuggestTest(SimpleTestCase):
    def test_same_rack(self):
        self.assertEqual(lengths.suggest(device("A-01"), device("A-01")), (3, "m"))

    def test_neighbouring_racks_round_up(self):
        # |12 - 14| * 0.6 + 3.0 = 4.2 -> 5
        self.assertEqual(lengths.suggest(device("3F-03-12", pk=1), device("3F-03-14", pk=2)), (5, "m"))

    def test_far_racks(self):
        # 15 * 0.6 + 3 = 12 -> 15
        self.assertEqual(lengths.suggest(device("R01", pk=1), device("R16", pk=2)), (15, "m"))

    def test_beyond_largest_size_caps(self):
        self.assertEqual(lengths.suggest(device("R1", pk=1), device("R999", pk=2)), (30, "m"))

    def test_unracked_or_unnumbered(self):
        self.assertIsNone(lengths.suggest(device(None), device("A-01")))
        self.assertIsNone(lengths.suggest(device("left", pk=1), device("right", pk=2)))

    def test_different_locations(self):
        self.assertIsNone(lengths.suggest(device("A-01", pk=1, location=1), device("A-02", pk=2, location=2)))

    def test_disabled(self):
        with plugin_settings(length_model=None):
            self.assertIsNone(lengths.suggest(device("A-01"), device("A-01")))
