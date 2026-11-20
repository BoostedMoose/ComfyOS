"""Translate between the Hue Bridge (CLIP API v2) and ComfyOS. Pure functions, no network access.

Reading:  CLIP resources (light, device, zigbee_connectivity, room, motion, temperature, light_level, device_power,
          button, scene) -> HueLight, HueMotionSensor, HueSwitch, HueButtonPress, HueReachabilityChange, ScenePalette

Reachability is read the same way for every kind: from the device's zigbee_connectivity resource (``is_connected``).
Writing:  a ComfyOS light command such as {"on": True, "color": "#ff8000"}
          -> the JSON body for ``PUT /clip/v2/resource/light/<id>``

The bridge sends partial updates over its event stream. The ``apply_*`` functions copy
such an update into an existing HueLight so the cached state stays current.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from plugin_api import (
    DEFAULT_WHITE_RANGE,
    PRESS_DOUBLE,
    PRESS_LONG,
    PRESS_SHORT,
    LightCapabilities,
    LightState,
    ScenePalette,
    hex_to_xy,
    kelvin_to_mirek,
    mirek_to_hex,
    xy_to_hex,
    xy_to_mirek,
)

GRADIENT_MODES = ("interpolated_palette", "interpolated_palette_mirrored", "random_pixelated", "segmented_palette")
NO_EFFECT = "no_effect"
MAX_TRANSITION_MS = 60_000
MAX_NAME_LENGTH = 32            # the bridge stores at most 32 characters
MAX_PALETTE_COLORS = 9
MAX_GRADIENT_POINTS = 10
MAX_EFFECTS = 20

#: Keys of a light command that need a dimmable lamp (a plug only understands "on").
LIGHT_ONLY_KEYS = ("brightness", "kelvin", "mirek", "color", "xy", "gradient", "gradient_xy", "gradient_mode", "effect")

_LEGACY_LIGHT_ID = re.compile(r"^/lights/(\d+)$")


@dataclass
class HueLight:
    """One light service of the bridge together with facts about its device."""

    resource_id: str                 # id of the CLIP "light" resource; commands go here
    device_id: str | None            # id of the owning CLIP "device" resource (name, deletion)
    legacy_id: str | None            # number from the old v1 API ("/lights/9" -> "9"); the Home Base uses it
    name: str
    archetype: str | None            # e.g. "sultan_bulb", "plug"
    on: bool
    brightness: int | None           # per cent; None for plugs
    reachable: bool
    connection_status: str | None    # zigbee status as the bridge reports it
    model: str | None
    vendor: str | None
    mac: str | None                  # zigbee address
    room_name: str | None            # room in the Hue app
    light_state: LightState | None   # None for plugs and other non-dimmable devices

    @property
    def is_plug(self) -> bool:
        """True for smart plugs – they are shown as switches, not as lamps."""
        return "plug" in (self.archetype or "")


@dataclass
class HueMotionSensor:
    """A motion service of the bridge, identified by the zigbee address of its device.

    The same device usually also measures temperature and ambient light and reports its battery – each through a
    resource of its own, which ``attach_sensor_values`` links by the owning device."""

    resource_id: str
    zigbee_mac: str                  # 16 hex digits, lower case
    motion: bool | None
    name: str | None = None          # name of the sensor's device in the Hue app
    device_id: str | None = None     # id of the owning CLIP "device" resource
    temperature: float | None = None             # °C
    illuminance: float | None = None             # lux
    battery: int | None = None                   # per cent
    low_battery: bool | None = None
    temperature_id: str | None = None            # ids of the resources that report these values (event stream)
    light_level_id: str | None = None
    power_id: str | None = None
    connection_status: str | None = None         # zigbee status as the bridge reports it

    @property
    def reachable(self) -> bool:
        """True while the bridge reaches the sensor (see ``is_connected``)."""
        return is_connected(self.connection_status)


@dataclass
class HueSwitch:
    """A wall switch or remote of the bridge (dimmer switch, tap dial, smart button, wall switch module)."""

    device_id: str                   # id of the CLIP "device" resource
    name: str
    model: str | None                # product name for the device sheet, e.g. "Hue dimmer switch"
    zigbee_mac: str | None
    buttons: dict[str, str]          # id of the "button" resource -> control id ("1", "2" …)
    battery: int | None = None
    low_battery: bool | None = None
    power_id: str | None = None
    model_id: str | None = None      # technical model, e.g. "RWL021" – names the buttons (BUTTON_NAMES)
    connection_status: str | None = None         # zigbee status as the bridge reports it

    @property
    def reachable(self) -> bool:
        """True while the bridge reaches the switch (see ``is_connected``)."""
        return is_connected(self.connection_status)


@dataclass(frozen=True)
class HueButtonPress:
    """One press of a button, read from the event stream."""

    device_id: str                   # the switch (CLIP device resource)
    control_id: str                  # which of its buttons ("1" …)
    press: str                       # plugin_api.PRESS_SHORT, PRESS_LONG or PRESS_DOUBLE


@dataclass(frozen=True)
class HueReachabilityChange:
    """The bridge reaches a motion sensor or switch again, or no longer (read from the event stream).

    Lights need no such message: a changed light is reported with all its values, ``reachable`` among them."""

    item: HueMotionSensor | HueSwitch


#: Events of a CLIP button -> kind of press. "initial_press", "repeat" and "long_release" are left out: a short press
#: ends with "short_release", a held one is reported once with "long_press".
BUTTON_EVENTS = {"short_release": PRESS_SHORT, "long_press": PRESS_LONG, "double_short_release": PRESS_DOUBLE}
#: Names of the buttons by model (product_data.model_id); other models get "Taste 1", "Taste 2" …
BUTTON_NAMES = {
    "RWL020": ("An", "Heller", "Dunkler", "Aus"),
    "RWL021": ("An", "Heller", "Dunkler", "Aus"),
    "RWL022": ("An/Aus", "Heller", "Dunkler", "Hue"),
    "ROM001": ("Taste",),
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def clip_name(name: str | None) -> str:
    """Return ``name`` the way the bridge stores it: single spaces, at most 32 characters."""
    return " ".join(str(name or "").split())[:MAX_NAME_LENGTH].strip()


def bridge_mac(bridge_id: str | None) -> str | None:
    """Bridge id 'ecb5fafffeaabbcc' -> MAC 'ecb5faaabbcc' (the form the Home Base uses in its ids)."""
    compact = (bridge_id or "").lower().replace(":", "")
    if len(compact) == 16 and compact[6:10] == "fffe":
        return compact[:6] + compact[10:]
    return compact[:12] or None


def zigbee_mac16(mac: str | None) -> str | None:
    """'00:17:88:01:0b:00:00:01' -> '001788010b000001'; None if it is not a 64-bit address."""
    digits = re.sub(r"[^0-9a-f]", "", str(mac or "").lower())
    return digits if len(digits) == 16 else None


def is_connected(connection_status: str | None) -> bool:
    """Whether the bridge reaches a device, from the status of its zigbee_connectivity resource – the same rule for
    lights, plugs, motion sensors and switches. Only "connected" counts; "connectivity_issue", "disconnected" and
    "unidirectional_incoming" do not. A device without such a resource (e.g. Bluetooth only) counts as reachable."""
    return connection_status in (None, "connected")


def _legacy_light_id(id_v1: str | None) -> str | None:
    """Return the number from an old-style light id (``/lights/7`` -> ``7``), or ``None``."""
    match = _LEGACY_LIGHT_ID.match(id_v1 or "")
    return match.group(1) if match else None


def _as_dict(value: Any) -> dict[str, Any]:
    """Return the value if it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def _clamped_number(value: Any, minimum: float, maximum: float) -> float:
    """Return the value as a number within the limits. Raises ``ValueError`` if it is not a number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Zahl erwartet.")
    if number != number:      # NaN
        raise ValueError("Zahl erwartet.")
    return max(minimum, min(maximum, number))


def _xy_point(value: Any) -> dict[str, float]:
    """Return an xy colour as the bridge expects it, each coordinate within 0-1. Raises ``ValueError`` if the value is
    not a pair."""
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        raise ValueError("xy muss [x, y] sein.")
    x, y = (max(0.0, min(1.0, float(coordinate))) for coordinate in value)
    return {"x": round(x, 4), "y": round(y, 4)}


def _hex_to_xy_point(color: str) -> dict[str, float]:
    """Convert a ``#rrggbb`` colour to the bridge's xy form."""
    x, y = hex_to_xy(color)
    return {"x": x, "y": y}


# ---------------------------------------------------------------------------
# Reading lights
# ---------------------------------------------------------------------------

def read_light_state(light_resource: Mapping[str, Any]) -> LightState | None:
    """Capabilities and current light of a CLIP light; None if it cannot be dimmed (a plug)."""
    if not isinstance(light_resource, dict) or "dimming" not in light_resource:
        return None
    color_temperature = light_resource.get("color_temperature")
    gradient = _as_dict(light_resource.get("gradient"))
    effects = _as_dict(light_resource.get("effects"))

    white_range = None
    if isinstance(color_temperature, dict):
        schema = _as_dict(color_temperature.get("mirek_schema"))
        white_range = (int(schema.get("mirek_minimum") or DEFAULT_WHITE_RANGE[0]),
                       int(schema.get("mirek_maximum") or DEFAULT_WHITE_RANGE[1]))

    capabilities = LightCapabilities(
        white_range=white_range,
        color=isinstance(light_resource.get("color"), dict),
        gradient_points=int(gradient.get("points_capable") or 0),
        gradient_modes=[mode for mode in gradient.get("mode_values") or [] if mode in GRADIENT_MODES],
        effects=[effect for effect in effects.get("effect_values") or []
                 if isinstance(effect, str) and effect != NO_EFFECT][:MAX_EFFECTS],
        # "effects_v2" means an effect can run with its own colour and speed
        effect_parameters=isinstance(light_resource.get("effects_v2"), dict),
    )
    state = LightState(capabilities=capabilities)
    update_light_state(state, light_resource)
    return state


def update_light_state(state: LightState, resource: Mapping[str, Any]) -> None:
    """Copy a full light resource or a partial event into ``state``."""
    color_temperature = resource.get("color_temperature")
    if isinstance(color_temperature, dict) and "mirek" in color_temperature:
        mirek = color_temperature.get("mirek")
        is_valid = color_temperature.get("mirek_valid", mirek is not None)
        state.mirek = mirek if is_valid else None

    color = resource.get("color")
    if isinstance(color, dict) and isinstance(color.get("xy"), dict):
        state.xy = [round(float(color["xy"].get("x", 0)), 4), round(float(color["xy"].get("y", 0)), 4)]

    gradient = resource.get("gradient")
    if isinstance(gradient, dict):
        if isinstance(gradient.get("points"), list):
            state.gradient = [
                [round(float(point["color"]["xy"]["x"]), 4), round(float(point["color"]["xy"]["y"]), 4)]
                for point in gradient["points"]
                if isinstance(point, dict) and isinstance(_as_dict(point.get("color")).get("xy"), dict)
            ]
        if gradient.get("mode") in GRADIENT_MODES:
            state.gradient_mode = gradient["mode"]

    effects = resource.get("effects")
    if isinstance(effects, dict):
        effect = effects.get("status") or effects.get("effect")
        if isinstance(effect, str):
            state.effect = None if effect == NO_EFFECT else effect

    effects_v2 = resource.get("effects_v2")
    if isinstance(effects_v2, dict):
        effect = _as_dict(effects_v2.get("status")).get("effect")
        if isinstance(effect, str):
            state.effect = None if effect == NO_EFFECT else effect

    # What the lamp shows right now: a gradient wins over a white tone, a white tone over a colour.
    if len(state.gradient) >= 2 and state.capabilities.gradient_points:
        state.mode = "gradient"
    elif state.mirek:
        state.mode = "ct"
    elif state.xy and state.capabilities.color:
        state.mode = "xy"


def parse_lights(lights: list[dict], devices: list[dict], zigbee: list[dict], rooms: list[dict]) -> list[HueLight]:
    """CLIP resources -> one HueLight per light service."""
    device_by_id = {device.get("id"): device for device in devices if isinstance(device, dict)}
    zigbee_by_device = {_as_dict(entry.get("owner")).get("rid"): entry for entry in zigbee if isinstance(entry, dict)}
    room_name_by_device: dict[str, str | None] = {}
    for room in rooms:
        if not isinstance(room, dict):
            continue
        for child in room.get("children") or []:
            if child.get("rtype") == "device":
                room_name_by_device[child.get("rid")] = _as_dict(room.get("metadata")).get("name")

    result = []
    for light in lights:
        if not isinstance(light, dict) or not light.get("id"):
            continue
        device_id = _as_dict(light.get("owner")).get("rid")
        device = device_by_id.get(device_id, {})
        connectivity = zigbee_by_device.get(device_id) or {}
        product = _as_dict(device.get("product_data"))
        light_metadata = _as_dict(light.get("metadata"))
        metadata = _as_dict(device.get("metadata")) or light_metadata
        brightness = _as_dict(light.get("dimming")).get("brightness")
        connection_status = connectivity.get("status")
        result.append(HueLight(
            resource_id=light["id"],
            device_id=device_id,
            legacy_id=_legacy_light_id(light.get("id_v1")) or _legacy_light_id(device.get("id_v1")),
            name=metadata.get("name") or light_metadata.get("name") or "Hue-Lampe",
            archetype=metadata.get("archetype") or light_metadata.get("archetype"),
            on=bool(_as_dict(light.get("on")).get("on")),
            brightness=None if brightness is None else int(round(float(brightness))),
            reachable=is_connected(connection_status),
            connection_status=connection_status,
            model=product.get("product_name") or product.get("model_id"),
            vendor=product.get("manufacturer_name") or "Signify",
            mac=connectivity.get("mac_address"),
            room_name=room_name_by_device.get(device_id),
            light_state=read_light_state(light),
        ))
    return result


def apply_light_update(light: HueLight, resource: Mapping[str, Any]) -> None:
    """Copy a light event (on, brightness, colour …) into ``light``."""
    on = resource.get("on")
    if isinstance(on, dict) and "on" in on:
        light.on = bool(on["on"])
    dimming = resource.get("dimming")
    if isinstance(dimming, dict) and dimming.get("brightness") is not None:
        light.brightness = int(round(float(dimming["brightness"])))
    if light.light_state is not None:
        update_light_state(light.light_state, resource)


# ---------------------------------------------------------------------------
# Reading motion sensors
# ---------------------------------------------------------------------------

def read_motion(motion_resource: Mapping[str, Any]) -> bool | None:
    """Current motion value of a CLIP motion resource or event; None if it carries none."""
    motion = motion_resource.get("motion")
    if not isinstance(motion, dict):
        return None
    report = motion.get("motion_report")
    if isinstance(report, dict) and isinstance(report.get("motion"), bool):
        return report["motion"]
    return motion["motion"] if isinstance(motion.get("motion"), bool) else None


def parse_motion_sensors(motions: list[dict], zigbee: list[dict], devices: list[dict]) -> dict[str, HueMotionSensor]:
    """CLIP motion resources -> sensors by zigbee address (the Home Base identifies them that way), each with the
    status of its zigbee connection."""
    zigbee_by_device = {owner_id(entry): entry for entry in zigbee if isinstance(entry, dict)}
    name_by_device = {device.get("id"): _as_dict(device.get("metadata")).get("name")
                      for device in devices if isinstance(device, dict)}
    sensors = {}
    for resource in motions:
        if not isinstance(resource, dict) or not resource.get("id"):
            continue
        device_id = _as_dict(resource.get("owner")).get("rid")
        connectivity = zigbee_by_device.get(device_id) or {}
        zigbee_mac = zigbee_mac16(connectivity.get("mac_address"))
        if zigbee_mac:
            sensors[zigbee_mac] = HueMotionSensor(resource["id"], zigbee_mac, read_motion(resource),
                                                  name_by_device.get(device_id), device_id=device_id,
                                                  connection_status=connectivity.get("status"))
    return sensors


# ---------------------------------------------------------------------------
# Reading further sensor values, batteries and buttons
# ---------------------------------------------------------------------------

def read_temperature(resource: Mapping[str, Any]) -> float | None:
    """Temperature in °C of a CLIP temperature resource or event; None if it carries none."""
    block = _as_dict(resource.get("temperature"))
    report = _as_dict(block.get("temperature_report"))
    value = report.get("temperature", block.get("temperature"))
    return round(float(value), 1) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def read_light_level(resource: Mapping[str, Any]) -> float | None:
    """Ambient light in lux of a CLIP light_level resource or event; None if it carries none.

    The bridge reports 10000 × log10(lux) + 1 – the usual way of the Zigbee standard."""
    block = _as_dict(resource.get("light"))
    report = _as_dict(block.get("light_level_report"))
    value = report.get("light_level", block.get("light_level"))
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 60000:
        return None
    return round(10 ** ((value - 1) / 10000), 1)


def read_power(resource: Mapping[str, Any]) -> tuple[int | None, bool | None]:
    """(battery in per cent, weak battery) of a CLIP device_power resource or event."""
    state = _as_dict(resource.get("power_state"))
    level = state.get("battery_level")
    battery = int(level) if isinstance(level, (int, float)) and not isinstance(level, bool) and 0 <= level <= 100 else None
    kind = state.get("battery_state")
    return battery, (kind in ("low", "critical")) if isinstance(kind, str) else None


def read_button_event(resource: Mapping[str, Any]) -> str | None:
    """Kind of press of a CLIP button event (plugin_api.PRESS_*), or None for events that are no press of their own."""
    block = _as_dict(resource.get("button"))
    event = _as_dict(block.get("button_report")).get("event") or block.get("last_event")
    return BUTTON_EVENTS.get(event) if isinstance(event, str) else None


def owner_id(resource: Mapping[str, Any]) -> str | None:
    """Id of the device a CLIP resource belongs to."""
    owner = _as_dict(resource.get("owner"))
    return owner.get("rid") if isinstance(owner.get("rid"), str) else None


def attach_sensor_values(sensors: Mapping[str, HueMotionSensor], temperatures: list[dict], light_levels: list[dict],
                         powers: list[dict]) -> None:
    """Give each motion sensor the temperature, ambient light and battery its device reports."""
    by_device = {sensor.device_id: sensor for sensor in sensors.values() if sensor.device_id}
    for resource in temperatures:
        sensor = by_device.get(owner_id(resource)) if isinstance(resource, dict) else None
        if sensor is not None:
            sensor.temperature, sensor.temperature_id = read_temperature(resource), resource.get("id")
    for resource in light_levels:
        sensor = by_device.get(owner_id(resource)) if isinstance(resource, dict) else None
        if sensor is not None:
            sensor.illuminance, sensor.light_level_id = read_light_level(resource), resource.get("id")
    for resource in powers:
        sensor = by_device.get(owner_id(resource)) if isinstance(resource, dict) else None
        if sensor is not None:
            (sensor.battery, sensor.low_battery), sensor.power_id = read_power(resource), resource.get("id")


def parse_switches(buttons: list[dict], devices: list[dict], zigbee: list[dict], powers: list[dict]) -> dict[str, HueSwitch]:
    """CLIP button resources -> switches by device id, with their buttons in the order of their control ids and the
    status of their zigbee connection."""
    by_device: dict[str, dict[str, str]] = {}
    for resource in buttons:
        if not isinstance(resource, dict) or not resource.get("id") or not owner_id(resource):
            continue
        control = _as_dict(resource.get("metadata")).get("control_id")
        if isinstance(control, int) and not isinstance(control, bool) and 0 < control < 100:
            by_device.setdefault(owner_id(resource), {})[resource["id"]] = str(control)
    device_rows = {device.get("id"): device for device in devices if isinstance(device, dict)}
    zigbee_by_device = {owner_id(entry): entry for entry in zigbee if isinstance(entry, dict)}
    power_by_device = {owner_id(entry): entry for entry in powers if isinstance(entry, dict)}
    switches = {}
    for device_id, button_ids in by_device.items():
        row = device_rows.get(device_id) or {}
        product = _as_dict(row.get("product_data"))
        battery, low = read_power(power_by_device.get(device_id) or {})
        connectivity = zigbee_by_device.get(device_id) or {}
        switches[device_id] = HueSwitch(
            device_id=device_id, name=_as_dict(row.get("metadata")).get("name") or "Hue-Schalter",
            model=product.get("product_name") or product.get("model_id"),
            zigbee_mac=zigbee_mac16(connectivity.get("mac_address")),
            buttons=dict(sorted(button_ids.items(), key=lambda item: int(item[1]))), battery=battery, low_battery=low,
            power_id=(power_by_device.get(device_id) or {}).get("id"), model_id=product.get("model_id"),
            connection_status=connectivity.get("status"))
    return switches


def button_names(switch: HueSwitch) -> list[tuple[str, str]]:
    """(control id, name to show) of every button of a switch – known models by name, the others numbered."""
    names = BUTTON_NAMES.get(switch.model_id or "", ())
    out = []
    for index, control in enumerate(dict.fromkeys(switch.buttons.values())):
        out.append((control, names[index] if index < len(names) else f"Taste {control}"))
    return out


# ---------------------------------------------------------------------------
# Reading scenes
# ---------------------------------------------------------------------------

def _palette_entries(scene: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Colours of a scene: its palette if it has one, otherwise the colours of its light actions."""
    entries: list[dict[str, Any]] = []
    palette = _as_dict(scene.get("palette"))
    for color in palette.get("color") or []:
        xy = _as_dict(color.get("color")).get("xy")
        if isinstance(xy, dict):
            brightness = int(round(_as_dict(color.get("dimming")).get("brightness") or 100))
            entries.append({"c": xy_to_hex(xy.get("x", .3), xy.get("y", .3)), "b": brightness})
    for white in palette.get("color_temperature") or []:
        mirek = _as_dict(white.get("color_temperature")).get("mirek")
        if mirek:
            brightness = int(round(_as_dict(white.get("dimming")).get("brightness") or 100))
            entries.append({"ct": int(mirek), "b": brightness})
    if entries:
        return entries

    for item in scene.get("actions") or []:
        action = _as_dict(item.get("action")) if isinstance(item, dict) else {}
        brightness = int(round(_as_dict(action.get("dimming")).get("brightness") or 100))
        gradient_points = _as_dict(action.get("gradient")).get("points") or []
        colors = [_as_dict(point.get("color")).get("xy") for point in gradient_points] \
            or [_as_dict(action.get("color")).get("xy")]
        for xy in colors:
            if isinstance(xy, dict):
                entries.append({"c": xy_to_hex(xy.get("x", .3), xy.get("y", .3)), "b": brightness})
        mirek = _as_dict(action.get("color_temperature")).get("mirek")
        if mirek and not any(isinstance(xy, dict) for xy in colors):
            entries.append({"ct": int(mirek), "b": brightness})
    return entries


def _without_duplicates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the palette entries without repeated colours or white tones, in their original order."""
    seen, unique = set(), []
    for entry in entries:
        key = entry.get("c") or entry.get("ct")
        if key not in seen:
            seen.add(key)
            unique.append(entry)
    return unique


def parse_scenes(scenes: list[dict], groups: list[dict]) -> list[ScenePalette]:
    """CLIP scenes -> palettes for import. ``groups`` are the rooms and zones of the bridge.

    The bridge stores a scene once per room. For the import each name is listed once,
    with all rooms it exists in.
    """
    group_names = {group.get("id"): _as_dict(group.get("metadata")).get("name")
                   for group in groups if isinstance(group, dict)}
    palettes = []
    for scene in scenes:
        if not isinstance(scene, dict) or not scene.get("id"):
            continue
        palette = _without_duplicates(_palette_entries(scene))[:MAX_PALETTE_COLORS]
        if not palette:
            continue
        metadata = _as_dict(scene.get("metadata"))
        image = _as_dict(metadata.get("image"))
        group_name = group_names.get(_as_dict(scene.get("group")).get("rid"))
        palettes.append(ScenePalette(
            id=scene["id"], name=metadata.get("name") or "Hue-Szene", palette=palette,
            groups=[group_name] if group_name else [],
            dynamic=bool(scene.get("auto_dynamic")),
            image_id=image.get("rid") if image.get("rtype") == "public_image" else None,
        ))
    palettes.sort(key=lambda scene: (scene.groups[0] if scene.groups else "", scene.name))

    by_name: dict[str, ScenePalette] = {}
    for scene in palettes:
        known = by_name.get(scene.name.lower())
        if known is None:
            by_name[scene.name.lower()] = scene
        elif scene.groups and scene.groups[0] not in known.groups:
            known.groups.append(scene.groups[0])
    return list(by_name.values())


# ---------------------------------------------------------------------------
# Writing: light commands
# ---------------------------------------------------------------------------

def build_command(light_state: LightState | None, requested: Mapping[str, Any]) -> dict[str, Any]:
    """ComfyOS light command -> body for ``PUT /resource/light/<id>``.

    ``requested`` may contain: on, brightness (0–100, 0 switches off), kelvin or mirek,
    color ("#rrggbb") or xy, gradient (list of "#rrggbb") or gradient_xy, gradient_mode,
    effect, effect_color, effect_mirek, effect_speed (0–1) and duration (milliseconds).

    What the lamp cannot do is replaced sensibly: a colour becomes the nearest white tone
    on a white-only lamp, a white tone becomes a colour on a colour-only lamp.
    Raises ValueError when the command makes no sense.
    """
    capabilities = light_state.capabilities if light_state else LightCapabilities()
    white_range = capabilities.white_range or DEFAULT_WHITE_RANGE
    body: dict[str, Any] = {}

    def set_white(mirek: float) -> None:
        mirek = int(max(white_range[0], min(white_range[1], round(mirek))))
        if capabilities.white_range:
            body["color_temperature"] = {"mirek": mirek}
        elif capabilities.color:
            body["color"] = {"xy": _hex_to_xy_point(mirek_to_hex(mirek))}

    def set_color(xy: dict[str, float]) -> None:
        if capabilities.color:
            body["color"] = {"xy": xy}
        elif capabilities.white_range:
            body["color_temperature"] = {"mirek": xy_to_mirek(xy["x"], xy["y"], white_range)}

    # on / brightness
    if "on" in requested:
        body["on"] = {"on": bool(requested["on"])}
    if requested.get("brightness") is not None:
        brightness = _clamped_number(requested["brightness"], 0, 100)
        if brightness <= 0:
            body["on"] = {"on": False}
        else:
            body["dimming"] = {"brightness": round(max(1.0, brightness), 1)}
            body.setdefault("on", {"on": True})

    # white tone or colour (the first one given wins)
    if requested.get("mirek") is not None:
        set_white(_clamped_number(requested["mirek"], 100, 1000))
    elif requested.get("kelvin") is not None:
        set_white(kelvin_to_mirek(_clamped_number(requested["kelvin"], 1000, 10000)))
    elif requested.get("color") is not None:
        set_color(_hex_to_xy_point(requested["color"]))
    elif requested.get("xy") is not None:
        set_color(_xy_point(requested["xy"]))

    # gradient
    gradient_points = None
    if requested.get("gradient") is not None:
        if not isinstance(requested["gradient"], list):
            raise ValueError("gradient muss eine Liste von Farben sein.")
        gradient_points = [_hex_to_xy_point(color) for color in requested["gradient"][:MAX_GRADIENT_POINTS]]
    elif requested.get("gradient_xy") is not None:
        if not isinstance(requested["gradient_xy"], list):
            raise ValueError("gradient_xy muss eine Liste sein.")
        gradient_points = [_xy_point(point) for point in requested["gradient_xy"][:MAX_GRADIENT_POINTS]]
    if gradient_points:
        if capabilities.gradient_points and len(gradient_points) >= 2:
            shown = gradient_points[:capabilities.gradient_points]
            body["gradient"] = {"points": [{"color": {"xy": point}} for point in shown]}
            body.pop("color_temperature", None)
            body.pop("color", None)
        elif "color" not in body and "color_temperature" not in body:
            set_color(gradient_points[0])     # no gradient strip: show the first colour
    if requested.get("gradient_mode") is not None and capabilities.gradient_points:
        if requested["gradient_mode"] not in (capabilities.gradient_modes or GRADIENT_MODES):
            raise ValueError("Unbekannter Verlaufsmodus.")
        body.setdefault("gradient", {})["mode"] = requested["gradient_mode"]

    # effect
    if requested.get("effect") is not None:
        effect = str(requested["effect"])
        if effect not in (NO_EFFECT, *capabilities.effects):
            raise ValueError("Diesen Effekt kann die Lampe nicht.")
        parameters: dict[str, Any] = {}
        if effect != NO_EFFECT and capabilities.effect_parameters:
            # Colour and speed of the effect – silently ignored by lamps that cannot do it.
            if requested.get("effect_color") is not None:
                parameters["color"] = {"xy": _hex_to_xy_point(requested["effect_color"])}
            elif requested.get("effect_mirek") is not None:
                mirek = round(_clamped_number(requested["effect_mirek"], 100, 1000))
                parameters["color_temperature"] = {"mirek": int(max(white_range[0], min(white_range[1], mirek)))}
            if requested.get("effect_speed") is not None:
                parameters["speed"] = round(_clamped_number(requested["effect_speed"], 0, 1), 3)
        if parameters:
            body["effects_v2"] = {"action": {"effect": effect, "parameters": parameters}}
        else:
            body["effects"] = {"effect": effect}

    # transition time – only meaningful when something besides on/off changes
    changes_besides_on = len(body) - (1 if "on" in body else 0)
    if requested.get("duration") is not None and changes_besides_on > 0:
        body["dynamics"] = {"duration": int(_clamped_number(requested["duration"], 0, MAX_TRANSITION_MS))}

    if not body:
        raise ValueError("Nichts zu schalten – bitte on, brightness, Farbe oder Weißton angeben.")
    return body


def apply_command(light: HueLight, body: Mapping[str, Any]) -> None:
    """Copy a command that was just sent into ``light`` (the bridge confirms it later by event)."""
    if "on" in body:
        light.on = bool(body["on"]["on"])
    if "dimming" in body:
        light.brightness = int(round(body["dimming"]["brightness"]))
    state = light.light_state
    if state is None:
        return
    if "color_temperature" in body:
        state.mirek, state.gradient = body["color_temperature"]["mirek"], []
    if "color" in body:
        state.xy = [body["color"]["xy"]["x"], body["color"]["xy"]["y"]]
        state.mirek, state.gradient = None, []
    if "gradient" in body:
        if "points" in body["gradient"]:
            state.gradient = [[point["color"]["xy"]["x"], point["color"]["xy"]["y"]]
                              for point in body["gradient"]["points"]]
            state.mirek = None
        if "mode" in body["gradient"]:
            state.gradient_mode = body["gradient"]["mode"]
    if "effects" in body:
        effect = body["effects"]["effect"]
        state.effect = None if effect == NO_EFFECT else effect
    if "effects_v2" in body:
        state.effect = body["effects_v2"]["action"]["effect"]
    update_light_state(state, {})      # recompute the mode
