"""Two HTTP servers.

- Panel API (LAN, panel_port): discovery check, pairing, WebSocket gateway.
- Management UI (ingress_port): opened from the Home Assistant sidebar. Requests are
  accepted only from the Supervisor's ingress proxy, so the LAN cannot reach it.
"""

import asyncio
import base64
import ipaddress
import logging
import re
import time
from pathlib import Path

import aiohttp
from aiohttp import web

from .airplay import AirPlayInvalid
from .cards import REGISTRY
from .intercom import ECHO, IntercomInvalid
from .intercom import addresses as intercom_addresses
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
                                    str(body.get("platform", "android")), body.get("type"))
        return web.json_response({"request_id": req.request_id, "code": req.code})

    async def pair_status(request: web.Request):
        return web.json_response(store.pairing_status(request.match_info["rid"]))

    async def media(request: web.Request):
        """A sound HA keeps (TTS, its media folder), handed to a panel on the add-on's network
        for HA's command to its speaker (speakers.py): only the paths handed out."""
        path = gateway.speakers.media_path(request.match_info["token"]) if gateway.speakers else None
        if path is None:
            raise web.HTTPNotFound()
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60)
        headers = dict(gateway.ha_auth)
        if "Range" in request.headers:
            headers["Range"] = request.headers["Range"]
        try:
            async with gateway.session.get(gateway.ha_http + path, headers=headers, timeout=timeout) as upstream:
                resp = web.StreamResponse(status=upstream.status, headers={
                    k: v for k, v in upstream.headers.items()
                    if k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control")})
                await resp.prepare(request)
                async for chunk in upstream.content.iter_any():
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("sound for a panel failed: %s", e)
            raise web.HTTPBadGateway()

    async def integration(request: web.Request):
        if gateway.speakers is None:
            raise web.HTTPServiceUnavailable()
        return await gateway.speakers.handle(request)

    async def hls(request: web.Request):
        """HA's HLS video of a camera (camera/stream), for panels on the add-on's network.

        The address carries HA's own token for the stream, handed out to a panel for its own
        cameras only; HA serves these without a login too. Panels connected through HA's
        address fetch them from HA itself."""
        url = f"{gateway.ha_http}/api/hls/{request.match_info['token']}/{request.match_info['tail']}"
        timeout = aiohttp.ClientTimeout(total=60, sock_connect=10)
        try:
            async with gateway.session.get(url, params=request.query, timeout=timeout) as upstream:
                resp = web.StreamResponse(status=upstream.status, headers={
                    k: v for k, v in upstream.headers.items() if k in ("Content-Type", "Cache-Control")})
                await resp.prepare(request)
                async for chunk in upstream.content.iter_any():
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("camera video failed: %s", e)
            raise web.HTTPBadGateway()

    app.router.add_get("/api/info", info)
    app.router.add_get(r"/api/hls/{token:[a-f0-9]+}/{tail:[A-Za-z0-9_./-]+}", hls)
    app.router.add_post("/api/pair", pair)
    app.router.add_get("/api/pair/{rid}", pair_status)
    app.router.add_get("/api/websocket", gateway.handle)
    app.router.add_get(r"/api/media/{token:[a-f0-9]{32}}", media)

    async def airplay(request: web.Request):
        if getattr(gateway, "airplay", None) is None:
            raise web.HTTPNotFound()
        return await gateway.airplay.stream(request)

    app.router.add_get(r"/api/airplay/{token:[a-f0-9]{32}}", airplay)
    app.router.add_get("/api/integration", integration)
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
                  catalog: CatalogCache | None = None, browser=None, intercom=None) -> web.Application:
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
                "type": p.type,
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
                    "type": p.type,
                    "paired_at": p.paired_at,
                    "last_seen": p.last_seen,
                    "online": gateway.online(p.id),
                    "layout_version": (store.get_layout(p.id) or {}).get("version"),
                }
                for p in sorted(store.panels.values(), key=lambda p: p.name)
            ],
            "pending": [
                {"request_id": r.request_id, "panel_id": r.panel_id, "name": r.name, "model": r.model, "platform": r.platform, "type": r.type,
                 "code": r.code, "age": int(now - r.created)}
                for r in store.pending.values() if not r.key and not r.rejected
            ],
        })

    async def integration_secret(_):
        # For setting the integration up by hand: the Supervisor hands it over otherwise.
        return web.json_response({"secret": gateway.speakers.secret if gateway.speakers else None})

    # ---- intercom (intercom.py) ----

    async def intercom_view():
        registered = await intercom.registered()
        registrations = await intercom.registrations()

        def server(s: dict) -> dict:
            status = registrations.get(s["username"])
            shown = {k: v for k, v in s.items() if k != "secret"}
            return {**shown, "secret_set": bool(s.get("secret")), "registration": status}

        def door(d: dict) -> dict:
            return {**d, "panels": d.get("panels"), "open": opening(d.get("open")), "registered": registered.get(d["username"])}

        def opening(o: dict | None) -> dict:
            # The door's own password stays in the add-on.
            o = o or {"kind": "none"}
            return {**{k: v for k, v in o.items() if k != "password"}, "password_set": bool(o.get("password"))}

        panels = [{"id": pid, "name": p.name,
                   "registered": registered.get(intercom.accounts.get(pid, {}).get("username", ""))}
                  for pid, p in store.panels.items()]
        return {
            "enabled": intercom.settings["enabled"],
            "sip_log": bool(intercom.settings.get("sip_log")),
            "installed": bool(intercom.binary),
            "running": intercom.running(),
            "sip_port": intercom.sip_port,
            "addresses": await intercom_addresses(),
            "echo": ECHO,
            "servers": [server(s) for s in intercom.servers],
            "numbers_via": intercom.settings.get("numbers_via"),
            "doors": [door(d) for d in intercom.doors],
            "contacts": [{**c, "panels": c.get("panels"), "open": opening(c.get("open"))} for c in intercom.extra],
            "panels": panels,
        }

    async def get_intercom(_):
        if intercom is None:
            raise web.HTTPNotFound()
        return web.json_response(await intercom_view())

    async def put_intercom(request: web.Request):
        if intercom is None:
            raise web.HTTPNotFound()
        try:
            intercom.update(await _body(request))
        except IntercomInvalid as e:
            return _invalid(e)
        await intercom.apply()
        # HA's doors follow (their devices, Open buttons).
        if gateway.speakers is not None:
            await gateway.speakers.changed()
        return web.json_response(await intercom_view())

    async def door_password(request: web.Request):
        if intercom is None:
            raise web.HTTPNotFound()
        try:
            intercom.new_password(request.match_info["did"])
        except KeyError:
            raise web.HTTPNotFound()
        await intercom.apply()
        return web.json_response(await intercom_view())

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
        if intercom is not None:
            await intercom.forget(pid)
        if getattr(gateway, "airplay", None) is not None:
            gateway.airplay.forget(pid)
            await gateway.airplay.apply()
        if gateway.speakers:
            await gateway.speakers.gone(pid)
        return web.json_response({"ok": True})

    async def rename(request: web.Request):
        pid = request.match_info["pid"]
        if pid not in store.panels:
            raise web.HTTPNotFound()
        store.rename(pid, str((await request.json()).get("name", "")).strip() or "Panel")
        await gateway.notify_config(pid)
        if intercom is not None and pid in intercom.accounts:
            await intercom.apply()  # its name on its calls
        if gateway.speakers:
            await gateway.speakers.changed()
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

    async def display_all(request: web.Request):
        """Display settings for every panel: the project's, replacing each panel's own."""
        body = await _body(request)
        try:
            replaced = store.save_display_for_all(body.get("display") or {})
        except ConfigInvalid as e:
            return _invalid(e)
        await gateway.notify_config()
        return web.json_response({"replaced": replaced})

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
            "installed_widgets": gateway.installed_widgets.get(pid, []),
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

    async def area_picture(request: web.Request):
        """HA's picture of a room, for the designer's room cards (the page has no HA token)."""
        try:
            picture = await gateway.area_picture(request.match_info["aid"])
        except Exception as e:  # HA restarting...
            log.warning("area picture failed: %s", e)
            picture = None
        if not picture:
            raise web.HTTPNotFound()
        return web.Response(body=base64.b64decode(picture["data"]), content_type=picture["type"].split(";")[0],
                            headers={"Cache-Control": "max-age=86400"})

    async def camera_picture(request: web.Request):
        """A camera's picture now, for the designer's camera cards (the page has no HA token)."""
        eid = request.match_info["eid"]
        if not eid.startswith("camera."):
            raise web.HTTPNotFound()
        try:
            picture = await gateway.camera_snapshot(eid, 640)
        except Exception as e:  # HA restarting, camera offline...
            log.warning("camera picture failed: %s", e)
            picture = None
        if not picture:
            raise web.HTTPNotFound()
        return web.Response(body=base64.b64decode(picture["data"]), content_type=picture["type"],
                            headers={"Cache-Control": "no-store"})

    async def panel_floor(request: web.Request):
        """A floor's name and icon on this panel, or for all panels (all); a new floor (wp_ id) too."""
        pid = _panel(request)
        body = await _body(request)
        try:
            icon = body.get("icon")
            store.name_floor(pid, str(body.get("floor_id") or ""), str(body.get("name") or ""), bool(body.get("all")),
                             None if icon is None else str(icon))
        except ConfigInvalid as e:
            return _invalid(e)
        await gateway.notify_config(None if body.get("all") else pid)
        return web.json_response({"ok": True})

    # Paths are relative in the page, so they work behind the ingress prefix.
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC)
    app.router.add_get("/api/editor/catalog", editor_catalog)
    app.router.add_get("/api/editor/state", editor_state)
    app.router.add_get("/api/editor/cards", editor_cards)
    app.router.add_get("/api/editor/area_picture/{aid}", area_picture)
    app.router.add_get("/api/editor/camera/{eid}", camera_picture)
    app.router.add_post("/api/panels/{pid}/floor", panel_floor)
    app.router.add_put("/api/editor/panels/{pid}/draft", put_draft)
    app.router.add_delete("/api/editor/panels/{pid}/draft", delete_draft)
    app.router.add_post("/api/editor/panels/{pid}/send", send_draft)
    app.router.add_get("/api/overview", overview)
    app.router.add_get("/api/integration/secret", integration_secret)
    # AirPlay: each panel's switch and password (airplay.py).
    def airplay_view() -> dict:
        ap = gateway.airplay
        return {"installed": bool(ap and ap.binary),
                "panels": [{"id": pid, "name": p.name, **(ap.view(pid) if ap else {})} for pid, p in store.panels.items()]}

    async def get_airplay(_):
        if gateway.airplay is None:
            raise web.HTTPNotFound()
        return web.json_response(airplay_view())

    async def put_airplay(request: web.Request):
        if gateway.airplay is None:
            raise web.HTTPNotFound()
        try:
            gateway.airplay.update(request.match_info["pid"], await _body(request))
        except AirPlayInvalid as e:
            return _invalid(e)
        await gateway.airplay.apply()
        return web.json_response(airplay_view())

    app.router.add_get("/api/airplay", get_airplay)
    app.router.add_put("/api/airplay/{pid}", put_airplay)
    app.router.add_get("/api/intercom", get_intercom)
    app.router.add_put("/api/intercom", put_intercom)
    app.router.add_post("/api/intercom/doors/{did}/password", door_password)
    app.router.add_post("/api/pending/{rid}/approve", approve)
    app.router.add_post("/api/pending/{rid}/reject", reject)
    app.router.add_post("/api/panels/{pid}/revoke", revoke)
    app.router.add_post("/api/panels/{pid}/rename", rename)
    app.router.add_post("/api/panels/{pid}/area", place)
    app.router.add_get("/api/panels/{pid}/layout", get_layout)
    app.router.add_get("/api/site", get_site)
    app.router.add_put("/api/site", put_site)
    app.router.add_post("/api/site/pin", site_pin)
    app.router.add_post("/api/site/display_all", display_all)
    app.router.add_get("/api/panels/{pid}/config", get_panel_config)
    app.router.add_put("/api/panels/{pid}/config", put_panel_config)
    app.router.add_post("/api/panels/{pid}/pin", panel_pin)
    return app
