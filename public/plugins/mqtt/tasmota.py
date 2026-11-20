"""Tasmota's own discovery: read what a Tasmota device announces and translate its messages. No network access.

Tasmota devices describe themselves on two retained topics (``SetOption19 0``, the default)::

    tasmota/discovery/<MAC>/config      the device: name, topics, relays, kind of light, shutters
    tasmota/discovery/<MAC>/sensors     the sensors it carries, in the shape of its sensor messages

An empty message means "this device is gone". The format is the one Home Assistant's Tasmota
integration reads; it is not Home Assistant MQTT discovery, so it has its own reader here.

A Tasmota device talks on three topics built from its *full topic* (default
``%prefix%/%topic%/``) and the three prefixes ``cmnd`` (commands), ``stat`` (answers) and
``tele`` (periodic reports)::

    cmnd/<topic>/POWER1   ON            switch relay 1
    stat/<topic>/RESULT   {"POWER1": "ON"}
    tele/<topic>/STATE    {"POWER1": "ON", "Dimmer": 60, …}       every few minutes
    tele/<topic>/SENSOR   {"AM2301": {"Temperature": 21.5, "Humidity": 48}}
    tele/<topic>/LWT      Online / Offline                         (retained)

What this module supports
    * relays as switches (``POWER<n>``; a device with one relay reports plain ``POWER``)
    * lights: on/off, brightness (``Dimmer``), white tone (``CT``), colour (``HSBColor``)
    * shutters: open, close, stop and position (Tasmota counts 100 as open)
    * sensors: temperature, humidity, power and energy – through the ordinary sensor translator

Reference: the "Commands" page of the Tasmota documentation and the ``hatasmota`` library.
"""
from __future__ import annotations

import colorsys
import json
import re
from typing import Any, Mapping

from plugin_api import STATE_BRIGHTNESS, STATE_ON, STATE_POSITION, Capability, PluginError, hex_to_rgb, hex_to_xy, rgb_to_hex, xy_to_hex

from . import protocol
from .discovery import Announcement, DeviceInfo, Entity, StateRequest
from .translate import LampTranslator, Outgoing, Reading, Translator, to_number, wanted_light

DISCOVERY_FILTERS = ["tasmota/discovery/+/config", "tasmota/discovery/+/sensors"]
MAX_DEVICES = 300
MAX_RELAYS = 32
MAX_SENSOR_VALUES = 40
MAX_TEXT_LENGTH = 80
VENDOR_TEXT = "Tasmota"

PLATFORM_RELAY = "tasmota_relay"
PLATFORM_LIGHT = "tasmota_light"
PLATFORM_SHUTTER = "tasmota_shutter"

# Kinds of relay in the "rl" list of a config message
RELAY_SWITCH = 1
RELAY_LIGHT = 2
RELAY_SHUTTER = 3

# Kinds of light in "lt_st" of a config message
LIGHT_DIMMER = 1
LIGHT_WHITE_TONES = 2
LIGHT_RGB = 3
LIGHT_RGB_WHITE = 4
LIGHT_RGB_WHITE_TONES = 5

SET_OPTION_SEPARATE_CHANNELS = "68"         # every light channel is a dimmer of its own; only on/off is used then
WHITE_RANGE = (153, 500)                    # mirek; the range of Tasmota's CT command

# Sensor readings with a meaning for ComfyOS: key in the sensor message -> (device class, unit)
SENSOR_VALUES = {
    "Temperature": ("temperature", "°C"),
    "Humidity": ("humidity", "%"),
}
ENERGY_SENSOR = "ENERGY"
ENERGY_VALUES = {
    "Power": ("power", "W"),
    "Total": ("energy", "kWh"),
}

_DISCOVERY_TOPIC = re.compile(r"^tasmota/discovery/([0-9A-Fa-f]{12})/(config|sensors)$")
_SENSOR_NAME = re.compile(r"^[A-Za-z0-9_. -]{1,40}$")


# ---------------------------------------------------------------------------
# Reading the discovery messages
# ---------------------------------------------------------------------------

def _clip(value: Any) -> str:
    """Return a text as a trimmed, length-limited string; anything else becomes empty."""
    return value.strip()[:MAX_TEXT_LENGTH] if isinstance(value, str) else ""


class _DeviceTopics:
    """The topics of one Tasmota device, built from its full topic and its three prefixes."""

    def __init__(self, config: Mapping[str, Any], mac: str) -> None:
        """Read topic, full topic and prefixes. Raises ``ValueError`` when they do not give usable topics."""
        topic, full_topic, prefixes = config.get("t"), config.get("ft"), config.get("tp")
        if not isinstance(topic, str) or not isinstance(full_topic, str) or not isinstance(prefixes, list) or len(prefixes) != 3:
            raise ValueError("topics missing")
        filled = (full_topic.replace("%topic%", topic).replace("%hostname%", _clip(config.get("hn")))
                  .replace("%id%", mac))
        self.command, self.answer, self.report = (filled.replace("%prefix%", str(prefix)) for prefix in prefixes)
        if not all(protocol.is_publish_topic(base) and base.endswith("/") for base in (self.command, self.answer, self.report)):
            raise ValueError("topics unusable")

    @property
    def state_topics(self) -> list[str]:
        """Topics that carry relay and light states: answers to commands and the periodic report."""
        return [self.answer + "RESULT", self.report + "STATE"]


def _relay_name(config: Mapping[str, Any], index: int) -> str:
    """Return the friendly name the user gave relay ``index`` in Tasmota; empty for the first one (the device itself)."""
    names = config.get("fn")
    if index == 0 or not isinstance(names, list) or index >= len(names):
        return ""
    return _clip(names[index]) or str(index + 1)


def _relay_entities(config: Mapping[str, Any], info: DeviceInfo, topics: _DeviceTopics, shared: Mapping[str, Any]) -> list[Entity]:
    """Describe the relays of a device: switches, the light and shutters (a shutter uses two relays)."""
    relays = config.get("rl")
    relays = [kind for kind in relays if isinstance(kind, int)][:MAX_RELAYS] if isinstance(relays, list) else []
    options = config.get("so") if isinstance(config.get("so"), Mapping) else {}
    payloads = config.get("state") if isinstance(config.get("state"), list) and len(config["state"]) >= 2 else ["OFF", "ON"]
    entities = []
    shutter_relays = 0
    for index, kind in enumerate(relays):
        base = {**shared, "index": index + 1, "state_topics": topics.state_topics, "command_base": topics.command,
                "payload_off": str(payloads[0]), "payload_on": str(payloads[1])}
        unique_id = f"{info.identifier}_relay_{index + 1}"
        if kind == RELAY_SWITCH:
            entities.append(Entity(unique_id, PLATFORM_RELAY, _relay_name(config, index), info, base))
        elif kind == RELAY_LIGHT:
            light_kind = config.get("lt_st") if not options.get(SET_OPTION_SEPARATE_CHANNELS) else 0
            entities.append(Entity(unique_id, PLATFORM_LIGHT, _relay_name(config, index), info, {**base, "light_kind": light_kind}))
        elif kind == RELAY_SHUTTER:
            shutter_relays += 1
            if shutter_relays % 2 == 1:         # the first of the two relays that drive one shutter
                number = shutter_relays // 2 + 1
                shutter = {**shared, "index": number, "command_base": topics.command,
                           "state_topics": [topics.answer + "RESULT", topics.report + "SENSOR"]}
                entities.append(Entity(f"{info.identifier}_shutter_{number}", PLATFORM_SHUTTER,
                                       "" if number == 1 else str(number), info, shutter))
    return entities


def _sensor_template(sensor: str, value: str) -> str:
    """Return the template that picks one reading out of a sensor message."""
    return "{{ value_json['" + sensor + "']['" + value + "'] }}"


def _sensor_entities(sensors: Mapping[str, Any], info: DeviceInfo, topics: _DeviceTopics, shared: Mapping[str, Any]) -> list[Entity]:
    """Describe the sensors of a device as ordinary sensor entities reading ``tele/<topic>/SENSOR``."""
    readings = sensors.get("sn")
    if not isinstance(readings, Mapping):
        return []
    temperature_unit = "°F" if readings.get("TempUnit") == "F" else "°C"
    entities: list[Entity] = []
    for sensor, values in readings.items():
        if not isinstance(values, Mapping) or not isinstance(sensor, str) or not _SENSOR_NAME.match(sensor):
            continue
        wanted = ENERGY_VALUES if sensor == ENERGY_SENSOR else SENSOR_VALUES
        for value, (device_class, unit) in wanted.items():
            if value not in values or len(entities) >= MAX_SENSOR_VALUES:
                continue
            config = {**shared, "state_topic": topics.report + "SENSOR", "value_template": _sensor_template(sensor, value),
                      "device_class": device_class, "unit_of_measurement": temperature_unit if device_class == "temperature" else unit}
            entities.append(Entity(f"{info.identifier}_{sensor}_{value}", "sensor", f"{sensor} {value}", info, config))
    return entities


class TasmotaDiscovery:
    """Collects the two discovery messages of every Tasmota device and turns them into announcements."""

    def __init__(self) -> None:
        """Start without any device."""
        self._configs: dict[str, Mapping[str, Any]] = {}        # by MAC address
        self._sensors: dict[str, Mapping[str, Any]] = {}

    @staticmethod
    def is_discovery_topic(topic: str) -> bool:
        """True if ``topic`` is one of the two discovery topics of a Tasmota device."""
        return _DISCOVERY_TOPIC.match(topic) is not None

    def forget(self) -> None:
        """Drop everything that was announced (before connecting again)."""
        self._configs.clear()
        self._sensors.clear()

    def handle_message(self, topic: str, payload: bytes) -> Announcement | None:
        """Take in one discovery message and return what its device announces now.

        Returns None when the topic is not a discovery topic or the payload is unusable; an
        announcement without entities when the device is gone or not described completely yet.
        """
        match = _DISCOVERY_TOPIC.match(topic)
        if match is None:
            return None
        mac, part = match.group(1).upper(), match.group(2)
        store = self._configs if part == "config" else self._sensors
        if not payload.strip():
            store.pop(mac, None)
        else:
            try:
                message = json.loads(payload)
            except ValueError:
                return None
            if not isinstance(message, Mapping) or (mac not in store and len(store) >= MAX_DEVICES):
                return None
            store[mac] = message
        return self._announcement(mac)

    def _announcement(self, mac: str) -> Announcement:
        """Describe one device from its config message and, if it has arrived, its sensors message."""
        source = f"tasmota/discovery/{mac}"
        config = self._configs.get(mac)
        if config is None:
            return Announcement(source=source)
        try:
            topics = _DeviceTopics(config, mac)
        except ValueError:
            return Announcement(source=source)
        identifier = f"tasmota_{mac}"
        info = DeviceInfo(identifier=identifier, name=_clip(config.get("dn")) or identifier, manufacturer=VENDOR_TEXT,
                          model=_clip(config.get("md")) or None)
        shared = {"availability_topic": topics.report + "LWT", "payload_available": _clip(config.get("onln")) or "Online",
                  "payload_not_available": _clip(config.get("ofln")) or "Offline"}
        entities = _relay_entities(config, info, topics, shared)
        entities += _sensor_entities(self._sensors.get(mac) or {}, info, topics, shared)
        # "STATE" without a value makes the device publish its state on stat/<topic>/RESULT.
        requests = (StateRequest(identifier, topics.command + "STATE"),) if entities else ()
        return Announcement(source=source, entities=tuple(entities), state_requests=requests)


# ---------------------------------------------------------------------------
# Translators
# ---------------------------------------------------------------------------

def _json_object(text: str) -> Mapping[str, Any]:
    """Return a message as a JSON object; an empty one for anything else."""
    try:
        message = json.loads(text)
    except ValueError:
        return {}
    return message if isinstance(message, Mapping) else {}


class RelayTranslator(Translator):
    """One relay of a Tasmota device: on and off."""

    kind = "switch"

    def __init__(self, entity: Entity) -> None:
        """Read the relay's number, topics and payloads from the entity built by :class:`TasmotaDiscovery`."""
        super().__init__(entity)
        self._index = int(self.config["index"])
        self._state_topics = list(self.config["state_topics"])
        self._command_topic = f"{self.config['command_base']}POWER{self._index}"
        self._payload_on, self._payload_off = str(self.config["payload_on"]), str(self.config["payload_off"])

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the state keys this relay fills: on/off."""
        return (STATE_ON,)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (answers and the periodic report)."""
        return self._state_topics

    def _read_power(self, message: Mapping[str, Any]) -> bool | None:
        """Read this relay's state from a message; a device with a single relay reports ``POWER`` without a number."""
        value = message.get(f"POWER{self._index}")
        if value is None and self._index == 1:
            value = message.get("POWER")
        if not isinstance(value, str):
            return None
        if value.upper() == self._payload_on.upper():
            return True
        return False if value.upper() == self._payload_off.upper() else None

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into on/off; messages about other relays change nothing."""
        is_on = self._read_power(_json_object(text))
        return Reading(state={STATE_ON: is_on}) if is_on is not None else Reading()

    def capabilities(self) -> list[Capability]:
        """Return the switchable on/off capability."""
        return [Capability(STATE_ON, "bool", writable=True)]

    def _power_message(self, switch_on: bool) -> Outgoing:
        """Return the message that switches this relay."""
        return Outgoing(self._command_topic, self._payload_on if switch_on else self._payload_off)

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the message that switches on or off, or nothing if the state does not ask for it."""
        return [self._power_message(bool(state[STATE_ON]))] if STATE_ON in state else []


class TasmotaLightTranslator(RelayTranslator, LampTranslator):
    """The light of a Tasmota device: on/off and, depending on its kind, brightness, white tone and colour."""

    kind = "light"

    def __init__(self, entity: Entity) -> None:
        """Read what the light can show from its kind (``lt_st`` of the config message)."""
        super().__init__(entity)
        light_kind = self.config.get("light_kind")
        self.is_dimmable = light_kind in (LIGHT_DIMMER, LIGHT_WHITE_TONES, LIGHT_RGB, LIGHT_RGB_WHITE, LIGHT_RGB_WHITE_TONES)
        self.has_color = light_kind in (LIGHT_RGB, LIGHT_RGB_WHITE, LIGHT_RGB_WHITE_TONES)
        self.white_range = WHITE_RANGE if light_kind in (LIGHT_WHITE_TONES, LIGHT_RGB_WHITE_TONES) else None
        self._command_base = str(self.config["command_base"])

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the state keys of a light: on/off, plus brightness if it can be dimmed."""
        return (STATE_ON, STATE_BRIGHTNESS) if self.is_dimmable else (STATE_ON,)

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into on/off, brightness, white tone and colour, as far as it mentions them."""
        message = _json_object(text)
        reading = Reading()
        is_on = self._read_power(message)
        if is_on is not None:
            reading.state[STATE_ON] = is_on
        if not self.is_dimmable:
            return reading
        brightness = to_number(message.get("Dimmer"))
        if brightness is not None:
            reading.state[STATE_BRIGHTNESS] = int(max(0, min(100, round(brightness))))
        mirek = to_number(message.get("CT"))
        if mirek is not None and mirek > 0 and self.white_range is not None:
            reading.mirek = int(round(mirek))
        hue_saturation = str(message.get("HSBColor") or "").split(",")
        hue, saturation = (to_number(part) for part in hue_saturation[:2]) if len(hue_saturation) == 3 else (None, None)
        if hue is not None and saturation is not None and self.has_color:
            red, green, blue = colorsys.hsv_to_rgb(hue / 360, saturation / 100, 1.0)
            reading.xy = hex_to_xy(rgb_to_hex(red * 255, green * 255, blue * 255))
        if reading.mirek is not None or reading.xy is not None:
            shows_white = to_number(message.get("White")) or 0
            reading.color_mode = "ct" if self.white_range is not None and (shows_white > 0 or not self.has_color) else "xy"
        return reading

    def capabilities(self) -> list[Capability]:
        """Return on/off and, for dimmable lights, brightness in percent."""
        capabilities = [Capability(STATE_ON, "bool", writable=True)]
        if self.is_dimmable:
            capabilities.append(Capability(STATE_BRIGHTNESS, "number", unit="%", minimum=1, maximum=100, writable=True))
        return capabilities

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the messages that bring the light to the wanted state. Brightness 0 means off; setting brightness,
        colour or white tone switches the light on (Tasmota does that by itself)."""
        brightness = to_number(state.get(STATE_BRIGHTNESS))
        if brightness is not None and brightness <= 0:
            return [self._power_message(False)]
        messages = []
        wanted_xy, wanted_mirek = wanted_light(state)
        if wanted_xy is not None and self.has_color:
            red, green, blue = hex_to_rgb(xy_to_hex(*wanted_xy))
            hue, saturation, _ = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
            messages.append(Outgoing(self._command_base + "HSBColor1", str(round(hue * 360))))
            messages.append(Outgoing(self._command_base + "HSBColor2", str(round(saturation * 100))))
        elif wanted_mirek is not None and self.white_range is not None:
            messages.append(Outgoing(self._command_base + "CT", str(int(max(WHITE_RANGE[0], min(WHITE_RANGE[1], wanted_mirek))))))
        if brightness is not None and self.is_dimmable:
            messages.append(Outgoing(self._command_base + "Dimmer", str(int(max(1, min(100, round(brightness)))))))
        if STATE_ON in state and (not messages or not state[STATE_ON]):
            messages.append(self._power_message(bool(state[STATE_ON])))
        return messages


class ShutterTranslator(Translator):
    """A shutter driven by a Tasmota device. Tasmota counts 100 as open, ComfyOS counts 0 as open."""

    kind = "cover"

    def __init__(self, entity: Entity) -> None:
        """Read the shutter's number and topics from the entity built by :class:`TasmotaDiscovery`."""
        super().__init__(entity)
        self._index = int(self.config["index"])
        self._state_topics = list(self.config["state_topics"])
        self._command_base = str(self.config["command_base"])

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the state key of a cover: its position."""
        return (STATE_POSITION,)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (answers and the periodic sensor report)."""
        return self._state_topics

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into a position; messages without this shutter change nothing."""
        shutter = _json_object(text).get(f"Shutter{self._index}")
        position = to_number(shutter.get("Position")) if isinstance(shutter, Mapping) else None
        if position is None:
            return Reading()
        return Reading(state={STATE_POSITION: int(round(100 - max(0.0, min(100.0, position))))})

    def capabilities(self) -> list[Capability]:
        """Return the position capability in percent."""
        return [Capability(STATE_POSITION, "number", unit="%", minimum=0, maximum=100, writable=True)]

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the message for up, down, stop or a target position. Raises ``PluginError`` for an unknown command
        or a position outside 0-100."""
        direction = state.get("cover")              # "up", "down" or "stop" from the tile's buttons
        if direction is not None:
            command = {"up": "ShutterOpen", "down": "ShutterClose", "stop": "ShutterStop"}.get(str(direction))
            if command is None:
                raise PluginError("Dieser Rollladen kennt den Befehl nicht.")
            return [Outgoing(f"{self._command_base}{command}{self._index}", "")]
        if state.get(STATE_POSITION) is None:
            return []
        position = to_number(state[STATE_POSITION])
        if position is None or not 0 <= position <= 100:
            raise PluginError("Position muss zwischen 0 und 100 liegen.")
        return [Outgoing(f"{self._command_base}ShutterPosition{self._index}", str(int(round(100 - position))))]


TRANSLATORS: dict[str, type[Translator]] = {
    PLATFORM_RELAY: RelayTranslator,
    PLATFORM_LIGHT: TasmotaLightTranslator,
    PLATFORM_SHUTTER: ShutterTranslator,
}
