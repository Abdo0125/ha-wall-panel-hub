"""Paired panels, pairing requests, layouts and configuration, kept under the add-on's /data."""

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from . import config as panel_config
from .access import EditRefused, check_panel_edit, role_caps
from .cards import card_entities, check_card
from .migrate import is_current, upgrade
from .pins import verify_pin

# Display settings that live in the panel configuration once a hub manages the panel.
DISPLAY = ("lang", "theme", "screensaver", "home_title")
# The apps that pair with the hub, one per kind of device.
PLATFORMS = ("android", "esp32", "phone")
# The kinds of panel, each with its own app: a layout or a setting that depends on the screen's
# size goes to panels of the same type only. iPhone and Android phones share one.
TYPES = ("android_11", "android_4", "esp32_4", "phone")
DEFAULT_TYPE = {"android": "android_11", "esp32": "esp32_4", "phone": "phone"}


def _type(platform: str, kind: str | None) -> str:
    return kind if kind in TYPES else DEFAULT_TYPE.get(platform, "android_11")

PAIRING_TTL_S = 600
MAX_PENDING = 10

_SCHEMA = json.loads((Path(__file__).parent / "schema" / "layout.schema.json").read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(_SCHEMA)


class LayoutInvalid(ValueError):
    pass


class LayoutConflict(Exception):
    """The panel's layout changed (e.g. edited on the panel) after the draft was started."""

    def __init__(self, current: dict | None):
        super().__init__("layout changed since the draft was started")
        self.current = current


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


def layout_entities(layout: dict | None) -> set[str]:
    if not layout:
        return set()
    cards = [c for p in layout.get("pages", []) for c in p.get("cards", [])]
    cards += (layout.get("dashboard") or {}).get("cards", [])
    return {e for c in cards for e in card_entities(c)}


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
        if self.panels_file.exists():
            raw = json.loads(self.panels_file.read_text(encoding="utf-8"))
            self.panels = {p["id"]: Panel(**p) for p in raw}
        # Pairing requests live in memory only: a restart simply asks the panel to retry.
        self.pending: dict[str, PairRequest] = {}

    # ---- persistence ----

    def _write(self, path: Path, data) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a power cut never leaves half a file

    def _save_panels(self) -> None:
        self._write(self.panels_file, [asdict(p) for p in self.panels.values()])

    # ---- pairing ----

    def _expire(self) -> None:
        now = time.time()
        for rid in [r for r, p in self.pending.items() if now - p.created > PAIRING_TTL_S]:
            del self.pending[rid]

    def request_pairing(self, panel_id: str, name: str, model: str, platform: str = "android",
                        kind: str | None = None) -> PairRequest:
        self._expire()
        # One open request per panel: a retry replaces the previous one.
        for rid in [r for r, p in self.pending.items() if p.panel_id == panel_id]:
            del self.pending[rid]
        while len(self.pending) >= MAX_PENDING:
            del self.pending[min(self.pending.values(), key=lambda p: p.created).request_id]
        req = PairRequest(
            request_id=secrets.token_urlsafe(16),
            panel_id=panel_id,
            name=name[:60] or "Panel",
            model=model[:60],
            code=f"{secrets.randbelow(1_000_000):06d}",
            platform=platform if platform in PLATFORMS else "android",
            type=_type(platform, kind),
        )
        self.pending[req.request_id] = req
        return req

    def approve(self, request_id: str) -> Panel:
        self._expire()
        req = self.pending[request_id]
        key = secrets.token_urlsafe(32)
        panel = Panel(req.panel_id, req.name, req.model, _hash(key), time.time(), platform=req.platform, type=req.type)
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

    def rename(self, panel_id: str, name: str) -> None:
        self.panels[panel_id].name = name[:60]
        self._save_panels()

    def place(self, panel_id: str, area_id: str | None) -> None:
        self.panels[panel_id].area_id = area_id or None
        self._save_panels()

    def revoke(self, panel_id: str) -> None:
        self.panels.pop(panel_id, None)
        self._save_panels()
        self.delete_draft(panel_id)
        self._config_file(panel_id).unlink(missing_ok=True)

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

    # ---- configuration ----

    def get_site(self) -> dict:
        if self.site_file.exists():
            return json.loads(self.site_file.read_text(encoding="utf-8"))
        return panel_config.new_site()

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
                floors.append({"id": f["id"], "name": f["name"], "areas": areas})
        site = self.get_site()
        if floors == (site.get("floors") or []):
            return False
        self.save_site({**site, "floors": floors})
        return True

    def get_panel_config(self, panel_id: str) -> dict:
        f = self._config_file(panel_id)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else panel_config.new_panel()

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
        if not f.exists():
            return None
        draft = json.loads(f.read_text(encoding="utf-8"))
        layout = draft.get("layout") or {}
        if not is_current(layout):
            layout = upgrade(layout, self.get_layout(panel_id))
        layout.pop("panel", None)
        draft["layout"] = layout
        return draft

    def save_draft(self, panel_id: str, layout: dict, base_version: int | None) -> None:
        # Not validated: a draft may be half done. Validation happens on send.
        self._write(self._draft_file(panel_id), {"layout": layout, "base_version": base_version})

    def delete_draft(self, panel_id: str) -> None:
        self._draft_file(panel_id).unlink(missing_ok=True)

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
        if not f.exists():
            return None
        layout = json.loads(f.read_text(encoding="utf-8"))
        if not is_current(layout) or "panel" in layout:
            # Stored before 0.5.0 / 0.6.0: upgraded once, same version (what the panel
            # shows did not change); display settings move to the panel configuration.
            layout = self._move_display_settings(panel_id, upgrade(layout))
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
        """Stores a new revision. Its version is above both the stored and the submitted one.
        A v1 layout (from an older app) is upgraded first, keeping the stored cards' settings."""
        current = self.get_layout(panel_id)
        layout = self._move_display_settings(panel_id, upgrade(layout, current))
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
        if eff["installer_pin"] is None or verify_pin(pin, eff["installer_pin"]):
            return "installer"
        if verify_pin(pin, eff["user_pin"]):
            return "user"
        return "guest"

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

    def save_display_from_panel(self, panel_id: str, display: dict) -> dict:
        """Theme, language and screensaver changed on the panel: stored as this panel's own
        settings, so the add-on shows them too. The panel checked the PIN and the display
        permission when the change was made, maybe offline, so there is nothing to approve."""
        doc = self.get_panel_config(panel_id)
        overrides = dict(doc.get("overrides") or {})
        for key in ("lang", "theme", "home_title"):
            if key in display:
                overrides[key] = display[key]
        if "screensaver" in display:
            overrides["screensaver"] = {**(overrides.get("screensaver") or {}), **display["screensaver"]}
        return self.save_panel_config(panel_id, {**doc, "overrides": overrides})

    def save_from_panel(self, panel_id: str, layout: dict, pin: str | None) -> dict:
        """A layout edited on the panel: only changes the role may make (access.py).
        A panel with no layout yet (just paired) uploads its own as the starting point."""
        current = self.get_layout(panel_id)
        if current is not None:
            edited = {k: v for k, v in upgrade(layout, current).items() if k != "panel"}
            eff = self.effective_config(panel_id)
            allowed = {a["package"] for a in eff.get("apps", [])}
            sources = set(eff.get("sources", {}).values())
            check_panel_edit(current, edited, role_caps(eff, self.role_for(panel_id, pin)), allowed, sources)
        return self.save_layout(panel_id, layout)
