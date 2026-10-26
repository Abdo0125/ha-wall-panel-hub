// Wall Panel Hub designer: the panel as it looks on the wall, plus its settings and
// permissions, the project (PINs, defaults, device types) and new-panel setup.
// Layout changes go to a draft on the hub (kept across reloads); "send" applies them.
"use strict";

// ---------- helpers ----------

// Text only: names come from HA and panels, never render them as HTML.
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(e.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) e.append(c instanceof Node ? c : String(c));
  if (tag === "input" && attrs.type === "number") numberField(e);
  return e;
}
const $ = (id) => document.getElementById(id);
const clone = (o) => JSON.parse(JSON.stringify(o));
const enc = encodeURIComponent;

async function api(path, options = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let body = null;
  try { body = await r.json(); } catch (_) { /* empty body */ }
  return { status: r.status, ok: r.ok, body };
}
const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body) });
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

let toastTimer;
function toast(text) {
  const box = $("toast");
  box.textContent = text;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 3500);
}

// ---------- icons (Material Design Icons webfont, same set as the panel) ----------

let CODEPOINTS = {};
function icon(name, attrs = {}) {
  const key = (name || "").replace(/^mdi:/, "");
  const cp = CODEPOINTS[key] ?? CODEPOINTS["help-circle-outline"];
  return el("i", { class: "mdi " + (attrs.class || ""), style: attrs.style }, cp ? String.fromCodePoint(cp) : "?");
}
const validIcon = (name) => /^mdi:[a-z0-9-]+$/.test(name || "") && CODEPOINTS[name.slice(4)] !== undefined;

// ---------- card registry (schema/cards.json, from the hub) ----------

let R = { kinds: {}, cards: {} };
const domainOf = (id) => id.split(".")[0];
const kindOf = (entityId) => Object.keys(R.kinds).find((k) => R.kinds[k].domains.includes(domainOf(entityId)));
const kindOfCard = (type) => type.split(".")[0];
const cardDomains = (type) => R.cards[type]?.entities.domains || R.kinds[kindOfCard(type)]?.domains || [];
// A size a card type takes: one of its sizes, or any WxH up to the grid for a free-size type.
const sizeOk = (spec, size) => spec.sizes.includes(size) || (!!spec.free_size && /^[1-6]x[1-4]$/.test(size || ""));
const sizeOf = (c) => c.size || R.cards[c.card]?.default_size || "1x1";
const area = (s) => s.split("x").reduce((x, y) => x * y, 1);
const smallestSize = (type) => [...(R.cards[type]?.sizes || ["1x1"])].sort((a, b) => area(a) - area(b))[0];

// Plain names: the installer never sees card ids or entity ids.
const SHAPES = {
  "light.toggle": t("Switch"), "light.dimmer": t("Dimmer"), "light.color": t("Colour"), "light.group": t("Group"),
  "switch.toggle": t("Switch"), "switch.outlet": t("Outlet with power"),
  "cover.buttons": t("Buttons"), "cover.position": t("Position"), "cover.tilt": t("Tilt"), "cover.group": t("Group"),
  "climate.compact": t("Compact"), "climate.thermostat": t("Thermostat"),
  "fan.toggle": t("Switch"), "fan.speed": t("Speeds"), "lock.lock": t("Lock"),
  "scene.action": t("Button"), "scene.bar": t("Scene bar"), "script.action": t("Button"), "button.action": t("Button"),
  "sensor.value": t("Value"), "sensor.multi": t("Several values"), "sensor.binary": t("State"), "app.launch": t("App"), "camera.view": t("Picture every few seconds"), "camera.live": t("Live"), "room.card": t("Room picture"), "room.summary": t("Room details"), "widget.clock": t("Clock"),
  "widget.weather": t("Weather"), "widget.android": t("Android widget"), "widget.temperature": t("Outdoor temperature"), "widget.power": t("Power now"),
};
// Sizes in cells, columns × rows, as the panel names them.
// Isolated left to right, so 2×1 reads 2×1 on an Arabic page too.
const SIZES = Object.fromEntries(["1x1", "2x1", "2x2", "3x2", "4x2"].map((v) => [v, "⁦" + v.replace("x", "×") + "⁩"]));
// Each kind of device pairs with its own app and has its own rules (docs/ui-plan.md, 5.3).
const PLATFORMS = { android: t("Android panel"), esp32: t("ESP32 panel"), phone: t("Phone") };
const TYPES = { android_11: t("Android 11\""), android_4: t("Android 4\""), esp32_4: 'ESP32 4"', phone: t("Phone") };
const OPTION_NAMES = {
  enlarge: t("Tap to enlarge"), min_brightness: t("Minimum brightness (%)"), show_percent: t("Show percentage"), dimmer: t("Group dimming"),
  power_entity: t("Power sensor"), step: t("Temperature step"), package: t("App (package name)"), return_seconds: t("Return after (seconds)"),
};
const KIND_NAMES = {
  light: t("Lights"), switch: t("Switches"), cover: t("Covers"), climate: t("Climate"), fan: t("Fans"), lock: t("Locks"),
  scene: t("Scenes"), script: t("Scripts"), button: t("Buttons"), sensor: t("Sensors"), camera: t("Cameras"),
};

// Device types per domain (HA's device_class names), set once for the project.
const DEVICE_TYPES = {
  cover: [["shutter", t("Shutter")], ["shade", t("Roller blind")], ["curtain", t("Curtain")], ["blind", t("Blind")],
          ["window", t("Window")], ["garage", t("Garage")], ["gate", t("Gate")], ["door", t("Door")], ["awning", t("Awning")], ["damper", t("Damper")]],
  switch: [["switch", t("Switch")], ["outlet", t("Outlet")], ["light", t("Lights")], ["heater", t("Heater")], ["boiler", t("Water heater")],
           ["fan", t("Fan")], ["pump", t("Pump")], ["valve", t("Valve")]],
  binary_sensor: [["door", t("Door")], ["window", t("Window")], ["garage_door", t("Garage door")], ["motion", t("Motion")], ["occupancy", t("Presence")],
                  ["smoke", t("Smoke")], ["moisture", t("Water leak")], ["gas", t("Gas")]],
};

function variantsFor(card) {
  return Object.keys(R.cards).filter((type) => {
    const spec = R.cards[type];
    if (kindOfCard(type) !== kindOfCard(card.card)) return false;
    if (card.entities.length < spec.entities.min || card.entities.length > spec.entities.max) return false;
    const domains = cardDomains(type);
    return domains.includes("*") || card.entities.every((e) => domains.includes(domainOf(e)));
  });
}

// Same check as the hub (cards.py); the hub checks again on send.
function cardProblem(c) {
  const spec = R.cards[c.card];
  if (!spec) return t("Unknown card type.");
  const { min, max } = spec.entities;
  if (c.entities.length < min || c.entities.length > max) return t("Takes {0} to {1} devices.", min, max);
  const domains = cardDomains(c.card);
  if (c.entities.some((e) => !domains.includes("*") && !domains.includes(domainOf(e)))) return t("A device does not fit this shape.");
  if (c.size && !sizeOk(spec, c.size)) return t("Size not allowed for this shape.");
  for (const [key, opt] of Object.entries(spec.options || {})) {
    if (opt.required && (c.options?.[key] === undefined || c.options[key] === "")) return t("{0} is required.", OPTION_NAMES[key] || key);
  }
  return null;
}

function setCardType(c, type) {
  const spec = R.cards[type];
  c.card = type;
  if (!sizeOk(spec, c.size)) c.size = spec.default_size;
  for (const key of Object.keys(c.options || {})) if (!spec.options?.[key]) delete c.options[key];
  if (c.options && !Object.keys(c.options).length) delete c.options;
}

// ---------- grid packing (same rule as the app, GridPacking.kt) ----------

const COLS = 6, ROWS = 4;  // the 11" panel: halves and thirds, no odd cell left

function spanOf(size) {
  const [w, h] = (size || "1x1").split("x").map(Number);
  return [Math.min(Math.max(w || 1, 1), COLS), Math.min(Math.max(h || 1, 1), ROWS)];
}

// Cards with a place of their own (pos, free placement on Android panels) go there first
// if they fit; the others take the first free spot that fits, in card order. A card that
// fits nowhere starts the next screen.
function pack(cards) {
  const screens = [];
  const screen = (s) => { while (screens.length <= s) screens.push(Array.from({ length: ROWS }, () => Array(COLS).fill(false))); return screens[s]; };
  const free = (s, c, r, w, h) => {
    if (c < 0 || r < 0 || c + w > COLS || r + h > ROWS) return false;
    const g = screen(s);
    for (let rr = r; rr < r + h; rr++) for (let cc = c; cc < c + w; cc++) if (g[rr][cc]) return false;
    return true;
  };
  const take = (card, s, c, r, w, h) => {
    const g = screen(s);
    for (let rr = r; rr < r + h; rr++) for (let cc = c; cc < c + w; cc++) g[rr][cc] = true;
    return { card, screen: s, col: c, row: r, w, h };
  };
  screen(0);
  const at = new Map();
  for (const card of cards) {
    const pos = card.pos;
    if (!pos) continue;
    const [w, h] = spanOf(sizeOf(card));
    if (free(pos.screen, pos.col, pos.row, w, h)) at.set(card, take(card, pos.screen, pos.col, pos.row, w, h));
  }
  let first = 0;
  for (const card of cards) {
    if (at.has(card)) continue;
    const [w, h] = spanOf(sizeOf(card));
    let placed = null;
    for (let s = first; !placed; s++) {
      search: for (let r = 0; r <= ROWS - h; r++) for (let c = 0; c <= COLS - w; c++) {
        if (free(s, c, r, w, h)) { placed = take(card, s, c, r, w, h); break search; }
      }
    }
    at.set(card, placed);
    first = placed.screen;
  }
  return { placed: cards.map((c) => at.get(c)), screens: screens.length, grids: screens };
}

// Where a card dropped on an empty cell lands, as the panel does it (GridPacking.landing):
// starting there if it fits; else shifted, still covering that cell, as near as can be to
// where it is now (a wide card dragged one cell aside slides over). Its own cells are free.
function landing(placed, moving, screen, col, row) {
  const fits = (c, r) => c >= 0 && r >= 0 && c + moving.w <= COLS && r + moving.h <= ROWS && !placed.some((p) =>
    p !== moving && p.screen === screen && p.col < c + moving.w && c < p.col + p.w && p.row < r + moving.h && r < p.row + p.h);
  let best = null, bestScore = Infinity;
  for (let dy = 0; dy < moving.h; dy++) for (let dx = 0; dx < moving.w; dx++) {
    const c = col - dx, r = row - dy;
    if (!fits(c, r)) continue;
    const score = c === col && r === row ? -1
      : moving.screen === screen ? Math.abs(c - moving.col) + Math.abs(r - moving.row) : c + r;
    if (score < bestScore) { best = { screen, col: c, row: r }; bestScore = score; }
  }
  return best;
}

// Free placement, as the panel does it (GridPacking.place): every card keeps the place it
// shows now; onto another card the two swap, onto empty cells the card goes where landing()
// says. Then the cards are listed in reading order. False when it cannot go there.
function placeCard(cards, index, screen, col, row) {
  const { placed } = pack(cards);
  const moving = placed[index];
  const hit = placed.find((p) => p.screen === screen && col >= p.col && col < p.col + p.w && row >= p.row && row < p.row + p.h);
  if (hit === moving) return false;
  const target = hit ? { screen: hit.screen, col: hit.col, row: hit.row } : landing(placed, moving, screen, col, row);
  if (!target) return false;
  placed.forEach((p) => { p.card.pos = { screen: p.screen, col: p.col, row: p.row }; });
  if (hit) hit.card.pos = { screen: moving.screen, col: moving.col, row: moving.row };
  moving.card.pos = target;
  cards.sort((a, b) => (a.pos.screen - b.pos.screen) || (a.pos.row - b.pos.row) || (a.pos.col - b.pos.col));
  return true;
}

// ---------- state ----------

const S = {
  overview: { panels: [], pending: [], discovered: [] },
  site: null,
  catalog: { floors: [], areas: [], entities: [] },
  catalogError: null,
  editor: {},          // pid -> { sent, draft, area_id, online }
  work: {},            // pid -> { layout, base, unsent, undo, redo }
  configs: {},         // pid -> { config, effective }
  view: { kind: "home" },  // home | panel {pid, tab} | project {tab}
  page: -1,            // my dashboard (DASH), as the panel opens
  menu: false,         // the rooms menu open (over the page), as on the panel
  floorTab: "",        // its floor tab, by floor id ("" for the rooms on no floor)
  sel: null,           // selected card index on the page
};
const W = () => S.work[S.view.pid];
const L = () => W()?.layout;
const entities = () => Object.fromEntries(S.catalog.entities.map((e) => [e.entity_id, e]));
const devices = () => S.site?.devices || {};
const sources = () => S.site?.sources || {};

// An Android widget's name: as allowed on the open panel, else as the panel reported it.
function androidWidgetName(provider) {
  const cfg = S.configs[S.view.pid];
  const w = (cfg?.installed_widgets || []).find((x) => x.provider === provider);
  return w?.label || (provider || "").split("/")[0];
}

// The panel's own cards; all but the clock read one of the project's sources.
const WIDGETS = [
  { card: "widget.clock", icon: "mdi:clock-outline", size: "2x2" },
  { card: "widget.weather", icon: "mdi:weather-partly-cloudy", size: "2x2", source: "weather", domain: "weather" },
  { card: "widget.temperature", icon: "mdi:thermometer", size: "1x1", source: "outdoor_temperature", domain: "sensor", deviceClass: "temperature", units: ["°C", "°F"] },
  { card: "widget.power", icon: "mdi:flash", size: "1x1", source: "power", domain: "sensor", deviceClass: "power", units: ["W", "kW"] },
];

// A device as the panels show it: the project's name, type and icon over HA's.
function device(id) {
  const e = entities()[id];
  const d = devices()[id] || {};
  return {
    id,
    name: d.name || e?.name || id,
    type: d.type || e?.device_class || null,
    icon: d.icon || (e?.icon && e.icon.startsWith("mdi:") ? e.icon : null),
    area: e?.area_id || null,
    known: !!e,
  };
}

// The icon a card shows (off state), close to the app's cardIcon.
function cardIcon(c) {
  if (c.icon) return c.icon;
  const kind = kindOfCard(c.card);
  if (kind === "room") return L()?.pages.find((p) => p.id === c.options?.page)?.icon || "mdi:floor-plan";
  if (kind === "app") return "mdi:apps";
  if (c.card === "widget.android") return "mdi:widgets-outline";
  const widget = WIDGETS.find((w) => w.card === c.card);
  if (widget) return widget.icon;
  const d = c.entities[0] ? device(c.entities[0]) : null;
  if (d?.icon && c.entities.length === 1) return d.icon;
  const type = d?.type;
  const m = {
    light: c.card === "light.group" ? "lightbulb-group-outline" : "lightbulb-outline",
    switch: { outlet: "power-socket-eu", heater: "radiator", boiler: "water-boiler", pump: "pump", fan: "fan", light: "lightbulb-outline", valve: "valve" }[type]
      || (c.card === "switch.outlet" ? "power-socket-eu" : domainOf(c.entities[0] || "") === "input_boolean" ? "toggle-switch-off-outline" : "toggle-switch-variant-off"),
    cover: { blind: "blinds", curtain: "curtains-closed", garage: "garage", gate: "gate", door: "door-closed", window: "window-closed",
             awning: "awning-outline", shade: "roller-shade-closed", damper: "valve-closed" }[type] || "window-shutter",
    climate: "thermostat", fan: "fan", lock: "lock", scene: "palette-outline", script: "script-text-outline",
    button: "gesture-tap-button", camera: "cctv",
    sensor: domainOf(c.entities[0] || "") === "binary_sensor"
      ? ({ door: "door-closed", garage_door: "garage", window: "window-closed-variant", motion: "motion-sensor", smoke: "smoke-detector-variant", moisture: "water-off", gas: "gas-cylinder" }[type] || "checkbox-blank-circle-outline")
      : ({ temperature: "thermometer", humidity: "water-percent", power: "flash", energy: "lightning-bolt", illuminance: "brightness-5", battery: "battery" }[type] || "gauge"),
  }[kind];
  return "mdi:" + (m || "help-circle-outline");
}

function cardLabel(c) {
  if (c.label) return c.label;
  if (c.card === "widget.android") return androidWidgetName(c.options?.provider);
  if (c.card === "app.launch") return appName(c.options?.package);
  if (kindOfCard(c.card) === "room") return L()?.pages.find((p) => p.id === c.options?.page)?.title || t("Deleted room");
  if (kindOfCard(c.card) === "widget") return SHAPES[c.card] || c.card;
  if (c.entities.length > 1) return `${c.entities.length} × ${KIND_NAMES[kindOfCard(c.card)] || ""}`;
  if (c.entities[0]) return device(c.entities[0]).name;
  return SHAPES[c.card] || c.card;
}

// ---------- loading ----------

async function loadOverview() {
  const r = await api("api/overview");
  if (!r.ok) return;
  S.overview = r.body;
  $("version").textContent = "v" + r.body.version;
  $("pinWarning").hidden = r.body.installer_pin_set;
}

async function loadEditor() {
  const r = await api("api/editor/state");
  if (!r.ok) return;
  for (const p of r.body.panels) {
    S.editor[p.id] = p;
    if (S.work[p.id]?.unsent) continue;  // keep edits in progress
    if (p.draft) S.work[p.id] = { layout: p.draft.layout, base: p.draft.base_version, unsent: true, undo: [], redo: [] };
    else S.work[p.id] = { layout: p.sent ? clone(p.sent) : { schema: 2, version: 1, pages: [] }, base: p.sent ? p.sent.version : null, unsent: false, undo: [], redo: [] };
  }
}

async function loadCatalog(refresh) {
  const r = await api("api/editor/catalog" + (refresh ? "?refresh=1" : ""));
  if (r.ok) { S.catalog = r.body; S.catalogError = null; }
  else S.catalogError = r.body?.error || `HTTP ${r.status}`;
}

async function loadSite() {
  const r = await api("api/site");
  if (r.ok) S.site = r.body;
}

async function loadConfig(pid) {
  const r = await api(`api/panels/${enc(pid)}/config`);
  if (r.ok) S.configs[pid] = r.body;
}

// ---------- changes, undo, draft ----------

const saveTimers = {};
function change(fn) {
  const w = W();
  if (!w) return;
  w.undo.push(JSON.stringify(w.layout));
  if (w.undo.length > 100) w.undo.shift();
  w.redo = [];
  fn(w.layout);
  w.unsent = true;
  scheduleSave(S.view.pid);
  render();
}
function scheduleSave(pid) {
  clearTimeout(saveTimers[pid]);
  saveTimers[pid] = setTimeout(() => saveDraft(pid), 600);
}
async function saveDraft(pid) {
  const w = S.work[pid];
  await put(`api/editor/panels/${enc(pid)}/draft`, { layout: w.layout, base_version: w.base });
}
function undo() {
  const w = W();
  if (!w?.undo.length) return;
  w.redo.push(JSON.stringify(w.layout));
  w.layout = JSON.parse(w.undo.pop());
  w.unsent = true; scheduleSave(S.view.pid); render();
}
function redo() {
  const w = W();
  if (!w?.redo.length) return;
  w.undo.push(JSON.stringify(w.layout));
  w.layout = JSON.parse(w.redo.pop());
  w.unsent = true; scheduleSave(S.view.pid); render();
}

function problems(layout) {
  const out = [];
  if (!layout.pages.length) out.push(t("No rooms."));
  const pages = new Set(layout.pages.map((p) => p.id));
  const roomGone = (c) => kindOfCard(c.card) === "room" && !pages.has(c.options?.page) ? t("Its room is no longer on the panel. Delete the card.") : null;
  for (const page of layout.pages) {
    if (!page.title?.trim()) out.push(t("A room has no name."));
    for (const c of page.cards) {
      const p = cardProblem(c) || roomGone(c);
      if (p) out.push(`${page.title} ← ${cardLabel(c)}: ${p}`);
    }
  }
  for (const c of layout.dashboard?.cards || []) {
    const p = roomGone(c);
    if (p) out.push(t("My dashboard → {0}: {1}", cardLabel(c), p));
  }
  return out;
}

async function send(force) {
  const pid = S.view.pid;
  const issues = problems(L());
  if (issues.length) { alert(t("Cannot send:\n") + issues.join("\n")); return; }
  clearTimeout(saveTimers[pid]);
  await saveDraft(pid);
  const r = await post(`api/editor/panels/${enc(pid)}/send`, { force: !!force });
  if (r.status === 409) {
    if (confirm(t("This panel's cards were changed on the panel itself after you started.\nSend your changes over them? (Cancel = discard your changes)"))) return send(true);
    return discard(true);
  }
  if (!r.ok) { alert(t("The hub refused the layout: ") + (r.body?.error || r.status)); return; }
  S.work[pid].unsent = false;
  await loadEditor();
  toast(r.body.online ? t("Sent and applied on the panel.") : t("Sent. The panel is offline and will get it when it connects."));
  render();
}

async function discard(noAsk) {
  const pid = S.view.pid;
  if (!noAsk && !confirm(t("Discard all this panel's unsent changes?"))) return;
  clearTimeout(saveTimers[pid]);
  await api(`api/editor/panels/${enc(pid)}/draft`, { method: "DELETE" });
  delete S.work[pid];
  await loadEditor();
  S.page = DASH; S.sel = null;
  render();
}

// ---------- building pages and cards ----------

function nextCardId(layout, extra = []) {
  const taken = new Set([...layout.pages.flatMap((p) => p.cards.map((c) => c.id)), ...(layout.dashboard?.cards || []).map((c) => c.id), ...extra.map((c) => c.id)]);
  let n = 1;
  while (taken.has(`c${n}`)) n++;
  return `c${n}`;
}

function newCard(layout, entityId, extra) {
  const type = R.kinds[kindOf(entityId)].default;
  return { id: nextCardId(layout, extra), card: type, entities: [entityId], size: R.cards[type].default_size };
}

function pageId(wanted, taken) {
  const base = (wanted || "").toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "page";
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}

const controlsIn = (areaId) => S.catalog.entities.filter((e) => e.area_id === areaId && e.controllable).map((e) => e.entity_id);

// One page per room, with a default card for each of its devices.
function addRoomPages(layout, areaIds) {
  const areas = Object.fromEntries(S.catalog.areas.map((a) => [a.id, a]));
  for (const id of areaIds) {
    const area = areas[id];
    if (!area || layout.pages.some((p) => p.id === id)) continue;
    const page = { id: pageId(id, new Set(layout.pages.map((p) => p.id))), title: area.name, cards: [] };
    if (/^mdi:[a-z0-9-]+$/.test(area.icon || "")) page.icon = area.icon;
    layout.pages.push(page);
    for (const eid of controlsIn(id)) if (kindOf(eid)) page.cards.push(newCard(layout, eid, page.cards));
  }
}

// ---------- rendering: navigation ----------

function render() {
  renderNav();
  renderView();
}

function renderNav() {
  const nav = $("nav");
  nav.replaceChildren();
  const o = S.overview;
  if (o.pending.length) {
    nav.append(el("h2", {}, t("Panels asking to pair")));
    for (const r of o.pending) {
      nav.append(el("div", { class: "request" },
        el("div", { class: "row" }, el("span", { class: "code" }, r.code), el("span", { class: "muted" }, r.name)),
        el("div", { class: "row", style: { marginTop: "6px" } },
          el("button", { class: "primary", onclick: () => wizard(r) }, t("Set up")),
          el("button", { onclick: async () => { await post(`api/pending/${enc(r.request_id)}/reject`); await refresh(); } }, t("Reject"))),
      ));
    }
  }
  nav.append(el("h2", {}, t("Panels")));
  if (!o.panels.length) nav.append(el("p", { class: "muted", style: { margin: "0 6px" } }, t("No panels. Start a new panel on the same network and it shows here, asking to pair.")));
  for (const p of o.panels) {
    const active = S.view.kind === "panel" && S.view.pid === p.id;
    nav.append(el("div", { class: "navitem" + (active ? " active" : ""), onclick: () => openPanel(p.id) },
      el("span", { class: "dot " + (p.online ? "on" : "off") }, p.online ? "●" : "○"),
      el("span", { class: "grow" }, p.name),
      S.work[p.id]?.unsent ? el("span", { title: t("Unsent changes"), style: { color: "var(--accent)" } }, "✱") : null,
    ));
  }
  if (o.discovered.length) {
    nav.append(el("h2", {}, t("Other panels on the network")));
    for (const d of o.discovered) nav.append(el("div", { class: "navitem", title: d.address }, el("span", { class: "dot off" }, "○"), el("span", { class: "grow muted" }, d.name)));
  }
  nav.append(el("h2", {}, t("Project")));
  nav.append(el("div", { class: "navitem" + (S.view.kind === "project" ? " active" : ""), onclick: () => openProject("general") },
    icon("mdi:home-city-outline", { style: { fontSize: "18px" } }), el("span", { class: "grow" }, S.site?.name || t("Project"))));
}

async function openPanel(pid, tab = "design") {
  S.view = { kind: "panel", pid, tab };
  // The panel's home page first, as on the panel.
  S.page = DASH; S.sel = null; S.menu = false; S.floorTab = "";
  if (!S.configs[pid]) await loadConfig(pid);
  render();
}

function openProject(tab) {
  S.view = { kind: "project", tab };
  render();
}

function renderView() {
  const view = $("view");
  // Where each scrolled part was, by name: drawn again, it stays there.
  const kept = Object.fromEntries([...view.querySelectorAll("[data-scroll]")].map((n) => [n.dataset.scroll, n.scrollTop]));
  const pageY = window.scrollY;
  view.replaceChildren();
  queueMicrotask(() => {
    for (const n of view.querySelectorAll("[data-scroll]")) if (kept[n.dataset.scroll] !== undefined) n.scrollTop = kept[n.dataset.scroll];
    window.scrollTo(0, pageY);
  });
  if (S.view.kind === "panel" && S.overview.panels.some((p) => p.id === S.view.pid)) return renderPanel(view);
  if (S.view.kind === "project") return renderProject(view);
  view.append(el("div", { class: "box" },
    el("h3", {}, t("Welcome")),
    el("p", { class: "muted" }, t("Pick a panel from the list to design it, set up a new panel asking to pair, or open the project for PINs and device types.")),
    S.catalogError ? el("p", { class: "msg error" }, t("Could not read Home Assistant's devices: ") + S.catalogError) : null,
  ));
}

// ---------- rendering: one panel ----------

function renderPanel(view) {
  const pid = S.view.pid;
  const p = S.overview.panels.find((x) => x.id === pid);
  const w = W();
  const tabs = [["design", t("Design")], ["settings", t("Settings")], ["access", t("Permissions")]];
  view.append(el("div", { class: "titlebar" },
    el("h2", {}, p.name),
    el("span", { class: "dot " + (p.online ? "on" : "off") }, p.online ? t("● Online") : t("○ Offline")),
    el("div", { class: "tabs" }, tabs.map(([id, label]) =>
      el("button", { class: S.view.tab === id ? "active" : "", onclick: () => { S.view.tab = id; render(); } }, label))),
    el("span", { class: "spacer" }),
    S.view.tab === "design" ? [
      el("button", { disabled: !w?.undo.length, onclick: undo, title: "Ctrl+Z" }, "↶"),
      el("button", { disabled: !w?.redo.length, onclick: redo, title: "Ctrl+Y" }, "↷"),
      el("button", { class: "danger", disabled: !w?.unsent, onclick: () => discard() }, t("Discard")),
      el("button", { class: w?.unsent ? "solid" : "primary", onclick: () => send(false) }, w?.unsent ? t("Send to panel ✱") : t("Send to panel")),
    ] : null,
  ));
  if (S.view.tab === "design") renderDesign(view);
  else if (S.view.tab === "settings") renderSettings(view, p);
  else renderAccess(view, p);
}

// ---------- my dashboard (S.page === DASH) ----------

const DASH = -1;

// Until it is customized, as the panel shows it: the clock, then the rooms' scenes (copies).
function defaultDashboard(l) {
  const seen = new Set();
  const scenes = l.pages.flatMap((p) => p.cards)
    .filter((c) => kindOfCard(c.card) === "scene" && !seen.has(c.entities.join()) && seen.add(c.entities.join()))
    .map((c) => { const copy = JSON.parse(JSON.stringify(c)); copy.id = "dash_" + c.id; delete copy.pos; return copy; });
  return [{ id: "dash_clock", card: "widget.clock", entities: [], size: "2x2" }, ...scenes];
}

// The open room, or my dashboard as a page.
function curPage(l) {
  if (S.page !== DASH) return l.pages[S.page];
  return { id: "__dashboard", title: t("My dashboard"), icon: "mdi:view-dashboard-outline", cards: l.dashboard ? l.dashboard.cards : defaultDashboard(l) };
}

// The cards a change edits: my dashboard is saved the first time it is changed.
function pageCards(l) {
  if (S.page !== DASH) return l.pages[S.page].cards;
  if (!l.dashboard) l.dashboard = { cards: defaultDashboard(l) };
  return l.dashboard.cards;
}

function ensureDashboard() {
  if (S.page === DASH && !L().dashboard) change((l) => { l.dashboard = { cards: defaultDashboard(l) }; });
}

function renderDesign(view) {
  const layout = L();
  if (!layout) return;
  if (S.catalogError) view.append(el("p", { class: "msg error" }, t("Could not read Home Assistant's devices: ") + S.catalogError));
  const eff = S.configs[S.view.pid]?.effective || {};
  const rtl = eff.lang === "ar";
  if (S.page !== DASH) S.page = Math.min(S.page, Math.max(0, layout.pages.length - 1));
  // No room yet: the rooms menu, where rooms are added.
  if (!layout.pages.length && S.page !== DASH) S.menu = true;
  const page = curPage(layout);

  // Top bar, as on the panel: the page's name with ▾ opens the rooms menu; ⌂ my dashboard.
  const now = new Date();
  const title = S.menu ? t("Rooms") : page?.title || "";
  const top = el("div", { class: "dtop" },
    el("span", { class: "dtitle", title: t("Rooms menu"), onclick: () => { S.menu = !S.menu; S.sel = null; render(); } },
      S.menu ? null : icon(page?.icon || "mdi:home-outline", { style: { fontSize: "20px" } }),
      el("span", {}, title), icon(S.menu ? "mdi:chevron-up" : "mdi:chevron-down", { style: { fontSize: "20px" } })),
    el("span", { class: "clock" }, `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`),
    S.page !== DASH || S.menu
      ? el("span", { class: "dhome", title: t("My dashboard"), onclick: () => { S.page = DASH; S.menu = false; S.sel = null; render(); } }, icon("mdi:home-outline"))
      : null);

  const main = el("div", { class: "dmain" }, top);
  if (S.menu) {
    renderRoomsMenu(main);
  } else {
    const screensBox = el("div", { class: "screens", "data-scroll": `screens:${S.view.pid}:${S.page}` });
    const { placed, screens, grids } = pack(page.cards);
    for (let s = 0; s < screens; s++) {
      const grid = el("div", { class: "screen" });
      grid.style.height = "100%";
      if (screens > 1) grid.append(el("span", { class: "screenlabel" }, t("Screen {0} of {1} (swipe)", s + 1, screens)));
      for (const pl of placed.filter((x) => x.screen === s)) grid.append(cardCell(pl, page));
      for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) {
        if (grids[s][r][c]) continue;
        grid.append(emptyCell(s, c, r, true));
      }
      screensBox.append(grid);
    }
    // Room for one more screen after the last, as on the panel in edit mode.
    const grid = el("div", { class: "screen" });
    grid.append(el("span", { class: "screenlabel" }, t("Screen {0} (new, kept once a card is added)", screens + 1)));
    for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) grid.append(emptyCell(screens, c, r, true));
    screensBox.append(grid);
    main.append(screensBox);
  }
  const device = el("div", { class: "device" + (eff.theme === "dark" ? " dark" : ""), dir: rtl ? "rtl" : "ltr" }, main);
  view.append(S.menu ? device : el("div", { class: "stage" }, device, addPalette()));
  view.append(el("p", { class: "muted" }, S.menu
    ? t("Floors are tabs: the pencil for name, icon and deleting, + to add a floor. Drag a room to any cell, or onto another to swap them; + in an empty cell adds a room. A room's pencil sets its name, shape and size; tapping it opens it.")
    : t("Tap the page's name for the rooms menu. Tap + to add a device, an app or a widget, and a card to change its shape, size and name. Drag a card to any empty cell to put it there, or onto another to swap them.")));
  const issues = problems(layout).filter((x) => x !== t("No rooms."));
  for (const m of issues) view.append(el("div", { class: "msg error" }, m));
}

let dragFrom = null;

function cardCell(pl, page) {
  const c = pl.card;
  const index = page.cards.indexOf(c);
  const known = !S.catalog.entities.length || c.entities.every((id) => entities()[id]);
  const size = `${pl.w}x${pl.h}`;
  const big = pl.w >= 2 && pl.h >= 2;
  const cls = ["cell", "card", size === "2x1" ? "wide" : "", big ? "big" : "", S.sel === index ? "selected" : "", known ? "" : "missing"].join(" ");
  const body = big
    ? [el("div", { class: "ring" }, icon(cardIcon(c)), el("span", { class: "lbl" }, cardLabel(c)))]
    : size === "2x1"
      ? [icon(cardIcon(c), { style: { fontSize: "32px" } }), el("span", { class: "lbl" }, cardLabel(c)),
         ["light.dimmer", "cover.position", "cover.tilt"].includes(c.card) ? el("span", { class: "bar" }) : null]
      : [icon(cardIcon(c)), el("span", { class: "lbl" }, cardLabel(c))];
  let roomCls = "", roomBg = "";
  if (kindOfCard(c.card) === "room") {
    const room = L().pages.find((p) => p.id === c.options?.page);
    const a = room && S.catalog.areas.find((x) => x.id === room.id);
    const picture = c.card === "room.card" && a?.picture;
    body.splice(0, body.length,
      picture ? null : icon(cardIcon(c), { class: "ricon" }),
      el("div", { class: "rname" }, el("b", {}, cardLabel(c)), el("span", {}, c.card === "room.summary" && room ? t("{0} cards", room.cards.length) : "")));
    roomCls = " rtile" + (picture ? " pic" : "");
    roomBg = picture ? `url("api/editor/area_picture/${enc(room.id)}?v=${enc(a.picture)}")` : "";
  }
  if (kindOfCard(c.card) === "camera" && known && c.entities[0]) {
    // The camera's picture now, as the panel shows it; again at most once a minute.
    body.splice(0, body.length, el("div", { class: "rname" }, el("b", {}, cardLabel(c)), el("span", {}, c.card === "camera.live" ? t("Live") : "")));
    roomCls = " rtile pic";
    roomBg = `url("api/editor/camera/${enc(c.entities[0])}?m=${Math.floor(Date.now() / 60000)}"), linear-gradient(#3a3f44, #3a3f44)`;
  }
  const node = el("div", {
    class: cls + roomCls, draggable: "true", title: known ? "" : t("Device not in Home Assistant"),
    style: { gridColumn: `${pl.col + 1} / span ${pl.w}`, gridRow: `${pl.row + 1} / span ${pl.h}`, backgroundImage: roomBg },
    onclick: () => { S.sel = index; render(); cardSheet(index); },
    ondragstart: (ev) => { dragFrom = index; ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/plain", c.id); node.classList.add("dragging"); },
    ondragend: () => { dragFrom = null; node.classList.remove("dragging"); },
    ondragover: (ev) => { if (dragFrom !== null && dragFrom !== index) { ev.preventDefault(); node.classList.add("over"); } },
    ondragleave: () => node.classList.remove("over"),
    ondrop: (ev) => {
      ev.preventDefault();
      const from = dragFrom;
      if (from === null || from === index) return;
      // Dropped on a card: the two swap places.
      change((l) => {
        const cards = pageCards(l);
        const moved = cards[from];
        if (placeCard(cards, from, pl.screen, pl.col, pl.row)) S.sel = cards.indexOf(moved);
      });
    },
  }, el("span", { class: "kind" }, SHAPES[c.card] || ""), ...body);
  return node;
}

// Any empty cell takes a new card, or a dragged one (free placement).
function emptyCell(screen, col, row, open) {
  const node = el("div", {
    class: "cell" + (open ? " empty" : ""), title: open ? t("Add a card") : "",
    style: { gridColumn: `${col + 1}`, gridRow: `${row + 1}` },
    onclick: () => { if (open) addSheet(screen, col, row); },
    ondragover: (ev) => { if ((dragFrom !== null || paletteMake) && open) { ev.preventDefault(); node.classList.add("over"); } },
    ondragleave: () => node.classList.remove("over"),
    ondrop: (ev) => {
      ev.preventDefault();
      // A new card from the palette: in this cell, settings left for later.
      if (paletteMake) {
        const make = paletteMake;
        paletteMake = null;
        addCards(make, screen, col, row);
        return;
      }
      const from = dragFrom;
      if (from === null) return;
      change((l) => {
        const cards = pageCards(l);
        const moved = cards[from];
        if (placeCard(cards, from, screen, col, row)) S.sel = cards.indexOf(moved);
      });
    },
  }, open ? "+" : "");
  return node;
}

// ---------- sheets ----------

function sheet(...content) {
  const veil = $("veil");
  veil.replaceChildren(el("div", { class: "sheet", onclick: (ev) => ev.stopPropagation() }, ...content));
  veil.hidden = false;
  veil.onclick = closeSheet;
}
function closeSheet() { $("veil").hidden = true; $("veil").replaceChildren(); }
function field(label, input) { return el("div", { class: "field" }, el("label", {}, label), input); }
function chips(options, value, onPick) {
  return el("div", { class: "chips" }, options.map(([v, label, disabled]) =>
    el("span", { class: "chip" + (v === value ? " on" : "") + (disabled ? " off" : ""), onclick: () => { if (!disabled) onPick(v); } }, label)));
}
function iconInput(value, placeholder, onCommit) {
  const preview = icon(value || placeholder, { class: "icon-preview" });
  const input = el("input", { value: value || "", placeholder, dir: "ltr",
    oninput: (e) => { const v = e.target.value.trim(); preview.replaceWith(Object.assign(icon(validIcon(v) ? v : placeholder, { class: "icon-preview" }))); },
    onchange: (e) => onCommit(e.target.value.trim()) });
  return el("div", { class: "row", style: { flexWrap: "nowrap" } }, input, preview);
}

// A free size: width and height in cells, each up to the grid's.
function freeSizeFields(size, set) {
  const [w, h] = spanOf(size);
  const n = (count) => Array.from({ length: count }, (_, i) => [String(i + 1), String(i + 1)]);
  return el("div", {},
    field(t("Width"), chips(n(COLS), String(w), (v) => set(`${v}x${h}`))),
    field(t("Height"), chips(n(ROWS), String(h), (v) => set(`${w}x${v}`))));
}

// A card: shape, size, name, icon, confirmation, options, remove.
function cardSheet(index) {
  ensureDashboard();
  const page = curPage(L());
  const c = page.cards[index];
  if (!c) return;
  const spec = R.cards[c.card] || { sizes: [], options: {} };
  const d = c.entities[0] ? device(c.entities[0]) : null;
  const redraw = () => cardSheet(page.cards.indexOf(c));
  const edit = (fn) => { change(() => fn()); redraw(); };
  const variants = variantsFor(c);
  const optionFields = Object.entries(spec.options || {}).map(([key, opt]) => {
    const value = c.options?.[key];
    const set = (v) => edit(() => {
      if (v === undefined) { if (c.options) delete c.options[key]; if (c.options && !Object.keys(c.options).length) delete c.options; }
      else c.options = { ...(c.options || {}), [key]: v };
    });
    const label = OPTION_NAMES[key] || key;
    if (opt.type === "bool") return field(label, chips([["", t("Default")], ["true", t("Yes")], ["false", t("No")]], value === undefined ? "" : String(value), (v) => set(v === "" ? undefined : v === "true")));
    if (opt.type === "enum") return field(label, chips([["", t("Default")], ...opt.values.map((v) => [v, v])], value ?? "", (v) => set(v || undefined)));
    if (opt.type === "int") return field(label, el("input", { type: "number", min: opt.min, max: opt.max, value: value ?? "", placeholder: opt.default ?? "",
      onchange: (e) => set(e.target.value === "" ? undefined : Number(e.target.value)) }));
    if (opt.type === "entity") {
      const s = el("select", { onchange: (e) => set(e.target.value || undefined) },
        el("option", { value: "" }, t("— None —")),
        S.catalog.entities.filter((e) => opt.domains.includes(domainOf(e.entity_id))).map((e) =>
          el("option", { value: e.entity_id, selected: e.entity_id === value }, device(e.entity_id).name)));
      return field(label, s);
    }
    return field(label, el("input", { value: value ?? "", dir: "ltr", onchange: (e) => set(e.target.value.trim() || undefined) }));
  });
  sheet(
    el("h3", {}, cardLabel(c)),
    el("p", { class: "muted" }, d ? `${KIND_NAMES[kindOfCard(c.card)] || ""} · ${S.catalog.areas.find((a) => a.id === d.area)?.name || t("No room")}` : SHAPES[c.card]),
    // Widgets are each their own thing: no shape to switch to.
    variants.length > 1 && kindOfCard(c.card) !== "widget" ? field(t("Shape"), chips(variants.map((v) => [v, SHAPES[v] || v]), c.card, (v) => edit(() => setCardType(c, v)))) : null,
    spec.free_size ? freeSizeFields(sizeOf(c), (v) => edit(() => { c.size = v; }))
      : field(t("Size"), chips(R.sizes.map((v) => [v, SIZES[v], !spec.sizes.includes(v)]), sizeOf(c), (v) => edit(() => { c.size = v; }))),
    field(t("Name on the panel"), el("input", { value: c.label || "", placeholder: d?.name || "",
      onchange: (e) => edit(() => { const v = e.target.value.trim(); v ? c.label = v : delete c.label; }) })),
    field(t("Icon"), iconInput(c.icon, cardIcon({ ...c, icon: undefined }), (v) => edit(() => { v && validIcon(v) ? c.icon = v : delete c.icon; }))),
    // A camera does nothing to confirm.
    kindOfCard(c.card) === "camera" ? null : field(t("Ask before acting"), chips([["", t("Automatic")], ["true", t("Yes")], ["false", t("No")]], c.confirm === undefined ? "" : String(c.confirm),
      (v) => edit(() => { v === "" ? delete c.confirm : c.confirm = v === "true"; }))),
    ...optionFields,
    // A device's card to another room, as on the panel: in its first free cell there.
    S.page !== DASH && !["app", "widget", "room"].includes(kindOfCard(c.card)) && L().pages.length > 1 ? field(t("Move to another room"), (() => {
      const sel = selectEl([["", t("— Choose —")], ...L().pages.map((p, i) => [String(i), p.title]).filter(([i]) => Number(i) !== S.page)], "");
      sel.onchange = () => {
        if (!sel.value) return;
        const to = Number(sel.value);
        change((l) => { const cs = pageCards(l); cs.splice(cs.indexOf(c), 1); delete c.pos; l.pages[to].cards.push(c); S.sel = null; });
        closeSheet();
      };
      return sel;
    })()) : null,
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: () => { closeSheet(); S.sel = null; render(); } }, t("Done")),
      el("span", { class: "spacer" }),
      el("button", { class: "danger", onclick: () => { change((l) => { const cs = pageCards(l); cs.splice(cs.indexOf(c), 1); S.sel = null; }); closeSheet(); } }, t("Delete card"))),
  );
}

// ---------- adding a card: one window, a list per kind (rooms, devices, apps, widgets) ----------

const ADD_TABS = [
  ["rooms", t("Rooms"), "mdi:floor-plan"],
  ["devices", t("Devices"), "mdi:lightbulb-group-outline"],
  ["apps", t("Apps"), "mdi:apps"],
  ["widgets", t("Widgets"), "mdi:widgets-outline"],
];
ADD_TABS.splice(2, 0, ["roomcards", t("Room cards"), "mdi:image-multiple-outline"]);
// The window remembers where it was between openings.
const addState = { tab: "rooms", room: null, kind: "", area: "", control: true, wkind: "" };

// An allowed app's name: as set in the add-on, else as the panel reported it.
function appName(pkg) {
  const cfg = S.configs[S.view.pid];
  return (cfg?.effective.apps || []).find((a) => a.package === pkg)?.label
    || (cfg?.installed_apps || []).find((a) => a.package === pkg)?.label || pkg;
}

// New cards made by [make] into the cell (screen, col, row), the next ones in the first free
// spots after; without a cell, in the first free spots. True when it made one card.
function addCards(make, screen, col, row) {
  let single = false;
  change((l) => {
    const cards = pageCards(l);
    let last = null;
    const made = [].concat(make(l, cards));
    single = made.length === 1;
    for (const m of made) {
      // Added by hand: its smallest size, so it stays in the cell chosen.
      m.size = smallestSize(m.card);
      cards.push(m);
      if (screen !== null) placeCard(cards, cards.length - 1, screen, col, row);
      last = m;
    }
    S.sel = last ? cards.indexOf(last) : null;
  });
  return single;
}

// What is being dragged from the side palette: how to make its card(s), else null.
let paletteMake = null;

// + in a cell: the add lists in a window; a pick goes in that cell, then its settings.
function addSheet(screen, col, row) {
  const browser = addBrowser({
    pick: (make) => {
      const single = addCards(make, screen, col, row);
      closeSheet();
      // One card: its settings next, as on the panel.
      if (single && S.sel !== null) cardSheet(S.sel);
    },
  });
  sheet(el("div", { class: "addwin" },
    el("div", { class: "addtop" }, el("h3", {}, t("Add a card")), browser.search),
    browser.main,
    el("div", { class: "actions" }, el("span", { class: "spacer" }), el("button", { onclick: closeSheet }, t("Close")))));
  $("veil").firstChild.classList.add("wide");
  browser.search.focus();
}

// Beside the panel: the same lists, each row dragged onto an empty cell (or tapped: the
// first free one). Speeds up setting up a page; settings are changed after, by a tap.
function addPalette() {
  const browser = addBrowser({
    docked: true,
    pick: (make) => addCards(make, null),
  });
  return el("aside", { class: "palette addwin" },
    el("div", { class: "addtop" }, el("h3", {}, t("Add")), browser.search),
    browser.main,
    el("p", { class: "muted small" }, t("Drag onto an empty cell, or tap to add in the first free one.")));
}

// The add lists (rooms, devices, room cards, apps, widgets) with their search and filters.
function addBrowser({ pick, docked = false }) {
  const page = curPage(L());
  const cfg = S.configs[S.view.pid];
  if (!cfg) loadConfig(S.view.pid).then(() => draw());
  const areaName = Object.fromEntries(S.catalog.areas.map((a) => [a.id, a.name]));
  if (addState.room === null || !areaName[addState.room]) addState.room = areaName[page.id] ? page.id : (S.catalog.areas[0]?.id || "");
  // Docked, the search outlives the page being drawn again after each change.
  const search = el("input", { placeholder: "", value: docked ? addState.q || "" : "", oninput: () => { if (docked) addState.q = search.value; fillList(); } });
  const nav = el("ul", { class: "addnav" });
  const filters = el("div", { class: "addfilters" });
  const body = el("div", { class: "addbody", "data-scroll": docked ? "palette" : null });

  const add = (make) => pick(make);
  // A row that adds a card: tapped, or dragged from the palette onto a cell.
  const drag = (make) => docked ? {
    draggable: "true",
    ondragstart: (ev) => { paletteMake = make; ev.dataTransfer.effectAllowed = "copy"; ev.dataTransfer.setData("text/plain", "new"); },
    ondragend: () => { paletteMake = null; },
  } : {};
  const entityCard = (e) => (l, cards) => newCard(l, e.entity_id, cards);
  const onPage = () => new Set(page.cards.flatMap((c) => c.entities));
  const q = () => search.value.trim().toLowerCase();
  const hits = (...texts) => !q() || texts.some((t) => (t || "").toLowerCase().includes(q()));

  const row_ = (iconName, name, note, make, off) => el("li", { class: off ? "off" : "", onclick: off || !make ? null : () => add(make), ...(off || !make ? {} : drag(make)) },
    icon(iconName), el("span", { class: "grow" }, name), el("span", { class: "muted" }, note || ""));
  const listOf = (items, empty) => items.length ? el("ul", { class: "picklist" }, items) : el("p", { class: "muted" }, q() ? t("No results.") : empty);
  const entityRow = (e, note) => row_(cardIcon({ card: R.kinds[kindOf(e.entity_id)].default, entities: [e.entity_id] }),
    device(e.entity_id).name, note, entityCard(e));
  const kinds = (list) => {
    const n = {};
    for (const e of list) { const k = kindOf(e.entity_id); n[k] = (n[k] || 0) + 1; }
    return Object.keys(KIND_NAMES).filter((k) => n[k]).map((k) => [k, `${KIND_NAMES[k]} ${n[k]}`]);
  };
  const kindChips = (list, onPick) => chips([["", t("All {0}", list.length)], ...kinds(list)], addState.kind, (v) => { addState.kind = v; onPick(); });

  // The entities that can still go on this page.
  const free = () => { const have = onPage(); return S.catalog.entities.filter((e) => kindOf(e.entity_id) && kindOf(e.entity_id) !== "widget" && !have.has(e.entity_id)); };
  const androids = () => {
    const c = S.configs[S.view.pid];
    const allowed = new Set((c?.effective.apps || []).map((a) => a.package));
    return (c?.installed_widgets || []).filter((w) => allowed.has(w.provider.split("/")[0]) && !page.cards.some((x) => x.options?.provider === w.provider));
  };
  const apps = () => (S.configs[S.view.pid]?.effective.apps || []);
  const builtins = () => WIDGETS.filter((w) => !page.cards.some((c) => c.card === w.card));
  const counts = {
    rooms: () => S.catalog.areas.length,
    devices: () => free().filter((e) => !addState.control || e.controllable).length,
    apps: () => apps().filter((a) => !page.cards.some((c) => c.options?.package === a.package)).length,
    roomcards: () => L().pages.filter((p) => p.id !== page.id).length,
    widgets: () => builtins().length + androids().length,
  };

  function draw() {
    nav.replaceChildren(...ADD_TABS.map(([id, label, ic]) => el("li", { class: addState.tab === id ? "on" : "", onclick: () => { addState.tab = id; search.value = ""; draw(); } },
      icon(ic), el("span", { class: "grow" }, label), el("span", { class: "n" }, String(counts[id]())))));
    search.placeholder = { rooms: t("Search by room or device"), devices: t("Search by device"), roomcards: t("Search by room"), apps: t("Search by app"), widgets: t("Search by widget") }[addState.tab];
    fillList();
  }

  function fillList() {
    const tab = addState.tab;
    filters.replaceChildren();
    if (tab === "rooms") {
      // Rooms on one side, the chosen room's devices on the other.
      const ents = free();
      const inRoom = (id) => ents.filter((e) => e.area_id === id);
      const rooms = S.catalog.areas.filter((a) => hits(a.name) || inRoom(a.id).some((e) => hits(device(e.entity_id).name)));
      if (rooms.length && !rooms.some((a) => a.id === addState.room)) addState.room = rooms[0].id;
      const roomList = el("ul", { class: "picklist rooms", "data-scroll": docked ? "palette-rooms" : null }, rooms.map((a) => el("li", { class: a.id === addState.room ? "on" : "", onclick: () => { addState.room = a.id; addState.kind = ""; fillList(); } },
        icon(/^mdi:[a-z0-9-]+$/.test(a.icon || "") ? a.icon : "mdi:floor-plan"), el("span", { class: "grow" }, a.name),
        el("span", { class: "muted" }, a.id === page.id ? t("This room") : String(inRoom(a.id).filter((e) => !addState.control || e.controllable).length)))));
      const all = inRoom(addState.room).filter((e) => !addState.control || e.controllable);
      const shown = all.filter((e) => (!addState.kind || kindOf(e.entity_id) === addState.kind) && (hits(device(e.entity_id).name, e.entity_id) || hits(areaName[addState.room])));
      filters.append(kindChips(all, fillList), controlToggle());
      const addAll = shown.length > 1 ? el("button", { onclick: () => add((l, cards) => {
        const out = [];
        for (const e of shown) out.push(newCard(l, e.entity_id, [...cards, ...out]));
        return out;
      }) }, t("Add all ({0})", shown.length)) : null;
      body.replaceChildren(el("div", { class: "addsplit" },
        rooms.length ? roomList : el("p", { class: "muted" }, t("No rooms.")),
        el("div", {}, el("div", { class: "addhead" }, el("h4", {}, areaName[addState.room] || ""), addAll),
          listOf([...shown.map((e) => entityRow(e)), ...soon((e) => e.area_id === addState.room)], t("All this room's devices are on the page.")))));
    } else if (tab === "devices") {
      const sel = el("select", { onchange: (ev) => { addState.area = ev.target.value; fillList(); } },
        [["", t("All rooms")], ["-", t("No room")], ...S.catalog.areas.map((a) => [a.id, a.name])].map(([v, l]) => el("option", { value: v, selected: v === addState.area }, l)));
      const inArea = free().filter((e) => (!addState.control || e.controllable) &&
        (!addState.area || (addState.area === "-" ? !e.area_id : e.area_id === addState.area)));
      const shown = inArea.filter((e) => (!addState.kind || kindOf(e.entity_id) === addState.kind) && hits(device(e.entity_id).name, e.entity_id, areaName[e.area_id]))
        // This room's first.
        .sort((x, y) => (y.area_id === page.id) - (x.area_id === page.id));
      filters.append(kindChips(inArea, fillList), el("div", { class: "row" }, sel, controlToggle()));
      body.replaceChildren(listOf([...shown.slice(0, 300).map((e) => entityRow(e, areaName[e.area_id] || t("No room"))),
        ...soon((e) => !addState.area || (addState.area === "-" ? !e.area_id : e.area_id === addState.area))], t("No other devices.")));
    } else if (tab === "roomcards") {
      // The panel's rooms, as a card here: its picture, or its details.
      const rooms = L().pages.filter((p) => p.id !== page.id && hits(p.title));
      const roomCard = (p, type) => (l) => ({ id: nextCardId(l), card: type, entities: [], options: { page: p.id } });
      body.replaceChildren(listOf(rooms.map((p) => el("li", {},
        icon(p.icon || "mdi:floor-plan"), el("span", { class: "grow" }, p.title),
        el("button", { onclick: () => add(roomCard(p, "room.card")), ...drag(roomCard(p, "room.card")) }, t("Room picture")),
        el("button", { onclick: () => add(roomCard(p, "room.summary")), ...drag(roomCard(p, "room.summary")) }, t("Details")))), t("No other rooms on this panel.")));
    } else if (tab === "apps") {
      const list = apps().filter((a) => hits(appName(a.package), a.package));
      body.replaceChildren(
        listOf(list.map((a) => {
          const there = page.cards.some((c) => c.options?.package === a.package);
          return row_("mdi:apps", appName(a.package), there ? t("On the page") : "", (l) => (
            { id: nextCardId(l), card: "app.launch", entities: [], size: "1x1", options: { package: a.package } }), there);
        }), t("No apps allowed on this panel.")),
        el("p", { class: "muted small" }, t("Allowed apps are set in the panel's settings.")));
    } else {
      filters.append(chips([["", t("All")], ["builtin", t("Panel widgets")], ["android", t("Android widgets")]], addState.wkind, (v) => { addState.wkind = v; fillList(); }));
      const items = [];
      if (addState.wkind !== "android") {
        for (const w of builtins().filter((w) => hits(SHAPES[w.card]))) {
          const src = w.source ? sources()[w.source] : null;
          const missing = w.source && !src;
          items.push(row_(w.icon, SHAPES[w.card], missing ? t("Set its source in Project → Widgets") : src ? device(src).name : "",
            (l) => ({ id: nextCardId(l), card: w.card, entities: src ? [src] : [], size: w.size }), missing));
        }
      }
      if (addState.wkind !== "builtin") {
        for (const w of androids().filter((w) => hits(w.label, w.provider))) {
          items.push(row_("mdi:widgets-outline", w.label || w.provider, t("Bound on the panel"),
            (l) => ({ id: nextCardId(l), card: "widget.android", entities: [], size: "2x2", options: { provider: w.provider } })));
        }
      }
      body.replaceChildren(listOf(items, t("No widgets available. Android widgets come from the allowed apps.")));
    }
  }

  // Devices the panels have no card for yet: shown greyed, not added.
  function soon(where) {
    return (S.catalog.upcoming || []).filter((e) => where(e) && hits(e.name, e.entity_id))
      .map((e) => row_("mdi:clock-outline", e.name, t("Coming soon"), null, true));
  }

  function controlToggle() {
    return chips([["1", t("Controls")], ["0", t("With sensors")]], addState.control ? "1" : "0", (v) => { addState.control = v === "1"; addState.kind = ""; draw(); });
  }

  draw();
  return { search, main: el("div", { class: "addmain" }, nav, el("div", { class: "addpane" }, filters, body)) };
}

// ---------- the rooms menu, as on the panel: floors as tabs, rooms as cards ----------

// The panel's floors: HA's under the names given to them, then those added on the panels.
function effFloors() {
  const f = S.configs[S.view.pid]?.effective?.floors;
  if (f) return f;
  return S.catalog.floors.map((x) => ({ ...x, areas: S.catalog.areas.filter((a) => a.floor_id === x.id).map((a) => a.id) }));
}

// The tabs (as RoomGroups on the panel): a floor's rooms, its own or shown there too (also),
// the floors shown empty (layout floors), then the rooms on no floor.
function roomGroups(l) {
  const floors = effFloors();
  const ids = new Set(floors.map((f) => f.id));
  const own = (p) => (p.floor && ids.has(p.floor) ? p.floor : floors.find((f) => (f.areas || []).includes(p.id))?.id) || null;
  const shown = l.floors || [];
  const groups = [];
  for (const f of floors) {
    const pages = [], also = new Set();
    l.pages.forEach((p, i) => {
      if (own(p) === f.id) pages.push(i);
      else if ((p.also || []).some((a) => a.floor === f.id)) { pages.push(i); also.add(i); }
    });
    if (pages.length || shown.includes(f.id)) groups.push({ id: f.id, name: f.name, icon: f.icon, pages, also });
  }
  const rest = l.pages.map((_, i) => i).filter((i) => own(l.pages[i]) === null);
  if (rest.length || !groups.length) groups.push({ id: null, name: groups.length ? t("Other") : t("Rooms"), pages: rest, also: new Set() });
  return groups;
}

// What holds a room's card on a floor: the room itself, or its "shown here too" entry.
function tileHolder(l, g, i) {
  const p = l.pages[i];
  return g.also.has(i) ? p.also.find((a) => a.floor === g.id) : p;
}

// A room as a card of the menu's grid: its shape, size and place on that floor (picture, 2x1).
function menuCard(l, g, i) {
  const tile = tileHolder(l, g, i)?.tile || {};
  return { id: l.pages[i].id, card: tile.card || "room.card", size: tile.size || "2x1", pos: tile.pos, entities: [], _i: i };
}

function setTile(l, g, i, values) {
  const h = tileHolder(l, g, i);
  h.tile = { card: "room.card", size: "2x1", ...(h.tile || {}), ...values };
  if (!h.tile.pos) delete h.tile.pos;
  // On its own floor the default (picture at 2x1, in order) is left out, as the panel does.
  if (h === l.pages[i] && h.tile.card === "room.card" && h.tile.size === "2x1" && !h.tile.pos) delete h.tile;
}

// Every room of the tab fixed where it shows, so what is added or moved moves no other.
function pinGroup(l, g) {
  const { placed } = pack(g.pages.map((i) => menuCard(l, g, i)));
  for (const pl of placed) if (!pl.card.pos) setTile(l, g, pl.card._i, { pos: { screen: pl.screen, col: pl.col, row: pl.row } });
}

function currentGroup(l) {
  const groups = roomGroups(l);
  return { groups, g: groups.find((x) => (x.id || "") === S.floorTab) || groups[0] };
}

let roomDrag = null;

function renderRoomsMenu(main) {
  const l = L();
  const { groups, g } = currentGroup(l);
  S.floorTab = g.id || "";
  main.append(el("div", { class: "ftabs" },
    groups.map((x) => el("div", { class: "ftab" + (x === g ? " on" : ""), onclick: () => { S.floorTab = x.id || ""; render(); } },
      x.icon ? icon(x.icon) : null, el("span", { class: "fname" }, x.name),
      x.id ? el("span", { class: "fedit", title: t("The floor's name and icon, or delete it"), onclick: (ev) => { ev.stopPropagation(); floorSheet(x.id, x.name, x.icon, false); } }, icon("mdi:pencil-outline")) : null)),
    el("div", { class: "ftab add", title: t("Add a floor"), onclick: addFloorSheet }, icon("mdi:plus"))));

  const cards = g.pages.map((i) => menuCard(l, g, i));
  const { placed, screens, grids } = pack(cards);
  const box = el("div", { class: "screens" });
  for (let s = 0; s <= screens; s++) {
    const grid = el("div", { class: "screen" });
    if (s === screens) grid.append(el("span", { class: "screenlabel" }, t("Screen {0} (new)", screens + 1)));
    else if (screens > 1) grid.append(el("span", { class: "screenlabel" }, t("Screen {0} of {1}", s + 1, screens)));
    for (const pl of placed.filter((x) => x.screen === s)) grid.append(roomTile(pl, g));
    for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) {
      if (s < screens && grids[s][r][c]) continue;
      grid.append(roomCell(g, placed, s, c, r));
    }
    box.append(grid);
  }
  main.append(box);
}

function roomTile(pl, g) {
  const l = L();
  const i = pl.card._i;
  const p = l.pages[i];
  const a = S.catalog.areas.find((x) => x.id === p.id);
  const picture = pl.card.card === "room.card" && a?.picture;
  const devices = p.cards.filter((c) => kindOfCard(c.card) !== "app" && kindOfCard(c.card) !== "widget").length;
  const node = el("div", {
    class: ["cell", "rtile", picture ? "pic" : "", g.also.has(i) ? "also" : "", i === S.page ? "current" : ""].join(" "),
    draggable: "true",
    title: g.also.has(i) ? t("Shown here too") : "",
    style: {
      gridColumn: `${pl.col + 1} / span ${pl.w}`, gridRow: `${pl.row + 1} / span ${pl.h}`,
      backgroundImage: picture ? `url("api/editor/area_picture/${enc(p.id)}?v=${enc(a.picture)}")` : "",
    },
    onclick: () => { S.page = i; S.menu = false; S.sel = null; render(); },
    ondragstart: (ev) => { roomDrag = p.id; ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/plain", p.id); node.classList.add("dragging"); },
    ondragend: () => { roomDrag = null; node.classList.remove("dragging"); },
    ondragover: (ev) => { if (roomDrag && roomDrag !== p.id) { ev.preventDefault(); node.classList.add("over"); } },
    ondragleave: () => node.classList.remove("over"),
    ondrop: (ev) => { ev.preventDefault(); moveRoom(g, roomDrag, pl.screen, pl.col, pl.row); },
  },
  picture ? null : icon(p.icon || "mdi:floor-plan", { class: "ricon" }),
  el("div", { class: "rname" }, el("b", {}, p.title),
    el("span", {}, pl.card.card === "room.summary" || !picture ? t("{0} devices", devices) : "")),
  el("span", { class: "redit", title: t("Edit room"), onclick: (ev) => { ev.stopPropagation(); roomTileSheet(i, g.id); } }, icon("mdi:pencil-outline")));
  return node;
}

// An empty cell of the menu: + adds a room there; a dragged room goes there.
function roomCell(g, placed, screen, col, row) {
  const node = el("div", {
    class: "cell empty", title: t("Add a room here"),
    style: { gridColumn: `${col + 1}`, gridRow: `${row + 1}` },
    onclick: () => roomAddSheet(g, placed, screen, col, row),
    ondragover: (ev) => { if (roomDrag) { ev.preventDefault(); node.classList.add("over"); } },
    ondragleave: () => node.classList.remove("over"),
    ondrop: (ev) => { ev.preventDefault(); moveRoom(g, roomDrag, screen, col, row); },
  }, "+");
  return node;
}

// Onto a room the two swap places; onto empty cells it goes there (as cards do).
function moveRoom(g, id, screen, col, row) {
  if (!id) return;
  const l = L();
  const cards = g.pages.map((i) => menuCard(l, g, i));
  const index = cards.findIndex((c) => c.id === id);
  if (index < 0 || !placeCard(cards, index, screen, col, row)) return;
  change((lay) => { for (const c of cards) setTile(lay, g, c._i, { pos: c.pos }); });
}

// + in an empty cell: a room of this panel on another floor (shown here too), one of HA's,
// a whole floor of HA's, or an empty one. One room goes in the cell touched: its picture at
// 2x1, or 1x1 where 2x1 does not fit.
function roomAddSheet(g, placed, screen, col, row) {
  const fits = (size) => {
    const [w, h] = spanOf(size);
    return col + w <= COLS && row + h <= ROWS && !placed.some((p) =>
      p.screen === screen && p.col < col + w && col < p.col + p.w && p.row < row + h && row < p.row + p.h);
  };
  const size = fits("2x1") ? "2x1" : "1x1";
  const spot = { card: "room.card", size, pos: { screen, col, row } };
  const l = L();
  const have = new Set(l.pages.map((p) => p.id));
  const list = el("ul", { class: "picklist" });

  // The panel's rooms on other floors: the same room, shown on this floor too.
  if (g.id) {
    const here = new Set(g.pages);
    const others = roomGroups(l).filter((x) => x.id !== g.id)
      .map((x) => [x.name, x.pages.filter((i) => !here.has(i) && !x.also.has(i))]).filter(([, ps]) => ps.length);
    if (others.length) list.append(el("div", { class: "floorhead" }, el("h4", { class: "grow" }, t("This panel's rooms on other floors (shown here too)"))));
    for (const [name, ps] of others) {
      list.append(el("div", { class: "muted", style: { margin: "6px 4px 2px" } }, name));
      for (const i of ps) {
        const p = l.pages[i];
        list.append(el("li", { onclick: () => {
          change((lay) => { pinGroup(lay, g); (lay.pages[i].also ||= []).push({ floor: g.id, tile: spot }); });
          closeSheet();
        } }, icon(p.icon || "mdi:floor-plan"), el("span", { class: "grow" }, p.title), icon("mdi:layers-plus")));
      }
    }
  }

  // HA's rooms not on the panel, by floor: one, or a whole floor.
  const addAreas = (ids, one) => {
    change((lay) => {
      pinGroup(lay, g);
      const at = lay.pages.length;
      addRoomPages(lay, ids);
      for (const p of lay.pages.slice(at)) if (g.id) p.floor = g.id;
      if (one && lay.pages.length > at) lay.pages[at].tile = spot;
    });
    closeSheet();
    if (one) roomTileSheet(L().pages.length - 1, g.id);
  };
  const floors = [...S.catalog.floors, { id: null, name: t("No floor") }];
  let haRooms = 0;
  for (const f of floors) {
    const areas = S.catalog.areas.filter((a) => (a.floor_id || null) === f.id && !have.has(a.id));
    if (!areas.length) continue;
    haRooms += areas.length;
    list.append(el("div", { class: "floorhead" }, el("h4", { class: "grow" }, `Home Assistant · ${f.name}`),
      areas.length > 1 ? el("button", { onclick: () => addAreas(areas.map((a) => a.id), false) }, t("+ Whole floor ({0})", areas.length)) : null));
    for (const a of areas) {
      list.append(el("li", { onclick: () => addAreas([a.id], true) },
        icon(a.icon || "mdi:floor-plan"), el("span", { class: "grow" }, a.name), el("span", { class: "muted" }, t("{0} devices", controlsIn(a.id).length))));
    }
  }
  sheet(
    el("h3", {}, t("Add a room · {0}", g.name)),
    el("p", { class: "muted" }, t("A room of Home Assistant's comes with all its devices, in the cell you tapped.")),
    list.children.length ? list : null,
    !haRooms ? el("p", { class: "muted" }, t("All of Home Assistant's rooms are added.")) : null,
    el("div", { class: "actions" },
      el("button", { onclick: () => {
        change((lay) => {
          pinGroup(lay, g);
          const page = { id: pageId("room", new Set(lay.pages.map((p) => p.id))), title: t("New room"), cards: [], tile: spot };
          if (g.id) page.floor = g.id;
          lay.pages.push(page);
        });
        closeSheet();
        roomTileSheet(L().pages.length - 1, g.id);
      } }, t("+ Empty room")),
      el("span", { class: "spacer" }),
      el("button", { onclick: closeSheet }, t("Close"))),
  );
}

// A room's pencil, on a floor: name, icon, its card there (shape, size), its own floor, or
// taking it off a floor it is shown on too; deleting it.
function roomTileSheet(i, floorId) {
  const l = L();
  const p = l.pages[i];
  if (!p) return;
  const g = roomGroups(l).find((x) => x.id === floorId && x.pages.includes(i)) || { id: null, also: new Set(), pages: [i] };
  const isAlso = g.also.has(i);
  const tile = tileHolder(l, g, i)?.tile || {};
  const shape = tile.card || "room.card", size = tile.size || "2x1";
  const edit = (fn) => { change((lay) => fn(lay)); roomTileSheet(i, floorId); };
  const sizes = shape === "room.summary" ? ["1x1", "2x1"] : ["1x1", "2x1", "2x2"];
  const ownFloor = roomGroups(l).find((x) => x.pages.includes(i) && !x.also.has(i))?.name;
  sheet(
    el("h3", {}, p.title),
    isAlso ? el("p", { class: "muted" }, t("Shown here too. Its floor: {0}", ownFloor || "—")) : null,
    field(t("Name"), el("input", { value: p.title, onchange: (e) => { const v = e.target.value.trim(); if (v) edit((lay) => { lay.pages[i].title = v; }); } })),
    field(t("Icon"), iconInput(p.icon, "mdi:floor-plan", (v) => edit((lay) => { v && validIcon(v) ? lay.pages[i].icon = v : delete lay.pages[i].icon; }))),
    field(t("Its card in the rooms menu"), chips([["room.card", t("Room picture")], ["room.summary", t("Details")]], shape,
      (v) => edit((lay) => setTile(lay, g, i, { card: v, size: v === "room.summary" && !["1x1", "2x1"].includes(size) ? "2x1" : size })))),
    shape === "room.card" ? freeSizeFields(size, (v) => edit((lay) => setTile(lay, g, i, { size: v })))
      : field(t("Size"), chips(sizes.map((v) => [v, SIZES[v] || v]), size, (v) => edit((lay) => setTile(lay, g, i, { size: v })))),
    !isAlso && effFloors().length ? field(t("Floor on this panel"), (() => {
      const haFloor = effFloors().find((f) => (f.areas || []).includes(p.id))?.name;
      const s = selectEl([["", t("From HA ({0})", haFloor || t("No floor"))], ...effFloors().map((f) => [f.id, f.name])], p.floor || "");
      s.onchange = () => { change((lay) => { s.value ? lay.pages[i].floor = s.value : delete lay.pages[i].floor; }); closeSheet(); };
      return s;
    })()) : null,
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: closeSheet }, t("Done")),
      el("button", { onclick: () => { closeSheet(); S.page = i; S.menu = false; S.sel = null; render(); } }, t("Open room")),
      el("span", { class: "spacer" }),
      isAlso ? el("button", { onclick: () => {
        change((lay) => { lay.pages[i].also = lay.pages[i].also.filter((a) => a.floor !== g.id); if (!lay.pages[i].also.length) delete lay.pages[i].also; });
        closeSheet();
      } }, t("Remove from this floor")) : null,
      el("button", { class: "danger", onclick: () => {
        if (!confirm(t("Delete the room \"{0}\" and all its cards from this panel?", p.title))) return;
        change((lay) => { lay.pages.splice(i, 1); S.page = DASH; S.sel = null; });
        closeSheet();
      } }, t("Delete room"))),
  );
}

// A floor's name and icon, saved at once for this panel or for all panels (as on the panel);
// a new floor (not in HA) shows on this panel once named. Deleting it takes its rooms off
// this panel; a room shown on another floor too stays there.
function floorSheet(id, name, iconName, isNew) {
  const pid = S.view.pid;
  let text = isNew ? "" : name, chosen = iconName || "";
  const msg = el("div", { class: "msg error" });
  const save = async (all) => {
    if (isNew && !text.trim()) { msg.textContent = t("Enter the floor's name."); return; }
    const r = await post(`api/panels/${enc(pid)}/floor`, { floor_id: id, name: text.trim(), icon: chosen || null, all });
    if (!r.ok) { msg.textContent = t("Not saved: ") + (r.body?.error || r.status); return; }
    await loadConfig(pid);
    if (isNew) { S.floorTab = id; change((l) => { l.floors = [...new Set([...(l.floors || []), id])]; }); }
    closeSheet();
    render();
  };
  const g = roomGroups(L()).find((x) => x.id === id);
  sheet(
    el("h3", {}, isNew ? t("New floor") : name),
    field(t("Floor name"), el("input", { value: text, placeholder: isNew ? t("Garden") : t("Blank: Home Assistant's name"), oninput: (e) => { text = e.target.value; } })),
    field(t("Icon"), iconInput(chosen, "mdi:home-floor-l", (v) => { chosen = v && validIcon(v) ? v : ""; })),
    msg,
    el("div", { class: "actions" },
      el("button", { class: "solid", onclick: () => save(false) }, t("This panel")),
      el("button", { onclick: () => save(true) }, t("Copy to all panels")),
      el("span", { class: "spacer" }),
      !isNew ? el("button", { class: "danger", onclick: () => {
        const n = g ? g.pages.length : 0;
        if (!confirm(t("Delete the floor from this panel with its rooms ({0}) and all their cards?", n))) return;
        change((l) => deleteFloor(l, id));
        S.floorTab = "";
        closeSheet();
      } }, t("Delete floor")) : null,
      el("button", { onclick: closeSheet }, t("Cancel"))),
  );
}

function deleteFloor(l, id) {
  const g = roomGroups(l).find((x) => x.id === id);
  const rooms = new Set(g ? g.pages : []);
  const also = g ? g.also : new Set();
  const out = [];
  l.pages.forEach((p, i) => {
    const rest = (p.also || []).filter((a) => a.floor !== id);
    if (!rooms.has(i) || also.has(i)) { out.push(Object.assign(p, { also: rest })); }
    else if (rest.length) {
      // Its own floor goes: it stays on the next floor it shows on.
      const [next, ...more] = rest;
      p.floor = next.floor;
      if (next.tile) p.tile = next.tile; else delete p.tile;
      out.push(Object.assign(p, { also: more }));
    }
  });
  for (const p of out) if (!p.also?.length) delete p.also;
  l.pages = out;
  l.floors = (l.floors || []).filter((f) => f !== id);
  if (!l.floors.length) delete l.floors;
  S.page = DASH;
}

// + after the floor tabs: one of the floors not shown yet, empty or with its rooms of HA's,
// or a new floor.
function addFloorSheet() {
  const l = L();
  const shown = new Set(roomGroups(l).map((x) => x.id).filter(Boolean));
  const have = new Set(l.pages.map((p) => p.id));
  const list = el("ul", { class: "picklist" });
  for (const f of effFloors().filter((x) => !shown.has(x.id))) {
    const fresh = (f.areas || []).filter((a) => !have.has(a) && S.catalog.areas.some((x) => x.id === a));
    list.append(el("li", {}, icon(f.icon || "mdi:home-floor-l"), el("span", { class: "grow" }, f.name),
      // The floor added is the tab shown.
      el("button", { onclick: () => { S.floorTab = f.id; change((lay) => { lay.floors = [...new Set([...(lay.floors || []), f.id])]; }); closeSheet(); } }, t("Empty")),
      fresh.length ? el("button", { class: "solid", onclick: () => {
        S.floorTab = f.id;
        change((lay) => {
          lay.floors = [...new Set([...(lay.floors || []), f.id])];
          const at = lay.pages.length;
          addRoomPages(lay, fresh);
          for (const p of lay.pages.slice(at)) p.floor = f.id;
        });
        closeSheet();
      } }, t("With its rooms ({0})", fresh.length)) : null));
  }
  sheet(
    el("h3", {}, t("Add a floor")),
    list.children.length ? list : el("p", { class: "muted" }, t("All floors are shown.")),
    el("div", { class: "actions" },
      el("button", { class: "solid", onclick: () => floorSheet("wp_" + Math.random().toString(36).slice(2, 10), "", null, true) }, t("+ New floor")),
      el("span", { class: "spacer" }),
      el("button", { onclick: closeSheet }, t("Close"))),
  );
}

// ---------- settings and permissions ----------

// The panel app's languages, each in its own name.
const PANEL_LANGS = [["en", "English"], ["ar", "العربية"], ["fr", "Français"], ["es", "Español"]];
const THEMES = [["light", t("Light")], ["dark", t("Dark")]];
const ROLES = [["guest", t("No PIN")], ["user", t("User PIN")], ["installer", t("Installer PIN")]];
const CAPS = [["control", t("Control")], ["apps", t("Apps")], ["edit_cards", t("Edit cards")], ["reorder", t("Rearrange")], ["dashboard", t("Customise my dashboard")], ["add_widgets", t("Add apps and widgets")],
  ["connection", t("Connection page")], ["display", t("Display page")], ["brightness", t("Brightness")], ["system", t("System buttons")],
  ["rooms", t("Rooms")], ["devices", t("Devices")], ["panel", t("Panel settings")], ["project", t("Project")]];
// Pages of the panel's settings screen: the guest never opens it.
const PAGE_CAPS = new Set(["connection", "display", "brightness", "system"]);
const BUILT_IN = { lang: "en", theme: "light", screensaver: { idle_seconds: 60, dim_brightness: 10 }, app_return_seconds: 300, home_title: "" };

// A choice, as on the panel: a few in a joined bar; more folded to the chosen one, opening
// to a list with a search when long. Read and set as a <select> is: .value and "change".
function selectEl(options, value) {
  const box = el("div", { class: options.length > 4 ? "fold" : "seg" });
  let current = value;
  let open = false;
  Object.defineProperty(box, "value", { get: () => current, set: (v) => { current = v; draw(); } });
  const choose = (v) => {
    open = false;
    if (v !== current) { current = v; box.dispatchEvent(new Event("change")); }
    draw();
  };
  function draw() {
    if (box.className === "seg") {
      box.replaceChildren(...options.map(([v, label]) => el("button", { type: "button", class: v === current ? "on" : "", onclick: () => choose(v) }, label)));
      return;
    }
    const chosen = options.find(([v]) => v === current) || options[0];
    const head = el("div", { class: "foldhead", onclick: () => { open = !open; draw(); } },
      el("span", { class: "grow" + (current ? " on" : "") }, chosen ? chosen[1] : ""), icon(open ? "mdi:chevron-up" : "mdi:chevron-down"));
    if (!open) { box.replaceChildren(head); return; }
    const list = el("ul", { class: "foldlist" });
    const search = options.length > 8 ? el("input", { placeholder: t("Search {0}", options.length), oninput: () => fill() }) : null;
    const fill = () => {
      const q = (search?.value || "").trim().toLowerCase();
      const shown = options.filter(([, label]) => !q || String(label).toLowerCase().includes(q));
      list.replaceChildren(...(shown.length ? shown.map(([v, label]) => el("li", { class: v === current ? "on" : "", onclick: () => choose(v) },
        icon(v === current ? "mdi:check" : "mdi:circle-small"), el("span", {}, label))) : [el("li", { class: "off" }, t("No results."))]));
    };
    fill();
    box.replaceChildren(head, search, list);
    search?.focus();
  }
  draw();
  return box;
}

// Display settings: blank means "inherit".
function displayFields(form, values, inherited, inheritLabel) {
  const ss = values.screensaver || {};
  const name = (list, v) => list.find(([k]) => k === v)?.[1] || v;
  const f = {
    lang: selectEl([["", `${inheritLabel} (${name(PANEL_LANGS, inherited.lang)})`], ...PANEL_LANGS], values.lang || ""),
    theme: selectEl([["", `${inheritLabel} (${name(THEMES, inherited.theme)})`], ...THEMES], values.theme || ""),
    idle: el("input", { type: "number", min: 10, value: ss.idle_seconds ?? "", placeholder: inherited.screensaver.idle_seconds }),
    dim: el("input", { type: "number", min: 0, max: 100, value: ss.dim_brightness ?? "", placeholder: inherited.screensaver.dim_brightness }),
    ret: el("input", { type: "number", min: 30, max: 3600, value: values.app_return_seconds ?? "", placeholder: inherited.app_return_seconds }),
    home: el("input", { maxlength: 40, value: values.home_title ?? "", placeholder: inherited.home_title || t("My dashboard") }),
  };
  form.append(
    el("label", {}, t("Home page title")), f.home,
    el("label", {}, t("Language")), f.lang,
    el("label", {}, t("Theme")), f.theme,
    el("label", {}, t("Screensaver after (seconds)")), f.idle,
    el("label", {}, t("Dimmed brightness (%)")), f.dim,
    el("label", {}, t("Back from another app after (seconds)")), f.ret,
  );
  return () => {
    const out = {};
    if (f.lang.value) out.lang = f.lang.value;
    if (f.theme.value) out.theme = f.theme.value;
    const screensaver = {};
    if (f.idle.value !== "") screensaver.idle_seconds = Number(f.idle.value);
    if (f.dim.value !== "") screensaver.dim_brightness = Number(f.dim.value);
    if (Object.keys(screensaver).length) out.screensaver = screensaver;
    if (f.ret.value !== "") out.app_return_seconds = Number(f.ret.value);
    if (f.home.value.trim()) out.home_title = f.home.value.trim();
    return out;
  };
}

function pinRow(label, isSet, setText, onSet, onRemove, removeLabel) {
  const input = el("input", { type: "password", inputmode: "numeric", maxlength: 8, placeholder: t("4 to 8 digits"), autocomplete: "new-password", style: { width: "140px" } });
  return el("div", { class: "row", style: { margin: "6px 0" } },
    el("span", { style: { minWidth: "130px" } }, label),
    el("span", { class: isSet ? "dot on" : "dot off", style: { minWidth: "110px", fontSize: "13px" } }, setText),
    input,
    el("button", { class: "primary", onclick: async () => {
      if (!/^[0-9]{4,8}$/.test(input.value)) { alert(t("The PIN has 4 to 8 digits.")); return; }
      await onSet(input.value); input.value = "";
    } }, isSet ? t("Change") : t("Set")),
    isSet ? el("button", { class: "danger", onclick: onRemove }, removeLabel) : null,
  );
}

function inheritedDisplay() {
  const d = S.site?.defaults || {};
  return {
    lang: d.lang || BUILT_IN.lang, theme: d.theme || BUILT_IN.theme,
    screensaver: { ...BUILT_IN.screensaver, ...(d.screensaver || {}) },
    app_return_seconds: d.app_return_seconds ?? BUILT_IN.app_return_seconds,
    home_title: d.home_title ?? BUILT_IN.home_title,
  };
}

function renderSettings(view, p) {
  const pid = p.id;
  const cfg = S.configs[pid];
  if (!cfg) { loadConfig(pid).then(render); return; }
  const msg = el("div", { class: "msg" });
  const reload = async () => { await loadConfig(pid); render(); };

  const name = el("input", { value: p.name });
  const place = selectEl([["", t("— None —")], ...S.catalog.areas.map((a) => [a.id, a.name])], S.editor[pid]?.area_id || "");
  const general = el("div", { class: "form" }, el("label", {}, t("Panel name")), name, el("label", {}, t("Where it is mounted")), place,
    el("label", {}, t("Device type")), el("div", { class: "muted" }, `${TYPES[p.type] || PLATFORMS[p.platform || "android"] || p.platform}${p.model ? " · " + p.model : ""}`));
  const display = el("div", { class: "form" });
  const readDisplay = displayFields(display, cfg.config.overrides || {}, inheritedDisplay(), t("From the project"));

  view.append(
    el("div", { class: "box" }, el("h3", {}, t("Panel")), general,
      el("h3", { style: { marginTop: "18px" } }, t("Display")), el("p", { class: "muted" }, t("Empty fields take the project's value.")), display,
      el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
        const newName = name.value.trim();
        if (newName && newName !== p.name) await post(`api/panels/${enc(pid)}/rename`, { name: newName });
        if ((S.editor[pid]?.area_id || "") !== place.value) {
          await post(`api/panels/${enc(pid)}/area`, { area_id: place.value || null });
          if (S.editor[pid]) S.editor[pid].area_id = place.value || null;
        }
        const keep = cfg.config.overrides?.floor_names ? { floor_names: cfg.config.overrides.floor_names } : {};
        const r = await put(`api/panels/${enc(pid)}/config`, { overrides: { ...keep, ...readDisplay() } });
        if (!r.ok) { msg.className = "msg error"; msg.textContent = t("Not saved: ") + (r.body?.error || r.status); return; }
        await refresh(); await loadConfig(pid);
        toast(p.online ? t("Saved and on the panel.") : t("Saved. The panel gets it when it connects."));
        render();
      } }, t("Save for this panel")),
      el("button", { onclick: async () => {
        const display = readDisplay();
        if (!Object.keys(display).length) { alert(t("This panel has no settings of its own to copy.")); return; }
        if (!confirm(t("These become the project's settings, replacing every panel's own. Continue?"))) return;
        const r = await post("api/site/display_all", { display });
        if (!r.ok) { msg.className = "msg error"; msg.textContent = t("Not saved: ") + (r.body?.error || r.status); return; }
        await loadSite(); await loadConfig(pid);
        toast(r.body.replaced ? t("Copied to all panels; {0} panels' own settings replaced.", r.body.replaced) : t("Copied to all panels."));
        render();
      } }, t("Copy to all panels"))), msg),
    appsBox(pid, p, cfg),
    el("div", { class: "box" }, el("h3", {}, t("A PIN for this panel only (optional)")),
      el("p", { class: "muted" }, t("Without it the panel uses the project's PINs.")),
      pinRow(t("Installer PIN"), cfg.config.installer_pin_set, cfg.config.installer_pin_set ? t("● This panel only") : t("From the project"),
        async (pin) => { await post(`api/panels/${enc(pid)}/pin`, { role: "installer", pin }); reload(); },
        async () => { await post(`api/panels/${enc(pid)}/pin`, { role: "installer", pin: null }); reload(); }, t("Use the project's PIN")),
      pinRow(t("User PIN"), cfg.config.user_pin_set, cfg.config.user_pin_set ? t("● This panel only") : t("From the project"),
        async (pin) => { await post(`api/panels/${enc(pid)}/pin`, { role: "user", pin }); reload(); },
        async () => { await post(`api/panels/${enc(pid)}/pin`, { role: "user", pin: null }); reload(); }, t("Use the project's PIN"))),
    el("div", { class: "box" }, el("h3", {}, t("Unpair")),
      el("p", { class: "muted" }, t("The panel is cut off from Home Assistant at once and must be paired again.")),
      el("button", { class: "danger", onclick: async () => {
        if (!confirm(t("Unpair \"{0}\"?", p.name))) return;
        await post(`api/panels/${enc(pid)}/revoke`);
        S.view = { kind: "home" };
        await refresh();
      } }, t("Unpair the panel"))),
  );
}

// Apps the panel may open: picked from those installed on it (it reports them when it
// connects), or typed by package name.
function appsBox(pid, p, cfg) {
  const apps = (cfg.config.apps || []).map((a) => ({ ...a }));
  const installed = cfg.installed_apps || [];
  const nameOf = (pkg) => installed.find((a) => a.package === pkg)?.label || pkg;
  const list = el("div");
  const draw = () => {
    list.replaceChildren(...(apps.length ? apps.map((a, i) => el("div", { class: "row", style: { margin: "6px 0" } },
      el("input", { value: a.label || "", placeholder: nameOf(a.package), style: { width: "180px" },
                    oninput: (e) => { a.label = e.target.value.trim() || undefined; } }),
      el("span", { class: "muted", style: { minWidth: "220px", direction: "ltr", fontSize: "13px" } }, a.package),
      el("input", { type: "number", min: 30, max: 3600, value: a.return_seconds ?? "", placeholder: t("{0} s", cfg.effective.app_return_seconds),
                    style: { width: "110px" }, title: t("Back to the panel after (seconds)"),
                    oninput: (e) => { a.return_seconds = e.target.value === "" ? undefined : Number(e.target.value); } }),
      el("button", { class: "danger", onclick: () => { apps.splice(i, 1); draw(); } }, t("Delete")),
    )) : [el("p", { class: "muted" }, t("No allowed apps."))]));
  };
  draw();
  const free = () => installed.filter((a) => !apps.some((x) => x.package === a.package));
  let pick = selectEl([["", installed.length ? t("— Choose an installed app —") : t("— The panel has not sent its apps yet —")], ...free().map((a) => [a.package, a.label])], "");
  const typed = el("input", { placeholder: t("or the package name: com.example.app"), style: { width: "240px", direction: "ltr" } });
  const add = () => {
    const pkg = pick.value || typed.value.trim();
    if (!pkg) return;
    if (!/^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$/.test(pkg)) { alert(t("Not a valid package name.")); return; }
    if (!apps.some((a) => a.package === pkg)) apps.push({ package: pkg });
    typed.value = "";
    const fresh = selectEl([["", t("— Choose an installed app —")], ...free().map((a) => [a.package, a.label])], "");
    pick.replaceWith(fresh);
    pick = fresh;
    draw();
  };
  return el("div", { class: "box" }, el("h3", {}, t("Allowed apps")),
    el("p", { class: "muted" }, t("They show on the panel's apps page, and their widgets are added from +. A blank time takes the general return time.")),
    list,
    el("div", { class: "row", style: { marginTop: "10px" } }, pick, typed, el("button", { onclick: add }, t("Add"))),
    el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
      const clean = apps.map((a) => Object.fromEntries(Object.entries(a).filter(([, v]) => v !== undefined && v !== "")));
      const r = await put(`api/panels/${enc(pid)}/config`, { apps: clean });
      if (!r.ok) { alert(t("Not saved: ") + (r.body?.error || r.status)); return; }
      await loadConfig(pid);
      toast(p.online ? t("Saved and on the panel.") : t("Saved. The panel gets it when it connects."));
      render();
    } }, t("Save apps"))));
}

function renderAccess(view, p) {
  const pid = p.id;
  const cfg = S.configs[pid];
  if (!cfg) { loadConfig(pid).then(render); return; }
  const table = el("table", { class: "access" });
  table.append(el("tr", {}, el("th", {}, ""), ...CAPS.map(([, label]) => el("th", {}, label))));
  const boxes = {};
  for (const [role, label] of ROLES) {
    const tr = el("tr", {}, el("td", {}, label));
    for (const [cap] of CAPS) {
      const guestPage = role === "guest" && PAGE_CAPS.has(cap);
      const cb = el("input", { type: "checkbox", checked: !guestPage && cfg.effective.access[role].includes(cap),
                               disabled: cap === "control" || guestPage });
      boxes[role + ":" + cap] = cb;
      tr.append(el("td", {}, cb));
    }
    table.append(tr);
  }
  view.append(el("div", { class: "box" },
    el("h3", {}, t("What is allowed on this panel")),
    el("p", { class: "muted" }, t("Control is always allowed. Rooms, devices, the panel's settings and the project are edited here, and on the panel itself by whoever has the permission.")),
    el("p", { class: "muted" }, t("The panel's settings pages follow these permissions; the security page always shows. Without a PIN the settings do not open.")),
    table,
    el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
      const access = {};
      for (const [role] of ROLES) access[role] = CAPS.map(([c]) => c).filter((c) => boxes[role + ":" + c].checked);
      const r = await put(`api/panels/${enc(pid)}/config`, { access, access_known: CAPS.map(([c]) => c) });
      if (!r.ok) { alert(t("Not saved: ") + (r.body?.error || r.status)); return; }
      await loadConfig(pid);
      toast(p.online ? t("Saved and on the panel.") : t("Saved. The panel gets it when it connects."));
    } }, t("Save")))));
}

// ---------- project ----------

function renderProject(view) {
  const tabs = [["general", t("General")], ["devices", t("Device types")], ["widgets", t("Widgets")], ["intercom", t("Intercom")], ["airplay", "AirPlay"]];
  view.append(el("div", { class: "titlebar" },
    el("h2", {}, S.site?.name || t("Project")),
    el("div", { class: "tabs" }, tabs.map(([id, label]) =>
      el("button", { class: S.view.tab === id ? "active" : "", onclick: () => { S.view.tab = id; render(); } }, label)))));
  if (!S.site) { loadSite().then(render); return; }
  if (S.view.tab === "devices") return renderDevices(view);
  if (S.view.tab === "widgets") return renderSources(view);
  if (S.view.tab === "intercom") return renderIntercom(view);
  if (S.view.tab === "airplay") return renderAirPlay(view);

  const site = S.site;
  const name = el("input", { value: site.name || "" });
  const display = el("div", { class: "form" });
  const read = displayFields(display, site.defaults || {}, BUILT_IN, t("Default"));
  const reload = async () => { await loadSite(); await loadOverview(); render(); };
  view.append(
    el("div", { class: "box" }, el("h3", {}, t("Project")),
      el("div", { class: "form" }, el("label", {}, t("Project name")), name),
      el("h3", { style: { marginTop: "18px" } }, t("Default settings for all panels")), display,
      el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
        // Floor names are not on this form: kept as they are.
        const keep = site.defaults?.floor_names ? { floor_names: site.defaults.floor_names } : {};
        const r = await put("api/site", { name: name.value.trim(), defaults: { ...keep, ...read() } });
        if (!r.ok) { alert(t("Not saved: ") + (r.body?.error || r.status)); return; }
        S.site = r.body; toast(t("Saved, and on every online panel.")); render();
      } }, t("Save")))),
    el("div", { class: "box" }, el("h3", {}, t("PINs")),
      el("p", { class: "muted" }, t("The installer PIN gives every local permission on a panel; the user PIN gives what each panel's permissions allow. Panels only get them hashed, and they work offline.")),
      pinRow(t("Installer PIN"), site.installer_pin_set, site.installer_pin_set ? t("● Set") : t("○ Not set"),
        async (pin) => { await post("api/site/pin", { role: "installer", pin }); reload(); },
        async () => { if (confirm(t("Remove the installer PIN? The panels' settings become open."))) { await post("api/site/pin", { role: "installer", pin: null }); reload(); } }, t("Remove")),
      pinRow(t("User PIN"), site.user_pin_set, site.user_pin_set ? t("● Set") : t("○ Not set"),
        async (pin) => { await post("api/site/pin", { role: "user", pin }); reload(); },
        async () => { await post("api/site/pin", { role: "user", pin: null }); reload(); }, t("Remove"))),
    integrationBox(),
  );
}

// AirPlay: each panel an AirPlay speaker on the home network, named after it (off until turned
// on), with its own password if wanted; what plays shows on its media_player in HA.
let airplay = null;
async function renderAirPlay(view) {
  if (!airplay) {
    view.append(el("p", { class: "muted" }, t("Loading…")));
    const r = await api("api/airplay");
    airplay = r.ok ? r.body : { error: r.status };
    render();
    return;
  }
  if (airplay.error) { view.append(el("p", { class: "muted" }, t("Not available"))); return; }
  const save = async (pid, changes) => {
    const r = await put(`api/airplay/${enc(pid)}`, changes);
    if (!r.ok) { alert(t("Not saved: ") + (r.body?.error || r.status)); return; }
    airplay = r.body; render();
  };
  const states = { off: t("○ Off"), unavailable: t("○ Not installed in this add-on"), starting: t("… Starting"),
    ready: t("● Ready"), playing: t("● Playing"), paused: t("● Paused") };
  const box = el("div", { class: "box" }, el("h3", {}, "AirPlay"),
    el("p", { class: "muted" }, t("Each panel turned on here is an AirPlay speaker named after it, for iPhones, iPads and Macs on the home network (announced there only). Its sound goes from the add-on to the panel, and its media_player in Home Assistant shows what plays. Home Assistant's own sounds come first.")));
  if (!airplay.panels.length) box.append(el("p", { class: "muted" }, t("No panels yet.")));
  for (const p of airplay.panels) {
    const on = el("input", { type: "checkbox", checked: p.enabled, onchange: () => save(p.id, { enabled: on.checked }) });
    const password = el("input", { type: "password", autocomplete: "new-password", placeholder: p.password_set ? t("(unchanged)") : t("None") });
    box.append(el("div", { class: "request" },
      el("div", { class: "row" }, el("label", { class: "row grow" }, on, el("strong", {}, p.name)), el("span", { class: "muted" }, states[p.state] || p.state)),
      el("div", { class: "form", style: { marginTop: "6px" } }, el("label", {}, t("AirPlay password")), password),
      el("div", { class: "row", style: { marginTop: "6px" } },
        el("button", { class: "solid", onclick: () => { if (password.value) save(p.id, { password: password.value }); } }, t("Set password")),
        p.password_set ? el("button", { onclick: () => save(p.id, { password: "" }) }, t("Remove password")) : null)));
  }
  box.append(el("div", { class: "row", style: { marginTop: "10px" } }, el("button", { onclick: () => { airplay = null; render(); } }, t("Refresh"))));
  view.append(box);
}

// The intercom: doors (SIP door stations, or a phone for a test) register with the add-on's
// Asterisk under the accounts made here.
let intercom = null;
async function loadIntercom() {
  const r = await api("api/intercom");
  intercom = r.ok ? r.body : { error: r.status };
}

// Doors, servers and devices folded to one line; those opened stay open (after a save, and
// in this browser).
const icOpen = (() => {
  try { return new Set(JSON.parse(localStorage.getItem("wallpanel_intercom_open") || "[]")); } catch (_) { return new Set(); }
})();
function fold(key, summary, ...body) {
  const d = el("details", { class: "request fold" }, el("summary", { class: "row" }, ...summary), ...body);
  d.open = icOpen.has(key);
  d.addEventListener("toggle", () => {
    if (d.open) icOpen.add(key); else icOpen.delete(key);
    try { localStorage.setItem("wallpanel_intercom_open", JSON.stringify([...icOpen])); } catch (_) { /* no storage */ }
  });
  return d;
}

// A door's or device's camera and how it opens, edited the same way for both.
function cameraSelect(value) {
  const cams = (S.catalog?.entities || []).filter((e) => domainOf(e.entity_id) === "camera");
  return selectEl([["", t("— None —")], ...cams.map((e) => [e.entity_id, `${e.name} (${e.entity_id})`])], value || "");
}

function openFields(o) {
  o = o || { kind: "none" };
  const kind = selectEl([["none", t("Does not open from the panels")], ["dtmf", t("A code sent during the call (DTMF)")],
    ["http", t("The door's own address (Dahua, Akuvox…)")], ["entity", t("A device of Home Assistant's")]], o.kind || "none");
  const code = el("input", { value: o.code || "", placeholder: "#", dir: "ltr" });
  const url = el("input", { value: o.url || "", dir: "ltr", placeholder: "http://192.168.1.108/cgi-bin/accessControl.cgi?action=openDoor&channel=1&UserID=101&Type=Remote" });
  const user = el("input", { value: o.user || "", placeholder: "admin", dir: "ltr" });
  const password = el("input", { type: "password", autocomplete: "new-password", placeholder: o.password_set ? t("(unchanged)") : "" });
  const openers = (S.catalog?.entities || []).filter((e) => ["lock", "switch", "input_boolean", "script", "button", "input_button", "cover"].includes(domainOf(e.entity_id)));
  const entity = selectEl([["", t("— None —")], ...openers.map((e) => [e.entity_id, `${e.name} (${e.entity_id})`])], o.entity || "");
  const rows = el("div", { class: "form" });
  const show = () => {
    rows.replaceChildren(el("label", {}, t("Opens")), kind);
    if (kind.value === "dtmf") rows.append(el("label", {}, t("Code")), code);
    if (kind.value === "http") rows.append(el("label", {}, t("Address")), url, el("label", {}, t("User name")), user, el("label", {}, t("Password")), password);
    if (kind.value === "entity") rows.append(el("label", {}, t("Device")), entity);
  };
  kind.onchange = show;
  show();
  // What is saved: the password only when typed (else the add-on keeps it).
  const value = () => {
    const v = { kind: kind.value };
    if (v.kind === "dtmf") v.code = code.value.trim();
    if (v.kind === "http") { v.url = url.value.trim(); v.user = user.value.trim(); if (password.value) v.password = password.value; }
    if (v.kind === "entity") v.entity = entity.value;
    return v;
  };
  return { rows, value };
}

function renderIntercom(view) {
  if (!intercom) { view.append(el("p", { class: "muted" }, t("Loading…"))); loadIntercom().then(render); return; }
  if (!S.catalog) { view.append(el("p", { class: "muted" }, t("Loading Home Assistant's devices…"))); loadCatalog().then(render); return; }
  if (intercom.error) { view.append(el("p", { class: "muted" }, t("Not available"))); return; }
  const ic = intercom;
  const save = async (changes) => {
    const r = await put("api/intercom", changes);
    if (!r.ok) { alert(t("Not saved: ") + (r.body?.error || r.status)); return; }
    intercom = r.body; render();
  };
  // What is sent back for each door: the secret only when typed (else the add-on keeps it).
  const keepOpen = (o) => { const v = { ...(o || { kind: "none" }) }; delete v.password_set; return v; };
  const doors = () => ic.doors.map((d) => ({ id: d.id, name: d.name, panels: d.panels, camera: d.camera || null, open: keepOpen(d.open) }));
  const serverList = () => (ic.servers || []).map((s) => ({ id: s.id, name: s.name, server: s.server, user: s.user }));
  const devices = () => (ic.contacts || []).map((c) => ({ ...c, open: keepOpen(c.open) }));
  const change = (id, fields) => save({ doors: doors().map((x) => (x.id === id ? { ...x, ...fields } : x)) });
  const status = !ic.enabled ? t("Off") : !ic.installed ? t("Asterisk is not installed in this add-on") : ic.running ? t("● Running") : t("Starting…");
  const enabled = el("input", { type: "checkbox", checked: ic.enabled, onchange: () => save({ enabled: enabled.checked }) });
  view.append(el("div", { class: "box" }, el("h3", {}, t("Intercom")),
    el("p", { class: "muted" }, t("A call rings its panels; the first to answer takes it. Doors register with this add-on's SIP server, or are on an external server (Dahua VTO, Akuvox, a PBX…) where the add-on is one of the indoor monitors.")),
    el("div", { class: "form" },
      el("label", {}, t("On")), el("div", { class: "row" }, enabled, el("span", { class: "muted" }, status)),
      el("label", {}, t("SIP server")), el("div", {}, ...(ic.addresses.length ? ic.addresses : ["?"]).map((a) => el("div", {}, el("code", { style: { userSelect: "all" } }, `${a}:${ic.sip_port}`)))),
      el("label", {}, t("Test")), el("span", { class: "muted" }, t("Call {0} from a door: what you say comes back (echo), to check the sound's way.", ic.echo)),
      el("label", {}, t("SIP log")), el("label", { class: "row" },
        el("input", { type: "checkbox", checked: !!ic.sip_log, onchange: (e) => save({ sip_log: e.target.checked }) }),
        el("span", { class: "muted" }, t("Every SIP message in the add-on's log, to find why a call fails. Leave it off otherwise."))))));
  // Which panels ring: all (also those paired later), or those ticked. [onChange] saves at
  // once; else value() is read when saved.
  const ringsField = (panels, onChange) => {
    let now = panels == null ? null : [...panels];
    const all = el("input", { type: "checkbox", checked: now == null });
    const ticks = ic.panels.map((p) => el("input", { type: "checkbox", checked: now == null || now.includes(p.id), disabled: now == null,
      onchange: () => { now = ic.panels.filter((q, i) => ticks[i].checked).map((q) => q.id); if (onChange) onChange(now); } }));
    all.onchange = () => {
      now = all.checked ? null : ic.panels.map((p) => p.id);
      ticks.forEach((b) => { b.disabled = all.checked; b.checked = true; });
      if (onChange) onChange(now);
    };
    const rows = el("div", { class: "form", style: { marginTop: "6px" } },
      el("label", {}, t("Rings")), el("div", {}, el("label", { class: "row" }, all, t("All panels")),
        ...ic.panels.map((p, i) => el("label", { class: "row" }, ticks[i], p.name))));
    return { rows, value: () => now };
  };
  const servers = ic.servers || [];
  // The add-on's own server: doors that register with it.
  const box = el("div", { class: "box" }, el("h3", {}, t("This add-on's SIP server")),
    el("p", { class: "muted" }, t("A door station (or a phone, for a test) registers here with the account made for it.")));
  if (!ic.doors.length) box.append(el("p", { class: "muted" }, t("No doors yet.")));
  for (const d of ic.doors) {
    const state = d.registered ? t("● Registered from {0}", d.registered) : t("○ Not registered");
    const card = fold(`door:${d.id}`, [el("strong", { class: "grow" }, d.name), el("span", { class: "muted" }, state)],
      el("div", { class: "form", style: { marginTop: "6px" } },
        el("label", {}, t("User name")), el("code", { style: { userSelect: "all" } }, d.username),
        el("label", {}, t("Password")), el("code", { style: { userSelect: "all" } }, d.password)),
      ringsField(d.panels, (panels) => change(d.id, { panels })).rows);
    // Its camera, shown on the panel while it rings, and how it opens.
    const cam = cameraSelect(d.camera);
    const sub = cameraSelect(d.camera_sub);
    const opening = openFields(d.open);
    card.append(el("div", { class: "form", style: { marginTop: "6px" } }, el("label", {}, t("Camera")), cam,
      el("label", {}, t("Sub stream (shown first)")), sub), opening.rows,
      el("div", { class: "row", style: { marginTop: "6px" } }, el("button", { class: "solid", onclick: () =>
        change(d.id, { camera: cam.value || null, camera_sub: sub.value || null, open: opening.value() }) }, t("Save camera and opening"))));
    card.append(el("div", { class: "row", style: { marginTop: "6px" } },
      el("button", { onclick: async () => {
        if (!confirm(t("Make a new password? The door must be set up again with it."))) return;
        const r = await post(`api/intercom/doors/${enc(d.id)}/password`);
        if (r.ok) { intercom = r.body; render(); }
      } }, t("New password")),
      el("button", { onclick: () => { if (confirm(t("Remove {0}?", d.name))) save({ doors: doors().filter((x) => x.id !== d.id) }); } }, t("Remove"))));
    box.append(card);
  }
  const name = el("input", { placeholder: t("Front door") });
  box.append(fold("add:door", [el("strong", {}, "+ " + t("Add door"))], el("div", { class: "row", style: { marginTop: "6px" } }, name,
    el("button", { class: "solid", onclick: () => { if (name.value.trim()) save({ doors: [...doors(), { name: name.value.trim() }] }); } }, t("Add door")))));
  view.append(box);
  // External servers (Dahua VTO, Akuvox, a PBX...): the add-on is one of their monitors, and
  // their devices are set up under each by number: called by name from the panels, named
  // (with their camera) when they call.
  const kinds = [["door", t("Door station / doorbell")], ["panel", t("Monitor")], ["phone", t("Phone")]];
  const saveDevices = (list) => save({ contacts: list });
  const deviceForm = (c, onSave, label) => {
    const name = el("input", { value: c.name || "", placeholder: t("Front door") });
    const number = el("input", { value: c.number || "", placeholder: "8001", dir: "ltr" });
    const kind = selectEl(kinds, c.kind || "door");
    const cam = cameraSelect(c.camera);
    const sub = cameraSelect(c.camera_sub);
    const opening = openFields(c.open);
    const rings = ringsField(c.panels);
    return el("div", { style: { marginTop: "6px" } },
      el("div", { class: "form" }, el("label", {}, t("Name")), name, el("label", {}, t("Number")), number,
        el("label", {}, t("Kind")), kind, el("label", {}, t("Camera")), cam, el("label", {}, t("Sub stream (shown first)")), sub),
      opening.rows, rings.rows,
      el("div", { class: "row", style: { marginTop: "6px" } },
        el("button", { class: "solid", onclick: () => onSave({ ...c, name: name.value.trim(), number: number.value.trim(), kind: kind.value,
          camera: cam.value || null, camera_sub: sub.value || null, open: opening.value(), panels: rings.value() }) }, label),
        c.id ? el("button", { onclick: () => { if (confirm(t("Remove {0}?", c.name))) saveDevices(devices().filter((x) => x.id !== c.id)); } }, t("Remove")) : null));
  };
  const serverFields = (s) => {
    const name = el("input", { value: s.name || "", placeholder: "Dahua VTO" });
    const server = el("input", { value: s.server || "", placeholder: "192.168.1.108", dir: "ltr" });
    const user = el("input", { value: s.user || "", placeholder: "9901", dir: "ltr" });
    const secret = el("input", { type: "password", autocomplete: "new-password", placeholder: s.secret_set ? t("(unchanged)") : "" });
    const rows = el("div", { class: "form" }, el("label", {}, t("Name")), name, el("label", {}, t("Server's address")), server,
      el("label", {}, t("Account on the server")), user, el("label", {}, t("Password")), secret);
    const value = () => {
      const v = { ...(s.id ? { id: s.id } : {}), name: name.value.trim(), server: server.value.trim(), user: user.value.trim() };
      if (secret.value) v.secret = secret.value;
      return v;
    };
    return { rows, value };
  };
  const ext = el("div", { class: "box" }, el("h3", {}, t("External SIP servers")),
    el("p", { class: "muted" }, t("A server of its own (Dahua VTO, Akuvox, a PBX…): in its pages, add an indoor monitor with a password (Dahua: a room number such as 9901), enter it here, then add the server's devices with their numbers.")));
  for (const s of servers) {
    const state = s.registration === "Registered" ? t("● Linked to {0}", s.server) : t("○ Not linked") + (s.registration ? ` (${s.registration})` : "");
    const fields = serverFields(s);
    const mine = devices().filter((c) => c.via === s.id);
    const card = fold(`server:${s.id}`, [el("strong", { class: "grow" }, s.name),
      el("span", { class: "muted" }, t("{0} devices", mine.length) + " · " + state)],
      fields.rows,
      el("div", { class: "row", style: { marginTop: "6px" } },
        el("button", { class: "solid", onclick: () => save({ servers: serverList().map((x) => (x.id === s.id ? fields.value() : x)) }) }, t("Save")),
        el("button", { onclick: () => { if (confirm(t("Remove {0} and its devices?", s.name))) save({ servers: serverList().filter((x) => x.id !== s.id) }); } }, t("Remove"))),
      el("h4", {}, t("Its devices")));
    if (!mine.length) card.append(el("p", { class: "muted" }, t("No devices yet.")));
    const kindName = Object.fromEntries(kinds);
    for (const c of mine) {
      card.append(fold(`device:${c.id}`, [el("strong", { class: "grow" }, c.name), el("span", { class: "muted" }, `${c.number} · ${kindName[c.kind] || ""}`)],
        deviceForm(c, (v) => saveDevices(devices().map((x) => (x.id === c.id ? v : x))), t("Save"))));
    }
    card.append(fold(`add:device:${s.id}`, [el("strong", {}, "+ " + t("Add a device"))],
      deviceForm({ via: s.id }, (v) => saveDevices([...devices(), v]), t("Add"))));
    ext.append(card);
  }
  // Numbers typed on a panel: through one server, when there are several.
  if (servers.length > 1) {
    const via = selectEl(servers.map((s) => [s.id, s.name]), ic.numbers_via || servers[0].id);
    via.onchange = () => save({ numbers_via: via.value });
    ext.append(el("div", { class: "form", style: { marginTop: "6px" } }, el("label", {}, t("Numbers typed go through")), via));
  }
  const fresh = serverFields({});
  ext.append(fold("add:server", [el("strong", {}, "+ " + t("Add a server"))], el("div", { style: { marginTop: "6px" } }, fresh.rows),
    el("div", { class: "row", style: { marginTop: "6px" } }, el("button", { class: "solid", onclick: () => save({ servers: [...serverList(), fresh.value()] }) }, t("Add server")))),
    el("div", { class: "row", style: { marginTop: "10px" } }, el("button", { onclick: async () => { await loadIntercom(); render(); } }, t("Refresh"))));
  view.append(ext);
  // Panels: ready once connected with the intercom on (their account is made then).
  view.append(el("div", { class: "box" }, el("h3", {}, t("Panels")),
    ...(ic.panels.length ? ic.panels.map((p) => el("div", { class: "row" }, el("span", { class: "grow" }, p.name),
      el("span", { class: "muted" }, p.registered ? t("● Ready to ring") : t("○ Not connected to the intercom"))))
      : [el("p", { class: "muted" }, t("No panels paired."))])));
}

// The integration's secret, hidden until asked: Home Assistant gets it on its own, this is
// for setting the integration up by hand.
function integrationBox() {
  const value = el("code", { style: { userSelect: "all", wordBreak: "break-all" } }, "••••••••");
  const show = el("button", { onclick: async () => {
    const r = await api("api/integration/secret");
    if (!r.ok || !r.body?.secret) { alert(t("Not available")); return; }
    value.textContent = r.body.secret;
    show.remove();
  } }, t("Show"));
  return el("div", { class: "box" }, el("h3", {}, t("Home Assistant integration")),
    el("p", { class: "muted" }, t("Home Assistant gets this secret from the add-on on its own. Enter it only when adding the Wall Panel Hub integration by hand. With it, each panel is a speaker in Home Assistant.")),
    el("div", { class: "row" }, el("label", {}, t("Secret")), value, show));
}

// Where the built-in widgets read from: set once, every panel's widgets use it.
function renderSources(view) {
  if (!S.catalog) { view.append(el("p", { class: "muted" }, t("Loading Home Assistant's devices…"))); loadCatalog().then(render); return; }
  const chosen = { ...sources() };
  const rows = WIDGETS.filter((w) => w.source).map((w) => {
    // Only sensors of the right kind (a temperature, a power in W or kW); the one chosen stays listed.
    const fits = (e) => !w.units || e.device_class === w.deviceClass || w.units.includes(e.unit);
    const options = S.catalog.entities
      .filter((e) => domainOf(e.entity_id) === w.domain && (fits(e) || e.entity_id === chosen[w.source]))
      .sort((a, b) => device(a.entity_id).name.localeCompare(device(b.entity_id).name));
    const reading = (e) => e.unit ? ` · ${e.state} ${e.unit}` : "";
    const sel = selectEl([["", t("— None —")], ...options.map((e) => [e.entity_id, `${device(e.entity_id).name} (${e.entity_id})${reading(e)}`])], chosen[w.source] || "");
    sel.onchange = () => { if (sel.value) chosen[w.source] = sel.value; else delete chosen[w.source]; };
    return [el("label", {}, icon(w.icon), " ", SHAPES[w.card]), sel];
  });
  view.append(el("div", { class: "box" }, el("h3", {}, t("Widget sources")),
    el("p", { class: "muted" }, t("Set once for the project. A widget added on a panel or in the designer reads from here, so it adds no device to the panel. A widget with no source is not offered in +.")),
    el("div", { class: "form" }, rows.flat()),
    el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
      const r = await put("api/site", { sources: chosen });
      if (!r.ok) { alert(t("Not saved: ") + (r.body?.error || r.status)); return; }
      S.site = r.body; toast(t("Saved, and on every online panel.")); render();
    } }, t("Save")))));
}

// Device types: set once, used by every panel (icons, names; special cards later).
let devFilter = "cover";
function renderDevices(view) {
  const list = { ...devices() };
  const areaName = Object.fromEntries(S.catalog.areas.map((a) => [a.id, a.name]));
  const domains = [["cover", t("Covers and doors")], ["switch", t("Switches and outlets")], ["light", t("Lights")], ["binary_sensor", t("Sensors")], ["climate", t("Climate")]];
  const rows = S.catalog.entities.filter((e) => domainOf(e.entity_id) === devFilter);
  const dirty = { value: false };
  const save = el("button", { class: "solid", onclick: async () => {
    const r = await put("api/site", { devices: list });
    if (!r.ok) { alert(t("Not saved: ") + (r.body?.error || r.status)); return; }
    S.site = r.body; dirty.value = false; toast(t("Saved, and on every panel.")); render();
  } }, t("Save"));
  const setField = (id, key, value) => {
    const d = { ...(list[id] || {}) };
    if (value) d[key] = value; else delete d[key];
    if (Object.keys(d).length) list[id] = d; else delete list[id];
    dirty.value = true;
  };
  const table = el("table", { class: "devtable" },
    el("tr", {}, el("th", {}, ""), el("th", {}, t("Device in HA")), el("th", {}, t("Room")), DEVICE_TYPES[devFilter] ? el("th", {}, t("Type")) : null, el("th", {}, t("Name on the panels")), el("th", {}, t("Icon"))));
  for (const e of rows) {
    const d = list[e.entity_id] || {};
    const types = DEVICE_TYPES[devFilter];
    const haType = types?.find(([v]) => v === e.device_class)?.[1] || e.device_class || t("General");
    const probe = () => ({ card: R.kinds[kindOf(e.entity_id)]?.default || "sensor.value", entities: [e.entity_id] });
    const preview = icon(cardIcon(probe()), { style: { fontSize: "22px" } });
    const refreshIcon = () => preview.replaceWith(Object.assign(icon(cardIcon(probe()), { style: { fontSize: "22px" } })));
    table.append(el("tr", {},
      el("td", {}, preview),
      el("td", {}, e.name),
      el("td", { class: "muted" }, areaName[e.area_id] || "—"),
      types ? el("td", {}, (() => {
        const s = selectEl([["", t("From HA ({0})", haType)], ...types], d.type || "");
        s.onchange = () => { setField(e.entity_id, "type", s.value); S.site.devices = list; };
        return s;
      })()) : null,
      el("td", {}, el("input", { value: d.name || "", placeholder: e.name, onchange: (ev) => { setField(e.entity_id, "name", ev.target.value.trim()); S.site.devices = list; } })),
      el("td", {}, el("input", { value: d.icon || "", placeholder: "mdi:...", dir: "ltr",
        onchange: (ev) => { const v = ev.target.value.trim(); if (v && !validIcon(v)) { alert(t("Unknown icon. For example: mdi:garage")); return; } setField(e.entity_id, "icon", v); S.site.devices = list; } })),
    ));
  }
  view.append(el("div", { class: "box", style: { maxWidth: "1100px" } },
    el("h3", {}, t("Device types")),
    el("p", { class: "muted" }, t("Set each device's type and name once, and every panel shows it with the same icon and name. A blank field takes Home Assistant's.")),
    chips(domains.map(([v, l]) => [v, l]), devFilter, (v) => { devFilter = v; render(); }),
    el("div", { style: { marginTop: "12px" } }, rows.length ? table : el("p", { class: "muted" }, t("No devices of this kind."))),
    el("div", { class: "row", style: { marginTop: "14px" } }, save)));
}

// ---------- new panel setup: approve, rooms, send ----------

function wizard(req) {
  const st = { step: 1, pid: req.panel_id, name: req.name || t("Panel"), area: "", rooms: new Set() };
  const bar = () => el("div", { class: "steps" }, [1, 2, 3].map((n) => el("span", { class: n <= st.step ? "done" : "" })));
  function show() {
    if (st.step === 1) {
      sheet(bar(), el("h3", {}, t("1. Approve")),
        el("p", { class: "muted" }, t("Approve only if this code is the one on the panel.")),
        el("div", { class: "bigcode" }, req.code),
        el("p", { class: "muted", style: { textAlign: "center" } }, `${req.name || ""} ${req.model ? "· " + req.model : ""}`),
        el("div", { class: "actions" },
          el("button", { class: "solid", onclick: async () => {
            const r = await post(`api/pending/${enc(req.request_id)}/approve`);
            if (!r.ok) { alert(t("The request expired. The panel will ask to pair again.")); closeSheet(); refresh(); return; }
            st.pid = r.body.panel_id || st.pid;
            st.step = 2; show(); refresh();
          } }, t("Code matches, approve")),
          el("button", { onclick: async () => { await post(`api/pending/${enc(req.request_id)}/reject`); closeSheet(); refresh(); } }, t("Reject")),
          el("span", { class: "spacer" }), el("button", { onclick: closeSheet }, t("Later"))));
      return;
    }
    if (st.step === 2) {
      const name = el("input", { value: st.name, oninput: (e) => { st.name = e.target.value; } });
      const place = selectEl([["", t("— Choose —")], ...S.catalog.areas.map((a) => [a.id, a.name])], st.area);
      place.onchange = () => { st.area = place.value; if (st.area) st.rooms.add(st.area); show(); };
      const floors = [...S.catalog.floors, { id: null, name: S.catalog.floors.length ? t("No floor") : t("Rooms") }];
      const boxes = floors.map((f) => {
        const areas = S.catalog.areas.filter((a) => (a.floor_id || null) === f.id);
        if (!areas.length) return null;
        return el("div", { class: "floorbox" }, el("b", {}, f.name), el("div", { class: "checks" }, areas.map((a) =>
          el("label", {}, el("input", { type: "checkbox", checked: st.rooms.has(a.id), onchange: (e) => { e.target.checked ? st.rooms.add(a.id) : st.rooms.delete(a.id); } }),
            a.name, el("span", { class: "muted" }, `(${controlsIn(a.id).length})`)))));
      });
      sheet(bar(), el("h3", {}, t("2. Name and rooms")),
        field(t("Panel name"), name),
        field(t("Where it is mounted"), place),
        field(t("Rooms on the panel (each with its devices)"), el("div", {}, boxes)),
        el("div", { class: "actions" },
          el("button", { class: "solid", onclick: () => { if (!st.rooms.size) { alert(t("Choose at least one room.")); return; } st.step = 3; show(); } }, t("Next")),
          el("span", { class: "spacer" }), el("button", { onclick: closeSheet }, t("Later"))));
      return;
    }
    // Step 3: build the layout and send it.
    const layout = { schema: 2, version: 1, pages: [] };
    const ordered = S.catalog.areas.map((a) => a.id).filter((id) => st.rooms.has(id));
    if (st.area && ordered.includes(st.area)) ordered.splice(ordered.indexOf(st.area), 1), ordered.unshift(st.area);
    addRoomPages(layout, ordered);
    const count = layout.pages.reduce((n, p) => n + p.cards.length, 0);
    sheet(bar(), el("h3", {}, t("3. Send")),
      el("p", {}, t("{0} rooms, {1} cards.", layout.pages.length, count)),
      el("ul", {}, layout.pages.map((p) => el("li", {}, `${p.title}: ${p.cards.length}`))),
      el("p", { class: "muted" }, t("You can change everything afterwards in Design.")),
      el("div", { class: "actions" },
        el("button", { class: "solid", onclick: async () => {
          const pid = st.pid;
          if (st.name.trim()) await post(`api/panels/${enc(pid)}/rename`, { name: st.name.trim() });
          if (st.area) await post(`api/panels/${enc(pid)}/area`, { area_id: st.area });
          await put(`api/editor/panels/${enc(pid)}/draft`, { layout, base_version: null });
          const r = await post(`api/editor/panels/${enc(pid)}/send`, { force: true });
          closeSheet();
          await refresh(); await loadEditor();
          if (!r.ok) { alert(t("Not sent: ") + (r.body?.error || r.status)); }
          else toast(r.body.online ? t("The panel is ready.") : t("Sent. The panel gets it when it connects."));
          openPanel(pid);
        } }, t("Send to panel")),
        el("button", { onclick: () => { st.step = 2; show(); } }, t("Back"))));
  }
  show();
}

// ---------- wiring ----------

let lastNav = "";
async function refresh() {
  await loadOverview();
  // Panels approved elsewhere or removed.
  if (S.overview.panels.some((p) => !S.editor[p.id])) await loadEditor();
  // Redraw only on a change: a redraw under the pointer can swallow a click.
  const key = JSON.stringify([S.overview, Object.values(S.work).map((w) => w.unsent)]);
  if (key !== lastNav) { lastNav = key; renderNav(); }
  if (S.view.kind === "panel" && !S.overview.panels.some((p) => p.id === S.view.pid)) { S.view = { kind: "home" }; renderView(); }
}

document.addEventListener("keydown", (e) => {
  if (!$("veil").hidden || S.view.kind !== "panel" || S.view.tab !== "design") return;
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("veil").hidden) closeSheet(); });

(async () => {
  const cp = await fetch("static/vendor/mdi/codepoints.json").then((r) => r.json()).catch(() => ({}));
  CODEPOINTS = cp;
  const reg = await api("api/editor/cards");
  if (reg.ok) R = reg.body;
  await Promise.all([loadOverview(), loadEditor(), loadCatalog(false), loadSite()]);
  // Straight to the only panel, or the first one.
  if (S.overview.panels.length) await openPanel(S.overview.panels[0].id);
  else render();
  setInterval(refresh, 4000);
})();
