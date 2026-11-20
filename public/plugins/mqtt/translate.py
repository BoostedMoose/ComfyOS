"""Translate between the MQTT messages of an entity and ComfyOS values. Pure functions, no network access.

For every supported platform there is one *translator*. It is built from the expanded
configuration of an entity (see ``discovery.py``) and answers four questions:

* ``topics``        which topics carry the entity's state
* ``read``          what a message on one of these topics says, as a :class:`Reading`
* ``capabilities``  what ComfyOS can read and switch
* ``commands``      which messages to publish for a ComfyOS command, as :class:`Outgoing`

Lights, switches, fans, sensors, contacts, thermostats and covers use the state keys every
part of ComfyOS knows (``on``, ``brightness``, ``temperature`` …). Locks, selects, numbers and
buttons have no such key: they become *labelled* capabilities of their device (see
:func:`value_key`), which ComfyOS lists in the device sheet under "Weitere Funktionen".

Templates are never evaluated. Home Assistant configurations use Jinja2 templates to pick a
value out of a message. This plugin understands the one form nearly all devices use,
``{{ value_json.some.key }}``, by reading it as a path into the JSON message. Anything else
raises :class:`Unsupported`, and the entity is listed as "not supported" in the tile.

Units ComfyOS expects: brightness and positions in per cent, temperatures in °C, white tones
in mirek, colours as CIE xy. A cover position of 0 means open and 100 means closed.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from plugin_api import (
    PRESS_DOUBLE,
    PRESS_LONG,
    PRESS_SHORT,
    STATE_ALARM,
    STATE_BRIGHTNESS,
    STATE_HUMIDITY,
    STATE_ILLUMINANCE,
    STATE_MOTION,
    STATE_ON,
    STATE_OPEN,
    STATE_POSITION,
    STATE_PRESENT,
    STATE_TARGET,
    STATE_TEMPERATURE,
    Capability,
    PluginError,
    hex_to_rgb,
    hex_to_xy,
    kelvin_to_mirek,
    rgb_to_hex,
    xy_to_hex,
    xy_to_mirek,
)

from . import protocol
from .discovery import Entity

# Device kinds, best first: when a device has several functions, the first one decides what it is.
KIND_ORDER = ("light", "thermostat", "cover", "switch", "contact", "motion", "smoke", "presence", "sensor", "button",
              "unknown")

DEFAULT_BRIGHTNESS_SCALE = 255
DEFAULT_MIN_MIREK = 153
DEFAULT_MAX_MIREK = 500
DEFAULT_MIN_TARGET = 7.0                    # °C, Home Assistant's defaults for a thermostat
DEFAULT_MAX_TARGET = 35.0
DEFAULT_TARGET_STEP = 0.5
MAX_DETAIL_LENGTH = 40                      # readings shown in the device sheet are clipped to this length
MAX_OPTIONS = 40                            # choices of a select or preset modes of a fan
MAX_OPTION_LENGTH = 60
LABEL_LOCKED = "Verriegelt"                 # labels of extra capabilities, shown in the device sheet
LABEL_FAN_SPEED = "Stufe"
LABEL_FAN_MODE = "Modus"
# Sensor classes that never make a device a "sensor" on their own: they are extra values of some other device.
VALUES_WITHOUT_KIND = ("battery", "power", "energy")
# Kinds whose main value can be switched. A second entity of such a kind is a second channel of the device.
SWITCHABLE_KINDS = ("light", "switch", "cover")

# Binary sensor device classes and what they are in ComfyOS: class -> (device kind, state key)
BINARY_SENSOR_CLASSES = {
    "door": ("contact", STATE_OPEN),
    "window": ("contact", STATE_OPEN),
    "garage_door": ("contact", STATE_OPEN),
    "opening": ("contact", STATE_OPEN),
    "motion": ("motion", STATE_MOTION),
    "occupancy": ("motion", STATE_MOTION),
    "moving": ("motion", STATE_MOTION),
    "smoke": ("smoke", STATE_ALARM),
    "gas": ("smoke", STATE_ALARM),
    "carbon_monoxide": ("smoke", STATE_ALARM),
    "presence": ("presence", STATE_PRESENT),
}

# Colour modes of a light that mean "full colour", and the form of the "color" object each one uses.
COLOR_MODES_XY = ("xy",)
COLOR_MODES_HS = ("hs",)
COLOR_MODES_RGB = ("rgb", "rgbw", "rgbww")

_JSON_PATH_TEMPLATE = re.compile(
    r"""^\{\{\s*value_json((?:\.[A-Za-z_][A-Za-z0-9_]*|\[\s*(?:'[^'\]]*'|"[^"\]]*"|\d+)\s*\])+)\s*\}\}$""")
_JSON_PATH_STEP = re.compile(r"""\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*(?:'([^'\]]*)'|"([^"\]]*)"|(\d+))\s*\]""")
_RAW_VALUE_TEMPLATE = re.compile(r"^\{\{\s*value\s*\}\}$")
_COMMAND_PLACEHOLDER = re.compile(r"\{\{\s*(?:value|position)\s*\}\}")
_NOT_A_KEY_CHARACTER = re.compile(r"[^a-z0-9]+")

ValuePath = tuple[Any, ...]                # keys and list indexes into a JSON message; () = the whole payload


class Unsupported(Exception):
    """The entity uses something this plugin cannot handle. The message names it, in German."""


@dataclass(frozen=True)
class Outgoing:
    """One message to publish for a command."""

    topic: str
    payload: str
    qos: int = 0
    retain: bool = False


@dataclass
class Reading:
    """What one MQTT message says about a device. Fields that the message does not mention stay empty."""

    state: dict[str, Any] = field(default_factory=dict)     # values by ComfyOS state key
    battery: int | None = None                              # per cent
    power_w: float | None = None
    energy_kwh: float | None = None
    mirek: int | None = None                                # white tone of a light
    xy: tuple[float, float] | None = None                   # colour of a light
    color_mode: str | None = None                           # "ct" or "xy": which of the two the light shows
    detail: tuple[str, str] | None = None                   # (label, text) of a further reading, e.g. ("Helligkeit", "120 lx")
    press: tuple[str, str] | None = None                    # (button id, kind of press): a button was pressed (an event)


@dataclass
class LightValues:
    """The current light of a lamp, as far as its messages told us."""

    mirek: int | None = None
    xy: tuple[float, float] | None = None
    color_mode: str | None = None


# ---------------------------------------------------------------------------
# Templates and payloads
# ---------------------------------------------------------------------------

def parse_value_template(template: Any) -> ValuePath:
    """Turn a value template into a path into the JSON message.

    No template and ``{{ value }}`` mean "the whole payload" (an empty path).
    ``{{ value_json.state }}`` becomes ``("state",)``, ``{{ value_json['a'][0] }}`` becomes ``("a", 0)``.
    Raises Unsupported for every other template.
    """
    if template is None or template == "":
        return ()
    if not isinstance(template, str):
        raise Unsupported("Vorlage nicht lesbar")
    text = template.strip()
    if _RAW_VALUE_TEMPLATE.match(text):
        return ()
    match = _JSON_PATH_TEMPLATE.match(text)
    if match is None:
        raise Unsupported("Vorlage nicht lesbar")
    path: list[Any] = []
    for step in _JSON_PATH_STEP.finditer(match.group(1)):
        attribute, single_quoted, double_quoted, index = step.groups()
        if index is not None:
            path.append(int(index))
        else:
            path.append(next(key for key in (attribute, single_quoted, double_quoted) if key is not None))
    return tuple(path)


def extract_value(payload_text: str, path: ValuePath) -> Any:
    """Return the value at ``path`` in the message, or None when the message does not carry it."""
    if not path:
        return payload_text
    try:
        value = json.loads(payload_text)
    except ValueError:
        return None
    for step in path:
        if isinstance(step, int) and isinstance(value, list) and step < len(value):
            value = value[step]
        elif isinstance(value, Mapping) and step in value:
            value = value[step]
        else:
            return None
    return value


def render_command_template(template: Any, value: str) -> str | None:
    """Fill a command template that only inserts the value, e.g. ``{"position": {{ position }}}``.

    Returns ``value`` itself when there is no template and None when the template does more
    than insert the value (it would have to be evaluated, which this plugin never does).
    """
    if template is None or template == "":
        return value
    if not isinstance(template, str):
        return None
    rendered = _COMMAND_PLACEHOLDER.sub(value, template)
    if "{{" in rendered or "{%" in rendered:
        return None
    return rendered


def payload_text(value: Any) -> str:
    """Write a value the way it appears in an MQTT payload: true/false in lower case, objects as JSON."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Mapping, list)):
        return json.dumps(value)
    return str(value)


def same_payload(value: Any, expected: Any) -> bool:
    """Compare a received value with a configured payload, ignoring case (``True`` equals ``true``)."""
    return payload_text(value).strip().lower() == payload_text(expected).strip().lower()


def to_number(value: Any) -> float | None:
    """Read a number from a payload value; None for anything that is not a finite number."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _topic(config: Mapping[str, Any], key: str) -> str | None:
    """Return a topic from the configuration if it is one a message can be published to or read from."""
    topic = config.get(key)
    return topic if isinstance(topic, str) and protocol.is_publish_topic(topic) else None


def _qos(config: Mapping[str, Any]) -> int:
    """Return the quality of service to publish with: 0 or 1 (a configured 2 is sent as 1)."""
    return 1 if config.get("qos") in (1, 2) else 0          # quality of service 2 is sent as 1


def _clean_number(number: float) -> float | int:
    """Return whole numbers without a trailing ``.0`` (nicer in payloads and in the interface)."""
    return int(number) if float(number).is_integer() else round(number, 2)


def value_key(entity: Entity, name: str) -> str:
    """Return the state key of an extra value of a device, e.g. ``mode_3fa9c1``.

    ComfyOS has no well-known key for a preset mode, a select or a button. Such a value gets a
    key made of a readable part (from ``name``) and a short digest of the entity's unique id, so
    two values of one device never share a key and the key stays the same across restarts.
    """
    readable = _NOT_A_KEY_CHARACTER.sub("_", name.lower()).strip("_")[:24] or "value"
    digest = hashlib.blake2s(entity.unique_id.encode("utf-8"), digest_size=3).hexdigest()
    return f"{readable}_{digest}"


@dataclass(frozen=True)
class ValueChannel:
    """One value of an entity: the topic its state arrives on and the topic a new value is published to."""

    state_topic: str | None
    value_path: ValuePath
    command_topic: str | None                   # None = the value can only be read
    command_template: Any = None
    qos: int = 0
    retain: bool = False

    def read(self, topic: str, text: str) -> Any:
        """Return the value a message carries, or None when the message is not about this value."""
        if self.state_topic is None or topic != self.state_topic:
            return None
        value = extract_value(text, self.value_path)
        return None if value == "" else value

    def outgoing(self, value: str) -> Outgoing:
        """Return the message that sets the value. Only call this when ``command_topic`` is set."""
        payload = render_command_template(self.command_template, value)
        return Outgoing(self.command_topic or "", value if payload is None else payload, self.qos, self.retain)


def value_channel(config: Mapping[str, Any], state_topic_key: str, template_key: str, command_topic_key: str,
                  command_template_key: str = "command_template") -> ValueChannel:
    """Read one value channel from a configuration.

    A command template that would have to be evaluated makes the value read-only.
    Raises ``Unsupported`` when the value template is not a plain path into the message.
    """
    command_topic = _topic(config, command_topic_key)
    command_template = config.get(command_template_key)
    if render_command_template(command_template, "0") is None:
        command_topic = None
    return ValueChannel(state_topic=_topic(config, state_topic_key), value_path=parse_value_template(config.get(template_key)),
                        command_topic=command_topic, command_template=command_template,
                        qos=_qos(config), retain=bool(config.get("retain")))


def _text_options(values: Any) -> tuple[str, ...]:
    """Return the choices of a select or the preset modes of a fan as short texts, without duplicates."""
    if not isinstance(values, list):
        return ()
    texts = [str(value)[:MAX_OPTION_LENGTH] for value in values if isinstance(value, (str, int, float)) and not isinstance(value, bool)]
    return tuple(dict.fromkeys(text for text in texts if text))[:MAX_OPTIONS]


# ---------------------------------------------------------------------------
# Translators
# ---------------------------------------------------------------------------

class Translator:
    """Base class: what every platform translator offers. See the module docstring."""

    #: Device kind this entity suggests, or None when it only adds values to a device (battery, power …).
    kind: str | None = None

    def __init__(self, entity: Entity) -> None:
        """Keep the entity and its discovery configuration."""
        self.entity = entity
        self.config = entity.config

    @property
    def state_keys(self) -> tuple[str, ...]:
        """State keys this entity owns. Two entities of one device cannot own the same key."""
        return ()

    @property
    def topics(self) -> list[str]:
        """Topics that carry this entity's state."""
        return []

    def read(self, topic: str, text: str) -> Reading:
        """Return what a message on ``topic`` says. An empty Reading means "nothing for us"."""
        return Reading()

    def capabilities(self) -> list[Capability]:
        """Return what ComfyOS can read and switch on this entity."""
        return []

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the messages to publish for a ComfyOS command; empty when the command is not for this entity."""
        return []


class SwitchTranslator(Translator):
    """A socket or relay: on and off."""

    kind = "switch"

    def __init__(self, entity: Entity) -> None:
        """Read topics, payloads and the value template of a switch. Raises ``Unsupported`` if it has no topic at all."""
        super().__init__(entity)
        self._state_topic = _topic(self.config, "state_topic")
        self._command_topic = _topic(self.config, "command_topic")
        self._value_path = parse_value_template(self.config.get("value_template"))
        self._payload_on = self.config.get("payload_on", "ON")
        self._payload_off = self.config.get("payload_off", "OFF")
        self._state_on = self.config.get("state_on", self._payload_on)
        self._state_off = self.config.get("state_off", self._payload_off)
        if self._state_topic is None and self._command_topic is None:
            raise Unsupported("ohne Topic")

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the state keys this entity fills: on/off."""
        return (STATE_ON,)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (the state topic, if there is one)."""
        return [self._state_topic] if self._state_topic else []

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message on the state topic into on/off; unknown payloads change nothing."""
        value = extract_value(text, self._value_path)
        if value is None:
            return Reading()
        if same_payload(value, self._state_on):
            return Reading(state={STATE_ON: True})
        if same_payload(value, self._state_off):
            return Reading(state={STATE_ON: False})
        return Reading()

    def capabilities(self) -> list[Capability]:
        """Return the on/off capability; writable only if the switch has a command topic."""
        return [Capability(STATE_ON, "bool", writable=self._command_topic is not None)]

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the message that switches on or off, or nothing if the state does not ask for it."""
        if STATE_ON not in state or self._command_topic is None:
            return []
        payload = self._payload_on if state[STATE_ON] else self._payload_off
        return [Outgoing(self._command_topic, payload_text(payload), _qos(self.config), bool(self.config.get("retain")))]


class BinarySensorTranslator(Translator):
    """A contact, motion detector, smoke detector or presence sensor: one yes/no value."""

    def __init__(self, entity: Entity) -> None:
        """Map the sensor's device class to a ComfyOS kind and state key. Raises ``Unsupported`` for classes ComfyOS
        has no meaning for, or without a state topic."""
        super().__init__(entity)
        meaning = BINARY_SENSOR_CLASSES.get(entity.device_class or "")
        if meaning is None:
            raise Unsupported(f"Melder der Art „{entity.device_class or 'ohne Angabe'}“")
        self.kind, self._state_key = meaning
        self._state_topic = _topic(self.config, "state_topic")
        if self._state_topic is None:
            raise Unsupported("ohne Topic")
        self._value_path = parse_value_template(self.config.get("value_template"))
        self._payload_on = self.config.get("payload_on", "ON")
        self._payload_off = self.config.get("payload_off", "OFF")

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the one state key this sensor reports (open, motion, alarm ...)."""
        return (self._state_key,)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (the state topic)."""
        return [self._state_topic] if self._state_topic else []

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into true or false for the sensor's state key; unknown payloads change nothing."""
        value = extract_value(text, self._value_path)
        if value is None:
            return Reading()
        if same_payload(value, self._payload_on):
            return Reading(state={self._state_key: True})
        if same_payload(value, self._payload_off):
            return Reading(state={self._state_key: False})
        return Reading()

    def capabilities(self) -> list[Capability]:
        """Return the sensor's read-only yes/no capability."""
        return [Capability(self._state_key, "bool")]


class SensorTranslator(Translator):
    """A measured value: temperature, humidity, battery, power, energy – or any other reading as a detail."""

    def __init__(self, entity: Entity) -> None:
        """Read topic, unit and what the sensor measures. Battery, power and energy sensors only add values to their
        device and do not set its kind. Raises ``Unsupported`` without a state topic."""
        super().__init__(entity)
        self._state_topic = _topic(self.config, "state_topic")
        if self._state_topic is None:
            raise Unsupported("ohne Topic")
        self._value_path = parse_value_template(self.config.get("value_template"))
        self._unit = str(self.config.get("unit_of_measurement") or "")
        self._measures = entity.device_class or ""
        if self._measures not in VALUES_WITHOUT_KIND:
            self.kind = "sensor"                    # battery, power and energy only add values to a device

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the state key for temperature or humidity; other measurements are reported as device values, not as
        state."""
        if self._measures == "temperature":
            return (STATE_TEMPERATURE,)
        if self._measures == "humidity":
            return (STATE_HUMIDITY,)
        if self._measures == "illuminance":
            return (STATE_ILLUMINANCE,)
        return ()

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (the state topic)."""
        return [self._state_topic] if self._state_topic else []

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into a reading: temperature in °C (Fahrenheit is converted), humidity, battery percent,
        power in watts or energy in kWh. Anything that is not a number changes nothing."""
        value = extract_value(text, self._value_path)
        if value is None or value == "":
            return Reading()
        number = to_number(value)
        if self._measures == "temperature" and number is not None:
            celsius = (number - 32) / 1.8 if self._unit.endswith("F") else number
            return Reading(state={STATE_TEMPERATURE: round(celsius, 1)})
        if self._measures == "humidity" and number is not None:
            return Reading(state={STATE_HUMIDITY: round(number, 1)})
        if self._measures == "illuminance" and number is not None and number >= 0:
            return Reading(state={STATE_ILLUMINANCE: round(number, 1)})
        if self._measures == "battery" and number is not None:
            return Reading(battery=int(max(0, min(100, round(number)))))
        if self._measures == "power" and number is not None:
            return Reading(power_w=round(number * 1000 if self._unit == "kW" else number, 1))
        if self._measures == "energy" and number is not None:
            return Reading(energy_kwh=round(number / 1000 if self._unit == "Wh" else number, 3))
        shown = _clean_number(number) if number is not None else payload_text(value)
        detail_text = f"{shown} {self._unit}".strip()[:MAX_DETAIL_LENGTH]
        return Reading(detail=(self.entity.label, detail_text))

    def capabilities(self) -> list[Capability]:
        """Return the read-only capability for temperature or humidity; other measurements have none."""
        if self._measures == "temperature":
            return [Capability(STATE_TEMPERATURE, "number", unit="°C")]
        if self._measures == "humidity":
            return [Capability(STATE_HUMIDITY, "number", unit="%")]
        if self._measures == "illuminance":
            return [Capability(STATE_ILLUMINANCE, "number", unit="lx")]
        return []


# Words at the end of an action that say how a button was pressed (Zigbee2MQTT, Home Assistant events), longest
# first. "1_single" is button "1", pressed short; "on_hold" is button "on", held.
PRESS_WORDS = (("double_press", PRESS_DOUBLE), ("double_click", PRESS_DOUBLE), ("long_press", PRESS_LONG),
               ("long_click", PRESS_LONG), ("short_press", PRESS_SHORT), ("double", PRESS_DOUBLE), ("hold", PRESS_LONG),
               ("long", PRESS_LONG), ("single", PRESS_SHORT), ("click", PRESS_SHORT), ("pressed", PRESS_SHORT),
               ("press", PRESS_SHORT), ("short", PRESS_SHORT))
# Actions that end like this only close a press (or count presses ComfyOS does not tell apart): left out.
RELEASE_WORDS = ("release", "stop", "triple", "quadruple", "many")
# Names of buttons that remotes often use, in the language of the interface
BUTTON_WORDS = {"on": "An", "off": "Aus", "toggle": "Umschalten", "brightness_up": "Heller", "brightness_down": "Dunkler",
                "up": "Hoch", "down": "Runter", "left": "Links", "right": "Rechts", "arrow_left": "Pfeil links",
                "arrow_right": "Pfeil rechts", "open": "Auf", "close": "Zu"}
MAIN_BUTTON = "taste"                       # button id of a remote with one button ("single", "double", "hold")
HOLD_REPEAT_SECONDS = 2.0                   # a held button repeats its action; within this time it counts once
MAX_BUTTON_ID = 40


def split_action(action: Any) -> tuple[str, str] | None:
    """Turn an action into (button id, kind of press): "1_single" -> ("1", short), "on_hold" -> ("on", long),
    "single" -> ("taste", short), "on" -> ("on", short). None for the end of a press ("on_press_release") and for
    anything that is no action."""
    if not isinstance(action, str) or not action.strip() or len(action) > 60:
        return None
    words = _NOT_A_KEY_CHARACTER.sub("_", action.strip().lower()).strip("_")
    if not words or any(words == word or words.endswith("_" + word) for word in RELEASE_WORDS):
        return None
    for word, press in PRESS_WORDS:
        if words == word:
            return MAIN_BUTTON, press
        if words.endswith("_" + word):
            return words[:-len(word) - 1][:MAX_BUTTON_ID] or MAIN_BUTTON, press
    return words[:MAX_BUTTON_ID], PRESS_SHORT


def button_name(button: str) -> str:
    """Name to show for a button id: "1" -> "Taste 1", "brightness_up" -> "Heller", "taste" -> "Taste"."""
    if button == MAIN_BUTTON:
        return "Taste"
    if button in BUTTON_WORDS:
        return BUTTON_WORDS[button]
    number = button[len("button_"):] if button.startswith("button_") else button
    if number.isdigit():
        return f"Taste {number}"
    return button.replace("_", " ").capitalize()


class EventTranslator(Translator):
    """A remote or wall switch: its messages are presses, not states (Home Assistant's "event" platform, and the
    "action" of a Zigbee2MQTT device). Every press goes to ComfyOS once; the device keeps no state of it."""

    kind = "button"

    def __init__(self, entity: Entity) -> None:
        """Read topic and value path. A template the plugin cannot evaluate still works when it names one value of the
        message (``value_json.action``). Without a template the message carries ``event_type`` (Home Assistant).
        Raises ``Unsupported`` without a state topic."""
        super().__init__(entity)
        self._state_topic = _topic(self.config, "state_topic")
        if self._state_topic is None:
            raise Unsupported("ohne Topic")
        template = self.config.get("value_template")
        try:
            self._value_path = parse_value_template(template) if template else ("event_type",)
        except Unsupported:
            named = re.search(r"value_json\.([A-Za-z_][A-Za-z0-9_]*)", str(template))
            if named is None:
                raise
            self._value_path = (named.group(1),)
        self._event_types = _text_options(self.config.get("event_types") or self.config.get("options"))
        self._last_hold: tuple[tuple[str, str], float] | None = None

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (the state topic)."""
        return [self._state_topic] if self._state_topic else []

    def buttons(self) -> list[tuple[str, str]]:
        """Return (button id, name) of every button the announced actions name, in their order."""
        ids = [split[0] for split in (split_action(event) for event in self._event_types) if split]
        return [(button, button_name(button)) for button in dict.fromkeys(ids)]

    def read(self, topic: str, text: str, now: float | None = None) -> Reading:
        """Turn a message into a press. A button held down repeats its action – that counts once."""
        split = split_action(extract_value(text, self._value_path))
        if split is None:
            return Reading()
        if split[1] == PRESS_LONG:
            now = time.monotonic() if now is None else now
            repeated = self._last_hold is not None and self._last_hold[0] == split and now - self._last_hold[1] < HOLD_REPEAT_SECONDS
            self._last_hold = (split, now)
            if repeated:
                return Reading()
        return Reading(press=split)


class ClimateTranslator(Translator):
    """A radiator thermostat: measured temperature and target temperature. Operating modes are not used."""

    kind = "thermostat"

    def __init__(self, entity: Entity) -> None:
        """Read the topics for current and target temperature and the allowed range. Fahrenheit thermostats are not
        supported; a target that needs a template ComfyOS cannot evaluate becomes read-only. Raises ``Unsupported``
        without any temperature topic."""
        super().__init__(entity)
        if str(self.config.get("temperature_unit") or "C").upper() == "F":
            raise Unsupported("Thermostat in Fahrenheit")
        self._current_topic = _topic(self.config, "current_temperature_topic")
        self._current_path = parse_value_template(self.config.get("current_temperature_template"))
        self._target_topic = _topic(self.config, "temperature_state_topic")
        self._target_path = parse_value_template(self.config.get("temperature_state_template"))
        self._command_topic = _topic(self.config, "temperature_command_topic")
        if render_command_template(self.config.get("temperature_command_template"), "0") is None:
            self._command_topic = None              # the target cannot be set without evaluating a template
        if self._current_topic is None and self._target_topic is None:
            raise Unsupported("ohne Topic")
        self._minimum = to_number(self.config.get("min_temp")) or DEFAULT_MIN_TARGET
        self._maximum = to_number(self.config.get("max_temp")) or DEFAULT_MAX_TARGET
        self._step = to_number(self.config.get("temp_step")) or DEFAULT_TARGET_STEP

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the state keys of a thermostat: current temperature and target."""
        return (STATE_TEMPERATURE, STATE_TARGET)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (current and target temperature, as far as configured)."""
        return [topic for topic in (self._current_topic, self._target_topic) if topic]

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into the current temperature or the target, depending on the topic it arrived on."""
        reading = Reading()
        if topic == self._current_topic:
            current = to_number(extract_value(text, self._current_path))
            if current is not None:
                reading.state[STATE_TEMPERATURE] = round(current, 1)
        if topic == self._target_topic:
            target = to_number(extract_value(text, self._target_path))
            if target is not None:
                reading.state[STATE_TARGET] = round(target, 1)
        return reading

    def capabilities(self) -> list[Capability]:
        """Return current temperature (read-only) and target (with range and step; writable if it can be set)."""
        return [
            Capability(STATE_TEMPERATURE, "number", unit="°C"),
            Capability(STATE_TARGET, "number", unit="°C", minimum=self._minimum, maximum=self._maximum, step=self._step,
                       writable=self._command_topic is not None),
        ]

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the message that sets the target temperature. Raises ``PluginError`` if the target is outside the
        thermostat's range."""
        if state.get(STATE_TARGET) is None or self._command_topic is None:
            return []
        target = to_number(state[STATE_TARGET])
        if target is None or not self._minimum <= target <= self._maximum:
            raise PluginError(f"Solltemperatur muss zwischen {_clean_number(self._minimum)} und {_clean_number(self._maximum)} °C liegen.")
        payload = render_command_template(self.config.get("temperature_command_template"), str(_clean_number(target)))
        return [Outgoing(self._command_topic, payload or str(_clean_number(target)), _qos(self.config), bool(self.config.get("retain")))]


class CoverTranslator(Translator):
    """A roller shutter or blind. ComfyOS counts 0 % as open and 100 % as closed."""

    kind = "cover"

    def __init__(self, entity: Entity) -> None:
        """Read position, state and command topics and the device's position scale (Home Assistant: 100 = open by
        default, devices may reverse it). Raises ``Unsupported`` without any topic."""
        super().__init__(entity)
        self._position_topic = _topic(self.config, "position_topic")
        self._position_path = parse_value_template(self.config.get("position_template"))
        self._state_topic = _topic(self.config, "state_topic")
        self._state_path = parse_value_template(self.config.get("value_template"))
        self._command_topic = _topic(self.config, "command_topic")
        self._set_position_topic = _topic(self.config, "set_position_topic")
        if render_command_template(self.config.get("set_position_template"), "0") is None:
            self._set_position_topic = None
        # Home Assistant's scale: by default 100 = open and 0 = closed; devices may turn it around.
        self._device_open = to_number(self.config.get("position_open"))
        self._device_closed = to_number(self.config.get("position_closed"))
        if self._device_open is None:
            self._device_open = 100.0
        if self._device_closed is None:
            self._device_closed = 0.0
        if self._device_open == self._device_closed:
            raise Unsupported("Rollladen ohne Positionsbereich")
        if not (self._position_topic or self._state_topic or self._command_topic):
            raise Unsupported("ohne Topic")

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the state key of a cover: its position."""
        return (STATE_POSITION,)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (position and state, as far as configured)."""
        return [topic for topic in (self._position_topic, self._state_topic) if topic]

    def _to_comfyos(self, device_position: float) -> int:
        """Convert a position on the device's scale to ComfyOS percent, where 0 is open and 100 is closed."""
        share_closed = (device_position - self._device_open) / (self._device_closed - self._device_open)
        return int(round(max(0.0, min(1.0, share_closed)) * 100))

    def _to_device(self, comfyos_position: float) -> float:
        """Convert a ComfyOS position (0 = open, 100 = closed) to the device's scale."""
        return self._device_open + (self._device_closed - self._device_open) * comfyos_position / 100

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into a position: from the position topic, or from open/closed on the state topic if the
        cover reports no position."""
        if topic == self._position_topic:
            device_position = to_number(extract_value(text, self._position_path))
            if device_position is not None:
                return Reading(state={STATE_POSITION: self._to_comfyos(device_position)})
        if topic == self._state_topic and self._position_topic is None:
            value = extract_value(text, self._state_path)
            if value is not None and same_payload(value, self.config.get("state_open", "open")):
                return Reading(state={STATE_POSITION: 0})
            if value is not None and same_payload(value, self.config.get("state_closed", "closed")):
                return Reading(state={STATE_POSITION: 100})
        return Reading()

    def capabilities(self) -> list[Capability]:
        """Return the position capability in percent; writable if the cover accepts commands or positions."""
        is_writable = self._command_topic is not None or self._set_position_topic is not None
        return [Capability(STATE_POSITION, "number", unit="%", minimum=0, maximum=100, writable=is_writable)]

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the message for up, down, stop or a target position. Raises ``PluginError`` for a command the cover
        does not know or a position outside 0-100."""
        qos, retain = _qos(self.config), bool(self.config.get("retain"))
        direction = state.get("cover")              # "up", "down" or "stop" from the tile's buttons
        if direction is not None:
            payload_key, default_payload = {"up": ("payload_open", "OPEN"), "down": ("payload_close", "CLOSE"),
                                            "stop": ("payload_stop", "STOP")}.get(str(direction), ("", ""))
            payload = self.config.get(payload_key, default_payload) if payload_key else None
            if self._command_topic is None or payload is None:
                raise PluginError("Dieser Rollladen kennt den Befehl nicht.")
            return [Outgoing(self._command_topic, payload_text(payload), qos, retain)]
        if state.get(STATE_POSITION) is None:
            return []
        position = to_number(state[STATE_POSITION])
        if position is None or not 0 <= position <= 100:
            raise PluginError("Position muss zwischen 0 und 100 liegen.")
        if self._set_position_topic is not None:
            device_position = str(_clean_number(self._to_device(position)))
            payload = render_command_template(self.config.get("set_position_template"), device_position)
            return [Outgoing(self._set_position_topic, payload or device_position, qos, retain)]
        if self._command_topic is not None and position in (0, 100):
            payload = self.config.get("payload_open", "OPEN") if position == 0 else self.config.get("payload_close", "CLOSE")
            return [Outgoing(self._command_topic, payload_text(payload), qos, retain)]
        raise PluginError("Dieser Rollladen lässt sich nur ganz öffnen oder schließen.")


class LampTranslator(Translator):
    """Base class of everything that is a lamp: says what the lamp can show beyond on and off.

    The registry builds the ``LightState`` of a device from these three attributes.
    """

    kind = "light"
    is_dimmable: bool = False
    has_color: bool = False
    white_range: tuple[int, int] | None = None      # (coolest, warmest) white tone in mirek


class LightTranslator(LampTranslator):
    """A lamp. Two schemas exist: "json" (one JSON message for everything, used by Zigbee2MQTT and ESPHome)
    and "basic" (one topic per property; this plugin uses on/off and brightness of it)."""

    def __init__(self, entity: Entity) -> None:
        """Read the light's schema (basic or JSON), topics, brightness scale and colour modes. Raises ``Unsupported``
        for the template schema or without any topic."""
        super().__init__(entity)
        self._uses_json = self.config.get("schema") == "json"
        self._state_topic = _topic(self.config, "state_topic")
        self._command_topic = _topic(self.config, "command_topic")
        if self._command_topic is None and self._state_topic is None:
            raise Unsupported("ohne Topic")
        if self.config.get("schema") not in (None, "basic", "json"):
            raise Unsupported("Lampe mit Vorlagen-Schema")
        self._brightness_scale = to_number(self.config.get("brightness_scale")) or DEFAULT_BRIGHTNESS_SCALE
        color_modes = self.config.get("supported_color_modes")
        self._color_modes = [str(mode) for mode in color_modes] if isinstance(color_modes, list) else []

        if self._uses_json:
            self._state_path: ValuePath = ()
            self._brightness_topic = self._state_topic
            self._brightness_command_topic = self._command_topic
            self.is_dimmable = bool(self.config.get("brightness")) or any(mode != "onoff" for mode in self._color_modes)
        else:
            self._state_path = parse_value_template(self.config.get("state_value_template"))
            self._brightness_topic = _topic(self.config, "brightness_state_topic")
            self._brightness_path = parse_value_template(self.config.get("brightness_value_template"))
            self._brightness_command_topic = _topic(self.config, "brightness_command_topic")
            self.is_dimmable = self._brightness_command_topic is not None
            self._color_modes = []                  # colour topics of the basic schema are not used

        has_white_tones = "color_temp" in self._color_modes or bool(self.config.get("color_temp"))
        coolest = int(to_number(self.config.get("min_mireds")) or DEFAULT_MIN_MIREK)
        warmest = int(to_number(self.config.get("max_mireds")) or DEFAULT_MAX_MIREK)
        self.white_range: tuple[int, int] | None = (coolest, warmest) if has_white_tones and self._uses_json else None
        self.has_color = self._uses_json and any(mode in COLOR_MODES_XY + COLOR_MODES_HS + COLOR_MODES_RGB
                                                 for mode in self._color_modes)

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the state keys of a light: on/off, plus brightness if it can be dimmed."""
        return (STATE_ON, STATE_BRIGHTNESS) if self.is_dimmable else (STATE_ON,)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (state and brightness, without duplicates)."""
        return list(dict.fromkeys(topic for topic in (self._state_topic, self._brightness_topic) if topic))

    # -- reading ------------------------------------------------------------

    def _percent(self, device_brightness: float) -> int:
        """Convert the device's brightness to percent; anything above zero is at least 1 %."""
        percent = round(device_brightness / self._brightness_scale * 100)
        return int(max(1 if device_brightness > 0 else 0, min(100, percent)))

    def _read_on(self, value: Any) -> bool | None:
        """Compare a payload with the configured on and off payloads; ``None`` if it is neither."""
        if same_payload(value, self.config.get("payload_on", "ON")):
            return True
        if same_payload(value, self.config.get("payload_off", "OFF")):
            return False
        return None

    def _read_json(self, text: str) -> Reading:
        """Turn a JSON state message into on/off, brightness and colour; anything that is not a JSON object changes
        nothing."""
        try:
            message = json.loads(text)
        except ValueError:
            return Reading()
        if not isinstance(message, Mapping):
            return Reading()
        reading = Reading()
        is_on = self._read_on(message.get("state")) if "state" in message else None
        if is_on is not None:
            reading.state[STATE_ON] = is_on
        brightness = to_number(message.get("brightness"))
        if brightness is not None and self.is_dimmable:
            reading.state[STATE_BRIGHTNESS] = self._percent(brightness)
        mirek = to_number(message.get("color_temp"))
        if mirek is not None and mirek > 0:
            reading.mirek = int(round(mirek))
        reading.xy = _read_color(message.get("color"))
        color_mode = message.get("color_mode")
        if color_mode == "color_temp":
            reading.color_mode = "ct"
        elif isinstance(color_mode, str) and color_mode in COLOR_MODES_XY + COLOR_MODES_HS + COLOR_MODES_RGB:
            reading.color_mode = "xy"
        return reading

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into on/off or brightness - from JSON for the JSON schema, otherwise by the topic it arrived
        on."""
        if self._uses_json:
            return self._read_json(text)
        reading = Reading()
        if topic == self._state_topic:
            value = extract_value(text, self._state_path)
            is_on = self._read_on(value) if value is not None else None
            if is_on is not None:
                reading.state[STATE_ON] = is_on
        if topic == self._brightness_topic:
            brightness = to_number(extract_value(text, self._brightness_path))
            if brightness is not None:
                reading.state[STATE_BRIGHTNESS] = self._percent(brightness)
        return reading

    def capabilities(self) -> list[Capability]:
        """Return on/off and, for dimmable lights, brightness in percent; writable if the light has a command topic."""
        is_writable = self._command_topic is not None
        capabilities = [Capability(STATE_ON, "bool", writable=is_writable)]
        if self.is_dimmable:
            capabilities.append(Capability(STATE_BRIGHTNESS, "number", unit="%", minimum=1, maximum=100, writable=is_writable))
        return capabilities

    # -- commands -----------------------------------------------------------

    def _color_object(self, xy: tuple[float, float]) -> dict[str, Any]:
        """Write a colour the way the lamp's colour mode expects it."""
        if any(mode in COLOR_MODES_XY for mode in self._color_modes):
            return {"x": xy[0], "y": xy[1]}
        red, green, blue = hex_to_rgb(xy_to_hex(*xy))
        if any(mode in COLOR_MODES_HS for mode in self._color_modes):
            hue, saturation, _ = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
            return {"h": round(hue * 360, 1), "s": round(saturation * 100, 1)}
        return {"r": red, "g": green, "b": blue}

    def _json_command(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Build the JSON command for the wanted state: on/off, brightness on the device's scale and colour or white
        tone, as far as the light supports them. Brightness 0 means off."""
        body: dict[str, Any] = {}
        brightness = to_number(state.get(STATE_BRIGHTNESS))
        if brightness is not None and brightness <= 0:
            return {"state": payload_text(self.config.get("payload_off", "OFF"))}      # brightness 0 means "off"
        if STATE_ON in state:
            payload = self.config.get("payload_on", "ON") if state[STATE_ON] else self.config.get("payload_off", "OFF")
            body["state"] = payload_text(payload)
        if brightness is not None and self.is_dimmable:
            body["brightness"] = int(max(1, round(min(100.0, brightness) / 100 * self._brightness_scale)))
        wanted_xy, wanted_mirek = wanted_light(state)
        if wanted_xy is not None and self.has_color:
            body["color"] = self._color_object(wanted_xy)
        elif wanted_xy is not None and self.white_range is not None:
            body["color_temp"] = xy_to_mirek(wanted_xy[0], wanted_xy[1], self.white_range)    # a white lamp shows the nearest white
        elif wanted_mirek is not None and self.white_range is not None:
            body["color_temp"] = int(max(self.white_range[0], min(self.white_range[1], wanted_mirek)))
        duration_ms = to_number(state.get("duration"))
        if duration_ms is not None and duration_ms > 0 and body:
            body["transition"] = round(duration_ms / 1000, 1)
        if body and "state" not in body:
            body["state"] = payload_text(self.config.get("payload_on", "ON"))           # setting light switches the lamp on
        return body

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the messages that bring the light to the wanted state: one JSON message, or separate messages for
        brightness and on/off with the basic schema."""
        if self._command_topic is None:
            return []
        qos, retain = _qos(self.config), bool(self.config.get("retain"))
        if self._uses_json:
            body = self._json_command(state)
            return [Outgoing(self._command_topic, json.dumps(body), qos, retain)] if body else []
        messages = []
        brightness = to_number(state.get(STATE_BRIGHTNESS))
        switch_on = state.get(STATE_ON)
        if brightness is not None and brightness <= 0:
            switch_on, brightness = False, None
        if brightness is not None and self._brightness_command_topic is not None:
            device_brightness = int(max(1, round(min(100.0, brightness) / 100 * self._brightness_scale)))
            messages.append(Outgoing(self._brightness_command_topic, str(device_brightness), qos, retain))
            switch_on = True if switch_on is None else switch_on
        if switch_on is not None:
            payload = self.config.get("payload_on", "ON") if switch_on else self.config.get("payload_off", "OFF")
            messages.append(Outgoing(self._command_topic, payload_text(payload), qos, retain))
        return messages


def _read_color(color: Any) -> tuple[float, float] | None:
    """Read the "color" object of a JSON light message (x/y, r/g/b, h/s or hue/saturation) as CIE xy."""
    if not isinstance(color, Mapping):
        return None
    x, y = to_number(color.get("x")), to_number(color.get("y"))
    if x is not None and y is not None:
        return round(x, 4), round(y, 4)
    red, green, blue = (to_number(color.get(channel)) for channel in ("r", "g", "b"))
    if red is not None and green is not None and blue is not None:
        return hex_to_xy(rgb_to_hex(red, green, blue))
    hue, saturation = to_number(color.get("h", color.get("hue"))), to_number(color.get("s", color.get("saturation")))
    if hue is not None and saturation is not None:
        red, green, blue = colorsys.hsv_to_rgb(hue / 360, saturation / 100, 1.0)
        return hex_to_xy(rgb_to_hex(red * 255, green * 255, blue * 255))
    return None


def wanted_light(state: Mapping[str, Any]) -> tuple[tuple[float, float] | None, int | None]:
    """Read the colour or white tone a ComfyOS command asks for: ``(xy, mirek)``, at most one of them set."""
    xy = state.get("xy")
    if isinstance(xy, (list, tuple)) and len(xy) == 2 and to_number(xy[0]) is not None and to_number(xy[1]) is not None:
        return (float(xy[0]), float(xy[1])), None
    if isinstance(state.get("color"), str):
        try:
            return hex_to_xy(state["color"]), None
        except ValueError:
            raise PluginError("Farbe muss als #rrggbb angegeben sein.")
    mirek = to_number(state.get("mirek"))
    if mirek is not None and mirek > 0:
        return None, int(round(mirek))
    kelvin = to_number(state.get("kelvin"))
    if kelvin is not None and kelvin > 0:
        return None, kelvin_to_mirek(kelvin)
    return None, None


class FanTranslator(Translator):
    """A fan: on and off like a switch, optionally a speed in per cent and preset modes.

    ComfyOS shows a fan as a switch. Speed and mode are labelled capabilities of the device.
    """

    kind = "switch"

    def __init__(self, entity: Entity) -> None:
        """Read the topics for on/off, speed and preset mode. Raises ``Unsupported`` without any topic."""
        super().__init__(entity)
        self._power = value_channel(self.config, "state_topic", "state_value_template", "command_topic")
        if self._power.state_topic is None and self._power.command_topic is None:
            raise Unsupported("ohne Topic")
        self._payload_on = self.config.get("payload_on", "ON")
        self._payload_off = self.config.get("payload_off", "OFF")

        self._speed = value_channel(self.config, "percentage_state_topic", "percentage_value_template",
                                    "percentage_command_topic", "percentage_command_template")
        self._slowest = int(to_number(self.config.get("speed_range_min")) or 1)
        self._fastest = int(to_number(self.config.get("speed_range_max")) or 100)
        if self._fastest < self._slowest:
            raise Unsupported("Lüfter ohne Stufenbereich")
        self._has_speed = self._speed.state_topic is not None or self._speed.command_topic is not None
        self._speed_key = value_key(entity, "speed")

        self._mode = value_channel(self.config, "preset_mode_state_topic", "preset_mode_value_template",
                                   "preset_mode_command_topic", "preset_mode_command_template")
        self._modes = _text_options(self.config.get("preset_modes"))
        self._mode_key = value_key(entity, "mode")

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return on/off plus the keys for speed and mode, as far as the fan has them."""
        keys = [STATE_ON]
        if self._has_speed:
            keys.append(self._speed_key)
        if self._modes:
            keys.append(self._mode_key)
        return tuple(keys)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (on/off, speed and mode, without duplicates)."""
        topics = (self._power.state_topic, self._speed.state_topic, self._mode.state_topic)
        return list(dict.fromkeys(topic for topic in topics if topic))

    def _percent(self, device_speed: float) -> int:
        """Convert a speed on the fan's scale to per cent; 0 stays 0 (the fan is off)."""
        if device_speed <= 0:
            return 0
        steps = self._fastest - self._slowest + 1
        return int(max(1, min(100, round((device_speed - self._slowest + 1) * 100 / steps))))

    def _device_speed(self, percent: float) -> int:
        """Convert per cent to the fan's scale, rounding up so that 1 % already is the slowest speed."""
        steps = self._fastest - self._slowest + 1
        return int(math.ceil(steps * min(100.0, percent) / 100)) + self._slowest - 1

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into on/off, speed or mode, depending on the topic it arrived on."""
        reading = Reading()
        power = self._power.read(topic, text)
        if power is not None and same_payload(power, self._payload_on):
            reading.state[STATE_ON] = True
        elif power is not None and same_payload(power, self._payload_off):
            reading.state[STATE_ON] = False
        speed = to_number(self._speed.read(topic, text))
        if speed is not None and self._has_speed:
            reading.state[self._speed_key] = self._percent(speed)
        mode = self._mode.read(topic, text)
        if mode is not None and payload_text(mode) in self._modes:
            reading.state[self._mode_key] = payload_text(mode)
        return reading

    def capabilities(self) -> list[Capability]:
        """Return on/off and the labelled capabilities for speed and mode."""
        capabilities = [Capability(STATE_ON, "bool", writable=self._power.command_topic is not None)]
        if self._has_speed:
            capabilities.append(Capability(self._speed_key, "number", unit="%", minimum=0, maximum=100, step=1,
                                           writable=self._speed.command_topic is not None, label=LABEL_FAN_SPEED))
        if self._modes:
            capabilities.append(Capability(self._mode_key, "enum", options=self._modes,
                                           writable=self._mode.command_topic is not None, label=LABEL_FAN_MODE))
        return capabilities

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the messages for on/off, a speed or a mode. Raises ``PluginError`` for a speed outside 0-100 or a
        mode the fan does not have."""
        messages = []
        if STATE_ON in state and self._power.command_topic is not None:
            messages.append(self._power.outgoing(payload_text(self._payload_on if state[STATE_ON] else self._payload_off)))
        if state.get(self._speed_key) is not None and self._speed.command_topic is not None:
            percent = to_number(state[self._speed_key])
            if percent is None or not 0 <= percent <= 100:
                raise PluginError("Die Stufe muss zwischen 0 und 100 liegen.")
            messages.append(self._speed.outgoing(str(self._device_speed(percent) if percent > 0 else 0)))
        if state.get(self._mode_key) is not None and self._mode.command_topic is not None:
            mode = payload_text(state[self._mode_key])
            if mode not in self._modes:
                raise PluginError("Diesen Modus kennt der Lüfter nicht.")
            messages.append(self._mode.outgoing(mode))
        return messages


class LockTranslator(Translator):
    """A door lock: locked or unlocked. Locks that ask for a code are not supported.

    ComfyOS has no device kind for locks. The lock is a labelled yes/no capability of its device,
    so no scene, no "all lights off" and no voice assistant ever opens a door by accident.
    """

    kind = "unknown"

    def __init__(self, entity: Entity) -> None:
        """Read topics and payloads of a lock. Raises ``Unsupported`` for locks with a code or without a topic."""
        super().__init__(entity)
        if self.config.get("code_format"):
            raise Unsupported("Schloss mit Code")
        self._channel = value_channel(self.config, "state_topic", "value_template", "command_topic")
        if self._channel.state_topic is None and self._channel.command_topic is None:
            raise Unsupported("ohne Topic")
        self._payload_lock = self.config.get("payload_lock", "LOCK")
        self._payload_unlock = self.config.get("payload_unlock", "UNLOCK")
        self._state_locked = self.config.get("state_locked", "LOCKED")
        self._state_unlocked = self.config.get("state_unlocked", "UNLOCKED")
        self._key = value_key(entity, "locked")

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the key under which the lock reports "locked"."""
        return (self._key,)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (the state topic, if there is one)."""
        return [self._channel.state_topic] if self._channel.state_topic else []

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into locked or unlocked; states in between (locking, jammed) change nothing."""
        value = self._channel.read(topic, text)
        if value is not None and same_payload(value, self._state_locked):
            return Reading(state={self._key: True})
        if value is not None and same_payload(value, self._state_unlocked):
            return Reading(state={self._key: False})
        return Reading()

    def capabilities(self) -> list[Capability]:
        """Return the labelled yes/no capability "locked"."""
        return [Capability(self._key, "bool", writable=self._channel.command_topic is not None, label=LABEL_LOCKED)]

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the message that locks or unlocks, or nothing if the command is not for the lock."""
        if state.get(self._key) is None or self._channel.command_topic is None:
            return []
        return [self._channel.outgoing(payload_text(self._payload_lock if state[self._key] else self._payload_unlock))]


class SelectTranslator(Translator):
    """A choice between fixed options, e.g. the operating mode of a device. A labelled capability of its device."""

    kind = "unknown"

    def __init__(self, entity: Entity) -> None:
        """Read topics and options of a select. Raises ``Unsupported`` without options or without a topic."""
        super().__init__(entity)
        self._channel = value_channel(self.config, "state_topic", "value_template", "command_topic")
        self._options = _text_options(self.config.get("options"))
        if not self._options:
            raise Unsupported("Auswahl ohne Werte")
        if self._channel.state_topic is None and self._channel.command_topic is None:
            raise Unsupported("ohne Topic")
        self._key = value_key(entity, entity.label)

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the key under which the chosen option is reported."""
        return (self._key,)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (the state topic, if there is one)."""
        return [self._channel.state_topic] if self._channel.state_topic else []

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into the chosen option; anything that is not one of the options changes nothing."""
        value = self._channel.read(topic, text)
        if value is not None and payload_text(value) in self._options:
            return Reading(state={self._key: payload_text(value)})
        return Reading()

    def capabilities(self) -> list[Capability]:
        """Return the labelled choice with its options."""
        return [Capability(self._key, "enum", options=self._options, writable=self._channel.command_topic is not None,
                           label=self.entity.label)]

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the message that picks an option. Raises ``PluginError`` for a value that is not an option."""
        if state.get(self._key) is None or self._channel.command_topic is None:
            return []
        option = payload_text(state[self._key])
        if option not in self._options:
            raise PluginError(f"„{self.entity.label}“ kennt diesen Wert nicht.")
        return [self._channel.outgoing(option)]


class NumberTranslator(Translator):
    """A number the user can set within a range, e.g. a delay or a threshold. A labelled capability of its device."""

    kind = "unknown"

    def __init__(self, entity: Entity) -> None:
        """Read topics, range and unit of a number. Raises ``Unsupported`` without a topic or with an empty range."""
        super().__init__(entity)
        self._channel = value_channel(self.config, "state_topic", "value_template", "command_topic")
        if self._channel.state_topic is None and self._channel.command_topic is None:
            raise Unsupported("ohne Topic")
        minimum, maximum, step = (to_number(self.config.get(key)) for key in ("min", "max", "step"))
        self._minimum = 1.0 if minimum is None else minimum             # Home Assistant's defaults: 1 … 100, step 1
        self._maximum = 100.0 if maximum is None else maximum
        self._step = step if step is not None and step > 0 else 1.0
        if self._maximum <= self._minimum:
            raise Unsupported("Zahl ohne Wertebereich")
        self._unit = str(self.config.get("unit_of_measurement") or "")[:12] or None
        self._key = value_key(entity, entity.label)

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Return the key under which the number is reported."""
        return (self._key,)

    @property
    def topics(self) -> list[str]:
        """Return the topics to subscribe to (the state topic, if there is one)."""
        return [self._channel.state_topic] if self._channel.state_topic else []

    def read(self, topic: str, text: str) -> Reading:
        """Turn a message into the current number; anything that is not a number changes nothing."""
        number = to_number(self._channel.read(topic, text))
        return Reading(state={self._key: _clean_number(number)}) if number is not None else Reading()

    def capabilities(self) -> list[Capability]:
        """Return the labelled number with its range, step and unit."""
        return [Capability(self._key, "number", unit=self._unit, minimum=self._minimum, maximum=self._maximum, step=self._step,
                           writable=self._channel.command_topic is not None, label=self.entity.label)]

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the message that sets the number. Raises ``PluginError`` for a value outside the range."""
        if state.get(self._key) is None or self._channel.command_topic is None:
            return []
        number = to_number(state[self._key])
        if number is None or not self._minimum <= number <= self._maximum:
            raise PluginError(f"„{self.entity.label}“ muss zwischen {_clean_number(self._minimum)} und "
                              f"{_clean_number(self._maximum)} liegen.")
        return [self._channel.outgoing(str(_clean_number(number)))]


class ButtonTranslator(Translator):
    """A button: something the device does when asked, without a state. A labelled action of its device."""

    kind = "unknown"

    def __init__(self, entity: Entity) -> None:
        """Read the command topic and the payload that presses the button. Raises ``Unsupported`` without a topic."""
        super().__init__(entity)
        self._channel = value_channel(self.config, "state_topic", "value_template", "command_topic")
        if self._channel.command_topic is None:
            raise Unsupported("ohne Topic")
        self._payload_press = self.config.get("payload_press", "PRESS")
        self._key = value_key(entity, entity.label)

    def capabilities(self) -> list[Capability]:
        """Return the labelled action. It has no state, so it is never reported in ``Device.state``."""
        return [Capability(self._key, "action", readable=False, writable=True, label=self.entity.label)]

    def commands(self, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the message that presses the button, or nothing if the command is not for it."""
        if not state.get(self._key):
            return []
        return [self._channel.outgoing(payload_text(self._payload_press))]


TRANSLATORS: dict[str, type[Translator]] = {
    "light": LightTranslator,
    "switch": SwitchTranslator,
    "binary_sensor": BinarySensorTranslator,
    "sensor": SensorTranslator,
    "climate": ClimateTranslator,
    "cover": CoverTranslator,
    "fan": FanTranslator,
    "lock": LockTranslator,
    "select": SelectTranslator,
    "number": NumberTranslator,
    "button": ButtonTranslator,
    "event": EventTranslator,
}


def translator_for(entity: Entity, translators: Mapping[str, type[Translator]] = TRANSLATORS) -> Translator:
    """Build the translator for an entity. Raises Unsupported when the platform or its configuration is not handled.

    ``translators`` maps platform names to translator classes; the registry adds the platforms
    of Tasmota's own discovery to the ones of Home Assistant discovery.
    """
    translator_class = translators.get(entity.platform)
    if translator_class is None:
        raise Unsupported(f"Art „{entity.platform}“")
    return translator_class(entity)
