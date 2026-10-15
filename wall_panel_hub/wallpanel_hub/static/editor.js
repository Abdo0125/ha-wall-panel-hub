// Wall Panel Hub layout editor. Four panes, like ETS:
// HA building (source) | pages | page preview | properties, plus a message pane.
// Changes go to a draft on the hub (kept across reloads); "send" pushes it to the panel.
"use strict";

// ---------- helpers ----------

// Text only: names come from HA and panels, never render them as HTML.
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(e.dataset, v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) e.append(c instanceof Node ? c : String(c));
  return e;
}
const $ = (id) => document.getElementById(id);
const clone = (o) => JSON.parse(JSON.stringify(o));

async function api(path, options = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let body = null;
  try { body = await r.json(); } catch (_) { /* empty body */ }
  return { status: r.status, ok: r.ok, body };
}

// Same rules as the schema and the panel (HaCatalog.kt / LayoutEditing.kt).
const TILE_TYPES = {
  light: "light", switch: "switch", input_boolean: "switch", cover: "cover", climate: "climate",
  fan: "fan", lock: "lock", scene: "scene", script: "script", button: "button", input_button: "button",
  sensor: "generic", binary_sensor: "generic", automation: "generic",
};
const TYPE_DOMAINS = {
  light: ["light"], switch: ["switch", "input_boolean"], cover: ["cover"], climate: ["climate"], fan: ["fan"],
  lock: ["lock"], scene: ["scene"], script: ["script"], button: ["button", "input_button"],
};
const domainOf = (id) => id.split(".")[0];
const typesFor = (id) => [...new Set([TILE_TYPES[domainOf(id)], "generic"].filter(Boolean))];

function pageId(wanted, taken) {
  const base = (wanted || "").toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "page";
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}

// ---------- state ----------

const S = {
  catalog: { floors: [], areas: [], entities: [] },
  catalogError: null,
  panels: [],          // from the hub: id, name, online, sent, draft
  work: {},            // pid -> { layout, base, unsent, undo: [], redo: [] }
  current: null,       // pid
  page: 0,
  sel: { kind: "panel" },
  open: new Set(),     // expanded tree nodes
  notices: [],         // transient messages: {level, text, actions}
};
const W = () => S.work[S.current];
const L = () => W()?.layout;
const entityById = () => Object.fromEntries(S.catalog.entities.map((e) => [e.entity_id, e]));

function blankLayout(name) {
  return { version: 1, panel: { name: name || "Panel", lang: "ar", theme: "dark" }, pages: [] };
}

async function loadState() {
  const r = await api("api/editor/state");
  S.panels = r.body.panels;
  for (const p of S.panels) {
    if (p.draft) S.work[p.id] = { layout: p.draft.layout, base: p.draft.base_version, unsent: true, undo: [], redo: [] };
    else S.work[p.id] = { layout: p.sent ? clone(p.sent) : blankLayout(p.name), base: p.sent ? p.sent.version : null, unsent: false, undo: [], redo: [] };
  }
  if (!S.current || !S.work[S.current]) S.current = S.panels[0]?.id || null;
}

async function loadCatalog(refresh) {
  const r = await api("api/editor/catalog" + (refresh ? "?refresh=1" : ""));
  if (r.ok) { S.catalog = r.body; S.catalogError = null; }
  else S.catalogError = r.body?.error || `HTTP ${r.status}`;
}

// ---------- changes, undo, draft autosave ----------

const saveTimers = {};
function change(fn) {
  const w = W();
  if (!w) return;
  w.undo.push(JSON.stringify(w.layout));
  if (w.undo.length > 100) w.undo.shift();
  w.redo = [];
  fn(w.layout);
  w.unsent = true;
  scheduleSave(S.current);
  render();
}

function scheduleSave(pid) {
  clearTimeout(saveTimers[pid]);
  saveTimers[pid] = setTimeout(() => saveDraft(pid), 600);
}

async function saveDraft(pid) {
  const w = S.work[pid];
  await api(`api/editor/panels/${encodeURIComponent(pid)}/draft`, {
    method: "PUT", body: JSON.stringify({ layout: w.layout, base_version: w.base }),
  });
}

function undo() {
  const w = W();
  if (!w?.undo.length) return;
  w.redo.push(JSON.stringify(w.layout));
  w.layout = JSON.parse(w.undo.pop());
  w.unsent = true;
  scheduleSave(S.current);
  render();
}

function redo() {
  const w = W();
  if (!w?.redo.length) return;
  w.undo.push(JSON.stringify(w.layout));
  w.layout = JSON.parse(w.redo.pop());
  w.unsent = true;
  scheduleSave(S.current);
  render();
}

// ---------- edits ----------

function tilesForArea(areaId) {
  return S.catalog.entities.filter((e) => e.area_id === areaId && e.controllable).map((e) => ({ type: e.type, entity: e.entity_id }));
}

function addAreaPages(layout, areaIds, at) {
  const areas = Object.fromEntries(S.catalog.areas.map((a) => [a.id, a]));
  const newPages = [];
  for (const id of areaIds) {
    const area = areas[id];
    if (!area) continue;
    const existing = layout.pages.find((p) => p.id === id);
    if (existing) {  // re-import: only what is missing
      const have = new Set(existing.tiles.map((t) => t.entity));
      existing.tiles.push(...tilesForArea(id).filter((t) => !have.has(t.entity)));
      continue;
    }
    const taken = new Set([...layout.pages, ...newPages].map((p) => p.id));
    const page = { id: pageId(id, taken), title: area.name, tiles: tilesForArea(id) };
    if (/^mdi:[a-z0-9-]+$/.test(area.icon || "")) page.icon = area.icon;
    newPages.push(page);
  }
  layout.pages.splice(at ?? layout.pages.length, 0, ...newPages);
  return newPages.length;
}

function addTiles(page, entityIds, at) {
  const have = new Set(page.tiles.map((t) => t.entity));
  const byId = entityById();
  const tiles = entityIds.filter((id) => !have.has(id) && byId[id]).map((id) => ({ type: byId[id].type, entity: id }));
  page.tiles.splice(at ?? page.tiles.length, 0, ...tiles);
}

async function copyPageTo(pid, page) {
  if (pid === S.current) return;
  const w = S.work[pid];
  w.undo.push(JSON.stringify(w.layout));
  const copy = clone(page);
  copy.id = pageId(copy.id, new Set(w.layout.pages.map((p) => p.id)));
  w.layout.pages.push(copy);
  w.unsent = true;
  await saveDraft(pid);
  const name = S.panels.find((p) => p.id === pid)?.name;
  notice("ok", `نُسخت الصفحة "${page.title}" إلى ${name}`);
}

// ---------- send / discard ----------

async function send(force) {
  const pid = S.current;
  const problems = validate(L()).filter((m) => m.level === "error");
  if (problems.length) { notice("error", "لا يمكن الإرسال قبل إصلاح الأخطاء أدناه."); return; }
  clearTimeout(saveTimers[pid]);
  await saveDraft(pid);
  const r = await api(`api/editor/panels/${encodeURIComponent(pid)}/send`, { method: "POST", body: JSON.stringify({ force: !!force }) });
  if (r.status === 409) {
    notice("warn", `عُدّل تخطيط هذه الشاشة (v${r.body.current?.version}) بعد بدء تعديلاتك، غالباً من الشاشة نفسها.`, [
      ["إرسال تعديلاتي فوقه", () => send(true)],
      ["تجاهل تعديلاتي", discard],
    ]);
    return;
  }
  if (!r.ok) { notice("error", "رفض الـ Hub التخطيط: " + (r.body?.error || r.status)); return; }
  await loadState();
  notice("ok", `أُرسل التخطيط v${r.body.version}` + (r.body.online ? " وطُبّق على الشاشة." : ". الشاشة غير متصلة الآن، وستستلمه عند اتصالها."));
  render();
}

async function discard() {
  const pid = S.current;
  if (!confirm("تجاهل كل التعديلات غير المرسلة لهذه الشاشة؟")) return;
  clearTimeout(saveTimers[pid]);
  await api(`api/editor/panels/${encodeURIComponent(pid)}/draft`, { method: "DELETE" });
  delete S.work[pid];
  await loadState();
  S.page = 0; S.sel = { kind: "panel" };
  render();
}

// ---------- validation (mirrors the schema; the hub re-checks on send) ----------

function validate(layout) {
  const out = [];
  if (!layout) return out;
  const err = (text) => out.push({ level: "error", text });
  const warn = (text) => out.push({ level: "warn", text });
  const byId = entityById();
  const p = layout.panel;
  if (!p.name?.trim()) err("اسم الشاشة فارغ.");
  const idle = p.screensaver?.idle_seconds;
  if (idle !== undefined && !(idle >= 10)) err("مدة شاشة التوقف أقل من 10 ثوانٍ.");
  const dim = p.screensaver?.dim_brightness;
  if (dim !== undefined && !(dim >= 0 && dim <= 100)) err("سطوع التعتيم خارج 0 إلى 100.");
  if (!layout.pages.length) err("لا توجد صفحات.");
  const ids = new Set();
  for (const page of layout.pages) {
    if (ids.has(page.id)) err(`معرّف الصفحة "${page.id}" مكرر.`);
    ids.add(page.id);
    if (!/^[a-z0-9_]+$/.test(page.id)) err(`معرّف الصفحة "${page.id}" غير صالح.`);
    if (!page.title?.trim()) err(`الصفحة "${page.id}" بلا عنوان.`);
    if (page.icon && !/^mdi:[a-z0-9-]+$/.test(page.icon)) err(`أيقونة الصفحة "${page.title}" يجب أن تكون بصيغة mdi:name.`);
    if (!page.tiles.length) warn(`الصفحة "${page.title}" فارغة.`);
    for (const t of page.tiles) {
      const allowed = TYPE_DOMAINS[t.type];
      if (allowed && !allowed.includes(domainOf(t.entity))) err(`${t.entity}: النوع ${t.type} لا يناسب هذا الكيان.`);
      if (t.icon && !/^mdi:[a-z0-9-]+$/.test(t.icon)) err(`${t.entity}: الأيقونة يجب أن تكون بصيغة mdi:name.`);
      if (S.catalog.entities.length && !byId[t.entity]) warn(`${t.entity} غير موجود في Home Assistant (في "${page.title}").`);
    }
  }
  return out;
}

function notice(level, text, actions = []) {
  S.notices = [{ level, text, actions }];
  renderMessages();
}

// ---------- rendering ----------

// Each pane re-creates its drag handlers when it redraws; old ones are destroyed first.
const sortables = {};
function resetSortables(section) {
  for (const s of sortables[section] || []) s.destroy();
  sortables[section] = [];
}
function sortable(section, node, options) {
  sortables[section].push(Sortable.create(node, { animation: 120, ...options }));
}

function render() {
  renderTabs();
  renderTree();
  renderPages();
  renderGrid();
  renderProps();
  renderMessages();
  const w = W();
  $("undo").disabled = !w?.undo.length;
  $("redo").disabled = !w?.redo.length;
  $("send").disabled = !w;
  $("discard").disabled = !w?.unsent;
}

function renderTabs() {
  resetSortables("tabs");
  const tabs = $("tabs");
  tabs.replaceChildren();
  if (!S.panels.length) { tabs.append(el("span", { class: "muted" }, "لا توجد شاشات مربوطة. اربط شاشة أولاً.")); return; }
  for (const p of S.panels) {
    const w = S.work[p.id];
    const tab = el("div", {
      class: "tab" + (p.id === S.current ? " active" : ""),
      dataset: { pid: p.id },
      onclick: () => { S.current = p.id; S.page = 0; S.sel = { kind: "panel" }; render(); },
    },
      el("span", { class: "dot", style: `color:${p.online ? "var(--ok)" : "var(--muted)"}` }, p.online ? "●" : "○"),
      p.name,
      w?.unsent ? el("span", { class: "unsent", title: "تعديلات غير مرسلة" }, "✱") : null,
    );
    tabs.append(tab);
    // Each tab is a drop target: a page dropped on it is copied to that panel.
    if (p.id !== S.current) {
      sortable("tabs", tab, {
        group: { name: "tab", put: ["pages"], pull: false }, sort: false,
        onAdd: async (evt) => {
          const page = L().pages[Number(evt.item.dataset.index)];
          evt.item.remove();
          if (page) await copyPageTo(p.id, page);
          render();
        },
      });
    }
  }
}

function usage() {
  const used = {};
  for (const p of S.panels) {
    for (const page of S.work[p.id]?.layout.pages || []) {
      for (const t of page.tiles) (used[t.entity] ||= new Set()).add(p.name);
    }
  }
  return used;
}

function renderTree() {
  resetSortables("tree");
  const tree = $("tree");
  tree.replaceChildren();
  if (S.catalogError) { tree.append(el("p", { class: "msg error" }, "تعذّرت قراءة المبنى من HA: " + S.catalogError)); return; }
  const q = $("search").value.trim().toLowerCase();
  const controlsOnly = $("controlsOnly").checked;
  const unusedOnly = $("unusedOnly").checked;
  const used = usage();
  const match = (e) =>
    (!controlsOnly || e.controllable) && (!unusedOnly || !used[e.entity_id]) &&
    (!q || e.name.toLowerCase().includes(q) || e.entity_id.includes(q));

  const entitiesOf = (areaId) => S.catalog.entities.filter((e) => (e.area_id || null) === areaId && match(e));
  const filtering = !!q || unusedOnly;

  function entityList(areaId) {
    const ul = el("ul", { class: "tree" });
    for (const e of entitiesOf(areaId)) {
      const where = used[e.entity_id];
      ul.append(el("li", { class: "ent", dataset: { entity: e.entity_id }, title: e.entity_id },
        el("span", { class: "badge" }, e.type.toUpperCase().slice(0, 5)),
        el("span", { class: "name" }, e.name),
        where ? el("span", { class: "used", title: [...where].join("، ") }, `⧉${where.size}`) : null,
      ));
    }
    sortable("tree", ul, { group: { name: "ha", pull: "clone", put: false }, sort: false });
    return ul;
  }

  function areaNode(area) {
    const ents = entitiesOf(area.id);
    if (filtering && !ents.length) return null;
    const key = "a:" + area.id;
    const open = S.open.has(key) || filtering;
    const li = el("li", { class: "area", dataset: { area: area.id } },
      el("div", { class: "node" },
        el("span", { class: "twist", onclick: () => { S.open.has(key) ? S.open.delete(key) : S.open.add(key); renderTree(); } }, open ? "▾" : "▸"),
        el("span", {}, area.name),
        el("span", { class: "count" }, ents.length),
      ));
    if (open) li.append(entityList(area.id));
    return li;
  }

  function areaList(areas) {
    const ul = el("ul", { class: "tree" });
    for (const a of areas) { const n = areaNode(a); if (n) ul.append(n); }
    sortable("tree", ul, { group: { name: "ha-area", pull: "clone", put: false }, sort: false, handle: ".node" });
    return ul;
  }

  const root = el("ul", { class: "tree root" });
  const floors = [...S.catalog.floors, { id: null, name: "بدون طابق" }];
  for (const f of floors) {
    const areas = S.catalog.areas.filter((a) => (a.floor_id || null) === f.id);
    if (!areas.length) continue;
    const key = "f:" + f.id;
    const open = !S.open.has(key + ":closed") || filtering;
    const li = el("li", { class: "floor", dataset: f.id ? { floor: f.id } : {} },
      el("div", { class: "node" },
        el("span", { class: "twist", onclick: () => { S.open.has(key + ":closed") ? S.open.delete(key + ":closed") : S.open.add(key + ":closed"); renderTree(); } }, open ? "▾" : "▸"),
        el("span", {}, f.name),
        el("span", { class: "count" }, areas.length),
      ));
    if (open) li.append(areaList(areas));
    root.append(li);
  }
  // Floors can be dragged as a whole (one page per area); "no floor" cannot.
  sortable("tree", root, { group: { name: "ha-floor", pull: "clone", put: false }, sort: false, handle: ".node", filter: "li:not([data-floor])" });
  tree.append(root);

  const loose = entitiesOf(null);
  if (loose.length) {
    tree.append(el("h2", { style: "margin-top:12px" }, "بدون منطقة"));
    tree.append(entityList(null));
  }
}

function renderPages() {
  resetSortables("pages");
  const ul = $("pages");
  ul.replaceChildren();
  const layout = L();
  if (!layout) return;
  layout.pages.forEach((p, i) => {
    ul.append(el("li", {
      class: i === S.page ? "active" : "",
      dataset: { index: i },
      onclick: () => { S.page = i; S.sel = { kind: "page", page: i }; render(); },
    }, el("span", {}, p.title || p.id), el("span", { class: "muted" }, p.tiles.length)));
  });
  sortable("pages", ul, {
    group: { name: "pages", pull: "clone", put: ["ha-area", "ha-floor"] },
    onUpdate: (evt) => change((l) => {
      l.pages.splice(evt.newIndex, 0, l.pages.splice(evt.oldIndex, 1)[0]);
      S.page = evt.newIndex;
    }),
    onAdd: (evt) => {
      const { area, floor } = evt.item.dataset;
      evt.item.remove();
      const ids = floor ? S.catalog.areas.filter((a) => a.floor_id === floor).map((a) => a.id) : area ? [area] : [];
      change((l) => { if (addAreaPages(l, ids, evt.newIndex)) S.page = evt.newIndex; });
    },
  });
}

function renderGrid() {
  resetSortables("grid");
  const grid = $("grid");
  grid.replaceChildren();
  const layout = L();
  const page = layout?.pages[S.page];
  $("pageTitle").textContent = page ? `المعاينة · ${page.title}` : "المعاينة";
  if (!page) { grid.append(el("p", { class: "muted" }, layout ? "أضف صفحة أو اسحب غرفة إلى قائمة الصفحات." : "")); return; }
  const byId = entityById();
  page.tiles.forEach((t, i) => {
    const e = byId[t.entity];
    const selected = S.sel.kind === "tile" && S.sel.page === S.page && S.sel.tile === i;
    grid.append(el("div", {
      class: "tile" + (selected ? " selected" : "") + (S.catalog.entities.length && !e ? " missing" : ""),
      dataset: { index: i },
      onclick: () => { S.sel = { kind: "tile", page: S.page, tile: i }; render(); },
    },
      el("span", { class: "x", title: "حذف", onclick: (ev) => { ev.stopPropagation(); removeTile(i); } }, "✕"),
      el("span", { class: "badge" }, t.type.toUpperCase()),
      el("div", {}, el("div", { class: "label" }, t.label || e?.name || t.entity), el("div", { class: "eid" }, t.entity)),
    ));
  });
  sortable("grid", grid, {
    group: { name: "tiles", put: ["ha", "ha-area"] },
    onUpdate: (evt) => change((l) => {
      const tiles = l.pages[S.page].tiles;
      tiles.splice(evt.newIndex, 0, tiles.splice(evt.oldIndex, 1)[0]);
      S.sel = { kind: "tile", page: S.page, tile: evt.newIndex };
    }),
    onAdd: (evt) => {
      const { entity, area } = evt.item.dataset;
      evt.item.remove();
      const ids = entity ? [entity] : tilesForArea(area).map((t) => t.entity);
      change((l) => addTiles(l.pages[S.page], ids, evt.newIndex));
    },
  });
}

function removeTile(i) {
  change((l) => {
    l.pages[S.page].tiles.splice(i, 1);
    S.sel = { kind: "page", page: S.page };
  });
}

function field(label, input) {
  return el("div", { class: "field" }, el("label", {}, label), input);
}

function textInput(value, onCommit, attrs = {}) {
  // Commit on change (blur/enter), so typing a word is one undo step.
  return el("input", { value: value ?? "", ...attrs, onchange: (e) => onCommit(e.target.value) });
}

function select(options, value, onCommit) {
  const s = el("select", { onchange: (e) => onCommit(e.target.value) });
  for (const [v, label] of options) s.append(el("option", { value: v, selected: v === value }, label));
  return s;
}

function renderProps() {
  const box = $("props");
  box.replaceChildren();
  const layout = L();
  if (!layout) return;
  const sel = S.sel;

  if (sel.kind === "tile") {
    const t = layout.pages[sel.page]?.tiles[sel.tile];
    if (!t) { S.sel = { kind: "panel" }; return renderProps(); }
    const e = entityById()[t.entity];
    box.append(
      el("p", {}, el("b", {}, e?.name || t.entity), el("br"), el("span", { class: "muted" }, t.entity)),
      field("النوع", select(typesFor(t.entity).map((v) => [v, v]), t.type, (v) => change(() => { t.type = v; }))),
      field("الاسم على الشاشة", textInput(t.label, (v) => change(() => { v.trim() ? t.label = v.trim() : delete t.label; }), { placeholder: e?.name || "" })),
      field("الأيقونة (mdi:...)", textInput(t.icon, (v) => change(() => { v.trim() ? t.icon = v.trim() : delete t.icon; }), { placeholder: "mdi:lightbulb", dir: "ltr" })),
      field("تأكيد قبل التنفيذ", select([["", "افتراضي (الأقفال فقط)"], ["true", "نعم"], ["false", "لا"]],
        t.confirm === undefined ? "" : String(t.confirm),
        (v) => change(() => { v === "" ? delete t.confirm : t.confirm = v === "true"; }))),
      el("button", { class: "danger", onclick: () => removeTile(sel.tile) }, "حذف البلاطة"),
    );
    return;
  }

  if (sel.kind === "page") {
    const p = layout.pages[sel.page];
    if (!p) { S.sel = { kind: "panel" }; return renderProps(); }
    box.append(
      field("عنوان الصفحة", textInput(p.title, (v) => change(() => { p.title = v; }))),
      field("المعرّف", el("input", { value: p.id, disabled: true, dir: "ltr" })),
      field("الأيقونة (mdi:...)", textInput(p.icon, (v) => change(() => { v.trim() ? p.icon = v.trim() : delete p.icon; }), { placeholder: "mdi:sofa", dir: "ltr" })),
      el("button", { class: "danger", onclick: () => change((l) => {
        l.pages.splice(sel.page, 1);
        S.page = Math.max(0, Math.min(S.page, l.pages.length - 1));
        S.sel = { kind: "panel" };
      }) }, "حذف الصفحة"),
    );
    return;
  }

  // Panel settings.
  const panel = layout.panel;
  const ss = panel.screensaver || {};
  const setSS = (key, v) => change(() => {
    panel.screensaver = { ...(panel.screensaver || {}) };
    if (v === "" || isNaN(Number(v))) delete panel.screensaver[key];
    else panel.screensaver[key] = Number(v);
  });
  const hubPanel = S.panels.find((p) => p.id === S.current);
  box.append(
    el("p", { class: "muted" }, `${hubPanel?.model || ""} · المرسل: ${hubPanel?.sent ? "v" + hubPanel.sent.version : "لا شيء بعد"}`),
    field("اسم الشاشة", textInput(panel.name, (v) => change(() => { panel.name = v; }))),
    field("اللغة", select([["ar", "العربية"], ["fr", "Français"], ["en", "English"]], panel.lang || "ar", (v) => change(() => { panel.lang = v; }))),
    field("المظهر", select([["dark", "داكن"], ["light", "فاتح"]], panel.theme || "dark", (v) => change(() => { panel.theme = v; }))),
    field("شاشة التوقف بعد (ثانية)", textInput(ss.idle_seconds, (v) => setSS("idle_seconds", v), { type: "number", min: 10, placeholder: "60" })),
    field("سطوع التعتيم (%)", textInput(ss.dim_brightness, (v) => setSS("dim_brightness", v), { type: "number", min: 0, max: 100, placeholder: "10" })),
  );
}

function renderMessages() {
  const box = $("messages");
  box.replaceChildren();
  const w = W();
  for (const n of S.notices) {
    box.append(el("div", { class: "msg " + n.level }, n.text,
      ...n.actions.map(([label, fn]) => el("button", { onclick: () => { S.notices = []; fn(); } }, label))));
  }
  if (!w) return;
  if (w.unsent) box.append(el("div", { class: "msg warn" }, "✱ تعديلات غير مرسلة. اضغط \"إرسال للشاشة\" لتطبيقها."));
  const sent = S.panels.find((p) => p.id === S.current)?.sent;
  if (w.unsent && sent && w.base !== null && sent.version !== w.base) {
    box.append(el("div", { class: "msg warn" }, `تنبيه: تخطيط الشاشة تغيّر (v${sent.version}) منذ بدأت التعديل (v${w.base}).`));
  }
  const problems = validate(w.layout);
  for (const m of problems) box.append(el("div", { class: "msg " + m.level }, m.text));
  if (!problems.length && !w.unsent && !S.notices.length) box.append(el("div", { class: "msg ok" }, "✓ لا ملاحظات. التخطيط مطابق لما على الشاشة."));
}

// ---------- wiring ----------

$("undo").onclick = undo;
$("redo").onclick = redo;
$("send").onclick = () => send(false);
$("discard").onclick = discard;
$("addPage").onclick = () => change((l) => {
  const id = pageId("page", new Set(l.pages.map((p) => p.id)));
  l.pages.push({ id, title: "صفحة جديدة", tiles: [] });
  S.page = l.pages.length - 1;
  S.sel = { kind: "page", page: S.page };
});
$("panelProps").onclick = () => { S.sel = { kind: "panel" }; render(); };
$("refresh").onclick = async () => { await loadCatalog(true); render(); };
for (const id of ["search", "controlsOnly", "unusedOnly"]) $(id).addEventListener("input", renderTree);

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
  else if (e.key === "Delete" && S.sel.kind === "tile") removeTile(S.sel.tile);
});

// Panels' online state and versions change while the editor is open.
setInterval(async () => {
  const r = await api("api/editor/state");
  if (!r.ok) return;
  for (const p of r.body.panels) {
    const old = S.panels.find((x) => x.id === p.id);
    if (old) { old.online = p.online; old.sent = p.sent; }
  }
  renderTabs();
  renderMessages();
}, 5000);

(async () => {
  await Promise.all([loadState(), loadCatalog(false)]);
  render();
})();
