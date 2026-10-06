from netbox.plugins import PluginConfig

__version__ = "0.5.0"


class NetBoxPortMapConfig(PluginConfig):
    name = "netbox_portmap"
    verbose_name = "Port Map"
    description = (
        "Map ports between devices visually and create the cables: a hub/spoke "
        "workbench with media compatibility checks."
    )
    version = __version__
    author = "Minjae Kim"
    base_url = "portmap"
    min_version = "4.5.0"
    default_settings = {
        # Which port families may be cabled together. Keys are the two media
        # families (see media.py) in alphabetical order joined by ":", values
        # "ok" | "warn" | "block". Pairs not listed fall back to "block" when the
        # families differ and "ok" when they match. A site that uses 10GBASE-T
        # transceivers in SFP+ cages can set "copper:sfp" to "warn".
        "compatibility": {
            "copper:copper": "ok",
            "sfp:sfp": "ok",
            "qsfp:qsfp": "ok",
            "stack:stack": "ok",
            "copper:sfp": "block",
            "copper:qsfp": "block",
            "sfp:qsfp": "block",
        },
        # Warn (never block) when a management-only port meets a data port.
        "warn_mgmt_to_data": True,
        # Warn when the two ends advertise different speeds (e.g. 1G <-> 10G).
        "warn_speed_mismatch": True,
        # Cable type to suggest for a compatible pair, by media family of the pair.
        # Values are NetBox cable types (dcim.choices.CableTypeChoices); "" leaves the type unset.
        "cable_type_defaults": {
            "copper": "cat6a",
            "sfp": "mmf-om4",
            "qsfp": "aoc",
            "stack": "",
        },
        # Length suggestion from rack positions. None disables the suggestion.
        # Distance = |rack index difference| * rack_pitch + vertical run; the
        # result is rounded up to the next entry of "sizes".
        "length_model": {
            "unit": "m",
            "rack_pitch": 0.6,
            "vertical": 3.0,
            "same_rack": 3.0,
            "sizes": [1, 2, 3, 5, 10, 15, 20, 30],
        },
        # Default status for cables created from the workbench.
        "cable_status": "planned",
    }


config = NetBoxPortMapConfig
