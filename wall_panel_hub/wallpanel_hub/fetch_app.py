"""Fetches the panel app into the add-on's image, once, when it is built (Dockerfile).

The file comes from the add-on's own GitHub release and must match panel_app.json (size and
SHA-256), else it is not kept. A failure does not stop the add-on from installing: it runs
without the file, and its page says so (panel_app.info()).

    python3 -m wallpanel_hub.fetch_app
"""

import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from . import panel_app
from .settings import VERSION


def fetch(url: str, expected: dict, dest: Path, timeout: int = 120) -> bool:
    """Downloads [url] to [dest] if it is the expected file. True if it is there now."""
    digest = hashlib.sha256()
    size = 0
    failed = None
    with tempfile.NamedTemporaryFile(delete=False, dir=dest.parent if dest.parent.exists() else None) as tmp:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                while chunk := resp.read(1 << 16):
                    digest.update(chunk)
                    size += len(chunk)
                    tmp.write(chunk)
        except Exception as e:
            failed = e
    if failed is not None:
        print(f"panel app not fetched from {url}: {failed}")
        Path(tmp.name).unlink(missing_ok=True)
        return False
    if size != expected.get("size") or digest.hexdigest() != expected.get("sha256"):
        print(f"panel app from {url} is not the expected one (size {size}): not kept")
        Path(tmp.name).unlink(missing_ok=True)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(tmp.name, dest)
    return True


def main() -> int:
    app = panel_app.expected()
    if not app:
        print("no panel_app.json: the add-on is built without the panel app")
        return 0
    url = panel_app.release_url(VERSION, app["version_name"])
    if fetch(url, app, panel_app.BUNDLED):
        print(f"panel app {app['version_name']} fetched")
    # Never a failed build: the add-on works without the file.
    return 0


if __name__ == "__main__":
    sys.exit(main())
