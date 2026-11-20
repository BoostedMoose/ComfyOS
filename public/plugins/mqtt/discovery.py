"""Home Assistant MQTT discovery: read config messages and describe what they announce. No network access.

Devices such as Zigbee2MQTT or ESPHome describe themselves on the broker. They publish a
retained JSON message on a *config topic*::

    <prefix>/<platform>/[<node id>/]<object id>/config        one entity per message
    <prefix>/device/[<node id>/]<object id>/config            one device with all its entities

``<prefix>`` is ``homeassistant`` unless the user changed it. An *entity* is one function
of a device: the light itself, its battery level, its signal strength. This module turns
such a message into :class:`Entity` objects with a fully written-out configuration:

* abbreviated keys are expanded (``stat_t`` -> ``state_topic``, ``dev`` -> ``device`` …)
* ``~`` at the start or end of a topic is replaced by the base topic
* in the device form, options given once for the device are copied into every entity

An empty message on a config topic means "this is gone"; the result then has no entities.
What the entities *mean* for ComfyOS is decided in ``translate.py``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

DEFAULT_PREFIX = "homeassistant"
DEVICE_PLATFORM = "device"                  # the platform name of the device form
MAX_ENTITIES_PER_MESSAGE = 200
MAX_TEXT_LENGTH = 80                        # names and identifiers are clipped to this length

# Short keys Home Assistant accepts in a config message, and what they stand for.
# Only keys this plugin reads are listed; unknown keys are kept as they are.
ABBREVIATIONS = {
    "avty": "availability",
    "avty_mode": "availability_mode",
    "avty_t": "availability_topic",
    "avty_tpl": "availability_template",
    "bri_cmd_t": "brightness_command_topic",
    "bri_scl": "brightness_scale",
    "bri_stat_t": "brightness_state_topic",
    "bri_val_tpl": "brightness_value_template",
    "cmd_t": "command_topic",
    "cmd_tpl": "command_template",
    "cmps": "components",
    "curr_temp_t": "current_temperature_topic",
    "curr_temp_tpl": "current_temperature_template",
    "dev": "device",
    "dev_cla": "device_class",
    "ent_cat": "entity_category",
    "evt_typ": "event_types",
    "max_mirs": "max_mireds",
    "min_mirs": "min_mireds",
    "o": "origin",
    "ops": "options",
    "p": "platform",
    "pct_cmd_t": "percentage_command_topic",
    "pct_cmd_tpl": "percentage_command_template",
    "pct_stat_t": "percentage_state_topic",
    "pct_val_tpl": "percentage_value_template",
    "pl_avail": "payload_available",
    "pl_cls": "payload_close",
    "pl_lock": "payload_lock",
    "pl_not_avail": "payload_not_available",
    "pl_off": "payload_off",
    "pl_on": "payload_on",
    "pl_open": "payload_open",
    "pl_prs": "payload_press",
    "pl_stop": "payload_stop",
    "pl_unlk": "payload_unlock",
    "pos_clsd": "position_closed",
    "pos_open": "position_open",
    "pos_t": "position_topic",
    "pos_tpl": "position_template",
    "pr_mode_cmd_t": "preset_mode_command_topic",
    "pr_mode_cmd_tpl": "preset_mode_command_template",
    "pr_mode_stat_t": "preset_mode_state_topic",
    "pr_mode_val_tpl": "preset_mode_value_template",
    "pr_modes": "preset_modes",
    "ret": "retain",
    "set_pos_t": "set_position_topic",
    "set_pos_tpl": "set_position_template",
    "spd_rng_max": "speed_range_max",
    "spd_rng_min": "speed_range_min",
    "stat_clsd": "state_closed",
    "stat_locked": "state_locked",
    "stat_off": "state_off",
    "stat_on": "state_on",
    "stat_open": "state_open",
    "stat_t": "state_topic",
    "stat_tpl": "state_template",
    "stat_unlocked": "state_unlocked",
    "stat_val_tpl": "state_value_template",
    "sup_clrm": "supported_color_modes",
    "temp_cmd_t": "temperature_command_topic",
    "temp_cmd_tpl": "temperature_command_template",
    "temp_stat_t": "temperature_state_topic",
    "temp_stat_tpl": "temperature_state_template",
    "temp_unit": "temperature_unit",
    "uniq_id": "unique_id",
    "unit_of_meas": "unit_of_measurement",
    "val_tpl": "value_template",
}

# Short keys inside the "device" block.
DEVICE_ABBREVIATIONS = {
    "cns": "connections",
    "ids": "identifiers",
    "mdl": "model",
    "mf": "manufacturer",
    "sa": "suggested_area",
}

# Short keys inside one entry of the "availability" list.
AVAILABILITY_ABBREVIATIONS = {
    "t": "topic",
    "val_tpl": "value_template",
    "pl_avail": "payload_available",
    "pl_not_avail": "payload_not_available",
}

BASE_TOPIC_KEY = "~"
# Keys of the device form that describe the device as a whole and are not copied into its entities.
_DEVICE_FORM_KEYS = ("device", "origin", "components", "child_devices")


@dataclass(frozen=True)
class DeviceInfo:
    """The physical device an entity belongs to."""

    identifier: str                         # stable id on the broker, e.g. "zigbee2mqtt_0x00158d0001a2b3c4"
    name: str
    manufacturer: str | None = None
    model: str | None = None
    suggested_area: str | None = None       # room name the device suggests


@dataclass(frozen=True)
class Entity:
    """One function of a device with its fully expanded configuration."""

    unique_id: str
    platform: str                           # "light", "switch", "sensor", "binary_sensor", "climate", "cover", "fan" …
    name: str                               # may be empty: the entity then is the device itself
    device: DeviceInfo
    config: Mapping[str, Any] = field(default_factory=dict)

    @property
    def device_class(self) -> str | None:
        """What the entity measures or reports, e.g. "temperature", "door", "battery"."""
        value = self.config.get("device_class")
        return value if isinstance(value, str) and value else None

    @property
    def is_secondary(self) -> bool:
        """True for settings and diagnostics of a device (child lock, signal strength), not its main function."""
        return self.config.get("entity_category") in ("config", "diagnostic")

    @property
    def label(self) -> str:
        """A name to show the user: the entity's own name, else its device class, else its platform."""
        return self.name or self.device_class or self.platform


@dataclass(frozen=True)
class StateRequest:
    """A message that asks a device to publish its current state.

    Many devices publish their state only when it changes. Where a system offers a way to ask
    (Zigbee2MQTT ``…/get``, Tasmota ``STATE``), the plugin asks once after connecting, for
    devices that have not said anything by then.
    """

    device_identifier: str                  # DeviceInfo.identifier of the device that is asked
    topic: str
    payload: str = ""


@dataclass(frozen=True)
class Announcement:
    """Everything one source announces right now. A later announcement of the same source replaces it.

    The source is a config topic of Home Assistant discovery, a Tasmota device or the device
    list of a Zigbee2MQTT bridge.
    """

    source: str                             # the config topic, or another stable name of the source
    entities: tuple[Entity, ...] = ()
    state_requests: tuple[StateRequest, ...] = ()
    # True for a source that describes devices another source may describe better. Its entities
    # are only used for devices no other source announces (Zigbee2MQTT's own device list next
    # to the Home Assistant discovery of the same bridge).
    is_fallback: bool = False


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

def discovery_filters(prefix: str) -> list[str]:
    """Return the topic filters that catch every config topic below ``prefix`` (with and without node id)."""
    return [f"{prefix}/+/+/config", f"{prefix}/+/+/+/config"]


def split_config_topic(prefix: str, topic: str) -> tuple[str, str] | None:
    """Split a config topic into ``(platform, object path)``; None if ``topic`` is not a config topic.

    The object path is ``<node id>/<object id>`` or just ``<object id>``.
    """
    start = prefix + "/"
    if not topic.startswith(start) or not topic.endswith("/config"):
        return None
    levels = topic[len(start):-len("/config")].split("/")
    if len(levels) not in (2, 3) or not all(levels):
        return None
    return levels[0], "/".join(levels[1:])


# ---------------------------------------------------------------------------
# Expanding a configuration
# ---------------------------------------------------------------------------

def _expand_keys(raw: Mapping[str, Any], abbreviations: Mapping[str, str]) -> dict[str, Any]:
    """Replace Home Assistant's abbreviated keys by their full names."""
    return {abbreviations.get(str(key), str(key)): value for key, value in raw.items()}


def _with_base_topic(value: Any, base_topic: str) -> Any:
    """Replace ``~`` at the start or end of a topic by the base topic."""
    if not isinstance(value, str):
        return value
    if value.startswith(BASE_TOPIC_KEY):
        return base_topic + value[1:]
    if value.endswith(BASE_TOPIC_KEY):
        return value[:-1] + base_topic
    return value


def expand_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Write out one entity configuration: expand short keys, the device block, the availability list and ``~``."""
    config = _expand_keys(raw, ABBREVIATIONS)
    if isinstance(config.get("device"), Mapping):
        config["device"] = _expand_keys(config["device"], DEVICE_ABBREVIATIONS)
    if isinstance(config.get("availability"), list):
        config["availability"] = [_expand_keys(entry, AVAILABILITY_ABBREVIATIONS)
                                  for entry in config["availability"] if isinstance(entry, Mapping)]

    base_topic = config.pop(BASE_TOPIC_KEY, None)
    if isinstance(base_topic, str) and base_topic:
        for key in list(config):
            if key.endswith("_topic"):
                config[key] = _with_base_topic(config[key], base_topic)
        for entry in config.get("availability") or []:
            entry["topic"] = _with_base_topic(entry.get("topic"), base_topic)
    return config


# ---------------------------------------------------------------------------
# Reading a config message
# ---------------------------------------------------------------------------

def _clip(value: Any) -> str:
    """Return a text or number as a trimmed, length-limited string; anything else becomes empty."""
    return str(value).strip()[:MAX_TEXT_LENGTH] if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _first_identifier(device_block: Mapping[str, Any]) -> str:
    """Return the first identifier of a device block; Home Assistant allows one text or a list."""
    identifiers = device_block.get("identifiers")
    if isinstance(identifiers, list) and identifiers:
        return _clip(identifiers[0])
    return _clip(identifiers)


def _device_info(config: Mapping[str, Any], fallback_identifier: str) -> DeviceInfo:
    """Read the device block of a discovery message; entities without one become a device of their own."""
    device_block = config.get("device") if isinstance(config.get("device"), Mapping) else {}
    identifier = _first_identifier(device_block) or fallback_identifier
    return DeviceInfo(
        identifier=identifier,
        name=_clip(device_block.get("name")) or _clip(config.get("name")) or identifier,
        manufacturer=_clip(device_block.get("manufacturer")) or None,
        model=_clip(device_block.get("model")) or None,
        suggested_area=_clip(device_block.get("suggested_area")) or None,
    )


def _entity(platform: str, config: Mapping[str, Any], fallback_id: str) -> Entity:
    """Build an entity from a discovery message, using the fallback id if it has no unique id."""
    unique_id = _clip(config.get("unique_id")) or fallback_id
    return Entity(unique_id=unique_id, platform=platform, name=_clip(config.get("name")),
                  device=_device_info(config, fallback_identifier=unique_id), config=config)


def _entities_of_device_form(raw: Mapping[str, Any], object_path: str) -> list[Entity]:
    """Read the device form: one message with a ``components`` map; shared options apply to every entry."""
    root = _expand_keys(raw, ABBREVIATIONS)
    components = root.get("components")
    if not isinstance(components, Mapping):
        return []
    shared_options = {key: value for key, value in root.items() if key not in _DEVICE_FORM_KEYS}
    entities = []
    for component_key, component in list(components.items())[:MAX_ENTITIES_PER_MESSAGE]:
        if not isinstance(component, Mapping):
            continue
        component_config = _expand_keys(component, ABBREVIATIONS)
        platform = component_config.get("platform")
        if not isinstance(platform, str) or len(component_config) == 1:
            continue                            # an entry with only its platform means "this entity was removed"
        config = expand_config({**shared_options, **component_config, "device": root.get("device")})
        entities.append(_entity(platform, config, fallback_id=f"{object_path}/{_clip(component_key)}"))
    return entities


def parse_announcement(prefix: str, topic: str, payload: bytes) -> Announcement | None:
    """Read one message on a config topic.

    Returns None when the topic is not a config topic or the payload is not a JSON object;
    an :class:`Announcement` without entities when the payload is empty (the entity was removed).
    """
    parts = split_config_topic(prefix, topic)
    if parts is None:
        return None
    platform, object_path = parts
    if not payload.strip():
        return Announcement(source=topic)
    try:
        raw = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(raw, Mapping):
        return None
    if platform == DEVICE_PLATFORM:
        return Announcement(source=topic, entities=tuple(_entities_of_device_form(raw, object_path)))
    return Announcement(source=topic, entities=(_entity(platform, expand_config(raw), fallback_id=f"{platform}/{object_path}"),))
