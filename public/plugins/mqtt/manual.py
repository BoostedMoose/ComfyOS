"""Devices the user describes by hand, for everything that does not announce itself. No network access.

Many devices publish their state on a fixed topic without Home Assistant discovery
(Shelly, Tasmota, own scripts). The user then tells ComfyOS the topics. Such a
:class:`ManualDevice` is turned into the same :class:`discovery.Entity` a discovery
message would produce, so the rest of the plugin treats both alike.

A manual device is deliberately simple: one state topic, optionally one command topic,
two payloads for "on" and "off", and optionally the key of a JSON message that holds the value.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from plugin_api import PluginError

from . import protocol
from .discovery import DeviceInfo, Entity

MAX_MANUAL_DEVICES = 40
MAX_NAME_LENGTH = 60
MAX_TOPIC_LENGTH = 200
MAX_PAYLOAD_LENGTH = 60
MANUAL_MODEL_TEXT = "Eigenes MQTT-Gerät"
IDENTIFIER_PREFIX = "manual_"               # device id: "mqtt:manual_<key>"

# What a manual device can be: kind -> (label for the user, platform, device class)
MANUAL_KINDS = {
    "switch": ("Steckdose / Schalter", "switch", None),
    "light": ("Lampe (an/aus)", "light", None),
    "temperature": ("Temperatur", "sensor", "temperature"),
    "humidity": ("Luftfeuchte", "sensor", "humidity"),
    "contact": ("Fenster / Tür", "binary_sensor", "door"),
    "motion": ("Bewegung", "binary_sensor", "motion"),
}
SWITCHABLE_MANUAL_KINDS = ("switch", "light")

_KEY_PATTERN = re.compile(r"^[a-z0-9]{4,16}$")
# A JSON key such as "temperature" or "sensor.temperature": letters, digits and "_", up to four levels.
_JSON_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}(\.[A-Za-z_][A-Za-z0-9_]{0,40}){0,3}$")


@dataclass(frozen=True)
class ManualDevice:
    """One device the user created by hand."""

    key: str                                # short random id; part of the device id
    name: str
    kind: str                               # one of MANUAL_KINDS
    state_topic: str = ""
    command_topic: str = ""                 # only for switchable kinds
    payload_on: str = "ON"                  # also "open" for a contact and "motion" for a motion detector
    payload_off: str = "OFF"
    json_key: str = ""                      # the value is this key of a JSON message; empty = the whole payload


def _checked_topic(topic: Any, what: str) -> str:
    """Return the topic trimmed, or empty if none was given. Raises ``PluginError`` for a topic that is too long or
    contains wildcards."""
    text = str(topic or "").strip()
    if not text:
        return ""
    if len(text) > MAX_TOPIC_LENGTH or not protocol.is_publish_topic(text):
        raise PluginError(f"{what}: kein gültiges Topic (ohne + und #).")
    return text


def checked(key: str, values: Mapping[str, Any]) -> ManualDevice:
    """Build a ManualDevice from user input. Raises PluginError with a message for the user."""
    name = " ".join(str(values.get("name") or "").split())[:MAX_NAME_LENGTH]
    kind = str(values.get("kind") or "")
    if not _KEY_PATTERN.match(key):
        raise PluginError("Ungültige Kennung.")
    if not name:
        raise PluginError("Bitte einen Namen angeben.")
    if kind not in MANUAL_KINDS:
        raise PluginError("Unbekannte Geräteart.")
    state_topic = _checked_topic(values.get("state_topic"), "Zustands-Topic")
    command_topic = _checked_topic(values.get("command_topic"), "Befehls-Topic") if kind in SWITCHABLE_MANUAL_KINDS else ""
    if not state_topic and not command_topic:
        raise PluginError("Bitte ein Zustands-Topic angeben." if kind not in SWITCHABLE_MANUAL_KINDS
                          else "Bitte ein Zustands- oder Befehls-Topic angeben.")
    json_key = str(values.get("json_key") or "").strip()
    if json_key and not _JSON_KEY_PATTERN.match(json_key):
        raise PluginError("JSON-Schlüssel: nur Buchstaben, Ziffern, _ und Punkte, z. B. „temperature“.")
    payload_on = str(values.get("payload_on") or "ON").strip()[:MAX_PAYLOAD_LENGTH]
    payload_off = str(values.get("payload_off") or "OFF").strip()[:MAX_PAYLOAD_LENGTH]
    return ManualDevice(key=key, name=name, kind=kind, state_topic=state_topic, command_topic=command_topic,
                        payload_on=payload_on, payload_off=payload_off, json_key=json_key)


def to_entity(device: ManualDevice) -> Entity:
    """Describe a manual device the way a discovery message would."""
    _, platform, device_class = MANUAL_KINDS[device.kind]
    identifier = IDENTIFIER_PREFIX + device.key
    value_template = "{{ value_json." + device.json_key + " }}" if device.json_key else None
    config: dict[str, Any] = {"payload_on": device.payload_on, "payload_off": device.payload_off}
    if device.state_topic:
        config["state_topic"] = device.state_topic
    if device.command_topic:
        config["command_topic"] = device.command_topic
    if device_class:
        config["device_class"] = device_class
    if device.kind == "temperature":
        config["unit_of_measurement"] = "°C"
    if value_template:
        # the basic light schema names its template differently from every other platform
        config["state_value_template" if platform == "light" else "value_template"] = value_template
    return Entity(unique_id=identifier, platform=platform, name="",
                  device=DeviceInfo(identifier=identifier, name=device.name, model=MANUAL_MODEL_TEXT), config=config)


def load(stored: Any) -> list[ManualDevice]:
    """Read manual devices from the plugin's settings; entries that are no longer valid are skipped."""
    devices: list[ManualDevice] = []
    for entry in stored if isinstance(stored, list) else []:
        if not isinstance(entry, Mapping) or len(devices) >= MAX_MANUAL_DEVICES:
            continue
        try:
            devices.append(checked(str(entry.get("key") or ""), entry))
        except PluginError:
            continue
    return devices


def dump(devices: list[ManualDevice]) -> list[dict[str, Any]]:
    """Turn manual devices into plain data for the plugin's settings."""
    return [asdict(device) for device in devices]
