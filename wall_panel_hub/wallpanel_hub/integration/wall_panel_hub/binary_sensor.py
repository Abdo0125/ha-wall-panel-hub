"""Each external SIP server: whether the hub is linked there (registered as one of its monitors)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DoorEntity, add_door_entities
from .hub import HubLink


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add_door_entities(hass, entry, add, ServerLink, want=lambda door: door.get("kind") == "server")


class ServerLink(DoorEntity, BinarySensorEntity):
    _attr_translation_key = "link"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, link: HubLink, target: str) -> None:
        super().__init__(link, target, "link")

    @property
    def is_on(self) -> bool | None:
        return bool((self.door or {}).get("linked"))
