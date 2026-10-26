"""The link to the Wall Panel Hub add-on: its panels and their speakers, and commands to them;
its doors, with their doorbells' events, and opening them.

The add-on answers at /api/integration on its panel port, to its own secret only. It sends
every paired panel with its speaker's state and its call ("panels"), and the intercom's doors
and external servers ("doors"), again on each change; each door's events ("door_event": ring, answered, missed,
opened); and the result of each command.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from aiohttp import ClientError, WSMsgType

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

BACKOFF_S = (1, 2, 5, 10, 30)
COMMAND_TIMEOUT_S = 10


class HubLink:
    def __init__(self, hass: HomeAssistant, host: str, port: int, secret: str) -> None:
        self.hass = hass
        self.url = f"http://{host}:{port}/api/integration"
        self.secret = secret
        # Panels by id, as the add-on last sent them; trusted once [synced].
        self.panels: dict[str, dict[str, Any]] = {}
        # The intercom's doors (and doorbells) by target ("door:<id>", "contact:<id>"), and its
        # external servers ("server:<id>", kind "server").
        self.doors: dict[str, dict[str, Any]] = {}
        self.synced = False
        self._listeners: list[Callable[[], None]] = []
        self._door_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._ws = None
        self._task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 1

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed and self.synced

    @callback
    def listen(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Called on each change of the panels or of the link; returns the way to stop."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def listen_doors(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Called with each door's event; returns the way to stop."""
        self._door_listeners.append(listener)
        return lambda: self._door_listeners.remove(listener)

    @callback
    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()

    def start(self) -> None:
        self._task = self.hass.async_create_background_task(self._run(), "wall_panel_hub link")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def _run(self) -> None:
        session = async_get_clientsession(self.hass)
        attempt = 0
        while True:
            try:
                async with session.ws_connect(self.url, heartbeat=30) as ws:
                    await ws.send_json({"type": "auth", "secret": self.secret})
                    first = await ws.receive_json(timeout=10)
                    if first.get("type") != "auth_ok":
                        _LOGGER.error("Wall Panel Hub refused this integration's secret; set it up again")
                        await ws.close()
                        await asyncio.sleep(300)
                        continue
                    self._ws = ws
                    attempt = 0
                    async for msg in ws:
                        if msg.type != WSMsgType.TEXT:
                            break
                        self._message(msg.json())
            except (ClientError, asyncio.TimeoutError, ValueError) as err:
                _LOGGER.debug("Wall Panel Hub link: %s", err)
            finally:
                self._ws = None
                self.synced = False
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(HomeAssistantError("Wall Panel Hub disconnected"))
                self._pending.clear()
                self._changed()
            await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
            attempt += 1

    @callback
    def _message(self, msg: dict[str, Any]) -> None:
        if msg.get("type") == "panels":
            self.panels = {p["id"]: p for p in msg.get("panels") or [] if isinstance(p, dict) and p.get("id")}
            self.doors = {d["target"]: d for d in msg.get("doors") or [] if isinstance(d, dict) and d.get("target")}
            self.synced = True
            self._changed()
        elif msg.get("type") == "door_event":
            for listener in list(self._door_listeners):
                listener(msg)
        elif msg.get("type") == "result":
            future = self._pending.pop(msg.get("id"), None)
            if future is not None and not future.done():
                if msg.get("success"):
                    future.set_result(None)
                else:
                    future.set_exception(HomeAssistantError(f"Wall Panel Hub: {msg.get('error') or 'refused'}"))

    async def command(self, panel_id: str, command: dict[str, Any]) -> None:
        """Sends a command to a panel's speaker; raises if the add-on or the panel refuses it."""
        await self._ask({"type": "command", "panel_id": panel_id, "command": command})

    async def open(self, target: str) -> None:
        """Opens a door; raises if it does not."""
        await self._ask({"type": "open", "target": target})

    async def _ask(self, message: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise HomeAssistantError("Wall Panel Hub is not connected")
        mid = self._next
        self._next += 1
        future = self.hass.loop.create_future()
        self._pending[mid] = future
        await ws.send_json({**message, "id": mid})
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT_S):
                await future
        except TimeoutError as err:
            raise HomeAssistantError("Wall Panel Hub did not answer") from err
        finally:
            self._pending.pop(mid, None)
