# Changelog

## 0.26.0

- A room shown on more than one floor of a panel's rooms menu (layout page `also`: the other
  floors, each with the room's card there), with the rooms permission. The same room, not a copy.
- Panel app 0.26.0: + in a floor's tab also lists the panel's rooms on other floors, shown
  there too; such a room's pencil sets its card on that floor or takes it off that floor only.
  Deleting a floor keeps a room shown on another floor there.

## 0.25.0

- Floors added from the panel: layout `floors` lists the floors a panel's rooms menu shows
  even with no room on them (rooms permission). A floor not in HA is named on the panel
  (`wp_` id, this panel or all panels) and comes after HA's floors, with no area.
- Panel app 0.25.0: + after the floor tabs in edit mode adds one of HA's floors (empty or
  with its rooms) or a new floor; a floor's pencil deletes it from the panel with its rooms.

## 0.24.0

- A room's place in the rooms menu (layout page `tile.pos`): rooms are placed freely in their
  floor's grid on a panel, as cards are on a page.
- Panel app 0.24.0: the edit bar is one line as high as the top bar (the hint in its middle),
  so the grid keeps its 4 rows while editing; flat Cancel and Done; rooms moved to any cell
  of the rooms menu, and added from + in an empty cell; a card or a room picked to add shows
  its settings first, added with Add, in the cell touched (sizes that do not fit there
  greyed); cards added by hand start at 1x1.

## 0.23.0

- A room's card in the panel's rooms menu (layout page `tile`): picture or details, and its
  size; by default its picture at 2x1. Changed from the panel with the rooms permission.
- The room picture card (room.card) also comes in 1x1.
- Panel app 0.23.0: the rooms menu shows each room as a room card (picture at 2x1 by
  default); in edit mode the room's sheet picks its card in the menu (previews) and size.

## 0.22.0

- Room details card (room.summary, 2x1 by default, or 1x1): the room's picture band, its
  name, lights on of all, covers open of all and its AC. The picture card is room.card;
  each switches to the other.
- A card added by hand starts at its smallest size (the room details card: 2x1), in the
  designer and on the panel.
- Panel app 0.22.0: room cards have a list of their own in + (details or picture); a card's
  shapes show as small pictures of the card; the pencil shows on rooms with a picture.

## 0.21.0

- Room card (room.card, 2x2 or 2x1): one of the panel's rooms with HA's picture of its area,
  its name, how many lights are on and its temperature; a button turns its lights off, a
  touch opens the room. Added on the panel (+ > Room cards) to my dashboard or a room.
- The hub hands panels their rooms' pictures from HA (wallpanel/area/picture); the catalog
  carries each area's picture.
- Panel app 0.21.0: the room card; the rooms menu shows each room's picture in place of its
  icon; pictures are kept on the panel for offline use.

## 0.20.0

- Parity, phase 3: devices from the panel. With the devices permission a panel adds any of
  HA's devices to any room or to my dashboard, removes device cards and moves them to
  another room; the hub accepts it.
- A room's floor on a panel (layout `floor`): a room added in a floor's tab stays there,
  whatever its floor in HA; changed from the panel (rooms) or the designer's room settings.
- HA's devices with no card yet (media players, cameras, alarms...) are listed as coming soon.
- Panel app 0.20.0: + lists every device in HA by room ("No room" for the rest), the coming
  ones greyed; a device card can be removed or moved to another room; rooms keep the floor
  they were added in, and their sheet changes it; "Empty room" in Add rooms.

## 0.19.1

- Fix: rooms added on a panel came without devices ("0 devices"): the panel read HA's
  states through the hub, which keeps them to the panel's own entities. The panel now asks
  the hub for HA's rooms and devices (wallpanel/catalog/get, rooms or devices permission).
- Panel app 0.19.3: reads them from the hub.

## 0.19.0

- Floors get a name and an icon of their own on a panel, or for all panels: HA's (or an
  icon by level) where none is given; nothing changes in HA. Set from the panel (rooms
  permission for this panel, project for all panels).
- The panels get each floor's icon from HA.
- Panel app 0.19.0: floor tabs show their icon; in edit mode a pencil on each opens its
  name and icon ("This panel" / "Copy to all panels"). The edit bar keeps the page name
  where it is, with Cancel and Done at the end, and no home button. The pencil shows in
  the rooms menu too, editing the rooms there.

## 0.18.0

- Parity, phase 2: rooms from the panel. With the rooms permission a panel adds rooms
  (with their devices), removes them and changes their name and icon; the hub accepts it.
  A device added to a room already there still waits for phase 3.
- The permissions page no longer says rooms and devices are edited in the add-on only.
- Panel app 0.18.0: in edit mode the rooms menu (the page name) is where rooms are edited:
  long-press and drag to move one, the pencil for its name, icon and removal, + to add HA's
  rooms by floor or a whole floor. The rooms drawer is gone; home is beside Cancel.

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
