"""Client for a QIVICON / Telekom Magenta Home Base 2 in the home network.

Local endpoints (known from the web UI of the Home Base and the Magenta SmartHome app):
    GET  http://<host>/system/info      system info without login (diagnostics)
    POST https://<host>/remote/json-rpc method "smarthome.rest/proxy": wraps REST calls to the
                                        openHAB inside, e.g. /rest/things, /rest/items,
                                        /rest/qivicon/rooms, /rest/qivicon/devices

Login (verified against a capture of the Home Base 2 web UI)
    GET a protected page; the Home Base answers with its login form (often HTTP 401) and a
    session cookie. The form is posted back to the same URL with the fields
        t=<epoch ms>  ts=<JS Date.toString()>  f=  lb=  u=  p=<password>  submit-btn=
    (``u`` stays empty – the Home Base 2 asks for a password only). Afterwards the dashboard
    page carries the session token that is sent as "Authorization: Bearer …" together with
    the header "QIVICON-ServiceGatewayId" (the serial number) on /remote/json-rpc.

Safety
    * One way for every call: ``_proxy``. An expired session is renewed once and the call is
      repeated (the Home Base rejected the first attempt, so nothing was executed) – a switch
      command is never lost that way.
    * ``_login`` runs under a lock: the password is never sent twice in parallel. After a
      rejected login nothing is tried for LOGIN_BACKOFF_SECONDS – repeated attempts can lock
      the Home Base. "Verbinden" and "Verbindung testen" lift the pause.
    * The device list is fetched once at a time; an answer that was requested before a switch
      command is not cached (``invalidate_devices``).

Pairing, reconnecting, deleting (verified against a capture of the web UI, Nov 2026)
    pairable types   GET  /rest/qivicon/pairing/devices
    search           POST /rest/discovery/bindings/<binding>/scan            (answer = seconds)
    found devices    GET  /rest/inbox
    add              GET  /rest/qivicon/flows/thing/<uid>/contexts/setup-inbox, then
                     POST …/setup-inbox/<flow> {"debugging": false, "label": …} (state FINISHED),
                     DELETE /rest/qivicon/flows/<flow uid>; without a flow: openHAB "approve"
    reconnect        GET/POST …/contexts/setup-reconnect[/<flow>], POST /rest/qivicon/flows/<flow uid>/steps/<step>
    delete           DELETE /rest/qivicon/devices/<uid>                      (204)
    The product catalog (vendors, pictures, instructions) comes from the Telekom backend with the
    token and URL the dashboard page carries (data-backend-token / data-backend-url). The token
    only ever goes to *.qivicon.com. UIDs are always quoted with ``quote(uid, safe="")``.
"""
from __future__ import annotations

import asyncio
import base64
import html
import json
import re
import time
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

import httpx

from plugin_api import DeviceUnreachable, PluginError

from . import model
from .model import HomeBaseDevice

WEB_PORTS = (443, 8444)                  # the web UI first: that is where the password login works
TOKEN_TTL_SECONDS = 7 * 3600             # used when the token carries no expiry
RENEW_BEFORE_SECONDS = 600               # renew the session 10 minutes before the token expires
LOGIN_BACKOFF_SECONDS = 300              # pause after the Home Base rejected the password
SHORT_BACKOFF_SECONDS = 30               # pause after a failure that did not involve the password
DEVICE_CACHE_SECONDS = 5.0
PAIRABLE_CACHE_SECONDS = 3600
# The local web dashboard ("aurora") embeds the session token for the REST proxy.
TOKEN_PAGES = ("dashboard/system.app.aurora/index.html", "", "dashboard/system.app.config/index.html")
LOGIN_PAGES = ("", "dashboard/system.app.aurora/index.html", "system/http/login")
JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
GATEWAY_ID_PATTERN = re.compile(r"\b[A-Z]{2}\d{5,}-\d{3,}\b")
ACCESS_TOKEN_PATTERN = re.compile(r'data-access-token=["\']([^"\']+)', re.I)
BACKEND_TOKEN_PATTERN = re.compile(r'data-backend-token=["\']([^"\']+)', re.I)
BACKEND_URL_PATTERN = re.compile(r'data-backend-url=["\']([^"\']+)', re.I)
CLOUD_HOST_SUFFIX = ".qivicon.com"       # the catalog token only goes to Telekom servers
TEXT_PLAIN = "text/plain; charset=UTF-8"
JSON_BODY = "application/json; charset=UTF-8"
MAX_ROOM_NAME = 40
THERMOSTAT_RANGE = (5.0, 30.0)
DEFAULT_TARGET = 20.0
# Read-only tour of the REST API for diagnostics (never a request body, never a command)
EXPLORE_PATHS = (
    "/rest/",                                           # openHAB: index of all REST endpoints
    "/rest/items?tags=thing&includeThingStatus=true",   # what the Magenta app reads (device states)
    "/rest/items", "/rest/things", "/rest/rules", "/rest/persistence", "/rest/qivicon/devices",
    "/rest/qivicon/rooms", "/rest/inbox", "/rest/discovery", "/rest/bindings", "/rest/thing-types",
)

ClientFactory = Callable[..., Any]       # returns an httpx.AsyncClient (PluginContext.http with the egress key bound)


class QiviconError(PluginError):
    """The Home Base refused something or answered in an unexpected way (message for the user)."""


class HomeBaseUnreachable(QiviconError, DeviceUnreachable):
    """The Home Base did not answer (network down, restarting)."""


class SessionExpired(Exception):
    """The Home Base rejected the session; the caller signs in again and repeats the call."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def normalize_origin(url: str) -> tuple[str, str, int | None]:
    """'https://192.168.178.46/system/http/login/' -> ('https', host, port).

    Any path the user pasted is dropped; only host and port matter.
    """
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        host, port = parts.hostname, parts.port
    except ValueError:
        host, port = None, None
    if not host:
        raise QiviconError("Adresse der Home Base ist ungültig.")
    if ":" in host:                       # IPv6 literal
        host = f"[{host}]"
    return "https", host, port


def token_expiry(token: str) -> float | None:
    """Read the ``exp`` claim of a JWT (no signature check – only used to time the renewal)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        expiry = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(expiry) if expiry else None
    except Exception:
        return None


def find_gateway_id(data: Any) -> str | None:
    """Find the serial number of the Home Base (two letters, digits, dash, digits) in JSON or HTML."""
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str) and "serial" in key.lower() and value.strip():
                return value.strip()
        for value in data.values():
            found = find_gateway_id(value)
            if found:
                return found
        return None
    if isinstance(data, list):
        for value in data:
            found = find_gateway_id(value)
            if found:
                return found
        return None
    if isinstance(data, str):
        match = GATEWAY_ID_PATTERN.search(data)
        return match.group(0) if match else None
    return None


def javascript_date(now: float) -> str:
    """Format a time like JavaScript's ``Date.prototype.toString()`` (the web UI sends it as ``ts``).

    Example: 'Thu Nov 12 2026 21:00:59 GMT+0100 (Mitteleuropäische Normalzeit)'.
    """
    local = time.localtime(now)
    days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    offset = local.tm_gmtoff or 0
    sign = "+" if offset >= 0 else "-"
    offset = abs(offset)
    zone = (local.tm_zone or "").strip()
    text = (f"{days[local.tm_wday]} {months[local.tm_mon - 1]} {local.tm_mday:02d} {local.tm_year} "
            f"{local.tm_hour:02d}:{local.tm_min:02d}:{local.tm_sec:02d} GMT{sign}{offset // 3600:02d}{offset % 3600 // 60:02d}")
    return text + (f" ({zone})" if zone else "")


def is_login_url(url: Any) -> bool:
    """True for the login page of the Home Base (…/system/http/login?target=…)."""
    return urlsplit(str(url)).path.rstrip("/").endswith("/system/http/login")


def looks_like_login_page(text: str) -> bool:
    """True if the HTML contains the password form (the Home Base shows it with any status code)."""
    return bool(re.search(r"<input\b[^>]*\bname=[\"']?p[\"'\s>]", text, flags=re.I)
                or re.search(r"<input\b[^>]*type=[\"']?password", text, flags=re.I))


def login_fields(page_text: str, password: str) -> dict[str, str]:
    """Build the form fields of the login. Hidden inputs of the page (e.g. CSRF) are kept."""
    now = time.time()
    fields = {"t": str(int(now * 1000)), "ts": javascript_date(now), "f": "", "lb": "", "u": "", "p": password,
              "submit-btn": ""}
    for tag in re.findall(r"<input\b[^>]*>", page_text, flags=re.I):
        if not re.search(r"type=[\"']?hidden", tag, flags=re.I):
            continue
        name = re.search(r"\bname=[\"']([^\"']+)", tag, flags=re.I)
        if not name or name.group(1) in ("p", "u", "t", "ts"):       # never overwrite the login itself
            continue
        value = re.search(r"\bvalue=[\"']([^\"']*)", tag, flags=re.I)
        fields[name.group(1)] = html.unescape(value.group(1)) if value else fields.get(name.group(1), "")
    return fields


def catalog_access(text: str) -> tuple[str, str] | None:
    """Read (token, URL) of the Telekom product catalog from the dashboard page.

    Returns None unless the URL is https on *.qivicon.com and the token is a real value.
    """
    token_match, url_match = BACKEND_TOKEN_PATTERN.search(text or ""), BACKEND_URL_PATTERN.search(text or "")
    if not token_match or not url_match:
        return None
    url = html.unescape(url_match.group(1)).strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(CLOUD_HOST_SUFFIX):
        return None
    token = html.unescape(token_match.group(1)).strip()
    if not token or token.startswith("{") or "{{" in token:           # an error object or a placeholder
        return None
    return token, url


def sample(data: Any, depth: int = 0) -> Any:
    """Shrink an answer to its structure: few entries, short strings (for the diagnostics page)."""
    if isinstance(data, str):
        return data if len(data) <= 80 else data[:80] + "…"
    if isinstance(data, list):
        return [sample(entry, depth + 1) for entry in data[:3]] + ([f"… +{len(data) - 3} weitere"] if len(data) > 3 else [])
    if isinstance(data, dict):
        if depth > 3:
            return {key: "…" for key in list(data)[:12]}
        return {key: sample(value, depth + 1) for key, value in list(data.items())[:25]}
    return data


def clean_room_name(name: str | None) -> str:
    """Trim a room name to what the Home Base accepts."""
    return (name or "").strip()[:MAX_ROOM_NAME]


# ---------------------------------------------------------------------------
# The connector
# ---------------------------------------------------------------------------

class HomeBaseConnector:
    """One Home Base: session, REST proxy, devices, rooms, pairing and commands."""

    def __init__(self, base_url: str, password: str, *, verify_tls: bool = False,
                 open_client: ClientFactory | None = None, open_catalog_client: ClientFactory | None = None) -> None:
        """Describe the connection to one Home Base; nothing is sent until the first request. ``open_client`` and
        ``open_catalog_client`` create the HTTP clients (home network and device catalogue)."""
        _, self.host, self.port = normalize_origin(base_url)
        self.base_url = self._base(self.port or WEB_PORTS[0])
        self.password = password
        self.verify_tls = bool(verify_tls)
        self._open_client = open_client
        self._open_catalog_client = open_catalog_client
        self.client: Any = None                       # httpx.AsyncClient, created on first use
        self.access_token: str | None = None
        self.session_only = False                     # the session cookie alone authorises the proxy
        self._token_at = 0.0
        self._token_exp = 0.0
        self.gateway_id: str | None = None            # serial number of the Home Base
        self.backend_token: str | None = None         # access to the product catalog (from the dashboard page)
        self.backend_url: str | None = None
        self._blocked_until = 0.0
        self._last_error = ""
        self._password_sent = False
        self.system: dict[str, Any] = {}
        self.info: dict[str, Any] = {}
        self.last_ok: bool | None = None              # did the last call through the proxy succeed?
        self.hue_bridges: list[dict[str, Any]] = []   # Hue Bridges the Home Base knows (address hints)
        self.sonos_speakers: list[dict[str, Any]] = []    # Sonos speakers the Home Base knows (hints for the Sonos plugin)
        self._login_lock = asyncio.Lock()
        self._device_lock = asyncio.Lock()
        self._dev_cache: tuple[float, list[HomeBaseDevice]] | None = None
        self._dev_started = 0.0
        self._dev_generation = 0
        self._pairable_cache: tuple[float, list[dict[str, Any]]] | None = None

    def _base(self, port: int) -> str:
        """Return the base address for a port (443 is left out of the address)."""
        return f"https://{self.host}/" if port == 443 else f"https://{self.host}:{port}/"

    # -- life cycle ---------------------------------------------------------

    async def close(self) -> None:
        """Close the HTTP client."""
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception:
                pass
            self.client = None

    async def _new_client(self) -> None:
        """Close the old HTTP client and open a fresh one. Raises ``QiviconError`` if no client factory was given."""
        if self._open_client is None:
            raise QiviconError("Keine Verbindung zur Home Base möglich.")
        await self.close()
        self.client = self._open_client(
            verify=self.verify_tls, timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 ComfyOS", "Accept": "text/html,application/json,*/*"})

    def reset_backoff(self) -> None:
        """Lift the pause after a failed login (the user asked for a new attempt)."""
        self._blocked_until = 0.0
        self._last_error = ""

    # -- diagnostics --------------------------------------------------------

    async def probe_system_info(self) -> dict[str, Any]:
        """GET http://<host>/system/info without login. Never raises; {} = no answer."""
        try:
            if self.client is None:
                await self._new_client()
        except PluginError:
            return {}
        for path in ("system/info", "system/info/"):
            try:
                response = await self.client.get(f"http://{self.host}/{path}")
            except httpx.HTTPError:
                continue
            if response.status_code < 400 and response.text.strip():
                try:
                    self.info = response.json()
                except ValueError:
                    self.info = {"raw": response.text[:500]}
                self.gateway_id = self.gateway_id or find_gateway_id(self.info)
                return self.info
        return {}

    # -- login --------------------------------------------------------------

    def _set_token(self, token: str) -> None:
        """Keep an access token and work out when it expires (from the token itself, otherwise a fixed lifetime)."""
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self.access_token, self.session_only = token, False
        self._token_at = time.time()
        self._token_exp = token_expiry(token) or (self._token_at + TOKEN_TTL_SECONDS)

    def _remember_backend(self, text: str) -> None:
        """Keep the access to the product catalog that the dashboard page carries."""
        access = catalog_access(text)
        if access is not None:
            self.backend_token, self.backend_url = access

    def _token_from_text(self, text: str) -> str | None:
        """Find an access token in a page of the Home Base, or return ``None``."""
        self._remember_backend(text)
        match = ACCESS_TOKEN_PATTERN.search(text)
        if match:
            return html.unescape(match.group(1))
        match = JWT_PATTERN.search(text)
        return match.group(0) if match else None

    async def _token_after_login(self, base: str, login_response: Any) -> bool:
        """Find the session token after the form was posted. True = the session works."""
        try:                                                  # 1) the token comes back as JSON
            body = login_response.json()
            if isinstance(body, dict):
                for key in ("access_token", "accessToken", "token"):
                    if isinstance(body.get(key), str) and body[key]:
                        self._set_token(body[key])
                        return True
        except ValueError:
            pass
        token = self._token_from_text(login_response.text)    # 2) the token is embedded in the dashboard
        if not token:
            for path in TOKEN_PAGES:
                try:
                    page = await self.client.get(urljoin(base, path))
                except Exception:
                    continue
                if page.status_code >= 400:
                    continue
                self.gateway_id = self.gateway_id or find_gateway_id(page.text)
                token = self._token_from_text(page.text)
                if token:
                    break
        if token:
            self._set_token(token)
            return True
        self.access_token, self.session_only = None, True     # 3) the session cookie alone is enough
        try:
            await self._rpc_raw(base, "/rest/qivicon/system")
            self._token_at = time.time()
            self._token_exp = self._token_at + TOKEN_TTL_SECONDS
            return True
        except Exception:
            self.session_only = False
            return False

    def _needs_login(self) -> bool:
        """Return whether a login is due: no client yet, no token or session, or the token is about to expire."""
        if self.client is None or time.time() > self._token_exp - RENEW_BEFORE_SECONDS:
            return True
        return not (self.access_token or self.session_only)

    async def _login(self) -> None:
        """Sign in – only one at a time.

        If several calls run into an expired session, the first one signs in and the others
        use its session. The password never goes out twice in parallel.
        """
        async with self._login_lock:
            if not self._needs_login():
                return
            now = time.time()
            if now < self._blocked_until:
                wait_seconds = int(self._blocked_until - now)
                raise QiviconError(f"{self._last_error} · Nächster automatischer Versuch in {wait_seconds} s "
                                   "(Schutz vor Sperre der Home Base).")
            self._password_sent = False
            try:
                await self._login_once()
            except QiviconError as error:
                self._last_error = str(error)
                # the long pause only if the password really reached the Home Base
                self._blocked_until = time.time() + (LOGIN_BACKOFF_SECONDS if self._password_sent else SHORT_BACKOFF_SECONDS)
                raise
            except httpx.HTTPError as error:      # network failure during login: wait a little instead of sending again
                self._last_error = f"Home Base nicht erreichbar ({type(error).__name__})."
                self._blocked_until = time.time() + SHORT_BACKOFF_SECONDS
                raise HomeBaseUnreachable(self._last_error)

    async def _login_once(self) -> None:
        """Log in to the Home Base once: find the login form on the known ports and pages, submit the password and
        keep the token or session. Raises ``QiviconError`` with what was found if that fails."""
        if self.client is None:
            await self._new_client()
        findings = []
        for port in ((self.port,) if self.port else WEB_PORTS):
            base = self._base(port)
            page, seen = None, []
            # The Home Base shows its login form on protected pages, often with HTTP 401 –
            # a browser renders it anyway. Judge by content, not by status.
            for path in LOGIN_PAGES:
                try:
                    response = await self.client.get(urljoin(base, path))        # also sets the session cookie
                except httpx.HTTPError:
                    if path == "":
                        break                    # the port is not reachable at all
                    continue
                seen.append(f"/{path} → HTTP {response.status_code}")
                if is_login_url(response.url) or looks_like_login_page(response.text):
                    page = response
                    break
                # the session cookie is still valid: take the fresh token, no password needed
                token = self._token_from_text(response.text) if response.status_code < 400 else None
                if token:
                    if not self.gateway_id:
                        self.gateway_id = find_gateway_id(response.text)
                        if not self.gateway_id:
                            await self.probe_system_info()
                    self._set_token(token)
                    self.base_url = base
                    self.reset_backoff()
                    return
            if page is None:
                findings.append(f"{base}: " + ("; ".join(seen) + ", keine Login-Maske gefunden" if seen else "nicht erreichbar"))
                if seen:
                    break
                continue
            # The form posts back to the page it is shown on, including ?target=…, so the
            # Home Base redirects to the dashboard that carries the token.
            post_url = str(page.url)
            self._password_sent = True
            response = await self.client.post(
                post_url, data=login_fields(page.text, self.password),
                headers={"Referer": post_url, "Origin": base.rstrip("/"),
                         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
            if response.status_code in (401, 403):
                raise QiviconError(f"Passwort von der Home Base abgelehnt (HTTP {response.status_code}). "
                                   "Keine automatischen Wiederholungen.")
            if not self.gateway_id:
                await self.probe_system_info()
            if await self._token_after_login(base, response):
                self.base_url = base
                self.reset_backoff()
                return
            if is_login_url(response.url) or looks_like_login_page(response.text):
                raise QiviconError("Anmeldung nicht angenommen (Login-Maske erscheint erneut). Passwort der Home Base prüfen.")
            findings.append(f"{base}: Login abgeschickt, aber kein Token auf /dashboard/system.app.aurora/index.html gefunden")
            break                                # the password was sent once – never repeat it on another port
        if all(finding.endswith("nicht erreichbar") for finding in findings):
            raise HomeBaseUnreachable("Home Base nicht erreichbar: " + " | ".join(findings))
        raise QiviconError("Keine API-Verbindung: " + " | ".join(findings))

    # -- REST proxy ---------------------------------------------------------

    async def _rpc_raw(self, base: str, path: str, method: str = "GET", payload: str | None = None,
                       content_type: str | None = None) -> Any:
        """One JSON-RPC call to the proxy. Raises SessionExpired when the Home Base wants a login."""
        inner_headers = {"accept": "application/json, text/plain", "accept-language": "de"}
        if content_type:
            inner_headers["content-type"] = content_type
        body = [{"jsonrpc": "2.0", "method": "smarthome.rest/proxy", "id": f"dashboard-{int(time.time() * 1000)}",
                 "params": [{"method": method, "path": path, "headers": inner_headers, "body": payload}],
                 "options": "confidential"}]
        headers = {"Content-Type": "application/json;charset=utf-8", "Accept": "application/json, text/plain, */*",
                   "Origin": base.rstrip("/")}
        if self.access_token:
            headers["Authorization"] = "Bearer " + self.access_token
        if self.gateway_id:
            headers["QIVICON-ServiceGatewayId"] = self.gateway_id
        response = await self.client.post(urljoin(base, "remote/json-rpc"), json=body, headers=headers)
        if response.status_code in (401, 403):
            raise SessionExpired()
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            if looks_like_login_page(response.text):       # the login form instead of JSON: the session expired
                raise SessionExpired()
            raise QiviconError("Unerwartete Antwort der Home Base (kein JSON).")
        entry = data[0] if isinstance(data, list) and data else data
        if isinstance(entry, dict) and entry.get("error"):
            raise QiviconError(str(entry["error"].get("message", "JSON-RPC-Fehler")))
        result = entry.get("result", entry) if isinstance(entry, dict) else entry
        if isinstance(result, dict) and "Status" in result and int(result.get("Status", 0)) >= 400:
            raise QiviconError(f"Home Base API meldet HTTP {result.get('Status')} für {path}.")
        if isinstance(result, dict) and "Body" in result:
            raw = result["Body"]
            try:
                return json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                return raw
        return result

    async def _proxy(self, path: str, method: str = "GET", payload: str | None = None,
                     content_type: str | None = None) -> Any:
        """One call through the proxy of the Home Base – the single way for reading, writing and switching."""
        try:
            if self._needs_login():
                await self._login()
            used = (self.access_token, self.session_only)
            try:
                result = await self._rpc_raw(self.base_url, path, method, payload, content_type)
            except SessionExpired:
                if (self.access_token, self.session_only) == used:   # do not drop a session someone else just renewed
                    self.access_token, self.session_only = None, False
                await self._login()                                 # one new login, subject to the pause
                try:
                    result = await self._rpc_raw(self.base_url, path, method, payload, content_type)
                except SessionExpired:
                    raise QiviconError("Home Base hat den API-Zugriff abgelehnt. Sitzung/Token ungültig.")
        except httpx.HTTPStatusError as error:
            self.last_ok = False
            raise QiviconError(f"Home Base antwortet mit HTTP {error.response.status_code}.")
        except httpx.HTTPError as error:
            self.last_ok = False
            raise HomeBaseUnreachable(f"Home Base nicht erreichbar ({type(error).__name__}).")
        except QiviconError:
            self.last_ok = False
            raise
        self.last_ok = True
        return result

    async def _rpc_get(self, path: str) -> Any:
        """Read from the Home Base's REST interface through its proxy."""
        return await self._proxy(path)

    async def _rpc_write(self, method: str, path: str, data: Any) -> Any:
        """Write JSON to the Home Base's REST interface through its proxy."""
        return await self._proxy(path, method, json.dumps(data, ensure_ascii=False), JSON_BODY)

    async def _rpc_call(self, method: str, path: str, payload: str | None = None, content_type: str | None = None) -> Any:
        """Send any request to the Home Base's REST interface through its proxy."""
        return await self._proxy(path, method, payload, content_type)

    async def _write_item(self, item: str, command: str) -> None:
        """Send a command to an item (POST /rest/items/<item>), the way ComfyOS has switched since 0.1."""
        await self._proxy(f"/rest/items/{item}", "POST", command, "text/plain")

    # -- connection ---------------------------------------------------------

    async def connect(self, force: bool = False) -> dict[str, Any]:
        """Read the system record of the Home Base (signs in if needed). ``force`` lifts a login pause."""
        if force:
            self.reset_backoff()
        system = await self._rpc_get("/rest/qivicon/system")
        self.system = system if isinstance(system, dict) else {}
        return self.system

    async def status(self) -> dict[str, Any]:
        """Describe the connection for the settings tile. Never raises."""
        try:
            if not self.system:
                await self.connect()
            internet = self.system.get("internet", {}) if isinstance(self.system.get("internet"), dict) else {}
            return {"connected": True, "gateway": self.system.get("label", "QIVICON Home Base"), "endpoint": self.base_url,
                    "message": f"Online · {self.base_url} · Internetstatus: {internet.get('status', 'unbekannt')}"}
        except Exception as error:
            message = str(error) if isinstance(error, PluginError) else f"Verbindung fehlgeschlagen ({type(error).__name__})."
            return {"connected": False, "gateway": "Home Base 2", "message": message}

    async def explore(self, paths: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """Read a list of REST paths and report what the session may see. Strictly read-only."""
        report = []
        for path in paths or EXPLORE_PATHS:
            entry: dict[str, Any] = {"path": path}
            try:
                data = await self._rpc_get(path)
                entry.update(ok=True, type=type(data).__name__, sample=sample(data))
                if isinstance(data, list):
                    entry["count"] = len(data)
                elif isinstance(data, dict):
                    entry["keys"] = list(data)[:30]
            except Exception as error:
                entry.update(ok=False, error=str(error) if isinstance(error, PluginError) else type(error).__name__)
            report.append(entry)
        return report

    # -- rooms (like the web UI; the Magenta app reads the same list) -------

    async def list_rooms(self) -> list[dict[str, Any]]:
        """Return the room list of the Home Base as ``[{"name": <room id>, "label": <name>}]``."""
        rooms = await self._rpc_get("/rest/qivicon/rooms")
        return [room for room in rooms if isinstance(room, dict) and room.get("name")] if isinstance(rooms, list) else []

    async def create_room(self, label: str) -> str:
        """Create a room and return its id.

        Like the web UI: post the complete list with the new room (name: null) first; the
        Home Base assigns the id.
        """
        label = clean_room_name(label)
        if not label:
            raise QiviconError("Raumname fehlt.")
        before = await self.list_rooms()
        known_ids = {room["name"] for room in before}
        body = [{"name": None, "label": label}] + [{"name": room["name"], "label": room.get("label")} for room in before]
        answer = await self._rpc_write("POST", "/rest/qivicon/rooms", body)
        after = answer if isinstance(answer, list) else await self.list_rooms()
        new_id = next((room.get("name") for room in after if isinstance(room, dict)
                       and room.get("name") not in known_ids and room.get("label") == label), None)
        if not new_id:
            raise QiviconError("Home Base hat den neuen Raum nicht bestätigt.")
        self.invalidate_devices()
        return new_id

    async def rename_room(self, room_id: str, label: str) -> None:
        """Rename a room: write the complete list back with the new name, then check that it arrived."""
        label = clean_room_name(label)
        rooms = await self.list_rooms()
        if not label or room_id not in {room["name"] for room in rooms}:
            raise QiviconError(f"Raum {room_id} gibt es auf der Home Base nicht.")
        body = [{"name": room["name"], "label": label if room["name"] == room_id else room.get("label")} for room in rooms]
        await self._rpc_write("POST", "/rest/qivicon/rooms", body)
        if not any(room["name"] == room_id and room.get("label") == label for room in await self.list_rooms()):
            raise QiviconError("Home Base hat den neuen Raumnamen nicht übernommen.")
        self.invalidate_devices()

    async def delete_room(self, room_id: str) -> None:
        """Remove an empty room from the list of the Home Base."""
        rooms = await self.list_rooms()
        if room_id not in {room["name"] for room in rooms}:
            raise QiviconError(f"Raum {room_id} gibt es auf der Home Base nicht.")
        devices = await self._rpc_get("/rest/qivicon/devices")
        if isinstance(devices, list) and any(isinstance(entry, dict) and entry.get("room") == room_id for entry in devices):
            raise QiviconError("Der Raum enthält noch Geräte. Bitte zuerst alle Geräte umziehen.")
        body = [{"name": room["name"], "label": room.get("label")} for room in rooms if room["name"] != room_id]
        await self._rpc_write("POST", "/rest/qivicon/rooms", body)
        if any(room["name"] == room_id for room in await self.list_rooms()):
            raise QiviconError("Home Base hat das Löschen des Raums nicht bestätigt.")
        self.invalidate_devices()

    async def move_device(self, device_id: str, room_id: str) -> None:
        """Put a device into a room: fetch its record, set ``room``, write it back."""
        records = await self._rpc_get("/rest/qivicon/devices")
        record = next((entry for entry in records if isinstance(entry, dict) and entry.get("id") == device_id), None) \
            if isinstance(records, list) else None
        if record is None:
            raise QiviconError(f"Gerät {device_id} ist der Home Base unbekannt.")
        if room_id not in {room["name"] for room in await self.list_rooms()}:
            raise QiviconError(f"Raum {room_id} gibt es auf der Home Base nicht.")
        await self._rpc_write("PUT", "/rest/qivicon/devices/" + quote(device_id, safe=""), {**record, "room": room_id})
        self.invalidate_devices()

    # -- pairing, reconnecting, deleting ------------------------------------

    async def pairable_types(self) -> list[dict[str, Any]]:
        """Return the device types this Home Base can pair (thingTypeUID, setupInbox / setupManual)."""
        cache = self._pairable_cache
        if cache and time.time() - cache[0] < PAIRABLE_CACHE_SECONDS:
            return cache[1]
        data = await self._rpc_get("/rest/qivicon/pairing/devices")
        types = [entry for entry in data if isinstance(entry, dict) and entry.get("thingTypeUID")] if isinstance(data, list) else []
        self._pairable_cache = (time.time(), types)
        return types

    async def inbox(self) -> list[dict[str, Any]]:
        """Return the devices the Home Base found but has not added yet."""
        data = await self._rpc_get("/rest/inbox")
        return [entry for entry in data if isinstance(entry, dict) and entry.get("thingUID")] if isinstance(data, list) else []

    async def start_discovery(self, binding: str) -> int:
        """Start the device search of a binding (e.g. "dect") and return its duration in seconds."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", binding or ""):
            raise QiviconError("Unbekannte Geräteart.")
        answer = await self._rpc_call("POST", f"/rest/discovery/bindings/{binding}/scan", None, TEXT_PLAIN)
        try:
            return max(10, min(600, int(str(answer).strip())))
        except (TypeError, ValueError):
            return 60

    async def device_status(self, thing_uid: str) -> dict[str, Any] | None:
        """Return the current status of one device straight from the Home Base (not cached), None = unknown."""
        records = await self._rpc_get("/rest/qivicon/devices")
        record = next((entry for entry in records if isinstance(entry, dict) and entry.get("id") == thing_uid), None) \
            if isinstance(records, list) else None
        if record is None:
            return None
        status = record.get("deviceStatus") or {}
        return {"status": status.get("status"), "detail": status.get("statusDetail"),
                "description": status.get("statusDescription"), "type": record.get("thingTypeId"), "label": record.get("label")}

    async def delete_device(self, thing_uid: str) -> None:
        """Delete a device from the Home Base for good (it answers 204)."""
        await self._rpc_call("DELETE", "/rest/qivicon/devices/" + quote(thing_uid, safe=""), None, TEXT_PLAIN)
        self.invalidate_devices()

    async def flow_descriptions(self, thing_uid: str, context: str) -> list[dict[str, Any]]:
        """Return the flows the Home Base offers for a device (``context`` e.g. "setup-reconnect"; [] = none)."""
        data = await self._rpc_get(f"/rest/qivicon/flows/thing/{quote(thing_uid, safe='')}/contexts/{context}")
        return [flow for flow in data if isinstance(flow, dict) and flow.get("id")] if isinstance(data, list) else []

    async def start_flow(self, thing_uid: str, context: str, flow_id: str, **config: Any) -> dict[str, Any]:
        """Start a flow for a device and return its record (with ``id`` and ``state``)."""
        answer = await self._rpc_write(
            "POST", f"/rest/qivicon/flows/thing/{quote(thing_uid, safe='')}/contexts/{context}/{quote(flow_id, safe='')}",
            {"debugging": False, **config})
        if not isinstance(answer, dict) or not answer.get("id"):
            raise QiviconError("Home Base hat den Ablauf nicht gestartet.")
        return answer

    async def end_flow(self, flow_uid: str) -> None:
        """Clean up a finished flow on the Home Base (like the web UI)."""
        await self._rpc_call("DELETE", "/rest/qivicon/flows/" + quote(flow_uid, safe=""), None, TEXT_PLAIN)

    async def run_flow_step(self, flow_uid: str, step_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute one step of a running flow."""
        answer = await self._rpc_write(
            "POST", f"/rest/qivicon/flows/{quote(flow_uid, safe='')}/steps/{quote(step_id, safe='')}", data or {})
        return answer if isinstance(answer, dict) else {}

    async def approve_inbox(self, thing_uid: str, label: str) -> None:
        """Add a found device the openHAB way (POST /rest/inbox/<uid>/approve with the name as body)."""
        await self._rpc_call("POST", "/rest/inbox/" + quote(thing_uid, safe="") + "/approve", label or thing_uid, "text/plain")
        self.invalidate_devices()

    async def pair_from_inbox(self, thing_uid: str, label: str) -> None:
        """Add a found device like the web UI: run the flow setup-inbox with the name, then clean it up.

        Without an offered flow the openHAB default (approve) is used.
        """
        label = clean_room_name(label) or thing_uid
        flows = await self.flow_descriptions(thing_uid, "setup-inbox")
        if not flows:
            await self.approve_inbox(thing_uid, label)
            return
        flow = flows[0]
        if flow.get("steps"):
            raise QiviconError("Dieses Gerät braucht bei der Einrichtung weitere Angaben – bitte in der "
                               "QIVICON-Oberfläche abschließen.")
        started = await self.start_flow(thing_uid, "setup-inbox", flow["id"], label=label)
        try:
            if started.get("state") != "FINISHED":
                raise QiviconError(f"Home Base hat das Einbinden nicht abgeschlossen (Status {started.get('state')}).")
        finally:
            await self.end_flow(started["id"])
        self.invalidate_devices()

    # -- product catalog (Telekom backend) ----------------------------------

    def _catalog_client(self) -> Any:
        """Return an HTTP client for the device catalogue. Raises ``QiviconError`` if the catalogue is not available."""
        if self._open_catalog_client is None:
            raise QiviconError("Der Gerätekatalog ist nicht erreichbar.")
        return self._open_catalog_client(timeout=15.0)

    async def _refresh_backend_token(self) -> None:
        """Fetch the catalog access again from the dashboard page – with the running session, without the password."""
        if self.client is None:
            return
        try:
            page = await self.client.get(urljoin(self.base_url, TOKEN_PAGES[0]))
        except Exception:
            return
        if page.status_code < 400 and not looks_like_login_page(page.text):
            self._remember_backend(page.text)

    async def catalog(self, hardware_id: str | None = None) -> Any:
        """Read the Telekom product catalog like the web UI.

        Without ``hardware_id`` the list of devices, with it the details of one device
        including the step-by-step instructions.
        """
        if hardware_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", hardware_id):
            raise QiviconError("Unbekanntes Gerät.")
        if self._needs_login():
            await self._login()
        if not self.backend_token:
            await self._refresh_backend_token()
        if not self.backend_token or not self.backend_url:
            raise QiviconError("Die Home Base liefert keinen Zugang zum Gerätekatalog.")
        url = f"{self.backend_url}/pis/hardware/devices" + (f"/{hardware_id}" if hardware_id else "")
        try:
            async with self._catalog_client() as client:
                response = await client.get(url, headers={"Authorization": "Bearer " + self.backend_token,
                                                          "Accept": "application/json", "Accept-Language": "de"})
        except httpx.HTTPError as error:
            raise QiviconError(f"Gerätekatalog nicht erreichbar ({type(error).__name__}).")
        if response.status_code in (401, 403):
            self.backend_token = None          # the next attempt fetches it fresh from the dashboard page
            raise QiviconError("Zugang zum Gerätekatalog abgelaufen.")
        if response.status_code >= 400:
            raise QiviconError(f"Gerätekatalog meldet HTTP {response.status_code}.")
        try:
            return response.json()
        except ValueError:
            raise QiviconError("Gerätekatalog: unerwartete Antwort.")

    async def catalog_image(self, blob_path: str) -> tuple[bytes, str] | None:
        """Fetch a picture of the catalog. Returns (bytes, content type) or None."""
        if not self.backend_url:
            return None
        try:
            async with self._catalog_client() as client:
                response = await client.get(f"{self.backend_url}/pis/blob/{blob_path}")
        except httpx.HTTPError as error:
            raise HomeBaseUnreachable(f"Gerätekatalog nicht erreichbar ({type(error).__name__}).")
        if response.status_code != 200:
            return None
        return response.content, response.headers.get("content-type", "").split(";")[0].strip().lower()

    # -- devices ------------------------------------------------------------

    def invalidate_devices(self) -> None:
        """Drop the cached device list (after a command or a change on the Home Base)."""
        self._dev_cache = None
        self._dev_generation += 1

    async def list_devices(self, max_age: float | None = None) -> list[HomeBaseDevice]:
        """Return the devices with their current values (cached for a few seconds).

        Only one fetch runs at a time: start page, live updates and flows would otherwise ask
        for the same data in parallel (three calls each). A waiting caller takes the result of
        a fetch that started after its own request – fresh enough even for ``max_age=0``.
        """
        max_age = DEVICE_CACHE_SECONDS if max_age is None else max_age
        cache = self._dev_cache
        if cache and time.time() - cache[0] < max_age:
            return cache[1]
        asked = time.time()
        async with self._device_lock:
            cache = self._dev_cache
            if cache and (self._dev_started >= asked or time.time() - cache[0] < max_age):
                return cache[1]
            generation = self._dev_generation
            started = time.time()
            things = await self._rpc_get("/rest/things")
            items = await self._rpc_get("/rest/items")
            try:
                records = await self._rpc_get("/rest/qivicon/devices")
            except QiviconError:
                records = []                    # optional: battery and last communication
            if not isinstance(things, list) or not isinstance(items, list):
                raise QiviconError("Unerwartetes Format von /rest/things bzw. /rest/items.")
            devices = model.build_devices(things, items, records if isinstance(records, list) else [])
            self.hue_bridges = [{"uid": thing.get("UID"), "mac": str(thing.get("UID")).split(":")[-1].lower(),
                                 "host": (thing.get("configuration") or {}).get("ipAddress")}
                                for thing in things if isinstance(thing, dict)
                                and str(thing.get("thingTypeUID") or "").lower().startswith("hue:bridge")]
            self.sonos_speakers = model.sonos_speakers(things)
            if generation == self._dev_generation:       # switched during the fetch: do not cache the old state
                self._dev_cache = (time.time(), devices)
                self._dev_started = started
            return devices

    # -- commands -----------------------------------------------------------

    async def set_device_state(self, device_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """Switch or set a device and return the values it has afterwards.

        ``state``: ``{"on": bool}`` for lamp, plug, actuator; ``{"target": °C}`` or ``{"delta": °C}``
        for a thermostat; ``{"cover": "up"|"down"|"stop"}`` or ``{"position": 0-100}`` for shutters.
        The command words are openHAB's: ON/OFF, a number, UP/DOWN/STOP or per cent. For the cover
        words there is no capture of a real Home Base yet – the way is the proven one, only the
        words come from the openHAB specification.
        """
        device = next((entry for entry in await self.list_devices() if entry.id == device_id), None)
        if device is None:
            raise QiviconError("Gerät nicht gefunden.")
        if not device.control_item:
            raise QiviconError(f"{device.name or device_id} ist nicht schaltbar.")
        after: dict[str, Any] = {}
        if device.kind in ("light", "switch"):
            if "on" not in state:
                raise QiviconError("Farbe und Helligkeit gehen bei Geräten der Home Base nicht – nur ein und aus.")
            command = "ON" if state["on"] else "OFF"
            after["on"] = bool(state["on"])
        elif device.kind == "thermostat":
            if state.get("target") is not None:
                target = float(state["target"])
            elif state.get("delta") is not None:
                target = float(device.state.get("target") or DEFAULT_TARGET) + float(state["delta"])
            else:
                raise QiviconError("Für Thermostat bitte 'target' oder 'delta' angeben.")
            target = round(target * 2) / 2
            if not THERMOSTAT_RANGE[0] <= target <= THERMOSTAT_RANGE[1]:
                raise QiviconError("Solltemperatur muss zwischen 5 und 30 °C liegen.")
            command = f"{target:.1f}"
            after["target"] = target
        elif device.kind == "cover":
            if state.get("position") is not None:
                position = int(round(float(state["position"])))
                if not 0 <= position <= 100:
                    raise QiviconError("Position muss zwischen 0 und 100 liegen.")
                command = str(position)
                after["position"] = position
            else:
                move = str(state.get("cover") or ("down" if state.get("on") else "up")).lower()
                if move not in ("up", "down", "stop"):
                    raise QiviconError("Für Rollladen bitte 'up', 'down' oder 'stop' angeben.")
                command = move.upper()
        else:
            raise QiviconError(f"Steuerung für Gerätetyp '{device.kind}' nicht unterstützt.")
        await self._write_item(device.control_item, command)
        self.invalidate_devices()
        return after
