# Changelog

## 0.5.0

- Layout format v2: pages of **cards**. A card has a type from the card registry
  (`light.toggle`, `light.dimmer`, `cover.position`, `climate.thermostat`, `scene.bar`...),
  one or more entities, a size (1x1, 2x1, 2x2) and options. The hub checks every card
  against the registry on save.
- Stored layouts and drafts are migrated automatically; nothing is lost. Panels running
  an older app keep receiving the previous format until they are updated.
- Editor: choose a card's shape and size, edit its options; the preview shows the size.
- Project and panel configuration documents (PIN, access, apps, display defaults) are
  defined and stored; their management page comes in the next version.

## 0.4.0

- Editor: filter the building by entity type (lights, switches, covers, climate, fans,
  locks, scenes, scripts, buttons, sensors), remembered in the browser.
- Panels have a location (an HA area). They show inside their room in the building tree;
  click one to open it, drop an entity or a room on it to add it there. An entity lands
  on the page of its own room, created if missing.

## 0.3.0

- Layout editor in the Wall Panels page, ETS style: HA building (floors → areas →
  entities) | pages | page preview | properties, plus a message pane.
- Drag and drop: an entity or a whole area onto the preview, an area or a floor onto
  the pages list (one page per area), a page onto another panel's tab to copy it.
- "Used in" marker per entity and an "unused only" filter.
- Changes are kept as a draft until **Send to panel**; a conflict warning appears if the
  panel's layout was edited on the panel in the meantime. Undo/redo (Ctrl+Z / Ctrl+Y).
- SortableJS 1.15.6 (MIT) bundled, so the editor works without internet.

## 0.2.0

- Remote panels: the add-on installs a companion integration that opens
  `/api/wall_panel_hub/` on Home Assistant's own address (local, Nabu Casa, Cloudflare
  tunnel...). Panels on other networks pair and connect through it.
- After the first start, restart Home Assistant and accept **Wall Panel Hub** under
  Settings → Devices & services → Discovered.

## 0.1.2

- Fix: the add-on started in developer mode inside Home Assistant (the Supervisor
  token is now read where s6-overlay keeps it), so the page and the gateway were refused.

## 0.1.1

- Fix: the Wall Panels page was refused on installs where ingress requests come from
  another address of Home Assistant's internal network.

## 0.1.0

- Secure gateway between wall panels and Home Assistant: panels never hold an HA token,
  and may only control the entities in their own layout.
- Pairing with a 6-digit code approved from the Wall Panels sidebar page; one-click revoke.
- Per-panel layouts, validated and versioned, pushed to the panel on every change.
- mDNS announcement (`_wallpanel-hub._tcp`) so panels find the hub on the LAN.
