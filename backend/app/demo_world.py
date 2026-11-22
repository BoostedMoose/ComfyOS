"""The demo home (since 0.23): sample devices, floor plan, scenes, a heating plan and flows for the demo (demo.py).

The devices come from `DemoPlugin`, a plugin of the core that only a session in the demo sees (plugins._registry).
It lives in memory: switching changes its state at once, temperatures move towards their set point, the motion
detector reports now and then, the ambient light follows the time of day. Nothing of it reaches a real device or
the internet. `seed` writes the starting point of the demo – it runs inside the demo, so the stores write to
data/demo/.

Since 0.24.1 the demo is a four-room flat with seven rooms (ROOMS): an L-shaped living room with a bay window, an
open kitchen, hallway, bath, bedroom, children's room and study – so that the floor plan, the heat map and the
weather around the house show what they can (also for the README pictures, tools/readme_screenshot.py). Since 0.25
it has a balcony in front of the living room and a second floor: an attic studio with a roof terrace. The devices
report their rooms like a hub (Device.room_id) and the floor plan names them. All names are sample data – no real
home is behind them.
"""
from __future__ import annotations

import math
import time
from typing import Any, Mapping

from .plugin_api import (DEFAULT_WHITE_RANGE, PRESS_SHORT, STATE_ALARM, STATE_BRIGHTNESS, STATE_HUMIDITY, STATE_ILLUMINANCE,
                         STATE_MOTION, STATE_MUTED, STATE_ON, STATE_OPEN, STATE_PLAYING, STATE_POSITION, STATE_PRESENT,
                         STATE_TARGET, STATE_TEMPERATURE, STATE_VOLUME, Button, Capability, Device, LightCapabilities,
                         LightState, MediaState, Plugin, PluginError, hex_to_xy, kelvin_to_mirek)

PLUGIN_ID = "demo"
NAME = "Demo-Wohnung"
DESCRIPTION = "Beispielgeräte der Demo – nur in der Demo sichtbar, ohne echte Geräte und ohne Internet."
ID_PREFIX = "demo:"
ROOMS = {"wohnen": "Wohnzimmer", "kueche": "Küche", "schlafen": "Schlafzimmer", "kind": "Kinderzimmer",
         "arbeit": "Arbeitszimmer", "bad": "Bad", "flur": "Flur", "studio": "Dachstudio"}
MOTION_EVERY = 180            # s – the motion detector reports motion at the start of every such window …
MOTION_FOR = 25               # s – … for this long
FAVORITES = ("Radio Musterstadt", "Jazz am Abend", "Nachrichten")

# Starting values per device. A light carries its light state, a thermostat its set point and so on.
_START: dict[str, dict[str, Any]] = {
    "decke": {"name": "Deckenlicht", "kind": "light", "room": "wohnen", "on": True, "brightness": 70,
              "light": "color", "mirek": 370},
    "strip": {"name": "Lichtstreifen", "kind": "light", "room": "wohnen", "on": True, "brightness": 45,
              "light": "color", "xy": [0.5, 0.38]},
    "stehlampe": {"name": "Stehlampe", "kind": "light", "room": "wohnen", "on": False, "brightness": 60,
                  "light": "white", "mirek": 400},
    "esstisch": {"name": "Pendelleuchte Esstisch", "kind": "light", "room": "wohnen", "on": True, "brightness": 55,
                 "light": "color", "xy": [0.46, 0.41]},
    "kueche": {"name": "Küchenlicht", "kind": "light", "room": "kueche", "on": False, "brightness": 100,
               "light": "white", "mirek": 250},
    "nachttisch": {"name": "Nachttisch", "kind": "light", "room": "schlafen", "on": False, "brightness": 30,
                   "light": "white", "mirek": 450},
    "nachtlicht": {"name": "Nachtlicht", "kind": "light", "room": "kind", "on": True, "brightness": 20,
                   "light": "color", "xy": [0.38, 0.22]},
    "schreibtisch": {"name": "Schreibtischlampe", "kind": "light", "room": "arbeit", "on": True, "brightness": 85,
                     "light": "white", "mirek": 230},
    "spiegel": {"name": "Spiegelleuchte", "kind": "light", "room": "bad", "on": False, "brightness": 90,
                "light": "white", "mirek": 280},
    "flurlicht": {"name": "Flurlicht", "kind": "light", "room": "flur", "on": False, "brightness": 50,
                  "light": "white", "mirek": 370},
    "tv": {"name": "Stecker TV", "kind": "switch", "room": "wohnen", "on": True, "watts": 84.0, "kwh": 31.4},
    "kaffee": {"name": "Kaffeemaschine", "kind": "switch", "room": "kueche", "on": False, "watts": 0.0, "kwh": 12.8},
    "pc": {"name": "Stecker Schreibtisch", "kind": "switch", "room": "arbeit", "on": True, "watts": 46.0, "kwh": 58.2},
    "waschmaschine": {"name": "Waschmaschine", "kind": "switch", "room": "bad", "on": True, "watts": 380.0, "kwh": 96.5},
    "heizung-wohnen": {"name": "Heizung Wohnzimmer", "kind": "thermostat", "room": "wohnen", "temperature": 21.1,
                       "target": 21.5},
    "heizung-kueche": {"name": "Heizung Küche", "kind": "thermostat", "room": "kueche", "temperature": 18.4,
                       "target": 19.0},
    "heizung-schlafen": {"name": "Heizung Schlafzimmer", "kind": "thermostat", "room": "schlafen", "temperature": 17.2,
                         "target": 17.0},
    "heizung-kind": {"name": "Heizung Kinderzimmer", "kind": "thermostat", "room": "kind", "temperature": 20.2,
                     "target": 20.5},
    "heizung-arbeit": {"name": "Heizung Arbeitszimmer", "kind": "thermostat", "room": "arbeit", "temperature": 19.6,
                       "target": 20.5},
    "heizung-bad": {"name": "Heizung Bad", "kind": "thermostat", "room": "bad", "temperature": 22.6, "target": 23.0},
    "fenster-wohnen": {"name": "Fenster Wohnzimmer", "kind": "contact", "room": "wohnen", "open": False},
    "fenster-kueche": {"name": "Fenster Küche", "kind": "contact", "room": "kueche", "open": True},
    "fenster-schlafen": {"name": "Fenster Schlafzimmer", "kind": "contact", "room": "schlafen", "open": False},
    "fenster-kind": {"name": "Fenster Kinderzimmer", "kind": "contact", "room": "kind", "open": False},
    "fenster-arbeit": {"name": "Fenster Arbeitszimmer", "kind": "contact", "room": "arbeit", "open": False},
    "fenster-bad": {"name": "Fenster Bad", "kind": "contact", "room": "bad", "open": False},
    "haustuer": {"name": "Haustür", "kind": "contact", "room": "flur", "open": False},
    "bewegung": {"name": "Bewegung Küche", "kind": "motion", "room": "kueche", "temperature": 19.3, "battery": 86},
    "bewegung-flur": {"name": "Bewegung Flur", "kind": "motion", "room": "flur", "temperature": 18.6, "battery": 12},
    "klima": {"name": "Raumklima Schlafzimmer", "kind": "sensor", "room": "schlafen", "temperature": 17.4,
              "humidity": 52, "battery": 64},
    "klima-bad": {"name": "Raumklima Bad", "kind": "sensor", "room": "bad", "temperature": 22.4, "humidity": 68,
                  "battery": 71},
    "rauchmelder": {"name": "Rauchmelder Flur", "kind": "smoke", "room": "flur", "alarm": False, "battery": 91},
    "rauchmelder-kind": {"name": "Rauchmelder Kinderzimmer", "kind": "smoke", "room": "kind", "alarm": False,
                         "battery": 88},
    "rollladen": {"name": "Rollladen Schlafzimmer", "kind": "cover", "room": "schlafen", "position": 0},
    "rollladen-wohnen": {"name": "Rollladen Wohnzimmer", "kind": "cover", "room": "wohnen", "position": 35},
    "lautsprecher": {"name": "Lautsprecher Wohnzimmer", "kind": "media", "room": "wohnen", "playing": True,
                     "volume": 18, "muted": False, "favorite": FAVORITES[0]},
    "lautsprecher-kueche": {"name": "Lautsprecher Küche", "kind": "media", "room": "kueche", "playing": False,
                            "volume": 25, "muted": False, "favorite": FAVORITES[2]},
    "taster": {"name": "Taster Wohnzimmer", "kind": "button", "room": "wohnen", "battery": 78},
    "studio-licht": {"name": "Deckenlicht Studio", "kind": "light", "room": "studio", "on": False, "brightness": 65,
                     "light": "white", "mirek": 330},
    "heizung-studio": {"name": "Heizung Studio", "kind": "thermostat", "room": "studio", "temperature": 19.1,
                       "target": 19.5},
    "fenster-studio": {"name": "Dachfenster", "kind": "contact", "room": "studio", "open": False},
    "handy": {"name": "Handy Alex", "kind": "presence", "room": None, "present": True},
    "handy-sam": {"name": "Handy Sam", "kind": "presence", "room": None, "present": False},
}
TASTER_BUTTONS = (Button("1", "An"), Button("2", "Heller"), Button("3", "Dunkler"), Button("4", "Aus"))

_state: dict[str, dict[str, Any]] = {}       # current values of the demo devices (memory only)


def reset_state() -> None:
    """Every demo device back to its starting value (demo.reset)."""
    _state.clear()
    for key, start in _START.items():
        _state[key] = dict(start)


def _device_state(key: str) -> dict[str, Any]:
    """Current values of one demo device (starting values on first use)."""
    if not _state:
        reset_state()
    return _state[key]


def _ambient_lux(now: float) -> float:
    """Ambient light of the demo by the time of day: dark at night, a few hundred lux around noon."""
    hour = time.localtime(now).tm_hour + time.localtime(now).tm_min / 60
    daylight = max(0.0, math.sin((hour - 6) / 13 * math.pi)) if 6 <= hour <= 19 else 0.0
    return round(2 + 640 * daylight)


def _light_state(values: Mapping[str, Any]) -> LightState:
    """Light state of a demo lamp: colour lamps know colour and white tones, the others only white tones."""
    color = values["light"] == "color"
    capabilities = LightCapabilities(white_range=DEFAULT_WHITE_RANGE, color=color,
                                     effects=["candle", "fire"] if color else [])
    if values.get("xy"):
        return LightState(capabilities=capabilities, mode="xy", xy=list(values["xy"]), effect=values.get("effect"))
    return LightState(capabilities=capabilities, mode="ct", mirek=int(values.get("mirek") or 370), effect=values.get("effect"))


class DemoPlugin(Plugin):
    """The devices of the demo home – only ever seen by a session in the demo."""

    def __init__(self, context: Any) -> None:
        """The demo needs nothing of the core (no network, no settings)."""
        super().__init__(context)

    def status(self) -> dict[str, Any]:
        """Always connected and live: the demo answers from memory."""
        return {"configured": True, "connected": True, "live": True, "message": "Beispielgeräte der Demo"}

    async def devices(self) -> list[Device]:
        """All demo devices with their current values; motion, temperatures and ambient light move with the time."""
        now = time.time()
        return [self._device(key, now) for key in _START]

    def _device(self, key: str, now: float) -> Device:
        """One demo device as a plugin device."""
        values = _device_state(key)
        kind = values["kind"]
        base = {"id": ID_PREFIX + key, "name": values["name"], "kind": kind, "room_id": values.get("room"),
                "room_hint": ROOMS.get(values.get("room") or ""), "vendor": "ComfyOS Demo", "model": "Beispielgerät",
                "battery": values.get("battery")}
        if kind == "light":
            return Device(**base, state={STATE_ON: values["on"], STATE_BRIGHTNESS: values["brightness"]},
                          capabilities=[Capability(STATE_ON, writable=True),
                                        Capability(STATE_BRIGHTNESS, "number", unit="%", minimum=0, maximum=100, writable=True)],
                          light=_light_state(values))
        if kind == "switch":
            watts = (values["watts"] or 0.0) if values["on"] else 0.0
            jitter = round(watts * 0.04 * math.sin(now / 37), 1) if watts else 0.0
            return Device(**base, state={STATE_ON: values["on"]}, capabilities=[Capability(STATE_ON, writable=True)],
                          power_w=round(watts + jitter, 1), energy_kwh=values["kwh"])
        if kind == "thermostat":
            return Device(**base, state={STATE_TEMPERATURE: self._drift(values, now), STATE_TARGET: values["target"]},
                          capabilities=[Capability(STATE_TEMPERATURE, "number", unit="°C"),
                                        Capability(STATE_TARGET, "number", unit="°C", minimum=5, maximum=30, step=0.5,
                                                   writable=True)])
        if kind == "contact":
            return Device(**base, state={STATE_OPEN: values["open"]}, capabilities=[Capability(STATE_OPEN)])
        if kind == "motion":
            moving = now % MOTION_EVERY < MOTION_FOR
            return Device(**base, state={STATE_MOTION: moving, STATE_TEMPERATURE: values["temperature"],
                                         STATE_ILLUMINANCE: _ambient_lux(now)},
                          capabilities=[Capability(STATE_MOTION), Capability(STATE_TEMPERATURE, "number", unit="°C"),
                                        Capability(STATE_ILLUMINANCE, "number", unit="lx")])
        if kind == "sensor":
            humidity = round(values["humidity"] + 3 * math.sin(now / 900))
            return Device(**base, state={STATE_TEMPERATURE: values["temperature"], STATE_HUMIDITY: humidity},
                          capabilities=[Capability(STATE_TEMPERATURE, "number", unit="°C"),
                                        Capability(STATE_HUMIDITY, "number", unit="%")])
        if kind == "smoke":
            return Device(**base, state={STATE_ALARM: values["alarm"]}, capabilities=[Capability(STATE_ALARM)])
        if kind == "cover":
            return Device(**base, state={STATE_POSITION: values["position"]},
                          capabilities=[Capability(STATE_POSITION, "number", unit="%", minimum=0, maximum=100, writable=True)])
        if kind == "media":
            media = MediaState(title=values["favorite"], source="Radio", favorites=list(FAVORITES))
            return Device(**base, state={STATE_PLAYING: values["playing"], STATE_VOLUME: values["volume"],
                                         STATE_MUTED: values["muted"]},
                          capabilities=[Capability(STATE_PLAYING, writable=True),
                                        Capability(STATE_VOLUME, "number", unit="%", minimum=0, maximum=100, writable=True),
                                        Capability(STATE_MUTED, writable=True),
                                        Capability("next", "action", readable=False, writable=True),
                                        Capability("previous", "action", readable=False, writable=True),
                                        Capability("favorite", "enum", options=FAVORITES, readable=False, writable=True)],
                          media=media)
        if kind == "button":
            return Device(**base, buttons=list(TASTER_BUTTONS))
        return Device(**base, state={STATE_PRESENT: values["present"]}, capabilities=[Capability(STATE_PRESENT)])

    @staticmethod
    def _drift(values: dict[str, Any], now: float) -> float:
        """Room temperature of a thermostat: moves a little towards the set point with every query."""
        last = values.get("_at") or now
        step = max(-0.3, min(0.3, (values["target"] - values["temperature"]) * min(1.0, (now - last) / 1800)))
        values["temperature"], values["_at"] = round(values["temperature"] + step, 2), now
        return round(values["temperature"], 1)

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
        """Switch a demo device in memory and return its new values."""
        key = device_id[len(ID_PREFIX):] if device_id.startswith(ID_PREFIX) else ""
        if key not in _START:
            raise PluginError("Dieses Demo-Gerät gibt es nicht.")
        values = _device_state(key)
        kind = values["kind"]
        if kind == "light":
            return self._light_command(values, state)
        if kind == "switch" and STATE_ON in state:
            values["on"] = bool(state[STATE_ON])
            if key == "kaffee":
                values["watts"] = 1150.0 if values["on"] else 0.0
            return {STATE_ON: values["on"]}
        if kind == "thermostat" and state.get(STATE_TARGET) is not None:
            values["target"] = max(5.0, min(30.0, round(float(state[STATE_TARGET]) * 2) / 2))
            return {STATE_TARGET: values["target"]}
        if kind == "cover" and state.get(STATE_POSITION) is not None:
            values["position"] = int(max(0, min(100, float(state[STATE_POSITION]))))
            return {STATE_POSITION: values["position"]}
        if kind == "media":
            return self._media_command(values, state)
        raise PluginError(f"{values['name']} lässt sich so nicht schalten.")

    @staticmethod
    def _light_command(values: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        """Light command for a demo lamp: on/off, brightness, white tone, colour, effect."""
        if state.get(STATE_ON) is not None:
            values["on"] = bool(state[STATE_ON])
        if state.get(STATE_BRIGHTNESS) is not None:
            values["brightness"] = int(max(1, min(100, float(state[STATE_BRIGHTNESS]))))
            values["on"] = True if state.get(STATE_ON) is None else values["on"]
        if state.get("kelvin") is not None:
            values["mirek"], values["xy"] = kelvin_to_mirek(float(state["kelvin"])), None
        if state.get("mirek") is not None:
            values["mirek"], values["xy"] = int(float(state["mirek"])), None
        if values["light"] == "color" and state.get("color"):
            values["xy"] = list(hex_to_xy(str(state["color"])))
        if values["light"] == "color" and isinstance(state.get("xy"), (list, tuple)) and len(state["xy"]) == 2:
            values["xy"] = [float(state["xy"][0]), float(state["xy"][1])]
        if "effect" in state:
            values["effect"] = None if state["effect"] in (None, "", "no_effect") else str(state["effect"])
        return {STATE_ON: values["on"], STATE_BRIGHTNESS: values["brightness"], "light": _light_state(values)}

    @staticmethod
    def _media_command(values: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        """Playback command for the demo speaker: play, volume, mute, track change, favourite."""
        if state.get(STATE_PLAYING) is not None:
            values["playing"] = bool(state[STATE_PLAYING])
        if state.get(STATE_VOLUME) is not None:
            values["volume"] = int(max(0, min(100, float(state[STATE_VOLUME]))))
        if state.get(STATE_MUTED) is not None:
            values["muted"] = bool(state[STATE_MUTED])
        if state.get("favorite") in FAVORITES:
            values["favorite"], values["playing"] = state["favorite"], True
        if state.get("next") or state.get("previous"):
            step = 1 if state.get("next") else -1
            values["favorite"] = FAVORITES[(FAVORITES.index(values["favorite"]) + step) % len(FAVORITES)]
        return {STATE_PLAYING: values["playing"], STATE_VOLUME: values["volume"], STATE_MUTED: values["muted"],
                "media": MediaState(title=values["favorite"], source="Radio", favorites=list(FAVORITES))}


# ---------------------------------------------------------------- Starting point of the demo (runs inside the demo)
def _id(key: str) -> str:
    """Device id of a demo device in ComfyOS."""
    return ID_PREFIX + key


# The floor plan of the flat (grid cells of about half a metre). Top row from the left: bedroom, bath, children's
# room, study with a slanted corner; across the middle the hallway with the front door; below it the L-shaped living
# room with a bay window (a curved wall) and the kitchen, open to the living room through a passage. In front of the
# living room window lies a balcony (`decks`). The second floor (`floors`) is the attic: a studio above the right half
# of the flat and a roof terrace beside it.
PLAN: dict[str, Any] = {
    "width": 28, "height": 18, "heatmap": True, "shadow": True, "north": 20, "weather": True, "fx": True,
    "address": "Musterstraße 1, 12345 Musterstadt", "home_name": "Demo-Wohnung", "show_names": True,
    "walls": [{"id": "w1", "pts": [[1, 1], [24, 1], [27, 4], [27, 15], [11, 15], [5, 15], [1, 15], [1, 1]],
               "bend": [None, None, None, None, [8, 17.6], None, None]},
              {"id": "w2", "pts": [[1, 7], [27, 7]]},
              {"id": "w3", "pts": [[9, 1], [9, 7]]},
              {"id": "w4", "pts": [[14, 1], [14, 7]]},
              {"id": "w5", "pts": [[20, 1], [20, 7]]},
              {"id": "w6", "pts": [[7, 7], [7, 10], [27, 10]]},
              {"id": "w7", "pts": [[20, 10], [20, 15]]}],
    "rooms": [{"id": "r1", "name": "Wohnzimmer", "room_id": "wohnen", "x": 9, "y": 12.6},
              {"id": "r2", "name": "Küche", "room_id": "kueche", "x": 23.5, "y": 12.5},
              {"id": "r3", "name": "Schlafzimmer", "room_id": "schlafen", "x": 5, "y": 4.2},
              {"id": "r4", "name": "Kinderzimmer", "room_id": "kind", "x": 17, "y": 3.4},
              {"id": "r5", "name": "Arbeitszimmer", "room_id": "arbeit", "x": 23.2, "y": 4.4},
              {"id": "r6", "name": "Bad", "room_id": "bad", "x": 11.5, "y": 4.4},
              {"id": "r7", "name": "Flur", "room_id": "flur", "x": 15.5, "y": 8.5}],
    "openings": [
        # windows with their contacts, radiators with their thermostats
        {"id": "ow1", "type": "window", "a": [3, 1], "b": [7, 1], "side": 1, "device_id": _id("fenster-schlafen"),
         "extra_ids": [_id("rollladen")]},   # two devices at one window: contact and roller shutter
        {"id": "or1", "type": "radiator", "a": [3.4, 1], "b": [6.6, 1], "side": 1, "device_id": _id("heizung-schlafen")},
        {"id": "ow2", "type": "window", "a": [10.5, 1], "b": [12.5, 1], "side": 1, "device_id": _id("fenster-bad")},
        {"id": "or2", "type": "radiator", "a": [14, 4.5], "b": [14, 6.3], "side": 1, "device_id": _id("heizung-bad")},
        {"id": "ow3", "type": "window", "a": [15.5, 1], "b": [18.5, 1], "side": 1, "device_id": _id("fenster-kind")},
        {"id": "or3", "type": "radiator", "a": [15.8, 1], "b": [18.2, 1], "side": 1, "device_id": _id("heizung-kind")},
        {"id": "ow4", "type": "window", "a": [24.6, 1.6], "b": [26.4, 3.4], "side": 1, "device_id": _id("fenster-arbeit")},
        {"id": "or4", "type": "radiator", "a": [27, 4.6], "b": [27, 6.4], "side": 1, "device_id": _id("heizung-arbeit")},
        {"id": "ow5", "type": "window", "a": [1, 9], "b": [1, 13], "side": -1, "device_id": _id("fenster-wohnen")},
        {"id": "or5", "type": "radiator", "a": [1, 9.4], "b": [1, 12.6], "side": -1, "device_id": _id("heizung-wohnen")},
        {"id": "ow6", "type": "window", "a": [13, 15], "b": [17, 15], "side": -1, "device_id": _id("rollladen-wohnen")},
        {"id": "ow7", "type": "window", "a": [27, 11], "b": [27, 14], "side": 1, "device_id": _id("fenster-kueche")},
        {"id": "or6", "type": "radiator", "a": [22, 15], "b": [25, 15], "side": -1, "device_id": _id("heizung-kueche")},
        # doors, the front door with its contact, and the passage between kitchen and living room
        {"id": "od1", "type": "door", "a": [7.3, 7], "b": [8.7, 7], "side": -1, "device_id": None},
        {"id": "od2", "type": "door", "a": [10.8, 7], "b": [12.2, 7], "side": -1, "device_id": None},
        {"id": "od3", "type": "door", "a": [15.8, 7], "b": [17.2, 7], "side": -1, "device_id": None},
        {"id": "od4", "type": "door", "a": [21.5, 7], "b": [22.9, 7], "side": -1, "device_id": None},
        {"id": "od5", "type": "door", "a": [27, 7.8], "b": [27, 9.2], "side": 1, "device_id": _id("haustuer")},
        {"id": "od6", "type": "door", "a": [11, 10], "b": [12.6, 10], "side": 1, "device_id": None},
        {"id": "op1", "type": "passage", "a": [20, 11], "b": [20, 14], "side": 1, "device_id": None}],
    "devices": {
        # docked to their window, radiator or door
        _id("fenster-schlafen"): {"x": 5, "y": 1.5}, _id("heizung-schlafen"): {"x": 5, "y": 1.5},
        _id("fenster-bad"): {"x": 11.5, "y": 1.5}, _id("heizung-bad"): {"x": 13.5, "y": 5.4},
        _id("fenster-kind"): {"x": 17, "y": 1.5}, _id("heizung-kind"): {"x": 17, "y": 1.5},
        _id("fenster-arbeit"): {"x": 25.2, "y": 2.8}, _id("heizung-arbeit"): {"x": 26.5, "y": 5.5},
        _id("fenster-wohnen"): {"x": 1.5, "y": 11}, _id("heizung-wohnen"): {"x": 1.5, "y": 11},
        _id("fenster-kueche"): {"x": 26.5, "y": 12.5}, _id("heizung-kueche"): {"x": 23.5, "y": 14.5},
        _id("haustuer"): {"x": 26.5, "y": 8.5},
        # living room (its name stands in the wide part on the left, so the devices keep clear of it)
        _id("stehlampe"): {"x": 3.2, "y": 13.6, "furniture": "floor-lamp", "size": 0.8}, _id("esstisch"): {"x": 17, "y": 12},
        _id("tv"): {"x": 8, "y": 15.3, "furniture": "tv", "size": 1.4, "rot": 180}, _id("lautsprecher"): {"x": 11.5, "y": 13.2, "furniture": "speaker"},
        _id("rollladen-wohnen"): {"x": 15, "y": 14.5}, _id("taster"): {"x": 7.8, "y": 10.8},
        # kitchen
        _id("kueche"): {"x": 22.6, "y": 10.9}, _id("bewegung"): {"x": 20.9, "y": 13.6}, _id("kaffee"): {"x": 26, "y": 10.9, "furniture": "coffee", "size": 0.8},
        _id("lautsprecher-kueche"): {"x": 25.6, "y": 13.7, "furniture": "speaker"},
        # bedroom, bath, children's room, study, hallway
        _id("nachttisch"): {"x": 2.4, "y": 3}, _id("klima"): {"x": 5, "y": 6}, _id("rollladen"): {"x": 5, "y": 1.5},
        _id("spiegel"): {"x": 10.2, "y": 2.4}, _id("klima-bad"): {"x": 12.8, "y": 2.4},
        _id("waschmaschine"): {"x": 10.6, "y": 6, "furniture": "washer"},
        _id("nachtlicht"): {"x": 15.4, "y": 2.8}, _id("rauchmelder-kind"): {"x": 17.4, "y": 6},
        _id("schreibtisch"): {"x": 22, "y": 2.2}, _id("pc"): {"x": 21.6, "y": 5.6, "furniture": "computer"},
        _id("flurlicht"): {"x": 12.5, "y": 8.5}, _id("rauchmelder"): {"x": 21.5, "y": 8.5},
        _id("bewegung-flur"): {"x": 9, "y": 8.5}},
    "groups": [{"id": "g1", "name": "Licht Sofa", "x": 4.6, "y": 8.4, "members": [_id("decke"), _id("strip")]}],
    "decks": [{"id": "d1", "pts": [[12, 15], [19, 15], [19, 17.5], [12, 17.5], [12, 15]]}],
    "stairs": [{"id": "s1", "a": [20.2, 8.5], "b": [16.6, 8.5], "width": 1.6}],   # in the hallway, up to the studio
    # a load (since 0.29): a reading lamp without intelligence on the plug of the desk – it glows while that one is on
    "loads": [{"id": "l1", "x": 25.4, "y": 4.6, "kind": "lamp", "device_id": _id("pc"), "name": "Leselampe", "tone": "neutral",
               "brightness": 70, "furniture": "floor-lamp", "size": 0.7, "rot": 315}],
    "floor_id": "wohnung", "floor_name": "Wohnung",
    "floors": [{
        "id": "dach", "name": "Dachgeschoss", "level": 1,
        "walls": [{"id": "dw1", "pts": [[13, 1], [24, 1], [27, 4], [27, 10], [13, 10], [13, 1]]}],
        "rooms": [{"id": "dr1", "name": "Dachstudio", "room_id": "studio", "x": 20, "y": 5.5}],
        "openings": [
            {"id": "dow1", "type": "window", "a": [16, 1], "b": [20, 1], "side": 1, "device_id": _id("fenster-studio")},
            {"id": "dor1", "type": "radiator", "a": [27, 5.5], "b": [27, 8.5], "side": 1, "device_id": _id("heizung-studio")},
            {"id": "dod1", "type": "door", "a": [13, 5], "b": [13, 6.6], "side": 1, "device_id": None}],
        "devices": {_id("fenster-studio"): {"x": 18, "y": 1.5}, _id("heizung-studio"): {"x": 26.5, "y": 7},
                    _id("studio-licht"): {"x": 19, "y": 6.5}},
        "groups": [],
        "decks": [{"id": "dd1", "pts": [[3, 2], [13, 2], [13, 10], [3, 10], [3, 2]], "surface": "tiles"}]}],
}

# Heating plan of the demo per room: (time, temperature) – warm bath in the morning and evening, cool bedroom.
HEATING: dict[str, list[tuple[str, float]]] = {
    "wohnen": [("06:30", 21), ("22:30", 17)],
    "kueche": [("06:00", 20), ("21:00", 17)],
    "schlafen": [("06:00", 18), ("21:30", 16)],
    "kind": [("06:30", 20.5), ("20:00", 18)],
    "arbeit": [("07:30", 20.5), ("18:00", 17)],
    "bad": [("05:45", 23), ("08:30", 20), ("18:00", 22.5), ("22:00", 18)],
    "flur": [("06:00", 18), ("22:00", 16)],
    "studio": [("07:00", 19.5), ("21:00", 16.5)],
}


def _flows() -> list[tuple[str, str, list[dict], list[dict]]]:
    """Sample flows as (name, description, nodes, connections): light on motion in the evening, heating down while
    the kitchen window is open, and the wall switch."""
    def one(key: str) -> dict[str, Any]:
        """Target selection with exactly one demo device."""
        return {"devices": [_id(key)], "groups": [], "rooms": [], "all": False}

    def node(nid: str, typ: str, x: float, y: float, **config: Any) -> dict[str, Any]:
        """One node at a grid position."""
        return {"id": nid, "type": typ, "x": x * 290, "y": y * 150, "config": config}

    def edge(i: int, a: str, ap: str, z: str, zp: str) -> dict[str, Any]:
        """One connection."""
        return {"id": f"e{i}", "from": a, "fromPort": ap, "to": z, "toPort": zp}

    motion = [node("a", "device_state", 0, 0, role="trigger", target=one("bewegung"), prop="motion", hold_off=120,
                   **{"is": "yes"}),
              node("b", "time", 0, 1.25, role="condition", **{"from": "sunset", "to": "sunrise"}),
              node("d", "when", 1, 0.35, match="all"),
              node("e", "set_device", 2, 0, do="switch", target=one("kueche"), to="on"),
              node("i", "set_device", 2, 1.3, do="switch", target=one("kueche"), to="off")]
    window = [node("j", "device_state", 0, 0, role="trigger", target=one("fenster-kueche"), prop="open", hold_on=300,
                   **{"is": "yes"}),
              node("k", "set_device", 1, 0, do="temp", target=one("heizung-kueche"), temp_mode="abs", temp=12),
              node("l", "set_device", 1, 1, do="temp", target=one("heizung-kueche"), temp_mode="abs", temp=19)]
    switch = [node("m", "button", 0, 0, role="trigger", target=one("taster"), button="1", press=PRESS_SHORT),
              node("n", "set_device", 1, 0, do="switch", target={"devices": [], "groups": [], "rooms": ["wohnen"],
                                                                 "all": False}, to="on"),
              node("o", "button", 0, 1, role="trigger", target=one("taster"), button="4", press=PRESS_SHORT),
              node("p", "set_device", 1, 1, do="switch", target={"devices": [], "groups": [], "rooms": ["wohnen"],
                                                                 "all": False}, to="off")]
    return [("Licht bei Bewegung", "Küche: abends Licht an, zwei Minuten ohne Bewegung wieder aus", motion,
             [edge(1, "a", "out", "d", "in"), edge(2, "b", "val", "d", "c1"), edge(3, "d", "yes", "e", "in"),
              edge(4, "a", "end", "i", "in")]),
            ("Lüften", "Heizung Küche runter, solange das Fenster länger als fünf Minuten offen ist", window,
             [edge(1, "j", "out", "k", "in"), edge(2, "j", "end", "l", "in")]),
            ("Taster Wohnzimmer", "Taste An: Licht im Wohnzimmer an, Taste Aus: aus", switch,
             [edge(1, "m", "out", "n", "in"), edge(2, "o", "out", "p", "in")])]


def seed() -> None:
    """Write the starting point of the demo: floor plan, rooms of the scenes, a heating plan and the sample flows.
    Runs inside the demo (demo.reset), so every store writes to data/demo/."""
    from . import floorplan_store, flows_store, routines_store, scenes_store, widgets_store
    floorplan_store.save(PLAN)
    scenes_store.seed_rooms(list(ROOMS))
    routine = routines_store.create("Winter")
    routines_store.update_schedule(routine["key"], [
        {"room": room, "day": day, "points": [{"time": at, "temp": temp} for at, temp in HEATING[room]]}
        for room in ROOMS for day in range(7)])
    routines_store.set_active(routine["key"], pending=False)
    for name, description, nodes, edges in _flows():
        flows_store.create(name, nodes, edges, enabled=True, description=description)
    widgets_store.save({"pages": [_widget_page()]})


def _widget_page() -> dict[str, Any]:
    """Sample page of the widget view (since 0.24): what the demo home has, placed on the grid of twelve columns. The
    phone arrangement is left to the interface (it derives it from this one)."""
    def widget(kind: str, x: int, y: int, w: int, h: int, **opts: Any) -> dict[str, Any]:
        """One widget at a place of the wide grid."""
        return {"type": kind, "wide": {"x": x, "y": y, "w": w, "h": h}, "opts": opts,
                "head": kind not in ("clock", "heading")}

    return {"name": "Zuhause", "icon": "home", "cols": 12, "cols_narrow": 4, "widgets": [
        widget("clock", 0, 0, 3, 2, greeting=True),
        widget("weather", 3, 0, 3, 2),
        widget("presence", 6, 0, 3, 2),
        widget("status", 9, 0, 3, 2),
        widget("floorplan", 0, 2, 8, 6, heat=True, fx=True),
        widget("lights", 8, 2, 4, 2),
        widget("house_scene", 8, 4, 4, 2, scenes=["abendrot", "polarlicht"]),
        widget("power", 8, 6, 4, 2),
        widget("room", 0, 8, 4, 3, room="wohnen"),
        widget("media", 4, 8, 4, 3),
        widget("heating", 8, 8, 4, 3),
        widget("device", 0, 11, 2, 2, device=_id("stehlampe")),
        widget("value", 2, 11, 2, 2, device=_id("klima"), prop="humidity"),
        widget("openings", 4, 11, 4, 3),
        widget("events", 8, 11, 4, 3)]}
