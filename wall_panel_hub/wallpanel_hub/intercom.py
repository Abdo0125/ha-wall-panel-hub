"""The intercom: Asterisk, run and configured by the hub (docs/speaker-intercom-plan.md, part B).

Calls reach Asterisk in two ways:
- the hub's own server: a door (a SIP door station, or a phone standing in for one) registers
  with Asterisk under an account the hub makes: a user name and a long random password.
- an external server (Dahua VTO, Akuvox, a PBX...): Asterisk registers there under an
  indoor-monitor account made in its own pages, and the calls to that account come here.
  Its devices (door stations, doorbells, monitors, phones) are set up under it by number:
  named when they call, called by name. Its own monitors keep working.

Panels register too, each with an account made for it the first time it asks
(wallpanel/intercom/subscribe): SIP over a WebSocket that the hub carries inside the panel's
own connection (gateway.py), to Asterisk's web server on this machine only. A call rings its
panels (all by default); the first to answer takes it.

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
# Asterisk's manager (AMI), for doors' calls as HA events (doorbell.py): on this machine only.
AMI_PORT = 18038
AMI_USER = "wallpanel"
DOMAIN = "wallpanel"
# Asterisk's database family: the panels whose calls' sound comes through the hub (relay.py).
RELAY_DB = "wallpanel_relay"
ECHO = "600"
# How long a door's call rings the panels.
RING_S = 30
# A call with no sound this long either way is over: its other end gone (power, network) and
# its BYE lost.
RTP_TIMEOUT_S = 30
# How often the servers' registrations are read, for HA.
LINK_CHECK_S = 20
NAME = re.compile(r"[^\n\r\[\];=\"]{1,40}")
NAME_UNSAFE = re.compile(r"[\n\r\[\];=\"\\]")
HOST = re.compile(r"[A-Za-z0-9.-]{1,253}(:\d{1,5})?")
USER = re.compile(r"[A-Za-z0-9_.+#*-]{1,40}")
# What Asterisk takes in an endpoint's from_user: an account such as Dahua's second monitor
# "9901#2" is not, and would leave the door out (its calls then "not found").
FROM_USER = re.compile(r"[A-Za-z0-9_.~-]+")
MAX_SERVERS = 10
MAX_DOORS = 20
MAX_CONTACTS = 50
# How a door opens (door_open.py): a code sent during the call, its own address, a device of HA's.
OPENS = ("none", "dtmf", "http", "entity")
DTMF = re.compile(r"[0-9*#A-D]{1,16}")
URL = re.compile(r"https?://[^\s]{3,500}")
CAMERA = re.compile(r"camera\.[a-z0-9_]{1,100}")
OPENER = re.compile(r"(lock|switch|input_boolean|script|button|input_button|cover)\.[a-z0-9_]{1,100}")
NUMBER = re.compile(r"[0-9*#+]{1,32}")
KINDS = ("door", "panel", "phone")


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


def _endpoint(user: str, context: str) -> list[str]:
    return [
        f"[{user}]", "type=endpoint", f"context={context}", "disallow=all",
        # Video passed through as it comes (H.264 from most door stations).
        "allow=ulaw,alaw,g722,h264,vp8",
        # Behind NAT or a VPN: answer where the door's packets come from.
        "direct_media=no", "rtp_symmetric=yes", "force_rport=yes", "rewrite_contact=yes",
        # No session timers: some door stations end a call that requires them.
        "timers=no",
        # The door's code (opening it) as it takes it: in the sound's way, else in SIP INFO.
        "dtmf_mode=auto_info",
        # One codec in the answer: a phone told of several sends an UPDATE to keep one,
        # which a door's server may refuse (481), ending the call.
        "preferred_codec_only=yes",
        f"rtp_timeout={RTP_TIMEOUT_S}",
    ]


def pjsip_conf(doors: list[dict], sip_port: int, panels: list[dict] = (), servers: list[dict] = ()) -> str:
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
        lines += _endpoint(user, f"door-{d['id']}") + [
            f"callerid=\"{d['name']}\" <{user}>", f"auth={user}", f"aors={user}", "",
            f"[{user}]", "type=auth", "auth_type=userpass", f"username={user}", f"password={d['password']}", "",
            f"[{user}]", "type=aor", "max_contacts=1", "remove_existing=yes", "qualify_frequency=30", ""]
    for s in servers:
        user, host = s["username"], s["server"]
        # Our calls there come from the monitor's account: here, else set by the dialplan (through).
        own = [f"from_user={s['user']}"] if FROM_USER.fullmatch(s["user"]) else []
        lines += _endpoint(user, f"server-{s['id']}") + [
            f"outbound_auth={user}", f"aors={user}", *own, f"from_domain={host.split(':')[0]}", "",
            # Asterisk as one of the server's indoor monitors.
            f"[{user}]", "type=registration", "transport=transport-udp", f"outbound_auth={user}",
            f"server_uri=sip:{host}", f"client_uri=sip:{s['user']}@{host}",
            f"contact_user={s['user']}", "retry_interval=30", "forbidden_retry_interval=60",
            "max_retries=100000", "expiration=300", "",
            f"[{user}]", "type=auth", "auth_type=userpass", f"username={s['user']}",
            f"password={_conf_value(s.get('secret') or '')}", "",
            f"[{user}]", "type=aor", f"contact=sip:{host}", "",
            # The server's calls are known by where they come from.
            f"[{user}]", "type=identify", f"endpoint={user}", f"match={host.split(':')[0]}", ""]
    # A panel's account works through the hub only (its SIP WebSocket, from this machine): not
    # from the network, even with its password.
    hub_only = ["deny=0.0.0.0/0.0.0.0", "permit=127.0.0.1/255.255.255.255", f"rtp_timeout={RTP_TIMEOUT_S}"]
    for p in panels:
        user = p["username"]
        name = NAME_UNSAFE.sub("", p.get("name") or "Panel")[:40] or "Panel"
        lines += [
            f"[{user}]", "type=endpoint", "context=from-panel", "disallow=all", "allow=ulaw,alaw",
            "webrtc=yes", "dtls_auto_generate_cert=yes", "timers=no", f"callerid=\"{name}\" <{user}>",
            *hub_only, f"auth={user}", f"aors={user}", "",
            f"[{user}]", "type=auth", "auth_type=userpass", f"username={user}", f"password={p['password']}", "",
            f"[{user}]", "type=aor", "max_contacts=1", "remove_existing=yes", "",
            # The same panel, its sound through the hub (relay.py): plain RTP, on this machine.
            f"[{relay_user(user)}]", "type=endpoint", "context=from-panel", "disallow=all", "allow=ulaw,alaw",
            "transport=transport-ws", "direct_media=no", "rtp_symmetric=yes", "timers=no",
            f"callerid=\"{name}\" <{user}>", *hub_only, f"auth={user}", f"aors={user}", "",
        ]
    return "\n".join(lines) + "\n"


def relay_user(user: str) -> str:
    return f"{user}_relay"


def dial(user: str) -> str:
    """A panel's channel: its relay endpoint while its sound comes through the hub."""
    return f"${{IF(${{DB_EXISTS({RELAY_DB}/{user})}}?PJSIP/{relay_user(user)}:PJSIP/{user})}}"


def default_server(servers: list[dict], numbers_via: str | None) -> dict | None:
    """The server that numbers typed on a panel go through: the one chosen, else the first."""
    return next((s for s in servers if s["id"] == numbers_via), servers[0] if servers else None)


def extensions_conf(doors: list[dict] = (), panels: dict[str, str] | None = None, extra: list[dict] = (),
                    servers: list[dict] = (), numbers_via: str | None = None) -> str:
    """[panels]: panel id -> its user name, for the panels with an account; [extra]: the
    devices on the external servers, each called through its own."""
    panels = panels or {}
    call = "Call: ${CHANNEL(linkedid)}"
    lines = ["; Written by Wall Panel Hub: changes here are lost.",
             # Each call to a panel says who: a door's id, a device's target ("contact:<id>").
             "[wallpanel-door]", "exten => s,1,Set(PJSIP_HEADER(add,X-Wallpanel-Door)=${ARG1})", " same => n,Return()", "",
             # A panel took the call: told to the hub (doorbell.py), for HA.
             "[wallpanel-answered]", f"exten => s,1,UserEvent(WallpanelAnswered,Target: ${{ARG1}},{call},Panel: ${{CHANNEL(endpoint)}})",
             " same => n,Return()", ""]
    echo = [f"exten => {ECHO},1,Answer()", " same => n,Echo()", " same => n,Hangup()"]

    def ring(wanted, header: str | None, target: str | None) -> str:
        users = [u for pid, u in panels.items() if wanted is None or pid in wanted]
        if not users:
            return "Busy(5)"
        opts = (f"b(wallpanel-door^s^1({header}))" if header else "") + (f"U(wallpanel-answered^{target})" if target else "")
        return f"Dial({'&'.join(dial(u) for u in users)},{RING_S}{',' + opts if opts else ''})"

    def told(ext: str, target: str, dial_step: str) -> list[str]:
        # The hub hears of the call (and of how it ended, at h), for HA.
        return [f"exten => {ext},1,UserEvent(WallpanelRing,Target: {target},{call},Number: ${{CALLERID(num)}})",
                f" same => n,{dial_step}", " same => n,Hangup()"]

    end = f"exten => h,1,UserEvent(WallpanelEnd,{call},Status: ${{DIALSTATUS}})"
    for d in doors:
        # Any number the door calls rings its panels; "s" when it calls with none.
        lines += [f"[door-{d['id']}]", f"; {ECHO}: what is said comes back, to check the sound's way."] + echo
        for ext in ("_[0-9*#+]!", "s"):
            lines += told(ext, f"door:{d['id']}", ring(d.get("panels"), d["id"], f"door:{d['id']}"))
        lines += [end, ""]
    for s in servers:
        # A call from an external server: whose it is by the number it comes from (a device
        # set up under the server), else it rings every panel as the server's.
        here, there = f"server-{s['id']}", f"server-{s['id']}-from"
        exts = ["_[0-9*#+]!", "s"] + ([] if re.fullmatch(r"[0-9*#+]*", s["user"]) else [s["user"]])
        lines += [f"[{here}]"]
        for ext in exts:
            lines += [f"exten => {ext},1,Set(WHO=${{FILTER(0-9*#+,${{CALLERID(num)}})}})",
                      f" same => n,Goto({there},${{IF(${{DIALPLAN_EXISTS({there},${{WHO}},1)}}?${{WHO}}:any)}},1)"]
        lines += ["", f"[{there}]"]
        for c in extra:
            if c.get("via") == s["id"]:
                target = f"contact:{c['id']}"
                lines += told(c["number"], target, ring(c.get("panels"), target, target))
        lines += told("any", f"server:{s['id']}", ring(None, None, f"server:{s['id']}")) + [end, ""]
    # A panel calls: a door registered here, another panel, a device by name through its
    # server, or a number through the server chosen for numbers.
    lines += ["[from-panel]"] + echo
    for d in doors:
        lines += [f"exten => {d['username']},1,Dial(PJSIP/{d['username']},{RING_S})", " same => n,Hangup()"]
    for u in panels.values():
        lines += [f"exten => {u},1,Dial({dial(u)},{RING_S})", " same => n,Hangup()"]
    by_id = {s["id"]: s for s in servers}
    for c in extra:
        via = by_id.get(c.get("via"))
        if via is not None:
            lines += through(f"c_{c['id']}", via, c["number"])
    server = typed_through(list(servers), numbers_via)
    if server:
        lines += through("_[0-9*#+]!", server, "${EXTEN}")
    else:
        # Its devices only: their numbers typed still go through it, any other is refused.
        taken = {ECHO, *(d["username"] for d in doors), *panels.values()}
        via = default_server(list(servers), numbers_via)
        for c in extra:
            if via is not None and c.get("via") == via["id"] and c["number"] not in taken:
                taken.add(c["number"])
                lines += through(c["number"], via, c["number"])
        lines += ["exten => _[0-9*#+]!,1,Congestion(5)", " same => n,Hangup()"]
    lines.append("")
    return "\n".join(lines)


def typed_through(servers: list[dict], numbers_via: str | None) -> dict | None:
    """The server a number typed on a panel goes through; None when that server takes its own
    devices only (a PBX with outside lines: no calls out from any panel)."""
    server = default_server(servers, numbers_via)
    return server if server and server.get("numbers", "any") == "any" else None


def through(ext: str, server: dict, number: str) -> list[str]:
    """[number] called through an external [server], from its monitor's account: set here when
    the endpoint cannot have it (pjsip_conf)."""
    steps = [] if FROM_USER.fullmatch(server["user"]) else [f"Set(CALLERID(num)={server['user']})"]
    steps += [f"Dial(PJSIP/{number}@{server['username']},{RING_S})", "Hangup()"]
    return [f"exten => {ext},1,{steps[0]}"] + [f" same => n,{s}" for s in steps[1:]]


def _opens(o: dict | None) -> bool:
    return bool(o) and o.get("kind", "none") != "none"


def _in_call(o: dict | None) -> bool:
    """Opens only during a call: its code goes in the call's sound (DTMF)."""
    return bool(o) and o.get("kind") == "dtmf"


def _rings(x: dict, panel_id: str) -> bool:
    return x.get("panels") is None or panel_id in x["panels"]


def contacts(doors: list[dict], accounts: dict[str, dict], names: dict[str, str], panel_id: str,
             extra: list[dict] = (), servers: list[dict] = (), numbers_via: str | None = None) -> dict:
    """What a panel may call: the doors registered here, the devices on the external servers
    and the other panels, by name; numbers go through the server chosen for them (its name in
    "via"), else only the echo. "doors" and each device's "match" (the number it calls from)
    name a call that comes, with its camera and whether it opens (target for .../open)."""
    mine = [d for d in doors if _rings(d, panel_id)]
    found = [{"name": d["name"], "number": d["username"], "kind": "door", "camera": d.get("camera"), "camera_sub": d.get("camera_sub"),
              "open": _opens(d.get("open")), "in_call": _in_call(d.get("open")), "target": f"door:{d['id']}"}
             for d in mine]
    ids = {s["id"] for s in servers}
    found += [{"name": c["name"], "number": f"c_{c['id']}", "kind": c.get("kind", "door"), "camera": c.get("camera"), "camera_sub": c.get("camera_sub"),
               "open": _opens(c.get("open")), "in_call": _in_call(c.get("open")), "target": f"contact:{c['id']}", "match": c["number"]}
              for c in extra if c.get("via") in ids and _rings(c, panel_id)]
    found += sorted(({"name": names.get(pid) or "Panel", "number": a["username"], "kind": "panel"}
                     for pid, a in accounts.items() if pid != panel_id), key=lambda c: c["name"].lower())
    server = typed_through(list(servers), numbers_via)
    return {"contacts": found, "via": server["name"] if server else None, "echo": ECHO,
            "doors": [{"id": d["id"], "name": d["name"], "camera": d.get("camera"), "camera_sub": d.get("camera_sub"), "open": _opens(d.get("open")),
                       "in_call": _in_call(d.get("open")), "target": f"door:{d['id']}"} for d in mine]}


def manager_conf(secret: str) -> str:
    """Asterisk's manager, for the hub only: its user reads the dialplan's events, nothing else."""
    return "\n".join([
        "[general]", "enabled=yes", f"port={AMI_PORT}", "bindaddr=127.0.0.1", "",
        f"[{AMI_USER}]", f"secret={secret}", "deny=0.0.0.0/0.0.0.0", "permit=127.0.0.1/255.255.255.255",
        "read=user", "write=", "",
    ])


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
    # Panels' calls offer this machine's addresses but HA's and Docker's own (and IPv6 link-local).
    "rtp.conf": f"[general]\nrtpstart={RTP_START}\nrtpend={RTP_END}\nicesupport=yes\n"
                "ice_deny=172.30.0.0/16\nice_deny=172.17.0.0/16\nice_deny=fe80::/10\nice_deny=fd0c:ac1e:2100::/48\n",
    "http.conf": f"[general]\nenabled=yes\nbindaddr=127.0.0.1\nbindport={HTTP_PORT}\n",
}


class Intercom:
    def __init__(self, data_dir: Path, sip_port: int):
        self.file = data_dir / "intercom.json"
        self.root = data_dir / "asterisk"
        self.sip_port = sip_port
        self.settings = {"enabled": False, "servers": [], "doors": [], "panels": {}, "contacts": [], "numbers_via": None}
        if self.file.exists():
            try:
                self.settings = {**self.settings, **json.loads(self.file.read_text(encoding="utf-8"))}
            except ValueError:
                log.warning("intercom settings not readable: starting empty")
        # Before external servers had their own list, a door was one ("mode": "server"):
        # not carried over (set up again), nor the devices that went through it.
        self.settings["doors"] = [d for d in self.doors if d.get("mode", "here") == "here"]
        ids = {s["id"] for s in self.servers}
        self.settings["contacts"] = [c for c in self.extra if c.get("via") in ids]
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.binary = shutil.which("asterisk")
        # The hub's way into Asterisk's manager (doorbell.py), new each run.
        self.ami_secret = secrets.token_hex(16)
        # Panels' names (store.py), for their calls.
        self.panel_names = lambda: {}
        # Each external server's registration, as last read (watch): server id -> Registered...
        self.links: dict[str, str] = {}
        # One more at each change saved: the doors' cameras may have changed (gateway.py).
        self.generation = 0

    # ---- settings ----

    def _save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file)
        self.generation += 1

    @property
    def servers(self) -> list[dict]:
        """External SIP servers (Dahua VTO, Akuvox, a PBX...), the hub one of their monitors there."""
        return self.settings["servers"]

    @property
    def doors(self) -> list[dict]:
        """Doors registered with the hub's own server."""
        return self.settings["doors"]

    @property
    def extra(self) -> list[dict]:
        """The devices on the external servers: door stations, doorbells, monitors, phones."""
        return self.settings["contacts"]

    @property
    def accounts(self) -> dict[str, dict]:
        """Panels' accounts, by panel id."""
        return self.settings["panels"]

    def update(self, changes: dict) -> None:
        """{"enabled"?, "sip_log"?, "servers"?, "doors"?, "contacts"?, "numbers_via"?}: in each
        list, one without id is new, one left out is removed; a secret left out is kept. The
        devices of a server removed go with it."""
        self.generation += 1  # even when refused halfway
        if "enabled" in changes:
            self.settings["enabled"] = bool(changes["enabled"])
        if "sip_log" in changes:
            self.settings["sip_log"] = bool(changes["sip_log"])
        if "servers" in changes:
            wanted = changes["servers"]
            if not isinstance(wanted, list) or len(wanted) > MAX_SERVERS:
                raise IntercomInvalid(f"at most {MAX_SERVERS} servers")
            old = {s["id"]: s for s in self.servers}
            self.settings["servers"] = [self._server(w or {}, old) for w in wanted]
            ids = {s["id"] for s in self.servers}
            self.settings["contacts"] = [c for c in self.extra if c["via"] in ids]
        if "doors" in changes:
            wanted = changes["doors"]
            if not isinstance(wanted, list) or len(wanted) > MAX_DOORS:
                raise IntercomInvalid(f"at most {MAX_DOORS} doors")
            old = {d["id"]: d for d in self.doors}
            self.settings["doors"] = [self._door(w or {}, old) for w in wanted]
        if "contacts" in changes:
            wanted = changes["contacts"]
            if not isinstance(wanted, list) or len(wanted) > MAX_CONTACTS:
                raise IntercomInvalid(f"at most {MAX_CONTACTS} devices")
            old = {c["id"]: c for c in self.extra}
            ids = {s["id"] for s in self.servers}
            found = [self._contact(w or {}, old, ids) for w in wanted]
            numbers = [(c["via"], c["number"]) for c in found]
            if len(set(numbers)) != len(numbers):
                raise IntercomInvalid("two devices of one server have the same number")
            self.settings["contacts"] = found
        if "numbers_via" in changes:
            via = changes["numbers_via"] or None
            if via is not None and via not in {s["id"] for s in self.servers}:
                raise IntercomInvalid("numbers go through one of the servers")
            self.settings["numbers_via"] = via
        if self.settings.get("numbers_via") not in {s["id"] for s in self.servers}:
            self.settings["numbers_via"] = None
        self._save()

    @staticmethod
    def _camera(value) -> str | None:
        if not value:
            return None
        if not isinstance(value, str) or not CAMERA.fullmatch(value):
            raise IntercomInvalid("a camera is one of Home Assistant's (camera.…)")
        return value

    @staticmethod
    def _open(w, old: dict | None) -> dict:
        """{"kind", "code"?, "url"?, "user"?, "password"?, "entity"?}: the password kept when left out."""
        if not isinstance(w, dict):
            return old or {"kind": "none"}
        kind = w.get("kind") or "none"
        if kind not in OPENS:
            raise IntercomInvalid(f"a door opens by one of {', '.join(OPENS)}")
        if kind == "dtmf":
            code = str(w.get("code") or "").strip().upper()
            if not DTMF.fullmatch(code):
                raise IntercomInvalid("the opening code: 1 to 16 of 0-9 * # A-D")
            return {"kind": kind, "code": code}
        if kind == "http":
            url = str(w.get("url") or "").strip()
            if not URL.fullmatch(url):
                raise IntercomInvalid("the opening address: http://… or https://…")
            user = str(w.get("user") or "").strip()[:64]
            password = w["password"] if "password" in w else (old or {}).get("password", "")
            if len(str(password)) > 64 or re.search(r"[\r\n]", str(password)):
                raise IntercomInvalid("the opening password: at most 64 characters, on one line")
            return {"kind": kind, "url": url, "user": user, "password": str(password)}
        if kind == "entity":
            entity = str(w.get("entity") or "").strip()
            if not OPENER.fullmatch(entity):
                raise IntercomInvalid("the opening device: a lock, switch, script or button of Home Assistant's")
            return {"kind": kind, "entity": entity}
        return {"kind": "none"}

    @staticmethod
    def _panels(w: dict, kept: dict) -> list[str] | None:
        """Which panels ring: all (None, also those paired later), or these."""
        panels = w.get("panels", kept.get("panels"))
        return None if panels is None else [str(p) for p in panels if isinstance(p, str)][:100]

    @staticmethod
    def _name(w: dict, what: str) -> str:
        name = str(w.get("name") or "").strip()
        if not NAME.fullmatch(name):
            raise IntercomInvalid(f"a {what}'s name is 1 to 40 characters, without [ ] ; = \"")
        return name

    @staticmethod
    def _server(w: dict, old: dict) -> dict:
        name = Intercom._name(w, "server")
        kept = old.get(w.get("id"))
        if kept:
            server = {**kept, "name": name}
        else:
            sid = _door_id()
            server = {"id": sid, "name": name, "username": f"server_{sid}"}
        host = str(w.get("server") or server.get("server") or "").strip()
        user = str(w.get("user") or server.get("user") or "").strip()
        if not HOST.fullmatch(host):
            raise IntercomInvalid("the server's address: its IP or name, and :port if not 5060")
        if not USER.fullmatch(user):
            raise IntercomInvalid("the account on the server: 1 to 40 letters, digits or _ . + # * -")
        server.update(server=host, user=user)
        # Numbers typed on panels: any (as through a door station's server), or its devices only.
        numbers = w.get("numbers", server.get("numbers", "any"))
        if numbers not in ("any", "devices"):
            raise IntercomInvalid("numbers typed: any, or the server's devices only")
        server["numbers"] = numbers
        if "secret" in w:
            secret = str(w["secret"] or "")
            if len(secret) > 64 or re.search(r"[\r\n]", secret):
                raise IntercomInvalid("the account's password: at most 64 characters, on one line")
            server["secret"] = secret
        return server

    def _contact(self, w: dict, old: dict, servers: set) -> dict:
        name = self._name(w, "device")
        number = str(w.get("number") or "").strip()
        if not NUMBER.fullmatch(number):
            raise IntercomInvalid("a device's number: 1 to 32 of 0-9 * # +")
        kind = w.get("kind") or "door"
        if kind not in KINDS:
            raise IntercomInvalid(f"a device is one of {', '.join(KINDS)}")
        if w.get("via") not in servers:
            raise IntercomInvalid("a device is on one of the external servers")
        kept = old.get(w.get("id"), {})
        return {"id": kept.get("id") or _door_id(), "name": name, "number": number, "kind": kind, "via": w["via"],
                "panels": self._panels(w, kept), "camera": self._camera(w.get("camera")), "camera_sub": self._camera(w.get("camera_sub")),
                "open": self._open(w.get("open"), kept.get("open"))}

    def name_of(self, target: str) -> str | None:
        kind, _, tid = str(target).partition(":")
        found = {"door": self.doors, "contact": self.extra, "server": self.servers}.get(kind, [])
        return next((x["name"] for x in found if x["id"] == tid), None)

    def bells(self) -> list[dict]:
        """For HA: the doors and the devices that are doorbells, each a device there with its
        doorbell's events and, when it opens without a call, an Open button; and the external
        servers, each with whether the hub is linked there."""
        def bell(target: str, x: dict) -> dict:
            return {"target": target, "kind": "door", "name": x["name"], "camera": x.get("camera"),
                    "opens": (x.get("open") or {}).get("kind") in ("http", "entity")}
        return ([bell(f"door:{d['id']}", d) for d in self.doors]
                + [bell(f"contact:{c['id']}", c) for c in self.extra if c.get("kind", "door") == "door"]
                + [{"target": f"server:{s['id']}", "kind": "server", "name": s["name"], "opens": False,
                    "linked": self.links.get(s["id"]) == "Registered"} for s in self.servers])

    def panel_of(self, endpoint: str) -> str | None:
        """The panel an Asterisk endpoint (panel_x, panel_x_relay) is."""
        user = endpoint[:-len("_relay")] if endpoint.endswith("_relay") else endpoint
        return next((pid for pid, a in self.accounts.items() if a["username"] == user), None)

    def opener(self, target: str, panel_id: str | None) -> dict | None:
        """How [target] ("door:<id>", "contact:<id>") opens, if this panel may open it (any, for
        HA: panel_id None)."""
        kind, _, tid = str(target).partition(":")
        found = {"door": self.doors, "contact": self.extra}.get(kind, [])
        x = next((x for x in found if x["id"] == tid), None)
        if x is None or (panel_id is not None and not _rings(x, panel_id)):
            return None
        return x.get("open")

    @staticmethod
    def _door(w: dict, old: dict) -> dict:
        name = Intercom._name(w, "door")
        kept = old.get(w.get("id"))
        if kept:
            door = {**kept, "name": name}
        else:
            did = _door_id()
            door = {"id": did, "name": name, "username": f"door_{did}", "password": _password()}
        door["panels"] = Intercom._panels(w, door)
        door["camera"] = Intercom._camera(w.get("camera", door.get("camera")))
        # Its light stream (a camera's sub stream), shown first on a call until the main one comes.
        door["camera_sub"] = Intercom._camera(w.get("camera_sub", door.get("camera_sub")))
        door["open"] = Intercom._open(w.get("open"), door.get("open"))
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
        """A panel unpaired: its account goes, and it rings for nothing."""
        changed = self.accounts.pop(panel_id, None) is not None
        for x in self.doors + self.extra:
            if x.get("panels") and panel_id in x["panels"]:
                x["panels"].remove(panel_id)
                changed = True
        if changed:
            self._save()
            await self.apply()

    def directory(self, names: dict[str, str], panel_id: str) -> dict:
        """What [panel_id] may call (contacts)."""
        return contacts(self.doors, self.accounts, names, panel_id, self.extra, self.servers,
                        self.settings.get("numbers_via"))

    # ---- Asterisk ----

    def write_config(self) -> None:
        etc = self.root / "etc"
        for sub in ("etc", "db", "keys", "spool", "log"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        names = self.panel_names()
        panels = [{**self.accounts[p], "name": names.get(p)} for p in sorted(self.accounts)]
        users = {p: a["username"] for p, a in self.accounts.items()}
        files = {**STATIC_CONF, "asterisk.conf": asterisk_conf(self.root),
                 "pjsip.conf": pjsip_conf(self.doors, self.sip_port, panels, self.servers),
                 "extensions.conf": extensions_conf(self.doors, users, self.extra, self.servers,
                                                    self.settings.get("numbers_via")),
                 "manager.conf": manager_conf(self.ami_secret)}
        for name, text in files.items():
            (etc / name).write_text(text, encoding="utf-8")
        (etc / "pjsip.conf").chmod(0o600)
        (etc / "manager.conf").chmod(0o600)

    async def watch(self, changed) -> None:
        """The servers' registrations, read now and then; [changed]() when one links or drops."""
        while True:
            now = {}
            if self.running() and self.servers:
                found = await self.registrations()
                now = {s["id"]: found.get(s["username"], "") for s in self.servers}
            if now != self.links:
                self.links = now
                try:
                    await changed()
                except Exception:
                    log.exception("intercom: servers' links not told")
            await asyncio.sleep(LINK_CHECK_S)

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
        if not self.running():
            return
        # Panels say again how their sound goes when they connect.
        await self.cli(f"database deltree {RELAY_DB}")
        if self.settings.get("sip_log"):
            await self.sip_logger()

    async def relay(self, user: str, on: bool) -> None:
        """The panel's calls' sound through the hub (on), or by WebRTC."""
        await self.cli(f"database put {RELAY_DB} {user} 1" if on else f"database del {RELAY_DB} {user}")

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
                    # A door (or panel) Asterisk could not take: said plainly, its reason just above.
                    left = re.search(r"Could not create an object of type '(\w+)' with id '([^']+)'", text)
                    if left:
                        log.warning("intercom: Asterisk left out %s %s (see the error above): its calls will fail", *left.groups())
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


async def networks() -> list[str]:
    """This machine's IPv4 networks, as address/prefix (the LAN, Tailscale's...; not loopback or HA's own)."""
    try:
        proc = await asyncio.create_subprocess_exec("ip", "-4", "-o", "addr", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
    except (OSError, asyncio.TimeoutError):
        return []
    found = re.findall(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", out.decode())
    return [a for a in found if not a.startswith(("127.", "172.30.", "172.17."))]


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
