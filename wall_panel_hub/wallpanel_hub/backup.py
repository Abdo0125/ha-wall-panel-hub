"""The project in one file: exported from the add-on's page, and restored on this hub or another
(docs/audit-7-addon.md, item 5).

The file holds every panel, its layout, its draft and its settings, the project's settings, the
intercom's and AirPlay's. Panels stay paired when it is restored, because the hub keeps only their
keys' hashes. It also holds the doors' and SIP servers' passwords as Asterisk needs them, so the page
says to keep it safe.

Not in it: integration.json, the secret Home Assistant's integration on this machine was set up
with, and what the hub makes again (Asterisk's files, copies, files set aside).
"""

import io
import json
import re
import time
import zipfile
from dataclasses import fields
from pathlib import Path
from typing import Any

from . import config as panel_config
from .files import read_json, remove, write_bytes, write_json
from .migrate import SCHEMA
from .settings import VERSION
from .store import LayoutInvalid, Panel, readable, validate_layout

FORMAT = 1
# What the page may send, and what it may unpack to.
MAX_SIZE = 20 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024
MAX_FILES = 5000

TOP = ("panels.json", "site.json", "intercom.json", "airplay.json")
FOLDERS = ("config", "layouts", "drafts")
NAME = re.compile(r"^(?:(?:panels|site|intercom|airplay)\.json|(?:config|layouts|drafts)/[A-Za-z0-9_-]+\.json)$")
# The files only the hub's own user may read: they hold passwords.
PRIVATE = {"intercom.json", "airplay.json"}
# What was here before the last import, to go back to.
BEFORE = "before-import.zip"


class BackupInvalid(ValueError):
    """Not a backup this add-on can restore; nothing was changed."""


def _entries(data_dir: Path) -> dict[str, Path]:
    found = {name: data_dir / name for name in TOP if (data_dir / name).exists()}
    for folder in FOLDERS:
        if (data_dir / folder).is_dir():
            found |= {f"{folder}/{p.name}": p for p in sorted((data_dir / folder).glob("*.json"))}
    return found


def export(data_dir: Path) -> bytes:
    """The project as a zip file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({"format": FORMAT, "add_on": VERSION, "layout_schema": SCHEMA,
                                                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=2))
        for name, path in _entries(data_dir).items():
            # A damaged file comes back from its copy, or stays out.
            doc = read_json(path, private=name in PRIVATE)
            if doc is not None:
                z.writestr(name, json.dumps(doc, ensure_ascii=False, indent=2))
    return buf.getvalue()


def _json(z: zipfile.ZipFile, name: str) -> Any:
    try:
        return json.loads(z.read(name))
    except (ValueError, KeyError) as e:
        raise BackupInvalid(f"{name} cannot be read") from e


def _check(name: str, doc: Any) -> None:
    folder = name.split("/")[0]
    try:
        if name == "panels.json":
            if not isinstance(doc, list) or not all(isinstance(p, dict) for p in doc):
                raise BackupInvalid("panels.json is not a list of panels")
            known = {f.name for f in fields(Panel)}
            for p in doc:
                Panel(**{k: v for k, v in p.items() if k in known})
        elif name == "site.json":
            panel_config.validate("site", doc)
        elif folder == "config":
            panel_config.validate("panel", doc)
        elif folder == "layouts":
            if not isinstance(doc, dict):
                raise BackupInvalid(f"{name} is not a layout")
            validate_layout({k: v for k, v in readable(doc).items() if k != "panel"})
        elif not isinstance(doc, dict):  # drafts (half done, checked when sent), intercom, AirPlay
            raise BackupInvalid(f"{name} is not what it should be")
    except BackupInvalid:
        raise
    except (LayoutInvalid, ValueError, TypeError, KeyError) as e:
        raise BackupInvalid(f"{name}: {e}") from e


def read(data: bytes) -> dict[str, Any]:
    """The documents in a backup, each checked as the hub checks it when it is saved."""
    if len(data) > MAX_SIZE:
        raise BackupInvalid("the file is too large for a backup")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise BackupInvalid("not a Wall Panel Hub backup") from e
    with z:
        infos = z.infolist()
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise BackupInvalid("the file is too large for a backup")
        names = {i.filename for i in infos}
        if "manifest.json" not in names:
            raise BackupInvalid("not a Wall Panel Hub backup")
        manifest = _json(z, "manifest.json")
        made = manifest.get("format") if isinstance(manifest, dict) else None
        if made != FORMAT:
            raise BackupInvalid(f"made by an add-on with backup format {made}; this one reads {FORMAT}")
        docs = {}
        for name in sorted(names - {"manifest.json"}):
            if not NAME.match(name):
                raise BackupInvalid(f"{name} has no place in a backup")
            docs[name] = _json(z, name)
            _check(name, docs[name])
    if "panels.json" not in docs:
        raise BackupInvalid("the backup has no panels")
    return docs


def restore(data_dir: Path, data: bytes) -> dict:
    """The project in [data] in place of this one, which is kept as BEFORE first. Nothing changes
    when [data] is not a backup this add-on can restore (BackupInvalid)."""
    docs = read(data)
    write_bytes(data_dir / BEFORE, export(data_dir), private=True)
    for path in _entries(data_dir).values():
        remove(path)
    for name, doc in docs.items():
        (data_dir / name).parent.mkdir(parents=True, exist_ok=True)
        write_json(data_dir / name, doc, private=name in PRIVATE)
    return {"panels": len(docs["panels.json"]), "layouts": sum(n.startswith("layouts/") for n in docs)}
