from netbox.plugins import PluginMenuItem

menu_items = (
    PluginMenuItem(
        link="plugins:netbox_portmap:workbench",
        link_text="Port Map",
        permissions=["dcim.view_cable"],
    ),
)
