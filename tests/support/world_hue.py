"""Test world "Home Base + Hue Bridge": a mock Home Base with Hue things and a rebuilt Hue Bridge (CLIP v2
with live messages). Shared by tests/plugins/test_hue.py and the UI tests ui_system and ui_home.

On import the whole world is created: isolated environment, app, mocks. Whoever uses it imports the names they
need – only the contents (lists, dictionaries) are changed, never the names themselves.
"""
import json
import sys
from pathlib import Path

from . import boot  # noqa: E402

ROOT, tmp = boot.isolate("hue")

import httpx  # noqa: E402

from app import auth, core, device_store, egress, main, plugins  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402
from support.homebase import build_devices  # noqa: E402

BR = "ecb5faaabbcc"

# ------------------------------------------------------------------ Mock Home Base
def ch(cid, itype, item):
    """Channel of a device as the Home Base reports it."""
    return {"id": cid, "itemType": itype, "linkedItems": [item]}


THINGS = [
    {"UID": f"hue:bridge:{BR}", "thingTypeUID": "hue:bridge", "label": "Hue Bridge",
     "configuration": {"ipAddress": "192.0.2.10"}, "statusInfo": {"status": "ONLINE"}, "channels": []},
    {"UID": f"hue:0210:{BR}:1", "thingTypeUID": "hue:0210", "label": "Deckenlampe", "location": "kueche",
     "statusInfo": {"status": "ONLINE"}, "channels": [ch("color", "Color", "L1_color")]},
    {"UID": f"hue:0210:{BR}:9", "thingTypeUID": "hue:0210", "label": "Bodenlampe", "location": "wohnen",
     "statusInfo": {"status": "ONLINE"}, "channels": [ch("color", "Color", "L9_color")]},
    {"UID": f"hue:0210:{BR}:5", "thingTypeUID": "hue:0210", "label": "Alte Lampe", "location": "flur",
     "statusInfo": {"status": "OFFLINE"}, "channels": [ch("color", "Color", "L5_color")]},
    {"UID": f"hue:motionSensor:{BR}:001788010b000001", "thingTypeUID": "hue:motionSensor", "label": "Bewegung Flur",
     "location": "flur", "statusInfo": {"status": "ONLINE"}, "channels": [ch("presence", "Switch", "M_presence")]},
    {"UID": "zigbee:bulb:abc", "thingTypeUID": "zigbee:bulb", "label": "Flurlicht", "location": "flur",
     "statusInfo": {"status": "ONLINE"}, "channels": [ch("brightness", "Dimmer", "Z_bri")]},
    {"UID": "zwave:therm:1", "thingTypeUID": "zwave:thermostat", "label": "Heizung Küche", "location": "kueche",
     "statusInfo": {"status": "ONLINE"},
     "channels": [ch("temperature", "Number", "T_temp"), ch("setpointtemperature", "Number", "T_set")]},
]
ITEMS = [{"name": "L1_color", "state": "0,0,50"}, {"name": "L9_color", "state": "0,0,0"},
         {"name": "L5_color", "state": "NULL"}, {"name": "M_presence", "state": "OFF"}, {"name": "Z_bri", "state": "100"},
         {"name": "T_temp", "state": "20.5"}, {"name": "T_set", "state": "21"},
         {"name": "kueche", "label": "Küche", "type": "Group"}, {"name": "wohnen", "label": "Wohnzimmer", "type": "Group"},
         {"name": "flur", "label": "Flur", "type": "Group"}]
hb_writes = []


class MockHB(QiviconConnector):
    """Home Base of the Hue world: devices from THINGS/ITEMS, write commands are recorded in hb_writes."""
    async def list_devices(self, max_age=None):
        """Build devices from THINGS and ITEMS and report the Hue Bridge the Home Base knows."""
        devs = build_devices(THINGS, ITEMS, [], self.room_names_provider() if self.room_names_provider else {})
        self.hue_bridges = [{"uid": t["UID"], "mac": BR, "host": t["configuration"]["ipAddress"]}
                            for t in THINGS if t["thingTypeUID"] == "hue:bridge"]
        return devs

    async def list_rooms(self):
        """The three rooms of the Hue world."""
        return [{"name": "kueche", "label": "Küche"}, {"name": "wohnen", "label": "Wohnzimmer"}, {"name": "flur", "label": "Flur"}]

    async def _write_item(self, item, command):
        """Record a command and – like the real Home Base – change the state of the item."""
        hb_writes.append((item, command))
        for it in ITEMS:   # like the real Home Base: a command changes the item state
            if it["name"] == item:
                it["state"] = {"ON": "100", "OFF": "0"}.get(command, command)

    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}

    async def _rpc_get(self, path):
        """Only the inbox is allowed (a new device); every other read access is an error."""
        if path == "/rest/inbox":
            return [{"thingUID": "zigbee:plug:neu1", "label": "Neue Steckdose", "flag": "NEW", "thingTypeUID": "zigbee:plug"}]
        raise AssertionError("unerwarteter Lesezugriff " + path)

    async def move_device(self, device_id, room_id):
        """Must never be called in this world – Hue lamps only change their room in ComfyOS."""
        raise AssertionError(f"move_device darf für {device_id} nicht aufgerufen werden")


# ------------------------------------------------------------------ Mock Hue Bridge
U = lambda n: f"00000000-0000-4000-8000-{n:012d}"  # noqa: E731
LIGHTS = {
    1: dict(name="Hue Decke", on=True, bri=80.0, zb="connected"),
    9: dict(name="Hue Boden", on=False, bri=0.0, zb="connected"),
    18: dict(name="Neue Lampe", on=False, bri=0.0, zb="connected"),
    19: dict(name="Kaputte Lampe", on=False, bri=0.0, zb="connectivity_issue"),
    20: dict(name="Hue Stecker", on=False, bri=None, zb="connected", arch="plug"),
}
import queue as _queue  # noqa: E402
hue = {"pair_tries": 0, "down": False, "puts": [], "deletes": [], "renames": [], "events": _queue.Queue(), "streams": 0,
       "motion": False, "motion_zb": "connected"}


async def _event_body():
    """Event stream of the bridge: delivers what push_event queues."""
    import asyncio as _a
    yield b": hi\n\n"
    while True:
        try:
            ev = hue["events"].get_nowait()
        except _queue.Empty:
            await _a.sleep(0.05)
            continue
        yield ("data: " + json.dumps(ev) + "\n\n").encode()


def push_event(kind, *res):
    """Queue an event of the bridge (arrives at ComfyOS through the stream)."""
    hue["events"].put([{"type": kind, "id": "e", "data": list(res)}])


def handler(req: httpx.Request) -> httpx.Response:
    """Responses of the Hue Bridge: pairing (only on the second attempt), resources, commands, scenes, event stream."""
    if hue["down"]:
        raise httpx.ConnectError("down", request=req)
    path = req.url.path
    if path == "/api/0/config":
        return httpx.Response(200, json={"bridgeid": "ECB5FAFFFEAABBCC", "name": "Hue Bridge Test", "mac": "ec:b5:fa:aa:bb:cc"})
    if path == "/api" and req.method == "POST":
        hue["pair_tries"] += 1
        if hue["pair_tries"] == 1:
            return httpx.Response(200, json=[{"error": {"type": 101, "description": "link button not pressed"}}])
        return httpx.Response(200, json=[{"success": {"username": "KEY123", "clientkey": "x"}}])
    if req.headers.get("hue-application-key") != "KEY123":
        return httpx.Response(403, json={"errors": [{"description": "unauthorized user"}], "data": []})
    if path == "/eventstream/clip/v2":
        hue["streams"] += 1
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_event_body())
    if path == "/clip/v2/resource/bridge":
        return httpx.Response(200, json={"errors": [], "data": [{"id": U(999), "bridge_id": "ecb5fafffeaabbcc"}]})
    if path == "/clip/v2/resource/light" and req.method == "GET":
        data = [{"id": U(n), "id_v1": f"/lights/{n}", "owner": {"rid": U(1000 + n), "rtype": "device"},
                 "metadata": {"name": li["name"], "archetype": li.get("arch", "sultan_bulb")}, "on": {"on": li["on"]},
                 **({"dimming": {"brightness": li["bri"]}} if li["bri"] is not None else {})} for n, li in LIGHTS.items()]
        return httpx.Response(200, json={"errors": [], "data": data})
    if path == "/clip/v2/resource/device":
        data = [{"id": U(1000 + n), "id_v1": f"/lights/{n}", "metadata": {"name": li["name"], "archetype": li.get("arch", "sultan_bulb")},
                 "product_data": {"product_name": "Hue color lamp", "manufacturer_name": "Signify Netherlands B.V."}}
                for n, li in LIGHTS.items()]
        return httpx.Response(200, json={"errors": [], "data": data})
    if path == "/clip/v2/resource/zigbee_connectivity":
        data = [{"id": U(2000 + n), "owner": {"rid": U(1000 + n), "rtype": "device"}, "status": li["zb"]} for n, li in LIGHTS.items()]
        data.append({"id": U(2500), "owner": {"rid": U(1500), "rtype": "device"}, "status": hue["motion_zb"],
                     "mac_address": "00:17:88:01:0b:00:00:01"})
        return httpx.Response(200, json={"errors": [], "data": data})
    if path == "/clip/v2/resource/motion":
        return httpx.Response(200, json={"errors": [], "data": [
            {"id": U(4001), "type": "motion", "owner": {"rid": U(1500), "rtype": "device"},
             "motion": {"motion": hue["motion"], "motion_valid": True}}]})
    if path == "/clip/v2/resource/room":
        return httpx.Response(200, json={"errors": [], "data": [
            {"id": U(3000), "metadata": {"name": "küche"}, "children": [{"rid": U(1018), "rtype": "device"}]}]})
    if path.startswith("/clip/v2/resource/light/") and req.method == "PUT":
        rid = path.rsplit("/", 1)[1]
        on = json.loads(req.content)["on"]["on"]
        hue["puts"].append((rid, on))
        for n, li in LIGHTS.items():
            if U(n) == rid:
                li["on"] = on
        return httpx.Response(200, json={"errors": [], "data": [{"rid": rid, "rtype": "light"}]})
    if path.startswith("/clip/v2/resource/device/") and req.method == "PUT":
        rid = path.rsplit("/", 1)[1]
        name = json.loads(req.content)["metadata"]["name"]
        assert 1 <= len(name) <= 32, name
        hue["renames"].append((rid, name))
        for n in [n for n in LIGHTS if U(1000 + n) == rid]:
            LIGHTS[n]["name"] = name
        return httpx.Response(200, json={"errors": [], "data": [{"rid": rid, "rtype": "device"}]})
    if path.startswith("/clip/v2/resource/device/") and req.method == "DELETE":
        rid = path.rsplit("/", 1)[1]
        hue["deletes"].append(rid)
        for n in [n for n in LIGHTS if U(1000 + n) == rid]:
            del LIGHTS[n]
        return httpx.Response(200, json={"errors": [], "data": [{"rid": rid, "rtype": "device"}]})
    return httpx.Response(404, json={"errors": [{"description": "not found " + path}], "data": []})


transport = httpx.MockTransport(handler)
egress._lan_transport = transport              # all LAN clients of the plugins (context.http with scope "lan")
HUE = "/api/plugins/hue"
LID = lambda n: "huebridge:" + U(n)  # noqa: E731   device id of a lamp at the Hue plugin


def bridge():
    """The connection of the Hue plugin to the bridge."""
    return plugins.active("hue").bridge
