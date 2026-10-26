"""Panels as speakers in Home Assistant, through the companion integration.

The integration connects to /api/integration on the panel port with a secret the add-on
made and handed to it in its Supervisor discovery (the port is open to the whole network:
without the secret nothing is answered). It gets every paired panel and its speaker's state
(snapshots, sent again on each change) and sends commands, which the hub passes to the
panel's connection (wallpanel/speaker/subscribe).

The doors come too ("doors", Intercom.bells): HA has each with its doorbell's events (ring,
answered, missed, opened: "door_event", doorbell.py) and its Open button ("open"); and each
panel's call (idle, ringing, calling, active), as the panel tells it.

A sound HA keeps itself (TTS, its media folder) comes as a signed path. A panel through
HA's address fetches it there; one on the add-on's network fetches it from the hub, at
/api/media/<token>, for the paths handed out here only.
"""

import hmac
import json
import logging
import re
import secrets
import time
from pathlib import Path

from aiohttp import WSMsgType, web

log = logging.getLogger(__name__)

# What a panel reports; anything else is dropped.
STATES = ("idle", "playing", "paused", "buffering")
OUTPUTS = ("auto", "speaker", "usb", "bluetooth")
COMMANDS = ("play_media", "play", "pause", "stop", "volume", "mute", "output")
CALLS = ("idle", "ringing", "calling", "active")
# HA's own path for a sound: /api/tts_proxy/..., /media/local/...?authSig=...
HA_PATH = re.compile(r"/[A-Za-z0-9_./%~-]*(\?[A-Za-z0-9_.=&%~-]*)?")
MEDIA_TTL_S = 24 * 3600
MAX_MEDIA = 200


def load_secret(data_dir: Path) -> str:
    """The integration's secret, made once and kept with the add-on's data."""
    f = data_dir / "integration.json"
    if f.exists():
        try:
            secret = json.loads(f.read_text(encoding="utf-8")).get("secret")
            if isinstance(secret, str) and len(secret) >= 32:
                return secret
        except ValueError:
            pass
    secret = secrets.token_urlsafe(32)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"secret": secret}), encoding="utf-8")
    tmp.replace(f)
    return secret


def clean_state(raw) -> dict | None:
    """A panel's speaker state, as reported, kept to known fields and values."""
    if not isinstance(raw, dict):
        return None
    state = raw.get("state") if raw.get("state") in STATES else "idle"
    volume = raw.get("volume")
    volume = min(max(float(volume), 0.0), 1.0) if isinstance(volume, (int, float)) else None
    outputs = [o for o in raw.get("outputs") or [] if o in OUTPUTS] or ["auto"]
    output = raw.get("output") if raw.get("output") in outputs else outputs[0]
    title = raw.get("title")
    return {
        "state": state,
        "volume": volume,
        "muted": bool(raw.get("muted")),
        "title": str(title)[:200] if title else None,
        "output": output,
        "outputs": outputs,
    }


def check_speaker_command(cmd) -> tuple[dict | None, str | None]:
    """(command for the panel, None) or (None, why it is refused)."""
    if not isinstance(cmd, dict) or cmd.get("command") not in COMMANDS:
        return None, "unknown command"
    kind = cmd["command"]
    if kind == "play_media":
        url, path = cmd.get("url"), cmd.get("path")
        if isinstance(url, str) and re.match(r"https?://", url) and len(url) <= 4096:
            media = {"url": url}
        elif isinstance(path, str) and HA_PATH.fullmatch(path) and len(path) <= 4096 and ".." not in path:
            media = {"path": path}
        else:
            return None, "a sound's http(s) address or Home Assistant path is needed"
        title = cmd.get("title")
        return {"command": kind, **media, "title": str(title)[:200] if title else None,
                "announce": bool(cmd.get("announce"))}, None
    if kind == "volume":
        level = cmd.get("level")
        if not isinstance(level, (int, float)) or not 0 <= level <= 1:
            return None, "level is 0 to 1"
        return {"command": kind, "level": float(level)}, None
    if kind == "mute":
        return {"command": kind, "muted": bool(cmd.get("muted"))}, None
    if kind == "output":
        if cmd.get("output") not in OUTPUTS:
            return None, f"output is one of {', '.join(OUTPUTS)}"
        return {"command": kind, "output": cmd["output"]}, None
    return {"command": kind}, None


class Speakers:
    def __init__(self, store, gateway, secret: str):
        self.store = store
        self.gateway = gateway
        self.secret = secret
        # Speaker state by panel, while its connection is open.
        self.states: dict[str, dict] = {}
        # Each panel's call, as it tells it (wallpanel/intercom/state).
        self.calls: dict[str, dict] = {}
        self.links: set[web.WebSocketResponse] = set()
        # Sounds of HA's handed out to panels on the add-on's network: token -> (path, time).
        self.media: dict[str, tuple[str, float]] = {}

    # ---- what the integration sees ----

    def snapshot(self) -> list[dict]:
        return [
            {"id": p.id, "name": p.name, "model": p.model, "online": self.gateway.online(p.id),
             "speaker": self._speaker(p.id), "call": self.calls.get(p.id)}
            for p in self.store.panels.values()
        ]

    def _speaker(self, panel_id: str) -> dict | None:
        """The panel's speaker state, with AirPlay's while it plays on it (airplay.py)."""
        state = self.states.get(panel_id)
        airplay = getattr(self.gateway, "airplay", None)
        return airplay.overlay(panel_id, state) if airplay is not None else state

    async def art_changed(self, panel_id: str, art: tuple[str, str, bytes]) -> None:
        """A cover (AirPlay's) for the panel's media_player, sent once: the state names it by its hash."""
        if self.links:
            await self.send_all(self._art(panel_id, art))

    @staticmethod
    def _art(panel_id: str, art: tuple[str, str, bytes]) -> str:
        import base64
        return json.dumps({"type": "art", "panel_id": panel_id, "hash": art[0], "mime": art[1],
                           "data": base64.b64encode(art[2]).decode()})

    def message(self) -> str:
        ic = self.gateway.intercom
        doors = ic.bells() if ic is not None and ic.settings["enabled"] else []
        return json.dumps({"type": "panels", "panels": self.snapshot(), "doors": doors}, ensure_ascii=False)

    async def send_all(self, text: str) -> None:
        for ws in list(self.links):
            if not ws.closed:
                await ws.send_str(text)

    async def changed(self) -> None:
        if not self.links:
            return
        text = self.message()
        for ws in list(self.links):
            if not ws.closed:
                await ws.send_str(text)

    async def report(self, panel_id: str, raw) -> bool:
        state = clean_state(raw)
        if state is None:
            return False
        before = self.states.get(panel_id)
        if before != state:
            if (before or {}).get("state") != state["state"]:
                log.info("speaker %s is %s", panel_id, state["state"])
            self.states[panel_id] = state
            await self.changed()
        return True

    async def gone(self, panel_id: str) -> None:
        """The panel's last connection closed, or it was revoked."""
        self.states.pop(panel_id, None)
        self.calls.pop(panel_id, None)
        await self.changed()

    # ---- the intercom ----

    async def call_state(self, panel_id: str, raw) -> bool:
        """The panel's call: {"state", "with"?: who}."""
        if not isinstance(raw, dict) or raw.get("state") not in CALLS:
            return False
        who = raw.get("with")
        state = {"state": raw["state"], "with": str(who)[:60] if who and raw["state"] != "idle" else None}
        if self.calls.get(panel_id) != state:
            self.calls[panel_id] = state
            await self.changed()
        return True

    async def door_event(self, target: str, event: str, data: dict) -> None:
        """A door's (or doorbell's) event, for HA: ring, answered, missed, opened."""
        ic = self.gateway.intercom
        name = ic.name_of(target) if ic is not None else None
        log.info("door %s (%s): %s %s", name or target, target, event,
                 " ".join(f"{k}={v}" for k, v in data.items() if v) or "")
        if self.links:
            await self.send_all(json.dumps({"type": "door_event", "target": target, "event": event, **data},
                                           ensure_ascii=False))

    # ---- HA's sounds for panels on the add-on's network ----

    def media_token(self, path: str) -> str:
        now = time.time()
        for t in [t for t, (_, at) in self.media.items() if now - at > MEDIA_TTL_S]:
            del self.media[t]
        while len(self.media) >= MAX_MEDIA:
            del self.media[next(iter(self.media))]
        token = secrets.token_hex(16)
        self.media[token] = (path, now)
        return token

    def media_path(self, token: str) -> str | None:
        found = self.media.get(token)
        if found is None or time.time() - found[1] > MEDIA_TTL_S:
            return None
        return found[0]

    # ---- commands ----

    async def command(self, panel_id: str, cmd) -> str | None:
        """Passes a command on to the panel. Returns why it could not, or None."""
        if panel_id not in self.store.panels:
            return "unknown panel"
        if panel_id not in self.states:
            return "the panel is not connected"
        forward, reason = check_speaker_command(cmd)
        if forward is None:
            log.warning("speaker %s: command refused: %s", panel_id, reason)
            return reason
        if "path" in forward:
            forward["hub_path"] = f"/api/media/{self.media_token(forward['path'])}"
        # HA's own sound, or its stop or pause, ends AirPlay on the panel; an announcement only lowers it.
        airplay = getattr(self.gateway, "airplay", None)
        if (airplay is not None and airplay.active(panel_id) and forward["command"] in ("play_media", "stop", "pause")
                and not forward.get("announce")):
            await airplay.kick(panel_id)
        sent = await self.gateway.speaker_command(panel_id, forward)
        log.info("speaker %s: %s%s", panel_id, forward["command"], "" if sent else " (not connected)")
        return None if sent else "the panel is not connected"

    # ---- the integration's connection ----

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        try:
            first = await ws.receive_json(timeout=10)
        except Exception:
            await ws.close()
            return ws
        given = first.get("secret") if isinstance(first, dict) and first.get("type") == "auth" else None
        if not isinstance(given, str) or not hmac.compare_digest(given.encode(), self.secret.encode()):
            log.warning("integration connection refused from %s: wrong secret", request.remote)
            await ws.send_json({"type": "auth_invalid"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        self.links.add(ws)
        log.info("Home Assistant integration connected")
        try:
            await ws.send_str(self.message())
            airplay = getattr(self.gateway, "airplay", None)
            for pid, art in airplay.arts() if airplay is not None else ():
                await ws.send_str(self._art(pid, art))
            async for m in ws:
                if m.type != WSMsgType.TEXT:
                    break
                try:
                    msg = json.loads(m.data)
                except ValueError:
                    continue
                if not isinstance(msg, dict) or msg.get("type") not in ("command", "open"):
                    continue
                if msg["type"] == "open":
                    # HA's Open button: a door that opens without a call.
                    reason = await self.gateway.open_door(str(msg.get("target") or ""), None, calls=False)
                    reason = reason if isinstance(reason, str) else None
                else:
                    reason = await self.command(str(msg.get("panel_id") or ""), msg.get("command"))
                await ws.send_json({"type": "result", "id": msg.get("id"), "success": reason is None,
                                    **({"error": reason} if reason else {})})
        finally:
            self.links.discard(ws)
            log.info("Home Assistant integration disconnected")
        return ws
