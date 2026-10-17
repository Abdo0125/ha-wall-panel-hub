"""The HA building as the layout editor sees it: floors, areas and supported entities.

Same rules as the panel's own editor (HaCatalog.kt), so both show the same entities:
- an entity's area is its own, or else its device's;
- hidden, disabled, config and diagnostic entities are left out;
- only domains the panel has a card for (schema/cards.json).
Keep the two in step.
"""

import asyncio
import json

import aiohttp

from .cards import KINDS, kind_of

COMMANDS = {
    "floors": "config/floor_registry/list",
    "areas": "config/area_registry/list",
    "devices": "config/device_registry/list",
    "registry": "config/entity_registry/list",
    "states": "get_states",
}


def build(floors: list, areas: list, devices: list, registry: list, states: list) -> dict:
    device_area = {d["id"]: d.get("area_id") for d in devices if "id" in d}
    reg = {e["entity_id"]: e for e in registry if "entity_id" in e}

    entities = []
    for s in states:
        eid = s.get("entity_id", "")
        kind = kind_of(eid)
        if not kind:
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
        })
    entities.sort(key=lambda e: (e["name"].lower(), e["entity_id"]))

    return {
        "floors": sorted(
            ({"id": f["floor_id"], "name": f.get("name") or f["floor_id"], "level": f.get("level")}
             for f in floors if "floor_id" in f),
            key=lambda f: (f["level"] is None, f["level"] or 0, f["name"]),
        ),
        "areas": sorted(
            ({"id": a["area_id"], "name": a.get("name") or a["area_id"], "floor_id": a.get("floor_id"),
              "icon": a.get("icon")} for a in areas if "area_id" in a),
            key=lambda a: a["name"].lower(),
        ),
        "entities": entities,
    }


async def fetch(session: aiohttp.ClientSession, ws_url: str, token: str) -> dict:
    """One short WebSocket session to HA, all five lists in parallel."""
    async with session.ws_connect(ws_url, heartbeat=30) as ws:
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

    def __init__(self, fetcher, ttl: float = 60):
        self.fetcher = fetcher
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
            return self.value
