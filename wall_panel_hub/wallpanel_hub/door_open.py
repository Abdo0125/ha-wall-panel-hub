"""Opening a door from a panel (intercom.py, "open"): an address of the door station's own
(Dahua's accessControl.cgi, with its user name and password: Digest or Basic, as it asks), or
a device of Home Assistant's (a lock, a switch, a script...). A code sent during the call
(DTMF) is sent by the panel itself."""

import hashlib
import logging
import os
import re

import aiohttp

log = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10)
# What each kind of device does to open.
SERVICES = {
    "lock": "unlock", "switch": "turn_on", "input_boolean": "turn_on", "script": "turn_on",
    "button": "press", "input_button": "press", "cover": "open_cover",
}


class OpenFailed(Exception):
    pass


def _digest(challenge: str, method: str, url: str, user: str, password: str) -> str:
    fields = dict(re.findall(r'(\w+)="?([^",]*)"?', challenge))
    realm, nonce = fields.get("realm", ""), fields.get("nonce", "")
    uri = re.sub(r"^https?://[^/]+", "", url) or "/"
    algorithm = fields.get("algorithm", "MD5")
    h = hashlib.sha256 if algorithm.upper().startswith("SHA-256") else hashlib.md5

    def hx(s: str) -> str:
        return h(s.encode()).hexdigest()

    ha1, ha2 = hx(f"{user}:{realm}:{password}"), hx(f"{method}:{uri}")
    parts = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"', f"algorithm={algorithm}"]
    if "auth" in fields.get("qop", "").split(","):
        cnonce, nc = os.urandom(8).hex(), "00000001"
        parts += [f'response="{hx(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")}"', "qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
    else:
        parts.append(f'response="{hx(f"{ha1}:{nonce}:{ha2}")}"')
    if "opaque" in fields:
        parts.append(f'opaque="{fields["opaque"]}"')
    return "Digest " + ", ".join(parts)


async def by_address(session: aiohttp.ClientSession, url: str, user: str = "", password: str = "") -> None:
    """GET [url]; signed in as the door asks when it answers 401."""
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status == 401 and user:
                challenge = resp.headers.get("WWW-Authenticate", "")
            elif resp.status < 300:
                return
            else:
                raise OpenFailed(f"the door answered {resp.status}")
        if challenge.lower().startswith("digest"):
            auth = _digest(challenge, "GET", url, user, password)
        else:
            auth = aiohttp.BasicAuth(user, password).encode()
        async with session.get(url, headers={"Authorization": auth}, timeout=TIMEOUT) as resp:
            if resp.status >= 300:
                raise OpenFailed(f"the door answered {resp.status}")
    except (aiohttp.ClientError, TimeoutError) as e:
        raise OpenFailed(f"the door did not answer: {e}") from e


async def by_device(session: aiohttp.ClientSession, ha_http: str, auth: dict, entity_id: str) -> None:
    domain = entity_id.split(".", 1)[0]
    service = SERVICES.get(domain)
    if service is None:
        raise OpenFailed(f"{entity_id} cannot open")
    try:
        async with session.post(f"{ha_http}/api/services/{domain}/{service}", headers=auth,
                                json={"entity_id": entity_id}, timeout=TIMEOUT) as resp:
            if resp.status >= 300:
                raise OpenFailed(f"Home Assistant answered {resp.status}")
    except (aiohttp.ClientError, TimeoutError) as e:
        raise OpenFailed(f"Home Assistant did not answer: {e}") from e
