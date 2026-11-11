"""Updating the panel app from the hub (docs/distribution-plan.md, D4).

Each panel says its app's version when it connects (wallpanel/device/report); the hub keeps it.
The installer asks for an update (a panel, or all); the hub then offers the app it carries to that
panel (wallpanel/app/subscribe), never an older one or the same. The panel downloads it with its
key (/api/app/file, /api/app/dm), Android asks for a tap, and the panel tells how it goes
(wallpanel/app/status). It is done when the panel connects again with the new version.
"""

from . import panel_app

STATES = ("offered", "downloading", "confirm", "waiting", "failed")


class AppUpdates:
    def __init__(self, store):
        self.store = store
        # In memory: an update asked for and not done yet, by panel ({"state", "detail"}).
        self.state: dict[str, dict] = {}

    def carried(self) -> dict | None:
        """The app this add-on can hand out: its manifest, if its file is here."""
        app = panel_app.expected()
        return app if app and panel_app.path() is not None else None

    def needs(self, panel_id: str) -> bool:
        """The add-on carries a newer app than the panel's (an unknown version counts as older)."""
        app, panel = self.carried(), self.store.panels.get(panel_id)
        if app is None or panel is None:
            return False
        return (panel.app_version_code or 0) < app["version_code"]

    def request(self, panel_id: str) -> bool:
        """The installer asks: offered to the panel if it needs it. False if it does not."""
        if not self.needs(panel_id):
            self.state.pop(panel_id, None)
            return False
        self.state[panel_id] = {"state": "offered", "detail": None}
        return True

    def offer(self, panel_id: str) -> dict | None:
        """What the panel is told to install now, if anything."""
        if panel_id not in self.state or self.state[panel_id]["state"] == "failed" or not self.needs(panel_id):
            return None
        app = self.carried()
        offer = {k: app[k] for k in ("version_name", "version_code", "sha256", "size")}
        if app.get("dm") and panel_app.dm_path() is not None:
            offer["dm"] = {k: app["dm"][k] for k in ("sha256", "size")}
        return offer

    def reported(self, panel_id: str, version_name: str | None, version_code: int | None,
                 sdk: int | None, can_install: bool, device_owner: bool = False) -> None:
        self.store.save_device(panel_id, version_name, version_code, sdk, can_install, device_owner)
        # Updated (or the add-on carries nothing newer any more): nothing left to do.
        if panel_id in self.state and not self.needs(panel_id):
            self.state.pop(panel_id)

    def status(self, panel_id: str, state: str, detail: str | None) -> None:
        if panel_id in self.state and state in STATES:
            self.state[panel_id] = {"state": state, "detail": detail}

    def for_panel(self, panel_id: str) -> dict:
        """For the management page."""
        panel = self.store.panels.get(panel_id)
        return {
            "version_name": getattr(panel, "app_version_name", None),
            "version_code": getattr(panel, "app_version_code", None),
            "can_install": bool(getattr(panel, "can_install", False)),
            "device_owner": bool(getattr(panel, "device_owner", False)),
            "needs_update": self.needs(panel_id),
            "update": self.state.get(panel_id),
        }
