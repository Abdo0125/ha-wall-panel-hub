# Changelog

## 0.17.1

- "Apply to all panels" is now "Copy to all panels" (نسخ إلى كل الشاشات), in the add-on and the
  panel app 0.17.1.

## 0.17.0

- Parity, phase 1 (docs/parity.md): whoever has the permission will edit anything from the
  panel itself.
- Four new permissions, on for the installer by default and restrictable per panel:
  rooms, devices, panel, project. They take effect as the panel gains those edits.
- Each panel says its type when it pairs: Android 11", Android 4", ESP32 4", phone. Shown in
  the panel's settings; panels paired before are Android 11".
- English is the default language.
- "Apply to all panels": a panel's display settings become the project's and replace every
  panel's own, from the add-on (panel settings) or from the panel (project permission,
  online only).
- Panel app 0.17.0: sends its type; English by default (the demo too); "Apply to all
  panels" in its display page.

## 0.16.1

- Designer: "+ غرفة" lists HA's areas by floor, with "+ الطابق كاملاً" to add every room
  of a floor (with its devices) at once. The rooms list shows each room's floor.
- Panel app 0.16.2: settings leave the rooms menu for ⚙ in the top bar, shown while the
  menu is open (beside ⌂).

## 0.16.0

- The panels get Home Assistant's floors with their areas (read when the add-on starts,
  every 10 minutes and when the designer opens; sent only when they change).
- Panel app 0.16.0: the page name in the top bar opens the rooms menu over the grid:
  a large tile per room, a tab per floor (when the rooms are on more than one), home at
  the end of the tabs and settings as the last tile. The drawer stays for edit mode only.

## 0.15.0

- The panel's grid has 6 columns on an 11" screen (was 5): halves and thirds, no odd cell
  left beside wide cards. The designer's preview follows. Cards keep their places; the
  sixth column starts empty.
- A home page title per panel or for the project (settings: "عنوان الصفحة الرئيسية"),
  also set from the panel's display page; blank shows "My dashboard".
- Panel app 0.15.0: the rooms move to a drawer opened from the menu, so the grid has the
  whole width. The top bar: menu and page name; clock, date and weather; edit (as the
  PIN's role allows) and apps.

## 0.14.2

- A panel may ask HA to read its own devices again (`homeassistant.update_entity`), so a
  command whose state did not change is still confirmed, and each page shown is read
  again (at most once a minute per device).
- `get_states` from a panel returns that panel's entities only: smaller and private.
- Panel app 0.14.1: a command already as asked is done at once; no answer after a second
  reads the device again; every page shown takes HA's state again.

## 0.14.1

- Dragging a wide card onto the empty cell beside it slides it over (it still covers that
  cell) instead of refusing; a card dropped near the edge slides back to fit. Panel app
  0.13.2 does the same.

## 0.14.0

- One add window for +, as in ETS: a list per kind (rooms, devices, apps, widgets) beside
  the results, with a search and filters fitting each list (device kind, room, controls
  or with sensors, built-in or Android widgets). Rooms show their devices side by side,
  with "add all". Apps can now be added from the designer.
- The add-on's look follows ETS: square corners, flat buttons, lists in ruled rows with a
  bar on the chosen item. Colours and the panel preview are unchanged.
- Panel app 0.13.0: the same add window on the panel.

## 0.13.1

- Simpler: allowing an app allows its widgets. The separate list of allowed Android
  widgets is gone; + offers the widgets of the panel's allowed apps.
- Panel app 0.12.2: Android widgets fill their card with square corners, whatever the app
  drew.

## 0.13.0

- Android widgets: the panel reports the widgets its installed apps offer (music,
  cameras...). Allow them per panel in Settings > Allowed Android widgets, then add them
  with + in the designer or on the panel (same permissions as apps and widgets).
- The panel binds each widget itself: Android asks once, then the widget's own setup if
  it has one. Removing the card frees it.
- Card sizes 3x2 and 4x2, for Android widgets. Panel app 0.12.0.

## 0.12.1

- My dashboard: an app card already there no longer blocks saving when its app has
  since been taken off the allowed list; only apps added now must be allowed.
- Widget sources list only matching sensors (a temperature; a power in W or kW), with
  their current reading.

## 0.12.0

- Built-in widgets: weather, outdoor temperature and power now. Their sources are set
  once in Project > Widgets; a widget added from the panel or the designer reads from
  there and adds no new device.
- New permission "add apps and widgets" (user and installer by default): on the panel,
  + in an empty cell of a room adds an allowed app or a widget, and the card's sheet
  removes it. Devices are still added in the add-on only.
- Widgets are left out for older panel apps. Panel app 0.11.0.

## 0.11.1

- A new permission is on by default for panels whose permissions were saved before it
  existed (my dashboard was missing on them). Saving the permissions now records which
  ones the add-on offered, so what is switched off on purpose stays off.

## 0.11.0

- My dashboard: first in the designer's sidebar, with free placement like the rooms.
  Until it is changed it shows the clock, then the panel's scenes.
- The clock is a card of the panel itself (widget.clock): move it, remove it, add it
  back with "+".
- New permission "customize my dashboard" (user and installer by default): on the
  panel the dashboard takes only devices already in its rooms, scenes and allowed apps.
  Panel app 0.10.0.

## 0.10.1

- Updates now show at once: the page is never kept by the browser and its scripts carry
  the add-on version, so an update no longer needs the add-on removed and added again.

## 0.10.0

- Free placement on Android panels: a card can sit in any cell, with gaps. Drag it to
  an empty cell to put it there, or onto another card to swap them; "+" works in every
  empty cell. Cards keep their place (new "pos" field); cards without one fill in order.
- Each panel knows its type (Android panel, ESP32, phone), sent by its own app when it
  pairs; shown in the panel's settings. Existing panels are Android panels.
- Moving a card on the panel needs the reorder permission. Panel app 0.9.0.

## 0.9.1

- The installer may reorder cards and rooms on the panel by default (edit mode, panel
  app 0.8.2). The user still may not unless the installer allows it. Panels whose
  permissions were saved in the add-on keep what was saved.

## 0.9.0

- Allowed apps per panel (panel → settings): picked from the apps installed on the
  panel, which it reports when it connects, or typed by package name, each with its
  own return time. Panel app 0.7.0 shows them in an "Apps" page.

## 0.8.2

- Display settings changed on the panel no longer wait for the hub: the panel applies
  them at once, even offline, and sends them when connected. The hub stores them
  without a second check (the panel already checked the PIN). Panel app 0.6.2.

## 0.8.1

- Theme, language and screensaver can be changed on the panel itself (display
  permission). They are stored as that panel's own settings and show here too.
- Needs the panel app 0.6.1.

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
