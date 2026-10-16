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

// Plain names: the installer never sees card ids or entity ids.
const SHAPES = {
  "light.toggle": "مفتاح", "light.dimmer": "تعتيم", "light.color": "ألوان", "light.group": "مجموعة",
  "switch.toggle": "مفتاح", "switch.outlet": "مقبس مع الاستهلاك",
  "cover.buttons": "أزرار", "cover.position": "موضع", "cover.tilt": "ميلان", "cover.group": "مجموعة",
  "climate.compact": "مختصرة", "climate.thermostat": "منظم حرارة",
  "fan.toggle": "مفتاح", "fan.speed": "سرعات", "lock.lock": "قفل",
  "scene.action": "زر", "scene.bar": "شريط مشاهد", "script.action": "زر", "button.action": "زر",
  "sensor.value": "قيمة", "sensor.multi": "عدة قيم", "sensor.binary": "حالة", "app.launch": "تطبيق",
};
const SIZES = { "1x1": "صغيرة", "2x1": "عريضة", "2x2": "كبيرة" };
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

const COLS = 5, ROWS = 4;  // the 11" panel

function spanOf(size) {
  const [w, h] = (size || "1x1").split("x").map(Number);
  return [Math.min(Math.max(w || 1, 1), COLS), Math.min(Math.max(h || 1, 1), ROWS)];
}

// First free spot that fits, in card order; a card that fits nowhere starts the next screen.
function pack(cards) {
  const screens = [];
  const newScreen = () => { screens.push(Array.from({ length: ROWS }, () => Array(COLS).fill(false))); };
  newScreen();
  const out = [];
  let first = 0;
  for (const card of cards) {
    const [w, h] = spanOf(sizeOf(card));
    let placed = null;
    for (let s = first; !placed; s++) {
      if (s === screens.length) newScreen();
      const g = screens[s];
      search: for (let r = 0; r <= ROWS - h; r++) for (let c = 0; c <= COLS - w; c++) {
        let free = true;
        for (let rr = r; rr < r + h && free; rr++) for (let cc = c; cc < c + w; cc++) if (g[rr][cc]) { free = false; break; }
        if (free) {
          for (let rr = r; rr < r + h; rr++) for (let cc = c; cc < c + w; cc++) g[rr][cc] = true;
          placed = { card, screen: s, col: c, row: r, w, h };
          break search;
        }
      }
    }
    out.push(placed);
    first = placed.screen;
  }
  return { placed: out, screens: screens.length, grids: screens };
}

// Where in the order a card must go to land on (screen, col, row); nearest if it cannot.
function indexForCell(cards, card, screen, col, row) {
  let best = cards.length, bestCost = Infinity;
  for (let i = 0; i <= cards.length; i++) {
    const trial = [...cards.slice(0, i), card, ...cards.slice(i)];
    const p = pack(trial).placed[i];
    const cost = Math.abs(p.screen - screen) * 100 + Math.abs(p.row - row) * 10 + Math.abs(p.col - col);
    if (cost < bestCost) { best = i; bestCost = cost; }
    if (cost === 0) break;
  }
  return best;
}

// Cells a new 1x1 card can land on exactly (the packing fills earlier gaps first).
function reachableCells(cards) {
  const probe = { card: "light.toggle", size: "1x1", entities: [] };
  const out = new Set();
  for (let i = 0; i <= cards.length; i++) {
    const p = pack([...cards.slice(0, i), probe, ...cards.slice(i)]).placed[i];
    out.add(`${p.screen}:${p.col}:${p.row}`);
  }
  return out;
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
  sel: null,           // selected card index on the page
};
const W = () => S.work[S.view.pid];
const L = () => W()?.layout;
const entities = () => Object.fromEntries(S.catalog.entities.map((e) => [e.entity_id, e]));
const devices = () => S.site?.devices || {};

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
  const taken = new Set([...layout.pages.flatMap((p) => p.cards.map((c) => c.id)), ...extra.map((c) => c.id)]);
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
  S.page = 0; S.sel = null; S.screen = 0;
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

// ---------- the panel, drawn as the app draws it ----------

// The 11" panel in dp (1280x800): sidebar 210 + line, top bar 56 + line, 5x4 cells.
const DP = { w: 1280, h: 800, side: 211, top: 57 };
const CELL_W = (DP.w - DP.side) / COLS;
const CELL_H = (DP.h - DP.top) / ROWS;
const HVAC = { heat: "#FF6F22", cool: "#2196F3", heat_cool: "#FFC107", auto: "#4CAF50", dry: "#FF9800", fan_only: "#00BCD4" };
const DASHBOARD = -1;

// HA's state at the last catalog read, so the copy shows what is on now.
const stateOf = (id) => entities()[id]?.state;
const isOn = (id) => ["on", "open", "opening", "closing", "unlocked", "playing", "home"].includes(stateOf(id));

function iconColorClass(c) {
  const id = c.entities[0];
  if (!id) return "";
  const kind = kindOfCard(c.card);
  if (kind === "cover") return isOn(id) ? "cov" : "";
  if (kind === "climate") return "";
  return c.entities.some(isOn) ? "on" : "";
}
function cardIconEl(c, size) {
  const i = icon(cardIcon(c), { class: iconColorClass(c), style: { fontSize: size + "px" } });
  if (kindOfCard(c.card) === "climate") { const st = stateOf(c.entities[0]); if (HVAC[st]) i.style.color = HVAC[st]; }
  return i;
}
const rbtn = (name, size) => el("span", { class: "rbtn", style: { width: size + "px", height: size + "px" } }, icon(name, { style: { fontSize: size * 0.6 + "px" } }));

function renderDesign(view) {
  const layout = L();
  if (!layout) return;
  if (S.catalogError) view.append(el("p", { class: "msg error" }, "تعذّرت قراءة الأجهزة من Home Assistant: " + S.catalogError));
  const eff = S.configs[S.view.pid]?.effective || {};
  const rtl = (eff.lang || "ar") === "ar";
  if (S.page !== DASHBOARD) S.page = Math.min(S.page, Math.max(0, layout.pages.length - 1));
  if (S.page !== DASHBOARD && !layout.pages.length) S.page = 0;
  const dash = S.page === DASHBOARD;
  const page = dash ? null : layout.pages[S.page];

  // Sidebar: dashboard, rooms (drag to reorder, pencil to edit), settings.
  const item = (iconName, text, active, onclick, extra) => el("div", { class: "sitem" + (active ? " active" : "") + (onclick ? "" : " fixed"), onclick },
    el("span", { class: "mark" }), icon(iconName), el("span", { class: "grow" }, text), extra);
  const rooms = el("ul", {});
  layout.pages.forEach((pg, i) => {
    rooms.append(el("li", { dataset: { index: i } }, item(pg.icon || "mdi:floor-plan", pg.title, i === S.page,
      () => { S.page = i; S.sel = null; S.screen = 0; render(); },
      icon("mdi:pencil-outline", { class: "edit" }))));
    const pencil = rooms.lastChild.querySelector(".edit");
    pencil.onclick = (ev) => { ev.stopPropagation(); pageSheet(i); };
  });
  Sortable.create(rooms, {
    animation: 120,
    onEnd: (evt) => { if (evt.oldIndex !== evt.newIndex) change((l) => { l.pages.splice(evt.newIndex, 0, l.pages.splice(evt.oldIndex, 1)[0]); S.page = evt.newIndex; }); },
  });
  const side = el("div", { class: "dside" },
    item("mdi:view-dashboard-outline", "لوحتي", dash, () => { S.page = DASHBOARD; S.sel = null; S.screen = 0; render(); }),
    el("div", { class: "line" }),
    rooms,
    el("button", { class: "addroom", onclick: roomSheet }, "+ غرفة"),
    el("div", { class: "line" }),
    item("mdi:cog-outline", "الإعدادات", false, null));

  const now = new Date();
  const top = el("div", { class: "dtop" }, icon("mdi:home-outline", { class: "home" }),
    el("div", { class: "title" }, dash ? "لوحتي" : page?.title || ""),
    el("div", { class: "clock" }, el("span", { dir: "ltr" }, `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`)));

  // The page's screens, one shown at a time, with dots as on the panel.
  const screensBox = el("div", { class: "screens" });
  const cards = dash ? dashboardCards(layout) : page?.cards || [];
  const { placed, screens, grids } = pack(cards);
  const editable = !dash && !!page;
  const reachable = editable ? reachableCells(cards) : new Set();
  // A full last screen gets an empty one after it, to add more.
  const lastFull = grids[screens - 1].every((row) => row.every(Boolean));
  const total = screens + (editable && lastFull && cards.length ? 1 : 0);
  S.screen = Math.min(S.screen || 0, total - 1);
  const s = S.screen;
  const grid = el("div", { class: "screen" });
  if (!page && !dash) {
    grid.append(el("div", { class: "cell", style: { gridColumn: "1 / -1", gridRow: "1 / -1", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "16px", fontSize: "20px", color: "var(--dmuted)" } },
      "لا توجد غرف بعد.", el("button", { class: "addroom", onclick: roomSheet }, "+ أضف غرفة من Home Assistant")));
  } else {
    for (const pl of placed.filter((x) => x.screen === s)) grid.append(cardCell(pl, cards, editable));
    for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) {
      if (s < screens && grids[s][r][c]) continue;
      grid.append(emptyCell(s, c, r, editable && reachable.has(`${s}:${c}:${r}`)));
    }
  }
  screensBox.append(grid);
  if (total > 1) {
    screensBox.append(el("div", { class: "pagedots" }, Array.from({ length: total }, (_, i) =>
      el("span", { class: i === s ? "on" : "", title: `الشاشة ${i + 1}`, onclick: () => { S.screen = i; render(); } }))));
  }

  const device = el("div", { class: "device" + (eff.theme === "dark" ? " dark" : ""), dir: rtl ? "rtl" : "ltr" },
    side, el("div", { class: "dmain" }, top, screensBox));
  const bezel = el("div", { class: "bezel" }, device);
  const wrap = el("div", { class: "devicewrap" }, bezel);
  view.append(wrap);
  // Scale the 1280x800 canvas to the page width.
  const fit = () => {
    const k = bezel.clientWidth / DP.w;
    device.style.transform = `scale(${k})`;
    bezel.style.height = DP.h * k + "px";
  };
  new ResizeObserver(fit).observe(bezel);
  requestAnimationFrame(fit);

  view.append(el("p", { class: "muted" }, dash
    ? "لوحتي تعرض الساعة ومشاهد الشاشة. تخصيصها يأتي مع التحرير من الشاشة (U3)."
    : "اضغط + لإضافة جهاز، واضغط بطاقة لتغيير شكلها وحجمها واسمها. اسحب البطاقة إلى بطاقة أخرى أو إلى + لنقلها، واسحب الغرف لترتيبها. البطاقات تملأ الفراغات بالترتيب، تماماً كما على الشاشة."));
  const issues = problems(layout).filter((x) => x !== "لا توجد غرف.");
  for (const m of issues) view.append(el("div", { class: "msg error" }, m));
}

// Same as the app until the dashboard can be customised: the clock, then every scene.
function dashboardCards(layout) {
  const seen = new Set();
  const scenes = layout.pages.flatMap((p) => p.cards).filter((c) => {
    if (kindOfCard(c.card) !== "scene") return false;
    const key = c.entities.join(",");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return [{ id: "_clock", card: "widget.clock", entities: [], size: "2x2" }, ...scenes];
}

let dragFrom = null;

function cardCell(pl, cards, editable) {
  const c = pl.card;
  const index = cards.indexOf(c);
  const known = !S.catalog.entities.length || c.entities.every((id) => entities()[id]);
  const node = el("div", {
    class: ["cell", "card", S.sel === index && editable ? "selected" : "", known ? "" : "missing"].join(" "),
    title: known ? "" : "جهاز غير موجود في Home Assistant",
    style: { gridColumn: `${pl.col + 1} / span ${pl.w}`, gridRow: `${pl.row + 1} / span ${pl.h}` },
  }, ...cardBody(c, pl.w, pl.h), el("span", { class: "kindtag" }, SHAPES[c.card] || ""));
  if (!editable) return node;
  node.draggable = true;
  Object.assign(node, {
    onclick: () => { S.sel = index; render(); cardSheet(index); },
    ondragstart: (ev) => { dragFrom = index; ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/plain", c.id); node.classList.add("dragging"); },
    ondragend: () => { dragFrom = null; node.classList.remove("dragging"); },
    ondragover: (ev) => { if (dragFrom !== null && dragFrom !== index) { ev.preventDefault(); node.classList.add("over"); } },
    ondragleave: () => node.classList.remove("over"),
    ondrop: (ev) => {
      ev.preventDefault();
      const from = dragFrom;
      if (from === null || from === index) return;
      // Dropped on a card: take its place in the order.
      change((l) => {
        const list = l.pages[S.page].cards;
        const [moved] = list.splice(from, 1);
        list.splice(index, 0, moved);
        S.sel = index;
      });
    },
  });
  return node;
}

// What the app's CardCell shows for this card at this size.
function cardBody(c, w, h) {
  const kind = kindOfCard(c.card);
  const single = c.entities.length === 1;
  if (c.card === "widget.clock") {
    const d = new Date();
    return [el("div", { class: "clockcell" }, el("b", {}, `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`),
      el("span", {}, d.toLocaleDateString("ar", { weekday: "long", day: "numeric", month: "long" })))];
  }
  if (w >= 2 && h >= 2 && (c.card === "climate.thermostat" || (single && (kind === "light" || kind === "cover")))) return [dialCell(c, w, h)];
  if (w >= 2 && h === 1 && single && kind === "light") return [lightBar(c)];
  if (w >= 2 && h === 1 && single && kind === "cover") return [coverBar(c)];
  if (c.card === "scene.bar") {
    return [el("div", { class: "scenes" }, c.label ? el("div", { class: "muted", style: { fontSize: "14px" } }, c.label) : null,
      el("div", { class: "row2" }, c.entities.map((id) => el("div", { class: "one" }, icon(device(id).icon || "mdi:palette-outline"), el("span", {}, device(id).name)))))];
  }
  if (c.card === "sensor.multi") {
    return [el("div", { class: "multi" }, c.entities.map((id) => el("div", {}, icon(cardIcon({ card: "sensor.value", entities: [id] })), el("span", {}, stateOf(id) ?? "—"))))];
  }
  // Standard cell: icon in the middle, name under it; shutters and fan speeds get ˄ ˅ on the side.
  const sideButtons = (kind === "cover" && c.entities.length >= 1) || c.card === "fan.speed";
  const value = c.entities.length > 1 ? `${c.entities.filter(isOn).length}/${c.entities.length}` : "";
  return [el("div", { class: "std" + (sideButtons ? " side" : "") },
    el("span", { class: "val" + (sideButtons ? " bottom" : "") }, value),
    el("div", { class: "mid" }, cardIconEl(c, 46), el("span", { class: "lbl" }, cardLabel(c)))),
    sideButtons ? el("div", { class: "steps" }, rbtn("mdi:chevron-up", 46), rbtn("mdi:chevron-down", 46)) : null];
}

function levelBar(fraction, color) {
  const on = fraction > 0;
  return el("div", { class: "bar", style: { background: on ? `color-mix(in srgb, ${color} 22%, transparent)` : "color-mix(in srgb, var(--dmuted) 25%, transparent)" } },
    on ? el("i", { style: { width: `${Math.max(fraction * 100, 10)}%`, background: color } }) : null,
    on ? el("b", { style: { insetInlineStart: `calc(${Math.max(fraction * 100, 10)}% - 18px)` } }) : null);
}

function lightBar(c) {
  const id = c.entities[0];
  const dimmer = c.card !== "light.toggle";
  return el("div", { class: "wide" },
    el("div", { class: "head" }, cardIconEl(c, 40), el("div", { class: "names" }, el("div", { class: "name" }, cardLabel(c)))),
    dimmer ? levelBar(isOn(id) ? 0.6 : 0, "var(--active)") : null);
}

function coverBar(c) {
  const id = c.entities[0];
  const bar = c.card === "cover.position" || c.card === "cover.tilt";
  const stateText = { open: "مفتوح", closed: "مغلق", opening: "يُفتح…", closing: "يُغلق…" }[stateOf(id)] || "";
  return el("div", { class: "wide" },
    el("div", { class: "head" }, cardIconEl(c, 40),
      el("div", { class: "names" }, el("div", { class: "name" }, cardLabel(c)), el("div", { class: "sub" }, stateText)),
      el("div", { class: "btns" }, rbtn("mdi:chevron-up", 40), rbtn("mdi:stop", 40), rbtn("mdi:chevron-down", 40))),
    bar ? levelBar(isOn(id) ? 0.5 : 0, "var(--cover)") : null);
}

// ---- 2x2 dial (DialPanel): title, 270° arc, choices on a ring, centre, − and + ----

const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function arcPath(cx, cy, r, start, sweep) {
  const rad = (d) => (d * Math.PI) / 180;
  const x1 = cx + r * Math.cos(rad(start)), y1 = cy + r * Math.sin(rad(start));
  const x2 = cx + r * Math.cos(rad(start + sweep)), y2 = cy + r * Math.sin(rad(start + sweep));
  return `M ${x1} ${y1} A ${r} ${r} 0 ${sweep > 180 ? 1 : 0} 1 ${x2} ${y2}`;
}

function dialCell(c, w, h) {
  const kind = kindOfCard(c.card);
  const id = c.entities[0];
  const st = stateOf(id);
  const on = isOn(id);
  const boxW = w * CELL_W - 28, boxH = h * CELL_H - 28;
  const side = Math.min(boxW, boxH - 44);
  const stroke = 14, arcR = side / 2 - stroke, ringOuter = arcR - stroke * 1.2;
  let fraction = null, color = "var(--active)", segments = [], center = null, frac = 0.22, power = null;

  if (kind === "light") {
    power = on;
    fraction = c.card === "light.toggle" ? null : on ? 0.6 : 0;
    frac = c.card === "light.color" ? 0.31 : 0.27;
    center = c.card === "light.color" ? "wheel" : "halves";
  } else if (kind === "cover") {
    color = "var(--cover)";
    if (c.card === "cover.buttons") { center = "coverhalves"; frac = 0.27; }
    else {
      fraction = on ? 0.5 : 0;
      segments = [["mdi:arrow-up", st === "opening"], ["mdi:stop", false], ["mdi:arrow-down", st === "closing"]];
      center = "number";
    }
  } else {
    power = st && st !== "off";
    color = HVAC[st] || "var(--active)";
    fraction = power ? 0.45 : null;
    segments = [["mdi:power", st === "off"], ["mdi:fire", st === "heat"], ["mdi:snowflake", st === "cool"], ["mdi:water-percent", st === "dry"], ["mdi:fan", st === "fan_only"]];
    center = "temp";
  }
  const centerR = side * frac;
  const g = svg("svg", { width: side, height: side, viewBox: `0 0 ${side} ${side}` });
  const cx = side / 2;
  if (fraction !== null) {
    g.append(svg("path", { d: arcPath(cx, cx, arcR, 135, 270), fill: "none", stroke: "var(--control)", "stroke-width": stroke, "stroke-linecap": "round" }));
    if (fraction > 0) {
      g.append(svg("path", { d: arcPath(cx, cx, arcR, 135, 270 * fraction), fill: "none", stroke: color, "stroke-width": stroke, "stroke-linecap": "round" }));
      const a = ((135 + 270 * fraction) * Math.PI) / 180;
      g.append(svg("circle", { cx: cx + arcR * Math.cos(a), cy: cx + arcR * Math.sin(a), r: stroke * 0.9, fill: "#fff", stroke: "rgba(0,0,0,.25)", "stroke-width": 1 }));
    }
  }
  if (segments.length) {
    const each = 360 / segments.length, r = (centerR + ringOuter) / 2, width = ringOuter - centerR;
    segments.forEach(([, selected], i) => {
      g.append(svg("path", { d: arcPath(cx, cx, r, -90 + i * each + 1.5, each - 3), fill: "none", "stroke-width": width,
        stroke: selected ? `color-mix(in srgb, ${color} 22%, transparent)` : "var(--control)" }));
    });
  }
  if (center === "number" || center === "temp") g.append(svg("circle", { cx, cy: cx, r: centerR, fill: "var(--cell)", stroke: "var(--divider)", "stroke-width": 2 }));

  const box = el("div", { class: "dial" }, el("div", { class: "dtitle" }, cardLabel(c)));
  if (power !== null) box.append(el("span", { class: "switch", style: { background: power ? "var(--active)" : "var(--control)", justifyContent: power ? "flex-end" : "flex-start" } }, el("i")));
  const stage = el("div", { style: { position: "absolute", left: 0, right: 0, top: "36px", bottom: "8px", display: "flex", alignItems: "center", justifyContent: "center", direction: "ltr" } });
  const holder = el("div", { style: { position: "relative", width: side + "px", height: side + "px" } }, g);
  // Segment icons and the centre, laid over the drawing.
  if (segments.length) {
    const r = (centerR + ringOuter) / 2, each = 360 / segments.length;
    segments.forEach(([name], i) => {
      const a = ((-90 + (i + 0.5) * each) * Math.PI) / 180;
      const size = Math.min(30, (ringOuter - centerR) * 0.45);
      holder.append(icon(name, { style: { position: "absolute", fontSize: size + "px", left: cx + r * Math.cos(a) - size / 2 + "px", top: cx + r * Math.sin(a) - size / 2 + "px" } }));
    });
  }
  const mid = el("div", { style: { position: "absolute", left: cx - centerR + "px", top: cx - centerR + "px", width: centerR * 2 + "px", height: centerR * 2 + "px",
    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", overflow: "hidden", borderRadius: "50%" } });
  if (center === "halves" || center === "coverhalves") {
    const accent = center === "coverhalves" ? "var(--cover)" : "var(--active)";
    const lit = center === "coverhalves" ? st !== "closed" : on;
    const label = (t, strong) => el("span", { style: { fontSize: centerR * 2 * 0.15 + "px", color: strong ? "var(--text)" : "var(--dmuted)" } }, t);
    mid.style.border = "2px solid var(--divider)";
    mid.append(
      el("div", { style: { flex: 1, width: "100%", display: "flex", alignItems: "center", justifyContent: "center", paddingBottom: centerR * 0.24 + "px",
        background: lit ? `color-mix(in srgb, ${accent} 30%, var(--cell))` : "var(--cell)" } }, label(center === "coverhalves" ? "فتح" : "تشغيل", lit)),
      el("div", { style: { height: "1px", width: "100%", background: "var(--divider)" } }),
      el("div", { style: { flex: 1, width: "100%", display: "flex", alignItems: "center", justifyContent: "center", paddingTop: centerR * 0.24 + "px",
        background: lit ? "var(--cell)" : "var(--control)" } }, label(center === "coverhalves" ? "إغلاق" : "إطفاء", !lit)),
      el("span", { style: { position: "absolute", width: centerR * 0.52 + "px", height: centerR * 0.52 + "px", borderRadius: "50%", background: "var(--cell)",
        border: "1px solid var(--divider)", display: "flex", alignItems: "center", justifyContent: "center" } },
        icon(cardIcon(c), { style: { fontSize: centerR * 0.3 + "px", color: lit ? accent : "var(--icon)" } })));
  } else if (center === "wheel") {
    mid.style.background = "radial-gradient(circle, #fff 0%, rgba(255,255,255,0) 70%), conic-gradient(from 90deg, red, yellow, lime, cyan, blue, magenta, red)";
  } else if (center === "number") {
    mid.append(el("span", { style: { fontSize: "40px", fontWeight: 300 } }, on ? "50" : "0", el("small", { style: { fontSize: "18px" } }, "%")),
      el("span", { style: { fontSize: "14px", color: "var(--dmuted)" } }, { open: "مفتوح", closed: "مغلق" }[st] || ""));
  } else if (center === "temp") {
    mid.append(el("span", { style: { fontSize: "40px", fontWeight: 300 } }, "22", el("small", { style: { fontSize: "18px" } }, ".0°C")));
  }
  holder.append(mid);
  stage.append(holder);
  box.append(stage);
  if (fraction !== null) box.append(el("span", { class: "minus" }, rbtn("mdi:minus", 48)), el("span", { class: "plus" }, rbtn("mdi:plus", 48)));
  return box;
}

// Only cells a card can reach take a new one; the others stay blank (the panel fills
// gaps in order, so a card there would land elsewhere).
function emptyCell(screen, col, row, open) {
  const node = el("div", {
    class: "cell empty" + (open ? " open" : ""), title: open ? "إضافة جهاز" : "",
    style: { gridColumn: `${col + 1}`, gridRow: `${row + 1}` },
    onclick: () => { if (open) entitySheet(screen, col, row); },
    ondragover: (ev) => { if (dragFrom !== null && open) { ev.preventDefault(); node.classList.add("over"); } },
    ondragleave: () => node.classList.remove("over"),
    ondrop: (ev) => {
      ev.preventDefault();
      const from = dragFrom;
      if (from === null) return;
      change((l) => {
        const cards = l.pages[S.page].cards;
        const [moved] = cards.splice(from, 1);
        const at = indexForCell(cards, moved, screen, col, row);
        cards.splice(at, 0, moved);
        S.sel = at;
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
  const page = L().pages[S.page];
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
    variants.length > 1 ? field("الشكل", chips(variants.map((v) => [v, SHAPES[v] || v]), c.card, (v) => edit(() => setCardType(c, v)))) : null,
    field("الحجم", chips(["1x1", "2x1", "2x2"].map((v) => [v, SIZES[v], !spec.sizes.includes(v)]), sizeOf(c), (v) => edit(() => { c.size = v; }))),
    field("الاسم على الشاشة", el("input", { value: c.label || "", placeholder: d?.name || "",
      onchange: (e) => edit(() => { const v = e.target.value.trim(); v ? c.label = v : delete c.label; }) })),
    field("الأيقونة", iconInput(c.icon, cardIcon({ ...c, icon: undefined }), (v) => edit(() => { v && validIcon(v) ? c.icon = v : delete c.icon; }))),
    field("طلب تأكيد قبل التنفيذ", chips([["", "تلقائي"], ["true", "نعم"], ["false", "لا"]], c.confirm === undefined ? "" : String(c.confirm),
      (v) => edit(() => { v === "" ? delete c.confirm : c.confirm = v === "true"; }))),
    ...optionFields,
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: () => { closeSheet(); S.sel = null; render(); } }, "تم"),
      el("span", { class: "spacer" }),
      el("button", { class: "danger", onclick: () => { change((l) => { l.pages[S.page].cards.splice(page.cards.indexOf(c), 1); S.sel = null; }); closeSheet(); } }, "حذف البطاقة")),
  );
}

// Pick a device for an empty cell: this room's first, then the rest of the house.
function entitySheet(screen, col, row) {
  const page = L().pages[S.page];
  const onPage = new Set(page.cards.flatMap((c) => c.entities));
  const list = el("div", {});
  const search = el("input", { placeholder: "بحث باسم الجهاز", oninput: () => fill() });
  const areaName = Object.fromEntries(S.catalog.areas.map((a) => [a.id, a.name]));
  function fill() {
    const q = search.value.trim().toLowerCase();
    const match = (e) => !onPage.has(e.entity_id) && kindOf(e.entity_id) && (!q || device(e.entity_id).name.toLowerCase().includes(q) || e.entity_id.includes(q));
    const here = S.catalog.entities.filter((e) => e.area_id === page.id && match(e));
    const rest = S.catalog.entities.filter((e) => e.area_id !== page.id && match(e) && (q || e.controllable));
    list.replaceChildren();
    const group = (title, items) => {
      if (!items.length) return;
      const ul = el("ul", { class: "picklist" });
      for (const e of items.slice(0, 200)) {
        const probe = { card: R.kinds[kindOf(e.entity_id)].default, entities: [e.entity_id] };
        ul.append(el("li", { onclick: () => {
          change((l) => {
            const cards = l.pages[S.page].cards;
            const card = newCard(l, e.entity_id);
            const at = indexForCell(cards, card, screen, col, row);
            cards.splice(at, 0, card);
            S.sel = at;
          });
          closeSheet();
        } }, icon(cardIcon(probe)), el("span", { class: "grow" }, device(e.entity_id).name), el("span", { class: "muted" }, e.area_id && e.area_id !== page.id ? areaName[e.area_id] || "" : "")));
      }
      list.append(el("h4", {}, title), ul);
    };
    group("في هذه الغرفة", here);
    group(q ? "في باقي المنزل" : "أجهزة التحكم في باقي المنزل", rest);
    if (!list.children.length) list.append(el("p", { class: "muted" }, q ? "لا نتائج." : "كل أجهزة هذه الغرفة على الشاشة. ابحث عن جهاز من غرفة أخرى."));
  }
  fill();
  sheet(el("h3", {}, "إضافة جهاز"), search, list, el("div", { class: "actions" }, el("button", { onclick: closeSheet }, "إغلاق")));
  search.focus();
}

// Add a room: from HA's areas (with its devices), or an empty one.
function roomSheet() {
  const layout = L();
  const have = new Set(layout.pages.map((p) => p.id));
  const list = el("ul", { class: "picklist" });
  for (const a of S.catalog.areas) {
    if (have.has(a.id)) continue;
    const n = controlsIn(a.id).length;
    list.append(el("li", { onclick: () => { change((l) => { addRoomPages(l, [a.id]); S.page = l.pages.length - 1; }); closeSheet(); } },
      icon(a.icon || "mdi:floor-plan"), el("span", { class: "grow" }, a.name), el("span", { class: "muted" }, `${n} جهاز`)));
  }
  sheet(
    el("h3", {}, "إضافة غرفة"),
    el("p", { class: "muted" }, "الغرفة تُضاف بكل أجهزتها. يمكنك بعدها حذف أو تعديل أي بطاقة."),
    list.children.length ? list : el("p", { class: "muted" }, "كل غرف Home Assistant مضافة."),
    el("div", { class: "actions" },
      el("button", { onclick: () => { change((l) => { l.pages.push({ id: pageId("page", have), title: "صفحة جديدة", cards: [] }); S.page = l.pages.length - 1; }); closeSheet(); pageSheet(S.page); } }, "+ صفحة فارغة"),
      el("span", { class: "spacer" }),
      el("button", { onclick: closeSheet }, "إغلاق")),
  );
}

function pageSheet(i) {
  const p = L().pages[i];
  if (!p) return;
  const edit = (fn) => { change(() => fn()); pageSheet(i); };
  sheet(
    el("h3", {}, p.title),
    field("الاسم", el("input", { value: p.title, onchange: (e) => { const v = e.target.value.trim(); if (v) edit(() => { p.title = v; }); } })),
    field("الأيقونة", iconInput(p.icon, "mdi:floor-plan", (v) => edit(() => { v && validIcon(v) ? p.icon = v : delete p.icon; }))),
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: closeSheet }, "تم"),
      el("span", { class: "spacer" }),
      el("button", { class: "danger", onclick: () => {
        if (!confirm(`حذف الغرفة "${p.title}" وكل بطاقاتها من هذه الشاشة؟`)) return;
        change((l) => { l.pages.splice(i, 1); S.page = Math.max(0, Math.min(S.page, l.pages.length - 1)); S.sel = null; });
        closeSheet();
      } }, "حذف الغرفة")),
  );
}

// ---------- settings and permissions ----------

const LANGS = [["ar", "العربية"], ["fr", "Français"], ["en", "English"]];
const THEMES = [["light", "فاتح"], ["dark", "داكن"]];
const ROLES = [["guest", "بدون رمز"], ["user", "رمز المستخدم"], ["installer", "رمز المُركِّب"]];
const CAPS = [["control", "التحكم"], ["apps", "فتح التطبيقات"], ["edit_cards", "تعديل البطاقات"], ["reorder", "إعادة الترتيب"], ["settings", "الإعدادات المحلية"]];
const BUILT_IN = { lang: "ar", theme: "light", screensaver: { idle_seconds: 60, dim_brightness: 10 }, app_return_seconds: 300 };

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
  };
  form.append(
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
  const general = el("div", { class: "form" }, el("label", {}, "اسم الشاشة"), name, el("label", {}, "مكان تركيبها"), place);
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
        const r = await put(`api/panels/${enc(pid)}/config`, { overrides: readDisplay() });
        if (!r.ok) { msg.className = "msg error"; msg.textContent = "لم يُحفظ: " + (r.body?.error || r.status); return; }
        await refresh(); await loadConfig(pid);
        toast(p.online ? "حُفظ ووصل للشاشة." : "حُفظ. ستستلمه الشاشة عند اتصالها.");
        render();
      } }, "حفظ")), msg),
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
      const cb = el("input", { type: "checkbox", checked: cfg.effective.access[role].includes(cap), disabled: cap === "control" });
      boxes[role + ":" + cap] = cb;
      tr.append(el("td", {}, cb));
    }
    table.append(tr);
  }
  view.append(el("div", { class: "box" },
    el("h3", {}, "ما يُسمح به على هذه الشاشة"),
    el("p", { class: "muted" }, "التحكم مسموح دائماً. إضافة أو حذف أجهزة وغرف يتم من هنا فقط، ولا يُسمح به من الشاشة."),
    table,
    el("div", { class: "row", style: { marginTop: "14px" } }, el("button", { class: "solid", onclick: async () => {
      const access = {};
      for (const [role] of ROLES) access[role] = CAPS.map(([c]) => c).filter((c) => boxes[role + ":" + c].checked);
      const r = await put(`api/panels/${enc(pid)}/config`, { access });
      if (!r.ok) { alert("لم يُحفظ: " + (r.body?.error || r.status)); return; }
      await loadConfig(pid);
      toast(p.online ? "حُفظ ووصل للشاشة." : "حُفظ. ستستلمه الشاشة عند اتصالها.");
    } }, "حفظ"))));
}

// ---------- project ----------

function renderProject(view) {
  const tabs = [["general", "عام"], ["devices", "أنواع الأجهزة"]];
  view.append(el("div", { class: "titlebar" },
    el("h2", {}, S.site?.name || "المشروع"),
    el("div", { class: "tabs" }, tabs.map(([id, label]) =>
      el("button", { class: S.view.tab === id ? "active" : "", onclick: () => { S.view.tab = id; render(); } }, label)))));
  if (!S.site) { loadSite().then(render); return; }
  if (S.view.tab === "devices") return renderDevices(view);

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
        const r = await put("api/site", { name: name.value.trim(), defaults: read() });
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
  const bar = () => el("div", { class: "wsteps" }, [1, 2, 3].map((n) => el("span", { class: n <= st.step ? "done" : "" })));
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
