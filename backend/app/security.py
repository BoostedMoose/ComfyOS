"""Protection of the web interface against attacks from the home network and through the browser.

* Host check against DNS rebinding: accepted are IP addresses, names in the home network
  (without a dot, .local, .fritz.box …), the machine's own host name and names the user entered.
* Cross-site requests: writing requests with a foreign Origin/Referer are rejected.
  Browsers always send Origin with POST/PUT/PATCH/DELETE – if it is missing, it is not a browser
  (script, webhook) and therefore not CSRF.
* Size limit for requests, security headers for every response.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import time
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from .egress import LOCAL_SUFFIXES

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_BODY = 4 * 1024 * 1024
UNLIMITED_PATHS = {"/api/update/upload"}       # own, higher limit when streaming
HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9](?:[a-z0-9-]{0,62}))*$")
# Host header: like HOST_RE, plus the underscore (some host names in the home network)
NAME_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,62})(?:\.[a-z0-9_](?:[a-z0-9_-]{0,62}))*$")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; manifest-src 'self'; worker-src 'self'; object-src 'none'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

_extra_cache: tuple[float, frozenset[str]] = (0.0, frozenset())
_blocked_logged: dict[str, float] = {}
_hits: dict[str, list[float]] = {}


def rate_ok(key: str, limit: int, window: float) -> bool:
    """Simple rate limit per key (e.g. webhook per IP)."""
    now = time.time()
    hits = [t for t in _hits.get(key, []) if now - t < window]
    if len(hits) >= limit:
        _hits[key] = hits
        return False
    hits.append(now)
    _hits[key] = hits
    if len(_hits) > 2000:
        _hits.clear()
    return True


def clean_hosts(values) -> list[str]:
    """Clean the list of further allowed host names: lower case, valid, without duplicates, at most ten. IP addresses
    are dropped – they are allowed anyway."""
    out: list[str] = []
    for v in values or []:
        h = str(v or "").strip().lower().rstrip(".")
        if h.startswith("[") or ":" in h:
            continue                      # IP-Adressen sind ohnehin erlaubt
        if HOST_RE.match(h) and len(h) <= 253 and h not in out:
            out.append(h)
    return out[:10]


def _extra_hosts() -> frozenset[str]:
    """Host names that are allowed besides IP addresses and local names: from the settings, from
    COMFYOS_ALLOWED_HOSTS and the machine's own host name. Remembered for ten seconds."""
    global _extra_cache
    now = time.time()
    if now - _extra_cache[0] < 10:
        return _extra_cache[1]
    from .settings_store import load_settings
    hosts = set(clean_hosts(load_settings().get("allowed_hosts") or []))
    hosts.update(clean_hosts((os.getenv("COMFYOS_ALLOWED_HOSTS") or "").split(",")))
    try:
        name = socket.gethostname().lower()
        hosts.update({name, name + ".local"})
        fqdn = socket.getfqdn().lower()
        if fqdn and fqdn != "localhost":
            hosts.add(fqdn)
    except OSError:
        pass   # host name cannot be determined: then only the entered names
    _extra_cache = (now, frozenset(hosts))
    return _extra_cache[1]


def reset_cache() -> None:
    """Discard the remembered host names (after the settings changed)."""
    global _extra_cache
    _extra_cache = (0.0, frozenset())


def split_host(value: str) -> tuple[str, str]:
    """'Host' header -> (host, port) without brackets for IPv6."""
    v = (value or "").strip().lower()
    if v.startswith("["):
        end = v.find("]")
        return v[1:end], v[end + 2:] if end != -1 and v[end + 1:end + 2] == ":" else ""
    host, _, port = v.partition(":")
    return host, port


def host_allowed(host_header: str) -> bool:
    """May ComfyOS answer under this Host header? IP addresses and names from the home network always, otherwise only
    entered ones – that way DNS rebinding does not work."""
    host, _ = split_host(host_header)
    host = host.rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True                       # IP address: no DNS rebinding possible
    except ValueError:
        pass   # not an IP address: checked as a name below
    if not NAME_RE.match(host) or len(host) > 253:
        return False                      # not a valid host name (space, #, @, comma …)
    if host == "localhost" or "." not in host or host.endswith(LOCAL_SUFFIXES):
        return True
    return host in _extra_hosts()


def _norm(host: str, port: str, scheme: str) -> str:
    """Host and port in comparable form, with the default port of the scheme."""
    default = "443" if scheme == "https" else "80"
    return f"{host.rstrip('.')}:{port or default}"


def same_origin(request: Request) -> bool:
    """Foreign Origin (or Referer) on a writing request? Without both: not a browser -> ok."""
    src = request.headers.get("origin")
    if src is None:
        src = request.headers.get("referer")
        if src is None:
            return True
    if src == "null":
        return False
    u = urlsplit(src)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host, port = split_host(request.headers.get("host", ""))
    origin_port = str(u.port) if u.port else ""
    # the Host header carries no scheme: add the port with the scheme of the Origin
    return _norm(u.hostname.lower(), origin_port, u.scheme) == _norm(host, port, u.scheme)


def blocked_host_page(host: str, api: bool) -> Response:
    """Response for a host that is not allowed: JSON for the API, otherwise a short page with the hint where to
    enter the name. The name is shown only in cleaned form."""
    shown = re.sub(r"[^a-zA-Z0-9.:\-\[\]]", "", host or "")[:80]
    msg = (f"Zugriff über die Adresse „{shown}“ ist nicht freigegeben. Öffne ComfyOS über die IP-Adresse "
           "des Geräts und trage den Namen unter System → Sicherheit → Weitere Adressen ein.")
    if api:
        return JSONResponse({"detail": msg}, status_code=400)
    from html import escape
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>ComfyOS</title>"
                        f"<p style='font-family:sans-serif;max-width:520px;margin:40px auto'>{escape(msg)}</p>",
                        status_code=400)


def should_log_blocked(host: str) -> bool:
    """Should the rejection of this host go to the event log? At most once per hour per host, so that an attacker
    cannot fill the log."""
    now = time.time()
    key = (host or "")[:80]
    if now - _blocked_logged.get(key, 0) < 3600:
        return False
    if len(_blocked_logged) > 200:
        _blocked_logged.clear()
    _blocked_logged[key] = now
    return True


def apply_headers(response: Response) -> Response:
    """Add the security headers to a response (existing ones stay)."""
    for k, v in HEADERS.items():
        response.headers.setdefault(k, v)
    return response
