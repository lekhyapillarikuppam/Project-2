"""Per-IP upload limit (D30, SPEC §4): a sliding window kept in memory, so it resets on restart."""

import ipaddress
import math
import threading
import time
from collections import deque
from collections.abc import Callable

WINDOW_SECONDS = 3600.0
# Networks Docker assigns to compose networks (RFC 1918, IPv6 unique-local). Only Caddy shares the
# api container's network on the VM; the api port is not published on the host.
PROXY_NETWORKS = tuple(
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


def is_trusted_proxy(peer: str | None) -> bool:
    """True if the direct peer can be the Caddy container: an address on a private (Docker) network.

    Loopback, public and documentation addresses are not trusted, and neither is anything that does
    not parse as an IP address (e.g. Starlette's "testclient").
    """
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in PROXY_NETWORKS)


def client_ip(peer: str | None, forwarded_for: str | None) -> str:
    """The client to count: the first `X-Forwarded-For` address when the peer is the proxy, else the peer."""
    if forwarded_for and is_trusted_proxy(peer):
        first = forwarded_for.split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(first))
        except ValueError:
            pass  # malformed header: fall back to the peer
    return peer or "unknown"


class UploadRateLimiter:
    """At most `limit` uploads per key in any rolling `window_seconds`. limit 0 disables it."""

    def __init__(
        self,
        limit: int,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int | None:
        """Count one upload for `key`. Returns None if allowed, else the seconds until a slot frees
        up (for Retry-After); a rejected attempt is not recorded."""
        if self.limit <= 0:
            return None
        now = self.clock()
        with self._lock:
            self._prune(now)
            hits = self._hits.setdefault(key, deque())
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            return None

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]
