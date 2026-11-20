"""MQTT plugin: devices on an MQTT broker in the home network.

What it does
    * Connects to one broker (address, optional user name and password, optional TLS).
    * Finds devices through Home Assistant MQTT discovery – the way Zigbee2MQTT, ESPHome and
      many other bridges announce what they have –, through the device list of a Zigbee2MQTT
      bridge, through Tasmota's own discovery and through topics the user enters by hand.
    * Reports lights, switches, fans, sensors, contacts, motion detectors, thermostats, covers
      and locks as ComfyOS devices and switches them by publishing to their command topics.
    * Listens to their state topics, so changes show up in ComfyOS at once.
    * Asks devices that stay silent after connecting for their state, where their system offers that.

How the device list comes together
    Discovery messages are retained on the broker and arrive in a burst right after the
    connection is made. The plugin waits until that burst is over before it reports devices.
    Otherwise ComfyOS would see half a list and forget rooms and names of devices that have
    simply not arrived yet. While the broker is unreachable, known devices are shown as offline.

What it never does
    * It does not evaluate templates of a discovery message (see translate.py).
    * It does not announce itself as Home Assistant (``homeassistant/status`` is left alone).
    * It publishes only to command topics a device announced or the user entered, and to the
      topics that ask a Zigbee2MQTT or Tasmota device for its state.

Files
    protocol.py    MQTT 3.1.1 packets (pure functions)
    client.py      connection to the broker: connect, subscribe, keep alive, reconnect
    discovery.py   Home Assistant discovery messages -> entities (pure functions)
    zigbee2mqtt.py device list of a Zigbee2MQTT bridge -> entities (pure functions)
    tasmota.py     Tasmota's own discovery -> entities, and the translators for Tasmota's messages
    translate.py   entity + MQTT message <-> ComfyOS values and commands (pure functions)
    registry.py    entities grouped into devices, with their current values
    manual.py      devices the user describes by hand
    routes.py      endpoints for the settings tile
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Mapping

from plugin_api import Device, DeviceUnreachable, Plugin, PluginContext, PluginError

from . import discovery, manual, zigbee2mqtt
from .client import Credentials, MqttClient
from .manual import ManualDevice
from .protocol import Message
from .registry import DeviceRegistry
from .routes import build_router
from .tasmota import DISCOVERY_FILTERS as TASMOTA_DISCOVERY_FILTERS, TasmotaDiscovery

EGRESS_KEY = "broker"                           # see manifest.json -> egress
DEFAULT_PORT = 1883
CONNECT_TIMEOUT_SECONDS = 6.0
SETTLE_SECONDS = 1.5                            # quiet time after the last discovery message before devices are rebuilt
LONGEST_SETTLE_SECONDS = 10.0                   # rebuild at the latest after this time, even if messages keep coming
MIN_REPORT_INTERVAL_SECONDS = 0.5               # one device reports changes to the interface at most twice a second
STATE_REQUEST_DELAY_SECONDS = 2.0               # time retained states get to arrive before silent devices are asked
STATE_REQUEST_GAP_SECONDS = 0.05                # pause between two state requests, so a large network is not flooded
LOADING_TEXT = "MQTT: Die Geräteliste wird noch geladen."

# Settings and secrets this plugin stores
SETTING_HOST = "host"
SETTING_PORT = "port"
SETTING_USER_NAME = "username"
SETTING_TLS = "tls"
SETTING_VERIFY_TLS = "verify_tls"
SETTING_DISCOVERY_PREFIX = "discovery_prefix"
SETTING_ZIGBEE2MQTT_TOPIC = "zigbee2mqtt_topic"
SETTING_CLIENT_ID = "client_id"                 # created once, so the broker recognises ComfyOS after a restart
SETTING_MANUAL_DEVICES = "manual_devices"
SECRET_PASSWORD = "password"


class MqttPlugin(Plugin):
    """Connects ComfyOS to one MQTT broker and reports the devices found there."""

    def __init__(self, context: PluginContext) -> None:
        """Start without a connection; ``start()`` reads the settings and connects to the broker."""
        super().__init__(context)
        self.registry = DeviceRegistry()
        self._client: MqttClient | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._manual_devices: list[ManualDevice] = []
        self._discovery_prefix = discovery.DEFAULT_PREFIX
        self._zigbee2mqtt_topic = zigbee2mqtt.DEFAULT_BASE_TOPIC
        self._tasmota = TasmotaDiscovery()
        self._state_request_timer: asyncio.TimerHandle | None = None
        self._state_request_task: asyncio.Task[None] | None = None

        self._list_is_complete = False                      # the first burst of discovery messages has been taken in
        self._rebuild_timer: asyncio.TimerHandle | None = None
        self._rebuild_waiting_since = 0.0                   # time.monotonic() when the pending rebuild was first asked for
        self._held_back: dict[str, dict[str, Any]] = {}     # changes waiting for the next report, by device id
        self._reported_at: dict[str, float] = {}            # time.monotonic() of the last report, by device id
        self._report_timer: asyncio.TimerHandle | None = None

    # -- life cycle ---------------------------------------------------------

    async def start(self) -> None:
        """Load the manual devices and, if a broker is configured, start the connection in the background."""
        settings = self.context.settings
        self._manual_devices = manual.load(settings.get(SETTING_MANUAL_DEVICES))
        self._discovery_prefix = self._configured_prefix()
        self._zigbee2mqtt_topic = self._configured_topic(SETTING_ZIGBEE2MQTT_TOPIC, zigbee2mqtt.DEFAULT_BASE_TOPIC)
        self._tasmota.forget()
        self.registry.forget_announcements()
        self.registry.set_manual_entities([manual.to_entity(device) for device in self._manual_devices])
        self.registry.rebuild()
        self._list_is_complete = False
        if not self.host:
            return
        credentials = Credentials(client_id=self._client_id(), user_name=str(settings.get(SETTING_USER_NAME) or "") or None,
                                  password=self.context.secrets.get(SECRET_PASSWORD) or None)
        self._client = MqttClient(self._open_stream, credentials, self._on_message, self._on_connection_change, self.context.log)
        self._client.subscribe(discovery.discovery_filters(self._discovery_prefix))
        self._client.subscribe([zigbee2mqtt.devices_topic(self._zigbee2mqtt_topic)])
        self._client.subscribe(TASMOTA_DISCOVERY_FILTERS)
        self._client.subscribe(self.registry.topics())
        self._connection_task = asyncio.create_task(self._client.run())

    async def stop(self) -> None:
        """End the connection and cancel everything that is still waiting."""
        for timer in (self._rebuild_timer, self._report_timer, self._state_request_timer):
            if timer is not None:
                timer.cancel()
        self._rebuild_timer = self._report_timer = self._state_request_timer = None
        self._held_back.clear()
        if self._state_request_task is not None:
            self._state_request_task.cancel()
            self._state_request_task = None
        if self._client is not None:
            await self._client.close()
        if self._connection_task is not None:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except (asyncio.CancelledError, Exception):     # noqa: BLE001 - the task is over either way
                pass
        self._client = self._connection_task = None

    def status(self) -> dict[str, Any]:
        """Report whether a broker is configured and connected, with the reason while it is not."""
        if not self.host:
            return {"configured": False, "connected": False, "live": False, "message": "Kein Broker eingetragen."}
        if not self.is_connected:
            reason = self._client.last_error if self._client is not None else ""
            return {"configured": True, "connected": False, "live": False, "name": self.host,
                    "message": reason or "Verbindung zum Broker wird aufgebaut."}
        return {"configured": True, "connected": True, "live": True, "name": self.host}

    # -- settings -----------------------------------------------------------

    @property
    def host(self) -> str:
        """Address of the broker as the user entered it; empty when no broker is configured."""
        return str(self.context.settings.get(SETTING_HOST) or "").strip()

    @property
    def is_connected(self) -> bool:
        """True while the session with the broker is established."""
        return self._client is not None and self._client.is_connected

    @property
    def last_error(self) -> str:
        """Why the last connection attempt failed; empty when there was none."""
        return self._client.last_error if self._client is not None else ""

    def _configured_port(self) -> int:
        """Return the broker port from the settings, or the default if it is missing or out of range."""
        port = self.context.settings.get(SETTING_PORT)
        return int(port) if isinstance(port, (int, float)) and 1 <= port <= 65535 else DEFAULT_PORT

    def _configured_prefix(self) -> str:
        """Return the discovery prefix from the settings, or the default if it is empty or contains wildcards."""
        return self._configured_topic(SETTING_DISCOVERY_PREFIX, discovery.DEFAULT_PREFIX)

    def _configured_topic(self, setting: str, default: str) -> str:
        """Return a topic prefix from the settings, or ``default`` if it is empty or contains wildcards."""
        prefix = str(self.context.settings.get(setting) or "").strip().strip("/")
        is_usable = bool(prefix) and not any(character in prefix for character in ("+", "#", " "))
        return prefix if is_usable else default

    def _client_id(self) -> str:
        """Return this installation's client id; it is created once and kept, so the broker recognises the session."""
        client_id = self.context.settings.get(SETTING_CLIENT_ID)
        if not isinstance(client_id, str) or not client_id:
            client_id = "comfyos-" + uuid.uuid4().hex[:8]
            self.context.settings.set(SETTING_CLIENT_ID, client_id)
        return client_id

    async def _open_stream(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Ask ComfyOS for the connection to the broker. Only addresses in the home network are reached."""
        settings = self.context.settings
        return await self.context.open_stream(
            EGRESS_KEY, self.host, self._configured_port(), tls=bool(settings.get(SETTING_TLS)),
            verify=settings.get(SETTING_VERIFY_TLS) is not False, timeout=CONNECT_TIMEOUT_SECONDS)

    # -- devices ------------------------------------------------------------

    async def devices(self) -> list[Device]:
        """Return the devices known from discovery and manual entries. Raises ``DeviceUnreachable`` until the first
        burst of discovery messages has been taken in."""
        if not self.host:
            return []
        if not self._list_is_complete:
            raise DeviceUnreachable(self.last_error or LOADING_TEXT)
        return self.registry.devices(broker_connected=self.is_connected)

    @property
    def manual_devices(self) -> list[ManualDevice]:
        """The devices the user created by hand."""
        return list(self._manual_devices)

    def add_manual_device(self, values: Mapping[str, Any]) -> ManualDevice:
        """Create a manual device from user input. Raises PluginError when the input is not usable."""
        if len(self._manual_devices) >= manual.MAX_MANUAL_DEVICES:
            raise PluginError(f"Höchstens {manual.MAX_MANUAL_DEVICES} eigene Geräte.")
        device = manual.checked(uuid.uuid4().hex[:8], values)
        self._store_manual_devices(self._manual_devices + [device])
        return device

    def remove_manual_device(self, key: str) -> bool:
        """Delete a manual device. Returns False when there is none with this key."""
        remaining = [device for device in self._manual_devices if device.key != key]
        if len(remaining) == len(self._manual_devices):
            return False
        self._store_manual_devices(remaining)
        return True

    def _store_manual_devices(self, devices: list[ManualDevice]) -> None:
        """Save the manually created devices and rebuild the device list."""
        self.context.settings.set(SETTING_MANUAL_DEVICES, manual.dump(devices))
        self._manual_devices = devices
        self.registry.set_manual_entities([manual.to_entity(device) for device in devices])
        self._rebuild_devices()

    # -- commands -----------------------------------------------------------

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
        """Publish the messages a command needs and return the state the device is expected to take."""
        tracked = self.registry.find(device_id)
        if tracked is None:
            raise PluginError("Dieses MQTT-Gerät gibt es nicht mehr.")
        if self._client is None or not self._client.is_connected:
            raise DeviceUnreachable("MQTT-Broker ist nicht verbunden.")
        for message in self.registry.commands(tracked, state):
            await self._client.publish(message.topic, message.payload.encode("utf-8"), message.qos, message.retain)
        return self.registry.assume(tracked, state)

    # -- live updates -------------------------------------------------------

    def _on_connection_change(self, is_connected: bool) -> None:
        """Called by the client when the session with the broker starts or ends."""
        if is_connected:
            self.context.event(f"MQTT-Broker {self.host} verbunden")
            self.registry.forget_state_requests()
            self._ask_for_rebuild()             # the burst of retained discovery messages follows now
        self.context.devices_changed()          # devices switch between online and offline

    def _on_message(self, message: Message) -> None:
        """Called by the client for every message: discovery goes to the registry, states to the interface."""
        if discovery.split_config_topic(self._discovery_prefix, message.topic) is not None:
            announcement = discovery.parse_announcement(self._discovery_prefix, message.topic, message.payload)
            if announcement is not None:
                self.registry.announce(announcement)
                self._ask_for_rebuild()
            return
        if message.topic == zigbee2mqtt.devices_topic(self._zigbee2mqtt_topic):
            announcement = zigbee2mqtt.parse_devices(self._zigbee2mqtt_topic, message.payload)
        else:
            announcement = self._tasmota.handle_message(message.topic, message.payload)
        if announcement is not None:
            self.registry.announce(announcement)
            self._ask_for_rebuild()
            return
        if self._tasmota.is_discovery_topic(message.topic):
            return                              # an unreadable discovery message is not a device state
        changes = self.registry.handle_message(message.topic, message.payload.decode("utf-8", "replace"), message.retain)
        if changes:
            self._report(changes)
        for device_id, button, press in self.registry.take_presses():
            self.context.button_pressed(device_id, button, press)

    def _ask_for_rebuild(self) -> None:
        """Rebuild the device list once discovery messages stop arriving (but not later than LONGEST_SETTLE_SECONDS)."""
        now = time.monotonic()
        if self._rebuild_timer is None:
            self._rebuild_waiting_since = now
        else:
            self._rebuild_timer.cancel()
        time_left = LONGEST_SETTLE_SECONDS - (now - self._rebuild_waiting_since)
        self._rebuild_timer = asyncio.get_running_loop().call_later(max(0.0, min(SETTLE_SECONDS, time_left)), self._rebuild_devices)

    def _rebuild_devices(self) -> None:
        """Group the entities into devices again, subscribe to their topics and tell ComfyOS when the list changed."""
        if self._rebuild_timer is not None:
            self._rebuild_timer.cancel()
            self._rebuild_timer = None
        list_changed = self.registry.rebuild()
        if self._client is not None:
            self._client.subscribe(self.registry.topics())
        became_complete = self.is_connected and not self._list_is_complete
        if became_complete:
            self._list_is_complete = True
        if list_changed or became_complete:
            self.context.devices_changed()
        if self.is_connected and self._state_request_timer is None:
            self._state_request_timer = asyncio.get_running_loop().call_later(STATE_REQUEST_DELAY_SECONDS, self._start_state_requests)

    def _start_state_requests(self) -> None:
        """Ask the devices that are still silent for their state, in the background."""
        self._state_request_timer = None
        if self._state_request_task is not None:            # a round is still running: look again afterwards
            self._state_request_timer = asyncio.get_running_loop().call_later(STATE_REQUEST_DELAY_SECONDS, self._start_state_requests)
            return
        requests = self.registry.due_state_requests()
        if requests:
            self._state_request_task = asyncio.create_task(self._send_state_requests(requests))

    async def _send_state_requests(self, requests: list[discovery.StateRequest]) -> None:
        """Publish state requests one after the other. A lost connection ends the round; the next one starts anew."""
        try:
            for request in requests:
                if self._client is None or not self._client.is_connected:
                    return
                await self._client.publish(request.topic, request.payload.encode("utf-8"))
                await asyncio.sleep(STATE_REQUEST_GAP_SECONDS)
        except PluginError as error:
            self.context.log.info("MQTT state requests stopped: %s", type(error).__name__)
        finally:
            self._state_request_task = None

    def _report(self, changes: Mapping[str, Mapping[str, Any]]) -> None:
        """Pass changes to ComfyOS; a device that reports very often is held back to two reports a second."""
        now = time.monotonic()
        for device_id, values in changes.items():
            is_due = now - self._reported_at.get(device_id, 0.0) >= MIN_REPORT_INTERVAL_SECONDS
            if is_due and device_id not in self._held_back:
                self._reported_at[device_id] = now
                self.context.device_changed(device_id, values)
            else:
                self._held_back.setdefault(device_id, {}).update(values)
        if self._held_back and self._report_timer is None:
            self._report_timer = asyncio.get_running_loop().call_later(MIN_REPORT_INTERVAL_SECONDS, self._report_held_back)

    def _report_held_back(self) -> None:
        """Push the state changes that were held back to limit how often a chatty device is reported."""
        self._report_timer = None
        now = time.monotonic()
        held_back, self._held_back = self._held_back, {}
        for device_id, values in held_back.items():
            self._reported_at[device_id] = now
            self.context.device_changed(device_id, values)

    # -- endpoints ----------------------------------------------------------

    def routes(self) -> Any:
        """Return the plugin's own routes (manual devices and diagnosis)."""
        return build_router(self)


def setup(context: PluginContext) -> Plugin:
    """Entry point named in manifest.json. The connection is made later, in ``start()``."""
    return MqttPlugin(context)
