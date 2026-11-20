"""Zigbee2MQTT's own device list: read ``<base topic>/bridge/devices`` and describe what it lists. No network access.

Zigbee2MQTT can announce its devices through Home Assistant discovery (see ``discovery.py``),
but only if the user switched that on. It always publishes one retained JSON message with
every device it knows::

    zigbee2mqtt/bridge/devices      [{"ieee_address": "0x00158d0001a2b3c4", "friendly_name": "Stehlampe",
                                      "definition": {"vendor": "…", "model": "…", "exposes": […]}}, …]

``exposes`` says what a device can do: a light with brightness and colour, a switch, a contact,
a temperature. This module turns each *expose* into the same :class:`discovery.Entity` a
Home Assistant discovery message would produce, so the translators in ``translate.py`` handle
both alike:

* the state of a device arrives as one JSON message on ``<base topic>/<friendly name>``
* a value is set by publishing it to ``<base topic>/<friendly name>/set/<property>``
  (a light gets one JSON message on ``…/set``)
* the current state is asked for with ``<base topic>/<friendly name>/get``

The result is a *fallback* announcement: a device that also shows up in Home Assistant
discovery is taken from there, so nothing appears twice. Both ways give a device the same
identifier (``zigbee2mqtt_<address>``) – a device keeps its name and room in ComfyOS when the
user switches the Home Assistant integration of Zigbee2MQTT on or off.

Reference: the "Exposes" page of the Zigbee2MQTT documentation (guide, usage).
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .discovery import Announcement, DeviceInfo, Entity, StateRequest

DEFAULT_BASE_TOPIC = "zigbee2mqtt"
MAX_DEVICES = 500
MAX_EXPOSES_PER_DEVICE = 80
MAX_TEXT_LENGTH = 80

# Bits of the "access" number of an expose
ACCESS_PUBLISHED = 1                        # the value is part of the device's state message
ACCESS_SET = 2                              # the value can be set with …/set
ACCESS_GET = 4                              # the value can be asked for with …/get

DEFAULT_LIGHT_BRIGHTNESS_SCALE = 254

# Yes/no values and the Home Assistant device class that says the same. ``value_on`` of an expose
# already is the value that means "on" in Home Assistant's sense: for "contact" that is ``false``,
# because a contact that is not closed is open.
BINARY_DEVICE_CLASSES = {
    "contact": "door",
    "occupancy": "occupancy",
    "presence": "presence",
    "smoke": "smoke",
    "gas": "gas",
    "carbon_monoxide": "carbon_monoxide",
}

# Numbers with a meaning for ComfyOS: property -> device class
NUMERIC_DEVICE_CLASSES = {
    "temperature": "temperature",
    "humidity": "humidity",
    "battery": "battery",
    "power": "power",
    "energy": "energy",
    "illuminance_lux": "illuminance",
}
# Read-only values that are events, not states ("the button was pressed"): they become presses of the device's buttons
# (platform "event", since plugin 1.2.0).
EVENT_PROPERTIES = ("action", "click")

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{16}$")
_PROPERTY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$")


def devices_topic(base_topic: str) -> str:
    """Return the topic that carries the device list of the bridge."""
    return f"{base_topic}/bridge/devices"


def _clip(value: Any) -> str:
    """Return a text as a trimmed, length-limited string; anything else becomes empty."""
    return value.strip()[:MAX_TEXT_LENGTH] if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    """Return a number from the device list; None for anything else."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _property(expose: Mapping[str, Any]) -> str | None:
    """Return the key under which an expose appears in the state message, if it is a plain name."""
    name = expose.get("property")
    return name if isinstance(name, str) and _PROPERTY.match(name) else None


def _access(expose: Mapping[str, Any]) -> int:
    """Return the access bits of an expose (see the ACCESS_ constants); 0 when missing."""
    access = expose.get("access")
    return access if isinstance(access, int) and not isinstance(access, bool) else 0


def _features(expose: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return the parts of a composite expose (a light's state, brightness, colour …) by their name."""
    features = expose.get("features")
    if not isinstance(features, list):
        return {}
    return {feature["name"]: feature for feature in features
            if isinstance(feature, Mapping) and isinstance(feature.get("name"), str) and _property(feature)}


def _value_template(property_name: str) -> str:
    """Return the template that picks a property out of the state message."""
    return "{{ value_json." + property_name + " }}"


class _DeviceTopics:
    """The topics of one Zigbee2MQTT device and the options every entity of it shares."""

    def __init__(self, base_topic: str, friendly_name: str) -> None:
        """Remember the device's state topic and how its availability is reported."""
        self.state = f"{base_topic}/{friendly_name}"
        self.get = f"{self.state}/get"
        # A device is reachable while the bridge is online and – if Zigbee2MQTT checks that – the device itself.
        self.shared_options = {
            "availability": [{"topic": f"{base_topic}/bridge/state"}, {"topic": f"{self.state}/availability"}],
            "availability_mode": "all",
        }

    def set(self, property_name: str | None = None) -> str:
        """Return the topic a value is set on: ``…/set/<property>`` for one value, ``…/set`` for a JSON message."""
        return f"{self.state}/set/{property_name}" if property_name else f"{self.state}/set"


def _light(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe a light expose as a Home Assistant light with the JSON schema."""
    features = _features(expose)
    state = features.get("state")
    if state is None:
        return None
    if _property(state) != "state":
        # One lamp of a device with several (state_l1, state_l2 …): its JSON keys carry the
        # endpoint name, which the light translator does not know. It can be switched on and off.
        return _switch(expose, topics)
    color_modes = [mode for feature_name, mode in (("color_xy", "xy"), ("color_hs", "hs"), ("color_temp", "color_temp"))
                   if feature_name in features]
    config: dict[str, Any] = {"schema": "json", "state_topic": topics.state, "command_topic": topics.set(),
                              "brightness": "brightness" in features, "supported_color_modes": color_modes}
    if "brightness" in features:
        config["brightness_scale"] = _number(features["brightness"].get("value_max")) or DEFAULT_LIGHT_BRIGHTNESS_SCALE
    if "color_temp" in features:
        coolest, warmest = _number(features["color_temp"].get("value_min")), _number(features["color_temp"].get("value_max"))
        if coolest is not None and warmest is not None:
            config["min_mireds"], config["max_mireds"] = coolest, warmest
    return "light", config


def _switch(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe a switch expose (or one lamp of a multi-lamp device) as a Home Assistant switch."""
    state = _features(expose).get("state")
    property_name = _property(state) if state is not None else None
    if state is None or property_name is None:
        return None
    return "switch", {"state_topic": topics.state, "value_template": _value_template(property_name),
                      "command_topic": topics.set(property_name),
                      "payload_on": state.get("value_on", "ON"), "payload_off": state.get("value_off", "OFF")}


def _climate(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe a thermostat expose: measured temperature and the set point for heating."""
    features = _features(expose)
    config: dict[str, Any] = {}
    if "local_temperature" in features:
        config["current_temperature_topic"] = topics.state
        config["current_temperature_template"] = _value_template(_property(features["local_temperature"]) or "")
    set_point = features.get("occupied_heating_setpoint") or features.get("current_heating_setpoint")
    if set_point is not None:
        property_name = _property(set_point) or ""
        config["temperature_state_topic"] = topics.state
        config["temperature_state_template"] = _value_template(property_name)
        if _access(set_point) & ACCESS_SET:
            config["temperature_command_topic"] = topics.set(property_name)
        for config_key, feature_key in (("min_temp", "value_min"), ("max_temp", "value_max"), ("temp_step", "value_step")):
            if _number(set_point.get(feature_key)) is not None:
                config[config_key] = _number(set_point.get(feature_key))
    return ("climate", config) if config else None


def _cover(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe a cover expose: open, close, stop and – if the cover reports one – a position (100 = open)."""
    features = _features(expose)
    config: dict[str, Any] = {}
    state = features.get("state")
    if state is not None:
        property_name = _property(state) or ""
        config.update({"command_topic": topics.set(property_name), "state_topic": topics.state,
                       "value_template": _value_template(property_name), "state_open": "OPEN", "state_closed": "CLOSE"})
    position = features.get("position")
    if position is not None:
        property_name = _property(position) or ""
        config.update({"position_topic": topics.state, "position_template": _value_template(property_name)})
        if _access(position) & ACCESS_SET:
            config["set_position_topic"] = topics.set(property_name)
    return ("cover", config) if config else None


def _lock(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe a lock expose: the state is LOCK or UNLOCK, and the same words are the commands."""
    state = _features(expose).get("state")
    property_name = _property(state) if state is not None else None
    if state is None or property_name is None:
        return None
    locked, unlocked = state.get("value_on", "LOCK"), state.get("value_off", "UNLOCK")
    return "lock", {"state_topic": topics.state, "value_template": _value_template(property_name),
                    "command_topic": topics.set(property_name), "payload_lock": locked, "payload_unlock": unlocked,
                    "state_locked": locked, "state_unlocked": unlocked}


def _fan(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe a fan expose: on/off and the fan's modes as preset modes."""
    features = _features(expose)
    config: dict[str, Any] = {}
    state = features.get("state")
    if state is not None:
        property_name = _property(state) or ""
        config.update({"state_topic": topics.state, "state_value_template": _value_template(property_name),
                       "command_topic": topics.set(property_name),
                       "payload_on": state.get("value_on", "ON"), "payload_off": state.get("value_off", "OFF")})
    mode = features.get("mode")
    if mode is not None and isinstance(mode.get("values"), list):
        property_name = _property(mode) or ""
        config.update({"preset_modes": list(mode["values"]), "preset_mode_state_topic": topics.state,
                       "preset_mode_value_template": _value_template(property_name),
                       "preset_mode_command_topic": topics.set(property_name)})
    return ("fan", config) if state is not None else None


def _binary(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe a yes/no value: a detector when it is only reported, a switch when it can be set."""
    property_name = _property(expose)
    if property_name is None:
        return None
    value_on, value_off = expose.get("value_on", True), expose.get("value_off", False)
    config = {"state_topic": topics.state, "value_template": _value_template(property_name)}
    if _access(expose) & ACCESS_SET:
        return "switch", {**config, "command_topic": topics.set(property_name), "payload_on": value_on, "payload_off": value_off}
    return "binary_sensor", {**config, "payload_on": value_on, "payload_off": value_off,
                             "device_class": BINARY_DEVICE_CLASSES.get(property_name, property_name)}


def _numeric(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe a number: a sensor when it is only reported, a number field when it can be set."""
    property_name = _property(expose)
    if property_name is None:
        return None
    config: dict[str, Any] = {"state_topic": topics.state, "value_template": _value_template(property_name)}
    if isinstance(expose.get("unit"), str):
        config["unit_of_measurement"] = expose["unit"][:12]
    if _access(expose) & ACCESS_SET:
        config["command_topic"] = topics.set(property_name)
        for config_key, expose_key in (("min", "value_min"), ("max", "value_max"), ("step", "value_step")):
            if _number(expose.get(expose_key)) is not None:
                config[config_key] = _number(expose.get(expose_key))
        return "number", config
    if property_name in NUMERIC_DEVICE_CLASSES:
        config["device_class"] = NUMERIC_DEVICE_CLASSES[property_name]
    elif property_name == "illuminance" and str(expose.get("unit") or "") == "lx":
        config["device_class"] = "illuminance"      # newer versions report lux here; older ones a raw value (no class)
    return "sensor", config


def _event(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe the "action" of a remote or wall switch: every new value is a press (platform "event"). The values of
    the expose name the buttons."""
    property_name = _property(expose)
    if property_name is None:
        return None
    values = expose.get("values") if isinstance(expose.get("values"), list) else []
    return "event", {"state_topic": topics.state, "value_template": _value_template(property_name), "event_types": values}


def _enum(expose: Mapping[str, Any], topics: _DeviceTopics) -> tuple[str, dict[str, Any]] | None:
    """Describe a choice the user can set; choices that are only reported (events such as "action") are left out."""
    property_name = _property(expose)
    if property_name is None or not _access(expose) & ACCESS_SET or not isinstance(expose.get("values"), list):
        return None
    return "select", {"state_topic": topics.state, "value_template": _value_template(property_name),
                      "command_topic": topics.set(property_name), "options": list(expose["values"])}


_DESCRIBERS = {
    "light": _light,
    "switch": _switch,
    "climate": _climate,
    "cover": _cover,
    "lock": _lock,
    "fan": _fan,
    "binary": _binary,
    "numeric": _numeric,
    "enum": _enum,
}
# Exposes that are the device itself; their entity has no name of its own.
_MAIN_EXPOSE_TYPES = ("light", "switch", "climate", "cover", "lock", "fan")


def _gettable_properties(exposes: list[Any]) -> list[str]:
    """Return the properties of a device that answer to ``…/get``, top-level values and parts of composites alike."""
    properties: list[str] = []
    for expose in exposes:
        if not isinstance(expose, Mapping):
            continue
        for part in [expose, *_features(expose).values()]:
            property_name = _property(part)
            if property_name and _access(part) & ACCESS_GET and property_name not in properties:
                properties.append(property_name)
    return properties


def _entities_of_device(base_topic: str, device: Mapping[str, Any]) -> tuple[list[Entity], StateRequest | None]:
    """Turn one entry of the device list into entities and the message that asks for the device's state."""
    address, friendly_name = device.get("ieee_address"), device.get("friendly_name")
    definition = device.get("definition")
    if not isinstance(address, str) or not _ADDRESS.match(address) or not isinstance(friendly_name, str) or not friendly_name:
        return [], None
    if device.get("type") == "Coordinator" or device.get("disabled") or not isinstance(definition, Mapping):
        return [], None                         # the stick itself, a switched-off device or one Zigbee2MQTT cannot handle
    exposes = definition.get("exposes")
    if not isinstance(exposes, list):
        return [], None

    identifier = f"zigbee2mqtt_{address}"
    info = DeviceInfo(identifier=identifier, name=_clip(friendly_name) or identifier,
                      manufacturer=_clip(definition.get("vendor")) or None, model=_clip(definition.get("model")) or None)
    topics = _DeviceTopics(base_topic, friendly_name)
    entities = []
    for index, expose in enumerate(exposes[:MAX_EXPOSES_PER_DEVICE]):
        describe = _DESCRIBERS.get(expose.get("type")) if isinstance(expose, Mapping) else None
        if describe is not None and _property(expose) in EVENT_PROPERTIES:
            describe = _event
        if describe is None:
            continue
        described = describe(expose, topics)
        if described is None:
            continue
        platform, config = described
        if isinstance(expose.get("category"), str):
            config["entity_category"] = expose["category"]         # "config" or "diagnostic": not a main function
        is_main = expose["type"] in _MAIN_EXPOSE_TYPES
        name = "" if is_main else _clip(expose.get("label")) or _clip(expose.get("name")) or _clip(expose.get("property"))
        endpoint = _clip(expose.get("endpoint"))
        entities.append(Entity(unique_id=f"{address}_{index}_{platform}_native", platform=platform,
                               name=endpoint if is_main and endpoint else name, device=info,
                               config={**topics.shared_options, **config}))

    gettable = _gettable_properties(exposes[:MAX_EXPOSES_PER_DEVICE])
    request = StateRequest(identifier, topics.get, json.dumps({name: "" for name in gettable})) if gettable else None
    return entities, request


def parse_devices(base_topic: str, payload: bytes) -> Announcement | None:
    """Read the device list of a bridge. Returns None when the payload is not a JSON list."""
    source = devices_topic(base_topic)
    try:
        listed = json.loads(payload) if payload.strip() else []
    except ValueError:
        return None
    if not isinstance(listed, list):
        return None
    entities: list[Entity] = []
    state_requests: list[StateRequest] = []
    for device in listed[:MAX_DEVICES]:
        if not isinstance(device, Mapping):
            continue
        device_entities, request = _entities_of_device(base_topic, device)
        entities.extend(device_entities)
        if request is not None and device_entities:
            state_requests.append(request)
    return Announcement(source=source, entities=tuple(entities), state_requests=tuple(state_requests), is_fallback=True)
