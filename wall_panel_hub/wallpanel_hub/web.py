"""Two HTTP servers.

- Panel API (LAN, panel_port): discovery check, pairing, WebSocket gateway.
- Management UI (ingress_port): opened from the Home Assistant sidebar. Requests are
  accepted only from the Supervisor's ingress proxy, so the LAN cannot reach it.
"""

import ipaddress
import logging
import time
from pathlib import Path

from aiohttp import web

from .gateway import Gateway
from .settings import VERSION
from .store import Store

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)


def panel_api(store: Store, gateway: Gateway) -> web.Application:
    app = web.Application(client_max_size=256 * 1024)

    async def info(_):
        return web.json_response({"service": "wall_panel_hub", "version": VERSION})

    async def pair(request: web.Request):
        try:
            body = await request.json()
            panel_id = str(body["panel_id"])[:64]
        except Exception:
            raise web.HTTPBadRequest(text="panel_id required")
        req = store.request_pairing(panel_id, str(body.get("name", "")), str(body.get("model", "")))
        return web.json_response({"request_id": req.request_id, "code": req.code})

    async def pair_status(request: web.Request):
        return web.json_response(store.pairing_status(request.match_info["rid"]))

    app.router.add_get("/api/info", info)
    app.router.add_post("/api/pair", pair)
    app.router.add_get("/api/pair/{rid}", pair_status)
    app.router.add_get("/api/websocket", gateway.handle)
    return app


def _allowed(remote: str | None, networks: list) -> bool:
    try:
        ip = ipaddress.ip_address(remote or "")
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in n for n in networks)


def management_ui(store: Store, gateway: Gateway, allowed: tuple[str, ...]) -> web.Application:
    networks = [ipaddress.ip_network(n) for n in allowed]

    @web.middleware
    async def ingress_only(request: web.Request, handler):
        if not _allowed(request.remote, networks):
            log.warning("management UI refused for %s", request.remote)
            raise web.HTTPForbidden(text=f"Open Wall Panels from the Home Assistant sidebar. ({request.remote})")
        return await handler(request)

    app = web.Application(middlewares=[ingress_only])

    async def index(_):
        return web.FileResponse(STATIC / "index.html")

    async def overview(_):
        now = time.time()
        return web.json_response({
            "version": VERSION,
            "panels": [
                {
                    "id": p.id,
                    "name": p.name,
                    "model": p.model,
                    "paired_at": p.paired_at,
                    "last_seen": p.last_seen,
                    "online": gateway.online(p.id),
                    "layout_version": (store.get_layout(p.id) or {}).get("version"),
                }
                for p in sorted(store.panels.values(), key=lambda p: p.name)
            ],
            "pending": [
                {"request_id": r.request_id, "name": r.name, "model": r.model, "code": r.code,
                 "age": int(now - r.created)}
                for r in store.pending.values() if not r.key and not r.rejected
            ],
        })

    async def approve(request: web.Request):
        try:
            store.approve(request.match_info["rid"])
        except KeyError:
            raise web.HTTPNotFound(text="request expired")
        return web.json_response({"ok": True})

    async def reject(request: web.Request):
        store.reject(request.match_info["rid"])
        return web.json_response({"ok": True})

    async def revoke(request: web.Request):
        pid = request.match_info["pid"]
        store.revoke(pid)
        await gateway.disconnect(pid)
        return web.json_response({"ok": True})

    async def rename(request: web.Request):
        pid = request.match_info["pid"]
        if pid not in store.panels:
            raise web.HTTPNotFound()
        store.rename(pid, str((await request.json()).get("name", "")).strip() or "Panel")
        return web.json_response({"ok": True})

    async def get_layout(request: web.Request):
        return web.json_response({"layout": store.get_layout(request.match_info["pid"])})

    # Paths are relative in the page, so they work behind the ingress prefix.
    app.router.add_get("/", index)
    app.router.add_get("/api/overview", overview)
    app.router.add_post("/api/pending/{rid}/approve", approve)
    app.router.add_post("/api/pending/{rid}/reject", reject)
    app.router.add_post("/api/panels/{pid}/revoke", revoke)
    app.router.add_post("/api/panels/{pid}/rename", rename)
    app.router.add_get("/api/panels/{pid}/layout", get_layout)
    return app
