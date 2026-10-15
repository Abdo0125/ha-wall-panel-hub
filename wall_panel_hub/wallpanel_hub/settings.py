"""Runtime settings: inside Home Assistant (Supervisor) or on a developer PC."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.0"

# Ingress requests always come from the Supervisor at this address.
SUPERVISOR_INGRESS_IP = "172.30.32.2"


@dataclass(frozen=True)
class Settings:
    ha_ws_url: str
    ha_token: str
    data_dir: Path
    panel_port: int
    ui_port: int
    # Addresses allowed to open the management UI.
    ui_allowed: frozenset[str]
    dev: bool


def load() -> Settings:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        options = {}
        options_file = Path("/data/options.json")
        if options_file.exists():
            options = json.loads(options_file.read_text())
        return Settings(
            ha_ws_url="ws://supervisor/core/websocket",
            ha_token=token,
            data_dir=Path("/data"),
            panel_port=int(options.get("panel_port", 8765)),
            ui_port=8099,
            ui_allowed=frozenset({SUPERVISOR_INGRESS_IP}),
            dev=False,
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
        ui_allowed=frozenset({"127.0.0.1", "::1"}),
        dev=True,
    )
