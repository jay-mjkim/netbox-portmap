# netbox-portmap

Map ports between devices visually and create the cables — in NetBox, from the
browser, with the same data you already have.

Pick a **hub** device (a switch, usually). Its ports are drawn as tiles, and
every device it has cables to is listed underneath as a **spoke**. Click a free
port on the hub, click a free port on a spoke, and a cable is staged. Ports that
cannot take that cable (SFP cage vs RJ45, already cabled, same device) are dimmed
with the reason on hover. When you are done, **Save** writes every staged
cable in one transaction — all of it or none of it.

No per-model drawings are needed: ports are laid out from what NetBox already
knows (interface names and types), so it works for any device type on day one.

## Features

- Hub/spoke workbench: one device on top, its peers stacked below in the hub's port order
  (the device on hub port 1 first); add any device from a rack as an extra spoke.
- Inspect a connection: click a cabled port, a chip or a row of the connection list. Both ends
  are marked, a hub cable's spoke moves directly under the hub so its line is short, and the
  inspector shows both ends, type, length, status and label. `←` / `→` step through the hub's
  cables in port order, `Esc` clears, double-click opens the cable editor.
- Port tiles with a colour stripe for the media family (RJ45 / SFP / QSFP / stacking) and a fill
  for the cable status: connected, planned, decommissioning, new (not saved), to delete, free.
  Hovering shows an immediate tooltip with the far end.
- Click a free port, then a free port on another device, to connect. Incompatible targets are dimmed and explained;
  each spoke shows how many of its free ports are compatible with the armed port.
- Cable type and length suggested from the port media and the rack positions;
  edit type, length, label and status before saving.
- Staged changes (create / edit / delete) are applied with one request,
  all-or-nothing, re-validated on the server.
- Connection list for the hub with row ↔ line highlighting.
- Spoke filter (name, type, rack, role) and expand/collapse all, for hubs with dozens of peers.
- A **Port Map** tab on every device page.
- No dependency on other plugins.

- **Excel download** — the hub's cables in faceplate order, or every cable on the hub's
  rack or site on one sheet, in the port-map layout (SRC · Cable · DST · Comment) with the
  NetBox cable id first and each end's interface id, so a row can be found again in NetBox.
  Cable labels too: `HOST, PORT, RACK U` for both ends plus the length, one sheet per cable family. No
  spreadsheet library needed.

## Compatibility

| netbox-portmap | NetBox |
|----------------|--------|
| 0.1.x – 0.2.x  | 4.5 – 4.6 |

## Installation

```sh
pip install netbox-portmap
```

Add the plugin to `configuration.py`:

```python
PLUGINS = ["netbox_portmap"]
```

Then run `manage.py collectstatic` and restart NetBox. No migrations: the plugin
adds no models — it only reads and writes NetBox's own `Cable` objects.

The workbench is under **Plugins → Port Map**, and on every device page as the
**Port Map** tab.

## Configuration

All settings are optional. Defaults:

```python
PLUGINS_CONFIG = {
    "netbox_portmap": {
        # Which media families may be cabled together: "ok", "warn" or "block".
        # Keys are the two families in alphabetical order joined by ":".
        # Families: copper (xBASE-T), sfp (SFP/SFP+/SFP28), qsfp (QSFP*/OSFP/InfiniBand), stack, other.
        "compatibility": {
            "copper:copper": "ok",
            "sfp:sfp": "ok",
            "qsfp:qsfp": "ok",
            "stack:stack": "ok",
            "copper:sfp": "block",  # set to "warn" if you use 10GBASE-T transceivers in SFP+ cages
            "copper:qsfp": "block",
            "sfp:qsfp": "block",
        },
        "warn_mgmt_to_data": True,  # management-only port <-> data port
        "warn_speed_mismatch": True,  # e.g. 1000base-t <-> 10gbase-t
        # Cable type suggested for a compatible pair, by media family.
        "cable_type_defaults": {"copper": "cat6a", "sfp": "mmf-om4", "qsfp": "aoc", "stack": ""},  # NetBox cable types; "" = unset
        # Length suggestion from rack positions: |rack index difference| * rack_pitch + vertical,
        # rounded up to the next entry of "sizes". The rack index is the trailing number of the
        # rack name ("A-07" -> 7) and the rest of the name is the row ("A-"): racks in different
        # rows or locations get no suggestion. Set to None to disable.
        "length_model": {
            "unit": "m",
            "rack_pitch": 0.6,
            "vertical": 3.0,
            "same_rack": 3.0,
            "sizes": [1, 2, 3, 5, 10, 15, 20, 30],
        },
        # Status of cables created from the workbench.
        "cable_status": "planned",
    }
}
```

Export conventions are settings too: `seat_strip_prefix` (regex dropped from a rack name on a
label, default `^\d+F-` so `3F-03-18` prints as `03-18`) and `cable_type_labels` (NetBox cable
type → the name the sheets use; the built-in table covers AOC, DAC, CAT6/6a, OM2/OM3/OM4 and SMF).

## Permissions

Viewing needs `dcim.view_device`, `dcim.view_interface` and `dcim.view_cable`.
Saving needs `dcim.add_cable`, `dcim.change_cable` and `dcim.delete_cable`;
without them the workbench opens read-only. Object permissions are honoured.

## API

The workbench talks to its own endpoints under `/api/plugins/portmap/`:

| Endpoint | Purpose |
|----------|---------|
| `GET devices/<id>/ports/` | the device, its physical interfaces on a grid, cables and peers |
| `GET devices/<id>/peers/` | devices it has cables to |
| `GET racks/<id>/devices/` | racked devices (the shelf) |
| `POST check/` `{"a": id, "b": id}` or `{"a": id, "targets": [ids]}` | compatibility verdict + suggested cable type/length |
| `POST commit/` `{"create": [...], "update": [...], "delete": [...]}` | apply staged changes atomically |
| `GET devices/<id>/export/?scope=hub\|rack\|site&kind=portmap\|labels` | the port map, or the cable labels, as `.xlsx` — NetBox cable and interface ids included |

## Development

```sh
ruff check . && ruff format --check .
# inside a NetBox checkout with the plugin installed:
python manage.py test netbox_portmap
```

## License

MIT
