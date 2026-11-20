"""Client for one Philips Hue Bridge: pairing, lights, commands and the live event stream.

Uses the official local CLIP API v2 over HTTPS. The bridge has its own certificate,
so it is not verified – the connection never leaves the home network (the manifest
declares the target with ``"scope": "lan"`` and the core blocks everything else).

Pairing: the user presses the link button on the bridge, then ``request_app_key``
returns an application key. The key is the only credential and is stored as a secret.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable

import httpx

from plugin_api import DeviceUnreachable, PluginError, ScenePalette

from . import clip
from .clip import HueButtonPress, HueLight, HueMotionSensor, HueReachabilityChange, HueSwitch

LIGHT_CACHE_SECONDS = 2.0           # several pages may ask at once; one request serves them all
# While the event stream is connected, the events keep the cached list current (lights, names, reachability, sensors,
# buttons; a light added or removed drops the cache). Asking for everything every few seconds as well made the bridge
# answer HTTP 429 (seen with plugin 1.1.0: nine requests at once every three seconds while the live comparison ran).
LIVE_CACHE_SECONDS = 60.0
MAX_PARALLEL_REQUESTS = 3           # the bridge refuses bursts – at most this many requests at once
BUSY_RETRY_SECONDS = 1.0            # after HTTP 429 a request is tried once more after this pause
RECENT_CHANGES_SECONDS = 10.0       # how long commands and events are kept for replaying
RECONNECT_DELAY_SECONDS = 2.0
RECONNECT_DELAY_MAX_SECONDS = 60.0

IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")    # raster images only – never SVG, it can carry scripts
IMAGE_MAX_BYTES = 600_000
IMAGE_CACHE_SIZE = 80
IMAGE_RETRY_SECONDS = 600           # do not ask again for a while if the bridge had no image

_HOST = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,64}$")
_RESOURCE_ID = re.compile(r"[0-9a-fA-F-]{36}")

#: Opens an HTTP client that can only reach the home network (``context.http("bridge", …)``).
OpenClient = Callable[..., httpx.AsyncClient]
#: Called with the lights and sensors that changed, the buttons pressed and the sensors and switches the bridge reaches
#: again or no longer, or with None when the device list must be reloaded.
BridgeChange = HueLight | HueMotionSensor | HueButtonPress | HueReachabilityChange
ChangeListener = Callable[[list[BridgeChange] | None], Awaitable[None]]


class LinkButtonNotPressed(PluginError):
    """The bridge refused the pairing because its link button was not pressed."""


def clean_host(host: str) -> str:
    """'https://192.168.1.20/' -> '192.168.1.20'. Raises PluginError for anything that is not a host."""
    cleaned = (host or "").strip()
    for scheme in ("https://", "http://"):
        if cleaned.lower().startswith(scheme):
            cleaned = cleaned[len(scheme):]
    cleaned = cleaned.split("/")[0]
    if not cleaned or not _HOST.match(cleaned):
        raise PluginError("Adresse der Hue Bridge ist ungültig.")
    return cleaned


def is_resource_id(value: str | None) -> bool:
    """True for ids as the bridge uses them (36 characters, hex digits and dashes)."""
    return bool(_RESOURCE_ID.fullmatch(value or ""))


async def fetch_bridge_config(open_client: OpenClient, host: str) -> dict[str, Any]:
    """Public facts about a bridge (no key needed): name, bridge_id, mac, software version, model."""
    try:
        async with open_client(timeout=5.0) as client:
            response = await client.get(f"https://{host}/api/0/config")
            config = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DeviceUnreachable(f"Hue Bridge unter {host} nicht erreichbar ({type(error).__name__}).")
    if not isinstance(config, dict) or not config.get("bridgeid"):
        raise PluginError(f"Unter {host} antwortet keine Hue Bridge.")
    return {
        "name": config.get("name") or "Hue Bridge",
        "bridge_id": str(config["bridgeid"]).lower(),
        "mac": config.get("mac"),
        "swversion": config.get("swversion"),
        "model": config.get("modelid"),
    }


async def request_app_key(open_client: OpenClient, host: str, device_name: str = "comfyos") -> str:
    """Ask the bridge for an application key. Raises LinkButtonNotPressed until the button was pressed."""
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "", device_name)[:19] or "comfyos"
    try:
        async with open_client(timeout=6.0) as client:
            response = await client.post(f"https://{host}/api",
                                         json={"devicetype": f"comfyos#{safe_name}", "generateclientkey": True})
            answer = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DeviceUnreachable(f"Hue Bridge unter {host} nicht erreichbar ({type(error).__name__}).")

    first = answer[0] if isinstance(answer, list) and answer else {}
    if isinstance(first, dict) and (first.get("success") or {}).get("username"):
        return str(first["success"]["username"])
    error_info = (first or {}).get("error") or {}
    if error_info.get("type") == 101:
        raise LinkButtonNotPressed("Bitte die runde Link-Taste auf der Hue Bridge drücken.")
    raise PluginError(error_info.get("description") or "Kopplung von der Bridge abgelehnt.")


class HueBridge:
    """Connection to one paired bridge. Keeps the light list cached and current."""

    def __init__(self, open_client: OpenClient, host: str, app_key: str,
                 bridge_id: str | None = None, name: str | None = None) -> None:
        """Describe one bridge: its address, app key and id. Nothing is sent until the first request; ``open_client``
        creates the HTTP client."""
        self.host = clean_host(host)
        self.app_key = app_key
        self.bridge_id = (bridge_id or "").lower() or None
        self.name = name or "Hue Bridge"
        self.last_error = ""                # empty = the last request worked
        self.is_live = False                # the event stream is connected: changes arrive at once
        self.motion_sensors: dict[str, HueMotionSensor] = {}    # by zigbee address; events keep them current
        self.switches: dict[str, HueSwitch] = {}                # wall switches and remotes by device id
        self.image_status = ""              # why the bridge delivered no scene image (for the event log)

        self._open_client = open_client
        self._client: httpx.AsyncClient | None = None
        self._lights_cache: tuple[float, list[HueLight]] | None = None    # (time fetched, lights)
        self._fetch_lock = asyncio.Lock()
        self._request_slots = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)
        # Commands and events of the last seconds: (time, "command" | "event", light id, payload).
        self._recent_changes: list[tuple[float, str, str, dict[str, Any]]] = []
        self._images: dict[str, tuple[bytes, str]] = {}
        self._image_missing_since: dict[str, float] = {}

    @property
    def mac(self) -> str | None:
        """MAC address of the bridge as the Home Base writes it in its device ids."""
        return clip.bridge_mac(self.bridge_id)

    async def close(self) -> None:
        """Close the HTTP connection. The bridge object can be used again afterwards."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def forget_lights(self) -> None:
        """Drop the cached light list so the next call asks the bridge again."""
        self._lights_cache = None

    # -- requests -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Return the header that authenticates requests to the bridge."""
        return {"hue-application-key": self.app_key}

    async def _request(self, method: str, path: str, body: Any = None) -> list[dict[str, Any]]:
        """Call ``/clip/v2/<path>`` and return the ``data`` list of the answer. At most ``MAX_PARALLEL_REQUESTS`` run at
        once; a request the bridge refuses as busy (HTTP 429) is tried once more after a short pause."""
        if self._client is None:
            self._client = self._open_client()
        for attempt in (1, 2):
            async with self._request_slots:
                try:
                    response = await self._client.request(method, f"https://{self.host}/clip/v2/{path}",
                                                          json=body, headers=self._headers())
                except httpx.HTTPError as error:
                    raise DeviceUnreachable(f"Hue Bridge nicht erreichbar ({type(error).__name__}).")
            if response.status_code != 429 or attempt == 2:
                break
            await asyncio.sleep(BUSY_RETRY_SECONDS)
        if response.status_code in (401, 403):
            raise DeviceUnreachable("Hue Bridge lehnt den Schlüssel ab – bitte neu koppeln.")
        if response.status_code == 429:
            raise DeviceUnreachable("Hue Bridge ist ausgelastet – bitte kurz warten.")
        try:
            answer = response.json()
        except ValueError:
            answer = None
        if not isinstance(answer, dict):
            raise DeviceUnreachable(f"Unerwartete Antwort der Hue Bridge (HTTP {response.status_code}).")
        descriptions = [error.get("description") for error in answer.get("errors") or [] if isinstance(error, dict)]
        if response.status_code >= 400 or descriptions:
            raise PluginError("Hue Bridge: " + (descriptions[0] if descriptions else f"HTTP {response.status_code}"))
        return answer.get("data") or []

    async def _request_optional(self, path: str) -> list[dict[str, Any]] | None:
        """Like ``_request`` for extras such as motion sensors: without them lights still work."""
        try:
            return await self._request("GET", path)
        except PluginError:
            return None

    async def check_key(self) -> None:
        """Ask for the bridge resource; raises if the key is not accepted. Learns the bridge id."""
        rows = await self._request("GET", "resource/bridge")
        if rows and rows[0].get("bridge_id"):
            self.bridge_id = str(rows[0]["bridge_id"]).lower()

    # -- lights -------------------------------------------------------------

    def cache_age(self) -> float:
        """How old the cached light list may be for the device list: long while the event stream keeps it current,
        short otherwise."""
        return LIVE_CACHE_SECONDS if self.is_live else LIGHT_CACHE_SECONDS

    async def lights(self, max_age: float = LIGHT_CACHE_SECONDS) -> list[HueLight]:
        """All lights of the bridge. Answers from the cache if it is younger than ``max_age`` seconds."""
        async with self._fetch_lock:
            if self._lights_cache and time.time() - self._lights_cache[0] < max_age:
                return self._lights_cache[1]
            previous_fetch = self._lights_cache[0] if self._lights_cache else None
            started = time.time()
            try:
                if not self.bridge_id:
                    await self.check_key()
                (light_rows, device_rows, zigbee_rows, room_rows, motion_rows, temperature_rows, light_level_rows,
                 power_rows, button_rows) = await asyncio.gather(
                    self._request("GET", "resource/light"),
                    self._request("GET", "resource/device"),
                    self._request("GET", "resource/zigbee_connectivity"),
                    self._request("GET", "resource/room"),
                    self._request_optional("resource/motion"),
                    self._request_optional("resource/temperature"),
                    self._request_optional("resource/light_level"),
                    self._request_optional("resource/device_power"),
                    self._request_optional("resource/button"),
                )
            except PluginError as error:
                self.last_error = str(error)
                raise
            self.last_error = ""
            if motion_rows is not None:
                self.motion_sensors = clip.parse_motion_sensors(motion_rows, zigbee_rows, device_rows)
                clip.attach_sensor_values(self.motion_sensors, temperature_rows or [], light_level_rows or [],
                                          power_rows or [])
            if button_rows is not None:
                self.switches = clip.parse_switches(button_rows, device_rows, zigbee_rows, power_rows or [])
            lights = clip.parse_lights(light_rows, device_rows, zigbee_rows, room_rows)
            self._lights_cache = (started, lights)
            self._replay_recent_changes(lights, since=previous_fetch if previous_fetch is not None else started)
            return lights

    def cached_light(self, resource_id: str) -> HueLight | None:
        """The light with this id from the last answer of the bridge, without asking again."""
        lights = self._lights_cache[1] if self._lights_cache else []
        return next((light for light in lights if light.resource_id == resource_id), None)

    def _remember_change(self, kind: str, resource_id: str, payload: dict[str, Any]) -> None:
        """Keep a change this plugin just sent, so its echo on the event stream can be told apart from changes made
        elsewhere."""
        now = time.time()
        still_recent = [change for change in self._recent_changes if now - change[0] < RECENT_CHANGES_SECONDS]
        self._recent_changes = still_recent + [(now, kind, resource_id, payload)]

    def _replay_recent_changes(self, lights: list[HueLight], since: float) -> None:
        """Apply commands and events that happened after ``since`` to a fresh answer.

        A request that started before a command returns the old state. Without the replay
        that older answer would overwrite the new state and the lamp would jump back in the UI.
        """
        by_id = {light.resource_id: light for light in lights}
        for happened_at, kind, resource_id, payload in self._recent_changes:
            light = by_id.get(resource_id)
            if happened_at < since or light is None:
                continue
            if kind == "command":
                clip.apply_command(light, payload)
            else:
                clip.apply_light_update(light, payload)

    async def send_light_command(self, resource_id: str, body: dict[str, Any]) -> HueLight | None:
        """Send a CLIP body (see ``clip.build_command``) to a light; returns the updated cached light."""
        if not is_resource_id(resource_id):
            raise PluginError("Ungültige Lampen-ID.")
        await self._request("PUT", f"resource/light/{resource_id}", body)
        self._remember_change("command", resource_id, body)
        light = self.cached_light(resource_id)
        if light is not None:       # show the new state at once; the bridge confirms it by event
            clip.apply_command(light, body)
        return light

    async def rename_device(self, device_id: str, name: str) -> str:
        """Set the name of a device on the bridge (it then shows in the Hue app). Returns the stored name."""
        if not is_resource_id(device_id):
            raise PluginError("Ungültige Geräte-ID.")
        stored_name = clip.clip_name(name)
        if not stored_name:
            raise PluginError("Der Name darf nicht leer sein.")
        await self._request("PUT", f"resource/device/{device_id}", {"metadata": {"name": stored_name}})
        for light in (self._lights_cache[1] if self._lights_cache else []):
            if light.device_id == device_id:
                light.name = stored_name
        return stored_name

    async def delete_device(self, device_id: str) -> None:
        """Remove a device from the bridge (the same as deleting it in the Hue app)."""
        if not is_resource_id(device_id):
            raise PluginError("Ungültige Geräte-ID.")
        await self._request("DELETE", f"resource/device/{device_id}")
        self.forget_lights()

    # -- scenes -------------------------------------------------------------

    async def scenes(self) -> list[ScenePalette]:
        """Scenes of the bridge as colour palettes, each name once (see ``clip.parse_scenes``)."""
        scene_rows, room_rows, zone_rows = await asyncio.gather(
            self._request("GET", "resource/scene"),
            self._request("GET", "resource/room"),
            self._request("GET", "resource/zone"),
        )
        return clip.parse_scenes(scene_rows, room_rows + zone_rows)

    async def fetch_image(self, image_id: str) -> tuple[bytes, str] | None:
        """Preview image of a scene as ``(bytes, mime type)``; None if the bridge delivers none.

        Raises DeviceUnreachable only if the bridge cannot be reached at all.
        """
        if not is_resource_id(image_id):
            return None
        if image_id in self._images:
            return self._images[image_id]
        if time.time() - self._image_missing_since.get(image_id, 0) < IMAGE_RETRY_SECONDS:
            return None
        if self._client is None:
            self._client = self._open_client()
        try:
            response = await self._client.get(f"https://{self.host}/clip/v2/resource/public_image/{image_id}",
                                              headers=self._headers())
        except httpx.HTTPError as error:
            raise DeviceUnreachable(f"Hue Bridge nicht erreichbar ({type(error).__name__}).")
        mime_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        is_usable = (response.status_code == 200 and mime_type in IMAGE_TYPES
                     and response.content and len(response.content) <= IMAGE_MAX_BYTES)
        if not is_usable:
            self._image_missing_since[image_id] = time.time()
            self.image_status = f"HTTP {response.status_code}, {mime_type or 'ohne Typ'}"
            return None
        if len(self._images) >= IMAGE_CACHE_SIZE:
            self._images.pop(next(iter(self._images)))      # drop the oldest entry
        self._images[image_id] = (response.content, mime_type)
        return self._images[image_id]

    # -- live events --------------------------------------------------------

    def apply_event(self, resource: dict[str, Any]) -> BridgeChange | None:
        """Copy one update from the event stream into the cache.

        Returns the light or motion sensor that changed, a button press, a sensor or switch whose reachability changed,
        or None if nothing we show changed.
        """
        resource_type, resource_id = resource.get("type"), resource.get("id")

        if resource_type == "zigbee_connectivity" and resource.get("status"):
            return self._apply_connectivity(clip.owner_id(resource), str(resource["status"]))

        if resource_type == "motion":
            new_value = clip.read_motion(resource)
            sensor = next((candidate for candidate in self.motion_sensors.values()
                           if candidate.resource_id == resource_id), None)
            if sensor is None or new_value is None or sensor.motion == new_value:
                return None
            sensor.motion = new_value
            return sensor

        if resource_type == "button":
            press = clip.read_button_event(resource)
            switch = next((candidate for candidate in self.switches.values() if resource_id in candidate.buttons), None)
            if switch is None or press is None:
                return None
            return HueButtonPress(switch.device_id, switch.buttons[resource_id], press)

        if resource_type in ("temperature", "light_level", "device_power"):
            return self._apply_sensor_value(resource_type, resource_id, resource)

        if resource_type == "light" and resource_id:
            self._remember_change("event", resource_id, resource)
        if not self._lights_cache:
            return None
        lights = self._lights_cache[1]

        if resource_type == "light":
            light = next((item for item in lights if item.resource_id == resource_id), None)
            if light is not None:
                clip.apply_light_update(light, resource)
            return light

        if resource_type == "device" and (resource.get("metadata") or {}).get("name"):
            light = next((item for item in lights if item.device_id == resource_id), None)
            if light is not None:
                light.name = resource["metadata"]["name"]
            return light

        return None

    def _apply_connectivity(self, device_id: str | None, status: str) -> HueLight | HueReachabilityChange | None:
        """Copy a new zigbee status into the light, motion sensor or switch of that device – one rule for all of them
        (``clip.is_connected``). Returns the light, or for a sensor or switch a ``HueReachabilityChange`` if its
        reachability really changed; None if the device is not known."""
        lights = self._lights_cache[1] if self._lights_cache else []
        light = next((item for item in lights if item.device_id == device_id), None)
        if light is not None:
            light.connection_status = status
            light.reachable = clip.is_connected(status)
            return light
        sensor = next((item for item in self.motion_sensors.values() if item.device_id == device_id), None)
        item: HueMotionSensor | HueSwitch | None = sensor or self.switches.get(device_id or "")
        if item is None:
            return None
        was_reachable = item.reachable
        item.connection_status = status
        return HueReachabilityChange(item) if item.reachable != was_reachable else None

    def _apply_sensor_value(self, resource_type: str, resource_id: Any,
                            resource: dict[str, Any]) -> HueMotionSensor | None:
        """Copy a new temperature, ambient light or battery level into its motion sensor (or switch). Returns the
        motion sensor if a value changed that ComfyOS shows live, otherwise None."""
        if resource_type == "device_power":
            battery, low = clip.read_power(resource)
            for switch in self.switches.values():
                if switch.power_id == resource_id and battery is not None:
                    switch.battery, switch.low_battery = battery, low
        for sensor in self.motion_sensors.values():
            if resource_type == "temperature" and sensor.temperature_id == resource_id:
                value = clip.read_temperature(resource)
                if value is None or value == sensor.temperature:
                    return None
                sensor.temperature = value
                return sensor
            if resource_type == "light_level" and sensor.light_level_id == resource_id:
                value = clip.read_light_level(resource)
                if value is None or value == sensor.illuminance:
                    return None
                sensor.illuminance = value
                return sensor
            if resource_type == "device_power" and sensor.power_id == resource_id:
                battery, low = clip.read_power(resource)
                if battery is not None:
                    sensor.battery, sensor.low_battery = battery, low
                return None
        return None

    async def listen(self, on_change: ChangeListener) -> None:
        """Read the event stream until the task is cancelled; reconnects with growing pauses.

        ``on_change(changes)`` is awaited for every batch of updates. ``on_change(None)`` means
        the light list must be reloaded (a light was added or removed, or the stream reconnected).
        """
        delay = RECONNECT_DELAY_SECONDS
        while True:
            try:
                async with self._open_client(timeout=httpx.Timeout(10.0, read=300.0)) as client:
                    async with client.stream("GET", f"https://{self.host}/eventstream/clip/v2",
                                             headers={**self._headers(), "Accept": "text/event-stream"}) as response:
                        if response.status_code != 200:
                            raise DeviceUnreachable(f"Event-Stream HTTP {response.status_code}")
                        delay = RECONNECT_DELAY_SECONDS
                        self.is_live = True
                        self.forget_lights()        # reload what changed while we were disconnected
                        await on_change(None)
                        await self._read_events(response, on_change)
            except asyncio.CancelledError:
                self.is_live = False
                raise
            except Exception as error:      # network down, bridge restarting, timeout -> reconnect
                self.last_error = f"Live-Verbindung unterbrochen ({type(error).__name__})"
            self.is_live = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_DELAY_MAX_SECONDS)

    async def _read_events(self, response: httpx.Response, on_change: ChangeListener) -> None:
        """Parse server-sent events: ``data:`` lines form one JSON message, an empty line ends it."""
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
                continue
            if line or not data_lines:
                continue        # comment or keep-alive
            try:
                containers = json.loads("".join(data_lines))
            except ValueError:
                containers = []
            data_lines = []

            changed: list[BridgeChange] = []
            must_reload = False
            for container in containers if isinstance(containers, list) else []:
                event_type = container.get("type")
                for resource in container.get("data") or []:
                    if event_type in ("add", "delete") and resource.get("type") in ("light", "device"):
                        must_reload = True
                    elif event_type == "update":
                        item = self.apply_event(resource)
                        if item is not None:
                            changed.append(item)
            if must_reload:
                self.forget_lights()
                await on_change(None)
            elif changed:
                await on_change(changed)
