"""Set up from the add-on's Supervisor discovery, or by hand (Add integration)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HOST, CONF_PORT, DEFAULT_HOST, DEFAULT_PORT, DOMAIN

try:  # Moved in Home Assistant 2025.x
    from homeassistant.helpers.service_info.hassio import HassioServiceInfo
except ImportError:  # pragma: no cover
    from homeassistant.components.hassio import HassioServiceInfo


class WallPanelHubFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def _reachable(self, host: str, port: int) -> bool:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(f"http://{host}:{port}/api/info", timeout=5) as resp:
                return resp.status == 200 and (await resp.json()).get("service") == "wall_panel_hub"
        except Exception:  # noqa: BLE001 - any failure means "not reachable"
            return False

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """The add-on announced itself through the Supervisor."""
        self._data = {
            CONF_HOST: discovery_info.config.get(CONF_HOST, DEFAULT_HOST),
            CONF_PORT: int(discovery_info.config.get(CONF_PORT, DEFAULT_PORT)),
        }
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured(updates=self._data)
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Wall Panel Hub", data=self._data)
        return self.async_show_form(step_id="hassio_confirm")

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            if await self._reachable(user_input[CONF_HOST], user_input[CONF_PORT]):
                return self.async_create_entry(title="Wall Panel Hub", data=user_input)
            errors["base"] = "cannot_connect"
        schema = vol.Schema({
            vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
