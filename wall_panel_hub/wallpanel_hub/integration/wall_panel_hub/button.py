"""Each door that opens without a call (its own address, a device of HA's): its Open button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DoorEntity, add_door_entities
from .hub import HubLink


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add_door_entities(hass, entry, add, OpenDoor, want=lambda door: door.get("opens"))


class OpenDoor(DoorEntity, ButtonEntity):
    _attr_translation_key = "open"

    def __init__(self, link: HubLink, target: str) -> None:
        super().__init__(link, target, "open")

    @property
    def available(self) -> bool:
        # Its opening changed in the add-on to one in a call only: no longer from here.
        return bool((self.door or {}).get("opens"))

    async def async_press(self) -> None:
        await self.link.open(self.target)
