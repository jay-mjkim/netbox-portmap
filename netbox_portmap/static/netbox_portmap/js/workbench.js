/* netbox-portmap workbench.
 *
 * One hub device on top, its peers ("spokes") stacked below. Ports are tiles;
 * click a free port, then a free port on another device, and a cable is
 * staged. Nothing touches NetBox until Save, which sends every staged change
 * in one request that is applied all-or-nothing.
 */
(() => {
  "use strict";

  const cfg = window.PORTMAP;
  const CHOICES = JSON.parse(document.getElementById("pm-config").textContent);
  const PITCH = 30; // tile pitch in px, must match --pm-pitch in the stylesheet
  const EXPAND_DEFAULT = 4; // spokes opened on load; the rest start collapsed

  const $ = (sel, root = document) => root.querySelector(sel);
  const bench = $("#pm-bench");
  const svg = $("#pm-lines");

  // ------------------------------------------------------------------ helpers

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null) node.append(c);
    return node;
  }

  function svgEl(tag, attrs = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }

  const labelOf = (choices, value) => {
    for (const g of choices) for (const [v, l] of g.options) if (v === value) return l;
    return value || "";
  };

  async function api(path, body) {
    const res = await fetch(cfg.api + path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-CSRFToken": cfg.csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = res.status === 204 ? null : await res.json().catch(() => null);
    if (!res.ok) {
      const err = new Error((data && (data.detail || JSON.stringify(data))) || `HTTP ${res.status}`);
      err.body = data;
      throw err;
    }
    return data;
  }

  function toast(text, kind = "info") {
    let host = $("#pm-toasts");
    if (!host) {
      host = el("div", { id: "pm-toasts", class: "pm-toasts" });
      document.body.append(host);
    }
    const node = el("div", { class: `alert alert-${kind} pm-toast` }, text);
    host.append(node);
    setTimeout(() => node.remove(), kind === "info" ? 3500 : 6000);
  }

  const fmtLen = (len, unit) => (len == null || len === "" ? "" : `${Number(len)} ${unit || ""}`.trim());

  // Cisco-style abbreviations keep peer names short in chips, tooltips and the inspector.
  const IF_ABBR = [
    [/^HundredGigE/i, "Hu"], [/^FortyGigabitEthernet/i, "Fo"], [/^TwentyFiveGigE/i, "Twe"],
    [/^TenGigabitEthernet/i, "Te"], [/^GigabitEthernet/i, "Gi"], [/^FastEthernet/i, "Fa"],
  ];
  const shortIf = (name) => {
    for (const [re, abbr] of IF_ABBR) if (re.test(name || "")) return name.replace(re, abbr);
    return name || "";
  };
  const statusLabel = (value) => labelOf(CHOICES.statuses, value) || value || "";
  // Type and length. The label is left out: labels often repeat both ends verbatim and drowned
  // everything else; the inspector shows it on its own line.
  const cableText = (cab) => [labelOf(CHOICES.cable_types, cab.type), fmtLen(cab.length, cab.length_unit)].filter(Boolean).join(" · ");

  function shortLabel(p) {
    const m = /^(.*?)(\d+)$/.exec(p.name);
    if (m && m[1]) return m[2];
    return p.name.length > 4 ? p.name.slice(0, 4) : p.name;
  }

  // -------------------------------------------------------------------- state

  const state = {
    hub: null,
    spokes: [],
    cards: new Map(), // device id -> card
    ports: new Map(), // interface id -> { port, card }
    pending: { create: [], update: new Map(), delete: new Set() },
    armed: null, // interface id
    compat: new Map(), // interface id -> verdict, while armed
    focus: null, // device id of the spoke sitting right under the hub; only its cables get lines
    hoverKey: null, // link key under the pointer: its line is drawn thicker
    selected: null, // interface id whose connection is being inspected
    seq: 0,
  };

  const card = (deviceId) => state.cards.get(deviceId);
  const armedCard = () => (state.armed == null ? null : state.ports.get(state.armed).card);

  /** The cable a port takes part in, existing or staged, or null. */
  function cableOf(portId) {
    const entry = state.ports.get(portId);
    if (!entry) return null;
    const p = entry.port;
    for (const c of state.pending.create) {
      if (c.a === portId || c.b === portId) {
        const peerId = c.a === portId ? c.b : c.a;
        const peer = state.ports.get(peerId);
        return {
          kind: "pending",
          ref: c.ref,
          item: c,
          peerPortId: peerId,
          peerDeviceId: peer ? peer.card.device.id : null,
          peerDevice: peer ? peer.card.device.name : "?",
          peerName: peer ? peer.port.name : "?",
          type: c.type,
          status: c.status,
          label: c.label,
          length: c.length,
          length_unit: c.length_unit,
          verdict: c.verdict,
        };
      }
    }
    if (!p.cable) return null;
    const upd = state.pending.update.get(p.cable) || {};
    return {
      kind: "existing",
      id: p.cable,
      deleted: state.pending.delete.has(p.cable),
      edited: state.pending.update.has(p.cable),
      peerPortId: p.peer && p.peer.kind === "interface" ? p.peer.id : null,
      peerDeviceId: p.peer ? p.peer.device_id : null,
      peerDevice: p.peer ? p.peer.device || p.peer.kind : "?",
      peerName: p.peer ? p.peer.name : "?",
      type: "type" in upd ? upd.type : p.cable_type || "",
      status: "status" in upd ? upd.status : p.cable_status || "",
      label: "label" in upd ? upd.label : p.cable_label || "",
      length: "length" in upd ? upd.length : p.cable_length,
      length_unit: "length_unit" in upd ? upd.length_unit : p.cable_unit || "",
    };
  }

  /** A card whose ports are drawn: the hub, or an expanded spoke not hidden by the filter. */
  const showsPorts = (c) => !!c && (c.isHub || (c.expanded && !c.filteredOut));

  const pendingCount = () => state.pending.create.length + state.pending.update.size + state.pending.delete.size;

  // ------------------------------------------------------------------- cards

  function registerPorts(c) {
    for (const p of c.ports) state.ports.set(p.id, { port: p, card: c });
  }

  function unregisterPorts(c) {
    for (const p of c.ports) state.ports.delete(p.id);
  }

  async function loadCard(deviceId, isHub) {
    const data = await api(`devices/${deviceId}/ports/`);
    const c = { device: data.device, ports: data.ports, grid: data.grid, isHub, expanded: true, cables: 0, el: null };
    return c;
  }

  function cablesToBench(c) {
    let n = 0;
    for (const p of c.ports) {
      const cab = cableOf(p.id);
      if (cab && !cab.deleted && cab.peerDeviceId != null && state.cards.has(cab.peerDeviceId)) n++;
    }
    return n;
  }

  function cablesToCard(c, other) {
    if (!other) return 0;
    let n = 0;
    for (const p of c.ports) {
      const cab = cableOf(p.id);
      if (cab && !cab.deleted && cab.peerDeviceId === other.device.id) n++;
    }
    return n;
  }

  function renderCard(c) {
    const d = c.device;
    c.el.replaceChildren();
    const where = [d.rack, d.position != null ? `U${d.position}` : null].filter(Boolean).join(" · ");
    const cabled = c.ports.filter((p) => {
      const cab = cableOf(p.id);
      return cab && !cab.deleted;
    }).length;

    const toHub = c.isHub ? 0 : cablesToCard(c, state.hub);
    const header = el(
      "div",
      {
        class: "card-header",
        title: c.isHub ? null : "Click to bring this device under the hub and show its cables",
        // Header click focuses the spoke; links, buttons and the toggle keep their own behaviour.
        onclick: (e) => { if (!c.isHub && !e.target.closest("a, button")) setFocus(c); },
        // ... and so does Enter/Space for keyboard users.
        tabindex: c.isHub ? null : "0",
        role: c.isHub ? null : "button",
        "aria-pressed": c.isHub ? null : String(state.focus === d.id),
        onkeydown: (e) => {
          if (c.isHub || e.target !== e.currentTarget || (e.key !== "Enter" && e.key !== " ")) return;
          e.preventDefault();
          setFocus(c);
          c.el.querySelector(".card-header")?.focus({ preventScroll: true }); // the header was re-rendered
        },
      },
      c.isHub
        ? null
        : el(
            "button",
            { type: "button", class: "pm-toggle", title: c.expanded ? "Collapse" : "Expand", onclick: () => toggleSpoke(c) },
            el("i", { class: `mdi mdi-chevron-${c.expanded ? "down" : "right"}` }),
          ),
      el("a", { href: d.url, class: "fw-bold" }, d.name),
      el("span", { class: "text-muted small" }, d.device_type),
      where ? el("span", { class: "text-muted small" }, where) : null,
      c.isHub ? el("span", { class: "badge bg-primary-lt ms-1" }, "hub") : null,
      !c.isHub && state.focus === d.id ? el("span", { class: "badge bg-primary-lt ms-1" }, "focus") : null,
      el("span", { class: "ms-auto d-flex align-items-center gap-2" },
        el("span", { class: "pm-compat", dataset: { compat: d.id } }),
        !c.isHub && toHub ? el("span", { class: "text-muted small", title: "cables between this device and the hub" }, `${toHub} ↔ hub`) : null,
        el("span", { class: "text-muted small", title: "cabled ports / physical ports" }, `${cabled}/${c.ports.length} cabled`),
        !c.isHub && cablesToBench(c) === 0 && !c.busy
          ? el("button", { type: "button", class: "btn-close", "aria-label": "Remove from bench", onclick: () => removeSpoke(c) })
          : null,
      ),
    );
    c.el.classList.toggle("pm-focus", !c.isHub && state.focus === d.id);
    c.el.append(header);

    if (c.isHub || c.expanded) {
      c.el.append(renderGrid(c));
    } else {
      c.el.append(renderChips(c));
    }
  }

  function renderGrid(c) {
    const g = c.grid;
    const inner = el("div", { class: `pm-grid-inner rows-${g.rows}`, style: `width:${Math.max(g.cols, 1) * PITCH}px` });
    // Stubs (tile centre -> column edge) live under the tiles, so a line to a top-row port
    // visibly passes behind the tile below it instead of being drawn across it.
    const stubs = svgEl("svg", { class: "pm-stubs", width: Math.max(g.cols, 1) * PITCH, height: g.rows * PITCH });
    inner.append(stubs);
    for (const p of c.ports) {
      inner.append(
        el(
          "button",
          {
            type: "button",
            class: "pm-port",
            dataset: { id: p.id, family: p.family, name: p.name },
            style: `left:${p.col * PITCH}px; top:${p.row * PITCH}px`,
            onclick: () => onPortClick(p.id),
            ondblclick: () => { const cab = cableOf(p.id); if (cab && state.armed == null) openEditor(cab, p.id); },
            onmouseenter: (e) => { highlight(p.id, true); showTip(e.currentTarget); },
            onmouseleave: () => { highlight(p.id, false); hideTip(); },
            onfocus: (e) => showTip(e.currentTarget),
            onblur: hideTip,
          },
          shortLabel(p),
        ),
      );
    }
    for (const grp of g.groups) {
      const width = (grp.end - grp.start + 1) * PITCH + (grp.end < g.cols - 1 ? PITCH - 6 : -4); // may run into the gap
      inner.append(el("span", { class: "pm-group", style: `left:${grp.start * PITCH}px; max-width:${width}px`, title: grp.label }, shortIf(grp.label)));
    }
    return el("div", { class: "pm-grid", onscroll: scheduleLines }, inner);
  }

  /** Collapsed spoke: one chip per cable to a device on the bench. */
  function renderChips(c) {
    const chips = el("div", { class: "pm-chips" });
    for (const other of state.cards.values()) {
      if (other === c) continue;
      for (const p of other.ports) {
        const cab = cableOf(p.id);
        if (!cab || cab.deleted || cab.peerDeviceId !== c.device.id) continue;
        chips.append(
          el("button", {
            type: "button",
            class: `pm-chip${cab.kind === "pending" ? " text-primary" : ""}${state.selected === p.id || state.selected === cab.peerPortId ? " sel" : ""}`,
            title: `${c.device.name} ${cab.peerName} ↔ ${other.device.name} ${p.name}`,
            onclick: () => select(p.id),
          },
          el("b", {}, shortIf(cab.peerName)), " ↔ ", other.isHub ? shortIf(p.name) : `${other.device.name} ${shortIf(p.name)}`),
        );
      }
    }
    if (!chips.children.length) chips.append(el("span", { class: "text-muted small" }, "No cables to devices on the bench"));
    return chips;
  }

  const TILE_STATES = ["st-connected", "st-planned", "st-decommissioning", "pend", "del", "armed", "dim", "ok", "sel"];

  function refreshTiles() {
    const armed = state.armed;
    const armedDev = armed == null ? null : state.ports.get(armed).card.device.id;
    for (const [id, { port, card: c }] of state.ports) {
      const tile = c.el.querySelector(`.pm-port[data-id="${id}"]`);
      if (!tile) continue;
      const cab = cableOf(id);
      tile.classList.remove(...TILE_STATES);
      // Tooltip lines: the port, where it goes, what the cable is.
      let title = `${port.name} · ${port.form_factor}${port.mgmt_only ? " · mgmt" : ""}`;
      if (cab) {
        title += `\n→ ${cab.peerDevice} ${cab.peerName}`;
        const detail = cableText(cab);
        if (cab.kind === "pending") {
          tile.classList.add("pend");
          title += `\nnew${detail ? " · " + detail : ""} — not saved`;
        } else if (cab.deleted) {
          tile.classList.add("del");
          title += `\ncable #${cab.id} — marked for deletion`;
        } else {
          tile.classList.add(`st-${cab.status || "connected"}`);
          title += `\n${[detail, statusLabel(cab.status), `#${cab.id}`].filter(Boolean).join(" · ")}`;
        }
        if (state.selected != null && (id === state.selected || id === cableOf(state.selected)?.peerPortId)) tile.classList.add("sel");
      }
      if (armed != null) {
        if (id === armed) tile.classList.add("armed");
        else if (c.device.id !== armedDev) {
          const v = state.compat.get(id);
          if (!v || v.level === "block") {
            tile.classList.add("dim");
            if (v) title += `\n✕ ${v.reason}`;
          } else {
            tile.classList.add("ok");
            title += `\n✓ ${v.reason}`;
          }
        }
      }
      tile.dataset.tip = title;
      tile.setAttribute("aria-label", title.replace(/\n/g, ", "));
      tile.removeAttribute("title");
    }
    for (const c of state.cards.values()) {
      const badge = c.el.querySelector(`[data-compat="${c.device.id}"]`);
      if (!badge) continue;
      badge.replaceChildren();
      if (armed == null || c.device.id === armedDev || !showsPorts(c)) continue;
      const free = c.ports.filter((p) => !cableOf(p.id) || cableOf(p.id).deleted);
      const okCount = free.filter((p) => (state.compat.get(p.id) || {}).level !== "block" && state.compat.has(p.id)).length;
      badge.append(el("span", { class: `badge ${okCount ? "bg-blue-lt" : "bg-secondary-lt"}` }, `compatible ${okCount} / ${free.length}`));
    }
  }

  // ------------------------------------------------------------------- lines

  let linesQueued = false;
  function scheduleLines() {
    if (linesQueued) return;
    linesQueued = true;
    requestAnimationFrame(() => {
      linesQueued = false;
      drawLines();
    });
  }

  function tileAnchor(portId, benchRect) {
    const entry = state.ports.get(portId);
    if (!entry || !showsPorts(entry.card)) return null;
    const tile = entry.card.el.querySelector(`.pm-port[data-id="${portId}"]`);
    if (!tile) return null;
    const r = tile.getBoundingClientRect();
    const grid = tile.closest(".pm-grid").getBoundingClientRect();
    if (r.right < grid.left || r.left > grid.right) return null; // scrolled out of its grid
    const inner = tile.parentElement;
    const ir = inner.getBoundingClientRect();
    const rows = entry.card.grid.rows;
    // Ports stacked in one column would get identical lines; nudge by row so both stay visible.
    const dx = (entry.port.row - (rows - 1) / 2) * 6;
    return {
      card: entry.card,
      x: r.left + r.width / 2 + dx - benchRect.left,
      cy: r.top + r.height / 2 - benchRect.top,
      // Column edges: where the line leaves the grid (see stubs in renderGrid).
      colTop: ir.top - benchRect.top,
      colBottom: ir.top + rows * PITCH - 4 - benchRect.top,
      family: entry.port.family,
      stubs: inner.querySelector(".pm-stubs"),
      lx: r.left + r.width / 2 + dx - ir.left,
      ly: r.top + r.height / 2 - ir.top,
    };
  }

  /** Cables to draw: hub <-> focused spoke (adjacent cards, so lines cross nothing), staged ones,
   *  and the selected one. Hovering never adds a line: a hover line to a spoke further down cut
   *  across every card in between. Selecting a hub cable brings its spoke under the hub instead. */
  function visibleLinks() {
    const links = new Map();
    const hubId = state.hub ? state.hub.device.id : null;
    const focusId = state.focus;
    const selKey = state.selected == null ? null : linkKeyOf(state.selected);
    const wanted = (aId, bId, key) => {
      if (key === selKey) return true;
      const da = state.ports.get(aId).card.device.id;
      const db = state.ports.get(bId).card.device.id;
      return (da === hubId && db === focusId) || (da === focusId && db === hubId);
    };
    for (const c of state.pending.create) links.set(`p${c.ref}`, { a: c.a, b: c.b, pending: true, key: `p${c.ref}` });
    for (const [id] of state.ports) {
      const cab = cableOf(id);
      if (!cab || cab.kind !== "existing" || cab.deleted || cab.peerPortId == null) continue;
      if (!state.ports.has(cab.peerPortId)) continue;
      const key = `c${cab.id}`;
      if (wanted(id, cab.peerPortId, key)) links.set(key, { a: id, b: cab.peerPortId, pending: false, key });
    }
    return [...links.values()];
  }

  function drawLines() {
    svg.replaceChildren();
    for (const s of bench.querySelectorAll(".pm-stubs")) s.replaceChildren();
    const benchRect = bench.getBoundingClientRect();
    svg.setAttribute("width", benchRect.width);
    svg.setAttribute("height", benchRect.height);
    const colours = getComputedStyle(document.documentElement);
    for (const link of visibleLinks()) {
      const a = tileAnchor(link.a, benchRect);
      const b = tileAnchor(link.b, benchRect);
      if (!a || !b) continue;
      const hi = link.key === state.hoverKey || (state.selected != null && link.key === linkKeyOf(state.selected));
      const cls = `pm-line${link.pending ? " pend" : ""}${hi ? " hi" : ""}`;
      const colour = colours.getPropertyValue(`--pm-${a.family}`).trim() || colours.getPropertyValue("--pm-other").trim();
      const [top, bot] = a.cy <= b.cy ? [a, b] : [b, a];
      // Stubs under the tiles: tile centre -> column edge, in each grid's own SVG.
      top.stubs.append(svgEl("line", { class: cls, x1: top.lx, y1: top.ly, x2: top.lx, y2: top.card.grid.rows * PITCH - 4, stroke: colour, "data-key": link.key }));
      bot.stubs.append(svgEl("line", { class: cls, x1: bot.lx, y1: 0, x2: bot.lx, y2: bot.ly, stroke: colour, "data-key": link.key }));
      const y1 = top.colBottom;
      const y2 = bot.colTop;
      const dy = Math.max(24, Math.min(80, (y2 - y1) / 2));
      svg.append(svgEl("path", {
        class: cls,
        d: `M ${top.x} ${y1} C ${top.x} ${y1 + dy}, ${bot.x} ${y2 - dy}, ${bot.x} ${y2}`,
        stroke: colour,
        "data-key": link.key,
      }));
      for (const [x, y] of [[top.x, y1], [bot.x, y2]]) {
        svg.append(svgEl("circle", { class: "pm-dot", cx: x, cy: y, r: 2.5, fill: colour, "data-key": link.key }));
      }
    }
  }

  function linkKeyOf(portId) {
    const cab = cableOf(portId);
    if (!cab || cab.deleted) return null;
    return cab.kind === "pending" ? `p${cab.ref}` : `c${cab.id}`;
  }

  function highlight(portId, on) {
    const cab = cableOf(portId);
    if (!cab || cab.deleted) return;
    const key = linkKeyOf(portId);
    state.hoverKey = on ? key : null;
    drawLines();
    for (const id of [portId, cab.peerPortId]) {
      const entry = id != null && state.ports.get(id);
      const tile = entry && entry.card.el.querySelector(`.pm-port[data-id="${id}"]`);
      if (tile) tile.classList.toggle("hi", on);
    }
    const row = $(`#pm-connections tr[data-key="${key}"]`);
    if (row) row.classList.toggle("hi", on);
  }

  /** Put a spoke right under the hub and draw its cables. */
  function setFocus(c) {
    if (!c || c.isHub) return;
    state.focus = c.device.id;
    if (!c.expanded) c.expanded = true;
    orderSpokes();
    rerender();
  }

  /** Hub ports in reading order (the order of the connection list and of ← →). */
  const hubPortOrder = () => (state.hub ? [...state.hub.ports].sort((x, y) => x.col - y.col || x.row - y.row) : []);

  /**
   * Spokes follow the hub's port order (the device on hub port 1 first), with the focused spoke
   * on top. Walking the hub's ports then walks down the bench instead of jumping around it.
   */
  function orderSpokes() {
    const rank = new Map();
    hubPortOrder().forEach((p, i) => {
      const cab = cableOf(p.id);
      if (cab && cab.peerDeviceId != null && !rank.has(cab.peerDeviceId)) rank.set(cab.peerDeviceId, i);
    });
    const key = (s) => (s.device.id === state.focus ? -1 : rank.has(s.device.id) ? rank.get(s.device.id) : 1e6);
    state.spokes.sort((a, b) => key(a) - key(b) || a.device.name.localeCompare(b.device.name));
    const host = $("#pm-spokes");
    for (const s of state.spokes) host.append(s.el);
  }

  // ------------------------------------------------------------ inspection

  const tip = el("div", { class: "pm-tip", role: "tooltip", hidden: true });
  document.body.append(tip);

  /** Immediate tooltip: the browser's title tooltip took a second and could not be styled. */
  function showTip(node) {
    const text = node.dataset.tip;
    if (!text) return;
    tip.replaceChildren(...text.split("\n").map((line, i) => el("div", { class: i === 0 ? "pm-tip-head" : null }, line)));
    tip.hidden = false;
    const r = node.getBoundingClientRect();
    const w = tip.offsetWidth;
    const left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
    // Above the port when there is room: below it, the tip covered the spoke right under the hub.
    const above = r.top - tip.offsetHeight - 8 > 0;
    tip.style.left = `${left + window.scrollX}px`;
    tip.style.top = `${(above ? r.top - tip.offsetHeight - 8 : r.bottom + 8) + window.scrollY}px`;
  }
  const hideTip = () => { tip.hidden = true; };

  /**
   * Inspect a connection: both ends are marked, a hub <-> spoke cable brings the spoke right under
   * the hub (so its line is short and both ends are on screen), and the inspector shows the cable.
   */
  function select(portId) {
    hideTip();
    const cab = portId == null ? null : cableOf(portId);
    if (!cab) return deselect();
    state.selected = portId;
    const me = state.ports.get(portId).card;
    const peer = cab.peerDeviceId != null ? card(cab.peerDeviceId) : null;
    let spoke = null;
    if (me.isHub && peer && !peer.busy) spoke = peer;
    else if (peer && peer.isHub) spoke = me;
    if (spoke && spoke.filteredOut) {
      filterBox.value = "";
    }
    if (spoke) setFocus(spoke);
    else rerender();
    if (spoke) revealUnderHub(spoke);
    const row = $(`#pm-connections tr[data-port="${me.isHub ? portId : cab.peerPortId}"]`);
    if (row) row.scrollIntoView({ block: "nearest" });
  }

  function deselect() {
    if (state.selected == null) return;
    state.selected = null;
    rerender();
  }

  /** Scroll so the focused spoke sits fully visible below the (sticky) hub. */
  function revealUnderHub(spoke) {
    requestAnimationFrame(() => {
      const hubBottom = Math.max(0, state.hub.el.getBoundingClientRect().bottom);
      const r = spoke.el.getBoundingClientRect();
      if (r.top < hubBottom + 4 || r.bottom > window.innerHeight) {
        window.scrollBy({ top: r.top - hubBottom - 40, behavior: "smooth" });
      }
    });
  }

  /** ← / →: step through the hub's cables in port order. */
  function step(delta) {
    const order = hubPortOrder().filter((p) => cableOf(p.id));
    if (!order.length) return;
    let from = -1;
    if (state.selected != null) {
      const entry = state.ports.get(state.selected);
      const hubPort = entry && entry.card.isHub ? state.selected : cableOf(state.selected)?.peerPortId;
      from = order.findIndex((p) => p.id === hubPort);
    }
    const next = from < 0 ? (delta > 0 ? 0 : order.length - 1) : (from + delta + order.length) % order.length;
    select(order[next].id);
  }

  function renderInspector() {
    const body = $("#pm-inspector-body");
    if (!body) return;
    const cab = state.selected == null ? null : cableOf(state.selected);
    const count = $("#pm-inspector-pos");
    const order = hubPortOrder().filter((p) => cableOf(p.id));
    if (!cab) {
      body.replaceChildren(el("div", { class: "text-muted small" },
        "Click a cabled port, a chip or a row of the list to inspect its connection. ",
        el("kbd", {}, "←"), " ", el("kbd", {}, "→"), " step through the hub's cables, ",
        el("kbd", {}, "Esc"), " clears. Double-click a port to edit its cable."));
      if (count) count.textContent = order.length ? `${order.length} cables on the hub` : "";
      return;
    }
    const me = state.ports.get(state.selected);
    // Show the hub end first when the hub is one of the ends.
    let a = { device: me.card.device, port: me.port, portId: state.selected };
    let b = cab.peerPortId != null && state.ports.get(cab.peerPortId)
      ? { device: state.ports.get(cab.peerPortId).card.device, port: state.ports.get(cab.peerPortId).port, portId: cab.peerPortId }
      : { device: { name: cab.peerDevice, id: cab.peerDeviceId }, port: { name: cab.peerName } };
    if (!me.card.isHub && b.device.id === (state.hub && state.hub.device.id)) [a, b] = [b, a];
    const end = (x) => el("div", { class: "pm-end" },
      el("div", {}, el("b", {}, x.device.name), " ", el("span", { class: "pm-end-port" }, shortIf(x.port.name))),
      el("div", { class: "text-muted small" },
        [x.port.form_factor, x.device.rack, x.device.position != null ? `U${x.device.position}` : null].filter(Boolean).join(" · ")));
    const status = cab.kind === "pending" ? "new — not saved" : cab.deleted ? "marked for deletion" : statusLabel(cab.status);
    const statusClass = cab.kind === "pending" ? "pend" : cab.deleted ? "del" : `st-${cab.status || "connected"}`;
    const actions = el("div", { class: "d-flex gap-2 mt-3" },
      el("button", { type: "button", class: "btn btn-sm btn-primary", onclick: () => openEditor(cab, state.selected) }, cfg.canEdit ? "Edit" : "Details"),
      cab.kind === "existing" ? el("a", { class: "btn btn-sm btn-outline-secondary", href: `/dcim/cables/${cab.id}/` }, `Cable #${cab.id}`) : null,
      cab.peerDeviceId != null && !state.cards.has(cab.peerDeviceId)
        ? el("button", { type: "button", class: "btn btn-sm btn-outline-primary", onclick: async () => { await addSpoke(cab.peerDeviceId, true); select(state.selected); } }, "Add peer to bench")
        : null,
    );
    body.replaceChildren(
      end(a),
      el("div", { class: "pm-link" },
        el("span", { class: `pm-status ${statusClass}` }, status),
        el("span", {}, cableText(cab) || "no type / length"),
        cab.label ? el("span", { class: "text-muted small pm-label", title: cab.label }, cab.label) : null),
      end(b),
      elevation(a.device, b.device.rack ? b.device : (me.port.peer && me.port.peer.rack ? me.port.peer : b.device)),
      actions,
    );
    if (count) {
      const hubPort = a.portId != null && state.ports.get(a.portId)?.card.isHub ? a.portId : null;
      const i = order.findIndex((p) => p.id === hubPort);
      count.textContent = i >= 0 ? `${i + 1} / ${order.length} on the hub` : "";
    }
  }

  /** Where the two ends sit: the rack(s) drawn as a strip, the two devices marked at their U,
   *  and — in different racks — how many racks apart. Nothing else; the eye reads it as "low
   *  in 03-03, high in 03-12, nine racks over". */
  const rackIndex = (name) => { const m = /(\d+)\s*$/.exec(name || ""); return m ? Number(m[1]) : null; };
  function elevation(a, b) {
    if (!a || !b || !a.rack || !b.rack || a.position == null || b.position == null) return null;
    const same = a.rack_id != null && a.rack_id === b.rack_id;
    const racks = same ? [{ name: a.rack, height: a.rack_height || 42, devices: [a, b] }]
      : [{ name: a.rack, height: a.rack_height || 42, devices: [a] }, { name: b.rack, height: b.rack_height || 42, devices: [b] }];
    const perU = 2.6, w = 22, gap = 46, top = 16, bottom = 16;
    const tall = Math.max(...racks.map((r) => r.height)) * perU;
    const svg = svgEl("svg", { class: "pm-elev", width: racks.length * (w + gap) + 60, height: tall + top + bottom, role: "img", "aria-label": `${a.name} ${a.rack} U${a.position}; ${b.name} ${b.rack} U${b.position}` });
    racks.forEach((r, i) => {
      const x0 = 30 + i * (w + gap), h = r.height * perU;
      svg.append(svgEl("rect", { x: x0, y: top + tall - h, width: w, height: h, class: "pm-elev-rack" }));
      // A tick every 10U, numbered from the bottom like the rack itself.
      for (let u = 10; u < r.height; u += 10) {
        const y = top + tall - u * perU;
        svg.append(svgEl("line", { x1: x0 - 3, x2: x0, y1: y, y2: y, class: "pm-elev-tick" }));
      }
      const name = svgEl("text", { x: x0 + w / 2, y: top + tall + 12, class: "pm-elev-name", "text-anchor": "middle" }); name.textContent = r.name; svg.append(name);
      for (const d of r.devices) {
        const uh = Math.max(d.u_height || 1, 1), y = top + tall - (d.position - 1 + uh) * perU, h = Math.max(uh * perU, 3);
        svg.append(svgEl("rect", { x: x0 + 1, y, width: w - 2, height: h, class: "pm-elev-dev" + (d === a ? " a" : " b") }));
        const t = svgEl("text", { x: x0 + w + 5, y: y + h / 2 + 3.5, class: "pm-elev-u" }); t.textContent = `U${d.position}`; svg.append(t);
      }
    });
    if (!same) {
      const x1 = 30 + w / 2, x2 = 30 + (w + gap) + w / 2, y = top - 3;
      svg.append(svgEl("path", { d: `M${x1} ${top + tall - (a.position - 1 + (a.u_height || 1)) * perU} V${y} H${x2} V${top + tall - (b.position - 1 + (b.u_height || 1)) * perU}`, class: "pm-elev-run" }));
      const ia = rackIndex(a.rack), ib = rackIndex(b.rack), apart = ia != null && ib != null ? Math.abs(ia - ib) : null;
      if (apart) { const t = svgEl("text", { x: (x1 + x2) / 2, y: y - 2, class: "pm-elev-name", "text-anchor": "middle" }); t.textContent = `${apart} rack${apart > 1 ? "s" : ""} apart`; svg.append(t); }
    }
    return el("div", { class: "pm-elev-wrap" }, svg);
  }

  // --------------------------------------------------------------- arming

  async function arm(portId) {
    state.armed = portId;
    state.compat = new Map();
    const { port, card: c } = state.ports.get(portId);
    const badge = $("#pm-armed");
    badge.textContent = `Connecting ${c.device.name} ${port.name} — click a free port on another device, Esc to cancel`;
    badge.hidden = false;
    refreshTiles();
    const targets = [];
    for (const [id, entry] of state.ports) {
      if (entry.card.device.id === c.device.id || !showsPorts(entry.card)) continue;
      targets.push(id);
    }
    if (!targets.length) return;
    try {
      const res = await api("check/", { a: portId, targets, free_cables: [...state.pending.delete] });
      if (state.armed !== portId) return;
      for (const [id, v] of Object.entries(res.results)) state.compat.set(Number(id), v);
      // The server does not know about staged cables; those ports are taken too.
      for (const id of targets) {
        const cab = cableOf(id);
        if (cab && cab.kind === "pending") state.compat.set(id, { level: "block", reason: "already has a pending cable" });
      }
    } catch (e) {
      toast(`Compatibility check failed: ${e.message}`, "danger");
    }
    refreshTiles();
  }

  function disarm() {
    state.armed = null;
    state.compat = new Map();
    $("#pm-armed").hidden = true;
    refreshTiles();
  }

  function onPortClick(portId) {
    const { card: c } = state.ports.get(portId);
    const cab = cableOf(portId);
    if (state.armed == null) {
      if (cab) return state.selected === portId ? deselect() : select(portId);
      if (cfg.canEdit) {
        state.selected = null;
        arm(portId);
      }
      return;
    }
    if (portId === state.armed) return disarm();
    if (c.device.id === armedCard().device.id) {
      // Another port on the same device: switch to it if it is free, otherwise show its cable.
      if (cab && !cab.deleted) {
        disarm();
        return select(portId);
      }
      return arm(portId);
    }
    const v = state.compat.get(portId);
    if (!v) return toast("Still checking compatibility, try again", "warning");
    if (v.level === "block") return toast(`Cannot connect: ${v.reason}`, "warning");
    stageCreate(state.armed, portId, v);
  }

  function stageCreate(a, b, verdict) {
    const item = {
      ref: `n${++state.seq}`,
      a,
      b,
      type: verdict.cable_type || "",
      status: CHOICES.default_status,
      label: "",
      length: verdict.length,
      length_unit: verdict.length_unit || "",
      verdict,
    };
    state.pending.create.push(item);
    const pa = state.ports.get(a);
    const pb = state.ports.get(b);
    disarm();
    rerender();
    const detail = [labelOf(CHOICES.cable_types, item.type), fmtLen(item.length, item.length_unit)].filter(Boolean).join(" · ");
    toast(`Staged ${pa.card.device.name} ${pa.port.name} ↔ ${pb.card.device.name} ${pb.port.name}${detail ? " · " + detail : ""}`,
      verdict.level === "warn" ? "warning" : "info");
    if (verdict.level === "warn") toast(verdict.reason, "warning");
  }

  // --------------------------------------------------------------- editor

  const editor = {
    node: $("#pm-editor"),
    type: $("#pm-editor-type"),
    length: $("#pm-editor-length"),
    unit: $("#pm-editor-unit"),
    label: $("#pm-editor-label"),
    status: $("#pm-editor-status"),
    current: null,
  };

  function fillSelect(select, choices, blank) {
    select.replaceChildren();
    if (blank) select.append(el("option", { value: "" }, blank));
    for (const g of choices) {
      const host = g.group ? el("optgroup", { label: g.group }) : select;
      for (const [v, l] of g.options) host.append(el("option", { value: v }, l));
      if (g.group) select.append(host);
    }
  }
  fillSelect(editor.type, CHOICES.cable_types, "—");
  fillSelect(editor.unit, CHOICES.length_units, "—");
  fillSelect(editor.status, CHOICES.statuses);

  // NetBox bundles Bootstrap's offcanvas but does not expose window.bootstrap. Toggling the
  // "show" class by hand left an editor that neither the close buttons nor Esc could close
  // (Bootstrap's dismiss handler ignores an instance it did not open), so drive it through
  // Bootstrap's data API with a hidden trigger. Only without Bootstrap at all fall back to the class.
  const editorTrigger = el("button", { type: "button", hidden: true, "data-bs-toggle": "offcanvas", "data-bs-target": "#pm-editor" });
  document.body.append(editorTrigger);
  const editorIsOpen = () => editor.node.classList.contains("show") || editor.node.classList.contains("showing");

  function showOffcanvas(show) {
    const Off = window.bootstrap && window.bootstrap.Offcanvas;
    if (Off) {
      const inst = Off.getOrCreateInstance(editor.node);
      show ? inst.show() : inst.hide();
      return;
    }
    if (show === editorIsOpen()) return;
    if (show) {
      editorTrigger.click();
      editor.fallback = !editorIsOpen();
      if (editor.fallback) editor.node.classList.add("show");
    } else if (editor.fallback) {
      editor.node.classList.remove("show");
    } else {
      editor.node.querySelector('[data-bs-dismiss="offcanvas"]').click();
    }
  }
  // Fallback only: close buttons and Esc when Bootstrap is not on the page.
  for (const b of editor.node.querySelectorAll('[data-bs-dismiss="offcanvas"]')) {
    b.addEventListener("click", () => { if (editor.fallback) editor.node.classList.remove("show"); });
  }

  function openEditor(cab, fromPortId) {
    const me = state.ports.get(fromPortId);
    editor.current = { cab, fromPortId };
    const ends = $("#pm-editor-ends");
    ends.replaceChildren(
      el("div", {}, el("b", {}, me.card.device.name), " ", me.port.name, el("span", { class: "text-muted" }, ` · ${me.port.form_factor}`)),
      el("div", { class: "text-muted my-1" }, "↕"),
      el("div", {}, el("b", {}, cab.peerDevice), " ", cab.peerName,
        cab.peerDeviceId != null && !state.cards.has(cab.peerDeviceId)
          ? el("button", { type: "button", class: "btn btn-sm btn-link p-0 ms-2", onclick: () => addSpoke(cab.peerDeviceId, true) }, "add to bench")
          : null),
    );
    const verdict = $("#pm-editor-verdict");
    verdict.replaceChildren();
    if (cab.kind === "pending") {
      verdict.append(el("div", { class: `alert alert-${cab.verdict.level === "warn" ? "warning" : "info"} py-2 mb-0` }, cab.verdict.reason));
    } else if (cab.deleted) {
      verdict.append(el("div", { class: "alert alert-danger py-2 mb-0" }, `Cable #${cab.id} is marked for deletion.`));
    } else {
      verdict.append(el("div", { class: "text-muted small" }, "Cable ", el("a", { href: `/dcim/cables/${cab.id}/` }, `#${cab.id}`), cab.edited ? " · edited, not saved" : ""));
    }
    editor.type.value = cab.type || "";
    editor.length.value = cab.length == null ? "" : cab.length;
    editor.unit.value = cab.length_unit || "";
    editor.label.value = cab.label || "";
    editor.status.value = cab.status || CHOICES.default_status;
    $("#pm-editor-hint").textContent =
      cab.kind === "pending" && cab.verdict.length != null
        ? `Suggested from rack positions: ${fmtLen(cab.verdict.length, cab.verdict.length_unit)}`
        : "";
    const del = $("#pm-editor-delete");
    del.textContent = cab.kind === "pending" ? "Discard" : cab.deleted ? "Restore" : "Delete";
    del.className = `btn ${cab.deleted ? "btn-outline-success" : "btn-outline-danger"}`;
    del.hidden = !cfg.canEdit;
    // A port whose cable is marked for deletion is free for this session: offer to reuse it.
    const reuse = $("#pm-editor-reuse");
    reuse.hidden = !cfg.canEdit || !cab.deleted || cableOf(fromPortId).kind === "pending";
    $("#pm-editor-apply").hidden = !cfg.canEdit || cab.deleted;
    for (const f of [editor.type, editor.length, editor.unit, editor.label, editor.status]) f.disabled = !cfg.canEdit || cab.deleted;
    showOffcanvas(true);
  }

  function applyEditor() {
    const { cab } = editor.current || {};
    if (!cab) return;
    const fields = {
      type: editor.type.value,
      status: editor.status.value,
      label: editor.label.value.trim(),
      length: editor.length.value === "" ? null : Number(editor.length.value),
      length_unit: editor.unit.value,
    };
    if (fields.length != null && !fields.length_unit) return toast("Pick a length unit", "warning");
    if (cab.kind === "pending") {
      Object.assign(cab.item, fields);
    } else {
      const entry = state.ports.get(editor.current.fromPortId);
      const p = entry.port;
      const orig = { type: p.cable_type || "", status: p.cable_status || "", label: p.cable_label || "", length: p.cable_length, length_unit: p.cable_unit || "" };
      const diff = {};
      for (const k of Object.keys(fields)) if (fields[k] !== orig[k] && !(fields[k] == null && orig[k] == null)) diff[k] = fields[k];
      if (Object.keys(diff).length) state.pending.update.set(cab.id, diff);
      else state.pending.update.delete(cab.id);
    }
    showOffcanvas(false);
    rerender();
  }

  function deleteFromEditor() {
    const { cab } = editor.current || {};
    if (!cab) return;
    if (cab.kind === "pending") {
      state.pending.create = state.pending.create.filter((c) => c.ref !== cab.ref);
    } else if (cab.deleted) {
      // Restoring needs both ends back; a staged cable may have taken one of them meanwhile.
      const ends = [editor.current.fromPortId, cab.peerPortId];
      const taker = state.pending.create.find((c) => ends.includes(c.a) || ends.includes(c.b));
      if (taker) return toast("A staged cable now uses one of its ports — discard that cable first", "warning");
      state.pending.delete.delete(cab.id);
    } else {
      state.pending.delete.add(cab.id);
      state.pending.update.delete(cab.id);
    }
    showOffcanvas(false);
    rerender();
  }

  $("#pm-editor-apply").addEventListener("click", applyEditor);
  $("#pm-editor-delete").addEventListener("click", deleteFromEditor);
  $("#pm-editor-reuse").addEventListener("click", () => {
    const { fromPortId } = editor.current || {};
    if (fromPortId == null) return;
    showOffcanvas(false);
    arm(fromPortId);
  });

  // ----------------------------------------------------------- connections

  function renderConnections() {
    const tbody = $("#pm-connections tbody");
    tbody.replaceChildren();
    if (!state.hub) return;
    const rows = [];
    for (const p of hubPortOrder()) {
      const cab = cableOf(p.id);
      if (cab) rows.push([p, cab]);
    }
    if (!rows.length) {
      tbody.append(el("tr", {}, el("td", { colspan: 3, class: "text-muted" }, "No cables on the hub yet")));
      return;
    }
    const selHubPort = state.selected == null ? null
      : state.ports.get(state.selected)?.card.isHub ? state.selected : cableOf(state.selected)?.peerPortId;
    for (const [p, cab] of rows) {
      const key = cab.kind === "pending" ? `p${cab.ref}` : `c${cab.id}`;
      const statusClass = cab.kind === "pending" ? "pend" : cab.deleted ? "del" : `st-${cab.status || "connected"}`;
      const statusText = cab.kind === "pending" ? "new" : cab.deleted ? "delete" : statusLabel(cab.status);
      const peerCell = el("td", {}, el("b", {}, cab.peerDevice), " ", shortIf(cab.peerName));
      if (cab.peerDeviceId != null && !state.cards.has(cab.peerDeviceId)) {
        peerCell.append(" ", el("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); e.stopPropagation(); addSpoke(cab.peerDeviceId, true); } }, "+ bench"));
      }
      const text = cableText(cab);
      tbody.append(
        el("tr", {
          dataset: { key, port: p.id },
          class: [cab.deleted ? "text-muted text-decoration-line-through" : "", p.id === selHubPort ? "table-active pm-row-sel" : ""].join(" ").trim(),
          onclick: () => select(p.id),
          ondblclick: () => openEditor(cab, p.id),
          onmouseenter: () => highlight(p.id, true),
          onmouseleave: () => highlight(p.id, false),
        },
          el("td", {}, el("i", { class: `pm-dot-status ${statusClass}`, title: statusText }), shortIf(p.name)),
          peerCell,
          el("td", { title: [text, statusText, cab.label].filter(Boolean).join(" · ") }, text || "—"),
        ),
      );
    }
  }

  // ------------------------------------------------------------ pending UI

  function renderPending() {
    const n = pendingCount();
    $("#pm-pending").textContent = `Pending ${n}`;
    $("#pm-pending").className = `badge ${n ? "bg-blue" : "bg-blue-lt"}`;
    for (const id of ["#pm-save", "#pm-discard"]) {
      const b = $(id);
      if (b) b.disabled = !n;
    }
  }

  async function save() {
    const payload = {
      create: state.pending.create.map(({ ref, a, b, type, status, label, length, length_unit }) => ({ ref, a, b, type, status, label, length, length_unit })),
      update: [...state.pending.update].map(([id, f]) => ({ id, ...f })),
      delete: [...state.pending.delete],
    };
    const btn = $("#pm-save");
    const discardBtn = $("#pm-discard");
    const label = btn.textContent;
    btn.disabled = true;
    if (discardBtn) discardBtn.disabled = true;
    btn.textContent = "Saving…";
    let res;
    try {
      res = await api("commit/", payload);
    } catch (e) {
      const errors = (e.body && e.body.errors) || [{ error: e.message }];
      showErrors(errors);
      btn.textContent = label;
      renderPending();
      return;
    }
    btn.textContent = label;
    clearErrors();
    toast(`Saved: ${res.created.length} created, ${res.updated} updated, ${res.deleted} deleted`, "success");
    state.pending = { create: [], update: new Map(), delete: new Set() };
    // The commit is done; a failure from here on must not read as "nothing was saved".
    try {
      await reloadAll();
    } catch (e) {
      toast(`Saved, but refreshing the bench failed (${e.message}). Reload the page.`, "warning");
      rerender();
    }
  }

  function clearErrors() {
    const old = $("#pm-errors");
    if (old) old.remove();
  }

  function showErrors(errors) {
    clearErrors();
    const box = el("div", { id: "pm-errors", class: "alert alert-danger alert-dismissible" },
      el("div", { class: "fw-bold mb-1" }, "Nothing was saved:"),
      el("ul", { class: "mb-0" }, errors.map((e) => el("li", {}, describeError(e)))),
      el("button", { type: "button", class: "btn-close", onclick: () => box.remove() }),
    );
    bench.before(box);
  }

  function describeError(e) {
    if (e.ref) {
      const item = state.pending.create.find((c) => c.ref === e.ref);
      if (item) {
        const a = state.ports.get(item.a), b = state.ports.get(item.b);
        return `${a.card.device.name} ${a.port.name} ↔ ${b.card.device.name} ${b.port.name}: ${e.error}`;
      }
    }
    if (e.op === "update" && e.id != null) {
      for (const { port, card: c } of state.ports.values()) {
        if (port.cable === e.id) return `Cable #${e.id} (${c.device.name} ${port.name}): ${e.error}`;
      }
    }
    return `${e.op ? e.op + " " : ""}${e.id ? "#" + e.id + " " : ""}${e.error}`;
  }

  function discard() {
    state.pending = { create: [], update: new Map(), delete: new Set() };
    clearErrors();
    disarm();
    rerender();
  }

  // ---------------------------------------------------------------- spokes

  /** Run fn over items with at most `limit` in flight; results keep the input order. */
  async function mapLimit(items, limit, fn) {
    const out = new Array(items.length);
    let next = 0;
    const worker = async () => {
      while (next < items.length) {
        const i = next++;
        out[i] = await fn(items[i], i);
      }
    };
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
    return out;
  }

  const LOAD_CONCURRENCY = 6;

  /**
   * Put devices on the bench as spokes, in the given order. Loading happens in parallel and
   * the bench is rendered once at the end: one request and one full re-render per spoke made a
   * hub with forty peers take seconds to appear.
   */
  async function addSpokes(deviceIds, expandedOf) {
    const fresh = [];
    for (const id of deviceIds) {
      if (state.cards.has(id)) {
        const c = card(id);
        if (!c.isHub && !c.busy && !c.expanded && expandedOf(id)) c.expanded = true;
        continue;
      }
      const placeholder = { device: { id }, ports: [], busy: true, isHub: false, expanded: false, el: el("div", { class: "card pm-spoke" }) };
      state.cards.set(id, placeholder);
      fresh.push(placeholder);
    }
    const status = $("#pm-spoke-count");
    if (fresh.length > 1 && status) status.textContent = `loading ${fresh.length} devices…`;
    const loaded = await mapLimit(fresh, LOAD_CONCURRENCY, (ph) =>
      loadCard(ph.device.id, false).catch((e) => ({ error: e, id: ph.device.id })));
    const failed = [];
    for (const [i, c] of loaded.entries()) {
      const ph = fresh[i];
      if (c.error) {
        state.cards.delete(ph.device.id);
        failed.push(`${ph.device.id} (${c.error.message})`);
        continue;
      }
      c.expanded = expandedOf(ph.device.id);
      c.el = ph.el;
      state.cards.set(c.device.id, c);
      state.spokes.push(c);
      registerPorts(c);
      $("#pm-spokes").append(c.el);
    }
    if (failed.length) toast(`Could not load ${failed.length > 1 ? "devices" : "device"} ${failed.join(", ")}`, "danger");
    orderSpokes();
    if (state.focus == null && state.spokes.length) {
      state.focus = state.spokes[0].device.id; // the first spoke starts focused
      orderSpokes();
    }
    rerender();
    if (state.armed != null) arm(state.armed); // new ports to check against
  }

  async function addSpoke(deviceId, expanded) {
    await addSpokes([deviceId], () => expanded);
    return card(deviceId) || null;
  }

  function removeSpoke(c) {
    if (state.pending.create.some((p) => [p.a, p.b].some((id) => state.ports.get(id)?.card === c))) {
      return toast("Discard or save its pending cables first", "warning");
    }
    unregisterPorts(c);
    if (state.selected != null && !state.ports.has(state.selected)) state.selected = null;
    state.cards.delete(c.device.id);
    state.spokes = state.spokes.filter((s) => s !== c);
    c.el.remove();
    if (state.focus === c.device.id) state.focus = state.spokes.length ? state.spokes[0].device.id : null;
    rerender();
  }

  function toggleSpoke(c) {
    c.expanded = !c.expanded;
    if (state.armed != null) disarm();
    rerender();
  }

  // ------------------------------------------------------------------ shelf

  let shelf = null;

  async function loadShelf(rackId) {
    const list = $("#pm-shelf-list");
    if (!rackId) {
      shelf = null;
      list.replaceChildren(el("div", { class: "list-group-item text-muted small" }, "Pick a rack above to list its devices."));
      $("#pm-shelf-rack").textContent = "";
      $("#pm-shelf-add").disabled = true;
      return;
    }
    try {
      shelf = await api(`racks/${rackId}/devices/`);
    } catch (e) {
      toast(`Could not load rack: ${e.message}`, "danger");
      return;
    }
    $("#pm-shelf-rack").textContent = shelf.rack.name;
    $("#pm-shelf").hidden = false;
    renderShelf();
  }

  function renderShelf() {
    if (!shelf) return;
    const list = $("#pm-shelf-list");
    list.replaceChildren();
    for (const d of shelf.devices) {
      const onBench = state.cards.has(d.id);
      list.append(
        el("label", { class: `list-group-item ${onBench ? "text-muted" : ""}` },
          el("input", { type: "checkbox", class: "form-check-input m-0", value: d.id, disabled: onBench, onchange: updateShelfButton }),
          el("span", { class: "text-muted small", style: "width:2.5em" }, d.position != null ? `U${d.position}` : ""),
          el("span", { class: "flex-grow-1 text-truncate" }, d.name),
          onBench ? el("span", { class: "badge bg-secondary-lt" }, card(d.id).isHub ? "hub" : "on bench") : el("span", { class: "text-muted small" }, d.role || ""),
        ),
      );
    }
    updateShelfButton();
  }

  function updateShelfButton() {
    $("#pm-shelf-add").disabled = !$("#pm-shelf-list input:checked");
  }

  $("#pm-shelf-add").addEventListener("click", async () => {
    const ids = [...document.querySelectorAll("#pm-shelf-list input:checked")].map((i) => Number(i.value));
    await addSpokes(ids, () => true);
  });

  // --------------------------------------------------------------- render

  const filterBox = $("#pm-filter");

  function matchesFilter(c) {
    const q = (filterBox ? filterBox.value : "").trim().toLowerCase();
    if (!q) return true;
    const d = c.device;
    return [d.name, d.device_type, d.rack, d.role].some((v) => v && String(v).toLowerCase().includes(q));
  }

  function renderSpokeCount() {
    const node = $("#pm-spoke-count");
    if (!node) return;
    const ready = state.spokes.length;
    const shown = state.spokes.filter((s) => !s.filteredOut).length;
    node.textContent = !ready ? "" : shown === ready ? `${ready} spokes` : `${shown} of ${ready} spokes`;
  }

  function setAllExpanded(expanded) {
    for (const s of state.spokes) if (!s.filteredOut) s.expanded = expanded;
    if (state.armed != null) disarm();
    rerender();
  }

  function rerender() {
    for (const c of state.cards.values()) {
      if (!c.el || c.busy) continue;
      c.filteredOut = !c.isHub && !matchesFilter(c);
      c.el.hidden = c.filteredOut;
      renderCard(c);
    }
    renderSpokeCount();
    refreshTiles();
    renderInspector();
    renderConnections();
    renderPending();
    renderShelf();
    scheduleLines();
  }

  async function reloadAll() {
    const expanded = new Map(state.spokes.map((s) => [s.device.id, s.expanded]));
    const hub = await loadCard(cfg.hubId, true);
    hub.el = state.hub.el;
    unregisterPorts(state.hub);
    state.hub = hub;
    state.cards.set(hub.device.id, hub);
    registerPorts(hub);
    const fresh = await mapLimit(state.spokes, LOAD_CONCURRENCY, (s) => loadCard(s.device.id, false));
    const spokes = [];
    for (const [i, s] of state.spokes.entries()) {
      const c = fresh[i];
      c.el = s.el;
      c.expanded = expanded.get(s.device.id);
      unregisterPorts(s);
      state.cards.set(c.device.id, c);
      registerPorts(c);
      spokes.push(c);
    }
    state.spokes = spokes;
    if (state.selected != null && !state.ports.has(state.selected)) state.selected = null;
    orderSpokes();
    rerender();
  }

  async function init() {
    const [hub, { peers }] = await Promise.all([loadCard(cfg.hubId, true), api(`devices/${cfg.hubId}/peers/`)]);
    hub.el = $("#pm-hub");
    state.hub = hub;
    state.cards.set(hub.device.id, hub);
    registerPorts(hub);
    rerender();
    if (cfg.rackId) loadShelf(cfg.rackId);
    const open = new Set(peers.slice(0, EXPAND_DEFAULT).map((p) => p.id));
    await addSpokes(peers.map((p) => p.id), (id) => open.has(id));
  }

  // ---------------------------------------------------------------- wiring

  $("#pm-save")?.addEventListener("click", save);
  if (filterBox) {
    filterBox.addEventListener("input", () => {
      rerender();
      if (state.armed != null) arm(state.armed); // re-check against the spokes now visible
    });
  }
  $("#pm-prev")?.addEventListener("click", () => step(-1));
  $("#pm-next")?.addEventListener("click", () => step(1));
  $("#pm-expand-all")?.addEventListener("click", () => setAllExpanded(true));
  $("#pm-collapse-all")?.addEventListener("click", () => setAllExpanded(false));
  $("#pm-discard")?.addEventListener("click", discard);
  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest && e.target.closest("input, select, textarea, [contenteditable]");
    if (!typing && !editorIsOpen() && (e.key === "ArrowRight" || e.key === "ArrowLeft") && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      step(e.key === "ArrowRight" ? 1 : -1);
      return;
    }
    if (e.key !== "Escape") return;
    if (editor.fallback && editorIsOpen()) showOffcanvas(false);
    else if (state.armed != null) disarm();
    else if (!editorIsOpen() && state.selected != null) deselect();
  });
  window.addEventListener("resize", scheduleLines);
  window.addEventListener("scroll", scheduleLines, { passive: true });
  window.addEventListener("beforeunload", (e) => {
    if (pendingCount()) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
  // Rack (shelf) changes need no page reload — that would drop pending work.
  const rackSelect = $("#id_rack");
  if (rackSelect) {
    rackSelect.addEventListener("change", () => {
      const url = new URL(location.href);
      rackSelect.value ? url.searchParams.set("rack", rackSelect.value) : url.searchParams.delete("rack");
      history.replaceState(null, "", url);
      loadShelf(rackSelect.value);
    });
  }
  $("#pm-form").addEventListener("submit", (e) => {
    if (pendingCount() && !window.confirm("Pending changes will be lost. Continue?")) e.preventDefault();
  });

  init().catch((e) => {
    toast(`Failed to load: ${e.message}`, "danger");
    console.error(e);
  });
})();
