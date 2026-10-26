"""Runtime settings: inside Home Assistant (Supervisor) or on a developer PC."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.45.0"

# Home Assistant's internal network (hassio). Ingress requests come from the Supervisor
# inside it; with host networking the exact source address in it varies by install.
HASSIO_NETWORK = "172.30.32.0/23"


@dataclass(frozen=True)
class Settings:
    ha_ws_url: str
    ha_token: str
    data_dir: Path
    panel_port: int
    ui_port: int
    # Networks allowed to open the management UI.
    ui_allowed: tuple[str, ...]
    dev: bool
    # Home Assistant's config folder, where the companion integration goes (add-on only).
    ha_config: Path | None = None
    # The intercom's SIP port (Asterisk, on the host's network).
    sip_port: int = 5060


# HA's base images start the program through s6-overlay, which does not pass the
# container environment on; the Supervisor's variables are kept in this folder instead.
S6_ENV = Path("/run/s6/container_environment")
OPTIONS_FILE = Path("/data/options.json")


def _supervisor_token() -> str | None:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        return token
    f = S6_ENV / "SUPERVISOR_TOKEN"
    return f.read_text().strip() if f.exists() else None


def load() -> Settings:
    token = _supervisor_token()
    # The Supervisor always writes options.json: it marks a real add-on install.
    if OPTIONS_FILE.exists():
        if not token:
            raise RuntimeError("Running as an add-on but SUPERVISOR_TOKEN is missing (homeassistant_api: true?)")
        options = json.loads(OPTIONS_FILE.read_text())
        return Settings(
            ha_ws_url="ws://supervisor/core/websocket",
            ha_token=token,
            data_dir=Path("/data"),
            panel_port=int(options.get("panel_port", 8765)),
            ui_port=8099,
            ui_allowed=(HASSIO_NETWORK,),
            dev=False,
            ha_config=Path("/homeassistant"),
            sip_port=int(options.get("sip_port", 5060)),
        )

    # Developer mode: python -m wallpanel_hub with HA_URL and HA_TOKEN set.
    url = os.environ.get("HA_URL", "http://localhost:8123").rstrip("/")
    ws = url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    return Settings(
        ha_ws_url=ws,
        ha_token=os.environ.get("HA_TOKEN", "dev-token"),
        data_dir=Path(os.environ.get("DATA_DIR", ".dev-data")),
        panel_port=int(os.environ.get("PANEL_PORT", "8765")),
        ui_port=int(os.environ.get("UI_PORT", "8099")),
        ui_allowed=("127.0.0.0/8", "::1/128"),
        dev=True,
    )
