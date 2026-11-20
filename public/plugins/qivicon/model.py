"""Turn the Home Base's openHAB data into devices (pure functions, no network).

Sources, all read through the Home Base's REST proxy (verified on a Home Base 2):
    /rest/things            things with channels -> linked items, location (room id), status
    /rest/items             item name -> state, type, label (rooms are group items, too)
    /rest/qivicon/devices   battery, low battery, last communication, bridges

How a thing becomes a device
    ``classify`` decides the kind. The channel helpers find the channel that switches the
    device. A switching channel is **not** looked up in a fixed list of names (plugs with
    channels such as ``switchState`` were missed that way) but by item type plus a block
    list of channels that only report (buttons, tamper, smoke, contact, reachability …).
    Without such a channel the device has no ``control_item`` and ComfyOS shows no switch –
    it never switches blindly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from plugin_api import (
    STATE_ALARM,
    STATE_BRIGHTNESS,
    STATE_HUMIDITY,
    STATE_ILLUMINANCE,
    STATE_MOTION,
    STATE_ON,
    STATE_OPEN,
    STATE_POSITION,
    STATE_TARGET,
    STATE_TEMPERATURE,
    Capability,
    Device,
    Reading,
)

LEADING_NUMBER = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)")
UNKNOWN_STATES = ("NULL", "UNDEF", "")
MAX_READINGS = 20
GONE_TEXT = "Von der Home Base getrennt – Wiederverbinden nötig"

# Switch channels that only report and never switch anything. A channel listed here never becomes
# the control channel – otherwise a button press or a tamper message would show up as a switch.
READ_ONLY_WORDS = ("alertstate", "batterylow", "lowbattery", "battery_low", "tamper", "sabotage", "presence", "motion",
                   "contact", "openclose", "smoke", "fire", "fault", "error", "reachable", "online", "unreach",
                   "button", "key", "rocker", "remote", "scene", "event", "trigger", "alarm", "leak", "water",
                   "status", "heartbeat", "test")
SWITCH_CHANNEL_NAMES = ("switch", "power", "onoff", "on_off", "state", "switchstate", "powerswitch", "relay", "relaystate")
DIMMER_SKIP_WORDS = ("temperature", "colortemperature")
COVER_WORDS = ("shutter", "rollershutter", "blind", "jalousie", "awning", "curtain", "rollo", "marki", "screen")
LIGHT_WORDS = ("light", "bulb", "lamp", "dimmer", "led")
PLUG_WORDS = ("plug", "switch", "socket", "outlet", "relay", "sw_", "actuator")
SETPOINT_WORDS = ("heatingtemperature", "setpoint", "targettemperature")
# Valve opening of a radiator thermostat – read only. No capture of a Home Base proves the channel name;
# a number/dimmer channel with "valve" in its name is accepted (openHAB: valvePosition, VALVE_LEVEL …)
# or "level" (Homematic IP: LEVEL = 0…1). Without such a channel the device reports no valve.
VALVE_SKIP_WORDS = ("adapt", "error", "fault", "offset", "max", "min", "state", "mode", "boost", "maintenance")
VALVE_LEVEL_NAMES = ("level", "valvelevel", "valve_level")
POWER_SKIP_WORDS = ("factor", "max", "limit", "threshold")
ENERGY_WORDS = ("energy", "consumption", "meter")
DETAIL_FIELDS = (("Seriennummer", ("serialNumber", "serial", "serial_number")),
                 ("Modell", ("modelId", "model", "modelName")),
                 ("Firmware", ("firmwareVersion", "firmware", "softwareVersion")),
                 ("MAC", ("macAddress", "zigbee_macaddress", "mac")),
                 ("Unique-ID", ("uniqueId",)))
# The Home Base lists lamps and sensors of a Hue Bridge through its own Hue binding. Such things are
# relayed and carry the same link key as the device of the Hue plugin:
#     hue:<type, 4 digits>:<bridge mac>:<number>          ->  hue:<bridge mac>:light:<number>
#     hue:<type>:<bridge mac>:<zigbee address, 16 hex>    ->  zigbee:<address>     (motion sensors only)
HUE_LIGHT_ID = re.compile(r"^hue:(\d{4}):([0-9a-fA-F]{12}):(\d+)$")
HUE_SENSOR_ID = re.compile(r"^hue:[A-Za-z0-9]+:([0-9a-fA-F]{12}):([0-9a-fA-F]{16})$")
# Sonos speakers come through the Home Base's Sonos binding. The thing UID ends with the id the
# speaker has in its own system, and the Sonos plugin uses the same id as link key:
#     sonos:<model>:RINCON_<id>                           ->  sonos:RINCON_<id>
SONOS_ID = re.compile(r"^sonos:[A-Za-z0-9_-]+:(RINCON_[0-9A-Fa-f]{6,40})$")


@dataclass
class Channel:
    """One channel of a thing together with the state of its linked item."""

    id: str
    label: str
    item_type: str                  # openHAB item type, e.g. "Switch", "Number:Temperature"
    item: str | None                # name of the linked item, None = not linked
    state: str | None               # current state as text, None = unknown

    @property
    def key(self) -> str:
        """The channel id in lower case, for comparisons."""
        return self.id.lower()


@dataclass
class HomeBaseDevice:
    """A thing of the Home Base, reduced to what ComfyOS shows and switches."""

    id: str                                   # thing UID, e.g. "dect:thermostat:1"
    name: str
    kind: str                                 # one of plugin_api.DEVICE_KINDS
    online: bool
    type_id: str                              # thing type UID
    room_id: str | None = None                # room (group item) on the Home Base
    room_name: str | None = None              # label of that group item
    status: str | None = None                 # ONLINE, OFFLINE, UNKNOWN …
    status_code: str | None = None            # status detail, e.g. GONE
    status_text: str | None = None            # short note for the user
    vendor: str | None = None
    battery: int | None = None
    low_battery: bool | None = None
    last_seen: Any = None
    control_item: str | None = None           # item that takes commands; None = cannot be switched
    state: dict[str, Any] = field(default_factory=dict)
    power_w: float | None = None
    energy_kwh: float | None = None
    problems: list[str] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)
    readings: list[Reading] = field(default_factory=list)


def number(state: Any) -> float | None:
    """'21.5 °C' -> 21.5, '0,0,100' -> 0.0, 'NULL' / 'UNDEF' / None -> None."""
    if state is None:
        return None
    if isinstance(state, (int, float)):
        return float(state)
    text = str(state).strip()
    if text.upper() in UNKNOWN_STATES:
        return None
    match = LEADING_NUMBER.match(text)
    return float(match.group(1).replace(",", ".")) if match else None


def known(state: Any) -> str | None:
    """Return the state as text, or None for openHAB's 'NULL' and 'UNDEF'."""
    if state is None:
        return None
    text = str(state).strip()
    return None if text.upper() in UNKNOWN_STATES else text


def _rounded(value: float | None, digits: int = 1) -> float | None:
    """Round a value, passing ``None`` through."""
    return None if value is None else round(value, digits)


def _usable(channel: Channel, *item_types: str) -> bool:
    """True for a linked channel of one of the item types that does more than report."""
    if not channel.item or not channel.item_type.startswith(item_types):
        return False
    return not any(word in channel.key for word in READ_ONLY_WORDS)


def switch_channel(channels: list[Channel]) -> Channel | None:
    """Find the channel that switches the device on and off.

    Well-known names win; after that any usable switch channel counts, so plugs and
    actuators with unusual channel names ("switchState", "powerSwitch", "relay") work too.
    """
    for channel in channels:
        if _usable(channel, "Switch") and channel.key in SWITCH_CHANNEL_NAMES:
            return channel
    for channel in channels:
        if _usable(channel, "Switch"):
            return channel
    return None


def dim_channel(channels: list[Channel]) -> Channel | None:
    """Find the colour or dimmer channel of a lamp."""
    for channel in channels:
        if _usable(channel, "Color", "Dimmer") and not any(word in channel.key for word in DIMMER_SKIP_WORDS):
            return channel
    return None


def cover_channel(channels: list[Channel]) -> Channel | None:
    """Find the channel that moves a roller shutter, blind or awning."""
    for channel in channels:
        if _usable(channel, "Rollershutter"):
            return channel
    return None


def valve_channel(channels: list[Channel]) -> Channel | None:
    """Find the channel that reports the valve opening of a thermostat (read only)."""
    candidates = [channel for channel in channels
                  if channel.item and channel.item_type.startswith(("Number", "Dimmer"))
                  and not any(word in channel.key for word in VALVE_SKIP_WORDS)]
    by_name = next((channel for channel in candidates if "valve" in channel.key), None)
    return by_name or next((channel for channel in candidates if channel.key in VALVE_LEVEL_NAMES), None)


def valve_percent(channel: Channel | None) -> int | None:
    """'40 %' -> 40, '0.4' (a share, Homematic LEVEL) -> 40, 'NULL' -> None; always 0…100."""
    if channel is None:
        return None
    raw = channel.state if channel.state is not None else ""
    value = number(raw)
    if value is None:
        return None
    is_share = "%" not in raw and 0 <= value <= 1 and ("." in raw or "," in raw or channel.key == "level")
    if is_share:
        value *= 100
    return int(round(max(0.0, min(100.0, value))))


def classify(thing_type: str, channels: list[Channel]) -> str:
    """Decide the device kind from the thing type and the channels."""
    type_name = thing_type.lower()
    keys = {channel.key for channel in channels}
    item_types = {channel.item_type.split(":")[0] for channel in channels}
    if "temperature" in keys and any(word in key for key in keys for word in SETPOINT_WORDS):
        return "thermostat"
    if "smoke" in type_name or any("smoke" in key for key in keys):
        return "smoke"
    if type_name.startswith("sonos") or "speaker" in type_name or "player" in type_name:
        return "media"
    if "Rollershutter" in item_types or any(word in type_name for word in COVER_WORDS):
        return "cover"
    if "contact" in {item_type.lower() for item_type in item_types} or "openclose" in type_name \
            or "contact" in type_name or "window" in type_name:
        return "contact"
    if "motion" in type_name or "presence" in keys or "motion" in keys:
        return "motion"
    if "Color" in item_types or type_name.startswith("hue:0") or any(word in type_name for word in LIGHT_WORDS):
        return "light"
    if "Dimmer" in item_types and dim_channel(channels):
        return "light"
    if any(word in type_name for word in PLUG_WORDS) or switch_channel(channels):
        return "switch"
    if "temperature" in keys or any(item_type.startswith("Number") for item_type in item_types):
        return "sensor"
    return "unknown"


def _energy_kwh(state: str | None) -> float | None:
    """Meter reading in kWh. A reading in Wh is converted ('1234 Wh' -> 1.234)."""
    value = number(state)
    if value is None:
        return None
    text = str(state).lower()
    if "kwh" not in text and ("wh" in text or "watt" in text):
        value = value / 1000
    return round(max(0.0, value), 3)


def _brightness(state: str | None) -> float | None:
    """HSB 'h,s,b' -> b; dimmer '57' -> 57."""
    if state is None:
        return None
    parts = state.split(",")
    return number(parts[-1]) if len(parts) == 3 else number(state)


def _channels(thing: dict[str, Any], items_by_name: dict[Any, dict[str, Any]]) -> list[Channel]:
    """Return the channels of a thing with the state of the item each one is linked to."""
    channels = []
    for raw in thing.get("channels") or []:
        linked = raw.get("linkedItems") or []
        item = items_by_name.get(linked[0]) if linked else None
        channels.append(Channel(id=raw.get("id") or "", label=raw.get("label") or raw.get("id") or "",
                                item_type=raw.get("itemType") or (item or {}).get("type") or "",
                                item=linked[0] if linked else None, state=known((item or {}).get("state"))))
    return channels


def _first_state(channels: list[Channel], matches: Callable[[Channel], bool]) -> str | None:
    """State of the first channel with a known state that ``matches``."""
    return next((channel.state for channel in channels if channel.state is not None and matches(channel)), None)


def _state_of(channels: list[Channel], *names: str) -> str | None:
    """State of the first channel with one of the ``names`` that has a known state."""
    by_key = {channel.key: channel for channel in channels}
    for name in names:
        channel = by_key.get(name.lower())
        if channel is not None and channel.state is not None:
            return channel.state
    return None


def _details(source: dict[str, Any]) -> dict[str, str]:
    """Technical facts for the device list: serial number, firmware …"""
    details = {}
    for label, keys in DETAIL_FIELDS:
        value = next((source[key] for key in keys if source.get(key) not in (None, "")), None)
        if value is not None and isinstance(value, (str, int, float)):
            details[label] = str(value)[:80]
    return details


def _problems(channels: list[Channel]) -> tuple[list[str], bool]:
    """Faults and alarms from the alertState channels: (messages, smoke alarm)."""
    problems, alarm = [], False
    for channel in channels:
        if not channel.key.startswith("alertstate") or str(channel.state).upper() not in ("ON", "OPEN", "TRUE"):
            continue
        if "smoke" in channel.key or "fire" in channel.key:
            alarm = True
        elif "dooropenclose" not in channel.key and "window" not in channel.key:
            code = channel.id.split("-")[-1].split("_")[-1]          # e.g. alertState-E1 -> E1
            problems.append(f"Gerätefehler {code}" if len(code) <= 4 else f"Fehler: {channel.label or channel.id}")
    return problems, alarm


def _kind_state(kind: str, channels: list[Channel]) -> tuple[dict[str, Any], str | None]:
    """Values and control item that depend on the device kind."""
    state: dict[str, Any] = {}
    control_item = None
    if kind == "thermostat":
        setpoint = next((channel for channel in channels
                         if channel.item and any(word in channel.key for word in SETPOINT_WORDS)), None)
        state[STATE_TARGET] = _rounded(number(setpoint.state)) if setpoint else None
        control_item = setpoint.item if setpoint else None
        valve = valve_percent(valve_channel(channels))
        if valve is not None:
            state["valve"] = valve                                   # valve opening in % (display and history only)
    elif kind == "contact":
        contact = _first_state(channels, lambda channel: channel.item_type.startswith("Contact"))
        state[STATE_OPEN] = None if contact is None else contact.upper() == "OPEN"
    elif kind == "motion":
        motion = _state_of(channels, "presence", "motion")
        state[STATE_MOTION] = None if motion is None else motion.upper() == "ON"
    elif kind == "cover":
        cover = cover_channel(channels)
        control_item = cover.item if cover else None
        position = number(cover.state) if cover and cover.state is not None else None
        state[STATE_POSITION] = None if position is None else int(max(0, min(100, position)))   # 0 = open, 100 = closed
    elif kind in ("light", "switch"):
        switch, dimmer = switch_channel(channels), dim_channel(channels)
        control = switch or dimmer
        control_item = control.item if control else None
        switched = switch.state if switch else None
        brightness = _brightness(dimmer.state) if dimmer else None
        if switched is not None:
            state[STATE_ON] = switched.upper() == "ON"
        elif brightness is not None:
            state[STATE_ON] = brightness > 0
        else:
            state[STATE_ON] = None
        state[STATE_BRIGHTNESS] = None if brightness is None else int(brightness)
    return state, control_item


def build_devices(things: list[dict[str, Any]], items: list[dict[str, Any]],
                  qivicon_devices: list[dict[str, Any]] | None = None) -> list[HomeBaseDevice]:
    """Combine the three REST answers into the list of devices. Bridges and coordinators are left out."""
    items_by_name = {item.get("name"): item for item in items if isinstance(item, dict)}
    extra_by_id = {entry.get("id"): entry for entry in (qivicon_devices or []) if isinstance(entry, dict)}
    devices = []
    for thing in things:
        if not isinstance(thing, dict):
            continue
        uid = thing.get("UID") or thing.get("uid")
        if not uid:
            continue
        extra = extra_by_id.get(uid, {})
        type_id = thing.get("thingTypeUID") or extra.get("thingTypeId") or ""
        if extra.get("isBridge") or "bridge" in type_id.lower() or "coordinator" in type_id.lower():
            continue                  # infrastructure (DECT/Hue bridge, ZigBee coordinator), not a device
        channels = _channels(thing, items_by_name)
        kind = classify(type_id, channels)
        status_info, device_status = thing.get("statusInfo") or {}, extra.get("deviceStatus") or {}
        status = status_info.get("status") or device_status.get("status")
        status_code = device_status.get("statusDetail") or status_info.get("statusDetail")
        condition = extra.get("condition") or {}
        battery = condition.get("batteryLevel")
        if battery is None:
            battery = number(_state_of(channels, "battery_level", "batteryLevel", "battery"))
        room_id = thing.get("location") or extra.get("room") or None
        state, control_item = _kind_state(kind, channels)
        state[STATE_TEMPERATURE] = _rounded(number(_state_of(channels, "temperature")))
        # Read only – humidity of room thermostats and climate sensors, ambient light of motion detectors (since 0.23)
        humidity = number(_state_of(channels, "humidity", "relativeHumidity"))
        if humidity is not None and 0 <= humidity <= 100:
            state[STATE_HUMIDITY] = round(humidity)
        illuminance = number(_state_of(channels, "illuminance", "luminance"))
        if illuminance is not None and illuminance >= 0:
            state[STATE_ILLUMINANCE] = round(illuminance, 1)
        power = _rounded(number(_first_state(channels, lambda channel: channel.item_type.startswith("Number")
                                             and "power" in channel.key
                                             and not any(word in channel.key for word in POWER_SKIP_WORDS))))
        energy = _energy_kwh(_first_state(channels, lambda channel: channel.item_type.startswith("Number")
                                          and any(word in channel.key for word in ENERGY_WORDS)
                                          and "power" not in channel.key))
        problems, alarm = _problems(channels)
        low_battery = condition.get("lowBattery")
        if str(_state_of(channels, "batteryLow", "lowBattery", "battery_low") or "").upper() == "ON":
            low_battery = True
        if kind == "smoke" or alarm:
            state[STATE_ALARM] = alarm
        devices.append(HomeBaseDevice(
            id=uid, name=thing.get("label") or extra.get("label") or uid, kind=kind, online=status == "ONLINE",
            type_id=type_id, room_id=room_id, room_name=(items_by_name.get(room_id) or {}).get("label") if room_id else None,
            status=status, status_code=status_code,
            status_text=device_status.get("statusDescription") or status_info.get("description")
            or (GONE_TEXT if status_code == "GONE" else None),
            vendor=(thing.get("properties") or {}).get("vendor") or (extra.get("detail") or {}).get("vendor"),
            battery=None if battery is None else int(battery), low_battery=low_battery,
            last_seen=condition.get("lastCommunicationSuccess"), control_item=control_item, state=state,
            power_w=None if power is None else max(0.0, power), energy_kwh=energy, problems=problems,
            details=_details({**(extra.get("detail") or {}), **(thing.get("properties") or {})}),
            readings=[Reading(channel.id, channel.label, channel.state)
                      for channel in channels if channel.state is not None][:MAX_READINGS]))
    return devices


def link_keys(device: HomeBaseDevice) -> list[str]:
    """Keys under which another plugin may know the same physical device (see HUE_LIGHT_ID)."""
    match = HUE_LIGHT_ID.match(device.id)
    if match:
        return [f"hue:{match.group(2).lower()}:light:{match.group(3)}"]
    match = HUE_SENSOR_ID.match(device.id)
    if match and device.kind == "motion":
        return [f"zigbee:{match.group(2).lower()}"]
    match = SONOS_ID.match(device.id)
    if match:
        return [f"sonos:{match.group(1).upper()}"]
    return []


def sonos_speakers(things: list[Any]) -> list[dict[str, Any]]:
    """Sonos speakers among the things of the Home Base, as hints for the Sonos plugin.

    Each hint carries the speaker's own id and, if the Home Base stores one, its address.
    """
    speakers = []
    for thing in things:
        match = SONOS_ID.match(str(thing.get("UID") or "")) if isinstance(thing, dict) else None
        if match:
            configuration = thing.get("configuration") or {}
            speakers.append({"uuid": match.group(1).upper(), "host": configuration.get("ipAddress") or None})
    return speakers


def capabilities(device: HomeBaseDevice) -> list[Capability]:
    """What the device accepts as a command. Without a control item it accepts nothing."""
    if not device.control_item:
        return []
    if device.kind in ("light", "switch"):
        return [Capability(STATE_ON, "bool", writable=True)]
    if device.kind == "thermostat":
        return [Capability(STATE_TARGET, "number", unit="°C", minimum=5.0, maximum=30.0, step=0.5, writable=True)]
    if device.kind == "cover":
        return [Capability(STATE_POSITION, "number", unit="%", minimum=0.0, maximum=100.0, step=1.0, writable=True)]
    return []


def to_device(device: HomeBaseDevice) -> Device:
    """Convert to the device type of the plugin API.

    The thing UID stays the device id (floor plan, flows and scenes point to it). The status
    of the Home Base travels in the state as ``hb_status`` / ``hb_detail`` for the plugin's pages.
    """
    keys = link_keys(device)
    state = {**device.state, "hb_status": device.status, "hb_detail": device.status_code}
    return Device(id=device.id, name=device.name, kind=device.kind, online=device.online, state=state,
                  capabilities=capabilities(device), room_hint=device.room_name, room_id=device.room_id,
                  vendor=device.vendor, type_name=device.type_id, battery=device.battery, low_battery=device.low_battery,
                  last_seen=device.last_seen, power_w=device.power_w, energy_kwh=device.energy_kwh,
                  status_detail=device.status_text, problems=list(device.problems), readings=list(device.readings),
                  link_keys=keys, relayed=bool(keys), details=dict(device.details))
