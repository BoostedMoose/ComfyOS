"""Test world "light and scenes": rooms with Hue lamps (gradient, white tone, colour), a mock Home Base and a rebuilt
Hue Bridge that records every command. Shared by tests/core/test_scenes.py, the UI test ui_scenes and
tools/readme_screenshot.py.

On import the whole world is created: isolated environment, app, mocks. Whoever uses it imports the names they
need – only the contents (lists, dictionaries) are changed, never the names themselves.
"""
import json
import sys
import time as _time
from pathlib import Path

from . import boot  # noqa: E402

ROOT, tmp = boot.isolate("scenes")

import httpx  # noqa: E402

from app import auth, core, effects_store, egress, flow_engine, light_model as lm, main, plugins, scenes_store  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402
from support.homebase import build_devices  # noqa: E402

BR = "ecb5faaabbcc"

# ------------------------------------------------------------------ 1) Reines Modell
assert lm.hex_to_xy("#ff0000") == (0.7006, 0.2993), lm.hex_to_xy("#ff0000")
for h in ("#ff0000", "#00ff00", "#3355ff", "#ffaa00"):
    back = lm.hex_to_rgb(lm.xy_to_hex(*lm.hex_to_xy(h)))
    want = lm.hex_to_rgb(h)
    assert max(abs(a - b) for a, b in zip(back, want)) <= 12, (h, back)
assert lm.kelvin_to_mirek(2700) == 370 and 150 <= lm.xy_to_mirek(0.3127, 0.329) <= 160
assert lm.xy_to_mirek(0.7, 0.3, (153, 454)) == 454                                  # red -> warmest white tone of the lamp
CAP_ALL = {"caps": {"ct": [153, 500], "color": True, "gradient": 5, "gradient_modes": ["interpolated_palette", "random_pixelated"],
                    "effects": ["candle", "fire"]}}
CAP_CT = {"caps": {"ct": [153, 454], "color": False, "gradient": 0, "gradient_modes": [], "effects": []}}
CAP_DIM = {"caps": {"ct": None, "color": False, "gradient": 0, "gradient_modes": [], "effects": []}}
devs = [{"id": "a", "light": CAP_ALL}, {"id": "b", "light": CAP_CT}, {"id": "c", "light": None}]
plan = lm.scene_states([{"c": "#ff0000", "b": 50}, {"ct": 300, "b": 90}], devs)
assert plan[0][1]["gradient"] == ["#ff0000", lm.mirek_to_hex(300)] and plan[0][1]["brightness"] == 70, plan[0]
assert plan[1][1]["mirek"] == 300 and plan[1][1]["brightness"] == 90 and plan[2][1] == {"on": True}, plan
assert lm.scene_states([{"c": "#ff0000", "b": 50}, {"c": "#00ff00", "b": 50}], devs[1:], offset=1)[0][1]["color"] == "#00ff00"
print("1 Modell: Farbräume, Befehle je Fähigkeit (Farbe -> Weißton -> nur Helligkeit), Verteilung der Palette")

# ------------------------------------------------------------------ Mock Home Base
def ch(cid, itype, item):
    """Channel of a device as the Home Base reports it."""
    return {"id": cid, "itemType": itype, "linkedItems": [item]}


def hue_thing(n, label, room, tt="0210"):
    """A Hue lamp the way the Home Base reports it."""
    return {"UID": f"hue:{tt}:{BR}:{n}", "thingTypeUID": f"hue:{tt}", "label": label, "location": room,
            "statusInfo": {"status": "ONLINE"}, "channels": [ch("color", "Color", f"L{n}_color")]}


THINGS = [hue_thing(1, "Lightstrip", "wohnen"), hue_thing(2, "Deckenlampe", "wohnen"), hue_thing(3, "Ambiance", "flur"),
          hue_thing(4, "Weiß", "wohnen"), hue_thing(5, "Steckdose", "wohnen", "0010"),
          {"UID": "zigbee:bulb:z1", "thingTypeUID": "zigbee:bulb", "label": "Stehlampe", "location": "wohnen",
           "statusInfo": {"status": "ONLINE"}, "channels": [ch("brightness", "Dimmer", "Z_bri")]}]
ITEMS = [{"name": f"L{n}_color", "state": "0,0,0"} for n in range(1, 6)] + [
    {"name": "Z_bri", "state": "0"}, {"name": "wohnen", "label": "Wohnzimmer", "type": "Group"},
    {"name": "flur", "label": "Flur", "type": "Group"}]
hb_writes = []


class MockHB(QiviconConnector):
    """Home Base of the scenes world: devices from THINGS/ITEMS, write commands are recorded in hb_writes."""
    async def list_devices(self, max_age=None):
        """Build devices from THINGS and ITEMS and report the Hue Bridge the Home Base knows."""
        self.hue_bridges = [{"uid": "hue:bridge:" + BR, "mac": BR, "host": "192.0.2.10"}]
        return build_devices(THINGS, ITEMS, [], self.room_names_provider() if self.room_names_provider else {})

    async def list_rooms(self):
        """The two rooms of the scenes world."""
        return [{"name": "wohnen", "label": "Wohnzimmer"}, {"name": "flur", "label": "Flur"}]

    async def _write_item(self, item, command):
        """Record a command and – like the real Home Base – change the state of the item."""
        hb_writes.append((item, command))
        for it in ITEMS:
            if it["name"] == item:
                it["state"] = {"ON": "100", "OFF": "0"}.get(command, command)

    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}


# ------------------------------------------------------------------ Mock Hue Bridge
U = lambda n: f"00000000-0000-4000-8000-{n:012d}"  # noqa: E731
LIGHTS = {
    1: dict(name="Strip", on=True, bri=60.0, xy=(0.5, 0.4), ct=(153, 500), mirek=None, grad=5, fx=True, fx2=True),
    2: dict(name="Decke", on=True, bri=80.0, xy=(0.45, 0.41), ct=(153, 500), mirek=366, fx=True),
    3: dict(name="Ambiance", on=False, bri=50.0, ct=(153, 454), mirek=250),
    4: dict(name="Keller", on=True, bri=100.0),
    5: dict(name="Stecker", on=False, plug=True),
}
puts = []


def light_res(n, li):
    """A lamp as a resource of the Hue Bridge – with exactly the capabilities its entry names."""
    r = {"id": U(n), "id_v1": f"/lights/{n}", "owner": {"rid": U(1000 + n), "rtype": "device"},
         "metadata": {"name": li["name"], "archetype": "plug" if li.get("plug") else "sultan_bulb"}, "on": {"on": li["on"]}}
    if not li.get("plug"):
        r["dimming"] = {"brightness": li["bri"]}
    if li.get("ct"):
        r["color_temperature"] = {"mirek": li["mirek"], "mirek_valid": li["mirek"] is not None,
                                  "mirek_schema": {"mirek_minimum": li["ct"][0], "mirek_maximum": li["ct"][1]}}
    if li.get("xy"):
        r["color"] = {"xy": {"x": li["xy"][0], "y": li["xy"][1]}, "gamut_type": "C"}
    if li.get("grad"):
        r["gradient"] = {"points": [], "points_capable": li["grad"], "mode": "interpolated_palette",
                         "mode_values": ["interpolated_palette", "interpolated_palette_mirrored", "random_pixelated"]}
    if li.get("fx"):
        r["effects"] = {"effect_values": ["no_effect", "candle", "fire", "prism"], "status": li.get("fx_status", "no_effect")}
    if li.get("fx2"):   # neuere Lampen: Effekt mit eigener Farbe/Tempo (CLIP v2 effects_v2)
        r["effects_v2"] = {"action": {"effect_values": ["no_effect", "candle", "fire", "prism"]},
                           "status": {"effect": li.get("fx_status", "no_effect")}}
    return r


BRIDGE_SCENES = [
    {"id": U(7003), "metadata": {"name": "Tropendämmerung", "image": {"rid": U(8001), "rtype": "public_image"}},
     "group": {"rid": U(3002), "rtype": "room"},
     "palette": {"color": [{"color": {"xy": {"x": 0.6, "y": 0.35}}, "dimming": {"brightness": 70}}], "color_temperature": []}},
    {"id": U(7001), "metadata": {"name": "Tropendämmerung", "image": {"rid": U(8001), "rtype": "public_image"}},
     "group": {"rid": U(3000), "rtype": "room"},
     "palette": {"color": [{"color": {"xy": {"x": 0.6, "y": 0.35}}, "dimming": {"brightness": 70}},
                           {"color": {"xy": {"x": 0.17, "y": 0.7}}, "dimming": {"brightness": 50}}], "color_temperature": []},
     "actions": [], "auto_dynamic": True},
    {"id": U(7002), "metadata": {"name": "Lesen", "image": {"rid": U(8002), "rtype": "public_image"}},
     "group": {"rid": U(3001), "rtype": "zone"}, "palette": {},
     "actions": [{"target": {"rid": U(2), "rtype": "light"}, "action": {"on": {"on": True}, "dimming": {"brightness": 100},
                                                                       "color_temperature": {"mirek": 233}}}]},
]


def handler(req: httpx.Request) -> httpx.Response:
    """Responses of the Hue Bridge: pairing, resources, commands to lamps and groups, scenes."""
    path = req.url.path
    ok = lambda data: httpx.Response(200, json={"errors": [], "data": data})  # noqa: E731
    if path == "/api/0/config":
        return httpx.Response(200, json={"bridgeid": "ECB5FAFFFEAABBCC", "name": "Hue Bridge Test"})
    if path == "/api" and req.method == "POST":
        return httpx.Response(200, json=[{"success": {"username": "KEY123"}}])
    if req.headers.get("hue-application-key") != "KEY123":
        return httpx.Response(403, json={"errors": [{"description": "unauthorized user"}], "data": []})
    if path == "/eventstream/clip/v2":
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b": hi\n\n")
    if path == "/clip/v2/resource/bridge":
        return ok([{"id": U(999), "bridge_id": "ecb5fafffeaabbcc"}])
    if path == "/clip/v2/resource/light" and req.method == "GET":
        return ok([light_res(n, li) for n, li in LIGHTS.items()])
    if path == "/clip/v2/resource/device":
        return ok([{"id": U(1000 + n), "id_v1": f"/lights/{n}", "metadata": {"name": li["name"]},
                    "product_data": {"product_name": "Hue", "manufacturer_name": "Signify"}} for n, li in LIGHTS.items()])
    if path == "/clip/v2/resource/zigbee_connectivity":
        return ok([{"id": U(2000 + n), "owner": {"rid": U(1000 + n)}, "status": "connected"} for n in LIGHTS])
    if path == "/clip/v2/resource/room":
        return ok([{"id": U(3000), "metadata": {"name": "Wohnzimmer"}, "children": []},
                   {"id": U(3002), "metadata": {"name": "Zimmer"}, "children": []}])
    if path == f"/clip/v2/resource/public_image/{U(8001)}":
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG\r\n\x1a\nTEST")
    if path == "/clip/v2/resource/zone":
        return ok([{"id": U(3001), "metadata": {"name": "Leseecke"}, "children": []}])
    if path == "/clip/v2/resource/scene":
        return ok(BRIDGE_SCENES)
    if path.startswith("/clip/v2/resource/light/") and req.method == "PUT":
        rid, body = path.rsplit("/", 1)[1], json.loads(req.content)
        n = next(n for n in LIGHTS if U(n) == rid)
        puts.append((n, body))
        li = LIGHTS[n]
        if "on" in body:
            li["on"] = body["on"]["on"]
        if "dimming" in body:
            li["bri"] = body["dimming"]["brightness"]
        if "color_temperature" in body:
            li["mirek"] = body["color_temperature"]["mirek"]
        if "color" in body:
            li["xy"], li["mirek"] = (body["color"]["xy"]["x"], body["color"]["xy"]["y"]), None
        if "effects" in body:
            li["fx_status"] = body["effects"]["effect"]
        if "effects_v2" in body:
            li["fx_status"] = body["effects_v2"]["action"]["effect"]
        return ok([{"rid": rid, "rtype": "light"}])
    return httpx.Response(404, json={"errors": [{"description": "not found " + path}], "data": []})


transport = httpx.MockTransport(handler)
egress._lan_transport = transport      # Heimnetz-Client des Hue-Plugins


def bridge():
    """The connection of the Hue plugin to the bridge."""
    return plugins.active("hue").bridge
