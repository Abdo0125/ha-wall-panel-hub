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
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
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
const sizeOf = (c) => c.size || R.cards[c.card]?.default_size || "1x1";
const area = (s) => s.split("x").reduce((x, y) => x * y, 1);
const smallestSize = (type) => [...(R.cards[type]?.sizes || ["1x1"])].sort((a, b) => area(a) - area(b))[0];

// Plain names: the installer never sees card ids or entity ids.
const SHAPES = {
  "light.toggle": "مفتاح", "light.dimmer": "تعتيم", "light.color": "ألوان", "light.group": "مجموعة",
  "switch.toggle": "مفتاح", "switch.outlet": "مقبس مع الاستهلاك",
  "cover.buttons": "أزرار", "cover.position": "موضع", "cover.tilt": "ميلان", "cover.group": "مجموعة",
  "climate.compact": "مختصرة", "climate.thermostat": "منظم حرارة",
  "fan.toggle": "مفتاح", "fan.speed": "سرعات", "lock.lock": "قفل",
  "scene.action": "زر", "scene.bar": "شريط مشاهد", "script.action": "زر", "button.action": "زر",
  "sensor.value": "قيمة", "sensor.multi": "عدة قيم", "sensor.binary": "حالة", "app.launch": "تطبيق", "room.card": "صورة الغرفة", "room.summary": "تفاصيل الغرفة", "widget.clock": "الساعة",
  "widget.weather": "الطقس", "widget.android": "ودجة أندرويد", "widget.temperature": "الحرارة الخارجية", "widget.power": "الاستهلاك الآن",
};
// Sizes in cells, columns × rows, as the panel names them.
// Isolated left to right, so 2×1 reads 2×1 on an Arabic page too.
const SIZES = Object.fromEntries(["1x1", "2x1", "2x2", "3x2", "4x2"].map((v) => [v, "⁦" + v.replace("x", "×") + "⁩"]));
// Each kind of device pairs with its own app and has its own rules (docs/ui-plan.md, 5.3).
const PLATFORMS = { android: "شاشة أندرويد", esp32: "شاشة ESP32", phone: "هاتف" };
const TYPES = { android_11: 'أندرويد 11"', android_4: 'أندرويد 4"', esp32_4: 'ESP32 4"', phone: "هاتف" };
const OPTION_NAMES = {
  min_brightness: "أدنى سطوع (%)", show_percent: "إظهار النسبة", dimmer: "تعتيم المجموعة",
  power_entity: "حساس الاستهلاك", step: "خطوة الحرارة", package: "التطبيق (اسم الحزمة)", return_seconds: "العودة بعد (ثانية)",
};
const KIND_NAMES = {
  light: "إنارة", switch: "مفاتيح", cover: "ستائر", climate: "مكيفات", fan: "مراوح", lock: "أقفال",
  scene: "مشاهد", script: "سكربتات", button: "أزرار", sensor: "حساسات",
};

// Device types per domain (HA's device_class names), set once for the project.
const DEVICE_TYPES = {
  cover: [["shutter", "ستارة معدنية"], ["shade", "ستارة لفّ"], ["curtain", "ستارة قماش"], ["blind", "شيش"],
          ["window", "نافذة"], ["garage", "غراج"], ["gate", "بوابة"], ["door", "باب"], ["awning", "مظلة"], ["damper", "فتحة تهوية"]],
  switch: [["switch", "مفتاح"], ["outlet", "مقبس"], ["light", "إنارة"], ["heater", "مدفأة"], ["boiler", "سخان ماء"],
           ["fan", "مروحة"], ["pump", "مضخة"], ["valve", "صمام"]],
  binary_sensor: [["door", "باب"], ["window", "نافذة"], ["garage_door", "باب غراج"], ["motion", "حركة"], ["occupancy", "تواجد"],
                  ["smoke", "دخان"], ["moisture", "تسرب ماء"], ["gas", "غاز"]],
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
  if (!spec) return "نوع بطاقة غير معروف.";
  const { min, max } = spec.entities;
  if (c.entities.length < min || c.entities.length > max) return `تقبل ${min} إلى ${max} أجهزة.`;
  const domains = cardDomains(c.card);
  if (c.entities.some((e) => !domains.includes("*") && !domains.includes(domainOf(e)))) return "جهاز لا يناسب هذا الشكل.";
  if (c.size && !spec.sizes.includes(c.size)) return "حجم غير مسموح لهذا الشكل.";
  for (const [key, opt] of Object.entries(spec.options || {})) {
    if (opt.required && (c.options?.[key] === undefined || c.options[key] === "")) return `${OPTION_NAMES[key] || key} مطلوب.`;
  }
  return null;
}

function setCardType(c, type) {
  const spec = R.cards[type];
  c.card = type;
  if (!spec.sizes.includes(c.size)) c.size = spec.default_size;
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
  page: 0,
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
  if (kind === "app") return "mdi:apps";
  if (c.card === "widget.android") return "mdi:widgets-outline";
  const widget = WIDGETS.find((w) => w.card === c.card);
  if (widget) return widget.icon;
  const d = c.entities[0] ? device(c.entities[0]) : null;
  if (d?.icon && c.entities.length === 1) return d.icon;
  const t = d?.type;
  const m = {
    light: c.card === "light.group" ? "lightbulb-group-outline" : "lightbulb-outline",
    switch: { outlet: "power-socket-eu", heater: "radiator", boiler: "water-boiler", pump: "pump", fan: "fan", light: "lightbulb-outline", valve: "valve" }[t]
      || (c.card === "switch.outlet" ? "power-socket-eu" : domainOf(c.entities[0] || "") === "input_boolean" ? "toggle-switch-off-outline" : "toggle-switch-variant-off"),
    cover: { blind: "blinds", curtain: "curtains-closed", garage: "garage", gate: "gate", door: "door-closed", window: "window-closed",
             awning: "awning-outline", shade: "roller-shade-closed", damper: "valve-closed" }[t] || "window-shutter",
    climate: "thermostat", fan: "fan", lock: "lock", scene: "palette-outline", script: "script-text-outline",
    button: "gesture-tap-button",
    sensor: domainOf(c.entities[0] || "") === "binary_sensor"
      ? ({ door: "door-closed", garage_door: "garage", window: "window-closed-variant", motion: "motion-sensor", smoke: "smoke-detector-variant", moisture: "water-off", gas: "gas-cylinder" }[t] || "checkbox-blank-circle-outline")
      : ({ temperature: "thermometer", humidity: "water-percent", power: "flash", energy: "lightning-bolt", illuminance: "brightness-5", battery: "battery" }[t] || "gauge"),
  }[kind];
  return "mdi:" + (m || "help-circle-outline");
}

function cardLabel(c) {
  if (c.label) return c.label;
  if (c.card === "widget.android") return androidWidgetName(c.options?.provider);
  if (c.card === "app.launch") return appName(c.options?.package);
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
  if (!layout.pages.length) out.push("لا توجد غرف.");
  for (const page of layout.pages) {
    if (!page.title?.trim()) out.push("غرفة بلا اسم.");
    for (const c of page.cards) {
      const p = cardProblem(c);
      if (p) out.push(`${page.title} ← ${cardLabel(c)}: ${p}`);
    }
  }
  return out;
}

async function send(force) {
  const pid = S.view.pid;
  const issues = problems(L());
  if (issues.length) { alert("لا يمكن الإرسال:\n" + issues.join("\n")); return; }
  clearTimeout(saveTimers[pid]);
  await saveDraft(pid);
  const r = await post(`api/editor/panels/${enc(pid)}/send`, { force: !!force });
  if (r.status === 409) {
    if (confirm("عُدّلت بطاقات هذه الشاشة من الشاشة نفسها بعد بدء تعديلاتك.\nإرسال تعديلاتك فوقها؟ (إلغاء = تجاهل تعديلاتك)")) return send(true);
    return discard(true);
  }
  if (!r.ok) { alert("رفض الـ Hub التخطيط: " + (r.body?.error || r.status)); return; }
  S.work[pid].unsent = false;
  await loadEditor();
  toast(r.body.online ? "أُرسل وطُبّق على الشاشة." : "أُرسل. الشاشة غير متصلة الآن، وستستلمه عند اتصالها.");
  render();
}

async function discard(noAsk) {
  const pid = S.view.pid;
  if (!noAsk && !confirm("تجاهل كل التعديلات غير المرسلة لهذه الشاشة؟")) return;
  clearTimeout(saveTimers[pid]);
  await api(`api/editor/panels/${enc(pid)}/draft`, { method: "DELETE" });
  delete S.work[pid];
  await loadEditor();
  S.page = 0; S.sel = null;
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
    nav.append(el("h2", {}, "شاشات تطلب الربط"));
    for (const r of o.pending) {
      nav.append(el("div", { class: "request" },
        el("div", { class: "row" }, el("span", { class: "code" }, r.code), el("span", { class: "muted" }, r.name)),
        el("div", { class: "row", style: { marginTop: "6px" } },
          el("button", { class: "primary", onclick: () => wizard(r) }, "إعداد"),
          el("button", { onclick: async () => { await post(`api/pending/${enc(r.request_id)}/reject`); await refresh(); } }, "رفض")),
      ));
    }
  }
  nav.append(el("h2", {}, "الشاشات"));
  if (!o.panels.length) nav.append(el("p", { class: "muted", style: { margin: "0 6px" } }, "لا توجد شاشات. شغّل شاشة جديدة في نفس الشبكة وستظهر هنا تطلب الربط."));
  for (const p of o.panels) {
    const active = S.view.kind === "panel" && S.view.pid === p.id;
    nav.append(el("div", { class: "navitem" + (active ? " active" : ""), onclick: () => openPanel(p.id) },
      el("span", { class: "dot " + (p.online ? "on" : "off") }, p.online ? "●" : "○"),
      el("span", { class: "grow" }, p.name),
      S.work[p.id]?.unsent ? el("span", { title: "تعديلات غير مرسلة", style: { color: "var(--accent)" } }, "✱") : null,
    ));
  }
  if (o.discovered.length) {
    nav.append(el("h2", {}, "شاشات أخرى في الشبكة"));
    for (const d of o.discovered) nav.append(el("div", { class: "navitem", title: d.address }, el("span", { class: "dot off" }, "○"), el("span", { class: "grow muted" }, d.name)));
  }
  nav.append(el("h2", {}, "المشروع"));
  nav.append(el("div", { class: "navitem" + (S.view.kind === "project" ? " active" : ""), onclick: () => openProject("general") },
    icon("mdi:home-city-outline", { style: { fontSize: "18px" } }), el("span", { class: "grow" }, S.site?.name || "المشروع")));
}

async function openPanel(pid, tab = "design") {
  S.view = { kind: "panel", pid, tab };
  S.page = 0; S.sel = null; S.menu = false; S.floorTab = "";
  if (!S.configs[pid]) await loadConfig(pid);
  render();
}

function openProject(tab) {
  S.view = { kind: "project", tab };
  render();
}

function renderView() {
  const view = $("view");
  view.replaceChildren();
  if (S.view.kind === "panel" && S.overview.panels.some((p) => p.id === S.view.pid)) return renderPanel(view);
  if (S.view.kind === "project") return renderProject(view);
  view.append(el("div", { class: "box" },
    el("h3", {}, "مرحباً"),
    el("p", { class: "muted" }, "اختر شاشة من القائمة لتصميمها، أو أعدّ شاشة جديدة تطلب الربط، أو افتح المشروع لضبط كلمات السر وأنواع الأجهزة."),
    S.catalogError ? el("p", { class: "msg error" }, "تعذّرت قراءة الأجهزة من Home Assistant: " + S.catalogError) : null,
  ));
}

// ---------- rendering: one panel ----------

function renderPanel(view) {
  const pid = S.view.pid;
  const p = S.overview.panels.find((x) => x.id === pid);
  const w = W();
  const tabs = [["design", "التصميم"], ["settings", "الإعدادات"], ["access", "الصلاحيات"]];
  view.append(el("div", { class: "titlebar" },
    el("h2", {}, p.name),
    el("span", { class: "dot " + (p.online ? "on" : "off") }, p.online ? "● متصلة" : "○ غير متصلة"),
    el("div", { class: "tabs" }, tabs.map(([id, label]) =>
      el("button", { class: S.view.tab === id ? "active" : "", onclick: () => { S.view.tab = id; render(); } }, label))),
    el("span", { class: "spacer" }),
    S.view.tab === "design" ? [
      el("button", { disabled: !w?.undo.length, onclick: undo, title: "Ctrl+Z" }, "↶"),
      el("button", { disabled: !w?.redo.length, onclick: redo, title: "Ctrl+Y" }, "↷"),
      el("button", { class: "danger", disabled: !w?.unsent, onclick: () => discard() }, "تجاهل"),
      el("button", { class: w?.unsent ? "solid" : "primary", onclick: () => send(false) }, w?.unsent ? "إرسال للشاشة ✱" : "إرسال للشاشة"),
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
  return { id: "__dashboard", title: "لوحتي", icon: "mdi:view-dashboard-outline", cards: l.dashboard ? l.dashboard.cards : defaultDashboard(l) };
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
  if (S.catalogError) view.append(el("p", { class: "msg error" }, "تعذّرت قراءة الأجهزة من Home Assistant: " + S.catalogError));
  const eff = S.configs[S.view.pid]?.effective || {};
  const rtl = eff.lang === "ar";
  if (S.page !== DASH) S.page = Math.min(S.page, Math.max(0, layout.pages.length - 1));
  // No room yet: the rooms menu, where rooms are added.
  if (!layout.pages.length && S.page !== DASH) S.menu = true;
  const page = curPage(layout);

  // Top bar, as on the panel: the page's name with ▾ opens the rooms menu; ⌂ my dashboard.
  const now = new Date();
  const title = S.menu ? "الغرف" : page?.title || "";
  const top = el("div", { class: "dtop" },
    el("span", { class: "dtitle", title: "قائمة الغرف", onclick: () => { S.menu = !S.menu; S.sel = null; render(); } },
      S.menu ? null : icon(page?.icon || "mdi:home-outline", { style: { fontSize: "20px" } }),
      el("span", {}, title), icon(S.menu ? "mdi:chevron-up" : "mdi:chevron-down", { style: { fontSize: "20px" } })),
    el("span", { class: "clock" }, `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`),
    S.page !== DASH || S.menu
      ? el("span", { class: "dhome", title: "لوحتي", onclick: () => { S.page = DASH; S.menu = false; S.sel = null; render(); } }, icon("mdi:home-outline"))
      : null);

  const main = el("div", { class: "dmain" }, top);
  if (S.menu) {
    renderRoomsMenu(main);
  } else {
    const screensBox = el("div", { class: "screens" });
    const { placed, screens, grids } = pack(page.cards);
    for (let s = 0; s < screens; s++) {
      const grid = el("div", { class: "screen" });
      grid.style.height = "100%";
      if (screens > 1) grid.append(el("span", { class: "screenlabel" }, `الشاشة ${s + 1} من ${screens} (سحب أفقي)`));
      for (const pl of placed.filter((x) => x.screen === s)) grid.append(cardCell(pl, page));
      for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) {
        if (grids[s][r][c]) continue;
        grid.append(emptyCell(s, c, r, true));
      }
      screensBox.append(grid);
    }
    // Room for one more screen after the last, as on the panel in edit mode.
    const grid = el("div", { class: "screen" });
    grid.append(el("span", { class: "screenlabel" }, `الشاشة ${screens + 1} (جديدة، تُحفظ عند إضافة بطاقة)`));
    for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) grid.append(emptyCell(screens, c, r, true));
    screensBox.append(grid);
    main.append(screensBox);
  }
  const device = el("div", { class: "device" + (eff.theme === "dark" ? " dark" : ""), dir: rtl ? "rtl" : "ltr" }, main);
  view.append(device);
  view.append(el("p", { class: "muted" }, S.menu
    ? "الطوابق تبويبات: القلم للاسم والأيقونة والحذف، و+ لإضافة طابق. اسحب الغرفة إلى أي خانة أو فوق غرفة أخرى لتبديل مكانيهما، و+ في الخانة الفارغة يضيف غرفة. القلم على الغرفة لاسمها وشكلها وحجمها، والضغط عليها يفتحها."
    : "اضغط اسم الصفحة لقائمة الغرف. اضغط + لإضافة جهاز أو تطبيق أو ودجة، واضغط بطاقة لتغيير شكلها وحجمها واسمها. اسحب البطاقة إلى أي خلية فارغة لتضعها هناك، أو فوق بطاقة أخرى لتبديل مكانيهما."));
  const issues = problems(layout).filter((x) => x !== "لا توجد غرف.");
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
  const node = el("div", {
    class: cls, draggable: "true", title: known ? "" : "جهاز غير موجود في Home Assistant",
    style: { gridColumn: `${pl.col + 1} / span ${pl.w}`, gridRow: `${pl.row + 1} / span ${pl.h}` },
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
    class: "cell" + (open ? " empty" : ""), title: open ? "إضافة بطاقة" : "",
    style: { gridColumn: `${col + 1}`, gridRow: `${row + 1}` },
    onclick: () => { if (open) addSheet(screen, col, row); },
    ondragover: (ev) => { if (dragFrom !== null && open) { ev.preventDefault(); node.classList.add("over"); } },
    ondragleave: () => node.classList.remove("over"),
    ondrop: (ev) => {
      ev.preventDefault();
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
    if (opt.type === "bool") return field(label, chips([["", "افتراضي"], ["true", "نعم"], ["false", "لا"]], value === undefined ? "" : String(value), (v) => set(v === "" ? undefined : v === "true")));
    if (opt.type === "enum") return field(label, chips([["", "افتراضي"], ...opt.values.map((v) => [v, v])], value ?? "", (v) => set(v || undefined)));
    if (opt.type === "int") return field(label, el("input", { type: "number", min: opt.min, max: opt.max, value: value ?? "", placeholder: opt.default ?? "",
      onchange: (e) => set(e.target.value === "" ? undefined : Number(e.target.value)) }));
    if (opt.type === "entity") {
      const s = el("select", { onchange: (e) => set(e.target.value || undefined) },
        el("option", { value: "" }, "— بدون —"),
        S.catalog.entities.filter((e) => opt.domains.includes(domainOf(e.entity_id))).map((e) =>
          el("option", { value: e.entity_id, selected: e.entity_id === value }, device(e.entity_id).name)));
      return field(label, s);
    }
    return field(label, el("input", { value: value ?? "", dir: "ltr", onchange: (e) => set(e.target.value.trim() || undefined) }));
  });
  sheet(
    el("h3", {}, cardLabel(c)),
    el("p", { class: "muted" }, d ? `${KIND_NAMES[kindOfCard(c.card)] || ""} · ${S.catalog.areas.find((a) => a.id === d.area)?.name || "بدون غرفة"}` : SHAPES[c.card]),
    // Widgets are each their own thing: no shape to switch to.
    variants.length > 1 && kindOfCard(c.card) !== "widget" ? field("الشكل", chips(variants.map((v) => [v, SHAPES[v] || v]), c.card, (v) => edit(() => setCardType(c, v)))) : null,
    field("الحجم", chips(R.sizes.map((v) => [v, SIZES[v], !spec.sizes.includes(v)]), sizeOf(c), (v) => edit(() => { c.size = v; }))),
    field("الاسم على الشاشة", el("input", { value: c.label || "", placeholder: d?.name || "",
      onchange: (e) => edit(() => { const v = e.target.value.trim(); v ? c.label = v : delete c.label; }) })),
    field("الأيقونة", iconInput(c.icon, cardIcon({ ...c, icon: undefined }), (v) => edit(() => { v && validIcon(v) ? c.icon = v : delete c.icon; }))),
    field("طلب تأكيد قبل التنفيذ", chips([["", "تلقائي"], ["true", "نعم"], ["false", "لا"]], c.confirm === undefined ? "" : String(c.confirm),
      (v) => edit(() => { v === "" ? delete c.confirm : c.confirm = v === "true"; }))),
    ...optionFields,
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: () => { closeSheet(); S.sel = null; render(); } }, "تم"),
      el("span", { class: "spacer" }),
      el("button", { class: "danger", onclick: () => { change((l) => { const cs = pageCards(l); cs.splice(cs.indexOf(c), 1); S.sel = null; }); closeSheet(); } }, "حذف البطاقة")),
  );
}

// ---------- adding a card: one window, a list per kind (rooms, devices, apps, widgets) ----------

const ADD_TABS = [
  ["rooms", "الغرف", "mdi:floor-plan"],
  ["devices", "الأجهزة", "mdi:lightbulb-group-outline"],
  ["apps", "التطبيقات", "mdi:apps"],
  ["widgets", "الودجات", "mdi:widgets-outline"],
];
// The window remembers where it was between openings.
const addState = { tab: "rooms", room: null, kind: "", area: "", control: true, wkind: "" };

// An allowed app's name: as set in the add-on, else as the panel reported it.
function appName(pkg) {
  const cfg = S.configs[S.view.pid];
  return (cfg?.effective.apps || []).find((a) => a.package === pkg)?.label
    || (cfg?.installed_apps || []).find((a) => a.package === pkg)?.label || pkg;
}

function addSheet(screen, col, row) {
  const page = curPage(L());
  const cfg = S.configs[S.view.pid];
  if (!cfg) loadConfig(S.view.pid).then(() => { if (!$("veil").hidden) draw(); });
  const areaName = Object.fromEntries(S.catalog.areas.map((a) => [a.id, a.name]));
  if (addState.room === null || !areaName[addState.room]) addState.room = areaName[page.id] ? page.id : (S.catalog.areas[0]?.id || "");
  const search = el("input", { placeholder: "", oninput: () => fillList() });
  const nav = el("ul", { class: "addnav" });
  const filters = el("div", { class: "addfilters" });
  const body = el("div", { class: "addbody" });

  // Puts new cards in the chosen cell, the next ones in the first free spots.
  const add = (make) => {
    change((l) => {
      const cards = pageCards(l);
      let last = null;
      for (const m of [].concat(make(l, cards))) {
        // Added by hand: its smallest size (a room's details card: wide, as asked).
        if (m.card !== "room.summary") m.size = smallestSize(m.card);
        cards.push(m);
        placeCard(cards, cards.length - 1, screen, col, row);
        last = m;
      }
      S.sel = last ? cards.indexOf(last) : null;
    });
    closeSheet();
  };
  const entityCard = (e) => (l, cards) => newCard(l, e.entity_id, cards);
  const onPage = () => new Set(page.cards.flatMap((c) => c.entities));
  const q = () => search.value.trim().toLowerCase();
  const hits = (...texts) => !q() || texts.some((t) => (t || "").toLowerCase().includes(q()));

  const row_ = (iconName, name, note, onPick, off) => el("li", { class: off ? "off" : "", onclick: off ? null : onPick },
    icon(iconName), el("span", { class: "grow" }, name), el("span", { class: "muted" }, note || ""));
  const listOf = (items, empty) => items.length ? el("ul", { class: "picklist" }, items) : el("p", { class: "muted" }, q() ? "لا نتائج." : empty);
  const entityRow = (e, note) => row_(cardIcon({ card: R.kinds[kindOf(e.entity_id)].default, entities: [e.entity_id] }),
    device(e.entity_id).name, note, () => add(entityCard(e)));
  const kinds = (list) => {
    const n = {};
    for (const e of list) { const k = kindOf(e.entity_id); n[k] = (n[k] || 0) + 1; }
    return Object.keys(KIND_NAMES).filter((k) => n[k]).map((k) => [k, `${KIND_NAMES[k]} ${n[k]}`]);
  };
  const kindChips = (list, onPick) => chips([["", `الكل ${list.length}`], ...kinds(list)], addState.kind, (v) => { addState.kind = v; onPick(); });

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
    widgets: () => builtins().length + androids().length,
  };

  function draw() {
    nav.replaceChildren(...ADD_TABS.map(([id, label, ic]) => el("li", { class: addState.tab === id ? "on" : "", onclick: () => { addState.tab = id; search.value = ""; draw(); } },
      icon(ic), el("span", { class: "grow" }, label), el("span", { class: "n" }, String(counts[id]())))));
    search.placeholder = { rooms: "بحث باسم الغرفة أو الجهاز", devices: "بحث باسم الجهاز", apps: "بحث باسم التطبيق", widgets: "بحث باسم الودجة" }[addState.tab];
    fillList();
  }

  function fillList() {
    const t = addState.tab;
    filters.replaceChildren();
    if (t === "rooms") {
      // Rooms on one side, the chosen room's devices on the other.
      const ents = free();
      const inRoom = (id) => ents.filter((e) => e.area_id === id);
      const rooms = S.catalog.areas.filter((a) => hits(a.name) || inRoom(a.id).some((e) => hits(device(e.entity_id).name)));
      if (rooms.length && !rooms.some((a) => a.id === addState.room)) addState.room = rooms[0].id;
      const roomList = el("ul", { class: "picklist rooms" }, rooms.map((a) => el("li", { class: a.id === addState.room ? "on" : "", onclick: () => { addState.room = a.id; addState.kind = ""; fillList(); } },
        icon(/^mdi:[a-z0-9-]+$/.test(a.icon || "") ? a.icon : "mdi:floor-plan"), el("span", { class: "grow" }, a.name),
        el("span", { class: "muted" }, a.id === page.id ? "هذه الغرفة" : String(inRoom(a.id).filter((e) => !addState.control || e.controllable).length)))));
      const all = inRoom(addState.room).filter((e) => !addState.control || e.controllable);
      const shown = all.filter((e) => (!addState.kind || kindOf(e.entity_id) === addState.kind) && (hits(device(e.entity_id).name, e.entity_id) || hits(areaName[addState.room])));
      filters.append(kindChips(all, fillList), controlToggle());
      const addAll = shown.length > 1 ? el("button", { onclick: () => add((l, cards) => {
        const out = [];
        for (const e of shown) out.push(newCard(l, e.entity_id, [...cards, ...out]));
        return out;
      }) }, `إضافة الكل (${shown.length})`) : null;
      body.replaceChildren(el("div", { class: "addsplit" },
        rooms.length ? roomList : el("p", { class: "muted" }, "لا غرف."),
        el("div", {}, el("div", { class: "addhead" }, el("h4", {}, areaName[addState.room] || ""), addAll),
          listOf(shown.map((e) => entityRow(e)), "كل أجهزة هذه الغرفة على الصفحة."))));
    } else if (t === "devices") {
      const sel = el("select", { onchange: (ev) => { addState.area = ev.target.value; fillList(); } },
        [["", "كل الغرف"], ["-", "بدون غرفة"], ...S.catalog.areas.map((a) => [a.id, a.name])].map(([v, l]) => el("option", { value: v, selected: v === addState.area }, l)));
      const inArea = free().filter((e) => (!addState.control || e.controllable) &&
        (!addState.area || (addState.area === "-" ? !e.area_id : e.area_id === addState.area)));
      const shown = inArea.filter((e) => (!addState.kind || kindOf(e.entity_id) === addState.kind) && hits(device(e.entity_id).name, e.entity_id, areaName[e.area_id]))
        // This room's first.
        .sort((x, y) => (y.area_id === page.id) - (x.area_id === page.id));
      filters.append(kindChips(inArea, fillList), el("div", { class: "row" }, sel, controlToggle()));
      body.replaceChildren(listOf(shown.slice(0, 300).map((e) => entityRow(e, areaName[e.area_id] || "بدون غرفة")), "لا أجهزة أخرى."));
    } else if (t === "apps") {
      const list = apps().filter((a) => hits(appName(a.package), a.package));
      body.replaceChildren(
        listOf(list.map((a) => {
          const there = page.cards.some((c) => c.options?.package === a.package);
          return row_("mdi:apps", appName(a.package), there ? "على الصفحة" : "", () => add((l) => (
            { id: nextCardId(l), card: "app.launch", entities: [], size: "1x1", options: { package: a.package } })), there);
        }), "لا تطبيقات مسموحة لهذه الشاشة."),
        el("p", { class: "muted small" }, "التطبيقات المسموحة تُضبط في إعدادات الشاشة."));
    } else {
      filters.append(chips([["", "الكل"], ["builtin", "ودجات الشاشة"], ["android", "ودجات أندرويد"]], addState.wkind, (v) => { addState.wkind = v; fillList(); }));
      const items = [];
      if (addState.wkind !== "android") {
        for (const w of builtins().filter((w) => hits(SHAPES[w.card]))) {
          const src = w.source ? sources()[w.source] : null;
          const missing = w.source && !src;
          items.push(row_(w.icon, SHAPES[w.card], missing ? "حدّد مصدرها في المشروع ← الودجات" : src ? device(src).name : "",
            () => add((l) => ({ id: nextCardId(l), card: w.card, entities: src ? [src] : [], size: w.size })), missing));
        }
      }
      if (addState.wkind !== "builtin") {
        for (const w of androids().filter((w) => hits(w.label, w.provider))) {
          items.push(row_("mdi:widgets-outline", w.label || w.provider, "تُربط على الشاشة",
            () => add((l) => ({ id: nextCardId(l), card: "widget.android", entities: [], size: "2x2", options: { provider: w.provider } }))));
        }
      }
      body.replaceChildren(listOf(items, "لا ودجات متاحة. ودجات أندرويد تأتي من التطبيقات المسموحة."));
    }
  }

  function controlToggle() {
    return chips([["1", "أجهزة التحكم"], ["0", "مع الحساسات"]], addState.control ? "1" : "0", (v) => { addState.control = v === "1"; addState.kind = ""; draw(); });
  }

  draw();
  sheet(el("div", { class: "addwin" },
    el("div", { class: "addtop" }, el("h3", {}, "إضافة بطاقة"), search),
    el("div", { class: "addmain" }, nav, el("div", { class: "addpane" }, filters, body)),
    el("div", { class: "actions" }, el("span", { class: "spacer" }), el("button", { onclick: closeSheet }, "إغلاق"))));
  $("veil").firstChild.classList.add("wide");
  search.focus();
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
  if (rest.length || !groups.length) groups.push({ id: null, name: groups.length ? "أخرى" : "الغرف", pages: rest, also: new Set() });
  return groups;
}

// What holds a room's card on a floor: the room itself, or its "shown here too" entry.
function tileHolder(l, g, i) {
  const p = l.pages[i];
  return g.also.has(i) ? p.also.find((a) => a.floor === g.id) : p;
}

// A room as a card of the menu's grid: its shape, size and place on that floor (picture, 2x1).
function menuCard(l, g, i) {
  const t = tileHolder(l, g, i)?.tile || {};
  return { id: l.pages[i].id, card: t.card || "room.card", size: t.size || "2x1", pos: t.pos, entities: [], _i: i };
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
      x.id ? el("span", { class: "fedit", title: "اسم الطابق وأيقونته، أو حذفه", onclick: (ev) => { ev.stopPropagation(); floorSheet(x.id, x.name, x.icon, false); } }, icon("mdi:pencil-outline")) : null)),
    el("div", { class: "ftab add", title: "إضافة طابق", onclick: addFloorSheet }, icon("mdi:plus"))));

  const cards = g.pages.map((i) => menuCard(l, g, i));
  const { placed, screens, grids } = pack(cards);
  const box = el("div", { class: "screens" });
  for (let s = 0; s <= screens; s++) {
    const grid = el("div", { class: "screen" });
    if (s === screens) grid.append(el("span", { class: "screenlabel" }, `الشاشة ${screens + 1} (جديدة)`));
    else if (screens > 1) grid.append(el("span", { class: "screenlabel" }, `الشاشة ${s + 1} من ${screens}`));
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
    title: g.also.has(i) ? "تظهر هنا أيضاً" : "",
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
    el("span", {}, pl.card.card === "room.summary" || !picture ? `${devices} جهاز` : "")),
  el("span", { class: "redit", title: "تعديل الغرفة", onclick: (ev) => { ev.stopPropagation(); roomTileSheet(i, g.id); } }, icon("mdi:pencil-outline")));
  return node;
}

// An empty cell of the menu: + adds a room there; a dragged room goes there.
function roomCell(g, placed, screen, col, row) {
  const node = el("div", {
    class: "cell empty", title: "إضافة غرفة هنا",
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
    if (others.length) list.append(el("div", { class: "floorhead" }, el("h4", { class: "grow" }, "غرف هذه الشاشة في طوابق أخرى (تظهر هنا أيضاً)")));
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
  const floors = [...S.catalog.floors, { id: null, name: "بدون طابق" }];
  let haRooms = 0;
  for (const f of floors) {
    const areas = S.catalog.areas.filter((a) => (a.floor_id || null) === f.id && !have.has(a.id));
    if (!areas.length) continue;
    haRooms += areas.length;
    list.append(el("div", { class: "floorhead" }, el("h4", { class: "grow" }, `Home Assistant · ${f.name}`),
      areas.length > 1 ? el("button", { onclick: () => addAreas(areas.map((a) => a.id), false) }, `+ الطابق كاملاً (${areas.length})`) : null));
    for (const a of areas) {
      list.append(el("li", { onclick: () => addAreas([a.id], true) },
        icon(a.icon || "mdi:floor-plan"), el("span", { class: "grow" }, a.name), el("span", { class: "muted" }, `${controlsIn(a.id).length} جهاز`)));
    }
  }
  sheet(
    el("h3", {}, `إضافة غرفة · ${g.name}`),
    el("p", { class: "muted" }, "الغرفة من Home Assistant تُضاف بكل أجهزتها، في الخانة التي ضغطتها."),
    list.children.length ? list : null,
    !haRooms ? el("p", { class: "muted" }, "كل غرف Home Assistant مضافة.") : null,
    el("div", { class: "actions" },
      el("button", { onclick: () => {
        change((lay) => {
          pinGroup(lay, g);
          const page = { id: pageId("room", new Set(lay.pages.map((p) => p.id))), title: "غرفة جديدة", cards: [], tile: spot };
          if (g.id) page.floor = g.id;
          lay.pages.push(page);
        });
        closeSheet();
        roomTileSheet(L().pages.length - 1, g.id);
      } }, "+ غرفة فارغة"),
      el("span", { class: "spacer" }),
      el("button", { onclick: closeSheet }, "إغلاق")),
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
  const t = tileHolder(l, g, i)?.tile || {};
  const shape = t.card || "room.card", size = t.size || "2x1";
  const edit = (fn) => { change((lay) => fn(lay)); roomTileSheet(i, floorId); };
  const sizes = shape === "room.summary" ? ["1x1", "2x1"] : ["1x1", "2x1", "2x2"];
  const ownFloor = roomGroups(l).find((x) => x.pages.includes(i) && !x.also.has(i))?.name;
  sheet(
    el("h3", {}, p.title),
    isAlso ? el("p", { class: "muted" }, `تظهر هنا أيضاً. طابقها: ${ownFloor || "—"}`) : null,
    field("الاسم", el("input", { value: p.title, onchange: (e) => { const v = e.target.value.trim(); if (v) edit((lay) => { lay.pages[i].title = v; }); } })),
    field("الأيقونة", iconInput(p.icon, "mdi:floor-plan", (v) => edit((lay) => { v && validIcon(v) ? lay.pages[i].icon = v : delete lay.pages[i].icon; }))),
    field("بطاقتها في قائمة الغرف", chips([["room.card", "صورة الغرفة"], ["room.summary", "التفاصيل"]], shape,
      (v) => edit((lay) => setTile(lay, g, i, { card: v, size: v === "room.summary" && size === "2x2" ? "2x1" : size })))),
    field("الحجم", chips(sizes.map((v) => [v, SIZES[v] || v]), size, (v) => edit((lay) => setTile(lay, g, i, { size: v })))),
    !isAlso && effFloors().length ? field("الطابق على الشاشة", (() => {
      const haFloor = effFloors().find((f) => (f.areas || []).includes(p.id))?.name;
      const s = selectEl([["", `من HA (${haFloor || "بدون طابق"})`], ...effFloors().map((f) => [f.id, f.name])], p.floor || "");
      s.onchange = () => { change((lay) => { s.value ? lay.pages[i].floor = s.value : delete lay.pages[i].floor; }); closeSheet(); };
      return s;
    })()) : null,
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: closeSheet }, "تم"),
      el("button", { onclick: () => { closeSheet(); S.page = i; S.menu = false; S.sel = null; render(); } }, "فتح الغرفة"),
      el("span", { class: "spacer" }),
      isAlso ? el("button", { onclick: () => {
        change((lay) => { lay.pages[i].also = lay.pages[i].also.filter((a) => a.floor !== g.id); if (!lay.pages[i].also.length) delete lay.pages[i].also; });
        closeSheet();
      } }, "إزالة من هذا الطابق") : null,
      el("button", { class: "danger", onclick: () => {
        if (!confirm(`حذف الغرفة "${p.title}" وكل بطاقاتها من هذه الشاشة؟`)) return;
        change((lay) => { lay.pages.splice(i, 1); S.page = DASH; S.sel = null; });
        closeSheet();
      } }, "حذف الغرفة")),
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
    if (isNew && !text.trim()) { msg.textContent = "اكتب اسم الطابق."; return; }
    const r = await post(`api/panels/${enc(pid)}/floor`, { floor_id: id, name: text.trim(), icon: chosen || null, all });
    if (!r.ok) { msg.textContent = "لم يُحفظ: " + (r.body?.error || r.status); return; }
    await loadConfig(pid);
    if (isNew) { S.floorTab = id; change((l) => { l.floors = [...new Set([...(l.floors || []), id])]; }); }
    closeSheet();
    render();
  };
  const g = roomGroups(L()).find((x) => x.id === id);
  sheet(
    el("h3", {}, isNew ? "طابق جديد" : name),
    field("اسم الطابق", el("input", { value: text, placeholder: isNew ? "مثلاً: الحديقة" : "فارغ: اسم Home Assistant", oninput: (e) => { text = e.target.value; } })),
    field("الأيقونة", iconInput(chosen, "mdi:home-floor-l", (v) => { chosen = v && validIcon(v) ? v : ""; })),
    msg,
    el("div", { class: "actions" },
      el("button", { class: "solid", onclick: () => save(false) }, "هذه الشاشة"),
      el("button", { onclick: () => save(true) }, "نسخ إلى كل الشاشات"),
      el("span", { class: "spacer" }),
      !isNew ? el("button", { class: "danger", onclick: () => {
        const n = g ? g.pages.length : 0;
        if (!confirm(`حذف الطابق من هذه الشاشة مع غرفه (${n}) وكل بطاقاتها؟`)) return;
        change((l) => deleteFloor(l, id));
        S.floorTab = "";
        closeSheet();
      } }, "حذف الطابق") : null,
      el("button", { onclick: closeSheet }, "إلغاء")),
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
      el("button", { onclick: () => { S.floorTab = f.id; change((lay) => { lay.floors = [...new Set([...(lay.floors || []), f.id])]; }); closeSheet(); } }, "فارغ"),
      fresh.length ? el("button", { class: "solid", onclick: () => {
        S.floorTab = f.id;
        change((lay) => {
          lay.floors = [...new Set([...(lay.floors || []), f.id])];
          const at = lay.pages.length;
          addRoomPages(lay, fresh);
          for (const p of lay.pages.slice(at)) p.floor = f.id;
        });
        closeSheet();
      } }, `مع غرفه (${fresh.length})`) : null));
  }
  sheet(
    el("h3", {}, "إضافة طابق"),
    list.children.length ? list : el("p", { class: "muted" }, "كل الطوابق ظاهرة."),
    el("div", { class: "actions" },
      el("button", { class: "solid", onclick: () => floorSheet("wp_" + Math.random().toString(36).slice(2, 10), "", null, true) }, "+ طابق جديد"),
      el("span", { class: "spacer" }),
      el("button", { onclick: closeSheet }, "إغلاق")),
  );
}

// ---------- settings and permissions ----------

const LANGS = [["ar", "العربية"], ["fr", "Français"], ["en", "English"]];
const THEMES = [["light", "فاتح"], ["dark", "داكن"]];
const ROLES = [["guest", "بدون رمز"], ["user", "رمز المستخدم"], ["installer", "رمز المُركِّب"]];
const CAPS = [["control", "التحكم"], ["apps", "التطبيقات"], ["edit_cards", "تعديل البطاقات"], ["reorder", "إعادة الترتيب"], ["dashboard", "تخصيص لوحتي"], ["add_widgets", "إضافة تطبيقات وودجات"],
  ["connection", "صفحة الاتصال"], ["display", "صفحة الشاشة"], ["brightness", "السطوع"], ["system", "أزرار النظام"],
  ["rooms", "الغرف"], ["devices", "الأجهزة"], ["panel", "إعدادات الشاشة"], ["project", "المشروع"]];
// Pages of the panel's settings screen: the guest never opens it.
const PAGE_CAPS = new Set(["connection", "display", "brightness", "system"]);
const BUILT_IN = { lang: "en", theme: "light", screensaver: { idle_seconds: 60, dim_brightness: 10 }, app_return_seconds: 300, home_title: "" };

function selectEl(options, value) {
  const s = el("select");
  for (const [v, label] of options) s.append(el("option", { value: v, selected: v === value }, label));
  return s;
}

// Display settings: blank means "inherit".
function displayFields(form, values, inherited, inheritLabel) {
  const ss = values.screensaver || {};
  const name = (list, v) => list.find(([k]) => k === v)?.[1] || v;
  const f = {
    lang: selectEl([["", `${inheritLabel} (${name(LANGS, inherited.lang)})`], ...LANGS], values.lang || ""),
    theme: selectEl([["", `${inheritLabel} (${name(THEMES, inherited.theme)})`], ...THEMES], values.theme || ""),
    idle: el("input", { type: "number", min: 10, value: ss.idle_seconds ?? "", placeholder: inherited.screensaver.idle_seconds }),
    dim: el("input", { type: "number", min: 0, max: 100, value: ss.dim_brightness ?? "", placeholder: inherited.screensaver.dim_brightness }),
    ret: el("input", { type: "number", min: 30, max: 3600, value: values.app_return_seconds ?? "", placeholder: inherited.app_return_seconds }),
    home: el("input", { maxlength: 40, value: values.home_title ?? "", placeholder: inherited.home_title || "لوحتي" }),
  };
  form.append(
    el("label", {}, "عنوان الصفحة الرئيسية"), f.home,
    el("label", {}, "اللغة"), f.lang,
    el("label", {}, "المظهر"), f.theme,
    el("label", {}, "شاشة التوقف بعد (ثانية)"), f.idle,
    el("label", {}, "سطوع التعتيم (%)"), f.dim,
    el("label", {}, "العودة من تطبيق آخر بعد (ثانية)"), f.ret,
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
  const input = el("input", { type: "password", inputmode: "numeric", maxlength: 8, placeholder: "4 إلى 8 أرقام", autocomplete: "new-password", style: { width: "140px" } });
  return el("div", { class: "row", style: { margin: "6px 0" } },
    el("span", { style: { minWidth: "130px" } }, label),
    el("span", { class: isSet ? "dot on" : "dot off", style: { minWidth: "110px", fontSize: "13px" } }, setText),
    input,
    el("button", { class: "primary", onclick: async () => {
      if (!/^[0-9]{4,8}$/.test(input.value)) { alert("الرمز من 4 إلى 8 أرقام."); return; }
      await onSet(input.value); input.value = "";
    } }, isSet ? "تغيير" : "ضبط"),
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
  const place = selectEl([["", "— بدون —"], ...S.catalog.areas.map((a) => [a.id, a.name])], S.editor[pid]?.area_id || "");
  const general = el("div", { class: "form" }, el("label", {}, "اسم الشاشة"), name, el("label", {}, "مكان تركيبها"), place,
    el("label", {}, "نوع الجهاز"), el("div", { class: "muted" }, `${TYPES[p.type] || PLATFORMS[p.platform || "android"] || p.platform}${p.model ? " · " + p.model : ""}`));
  const display = el("div", { class: "form" });
  const readDisplay = displayFields(display, cfg.config.overrides || {}, inheritedDisplay(), "من المشروع");

  view.append(
    el("div", { class: "box" }, el("h3", {}, "الشاشة"), general,
      el("h3", { style: { marginTop: "18px" } }, "العرض"), el("p", { class: "muted" }, "الحقول الفارغة تأخذ قيمة المشروع."), display,
      el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
        const newName = name.value.trim();
        if (newName && newName !== p.name) await post(`api/panels/${enc(pid)}/rename`, { name: newName });
        if ((S.editor[pid]?.area_id || "") !== place.value) {
          await post(`api/panels/${enc(pid)}/area`, { area_id: place.value || null });
          if (S.editor[pid]) S.editor[pid].area_id = place.value || null;
        }
        const keep = cfg.config.overrides?.floor_names ? { floor_names: cfg.config.overrides.floor_names } : {};
        const r = await put(`api/panels/${enc(pid)}/config`, { overrides: { ...keep, ...readDisplay() } });
        if (!r.ok) { msg.className = "msg error"; msg.textContent = "لم يُحفظ: " + (r.body?.error || r.status); return; }
        await refresh(); await loadConfig(pid);
        toast(p.online ? "حُفظ ووصل للشاشة." : "حُفظ. ستستلمه الشاشة عند اتصالها.");
        render();
      } }, "حفظ لهذه الشاشة"),
      el("button", { onclick: async () => {
        const display = readDisplay();
        if (!Object.keys(display).length) { alert("لا إعداد خاص بهذه الشاشة لتطبيقه."); return; }
        if (!confirm("تصبح هذه الإعدادات إعدادات المشروع، وتُستبدل الإعدادات الخاصة في كل الشاشات. متابعة؟")) return;
        const r = await post("api/site/display_all", { display });
        if (!r.ok) { msg.className = "msg error"; msg.textContent = "لم يُحفظ: " + (r.body?.error || r.status); return; }
        await loadSite(); await loadConfig(pid);
        toast(r.body.replaced ? `نُسخ إلى كل الشاشات، واستُبدل إعداد ${r.body.replaced} شاشة.` : "نُسخ إلى كل الشاشات.");
        render();
      } }, "نسخ إلى كل الشاشات")), msg),
    appsBox(pid, p, cfg),
    el("div", { class: "box" }, el("h3", {}, "رمز خاص بهذه الشاشة (اختياري)"),
      el("p", { class: "muted" }, "بدونه تستعمل الشاشة رموز المشروع."),
      pinRow("رمز المُركِّب", cfg.config.installer_pin_set, cfg.config.installer_pin_set ? "● خاص بالشاشة" : "من المشروع",
        async (pin) => { await post(`api/panels/${enc(pid)}/pin`, { role: "installer", pin }); reload(); },
        async () => { await post(`api/panels/${enc(pid)}/pin`, { role: "installer", pin: null }); reload(); }, "استعمال رمز المشروع"),
      pinRow("رمز المستخدم", cfg.config.user_pin_set, cfg.config.user_pin_set ? "● خاص بالشاشة" : "من المشروع",
        async (pin) => { await post(`api/panels/${enc(pid)}/pin`, { role: "user", pin }); reload(); },
        async () => { await post(`api/panels/${enc(pid)}/pin`, { role: "user", pin: null }); reload(); }, "استعمال رمز المشروع")),
    el("div", { class: "box" }, el("h3", {}, "إلغاء الربط"),
      el("p", { class: "muted" }, "تنقطع الشاشة عن Home Assistant فوراً، ويلزم ربطها من جديد."),
      el("button", { class: "danger", onclick: async () => {
        if (!confirm(`إلغاء ربط "${p.name}"؟`)) return;
        await post(`api/panels/${enc(pid)}/revoke`);
        S.view = { kind: "home" };
        await refresh();
      } }, "إلغاء ربط الشاشة")),
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
      el("input", { type: "number", min: 30, max: 3600, value: a.return_seconds ?? "", placeholder: `${cfg.effective.app_return_seconds} ث`,
                    style: { width: "110px" }, title: "العودة للوحة بعد (ثانية)",
                    oninput: (e) => { a.return_seconds = e.target.value === "" ? undefined : Number(e.target.value); } }),
      el("button", { class: "danger", onclick: () => { apps.splice(i, 1); draw(); } }, "حذف"),
    )) : [el("p", { class: "muted" }, "لا توجد تطبيقات مسموحة.")]));
  };
  draw();
  const free = () => installed.filter((a) => !apps.some((x) => x.package === a.package));
  const pick = selectEl([["", installed.length ? "— اختر تطبيقاً مثبتاً —" : "— الشاشة لم ترسل تطبيقاتها بعد —"], ...free().map((a) => [a.package, a.label])], "");
  const typed = el("input", { placeholder: "أو اسم الحزمة: com.example.app", style: { width: "240px", direction: "ltr" } });
  const add = () => {
    const pkg = pick.value || typed.value.trim();
    if (!pkg) return;
    if (!/^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$/.test(pkg)) { alert("اسم الحزمة غير صالح."); return; }
    if (!apps.some((a) => a.package === pkg)) apps.push({ package: pkg });
    typed.value = "";
    pick.replaceChildren(...[["", "— اختر تطبيقاً مثبتاً —"], ...free().map((a) => [a.package, a.label])]
      .map(([v, l]) => el("option", { value: v }, l)));
    draw();
  };
  return el("div", { class: "box" }, el("h3", {}, "التطبيقات المسموحة"),
    el("p", { class: "muted" }, "تظهر في صفحة التطبيقات على الشاشة، وودجاتها تُضاف من +. الوقت الفارغ يأخذ مدة العودة العامة."),
    list,
    el("div", { class: "row", style: { marginTop: "10px" } }, pick, typed, el("button", { onclick: add }, "إضافة")),
    el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
      const clean = apps.map((a) => Object.fromEntries(Object.entries(a).filter(([, v]) => v !== undefined && v !== "")));
      const r = await put(`api/panels/${enc(pid)}/config`, { apps: clean });
      if (!r.ok) { alert("لم يُحفظ: " + (r.body?.error || r.status)); return; }
      await loadConfig(pid);
      toast(p.online ? "حُفظ ووصل للشاشة." : "حُفظ. ستستلمه الشاشة عند اتصالها.");
      render();
    } }, "حفظ التطبيقات")));
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
    el("h3", {}, "ما يُسمح به على هذه الشاشة"),
    el("p", { class: "muted" }, "التحكم مسموح دائماً. الغرف والأجهزة وإعدادات الشاشة والمشروع تُعدَّل من هنا، ومن الشاشة نفسها لمن يملك صلاحيتها."),
    el("p", { class: "muted" }, "الصفحات في إعدادات الشاشة تظهر حسب هذه الصلاحيات، وصفحة الأمان تظهر دائماً. بدون رمز لا تُفتح الإعدادات."),
    table,
    el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
      const access = {};
      for (const [role] of ROLES) access[role] = CAPS.map(([c]) => c).filter((c) => boxes[role + ":" + c].checked);
      const r = await put(`api/panels/${enc(pid)}/config`, { access, access_known: CAPS.map(([c]) => c) });
      if (!r.ok) { alert("لم يُحفظ: " + (r.body?.error || r.status)); return; }
      await loadConfig(pid);
      toast(p.online ? "حُفظ ووصل للشاشة." : "حُفظ. ستستلمه الشاشة عند اتصالها.");
    } }, "حفظ"))));
}

// ---------- project ----------

function renderProject(view) {
  const tabs = [["general", "عام"], ["devices", "أنواع الأجهزة"], ["widgets", "الودجات"]];
  view.append(el("div", { class: "titlebar" },
    el("h2", {}, S.site?.name || "المشروع"),
    el("div", { class: "tabs" }, tabs.map(([id, label]) =>
      el("button", { class: S.view.tab === id ? "active" : "", onclick: () => { S.view.tab = id; render(); } }, label)))));
  if (!S.site) { loadSite().then(render); return; }
  if (S.view.tab === "devices") return renderDevices(view);
  if (S.view.tab === "widgets") return renderSources(view);

  const site = S.site;
  const name = el("input", { value: site.name || "" });
  const display = el("div", { class: "form" });
  const read = displayFields(display, site.defaults || {}, BUILT_IN, "الافتراضي");
  const reload = async () => { await loadSite(); await loadOverview(); render(); };
  view.append(
    el("div", { class: "box" }, el("h3", {}, "المشروع"),
      el("div", { class: "form" }, el("label", {}, "اسم المشروع"), name),
      el("h3", { style: { marginTop: "18px" } }, "الإعدادات الافتراضية لكل الشاشات"), display,
      el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
        // Floor names are not on this form: kept as they are.
        const keep = site.defaults?.floor_names ? { floor_names: site.defaults.floor_names } : {};
        const r = await put("api/site", { name: name.value.trim(), defaults: { ...keep, ...read() } });
        if (!r.ok) { alert("لم يُحفظ: " + (r.body?.error || r.status)); return; }
        S.site = r.body; toast("حُفظ، ووصل لكل الشاشات المتصلة."); render();
      } }, "حفظ"))),
    el("div", { class: "box" }, el("h3", {}, "كلمات السر"),
      el("p", { class: "muted" }, "رمز المُركِّب يعطي كل الصلاحيات المحلية على الشاشة، ورمز المستخدم يعطي ما تحدده في صلاحيات كل شاشة. يصل للشاشات مشفّراً فقط، ويعمل دون اتصال."),
      pinRow("رمز المُركِّب", site.installer_pin_set, site.installer_pin_set ? "● مضبوط" : "○ غير مضبوط",
        async (pin) => { await post("api/site/pin", { role: "installer", pin }); reload(); },
        async () => { if (confirm("إزالة رمز المُركِّب؟ تصبح إعدادات الشاشات مفتوحة.")) { await post("api/site/pin", { role: "installer", pin: null }); reload(); } }, "إزالة"),
      pinRow("رمز المستخدم", site.user_pin_set, site.user_pin_set ? "● مضبوط" : "○ غير مضبوط",
        async (pin) => { await post("api/site/pin", { role: "user", pin }); reload(); },
        async () => { await post("api/site/pin", { role: "user", pin: null }); reload(); }, "إزالة")),
  );
}

// Where the built-in widgets read from: set once, every panel's widgets use it.
function renderSources(view) {
  if (!S.catalog) { view.append(el("p", { class: "muted" }, "جارٍ تحميل أجهزة Home Assistant…")); loadCatalog().then(render); return; }
  const chosen = { ...sources() };
  const rows = WIDGETS.filter((w) => w.source).map((w) => {
    // Only sensors of the right kind (a temperature, a power in W or kW); the one chosen stays listed.
    const fits = (e) => !w.units || e.device_class === w.deviceClass || w.units.includes(e.unit);
    const options = S.catalog.entities
      .filter((e) => domainOf(e.entity_id) === w.domain && (fits(e) || e.entity_id === chosen[w.source]))
      .sort((a, b) => device(a.entity_id).name.localeCompare(device(b.entity_id).name));
    const reading = (e) => e.unit ? ` · ${e.state} ${e.unit}` : "";
    const sel = selectEl([["", "— بدون —"], ...options.map((e) => [e.entity_id, `${device(e.entity_id).name} (${e.entity_id})${reading(e)}`])], chosen[w.source] || "");
    sel.onchange = () => { if (sel.value) chosen[w.source] = sel.value; else delete chosen[w.source]; };
    return [el("label", {}, icon(w.icon), " ", SHAPES[w.card]), sel];
  });
  view.append(el("div", { class: "box" }, el("h3", {}, "مصادر الودجات"),
    el("p", { class: "muted" }, "تُحدَّد مرة واحدة للمشروع. الودجة المضافة من الشاشة أو من المصمم تقرأ من هنا، فلا تضيف جهازاً جديداً للشاشة. الودجة التي لا مصدر لها لا تظهر في قائمة +."),
    el("div", { class: "form" }, rows.flat()),
    el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
      const r = await put("api/site", { sources: chosen });
      if (!r.ok) { alert("لم يُحفظ: " + (r.body?.error || r.status)); return; }
      S.site = r.body; toast("حُفظ، ووصل لكل الشاشات المتصلة."); render();
    } }, "حفظ"))));
}

// Device types: set once, used by every panel (icons, names; special cards later).
let devFilter = "cover";
function renderDevices(view) {
  const list = { ...devices() };
  const areaName = Object.fromEntries(S.catalog.areas.map((a) => [a.id, a.name]));
  const domains = [["cover", "ستائر وأبواب"], ["switch", "مفاتيح ومقابس"], ["light", "إنارة"], ["binary_sensor", "حساسات"], ["climate", "مكيفات"]];
  const rows = S.catalog.entities.filter((e) => domainOf(e.entity_id) === devFilter);
  const dirty = { value: false };
  const save = el("button", { class: "solid", onclick: async () => {
    const r = await put("api/site", { devices: list });
    if (!r.ok) { alert("لم يُحفظ: " + (r.body?.error || r.status)); return; }
    S.site = r.body; dirty.value = false; toast("حُفظ، ووصل لكل الشاشات."); render();
  } }, "حفظ");
  const setField = (id, key, value) => {
    const d = { ...(list[id] || {}) };
    if (value) d[key] = value; else delete d[key];
    if (Object.keys(d).length) list[id] = d; else delete list[id];
    dirty.value = true;
  };
  const table = el("table", { class: "devtable" },
    el("tr", {}, el("th", {}, ""), el("th", {}, "الجهاز في HA"), el("th", {}, "الغرفة"), DEVICE_TYPES[devFilter] ? el("th", {}, "النوع") : null, el("th", {}, "الاسم على الشاشات"), el("th", {}, "الأيقونة")));
  for (const e of rows) {
    const d = list[e.entity_id] || {};
    const types = DEVICE_TYPES[devFilter];
    const haType = types?.find(([v]) => v === e.device_class)?.[1] || e.device_class || "عام";
    const probe = () => ({ card: R.kinds[kindOf(e.entity_id)]?.default || "sensor.value", entities: [e.entity_id] });
    const preview = icon(cardIcon(probe()), { style: { fontSize: "22px" } });
    const refreshIcon = () => preview.replaceWith(Object.assign(icon(cardIcon(probe()), { style: { fontSize: "22px" } })));
    table.append(el("tr", {},
      el("td", {}, preview),
      el("td", {}, e.name),
      el("td", { class: "muted" }, areaName[e.area_id] || "—"),
      types ? el("td", {}, (() => {
        const s = selectEl([["", `من HA (${haType})`], ...types], d.type || "");
        s.onchange = () => { setField(e.entity_id, "type", s.value); S.site.devices = list; };
        return s;
      })()) : null,
      el("td", {}, el("input", { value: d.name || "", placeholder: e.name, onchange: (ev) => { setField(e.entity_id, "name", ev.target.value.trim()); S.site.devices = list; } })),
      el("td", {}, el("input", { value: d.icon || "", placeholder: "mdi:...", dir: "ltr",
        onchange: (ev) => { const v = ev.target.value.trim(); if (v && !validIcon(v)) { alert("أيقونة غير معروفة. مثال: mdi:garage"); return; } setField(e.entity_id, "icon", v); S.site.devices = list; } })),
    ));
  }
  view.append(el("div", { class: "box", style: { maxWidth: "1100px" } },
    el("h3", {}, "أنواع الأجهزة"),
    el("p", { class: "muted" }, "حدّد نوع كل جهاز واسمه مرة واحدة، فتعرضه كل الشاشات بنفس الأيقونة والاسم. الحقل الفارغ يأخذ ما في Home Assistant."),
    chips(domains.map(([v, l]) => [v, l]), devFilter, (v) => { devFilter = v; render(); }),
    el("div", { style: { marginTop: "12px" } }, rows.length ? table : el("p", { class: "muted" }, "لا توجد أجهزة من هذا النوع.")),
    el("div", { class: "row", style: { marginTop: "14px" } }, save)));
}

// ---------- new panel setup: approve, rooms, send ----------

function wizard(req) {
  const st = { step: 1, pid: req.panel_id, name: req.name || "شاشة", area: "", rooms: new Set() };
  const bar = () => el("div", { class: "steps" }, [1, 2, 3].map((n) => el("span", { class: n <= st.step ? "done" : "" })));
  function show() {
    if (st.step === 1) {
      sheet(bar(), el("h3", {}, "1. الموافقة"),
        el("p", { class: "muted" }, "وافق فقط إذا كان هذا الرمز هو الظاهر على الشاشة."),
        el("div", { class: "bigcode" }, req.code),
        el("p", { class: "muted", style: { textAlign: "center" } }, `${req.name || ""} ${req.model ? "· " + req.model : ""}`),
        el("div", { class: "actions" },
          el("button", { class: "solid", onclick: async () => {
            const r = await post(`api/pending/${enc(req.request_id)}/approve`);
            if (!r.ok) { alert("انتهت صلاحية الطلب. ستطلب الشاشة الربط من جديد."); closeSheet(); refresh(); return; }
            st.pid = r.body.panel_id || st.pid;
            st.step = 2; show(); refresh();
          } }, "الرمز مطابق، موافقة"),
          el("button", { onclick: async () => { await post(`api/pending/${enc(req.request_id)}/reject`); closeSheet(); refresh(); } }, "رفض"),
          el("span", { class: "spacer" }), el("button", { onclick: closeSheet }, "لاحقاً")));
      return;
    }
    if (st.step === 2) {
      const name = el("input", { value: st.name, oninput: (e) => { st.name = e.target.value; } });
      const place = selectEl([["", "— اختر —"], ...S.catalog.areas.map((a) => [a.id, a.name])], st.area);
      place.onchange = () => { st.area = place.value; if (st.area) st.rooms.add(st.area); show(); };
      const floors = [...S.catalog.floors, { id: null, name: S.catalog.floors.length ? "بدون طابق" : "الغرف" }];
      const boxes = floors.map((f) => {
        const areas = S.catalog.areas.filter((a) => (a.floor_id || null) === f.id);
        if (!areas.length) return null;
        return el("div", { class: "floorbox" }, el("b", {}, f.name), el("div", { class: "checks" }, areas.map((a) =>
          el("label", {}, el("input", { type: "checkbox", checked: st.rooms.has(a.id), onchange: (e) => { e.target.checked ? st.rooms.add(a.id) : st.rooms.delete(a.id); } }),
            a.name, el("span", { class: "muted" }, `(${controlsIn(a.id).length})`)))));
      });
      sheet(bar(), el("h3", {}, "2. الاسم والغرف"),
        field("اسم الشاشة", name),
        field("مكان تركيبها", place),
        field("الغرف التي تظهر على الشاشة (كل غرفة بأجهزتها)", el("div", {}, boxes)),
        el("div", { class: "actions" },
          el("button", { class: "solid", onclick: () => { if (!st.rooms.size) { alert("اختر غرفة واحدة على الأقل."); return; } st.step = 3; show(); } }, "التالي"),
          el("span", { class: "spacer" }), el("button", { onclick: closeSheet }, "لاحقاً")));
      return;
    }
    // Step 3: build the layout and send it.
    const layout = { schema: 2, version: 1, pages: [] };
    const ordered = S.catalog.areas.map((a) => a.id).filter((id) => st.rooms.has(id));
    if (st.area && ordered.includes(st.area)) ordered.splice(ordered.indexOf(st.area), 1), ordered.unshift(st.area);
    addRoomPages(layout, ordered);
    const count = layout.pages.reduce((n, p) => n + p.cards.length, 0);
    sheet(bar(), el("h3", {}, "3. الإرسال"),
      el("p", {}, `${layout.pages.length} غرف، ${count} بطاقة.`),
      el("ul", {}, layout.pages.map((p) => el("li", {}, `${p.title}: ${p.cards.length}`))),
      el("p", { class: "muted" }, "يمكنك تعديل كل شيء بعدها في التصميم."),
      el("div", { class: "actions" },
        el("button", { class: "solid", onclick: async () => {
          const pid = st.pid;
          if (st.name.trim()) await post(`api/panels/${enc(pid)}/rename`, { name: st.name.trim() });
          if (st.area) await post(`api/panels/${enc(pid)}/area`, { area_id: st.area });
          await put(`api/editor/panels/${enc(pid)}/draft`, { layout, base_version: null });
          const r = await post(`api/editor/panels/${enc(pid)}/send`, { force: true });
          closeSheet();
          await refresh(); await loadEditor();
          if (!r.ok) { alert("لم يُرسل: " + (r.body?.error || r.status)); }
          else toast(r.body.online ? "الشاشة جاهزة." : "أُرسل. ستستلمه الشاشة عند اتصالها.");
          openPanel(pid);
        } }, "إرسال للشاشة"),
        el("button", { onclick: () => { st.step = 2; show(); } }, "رجوع")));
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
