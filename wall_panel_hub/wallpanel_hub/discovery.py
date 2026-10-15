"""Announces the hub on the LAN (mDNS/DNS-SD) so panels find it without typing an address."""

import logging
import socket

from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from .settings import VERSION

log = logging.getLogger(__name__)

SERVICE_TYPE = "_wallpanel-hub._tcp.local."


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
