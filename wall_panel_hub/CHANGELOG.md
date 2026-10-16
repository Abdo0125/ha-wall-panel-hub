# Changelog

## 0.8.0

- Permissions now choose which pages of the panel's settings each role sees:
  connection, display (or brightness only), system buttons. Cards and apps follow
  their existing permissions. The older "local settings" permission means all four.
- Needs the panel app 0.6.0 for the new settings pages.

## 0.7.2

- Back to the 0.7.0 designer (0.7.1 is withdrawn). A higher version number so Home
  Assistant offers the update.

## 0.7.0

- New main page, the panel designer: each panel is shown as it looks on the wall (rooms on
  the side, the 5x4 grid, light or dark). Tap + to add a device, tap a card to change its
  shape, size, name and icon in plain words, drag cards and rooms to move them.
- Each panel has three tabs: Design, Settings, Permissions.
- New panels are set up in three steps: approve the code, pick the name and rooms, send.
- Project → Device types: set once what each device is (garage, curtain, window, outlet,
  heater...), and its name and icon; every panel shows it that way (app 0.5.0).
- Fix: the 2x2 size added in 0.6.3 went to the switch card instead of the button-only
  shutter card.
- The previous editor stays available as "Advanced editor".

## 0.6.3

- A shutter card with buttons (cover.buttons) can also be 2x2: it shows the shutter dial (app 0.4.18).

## 0.6.2

- An on/off light card (light.toggle) can also be 2x2: it shows the light dial (app 0.4.8).

## 0.6.1

- Light is the default theme for panels without their own setting (app 0.4.0 has the
  new wall-keypad look: fixed grid of cells, line icons, dashboard and rooms in the sidebar).

## 0.6.0

- **Project** tab: project name, installer PIN, user PIN, default language, theme,
  screensaver and return time for all panels. PINs are stored and sent as PBKDF2 hashes;
  the panel checks them offline.
- Per-panel **settings**: display overrides (blank = project's), a PIN for this panel
  only, and access per role (no PIN / user PIN / installer PIN): control, apps, edit
  cards, reorder, local settings.
- The hub enforces it: an edit saved from a panel is checked against the PIN's role.
  Adding or removing entities, cards, pages or rooms from a panel is always refused.
- Panels receive their configuration live (`wallpanel/config/subscribe`); language,
  theme and screensaver moved out of the layout into it (migrated automatically).
- New panels on the LAN announce themselves and request pairing on their own; they
  appear in the pairing list as soon as they are switched on.
- A warning shows until an installer PIN is set.

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
