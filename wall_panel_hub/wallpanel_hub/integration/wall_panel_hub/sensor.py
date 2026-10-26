"""Each panel's call: idle, ringing, calling, in a call (and with whom)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PanelEntity, add_speaker_entities
from .hub import HubLink

CALLS = ["idle", "ringing", "calling", "active"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add_speaker_entities(hass, entry, add, PanelCall, key="call")


class PanelCall(PanelEntity, SensorEntity):
    _attr_translation_key = "call"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CALLS

    def __init__(self, link: HubLink, panel_id: str) -> None:
        super().__init__(link, panel_id, "call")

    @property
    def call(self) -> dict[str, Any] | None:
        panel = self.link.panels.get(self.panel_id)
        return (panel or {}).get("call") if self.link.connected else None

    @property
    def available(self) -> bool:
        return self.call is not None

    @property
    def native_value(self) -> str | None:
        return (self.call or {}).get("state")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"with": (self.call or {}).get("with")}
