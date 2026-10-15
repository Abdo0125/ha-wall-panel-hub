"""mDNS/DNS-SD on the LAN, both ways.

- The hub announces itself (_wallpanel-hub._tcp) so panels find it without an address.
- Panels not yet paired announce themselves (_wallpanel._tcp); the hub lists them so
  the installer sees a new panel as soon as it is switched on.
"""

import asyncio
import logging
import socket
import time

from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .settings import VERSION

log = logging.getLogger(__name__)

SERVICE_TYPE = "_wallpanel-hub._tcp.local."
PANEL_TYPE = "_wallpanel._tcp.local."


def _lan_ip() -> str:
    # The address used for outgoing traffic is the one panels can reach.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]


async def announce(port: int) -> AsyncZeroconf | None:
    try:
        ip = _lan_ip()
        zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        info = AsyncServiceInfo(
            SERVICE_TYPE,
            f"Wall Panel Hub.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={"version": VERSION},
        )
        await zc.async_register_service(info)
        log.info("announced %s on %s:%s", SERVICE_TYPE, ip, port)
        return zc
    except Exception as e:  # Discovery is a convenience; the address can always be typed.
        log.warning("mDNS announcement failed: %s", e)
        return None


class PanelBrowser:
    """Unpaired panels seen on the LAN: {panel id: {id, name, model, version, address, seen}}."""

    def __init__(self):
        self.panels: dict[str, dict] = {}
        self._names: dict[str, str] = {}  # mDNS service name -> panel id
        self._browser = None
        self._tasks: set[asyncio.Task] = set()

    def start(self, zc: AsyncZeroconf) -> None:
        try:
            self._browser = AsyncServiceBrowser(zc.zeroconf, PANEL_TYPE, handlers=[self._on_change])
        except Exception as e:
            log.warning("panel discovery failed: %s", e)

    def _on_change(self, zeroconf, service_type, name, state_change) -> None:
        if state_change is ServiceStateChange.Removed:
            self.panels.pop(self._names.pop(name, ""), None)
            return
        task = asyncio.ensure_future(self._resolve(zeroconf, service_type, name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _resolve(self, zeroconf, service_type, name) -> None:
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(zeroconf, 3000):
            return
        props = {k.decode(): (v or b"").decode(errors="replace") for k, v in (info.properties or {}).items()}
        pid = props.get("id", "")[:64]
        if not pid:
            return
        self._names[name] = pid
        self.panels[pid] = {
            "id": pid,
            "name": props.get("name", "")[:60],
            "model": props.get("model", "")[:60],
            "version": props.get("version", "")[:20],
            "address": (info.parsed_addresses() or [""])[0],
            "seen": time.time(),
        }

    async def close(self) -> None:
        if self._browser:
            await self._browser.async_cancel()
