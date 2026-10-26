"""AirPlay: each panel with it turned on is an AirPlay speaker on the home network, named after it.

shairport-sync runs for it here (one process a panel, as the unprivileged user "airplay"),
announced by avahi on the home network's interfaces only (not HA's or Docker's, not a VPN's);
an AirPlay password may be set for each panel. What it receives (16-bit stereo PCM) is passed
on to the panel at /api/airplay/<token>, a token made for each session and handed to that panel
only, played as HA's sounds are; the panel's media_player in HA shows it ("AirPlay": its title,
artist, album and cover). What plays is not written to the log nor kept on disk.

The panel's other sounds come first: HA playing something on it, or stopping or pausing it,
ends the AirPlay session (the sender is cut off); an announcement only lowers it.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import struct
import time
from pathlib import Path

from aiohttp import web

log = logging.getLogger(__name__)

RUN = Path("/run/wallpanel-airplay")
USER = "airplay"
# Each panel's ports, by its slot: RAOP (TCP), its UDP ports, its metadata (UDP, loopback only).
TCP_BASE = 51000
UDP_BASE = 51100
UDP_RANGE = 10
META_BASE = 51600
MAX_SLOTS = 50
# shairport-sync's output: 44.1 kHz, 16-bit, stereo.
RATE = 44100
CHANNELS = 2
BYTES_PER_S = RATE * CHANNELS * 2
# A paused session keeps the panel's player fed with silence (its connection would time out).
SILENCE_AFTER_S = 1.0
SILENCE_STEP_S = 0.2
# What a slow panel may fall behind before the oldest is dropped (~4 s).
QUEUE_CHUNKS = 100
PASSWORD = re.compile(r"[^\"\\\r\n]{0,64}")
# Interfaces never announced on: loopback, HA's and Docker's, VPNs.
NOT_HOME = ("lo", "docker", "hassio", "veth", "tailscale", "wg", "zt", "tun", "tap")


class AirPlayInvalid(ValueError):
    pass


def wav_header() -> bytes:
    """A WAV header for a stream of unknown length (the largest size: ~6 h at 44.1 kHz)."""
    size = 0xFFFFFFFF
    return (b"RIFF" + struct.pack("<I", size) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE, BYTES_PER_S, CHANNELS * 2, 16)
            + b"data" + struct.pack("<I", size - 36))


def quote(value: str) -> str:
    """A libconfig string."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def shairport_conf(name: str, slot: int, password: str | None, interface: str | None) -> str:
    general = [f"name = {quote(name)};", 'output_backend = "stdout";', 'mdns_backend = "avahi";',
               f"port = {TCP_BASE + slot};", f"udp_port_base = {UDP_BASE + slot * UDP_RANGE};",
               f"udp_port_range = {UDP_RANGE};", 'interpolation = "basic";',
               # The volume is the panel's own (HA's media_player shows it).
               'ignore_volume_control = "yes";']
    if password:
        general.append(f"password = {quote(password)};")
    if interface:
        general.append(f"interface = {quote(interface)};")
    return "\n".join([
        "general = {", *("  " + g for g in general), "};",
        "sessioncontrol = {", '  allow_session_interruption = "yes";', "  session_timeout = 20;", "};",
        # What plays, to the hub only (loopback); the cover kept in memory, not cached on disk.
        "metadata = {", '  enabled = "yes";', '  include_cover_art = "yes";', '  cover_art_cache_directory = "";',
        '  socket_address = "127.0.0.1";', f"  socket_port = {META_BASE + slot};", "  socket_msglength = 65000;", "};",
        "diagnostics = {", "  log_verbosity = 0;", "};", "",
    ])


def avahi_conf(interfaces: list[str]) -> str:
    """Announces the AirPlay speakers only: no host details, no reflector, no wide area."""
    allow = [f"allow-interfaces={','.join(interfaces)}"] if interfaces else []
    return "\n".join([
        "[server]", "host-name=wallpanel-hub", "use-ipv4=yes", "use-ipv6=no", *allow,
        "ratelimit-interval-usec=1000000", "ratelimit-burst=1000",
        "[wide-area]", "enable-wide-area=no",
        "[publish]", "publish-hinfo=no", "publish-workstation=no", "publish-domain=no", "publish-addresses=yes",
        "publish-aaaa-on-ipv4=no", "publish-a-on-ipv6=no",
        "[reflector]", "enable-reflector=no", "",
    ])


def home_interfaces(ip_addr: str) -> list[str]:
    """The home network's interfaces, from `ip -4 -o addr`."""
    found = []
    for name, address in re.findall(r"^\d+:\s+(\S+)\s+inet (\d+\.\d+\.\d+\.\d+)/", ip_addr, re.M):
        name = name.split("@")[0]
        if name.startswith(NOT_HOME) or address.startswith(("127.", "172.30.", "172.17.")):
            continue
        if name not in found:
            found.append(name)
    return found


def volume_level(pvol: str) -> float | None:
    """AirPlay's volume ("-30.0 .. 0.0", -144 for silent, first of pvol's fields) as 0..1."""
    try:
        level = float(pvol.split(",")[0])
    except ValueError:
        return None
    if level <= -144:
        return 0.0
    return round(min(max((level + 30) / 30, 0.0), 1.0), 3)


class Metadata:
    """shairport-sync's metadata packets (UDP): "type" "code" data, or chunks of a large item."""

    def __init__(self, on_item):
        self.on_item = on_item
        self.chunks: dict[tuple[bytes, bytes], list] = {}

    def packet(self, data: bytes) -> None:
        if len(data) < 8:
            return
        kind, code = data[:4], data[4:8]
        if kind == b"ssnc" and code == b"chnk":
            if len(data) < 24:
                return
            index, count = struct.unpack(">II", data[8:16])
            key = (data[16:20], data[20:24])
            if count == 0 or count > 1000 or index >= count:
                return
            parts = self.chunks.setdefault(key, [None] * count)
            if len(parts) != count:
                parts = self.chunks[key] = [None] * count
            parts[index] = data[24:]
            if all(p is not None for p in parts):
                del self.chunks[key]
                self.on_item(key[0], key[1], b"".join(parts))
            return
        self.on_item(kind, code, data[8:])


class _MetaSocket(asyncio.DatagramProtocol):
    def __init__(self, metadata: Metadata):
        self.metadata = metadata

    def datagram_received(self, data, addr) -> None:
        self.metadata.packet(data)


class Session:
    """One AirPlay session of a panel: its token, and what plays."""

    def __init__(self):
        self.token = secrets.token_hex(16)
        self.readers: set[asyncio.Queue] = set()
        self.paused = False
        self.heard_at = time.monotonic()

    def feed(self, data: bytes) -> None:
        self.heard_at = time.monotonic()
        for q in self.readers:
            if q.full():
                q.get_nowait()
            q.put_nowait(data)

    def close(self) -> None:
        for q in self.readers:
            if q.full():
                q.get_nowait()
            q.put_nowait(None)


class Speaker:
    """A panel's shairport-sync, and its session."""

    def __init__(self, panel_id: str, slot: int):
        self.panel_id = panel_id
        self.slot = slot
        self.conf = ""
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.transport = None
        self.session: Session | None = None
        self.now: dict = {}
        self.art: tuple[str, str, bytes] | None = None
        # The sender's volume, as last told (it may come before the sound).
        self.volume: float | None = None
        self.metadata = Metadata(lambda kind, code, data: None)


class AirPlay:
    def __init__(self, data_dir: Path, store, speaker_command, changed, art_changed):
        self.file = data_dir / "airplay.json"
        self.store = store
        self.speaker_command = speaker_command
        self.changed = changed
        self.art_changed = art_changed
        self.binary = shutil.which("shairport-sync")
        self.settings: dict = {"panels": {}}
        if self.file.exists():
            try:
                self.settings = {**self.settings, **json.loads(self.file.read_text(encoding="utf-8"))}
            except ValueError:
                log.warning("AirPlay settings not readable: starting with none")
        self.speakers: dict[str, Speaker] = {}
        self.daemons: list[asyncio.subprocess.Process] = []
        self.lock = asyncio.Lock()
        self._notify: asyncio.TimerHandle | None = None

    # ---- settings ----

    def _save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.settings, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.file)

    def view(self, panel_id: str) -> dict:
        own = self.settings["panels"].get(panel_id) or {}
        sp = self.speakers.get(panel_id)
        state = ("off" if not own.get("enabled") else "unavailable" if not self.binary
                 else "playing" if sp and sp.session and not sp.session.paused
                 else "paused" if sp and sp.session else "ready" if sp and sp.process and sp.process.returncode is None
                 else "starting")
        return {"enabled": bool(own.get("enabled")), "password_set": bool(own.get("password")), "state": state}

    def update(self, panel_id: str, changes: dict) -> None:
        if panel_id not in self.store.panels:
            raise AirPlayInvalid("unknown panel")
        own = dict(self.settings["panels"].get(panel_id) or {})
        if "enabled" in changes:
            own["enabled"] = bool(changes["enabled"])
        if "password" in changes:
            password = str(changes["password"] or "")
            if not PASSWORD.fullmatch(password):
                raise AirPlayInvalid("the AirPlay password: at most 64 characters, without \" \\ or line breaks")
            own["password"] = password
        if "slot" not in own:
            used = {p.get("slot") for p in self.settings["panels"].values()}
            own["slot"] = next((s for s in range(MAX_SLOTS) if s not in used), None)
            if own["slot"] is None:
                raise AirPlayInvalid(f"at most {MAX_SLOTS} panels with AirPlay")
        self.settings["panels"][panel_id] = own
        self._save()

    def forget(self, panel_id: str) -> None:
        """An unpaired panel: no longer an AirPlay speaker."""
        if self.settings["panels"].pop(panel_id, None) is not None:
            self._save()

    # ---- what HA sees ----

    def active(self, panel_id: str) -> bool:
        sp = self.speakers.get(panel_id)
        return bool(sp and sp.session)

    def overlay(self, panel_id: str, state: dict | None) -> dict | None:
        """The panel's speaker state, with AirPlay's while a session plays on it."""
        sp = self.speakers.get(panel_id)
        if state is None or not (sp and sp.session):
            return state
        return {**state, "state": "paused" if sp.session.paused else "playing", "source": "AirPlay",
                "title": sp.now.get("title"), "artist": sp.now.get("artist"), "album": sp.now.get("album"),
                "art": sp.art[0] if sp.art else None}

    def arts(self) -> list[tuple[str, tuple[str, str, bytes]]]:
        return [(pid, sp.art) for pid, sp in self.speakers.items() if sp.session and sp.art]

    def _changed_soon(self) -> None:
        """Metadata comes in bursts: HA told once they settle."""
        if self._notify:
            self._notify.cancel()
        self._notify = asyncio.get_running_loop().call_later(0.3, lambda: asyncio.ensure_future(self.changed()))

    # ---- running ----

    def wanted(self) -> dict[str, dict]:
        return {pid: own for pid, own in self.settings["panels"].items()
                if own.get("enabled") and pid in self.store.panels and own.get("slot") is not None}

    async def apply(self) -> None:
        """Runs what the settings ask for: a speaker for each panel with AirPlay on, avahi while there is one."""
        async with self.lock:
            wanted = self.wanted() if self.binary else {}
            for pid in [p for p in self.speakers if p not in wanted]:
                await self._stop(self.speakers.pop(pid))
            if not wanted:
                await self._stop_daemons()
                return
            interfaces = home_interfaces(await _ip_addr())
            if not self.daemons:
                await self._start_daemons(interfaces)
            for pid, own in wanted.items():
                name = self.store.panels[pid].name or "Wall panel"
                conf = shairport_conf(name, own["slot"], own.get("password") or None,
                                      interfaces[0] if len(interfaces) == 1 else None)
                sp = self.speakers.get(pid)
                if sp and sp.conf == conf and sp.task and not sp.task.done():
                    continue
                if sp:
                    await self._stop(sp)
                sp = self.speakers[pid] = Speaker(pid, own["slot"])
                sp.conf = conf
                await self._start(sp)

    async def stop(self) -> None:
        async with self.lock:
            for sp in list(self.speakers.values()):
                await self._stop(sp)
            self.speakers.clear()
            await self._stop_daemons()

    async def kick(self, panel_id: str) -> None:
        """HA's own sound on the panel: the AirPlay sender is cut off (its process started again)."""
        async with self.lock:
            sp = self.speakers.get(panel_id)
            if sp is None:
                return
            log.info("AirPlay on %s: ended for Home Assistant's sound", panel_id)
            await self._stop(sp, tell_panel=False)
            fresh = self.speakers[panel_id] = Speaker(panel_id, sp.slot)
            fresh.conf = sp.conf
            await self._start(fresh)

    async def _start_daemons(self, interfaces: list[str]) -> None:
        RUN.mkdir(parents=True, exist_ok=True)
        Path("/run/dbus").mkdir(parents=True, exist_ok=True)
        Path("/run/avahi-daemon").mkdir(parents=True, exist_ok=True)
        for stale in (Path("/run/dbus/dbus.pid"), Path("/run/dbus/pid"), Path("/run/avahi-daemon/pid")):
            stale.unlink(missing_ok=True)
        await _run("dbus-uuidgen", "--ensure")
        conf = RUN / "avahi-daemon.conf"
        conf.write_text(avahi_conf(interfaces), encoding="utf-8")
        log.info("AirPlay: announcing on %s", ", ".join(interfaces) or "every interface")
        for argv in (("dbus-daemon", "--system", "--nofork", "--nopidfile"),
                     ("avahi-daemon", "--no-chroot", "--no-rlimits", "-f", str(conf))):
            try:
                proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                                                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            except OSError as e:
                log.warning("AirPlay: %s not started: %s", argv[0], e)
                continue
            self.daemons.append(proc)
            asyncio.create_task(_log_lines(proc, argv[0]))
            await asyncio.sleep(1)

    async def _stop_daemons(self) -> None:
        for proc in reversed(self.daemons):
            await _end(proc)
        self.daemons.clear()

    async def _start(self, sp: Speaker) -> None:
        sp.metadata = Metadata(lambda kind, code, data: self._item(sp, kind, code, data))
        loop = asyncio.get_running_loop()
        try:
            sp.transport, _ = await loop.create_datagram_endpoint(lambda: _MetaSocket(sp.metadata),
                                                                  local_addr=("127.0.0.1", META_BASE + sp.slot))
        except OSError as e:
            log.warning("AirPlay on %s: no metadata port: %s", sp.panel_id, e)
        sp.task = asyncio.create_task(self._run(sp))

    async def _stop(self, sp: Speaker, tell_panel: bool = True) -> None:
        if sp.task:
            sp.task.cancel()
        if sp.process:
            await _end(sp.process)
        if sp.transport:
            sp.transport.close()
        await self._end_session(sp, tell_panel)

    async def _run(self, sp: Speaker) -> None:
        conf = RUN / f"{sp.slot}.conf"
        wait = 2
        while True:
            argv = [self.binary, "-c", str(conf)]
            conf.write_text(sp.conf, encoding="utf-8")
            os.chmod(conf, 0o600)
            if os.geteuid() == 0 and shutil.which("su-exec"):
                try:
                    import pwd
                    user = pwd.getpwnam(USER)
                    os.chown(conf, user.pw_uid, user.pw_gid)
                    argv = ["su-exec", USER, *argv]
                except KeyError:
                    pass
            started = time.monotonic()
            try:
                sp.process = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            except OSError as e:
                log.warning("AirPlay on %s: not started: %s", sp.panel_id, e)
                return
            log.info("AirPlay on %s: ready (port %s)", sp.panel_id, TCP_BASE + sp.slot)
            await self.changed()
            errors = asyncio.create_task(_log_lines(sp.process, f"AirPlay on {sp.panel_id}", stream="stderr"))
            try:
                while True:
                    data = await sp.process.stdout.read(8192)
                    if not data:
                        break
                    if sp.session is None:
                        # Sound with no "play begins" heard: a session all the same.
                        await self._begin(sp)
                    sp.session.feed(data)
            finally:
                errors.cancel()
            code = await sp.process.wait()
            await self._end_session(sp)
            log.warning("AirPlay on %s: stopped (code %s)", sp.panel_id, code)
            wait = 2 if time.monotonic() - started > 60 else min(wait * 2, 60)
            await asyncio.sleep(wait)

    # ---- sessions ----

    def _item(self, sp: Speaker, kind: bytes, code: bytes, data: bytes) -> None:
        if kind == b"core":
            field = {b"minm": "title", b"asar": "artist", b"asal": "album"}.get(code)
            if field:
                sp.now[field] = data.decode(errors="replace")[:200] or None
                self._changed_soon()
            return
        if kind != b"ssnc":
            return
        if code == b"pbeg":
            asyncio.ensure_future(self._begin(sp))
        elif code == b"pend":
            asyncio.ensure_future(self._end_session(sp))
        elif code == b"pfls" and sp.session:
            sp.session.paused = True
            self._changed_soon()
        elif code == b"prsm" and sp.session:
            sp.session.paused = False
            self._changed_soon()
        elif code == b"pvol":
            level = volume_level(data.decode(errors="replace"))
            if level is not None:
                sp.volume = level
            if level is not None and sp.session:
                asyncio.ensure_future(self.speaker_command(sp.panel_id, {"command": "volume", "level": level}))
        elif code == b"PICT" and data:
            mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            sp.art = (hashlib.sha1(data).hexdigest(), mime, data)
            asyncio.ensure_future(self.art_changed(sp.panel_id, sp.art))
            self._changed_soon()

    async def _begin(self, sp: Speaker) -> None:
        if sp.session is not None:
            sp.session.paused = False
            self._changed_soon()
            return
        sp.session = Session()
        asyncio.create_task(self._keep_fed(sp, sp.session))
        log.info("AirPlay on %s: playing", sp.panel_id)
        await self.speaker_command(sp.panel_id, {"command": "play_media", "hub_path": f"/api/airplay/{sp.session.token}",
                                                 "title": "AirPlay", "announce": False})
        if sp.volume is not None:
            await self.speaker_command(sp.panel_id, {"command": "volume", "level": sp.volume})
        await self.changed()

    async def _end_session(self, sp: Speaker, tell_panel: bool = True) -> None:
        session, sp.session = sp.session, None
        sp.now, sp.art = {}, None
        if session is None:
            return
        session.close()
        log.info("AirPlay on %s: ended", sp.panel_id)
        if tell_panel:
            await self.speaker_command(sp.panel_id, {"command": "stop"})
        await self.changed()

    async def _keep_fed(self, sp: Speaker, session: Session) -> None:
        silence = bytes(int(BYTES_PER_S * SILENCE_STEP_S) // 4 * 4)
        while sp.session is session:
            await asyncio.sleep(SILENCE_STEP_S)
            if time.monotonic() - session.heard_at > SILENCE_AFTER_S:
                for q in session.readers:
                    if q.full():
                        q.get_nowait()
                    q.put_nowait(silence)

    async def stream(self, request: web.Request) -> web.StreamResponse:
        """GET /api/airplay/<token>: the session's sound, as a WAV stream."""
        token = request.match_info["token"]
        sp = next((s for s in self.speakers.values()
                   if s.session and secrets.compare_digest(s.session.token, token)), None)
        if sp is None:
            raise web.HTTPNotFound()
        session = sp.session
        # One listener: the panel playing it now (a new one replaces the old).
        session.close()
        session.readers.clear()
        queue: asyncio.Queue = asyncio.Queue(QUEUE_CHUNKS)
        session.readers.add(queue)
        response = web.StreamResponse(headers={"Content-Type": "audio/wav", "Cache-Control": "no-store"})
        await response.prepare(request)
        try:
            await response.write(wav_header())
            while True:
                data = await queue.get()
                if data is None:
                    break
                await response.write(data)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            session.readers.discard(queue)
        return response


async def _ip_addr() -> str:
    try:
        proc = await asyncio.create_subprocess_exec("ip", "-4", "-o", "addr", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
        return out.decode(errors="replace")
    except (OSError, asyncio.TimeoutError):
        return ""


async def _run(*argv: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), 10)
    except (OSError, asyncio.TimeoutError):
        pass


async def _log_lines(proc: asyncio.subprocess.Process, who: str, stream: str = "stdout") -> None:
    reader = getattr(proc, stream)
    if reader is None:
        return
    async for line in reader:
        text = line.decode(errors="replace").rstrip()
        if text:
            log.info("%s: %s", who, text)


async def _end(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
