"""ComfyOS – devices of the Home Base: classification, controllability, covers, consumption, tile order.

    python tests/core/test_devices.py [path-to-comfyos]

Background: a device can only be operated if `build_devices` finds a control channel. Formerly
the list of allowed channel names was fixed – smart plugs with a different channel name stayed without
a switch. Therefore it is checked here for every device kind: right kind, right control channel and
that pure detectors (push button, tamper, smoke) never become a switch.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("dev")

from app import auth, core, floorplan_store, history, main  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402
from support.homebase import build_devices  # noqa: E402

PW = "comfyos" + "-test"


def ch(cid, itype, item, state=None):
    """Channel of a device with its state (the state moves into the items before delivery)."""
    return {"id": cid, "itemType": itype, "linkedItems": [item], "_state": state}


def things():
    """Device types the Home Base can pair – with the channel names as openHAB delivers them."""
    return [
        # smart plug with consumption metering: the channel is not called "switch" but "switchState"
        {"UID": "dect:sercomm_indoorSmartPlug_v1:aa", "thingTypeUID": "dect:sercomm_indoorSmartPlug_v1",
         "label": "TV", "location": "wohnen", "statusInfo": {"status": "ONLINE"},
         "channels": [ch("switchState", "Switch", "P1_sw"), ch("powerConsumption", "Number", "P1_pw"),
                      ch("energyConsumption", "Number", "P1_en")]},
        # socket of another maker: channel "relay", plus a meter reading in Wh
        {"UID": "zigbee:plug:bb", "thingTypeUID": "zigbee:plug", "label": "Stehlampe", "location": "wohnen",
         "statusInfo": {"status": "ONLINE"},
         "channels": [ch("relay", "Switch", "P2_sw"), ch("meterReading", "Number", "P2_en")]},
        # flush-mounted actuator without a telling type name – only the channel reveals the switch
        {"UID": "hmip:actor:cc", "thingTypeUID": "hmip:HmIP-FSM", "label": "Terrasse", "location": "flur",
         "statusInfo": {"status": "ONLINE"}, "channels": [ch("on_off", "Switch", "A1_sw")]},
        # Rollladen
        {"UID": "schellenberg:shutter:dd", "thingTypeUID": "schellenberg:rollershutter", "label": "Rollo Küche",
         "location": "kueche", "statusInfo": {"status": "ONLINE"}, "channels": [ch("position", "Rollershutter", "R1")]},
        # wall push button: switch channels that only report -> never a switch
        {"UID": "dect:button:ee", "thingTypeUID": "dect:wallButton", "label": "Taster Flur", "location": "flur",
         "statusInfo": {"status": "ONLINE"},
         "channels": [ch("button", "Switch", "B1"), ch("alertState-tamper", "Switch", "B1_tamper")]},
        # a smoke detector stays a smoke detector (the switch channel smokeAlarm must not switch)
        {"UID": "dect:smoke:ff", "thingTypeUID": "dect:smokeDetector", "label": "Rauch Flur", "location": "flur",
         "statusInfo": {"status": "ONLINE"}, "channels": [ch("alertState-smoke", "Switch", "S1")]},
        # pure electricity meter without a switch: consumption yes, switch no
        {"UID": "smappee:meter:gg", "thingTypeUID": "smappee:electricity", "label": "Zähler", "location": "flur",
         "statusInfo": {"status": "ONLINE"}, "channels": [ch("power", "Number", "M1_pw")]},
        # Dimmbare Lampe der Home Base
        {"UID": "zigbee:bulb:hh", "thingTypeUID": "zigbee:dimmableLight", "label": "Esstisch", "location": "kueche",
         "statusInfo": {"status": "ONLINE"}, "channels": [ch("brightness", "Dimmer", "L1_bri")]},
    ]


ITEMS = [{"name": "P1_sw", "state": "ON"}, {"name": "P1_pw", "state": "42.5 W"}, {"name": "P1_en", "state": "12.5 kWh"},
         {"name": "P2_sw", "state": "OFF"}, {"name": "P2_en", "state": "1500 Wh"},
         {"name": "A1_sw", "state": "OFF"}, {"name": "R1", "state": "100"},
         {"name": "B1", "state": "OFF"}, {"name": "B1_tamper", "state": "OFF"}, {"name": "S1", "state": "OFF"},
         {"name": "M1_pw", "state": "230"}, {"name": "L1_bri", "state": "60"},
         {"name": "wohnen", "label": "Wohnzimmer", "type": "Group"}, {"name": "kueche", "label": "Küche", "type": "Group"},
         {"name": "flur", "label": "Flur", "type": "Group"}]
writes = []


def clean(ts):
    """_state in the channels is only documentation – the states arrive as in reality through /rest/items."""
    for t in ts:
        for c in t["channels"]:
            c.pop("_state", None)
    return ts


class MockHB(QiviconConnector):
    """Home Base of this test: delivers the devices of all kinds and records write commands in writes."""
    def _needs_login(self):
        """The session is always valid."""
        return False

    async def _proxy(self, path, method="GET", payload=None, content_type=None):
        """Answer a call of the Home Base: devices, items, rooms; write commands are recorded."""
        if method == "POST" and path.startswith("/rest/items/"):
            writes.append((path.split("/rest/items/")[1], payload))
            return ""
        if path == "/rest/things":
            return clean(things())
        if path == "/rest/items":
            return ITEMS
        if path == "/rest/qivicon/devices":
            return []
        if path == "/rest/qivicon/rooms":
            return [{"name": k, "label": v} for k, v in (("wohnen", "Wohnzimmer"), ("kueche", "Küche"), ("flur", "Flur"))]
        return []

    async def status(self):
        """Immer verbunden."""
        return {"connected": True, "message": "Mock"}


def ok(r):
    """The response must be 200; returns its JSON content."""
    assert r.status_code == 200, (r.status_code, r.text[:300])
    return r.json()


def main_test():
    # put the mock Home Base in (plugin qivicon) and sign in
    """All checks of this file: classification and controllability per device kind, commands, merging, rooms."""
    auth.set_password(PW)
    install_homebase(MockHB("http://192.0.2.5"))
    from starlette.testclient import TestClient
    client = TestClient(main.app)
    ok(client.post("/api/auth/login", json={"password": PW}))
    rows = {d["id"]: d for d in ok(client.get("/api/devices"))}
    HB = lambda did: {"plugin": "qivicon", "device_id": did}   # noqa: E731

    # 1) Classification and controllability per device kind (through plugin and core down to the device row)
    plug = rows["dect:sercomm_indoorSmartPlug_v1:aa"]
    assert plug["kind"] == "switch" and plug["control"] == HB(plug["id"]) and plug["on"] is True, plug
    assert plug["power_w"] == 42.5 and plug["energy_kwh"] == 12.5 and plug["plugin"] == "qivicon", plug
    assert plug["room_id"] == "wohnen" and plug["room"] == "Wohnzimmer" and plug["raw_type"] == "dect:sercomm_indoorSmartPlug_v1", plug
    p2 = rows["zigbee:plug:bb"]
    assert p2["kind"] == "switch" and p2["control"] == HB(p2["id"]) and p2["on"] is False, p2
    assert p2["energy_kwh"] == 1.5 and p2.get("power_w") is None, p2       # Wh -> kWh, no power reported
    act = rows["hmip:actor:cc"]
    assert act["kind"] == "switch" and act["control_item"], act
    cover = rows["schellenberg:shutter:dd"]
    assert cover["kind"] == "cover" and cover["control_item"] and cover["position"] == 100, cover
    bulb = rows["zigbee:bulb:hh"]
    assert bulb["kind"] == "light" and bulb["control_item"] and bulb["brightness"] == 60 and bulb["light"] is None, bulb
    meter = rows["smappee:meter:gg"]
    assert meter["power_w"] == 230 and not meter.get("control_item") and "control" not in meter, meter
    print("1 smart plug/actuator/cover/lamp classified correctly and switchable, meter without a switch")

    # 2) Pure detectors never become a switch
    btn, smoke = rows["dect:button:ee"], rows["dect:smoke:ff"]
    assert not btn.get("control_item") and "control" not in btn, f"push button as a switch: {btn}"
    assert smoke["kind"] == "smoke" and not smoke.get("control_item"), smoke
    print("2 push button, tamper and smoke channels stay detectors (no switch, no false switching)")

    # 3) Switching through the API: plug, cover (up/down/stop/position), limits
    cmd = lambda did, state: client.post(f"/api/devices/{did}/command", json={"state": state})   # noqa: E731
    ok(cmd("dect:sercomm_indoorSmartPlug_v1:aa", {"on": False}))
    assert writes[-1] == ("P1_sw", "OFF"), writes
    ok(cmd("hmip:actor:cc", {"on": True}))
    assert writes[-1] == ("A1_sw", "ON"), writes
    for move, word in (("up", "UP"), ("down", "DOWN"), ("stop", "STOP")):
        ok(cmd("schellenberg:shutter:dd", {"cover": move}))
        assert writes[-1] == ("R1", word), writes
    ok(cmd("schellenberg:shutter:dd", {"position": 40}))
    assert writes[-1] == ("R1", "40"), writes
    assert cmd("schellenberg:shutter:dd", {"position": 400}).status_code == 400
    assert cmd("schellenberg:shutter:dd", {"cover": "rm -rf"}).status_code == 400
    assert cmd("dect:button:ee", {"on": True}).status_code == 400, "the push button could be switched"
    print("3 switching: plug and actuator on/off, cover up/down/stop/position; nonsense and detectors -> 400")

    # 4) Consumption: sum, rows, history and export (export/history are Pro)
    p = ok(client.get("/api/power"))
    assert p["total_w"] == 272.5 and p["devices"] == 3 and p["active"] == 2, p
    names = [r["name"] for r in p["rows"]]
    assert names[0] == "Zähler" and "Stehlampe" in names, p["rows"]
    # the query above has already recorded the state (the history costs no call of its own)
    assert history.record_power([{"id": "x", "name": "Stecker", "power_w": 10, "online": True}]) == 1
    series = ok(client.get("/api/power/series?hours=24"))
    assert series["points"] and series["points"][-1]["w"] == 282.5, series   # 230 + 42,5 Stecker + 10
    assert series["peak_w"] == 282.5 and {d["id"] for d in series["devices"]} >= {"x", "smappee:meter:gg"}, series
    csv = client.get("/api/power/export?hours=24")
    assert csv.status_code == 200 and csv.text.startswith("zeit;watt") and "282,5" in csv.text, csv.text[:200]
    assert "attachment" in csv.headers.get("content-disposition", "")
    print(f"4 consumption: {p['total_w']} W from {p['devices']} metering devices, history and CSV export")

    # 5) Order of the room tiles from the time before 0.19.3: only read any more (default without an own arrangement)
    ov = ok(client.get("/api/overview"))
    assert ov["room_order"] == [], ov["room_order"]
    rooms_file = Path(os.environ["COMFYOS_HOME"]) / "data/rooms.json"
    rooms_file.write_text(json.dumps({"names": {}, "order": ["flur", "wohnen", "flur", "", "kueche"] + ["x"] * 500}), encoding="utf-8")
    order = ok(client.get("/api/overview"))["room_order"]
    assert order[:3] == ["flur", "wohnen", "kueche"] and len(order) <= floorplan_store.MAX_ORDER, order   # duplicates and empty ones drop out
    floorplan_store.set_room_name("flur", "Diele")
    assert floorplan_store.room_names() == {"flur": "Diele"} and floorplan_store.card_order()[:3] == ["flur", "wohnen", "kueche"], \
        "setting a room name changed the order"
    assert client.put("/api/rooms/order", json={"order": ["kueche"]}).status_code in (404, 405), "the route for sorting no longer exists"
    print("5 the old tile order is read and limited, room names leave it untouched, it is no longer written")

    # 6) Capabilities on the device row (since 0.20): every row names what the device reports and what it accepts
    devs = ok(client.get("/api/devices"))
    assert devs and all(isinstance(d.get("caps"), list) for d in devs), "device row without caps"
    for d in devs:
        keys = [c["key"] for c in d["caps"]]
        assert len(keys) == len(set(keys)), (d["name"], keys)
        assert all({"key", "type", "readable", "writable"} <= set(c) for c in d["caps"]), d["caps"]
        assert bool(d.get("control")) == any(c["writable"] for c in d["caps"]), f"{d['name']}: switchable and caps contradict each other"
        for key in ("on", "temperature", "target", "open", "motion", "power_w", "position"):
            assert d.get(key) is None or key in keys, f"{d['name']} reports {key} but does not name it in caps"
    kinds = {d["kind"]: {c["key"] for c in d["caps"]} for d in devs}
    assert "position" in kinds.get("cover", set()) and "alarm" in kinds.get("smoke", set()) and "on" in kinds.get("switch", set()), kinds
    print(f"6 capabilities: {len(devs)} device rows name what they report and accept; switchable = one capability is writable")

    print("ALL DEVICE TESTS OK")


main_test()
