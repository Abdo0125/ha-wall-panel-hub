"""Wall Panel Hub companion integration.

Opens /api/wall_panel_hub/* on Home Assistant's own web server and forwards it to the
Wall Panel Hub add-on. Panels away from the add-on's LAN (another site, mobile data)
then pair and connect through whatever address already reaches Home Assistant.

These views need no Home Assistant login: panels authenticate to the hub with their
own key, and pairing still needs the installer's approval in the add-on.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import ClientError, WSMsgType, web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HOST, CONF_PORT, DOMAIN, URL_BASE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    data["base"] = f"http://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}/api"
    # Views cannot be unregistered; register once and let them follow the current entry.
    if not data.get("views"):
        for view in (InfoView, PairView, PairStatusView, SocketView):
            hass.http.register_view(view(hass))
        data["views"] = True
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN]["base"] = None
    return True


class _HubView(HomeAssistantView):
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def target(self, path: str) -> str:
        base = self.hass.data.get(DOMAIN, {}).get("base")
        if not base:
            raise web.HTTPServiceUnavailable(text="Wall Panel Hub is not set up")
        return base + path

    async def forward(self, method: str, path: str, body: bytes | None = None) -> web.Response:
        session = async_get_clientsession(self.hass)
        try:
            async with session.request(
                method, self.target(path), data=body,
                headers={"Content-Type": "application/json"} if body else None,
                timeout=15,
            ) as resp:
                return web.Response(status=resp.status, body=await resp.read(), content_type=resp.content_type)
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Wall Panel Hub add-on unreachable: %s", err)
            raise web.HTTPBadGateway(text="Wall Panel Hub add-on unreachable") from err


class InfoView(_HubView):
    url = f"{URL_BASE}/info"
    name = "api:wall_panel_hub:info"

    async def get(self, request: web.Request) -> web.Response:
        return await self.forward("GET", "/info")


class PairView(_HubView):
    url = f"{URL_BASE}/pair"
    name = "api:wall_panel_hub:pair"

    async def post(self, request: web.Request) -> web.Response:
        body = await request.content.read(64 * 1024)
        return await self.forward("POST", "/pair", body)


class PairStatusView(_HubView):
    url = URL_BASE + "/pair/{request_id}"
    name = "api:wall_panel_hub:pair_status"

    async def get(self, request: web.Request, request_id: str) -> web.Response:
        return await self.forward("GET", f"/pair/{request_id}")


class SocketView(_HubView):
    url = f"{URL_BASE}/websocket"
    name = "api:wall_panel_hub:websocket"

    async def get(self, request: web.Request) -> web.WebSocketResponse:
        target = self.target("/websocket").replace("http://", "ws://", 1)
        panel = web.WebSocketResponse(heartbeat=30)
        await panel.prepare(request)
        session = async_get_clientsession(self.hass)
        try:
            async with session.ws_connect(target, heartbeat=30) as hub:

                async def to_hub() -> None:
                    async for msg in panel:
                        if msg.type != WSMsgType.TEXT:
                            break
                        await hub.send_str(msg.data)

                async def to_panel() -> None:
                    async for msg in hub:
                        if msg.type != WSMsgType.TEXT:
                            break
                        await panel.send_str(msg.data)

                tasks = [asyncio.create_task(to_hub()), asyncio.create_task(to_panel())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Wall Panel Hub add-on unreachable: %s", err)
        finally:
            await panel.close()
        return panel
