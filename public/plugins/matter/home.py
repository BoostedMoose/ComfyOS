"""The home as Matter devices: room lights, all lights, house scenes, at home / away.

Matter transfers no rooms that a controller would read, and it has no scenes that reach
ComfyOS. So the bridge can offer a few virtual devices that stand for things ComfyOS itself
knows – then "Alexa, switch off the living room light" works without a group in the Alexa app:

* ``room:<id>``     "<room> Licht"   dimmable light: all lamps of the room
* ``all-lights``    "Alle Lichter"   dimmable light: all lamps of the home
* ``scene:<id>``    scene name       switch: on starts the house scene, off ends it. It stays "on"
                                     as long as the scene runs (a switch that resets itself at once
                                     confuses some controllers).
* ``home``          "Zuhause"        switch: on = at home, off = away

Like every device they are shared only when the user switches them on in the tile.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from plugin_api import STATE_BRIGHTNESS, STATE_ON, DeviceView, PluginContext, PluginError, SceneView

from .devices import DEVICE_ID_PREFIX, DIMMABLE_LIGHT, ON_OFF_PLUG, SharedEndpoint, brightness_to_level, label, level_to_brightness

ROOM_PREFIX = "room:"
SCENE_PREFIX = "scene:"
ALL_LIGHTS_KEY = "all-lights"
HOME_KEY = "home"
ALL_LIGHTS_NAME = "Alle Lichter"
HOME_NAME = "Zuhause"
STATE_PRESENT = "PRESENT"
STATE_AWAY = "AWAY"


def is_home_key(key: str) -> bool:
    """Return whether a share key stands for one of the virtual devices of this module."""
    return key in (ALL_LIGHTS_KEY, HOME_KEY) or key.startswith((ROOM_PREFIX, SCENE_PREFIX))


def _lamps(views: Sequence[DeviceView]) -> list[DeviceView]:
    """The lamps ComfyOS can switch – without those of the plugin's own controller."""
    return [view for view in views if view.kind == "light" and view.controllable and not view.id.startswith(DEVICE_ID_PREFIX)]


def lights_state(lamps: Sequence[DeviceView]) -> dict[str, Any]:
    """Matter values of a group of lamps: on when any lamp is on, level = mean brightness of the lamps that are on."""
    burning = [lamp for lamp in lamps if lamp.online and lamp.state.get(STATE_ON)]
    levels = [float(lamp.state[STATE_BRIGHTNESS]) for lamp in burning
              if isinstance(lamp.state.get(STATE_BRIGHTNESS), (int, float)) and not isinstance(lamp.state.get(STATE_BRIGHTNESS), bool)]
    mean = sum(levels) / len(levels) if levels else 100.0
    return {"on": bool(burning), "level": brightness_to_level(mean)}


def home_endpoints(views: Sequence[DeviceView], scenes: Sequence[SceneView], home_state: str | None) -> list[SharedEndpoint]:
    """Return every virtual device the home offers right now.

    ``scenes`` and ``home_state`` may be empty/None when the plugin lacks the permission – then
    those devices are simply not offered.
    """
    lamps = _lamps(views)
    found: list[SharedEndpoint] = []
    rooms: dict[str, tuple[str, list[DeviceView]]] = {}
    for lamp in lamps:
        if lamp.room_id:
            rooms.setdefault(lamp.room_id, (lamp.room or lamp.room_id, []))[1].append(lamp)
    for room_id, (room_name, room_lamps) in sorted(rooms.items(), key=lambda item: item[1][0].casefold()):
        found.append(SharedEndpoint(key=ROOM_PREFIX + room_id, kind=DIMMABLE_LIGHT, name=label(f"{room_name} Licht"),
                                    reachable=any(lamp.online for lamp in room_lamps), state=lights_state(room_lamps)))
    if lamps:
        found.append(SharedEndpoint(key=ALL_LIGHTS_KEY, kind=DIMMABLE_LIGHT, name=ALL_LIGHTS_NAME,
                                    reachable=any(lamp.online for lamp in lamps), state=lights_state(lamps)))
    for scene in scenes:
        if scene.rooms:                         # only scenes assigned to rooms can run as a house scene
            found.append(SharedEndpoint(key=SCENE_PREFIX + scene.id, kind=ON_OFF_PLUG, name=label(scene.name),
                                        state={"on": scene.active}))
    if home_state is not None:
        found.append(SharedEndpoint(key=HOME_KEY, kind=ON_OFF_PLUG, name=HOME_NAME, state={"on": home_state == STATE_PRESENT}))
    return found


async def run_command(context: PluginContext, key: str, wanted: Mapping[str, Any]) -> None:
    """Carry out what a controller wants from a virtual device, through the same helpers the interface uses.

    Raises PluginError (or DeviceLocked, DeviceUnreachable) when ComfyOS refuses or the key is unknown.
    """
    turn_on = wanted.get("on")
    if key == ALL_LIGHTS_KEY or key.startswith(ROOM_PREFIX):
        room_id = None if key == ALL_LIGHTS_KEY else key[len(ROOM_PREFIX):]
        if turn_on is False:
            await context.room_lights(room_id, False)
        elif "level" in wanted:
            await context.room_lights(room_id, True, float(level_to_brightness(wanted["level"])))
        elif turn_on is True:
            await context.room_lights(room_id, True)
        else:
            raise PluginError("Dieser Befehl lässt sich nicht ausführen.")
        return
    if not isinstance(turn_on, bool):
        raise PluginError("Dieser Befehl lässt sich nicht ausführen.")
    if key.startswith(SCENE_PREFIX):
        await context.house_scene(key[len(SCENE_PREFIX):], turn_on)
    elif key == HOME_KEY:
        await context.home(STATE_PRESENT if turn_on else STATE_AWAY)
    else:
        raise PluginError("Dieses Gerät gibt es nicht.")
