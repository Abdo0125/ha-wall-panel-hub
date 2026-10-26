"""Wall Panel Hub companion integration.

Opens /api/wall_panel_hub/* on Home Assistant's own web server and forwards it to the
Wall Panel Hub add-on. Panels away from the add-on's LAN (another site, mobile data)
then pair and connect through whatever address already reaches Home Assistant.

These views need no Home Assistant login: panels authenticate to the hub with their
own key, and pairing still needs the installer's approval in the add-on.

With the add-on's secret (handed over in its Supervisor discovery) it also links to the
add-on (hub.HubLink): each panel is a device here, with its speaker (media_player), its
sound output (select) and its call (sensor); each door of the intercom too, with its
doorbell (event) and, when it opens without a call, its Open button.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import ClientError, WSMsgType, web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HOST, CONF_PORT, CONF_SECRET, DOMAIN, URL_BASE
from .hub import HubLink

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SELECT, Platform.SENSOR, Platform.EVENT, Platform.BUTTON,
             Platform.BINARY_SENSOR]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    data["base"] = f"http://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}/api"
    # Views cannot be unregistered; register once and let them follow the current entry.
    if not data.get("views"):
        for view in (InfoView, PairView, PairStatusView, SocketView):
            hass.http.register_view(view(hass))
        data["views"] = True
    if entry.data.get(CONF_SECRET):
        link = HubLink(hass, entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_SECRET])
        data["link"] = link
        devices = dr.async_get(hass)

        @callback
        def follow_panels() -> None:
            # A panel unpaired in the add-on (a door removed) goes from here too, with its
            # entities; one renamed there is renamed here.
            if not link.synced:
                return
            for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
                ids = {i[1] for i in device.identifiers if i[0] == DOMAIN}
                found = next((link.panels.get(i) or link.doors.get(i) for i in ids if i in link.panels or i in link.doors), None)
                if ids and found is None:
                    devices.async_remove_device(device.id)
                elif found and found.get("name") and device.name != found["name"]:
                    devices.async_update_device(device.id, name=found["name"])

        entry.async_on_unload(link.listen(follow_panels))
        link.start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data[DOMAIN]
    link = data.pop("link", None)
    if link is not None:
        if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
            data["link"] = link
            return False
        await link.stop()
    data["base"] = None
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
