"""The hub's own certificate, for the panels on the LAN (docs/lan-tls-plan.md).

Made once, on the first start, and kept in tls.json (private, and in the backup: a hub restored from
it is the same hub to its panels). Panels trust it by its pin, the SHA-256 of its public key (SPKI),
never by a name or an issuer: the hub has no domain, and its address changes. So it never expires
for them, and a certificate made again with the same key keeps the pin.

The pairing code a panel shows is made from that pin too ([pairing_code]): a man in the middle cannot
make the panel's code and the add-on's agree (section 5.2).
"""

import base64
import datetime
import hashlib
import logging
import os
import ssl
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .files import read_json, write_json

log = logging.getLogger(__name__)

FILE = "tls.json"
# Trust is in the pin, not in dates: a hundred years.
VALID_DAYS = 36500
NAME = "Wall Panel Hub"


class TlsInvalid(ValueError):
    """Not a certificate and key this hub can serve."""


def make() -> dict:
    """A new key and its certificate, as PEM."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAME)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
            .sign(key, hashes.SHA256()))
    return {
        "cert": cert.public_bytes(serialization.Encoding.PEM).decode(),
        "key": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()).decode(),
    }


def spki_sha256(cert_pem: str) -> bytes:
    """The SHA-256 of the certificate's public key (SPKI, DER): what panels pin."""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
    except ValueError as e:
        raise TlsInvalid("not a certificate") from e
    spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(spki).digest()


def pin_of(spki: bytes) -> str:
    return "sha256/" + base64.b64encode(spki).decode()


def check(doc) -> None:
    """[doc] is a certificate with its own key (a backup's tls.json)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("cert"), str) or not isinstance(doc.get("key"), str):
        raise TlsInvalid("tls.json is not a certificate and its key")
    try:
        key = serialization.load_pem_private_key(doc["key"].encode(), password=None)
        cert = x509.load_pem_x509_certificate(doc["cert"].encode())
    except (ValueError, TypeError) as e:
        raise TlsInvalid("tls.json cannot be read") from e
    der = serialization.Encoding.DER
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    if key.public_key().public_bytes(der, spki) != cert.public_key().public_bytes(der, spki):
        raise TlsInvalid("tls.json: the key is not the certificate's")


def pairing_code(nonce_panel: bytes, nonce_hub: bytes, spki: bytes) -> str:
    """The 6 digits the panel and the add-on show for one pairing: SHA-256(nonce_p ‖ nonce_h ‖ spki),
    its first 8 bytes as a number, mod a million. The panel computes it with the pin it saw; the hub
    with its own. schema/fixtures.json holds a vector both sides' tests check."""
    digest = hashlib.sha256(nonce_panel + nonce_hub + spki).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"


class HubTls:
    """This hub's certificate: made once, then read from tls.json."""

    def __init__(self, data_dir: Path):
        self.file = data_dir / FILE
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.spki = b""
        self.load()

    @property
    def pin(self) -> str:
        return pin_of(self.spki)

    def load(self) -> None:
        """The certificate in tls.json, made first if there is none (or it cannot be read). Called
        again after a backup is restored: new connections get the restored one."""
        doc = read_json(self.file, private=True)
        try:
            check(doc)
        except TlsInvalid:
            if doc is not None:
                log.warning("the hub's certificate could not be read: a new one is made, paired panels must pair again")
            doc = make()
            write_json(self.file, doc, private=True)
            log.info("the hub's certificate was made")
        self.spki = spki_sha256(doc["cert"])
        # The ssl module reads PEM from files only: written for a moment, for this user alone.
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = Path(tmp) / "cert.pem", Path(tmp) / "key.pem"
            for path, text in ((cert, doc["cert"]), (key, doc["key"])):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(text)
            self.context.load_cert_chain(cert, key)
        log.info("the hub's pin: %s", self.pin)
