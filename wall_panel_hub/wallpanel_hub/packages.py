"""Other apps on the panels, managed from the add-on (docs/kiosk-plan.md, K6).

The hub keeps intent only: "this app, at this version, on these panels, with these options". Each
panel gets its list (wallpanel/packages/subscribe), compares it with what it has and does the
difference, so a panel off or away catches up when it is back. It says how each app is there
(wallpanel/packages/status), kept in memory: the panel says it again when it connects.

An entry with no files manages an app the panels have already (a vendor's): its options only. One
being removed is uninstalled from its panels, and dropped once each says the app is gone.

The list is in packages.json (in the project's backup); the files under apps/<package>/<code>/,
out of the backup for their size. After a restore an app may have no file here: its panels keep what
they have, and the page asks for it again.
"""

import asyncio
import hashlib
import logging
import re
import shutil
import time
import uuid
from pathlib import Path

from . import apk
from .files import read_json, write_json

log = logging.getLogger(__name__)

OURS = "com.smartelectricity.wallpanel"
MAX_SIZE = 300 * 1024 * 1024
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
SPLIT = re.compile(r"[A-Za-z0-9_.]{1,100}")
OPTIONS = ("block_uninstall", "grant", "hidden", "suspended")
STATES = ("installed", "absent", "downloading", "installing", "waiting", "failed")
# The first panel app that reads its list: an older one is told nothing and must be updated.
MIN_APP_CODE = 219


class Refused(ValueError):
    """Said to the installer as it is; nothing was changed."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _read_app(paths: list[Path], into: Path) -> dict:
    """The app in [paths] (one .apk, splits, or a bundle): its files moved into [into], named
    base.apk and <split>.apk. Blocking: run off the event loop."""
    apks: list[Path] = []
    for p in paths:
        if p.suffix.lower() in (".apks", ".xapk", ".zip"):
            apks += apk.unpack(p, p.parent, MAX_SIZE)
        else:
            apks.append(p)
    read = [(p, apk.read(p)) for p in apks]
    first = read[0][1]
    for p, a in read:
        if (a["package"], a["version_code"]) != (first["package"], first["version_code"]):
            raise Refused(f"the files are not one app: {first['package']} {first['version_code']} and "
                          f"{a['package']} {a['version_code']}")
    bases = [a for _, a in read if a["split"] is None]
    if len(bases) != 1:
        raise Refused("the app needs its base .apk, once" if not bases else "two base .apk files for one app")
    splits = [a["split"] for _, a in read if a["split"]]
    if len(set(splits)) != len(splits) or not all(SPLIT.fullmatch(s) for s in splits):
        raise Refused("the app's split files are not what Android makes")
    into.mkdir(parents=True, exist_ok=True)
    files = []
    for p, a in read:
        name = f"{a['split']}.apk" if a["split"] else "base.apk"
        dest = into / name
        shutil.move(str(p), dest)
        files.append({"name": name, "split": a["split"], "size": dest.stat().st_size, "sha256": _sha256(dest)})
    base = bases[0]
    return {"package": base["package"], "version_code": base["version_code"], "version_name": base["version_name"][:40],
            "min_sdk": base["min_sdk"], "files": sorted(files, key=lambda f: (f["split"] is not None, f["name"]))}


class Packages:
    def __init__(self, data_dir: Path, store):
        self.store = store
        self.file = data_dir / "packages.json"
        self.dir = data_dir / "apps"
        self.apps: list[dict] = []
        self.load()
        # How each app is on each panel, as the panel said: {panel: {package: {state, ...}}}.
        self.status: dict[str, dict[str, dict]] = {}
        self._lock = asyncio.Lock()
        # Told when a panel's list changed (Gateway.notify_packages).
        self.changed = None

    # ---- the list ----

    def load(self) -> None:
        """From packages.json: at start, and after a backup is restored."""
        doc = read_json(self.file)
        apps = doc.get("apps") if isinstance(doc, dict) else None
        self.apps = [a for a in apps or [] if isinstance(a, dict) and isinstance(a.get("package"), str)
                     and PACKAGE.fullmatch(a["package"])]

    def _save(self) -> None:
        write_json(self.file, {"apps": self.apps})

    def get(self, package: str) -> dict | None:
        return next((a for a in self.apps if a["package"] == package), None)

    def folder(self, entry: dict) -> Path:
        return self.dir / entry["package"] / str(entry["version_code"])

    def has_files(self, entry: dict) -> bool:
        return bool(entry.get("files")) and all((self.folder(entry) / f["name"]).is_file() for f in entry["files"])

    def targets(self, entry: dict) -> list[str]:
        """The panels [entry] is for, of those paired now."""
        panels = entry.get("panels")
        return [pid for pid in self.store.panels if panels is None or pid in panels]

    def for_panel(self, panel_id: str) -> list[dict]:
        """The panel's list: what it should have, what it should remove."""
        out = []
        for e in self.apps:
            if panel_id not in self.targets(e):
                continue
            if e.get("remove"):
                out.append({"package": e["package"], "remove": True})
                continue
            item = {"package": e["package"], "options": {k: bool(e.get("options", {}).get(k)) for k in OPTIONS}}
            # With no file here (a vendor's app, or after a restore): its options only.
            if self.has_files(e):
                item |= {k: e[k] for k in ("version_code", "version_name", "min_sdk")}
                item["files"] = [{k: f[k] for k in ("name", "split", "size", "sha256")} for f in e["files"]]
            out.append(item)
        return out

    def file_for(self, panel_id: str, package: str, name: str) -> Path | None:
        """A file the panel may download: of an app on its list, with its key."""
        e = self.get(package)
        if e is None or e.get("remove") or panel_id not in self.targets(e) or not self.has_files(e):
            return None
        return self.folder(e) / name if any(f["name"] == name for f in e["files"]) else None

    async def _tell(self, panels: list[str] | None = None) -> None:
        if self.changed is not None:
            await self.changed(panels)

    # ---- from the add-on's page ----

    async def upload(self, paths: list[Path], work: Path) -> dict:
        """A new app, or a newer version of one: replaces the files, keeps its panels and options."""
        async with self._lock:
            try:
                found = await asyncio.to_thread(_read_app, paths, work / "app")
            except apk.NotAnApp as e:
                raise Refused(str(e)) from e
            pkg = found["package"]
            if not PACKAGE.fullmatch(pkg):
                raise Refused(f"{pkg!r} is not a package name")
            if pkg == OURS:
                raise Refused("this is the panel app itself: it updates from the panel's page")
            old = self.get(pkg)
            if old and old.get("version_code") and found["version_code"] < old["version_code"]:
                raise Refused(f"older than the version here ({old['version_name']})")
            entry = {**(old or {"package": pkg, "panels": None, "options": {}}), **found, "remove": False,
                     "added": int(time.time())}
            dest = self.folder(entry)
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(work / "app"), dest)
            for other in dest.parent.iterdir():
                if other != dest:
                    shutil.rmtree(other, ignore_errors=True)
            self.apps = [a for a in self.apps if a["package"] != pkg] + [entry]
            self._save()
            log.info("app %s %s (%s) uploaded, %d file(s)", pkg, entry["version_name"], entry["version_code"], len(entry["files"]))
        await self._tell()
        return self.view(entry)

    async def manage(self, package: str) -> dict:
        """An app the panels already have, for its options: no file."""
        if not PACKAGE.fullmatch(package) or package == OURS:
            raise Refused("not an app to manage")
        if self.get(package) is not None:
            raise Refused("this app is managed already")
        entry = {"package": package, "panels": None, "options": {}, "files": [], "remove": False, "added": int(time.time())}
        self.apps.append(entry)
        self._save()
        await self._tell()
        return self.view(entry)

    async def update(self, package: str, body: dict) -> dict:
        """Its panels (all, or a list) and options."""
        e = self.get(package)
        if e is None or e.get("remove"):
            raise Refused("no such app")
        before = self.targets(e)
        if "panels" in body:
            panels = body["panels"]
            if panels is not None and (not isinstance(panels, list) or not all(isinstance(p, str) for p in panels)):
                raise Refused("panels: all, or a list of panels")
            e["panels"] = None if panels is None else sorted(set(panels) & set(self.store.panels))
        if isinstance(body.get("options"), dict):
            e["options"] = {k: bool(body["options"].get(k)) for k in OPTIONS}
        self._save()
        await self._tell(sorted(set(before) | set(self.targets(e))))
        return self.view(e)

    async def set_panel(self, package: str, panel_id: str, on: bool) -> None:
        """One panel in or out of an app's panels: from the panel itself (docs/kiosk-plan.md K6, decision 6)."""
        e = self.get(package)
        if e is None or e.get("remove"):
            raise Refused("no such app")
        if on == (panel_id in self.targets(e)):
            return
        panels = set(self.targets(e)) if e.get("panels") is None else set(e["panels"])
        e["panels"] = sorted(panels | {panel_id} if on else panels - {panel_id})
        self._save()
        await self._tell([panel_id])

    def for_settings(self, panel_id: str, names: dict[str, str]) -> list[dict]:
        """The managed apps, for a panel's own settings page: whether each is on it, and how."""
        out = []
        for e in sorted(self.apps, key=lambda a: names.get(a["package"], a["package"]).lower()):
            if e.get("remove"):
                continue
            on = panel_id in self.targets(e)
            state = self.on_panel(e, panel_id)["state"] if on else None
            if state == "installed" and e.get("version_code") and                     (self.status.get(panel_id, {}).get(e["package"], {}).get("version_code") or 0) < e["version_code"]:
                state = "pending"
            out.append({"package": e["package"], "name": names.get(e["package"], e["package"]),
                        "version_name": e.get("version_name"), "on": on, "state": state})
        return out

    async def remove(self, package: str) -> dict:
        """Uninstalled from its panels, its data with it. Its files go now."""
        e = self.get(package)
        if e is None:
            raise Refused("no such app")
        e["panels"] = self.targets(e)
        e["remove"] = True
        e["files"] = []
        shutil.rmtree(self.dir / package, ignore_errors=True)
        self._drop_removed()
        self._save()
        await self._tell(e["panels"])
        return {"removed": package}

    async def forget(self, package: str) -> None:
        """No longer managed: left as it is on the panels, its options undone there."""
        e = self.get(package)
        if e is None:
            return
        panels = self.targets(e)
        self.apps = [a for a in self.apps if a["package"] != package]
        shutil.rmtree(self.dir / package, ignore_errors=True)
        self._save()
        await self._tell(panels)

    # ---- from the panels ----

    def reported(self, panel_id: str, package: str, state: str, version_code: int | None,
                 version_name: str | None, detail: str | None) -> None:
        if state not in STATES or not PACKAGE.fullmatch(package):
            return
        self.status.setdefault(panel_id, {})[package] = {
            "state": state, "version_code": version_code, "version_name": version_name, "detail": detail}
        if state == "absent" and self._drop_removed():
            self._save()

    def _drop_removed(self) -> bool:
        """Removed apps every panel of which says they are gone."""
        done = [a for a in self.apps if a.get("remove") and all(
            self.status.get(pid, {}).get(a["package"], {}).get("state") == "absent" for pid in self.targets(a))]
        self.apps = [a for a in self.apps if a not in done]
        return bool(done)

    # ---- for the page ----

    def on_panel(self, entry: dict, panel_id: str) -> dict:
        """How [entry] is on a panel: the panel's word, or what the hub knows without it."""
        panel = self.store.panels.get(panel_id)
        said = self.status.get(panel_id, {}).get(entry["package"])
        if said:
            return said
        if (getattr(panel, "app_version_code", None) or 0) < MIN_APP_CODE:
            return {"state": "old_app"}
        if not getattr(panel, "device_owner", False) and entry.get("files"):
            return {"state": "not_owner"}
        return {"state": "pending"}

    def view(self, entry: dict) -> dict:
        size = sum(f["size"] for f in entry.get("files") or [])
        return {
            **{k: entry.get(k) for k in ("package", "version_name", "version_code", "min_sdk", "panels", "remove", "added")},
            "options": {k: bool(entry.get("options", {}).get(k)) for k in OPTIONS},
            "files": len(entry.get("files") or []),
            "size": size,
            "managed_only": not entry.get("files") and not entry.get("version_code"),
            "missing": bool(entry.get("version_code")) and not entry.get("remove") and not self.has_files(entry),
            "on": {pid: self.on_panel(entry, pid) for pid in self.targets(entry)},
        }

    def views(self) -> list[dict]:
        return [self.view(e) for e in sorted(self.apps, key=lambda a: a["package"])]


def work_dir(data_dir: Path) -> Path:
    """A folder for one upload, under the data folder so a move is a rename; gone after."""
    d = data_dir / "apps" / f".upload-{uuid.uuid4().hex}"
    d.mkdir(parents=True)
    return d
