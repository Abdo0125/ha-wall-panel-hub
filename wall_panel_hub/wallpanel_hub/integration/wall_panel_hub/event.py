"""Each door's doorbell: rang, answered by a panel, missed, opened (and by whom)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DoorEntity, add_door_entities
from .hub import HubLink

EVENTS = ["ring", "answered", "missed", "opened"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add_door_entities(hass, entry, add, Doorbell, want=lambda door: door.get("kind", "door") == "door")


class Doorbell(DoorEntity, EventEntity):
    _attr_translation_key = "doorbell"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = EVENTS

    def __init__(self, link: HubLink, target: str) -> None:
        super().__init__(link, target, "doorbell")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.link.listen_doors(self._event))

    @callback
    def _event(self, msg: dict[str, Any]) -> None:
        if msg.get("target") != self.target or msg.get("event") not in EVENTS:
            return
        # Who, how: the panel that answered, who opened and how, the number it called from.
        attributes = {k: v for k, v in msg.items() if k not in ("type", "target", "event", "id") and v is not None}
        door = self.link.doors.get(self.target) or {}
        if door.get("camera"):
            attributes["camera"] = door["camera"]
        self._trigger_event(msg["event"], attributes)
        self.async_write_ha_state()
