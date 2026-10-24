"""Panels as speakers in Home Assistant, through the companion integration.

The integration connects to /api/integration on the panel port with a secret the add-on
made and handed to it in its Supervisor discovery (the port is open to the whole network:
without the secret nothing is answered). It gets every paired panel and its speaker's state
(snapshots, sent again on each change) and sends commands, which the hub passes to the
panel's connection (wallpanel/speaker/subscribe).

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
        self.links: set[web.WebSocketResponse] = set()
        # Sounds of HA's handed out to panels on the add-on's network: token -> (path, time).
        self.media: dict[str, tuple[str, float]] = {}

    # ---- what the integration sees ----

    def snapshot(self) -> list[dict]:
        return [
            {"id": p.id, "name": p.name, "model": p.model, "online": self.gateway.online(p.id),
             "speaker": self.states.get(p.id)}
            for p in self.store.panels.values()
        ]

    async def changed(self) -> None:
        if not self.links:
            return
        text = json.dumps({"type": "panels", "panels": self.snapshot()}, ensure_ascii=False)
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
        await self.changed()

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
            await ws.send_str(json.dumps({"type": "panels", "panels": self.snapshot()}, ensure_ascii=False))
            async for m in ws:
                if m.type != WSMsgType.TEXT:
                    break
                try:
                    msg = json.loads(m.data)
                except ValueError:
                    continue
                if not isinstance(msg, dict) or msg.get("type") != "command":
                    continue
                reason = await self.command(str(msg.get("panel_id") or ""), msg.get("command"))
                await ws.send_json({"type": "result", "id": msg.get("id"), "success": reason is None,
                                    **({"error": reason} if reason else {})})
        finally:
            self.links.discard(ws)
            log.info("Home Assistant integration disconnected")
        return ws
