"""Each panel's sound output: its own speaker, a USB-C speaker or sound card, or Bluetooth."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PanelEntity, add_speaker_entities
from .hub import HubLink


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add_speaker_entities(hass, entry, add, AudioOutput)


class AudioOutput(PanelEntity, SelectEntity):
    _attr_translation_key = "audio_output"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, link: HubLink, panel_id: str) -> None:
        super().__init__(link, panel_id, "audio_output")

    @property
    def options(self) -> list[str]:
        # Those the panel has now: Bluetooth only while a speaker is paired, for one.
        return (self.speaker or {}).get("outputs") or ["auto"]

    @property
    def current_option(self) -> str | None:
        return (self.speaker or {}).get("output")

    async def async_select_option(self, option: str) -> None:
        await self.command({"command": "output", "output": option})
