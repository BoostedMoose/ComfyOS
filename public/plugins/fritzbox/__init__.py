"""FRITZ!Box plugin: the smart home devices of a FRITZ!Box and presence by its network (since ComfyOS 0.23).

What it does
    * Signs in at the box with a user of its own (challenge – the password never travels in plain text) and asks
      for the device list every few seconds (``interval``): FRITZ!DECT sockets with power and energy, radiator
      thermostats, lamps (brightness, white tone, colour), blinds, window contacts, temperature and humidity.
    * FRITZ!DECT 400/440 buttons: the box tells when each button was pressed last. A newer time is a press – it goes
      to the flows (trigger "Taster"). The delay is at most one query interval.
    * Optional presence: devices of the home network the user picks in the tile (phones) become presence devices –
      the box knows whether they are in its Wi-Fi (TR-064, see tr064.py).

Safety
    The box must be in the home network; the password only ever goes there (``context.is_local_host``). After a
    refused login the plugin waits ``LOGIN_BACKOFF_SECONDS`` and honours the block time the box names – a password
    is never tried in a loop, otherwise the box locks the user. Only one login runs at a time.

Files
    aha.py      login, device list and commands of the smart home interface (pure functions)
    tr064.py    presence through the host list (pure functions)
    routes.py   endpoints for the tile (status, devices of the network, chosen presence devices)
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable, Mapping

import httpx

from plugin_api import (
    PRESS_SHORT,
    STATE_BRIGHTNESS,
    STATE_ON,
    STATE_POSITION,
    STATE_PRESENT,
    STATE_TARGET,
    Button,
    Capability,
    Device,
    DeviceUnreachable,
    LightCapabilities,
    LightState,
    Plugin,
    PluginContext,
    PluginError,
    hex_to_xy,
    kelvin_to_mirek,
)

from . import aha, tr064
from .routes import build_router

EGRESS_KEY = "box"                              # see manifest.json -> egress
DEVICE_ID_PREFIX = "fritzbox:"                  # smart home device: fritzbox:<ain without spaces>
HOST_ID_PREFIX = "fritzbox:host:"               # presence device:   fritzbox:host:<mac without colons>
DEFAULT_HOST = "fritz.box"
DEFAULT_INTERVAL_SECONDS = 10
DEFAULT_AWAY_AFTER_MINUTES = 10
PRESENCE_EVERY_SECONDS = 30                     # presence is asked for at most this often
LOGIN_BACKOFF_SECONDS = 60                      # wait this long after a refused login before the next attempt
REQUEST_TIMEOUT_SECONDS = 8.0
MAX_PRESENCE_HOSTS = 20
WHITE_RANGE = (kelvin_to_mirek(aha.MAX_KELVIN), kelvin_to_mirek(aha.MIN_KELVIN))   # 6500 K … 2700 K in mirek

# Settings and secrets this plugin stores
SETTING_HOST = "host"
SETTING_USERNAME = "username"
SETTING_INTERVAL = "interval"
SETTING_PRESENCE = "presence"
SETTING_AWAY_AFTER = "away_after"
SETTING_PRESENCE_HOSTS = "presence_hosts"       # [{"mac", "name"}] the user picked in the tile
SECRET_PASSWORD = "password"

_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.\-]{1,120}$|^\[?[0-9A-Fa-f:]{2,45}\]?$")
_AIN_IN_ID = re.compile(r"[^0-9A-Za-z-]")


def device_id_of(ain: str) -> str:
    """Device id of a smart home device: the AIN without spaces ("11657 0272633" -> "fritzbox:116570272633")."""
    return DEVICE_ID_PREFIX + _AIN_IN_ID.sub("", ain)


def host_id_of(mac: str) -> str:
    """Device id of a presence device: "AA:BB:…" -> "fritzbox:host:aabb…"."""
    return HOST_ID_PREFIX + mac.replace(":", "").lower()


def clean_host(value: Any) -> str:
    """The address of the box as entered, without "http://" and slashes; empty when it is not usable."""
    host = str(value or "").strip()
    host = re.sub(r"^https?://", "", host, flags=re.IGNORECASE).strip("/")
    return host if _HOST_PATTERN.match(host) else ""


class FritzBoxPlugin(Plugin):
    """Connects ComfyOS to one FRITZ!Box."""

    command_interval = 0.2              # the box handles one command after another

    def __init__(self, context: PluginContext) -> None:
        """Start unconnected; ``start()`` signs in with the stored settings."""
        super().__init__(context)
        self._sid: str | None = None
        self._login_lock = asyncio.Lock()
        self._login_blocked_until = 0.0
        self._devices: dict[str, aha.AhaDevice] | None = None       # by device id; None = never read
        self._pressed_at: dict[str, int | None] = {}
        self._presence: dict[str, bool | None] = {}                 # by MAC
        self._last_seen: dict[str, float] = {}                      # by MAC: when the box last called it active
        self._presence_at = 0.0
        self._started_at = 0.0
        self._poll_task: asyncio.Task[None] | None = None
        self.last_error = ""
        self.clock: Callable[[], float] = time.time                 # tests replace it to let time pass

    # -- settings -----------------------------------------------------------

    @property
    def host(self) -> str:
        """Address of the box (default fritz.box)."""
        return clean_host(self.context.settings.get(SETTING_HOST) or DEFAULT_HOST)

    @property
    def username(self) -> str:
        """User the plugin signs in with; empty = the one the box offers."""
        return " ".join(str(self.context.settings.get(SETTING_USERNAME) or "").split())[:60]

    def _number_setting(self, key: str, default: int, minimum: int, maximum: int) -> int:
        """A numeric setting within its limits, or the default if it is not a number."""
        value = self.context.settings.get(key)
        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        return int(max(minimum, min(maximum, value))) if is_number else default

    @property
    def interval_seconds(self) -> int:
        """Seconds between two queries of the device list."""
        return self._number_setting(SETTING_INTERVAL, DEFAULT_INTERVAL_SECONDS, 3, 120)

    @property
    def away_after_seconds(self) -> int:
        """Seconds a picked device may be inactive before it counts as away."""
        return self._number_setting(SETTING_AWAY_AFTER, DEFAULT_AWAY_AFTER_MINUTES, 1, 120) * 60

    @property
    def presence_enabled(self) -> bool:
        """Presence through the host list is switched on."""
        return bool(self.context.settings.get(SETTING_PRESENCE))

    @property
    def presence_hosts(self) -> list[dict[str, str]]:
        """The devices of the network the user picked: [{"mac", "name"}]."""
        stored = self.context.settings.get(SETTING_PRESENCE_HOSTS)
        out: list[dict[str, str]] = []
        for entry in stored if isinstance(stored, list) else []:
            mac = tr064.clean_mac(entry.get("mac")) if isinstance(entry, Mapping) else None
            if mac and len(out) < MAX_PRESENCE_HOSTS and all(item["mac"] != mac for item in out):
                out.append({"mac": mac, "name": " ".join(str(entry.get("name") or mac).split())[:60]})
        return out

    def set_presence_hosts(self, hosts: list[Mapping[str, Any]]) -> list[dict[str, str]]:
        """Store the devices whose presence the plugin reports. Raises PluginError for too many."""
        if len(hosts) > MAX_PRESENCE_HOSTS:
            raise PluginError(f"Höchstens {MAX_PRESENCE_HOSTS} Geräte.")
        self.context.settings.set(SETTING_PRESENCE_HOSTS, [{"mac": str(host.get("mac")), "name": str(host.get("name") or "")}
                                                            for host in hosts])
        kept = self.presence_hosts
        kept_macs = {host["mac"] for host in kept}
        self._presence = {mac: value for mac, value in self._presence.items() if mac in kept_macs}
        self._presence_at = 0.0
        self.context.devices_changed()
        return kept

    @property
    def configured(self) -> bool:
        """Address and password are there."""
        return bool(self.host and self.context.secrets.get(SECRET_PASSWORD))

    # -- life cycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start asking the box in the background (only when it is set up)."""
        self._started_at = self.clock()
        if self.configured:
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop asking the box."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass                    # cancelled on purpose: the plugin stops
        self._poll_task = None

    def status(self) -> dict[str, Any]:
        """Configured, connected, live (changes arrive within the query interval) and a message for the tile."""
        if not self.configured:
            return {"configured": False, "connected": False, "live": False,
                    "message": "Adresse und Kennwort der FRITZ!Box eintragen."}
        connected = self._devices is not None and not self.last_error
        count = len(self._devices or {})
        return {"configured": True, "connected": connected, "live": connected and self.interval_seconds <= 10,
                "message": self.last_error or f"{count} Smart-Home-Gerät{'e' if count != 1 else ''} · Abfrage alle "
                                              f"{self.interval_seconds} s", "name": "FRITZ!Box", "host": self.host}

    # -- talking to the box ---------------------------------------------------

    def _client(self, timeout: float = REQUEST_TIMEOUT_SECONDS) -> httpx.AsyncClient:
        """HTTP client that only reaches the home network (manifest: egress "box")."""
        return self.context.http(EGRESS_KEY, timeout=timeout, headers={"User-Agent": "ComfyOS"})

    def _base(self, port: int | None = None) -> str:
        """Address of the box for requests."""
        host = f"[{self.host.strip('[]')}]" if ":" in self.host else self.host
        return f"http://{host}" + (f":{port}" if port else "")

    def _check_host(self) -> None:
        """The password only goes to an address in the home network. Raises PluginError otherwise."""
        if not self.host or not self.context.is_local_host(self.host.strip("[]")):
            raise PluginError("Die FRITZ!Box muss im Heimnetz liegen (Adresse wie fritz.box oder 192.168.178.1).")

    async def _login(self) -> str:
        """Sign in and return the session id. Never two at once, never in a loop: after a refusal the plugin waits."""
        async with self._login_lock:
            if self._sid:
                return self._sid
            wait = self._login_blocked_until - self.clock()
            if wait > 0:
                raise DeviceUnreachable(f"Anmeldung an der FRITZ!Box pausiert ({int(wait)} s).")
            self._check_host()
            password = self.context.secrets.get(SECRET_PASSWORD) or ""
            try:
                async with self._client() as client:
                    first = aha.parse_session((await client.get(self._base() + "/login_sid.lua",
                                                                params={"version": "2"})).text)
                    if first.block_seconds:
                        self._login_blocked_until = self.clock() + max(first.block_seconds, LOGIN_BACKOFF_SECONDS)
                        raise DeviceUnreachable(f"FRITZ!Box sperrt Anmeldungen noch {first.block_seconds} s.")
                    username = self.username or first.last_user or ""
                    answer = aha.parse_session((await client.post(
                        self._base() + "/login_sid.lua", params={"version": "2"},
                        data={"username": username, "response": aha.challenge_response(first.challenge, password)})).text)
            except httpx.HTTPError as error:
                raise DeviceUnreachable(f"FRITZ!Box nicht erreichbar ({type(error).__name__}).")
            except aha.AhaError:
                raise DeviceUnreachable("Unerwartete Antwort der FRITZ!Box bei der Anmeldung.")
            if answer.sid == aha.NO_SESSION:
                self._login_blocked_until = self.clock() + max(answer.block_seconds, LOGIN_BACKOFF_SECONDS)
                raise PluginError("FRITZ!Box lehnt die Anmeldung ab – Benutzer und Kennwort prüfen.")
            self._sid = answer.sid
            return answer.sid

    async def _aha(self, switchcmd: str, ain: str | None = None, params: Mapping[str, str] | None = None) -> str:
        """One request to the smart home interface; signs in anew once when the session has expired."""
        for attempt in (1, 2):
            sid = await self._login()
            query = {"switchcmd": switchcmd, "sid": sid, **({"ain": ain} if ain else {}), **dict(params or {})}
            try:
                async with self._client() as client:
                    response = await client.get(self._base() + "/webservices/homeautoswitch.lua", params=query)
            except httpx.HTTPError as error:
                raise DeviceUnreachable(f"FRITZ!Box nicht erreichbar ({type(error).__name__}).")
            if response.status_code == 403 and attempt == 1:
                self._sid = None            # session expired: sign in once more
                continue
            if response.status_code == 400:
                raise PluginError("FRITZ!Box lehnt den Befehl ab (Gerät weg oder Wert ungültig).")
            if response.status_code != 200:
                raise DeviceUnreachable(f"FRITZ!Box antwortet mit HTTP {response.status_code}.")
            return response.text
        raise DeviceUnreachable("Anmeldung an der FRITZ!Box abgelaufen.")

    async def _tr064(self, action: str, arguments: dict[str, str] | None = None) -> str:
        """One request to the Hosts service of TR-064 (digest authentication)."""
        self._check_host()
        auth = httpx.DigestAuth(self.username or "", self.context.secrets.get(SECRET_PASSWORD) or "")
        try:
            async with self._client() as client:
                response = await client.post(self._base(tr064.PORT) + tr064.CONTROL_PATH, auth=auth,
                                             content=tr064.soap_body(action, arguments).encode("utf-8"),
                                             headers=tr064.soap_headers(action))
        except httpx.HTTPError as error:
            raise DeviceUnreachable(f"TR-064 der FRITZ!Box nicht erreichbar ({type(error).__name__}).")
        if response.status_code == 401:
            raise PluginError("TR-064: FRITZ!Box lehnt die Anmeldung ab (Benutzer braucht das Recht dafür).")
        if response.status_code != 200:
            raise DeviceUnreachable(f"TR-064 antwortet mit HTTP {response.status_code} – „Zugriff für Anwendungen "
                                    "zulassen“ in der FRITZ!Box einschalten.")
        return response.text

    # -- asking in a rhythm ---------------------------------------------------

    async def _poll_loop(self) -> None:
        """Ask the box again and again until the plugin stops; an error waits for the next round."""
        while True:
            try:
                await self.poll()
            except PluginError as error:
                self.last_error = str(error)
            except Exception as error:      # an unexpected answer must not end the loop – the next round tries again
                self.last_error = f"Abfrage gescheitert ({type(error).__name__})"
                self.context.log.warning("poll: %s", type(error).__name__)
            await asyncio.sleep(self.interval_seconds)

    async def poll(self) -> None:
        """Read the device list once: report changed values and new button presses; then presence if it is due."""
        try:
            devices = aha.parse_devices(await self._aha("getdevicelistinfos"))
        except aha.AhaError:
            raise DeviceUnreachable("Unerwartete Antwort der FRITZ!Box (Geräteliste).")
        before = self._devices
        self._devices = {device_id_of(device.ain): device for device in devices}
        self.last_error = ""
        if before is not None:
            if set(before) != set(self._devices):
                self.context.devices_changed()
            for device_id, device in self._devices.items():
                changes = self._changes(before.get(device_id), device)
                if changes:
                    self.context.device_changed(device_id, changes)
            for ain, button in aha.new_presses(self._pressed_at, devices):
                self.context.button_pressed(device_id_of(ain), button, PRESS_SHORT)
        self._pressed_at = aha.press_times(devices)
        if self.presence_enabled and self.clock() - self._presence_at >= max(PRESENCE_EVERY_SECONDS, self.interval_seconds):
            self._presence_at = self.clock()
            await self.check_presence()

    def _changes(self, old: aha.AhaDevice | None, new: aha.AhaDevice) -> dict[str, Any]:
        """Values that differ from the last list, in the form ``context.device_changed`` expects."""
        if old is None:
            return {}
        changes = {key: value for key, value in new.state.items() if old.state.get(key) != value}
        if old.present != new.present:
            changes["online"] = new.present
        if old.power_w != new.power_w and new.power_w is not None:
            changes["power_w"] = new.power_w
        if (old.kelvin, old.color) != (new.kelvin, new.color) and new.kind == "light":
            changes["light"] = self._light_state(new)
        return changes

    async def check_presence(self) -> None:
        """Ask the box for every picked device whether it is active; report a change of present/away."""
        now = self.clock()
        for host in self.presence_hosts:
            try:
                active = tr064.is_active(await self._tr064("GetSpecificHostEntry", {"NewMACAddress": host["mac"]}))
            except (PluginError, aha.AhaError):
                continue                    # the box cannot say right now: the state stays as it is
            was = self._presence.get(host["mac"])
            if active:
                self._last_seen[host["mac"]] = now
                self._presence[host["mac"]] = True
            elif active is False:
                silent_since = self._last_seen.get(host["mac"], self._started_at)
                if now - silent_since >= self.away_after_seconds:
                    self._presence[host["mac"]] = False
            if self._presence.get(host["mac"]) is not None and self._presence[host["mac"]] != was:
                self.context.device_changed(host_id_of(host["mac"]), {STATE_PRESENT: self._presence[host["mac"]]})

    async def network_hosts(self) -> list[tr064.Host]:
        """Every device the box knows in its network (for picking presence devices in the tile)."""
        path = tr064.host_list_path(await self._tr064("X_AVM-DE_GetHostListPath"))
        try:
            async with self._client() as client:
                response = await client.get(self._base(tr064.PORT) + path)
        except httpx.HTTPError as error:
            raise DeviceUnreachable(f"FRITZ!Box nicht erreichbar ({type(error).__name__}).")
        try:
            return tr064.parse_hosts(response.text)
        except aha.AhaError:
            raise DeviceUnreachable("Unerwartete Antwort der FRITZ!Box (Geräteliste des Heimnetzes).")

    # -- devices ------------------------------------------------------------

    async def devices(self) -> list[Device]:
        """The smart home devices of the last query and the picked presence devices. Raises DeviceUnreachable as
        long as the box has not answered once."""
        if not self.configured:
            return []
        if self._devices is None:
            await self.poll()
        out = [self._device(device_id, device) for device_id, device in (self._devices or {}).items()]
        if self.presence_enabled:
            out += [self._presence_device(host) for host in self.presence_hosts]
        return out

    @staticmethod
    def _light_state(device: aha.AhaDevice) -> LightState | None:
        """Light state of a FRITZ!DECT lamp; None for a lamp without brightness."""
        if not device.mask & (aha.BIT_LEVEL | aha.BIT_COLOR):
            return None
        has_color = bool(device.mask & aha.BIT_COLOR)
        capabilities = LightCapabilities(white_range=WHITE_RANGE if has_color else None, color=has_color)
        if device.color is not None:
            color = aha.color_hex(device)
            return LightState(capabilities=capabilities, mode="xy", xy=list(hex_to_xy(color)) if color else None)
        if device.kelvin:
            return LightState(capabilities=capabilities, mode="ct", mirek=kelvin_to_mirek(device.kelvin))
        return LightState(capabilities=capabilities)

    def _device(self, device_id: str, device: aha.AhaDevice) -> Device:
        """One smart home device of the box as a ComfyOS device."""
        capabilities: list[Capability] = []
        if device.kind in ("light", "switch") and device.can_switch:
            capabilities.append(Capability(STATE_ON, writable=True))
        if device.kind == "light" and device.mask & aha.BIT_LEVEL:
            capabilities.append(Capability(STATE_BRIGHTNESS, "number", unit="%", minimum=0, maximum=100, writable=True))
        if device.kind == "thermostat":
            capabilities.append(Capability(STATE_TARGET, "number", unit="°C", minimum=aha.MIN_TARGET, maximum=aha.MAX_TARGET,
                                           step=0.5, writable=True))
        if device.kind == "cover":
            capabilities.append(Capability(STATE_POSITION, "number", unit="%", minimum=0, maximum=100, writable=True))
        details = {"AIN": device.ain, "Firmware": device.firmware}
        if device.window_open:
            details["Fenster"] = "offen erkannt (Absenkung aktiv)"
        return Device(
            id=device_id, name=device.name, kind=device.kind, online=device.present,
            state=dict(device.state), capabilities=capabilities,
            light=self._light_state(device) if device.kind == "light" else None,
            buttons=[Button(button.id, button.name) for button in device.buttons],
            vendor=device.manufacturer or "AVM", model=device.product, type_name=f"fritzbox:{device.mask}",
            battery=device.battery, low_battery=device.low_battery, power_w=device.power_w, energy_kwh=device.energy_kwh,
            status_detail=None if device.present else "FRITZ!Box erreicht das Gerät nicht (DECT-Reichweite, Batterie?)",
            details={label: str(value) for label, value in details.items() if value},
        )

    def _presence_device(self, host: dict[str, str]) -> Device:
        """A picked device of the network as a presence device."""
        present = self._presence.get(host["mac"])
        return Device(id=host_id_of(host["mac"]), name=host["name"], kind="presence", online=True,
                      state={STATE_PRESENT: present} if present is not None else {},
                      capabilities=[Capability(STATE_PRESENT, "bool")], model="Im Heimnetz der FRITZ!Box",
                      details={"MAC": host["mac"]})

    # -- commands -----------------------------------------------------------

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
        """Switch a smart home device; returns the values it has afterwards (as the box confirms them with the next
        query)."""
        device = (self._devices or {}).get(device_id)
        if device is None:
            raise PluginError("Dieses Gerät gibt es an der FRITZ!Box nicht (mehr).")
        if not device.present:
            raise DeviceUnreachable(f"{device.name}: FRITZ!Box erreicht das Gerät nicht.")
        try:
            steps = aha.commands(device, state)
        except ValueError as error:
            raise PluginError(str(error))
        for switchcmd, params in steps:
            await self._aha(switchcmd, device.ain, params)
        return self._assume(device, state)

    def _assume(self, device: aha.AhaDevice, state: Mapping[str, Any]) -> dict[str, Any]:
        """Keep what was asked for until the next query confirms it, and return the device's values."""
        for key in (STATE_ON, STATE_BRIGHTNESS, STATE_TARGET, STATE_POSITION):
            if state.get(key) is not None:
                device.state[key] = state[key]
        if state.get("kelvin") is not None:
            device.kelvin, device.color = int(state["kelvin"]), None
        result = dict(device.state)
        if device.kind == "light":
            result["light"] = self._light_state(device)
        return result

    # -- endpoints ----------------------------------------------------------

    def routes(self) -> Any:
        """The plugin's own routes (status, network devices for presence)."""
        return build_router(self)


def setup(context: PluginContext) -> Plugin:
    """Entry point named in manifest.json. Asking starts later, in ``start()``."""
    return FritzBoxPlugin(context)
