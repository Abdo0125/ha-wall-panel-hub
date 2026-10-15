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

// ---------- card registry (schema/cards.json, loaded from the hub) ----------

let R = { kinds: {}, cards: {} };
const domainOf = (id) => id.split(".")[0];
const kindOf = (entityId) => Object.keys(R.kinds).find((k) => R.kinds[k].domains.includes(domainOf(entityId)));
const kindOfCard = (cardType) => cardType.split(".")[0];
const cardDomains = (cardType) => R.cards[cardType]?.entities.domains || R.kinds[kindOfCard(cardType)]?.domains || [];
const cardEntities = (c) => [
  ...c.entities,
  ...Object.entries(c.options || {}).filter(([k, v]) => R.cards[c.card]?.options?.[k]?.type === "entity" && v).map(([, v]) => v),
];

// Card types that can show these entities: same kind, entity count and domains accepted.
function variantsFor(card) {
  return Object.keys(R.cards).filter((type) => {
    const spec = R.cards[type];
    if (kindOfCard(type) !== kindOfCard(card.card)) return false;
    if (card.entities.length < spec.entities.min || card.entities.length > spec.entities.max) return false;
    const domains = cardDomains(type);
    return domains.includes("*") || card.entities.every((e) => domains.includes(domainOf(e)));
  });
}

function nextCardId(layout) {
  const taken = new Set(layout.pages.flatMap((p) => p.cards.map((c) => c.id)));
  let n = 1;
  while (taken.has(`c${n}`)) n++;
  return `c${n}`;
}

// The default card for an entity (e.g. light.toggle for a light).
function newCard(layout, entityId) {
  const type = R.kinds[kindOf(entityId)].default;
  return { id: nextCardId(layout), card: type, entities: [entityId], size: R.cards[type].default_size };
}

// Same check as the hub (cards.py); the hub re-checks on send.
function cardProblem(c) {
  const spec = R.cards[c.card];
  if (!spec) return `نوع البطاقة ${c.card} غير معروف.`;
  const { min, max } = spec.entities;
  if (c.entities.length < min || c.entities.length > max) return `${c.card} تقبل ${min} إلى ${max} كيانات.`;
  const domains = cardDomains(c.card);
  const wrong = c.entities.find((e) => !domains.includes("*") && !domains.includes(domainOf(e)));
  if (wrong) return `${c.card} لا تناسب ${wrong}.`;
  if (c.size && !spec.sizes.includes(c.size)) return `${c.card} لا تقبل الحجم ${c.size}.`;
  for (const [key, opt] of Object.entries(spec.options || {})) {
    if (opt.required && (c.options?.[key] === undefined || c.options[key] === "")) return `${c.card}: الخيار ${key} مطلوب.`;
  }
  return null;
}

function pageId(wanted, taken) {
  const base = (wanted || "").toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "page";
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}

// Entity type filter chips; the choice is remembered in this browser.
const TYPE_FILTERS = [
  ["light", "إنارة"], ["switch", "مفاتيح"], ["cover", "ستائر"], ["climate", "مكيفات"], ["fan", "مراوح"],
  ["lock", "أقفال"], ["scene", "مشاهد"], ["script", "سكربتات"], ["button", "أزرار"], ["sensor", "حساسات"],
];
function loadTypes() {
  try {
    const saved = JSON.parse(localStorage.getItem("wph.types"));
    // "generic" is what sensors were called before 0.5.0.
    if (Array.isArray(saved)) return new Set(saved.map((t) => (t === "generic" ? "sensor" : t)));
  } catch (_) { /* storage unavailable: use the default */ }
  return new Set(TYPE_FILTERS.map(([t]) => t).filter((t) => t !== "sensor"));
}
function saveTypes() {
  try { localStorage.setItem("wph.types", JSON.stringify([...S.types])); } catch (_) { /* ignore */ }
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
  types: null,         // entity types shown in the building tree (set at start)
  notices: [],         // transient messages: {level, text, actions}
};
const W = () => S.work[S.current];
const L = () => W()?.layout;
const entityById = () => Object.fromEntries(S.catalog.entities.map((e) => [e.entity_id, e]));

function blankLayout(name) {
  return { schema: 2, version: 1, pages: [] };
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

async function loadRegistry() {
  const r = await api("api/editor/cards");
  if (r.ok) R = r.body;
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

function controlsInArea(areaId) {
  return S.catalog.entities.filter((e) => e.area_id === areaId && e.controllable).map((e) => e.entity_id);
}

function addAreaPages(layout, areaIds, at) {
  const areas = Object.fromEntries(S.catalog.areas.map((a) => [a.id, a]));
  const newPages = [];
  const sources = new Map();  // page -> area it is made from
  for (const id of areaIds) {
    const area = areas[id];
    if (!area) continue;
    const existing = layout.pages.find((p) => p.id === id);
    if (existing) {  // re-import: only what is missing
      addCards(layout, existing, controlsInArea(id));
      continue;
    }
    const taken = new Set([...layout.pages, ...newPages].map((p) => p.id));
    const page = { id: pageId(id, taken), title: area.name, cards: [] };
    if (/^mdi:[a-z0-9-]+$/.test(area.icon || "")) page.icon = area.icon;
    newPages.push(page);
    sources.set(page, id);
  }
  layout.pages.splice(at ?? layout.pages.length, 0, ...newPages);
  // Cards are added once the pages are in the layout, so their ids are unique across it.
  for (const page of newPages) addCards(layout, page, controlsInArea(sources.get(page)));
  return newPages.length;
}

// One default card per entity not yet on the page. Card ids are unique in the layout.
function addCards(layout, page, entityIds, at) {
  const have = new Set(page.cards.flatMap((c) => c.entities));
  const byId = entityById();
  const cards = [];
  for (const id of entityIds) {
    if (have.has(id) || !byId[id] || !kindOf(id)) continue;
    const card = newCard({ pages: [...layout.pages, { cards }] }, id);
    cards.push(card);
    have.add(id);
  }
  page.cards.splice(at ?? page.cards.length, 0, ...cards);
}

async function copyPageTo(pid, page) {
  if (pid === S.current) return;
  const w = S.work[pid];
  w.undo.push(JSON.stringify(w.layout));
  const copy = clone(page);
  copy.id = pageId(copy.id, new Set(w.layout.pages.map((p) => p.id)));
  // Card ids must stay unique in the target layout.
  copy.cards = [];
  w.layout.pages.push(copy);
  for (const c of page.cards) copy.cards.push({ ...clone(c), id: nextCardId(w.layout) });
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
  if (!layout.pages.length) err("لا توجد صفحات.");
  const ids = new Set();
  const cardIds = new Set();
  for (const page of layout.pages) {
    if (ids.has(page.id)) err(`معرّف الصفحة "${page.id}" مكرر.`);
    ids.add(page.id);
    if (!/^[a-z0-9_]+$/.test(page.id)) err(`معرّف الصفحة "${page.id}" غير صالح.`);
    if (!page.title?.trim()) err(`الصفحة "${page.id}" بلا عنوان.`);
    if (page.icon && !/^mdi:[a-z0-9-]+$/.test(page.icon)) err(`أيقونة الصفحة "${page.title}" يجب أن تكون بصيغة mdi:name.`);
    if (!page.cards.length) warn(`الصفحة "${page.title}" فارغة.`);
    for (const c of page.cards) {
      const name = c.label || c.entities[0] || c.card;
      if (cardIds.has(c.id)) err(`معرّف البطاقة "${c.id}" مكرر.`);
      cardIds.add(c.id);
      const problem = cardProblem(c);
      if (problem) err(`${name}: ${problem}`);
      if (c.icon && !/^mdi:[a-z0-9-]+$/.test(c.icon)) err(`${name}: الأيقونة يجب أن تكون بصيغة mdi:name.`);
      for (const e of cardEntities(c)) {
        if (S.catalog.entities.length && !byId[e]) warn(`${e} غير موجود في Home Assistant (في "${page.title}").`);
      }
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
      for (const c of page.cards) for (const e of cardEntities(c)) (used[e] ||= new Set()).add(p.name);
    }
  }
  return used;
}

function renderTree() {
  resetSortables("tree");
  const tree = $("tree");
  tree.replaceChildren();
  if (S.catalogError) { tree.append(el("p", { class: "msg error" }, "تعذّرت قراءة المبنى من HA: " + S.catalogError)); return; }
  renderTypeChips();
  const q = $("search").value.trim().toLowerCase();
  const unusedOnly = $("unusedOnly").checked;
  const used = usage();
  const match = (e) =>
    S.types.has(e.type) && (!unusedOnly || !used[e.entity_id]) &&
    (!q || e.name.toLowerCase().includes(q) || e.entity_id.includes(q));

  const entitiesOf = (areaId) => S.catalog.entities.filter((e) => (e.area_id || null) === areaId && match(e));
  const panelsIn = (areaId) => S.panels.filter((p) => (p.area_id || null) === areaId);
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
    const panels = panelsIn(area.id);
    if (filtering && !ents.length && !panels.length) return null;
    const key = "a:" + area.id;
    const open = S.open.has(key) || filtering;
    const li = el("li", { class: "area", dataset: { area: area.id } },
      el("div", { class: "node" },
        el("span", { class: "twist", onclick: () => { S.open.has(key) ? S.open.delete(key) : S.open.add(key); renderTree(); } }, open ? "▾" : "▸"),
        el("span", {}, area.name),
        el("span", { class: "count" }, ents.length),
      ));
    // Panels mounted in the room stay visible even when the room is folded.
    for (const p of panels) li.append(panelNode(p));
    if (open) li.append(entityList(area.id));
    return li;
  }

  function areaList(areas) {
    const ul = el("ul", { class: "tree" });
    for (const a of areas) { const n = areaNode(a); if (n) ul.append(n); }
    sortable("tree", ul, { group: { name: "ha-area", pull: "clone", put: false }, sort: false, handle: ".node" });
    return ul;
  }

  const unplaced = panelsIn(null);
  if (unplaced.length) {
    tree.append(el("h2", {}, "شاشات بدون موقع"));
    for (const p of unplaced) tree.append(panelNode(p));
    tree.append(el("p", { class: "muted" }, "حدّد موقع الشاشة من إعدادات الشاشة ← الموقع."));
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

function renderTypeChips() {
  const box = $("typeChips");
  box.replaceChildren();
  const count = {};
  for (const e of S.catalog.entities) count[e.type] = (count[e.type] || 0) + 1;
  const all = TYPE_FILTERS.every(([t]) => S.types.has(t));
  box.append(el("span", {
    class: "chip" + (all ? " on" : ""),
    onclick: () => { S.types = all ? new Set() : new Set(TYPE_FILTERS.map(([t]) => t)); saveTypes(); renderTree(); },
  }, "الكل"));
  for (const [type, label] of TYPE_FILTERS) {
    if (!count[type]) continue;
    box.append(el("span", {
      class: "chip" + (S.types.has(type) ? " on" : ""),
      onclick: () => { S.types.has(type) ? S.types.delete(type) : S.types.add(type); saveTypes(); renderTree(); },
    }, label, el("span", { class: "n" }, count[type])));
  }
}

function selectPanel(pid) {
  S.current = pid; S.page = 0; S.sel = { kind: "panel" };
  render();
}

// A panel in the building tree: click to open it, drop entities or a room on it.
function panelNode(p) {
  const row = el("div", {
    class: "panelnode" + (p.id === S.current ? " active" : ""),
    dataset: { pid: p.id },
    title: "اضغط لفتح الشاشة، أو اسحب إليها كيانات أو غرفة",
    onclick: () => selectPanel(p.id),
  },
    el("span", {}, "🖥"),
    el("span", { class: "name" }, p.name),
    S.work[p.id]?.unsent ? el("span", { class: "unsent", title: "تعديلات غير مرسلة" }, "✱") : null,
    el("span", { class: "dot", style: `color:${p.online ? "var(--ok)" : "var(--muted)"}` }, p.online ? "●" : "○"),
  );
  sortable("tree", row, {
    group: { name: "panel-drop", put: ["ha", "ha-area"], pull: false }, sort: false, draggable: ".none",
    onAdd: (evt) => {
      const { entity, area } = evt.item.dataset;
      evt.item.remove();
      dropOnPanel(p.id, entity ? [entity] : [], area);
    },
  });
  return row;
}

// Entities dropped on a panel land on the page of their own room (created if missing),
// else on the page of the panel's room, else on the page open now. A room dropped on a
// panel becomes a page. The editor then shows that panel.
function dropOnPanel(pid, entityIds, areaId) {
  const sameAsOpen = pid === S.current;
  const openPage = S.page;
  S.current = pid;
  const byId = entityById();
  const areas = Object.fromEntries(S.catalog.areas.map((a) => [a.id, a]));
  const panel = S.panels.find((p) => p.id === pid);
  let pageTitle = "";
  change((l) => {
    const pageFor = (target) => {
      let i = target ? l.pages.findIndex((pg) => pg.id === target) : -1;
      if (i < 0 && target && areas[target]) {
        l.pages.push({ id: pageId(target, new Set(l.pages.map((pg) => pg.id))), title: areas[target].name, cards: [] });
        i = l.pages.length - 1;
      }
      if (i < 0) {
        if (!l.pages.length) l.pages.push({ id: "page", title: "صفحة جديدة", cards: [] });
        i = sameAsOpen ? Math.min(openPage, l.pages.length - 1) : 0;
      }
      return i;
    };
    if (areaId) {
      addAreaPages(l, [areaId]);
      S.page = Math.max(0, l.pages.findIndex((pg) => pg.id === areaId));
    }
    for (const id of entityIds) {
      const e = byId[id];
      if (!e) continue;
      S.page = pageFor(e.area_id || panel?.area_id);
      addCards(l, l.pages[S.page], [id]);
    }
    S.sel = { kind: "page", page: S.page };
    pageTitle = l.pages[S.page]?.title || "";
  });
  notice("ok", `أُضيف إلى ${panel?.name} ← ${pageTitle}`);
}

async function renamePanel(pid, name) {
  name = name.trim();
  if (!name) return;
  await api(`api/panels/${encodeURIComponent(pid)}/rename`, { method: "POST", body: JSON.stringify({ name }) });
  const p = S.panels.find((x) => x.id === pid);
  if (p) p.name = name;
  render();
}

async function placePanel(pid, areaId) {
  await api(`api/panels/${encodeURIComponent(pid)}/area`, { method: "POST", body: JSON.stringify({ area_id: areaId || null }) });
  const p = S.panels.find((x) => x.id === pid);
  if (p) p.area_id = areaId || null;
  if (areaId) S.open.add("a:" + areaId);
  render();
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
    }, el("span", {}, p.title || p.id), el("span", { class: "muted" }, p.cards.length)));
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
  page.cards.forEach((c, i) => {
    const e = byId[c.entities[0]];
    const missing = S.catalog.entities.length && c.entities.some((id) => !byId[id]);
    const selected = S.sel.kind === "card" && S.sel.page === S.page && S.sel.card === i;
    const [cols, rows] = (c.size || R.cards[c.card]?.default_size || "1x1").split("x").map(Number);
    grid.append(el("div", {
      class: "tile" + (selected ? " selected" : "") + (missing ? " missing" : ""),
      style: `grid-column: span ${cols}; grid-row: span ${rows}`,
      dataset: { index: i },
      onclick: () => { S.sel = { kind: "card", page: S.page, card: i }; render(); },
    },
      el("span", { class: "x", title: "حذف", onclick: (ev) => { ev.stopPropagation(); removeCard(i); } }, "✕"),
      el("span", { class: "badge" }, c.card.toUpperCase()),
      el("div", {},
        el("div", { class: "label" }, c.label || (c.entities.length > 1 ? `${c.entities.length} ×` : e?.name) || c.entities[0] || c.card),
        el("div", { class: "eid" }, c.entities.join("، ") || c.options?.package || "")),
    ));
  });
  sortable("grid", grid, {
    group: { name: "cards", put: ["ha", "ha-area"] },
    onUpdate: (evt) => change((l) => {
      const cards = l.pages[S.page].cards;
      cards.splice(evt.newIndex, 0, cards.splice(evt.oldIndex, 1)[0]);
      S.sel = { kind: "card", page: S.page, card: evt.newIndex };
    }),
    onAdd: (evt) => {
      const { entity, area } = evt.item.dataset;
      evt.item.remove();
      const ids = entity ? [entity] : controlsInArea(area);
      change((l) => addCards(l, l.pages[S.page], ids, evt.newIndex));
    },
  });
}

function removeCard(i) {
  change((l) => {
    l.pages[S.page].cards.splice(i, 1);
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

// A new card type keeps the card's id and entities; size and options it does not accept are dropped.
function setCardType(c, type) {
  const spec = R.cards[type];
  c.card = type;
  if (!spec.sizes.includes(c.size)) c.size = spec.default_size;
  for (const key of Object.keys(c.options || {})) if (!spec.options?.[key]) delete c.options[key];
  if (c.options && !Object.keys(c.options).length) delete c.options;
}

// A property field built from the registry's option definition.
function optionField(c, key, opt) {
  const value = c.options?.[key];
  const set = (v) => change(() => {
    if (v === undefined) { if (c.options) delete c.options[key]; if (c.options && !Object.keys(c.options).length) delete c.options; }
    else c.options = { ...(c.options || {}), [key]: v };
  });
  const hint = opt.default !== undefined ? ` (افتراضي: ${opt.default})` : opt.required ? " *" : "";
  const label = key + hint;
  if (opt.type === "bool") {
    return field(label, select([["", "افتراضي"], ["true", "نعم"], ["false", "لا"]],
      value === undefined ? "" : String(value), (v) => set(v === "" ? undefined : v === "true")));
  }
  if (opt.type === "enum") {
    return field(label, select([["", "افتراضي"], ...opt.values.map((v) => [v, v])], value ?? "", (v) => set(v || undefined)));
  }
  if (opt.type === "int") {
    return field(label, textInput(value, (v) => set(v === "" || isNaN(Number(v)) ? undefined : Number(v)),
      { type: "number", min: opt.min, max: opt.max, placeholder: opt.default ?? "" }));
  }
  // string and entity
  return field(label, textInput(value, (v) => set(v.trim() || undefined),
    { dir: "ltr", placeholder: opt.type === "entity" ? `${opt.domains[0]}.…` : "" }));
}

function renderProps() {
  const box = $("props");
  box.replaceChildren();
  const layout = L();
  if (!layout) return;
  const sel = S.sel;

  if (sel.kind === "card") {
    const c = layout.pages[sel.page]?.cards[sel.card];
    if (!c) { S.sel = { kind: "panel" }; return renderProps(); }
    const e = entityById()[c.entities[0]];
    const spec = R.cards[c.card] || { sizes: [], options: {} };
    box.append(
      el("p", {}, el("b", {}, e?.name || c.entities[0] || c.card), el("br"),
        el("span", { class: "muted", dir: "ltr" }, c.entities.join(", "))),
      field("شكل البطاقة", select(variantsFor(c).map((v) => [v, v]), c.card, (v) => change(() => setCardType(c, v)))),
      field("الحجم", select(spec.sizes.map((v) => [v, v]), c.size || spec.default_size, (v) => change(() => { c.size = v; }))),
      field("الاسم على الشاشة", textInput(c.label, (v) => change(() => { v.trim() ? c.label = v.trim() : delete c.label; }), { placeholder: e?.name || "" })),
      field("الأيقونة (mdi:...)", textInput(c.icon, (v) => change(() => { v.trim() ? c.icon = v.trim() : delete c.icon; }), { placeholder: "mdi:lightbulb", dir: "ltr" })),
      field("تأكيد قبل التنفيذ", select([["", "افتراضي (الأقفال فقط)"], ["true", "نعم"], ["false", "لا"]],
        c.confirm === undefined ? "" : String(c.confirm),
        (v) => change(() => { v === "" ? delete c.confirm : c.confirm = v === "true"; }))),
      ...Object.entries(spec.options || {}).map(([key, opt]) => optionField(c, key, opt)),
      el("button", { class: "danger", onclick: () => removeCard(sel.card) }, "حذف البطاقة"),
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

  // Panel: location and name. Language, theme, screensaver and access are panel
  // settings (Wall Panels page), not part of the layout.
  const hubPanel = S.panels.find((p) => p.id === S.current);
  box.append(
    el("p", { class: "muted" }, `${hubPanel?.model || ""} · المرسل: ${hubPanel?.sent ? "v" + hubPanel.sent.version : "لا شيء بعد"}`),
    field("الموقع (الغرفة)", select(
      [["", "— بدون موقع —"], ...S.catalog.areas.map((a) => [a.id, a.name])],
      hubPanel?.area_id || "",
      (v) => placePanel(S.current, v),
    )),
    field("اسم الشاشة", textInput(hubPanel?.name, (v) => renamePanel(S.current, v))),
    el("p", { class: "muted" }, "اللغة والمظهر وشاشة التوقف والصلاحيات وكلمات السر: ", el("a", { href: "./", style: "color:var(--accent)" }, "صفحة الشاشات ← الإعدادات"), "."),
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
  l.pages.push({ id, title: "صفحة جديدة", cards: [] });
  S.page = l.pages.length - 1;
  S.sel = { kind: "page", page: S.page };
});
$("panelProps").onclick = () => { S.sel = { kind: "panel" }; render(); };
$("refresh").onclick = async () => { await loadCatalog(true); render(); };
for (const id of ["search", "unusedOnly"]) $(id).addEventListener("input", renderTree);

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
  else if (e.key === "Delete" && S.sel.kind === "card") removeCard(S.sel.card);
});

// Panels' online state and versions change while the editor is open.
setInterval(async () => {
  const r = await api("api/editor/state");
  if (!r.ok) return;
  for (const p of r.body.panels) {
    const old = S.panels.find((x) => x.id === p.id);
    if (old) { old.online = p.online; old.sent = p.sent; old.area_id = p.area_id; }
  }
  renderTabs();
  for (const node of document.querySelectorAll(".panelnode")) {
    const p = S.panels.find((x) => x.id === node.dataset.pid);
    const dot = node.querySelector(".dot");
    if (p && dot) { dot.textContent = p.online ? "●" : "○"; dot.style.color = p.online ? "var(--ok)" : "var(--muted)"; }
  }
  renderMessages();
}, 5000);

(async () => {
  S.types = loadTypes();
  await Promise.all([loadState(), loadCatalog(false), loadRegistry()]);
  render();
})();
