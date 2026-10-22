"""A panel's own settings, edited on the panel itself with the panel permission (docs/parity.md,
phase 4a): its name and place, the return time from other apps, the allowed apps, the
permissions of each role, its own PINs, and unpairing. The add-on edits the same things."""

from .access import role_caps
from .config import DEFAULT_ACCESS, ConfigInvalid
from .pins import hash_pin
from .store import Store

ROLES = ("guest", "user", "installer")
# Every permission there is, in the add-on's order.
ALL_CAPS = list(dict.fromkeys(c for caps in DEFAULT_ACCESS.values() for c in caps))


class Refused(Exception):
    """A change this panel's role may not make; the message says why."""


def allowed(store: Store, panel_id: str, pin: str | None) -> str:
    """The role at the panel, if it holds the panel permission."""
    role = store.role_for(panel_id, pin)
    if "panel" not in role_caps(store.effective_config(panel_id), role):
        raise Refused("this needs the panel permission")
    return role


def get(store: Store, panel_id: str, areas: list[dict], installed: list[dict]) -> dict:
    """What the page shows: the panel's own values, and what they fall back to."""
    panel = store.panels[panel_id]
    doc = store.get_panel_config(panel_id)
    eff = store.effective_config(panel_id)
    site = store.get_site()
    return {
        "name": panel.name,
        "area_id": panel.area_id,
        "areas": [{"id": a["id"], "name": a["name"]} for a in areas],
        "app_return_seconds": (doc.get("overrides") or {}).get("app_return_seconds"),
        "project_app_return_seconds": (site.get("defaults") or {}).get("app_return_seconds", 300),
        "apps": list(doc.get("apps") or []),
        "installed_apps": installed,
        "access": eff["access"],
        "caps": ALL_CAPS,
        "installer_pin_own": bool(doc.get("installer_pin")),
        "user_pin_own": bool(doc.get("user_pin")),
        "project_installer_pin": bool(site.get("installer_pin")),
    }


def update(store: Store, panel_id: str, role: str, changes: dict, area_ids: set[str]) -> None:
    """Applies what was changed. No role takes the panel permission away from itself here:
    the way back would be lost on this panel (the add-on can still)."""
    name = str(changes.get("name") or "").strip()[:60]
    if "name" in changes and not name:
        raise Refused("a panel needs a name")
    area = changes.get("area_id") or None
    if area is not None and area not in area_ids:
        raise Refused("unknown room")
    doc = store.get_panel_config(panel_id)
    if "app_return_seconds" in changes:
        overrides = dict(doc.get("overrides") or {})
        value = changes["app_return_seconds"]
        if value is None:
            overrides.pop("app_return_seconds", None)
        else:
            overrides["app_return_seconds"] = int(value)
        doc["overrides"] = overrides
    if "apps" in changes:
        doc["apps"] = list(changes["apps"] or [])
    if "access" in changes:
        access = changes["access"] or {}
        if set(access) != set(ROLES):
            raise Refused("access needs guest, user and installer")
        clean = {r: [c for c in dict.fromkeys(access[r]) if c in ALL_CAPS] for r in ROLES}
        if "panel" not in clean[role]:
            raise Refused("you would lose the panel permission on this panel; change it from the add-on")
        doc["access"] = clean
        doc["access_known"] = ALL_CAPS
    store.save_panel_config(panel_id, doc)  # validated first: ConfigInvalid, and nothing changed
    if "name" in changes:
        store.rename(panel_id, name)
    if "area_id" in changes:
        store.place(panel_id, area)


def set_pin(store: Store, panel_id: str, which: str, new_pin: str | None) -> None:
    """This panel's own PIN for a role (hashed here, never kept as typed), or None for the project's."""
    if which not in ("installer", "user"):
        raise Refused("role must be installer or user")
    doc = store.get_panel_config(panel_id)
    if new_pin:
        doc[f"{which}_pin"] = hash_pin(str(new_pin))
    else:
        doc.pop(f"{which}_pin", None)
    store.save_panel_config(panel_id, doc)


__all__ = ["Refused", "ConfigInvalid", "allowed", "get", "update", "set_pin"]
