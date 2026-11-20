"""Egress register: every connection ComfyOS opens out of the home network.

Everything that leaves the home network is listed here with purpose, data sent and trigger.
Clients for external targets are only created through client()/sync_client(); they allow only the
registered hosts – on redirects too. Addresses the user enters themselves
(flow HTTP) go through GuardedTransport: resolve DNS, block the home network, connect to exactly the
checked IP. Home Base and Hue Bridge are home network and therefore not listed here.
Plugins that do not speak HTTP (MQTT, reachability) get their TCP connection through open_stream() –
with the same limits: LAN targets only into the home network, internet targets only to the declared hosts.
UDP exists only in the home network (open_datagram(), ssdp_search(); since 0.22): sender and receiver must
be home network addresses, size and send rate are limited – ComfyOS never becomes an amplifier in the LAN.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import ssl
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from . import jsonfile
from .paths import DATA_DIR, ensure_dirs


@dataclass(frozen=True)
class Egress:
    """One entry in the egress register: where ComfyOS may connect to, what for, with which data and what triggers it."""
    id: str
    title: str
    hosts: tuple[str, ...]      # exact or "*.domain"; "*" = entered by the user (only with GuardedTransport)
    purpose: str
    data: str
    trigger: str


# An own update server via COMFYOS_UPDATE_API (a deliberate setting of the operator) counts too.
_UPDATE_EXTRA = tuple(h for h in [urlsplit(os.getenv("COMFYOS_UPDATE_API", "")).hostname] if h)
# Licence provider: only via HTTPS and only if COMFYOS_LICENSE_URL is set – otherwise there is no target.
_LICENSE_URL = urlsplit(os.getenv("COMFYOS_LICENSE_URL", "").strip())
_LICENSE_HOST = tuple(h for h in [_LICENSE_URL.hostname] if h and _LICENSE_URL.scheme == "https")

# Texts are shown one entry per line group under System (tile "Datenschutz", since 0.23.1 kept short): title in a few
# words, purpose in one line, data and trigger as short phrases – the full picture stays, without a wall of text.
REGISTRY: dict[str, Egress] = {e.id: e for e in (
    Egress("weather", "Wetter (Open-Meteo)", ("geocoding-api.open-meteo.com", "api.open-meteo.com"),
           "Außentemperatur und Sonnenzeiten",
           "PLZ bzw. Ort, Koordinaten auf ca. 1 km – nie Straße oder Hausnummer",
           "nur mit Adresse und eingeschaltetem Wetter"),
    Egress("weather_warnings", "Wetterwarnungen (DWD)", ("maps.dwd.de",),
           "Amtliche Warnungen für deinen Ort",
           "Koordinaten auf ca. 1 km – nie die Adresse",
           "nur mit Adresse, Wetter und Warnungen an"),
    Egress("updates", "Updates (GitHub)", ("api.github.com", "github.com", "*.githubusercontent.com") + _UPDATE_EXTRA,
           "Neue ComfyOS-Versionen suchen und laden",
           "installierte Version, IP-Adresse",
           "nur auf Knopfdruck"),
    Egress("flow_http", "Flow: HTTP-Aufruf", ("*",),
           "Aufrufe, die du selbst in Flows anlegst",
           "was du im Baustein einträgst",
           "wenn der Flow auslöst"),
    Egress("push", "Push-Nachrichten",
           ("fcm.googleapis.com", "*.push.apple.com", "updates.push.services.mozilla.com", "*.notify.windows.com"),
           "Flow-Nachrichten aufs Handy oder den Rechner",
           "verschlüsselte Nachricht – für den Dienst unlesbar",
           "nur mit eingeschaltetem Push, wenn ein Flow sendet"),
    Egress("license_provider", "Lizenzprüfung", _LICENSE_HOST,
           "Kaufschlüssel einlösen und prüfen",
           "Schlüssel und anonyme Geräte-Kennung – keine Nutzungsdaten",
           "nur mit Kaufschlüssel, etwa alle 24 Stunden"),
)}

LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain", ".fritz.box")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")   # u. a. Tailscale/VPN-Adressen
USAGE_FILE = DATA_DIR / "egress.json"

# Replaceable for tests: transport of all register clients, DNS resolution, transport of the LAN clients.
_transport: httpx.AsyncBaseTransport | None = None
_lan_transport: httpx.AsyncBaseTransport | None = None
_sync_transport: httpx.BaseTransport | None = None
# TCP connections of the plugins (open_stream): in tests a peer in memory instead of a socket
_stream_factory: Callable[[str, int, bool], Awaitable[tuple[Any, Any]]] | None = None
STREAM_TIMEOUT_MAX = 30.0
# UDP of the plugins (open_datagram): in tests a peer in memory instead of a socket. The factory gets
# (port, multicast, deliver) and returns an object with sendto(data, address) and close(); whatever the peer
# "sends" it hands to deliver(data, sender).
_datagram_factory: Callable[[int, str | None, Callable[[bytes, tuple], None]], Any] | None = None
MAX_DATAGRAM = 9000                 # Bytes je Datagramm (mDNS erlaubt bis 9000, SSDP bleibt weit darunter)
DATAGRAM_SEND_PER_SECOND = 40       # a socket sends at most this many datagrams per second
SSDP_GROUP, SSDP_PORT = "239.255.255.250", 1900
MULTICAST_GROUPS = (SSDP_GROUP, "224.0.0.251", "ff02::fb")    # SSDP, mDNS (IPv4 and IPv6) – there are no other groups
SSDP_TIMEOUT = (0.5, 5.0)           # limits for the duration of a search in seconds
_usage: dict[str, float] = {}
_usage_lock = threading.Lock()
_usage_written = 0.0


class EgressBlocked(httpx.RequestError):
    """The target is not allowed (foreign host or home network address)."""


class StreamBlocked(Exception):
    """The TCP target of a plugin is not allowed (open_stream)."""


class DatagramBlocked(Exception):
    """UDP of a plugin is not allowed this way (open_datagram): target outside the home network, foreign group, too
    large, too fast."""


# ---------------------------------------------------------------- Checks
def host_allowed(entry: Egress, host: str | None) -> bool:
    """Is the host on the list of the entry? Compared exactly or as a subdomain ("*.domain"); "*" allows
    any name."""
    h = (host or "").lower().rstrip(".")
    if not h:
        return False
    for pat in entry.hosts:
        if pat == "*":
            return True
        if pat.startswith("*.") and h.endswith(pat[1:]):
            return True
        if h == pat:
            return True
    return False


def is_local_ip(value: str) -> bool:
    """Is the IP address in the home network or otherwise not public (private, loopback, link-local, reserved,
    multicast, CGNAT)? Also for IPv4 in IPv6 notation and for link-local IPv6 addresses with an interface
    ("fe80::1%eth0")."""
    try:
        ip = ipaddress.ip_address(value.strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified
            or ip.is_multicast or (ip.version == 4 and ip in _CGNAT))


def is_local_name(host: str) -> bool:
    """Is this a name from the home network – without a dot, "localhost" or with a local suffix such as .local or
    .fritz.box?"""
    h = (host or "").lower().rstrip(".")
    if ":" in h:      # IPv6 address, not a name – is_local_ip decides about that
        return False
    return h == "localhost" or "." not in h or ("." + h).endswith(LOCAL_SUFFIXES)   # "fritz.box" itself too


def is_local_host(host: str) -> bool:
    """Does the host (name or IP) point into the home network?"""
    return is_local_name(host) or is_local_ip(host)


def own_address_towards(host: str) -> str | None:
    """Return the IPv4 address ComfyOS has in the network of a device in the home network, or None.

    Needed where a device has to call ComfyOS back (a speaker fetching the live audio stream). The operating
    system is only asked which of its addresses leads to `host`: a UDP socket is aimed there, nothing is sent.
    """
    if not is_local_ip(host):
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host, 9))
            own = probe.getsockname()[0]
    except (OSError, ValueError):
        return None                     # no route to that network (or not an IPv4 address)
    return own if is_local_ip(own) and own != "0.0.0.0" else None


# ---------------------------------------------------------------- Nutzung
def mark_used(eid: str) -> None:
    """Remember that an entry was just used (display "zuletzt verwendet"). Written at most once
    per minute."""
    global _usage_written
    now = time.time()
    with _usage_lock:
        if not _usage:
            _usage.update(_read_usage())
        _usage[eid] = now
        if now - _usage_written < 60:
            return
        _usage_written = now
        data = dict(_usage)
    try:
        ensure_dirs()
        jsonfile.write(USAGE_FILE, data, indent=None, ensure_ascii=True)
    except OSError:
        pass   # "last used" is only a display – without the file it is missing after a restart


def _read_usage() -> dict[str, float]:
    """When which register entry was last used (known entries only). The file is a cache."""
    try:
        return {k: float(v) for k, v in jsonfile.read(USAGE_FILE, {}, keep=False).items() if k in REGISTRY}
    except (TypeError, ValueError):
        return {}


def last_used() -> dict[str, float]:
    """When each entry was last used {id: time}."""
    with _usage_lock:
        if not _usage:
            _usage.update(_read_usage())
        return dict(_usage)


def _guard(entry: Egress, request: httpx.Request) -> None:
    """Check a request before it goes out: the host must be on the list of the entry, otherwise EgressBlocked.
    Entries with "*" only work through GuardedTransport."""
    if entry.hosts == ("*",) or not host_allowed(entry, request.url.host):
        raise EgressBlocked(f"Verbindung zu {request.url.host} ist nicht freigegeben ({entry.title}).", request=request)
    mark_used(entry.id)


# ---------------------------------------------------------------- Clients
def client(eid: str, **kwargs: Any) -> httpx.AsyncClient:
    """AsyncClient that only reaches the hosts of the register entry (after redirects too)."""
    entry = REGISTRY[eid]

    async def check(request: httpx.Request) -> None:
        _guard(entry, request)

    if _transport is not None:
        kwargs.setdefault("transport", _transport)
    return httpx.AsyncClient(event_hooks={"request": [check]}, **kwargs)


def register(entry: Egress) -> None:
    """Add a target of a plugin to the register (from its manifest; appears under System → Externe Verbindungen)."""
    REGISTRY[entry.id] = entry


def lan_client(**kwargs: Any) -> httpx.AsyncClient:
    """AsyncClient for devices in the home network (plugins with `"scope": "lan"`): reaches only local addresses and
    names – after redirects too. The home network is not in the register; this client never gets outside."""
    async def check(request: httpx.Request) -> None:
        if not is_local_host(request.url.host or ""):
            raise EgressBlocked(f"{request.url.host} liegt nicht im Heimnetz.", request=request)

    if _lan_transport is not None:
        kwargs.setdefault("transport", _lan_transport)
        kwargs.pop("verify", None)
    timeout = kwargs.pop("timeout", 8.0)
    if not isinstance(timeout, httpx.Timeout):
        timeout = httpx.Timeout(float(timeout), connect=4.0)
    return httpx.AsyncClient(event_hooks={"request": [check]}, timeout=timeout, **kwargs)


def sync_client(eid: str, **kwargs: Any) -> httpx.Client:
    """Synchronous HTTP client for an entry of the register – every request is checked against its hosts."""
    entry = REGISTRY[eid]

    def check(request: httpx.Request) -> None:
        _guard(entry, request)

    if _sync_transport is not None:
        kwargs.setdefault("transport", _sync_transport)
    return httpx.Client(event_hooks={"request": [check]}, **kwargs)


async def _getaddrinfo(host: str, port: int) -> list[str]:
    """IP addresses of a name (without duplicates)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(i[4][0]) for i in infos))


resolve: Callable[[str, int], Awaitable[list[str]]] = _getaddrinfo


async def open_stream(host: str, port: int, entry: Egress | None = None, tls: bool = False, verify: bool = True,
                      timeout: float = 5.0) -> tuple[Any, Any]:
    """TCP connection for a plugin (`context.open_stream`), returns (StreamReader, StreamWriter).

    `entry=None` means home network (`"scope": "lan"`): the host must be a name or an address in the home network, a
    name must also resolve to there, and the connection goes to exactly the checked address (no DNS rebinding).
    With `entry` (target in the internet) only its hosts are allowed; certificate verification always applies there.
    Raises StreamBlocked (not allowed) or OSError/TimeoutError (unreachable, port closed)."""
    name = str(host or "").strip().lower().rstrip(".")
    if not name or len(name) > 253 or any(c.isspace() or c in "/@\\" for c in name):
        raise StreamBlocked("Ungültige Adresse.")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise StreamBlocked("Ungültiger Port.")
    target = name
    if entry is None:
        if not is_local_host(name):
            raise StreamBlocked(f"{name} liegt nicht im Heimnetz.")
        if not is_local_ip(name):          # name: must point into the home network – the connection goes to exactly this address
            ips = await resolve(name, port)
            if not ips or not all(is_local_ip(ip) for ip in ips):
                raise StreamBlocked(f"{name} zeigt nicht ins Heimnetz.")
            ips.sort(key=lambda a: ":" in a)   # IPv4 zuerst
            target = ips[0]
    else:
        if entry.hosts == ("*",) or not host_allowed(entry, name):
            raise StreamBlocked(f"Verbindung zu {name} ist nicht freigegeben ({entry.title}).")
        verify = True
        mark_used(entry.id)
    if _stream_factory is not None:
        return await _stream_factory(name, port, bool(tls))
    context = None
    if tls:
        context = ssl.create_default_context()
        if not verify:                     # self-signed certificate of a device in the home network
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
    limit = max(0.5, min(STREAM_TIMEOUT_MAX, float(timeout)))
    return await asyncio.wait_for(
        asyncio.open_connection(target.strip("[]"), port, ssl=context, server_hostname=name if tls else None), limit)


# ---------------------------------------------------------------- UDP im Heimnetz
class _DatagramProtocol(asyncio.DatagramProtocol):
    """Reicht ankommende Datagramme eines echten Sockets an den LanDatagram weiter."""

    def __init__(self, deliver: Callable[[bytes, tuple], None]):
        """`deliver` gets every datagram with its sender."""
        self._deliver = deliver

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        """Ein Datagramm ist angekommen."""
        self._deliver(data, addr)

    def error_received(self, exc: Exception) -> None:
        """Error of the socket (e.g. ICMP "unreachable"): UDP knows no delivery – whoever needs an answer
        notices its absence themselves."""


class LanDatagram:
    """UDP socket of a plugin in the home network (`context.open_datagram`).

    Sending only goes to IP addresses in the home network, at most MAX_DATAGRAM bytes and DATAGRAM_SEND_PER_SECOND
    datagrams per second. What arrives from outside the home network or is too large never reaches the plugin."""

    def __init__(self) -> None:
        """Without a line yet; `open_datagram` attaches the socket (or the peer of a test)."""
        self._wire: Any = None
        self._callbacks: list[Callable[[bytes, tuple[str, int]], None]] = []
        self._sent: deque[float] = deque()
        self.closed = False

    def send(self, data: bytes, address: tuple[str, int]) -> None:
        """Send a datagram. Raises DatagramBlocked: socket closed, no target in the home network, too large, too fast."""
        if self.closed or self._wire is None:
            raise DatagramBlocked("Der UDP-Socket ist geschlossen.")
        if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= MAX_DATAGRAM:
            raise DatagramBlocked(f"Ein Datagramm darf höchstens {MAX_DATAGRAM} Bytes haben.")
        try:
            host, port = str(address[0]).strip("[]"), address[1]
        except (TypeError, IndexError, KeyError):
            raise DatagramBlocked("Ungültige Adresse.")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise DatagramBlocked("Ungültiger Port.")
        if not is_local_ip(host):          # IP addresses only: a name could be bent after the check
            raise DatagramBlocked("UDP geht nur an IP-Adressen im Heimnetz.")
        now = time.monotonic()
        while self._sent and now - self._sent[0] > 1.0:
            self._sent.popleft()
        if len(self._sent) >= DATAGRAM_SEND_PER_SECOND:
            raise DatagramBlocked("Zu viele Datagramme je Sekunde.")
        self._sent.append(now)
        self._wire.sendto(bytes(data), (host, port))

    def on_datagram(self, callback: Callable[[bytes, tuple[str, int]], None]) -> None:
        """Register a callback for incoming datagrams: `callback(data, (address, port))`."""
        self._callbacks.append(callback)

    def _deliver(self, data: bytes, sender: tuple) -> None:
        """Check an incoming datagram and pass it on: only from the home network, only up to the size limit. An error
        in the callback stays there."""
        try:
            host, port = str(sender[0]), int(sender[1])
        except (TypeError, ValueError, IndexError):
            return
        if self.closed or len(data) > MAX_DATAGRAM or not is_local_ip(host):
            return
        for callback in list(self._callbacks):
            try:
                callback(bytes(data), (host, port))
            except Exception:
                continue   # foreign callback (plugin): its error must not abort reception for the others

    def close(self) -> None:
        """Close the socket (allowed several times)."""
        if self.closed:
            return
        self.closed = True
        self._callbacks.clear()
        if self._wire is not None:
            self._wire.close()


def _datagram_socket(port: int, multicast: str | None) -> socket.socket:
    """Bind a UDP socket, join a multicast group on request. Bound with SO_REUSEADDR so that
    ComfyOS shares a port such as 1900 or 5353 with other services of the machine (avahi, helper programs)."""
    v6 = bool(multicast and ":" in multicast)
    sock = socket.socket(socket.AF_INET6 if v6 else socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("::" if v6 else "", port))
        if multicast and v6:
            group = socket.inet_pton(socket.AF_INET6, multicast) + struct.pack("@I", 0)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, group)
        elif multicast:
            group = socket.inet_aton(multicast) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group)
        if not v6:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)    # bleibt im Heimnetz (ein Router weiter)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


async def open_datagram(port: int = 0, multicast: str | None = None) -> LanDatagram:
    """UDP socket for a plugin in the home network (`context.open_datagram`). `port` = local port (0 = any free one),
    `multicast` = group the socket joins (only MULTICAST_GROUPS). Raises DatagramBlocked (not allowed) or
    OSError (the port cannot be opened)."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise DatagramBlocked("Ungültiger Port.")
    if multicast is not None and multicast not in MULTICAST_GROUPS:
        raise DatagramBlocked("Diese Multicast-Gruppe ist nicht freigegeben (nur SSDP und mDNS).")
    channel = LanDatagram()
    if _datagram_factory is not None:
        channel._wire = _datagram_factory(port, multicast, channel._deliver)
        return channel
    sock = _datagram_socket(port, multicast)
    try:
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _DatagramProtocol(channel._deliver), sock=sock)
    except Exception:
        sock.close()
        raise
    channel._wire = transport
    return channel


def parse_ssdp(data: bytes, sender: str, search_target: str) -> dict[str, Any] | None:
    """Read the answer to an SSDP search. None for everything that does not fit: not an answer, other search target, no
    description, or a description that is not at the sender (a device never points to another one)."""
    lines = data[:MAX_DATAGRAM].decode("utf-8", "replace").split("\r\n")
    if not lines or not lines[0].upper().startswith("HTTP/1.1 200"):
        return None
    headers: dict[str, str] = {}
    for line in lines[1:41]:
        name, sep, value = line.partition(":")
        if sep and name.strip() and len(line) <= 600:
            headers.setdefault(name.strip().lower(), value.strip())
    if search_target != "ssdp:all" and headers.get("st") != search_target:
        return None
    location = headers.get("location") or ""
    try:
        parts = urlsplit(location)
        where = parts.hostname or ""
    except ValueError:
        return None
    here = sender.split("%", 1)[0]
    if parts.scheme not in ("http", "https") or not where or where.split("%", 1)[0] != here:
        return None
    return {"host": here, "location": location, "usn": headers.get("usn", ""), "search_target": headers.get("st", ""),
            "server": headers.get("server", ""), "headers": headers}


async def ssdp_search(search_target: str, timeout: float = 3.0) -> list[dict[str, Any]]:
    """Search for devices in the home network via SSDP (`context.discover`): two search requests to the SSDP group, then
    collect the answers until the time is up. Every device is in the list once (form: parse_ssdp).
    Raises DatagramBlocked (invalid search target) or OSError (no socket)."""
    target = str(search_target or "").strip()
    if not target or len(target) > 200 or any(ord(c) < 33 or ord(c) > 126 for c in target):
        raise DatagramBlocked("Ungültiges Suchziel.")
    low, high = SSDP_TIMEOUT
    wait = max(low, min(high, float(timeout)))
    found: dict[str, dict[str, Any]] = {}

    def take(data: bytes, sender: tuple[str, int]) -> None:
        answer = parse_ssdp(data, sender[0], target)
        if answer is not None:
            found.setdefault(answer["usn"] or answer["location"], answer)

    message = ("M-SEARCH * HTTP/1.1\r\n"
               f"HOST: {SSDP_GROUP}:{SSDP_PORT}\r\n"
               'MAN: "ssdp:discover"\r\n'
               f"MX: {max(1, min(3, int(wait)))}\r\n"
               f"ST: {target}\r\n\r\n").encode("ascii")
    channel = await open_datagram(0)
    channel.on_datagram(take)
    try:
        channel.send(message, (SSDP_GROUP, SSDP_PORT))
        pause = min(0.3, wait / 3)
        await asyncio.sleep(pause)
        channel.send(message, (SSDP_GROUP, SSDP_PORT))      # UDP geht verloren: einmal wiederholen
        await asyncio.sleep(wait - pause)
    finally:
        channel.close()
    return list(found.values())


class GuardedTransport(httpx.AsyncBaseTransport):
    """For addresses entered by the user: block targets in the home network – through DNS names too –
    and connect to exactly the checked IP (no second resolution, no DNS rebinding)."""

    def __init__(self, allow_local: bool = False, inner: httpx.AsyncBaseTransport | None = None,
                 eid: str = "flow_http"):
        """`allow_local`: targets in the home network are allowed. `eid` is the entry of the register the use is
        attributed to."""
        self.allow_local = allow_local
        self.inner = inner or _transport or httpx.AsyncHTTPTransport()
        self.eid = eid

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Check the target and connect: block names and addresses in the home network (except with `allow_local`),
        resolve the name once and connect to exactly the checked IP."""
        url = request.url
        host = (url.host or "").lower()
        if not self.allow_local:
            if is_local_name(host):
                raise EgressBlocked("Adresse im Heimnetz", request=request)
            try:
                ipaddress.ip_address(host.strip("[]"))
                literal = True
            except ValueError:
                literal = False
            if literal:
                if is_local_ip(host):
                    raise EgressBlocked("Adresse im Heimnetz", request=request)
            else:
                port = url.port or (443 if url.scheme == "https" else 80)
                try:
                    ips = await resolve(host, port)
                except OSError as exc:
                    raise httpx.ConnectError(f"Name nicht auflösbar ({type(exc).__name__})", request=request)
                if not ips or any(is_local_ip(ip) for ip in ips):
                    raise EgressBlocked("Name zeigt ins Heimnetz", request=request)
                ips.sort(key=lambda a: ":" in a)   # IPv4 zuerst
                request.url = url.copy_with(host=ips[0])
                if url.scheme == "https":
                    request.extensions = {**request.extensions, "sni_hostname": host}
        mark_used(self.eid)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        """Close the underlying transport."""
        await self.inner.aclose()


def status() -> list[dict[str, Any]]:
    """The register for the display under System: per entry targets, purpose, data, trigger and last use."""
    used = last_used()
    return [{"id": e.id, "title": e.title, "hosts": [h for h in e.hosts if h != "*"], "purpose": e.purpose,
             "data": e.data, "trigger": e.trigger, "last_used": used.get(e.id)} for e in REGISTRY.values()]
