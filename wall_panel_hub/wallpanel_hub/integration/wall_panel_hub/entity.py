"""What the panels' and doors' entities share: their device, and the link's state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .hub import HubLink


class PanelEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, link: HubLink, panel_id: str, key: str) -> None:
        self.link = link
        self.panel_id = panel_id
        self._attr_unique_id = f"{panel_id}_{key}"
        panel = link.panels.get(panel_id, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, panel_id)},
            name=panel.get("name") or "Wall panel",
            model=panel.get("model") or None,
            manufacturer="Wall Panel",
        )

    @property
    def speaker(self) -> dict[str, Any] | None:
        """The panel's speaker state, while it is connected."""
        panel = self.link.panels.get(self.panel_id)
        return (panel or {}).get("speaker") if self.link.connected else None

    @property
    def available(self) -> bool:
        return self.speaker is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.link.listen(self.async_write_ha_state))

    async def command(self, command: dict[str, Any]) -> None:
        await self.link.command(self.panel_id, command)


def add_speaker_entities(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback,
    make: Callable[[HubLink, str], Entity], key: str = "speaker",
) -> None:
    """Adds [make]'s entity for each panel that reports a speaker (or its [key]: its call), now
    and when one comes."""
    link: HubLink = hass.data[DOMAIN]["link"]
    added: set[str] = set()

    @callback
    def update() -> None:
        # Unpaired (its entities removed with its device): added again if it pairs again.
        if link.synced:
            added.intersection_update(link.panels)
        # A panel with an older app reports no speaker: no entity for it.
        new = [pid for pid, p in link.panels.items() if p.get(key) and pid not in added]
        if new:
            added.update(new)
            add([make(link, pid) for pid in new])

    update()
    entry.async_on_unload(link.listen(update))


class DoorEntity(Entity):
    """A door's entity, on the door's own device (its name in the add-on)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, link: HubLink, target: str, key: str) -> None:
        self.link = link
        self.target = target
        self._attr_unique_id = f"{target}_{key}"
        door = link.doors.get(target, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, target)},
            name=door.get("name") or "Door",
            model="Door" if target.startswith("door:") else "Doorbell",
            manufacturer="Wall Panel Hub",
        )

    @property
    def door(self) -> dict[str, Any] | None:
        return self.link.doors.get(self.target) if self.link.connected else None

    @property
    def available(self) -> bool:
        return self.door is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.link.listen(self.async_write_ha_state))


def add_door_entities(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback,
    make: Callable[[HubLink, str], Entity], want: Callable[[dict[str, Any]], bool] = lambda door: True,
) -> None:
    """Adds [make]'s entity for each door ([want] says which), now and when one is added."""
    link: HubLink = hass.data[DOMAIN]["link"]
    added: set[str] = set()

    @callback
    def update() -> None:
        if link.synced:
            added.intersection_update(link.doors)
        new = [t for t, d in link.doors.items() if want(d) and t not in added]
        if new:
            added.update(new)
            add([make(link, t) for t in new])

    update()
    entry.async_on_unload(link.listen(update))
