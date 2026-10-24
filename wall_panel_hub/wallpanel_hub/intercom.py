"""The intercom: Asterisk, run and configured by the hub (docs/speaker-intercom-plan.md, part B).

Doors (a SIP door station, or a phone standing in for one) register with Asterisk under an
account the hub makes: a user name and a long random password. The hub writes Asterisk's
whole configuration under /data/asterisk from its own settings (intercom.json) and reloads it
on each change; nothing in it is edited by hand.

B1: doors register, and 600 answers with an echo (what is said comes back), to check the
sound's way through the network. Panels ringing come next (B2).
"""

import asyncio
import json
import logging
import re
import secrets
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

# Audio ports for calls (RTP); the SIP port is the add-on's option.
RTP_START, RTP_END = 10000, 10199
ECHO = "600"
NAME = re.compile(r"[^\n\r\[\];=\"]{1,40}")
MAX_DOORS = 20


class IntercomInvalid(ValueError):
    pass


def _door_id() -> str:
    return secrets.token_hex(3)


def _password() -> str:
    # Letters and digits only: door stations' forms often refuse the rest.
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(20))


def pjsip_conf(doors: list[dict], sip_port: int) -> str:
    lines = [
        "; Written by Wall Panel Hub: changes here are lost.",
        "[global]", "type=global", "user_agent=Wall Panel Hub", "endpoint_identifier_order=username,ip", "",
        "[transport-udp]", "type=transport", "protocol=udp", f"bind=0.0.0.0:{sip_port}", "",
        "[transport-tcp]", "type=transport", "protocol=tcp", f"bind=0.0.0.0:{sip_port}", "",
    ]
    for d in doors:
        user = d["username"]
        lines += [
            f"[{user}]", "type=endpoint", "context=from-door", "disallow=all",
            # Video passed through as it comes (H.264 from most door stations).
            "allow=ulaw,alaw,g722,h264,vp8",
            # Behind NAT or a VPN: answer where the door's packets come from.
            "direct_media=no", "rtp_symmetric=yes", "force_rport=yes", "rewrite_contact=yes",
            f"callerid=\"{d['name']}\" <{user}>", f"auth={user}", f"aors={user}", "",
            f"[{user}]", "type=auth", "auth_type=userpass", f"username={user}", f"password={d['password']}", "",
            f"[{user}]", "type=aor", "max_contacts=1", "remove_existing=yes", "qualify_frequency=30", "",
        ]
    return "\n".join(lines) + "\n"


def extensions_conf() -> str:
    return "\n".join([
        "; Written by Wall Panel Hub: changes here are lost.",
        "[from-door]",
        f"; {ECHO}: what is said comes back, to check the sound's way.",
        f"exten => {ECHO},1,Answer()",
        " same => n,Echo()",
        " same => n,Hangup()",
        "; Any other number: the panels ring (next step); busy until then.",
        "exten => _[0-9a-zA-Z*#+].,1,Busy(5)",
        "",
    ])


def asterisk_conf(root: Path) -> str:
    return "\n".join([
        "[directories](!)",
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
        "verbose = 1",
        "",
    ])


STATIC_CONF = {
    "modules.conf": "[modules]\nautoload=yes\n; Old SIP channel: pjsip has the port.\nnoload => chan_sip.so\nnoload => chan_iax2.so\nnoload => chan_skinny.so\nnoload => chan_mgcp.so\nnoload => chan_unistim.so\n",
    "logger.conf": "[general]\n[logfiles]\nconsole => notice,warning,error\nmessages.log => notice,warning,error\n",
    "rtp.conf": f"[general]\nrtpstart={RTP_START}\nrtpend={RTP_END}\n",
}


class Intercom:
    def __init__(self, data_dir: Path, sip_port: int):
        self.file = data_dir / "intercom.json"
        self.root = data_dir / "asterisk"
        self.sip_port = sip_port
        self.settings = {"enabled": False, "doors": []}
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

    def update(self, changes: dict) -> None:
        """{"enabled"?, "doors"?: [{"id"?, "name"}]}: a door without id is new, one left out is removed."""
        if "enabled" in changes:
            self.settings["enabled"] = bool(changes["enabled"])
        if "doors" in changes:
            wanted = changes["doors"]
            if not isinstance(wanted, list) or len(wanted) > MAX_DOORS:
                raise IntercomInvalid(f"at most {MAX_DOORS} doors")
            old = {d["id"]: d for d in self.doors}
            doors = []
            for w in wanted:
                name = str((w or {}).get("name") or "").strip()
                if not NAME.fullmatch(name):
                    raise IntercomInvalid("a door's name is 1 to 40 characters, without [ ] ; = \"")
                kept = old.get((w or {}).get("id"))
                if kept:
                    doors.append({**kept, "name": name})
                else:
                    did = _door_id()
                    doors.append({"id": did, "name": name, "username": f"door_{did}", "password": _password()})
            self.settings["doors"] = doors
        self._save()

    def new_password(self, door_id: str) -> None:
        door = next((d for d in self.doors if d["id"] == door_id), None)
        if door is None:
            raise KeyError(door_id)
        door["password"] = _password()
        self._save()

    # ---- Asterisk ----

    def write_config(self) -> None:
        etc = self.root / "etc"
        for sub in ("etc", "db", "keys", "spool", "log"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        files = {**STATIC_CONF, "asterisk.conf": asterisk_conf(self.root),
                 "pjsip.conf": pjsip_conf(self.doors, self.sip_port), "extensions.conf": extensions_conf()}
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
        else:
            self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        Path("/run/asterisk").mkdir(parents=True, exist_ok=True)
        wait = 2
        while self.settings["enabled"]:
            log.info("starting Asterisk (SIP port %s)", self.sip_port)
            self.process = await asyncio.create_subprocess_exec(
                self.binary, "-f", "-n", "-C", str(self.root / "etc" / "asterisk.conf"),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
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
        """Doors registered now: user name -> where from (address)."""
        out = await self.cli("pjsip show contacts") or ""
        return parse_contacts(out)

    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None


CONTACT = re.compile(r"Contact:\s+([\w-]+)/sip:[^@\s]*@?([^;\s>]+)\S*\s+\S+\s+(\w+)")


def parse_contacts(text: str) -> dict[str, str]:
    """`pjsip show contacts` -> {aor: "host:port"} for contacts that answer (Avail) or are not checked."""
    found = {}
    for aor, where, status in CONTACT.findall(text):
        if status in ("Avail", "NonQual", "Unknown"):
            found[aor] = where
    return found


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
