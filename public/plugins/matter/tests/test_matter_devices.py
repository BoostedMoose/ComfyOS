"""Tests for the translation between ComfyOS and Matter (devices.py), the home as devices (home.py)
and the share list (shares.py).

    python public/plugins/matter/tests/test_matter_devices.py [path-to-comfyos]

They run without ComfyOS and without Node. Covered:
    1  units: ranges and rounding there and back
    2  bridge: every kind of ComfyOS device becomes the right Matter device with the right values
    3  bridge: what a controller wants becomes a ComfyOS command
    4  controller: every Matter device type becomes a ComfyOS device; commands become Matter values
    5  the home as devices: room lights, all lights, scenes, at home – and their commands
    6  share list: permanent endpoint numbers, several at once, only a technical bound, no devices of the own controller
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "public" / "sdk"))
sys.path.insert(0, str(ROOT / "public" / "plugins"))

from plugin_api import DeviceView, LightCapabilities, LightState, PluginError, SceneView  # noqa: E402
from testing import FakeContext, MemoryStore  # noqa: E402

from matter import devices, home, protocol, shares  # noqa: E402


def view(identifier: str, kind: str, state: dict | None = None, **fields: object) -> DeviceView:
    """Build a ComfyOS device as the plugin sees it."""
    return DeviceView(id=identifier, name=fields.pop("name", identifier), kind=kind, plugin="test", controllable=True,
                      state=dict(state or {}), **fields)


def remote(types: list[int], state: dict, name: str = "Gerät", number: int = 3, online: bool = True,
           reachable: bool = True) -> tuple[protocol.RemoteNode, protocol.RemoteEndpoint]:
    """Build a commissioned Matter device with one endpoint."""
    endpoint = protocol.RemoteEndpoint(endpoint=number, types=types, name=name, reachable=reachable, state=state)
    return protocol.RemoteNode(node="peer2", online=online, name="Fremde Bridge", vendor="Signify", product="Bridge", serial="s",
                               endpoints=[endpoint]), endpoint


# ---------------------------------------------------------------------------
# 1) units
# ---------------------------------------------------------------------------
assert all(devices.level_to_brightness(devices.brightness_to_level(percent)) == percent for percent in range(1, 101)), "brightness round trip"
assert (devices.brightness_to_level(0), devices.brightness_to_level(1), devices.brightness_to_level(50), devices.brightness_to_level(100)) == (1, 3, 127, 254)
assert devices.brightness_to_level(500) == 254 and devices.brightness_to_level(-5) == 1
assert (devices.level_to_brightness(1), devices.level_to_brightness(254), devices.level_to_brightness(0), devices.level_to_brightness(999)) == (1, 100, 1, 100)
assert devices.celsius_to_matter(21.5) == 2150 and devices.matter_to_celsius(2150) == 21.5 and devices.celsius_to_matter(-3.25) == -325
assert all(devices.matter_to_celsius(devices.celsius_to_matter(half / 2)) == half / 2 for half in range(10, 61)), "temperature round trip"
assert devices.percent_to_matter(50) == 5000 and devices.matter_to_percent(5000) == 50 and devices.percent_to_matter(140) == 10000
assert all(devices.matter_to_percent(devices.percent_to_matter(percent)) == percent for percent in range(0, 101)), "position round trip"
assert devices.xy_to_matter(0.3127) == 20493 and abs(devices.matter_to_xy(devices.xy_to_matter(0.4567)) - 0.4567) < 0.0001
assert devices.xy_to_matter(1.5) == devices.MAX_MATTER_XY and devices.xy_to_matter(-1) == 0
assert devices.label("  Sehr   langer Name einer Lampe im Wohnzimmer  ") == "Sehr langer Name einer Lampe im " and devices.label("") == "Matter-Gerät"
print("1 units: level, temperature, position and colour keep their value there and back; out-of-range values are limited")

# ---------------------------------------------------------------------------
# 2) bridge: ComfyOS device -> Matter device
# ---------------------------------------------------------------------------
plain = devices.endpoints_of(view("hb:plain", "light", {"on": True}, name="Flur"))
assert [(e.key, e.kind, e.name, e.state) for e in plain] == [("hb:plain", devices.ON_OFF_LIGHT, "Flur", {"on": True})]
dimmable = devices.endpoints_of(view("hb:dim", "light", {"on": False, "brightness": 40}))[0]
assert dimmable.kind == devices.DIMMABLE_LIGHT and dimmable.state == {"on": False, "level": 102}
dim_only = devices.endpoints_of(view("hue:dim", "light", {"on": True, "brightness": 0}, light=LightState()))[0]
assert dim_only.kind == devices.DIMMABLE_LIGHT and dim_only.state == {"on": True, "level": 254}, "an off level is never sent"
white = devices.endpoints_of(view("hue:ct", "light", {"on": True, "brightness": 80},
                                  light=LightState(capabilities=LightCapabilities(white_range=(153, 454)), mode="ct", mirek=600)))[0]
assert white.kind == devices.COLOR_TEMPERATURE_LIGHT
assert white.state == {"on": True, "level": 203, "min_mireds": 153, "max_mireds": 454, "mireds": 454, "color_mode": "ct"}, white.state
colour = devices.endpoints_of(view("hue:col", "light", {"on": True, "brightness": 100},
                                   light=LightState(capabilities=LightCapabilities(white_range=(153, 500), color=True), mode="xy",
                                                    xy=[0.5, 0.4], mirek=None)))[0]
assert colour.kind == devices.EXTENDED_COLOR_LIGHT and colour.state["color_mode"] == "xy"
assert (colour.state["x"], colour.state["y"], colour.state["mireds"]) == (32768, 26214, 370)
gradient = devices.endpoints_of(view("hue:grad", "light", {"on": True, "brightness": 100},
                                     light=LightState(capabilities=LightCapabilities(color=True), mode="gradient")))[0]
assert gradient.state["color_mode"] == "xy" and (gradient.state["min_mireds"], gradient.state["max_mireds"]) == (153, 500)
assert devices.endpoints_of(view("hb:plug", "switch", {"on": True}))[0].kind == devices.ON_OFF_PLUG
thermostat = devices.endpoints_of(view("hb:therm", "thermostat", {"target": 21.5, "temperature": 20.3}))[0]
assert thermostat.kind == devices.THERMOSTAT
assert thermostat.state == {"heating_setpoint": 2150, "min_heat": 500, "max_heat": 3000, "local_temperature": 2030}
assert devices.endpoints_of(view("hb:therm", "thermostat", {"target": 44}))[0].state["heating_setpoint"] == 3000, "set point limited to 5–30 °C"
assert devices.endpoints_of(view("hb:therm", "thermostat", {"temperature": 20})) == [], "no set point: nothing to share"
assert devices.endpoints_of(view("hb:cover", "cover", {"position": 25}))[0].state == {"position": 2500}
assert devices.endpoints_of(view("hb:cover", "cover", {}))[0].state == {}
assert devices.endpoints_of(view("hb:win", "contact", {"open": True}))[0].state == {"contact": False}, "Matter: true = closed"
assert devices.endpoints_of(view("hb:win", "contact", {"open": False}))[0].state == {"contact": True}
assert devices.endpoints_of(view("hb:pir", "motion", {"motion": True}))[0].state == {"occupied": True}
assert devices.endpoints_of(view("hb:smoke", "smoke", {"alarm": False}))[0].state == {"smoke": False}
sensor = devices.endpoints_of(view("mqtt:klima", "sensor", {"temperature": 21.3, "humidity": 55}, name="Klima"))
assert [(e.key, e.kind, e.name, e.state) for e in sensor] == [
    ("mqtt:klima", devices.TEMPERATURE_SENSOR, "Klima", {"temperature": 2130}),
    ("mqtt:klima#humidity", devices.HUMIDITY_SENSOR, "Klima Feuchte", {"humidity": 5500})]
assert [e.kind for e in devices.endpoints_of(view("x:hum", "sensor", {"humidity": 40}))] == [devices.HUMIDITY_SENSOR]
for unsupported in ("presence", "media", "unknown"):
    assert devices.endpoints_of(view("x:1", unsupported, {"present": True})) == []
assert devices.endpoints_of(view("matter:peer2-3", "light", {"on": True})) == [], "devices of the own controller are never bridged back"
offline = devices.endpoints_of(view("hb:off", "switch", {"on": False}, online=False, locked=True))[0]
assert offline.reachable is False and offline.locked is True
assert devices.endpoints_of(view("hb:bool", "light", {"on": True, "brightness": True}))[0].kind == devices.ON_OFF_LIGHT, "a boolean is no brightness"
print("2 bridge: lamp (4 types), socket, thermostat, cover, contact, motion, smoke, temperature and humidity – each with Matter values")

# ---------------------------------------------------------------------------
# 3) bridge: what a controller wants -> ComfyOS command
# ---------------------------------------------------------------------------
assert devices.command_state({"on": True}) == {"on": True} and devices.command_state({"on": False}) == {"on": False}
assert devices.command_state({"level": 127, "on": True}) == {"on": True, "brightness": 50}
assert devices.command_state({"level": 127}) == {"brightness": 50}
assert devices.command_state({"level": 3, "on": False}) == {"on": False}, "off wins"
assert devices.command_state({"mireds": 250}) == {"mirek": 250}
assert devices.command_state({"x": 32768, "y": 26214}) == {"xy": [0.5, 0.4]}
assert devices.command_state({"heating_setpoint": 2150}) == {"target": 21.5}
assert devices.command_state({"heating_setpoint": 2130}) == {"target": 21.5} and devices.command_state({"heating_setpoint": 9000}) == {"target": 30.0}
assert devices.command_state({"heating_setpoint": -500}) == {"target": 5.0}
assert devices.command_state({"position": 7000}) == {"position": 70} and devices.command_state({"cover": "stop"}) == {"cover": "stop"}
for nothing in ({}, {"x": 5}, {"contact": True}):
    try:
        devices.command_state(nothing)
        raise AssertionError(f"accepted: {nothing}")
    except PluginError:
        pass
print("3 bridge: on/off, level, white tone, colour, set point (5–30 °C, half degrees), cover – and nothing else – become commands")

# ---------------------------------------------------------------------------
# 4) controller: Matter device -> ComfyOS device, command -> Matter values
# ---------------------------------------------------------------------------
node, endpoint = remote([0x010D, 0x0013], {"on": True, "level": 127, "mireds": 300, "x": 32768, "y": 26214, "color_mode": "xy",
                                           "min_mireds": 153, "max_mireds": 454}, name="Stehlampe")
lamp = devices.device_of(node, endpoint)
assert (lamp.id, lamp.name, lamp.kind, lamp.online, lamp.discovered) == ("matter:peer2-3", "Stehlampe", "light", True, True)
assert lamp.state == {"on": True, "brightness": 50} and lamp.vendor == "Signify" and lamp.model == "Bridge"
assert lamp.light.capabilities.color and lamp.light.capabilities.white_range == (153, 454) and lamp.light.mode == "xy"
assert lamp.light.xy == [0.5, 0.4] and lamp.light.mirek == 300
assert {capability.key for capability in lamp.capabilities if capability.writable} == {"on", "brightness"}
assert devices.split_device_id(lamp.id) == ("peer2", 3) and devices.split_device_id("matter:peer-with-dash-12") == ("peer-with-dash", 12)
for bad_id in ("matter:peer2", "hue:peer2-3", "matter:-3", "matter:peer2-x"):
    try:
        devices.split_device_id(bad_id)
        raise AssertionError(f"accepted id: {bad_id}")
    except PluginError:
        pass
expected = {
    0x0100: ("light", {"on": False}, {"on": False}), 0x0101: ("light", {"on": True, "level": 254}, {"on": True, "brightness": 100}),
    0x010C: ("light", {"on": True, "level": 3, "mireds": 370, "color_mode": "ct"}, {"on": True, "brightness": 1}),
    0x010A: ("switch", {"on": True}, {"on": True}), 0x010B: ("switch", {"on": False}, {"on": False}),
    0x0301: ("thermostat", {"heating_setpoint": 2100, "local_temperature": 1980, "min_heat": 700, "max_heat": 2800}, {"target": 21.0, "temperature": 19.8}),
    0x0202: ("cover", {"position": 2500}, {"position": 25}), 0x0015: ("contact", {"contact": False}, {"open": True}),
    0x0107: ("motion", {"occupied": True}, {"motion": True}), 0x0302: ("sensor", {"temperature": 2130}, {"temperature": 21.3}),
    0x0307: ("sensor", {"humidity": 5500}, {"humidity": 55}), 0x0076: ("smoke", {"smoke": True}, {"alarm": True}),
}
for type_id, (kind, matter_state, comfy_state) in expected.items():
    made = devices.device_of(*remote([type_id], matter_state))
    assert made is not None and made.kind == kind and made.state == comfy_state, (hex(type_id), made)
assert devices.device_of(*remote([0x0100], {"on": True})).light is None, "an on/off lamp has no light state"
assert devices.device_of(*remote([0x0101], {"on": True, "level": 10})).light is not None
limits = next(capability for capability in devices.device_of(*remote([0x0301], expected[0x0301][1])).capabilities if capability.key == "target")
assert (limits.minimum, limits.maximum, limits.writable) == (7.0, 28.0, True)
for no_device in ([22], [14], [19], [17], [], [0xFFFF]):
    assert devices.device_of(*remote(no_device, {})) is None, "root node, aggregator and unknown types are no devices"
assert devices.device_of(*remote([0x010A], {"on": True}, online=False)).online is False
assert devices.device_of(*remote([0x010A], {"on": True}, reachable=False)).online is False
assert devices.device_of(*remote([0x010A], {"on": True}, name="")).name == "Fremde Bridge", "an endpoint without a name takes the node's"
assert devices.invoke_state({"on": True}) == {"on": True} and devices.invoke_state({"on": False, "brightness": 40}) == {"on": False}
assert devices.invoke_state({"brightness": 50}) == {"level": 127} and devices.invoke_state({"brightness": 0}) == {"on": False}
assert devices.invoke_state({"on": True, "brightness": 50, "mirek": 300}) == {"level": 127, "mireds": 300}
assert devices.invoke_state({"kelvin": 2700}) == {"mireds": 370} and devices.invoke_state({"xy": [0.5, 0.4]}) == {"x": 32768, "y": 26214}
assert set(devices.invoke_state({"color": "#ff0000"})) == {"x", "y"} and devices.invoke_state({"target": 21.5}) == {"heating_setpoint": 2150}
assert devices.invoke_state({"position": 70}) == {"position": 7000} and devices.invoke_state({"cover": "up"}) == {"cover": "up"}
for nothing in ({}, {"effect": "candle"}, {"color": "rot"}, {"cover": "explode"}, {"brightness": "hell"}):
    try:
        devices.invoke_state(nothing)
        raise AssertionError(f"accepted: {nothing}")
    except PluginError:
        pass
assert devices.values_after({"on": True, "brightness": 40.4}) == {"on": True, "brightness": 40}
assert devices.values_after({"brightness": 0}) == {"on": False} and devices.values_after({"target": 21.5}) == {"target": 21.5}
print(f"4 controller: {len(expected)} Matter device types become ComfyOS devices; commands become Matter values")

# ---------------------------------------------------------------------------
# 5) the home as devices
# ---------------------------------------------------------------------------
views = [view("hue:1", "light", {"on": True, "brightness": 40}, room_id="wohnen", room="Wohnzimmer"),
         view("hue:2", "light", {"on": True, "brightness": 80}, room_id="wohnen", room="Wohnzimmer"),
         view("hue:3", "light", {"on": False, "brightness": 10}, room_id="kueche", room="Küche"),
         view("hb:plug", "switch", {"on": True}, room_id="kueche", room="Küche"),
         view("hue:4", "light", {"on": True}),                                               # no room
         view("matter:peer2-3", "light", {"on": True}, room_id="bad", room="Bad")]           # own controller device
scenes = [SceneView("abendrot", "Abendrot", ("wohnen",), True), SceneView("lesen", "Lesen", ("wohnen", "kueche"), False),
          SceneView("ohne", "Ohne Raum", (), False)]
offered = {endpoint.key: endpoint for endpoint in home.home_endpoints(views, scenes, "AWAY")}
assert list(offered) == ["room:kueche", "room:wohnen", "all-lights", "scene:abendrot", "scene:lesen", "home"], list(offered)
assert offered["room:wohnen"].name == "Wohnzimmer Licht" and offered["room:wohnen"].kind == devices.DIMMABLE_LIGHT
assert offered["room:wohnen"].state == {"on": True, "level": devices.brightness_to_level(60)}, "mean brightness of the lamps that are on"
assert offered["room:kueche"].state == {"on": False, "level": 254}
assert offered["all-lights"].name == "Alle Lichter" and offered["all-lights"].state["on"] is True
assert offered["scene:abendrot"].state == {"on": True} and offered["scene:lesen"].state == {"on": False}
assert offered["scene:abendrot"].kind == devices.ON_OFF_PLUG and offered["home"].state == {"on": False}
assert home.home_endpoints(views, scenes, "PRESENT")[-1].state == {"on": True}
assert [e.key for e in home.home_endpoints([], [], None)] == [], "without lamps, scenes and home state nothing is offered"
assert all(home.is_home_key(key) for key in offered) and not home.is_home_key("hue:1") and not home.is_home_key("homematic:1")


async def home_commands() -> None:
    """Commands to the virtual devices reach the matching helper of ComfyOS."""
    context = FakeContext("matter", access=("devices", "rooms", "scenes", "home"))
    await home.run_command(context, "room:wohnen", {"on": False})
    await home.run_command(context, "room:wohnen", {"level": 127, "on": True})
    await home.run_command(context, "all-lights", {"on": True})
    assert context.room_light_calls == [("wohnen", False, None), ("wohnen", True, 50.0), (None, True, None)]
    await home.run_command(context, "scene:abendrot", {"on": True})
    await home.run_command(context, "scene:abendrot", {"on": False})
    assert context.scene_calls == [("abendrot", True), ("abendrot", False)]
    await home.run_command(context, "home", {"on": False})
    await home.run_command(context, "home", {"on": True})
    assert context.home_calls == ["AWAY", "PRESENT"]
    for key, wanted in (("room:wohnen", {}), ("scene:abendrot", {"level": 5}), ("home", {}), ("nix", {"on": True}), ("room:wohnen", {"mireds": 300})):
        try:
            await home.run_command(context, key, wanted)
            raise AssertionError(f"accepted: {key} {wanted}")
        except PluginError:
            pass
    without = FakeContext("matter", access=("devices",))
    try:
        await home.run_command(without, "home", {"on": True})
        raise AssertionError("switched the home state without permission")
    except PluginError:
        pass


asyncio.run(home_commands())
print("5 the home as devices: room lights, all lights, house scenes, at home – offered and switched through ComfyOS")

# ---------------------------------------------------------------------------
# 6) share list
# ---------------------------------------------------------------------------
settings = MemoryStore()
share_list = shares.ShareList(settings)
assert share_list.shared_keys == [] and share_list.select(sensor) == [], "nothing is shared by default"
share_list.set_shared("mqtt:klima", True, 0, 2)
first = share_list.select(sensor)
assert [(number, endpoint.key) for number, endpoint in first] == [(10, "mqtt:klima"), (11, "mqtt:klima#humidity")]
assert share_list.key_of(11) == "mqtt:klima#humidity" and share_list.key_of(99) is None
share_list.set_shared("mqtt:klima", False, 2)
assert share_list.select(sensor) == [] and share_list.number_of("mqtt:klima") == 10, "a number stays with its key while not shared"
share_list.set_shared("hb:plug", True, 0)
share_list.set_shared("mqtt:klima", True, 1, 2)
plug = devices.endpoints_of(view("hb:plug", "switch", {"on": True}))
assert [number for number, _ in share_list.select(plug + sensor)] == [12, 10, 11]
reloaded = shares.ShareList(settings)                                                   # after a restart
assert reloaded.shared_keys == ["hb:plug", "mqtt:klima"] and reloaded.number_of("mqtt:klima#humidity") == 11
assert reloaded.number_of("neu:1") == 13, "numbers are never handed out twice"
try:
    share_list.set_shared("matter:peer2-3", True, 0)
    raise AssertionError("shared a device of the own controller")
except PluginError as error:
    assert "Matter" in str(error)
share_list.set_shared("hb:viele", True, shares.ADVISED_ENDPOINTS + 10)        # more than the advice is allowed since 0.21.1
assert share_list.is_shared("hb:viele")
try:
    share_list.set_shared("hb:zuviel", True, shares.MAX_SHARED_ENDPOINTS)
    raise AssertionError("shared beyond the technical bound")
except PluginError as error:
    assert str(shares.MAX_SHARED_ENDPOINTS) in str(error)
try:
    share_list.set_shared("hb:zwei", True, shares.MAX_SHARED_ENDPOINTS - 1, 2)
    raise AssertionError("a device with two endpoints exceeded the bound")
except PluginError:
    pass
bulk_store = MemoryStore()
bulk = shares.ShareList(bulk_store)
assert bulk.set_many(["a:1", "a:2", "a:2", "a:3"], True, 0, 3) == 3 and bulk.shared_keys == ["a:1", "a:2", "a:3"]
assert bulk.set_many(["a:2", "a:3", "x:9"], False, 3, 0) == 2 and bulk.shared_keys == ["a:1"]
assert bulk.set_many(["a:1"], True, 1, 0) == 0, "nothing new, nothing written"
try:
    bulk.set_many(["a:5", "matter:peer2-3"], True, 0, 2)
    raise AssertionError("a bulk share included a device of the own controller")
except PluginError:
    assert bulk.shared_keys == ["a:1"], "a refused bulk share must change nothing"
writes = []
original_set = bulk_store.set
bulk_store.set = lambda key, value: (writes.append(key), original_set(key, value))
bulk.set_many([f"b:{number}" for number in range(300)], True, 1, 300)
crowd_now = [devices.SharedEndpoint(key=f"b:{number}", kind=devices.ON_OFF_PLUG, name=str(number)) for number in range(300)]
writes.clear()
assert len(bulk.select(crowd_now)) == 300 and writes.count("endpoints") == 1, writes.count("endpoints")
assert bulk.summary(300) == {"used": 300, "advice": shares.ADVISED_ENDPOINTS}
for bad_key in ("", "x" * 500):
    try:
        share_list.set_shared(bad_key, True, 0)
        raise AssertionError("accepted an unusable key")
    except PluginError:
        pass
many = shares.ShareList(MemoryStore())
crowd = [devices.SharedEndpoint(key=f"hb:{number}", kind=devices.ON_OFF_PLUG, name=f"Gerät {number}")
         for number in range(shares.MAX_SHARED_ENDPOINTS + 20)]
many._shared = [endpoint.key for endpoint in crowd]                                     # as if stored beyond the bound
assert len(many.select(crowd)) == shares.MAX_SHARED_ENDPOINTS, "the technical bound also holds for a stored list"
damaged = MemoryStore()
damaged.set("shared", ["ok:1", 5, None, "x" * 500])
damaged.set("endpoints", {"ok:1": 20, "doppelt": 20, "zu-klein": 3, "text": "x", "bool": True})
damaged.set("next_endpoint", "viel")
recovered = shares.ShareList(damaged)
assert recovered.shared_keys == ["ok:1"] and recovered.number_of("ok:1") == 20 and recovered.number_of("neu") == 21
assert shares.share_key_of("mqtt:klima#humidity") == "mqtt:klima" and shares.share_key_of("room:wohnen") == "room:wohnen"
print("6 share list: nothing shared by default, permanent endpoint numbers, several at once with one write, no fixed limit "
      "(only a technical bound of 1000), no devices of the own controller")

print("ALL MATTER DEVICE TESTS OK")
