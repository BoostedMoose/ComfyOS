"""ComfyOS device <-> Matter device type and attributes, in both directions.

Bridge (ComfyOS -> Matter): :func:`endpoints_of` says as which Matter device(s) a ComfyOS device
appears and with which values; :func:`command_state` turns what a controller wants into a
ComfyOS command.

Controller (Matter -> ComfyOS): :func:`device_of` turns an endpoint of a commissioned Matter
device into a ComfyOS device; :func:`invoke_state` turns a ComfyOS command into Matter values.

| ComfyOS                | Matter device type (id)            |
|------------------------|------------------------------------|
| light, on/off          | On/Off Light (0x0100)              |
| light, dimmable        | Dimmable Light (0x0101)            |
| light, white tones     | Color Temperature Light (0x010C)   |
| light, colour          | Extended Color Light (0x010D)      |
| switch                 | On/Off Plug-in Unit (0x010A)       |
| thermostat             | Thermostat (0x0301), heating only  |
| cover                  | Window Covering (0x0202)           |
| contact                | Contact Sensor (0x0015)            |
| motion                 | Occupancy Sensor (0x0107)          |
| sensor                 | Temperature (0x0302), Humidity Sensor (0x0307) |
| smoke                  | Smoke CO Alarm (0x0076)            |

Units: brightness 0–100 % <-> level 1–254; white tone in mireds on both sides; colour as CIE xy,
in Matter scaled by 65536; temperatures in hundredths of a degree; cover position in hundredths
of a per cent, 0 = open on both sides.

Pure functions without network – tested in ``tests/test_matter_devices.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from plugin_api import (
    DEFAULT_WHITE_RANGE,
    STATE_ALARM,
    STATE_BRIGHTNESS,
    STATE_HUMIDITY,
    STATE_MOTION,
    STATE_ON,
    STATE_OPEN,
    STATE_POSITION,
    STATE_TARGET,
    STATE_TEMPERATURE,
    WARM_WHITE,
    Capability,
    Device,
    DeviceView,
    LightCapabilities,
    LightState,
    PluginError,
    hex_to_xy,
    kelvin_to_mirek,
)

from .protocol import RemoteEndpoint, RemoteNode

# Kinds the helper program can build (bridge/bridge.mjs -> KINDS)
ON_OFF_LIGHT = "on_off_light"
DIMMABLE_LIGHT = "dimmable_light"
COLOR_TEMPERATURE_LIGHT = "color_temperature_light"
EXTENDED_COLOR_LIGHT = "extended_color_light"
ON_OFF_PLUG = "on_off_plug"
THERMOSTAT = "thermostat"
WINDOW_COVERING = "window_covering"
CONTACT_SENSOR = "contact_sensor"
OCCUPANCY_SENSOR = "occupancy_sensor"
TEMPERATURE_SENSOR = "temperature_sensor"
HUMIDITY_SENSOR = "humidity_sensor"
SMOKE_ALARM = "smoke_alarm"

# Matter device type id -> kind. Order matters where an endpoint lists several types: the first match wins.
DEVICE_TYPES: dict[int, str] = {
    0x010D: EXTENDED_COLOR_LIGHT,
    0x010C: COLOR_TEMPERATURE_LIGHT,
    0x0101: DIMMABLE_LIGHT,
    0x0100: ON_OFF_LIGHT,
    0x010B: ON_OFF_PLUG,                # Dimmable Plug-in Unit: used as a plain socket
    0x010A: ON_OFF_PLUG,
    0x0301: THERMOSTAT,
    0x0202: WINDOW_COVERING,
    0x0015: CONTACT_SENSOR,
    0x0107: OCCUPANCY_SENSOR,
    0x0302: TEMPERATURE_SENSOR,
    0x0307: HUMIDITY_SENSOR,
    0x0076: SMOKE_ALARM,
}
LIGHT_KINDS = (ON_OFF_LIGHT, DIMMABLE_LIGHT, COLOR_TEMPERATURE_LIGHT, EXTENDED_COLOR_LIGHT)
COMFYOS_KINDS: dict[str, str] = {
    ON_OFF_LIGHT: "light", DIMMABLE_LIGHT: "light", COLOR_TEMPERATURE_LIGHT: "light", EXTENDED_COLOR_LIGHT: "light",
    ON_OFF_PLUG: "switch", THERMOSTAT: "thermostat", WINDOW_COVERING: "cover", CONTACT_SENSOR: "contact",
    OCCUPANCY_SENSOR: "motion", TEMPERATURE_SENSOR: "sensor", HUMIDITY_SENSOR: "sensor", SMOKE_ALARM: "smoke",
}

HUMIDITY_SUFFIX = "#humidity"           # key of the second endpoint of a sensor that reports temperature and humidity
DEVICE_ID_PREFIX = "matter:"            # ids of devices the controller commissioned: matter:<node>-<endpoint>
MAX_LABEL_LENGTH = 32                   # Matter NodeLabel
MIN_LEVEL, MAX_LEVEL = 1, 254
MIN_TARGET_CELSIUS, MAX_TARGET_CELSIUS = 5.0, 30.0
WHITE_POINT = (0.3127, 0.3290)          # CIE xy of white, for a colour lamp that reports no colour
MAX_MATTER_XY = 65279
DEFAULT_NAME = "Matter-Gerät"


@dataclass(frozen=True)
class SharedEndpoint:
    """One Matter device the bridge shows for something in ComfyOS."""

    key: str                                    # stable key; the endpoint number is assigned to it for good
    kind: str                                   # one of the kinds above
    name: str
    reachable: bool = True
    locked: bool = False
    state: dict[str, Any] = field(default_factory=dict)    # values in Matter units


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

def _number(value: Any) -> float | None:
    """Return ``value`` as a float if it is a real number, else None (booleans are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return float(value)


def brightness_to_level(percent: float) -> int:
    """0–100 % -> Matter level 1–254. Every whole per cent maps to its own level."""
    return int(max(MIN_LEVEL, min(MAX_LEVEL, round(float(percent) * MAX_LEVEL / 100))))


def level_to_brightness(level: float) -> int:
    """Matter level 1–254 -> 1–100 %. ``level_to_brightness(brightness_to_level(p)) == p`` for 1–100."""
    return int(max(1, min(100, round(float(level) * 100 / MAX_LEVEL))))


def celsius_to_matter(celsius: float) -> int:
    """21.5 °C -> 2150 (hundredths of a degree)."""
    return int(round(float(celsius) * 100))


def matter_to_celsius(value: float) -> float:
    """2150 -> 21.5 °C."""
    return round(float(value) / 100, 2)


def percent_to_matter(percent: float) -> int:
    """50 % -> 5000 (hundredths of a per cent), limited to 0–10000."""
    return int(max(0, min(10000, round(float(percent) * 100))))


def matter_to_percent(value: float) -> int:
    """5000 -> 50 %."""
    return int(max(0, min(100, round(float(value) / 100))))


def xy_to_matter(coordinate: float) -> int:
    """CIE coordinate 0–1 -> Matter value (scaled by 65536, at most 65279)."""
    return int(max(0, min(MAX_MATTER_XY, round(float(coordinate) * 65536))))


def matter_to_xy(value: float) -> float:
    """Matter colour value -> CIE coordinate 0–1."""
    return round(float(value) / 65536, 4)


def label(name: str) -> str:
    """Return a name the way Matter stores it: one line, at most 32 characters, never empty."""
    return " ".join(str(name or "").split())[:MAX_LABEL_LENGTH] or DEFAULT_NAME


# ---------------------------------------------------------------------------
# Bridge: ComfyOS device -> Matter device
# ---------------------------------------------------------------------------

def light_kind(view: DeviceView) -> str:
    """Return the Matter light type that matches what a lamp can do."""
    if view.light is None:
        can_dim = _number(view.state.get(STATE_BRIGHTNESS)) is not None
        return DIMMABLE_LIGHT if can_dim else ON_OFF_LIGHT
    if view.light.capabilities.color:
        return EXTENDED_COLOR_LIGHT
    if view.light.capabilities.white_range:
        return COLOR_TEMPERATURE_LIGHT
    return DIMMABLE_LIGHT


def _light_state(view: DeviceView, kind: str) -> dict[str, Any]:
    """Matter values of a lamp: on, level, and for white/colour lamps mireds, xy and the active colour mode."""
    state: dict[str, Any] = {"on": bool(view.state.get(STATE_ON))}
    if kind == ON_OFF_LIGHT:
        return state
    brightness = _number(view.state.get(STATE_BRIGHTNESS))
    state["level"] = brightness_to_level(brightness if brightness else 100)     # an off lamp keeps a valid level
    if kind == DIMMABLE_LIGHT or view.light is None:
        return state
    coolest, warmest = view.light.capabilities.white_range or DEFAULT_WHITE_RANGE
    state["min_mireds"], state["max_mireds"] = int(coolest), int(warmest)
    state["mireds"] = int(max(coolest, min(warmest, view.light.mirek or WARM_WHITE)))
    state["color_mode"] = "ct"
    if kind == EXTENDED_COLOR_LIGHT:
        x, y = (view.light.xy or WHITE_POINT)[:2]
        state["x"], state["y"] = xy_to_matter(x), xy_to_matter(y)
        state["color_mode"] = "ct" if view.light.mode == "ct" else "xy"
    return state


def endpoints_of(view: DeviceView) -> list[SharedEndpoint]:
    """Return the Matter devices a ComfyOS device appears as – none for kinds Matter has no type for here.

    A sensor that reports temperature and humidity becomes two devices; the humidity one carries
    the key ``<id>#humidity``.
    """
    def endpoint(kind: str, state: dict[str, Any], key: str = view.id, name: str = view.name) -> SharedEndpoint:
        return SharedEndpoint(key=key, kind=kind, name=label(name), reachable=view.online, locked=view.locked, state=state)

    if view.id.startswith(DEVICE_ID_PREFIX):
        return []                               # devices of the own controller are never bridged back
    if view.kind == "light":
        kind = light_kind(view)
        return [endpoint(kind, _light_state(view, kind))]
    if view.kind == "switch":
        return [endpoint(ON_OFF_PLUG, {"on": bool(view.state.get(STATE_ON))})]
    if view.kind == "thermostat":
        target = _number(view.state.get(STATE_TARGET))
        if target is None:
            return []                           # a thermostat without a set point has nothing to set
        state = {"heating_setpoint": celsius_to_matter(max(MIN_TARGET_CELSIUS, min(MAX_TARGET_CELSIUS, target))),
                 "min_heat": celsius_to_matter(MIN_TARGET_CELSIUS), "max_heat": celsius_to_matter(MAX_TARGET_CELSIUS)}
        measured = _number(view.state.get(STATE_TEMPERATURE))
        if measured is not None:
            state["local_temperature"] = celsius_to_matter(measured)
        return [endpoint(THERMOSTAT, state)]
    if view.kind == "cover":
        position = _number(view.state.get(STATE_POSITION))
        return [endpoint(WINDOW_COVERING, {} if position is None else {"position": percent_to_matter(position)})]
    if view.kind == "contact":
        return [endpoint(CONTACT_SENSOR, {"contact": not view.state.get(STATE_OPEN)})]     # Matter: true = closed
    if view.kind == "motion":
        return [endpoint(OCCUPANCY_SENSOR, {"occupied": bool(view.state.get(STATE_MOTION))})]
    if view.kind == "smoke":
        return [endpoint(SMOKE_ALARM, {"smoke": bool(view.state.get(STATE_ALARM))})]
    if view.kind == "sensor":
        found = []
        temperature = _number(view.state.get(STATE_TEMPERATURE))
        humidity = _number(view.state.get(STATE_HUMIDITY))
        if temperature is not None:
            found.append(endpoint(TEMPERATURE_SENSOR, {"temperature": celsius_to_matter(temperature)}))
        if humidity is not None:
            found.append(endpoint(HUMIDITY_SENSOR, {"humidity": percent_to_matter(humidity)}, view.id + HUMIDITY_SUFFIX,
                                  f"{view.name} Feuchte"))
        return found
    return []


def command_state(wanted: Mapping[str, Any]) -> dict[str, Any]:
    """Turn what a controller wants (Matter units, already checked by ``protocol``) into a ComfyOS command.

    "Off" wins over everything else in the same request. Raises PluginError when nothing usable is asked.
    """
    if wanted.get("on") is False:
        return {STATE_ON: False}
    command: dict[str, Any] = {}
    if wanted.get("on") is True:
        command[STATE_ON] = True
    if "level" in wanted:
        command[STATE_BRIGHTNESS] = level_to_brightness(wanted["level"])
    if "mireds" in wanted:
        command["mirek"] = int(wanted["mireds"])
    if "x" in wanted and "y" in wanted:
        command["xy"] = [matter_to_xy(wanted["x"]), matter_to_xy(wanted["y"])]
    if "heating_setpoint" in wanted:
        celsius = max(MIN_TARGET_CELSIUS, min(MAX_TARGET_CELSIUS, matter_to_celsius(wanted["heating_setpoint"])))
        command[STATE_TARGET] = round(celsius * 2) / 2          # ComfyOS sets in half degrees
    if "position" in wanted:
        command[STATE_POSITION] = matter_to_percent(wanted["position"])
    elif "cover" in wanted:
        command["cover"] = wanted["cover"]
    if not command:
        raise PluginError("Dieser Befehl lässt sich nicht ausführen.")
    return command


# ---------------------------------------------------------------------------
# Controller: Matter device -> ComfyOS device
# ---------------------------------------------------------------------------

def device_id(node: str, endpoint: int) -> str:
    """Return the ComfyOS id of an endpoint of a commissioned device: ``matter:<node>-<endpoint>``."""
    return f"{DEVICE_ID_PREFIX}{node}-{endpoint}"


def split_device_id(identifier: str) -> tuple[str, int]:
    """Return (node, endpoint) of a device id built by :func:`device_id`. Raises PluginError for anything else."""
    node, separator, endpoint = identifier[len(DEVICE_ID_PREFIX):].rpartition("-")
    if not identifier.startswith(DEVICE_ID_PREFIX) or not separator or not node or not endpoint.isdigit():
        raise PluginError("Dieses Gerät gibt es nicht.")
    return node, int(endpoint)


def kind_of(endpoint: RemoteEndpoint) -> str | None:
    """Return the kind of an endpoint from its device types, or None if ComfyOS has no use for it."""
    for type_id, kind in DEVICE_TYPES.items():
        if type_id in endpoint.types:
            return kind
    return None


def _remote_light(state: Mapping[str, Any], kind: str) -> LightState | None:
    """Light state of a commissioned lamp; None for a lamp that can only be switched."""
    if kind == ON_OFF_LIGHT:
        return None
    capabilities = LightCapabilities()
    if kind in (COLOR_TEMPERATURE_LIGHT, EXTENDED_COLOR_LIGHT):
        coolest = int(state.get("min_mireds") or DEFAULT_WHITE_RANGE[0])
        warmest = int(state.get("max_mireds") or DEFAULT_WHITE_RANGE[1])
        capabilities.white_range = (min(coolest, warmest), max(coolest, warmest))
    capabilities.color = kind == EXTENDED_COLOR_LIGHT
    light = LightState(capabilities=capabilities)
    if "mireds" in state:
        light.mirek = int(state["mireds"])
    if "x" in state and "y" in state:
        light.xy = [matter_to_xy(state["x"]), matter_to_xy(state["y"])]
    if capabilities.white_range or capabilities.color:
        light.mode = "xy" if state.get("color_mode") == "xy" and capabilities.color else "ct"
    return light


def _remote_state(state: Mapping[str, Any], kind: str) -> tuple[dict[str, Any], list[Capability]]:
    """ComfyOS values and capabilities of a commissioned endpoint."""
    values: dict[str, Any] = {}
    capabilities: list[Capability] = []
    if kind in LIGHT_KINDS or kind == ON_OFF_PLUG:
        values[STATE_ON] = bool(state.get("on"))
        capabilities.append(Capability(STATE_ON, "bool", writable=True))
    if kind in LIGHT_KINDS and kind != ON_OFF_LIGHT:
        if "level" in state:
            values[STATE_BRIGHTNESS] = level_to_brightness(state["level"])
        capabilities.append(Capability(STATE_BRIGHTNESS, "number", unit="%", minimum=1, maximum=100, writable=True))
    if kind == THERMOSTAT:
        if "heating_setpoint" in state:
            values[STATE_TARGET] = matter_to_celsius(state["heating_setpoint"])
        if "local_temperature" in state:
            values[STATE_TEMPERATURE] = matter_to_celsius(state["local_temperature"])
        low = matter_to_celsius(state.get("min_heat", celsius_to_matter(MIN_TARGET_CELSIUS)))
        high = matter_to_celsius(state.get("max_heat", celsius_to_matter(MAX_TARGET_CELSIUS)))
        capabilities.append(Capability(STATE_TARGET, "number", unit="°C", minimum=low, maximum=high, step=0.5, writable=True))
        capabilities.append(Capability(STATE_TEMPERATURE, "number", unit="°C"))
    if kind == WINDOW_COVERING:
        if "position" in state:
            values[STATE_POSITION] = matter_to_percent(state["position"])
        capabilities.append(Capability(STATE_POSITION, "number", unit="%", minimum=0, maximum=100, writable=True))
    if kind == CONTACT_SENSOR and "contact" in state:
        values[STATE_OPEN] = not state["contact"]
        capabilities.append(Capability(STATE_OPEN, "bool"))
    if kind == OCCUPANCY_SENSOR and "occupied" in state:
        values[STATE_MOTION] = bool(state["occupied"])
        capabilities.append(Capability(STATE_MOTION, "bool"))
    if kind == TEMPERATURE_SENSOR and "temperature" in state:
        values[STATE_TEMPERATURE] = matter_to_celsius(state["temperature"])
        capabilities.append(Capability(STATE_TEMPERATURE, "number", unit="°C"))
    if kind == HUMIDITY_SENSOR and "humidity" in state:
        values[STATE_HUMIDITY] = matter_to_percent(state["humidity"])
        capabilities.append(Capability(STATE_HUMIDITY, "number", unit="%"))
    if kind == SMOKE_ALARM and "smoke" in state:
        values[STATE_ALARM] = bool(state["smoke"])
        capabilities.append(Capability(STATE_ALARM, "bool"))
    return values, capabilities


def device_of(node: RemoteNode, endpoint: RemoteEndpoint) -> Device | None:
    """Turn an endpoint of a commissioned Matter device into a ComfyOS device.

    Returns None for endpoints without a device type ComfyOS knows (root node, aggregator, …).
    The device is "discovered": it appears in ComfyOS once the user adopts it in the tile.
    """
    kind = kind_of(endpoint)
    if kind is None:
        return None
    values, capabilities = _remote_state(endpoint.state, kind)
    return Device(
        id=device_id(node.node, endpoint.endpoint),
        name=endpoint.name or node.name or node.product or DEFAULT_NAME,
        kind=COMFYOS_KINDS[kind],
        online=node.online and endpoint.reachable,
        state=values,
        capabilities=capabilities,
        light=_remote_light(endpoint.state, kind) if kind in LIGHT_KINDS else None,
        vendor=node.vendor or None,
        model=node.product or None,
        details={"Matter-Gerät": node.name or node.product or node.node, "Endpunkt": str(endpoint.endpoint)},
        discovered=True,
    )


def invoke_state(command: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a ComfyOS command into Matter values for the helper's ``invoke``.

    Raises PluginError when the command holds nothing a Matter device understands.
    """
    brightness = _number(command.get(STATE_BRIGHTNESS))
    if command.get(STATE_ON) is False or brightness == 0:
        return {"on": False}
    wanted: dict[str, Any] = {}
    if brightness is not None:
        wanted["level"] = brightness_to_level(brightness)      # the helper switches the lamp on with the level
    elif command.get(STATE_ON) is True:
        wanted["on"] = True
    mirek = _number(command.get("mirek"))
    kelvin = _number(command.get("kelvin"))
    if mirek is not None or kelvin is not None:
        wanted["mireds"] = int(mirek) if mirek is not None else kelvin_to_mirek(kelvin or 2700)
    xy = command.get("xy")
    if not (isinstance(xy, (list, tuple)) and len(xy) == 2) and isinstance(command.get("color"), str):
        try:
            xy = hex_to_xy(command["color"])
        except ValueError:
            xy = None
    if isinstance(xy, (list, tuple)) and len(xy) == 2 and _number(xy[0]) is not None and _number(xy[1]) is not None:
        wanted["x"], wanted["y"] = xy_to_matter(xy[0]), xy_to_matter(xy[1])
    target = _number(command.get(STATE_TARGET))
    if target is not None:
        wanted["heating_setpoint"] = celsius_to_matter(target)
    position = _number(command.get(STATE_POSITION))
    if position is not None:
        wanted["position"] = percent_to_matter(position)
    elif command.get("cover") in ("up", "down", "stop"):
        wanted["cover"] = command["cover"]
    if not wanted:
        raise PluginError("Dieses Gerät kann das nicht.")
    return wanted


def values_after(command: Mapping[str, Any]) -> dict[str, Any]:
    """Return the ComfyOS values a device has after a command went through (shown at once in the interface)."""
    values: dict[str, Any] = {}
    brightness = _number(command.get(STATE_BRIGHTNESS))
    if command.get(STATE_ON) is False or brightness == 0:
        return {STATE_ON: False}
    if command.get(STATE_ON) is True or brightness is not None:
        values[STATE_ON] = True
    if brightness is not None:
        values[STATE_BRIGHTNESS] = int(round(brightness))
    for key in (STATE_TARGET, STATE_POSITION):
        number = _number(command.get(key))
        if number is not None:
            values[key] = number
    return values
