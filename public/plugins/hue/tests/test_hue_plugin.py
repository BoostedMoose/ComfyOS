"""Tests for the Hue plugin. They run without ComfyOS, against a small fake bridge.

    python public/plugins/hue/tests/test_hue_plugin.py [path-to-comfyos]

Covered: translation between CLIP and ComfyOS (clip.py), the bridge client (bridge.py)
including the race between a running request and a command, and the plugin itself
(pairing, devices, link keys, commands, renaming, live events) and its load on the bridge (since 1.1.1);
reachability of motion sensors and switches by the same rule as lights (since 1.1.2).
The interplay with the Home Base is tested in tests/plugins/test_hue.py of ComfyOS.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "public" / "sdk"))
sys.path.insert(0, str(ROOT / "public" / "plugins"))

import httpx  # noqa: E402

from plugin_api import DeviceUnreachable, LightCapabilities, LightState, PluginError  # noqa: E402
from testing import FakeContext  # noqa: E402

import hue  # noqa: E402
from hue import bridge as hue_bridge, clip  # noqa: E402

# ---------------------------------------------------------------------------
# 1) clip.py: small helpers
# ---------------------------------------------------------------------------
assert clip.clip_name("  Stehlampe   im Wohnzimmer neben dem Sofa ") == "Stehlampe im Wohnzimmer neben de"
assert clip.bridge_mac("ECB5FAFFFEAABBCC") == "ecb5faaabbcc" and clip.bridge_mac(None) is None
assert clip.zigbee_mac16("00:17:88:01:0b:00:00:01") == "001788010b000001" and clip.zigbee_mac16("00:17") is None
assert hue_bridge.clean_host("https://192.0.2.10/") == "192.0.2.10"
for bad_host in ("", "kein host!", "a" * 80):
    try:
        hue_bridge.clean_host(bad_host)
        raise AssertionError(f"accepted host {bad_host!r}")
    except PluginError:
        pass
print("1 helpers: names are clipped to 32 characters, ids and hosts are normalised")

# ---------------------------------------------------------------------------
# 2) clip.py: light command -> CLIP body
# ---------------------------------------------------------------------------
ALL = LightState(LightCapabilities(white_range=(153, 500), color=True, gradient_points=5,
                                   gradient_modes=["interpolated_palette", "random_pixelated"], effects=["candle", "fire"]))
WHITE_ONLY = LightState(LightCapabilities(white_range=(153, 454)))
DIM_ONLY = LightState(LightCapabilities())

body = clip.build_command(ALL, {"brightness": 40, "kelvin": 2700, "duration": 300})
assert body == {"on": {"on": True}, "dimming": {"brightness": 40.0}, "color_temperature": {"mirek": 370},
                "dynamics": {"duration": 300}}, body
assert clip.build_command(ALL, {"brightness": 0}) == {"on": {"on": False}}
body = clip.build_command(ALL, {"gradient": ["#ff0000", "#0000ff"], "gradient_mode": "random_pixelated"})
assert len(body["gradient"]["points"]) == 2 and body["gradient"]["mode"] == "random_pixelated" and "color" not in body, body
assert clip.build_command(WHITE_ONLY, {"color": "#ff0000"}) == {"color_temperature": {"mirek": 454}}    # colour -> white
assert clip.build_command(WHITE_ONLY, {"mirek": 100}) == {"color_temperature": {"mirek": 153}}          # clamped
assert clip.build_command(DIM_ONLY, {"on": True, "color": "#ff0000"}) == {"on": {"on": True}}           # on only
assert clip.build_command(None, {"on": False}) == {"on": {"on": False}}                                  # a plug
assert clip.build_command(ALL, {"on": True, "duration": 500}) == {"on": {"on": True}}                    # nothing to fade
WITH_EFFECT_PARAMETERS = LightState(LightCapabilities(white_range=(153, 500), color=True, effects=["candle"], effect_parameters=True))
body = clip.build_command(WITH_EFFECT_PARAMETERS, {"effect": "candle", "effect_color": "#ff0000", "effect_speed": 0.5})
assert body["effects_v2"]["action"]["effect"] == "candle" and body["effects_v2"]["action"]["parameters"]["speed"] == 0.5, body
assert clip.build_command(ALL, {"effect": "candle", "effect_color": "#ff0000"}) == {"effects": {"effect": "candle"}}
for nonsense in ({"color": "rot"}, {"effect": "disco"}, {}, {"xy": [1]}, {"brightness": "viel"}, {"gradient": "#ff0000"}):
    try:
        clip.build_command(ALL, nonsense)
        raise AssertionError(f"accepted: {nonsense}")
    except ValueError:
        pass
print("2 build_command: white, colour, gradient, effect; replaces what a lamp cannot do; rejects nonsense")

# ---------------------------------------------------------------------------
# 3) clip.py: reading resources and applying updates
# ---------------------------------------------------------------------------
LIGHT_RESOURCE = {
    "id": "light-1", "id_v1": "/lights/7", "owner": {"rid": "device-1", "rtype": "device"}, "on": {"on": True},
    "dimming": {"brightness": 54.6}, "color_temperature": {"mirek": 366, "mirek_valid": True,
                                                           "mirek_schema": {"mirek_minimum": 153, "mirek_maximum": 500}},
    "color": {"xy": {"x": 0.4573, "y": 0.41}}, "effects": {"effect_values": ["no_effect", "candle"], "status": "no_effect"},
}
PLUG_RESOURCE = {"id": "light-2", "id_v1": "/lights/8", "owner": {"rid": "device-2", "rtype": "device"}, "on": {"on": False}}
DEVICES = [{"id": "device-1", "metadata": {"name": "Decke", "archetype": "ceiling_round"},
            "product_data": {"product_name": "Hue color lamp", "manufacturer_name": "Signify"}},
           {"id": "device-2", "metadata": {"name": "Stecker", "archetype": "plug"}},
           {"id": "device-3", "metadata": {"name": "Flur Sensor"}}]
ZIGBEE = [{"owner": {"rid": "device-1"}, "status": "connected", "mac_address": "00:17:88:01:00:00:00:01"},
          {"owner": {"rid": "device-2"}, "status": "connectivity_issue"},
          {"owner": {"rid": "device-3"}, "status": "connected", "mac_address": "00:17:88:01:0b:00:00:01"}]
ROOMS = [{"metadata": {"name": "Küche"}, "children": [{"rid": "device-1", "rtype": "device"}]}]

lamp, plug = clip.parse_lights([LIGHT_RESOURCE, PLUG_RESOURCE, "kaputt", {}], DEVICES, ZIGBEE, ROOMS)
assert (lamp.name, lamp.legacy_id, lamp.brightness, lamp.reachable, lamp.room_name) == ("Decke", "7", 55, True, "Küche"), lamp
assert lamp.light_state.mode == "ct" and lamp.light_state.mirek == 366 and lamp.light_state.capabilities.effects == ["candle"]
assert plug.is_plug and plug.light_state is None and plug.brightness is None and not plug.reachable, plug
clip.apply_light_update(lamp, {"on": {"on": False}, "color": {"xy": {"x": 0.2, "y": 0.1}},
                               "color_temperature": {"mirek": None, "mirek_valid": False}})
assert lamp.on is False and lamp.light_state.mode == "xy" and lamp.light_state.xy == [0.2, 0.1] and lamp.light_state.mirek is None
clip.apply_command(lamp, {"on": {"on": True}, "dimming": {"brightness": 80.0}, "color_temperature": {"mirek": 250}})
assert lamp.on and lamp.brightness == 80 and lamp.light_state.mode == "ct" and lamp.light_state.mirek == 250

sensors = clip.parse_motion_sensors([{"id": "motion-1", "owner": {"rid": "device-3"}, "motion": {"motion": False}}], ZIGBEE, DEVICES)
assert list(sensors) == ["001788010b000001"] and sensors["001788010b000001"].name == "Flur Sensor"
assert sensors["001788010b000001"].connection_status == "connected" and sensors["001788010b000001"].reachable
# one rule for every kind (since 1.1.2): only "connected" counts, a device without a zigbee resource counts as reachable
assert [clip.is_connected(status) for status in ("connected", None, "connectivity_issue", "disconnected",
                                                 "unidirectional_incoming")] == [True, True, False, False, False]
far_sensor = clip.parse_motion_sensors([{"id": "motion-1", "owner": {"rid": "device-3"}, "motion": {"motion": True}}],
                                       [{**ZIGBEE[2], "status": "connectivity_issue"}], DEVICES)["001788010b000001"]
assert far_sensor.reachable is False and far_sensor.motion is True, far_sensor
assert clip.read_motion({"motion": {"motion": False, "motion_report": {"motion": True}}}) is True
assert clip.read_motion({"motion": "kaputt"}) is None

# temperature, ambient light and battery of the sensor's device; a dimmer switch with four buttons (since plugin 1.1.0)
SENSOR_VALUES = {"temperature": [{"id": "temp-1", "owner": {"rid": "device-3"}, "temperature": {"temperature_report": {"temperature": 21.53}}}],
                 "light_level": [{"id": "level-1", "owner": {"rid": "device-3"}, "light": {"light_level_report": {"light_level": 20001}}}],
                 "device_power": [{"id": "power-1", "owner": {"rid": "device-3"}, "power_state": {"battery_level": 64, "battery_state": "normal"}},
                                  {"id": "power-4", "owner": {"rid": "device-4"}, "power_state": {"battery_level": 9, "battery_state": "critical"}}]}
SWITCH_DEVICE = {"id": "device-4", "metadata": {"name": "Dimmschalter Flur"},
                 "product_data": {"product_name": "Hue dimmer switch", "model_id": "RWL021"}}
SWITCH_ZIGBEE = {"owner": {"rid": "device-4"}, "status": "connected", "mac_address": "00:17:88:01:0c:00:00:04"}
BUTTONS = [{"id": f"button-{n}", "owner": {"rid": "device-4"}, "metadata": {"control_id": n}} for n in (4, 2, 3, 1)]
clip.attach_sensor_values(sensors, SENSOR_VALUES["temperature"], SENSOR_VALUES["light_level"], SENSOR_VALUES["device_power"])
sensor = sensors["001788010b000001"]
assert (sensor.temperature, sensor.illuminance, sensor.battery, sensor.low_battery) == (21.5, 100.0, 64, False), sensor
assert clip.read_light_level({"light": {"light_level": 1}}) == 1.0 and clip.read_light_level({"light": {"light_level": -5}}) is None
assert clip.read_power({"power_state": {"battery_level": 150, "battery_state": "low"}}) == (None, True)
switches = clip.parse_switches(BUTTONS + [{"id": "button-x", "owner": {"rid": "device-4"}, "metadata": {"control_id": "eins"}}],
                               DEVICES + [SWITCH_DEVICE], ZIGBEE + [SWITCH_ZIGBEE], SENSOR_VALUES["device_power"])
dimmer = switches["device-4"]
assert list(dimmer.buttons.values()) == ["1", "2", "3", "4"] and (dimmer.battery, dimmer.low_battery) == (9, True)
assert clip.button_names(dimmer) == [("1", "An"), ("2", "Heller"), ("3", "Dunkler"), ("4", "Aus")]
assert dimmer.zigbee_mac == "001788010c000004" and dimmer.model == "Hue dimmer switch" and dimmer.reachable
lost_dimmer = clip.parse_switches(BUTTONS, [SWITCH_DEVICE], [{**SWITCH_ZIGBEE, "status": "disconnected"}], [])["device-4"]
assert lost_dimmer.reachable is False and lost_dimmer.zigbee_mac == "001788010c000004", lost_dimmer
for event, press in (("short_release", "short"), ("long_press", "long"), ("double_short_release", "double"),
                     ("initial_press", None), ("repeat", None), ("long_release", None)):
    assert clip.read_button_event({"button": {"button_report": {"event": event}}}) == press, event
assert clip.read_button_event({"button": {"last_event": "short_release"}}) == "short"

scenes = clip.parse_scenes(
    [{"id": "scene-b", "metadata": {"name": "Tropen", "image": {"rid": "image-1", "rtype": "public_image"}},
      "group": {"rid": "room-2"}, "auto_dynamic": True,
      "palette": {"color": [{"color": {"xy": {"x": 0.6, "y": 0.3}}, "dimming": {"brightness": 80}}],
                  "color_temperature": [{"color_temperature": {"mirek": 300}, "dimming": {"brightness": 60}}]}},
     {"id": "scene-a", "metadata": {"name": "Tropen"}, "group": {"rid": "room-1"},
      "actions": [{"action": {"color": {"xy": {"x": 0.6, "y": 0.3}}, "dimming": {"brightness": 50}}}]},
     {"id": "scene-c", "metadata": {"name": "Leer"}}],
    [{"id": "room-1", "metadata": {"name": "Bad"}}, {"id": "room-2", "metadata": {"name": "Wohnen"}}])
assert [scene.name for scene in scenes] == ["Tropen"] and scenes[0].groups == ["Bad", "Wohnen"], scenes
assert scenes[0].id == "scene-a" and len(scenes[0].palette) == 1, scenes[0]     # the room that sorts first wins
print("3 parsing: lights with capabilities, plugs, motion sensors with temperature, ambient light and battery, switches with "
      "named buttons, button events, scenes once per name; events and commands update the cache; reachability by one rule")


# ---------------------------------------------------------------------------
# 4) bridge.py: a request that started before a command must not undo it
# ---------------------------------------------------------------------------
async def race_between_request_and_command() -> None:
    """A command sent while a list request is still on its way must win: the stale answer of the bridge is not allowed
    to overwrite the state the command just set."""
    client_bridge = hue_bridge.HueBridge(lambda **options: None, "192.0.2.1", "key", bridge_id="ecb5fafffeaabbcc")
    answer_may_arrive = asyncio.Event()
    light_id = "00000000-0000-4000-8000-000000000001"

    def stale_lights(*_resources):
        """What the bridge answers: the state from before the command (off, 40 %)."""
        return [clip.HueLight(light_id, "device-1", "1", "Decke", None, False, 40, True, "connected",
                              None, None, None, None, LightState())]

    async def fake_request(method, path, body=None):
        if method == "GET" and path == "resource/light":
            await answer_may_arrive.wait()      # the bridge answers only after the command was sent
        return []

    client_bridge._request = fake_request
    original_parse = clip.parse_lights
    clip.parse_lights = stale_lights
    try:
        client_bridge._lights_cache = (0.0, stale_lights())
        fetch = asyncio.ensure_future(client_bridge.lights(max_age=0))
        await asyncio.sleep(0.01)
        await client_bridge.send_light_command(light_id, {"on": {"on": True}})
        answer_may_arrive.set()
        lights = await fetch
        assert lights[0].on is True, "the older answer overwrote the command"

        # an event that arrives while a request is running is kept too
        answer_may_arrive.clear()
        fetch = asyncio.ensure_future(client_bridge.lights(max_age=0))
        await asyncio.sleep(0.01)
        client_bridge.apply_event({"type": "light", "id": light_id, "dimming": {"brightness": 77.0}})
        answer_may_arrive.set()
        lights = await fetch
        assert lights[0].brightness == 77 and lights[0].on is True, lights[0]

        # commands from before the request are already part of the answer and are not replayed
        client_bridge._recent_changes = [(when - 60, kind, light_id, payload)
                                         for when, kind, light_id, payload in client_bridge._recent_changes]
        answer_may_arrive.set()
        lights = await client_bridge.lights(max_age=0)
        assert lights[0].on is False, lights[0]
    finally:
        clip.parse_lights = original_parse


asyncio.run(race_between_request_and_command())
print("4 race: new state and events survive a running request, old commands are not replayed")

# ---------------------------------------------------------------------------
# 5) the plugin against a fake bridge
# ---------------------------------------------------------------------------
fake = {"button_pressed": False, "down": False, "puts": [], "renames": []}
LIGHT_ID, PLUG_ID = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"
DEVICE_ID = "00000000-0000-4000-8000-000000001001"
MOTION_ID = "00000000-0000-4000-8000-000000004001"


def fake_bridge(request: httpx.Request) -> httpx.Response:
    """Answer like a Hue bridge: pairing only after the link button was pressed, resources only with the application
    key."""
    path = request.url.path
    if fake["down"]:
        raise httpx.ConnectError("down", request=request)
    if path == "/api/0/config":
        return httpx.Response(200, json={"bridgeid": "ECB5FAFFFEAABBCC", "name": "Testbridge"})
    if path == "/api":
        if not fake["button_pressed"]:
            return httpx.Response(200, json=[{"error": {"type": 101, "description": "link button not pressed"}}])
        return httpx.Response(200, json=[{"success": {"username": "APPKEY"}}])
    if request.headers.get("hue-application-key") != "APPKEY":
        return httpx.Response(403, json={"errors": [{"description": "unauthorized user"}]})
    data = {
        "/clip/v2/resource/bridge": [{"bridge_id": "ecb5fafffeaabbcc"}],
        "/clip/v2/resource/light": [{**LIGHT_RESOURCE, "id": LIGHT_ID, "owner": {"rid": DEVICE_ID}},
                                    {**PLUG_RESOURCE, "id": PLUG_ID, "owner": {"rid": "device-2"}}],
        "/clip/v2/resource/device": [{**DEVICES[0], "id": DEVICE_ID}, DEVICES[1], DEVICES[2], SWITCH_DEVICE],
        "/clip/v2/resource/zigbee_connectivity": [{**ZIGBEE[0], "owner": {"rid": DEVICE_ID}}, ZIGBEE[1], ZIGBEE[2], SWITCH_ZIGBEE],
        "/clip/v2/resource/room": [{"metadata": {"name": "Küche"}, "children": [{"rid": DEVICE_ID, "rtype": "device"}]}],
        "/clip/v2/resource/motion": [{"id": MOTION_ID, "owner": {"rid": "device-3"}, "motion": {"motion": False}}],
        "/clip/v2/resource/temperature": SENSOR_VALUES["temperature"],
        "/clip/v2/resource/light_level": SENSOR_VALUES["light_level"],
        "/clip/v2/resource/device_power": SENSOR_VALUES["device_power"],
        "/clip/v2/resource/button": BUTTONS,
    }
    if request.method == "GET" and path in data:
        return httpx.Response(200, json={"errors": [], "data": data[path]})
    if request.method == "PUT" and path.startswith("/clip/v2/resource/light/"):
        fake["puts"].append((path.rsplit("/", 1)[1], json.loads(request.content)))
        return httpx.Response(200, json={"errors": [], "data": []})
    if request.method == "PUT" and path.startswith("/clip/v2/resource/device/"):
        fake["renames"].append((path.rsplit("/", 1)[1], json.loads(request.content)["metadata"]["name"]))
        return httpx.Response(200, json={"errors": [], "data": []})
    return httpx.Response(404, json={"errors": [{"description": "not found"}]})


async def plugin_against_fake_bridge() -> None:
    """Walk the plugin through its life against the fake bridge: pairing only in the home network, devices, commands,
    names, live events, bridge down, forget."""
    context = FakeContext("hue", http_handler=fake_bridge, secrets=["app_key"])
    plugin = hue.setup(context)
    await plugin.start()
    assert plugin.status() == {"configured": False, "connected": False, "live": False} and await plugin.devices() == []

    # pairing: only inside the home network, only after the link button was pressed
    for outside in ("8.8.8.8", "evil.example.com"):
        try:
            await plugin.pair(outside)
            raise AssertionError(f"paired with {outside}")
        except PluginError:
            pass
    assert context.requests == [], "the plugin contacted an address outside the home network"
    try:
        await plugin.pair("192.0.2.10")
        raise AssertionError("paired without the link button")
    except hue_bridge.LinkButtonNotPressed:
        pass
    fake["button_pressed"] = True
    await plugin.pair("https://192.0.2.10/")
    assert context.secrets.get("app_key") == "APPKEY" and context.settings.get("host") == "192.0.2.10"
    assert "APPKEY" not in json.dumps(context.settings.values) and not any("APPKEY" in line for line in context.events)
    assert plugin.status()["configured"] and plugin.link_prefixes() == ["hue:ecb5faaabbcc:"]

    # devices: lights with link keys for the Home Base, plugs as switches, motion sensors by zigbee address
    devices = {device.id: device for device in await plugin.devices()}
    lamp_device, plug_device = devices["huebridge:" + LIGHT_ID], devices["huebridge:" + PLUG_ID]
    sensor_device = devices["huebridge:motion:" + MOTION_ID]
    assert lamp_device.kind == "light" and lamp_device.link_keys == ["hue:ecb5faaabbcc:light:7"] and lamp_device.discovered
    assert lamp_device.state == {"on": True, "brightness": 55} and lamp_device.room_hint == "Küche" and lamp_device.renamable
    assert plug_device.kind == "switch" and plug_device.light is None and plug_device.online is False and plug_device.status_detail
    assert sensor_device.kind == "motion" and sensor_device.link_keys == ["zigbee:001788010b000001"]
    assert sensor_device.online is True and sensor_device.status_detail is None, "a sensor reports whether the bridge reaches it"
    assert sensor_device.name == "Flur Sensor" and sensor_device.state == {"motion": False, "temperature": 21.5, "illuminance": 100.0}
    assert sensor_device.battery == 64 and {c.key for c in sensor_device.capabilities} == {"motion", "temperature", "illuminance"}
    switch_device = devices["huebridge:switch:device-4"]
    assert switch_device.kind == "button" and [(b.id, b.name) for b in switch_device.buttons][:2] == [("1", "An"), ("2", "Heller")]
    assert switch_device.low_battery is True and switch_device.link_keys == ["zigbee:001788010c000004"] and switch_device.discovered
    assert switch_device.online is True and switch_device.status_detail is None, "a switch reports whether the bridge reaches it"

    # commands
    result = await plugin.command("huebridge:" + LIGHT_ID, {"on": True, "brightness": 30, "color": "#ff0000"}, "command")
    assert fake["puts"][-1][0] == LIGHT_ID and fake["puts"][-1][1]["dimming"] == {"brightness": 30.0}, fake["puts"]
    assert result["on"] is True and result["brightness"] == 30 and result["light"].mode == "xy", result
    await plugin.command("huebridge:" + PLUG_ID, {"on": True}, "flow")
    for device_id, state in (("huebridge:" + PLUG_ID, {"brightness": 50}), ("huebridge:" + LIGHT_ID, {"effect": "disco"}),
                             ("huebridge:motion:" + MOTION_ID, {"on": True}), ("huebridge:unbekannt", {"on": True}),
                             ("huebridge:switch:device-4", {"on": True})):
        try:
            await plugin.command(device_id, state, "command")
            raise AssertionError(f"accepted {state} for {device_id}")
        except PluginError:
            pass

    # names: ComfyOS is the master, the bridge keeps at most 32 characters
    assert await plugin.rename("huebridge:" + LIGHT_ID, "Stehlampe im Wohnzimmer neben dem Sofa") == "Stehlampe im Wohnzimmer neben de"
    assert fake["renames"] == [(DEVICE_ID, "Stehlampe im Wohnzimmer neben de")]
    assert await plugin.rename("huebridge:motion:" + MOTION_ID, "Sensor") is None       # sensors are not renamed
    assert plugin.clean_name("  Zu   langer Name mit sehr vielen Zeichen  ") == "Zu langer Name mit sehr vielen Z"

    # live events are passed on with the plugin's own device ids
    await plugin._on_bridge_change([plugin.bridge.cached_light(LIGHT_ID), plugin.bridge.motion_sensors["001788010b000001"]])
    assert context.changes[0][0] == "huebridge:" + LIGHT_ID and set(context.changes[0][1]) == {"on", "brightness", "online", "light"}
    assert context.changes[1] == ("huebridge:motion:" + MOTION_ID, {"motion": False, "temperature": 21.5, "illuminance": 100.0})

    # button presses and sensor values from the event stream: a press goes to ComfyOS as such, once
    bridge = plugin.bridge
    for event in ("initial_press", "short_release", "repeat", "long_press", "long_release"):
        item = bridge.apply_event({"type": "button", "id": "button-1", "button": {"button_report": {"event": event}}})
        if item is not None:
            await plugin._on_bridge_change([item])
    assert context.presses == [("huebridge:switch:device-4", "1", "short"), ("huebridge:switch:device-4", "1", "long")]
    assert bridge.apply_event({"type": "button", "id": "button-unbekannt", "button": {"last_event": "short_release"}}) is None
    warmer = bridge.apply_event({"type": "temperature", "id": "temp-1", "temperature": {"temperature_report": {"temperature": 22.0}}})
    assert warmer is bridge.motion_sensors["001788010b000001"] and warmer.temperature == 22.0
    assert bridge.apply_event({"type": "temperature", "id": "temp-1", "temperature": {"temperature": 22.0}}) is None   # unchanged
    darker = bridge.apply_event({"type": "light_level", "id": "level-1", "light": {"light_level": 1}})
    await plugin._on_bridge_change([darker])
    assert context.changes[-1] == ("huebridge:motion:" + MOTION_ID, {"motion": False, "temperature": 22.0, "illuminance": 1.0})
    assert bridge.apply_event({"type": "device_power", "id": "power-4", "power_state": {"battery_level": 80}}) is None
    assert bridge.switches["device-4"].battery == 80

    # reachability of sensors and switches from the event stream (since 1.1.2): reported alone and only when it changes
    def connectivity(owner: str, status: str) -> dict:
        """A zigbee_connectivity update of the event stream for the device ``owner``."""
        return {"type": "zigbee_connectivity", "id": f"zigbee-{owner}", "owner": {"rid": owner, "rtype": "device"},
                "status": status}

    lost = bridge.apply_event(connectivity("device-3", "connectivity_issue"))
    assert lost is not None and lost.item is bridge.motion_sensors["001788010b000001"], lost
    assert bridge.apply_event(connectivity("device-3", "disconnected")) is None, "still unreachable: nothing to report"
    await plugin._on_bridge_change([lost])
    assert context.changes[-1] == ("huebridge:motion:" + MOTION_ID, {"online": False}), context.changes[-1]
    offline_sensor = plugin._motion_sensor_as_device(bridge.motion_sensors["001788010b000001"])
    assert offline_sensor.online is False and "Bewegungsmelder" in offline_sensor.status_detail, offline_sensor
    for item in (bridge.apply_event(connectivity("device-3", "connected")),
                 bridge.apply_event(connectivity("device-4", "connectivity_issue"))):
        await plugin._on_bridge_change([item])
    assert context.changes[-2:] == [("huebridge:motion:" + MOTION_ID, {"online": True}),
                                    ("huebridge:switch:device-4", {"online": False})], context.changes[-2:]
    assert bridge.apply_event(connectivity("device-unbekannt", "connected")) is None
    motion_event = bridge.apply_event({"type": "motion", "id": MOTION_ID, "motion": {"motion": True}})
    await plugin._on_bridge_change([motion_event])
    assert "online" not in context.changes[-1][1], "a motion event does not repeat the reachability"
    reloads = context.reloads
    await plugin._on_bridge_change(None)
    assert context.reloads == reloads + 1

    # bridge down: one line in the event log, DeviceUnreachable for the core, one line when it is back
    fake["down"] = True
    plugin.bridge.forget_lights()
    for _ in range(2):
        try:
            await plugin.devices()
            raise AssertionError("devices() answered although the bridge is down")
        except DeviceUnreachable:
            pass
    assert sum("nicht erreichbar" in line for line in context.events) == 1
    fake["down"] = False
    assert len(await plugin.devices()) == 4 and context.events[-1] == "Hue Bridge wieder erreichbar"

    # forget: key and settings are gone
    await plugin.forget()
    assert context.secrets.get("app_key") is None and context.settings.get("host") is None and plugin.bridge is None
    await plugin.stop()


asyncio.run(plugin_against_fake_bridge())
print("5 plugin: pairing only in the home network, devices with link keys, sensor values and switches, commands, names, "
      "live events and button presses, bridge down, forget")


# ---------------------------------------------------------------------------
# 6) gentle on the bridge (since 1.1.1): few requests at once, one retry when busy, the live cache serves
# ---------------------------------------------------------------------------
async def gentle_on_the_bridge() -> None:
    """A bridge that answers slowly and refuses every first request of a resource as busy (HTTP 429): never more than
    MAX_PARALLEL_REQUESTS at once, every refused request tried exactly once more, and while the event stream is live
    the device list does not ask the bridge again."""
    busy = {"now": 0, "most": 0, "refused": set(), "asked": []}

    async def slow_bridge(request: httpx.Request) -> httpx.Response:
        """Answer after a short pause; the first request per path is refused as busy."""
        busy["now"] += 1
        busy["most"] = max(busy["most"], busy["now"])
        try:
            await asyncio.sleep(0.01)
            path = request.url.path
            busy["asked"].append(path)
            if path not in busy["refused"]:
                busy["refused"].add(path)
                return httpx.Response(429, json={"errors": [{"description": "too many requests"}]})
            rows = {"/clip/v2/resource/bridge": [{"bridge_id": "ecb5fafffeaabbcc"}]}.get(path, [])
            return httpx.Response(200, json={"errors": [], "data": rows})
        finally:
            busy["now"] -= 1

    original_pause = hue_bridge.BUSY_RETRY_SECONDS
    hue_bridge.BUSY_RETRY_SECONDS = 0.01
    client_bridge = hue_bridge.HueBridge(lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(slow_bridge)),
                                         "192.0.2.1", "key")
    try:
        assert await client_bridge.lights(max_age=0) == [] and client_bridge.last_error == ""
        assert 1 < busy["most"] <= hue_bridge.MAX_PARALLEL_REQUESTS, busy["most"]
        assert all(busy["asked"].count(path) == 2 for path in busy["asked"]), "every refused request is tried exactly once more"
        asked = len(busy["asked"])
        # still busy on the second try: the error reaches the caller (and the status of the plugin)
        busy["refused"].clear()
        hue_bridge.BUSY_RETRY_SECONDS = 0.0
        original_request = client_bridge._client.request

        async def always_busy(*args, **kwargs):
            """The bridge refuses everything."""
            await original_request(*args, **kwargs)
            return httpx.Response(429, json={"errors": []})
        client_bridge._client.request = always_busy
        try:
            await client_bridge.lights(max_age=0)
            raise AssertionError("a bridge that stays busy was reported as reachable")
        except DeviceUnreachable as error:
            assert "ausgelastet" in str(error) and "ausgelastet" in client_bridge.last_error
        await asyncio.sleep(0.2)        # the other requests of that round finish in the background
        client_bridge._client.request = original_request
        # live: the cache the events keep current serves for a minute, without the stream only for two seconds
        busy["refused"] = {path for path in busy["asked"]}
        await client_bridge.lights(max_age=0)
        asked = len(busy["asked"])
        client_bridge.is_live = True
        assert client_bridge.cache_age() == hue_bridge.LIVE_CACHE_SECONDS >= 30
        client_bridge._lights_cache = (time.time() - 10, client_bridge._lights_cache[1])
        await client_bridge.lights(max_age=client_bridge.cache_age())
        assert len(busy["asked"]) == asked, "asked the bridge although the event stream keeps the list current"
        client_bridge.is_live = False
        assert client_bridge.cache_age() == hue_bridge.LIGHT_CACHE_SECONDS
        await client_bridge.lights(max_age=client_bridge.cache_age())
        assert len(busy["asked"]) > asked, "without the event stream the list is asked anew"
    finally:
        hue_bridge.BUSY_RETRY_SECONDS = original_pause
        await client_bridge.close()


asyncio.run(gentle_on_the_bridge())
print("6 gentle on the bridge: at most three requests at once, a busy answer tried once more, the live cache serves")
print("ALL HUE PLUGIN TESTS OK")
