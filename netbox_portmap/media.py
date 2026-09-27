"""
Media families and compatibility.

NetBox describes a physical interface by its ``type`` (``10gbase-t``,
``25gbase-x-sfp28``, ``200gbase-x-qsfp56``, ...). For cabling purposes what
matters is the *form factor* -- what plugs into it -- so every type is mapped to
one of a few media families:

* ``copper``  RJ45 (xBASE-T)
* ``sfp``     SFP / SFP+ / SFP28 cages (10G, 25G and 1G optics, DACs)
* ``qsfp``    QSFP+ / QSFP28 / QSFP56 / QSFP-DD / OSFP cages (40G and up, InfiniBand)
* ``stack``   vendor stacking ports
* ``other``   anything else with a physical connector we do not classify

Virtual interface types (``virtual``, ``lag``, ``bridge``) are not cabled and
are dropped before layout.

The decision for a pair of ports is ``ok``, ``warn`` or ``block`` with a
human-readable reason; the table is a plugin setting so an operator can
loosen it (10GBASE-T transceivers in SFP+ cages) without patching code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from netbox.plugins import get_plugin_config

VIRTUAL_TYPES = {"virtual", "lag", "bridge"}

# Ordered: first match wins, so the more specific tokens go first.
_FAMILY_RULES = (
    (re.compile(r"stackwise|stack", re.I), "stack"),
    (re.compile(r"qsfp|osfp|infiniband|cxp|cfp", re.I), "qsfp"),
    (re.compile(r"sfp", re.I), "sfp"),
    (re.compile(r"base-t\b|base-tx\b|base-t1\b|gbase-t\b|rj45", re.I), "copper"),
)

# "1000base-t" (Mbps, no unit), "10gbase-t", "2.5gbase-t", "100base-tx"
_SPEED_RE = re.compile(r"^(\d+(?:\.\d+)?)(g|m)?base", re.I)

FAMILY_LABELS = {
    "copper": "RJ45",
    "sfp": "SFP",
    "qsfp": "QSFP",
    "stack": "Stack",
    "other": "Other",
}


def family_of(interface_type: str) -> str:
    """Media family for a NetBox interface type value."""
    if not interface_type or interface_type in VIRTUAL_TYPES:
        return "virtual"
    for pattern, family in _FAMILY_RULES:
        if pattern.search(interface_type):
            return family
    return "other"


def speed_of(interface_type: str) -> int | None:
    """Nominal speed in Mbps parsed from the type (10gbase-t -> 10000), or None."""
    m = _SPEED_RE.match(interface_type or "")
    if not m:
        if "infiniband-hdr" in (interface_type or ""):
            return 200000
        if "infiniband-edr" in (interface_type or ""):
            return 100000
        return None
    n = float(m.group(1))
    return int(n * 1000) if (m.group(2) or "").lower() == "g" else int(n)


def form_factor(interface_type: str) -> str:
    """Short connector label for the port badge: '10G-T', 'SFP28', 'QSFP56' ..."""
    t = interface_type or ""
    fam = family_of(t)
    if fam == "copper":
        s = speed_of(t)
        return {1000: "1G-T", 2500: "2.5G-T", 5000: "5G-T", 10000: "10G-T"}.get(s, "RJ45")
    if fam == "sfp":
        if "sfp28" in t:
            return "SFP28"
        if "sfpp" in t or "sfp+" in t:
            return "SFP+"
        return "SFP"
    if fam == "qsfp":
        for token in ("qsfp-dd", "qsfpdd", "qsfp56", "qsfp28", "qsfpp", "osfp"):
            if token in t:
                return {"qsfpp": "QSFP+", "qsfpdd": "QSFP-DD", "qsfp-dd": "QSFP-DD"}.get(token, token.upper())
        if "infiniband" in t:
            return "IB"
        return "QSFP"
    if fam == "stack":
        return "Stack"
    return t.upper()[:10] if t else "?"


@dataclass
class Verdict:
    level: str  # "ok" | "warn" | "block"
    reason: str
    cable_type: str | None = None

    @property
    def allowed(self) -> bool:
        return self.level != "block"


def _pair_key(a: str, b: str) -> str:
    return ":".join(sorted((a, b)))


def check(a_type: str, b_type: str, a_mgmt: bool = False, b_mgmt: bool = False) -> Verdict:
    """Decide whether two interfaces may be cabled, and what cable to suggest."""
    fa, fb = family_of(a_type), family_of(b_type)
    if fa == "virtual" or fb == "virtual":
        return Verdict("block", "Virtual interfaces cannot be cabled")
    table = get_plugin_config("netbox_portmap", "compatibility") or {}
    level = table.get(_pair_key(fa, fb))
    if level is None:
        level = "ok" if fa == fb else "block"
    labels = f"{form_factor(a_type)} ↔ {form_factor(b_type)}"
    if level == "block":
        return Verdict("block", f"{labels}: media mismatch")
    reasons = []
    if fa == "other" or fb == "other":
        level = "warn"
        reasons.append("unclassified port type")
    if level == "warn" and not reasons:
        reasons.append("allowed by site policy")
    if get_plugin_config("netbox_portmap", "warn_speed_mismatch"):
        sa, sb = speed_of(a_type), speed_of(b_type)
        if sa and sb and sa != sb:
            level = "warn" if level == "ok" else level
            reasons.append(f"speed mismatch ({_fmt_speed(sa)} / {_fmt_speed(sb)})")
    if get_plugin_config("netbox_portmap", "warn_mgmt_to_data") and (a_mgmt != b_mgmt):
        level = "warn" if level == "ok" else level
        reasons.append("management port to data port")
    defaults = get_plugin_config("netbox_portmap", "cable_type_defaults") or {}
    cable_type = defaults.get(fa) or defaults.get(fb)
    if fa == "copper" and fb == "copper":
        sa, sb = speed_of(a_type) or 0, speed_of(b_type) or 0
        if max(sa, sb) <= 1000:
            cable_type = "cat6"
    reason = f"{labels}: " + ("; ".join(reasons) if reasons else "compatible")
    return Verdict(level, reason, cable_type)


def _fmt_speed(mbps: int) -> str:
    return f"{mbps // 1000}G" if mbps >= 1000 else f"{mbps}M"
