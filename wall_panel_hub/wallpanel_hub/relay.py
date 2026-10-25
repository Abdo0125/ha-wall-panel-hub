"""A call's sound through the hub, for a panel with no network path to this machine (remote,
without a VPN): the panel's SIP goes through its connection as always (gateway.py), and so
does its sound, as G.711 frames, instead of WebRTC.

The panel then signs in to Asterisk as its "relay" endpoint (plain RTP, intercom.py). Its SDP
names no address; the hub writes in its own: a UDP port on this machine, per call, where it
puts the panel's frames into RTP for Asterisk and takes Asterisk's out.
"""

import asyncio
import logging
import random
import re
import struct
import time

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
# A call whose sound stopped both ways this long is over (its BYE lost).
IDLE_S = 60
CALL_ID = re.compile(r"^(?:Call-ID|i)\s*:\s*(\S+)", re.I | re.M)
CSEQ = re.compile(r"^CSeq\s*:\s*\d+\s+(\w+)", re.I | re.M)
MEDIA_PORT = re.compile(r"^m=audio (\d+)", re.M)


def split(sip: str) -> tuple[str, str]:
    head, sep, body = sip.partition("\r\n\r\n")
    return head, body if sep else ""


def call_id(sip: str) -> str | None:
    m = CALL_ID.search(split(sip)[0])
    return m.group(1) if m else None


def has_sdp(sip: str) -> bool:
    head, body = split(sip)
    return "application/sdp" in head.lower() and "m=audio" in body


def ends(sip: str) -> bool:
    """A BYE or CANCEL, or an INVITE refused: the call's sound is over."""
    head = split(sip)[0]
    first = head.split("\r\n", 1)[0]
    if first.startswith(("BYE ", "CANCEL ")):
        return True
    m = re.match(r"SIP/2\.0 (\d{3})", first)
    method = CSEQ.search(head)
    return bool(m and int(m.group(1)) >= 300 and method and method.group(1).upper() == "INVITE")


def media_port(sip: str) -> int | None:
    m = MEDIA_PORT.search(split(sip)[1])
    return int(m.group(1)) if m else None


def with_address(sip: str, port: int) -> str:
    """The panel's SDP with the hub's address and port in it; Content-Length counted again."""
    head, body = split(sip)
    body = re.sub(r"^c=IN IP[46] \S+", f"c=IN IP4 {HOST}", body, flags=re.M)
    body = re.sub(r"^(o=\S+ \S+ \S+ IN) IP[46] \S+", rf"\1 IP4 {HOST}", body, flags=re.M)
    body = re.sub(r"^m=audio \d+", f"m=audio {port}", body, flags=re.M)
    head = re.sub(r"^(Content-Length|l)\s*:\s*\d+", f"Content-Length: {len(body.encode())}", head, flags=re.I | re.M)
    return f"{head}\r\n\r\n{body}"


class Relay(asyncio.DatagramProtocol):
    """One call's sound: RTP with Asterisk on one side, frames to the panel on the other."""

    def __init__(self, to_panel):
        self.to_panel = to_panel  # (payload, payload type) -> None
        self.transport: asyncio.DatagramTransport | None = None
        self.asterisk: tuple[str, int] | None = None
        self.ssrc = random.getrandbits(32)
        self.seq = random.getrandbits(16)
        self.ts = random.getrandbits(32)
        self.first = True
        self.last = time.monotonic()

    @classmethod
    async def open(cls, to_panel) -> "Relay":
        relay = cls(to_panel)
        await asyncio.get_running_loop().create_datagram_endpoint(lambda: relay, local_addr=(HOST, 0))
        return relay

    @property
    def port(self) -> int:
        return self.transport.get_extra_info("sockname")[1]

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if len(data) < 12 or data[0] >> 6 != 2:
            return
        self.last = time.monotonic()
        # Asterisk answers where the sound comes from (rtp_symmetric); so does the hub.
        self.asterisk = addr
        start = 12 + 4 * (data[0] & 0x0F)
        if data[0] & 0x10 and len(data) >= start + 4:
            start += 4 + 4 * struct.unpack_from("!H", data, start + 2)[0]
        payload = data[start:]
        if data[0] & 0x20 and payload:
            payload = payload[:-payload[-1]]
        if payload:
            self.to_panel(payload, data[1] & 0x7F)

    def send(self, payload: bytes, pt: int) -> None:
        """A frame from the panel (20 ms of G.711: 160 bytes), as RTP to Asterisk."""
        if self.transport is None or self.asterisk is None or not payload:
            return
        self.last = time.monotonic()
        marker = 0x80 if self.first else 0
        self.first = False
        header = struct.pack("!BBHII", 0x80, marker | (pt & 0x7F), self.seq, self.ts, self.ssrc)
        self.seq = (self.seq + 1) & 0xFFFF
        # G.711: one byte a sample.
        self.ts = (self.ts + len(payload)) & 0xFFFFFFFF
        self.transport.sendto(header + payload, self.asterisk)

    def idle(self) -> bool:
        return time.monotonic() - self.last > IDLE_S

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None
