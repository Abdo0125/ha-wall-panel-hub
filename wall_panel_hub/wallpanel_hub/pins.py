"""Installer and user PINs: stored and sent as PBKDF2-SHA256 hashes, never as digits.

The panel checks a PIN locally against the hash it received (so the settings open
without a connection); the hub checks the PIN a panel sends with a layout edit.
"""

import base64
import hashlib
import hmac
import re
import secrets

ITERATIONS = 100_000
PIN = re.compile(r"^[0-9]{4,8}$")


class PinInvalid(ValueError):
    pass


def hash_pin(pin: str, iterations: int = ITERATIONS) -> dict:
    if not PIN.match(pin or ""):
        raise PinInvalid("a PIN is 4 to 8 digits")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
    return {
        "algo": "pbkdf2_sha256",
        "iterations": iterations,
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(digest).decode(),
    }


def verify_pin(pin: str | None, stored: dict | None) -> bool:
    if not pin or not stored or stored.get("algo") != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), base64.b64decode(stored["salt"]), stored["iterations"])
    return hmac.compare_digest(digest, base64.b64decode(stored["hash"]))
