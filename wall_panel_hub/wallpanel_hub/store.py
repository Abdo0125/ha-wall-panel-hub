"""Paired panels, pairing requests, layouts and configuration, kept under the add-on's /data."""

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from jsonschema import Draft202012Validator

from . import config as panel_config
from .access import EditRefused, check_panel_edit, role_caps
from .cards import card_entities, check_card, kind_of_card
from .files import DAMAGED, read_json, remove, write_json
from .migrate import LayoutTooNew, LayoutTooOld, is_current, upgrade
from .pins import verify_pin

log = logging.getLogger(__name__)

# Display settings that live in the panel configuration once a hub manages the panel.
DISPLAY = ("lang", "theme", "screensaver", "home_title")
# The apps that pair with the hub, one per kind of device.
PLATFORMS = ("android", "esp32", "phone")
# The kinds of panel, each with its own app: a layout or a setting that depends on the screen's
# size goes to panels of the same type only. iPhone and Android phones share one.
TYPES = ("android_11", "android_4", "esp32_4", "phone")
DEFAULT_TYPE = {"android": "android_11", "esp32": "esp32_4", "phone": "phone"}


def _floor_icon(floor: dict) -> str:
    """HA's icon for a floor, else one by its level, as HA shows it."""
    if floor.get("icon"):
        return floor["icon"]
    return {-1: "mdi:home-floor-negative-1", 0: "mdi:home-floor-0", 1: "mdi:home-floor-1",
            2: "mdi:home-floor-2", 3: "mdi:home-floor-3"}.get(floor.get("level"), "mdi:home-floor-l")


def _type(platform: str, kind: str | None) -> str:
    return kind if kind in TYPES else DEFAULT_TYPE.get(platform, "android_11")

PAIRING_TTL_S = 600
MAX_PENDING = 10
# A setup from the add-on (provision.py): its token pairs the panel it was sent to, once, this long.
PROVISION_TTL_S = 600
# Wrong PINs a panel may send in PIN_LOCK_S before its PINs count for nothing that long, as the panel's
# own keypad does (docs/audit-6-settings.md, item 2).
PIN_TRIES = 5
PIN_LOCK_S = 60

_SCHEMA = json.loads((Path(__file__).parent / "schema" / "layout.schema.json").read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(_SCHEMA)


class LayoutInvalid(ValueError):
    pass


class LayoutConflict(Exception):
    """The panel's layout changed (e.g. edited on the panel) after the draft was started."""

    def __init__(self, current: dict | None):
        super().__init__("layout changed since the draft was started")
        self.current = current


def readable(layout: dict) -> dict:
    """[layout] in the current format, or refused with nothing stored: a format this add-on does
    not read is never saved as what could be made of it."""
    try:
        return upgrade(layout)
    except (LayoutTooNew, LayoutTooOld) as e:
        raise LayoutInvalid(str(e)) from e


def validate_layout(layout: dict) -> None:
    """The schema, then what it cannot express: unique card ids, and each card against the
    card registry (entity count and domains, size, options)."""
    errors = sorted(_VALIDATOR.iter_errors(layout), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise LayoutInvalid(f"{where}: {first.message}")
    seen: set[str] = set()
    groups = [(f"pages/{i}", page["cards"]) for i, page in enumerate(layout["pages"])]
    if "dashboard" in layout:
        groups.append(("dashboard", layout["dashboard"]["cards"]))
    for at, cards in groups:
        for j, card in enumerate(cards):
            where = f"{at}/cards/{j}"
            if card["id"] in seen:
                raise LayoutInvalid(f"{where}: card id {card['id']} is used twice")
            seen.add(card["id"])
            problem = check_card(card)
            if problem:
                raise LayoutInvalid(f"{where}: {problem}")


def without_lost_rooms(layout: dict) -> dict:
    """[layout] without the room cards whose room is no longer on the panel: a room deleted
    takes its cards on my dashboard and in other rooms with it."""
    rooms = {p.get("id") for p in layout.get("pages", [])}

    def keep(cards: list) -> list:
        return [c for c in cards if kind_of_card(c.get("card", "")) != "room" or (c.get("options") or {}).get("page") in rooms]

    out = {**layout, "pages": [{**p, "cards": keep(p.get("cards", []))} for p in layout.get("pages", [])]}
    if layout.get("dashboard"):
        out["dashboard"] = {**layout["dashboard"], "cards": keep(layout["dashboard"].get("cards", []))}
    return out


def layout_entities(layout: dict | None) -> set[str]:
    if not layout:
        return set()
    cards = [c for p in layout.get("pages", []) for c in p.get("cards", [])]
    cards += (layout.get("dashboard") or {}).get("cards", [])
    return {e for c in cards for e in card_entities(c)}


def layout_summary(layout: dict) -> str:
    """One line for the log: version, rooms and devices."""
    return (f"v{layout.get('version')}, {len(layout.get('pages', []))} rooms, "
            f"{len(layout_entities(layout))} devices")


def only_known(layout: dict, known: set[str]) -> dict:
    """[layout] without the cards showing a device HA does not have."""
    def keep(cards: list) -> list:
        return [c for c in cards if all(e in known for e in card_entities(c))]

    out = {**layout, "pages": [{**p, "cards": keep(p.get("cards", []))} for p in layout.get("pages", [])]}
    if layout.get("dashboard"):
        out["dashboard"] = {**layout["dashboard"], "cards": keep(layout["dashboard"].get("cards", []))}
    return out


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class Panel:
    id: str
    name: str
    model: str
    key_hash: str
    paired_at: float
    last_seen: float = 0.0
    # Room the panel is mounted in (an HA area id). Management data, not part of the layout.
    area_id: str | None = None
    # Which app the device runs: each has its own grid and card rules (docs/ui-plan.md, 5.3).
    platform: str = "android"
    # Which kind of panel (TYPES): Android 11" and 4" run the same platform.
    type: str = "android_11"
    # What the panel said last when it connected (wallpanel/device/report): its app, its Android,
    # whether it may install the hub's updates (app_updates.py), and whether its app is the
    # device owner (docs/kiosk-plan.md, K3).
    app_version_name: str | None = None
    app_version_code: int | None = None
    android_sdk: int | None = None
    can_install: bool = False
    device_owner: bool = False


@dataclass
class PairRequest:
    request_id: str
    panel_id: str
    name: str
    model: str
    code: str
    platform: str = "android"
    type: str = "android_11"
    created: float = field(default_factory=time.time)
    # Set once approved; handed to the panel a single time.
    key: str | None = None
    rejected: bool = False


@dataclass
class Provision:
    """A panel set up from the add-on over adb (docs/kiosk-plan.md, K4): its pairing comes with
    this token, given to the panel over adb only, and is approved at once under the name and room
    the installer gave."""
    token: str
    name: str
    area_id: str | None
    created: float = field(default_factory=time.time)
    # The panel it paired, once it did: the token is then spent.
    panel_id: str | None = None


class Store:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.layouts_dir = data_dir / "layouts"
        self.layouts_dir.mkdir(parents=True, exist_ok=True)
        # Editor drafts: changes not yet sent to the panel (the explicit "send", like ETS download).
        self.drafts_dir = data_dir / "drafts"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        # Configuration: one site document, one document per panel.
        self.site_file = data_dir / "site.json"
        self.config_dir = data_dir / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.panels_file = data_dir / "panels.json"
        self.panels: dict[str, Panel] = {}
        # One more at each write: what was read from the files may have changed (gateway.py keeps
        # each panel's devices until then).
        self.generation = 0
        self.reload()
        # Pairing requests live in memory only: a restart simply asks the panel to retry.
        self.pending: dict[str, PairRequest] = {}
        # Setups from the add-on, by token: in memory only too.
        self.provisions: dict[str, Provision] = {}
        # Each PIN's role, checked once (a check takes the hub some 80 ms), by a key made from the
        # PIN with a secret of this run: the digits are not kept. Wrong PINs by panel, and the
        # panels whose PINs count for nothing until a time.
        self._pin_key = secrets.token_bytes(32)
        self._roles: dict[bytes, str] = {}
        self._wrong: dict[str, dict[bytes, float]] = {}
        self._locked: dict[str, float] = {}

    def reload(self) -> None:
        """The paired panels as the files say (at the start, and after a backup is restored)."""
        # Fields of a newer add-on (a backup restored, a step back) stay out: they stop nothing.
        known = {f.name for f in fields(Panel)}
        self.panels = {p["id"]: Panel(**{k: v for k, v in p.items() if k in known})
                       for p in read_json(self.panels_file) or []}
        self.generation += 1

    # ---- persistence ----

    def _write(self, path: Path, data) -> None:
        write_json(path, data)
        self.generation += 1

    def _save_panels(self) -> None:
        self._write(self.panels_file, [asdict(p) for p in self.panels.values()])

    # ---- pairing ----

    def _expire(self) -> None:
        now = time.time()
        for rid in [r for r, p in self.pending.items() if now - p.created > PAIRING_TTL_S]:
            del self.pending[rid]

    def new_provision(self, name: str, area_id: str | None) -> str:
        """A token for one panel set up from the add-on (provision.py)."""
        now = time.time()
        for token in [t for t, p in self.provisions.items() if now - p.created > PROVISION_TTL_S]:
            del self.provisions[token]
        token = secrets.token_urlsafe(24)
        self.provisions[token] = Provision(token, name.strip()[:60], area_id or None)
        return token

    def provisioned(self, token: str) -> str | None:
        """The panel that paired with [token], if it did."""
        p = self.provisions.get(token)
        return p.panel_id if p else None

    def _provision(self, token: str | None) -> Provision | None:
        if not token:
            return None
        for t, p in self.provisions.items():
            if hmac.compare_digest(t, token):
                fresh = time.time() - p.created <= PROVISION_TTL_S and p.panel_id is None
                return p if fresh else None
        return None

    def request_pairing(self, panel_id: str, name: str, model: str, platform: str = "android",
                        kind: str | None = None, provision_token: str | None = None) -> PairRequest:
        self._expire()
        # One open request per panel: a retry replaces the previous one.
        for rid in [r for r, p in self.pending.items() if p.panel_id == panel_id]:
            del self.pending[rid]
        while len(self.pending) >= MAX_PENDING:
            del self.pending[min(self.pending.values(), key=lambda p: p.created).request_id]
        req = PairRequest(
            request_id=secrets.token_urlsafe(16),
            panel_id=panel_id,
            # A panel that only knows its model (an emulator, a plain tablet) gets a name to rename.
            name=name[:60] if name and name != model and "built for" not in name else f"Panel {len(self.panels) + 1}",
            model=model[:60],
            code=f"{secrets.randbelow(1_000_000):06d}",
            platform=platform if platform in PLATFORMS else "android",
            type=_type(platform, kind),
        )
        self.pending[req.request_id] = req
        # Set up from the add-on: the installer approved it by starting the setup.
        prov = self._provision(provision_token)
        if prov is not None:
            if prov.name:
                req.name = prov.name
            self.approve(req.request_id, area_id=prov.area_id)
            prov.panel_id = panel_id
            log.info("panel paired by its setup from the add-on")
        return req

    def approve(self, request_id: str, area_id: str | None = None) -> Panel:
        self._expire()
        req = self.pending[request_id]
        key = secrets.token_urlsafe(32)
        panel = Panel(req.panel_id, req.name, req.model, _hash(key), time.time(), platform=req.platform, type=req.type)
        # Paired again: it is still in its room.
        before = self.panels.get(panel.id)
        if before is not None:
            panel.area_id = before.area_id
        if area_id:
            panel.area_id = area_id
        self.panels[panel.id] = panel
        self._save_panels()
        req.key = key
        return panel

    def reject(self, request_id: str) -> None:
        if request_id in self.pending:
            self.pending[request_id].rejected = True

    def pairing_status(self, request_id: str) -> dict:
        self._expire()
        req = self.pending.get(request_id)
        if req is None:
            return {"status": "expired"}
        if req.rejected:
            del self.pending[request_id]
            return {"status": "rejected"}
        if req.key:
            del self.pending[request_id]  # the key leaves the add-on once, then only its hash remains
            return {"status": "approved", "key": req.key, "panel_id": req.panel_id}
        return {"status": "pending"}

    # ---- panels ----

    def authenticate(self, key: str) -> Panel | None:
        h = _hash(key)
        for panel in self.panels.values():
            if hmac.compare_digest(panel.key_hash, h):
                return panel
        return None

    def seen(self, panel_id: str) -> None:
        if panel_id in self.panels:
            self.panels[panel_id].last_seen = time.time()
            self._save_panels()

    def save_device(self, panel_id: str, version_name: str | None, version_code: int | None,
                    sdk: int | None, can_install: bool, device_owner: bool = False) -> None:
        """What a panel said of itself when it connected: written only when it changed."""
        panel = self.panels.get(panel_id)
        if panel is None:
            return
        now = (version_name, version_code, sdk, can_install, device_owner)
        if (panel.app_version_name, panel.app_version_code, panel.android_sdk, panel.can_install, panel.device_owner) != now:
            (panel.app_version_name, panel.app_version_code, panel.android_sdk, panel.can_install,
             panel.device_owner) = now
            self._save_panels()

    def rename(self, panel_id: str, name: str) -> None:
        self.panels[panel_id].name = name[:60]
        self._save_panels()

    def place(self, panel_id: str, area_id: str | None) -> None:
        self.panels[panel_id].area_id = area_id or None
        self._save_panels()

    def revoke(self, panel_id: str) -> None:
        self.panels.pop(panel_id, None)
        # Twice: the copy kept of the file must not bring a revoked panel back.
        self._save_panels()
        self._save_panels()
        self.delete_draft(panel_id)
        remove(self._config_file(panel_id))
        self.generation += 1

    # ---- layouts ----

    @staticmethod
    def _safe(panel_id: str) -> str:
        return "".join(c for c in panel_id if c.isalnum() or c in "-_")

    def _layout_file(self, panel_id: str) -> Path:
        return self.layouts_dir / f"{self._safe(panel_id)}.json"

    def _draft_file(self, panel_id: str) -> Path:
        return self.drafts_dir / f"{self._safe(panel_id)}.json"

    def _config_file(self, panel_id: str) -> Path:
        return self.config_dir / f"{self._safe(panel_id)}.json"

    def set_aside(self) -> list[str]:
        """The files that could not be read and were moved aside, damaged (files.py) or of another
        format (get_layout, get_draft), under the data folder: the add-on's page says so."""
        folders = (self.dir, self.layouts_dir, self.drafts_dir, self.config_dir)
        found = [p for d in folders for p in d.iterdir() if p.suffix in (DAMAGED, ".unread")]
        return sorted(p.relative_to(self.dir).as_posix() for p in found)

    def clear_set_aside(self) -> None:
        for name in self.set_aside():
            (self.dir / name).unlink(missing_ok=True)

    # ---- configuration ----

    def get_site(self) -> dict:
        return read_json(self.site_file) or panel_config.new_site()

    def save_site(self, site: dict) -> dict:
        """Stores a new revision of the site document; every panel's effective config changes."""
        saved = {**site, "version": self.get_site()["version"] + 1}
        panel_config.validate("site", saved)
        self._write(self.site_file, saved)
        return saved

    def save_floors(self, catalog: dict) -> bool:
        """HA's floors with their areas, kept for the panels' rooms menu. A new revision only
        when they changed (a floor added, an area moved); True then."""
        floors = []
        for f in catalog.get("floors") or []:
            areas = [a["id"] for a in catalog.get("areas") or [] if a.get("floor_id") == f["id"]]
            if areas:
                floors.append({"id": f["id"], "name": f["name"], "icon": _floor_icon(f), "areas": areas})
        site = self.get_site()
        if floors == (site.get("floors") or []):
            return False
        self.save_site({**site, "floors": floors})
        return True

    def get_panel_config(self, panel_id: str) -> dict:
        return read_json(self._config_file(panel_id)) or panel_config.new_panel()

    def save_panel_config(self, panel_id: str, doc: dict) -> dict:
        saved = {**doc, "version": self.get_panel_config(panel_id)["version"] + 1}
        panel_config.validate("panel", saved)
        self._write(self._config_file(panel_id), saved)
        return saved

    def effective_config(self, panel_id: str) -> dict:
        panel = self.panels.get(panel_id)
        return panel_config.effective(self.get_site(), self.get_panel_config(panel_id), panel.name if panel else "")

    # ---- drafts (editor) ----

    def get_draft(self, panel_id: str) -> dict | None:
        """{"layout": ..., "base_version": version of the sent layout it started from}."""
        f = self._draft_file(panel_id)
        draft = read_json(f)
        if draft is None:
            return None
        layout = draft.get("layout") or {}
        if not is_current(layout):
            try:
                layout = upgrade(layout)
            except (LayoutTooNew, LayoutTooOld) as e:
                log.warning("panel %s: draft set aside: %s", panel_id, e)
                f.replace(f.with_suffix(".unread"))
                return None
        layout.pop("panel", None)
        draft["layout"] = layout
        return draft

    def save_draft(self, panel_id: str, layout: dict, base_version: int | None) -> None:
        # Not validated: a draft may be half done. Validation happens on send.
        self._write(self._draft_file(panel_id), {"layout": layout, "base_version": base_version})

    def delete_draft(self, panel_id: str) -> None:
        remove(self._draft_file(panel_id))

    def send_draft(self, panel_id: str, force: bool = False) -> dict:
        """Validates and stores the draft as the panel's layout. Raises LayoutConflict if the
        layout moved on since the draft began, unless forced."""
        draft = self.get_draft(panel_id)
        if draft is None:
            raise KeyError(panel_id)
        current = self.get_layout(panel_id)
        current_version = current["version"] if current else None
        if not force and current_version != draft.get("base_version"):
            raise LayoutConflict(current)
        saved = self.save_layout(panel_id, draft["layout"])
        self.delete_draft(panel_id)
        return saved

    def get_layout(self, panel_id: str) -> dict | None:
        f = self._layout_file(panel_id)
        layout = read_json(f)
        if layout is None:
            return None
        if not is_current(layout) or "panel" in layout:
            # Upgraded once, same version: the panel takes it for the change it is not.
            # Display settings move to the panel configuration.
            try:
                layout = self._move_display_settings(panel_id, upgrade(layout))
            except (LayoutTooNew, LayoutTooOld) as e:
                # Kept beside, unread: the panel's own copy can come back in its place.
                log.warning("panel %s: layout set aside: %s", panel_id, e)
                f.replace(f.with_suffix(".unread"))
                return None
            self._write(f, layout)
        return layout

    def _move_display_settings(self, panel_id: str, layout: dict) -> dict:
        """A layout's own display settings (lang, theme, screensaver) become the panel's
        configuration the first time; after that the configuration is the only source."""
        panel = layout.pop("panel", None) or {}
        overrides = {k: panel[k] for k in DISPLAY if k in panel}
        if overrides and not self._config_file(panel_id).exists():
            self.save_panel_config(panel_id, {**panel_config.new_panel(), "overrides": overrides})
        return layout

    def save_layout(self, panel_id: str, layout: dict) -> dict:
        """Stores a new revision. Its version is above both the stored and the submitted one."""
        current = self.get_layout(panel_id)
        layout = without_lost_rooms(self._move_display_settings(panel_id, readable(layout)))
        validate_layout(layout)
        version = max(layout.get("version", 1), current["version"] if current else 0) + 1
        saved = {**layout, "version": version}
        self._write(self._layout_file(panel_id), saved)
        return saved

    # ---- edits made on the panel ----

    def role_for(self, panel_id: str, pin: str | None) -> str:
        """Who is editing on the panel, from the PIN it sent. Until an installer PIN is set
        the panel is in setup and counts as the installer."""
        eff = self.effective_config(panel_id)
        if eff["installer_pin"] is None:
            return "installer"
        if not pin:
            return "guest"
        now = time.monotonic()
        if self._locked.get(panel_id, 0) > now:
            return "guest"
        # The PINs set are in the key: a PIN changed is checked again.
        key = hmac.new(self._pin_key, json.dumps([panel_id, pin, eff["installer_pin"], eff["user_pin"]]).encode(),
                       hashlib.sha256).digest()
        role = self._roles.get(key)
        if role is None:
            role = ("installer" if verify_pin(pin, eff["installer_pin"])
                    else "user" if verify_pin(pin, eff["user_pin"]) else "guest")
            if len(self._roles) >= 256:
                self._roles.clear()
            self._roles[key] = role
        if role == "guest":
            # Different wrong PINs in the last PIN_LOCK_S: the same one sent again counts once.
            wrong = {k: t for k, t in self._wrong.get(panel_id, {}).items() if now - t < PIN_LOCK_S}
            wrong[key] = now
            self._wrong[panel_id] = wrong
            if len(wrong) >= PIN_TRIES:
                self._locked[panel_id] = now + PIN_LOCK_S
                del self._wrong[panel_id]
                log.warning("panel %s: %d wrong PINs in %d s, its PINs count for nothing for %d s",
                            panel_id, PIN_TRIES, PIN_LOCK_S, PIN_LOCK_S)
        return role

    def save_display_for_all(self, display: dict) -> int:
        """Display settings for every panel: the project's, and no panel keeps its own value
        for them any more. Returns how many panels had their own value replaced."""
        keys = [k for k in DISPLAY if k in display]
        site = self.get_site()
        defaults = dict(site.get("defaults") or {})
        for key in keys:
            defaults[key] = display[key]
        self.save_site({**site, "defaults": defaults})
        replaced = 0
        for pid in self.panels:
            doc = self.get_panel_config(pid)
            overrides = dict(doc.get("overrides") or {})
            if any(k in overrides for k in keys):
                replaced += 1
                for k in keys:
                    overrides.pop(k, None)
                self.save_panel_config(pid, {**doc, "overrides": overrides})
        return replaced

    def name_floor(self, panel_id: str, floor_id: str, name: str, everywhere: bool, icon: str | None = None) -> None:
        """A floor's name and icon on this panel, or the project's (and no panel keeps its own).
        Blank goes back to HA's; an icon of None stays as it is."""
        values = {"floor_names": name.strip()[:40]}
        if icon is not None:
            values["floor_icons"] = icon.strip()

        def put(settings: dict) -> dict:
            settings = dict(settings or {})
            for key, value in values.items():
                given = dict(settings.get(key) or {})
                if value:
                    given[floor_id] = value
                else:
                    given.pop(floor_id, None)
                settings[key] = given
            return settings

        if everywhere:
            site = self.get_site()
            self.save_site({**site, "defaults": put(site.get("defaults"))})
            for pid in self.panels:
                doc = self.get_panel_config(pid)
                overrides = dict(doc.get("overrides") or {})
                if any(floor_id in (overrides.get(k) or {}) for k in values):
                    for k in values:
                        overrides[k] = {f: v for f, v in (overrides.get(k) or {}).items() if f != floor_id}
                    self.save_panel_config(pid, {**doc, "overrides": overrides})
        else:
            doc = self.get_panel_config(panel_id)
            self.save_panel_config(panel_id, {**doc, "overrides": put(doc.get("overrides"))})

    def save_display_from_panel(self, panel_id: str, display: dict, before: dict | None = None) -> dict:
        """Theme, language and screensaver changed on the panel: stored as this panel's own
        settings, so the add-on shows them too. The panel checked the PIN and the display
        permission when the change was made, maybe offline, so there is nothing to approve.
        [before]: what the panel had of each when the change was made. One that changed since (in
        the add-on, while the panel was away) stays as it is now: the newer wins
        (docs/audit-6-settings.md, item 5)."""
        if isinstance(before, dict):
            now = self.effective_config(panel_id)
            kept = [k for k in display if k in before and now.get(k) != before[k]]
            if kept:
                log.info("panel %s: %s changed meanwhile, kept as it is", panel_id, ", ".join(kept))
            display = {k: v for k, v in display.items() if k not in kept}
        doc = self.get_panel_config(panel_id)
        overrides = dict(doc.get("overrides") or {})
        for key in ("lang", "theme", "home_title"):
            if key in display:
                overrides[key] = display[key]
        if "screensaver" in display:
            overrides["screensaver"] = {**(overrides.get("screensaver") or {}), **display["screensaver"]}
        return self.save_panel_config(panel_id, {**doc, "overrides": overrides})

    def save_from_panel(self, panel_id: str, layout: dict, pin: str | None, known: set[str] | None = None) -> dict:
        """A layout edited on the panel: only changes the role may make (access.py), and only
        devices HA has ([known]: every entity HA has now; None when HA cannot say, then not
        checked). A panel with no layout yet is checked as an edit of an empty one, and keeps
        only HA's devices: a panel moved from another HA brings none of that one's. An edit
        begun on an older version than the stored one (the designer sent one meanwhile) is
        refused: LayoutConflict, nothing of the newer one lost."""
        current = self.get_layout(panel_id)
        sent = layout.get("version")
        if current is not None and isinstance(sent, int) and sent < current.get("version", 0):
            raise LayoutConflict(current)
        if current is None:
            layout = readable(layout)
            if known is not None:
                before = layout_entities(layout)
                layout = only_known(layout, known)
                dropped = before - layout_entities(layout)
                if dropped:
                    log.info("panel %s: first layout without devices this HA does not have: %s",
                             panel_id, ", ".join(sorted(dropped)[:5]))
        base = current if current is not None else {"pages": [], "dashboard": None, "floors": []}
        edited = {k: v for k, v in readable(layout).items() if k != "panel"}
        eff = self.effective_config(panel_id)
        allowed = {a["package"] for a in eff.get("apps", [])}
        sources = set(eff.get("sources", {}).values())
        check_panel_edit(base, edited, role_caps(eff, self.role_for(panel_id, pin)), allowed, sources)
        if known is not None:
            new = layout_entities(edited) - layout_entities(base)
            missing = sorted(new - known)
            if missing:
                raise EditRefused(f"not in this Home Assistant: {', '.join(missing[:5])}")
        return self.save_layout(panel_id, layout)
