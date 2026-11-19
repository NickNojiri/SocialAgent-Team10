"""Keep capture away from internal addresses (docs/THREAT_MODEL.md T3, Track A #3).

A pasted link, or a redirect it leads to, must never make SpotBot's machine fetch
something on its own network: 127.0.0.1, the router, a cloud metadata address. The
admin API already refuses such links when they're pasted (serving/admin.py); this guards
the fetches themselves:

  - `blocked_reason(url)` checks a URL before the browser opens it. A hostname is looked
    up, and any private, loopback, link-local or reserved answer blocks it.
  - `get_following` / `stream_following` follow redirects one hop at a time and check
    every hop, instead of letting the HTTP client follow them blindly.

A name that doesn't resolve is let through: the fetch then fails on its own, the same
way, so there's nothing for an attacker to reach.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from contextlib import contextmanager
from typing import Iterator, Optional
from urllib.parse import urljoin, urlsplit

import httpx

MAX_REDIRECTS = 5
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
_NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+)){0,3}$", re.I)


class BlockedURL(Exception):
    """A fetch was stopped because it pointed at an internal address."""


def _bad_ip(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def _literal_ip(host: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_HOST.match(host):          # 127.1, 0x7f.0.0.1 and 2130706433 all mean 127.0.0.1
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def blocked_reason(url: str, *, resolve: bool = True) -> Optional[str]:
    """Why `url` must not be fetched, or None when it's a public http(s) address."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "malformed URL"
    if parts.scheme not in ("http", "https"):
        return "only http and https links are fetched"
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return "no host"
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        return "internal hostname"
    ip = _literal_ip(host)
    if ip is not None:
        return "internal address" if _bad_ip(ip) else None
    if not resolve:
        return None
    try:
        answers = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return None                          # doesn't resolve: the fetch fails by itself
    for *_, sockaddr in answers:
        try:
            if _bad_ip(ipaddress.ip_address(sockaddr[0].split("%")[0])):
                return "resolves to an internal address"
        except ValueError:
            return "unreadable address"
    return None


def _next_hop(resp: httpx.Response, url: str) -> Optional[str]:
    if resp.is_redirect and resp.headers.get("location"):
        return urljoin(url, resp.headers["location"])
    return None


async def get_following(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    """GET `url`, following up to MAX_REDIRECTS redirects, each checked before it's requested."""
    for _ in range(MAX_REDIRECTS + 1):
        resp = await client.get(url, follow_redirects=False, **kw)
        nxt = _next_hop(resp, url)
        if nxt is None:
            return resp
        reason = blocked_reason(nxt)
        if reason:
            raise BlockedURL(f"redirect to {nxt!r} refused: {reason}")
        url = nxt
    raise BlockedURL("too many redirects")


@contextmanager
def stream_following(url: str, **kw) -> Iterator[httpx.Response]:
    """httpx.stream("GET", …) with the same per-hop check, for downloads."""
    for _ in range(MAX_REDIRECTS + 1):
        with httpx.stream("GET", url, follow_redirects=False, **kw) as resp:
            nxt = _next_hop(resp, url)
            if nxt is None:
                yield resp
                return
        reason = blocked_reason(nxt)
        if reason:
            raise BlockedURL(f"redirect to {nxt!r} refused: {reason}")
        url = nxt
    raise BlockedURL("too many redirects")
