"""WebSocket gateway between panels and Home Assistant.

A panel speaks the normal HA WebSocket API to the hub, authenticated with its own key.
The hub opens its own connection to HA and forwards only what the panel may do:

- commands only on entities in the panel's layout, with the entity's own domain;
- state subscriptions limited to those entities;
- read-only registry and state lists (for the layout editor on the panel);
- wallpanel/* commands for the layout, answered by the hub itself.
"""

import asyncio
import json
import logging

import aiohttp
from aiohttp import WSMsgType, web

from .store import LayoutInvalid, Store, layout_entities

log = logging.getLogger(__name__)

READ_ONLY = {
    "get_states",
    "get_config",
    "config/area_registry/list",
    "config/device_registry/list",
    "config/entity_registry/list",
    "ping",
    "unsubscribe_events",
}

# Targets that would reach entities beyond the ones listed.
WIDE_TARGETS = ("area_id", "device_id", "floor_id", "label_id")


def _ids(value) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def check_command(msg: dict, allowed: set[str]) -> tuple[dict | None, str | None]:
    """Returns (message to forward, None) or (None, reason it is refused)."""
    kind = msg.get("type")

    if kind in READ_ONLY:
        return msg, None

    if kind == "subscribe_entities":
        wanted = _ids(msg.get("entity_ids")) or sorted(allowed)
        ids = [e for e in wanted if e in allowed]
        # An empty list means "everything" to HA: never forward it.
        if not ids:
            return None, "empty"
        return {**msg, "entity_ids": ids}, None

    if kind == "call_service":
        target = msg.get("target") or {}
        data = msg.get("service_data") or {}
        if any(k in target or k in data for k in WIDE_TARGETS):
            return None, "only entity_id targets are allowed"
        ids = _ids(target.get("entity_id")) + _ids(data.get("entity_id"))
        if not ids:
            return None, "a target entity is required"
        for e in ids:
            if e not in allowed:
                return None, f"{e} is not on this panel"
            if e.split(".", 1)[0] != msg.get("domain"):
                return None, f"{msg.get('domain')}.{msg.get('service')} cannot target {e}"
        return msg, None

    return None, f"{kind} is not allowed for panels"


class PanelConnection:
    def __init__(self, gateway: "Gateway", panel_id: str, ws: web.WebSocketResponse):
        self.gateway = gateway
        self.panel_id = panel_id
        self.ws = ws
        self.layout_subs: set[int] = set()

    @property
    def allowed(self) -> set[str]:
        return layout_entities(self.gateway.store.get_layout(self.panel_id))

    async def send(self, data: dict) -> None:
        if not self.ws.closed:
            await self.ws.send_str(json.dumps(data, ensure_ascii=False))

    async def push_layout(self) -> None:
        layout = self.gateway.store.get_layout(self.panel_id)
        for sub in list(self.layout_subs):
            await self.send({"id": sub, "type": "event", "event": {"layout": layout}})

    async def handle_local(self, msg: dict) -> None:
        """wallpanel/* commands, answered by the hub."""
        mid, kind = msg.get("id"), msg.get("type")
        store = self.gateway.store
        if kind == "wallpanel/layout/get":
            await self.send({"id": mid, "type": "result", "success": True,
                             "result": {"layout": store.get_layout(self.panel_id)}})
        elif kind == "wallpanel/layout/subscribe":
            self.layout_subs.add(mid)
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
            await self.push_layout()
        elif kind == "wallpanel/layout/save":
            try:
                saved = store.save_layout(self.panel_id, msg.get("layout") or {})
            except LayoutInvalid as e:
                await self.send(_error(mid, "invalid_layout", str(e)))
                return
            await self.send({"id": mid, "type": "result", "success": True, "result": {"version": saved["version"]}})
            await self.gateway.notify_layout(self.panel_id)
        else:
            await self.send(_error(mid, "unknown_command", f"{kind} is not known"))


def _error(mid, code: str, message: str) -> dict:
    return {"id": mid, "type": "result", "success": False, "error": {"code": code, "message": message}}


class Gateway:
    def __init__(self, settings, store: Store, session: aiohttp.ClientSession):
        self.settings = settings
        self.store = store
        self.session = session
        self.connections: dict[str, set[PanelConnection]] = {}

    def online(self, panel_id: str) -> bool:
        return bool(self.connections.get(panel_id))

    async def notify_layout(self, panel_id: str) -> None:
        for conn in list(self.connections.get(panel_id, ())):
            await conn.push_layout()

    async def disconnect(self, panel_id: str) -> None:
        """Used when a panel is revoked: its open connections close at once."""
        for conn in list(self.connections.get(panel_id, ())):
            await conn.ws.close(code=4001, message=b"revoked")

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "wall_panel_hub"})

        # ---- panel authentication ----
        try:
            first = await ws.receive_json(timeout=10)
        except Exception:
            await ws.close()
            return ws
        panel = self.store.authenticate(str(first.get("access_token", ""))) if first.get("type") == "auth" else None
        if panel is None:
            await ws.send_json({"type": "auth_invalid", "message": "Unknown or revoked panel"})
            await ws.close()
            return ws

        # ---- upstream to Home Assistant ----
        try:
            upstream = await self.session.ws_connect(self.settings.ha_ws_url, heartbeat=30)
            await upstream.receive_json(timeout=10)  # auth_required
            await upstream.send_json({"type": "auth", "access_token": self.settings.ha_token})
            ok = await upstream.receive_json(timeout=10)
            if ok.get("type") != "auth_ok":
                raise RuntimeError(f"Home Assistant refused the hub: {ok}")
        except Exception as e:
            log.error("cannot reach Home Assistant: %s", e)
            await ws.close(code=1011, message=b"home assistant unreachable")
            return ws

        conn = PanelConnection(self, panel.id, ws)
        self.connections.setdefault(panel.id, set()).add(conn)
        self.store.seen(panel.id)
        log.info("panel %s (%s) connected", panel.name, panel.id)
        await ws.send_json({"type": "auth_ok", "ha_version": ok.get("ha_version")})

        async def from_ha():
            async for m in upstream:
                if m.type != WSMsgType.TEXT:
                    break
                await ws.send_str(m.data)

        async def from_panel():
            async for m in ws:
                if m.type != WSMsgType.TEXT:
                    break
                msg = json.loads(m.data)
                if str(msg.get("type", "")).startswith("wallpanel/"):
                    await conn.handle_local(msg)
                    continue
                forward, reason = check_command(msg, conn.allowed)
                if forward is not None:
                    await upstream.send_json(forward)
                elif reason == "empty":
                    # Nothing on the panel yet: answer like HA would for zero entities.
                    await conn.send({"id": msg.get("id"), "type": "result", "success": True, "result": None})
                    await conn.send({"id": msg.get("id"), "type": "event", "event": {"a": {}}})
                else:
                    log.warning("panel %s refused: %s", panel.id, reason)
                    await conn.send(_error(msg.get("id"), "unauthorized", reason))

        tasks = [asyncio.create_task(from_ha()), asyncio.create_task(from_panel())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await upstream.close()
            await ws.close()
            self.connections.get(panel.id, set()).discard(conn)
            self.store.seen(panel.id)
            log.info("panel %s disconnected", panel.id)
        return ws
