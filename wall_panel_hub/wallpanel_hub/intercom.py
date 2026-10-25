"""The intercom: Asterisk, run and configured by the hub (docs/speaker-intercom-plan.md, part B).

A door reaches Asterisk in one of two ways:
- "here": the door (a SIP door station, or a phone standing in for one) registers with
  Asterisk under an account the hub makes: a user name and a long random password.
- "server": the door station has its own SIP server (Dahua VTO, Akuvox...): Asterisk
  registers there under an indoor-monitor account made in the door's own pages, and the
  door's calls to that account come here. The door's own monitors keep working.

Panels register too, each with an account made for it the first time it asks
(wallpanel/intercom/subscribe): SIP over a WebSocket that the hub carries inside the panel's
own connection (gateway.py), to Asterisk's web server on this machine only. A door's call
rings its panels (all by default); the first to answer takes it.

The hub writes Asterisk's whole configuration under /data/asterisk from its own settings
(intercom.json) and reloads it on each change; nothing in it is edited by hand.
"""

import asyncio
import hashlib
import json
import logging
import re
import secrets
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

# Audio ports for calls (RTP); the SIP port is the add-on's option.
RTP_START, RTP_END = 10000, 10199
# Asterisk's web server, for panels' SIP over WebSocket: on this machine only, through the hub.
HTTP_PORT = 18088
DOMAIN = "wallpanel"
ECHO = "600"
# How long a door's call rings the panels.
RING_S = 30
NAME = re.compile(r"[^\n\r\[\];=\"]{1,40}")
HOST = re.compile(r"[A-Za-z0-9.-]{1,253}(:\d{1,5})?")
USER = re.compile(r"[A-Za-z0-9_.+#*-]{1,40}")
MODES = ("here", "server")
MAX_DOORS = 20


class IntercomInvalid(ValueError):
    pass


def _door_id() -> str:
    return secrets.token_hex(3)


def _password() -> str:
    # Letters and digits only: door stations' forms often refuse the rest.
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(20))


def _conf_value(text: str) -> str:
    # ";" starts a comment in Asterisk's files.
    return text.replace("\\", "\\\\").replace(";", "\\;")


def panel_username(panel_id: str) -> str:
    return "panel_" + hashlib.sha256(panel_id.encode()).hexdigest()[:10]


def pjsip_conf(doors: list[dict], sip_port: int, panels: list[dict] = ()) -> str:
    lines = [
        "; Written by Wall Panel Hub: changes here are lost.",
        "[global]", "type=global", "user_agent=Wall Panel Hub", "endpoint_identifier_order=username,ip", "",
        "[transport-udp]", "type=transport", "protocol=udp", f"bind=0.0.0.0:{sip_port}", "",
        "[transport-tcp]", "type=transport", "protocol=tcp", f"bind=0.0.0.0:{sip_port}", "",
        # Panels: through the hub, to Asterisk's web server (http.conf).
        "[transport-ws]", "type=transport", "protocol=ws", "bind=0.0.0.0", "",
    ]
    for d in doors:
        user = d["username"]
        endpoint = [
            f"[{user}]", "type=endpoint", f"context=door-{d['id']}", "disallow=all",
            # Video passed through as it comes (H.264 from most door stations).
            "allow=ulaw,alaw,g722,h264,vp8",
            # Behind NAT or a VPN: answer where the door's packets come from.
            "direct_media=no", "rtp_symmetric=yes", "force_rport=yes", "rewrite_contact=yes",
            f"callerid=\"{d['name']}\" <{user}>",
        ]
        if d.get("mode") == "server":
            host = d["server"]
            lines += endpoint + [f"outbound_auth={user}", f"aors={user}", "",
                                 # Asterisk as one of the door's indoor monitors.
                                 f"[{user}]", "type=registration", "transport=transport-udp", f"outbound_auth={user}",
                                 f"server_uri=sip:{host}", f"client_uri=sip:{d['user']}@{host}",
                                 f"contact_user={d['user']}", "retry_interval=30", "forbidden_retry_interval=60",
                                 "max_retries=100000", "expiration=300", "",
                                 f"[{user}]", "type=auth", "auth_type=userpass", f"username={d['user']}",
                                 f"password={_conf_value(d.get('secret') or '')}", "",
                                 f"[{user}]", "type=aor", f"contact=sip:{host}", "",
                                 # The door's calls are known by where they come from.
                                 f"[{user}]", "type=identify", f"endpoint={user}", f"match={host.split(':')[0]}", ""]
        else:
            lines += endpoint + [f"auth={user}", f"aors={user}", "",
                                 f"[{user}]", "type=auth", "auth_type=userpass", f"username={user}", f"password={d['password']}", "",
                                 f"[{user}]", "type=aor", "max_contacts=1", "remove_existing=yes", "qualify_frequency=30", ""]
    for p in panels:
        user = p["username"]
        lines += [
            f"[{user}]", "type=endpoint", "context=from-panel", "disallow=all", "allow=ulaw,alaw",
            "webrtc=yes", "dtls_auto_generate_cert=yes", "timers=no", f"auth={user}", f"aors={user}", "",
            f"[{user}]", "type=auth", "auth_type=userpass", f"username={user}", f"password={p['password']}", "",
            f"[{user}]", "type=aor", "max_contacts=1", "remove_existing=yes", "",
        ]
    return "\n".join(lines) + "\n"


def extensions_conf(doors: list[dict] = (), panels: dict[str, str] | None = None) -> str:
    """[panels]: panel id -> its user name, for the panels with an account."""
    panels = panels or {}
    lines = ["; Written by Wall Panel Hub: changes here are lost."]
    echo = [f"exten => {ECHO},1,Answer()", " same => n,Echo()", " same => n,Hangup()"]
    for d in doors:
        wanted = d.get("panels")
        users = [u for pid, u in panels.items() if wanted is None or pid in wanted]
        ring = f"Dial({'&'.join(f'PJSIP/{u}' for u in users)},{RING_S})" if users else "Busy(5)"
        lines += [f"[door-{d['id']}]"]
        if d.get("mode") != "server":
            lines += [f"; {ECHO}: what is said comes back, to check the sound's way."] + echo
        # Any number the door calls rings its panels; "s" when it calls with none.
        exts = ["_[0-9*#+]!", "s"]
        if d.get("mode") == "server" and not re.fullmatch(r"[0-9*#+]*", d["user"]):
            exts.append(d["user"])
        for ext in exts:
            lines += [f"exten => {ext},1,{ring}", " same => n,Hangup()"]
        lines.append("")
    lines += ["[from-panel]"] + echo + ["exten => _[0-9a-zA-Z*#+].,1,Hangup()", ""]
    return "\n".join(lines)


def asterisk_conf(root: Path) -> str:
    return "\n".join([
        # Not a template ("(!)"): Asterisk ignores those, and would read an empty /etc/asterisk.
        "[directories]",
        f"astetcdir => {root / 'etc'}",
        "astmoddir => /usr/lib/asterisk/modules",
        "astvarlibdir => /var/lib/asterisk",
        "astdatadir => /var/lib/asterisk",
        f"astdbdir => {root / 'db'}",
        f"astkeydir => {root / 'keys'}",
        f"astspooldir => {root / 'spool'}",
        "astrundir => /run/asterisk",
        f"astlogdir => {root / 'log'}",
        "",
        "[options]",
        # Each call's steps in the add-on's log.
        "verbose = 3",
        "",
    ])


STATIC_CONF = {
    "modules.conf": "[modules]\nautoload=yes\n; Old SIP channel: pjsip has the port.\nnoload => chan_sip.so\nnoload => chan_iax2.so\nnoload => chan_skinny.so\nnoload => chan_mgcp.so\nnoload => chan_unistim.so\n",
    "logger.conf": "[general]\n[logfiles]\nconsole => notice,warning,error,verbose\nmessages.log => notice,warning,error\n",
    "rtp.conf": f"[general]\nrtpstart={RTP_START}\nrtpend={RTP_END}\nicesupport=yes\n",
    "http.conf": f"[general]\nenabled=yes\nbindaddr=127.0.0.1\nbindport={HTTP_PORT}\n",
}


class Intercom:
    def __init__(self, data_dir: Path, sip_port: int):
        self.file = data_dir / "intercom.json"
        self.root = data_dir / "asterisk"
        self.sip_port = sip_port
        self.settings = {"enabled": False, "doors": [], "panels": {}}
        if self.file.exists():
            try:
                self.settings = {**self.settings, **json.loads(self.file.read_text(encoding="utf-8"))}
            except ValueError:
                log.warning("intercom settings not readable: starting empty")
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.binary = shutil.which("asterisk")

    # ---- settings ----

    def _save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    @property
    def doors(self) -> list[dict]:
        return self.settings["doors"]

    @property
    def accounts(self) -> dict[str, dict]:
        """Panels' accounts, by panel id."""
        return self.settings["panels"]

    def update(self, changes: dict) -> None:
        """{"enabled"?, "doors"?: [{"id"?, "name", "mode"?, "server"?, "user"?, "secret"?, "panels"?}]}:
        a door without id is new, one left out is removed; a secret left out is kept."""
        if "enabled" in changes:
            self.settings["enabled"] = bool(changes["enabled"])
        if "sip_log" in changes:
            self.settings["sip_log"] = bool(changes["sip_log"])
        if "doors" in changes:
            wanted = changes["doors"]
            if not isinstance(wanted, list) or len(wanted) > MAX_DOORS:
                raise IntercomInvalid(f"at most {MAX_DOORS} doors")
            old = {d["id"]: d for d in self.doors}
            self.settings["doors"] = [self._door(w or {}, old) for w in wanted]
        self._save()

    @staticmethod
    def _door(w: dict, old: dict) -> dict:
        name = str(w.get("name") or "").strip()
        if not NAME.fullmatch(name):
            raise IntercomInvalid("a door's name is 1 to 40 characters, without [ ] ; = \"")
        mode = w.get("mode") or "here"
        if mode not in MODES:
            raise IntercomInvalid(f"a door's mode is one of {', '.join(MODES)}")
        kept = old.get(w.get("id"))
        if kept:
            door = {**kept, "name": name, "mode": mode}
        else:
            did = _door_id()
            door = {"id": did, "name": name, "mode": mode, "username": f"door_{did}", "password": _password()}
        if mode == "server":
            server = str(w.get("server") or door.get("server") or "").strip()
            user = str(w.get("user") or door.get("user") or "").strip()
            if not HOST.fullmatch(server):
                raise IntercomInvalid("the door's address: its IP or name, and :port if not 5060")
            if not USER.fullmatch(user):
                raise IntercomInvalid("the account on the door: 1 to 40 letters, digits or _ . + # * -")
            door.update(server=server, user=user)
            if "secret" in w:
                secret = str(w["secret"] or "")
                if len(secret) > 64 or re.search(r"[\r\n]", secret):
                    raise IntercomInvalid("the account's password: at most 64 characters, on one line")
                door["secret"] = secret
        panels = w.get("panels", door.get("panels"))
        door["panels"] = None if panels is None else [str(p) for p in panels if isinstance(p, str)][:100]
        return door

    def new_password(self, door_id: str) -> None:
        door = next((d for d in self.doors if d["id"] == door_id), None)
        if door is None:
            raise KeyError(door_id)
        door["password"] = _password()
        self._save()

    async def panel_account(self, panel_id: str) -> dict:
        """The panel's SIP account, made (and Asterisk reloaded) the first time."""
        account = self.accounts.get(panel_id)
        if account is None:
            account = {"username": panel_username(panel_id), "password": _password()}
            self.accounts[panel_id] = account
            self._save()
            log.info("intercom account for panel %s", panel_id)
            await self.apply()
        return account

    async def forget(self, panel_id: str) -> None:
        """A panel unpaired: its account goes, and it rings for no door."""
        changed = self.accounts.pop(panel_id, None) is not None
        for d in self.doors:
            if d.get("panels") and panel_id in d["panels"]:
                d["panels"].remove(panel_id)
                changed = True
        if changed:
            self._save()
            await self.apply()

    # ---- Asterisk ----

    def write_config(self) -> None:
        etc = self.root / "etc"
        for sub in ("etc", "db", "keys", "spool", "log"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        panels = [self.accounts[p] for p in sorted(self.accounts)]
        files = {**STATIC_CONF, "asterisk.conf": asterisk_conf(self.root),
                 "pjsip.conf": pjsip_conf(self.doors, self.sip_port, panels),
                 "extensions.conf": extensions_conf(self.doors, {p: a["username"] for p, a in self.accounts.items()})}
        for name, text in files.items():
            (etc / name).write_text(text, encoding="utf-8")
        (etc / "pjsip.conf").chmod(0o600)

    async def apply(self) -> None:
        """Asterisk as the settings say: started, reloaded, or stopped."""
        if not self.settings["enabled"]:
            await self.stop()
            return
        if not self.binary:
            log.warning("intercom on, but Asterisk is not installed here")
            return
        self.write_config()
        if self.task and not self.task.done():
            await self.cli("core reload")
            await self.sip_logger()
        else:
            self.task = asyncio.create_task(self._run())

    async def sip_logger(self) -> None:
        """Every SIP message in the add-on's log, while asked (to find why a door's call fails)."""
        await self.cli(f"pjsip set logger {'on' if self.settings.get('sip_log') else 'off'}")

    async def _after_start(self) -> None:
        await asyncio.sleep(5)
        if self.running() and self.settings.get("sip_log"):
            await self.sip_logger()

    async def _run(self) -> None:
        Path("/run/asterisk").mkdir(parents=True, exist_ok=True)
        wait = 2
        while self.settings["enabled"]:
            log.info("starting Asterisk (SIP port %s)", self.sip_port)
            self.process = await asyncio.create_subprocess_exec(
                self.binary, "-f", "-n", "-C", str(self.root / "etc" / "asterisk.conf"),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            asyncio.create_task(self._after_start())
            async for line in self.process.stdout:
                text = line.decode(errors="replace").rstrip()
                if text:
                    log.info("asterisk: %s", text)
            code = await self.process.wait()
            self.process = None
            if not self.settings["enabled"]:
                break
            log.warning("Asterisk stopped (%s); again in %s s", code, wait)
            await asyncio.sleep(wait)
            wait = min(wait * 2, 60)

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except asyncio.TimeoutError:
                self.process.kill()
        if self.task:
            self.task.cancel()
            self.task = None

    async def cli(self, command: str) -> str | None:
        """An Asterisk console command's answer; None when Asterisk is not running."""
        if not self.binary or not self.process:
            return None
        proc = await asyncio.create_subprocess_exec(
            self.binary, "-C", str(self.root / "etc" / "asterisk.conf"), "-rx", command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 10)
        except asyncio.TimeoutError:
            proc.kill()
            return None
        return out.decode(errors="replace")

    async def registered(self) -> dict[str, str]:
        """Who is registered now: user name -> where from (address)."""
        out = await self.cli("pjsip show contacts") or ""
        return parse_contacts(out)

    async def registrations(self) -> dict[str, str]:
        """Asterisk's own registrations at doors' servers: user name -> Registered, Rejected..."""
        out = await self.cli("pjsip show registrations") or ""
        return parse_registrations(out)

    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def connect(self, session):
        """A SIP WebSocket to Asterisk, for one panel (carried by the hub, gateway.py)."""
        return await session.ws_connect(f"ws://127.0.0.1:{HTTP_PORT}/ws", protocols=("sip",), heartbeat=30, timeout=10)


CONTACT = re.compile(r"Contact:\s+([\w-]+)/sip:[^@\s]*@?([^;\s>]+)\S*\s+\S+\s+(\w+)")
REGISTRATION = re.compile(r"^\s*([\w-]+)/sip:\S+\s+\S+\s+(\w+)", re.M)


def parse_contacts(text: str) -> dict[str, str]:
    """`pjsip show contacts` -> {aor: "host:port"} for contacts that answer (Avail) or are not checked."""
    found = {}
    for aor, where, status in CONTACT.findall(text):
        if status in ("Avail", "NonQual", "Unknown"):
            found[aor] = where
    return found


def parse_registrations(text: str) -> dict[str, str]:
    """`pjsip show registrations` -> {registration: status}."""
    return dict(REGISTRATION.findall(text))


async def addresses() -> list[str]:
    """This machine's IPv4 addresses a door may use: the LAN, Tailscale's... (not loopback or HA's own)."""
    try:
        proc = await asyncio.create_subprocess_exec("ip", "-4", "-o", "addr", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
    except (OSError, asyncio.TimeoutError):
        return []
    found = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", out.decode())
    return [a for a in found if not a.startswith(("127.", "172.30.", "172.17."))]
