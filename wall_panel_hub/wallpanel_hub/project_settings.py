"""The project's settings, edited on a panel with the project permission (docs/parity.md,
phase 4b): its name, its PINs and the widgets' sources. The add-on edits the same things;
every panel gets them."""

from .access import role_caps
from .pins import hash_pin
from .store import Store

# Where each built-in widget reads from: HA domain, and the kind of sensor (device class or unit).
SOURCES = {
    "weather": ("weather", None, ()),
    "outdoor_temperature": ("sensor", "temperature", ("°C", "°F")),
    "power": ("sensor", "power", ("W", "kW")),
}


class Refused(Exception):
    """A change this panel's role may not make; the message says why."""


def allowed(store: Store, panel_id: str, pin: str | None) -> str:
    role = store.role_for(panel_id, pin)
    if "project" not in role_caps(store.effective_config(panel_id), role):
        raise Refused("this needs the project permission")
    return role


def _fits(entity: dict, domain: str, device_class: str | None, units: tuple) -> bool:
    if not entity["entity_id"].startswith(domain + "."):
        return False
    return not units or entity.get("device_class") == device_class or entity.get("unit") in units


def get(store: Store, entities: list[dict]) -> dict:
    """The project's name, its sources with what each can be, and whether its PINs are set."""
    site = store.get_site()
    sources = dict(site.get("sources") or {})
    return {
        "name": site.get("name") or "",
        "sources": sources,
        # The one chosen stays listed, even if HA no longer calls it that kind.
        "candidates": {
            key: [{"entity_id": e["entity_id"], "name": e["name"], "state": e.get("state"), "unit": e.get("unit")}
                  for e in entities if _fits(e, *spec) or e["entity_id"] == sources.get(key)]
            for key, spec in SOURCES.items()
        },
        "installer_pin_set": bool(site.get("installer_pin")),
        "user_pin_set": bool(site.get("user_pin")),
    }


def update(store: Store, changes: dict) -> None:
    site = store.get_site()
    if "name" in changes:
        site["name"] = str(changes["name"] or "").strip()[:60]
    if "sources" in changes:
        given = changes["sources"] or {}
        site["sources"] = {k: v for k, v in given.items() if k in SOURCES and v}
    store.save_site(site)  # validated: ConfigInvalid on a bad entity id


def set_pin(store: Store, which: str, new_pin: str | None) -> None:
    """The project's PIN for a role, hashed here; None removes it (the user's, or the
    installer's, which leaves every panel's settings open)."""
    if which not in ("installer", "user"):
        raise Refused("role must be installer or user")
    site = store.get_site()
    store.save_site({**site, f"{which}_pin": hash_pin(str(new_pin)) if new_pin else None})
