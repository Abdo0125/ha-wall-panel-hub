"""Two HTTP servers.

- Panel API (LAN, panel_port): discovery check, pairing, WebSocket gateway.
- Management UI (ingress_port): opened from the Home Assistant sidebar. Requests are
  accepted only from the Supervisor's ingress proxy, so the LAN cannot reach it.
"""

import ipaddress
import logging
import re
import time
from pathlib import Path

from aiohttp import web

from .cards import REGISTRY
from .catalog import CatalogCache
from .config import ConfigInvalid
from .pins import PinInvalid, hash_pin
from .gateway import Gateway
from .settings import VERSION
from .store import LayoutConflict, LayoutInvalid, Store

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
        req = store.request_pairing(panel_id, str(body.get("name", "")), str(body.get("model", "")),
                                    str(body.get("platform", "android")))
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


def _page(name: str) -> web.Response:
    """A page that is never kept by the browser, whose files carry the add-on version: after
    an update the browser loads the new scripts instead of the ones it cached."""
    html = (STATIC / name).read_text(encoding="utf-8")
    html = re.sub(r'(src|href)="(static/[^"?#]+)"', lambda m: f'{m[1]}="{m[2]}?v={VERSION}"', html)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


@web.middleware
async def _revalidate_static(request: web.Request, handler):
    """Static files are checked with the add-on on every load (cheap: 304 when unchanged)."""
    response = await handler(request)
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _without_pins(doc: dict) -> dict:
    """PIN hashes stay in the hub (and on the panels); the page only learns whether one is set."""
    out = {k: v for k, v in doc.items() if k not in ("installer_pin", "user_pin")}
    out["installer_pin_set"] = bool(doc.get("installer_pin"))
    out["user_pin_set"] = bool(doc.get("user_pin"))
    return out


def management_ui(store: Store, gateway: Gateway, allowed: tuple[str, ...],
                  catalog: CatalogCache | None = None, browser=None) -> web.Application:
    networks = [ipaddress.ip_network(n) for n in allowed]

    @web.middleware
    async def ingress_only(request: web.Request, handler):
        if not _allowed(request.remote, networks):
            log.warning("management UI refused for %s", request.remote)
            raise web.HTTPForbidden(text=f"Open Wall Panels from the Home Assistant sidebar. ({request.remote})")
        return await handler(request)

    app = web.Application(middlewares=[ingress_only, _revalidate_static])

    async def index(_):
        return _page("index.html")

    async def editor(_):
        return _page("editor.html")

    # ---- layout editor ----

    async def editor_catalog(request: web.Request):
        if catalog is None:
            raise web.HTTPServiceUnavailable(text="no Home Assistant connection")
        try:
            return web.json_response(await catalog.get(refresh=request.query.get("refresh") == "1"))
        except Exception as e:  # HA down, auth... shown in the editor's message pane
            log.warning("catalog failed: %s", e)
            return web.json_response({"error": str(e)}, status=502)

    async def editor_cards(_):
        return web.json_response(REGISTRY)

    async def editor_state(_):
        return web.json_response({"panels": [
            {
                "id": p.id,
                "name": p.name,
                "model": p.model,
                "platform": p.platform,
                "area_id": p.area_id,
                "online": gateway.online(p.id),
                "sent": store.get_layout(p.id),
                "draft": store.get_draft(p.id),
            }
            for p in sorted(store.panels.values(), key=lambda p: p.name)
        ]})

    def _panel(request: web.Request) -> str:
        pid = request.match_info["pid"]
        if pid not in store.panels:
            raise web.HTTPNotFound(text="unknown panel")
        return pid

    async def put_draft(request: web.Request):
        pid = _panel(request)
        body = await request.json()
        store.save_draft(pid, body["layout"], body.get("base_version"))
        return web.json_response({"ok": True})

    async def delete_draft(request: web.Request):
        store.delete_draft(_panel(request))
        return web.json_response({"ok": True})

    async def send_draft(request: web.Request):
        pid = _panel(request)
        force = bool((await request.json()).get("force")) if request.can_read_body else False
        try:
            saved = store.send_draft(pid, force=force)
        except KeyError:
            raise web.HTTPNotFound(text="nothing to send")
        except LayoutInvalid as e:
            return web.json_response({"error": str(e)}, status=422)
        except LayoutConflict as e:
            return web.json_response({"conflict": True, "current": e.current}, status=409)
        await gateway.notify_layout(pid)
        return web.json_response({"version": saved["version"], "online": gateway.online(pid)})

    async def overview(_):
        now = time.time()
        requesting = {r.panel_id for r in store.pending.values() if not r.key and not r.rejected}
        discovered = [
            p for p in (browser.panels.values() if browser else [])
            if p["id"] not in store.panels and p["id"] not in requesting
        ]
        return web.json_response({
            "version": VERSION,
            "installer_pin_set": bool(store.get_site().get("installer_pin")),
            "discovered": sorted(discovered, key=lambda p: p["name"]),
            "panels": [
                {
                    "id": p.id,
                    "name": p.name,
                    "model": p.model,
                    "platform": p.platform,
                    "paired_at": p.paired_at,
                    "last_seen": p.last_seen,
                    "online": gateway.online(p.id),
                    "layout_version": (store.get_layout(p.id) or {}).get("version"),
                }
                for p in sorted(store.panels.values(), key=lambda p: p.name)
            ],
            "pending": [
                {"request_id": r.request_id, "panel_id": r.panel_id, "name": r.name, "model": r.model, "platform": r.platform,
                 "code": r.code, "age": int(now - r.created)}
                for r in store.pending.values() if not r.key and not r.rejected
            ],
        })

    async def approve(request: web.Request):
        try:
            panel = store.approve(request.match_info["rid"])
        except KeyError:
            raise web.HTTPNotFound(text="request expired")
        return web.json_response({"ok": True, "panel_id": panel.id})

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
        await gateway.notify_config(pid)
        return web.json_response({"ok": True})

    async def place(request: web.Request):
        pid = request.match_info["pid"]
        if pid not in store.panels:
            raise web.HTTPNotFound()
        store.place(pid, (await request.json()).get("area_id"))
        return web.json_response({"ok": True})

    # ---- configuration (project and panels) ----

    async def _body(request: web.Request) -> dict:
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="JSON body required")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON object required")
        return body

    def _invalid(e: Exception):
        return web.json_response({"error": str(e)}, status=422)

    async def get_site(_):
        return web.json_response(_without_pins(store.get_site()))

    async def put_site(request: web.Request):
        body = await _body(request)
        site = store.get_site()
        for key in ("name", "defaults", "devices", "sources"):
            if key in body:
                site[key] = body[key]
        try:
            saved = store.save_site(site)
        except ConfigInvalid as e:
            return _invalid(e)
        await gateway.notify_config()
        return web.json_response(_without_pins(saved))

    def _pin_value(body: dict):
        role = body.get("role")
        if role not in ("installer", "user"):
            raise web.HTTPBadRequest(text="role must be installer or user")
        pin = body.get("pin")
        return role, (hash_pin(str(pin)) if pin else None)

    async def site_pin(request: web.Request):
        try:
            role, value = _pin_value(await _body(request))
        except PinInvalid as e:
            return _invalid(e)
        store.save_site({**store.get_site(), f"{role}_pin": value})
        await gateway.notify_config()
        return web.json_response({"ok": True})

    async def get_panel_config(request: web.Request):
        pid = _panel(request)
        return web.json_response({
            "config": _without_pins(store.get_panel_config(pid)),
            "effective": _without_pins(store.effective_config(pid)),
            "installed_apps": gateway.installed_apps.get(pid, []),
        })

    async def put_panel_config(request: web.Request):
        pid = _panel(request)
        body = await _body(request)
        doc = store.get_panel_config(pid)
        for key in ("overrides", "access", "access_known", "apps"):
            if key in body:
                if body[key] is None:
                    doc.pop(key, None)
                else:
                    doc[key] = body[key]
        try:
            store.save_panel_config(pid, doc)
        except ConfigInvalid as e:
            return _invalid(e)
        await gateway.notify_config(pid)
        return web.json_response({"effective": _without_pins(store.effective_config(pid))})

    async def panel_pin(request: web.Request):
        """A PIN for this panel only, or null to use the project's again."""
        pid = _panel(request)
        try:
            role, value = _pin_value(await _body(request))
        except PinInvalid as e:
            return _invalid(e)
        doc = store.get_panel_config(pid)
        if value:
            doc[f"{role}_pin"] = value
        else:
            doc.pop(f"{role}_pin", None)
        store.save_panel_config(pid, doc)
        await gateway.notify_config(pid)
        return web.json_response({"ok": True})

    async def get_layout(request: web.Request):
        return web.json_response({"layout": store.get_layout(request.match_info["pid"])})

    # Paths are relative in the page, so they work behind the ingress prefix.
    app.router.add_get("/", index)
    app.router.add_get("/editor", editor)
    app.router.add_static("/static/", STATIC)
    app.router.add_get("/api/editor/catalog", editor_catalog)
    app.router.add_get("/api/editor/state", editor_state)
    app.router.add_get("/api/editor/cards", editor_cards)
    app.router.add_put("/api/editor/panels/{pid}/draft", put_draft)
    app.router.add_delete("/api/editor/panels/{pid}/draft", delete_draft)
    app.router.add_post("/api/editor/panels/{pid}/send", send_draft)
    app.router.add_get("/api/overview", overview)
    app.router.add_post("/api/pending/{rid}/approve", approve)
    app.router.add_post("/api/pending/{rid}/reject", reject)
    app.router.add_post("/api/panels/{pid}/revoke", revoke)
    app.router.add_post("/api/panels/{pid}/rename", rename)
    app.router.add_post("/api/panels/{pid}/area", place)
    app.router.add_get("/api/panels/{pid}/layout", get_layout)
    app.router.add_get("/api/site", get_site)
    app.router.add_put("/api/site", put_site)
    app.router.add_post("/api/site/pin", site_pin)
    app.router.add_get("/api/panels/{pid}/config", get_panel_config)
    app.router.add_put("/api/panels/{pid}/config", put_panel_config)
    app.router.add_post("/api/panels/{pid}/pin", panel_pin)
    return app
