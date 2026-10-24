"""Each panel's speaker: HA's sounds (TTS, its media, radio, any address) played on the panel."""

from __future__ import annotations

from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PanelEntity, add_speaker_entities
from .hub import HubLink

STATES = {
    "idle": MediaPlayerState.IDLE,
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "buffering": MediaPlayerState.BUFFERING,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add_speaker_entities(hass, entry, add, PanelSpeaker)


class PanelSpeaker(PanelEntity, MediaPlayerEntity):
    # The device's own name: media_player.<panel>.
    _attr_name = None
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
    )

    def __init__(self, link: HubLink, panel_id: str) -> None:
        super().__init__(link, panel_id, "speaker")

    @property
    def state(self) -> MediaPlayerState | None:
        speaker = self.speaker
        return STATES.get(speaker["state"], MediaPlayerState.IDLE) if speaker else None

    @property
    def volume_level(self) -> float | None:
        return (self.speaker or {}).get("volume")

    @property
    def is_volume_muted(self) -> bool | None:
        return (self.speaker or {}).get("muted")

    @property
    def media_title(self) -> str | None:
        return (self.speaker or {}).get("title")

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = item.url
        # HA's own sound: a signed path the panel fetches from HA, or from the add-on.
        media_id = async_process_play_media_url(self.hass, media_id, allow_relative=True)
        command: dict[str, Any] = {"command": "play_media", "announce": bool(kwargs.get(ATTR_MEDIA_ANNOUNCE))}
        command["path" if media_id.startswith("/") else "url"] = media_id
        extra = kwargs.get("extra") or {}
        title = extra.get("title") if isinstance(extra, dict) else None
        if title:
            command["title"] = str(title)
        await self.command(command)

    async def async_media_play(self) -> None:
        await self.command({"command": "play"})

    async def async_media_pause(self) -> None:
        await self.command({"command": "pause"})

    async def async_media_stop(self) -> None:
        await self.command({"command": "stop"})

    async def async_set_volume_level(self, volume: float) -> None:
        await self.command({"command": "volume", "level": volume})

    async def async_mute_volume(self, mute: bool) -> None:
        await self.command({"command": "mute", "muted": mute})

    async def async_browse_media(self, media_content_type: str | None = None,
                                 media_content_id: str | None = None) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass, media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )
