"""Client addressing for the multi-user layer's abuse controls.

Behind a reverse proxy ``request.remote_addr`` is the proxy, not the visitor, so a
per-address budget keyed on it silently becomes one bucket shared by the entire
service.  Forwarded headers are therefore honoured only when the immediate peer is
itself a private address -- a visitor on the public internet can never make its own
peer address private -- and the value used is the *rightmost* ``X-Forwarded-For``
entry, which the proxy in front of us appended rather than the ones the client was
free to choose.

``CF-Connecting-IP`` names a single address and is only believed when the operator
says this deployment really sits behind Cloudflare *and* the immediate peer is
itself a proxy -- declaring Cloudflare must not turn the origin port into a place
where any passer-by can choose the address their limits key on.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
from typing import NamedTuple

IP_MAX_LEN = 64
USER_AGENT_MAX_LEN = 300


class Settings(NamedTuple):
    trusted_proxy_cidrs: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
    trust_cloudflare: bool


_cached_raw: tuple[str, str] | None = None
_cached_settings = Settings((), False)


def _networks(raw: str):
    nets = []
    for chunk in (raw or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _settings() -> Settings:
    global _cached_raw, _cached_settings
    raw = (os.environ.get("MULTIUSER_TRUSTED_PROXY_CIDRS", ""),
           os.environ.get("MULTIUSER_TRUST_CLOUDFLARE", ""))
    if raw != _cached_raw:
        _cached_raw = raw
        _cached_settings = Settings(_networks(raw[0]), raw[1].strip().lower() in {"1", "true", "yes", "on"})
    return _cached_settings


def parse_ip(value: str) -> str:
    """Normalise one textual address (optionally :port or [bracketed]), or ''. """
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if value[0] == "[":
        value = value[1:].split("]", 1)[0]
    else:
        head, _, tail = value.rpartition(":")
        if head and tail.isdigit():
            value = head
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def _peer_is_trusted(peer: str, settings: Settings) -> bool:
    address = parse_ip(peer)
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if settings.trusted_proxy_cidrs:
        return any(ip in network for network in settings.trusted_proxy_cidrs)
    return ip.is_private or ip.is_loopback


def forwarded_for(headers) -> str:
    """The rightmost X-Forwarded-For address: appended by our own proxy."""
    chain = headers.get("X-Forwarded-For", "")
    for candidate in reversed(chain.split(",")):
        address = parse_ip(candidate)
        if address:
            return address
    return ""


def client_ip(request) -> str:
    """The address to audit and to key rate limits on. Never empty-ish garbage."""
    headers = getattr(request, "headers", {}) or {}
    peer = getattr(request, "remote_addr", "") or ""
    settings = _settings()
    # Every shortcut below needs the immediate peer to be a proxy. A visitor who
    # reaches the origin directly -- bypassing Cloudflare, or with no proxy at all
    # in front of this app -- must be answered with their own address, not one
    # they typed into a header, or a budget meant to be per-client is per-choice.
    if _peer_is_trusted(peer, settings):
        if settings.trust_cloudflare:
            candidate = parse_ip(headers.get("CF-Connecting-IP", ""))
            if candidate:
                return candidate[:IP_MAX_LEN]
        candidate = forwarded_for(headers)
        if candidate:
            return candidate[:IP_MAX_LEN]
    return (parse_ip(peer) or peer)[:IP_MAX_LEN]


def user_agent(request) -> str:
    return (getattr(request, "headers", {}) or {}).get("User-Agent", "")[:USER_AGENT_MAX_LEN]


def ip_bucket(ip: str) -> str:
    """A limiter key that survives an address block: IPv6 folds to its /64."""
    address = parse_ip(ip)
    if not address:
        return (ip or "unknown")[:IP_MAX_LEN]
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "unknown"
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False).network_address)
    return address


def identifier_bucket(value: str) -> str:
    """Hash a username or address so raw identities never sit in limiter memory."""
    return hashlib.sha256((value or "").strip().lower().encode("utf-8")).hexdigest()[:32]
