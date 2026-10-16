"""Panel configuration (schema/config.schema.json): behaviour and security, apart from the layout.

The add-on keeps one site document and one document per panel; a panel receives the
effective merge: its own value where set, else the site's, else the built-in default.
"""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

_SCHEMA = json.loads((Path(__file__).parent / "schema" / "config.schema.json").read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator({**_SCHEMA, "$ref": f"#/$defs/{name}"})


_VALIDATORS = {name: _validator(name) for name in ("site", "panel", "effective")}


class ConfigInvalid(ValueError):
    pass


def validate(kind: str, doc: dict) -> None:
    errors = sorted(_VALIDATORS[kind].iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise ConfigInvalid(f"{where}: {first.message}")


DEFAULTS = {
    "lang": "ar",
    "theme": "light",
    "screensaver": {"idle_seconds": 60, "dim_brightness": 10},
    "app_return_seconds": 300,
}

# Reordering is off unless the installer turns it on for a panel.
DEFAULT_ACCESS = {
    "guest": ["control", "apps"],
    "user": ["control", "apps", "edit_cards"],
    "installer": ["control", "apps", "edit_cards", "settings"],
}


def new_site() -> dict:
    return {"version": 1, "installer_pin": None, "user_pin": None, "defaults": {}}


def new_panel() -> dict:
    return {"version": 1}


def _setting(key: str, panel: dict, site: dict):
    for source in (panel.get("overrides") or {}, site.get("defaults") or {}):
        if key in source:
            return source[key]
    return DEFAULTS[key]


def effective(site: dict, panel: dict, name: str) -> dict:
    screensaver = {
        k: (panel.get("overrides") or {}).get("screensaver", {}).get(k,
            (site.get("defaults") or {}).get("screensaver", {}).get(k, DEFAULTS["screensaver"][k]))
        for k in DEFAULTS["screensaver"]
    }
    access = {role: list((panel.get("access") or {}).get(role, caps)) for role, caps in DEFAULT_ACCESS.items()}
    # Control is never taken away: a panel that cannot be used is a support call.
    for caps in access.values():
        if "control" not in caps:
            caps.insert(0, "control")
    out = {
        "version": site["version"] + panel["version"],
        "name": name,
        "lang": _setting("lang", panel, site),
        "theme": _setting("theme", panel, site),
        "screensaver": screensaver,
        "app_return_seconds": _setting("app_return_seconds", panel, site),
        "installer_pin": panel.get("installer_pin") or site.get("installer_pin"),
        "user_pin": panel.get("user_pin") or site.get("user_pin"),
        "access": access,
        "apps": list(panel.get("apps") or []),
    }
    validate("effective", out)
    return out
