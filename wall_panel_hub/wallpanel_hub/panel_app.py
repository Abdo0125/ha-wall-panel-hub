"""The panel app this add-on carries (docs/distribution-plan.md, D2).

One release is the add-on and the app built with it, so a panel never gets an app newer than
its hub. The app file (about 21 MB) is not kept in the add-on's repository: it is a GitHub
release asset, fetched once when the add-on's image is built (fetch_app.py), then served on the
local network with no internet needed. panel_app.json, next to this file, says which app that is.
"""

import json
import os
from pathlib import Path

MANIFEST = Path(__file__).parent / "panel_app.json"
# Where the image keeps the app (Dockerfile). On a PC (tools/run-hub-dev.ps1): $WALLPANEL_APP.
BUNDLED = Path("/app/panel_app/WallPanel.apk")
RELEASES = "https://github.com/Abdo0125/ha-wall-panel-hub/releases/download"
MIME = "application/vnd.android.package-archive"


def expected() -> dict | None:
    """The app this add-on carries: version_name, version_code, sha256, size."""
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def asset_name(version_name: str) -> str:
    return f"WallPanel-{version_name}.apk"


def release_url(addon_version: str, version_name: str) -> str:
    """The app's file in the add-on's own release (made by tools/publish_addon.py)."""
    return f"{RELEASES}/v{addon_version}/{asset_name(version_name)}"


def path() -> Path | None:
    """The app's file here, if the image has it."""
    override = os.environ.get("WALLPANEL_APP")
    found = Path(override) if override else BUNDLED
    return found if found.is_file() else None


def info() -> dict:
    """For the management page: which app, and whether the file is here to hand out."""
    app = expected() or {}
    return {
        "version_name": app.get("version_name"),
        "version_code": app.get("version_code"),
        "size": app.get("size"),
        "available": path() is not None,
    }
