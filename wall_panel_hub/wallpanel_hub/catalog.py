"""The HA building as the panels and the designer see it: floors, areas, supported entities, and
what each device can do. Its rules are the only ones:
- an entity's area is its own, or else its device's;
- hidden, disabled, config and diagnostic entities are left out;
- only domains the panel has a card for (schema/cards.json).
"""

import asyncio
import json

import aiohttp

from .cards import KINDS, kind_of
from .settings import HA_MAX_MESSAGE

COMMANDS = {
    "floors": "config/floor_registry/list",
    "areas": "config/area_registry/list",
    "devices": "config/device_registry/list",
    "registry": "config/entity_registry/list",
    "states": "get_states",
}


# Device domains the panels will get cards for.
UPCOMING = ("media_player", "alarm_control_panel", "vacuum", "water_heater", "humidifier", "valve",
            "siren", "remote", "number", "input_number", "select", "input_select")


TONE_MODES = ("color_temp", "hs", "xy", "rgb", "rgbw", "rgbww")
# HA's CoverEntityFeature.
COVER_OPEN, COVER_CLOSE, COVER_SET_POSITION = 1, 2, 4
COVER_OPEN_TILT, COVER_CLOSE_TILT, COVER_SET_TILT = 16, 32, 128
# What a device's features are read from.
FEATURE_ATTRS = ("supported_color_modes", "supported_features", "device_class", "entity_id", "current_position")
# What a card draws, for a device not on the panel yet (a preview): the catalog's copy of a state.
# Named one by one: what else HA holds (a camera's access token, its picture) never leaves the hub.
CARD_ATTRS = FEATURE_ATTRS + (
    "friendly_name", "icon", "unit_of_measurement", "color_mode", "brightness", "color_temp_kelvin", "hs_color",
    "min_color_temp_kelvin", "max_color_temp_kelvin", "effect", "effect_list", "percentage", "percentage_step",
    "current_tilt_position", "temperature", "temperature_unit", "target_temp_low", "target_temp_high",
    "target_temp_step", "min_temp", "max_temp", "current_temperature", "humidity", "current_humidity",
    "hvac_modes", "hvac_action", "fan_mode", "fan_modes", "swing_mode", "swing_modes", "preset_mode", "preset_modes",
    "last_triggered",
)


def features(entity_id: str, attrs: dict) -> list[str]:
    """What the device can do, the only place that says: the panels draw it (they get it with each
    state, DeviceFeatures) and a card's options need it ("requires" in cards.json)."""
    domain = entity_id.split(".", 1)[0]
    found = []
    modes = attrs.get("supported_color_modes") or []
    if domain == "light" and any(m != "onoff" for m in modes):
        found.append("dimming")
    # On / off only: how it looks when on (on_look).
    if domain == "light" and not any(m != "onoff" for m in modes):
        found.append("onoff")
    # No tone of its own: the colour its card shows when on (on_color).
    if domain == "light" and not any(m in TONE_MODES for m in modes):
        found.append("plain")
    if domain == "light" and "color_temp" in modes:
        found.append("white")
    if domain == "light" and any(m in ("hs", "xy", "rgb", "rgbw", "rgbww") for m in modes):
        found.append("color")
    # A Home Assistant group (a light group): its members in entity_id.
    if domain == "light" and isinstance(attrs.get("entity_id"), list) and attrs["entity_id"]:
        found.append("group")
    if domain == "fan" and int(attrs.get("supported_features") or 0) & 1:  # FanEntityFeature.SET_SPEED
        found.append("speeds")
    # How a cover is drawn, so which colours it takes (the app's coverStyle).
    if domain == "cover":
        known = attrs.get("supported_features")
        bits = int(known or 0)
        tilts = bits & (COVER_OPEN_TILT | COVER_SET_TILT)
        found.append("curtain" if attrs.get("device_class") == "curtain" else "blind" if tilts else "shutter")
        # A device that does not say still shows where it is.
        if bits & COVER_SET_POSITION or (known is None and "current_position" in attrs):
            found.append("position")
        if bits & COVER_SET_TILT:
            found.append("tilt")
        # Slats, a pergola's roof: no open, close or position of their own.
        if known is not None and not bits & (COVER_OPEN | COVER_CLOSE | COVER_SET_POSITION) and \
                bits & (COVER_SET_TILT | COVER_OPEN_TILT | COVER_CLOSE_TILT):
            found.append("tilt_only")
    return found


def device_types(config: dict) -> dict:
    """The types the project's device list gives, by entity."""
    return {e: d["type"] for e, d in (config.get("devices") or {}).items() if d.get("type")}


def typed(entity_id: str, attrs: dict, types: dict) -> dict:
    """[attrs] with the project's device list laid over them: a device's type there is its class."""
    return {**attrs, "device_class": types[entity_id]} if entity_id in types else attrs


class DeviceFeatures:
    """Adds what each device can do to the states going to one panel, so the two arrive together:
    "f" in subscribe_entities' format, for a whole state and in a change that alters it. [types]
    gives the project's types by entity (config "devices")."""

    def __init__(self, types=dict):
        self.types = types
        self.attrs: dict[str, dict] = {}
        self.sent: dict[str, list[str]] = {}

    def _now(self, entity_id: str) -> list[str]:
        return features(entity_id, typed(entity_id, self.attrs[entity_id], self.types()))

    def add_to_event(self, event: dict) -> dict:
        for eid, state in (event.get("a") or {}).items():
            attrs = state.get("a") or {}
            self.attrs[eid] = {k: attrs[k] for k in FEATURE_ATTRS if k in attrs}
            state["f"] = self.sent[eid] = self._now(eid)
        for eid, change in (event.get("c") or {}).items():
            attrs = self.attrs.get(eid)
            if attrs is None:
                continue
            plus = change.get("+") or {}
            attrs.update({k: v for k, v in (plus.get("a") or {}).items() if k in FEATURE_ATTRS})
            for key in (change.get("-") or {}).get("a") or []:
                attrs.pop(key, None)
            now = self._now(eid)
            if now != self.sent.get(eid):
                self.sent[eid] = change.setdefault("+", plus)["f"] = now
        for eid in event.get("r") or []:
            self.attrs.pop(eid, None)
            self.sent.pop(eid, None)
        return event

    def add_to_states(self, states: list) -> list:
        """The result of get_states: a missed update caught up."""
        types = self.types()
        for state in states:
            eid = state.get("entity_id", "")
            state["f"] = features(eid, typed(eid, state.get("attributes") or {}, types))
        return states

    def changed(self) -> dict | None:
        """The project's list changed: an event for the devices that now do otherwise, or None."""
        change = {}
        for eid in self.attrs:
            now = self._now(eid)
            if now != self.sent.get(eid):
                self.sent[eid] = now
                change[eid] = {"+": {"f": now}}
        return {"c": change} if change else None


def with_types(catalog: dict, types: dict) -> dict:
    """The catalog with the project's device list laid over it, as this panel or project sees it."""
    if not types:
        return catalog

    def entity(e: dict) -> dict:
        if e["entity_id"] not in types:
            return e
        attrs = typed(e["entity_id"], e["attributes"], types)
        return {**e, "attributes": attrs, "features": features(e["entity_id"], attrs)}

    return {**catalog, "entities": [entity(e) for e in catalog["entities"]]}


def features_of(catalog: dict):
    """An entity's features by the catalog: none for one HA does not have now."""
    known = {e["entity_id"]: e["features"] for e in catalog["entities"]}
    return lambda entity_id: known.get(entity_id, [])


def build(floors: list, areas: list, devices: list, registry: list, states: list) -> dict:
    device_area = {d["id"]: d.get("area_id") for d in devices if "id" in d}
    reg = {e["entity_id"]: e for e in registry if "entity_id" in e}

    entities = []
    upcoming = []
    for s in states:
        eid = s.get("entity_id", "")
        kind = kind_of(eid)
        if not kind:
            if eid.split(".", 1)[0] in UPCOMING:
                r = reg.get(eid)
                if not (r and (r.get("hidden_by") or r.get("disabled_by") or r.get("entity_category"))):
                    upcoming.append({
                        "entity_id": eid,
                        "name": (s.get("attributes") or {}).get("friendly_name") or (r or {}).get("name") or eid,
                        "area_id": (r or {}).get("area_id") or device_area.get((r or {}).get("device_id")),
                    })
            continue
        r = reg.get(eid)
        if r and (r.get("hidden_by") or r.get("disabled_by") or r.get("entity_category")):
            continue
        area = (r or {}).get("area_id") or device_area.get((r or {}).get("device_id"))
        attrs = s.get("attributes") or {}
        entities.append({
            "entity_id": eid,
            "name": (s.get("attributes") or {}).get("friendly_name") or (r or {}).get("name") or eid,
            "area_id": area,
            "type": kind,
            "controllable": KINDS[kind]["controllable"],
            "state": s.get("state"),
            # What HA says the device is and shows it with; the project's device list
            # (site.devices) can change both for every panel.
            "device_class": attrs.get("device_class"),
            "unit": attrs.get("unit_of_measurement"),
            "icon": attrs.get("icon") or (r or {}).get("icon"),
            "features": features(eid, attrs),
            "attributes": {k: attrs[k] for k in CARD_ATTRS if k in attrs},
        })
    entities.sort(key=lambda e: (e["name"].lower(), e["entity_id"]))

    return {
        "floors": sorted(
            ({"id": f["floor_id"], "name": f.get("name") or f["floor_id"], "level": f.get("level"),
              "icon": f.get("icon")}
             for f in floors if "floor_id" in f),
            key=lambda f: (f["level"] is None, f["level"] or 0, f["name"]),
        ),
        "areas": sorted(
            ({"id": a["area_id"], "name": a.get("name") or a["area_id"], "floor_id": a.get("floor_id"),
              "icon": a.get("icon"), "picture": a.get("picture")} for a in areas if "area_id" in a),
            key=lambda a: a["name"].lower(),
        ),
        "entities": entities,
        # Every entity HA has, hidden ones too: the hub checks panels' layouts by it.
        "known": sorted(s["entity_id"] for s in states if s.get("entity_id")),
        # Devices the panels have no card for yet: shown as coming soon.
        "upcoming": sorted(upcoming, key=lambda e: (e["name"].lower(), e["entity_id"])),
    }


async def fetch(session: aiohttp.ClientSession, ws_url: str, token: str) -> dict:
    """One short WebSocket session to HA, all five lists in parallel."""
    async with session.ws_connect(ws_url, heartbeat=30, max_msg_size=HA_MAX_MESSAGE) as ws:
        await ws.receive_json(timeout=10)
        await ws.send_json({"type": "auth", "access_token": token})
        if (await ws.receive_json(timeout=10)).get("type") != "auth_ok":
            raise RuntimeError("Home Assistant refused the hub")

        ids = {}
        for i, (key, command) in enumerate(COMMANDS.items(), start=1):
            ids[i] = key
            await ws.send_json({"id": i, "type": command})

        results: dict[str, list] = {}
        while len(results) < len(ids):
            msg = await ws.receive_json(timeout=30)
            key = ids.get(msg.get("id"))
            if key and msg.get("type") == "result":
                # Floors arrived in HA 2024.3; older versions simply have none.
                results[key] = (msg.get("result") or []) if msg.get("success") else []
                if not msg.get("success") and key != "floors":
                    raise RuntimeError(f"{COMMANDS[key]} failed: {json.dumps(msg.get('error'))}")
        return build(**results)


class CatalogCache:
    """Avoids hitting HA on every editor click; the editor can force a refresh."""

    def __init__(self, fetcher, ttl: float = 60, on_fetch=None):
        self.fetcher = fetcher
        # Told of every fresh catalog (the floors go to the panels from it).
        self.on_fetch = on_fetch
        self.ttl = ttl
        self.value: dict | None = None
        self.at = 0.0
        self.lock = asyncio.Lock()

    async def get(self, refresh: bool = False) -> dict:
        async with self.lock:
            now = asyncio.get_running_loop().time()
            if refresh or self.value is None or now - self.at > self.ttl:
                self.value = await self.fetcher()
                self.at = now
                if self.on_fetch:
                    await self.on_fetch(self.value)
            return self.value
