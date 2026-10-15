"""Installs the companion integration into Home Assistant and announces the add-on.

The integration (integration/wall_panel_hub) lets panels reach the hub through Home
Assistant's own address. The add-on copies it into /config/custom_components when it is
missing or older, then tells the Supervisor about itself so Home Assistant offers to set
it up ("Discovered: Wall Panel Hub") after its next restart.
"""

import json
import logging
import shutil
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

SOURCE = Path(__file__).parent / "integration" / "wall_panel_hub"
SUPERVISOR = "http://supervisor"


def _version(folder: Path) -> str | None:
    manifest = folder / "manifest.json"
    if not manifest.exists():
        return None
    return json.loads(manifest.read_text(encoding="utf-8")).get("version")


def install(ha_config: Path) -> bool:
    """Copies the integration if needed. Returns True when Home Assistant must restart."""
    if not ha_config.exists():
        log.warning("Home Assistant config folder not mapped; remote panels need the companion integration")
        return False
    target = ha_config / "custom_components" / "wall_panel_hub"
    have, ship = _version(target), _version(SOURCE)
    if have == ship:
        return False
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns("__pycache__"))
    log.info("companion integration %s installed (was %s): restart Home Assistant to load it", ship, have)
    return True


async def announce(session: aiohttp.ClientSession, token: str, port: int, restart_needed: bool) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        # Home Assistant runs on the host network like this add-on, so it reaches us on 127.0.0.1.
        async with session.post(f"{SUPERVISOR}/discovery", headers=headers, json={
            "service": "wall_panel_hub",
            "config": {"host": "127.0.0.1", "port": port},
        }) as resp:
            if resp.status >= 400:
                log.warning("Supervisor discovery refused: %s %s", resp.status, await resp.text())
        if restart_needed:
            async with session.post(
                f"{SUPERVISOR}/core/api/services/persistent_notification/create",
                headers=headers,
                json={
                    "notification_id": "wall_panel_hub_restart",
                    "title": "Wall Panel Hub",
                    "message": "The Wall Panel Hub integration was installed or updated. "
                               "Restart Home Assistant, then accept **Wall Panel Hub** under "
                               "Settings → Devices & services → Discovered, so that panels on "
                               "other networks can pair through this Home Assistant's address.",
                },
            ):
                pass
    except aiohttp.ClientError as e:
        log.warning("could not announce to the Supervisor: %s", e)
