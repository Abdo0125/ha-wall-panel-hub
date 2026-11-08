"""The hub's JSON files under /data: written whole, the one before kept, a damaged one set aside.

A file cut short (a power cut, a full disk) never stops the hub: it moves aside and the copy before
it comes back, so at most its last change is lost. What was set aside stays for the add-on's page
to say (Store.set_aside).
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BACKUP = ".bak"
DAMAGED = ".corrupt"


def _beside(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def _readable(data: bytes) -> bool:
    try:
        json.loads(data)
    except ValueError:
        return False
    return True


def _put(path: Path, data: bytes, private: bool) -> None:
    """On disk before it takes the file's name: a power cut leaves the old file or the new one."""
    tmp = _beside(path, ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    if os.name == "posix":  # Windows (developer mode) cannot open a folder
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def write_bytes(path: Path, data: bytes, private: bool = False) -> None:
    """[data] as [path], on disk before it takes the name."""
    _put(path, data, private)


def write_json(path: Path, data: Any, private: bool = False) -> None:
    """[data] as [path], the file before it kept beside it. [private]: for the hub's user alone,
    for what holds passwords (AirPlay's players run as another user)."""
    try:
        before = path.read_bytes()
    except FileNotFoundError:
        before = None
    # A damaged file never replaces the good copy.
    if before is not None and _readable(before):
        _put(_beside(path, BACKUP), before, private)
    _put(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"), private)


def read_json(path: Path, private: bool = False) -> Any:
    """What [path] holds, or None when there is none. A damaged one is set aside and the copy
    before it comes back; None when there is no copy."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return json.loads(data)
    except ValueError:
        pass
    path.replace(_beside(path, DAMAGED))
    try:
        data = _beside(path, BACKUP).read_bytes()
        restored = json.loads(data)
    except (FileNotFoundError, ValueError):
        log.error("%s is damaged and has no copy: set aside, starting without it", path)
        return None
    _put(path, data, private)
    log.error("%s is damaged: set aside, its last good copy is back", path)
    return restored


def remove(path: Path) -> None:
    """[path] and its copy: what is deleted does not come back."""
    path.unlink(missing_ok=True)
    _beside(path, BACKUP).unlink(missing_ok=True)
