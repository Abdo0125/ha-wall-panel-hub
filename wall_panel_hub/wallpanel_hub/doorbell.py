"""Doors' calls as Home Assistant events (intercom part B5).

Asterisk's dialplan (intercom.extensions_conf) tells of each door's call in UserEvents, read
here through Asterisk's manager (AMI: 127.0.0.1 only, a secret new each run of the hub):
WallpanelRing when it comes, WallpanelAnswered when a panel takes it, WallpanelEnd when it
ends. Each becomes an event of the door, or of the device of the door's server it comes from
(Intercom.caller): ring, answered (by which panel), missed. Heard whether or not a panel is
connected: HA's notification matters most when no one is at a panel.
"""

import asyncio
import logging

from .intercom import AMI_PORT, AMI_USER

log = logging.getLogger(__name__)

BACKOFF_S = (2, 5, 10, 30)
# Calls followed at once, at most (a call whose end was not heard is dropped at last).
MAX_CALLS = 50


def parse(block: str) -> dict[str, str]:
    """An AMI message ("Key: value" lines) as a dict; the first of a key repeated."""
    out: dict[str, str] = {}
    for line in block.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out.setdefault(key.strip(), value.strip())
    return out


class Doorbell:
    def __init__(self, intercom, emit, panel_name):
        self.intercom = intercom
        # emit(target, event, data): to HA (Speakers.door_event).
        self.emit = emit
        self.panel_name = panel_name
        # Calls ringing or taken, by Asterisk's linkedid: {"target", "answered"}.
        self.calls: dict[str, dict] = {}

    async def handle(self, ev: dict) -> None:
        name = ev.get("UserEvent")
        call = ev.get("Call") or ""
        if name == "WallpanelRing":
            # The same call through a second extension: told once.
            if not call or call in self.calls:
                return
            number = ev.get("Number") or None
            target = self.intercom.caller(ev.get("Door") or "", number)
            while len(self.calls) >= MAX_CALLS:
                del self.calls[next(iter(self.calls))]
            self.calls[call] = {"target": target, "answered": False}
            await self.emit(target, "ring", {"number": number})
        elif name == "WallpanelAnswered":
            c = self.calls.get(call)
            if c is None or c["answered"]:
                return
            c["answered"] = True
            pid = self.intercom.panel_of(ev.get("Panel") or "")
            await self.emit(c["target"], "answered", {"panel": self.panel_name(pid) if pid else None})
        elif name == "WallpanelEnd":
            c = self.calls.pop(call, None)
            if c is not None and not c["answered"]:
                await self.emit(c["target"], "missed", {"status": ev.get("Status") or None})

    async def run(self) -> None:
        """Listens to Asterisk while it runs; again after each restart of it."""
        attempt = 0
        while True:
            if not self.intercom.running():
                await asyncio.sleep(2)
                continue
            writer = None
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", AMI_PORT), 5)
                await asyncio.wait_for(reader.readline(), 5)  # Asterisk Call Manager/x
                writer.write(f"Action: Login\r\nUsername: {AMI_USER}\r\nSecret: {self.intercom.ami_secret}\r\n"
                             "Events: user\r\n\r\n".encode())
                await writer.drain()
                while True:
                    first = parse((await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)).decode(errors="replace"))
                    if "Response" in first:
                        break
                if first["Response"] != "Success":
                    log.warning("Asterisk's manager refused the hub: %s", first.get("Message"))
                    raise ConnectionError
                log.info("doorbell: hearing the doors' calls")
                attempt = 0
                while True:
                    ev = parse((await reader.readuntil(b"\r\n\r\n")).decode(errors="replace"))
                    if ev.get("Event") == "UserEvent":
                        try:
                            await self.handle(ev)
                        except Exception:
                            log.exception("doorbell: %s not told", ev.get("UserEvent"))
            except (OSError, ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                pass
            finally:
                if writer is not None:
                    writer.close()
            await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
            attempt += 1
