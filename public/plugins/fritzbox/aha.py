"""The smart home interface of a FRITZ!Box (AVM "AHA-HTTP-Interface"): login, device list, commands. No network access.

Login (``/login_sid.lua?version=2``)
    The box sends a challenge; the answer proves the password without sending it. FRITZ!OS from 7.24 uses
    PBKDF2 (challenge ``2$<iterations 1>$<salt 1>$<iterations 2>$<salt 2>``), older versions MD5 over UTF-16LE.
    The answer is a session id (SID) of 16 hex digits; ``0000000000000000`` means "refused". After failed
    attempts the box itself blocks further ones for ``BlockTime`` seconds.

Device list (``/webservices/homeautoswitch.lua?switchcmd=getdevicelistinfos&sid=…``)
    One XML document with every smart home device. ``functionbitmask`` says what a device can do (bits below),
    the child elements carry the values: ``switch``, ``powermeter``, ``temperature``, ``hkr`` (radiator),
    ``levelcontrol``, ``colorcontrol``, ``alert``, ``button``, ``humidity``, ``blind``. Temperatures come in tenths
    of a degree (``celsius``) or in half degrees (``hkr``), power in milliwatts, energy in watt hours.

Commands (same address, ``switchcmd=…&ain=…``): ``setswitchon``/``setswitchoff``, ``setsimpleonoff``,
``setlevelpercentage``, ``sethkrtsoll``, ``setcolortemperature``, ``setunmappedcolor``, ``setblind``.

Answers are foreign input: documents are limited in size, documents with a DOCTYPE are refused and every value is
read as text and checked. Written after AVM's documentation of the interface – details are listed as not checked
on a real box in the README.
"""
from __future__ import annotations

import colorsys
import hashlib
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import Any, Mapping

from plugin_api import (
    STATE_ALARM,
    STATE_BRIGHTNESS,
    STATE_HUMIDITY,
    STATE_ON,
    STATE_OPEN,
    STATE_POSITION,
    STATE_TARGET,
    STATE_TEMPERATURE,
    hex_to_rgb,
    mirek_to_hex,
)

MAX_XML_LENGTH = 2_000_000
MAX_DEVICES = 300
MAX_BUTTONS = 8
NO_SESSION = "0000000000000000"

# Bits of "functionbitmask" (AVM documentation of the AHA-HTTP-Interface)
BIT_HANFUN_DEVICE = 1 << 0
BIT_LIGHT = 1 << 2
BIT_ALARM = 1 << 4
BIT_BUTTON = 1 << 5
BIT_RADIATOR = 1 << 6
BIT_ENERGY_METER = 1 << 7
BIT_TEMPERATURE = 1 << 8
BIT_SOCKET = 1 << 9
BIT_REPEATER = 1 << 10
BIT_HANFUN_UNIT = 1 << 13
BIT_ON_OFF = 1 << 15
BIT_LEVEL = 1 << 16
BIT_COLOR = 1 << 17
BIT_BLIND = 1 << 18
BIT_HUMIDITY = 1 << 20

RADIATOR_OFF = 253                  # "hkr" set point: off (frost protection only)
RADIATOR_ON = 254                   # "hkr" set point: fully open
MIN_TARGET, MAX_TARGET = 8.0, 28.0  # °C the box accepts as a set point
MIN_KELVIN, MAX_KELVIN = 2700, 6500

_SID = re.compile(r"^[0-9a-f]{16}$")
_HEX = re.compile(r"^[0-9a-f]+$")
_AIN = re.compile(r"^[0-9A-Za-z: _-]{1,40}$")


class AhaError(Exception):
    """An answer of the box that cannot be read. The message names the reason (for the log, never shown raw)."""


# ---------------------------------------------------------------------------
# Reading XML
# ---------------------------------------------------------------------------

def parse_xml(text: str) -> ElementTree.Element:
    """Parse a document of the box. Raises ``AhaError`` when it is too large, declares a DOCTYPE or is not XML."""
    if len(text) > MAX_XML_LENGTH:
        raise AhaError("document too large")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise AhaError("document declares a DOCTYPE")
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise AhaError("not XML") from error


def _text(element: ElementTree.Element | None, path: str) -> str | None:
    """Text of a child element, stripped; None when it is missing or empty."""
    found = element.find(path) if element is not None else None
    value = (found.text or "").strip() if found is not None else ""
    return value or None


def _int(element: ElementTree.Element | None, path: str) -> int | None:
    """Whole number of a child element; None when it is missing or not a number."""
    value = _text(element, path)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _clip(value: Any, limit: int = 80) -> str:
    """Foreign text: printable characters only, single spaces, shortened."""
    return " ".join("".join(char for char in str(value or "") if char.isprintable()).split())[:limit]


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionInfo:
    """What ``login_sid.lua`` answers."""

    sid: str                                    # NO_SESSION = not signed in
    challenge: str
    block_seconds: int                          # the box refuses further attempts for this long
    last_user: str | None                       # user the box offers when none is named


def parse_session(text: str) -> SessionInfo:
    """Read the answer of ``login_sid.lua``. Raises ``AhaError`` when it is not one."""
    root = parse_xml(text)
    if root.tag != "SessionInfo":
        raise AhaError("no SessionInfo")
    sid = (_text(root, "SID") or "").lower()
    users = [user for user in root.iter("User") if (user.text or "").strip()]
    last = next((user for user in users if user.get("last") == "1"), users[0] if users else None)
    return SessionInfo(sid=sid if _SID.match(sid) else NO_SESSION, challenge=_clip(_text(root, "Challenge"), 200),
                       block_seconds=max(0, min(3600, _int(root, "BlockTime") or 0)),
                       last_user=_clip(last.text, 60) if last is not None else None)


def challenge_response(challenge: str, password: str) -> str:
    """Answer to a challenge: PBKDF2 for ``2$…`` (FRITZ!OS 7.24 and later), otherwise MD5 as older versions expect.
    Raises ``AhaError`` for a challenge that cannot be read."""
    if challenge.startswith("2$"):
        parts = challenge.split("$")
        if len(parts) != 5 or not all(_HEX.match(part.lower()) for part in (parts[2], parts[4])):
            raise AhaError("unreadable PBKDF2 challenge")
        try:
            first_rounds, second_rounds = int(parts[1]), int(parts[3])
        except ValueError as error:
            raise AhaError("unreadable PBKDF2 challenge") from error
        if not (0 < first_rounds <= 1_000_000 and 0 < second_rounds <= 1_000_000):
            raise AhaError("implausible PBKDF2 challenge")
        first = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(parts[2]), first_rounds)
        second = hashlib.pbkdf2_hmac("sha256", first, bytes.fromhex(parts[4]), second_rounds)
        return f"{parts[4]}${second.hex()}"
    if not challenge or len(challenge) > 64:
        raise AhaError("unreadable challenge")
    # Old method: characters above 255 are replaced by "." first (AVM's rule)
    text = f"{challenge}-" + "".join(char if ord(char) < 256 else "." for char in password)
    return f"{challenge}-{hashlib.md5(text.encode('utf-16-le')).hexdigest()}"


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AhaButton:
    """One button of a FRITZ!DECT 400/440 or a HAN-FUN switch."""

    id: str                                     # "id" attribute of the box, digits
    name: str
    last_pressed: int | None                    # Unix time of the last press as the box reports it


@dataclass
class AhaDevice:
    """One smart home device of the box, translated into ComfyOS terms."""

    ain: str                                    # "actor identification number", e.g. "11657 0272633"
    name: str
    kind: str                                   # one of plugin_api.DEVICE_KINDS
    present: bool
    product: str | None = None
    manufacturer: str | None = None
    firmware: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    battery: int | None = None
    low_battery: bool | None = None
    power_w: float | None = None
    energy_kwh: float | None = None
    buttons: list[AhaButton] = field(default_factory=list)
    mask: int = 0
    kelvin: int | None = None                   # white tone of a lamp
    color: tuple[int, int] | None = None        # (hue 0–359, saturation 0–255) of a colour lamp
    window_open: bool | None = None             # radiator: open window detected

    @property
    def can_switch(self) -> bool:
        """The device has an on/off switch (socket, lamp, plug)."""
        return bool(self.mask & (BIT_SOCKET | BIT_ON_OFF))

    @property
    def is_socket(self) -> bool:
        """A FRITZ!DECT socket (switched with setswitchon/off)."""
        return bool(self.mask & BIT_SOCKET)


def _kind(mask: int, has_buttons: bool) -> str:
    """Device kind by what it can do – the most important function decides."""
    if mask & BIT_BLIND:
        return "cover"
    if mask & BIT_RADIATOR:
        return "thermostat"
    if mask & (BIT_LIGHT | BIT_LEVEL | BIT_COLOR):
        return "light"
    if mask & (BIT_SOCKET | BIT_ON_OFF):
        return "switch"
    if mask & BIT_ALARM:
        return "contact"
    if has_buttons:
        return "button"
    if mask & (BIT_TEMPERATURE | BIT_HUMIDITY):
        return "sensor"
    return "unknown"


def _half_degrees(value: int | None) -> float | None:
    """Temperature of the "hkr" block (half degrees); None for "off"/"on" and anything out of range."""
    if value is None or value in (RADIATOR_OFF, RADIATOR_ON) or not 0 <= value <= 120:
        return None
    return value / 2


def _device(element: ElementTree.Element) -> AhaDevice | None:
    """Translate one ``<device>`` element; None for what is no device of its own (a HAN-FUN device without unit)."""
    ain = _clip(element.get("identifier"), 40)
    try:
        mask = int(element.get("functionbitmask") or 0)
    except ValueError:
        return None
    if not ain or not _AIN.match(ain):
        return None
    if mask & BIT_HANFUN_DEVICE and not mask & ~(BIT_HANFUN_DEVICE | BIT_TEMPERATURE):
        return None                     # a HAN-FUN device: its functions are listed as units (entries of their own)
    buttons = [AhaButton(id=_clip(button.get("id"), 12), name=_clip(_text(button, "name") or "Taste", 60),
                         last_pressed=_int(button, "lastpressedtimestamp"))
               for button in element.findall("button")[:MAX_BUTTONS] if (button.get("id") or "").isdigit()]
    device = AhaDevice(ain=ain, name=_clip(_text(element, "name") or ain), kind=_kind(mask, bool(buttons)),
                       present=_text(element, "present") == "1", product=_clip(element.get("productname")) or None,
                       manufacturer=_clip(element.get("manufacturer")) or None, firmware=_clip(element.get("fwversion"), 20) or None,
                       buttons=buttons, mask=mask)
    _read_values(element, device)
    return device


def _read_values(element: ElementTree.Element, device: AhaDevice) -> None:
    """Fill in the values the child elements carry."""
    state = device.state
    switch, simple = element.find("switch"), element.find("simpleonoff")
    on = _text(switch, "state") if switch is not None else _text(simple, "state")
    if on in ("0", "1"):
        state[STATE_ON] = on == "1"
    meter = element.find("powermeter")
    if meter is not None:
        milliwatts, watt_hours = _int(meter, "power"), _int(meter, "energy")
        device.power_w = round(milliwatts / 1000, 1) if milliwatts is not None and milliwatts >= 0 else None
        device.energy_kwh = round(watt_hours / 1000, 3) if watt_hours is not None and watt_hours >= 0 else None
    tenths = _int(element.find("temperature"), "celsius")
    if tenths is not None and -400 <= tenths <= 1000:
        state[STATE_TEMPERATURE] = tenths / 10
    radiator = element.find("hkr")
    if radiator is not None:
        measured, target = _half_degrees(_int(radiator, "tist")), _int(radiator, "tsoll")
        if measured is not None:
            state[STATE_TEMPERATURE] = measured
        state[STATE_TARGET] = _half_degrees(target) if target not in (RADIATOR_OFF, RADIATOR_ON) else (
            MIN_TARGET if target == RADIATOR_OFF else MAX_TARGET)
        device.battery = _int(radiator, "battery")
        device.low_battery = _text(radiator, "batterylow") == "1"
        device.window_open = _text(radiator, "windowopenactiv") == "1"
    level = element.find("levelcontrol")
    percent = _int(level, "levelpercentage")
    if percent is not None and 0 <= percent <= 100:
        state[STATE_POSITION if device.kind == "cover" else STATE_BRIGHTNESS] = percent
    color = element.find("colorcontrol")
    if color is not None:
        kelvin, hue, saturation = _int(color, "temperature"), _int(color, "hue"), _int(color, "saturation")
        device.kelvin = kelvin if kelvin is not None and 1000 <= kelvin <= 10000 else None
        if color.get("current_mode") == "1" and hue is not None and saturation is not None:
            device.color = (hue % 360, max(0, min(255, saturation)))
    alert = _text(element.find("alert"), "state")
    if alert in ("0", "1") and device.kind == "contact":
        state[STATE_OPEN] = alert == "1"
    elif alert in ("0", "1"):
        state[STATE_ALARM] = alert == "1"
    humidity = _int(element.find("humidity"), "rel_humidity")
    if humidity is not None and 0 <= humidity <= 100:
        state[STATE_HUMIDITY] = humidity
    if device.battery is None:
        device.battery = _int(element, "battery")
        if _text(element, "batterylow") is not None:
            device.low_battery = _text(element, "batterylow") == "1"
    if device.battery is not None and not 0 <= device.battery <= 100:
        device.battery = None


def parse_devices(text: str) -> list[AhaDevice]:
    """Read the answer of ``getdevicelistinfos``. Groups are left out (their members are listed anyway).
    Raises ``AhaError`` when it is no device list."""
    root = parse_xml(text)
    if root.tag != "devicelist":
        raise AhaError("no devicelist")
    devices = [_device(element) for element in root.findall("device")[:MAX_DEVICES]]
    return [device for device in devices if device is not None]


def new_presses(before: Mapping[str, int | None], devices: list[AhaDevice]) -> list[tuple[str, str]]:
    """Buttons pressed since the last list: (ain, button id) whose ``lastpressedtimestamp`` grew. A button seen for
    the first time is no press – after a start nothing is reported twice."""
    pressed = []
    for device in devices:
        for button in device.buttons:
            key = f"{device.ain}/{button.id}"
            old = before.get(key)
            if key in before and button.last_pressed is not None and (old is None or button.last_pressed > old):
                pressed.append((device.ain, button.id))
    return pressed


def press_times(devices: list[AhaDevice]) -> dict[str, int | None]:
    """``lastpressedtimestamp`` of every button, by "<ain>/<button id>" – the memory for ``new_presses``."""
    return {f"{device.ain}/{button.id}": button.last_pressed for device in devices for button in device.buttons}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

Command = tuple[str, dict[str, str]]           # (switchcmd, further parameters)


def _number(value: Any) -> float | None:
    """A number from a command; None for anything else."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value else None


def commands(device: AhaDevice, wanted: Mapping[str, Any]) -> list[Command]:
    """The commands that bring the device to the wanted values. Raises ``ValueError`` (German text for the user)
    when the device cannot do what is asked."""
    out: list[Command] = []
    if device.kind == "cover":
        position = _number(wanted.get(STATE_POSITION))
        if position is None:
            raise ValueError(f"{device.name} kennt nur eine Position.")
        position = round(max(0.0, min(100.0, position)))
        if position <= 0:
            return [("setblind", {"target": "open"})]
        if position >= 100:
            return [("setblind", {"target": "close"})]
        return [("setlevelpercentage", {"level": str(position)})]
    if device.kind == "thermostat":
        target = _number(wanted.get(STATE_TARGET))
        if target is None:
            raise ValueError(f"{device.name} kennt nur eine Solltemperatur.")
        target = max(MIN_TARGET, min(MAX_TARGET, round(target * 2) / 2))
        return [("sethkrtsoll", {"param": str(int(target * 2))})]
    if wanted.get(STATE_ON) is not None and device.can_switch:
        if device.is_socket:
            out.append(("setswitchon" if wanted[STATE_ON] else "setswitchoff", {}))
        else:
            out.append(("setsimpleonoff", {"onoff": "1" if wanted[STATE_ON] else "0"}))
    brightness = _number(wanted.get(STATE_BRIGHTNESS))
    if brightness is not None:
        if not device.mask & BIT_LEVEL:
            raise ValueError(f"{device.name} lässt sich nicht dimmen.")
        out.append(("setlevelpercentage", {"level": str(round(max(0.0, min(100.0, brightness))))}))
    out += _light_commands(device, wanted)
    if not out:
        raise ValueError(f"{device.name} lässt sich so nicht schalten.")
    return out


def _light_commands(device: AhaDevice, wanted: Mapping[str, Any]) -> list[Command]:
    """White tone and colour of a FRITZ!DECT lamp."""
    kelvin = _number(wanted.get("kelvin"))
    mirek = _number(wanted.get("mirek"))
    if kelvin is None and mirek is not None and mirek > 0:
        kelvin = 1_000_000 / mirek
    color = wanted.get("color")
    if (kelvin is not None or color) and not device.mask & BIT_COLOR:
        raise ValueError(f"{device.name} kennt keine Farben und Weißtöne.")
    out: list[Command] = []
    if kelvin is not None:
        out.append(("setcolortemperature", {"temperature": str(int(max(MIN_KELVIN, min(MAX_KELVIN, kelvin)))),
                                            "duration": "0"}))
    if isinstance(color, str) and color:
        try:
            red, green, blue = hex_to_rgb(color)
        except ValueError as error:
            raise ValueError("Farbe muss als #rrggbb angegeben sein.") from error
        hue, saturation, _ = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        out.append(("setunmappedcolor", {"hue": str(round(hue * 359)), "saturation": str(round(saturation * 255)),
                                         "duration": "0"}))
    return out


def color_hex(device: AhaDevice) -> str | None:
    """The colour a lamp shows as #rrggbb (for the light state): its colour, otherwise its white tone."""
    if device.color is not None:
        hue, saturation = device.color
        red, green, blue = colorsys.hsv_to_rgb(hue / 360, saturation / 255, 1.0)
        return "#" + "".join(f"{round(channel * 255):02x}" for channel in (red, green, blue))
    if device.kelvin:
        return mirek_to_hex(1_000_000 / device.kelvin)
    return None
