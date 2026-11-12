"""Limits on what reaches the add-on through Home Assistant's address (docs/audit-9-security.md).

Those views need no Home Assistant login, so anyone on the internet can call them. Per address:
a few pairing requests a minute, and a bounded number of open panel connections. The add-on
cannot tell the callers apart (they all come from Home Assistant), so the limits live here.

No Home Assistant imports: the add-on's tests load this file on its own.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

# A pairing request's id, as the add-on makes it (secrets.token_urlsafe): nothing else is forwarded,
# so no path of the add-on's beyond pairing is reachable from here (item 2).
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

PAIRS_PER_MINUTE = 5
# A site behind one address has many panels: each holds one connection, and a few more while reconnecting.
SOCKETS_PER_ADDRESS = 32


class Limits:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pairs: dict[str, list[float]] = {}
        self._sockets: dict[str, int] = {}

    def pair(self, address: str) -> bool:
        """True when [address] may send one more pairing request now."""
        now = self._clock()
        recent = [t for t in self._pairs.get(address, []) if now - t < 60]
        if len(recent) >= PAIRS_PER_MINUTE:
            self._pairs[address] = recent
            return False
        recent.append(now)
        self._pairs[address] = recent
        # Addresses with nothing recent are dropped, so the table stays small.
        if len(self._pairs) > 1000:
            self._pairs = {a: ts for a, ts in self._pairs.items() if ts and now - ts[-1] < 60}
        return True

    def open_socket(self, address: str) -> bool:
        """True and counted when [address] may open one more connection; close_socket when it ends."""
        if self._sockets.get(address, 0) >= SOCKETS_PER_ADDRESS:
            return False
        self._sockets[address] = self._sockets.get(address, 0) + 1
        return True

    def close_socket(self, address: str) -> None:
        left = self._sockets.get(address, 0) - 1
        if left > 0:
            self._sockets[address] = left
        else:
            self._sockets.pop(address, None)
