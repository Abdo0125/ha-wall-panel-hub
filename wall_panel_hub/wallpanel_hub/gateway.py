"""WebSocket gateway between panels and Home Assistant.

A panel speaks the normal HA WebSocket API to the hub, authenticated with its own key.
The hub opens its own connection to HA and forwards only what the panel may do:

- commands only on entities in the panel's layout, with the entity's own domain;
- state subscriptions limited to those entities;
- read-only registry and state lists (for the layout editor on the panel);
- wallpanel/* commands (layout, configuration, card registry), answered by the hub itself;
- the pictures of its cameras (wallpanel/camera/*), fetched from HA's HTTP API by the hub;
- its speaker (wallpanel/speaker/*): its state for HA's integration, HA's commands to it.
- the intercom (wallpanel/intercom/*): its SIP to Asterisk (intercom.py), carried here.

Layouts are stored in format v2 (cards). A panel asks for it with "schema": 2 in
wallpanel/layout/subscribe or /get; an older app, which does not, gets format v1.
"""

import asyncio
import base64
import json
import logging
import re

import aiohttp
from aiohttp import WSMsgType, web

from .airplay import AirPlayInvalid
from .intercom import DOMAIN, networks
from . import relay as sound
from . import door_open
from .access import EditRefused, role_caps
from .cards import REGISTRY
from . import panel_settings, project_settings
from .config import ConfigInvalid
from .pins import PinInvalid
from .migrate import SCHEMA, downgrade
from .store import LayoutInvalid, Store, layout_entities, layout_summary

PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
PROVIDER = re.compile(PACKAGE.pattern + r"/[A-Za-z0-9_.$]+")
log = logging.getLogger(__name__)

READ_ONLY = {
    "get_states",
    "get_config",
    "ping",
    "unsubscribe_events",
}

# Services of another domain a panel may call on its own entities: asking HA to read
# the device again changes nothing.
READS = {("homeassistant", "update_entity")}

# A camera's video from HA, for the panel's own cameras: its HLS address (camera/stream)
# and a WebRTC session through HA's go2rtc (offer, candidates, ICE servers).
CAMERA_VIDEO = {"camera/stream", "camera/webrtc/offer", "camera/webrtc/candidate", "camera/webrtc/get_client_config"}

# The services a panel may call, by its device's type: what its cards do, nothing that writes
# files (camera.snapshot), reaches other devices (media_player.join) or changes HA.
_ONOFF = {"turn_on", "turn_off", "toggle"}
SERVICES = {
    "light": _ONOFF,
    "switch": _ONOFF,
    "input_boolean": _ONOFF,
    "automation": _ONOFF,
    "fan": _ONOFF | {"set_percentage", "set_preset_mode", "oscillate", "set_direction"},
    "cover": {"open_cover", "close_cover", "stop_cover", "toggle", "set_cover_position", "open_cover_tilt",
              "close_cover_tilt", "stop_cover_tilt", "toggle_cover_tilt", "set_cover_tilt_position"},
    "climate": _ONOFF | {"set_temperature", "set_hvac_mode", "set_fan_mode", "set_swing_mode", "set_preset_mode",
                         "set_humidity"},
    "lock": {"lock", "unlock", "open"},
    "scene": {"turn_on"},
    "script": _ONOFF,
    "button": {"press"},
    "input_button": {"press"},
}

# Targets that would reach entities beyond the ones listed.
WIDE_TARGETS = ("area_id", "device_id", "floor_id", "label_id")


def _ids(value) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def only_allowed(event: dict, allowed: set[str]) -> dict | None:
    """A subscribe_entities event cut to [allowed]: a subscription made before the panel's
    layout lost a device sends nothing more of it. None when nothing is left."""
    out = {}
    for key in ("a", "c"):
        if isinstance(event.get(key), dict):
            kept = {e: v for e, v in event[key].items() if e in allowed}
            if kept:
                out[key] = kept
    if isinstance(event.get("r"), list):
        kept = [e for e in event["r"] if e in allowed]
        if kept:
            out["r"] = kept
    if not out and any(event.get(k) for k in ("a", "c", "r")):
        return None
    return out


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

    if kind in CAMERA_VIDEO:
        eid = msg.get("entity_id")
        if not (isinstance(eid, str) and eid.startswith("camera.") and eid in allowed):
            return None, "not a camera of this panel"
        # HLS only: the panel plays nothing else, and other formats would open other paths.
        return ({**msg, "format": "hls"} if kind == "camera/stream" else msg), None

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
            if (msg.get("domain"), msg.get("service")) in READS:
                continue
            if e.split(".", 1)[0] != msg.get("domain"):
                return None, f"{msg.get('domain')}.{msg.get('service')} cannot target {e}"
            if msg.get("service") not in SERVICES.get(msg.get("domain"), ()):
                return None, f"{msg.get('domain')}.{msg.get('service')} is not allowed for panels"
        return msg, None

    return None, f"{kind} is not allowed for panels"


class PanelConnection:
    def __init__(self, gateway: "Gateway", panel_id: str, ws: web.WebSocketResponse):
        self.gateway = gateway
        self.panel_id = panel_id
        self.ws = ws
        self.layout_subs: set[int] = set()
        self.config_subs: set[int] = set()
        # Layout format the panel's app reads: 1 until it asks for 2.
        self.schema = 1
        # Live cameras the panel watches, by the id of its wallpanel/camera/stream.
        self.streams: dict[int, asyncio.Task] = {}
        # wallpanel/speaker/subscribe: HA's commands to the panel's speaker go there.
        self.speaker_subs: set[int] = set()
        # wallpanel/intercom/subscribe: the panel's SIP WebSocket to Asterisk, by subscription.
        self.tunnels: dict[int, aiohttp.ClientWebSocketResponse] = {}
        # Subscriptions whose calls' sound comes through the hub (relay.py), and those calls by Call-ID.
        self.relayed: set[int] = set()
        self.relays: dict[str, sound.Relay] = {}
        self.frames: asyncio.Queue | None = None
        self.frames_task: asyncio.Task | None = None

    @property
    def allowed(self) -> set[str]:
        return layout_entities(self.gateway.store.get_layout(self.panel_id)) | self.door_cameras()

    def door_cameras(self) -> set[str]:
        """The cameras of the doors that ring this panel and of the project's devices (intercom)."""
        ic = getattr(self.gateway, "intercom", None)
        if ic is None or not ic.settings.get("enabled"):
            return set()
        found = {x.get(k) for x in ic.doors + ic.extra if x.get("panels") is None or self.panel_id in x["panels"]
                 for k in ("camera", "camera_sub")}
        return {c for c in found if c}

    async def project_settings(self, mid, kind: str, msg: dict) -> None:
        store = self.gateway.store
        try:
            role = project_settings.allowed(store, self.panel_id, msg.get("pin"))
            if kind == "wallpanel/project/get":
                entities = []
                if self.gateway.catalog is not None:
                    try:
                        entities = (await self.gateway.catalog.get())["entities"]
                    except Exception:  # HA restarting: the names and PINs still show
                        pass
                result = project_settings.get(store, entities)
            elif kind == "wallpanel/project/update":
                project_settings.update(store, msg.get("changes") or {})
                result = None
            else:
                project_settings.set_pin(store, str(msg.get("role") or ""), msg.get("new_pin"), role)
                result = None
        except project_settings.Refused as e:
            log.warning("panel %s: project settings refused: %s", self.panel_id, e)
            await self.send(_error(mid, "forbidden", str(e)))
            return
        except (ConfigInvalid, PinInvalid, ValueError, TypeError) as e:
            await self.send(_error(mid, "invalid_config", str(e)))
            return
        await self.send({"id": mid, "type": "result", "success": True, "result": result})
        if kind != "wallpanel/project/get":
            await self.gateway.notify_config()

    async def panel_settings(self, mid, kind: str, msg: dict) -> None:
        store = self.gateway.store
        try:
            role = panel_settings.allowed(store, self.panel_id, msg.get("pin"))
            if kind == "wallpanel/panel/get":
                result = panel_settings.get(store, self.panel_id, await _areas(self.gateway),
                                            self.gateway.installed_apps.get(self.panel_id, []))
                if getattr(self.gateway, "airplay", None) is not None:
                    result["airplay"] = self.gateway.airplay.view(self.panel_id)
            elif kind == "wallpanel/panel/update":
                areas = {a["id"] for a in await _areas(self.gateway)}
                changes = msg.get("changes") or {}
                airplay = changes.get("airplay")
                if isinstance(airplay, dict) and getattr(self.gateway, "airplay", None) is not None:
                    try:
                        self.gateway.airplay.update(self.panel_id, airplay)
                    except AirPlayInvalid as e:
                        raise panel_settings.Refused(str(e)) from e
                panel_settings.update(store, self.panel_id, role, {k: v for k, v in changes.items() if k != "airplay"}, areas)
                result = None
            elif kind == "wallpanel/panel/pin":
                panel_settings.set_pin(store, self.panel_id, str(msg.get("role") or ""), msg.get("new_pin"), role)
                result = None
            else:
                # Unpaired: told first, then cut off; it pairs again from its connection page.
                await self.send({"id": mid, "type": "result", "success": True, "result": None})
                store.revoke(self.panel_id)
                # As from the add-on (web.revoke): its intercom account goes, and it rings for nothing.
                if getattr(self.gateway, "intercom", None) is not None:
                    await self.gateway.intercom.forget(self.panel_id)
                if getattr(self.gateway, "airplay", None) is not None:
                    self.gateway.airplay.forget(self.panel_id)
                    await self.gateway.airplay.apply()
                await self.gateway.disconnect(self.panel_id)
                return
        except panel_settings.Refused as e:
            log.warning("panel %s: panel settings refused: %s", self.panel_id, e)
            await self.send(_error(mid, "forbidden", str(e)))
            return
        except (ConfigInvalid, PinInvalid, ValueError, TypeError) as e:
            await self.send(_error(mid, "invalid_config", str(e)))
            return
        await self.send({"id": mid, "type": "result", "success": True, "result": result})
        if kind != "wallpanel/panel/get":
            await self.gateway.notify_config(self.panel_id)

    def camera_allowed(self, entity_id: str) -> bool:
        return entity_id.startswith("camera.") and entity_id in self.allowed

    async def camera(self, mid, kind: str, msg: dict) -> None:
        """A camera of the panel's layout: its picture now, or its live picture as events."""
        if kind == "wallpanel/camera/stop":
            task = self.streams.pop(msg.get("subscription"), None)
            if task:
                task.cancel()
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
            return
        eid = str(msg.get("entity_id") or "")
        if not self.camera_allowed(eid):
            await self.send(_error(mid, "unauthorized", "not a camera of this panel"))
            return
        if kind == "wallpanel/camera/snapshot":
            width = msg.get("width")
            width = width if isinstance(width, int) and 16 <= width <= 4096 else None
            try:
                picture = await self.gateway.camera_snapshot(eid, width)
            except Exception as e:  # HA restarting, camera offline...
                log.warning("camera %s picture failed: %s", eid, e)
                picture = None
            if picture is None:
                await self.send(_error(mid, "unavailable", "no picture from the camera"))
                return
            await self.send({"id": mid, "type": "result", "success": True, "result": picture})
            return
        if len(self.streams) >= MAX_STREAMS:
            await self.send(_error(mid, "busy", f"at most {MAX_STREAMS} live cameras at once"))
            return
        try:
            fps = min(max(float(msg.get("fps") or 5), 0.2), 10.0)
        except (TypeError, ValueError):
            fps = 5.0
        width = msg.get("width")
        width = width if isinstance(width, int) and 16 <= width <= 4096 else None
        await self.send({"id": mid, "type": "result", "success": True, "result": None})
        self.streams[mid] = asyncio.create_task(self.stream_camera(mid, eid, fps, width))

    async def stream_camera(self, sub: int, eid: str, fps: float, width: int | None) -> None:
        """Sends the camera's frames until stopped; then an event with "end" (the panel may ask again)."""
        reason = "ended"
        try:
            async for ctype, data in self.gateway.camera_frames(eid, fps, width):
                # Taken off the panel meanwhile: no more pictures.
                if not self.camera_allowed(eid):
                    reason = "not a camera of this panel"
                    break
                await self.send({"id": sub, "type": "event",
                                 "event": {"type": ctype, "data": base64.b64encode(data).decode()}})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # HA restarting, camera offline...
            log.warning("camera %s stream failed: %s", eid, e)
            reason = str(e) or type(e).__name__
        finally:
            if self.streams.get(sub) is asyncio.current_task():
                del self.streams[sub]
        await self.send({"id": sub, "type": "event", "event": {"end": reason}})

    def stop_streams(self) -> None:
        for task in self.streams.values():
            task.cancel()
        self.streams.clear()
        for ws in self.tunnels.values():
            asyncio.ensure_future(ws.close())
        self.tunnels.clear()
        for r in self.relays.values():
            r.close()
        self.relays.clear()
        if self.frames_task is not None:
            self.frames_task.cancel()

    async def intercom(self, mid, kind: str, msg: dict) -> None:
        """SIP between the panel and Asterisk, as text both ways: the panel's messages in
        wallpanel/intercom/send, Asterisk's as events of its wallpanel/intercom/subscribe."""
        ic = self.gateway.intercom
        if kind == "wallpanel/intercom/subscribe":
            if ic is None or not ic.running():
                await self.send(_error(mid, "off", "the intercom is off"))
                return
            account = await ic.panel_account(self.panel_id)
            try:
                ws = await ic.connect(self.gateway.session)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                await self.send(_error(mid, "unavailable", f"Asterisk: {e}"))
                return
            self.tunnels[mid] = ws
            # The networks this machine is on: the panel sees if it has a path here for WebRTC.
            await self.send({"id": mid, "type": "result", "success": True,
                             "result": {**account, "domain": DOMAIN, "networks": await networks(), "sip_port": ic.sip_port}})
            asyncio.create_task(self._carry(mid, ws))
        elif kind == "wallpanel/intercom/contacts":
            if ic is None:
                await self.send(_error(mid, "off", "the intercom is off"))
                return
            names = {pid: p.name for pid, p in self.gateway.store.panels.items()}
            await self.send({"id": mid, "type": "result", "success": True,
                             "result": ic.directory(names, self.panel_id)})
        elif kind == "wallpanel/intercom/open":
            # {target}: a door's code comes back for the panel to send in its call; else opened here.
            result = await self.gateway.open_door(str(msg.get("target") or ""), self.panel_id)
            if isinstance(result, dict):
                await self.send({"id": mid, "type": "result", "success": True, "result": result})
            else:
                # The door said no: its user or password wrong (the add-on's settings), or as when
                # it opened a moment ago (its time between openings).
                code = ("denied" if re.search(r"answered 40[13]\b", result)
                        else "refused" if "answered 4" in result else "failed")
                await self.send(_error(mid, code, result))
        elif kind == "wallpanel/intercom/state":
            # {state, with?}: the panel's call, for HA; no answer.
            if self.gateway.speakers is not None:
                await self.gateway.speakers.call_state(self.panel_id, msg)
        elif kind == "wallpanel/intercom/mode":
            # {subscription, relay}: the panel's calls' sound through the hub, or by WebRTC.
            sub = msg.get("subscription")
            if sub not in self.tunnels or ic is None:
                await self.send(_error(mid, "closed", "no SIP connection"))
                return
            on = bool(msg.get("relay"))
            (self.relayed.add if on else self.relayed.discard)(sub)
            await ic.relay((await ic.panel_account(self.panel_id))["username"], on)
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
        elif kind == "wallpanel/intercom/send":
            sub = msg.get("subscription")
            ws = self.tunnels.get(sub)
            text = msg.get("sip")
            if ws is None or ws.closed or not isinstance(text, str) or len(text) > 65536:
                await self.send(_error(mid, "closed", "no SIP connection"))
                return
            if sub in self.relayed:
                text = await self._relayed(sub, text, from_panel=True)
            await ws.send_str(text)
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
        else:  # unsubscribe
            self.relayed.discard(msg.get("subscription"))
            ws = self.tunnels.pop(msg.get("subscription"), None)
            if ws is not None:
                await ws.close()
            await self.send({"id": mid, "type": "result", "success": True, "result": None})

    async def _carry(self, sub: int, ws) -> None:
        try:
            async for m in ws:
                if m.type != WSMsgType.TEXT:
                    break
                text = m.data
                if sub in self.relayed:
                    text = await self._relayed(sub, text, from_panel=False)
                await self.send({"id": sub, "type": "event", "event": {"sip": text}})
        except Exception as e:  # the panel gone...
            log.info("intercom for panel %s: %s", self.panel_id, e)
        finally:
            if self.tunnels.get(sub) is ws:
                del self.tunnels[sub]
                await ws.close()
                if not self.ws.closed:
                    await self.send({"id": sub, "type": "event", "event": {"end": "closed"}})

    async def _relayed(self, sub: int, text: str, from_panel: bool) -> str:
        """A SIP message of a relayed call: its sound's port opened, the panel's SDP given the
        hub's address, Asterisk's where to send; the port closed when the call ends."""
        cid = sound.call_id(text)
        if cid is None:
            return text
        if sound.has_sdp(text):
            relay = self.relays.get(cid)
            if relay is None:
                for old in [c for c, r in self.relays.items() if r.idle()]:
                    self.relays.pop(old).close()
                relay = self.relays[cid] = await sound.Relay.open(lambda payload, pt: self._frame(sub, cid, payload, pt))
            if from_panel:
                text = sound.with_address(text, relay.port)
            elif (port := sound.media_port(text)) is not None:
                relay.asterisk = (sound.HOST, port)
        if sound.ends(text) and (relay := self.relays.pop(cid, None)) is not None:
            relay.close()
        return text

    def frame(self, msg: dict) -> None:
        """wallpanel/intercom/rtp {call, pt, rtp}: a frame of the panel's sound; no answer."""
        relay = self.relays.get(msg.get("call"))
        data = msg.get("rtp")
        if relay is None or not isinstance(data, str) or len(data) > 2000:
            return
        try:
            relay.send(base64.b64decode(data), int(msg.get("pt") or 0))
        except (ValueError, TypeError):
            pass

    def _frame(self, sub: int, cid: str, payload: bytes, pt: int) -> None:
        # In order, and never more than a second behind: frames late are dropped.
        if self.frames is None:
            self.frames = asyncio.Queue(maxsize=50)
            self.frames_task = asyncio.create_task(self._send_frames())
        if not self.frames.full():
            self.frames.put_nowait({"id": sub, "type": "event",
                                    "event": {"rtp": base64.b64encode(payload).decode(), "pt": pt, "call": cid}})

    async def _send_frames(self) -> None:
        while not self.ws.closed:
            await self.send(await self.frames.get())

    async def send(self, data: dict) -> None:
        if not self.ws.closed:
            await self.ws.send_str(json.dumps(data, ensure_ascii=False))

    def layout_for_panel(self) -> dict | None:
        store = self.gateway.store
        layout = store.get_layout(self.panel_id)
        if layout is None or self.schema >= SCHEMA:
            return layout
        # An older app reads its display settings from the layout.
        eff = store.effective_config(self.panel_id)
        panel = {"name": eff["name"] or "Panel", **{k: eff[k] for k in ("lang", "theme", "screensaver")}}
        return {**downgrade(layout), "panel": panel}

    async def push_config(self) -> None:
        config = self.gateway.store.effective_config(self.panel_id)
        for sub in list(self.config_subs):
            await self.send({"id": sub, "type": "event", "event": {"config": config}})

    async def push_layout(self) -> None:
        layout = self.layout_for_panel()
        for sub in list(self.layout_subs):
            await self.send({"id": sub, "type": "event", "event": {"layout": layout}})

    async def handle_local(self, msg: dict) -> None:
        """wallpanel/* commands, answered by the hub. One that fails unexpectedly is answered
        with internal_error: the panel's connection (its cameras, its call) goes on."""
        try:
            await self._handle_local(msg)
        except (asyncio.CancelledError, ConnectionResetError):
            raise
        except Exception:
            log.exception("panel %s: %s failed", self.panel_id, msg.get("type"))
            await self.send(_error(msg.get("id"), "internal_error", "the hub could not do this"))

    async def _handle_local(self, msg: dict) -> None:
        mid, kind = msg.get("id"), msg.get("type")
        store = self.gateway.store
        if kind in ("wallpanel/layout/get", "wallpanel/layout/subscribe") and msg.get("schema") == SCHEMA:
            self.schema = SCHEMA
        if kind == "wallpanel/layout/get":
            await self.send({"id": mid, "type": "result", "success": True,
                             "result": {"layout": self.layout_for_panel()}})
        elif kind == "wallpanel/layout/subscribe":
            self.layout_subs.add(mid)
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
            await self.push_layout()
        elif kind == "wallpanel/layout/save":
            # The PIN entered on the panel says who edits: installer, user or guest.
            try:
                saved = store.save_from_panel(self.panel_id, msg.get("layout") or {}, msg.get("pin"),
                                              await _known(self.gateway))
            except LayoutInvalid as e:
                log.warning("panel %s layout invalid: %s", self.panel_id, e)
                await self.send(_error(mid, "invalid_layout", str(e)))
                return
            except EditRefused as e:
                log.warning("panel %s edit refused: %s", self.panel_id, e)
                await self.send(_error(mid, "forbidden", str(e)))
                return
            log.info("panel %s layout saved from the panel (%s): %s", self.panel_id,
                     store.role_for(self.panel_id, msg.get("pin")), layout_summary(saved))
            await self.send({"id": mid, "type": "result", "success": True, "result": {"version": saved["version"]}})
            await self.gateway.notify_layout(self.panel_id)
        elif kind in ("wallpanel/panel/get", "wallpanel/panel/update", "wallpanel/panel/pin", "wallpanel/panel/unpair"):
            # This panel's own settings, from the panel itself (panel permission; docs/parity.md 4a).
            await self.panel_settings(mid, kind, msg)
        elif kind in ("wallpanel/project/get", "wallpanel/project/update", "wallpanel/project/pin"):
            # The project's settings, from a panel (project permission; docs/parity.md 4b).
            await self.project_settings(mid, kind, msg)
        elif kind == "wallpanel/config/floor":
            # A floor's name: on this panel with the rooms permission, for all with project.
            everywhere = msg.get("scope") == "all"
            need = "project" if everywhere else "rooms"
            if need not in role_caps(store.effective_config(self.panel_id), store.role_for(self.panel_id, msg.get("pin"))):
                await self.send(_error(mid, "forbidden", f"naming floors needs the {need} permission"))
                return
            try:
                icon = msg.get("icon")
                store.name_floor(self.panel_id, str(msg.get("floor_id") or ""), str(msg.get("name") or ""), everywhere,
                                 None if icon is None else str(icon))
            except ConfigInvalid as e:
                await self.send(_error(mid, "invalid_config", str(e)))
                return
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
            await self.gateway.notify_config(None if everywhere else self.panel_id)
        elif kind == "wallpanel/config/display" and msg.get("scope") == "all":
            # For every panel: the project permission, checked with the PIN entered.
            if "project" not in role_caps(store.effective_config(self.panel_id), store.role_for(self.panel_id, msg.get("pin"))):
                await self.send(_error(mid, "forbidden", "changing the project is not allowed on this panel"))
                return
            try:
                replaced = store.save_display_for_all(msg.get("display") or {})
            except ConfigInvalid as e:
                await self.send(_error(mid, "invalid_config", str(e)))
                return
            await self.send({"id": mid, "type": "result", "success": True, "result": {"replaced": replaced}})
            await self.gateway.notify_config()
        elif kind == "wallpanel/config/display":
            try:
                store.save_display_from_panel(self.panel_id, msg.get("display") or {})
            except ConfigInvalid as e:
                await self.send(_error(mid, "invalid_config", str(e)))
                return
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
            await self.gateway.notify_config(self.panel_id)
        elif kind == "wallpanel/apps/report":
            # The apps installed on the panel, so the add-on can offer them by name.
            apps = [
                {"package": a["package"], "label": str(a.get("label") or a["package"])[:80]}
                for a in (msg.get("apps") or [])[:300]
                if isinstance(a, dict) and isinstance(a.get("package"), str) and PACKAGE.fullmatch(a["package"])
            ]
            self.gateway.installed_apps[self.panel_id] = sorted(apps, key=lambda a: a["label"].lower())
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
        elif kind == "wallpanel/widgets/report":
            # The widgets of the panel's installed apps, so the add-on can allow them by name.
            widgets = [
                {"provider": w["provider"], "label": str(w.get("label") or w["provider"])[:80]}
                for w in (msg.get("widgets") or [])[:300]
                if isinstance(w, dict) and isinstance(w.get("provider"), str) and PROVIDER.fullmatch(w["provider"])
            ]
            self.gateway.installed_widgets[self.panel_id] = sorted(widgets, key=lambda w: w["label"].lower())
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
        elif kind == "wallpanel/config/subscribe":
            self.config_subs.add(mid)
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
            await self.push_config()
        elif kind == "wallpanel/catalog/get":
            # HA's rooms and devices, to add some on the panel: the panel's own get_states
            # only has its entities. With the rooms or devices permission.
            if not {"rooms", "devices"} & role_caps(store.effective_config(self.panel_id), store.role_for(self.panel_id, msg.get("pin"))):
                await self.send(_error(mid, "forbidden", "reading HA's rooms needs the rooms or devices permission"))
                return
            if self.gateway.catalog is None:
                await self.send(_error(mid, "unavailable", "no Home Assistant connection"))
                return
            try:
                cat = await self.gateway.catalog.get()
            except Exception as e:  # HA restarting...
                await self.send(_error(mid, "unavailable", str(e)))
                return
            await self.send({"id": mid, "type": "result", "success": True, "result": public(cat)})
        elif kind == "wallpanel/area/picture":
            # HA's picture of one of this panel's rooms: the panel has no HA token.
            area = str(msg.get("area_id") or "")
            layout = store.get_layout(self.panel_id) or {"pages": []}
            if area not in {p["id"] for p in layout["pages"]}:
                await self.send(_error(mid, "unauthorized", "not a room of this panel"))
                return
            try:
                picture = await self.gateway.area_picture(area)
            except Exception as e:  # HA restarting...
                await self.send(_error(mid, "unavailable", str(e)))
                return
            await self.send({"id": mid, "type": "result", "success": True, "result": picture})
        elif kind in ("wallpanel/camera/snapshot", "wallpanel/camera/stream", "wallpanel/camera/stop"):
            await self.camera(mid, kind, msg)
        elif kind in ("wallpanel/speaker/subscribe", "wallpanel/speaker/state"):
            # The panel as a speaker in HA (speakers.py): its state, and HA's commands as events.
            speakers = self.gateway.speakers
            if speakers is None or not await speakers.report(self.panel_id, msg.get("state")):
                await self.send(_error(mid, "invalid_state", "a speaker state is needed"))
                return
            if kind == "wallpanel/speaker/subscribe":
                self.speaker_subs.add(mid)
            await self.send({"id": mid, "type": "result", "success": True, "result": None})
        elif kind == "wallpanel/intercom/rtp":
            self.frame(msg)
        elif kind in ("wallpanel/intercom/subscribe", "wallpanel/intercom/send", "wallpanel/intercom/unsubscribe",
                      "wallpanel/intercom/contacts", "wallpanel/intercom/mode", "wallpanel/intercom/open",
                      "wallpanel/intercom/state"):
            await self.intercom(mid, kind, msg)
        elif kind == "wallpanel/registry/get":
            await self.send({"id": mid, "type": "result", "success": True, "result": REGISTRY})
        else:
            await self.send(_error(mid, "unknown_command", f"{kind} is not known"))


async def _known(gateway) -> set[str] | None:
    """Every entity HA has now, to check a panel's layout by; None when HA cannot say (restarting)."""
    if gateway.catalog is None:
        return None
    try:
        return set((await gateway.catalog.get())["known"])
    except Exception:
        return None


def public(catalog: dict) -> dict:
    """The catalog as panels and the add-on's page get it (without every entity's id)."""
    return {k: v for k, v in catalog.items() if k != "known"}


async def _areas(gateway) -> list[dict]:
    """HA's rooms, for a panel's place; none without HA."""
    if gateway.catalog is None:
        return []
    try:
        return (await gateway.catalog.get())["areas"]
    except Exception:  # HA restarting...
        return []


class NoStream(Exception):
    """HA has no live stream for the camera."""


class MjpegFrames:
    """The JPEG pictures in an MJPEG stream, whatever its part boundary: HA announces
    "boundary=--frameboundary" but writes "--frameboundary", and cameras passed through
    have their own. A picture runs from its start marker for the part's Content-Length,
    or else to its end marker."""

    START, END = b"\xff\xd8", b"\xff\xd9"
    LIMIT = 8 * 1024 * 1024
    LENGTH = re.compile(rb"content-length:\s*(\d+)", re.I)

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buf += chunk
        frames = []
        while True:
            start = self.buf.find(self.START)
            if start < 0:  # the part's headers so far: kept for its length
                break
            length = self.LENGTH.findall(self.buf[:start])
            if length:
                end = start + int(length[-1])
                if len(self.buf) < end:
                    break
            else:
                found = self.buf.find(self.END, start + 2)
                if found < 0:
                    break
                end = found + 2
            frames.append(self.buf[start:end])
            self.buf = self.buf[end:]
        if len(self.buf) > self.LIMIT:  # not a picture stream: start again
            self.buf = b""
        return frames


# Live cameras one panel may watch at once (each is a stream from HA).
MAX_STREAMS = 4


def _error(mid, code: str, message: str) -> dict:
    return {"id": mid, "type": "result", "success": False, "error": {"code": code, "message": message}}


class Gateway:
    def __init__(self, settings, store: Store, session: aiohttp.ClientSession):
        self.settings = settings
        self.store = store
        self.session = session
        self.connections: dict[str, set[PanelConnection]] = {}
        # Reported by each panel when it connects; kept in memory only.
        self.installed_apps: dict[str, list[dict]] = {}
        self.installed_widgets: dict[str, list[dict]] = {}
        # HA's building (catalog.CatalogCache), for panels that add rooms and devices.
        self.catalog = None
        # Rooms' pictures from HA, by area, until HA's picture changes.
        self.pictures: dict[str, dict] = {}
        # Panels as speakers in HA (speakers.Speakers).
        self.speakers = None
        # The intercom (intercom.Intercom).
        self.intercom = None
        # Panels as AirPlay speakers (airplay.AirPlay).
        self.airplay = None

    async def open_door(self, target: str, panel_id: str | None, calls: bool = True) -> dict | str:
        """Opens [target] for a panel (or HA: panel_id None): {"opened"}, or {"dtmf": code} for
        the panel to send in its call ([calls]); else why not. HA hears that it opened."""
        ic = self.intercom
        how = ic.opener(target, panel_id) if ic is not None else None
        if not how or how.get("kind", "none") == "none" or (how["kind"] == "dtmf" and not calls):
            return "this door does not open from here"
        who = "Home Assistant" if panel_id is None else getattr(self.store.panels.get(panel_id), "name", panel_id)
        try:
            if how["kind"] == "dtmf":
                result = {"dtmf": how["code"]}
            elif how["kind"] == "http":
                await door_open.by_address(self.session, how["url"], how.get("user", ""), how.get("password", ""))
                result = {"opened": True}
            else:
                await door_open.by_device(self.session, self.ha_http, self.ha_auth, how["entity"])
                result = {"opened": True}
        except door_open.OpenFailed as e:
            log.warning("%s: door %s not opened: %s", who, target, e)
            return str(e)
        log.info("%s opens %s (%s)", who, target, how["kind"])
        if self.speakers is not None:
            await self.speakers.door_event(target, "opened", {"by": who, "how": how["kind"]})
        return result

    async def speaker_command(self, panel_id: str, command: dict) -> bool:
        """HA's command to a panel's speaker, to its connection that listens. False if none."""
        for conn in list(self.connections.get(panel_id, ())):
            for sub in list(conn.speaker_subs):
                await conn.send({"id": sub, "type": "event", "event": {"command": command}})
                return True
        return False

    async def area_picture(self, area_id: str) -> dict | None:
        """{"picture": HA's path, "type", "data" (base64)} of an area's picture, or None."""
        if self.catalog is None:
            return None
        area = next((a for a in (await self.catalog.get())["areas"] if a["id"] == area_id), None)
        path = (area or {}).get("picture")
        if not path:
            return None
        cached = self.pictures.get(area_id)
        if cached and cached["picture"] == path:
            return cached
        async with self.session.get(self.ha_http + path, headers=self.ha_auth) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
        picture = {"picture": path, "type": resp.headers.get("Content-Type", "image/jpeg"),
                   "data": base64.b64encode(data).decode()}
        self.pictures[area_id] = picture
        return picture

    @property
    def ha_http(self) -> str:
        """HA's HTTP API beside its WebSocket: ws://supervisor/core/websocket -> http://supervisor/core."""
        return re.sub(r"/(api/)?websocket$", "", self.settings.ha_ws_url).replace("ws", "http", 1)

    @property
    def ha_auth(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.ha_token}"}

    async def camera_still(self, entity_id: str, width: int | None = None) -> tuple[str, bytes] | None:
        """(type, bytes) of a camera's picture now (about [width] wide when the camera can), or None."""
        query = f"?width={width}" if width else ""
        async with self.session.get(f"{self.ha_http}/api/camera_proxy/{entity_id}{query}", headers=self.ha_auth,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return None
            return resp.headers.get("Content-Type", "image/jpeg").split(";")[0], await resp.read()

    async def camera_snapshot(self, entity_id: str, width: int | None = None) -> dict | None:
        """{"type", "data" (base64)} of a camera's picture now, or None."""
        still = await self.camera_still(entity_id, width)
        return {"type": still[0], "data": base64.b64encode(still[1]).decode()} if still else None

    # A live stream that gives no picture this long is given up for the camera's pictures.
    FIRST_FRAME_S = 10

    async def camera_frames(self, entity_id: str, fps: float, width: int | None = None):
        """A camera's live picture: (type, bytes), at most [fps] a second. From HA's MJPEG stream;
        when that gives nothing (HA's ffmpeg failing on the camera's RTSP...), from the
        camera's pictures one after the other, about [width] wide."""
        got = False
        try:
            async for frame in self._mjpeg_frames(entity_id, fps):
                got = True
                yield frame
            return
        except (asyncio.TimeoutError, aiohttp.ServerTimeoutError, aiohttp.ClientPayloadError, NoStream) as e:
            if got:
                raise
            log.info("camera %s: no live stream from HA (%s), its pictures instead", entity_id, e or type(e).__name__)
        loop = asyncio.get_running_loop()
        while True:
            began = loop.time()
            still = await self.camera_still(entity_id, width)
            if still is None:
                raise RuntimeError("no picture from the camera")
            yield still
            await asyncio.sleep(max(0.0, 1 / fps - (loop.time() - began)))

    async def _mjpeg_frames(self, entity_id: str, fps: float):
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=self.FIRST_FRAME_S)
        async with self.session.get(f"{self.ha_http}/api/camera_proxy_stream/{entity_id}", headers=self.ha_auth,
                                    timeout=timeout) as resp:
            if resp.status != 200:
                raise NoStream(f"Home Assistant answered {resp.status}")
            loop = asyncio.get_running_loop()
            began = loop.time()
            last = None
            frames = MjpegFrames()
            async for chunk in resp.content.iter_any():
                found = frames.feed(chunk)
                if last is None and not found and loop.time() - began > self.FIRST_FRAME_S:
                    raise NoStream("no picture in the stream")
                for frame in found:
                    now = loop.time()
                    # Frames in between are read and dropped: the panel gets the newest at its pace.
                    if last is not None and now - last < 1 / fps:
                        continue
                    last = now
                    yield "image/jpeg", frame

    def online(self, panel_id: str) -> bool:
        return bool(self.connections.get(panel_id))

    async def notify_layout(self, panel_id: str) -> None:
        for conn in list(self.connections.get(panel_id, ())):
            await conn.push_layout()

    async def notify_config(self, panel_id: str | None = None) -> None:
        """New effective configuration for one panel, or for all (site change)."""
        # A panel renamed: its AirPlay name too.
        if getattr(self, "airplay", None) is not None:
            await self.airplay.apply()
        for pid, conns in list(self.connections.items()):
            if panel_id in (None, pid):
                for conn in list(conns):
                    await conn.push_config()

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
        if self.speakers is not None:
            await self.speakers.changed()

        # get_states asked by the panel: HA answers with every entity; the panel gets its own.
        state_requests: set = set()
        # The panel's subscribe_entities: their events carry only what its layout has now.
        entity_subs: set = set()

        async def from_ha():
            async for m in upstream:
                if m.type != WSMsgType.TEXT:
                    break
                if entity_subs and '"event"' in m.data:
                    msg = json.loads(m.data)
                    if msg.get("type") == "event" and msg.get("id") in entity_subs:
                        event = only_allowed(msg.get("event") or {}, conn.allowed)
                        if event is not None:
                            await ws.send_json({**msg, "event": event})
                        continue
                if state_requests and '"result"' in m.data:
                    msg = json.loads(m.data)
                    if msg.get("type") == "result" and msg.get("id") in state_requests:
                        state_requests.discard(msg["id"])
                        if isinstance(msg.get("result"), list):
                            allowed = conn.allowed
                            msg["result"] = [s for s in msg["result"] if s.get("entity_id") in allowed]
                        await ws.send_json(msg)
                        continue
                await ws.send_str(m.data)

        async def from_panel():
            async for m in ws:
                if m.type != WSMsgType.TEXT:
                    break
                # Unreadable: dropped, the connection goes on.
                try:
                    msg = json.loads(m.data)
                except ValueError:
                    msg = None
                if not isinstance(msg, dict):
                    log.warning("panel %s sent an unreadable message", panel.id)
                    continue
                if str(msg.get("type", "")).startswith("wallpanel/"):
                    await conn.handle_local(msg)
                    continue
                forward, reason = check_command(msg, conn.allowed)
                if forward is not None:
                    if forward.get("type") == "get_states":
                        state_requests.add(forward.get("id"))
                    elif forward.get("type") == "subscribe_entities":
                        entity_subs.add(forward.get("id"))
                    elif forward.get("type") == "unsubscribe_events":
                        entity_subs.discard(forward.get("subscription"))
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
            conn.stop_streams()
            await upstream.close()
            await ws.close()
            self.connections.get(panel.id, set()).discard(conn)
            self.store.seen(panel.id)
            log.info("panel %s disconnected", panel.id)
            if self.speakers is not None:
                if not any(c.speaker_subs for c in self.connections.get(panel.id, ())):
                    await self.speakers.gone(panel.id)
                else:
                    await self.speakers.changed()
        return ws
