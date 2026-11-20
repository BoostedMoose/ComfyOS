"""Philips Hue plugin: lamps, plugs and motion sensors of a Hue Bridge in the home network.

What it does
    * Pairs with one bridge (link button) and keeps the application key as a secret.
    * Reports every light of the bridge as a device and switches it directly –
      on/off, brightness, white tone, colour, gradients and effects.
    * Listens to the bridge's event stream, so changes made in the Hue app or at a
      wall switch show up in ComfyOS at once.
    * Motion sensors report motion, temperature, ambient light and battery (since 1.1.0).
    * Dimmer switches, tap dials, smart buttons and wall switch modules are buttons: every press goes
      to the flows (trigger "Taster", since 1.1.0).
    * Offers the scenes of the bridge for import (as colour palettes).

How devices are linked
    A hub such as the Home Base may list the same lamps (through its own Hue binding).
    Each light therefore carries the link key ``hue:<bridge mac>:light:<number>`` and each
    motion sensor ``zigbee:<address>``. ComfyOS then shows one device: name and room come
    from the hub, state and commands go straight to the bridge.

Reachability
    Lights, plugs, motion sensors and switches all report ``online`` from the zigbee status the
    bridge keeps for them (``clip.is_connected``) – for a linked device the bridge's view wins
    over the hub's.

Files
    clip.py     translate between the bridge's CLIP API and ComfyOS (pure functions)
    bridge.py   HTTP client: pairing, lights, commands, event stream
    routes.py   endpoints for the settings tile (status, pair, forget, delete a lamp)
"""
from __future__ import annotations

import asyncio
import socket
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from plugin_api import (
    STATE_BRIGHTNESS,
    STATE_ILLUMINANCE,
    STATE_MOTION,
    STATE_ON,
    STATE_TEMPERATURE,
    Button,
    Capability,
    Device,
    DeviceUnreachable,
    Plugin,
    PluginContext,
    PluginError,
    ScenePalette,
)

from . import bridge as hue_bridge
from . import clip
from .bridge import HueBridge
from .bridge import BridgeChange
from .clip import HueButtonPress, HueLight, HueMotionSensor, HueReachabilityChange, HueSwitch
from .routes import build_router

LIGHT_ID_PREFIX = "huebridge:"                  # device id of a light:  huebridge:<light resource id>
MOTION_ID_PREFIX = "huebridge:motion:"          # device id of a sensor: huebridge:motion:<motion resource id>
SWITCH_ID_PREFIX = "huebridge:switch:"          # device id of a switch: huebridge:switch:<device resource id>
EGRESS_KEY = "bridge"                           # see manifest.json -> egress
DEFAULT_TIMEOUT_SECONDS = 8.0
UNREACHABLE_LIGHT_TEXT = "Hue Bridge erreicht die Lampe nicht (Strom am Schalter aus?)"
UNREACHABLE_SENSOR_TEXT = "Hue Bridge erreicht den Bewegungsmelder nicht (Batterie leer oder zu weit entfernt?)"
UNREACHABLE_SWITCH_TEXT = "Hue Bridge erreicht den Schalter nicht (Batterie leer oder zu weit entfernt?)"

# Settings and secrets this plugin stores
SETTING_HOST = "host"
SETTING_BRIDGE_ID = "bridge_id"
SETTING_NAME = "name"
SECRET_APP_KEY = "app_key"


class HuePlugin(Plugin):
    """Connects ComfyOS to one Hue Bridge."""

    # Philips recommends at most about ten light commands per second.
    command_interval = 0.1

    def __init__(self, context: PluginContext) -> None:
        """Start unconnected; ``start()`` connects with the stored settings."""
        super().__init__(context)
        self.bridge: HueBridge | None = None
        self._listener: asyncio.Task | None = None
        self._was_unreachable = False       # so "not reachable" is logged once, not every few seconds
        self._missing_images_logged = False

    # -- life cycle ---------------------------------------------------------

    async def start(self) -> None:
        """Connect to the bridge from the stored settings, if a bridge has been paired."""
        self._connect_from_settings()

    async def stop(self) -> None:
        """Stop listening for changes and close the connection."""
        await self._disconnect()

    def open_client(self, timeout: Any = DEFAULT_TIMEOUT_SECONDS) -> httpx.AsyncClient:
        """HTTP client for the bridge. It has its own certificate, so it is not verified."""
        return self.context.http(EGRESS_KEY, verify=False, timeout=timeout, headers={"User-Agent": "ComfyOS"})

    def _connect_from_settings(self) -> None:
        """Create the bridge client from stored settings and start listening for events."""
        host = self.context.settings.get(SETTING_HOST)
        bridge_id = self.context.settings.get(SETTING_BRIDGE_ID)
        app_key = self.context.secrets.get(SECRET_APP_KEY)
        if not (host and bridge_id and app_key):
            return
        try:
            self.bridge = HueBridge(self.open_client, host, app_key, bridge_id, self.context.settings.get(SETTING_NAME))
        except PluginError as error:
            self.context.log.warning("Hue Bridge nicht eingerichtet: %s", error)
            return
        self._listener = asyncio.get_running_loop().create_task(self.bridge.listen(self._on_bridge_change))

    async def _disconnect(self) -> None:
        """Cancel the event listener and close the bridge connection."""
        if self._listener is not None:
            self._listener.cancel()
            self._listener = None
        if self.bridge is not None:
            await self.bridge.close()
            self.bridge = None

    def status(self) -> dict[str, Any]:
        """Report whether a bridge is paired, reachable and delivering changes live, with its name and address."""
        if self.bridge is None:
            return {"configured": False, "connected": False, "live": False}
        return {
            "configured": True,
            "connected": not self.bridge.last_error,
            "live": self.bridge.is_live and self._listener is not None and not self._listener.done(),
            "message": self.bridge.last_error,
            "name": self.bridge.name,
            "host": self.bridge.host,
        }

    # -- pairing (called from routes.py) ------------------------------------

    async def pair(self, host: str) -> dict[str, Any]:
        """Pair with the bridge at ``host`` and connect. Raises LinkButtonNotPressed until the button was pressed.

        A key that already exists for the same bridge (for example after it got a new address)
        is used again, so the user does not have to press the button twice.
        """
        host = hue_bridge.clean_host(host)
        host_name = urlsplit("//" + host).hostname or ""
        if not self.context.is_local_host(host_name):
            # The application key must never be sent to an address outside the home network.
            raise PluginError("Die Hue Bridge muss im Heimnetz liegen (IP-Adresse oder Name im Heimnetz).")
        config = await hue_bridge.fetch_bridge_config(self.open_client, host)

        app_key = None
        if self.context.settings.get(SETTING_BRIDGE_ID) == config["bridge_id"]:
            app_key = await self._existing_key_if_valid(host, config["bridge_id"])
        if not app_key:
            app_key = await hue_bridge.request_app_key(self.open_client, host, socket.gethostname() or "comfyos")

        self.context.secrets.set(SECRET_APP_KEY, app_key)
        self.context.settings.set(SETTING_HOST, host)
        self.context.settings.set(SETTING_BRIDGE_ID, config["bridge_id"])
        self.context.settings.set(SETTING_NAME, config["name"])
        await self._disconnect()
        self._connect_from_settings()
        self.context.event(f"Hue Bridge „{config['name']}“ verbunden ({host})")
        self.context.devices_changed()
        return config

    async def _existing_key_if_valid(self, host: str, bridge_id: str) -> str | None:
        """Return the stored app key if the bridge at ``host`` still accepts it, otherwise ``None`` - then the bridge
        has to be paired again."""
        app_key = self.context.secrets.get(SECRET_APP_KEY)
        if not app_key:
            return None
        probe = HueBridge(self.open_client, host, app_key, bridge_id)
        try:
            await probe.check_key()
            return app_key
        except PluginError:
            return None
        finally:
            await probe.close()

    async def forget(self) -> None:
        """Delete the application key and disconnect from the bridge."""
        self.context.secrets.delete(SECRET_APP_KEY)
        for key in (SETTING_HOST, SETTING_BRIDGE_ID, SETTING_NAME):
            self.context.settings.delete(key)
        await self._disconnect()
        self.context.event("Hue Bridge getrennt – Lampen laufen wieder über die Home Base")
        self.context.devices_changed()

    # -- devices ------------------------------------------------------------

    async def devices(self) -> list[Device]:
        """Return all lights, plugs and motion sensors of the bridge. Raises ``DeviceUnreachable`` if the bridge does
        not answer; the event log notes the outage and the recovery once each. While the event stream is live the
        cached list serves (``HueBridge.cache_age``) – the events keep it current, the bridge is not asked every few
        seconds."""
        if self.bridge is None:
            return []
        try:
            lights = await self.bridge.lights(max_age=self.bridge.cache_age())
        except PluginError as error:
            if not self._was_unreachable:
                self.context.event(f"Hue Bridge nicht erreichbar: {error}")
            self._was_unreachable = True
            raise DeviceUnreachable(str(error))
        if self._was_unreachable:
            self.context.event("Hue Bridge wieder erreichbar")
        self._was_unreachable = False

        devices = [self._light_as_device(light) for light in lights]
        devices += [self._motion_sensor_as_device(sensor) for sensor in self.bridge.motion_sensors.values()]
        devices += [self._switch_as_device(switch) for switch in self.bridge.switches.values()]
        return devices

    def link_prefixes(self) -> list[str]:
        """Return the id prefix of this bridge's devices, so another hub's copies of the same lights are linked to
        them."""
        mac = self.bridge.mac if self.bridge else None
        return [f"hue:{mac}:"] if mac else []

    def _light_as_device(self, light: HueLight) -> Device:
        """Convert a Hue light (or plug) to a ComfyOS device with its capabilities and the key another hub knows it
        by."""
        capabilities = [Capability(STATE_ON, "bool", writable=True)]
        if light.light_state is not None:
            capabilities.append(Capability(STATE_BRIGHTNESS, "number", unit="%", minimum=0, maximum=100, writable=True))

        link_keys = []
        if self.bridge and self.bridge.mac and light.legacy_id:
            link_keys.append(f"hue:{self.bridge.mac}:light:{light.legacy_id}")

        details = {"Modell": light.model, "MAC": light.mac, "Hue-ID": light.legacy_id}
        return Device(
            id=LIGHT_ID_PREFIX + light.resource_id,
            name=light.name,
            kind="switch" if light.is_plug else "light",
            online=light.reachable,
            state={STATE_ON: light.on, STATE_BRIGHTNESS: light.brightness},
            capabilities=capabilities,
            light=light.light_state,
            room_hint=light.room_name,
            vendor=light.vendor,
            model=light.model,
            status_detail=None if light.reachable else UNREACHABLE_LIGHT_TEXT,
            link_keys=link_keys,
            discovered=True,        # lights the Home Base does not know appear after the user imports them
            renamable=bool(light.device_id),
            details={label: str(value) for label, value in details.items() if value},
            extra={"device_resource_id": light.device_id},
        )

    def _motion_sensor_as_device(self, sensor: HueMotionSensor) -> Device:
        """Convert a Hue motion sensor to a discovered ComfyOS device with temperature, ambient light and battery as
        far as it reports them; it is linked by its Zigbee address.

        Like a light it reports whether the bridge reaches it (since 1.1.2). Linked to the Home Base's copy, the
        bridge's view then counts – the Home Base sometimes reports a Hue sensor offline for seconds although the bridge
        reaches it (seen on 19.11.2026)."""
        state: dict[str, Any] = {STATE_MOTION: sensor.motion} if isinstance(sensor.motion, bool) else {}
        capabilities = [Capability(STATE_MOTION, "bool")]
        if sensor.temperature is not None:
            state[STATE_TEMPERATURE] = sensor.temperature
            capabilities.append(Capability(STATE_TEMPERATURE, "number", unit="°C"))
        if sensor.illuminance is not None:
            state[STATE_ILLUMINANCE] = sensor.illuminance
            capabilities.append(Capability(STATE_ILLUMINANCE, "number", unit="lx"))
        return Device(
            id=MOTION_ID_PREFIX + sensor.resource_id,
            name=sensor.name or "Hue-Bewegungsmelder",
            kind="motion",
            online=sensor.reachable,
            status_detail=None if sensor.reachable else UNREACHABLE_SENSOR_TEXT,
            state=state,
            capabilities=capabilities,
            battery=sensor.battery,
            low_battery=sensor.low_battery,
            link_keys=[f"zigbee:{sensor.zigbee_mac}"],
            discovered=True,
            details={"Bewegung": "live über die Hue Bridge"},
        )

    def _switch_as_device(self, switch: HueSwitch) -> Device:
        """Convert a Hue wall switch or remote to a discovered ComfyOS device of the kind "button"; every press goes to
        the flows (``_on_bridge_change``). It reports whether the bridge reaches it, like lights and motion sensors."""
        return Device(
            id=SWITCH_ID_PREFIX + switch.device_id,
            name=switch.name,
            kind="button",
            online=switch.reachable,
            status_detail=None if switch.reachable else UNREACHABLE_SWITCH_TEXT,
            buttons=[Button(control, name) for control, name in clip.button_names(switch)],
            battery=switch.battery,
            low_battery=switch.low_battery,
            model=switch.model,
            link_keys=[f"zigbee:{switch.zigbee_mac}"] if switch.zigbee_mac else [],
            discovered=True,
            details={"Tasten": "live über die Hue Bridge"},
        )

    # -- commands -----------------------------------------------------------

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
        """Send a state change to a light. Raises ``PluginError`` if the light is gone, the values are invalid or a
        plug is asked for colour or brightness."""
        bridge = self._require_bridge()
        light = await self._find_light(device_id)
        if light is None:
            raise PluginError("Diese Lampe gibt es auf der Hue Bridge nicht (mehr).")

        wants_light_features = any(state.get(key) is not None for key in clip.LIGHT_ONLY_KEYS)
        if light.light_state is None and wants_light_features:
            raise PluginError(f"{light.name} lässt sich nur ein- und ausschalten.")
        try:
            body = clip.build_command(light.light_state, state)
        except ValueError as error:
            raise PluginError(str(error))

        updated = await bridge.send_light_command(light.resource_id, body)
        if updated is not None:
            return {STATE_ON: updated.on, STATE_BRIGHTNESS: updated.brightness, "light": updated.light_state}
        return {STATE_ON: body["on"]["on"]} if "on" in body else None

    async def rename(self, device_id: str, name: str) -> str | None:
        """Rename the light's device on the bridge and return the name the bridge stored, or ``None`` if the light
        cannot be renamed."""
        bridge = self._require_bridge()
        light = await self._find_light(device_id)
        if light is None or not light.device_id:
            return None
        return await bridge.rename_device(light.device_id, name)

    def clean_name(self, name: str) -> str:
        """Return the name as the bridge would store it (the bridge limits its length)."""
        return clip.clip_name(name)

    def _require_bridge(self) -> HueBridge:
        """Return the connected bridge. Raises ``DeviceUnreachable`` if none is connected."""
        if self.bridge is None:
            raise DeviceUnreachable("Hue Bridge nicht verbunden.")
        return self.bridge

    async def _find_light(self, device_id: str) -> HueLight | None:
        """Look up the light behind a device id, first in the cache, then on the bridge."""
        bridge = self._require_bridge()
        if not device_id.startswith(LIGHT_ID_PREFIX) or device_id.startswith((MOTION_ID_PREFIX, SWITCH_ID_PREFIX)):
            return None
        resource_id = device_id[len(LIGHT_ID_PREFIX):]
        light = bridge.cached_light(resource_id)
        if light is None:
            lights = await bridge.lights()
            light = next((item for item in lights if item.resource_id == resource_id), None)
        return light

    # -- live updates -------------------------------------------------------

    async def _on_bridge_change(self, changes: list[BridgeChange] | None) -> None:
        """Called by the event stream. None = the device list changed (or the stream reconnected). A button press
        goes to ComfyOS as such – it is no state. A sensor or switch the bridge reaches again or no longer is reported
        with ``online`` alone (a light carries it with its other values)."""
        if changes is None:
            if self.bridge is not None:
                try:
                    await self.bridge.lights(max_age=0)     # refill the cache so later events find their light
                except PluginError:
                    pass
            self.context.devices_changed()
            return
        for item in changes:
            if isinstance(item, HueButtonPress):
                self.context.button_pressed(SWITCH_ID_PREFIX + item.device_id, item.control_id, item.press)
            elif isinstance(item, HueReachabilityChange):
                changed = item.item
                device_id = (MOTION_ID_PREFIX + changed.resource_id if isinstance(changed, HueMotionSensor)
                             else SWITCH_ID_PREFIX + changed.device_id)
                self.context.device_changed(device_id, {"online": changed.reachable})
            elif isinstance(item, HueMotionSensor):
                values = {STATE_MOTION: item.motion, STATE_TEMPERATURE: item.temperature, STATE_ILLUMINANCE: item.illuminance}
                self.context.device_changed(MOTION_ID_PREFIX + item.resource_id,
                                            {key: value for key, value in values.items() if value is not None})
            else:
                self.context.device_changed(LIGHT_ID_PREFIX + item.resource_id, {
                    STATE_ON: item.on,
                    STATE_BRIGHTNESS: item.brightness,
                    "online": item.reachable,
                    "light": item.light_state,
                })

    # -- scenes -------------------------------------------------------------

    async def scenes(self) -> list[ScenePalette]:
        """Return the scenes stored on the bridge with their colours, for importing into ComfyOS."""
        return await self._require_bridge().scenes()

    async def scene_image(self, image_id: str) -> tuple[bytes, str] | None:
        """Return a scene image from the bridge as (bytes, media type), or ``None``; the event log notes once if the
        bridge delivers none."""
        if self.bridge is None:
            return None
        image = await self.bridge.fetch_image(image_id)
        if image is None and not self._missing_images_logged:
            self._missing_images_logged = True
            self.context.event(f"Hue Bridge liefert keine Szenenbilder ({self.bridge.image_status}) – "
                               "ComfyOS zeigt stattdessen die Farben der Szene")
        return image

    # -- endpoints ----------------------------------------------------------

    def routes(self) -> Any:
        """Return the plugin's own routes (pairing and bridge search)."""
        return build_router(self)


def setup(context: PluginContext) -> Plugin:
    """Entry point named in manifest.json."""
    return HuePlugin(context)
