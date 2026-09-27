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
    hoverKey: null, // link key under the pointer, drawn even when not in focus
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

  const onScreen = (deviceId) => {
    const c = card(deviceId);
    return !!c && (c.isHub || c.expanded);
  };

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
        !c.isHub && toHub ? el("span", { class: "text-muted small", title: "cables to the hub" }, `${toHub} to hub`) : null,
        el("span", { class: "text-muted small", title: "cabled / total ports" }, `${cabled} / ${c.ports.length}`),
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
            dataset: { id: p.id, family: p.family },
            style: `left:${p.col * PITCH}px; top:${p.row * PITCH}px`,
            onclick: () => onPortClick(p.id),
            onmouseenter: () => highlight(p.id, true),
            onmouseleave: () => highlight(p.id, false),
          },
          shortLabel(p),
        ),
      );
    }
    for (const grp of g.groups) {
      const width = (grp.end - grp.start + 1) * PITCH + (grp.end < g.cols - 1 ? PITCH - 6 : -4); // may run into the gap
      inner.append(el("span", { class: "pm-group", style: `left:${grp.start * PITCH}px; max-width:${width}px`, title: grp.label }, grp.label));
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
          el("span", { class: `pm-chip${cab.kind === "pending" ? " text-primary" : ""}` },
            el("b", {}, cab.peerName), "↔", `${other.device.name} ${p.name}`),
        );
      }
    }
    if (!chips.children.length) chips.append(el("span", { class: "text-muted small" }, "No cables to devices on the bench"));
    return chips;
  }

  function refreshTiles() {
    const armed = state.armed;
    const armedDev = armed == null ? null : state.ports.get(armed).card.device.id;
    for (const [id, { port, card: c }] of state.ports) {
      const tile = c.el.querySelector(`.pm-port[data-id="${id}"]`);
      if (!tile) continue;
      const cab = cableOf(id);
      tile.classList.remove("on", "far", "pend", "del", "armed", "dim", "ok");
      let title = `${port.name} · ${port.form_factor}${port.mgmt_only ? " · mgmt" : ""}`;
      if (cab && cab.kind === "pending") {
        tile.classList.add("pend");
        title += `\n→ ${cab.peerDevice} ${cab.peerName} (pending${cab.type ? ", " + labelOf(CHOICES.cable_types, cab.type) : ""})`;
      } else if (cab && cab.deleted) {
        tile.classList.add("del");
        title += `\n→ ${cab.peerDevice} ${cab.peerName} · cable #${cab.id} marked for deletion`;
      } else if (cab) {
        tile.classList.add(onScreen(cab.peerDeviceId) ? "on" : "far");
        title += `\n→ ${cab.peerDevice} ${cab.peerName} · cable #${cab.id}`;
        const detail = [labelOf(CHOICES.cable_types, cab.type), fmtLen(cab.length, cab.length_unit), cab.label].filter(Boolean).join(", ");
        if (detail) title += ` (${detail})`;
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
      tile.title = title;
    }
    for (const c of state.cards.values()) {
      const badge = c.el.querySelector(`[data-compat="${c.device.id}"]`);
      if (!badge) continue;
      badge.replaceChildren();
      if (armed == null || c.device.id === armedDev || !(c.isHub || c.expanded)) continue;
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
    if (!entry || !(entry.card.isHub || entry.card.expanded)) return null;
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
   *  and the one under the pointer. Everything else is visible as tile state and in the list. */
  function visibleLinks() {
    const links = new Map();
    const hubId = state.hub ? state.hub.device.id : null;
    const focusId = state.focus;
    const wanted = (aId, bId, key) => {
      if (key === state.hoverKey) return true;
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
      const hi = link.key === state.hoverKey;
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
    state.spokes = [c, ...state.spokes.filter((s) => s !== c)];
    $("#pm-spokes").prepend(c.el);
    rerender();
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
      if (entry.card.device.id === c.device.id || !(entry.card.isHub || entry.card.expanded)) continue;
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
      if (cab) openEditor(cab, portId);
      else if (cfg.canEdit) arm(portId);
      return;
    }
    if (portId === state.armed) return disarm();
    if (c.device.id === armedCard().device.id) return arm(portId); // pick a different port on the same device
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

  function showOffcanvas(show) {
    const Off = window.bootstrap && window.bootstrap.Offcanvas;
    if (Off) {
      const inst = Off.getOrCreateInstance(editor.node);
      show ? inst.show() : inst.hide();
    } else {
      editor.node.classList.toggle("show", show);
    }
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

  // ----------------------------------------------------------- connections

  function renderConnections() {
    const tbody = $("#pm-connections tbody");
    tbody.replaceChildren();
    if (!state.hub) return;
    const rows = [];
    for (const p of [...state.hub.ports].sort((x, y) => x.col - y.col || x.row - y.row)) {
      const cab = cableOf(p.id);
      if (!cab) continue;
      rows.push([p, cab]);
    }
    if (!rows.length) {
      tbody.append(el("tr", {}, el("td", { colspan: 3, class: "text-muted" }, "No cables on the hub yet")));
      return;
    }
    for (const [p, cab] of rows) {
      const key = cab.kind === "pending" ? `p${cab.ref}` : `c${cab.id}`;
      const flag =
        cab.kind === "pending" ? el("span", { class: "badge bg-blue-lt ms-1" }, "new")
          : cab.deleted ? el("span", { class: "badge bg-red-lt ms-1" }, "delete")
            : cab.edited ? el("span", { class: "badge bg-yellow-lt ms-1" }, "edited") : null;
      const peerCell = el("td", {}, el("b", {}, cab.peerDevice), " ", cab.peerName);
      if (cab.peerDeviceId != null && !state.cards.has(cab.peerDeviceId)) {
        peerCell.append(" ", el("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); e.stopPropagation(); addSpoke(cab.peerDeviceId, true); } }, "+ bench"));
      }
      tbody.append(
        el("tr", {
          dataset: { key },
          class: cab.deleted ? "text-muted text-decoration-line-through" : "",
          onclick: () => openEditor(cab, p.id),
          onmouseenter: () => highlight(p.id, true),
          onmouseleave: () => highlight(p.id, false),
        },
          el("td", {}, p.name),
          peerCell,
          el("td", {}, [labelOf(CHOICES.cable_types, cab.type), fmtLen(cab.length, cab.length_unit), cab.label].filter(Boolean).join(" · ") || "—", flag),
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
    btn.disabled = true;
    try {
      const res = await api("commit/", payload);
      toast(`Saved: ${res.created.length} created, ${res.updated} updated, ${res.deleted} deleted`, "success");
      state.pending = { create: [], update: new Map(), delete: new Set() };
      await reloadAll();
    } catch (e) {
      const errors = (e.body && e.body.errors) || [{ error: e.message }];
      showErrors(errors);
      btn.disabled = false;
    }
  }

  function showErrors(errors) {
    const old = $("#pm-errors");
    if (old) old.remove();
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
    return `${e.op ? e.op + " " : ""}${e.id ? "#" + e.id + " " : ""}${e.error}`;
  }

  function discard() {
    state.pending = { create: [], update: new Map(), delete: new Set() };
    disarm();
    rerender();
  }

  // ---------------------------------------------------------------- spokes

  async function addSpoke(deviceId, expanded) {
    if (state.cards.has(deviceId)) {
      const c = card(deviceId);
      if (!c.isHub && !c.expanded) toggleSpoke(c);
      return c;
    }
    const placeholder = { device: { id: deviceId }, ports: [], busy: true, isHub: false, expanded: false, el: el("div", { class: "card pm-spoke" }) };
    state.cards.set(deviceId, placeholder);
    try {
      const c = await loadCard(deviceId, false);
      c.expanded = expanded;
      c.el = placeholder.el;
      state.cards.set(deviceId, c);
      state.spokes.push(c);
      registerPorts(c);
      $("#pm-spokes").append(c.el);
      if (state.focus == null) state.focus = deviceId; // the first spoke starts focused
      rerender();
      return c;
    } catch (e) {
      state.cards.delete(deviceId);
      toast(`Could not load device ${deviceId}: ${e.message}`, "danger");
      return null;
    }
  }

  function removeSpoke(c) {
    if (state.pending.create.some((p) => [p.a, p.b].some((id) => state.ports.get(id)?.card === c))) {
      return toast("Discard or save its pending cables first", "warning");
    }
    unregisterPorts(c);
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
    for (const id of ids) await addSpoke(id, true);
  });

  // --------------------------------------------------------------- render

  function rerender() {
    for (const c of state.cards.values()) if (c.el && !c.busy) renderCard(c);
    refreshTiles();
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
    const spokes = [];
    for (const s of state.spokes) {
      const c = await loadCard(s.device.id, false);
      c.el = s.el;
      c.expanded = expanded.get(s.device.id);
      unregisterPorts(s);
      state.cards.set(c.device.id, c);
      registerPorts(c);
      spokes.push(c);
    }
    state.spokes = spokes;
    rerender();
  }

  async function init() {
    const hub = await loadCard(cfg.hubId, true);
    hub.el = $("#pm-hub");
    state.hub = hub;
    state.cards.set(hub.device.id, hub);
    registerPorts(hub);
    rerender();
    const { peers } = await api(`devices/${cfg.hubId}/peers/`);
    for (const [i, p] of peers.entries()) await addSpoke(p.id, i < EXPAND_DEFAULT);
    if (cfg.rackId) loadShelf(cfg.rackId);
  }

  // ---------------------------------------------------------------- wiring

  $("#pm-save")?.addEventListener("click", save);
  $("#pm-discard")?.addEventListener("click", discard);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.armed != null) disarm();
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
