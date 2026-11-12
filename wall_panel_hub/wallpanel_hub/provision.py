"""Setting up a panel from the add-on over adb (docs/kiosk-plan.md, K4).

The installer types the panel's address in the management page; the hub installs the app it
carries (panel_app.py), grants what the panel needs, makes the app the device owner where Android
allows it, compiles it, starts it and pairs it, with no touch on the panel. Two things stay the
installer's, as Android wants: no account on the panel for the device owner, and adb turned on.

- Only fixed commands run: nothing the installer types reaches a shell but a checked address,
  and the pairing code, given to `adb pair` as its own argument.
- The hub's adb key stays in the add-on's data, and its server on its own port (ADB_PORT): an
  "ADB server" add-on may hold 5037 on the same host network.
- The pairing token is never logged; the panel takes it from adb only (ProvisionReceiver, held
  by the DUMP permission that apps do not get).
"""

import asyncio
import ipaddress
import logging
import os
import re
import secrets
import shutil
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import discovery, panel_app

log = logging.getLogger(__name__)

PKG = "com.smartelectricity.wallpanel"
ACTIVITY = f"{PKG}/.MainActivity"
ADMIN = f"{PKG}/.apps.PanelAdmin"
ACCESS = f"{PKG}/.apps.PanelAccess"
RECEIVER = f"{PKG}/.hub.ProvisionReceiver"
# What ProvisionReceiver answers: pairing with this hub now, or paired already (kept as it is).
PAIRING, PAIRED = 1, 2
ADB_PORT = int(os.environ.get("WALLPANEL_ADB_PORT", "5038"))
STEPS = ("connect", "read", "install", "grant", "owner", "compile", "pair")
PAIR_WAIT_S = 120
AUTH_WAIT_S = 60
MAX_JOBS = 20
# The Android emulator reaches the PC it runs on here (a hub on the PC, tools/run-hub-dev.ps1).
EMULATOR_HOST = "10.0.2.2"


class Invalid(ValueError):
    pass


class Failed(Exception):
    """A step that stops the setup, with a code the page explains and a short detail."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def address(text: str, default_port: int = 5555) -> tuple[str, int]:
    """'192.168.1.30' or '192.168.1.30:5555': an IPv4 address and a port, nothing else."""
    m = re.fullmatch(r"\s*(\d{1,3}(?:\.\d{1,3}){3})(?::(\d{1,5}))?\s*", text or "")
    if not m:
        raise Invalid("address: an IPv4 address, with :port if not 5555")
    try:
        ip = str(ipaddress.IPv4Address(m.group(1)))
    except ValueError:
        raise Invalid("address: an IPv4 address, with :port if not 5555") from None
    port = int(m.group(2) or default_port)
    if not 1 <= port <= 65535:
        raise Invalid("port: 1 to 65535")
    return ip, port


@dataclass
class Device:
    """What the hub read on the panel. Accounts by type only: their names are nobody's business here."""
    model: str = ""
    android: str = ""
    sdk: int = 0
    accounts: int = 0
    account_types: list = field(default_factory=list)
    other_owner: bool = False
    owner: bool = False
    owner_feature: bool = True
    users: int = 1
    app_version_code: int | None = None

    def owner_blocked(self) -> str | None:
        """Why Android would refuse the device owner, or None."""
        if self.owner:
            return None
        if not self.owner_feature:
            return "no_feature"
        if self.other_owner:
            return "other_owner"
        if self.accounts:
            return "accounts"
        if self.users > 1:
            return "users"
        return None


def parse_device(props: dict[str, str], accounts: str, owners: str, features: str, users: str,
                 packages: str) -> Device:
    found = set(re.findall(r"Account \{name=([^,]*), type=([^}]+)\}", accounts))
    code = None
    for line in packages.splitlines():
        m = re.fullmatch(rf"package:{re.escape(PKG)} versionCode:(\d+)", line.strip())
        if m:
            code = int(m.group(1))
    owner_lines = [l for l in owners.splitlines() if "admin=" in l]
    return Device(
        model=f"{props.get('ro.product.manufacturer', '')} {props.get('ro.product.model', '')}".strip(),
        android=props.get("ro.build.version.release", ""),
        sdk=int(props.get("ro.build.version.sdk") or 0),
        accounts=len(found),
        account_types=sorted({t for _, t in found}),
        owner=any(PKG + "/" in l for l in owner_lines),
        other_owner=any(PKG + "/" not in l for l in owner_lines),
        owner_feature="feature:android.software.device_admin" in features,
        users=max(1, users.count("UserInfo{")),
        app_version_code=code,
    )


class Adb:
    """The adb tool, run with fixed arguments. Its own server port and key folder."""

    def __init__(self, data_dir: Path, binary: str | None = None, port: int = ADB_PORT):
        self.binary = binary or os.environ.get("WALLPANEL_ADB") or shutil.which("adb")
        self.port = port
        home = data_dir / "adb"
        self.env = dict(os.environ)
        if os.name != "nt":
            # The key adb makes (home/.android/adbkey) stays with the add-on's data.
            home.mkdir(parents=True, exist_ok=True)
            self.env["HOME"] = str(home)
            self.env["ANDROID_USER_HOME"] = str(home / ".android")

    @property
    def available(self) -> bool:
        return bool(self.binary)

    async def run(self, *args: str, timeout: float = 60) -> str:
        if not self.binary:
            raise Failed("no_adb")
        proc = await asyncio.create_subprocess_exec(
            self.binary, "-P", str(self.port), *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=self.env)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise Failed("timeout", args[0] if args else "")
        return out.decode("utf-8", "replace").strip()

    async def shell(self, serial: str, command: str, timeout: float = 60) -> str:
        return await self.run("-s", serial, "shell", command, timeout=timeout)


@dataclass
class Job:
    id: str
    address: str
    name: str
    owner: bool
    replace: bool = False
    started: float = field(default_factory=time.time)
    # Per step: pending, running, done, skipped, failed; and a code the page explains.
    steps: dict = field(default_factory=lambda: {s: {"state": "pending"} for s in STEPS})
    device: dict | None = None
    state: str = "running"   # running, done, failed
    panel_id: str | None = None
    kept: bool = False       # the panel was paired already: its pairing was kept

    def view(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "started"}


class Provisioner:
    def __init__(self, store, panel_port: int, adb: Adb, tls=lambda: {}):
        self.store = store
        self.panel_port = panel_port
        # The encrypted port and the hub's pin (Gateway.tls_offer): handed to the panel over adb, so its
        # trust in this hub never crosses the network (docs/lan-tls-plan.md, 5.1).
        self.tls = tls
        self.adb = adb
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()

    def start(self, body: dict) -> Job:
        """A setup from the management page: {address, pair_address?, pair_code?, name?, area_id?, owner?, replace?}."""
        ip, port = address(str(body.get("address", "")))
        pair = None
        code = str(body.get("pair_code") or "").strip()
        if code:
            if not re.fullmatch(r"\d{6}", code):
                raise Invalid("pairing code: the 6 digits shown on the panel")
            pair = address(str(body.get("pair_address") or ""), default_port=0)
            if pair[1] == 0:
                raise Invalid("pairing address: the address and port shown with the code")
        if not self.adb.available:
            raise Invalid("adb is not in this add-on")
        if any(j.state == "running" and j.address == f"{ip}:{port}" for j in self.jobs.values()):
            raise Invalid("this panel is being set up already")
        job = Job(secrets.token_urlsafe(8), f"{ip}:{port}", str(body.get("name") or "").strip()[:60],
                  owner=body.get("owner") is not False, replace=body.get("replace") is True)
        while len(self.jobs) >= MAX_JOBS:
            del self.jobs[min(self.jobs.values(), key=lambda j: j.started).id]
        self.jobs[job.id] = job
        area = body.get("area_id")
        task = asyncio.create_task(self._run(job, pair, code, area if isinstance(area, str) else None))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    # ---- the steps ----

    def _step(self, job: Job, name: str, state: str, code: str = "", detail: str = "") -> None:
        job.steps[name] = {"state": state, **({"code": code} if code else {}), **({"detail": detail[:200]} if detail else {})}

    async def _run(self, job: Job, pair, code: str, area_id: str | None) -> None:
        current = "connect"
        try:
            for current, step in (("connect", lambda: self._connect(job, pair, code)),
                                  ("read", lambda: self._read(job)),
                                  ("install", lambda: self._install(job)),
                                  ("grant", lambda: self._grant(job)),
                                  ("owner", lambda: self._owner(job)),
                                  ("compile", lambda: self._compile(job)),
                                  ("pair", lambda: self._pair(job, area_id))):
                self._step(job, current, "running")
                result = await step()
                if job.steps[current]["state"] == "running":
                    self._step(job, current, "done", *(result or ()))
            job.state = "done"
            log.info("panel at %s set up from the add-on", job.address)
        except Failed as e:
            self._step(job, current, "failed", e.code, e.detail)
            job.state = "failed"
            log.warning("setting up %s stopped at %s: %s", job.address, current, e.code)
        except Exception as e:  # never left running
            self._step(job, current, "failed", "error", str(e))
            job.state = "failed"
            log.exception("setting up %s failed", job.address)
        finally:
            # The hub keeps no link to the panel after its setup.
            try:
                await self.adb.run("disconnect", job.address, timeout=10)
            except Failed:
                pass

    async def _connect(self, job: Job, pair, code: str):
        if pair:
            out = await self.adb.run("pair", f"{pair[0]}:{pair[1]}", code, timeout=30)
            if "Successfully paired" not in out:
                raise Failed("pair_refused", out.splitlines()[-1] if out else "")
        out = await self.adb.run("connect", job.address, timeout=20)
        if "connected to" not in out:
            raise Failed("unreachable", out.splitlines()[-1] if out else "")
        # Android may ask on the panel to allow this hub ("Allow USB debugging?"), once.
        until = time.monotonic() + AUTH_WAIT_S
        while True:
            state = await self.adb.run("-s", job.address, "get-state", timeout=10)
            if state.strip() == "device":
                return None
            if time.monotonic() > until:
                raise Failed("not_allowed" if "unauthorized" in state else "unreachable", state)
            self._step(job, "connect", "running", "allow_on_panel" if "unauthorized" in state else "")
            await asyncio.sleep(2)

    async def _read(self, job: Job):
        s = job.address
        props = {}
        for key in ("ro.product.manufacturer", "ro.product.model", "ro.build.version.release", "ro.build.version.sdk"):
            props[key] = await self.adb.shell(s, f"getprop {key}")
        device = parse_device(
            props,
            accounts=await self.adb.shell(s, "dumpsys account"),
            owners=await self.adb.shell(s, "dpm list-owners"),
            features=await self.adb.shell(s, "pm list features"),
            users=await self.adb.shell(s, "pm list users"),
            packages=await self.adb.shell(s, f"pm list packages --show-versioncode {PKG}"),
        )
        job.device = asdict(device)
        if not job.name:
            job.name = device.model[:60]
        return None

    async def _install(self, job: Job):
        app = panel_app.expected() or {}
        apk, dm = panel_app.path(), panel_app.dm_path()
        have = job.device.get("app_version_code")
        if have is not None and have >= (app.get("version_code") or 0):
            self._step(job, "install", "skipped", "up_to_date")
            return None
        if apk is None:
            raise Failed("no_app")
        if app.get("min_sdk") and 0 < job.device.get("sdk", 0) < app["min_sdk"]:
            raise Failed("android_too_old", job.device.get("android") or str(job.device["sdk"]))
        if job.replace and have is not None:
            out = await self.adb.run("-s", job.address, "uninstall", PKG, timeout=60)
            if "Success" not in out:
                raise Failed("not_removed", out.splitlines()[-1] if out else "")
        args = ["install-multiple", "-r", str(apk), str(dm)] if dm else ["install", "-r", str(apk)]
        out = await self.adb.run("-s", job.address, *args, timeout=300)
        if "Success" not in out:
            failure = re.search(r"INSTALL_FAILED_[A-Z_]+", out)
            if failure and failure.group(0) == "INSTALL_FAILED_UPDATE_INCOMPATIBLE":
                raise Failed("signed_differently")
            raise Failed("install_failed", failure.group(0) if failure else (out.splitlines()[-1] if out else ""))
        job.device["app_version_code"] = app.get("version_code")
        return None

    async def _grant(self, job: Job):
        s = job.address
        missed = []
        for command in (f"pm grant {PKG} android.permission.RECORD_AUDIO",
                        f"appops set {PKG} SYSTEM_ALERT_WINDOW allow",
                        f"appops set {PKG} REQUEST_INSTALL_PACKAGES allow",
                        f"cmd package set-home-activity {ACTIVITY}"):
            out = await self.adb.shell(s, command)
            if "Exception" in out or "Error" in out or "Unknown" in out:
                missed.append(command.split()[-2] if command.startswith("appops") else command.split()[-1])
        # The accessibility service (apps/PanelAccess.kt), kept beside any other already on.
        services = (await self.adb.shell(s, "settings get secure enabled_accessibility_services")).strip()
        on = [x for x in services.split(":") if x and x != "null"]
        if ACCESS not in on:
            await self.adb.shell(s, "settings put secure enabled_accessibility_services " + ":".join(on + [ACCESS]))
        await self.adb.shell(s, "settings put secure accessibility_enabled 1")
        return ("partly", ", ".join(missed)) if missed else None

    async def _owner(self, job: Job):
        if not job.owner:
            self._step(job, "owner", "skipped", "not_asked")
            return None
        device = Device(**job.device)
        if device.owner:
            self._step(job, "owner", "skipped", "already")
            return None
        blocked = device.owner_blocked()
        if blocked:
            # The basic lock still applies: the setup goes on.
            self._step(job, "owner", "skipped", blocked,
                       ", ".join(device.account_types) if blocked == "accounts" else "")
            return None
        out = await self.adb.shell(job.address, f"dpm set-device-owner {ADMIN}")
        if "Success" not in out:
            self._step(job, "owner", "skipped", "refused", out.splitlines()[-1] if out else "")
            return None
        job.device["owner"] = True
        return None

    async def _compile(self, job: Job):
        # Right after an install the profile is empty: speed, not speed-profile (docs/audit-8-app.md, item 1).
        out = await self.adb.shell(job.address, f"cmd package compile -m speed -f {PKG}", timeout=300)
        if "Success" not in out:
            return ("partly", out.splitlines()[-1] if out else "")
        return None

    async def _pair(self, job: Job, area_id: str | None):
        s = job.address
        await self.adb.shell(s, f"am start -n {ACTIVITY}")
        token = self.store.new_provision(job.name, area_id)
        hub = f"http://{self.hub_address(job.address.split(':')[0])}:{self.panel_port}"
        offer = self.tls()
        pinned = f" --es pin {offer['spki']} --ei tls_port {offer['tls_port']}" if offer else ""
        # The token goes to the panel only, never to the log.
        out = await self.adb.shell(s, f"am broadcast -n {RECEIVER} --es hub {hub} --es token {token}{pinned}")
        m = re.search(r"result=(-?\d+)", out)
        answer = int(m.group(1)) if m else None
        if answer == PAIRED:
            job.kept = True
            self._step(job, "pair", "done", "kept")
            return None
        if answer != PAIRING:
            raise Failed("no_answer")
        until = time.monotonic() + PAIR_WAIT_S
        while time.monotonic() < until:
            panel_id = self.store.provisioned(token)
            if panel_id:
                job.panel_id = panel_id
                return None
            await asyncio.sleep(1)
        raise Failed("not_paired")

    @staticmethod
    def hub_address(device_ip: str) -> str:
        """This hub's address as the panel at [device_ip] reaches it."""
        if ipaddress.IPv4Address(device_ip).is_loopback:
            return EMULATOR_HOST
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((device_ip, 1))
            return sock.getsockname()[0]

    # ---- the network, for the page's search ----

    async def scan(self, port: int = 5555) -> list[str]:
        """Addresses on this hub's /24 with [port] open: the panels adb may reach. Asked by the page only."""
        own = discovery.lan_address()
        net = ipaddress.IPv4Network(f"{own}/24", strict=False)

        async def probe(ip: str) -> str | None:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), 0.6)
                writer.close()
                return ip
            except (OSError, asyncio.TimeoutError):
                return None

        found = await asyncio.gather(*(probe(str(h)) for h in net.hosts() if str(h) != own))
        return [f"{ip}:{port}" if port != 5555 else ip for ip in found if ip]
