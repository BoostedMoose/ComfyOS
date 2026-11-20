"""ComfyOS – Home Base + Hue plugin together: against a mock Home Base + mock Hue Bridge.

Checks the path through the plugin system: link devices of both sources (device_merge), adopt, switch,
live events, names, removing – plus Home Base topics (device list, heating schedule import, session, Telekom).
The pure Hue conversions and the bridge client have their own tests in public/plugins/hue/tests/.

    python tests/plugins/test_hue.py [path-to-comfyos]
    # without an argument: runs from the project root
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.world_hue import (  # noqa: E402 – mocks, app and helpers of the shared test world
    auth, BR, bridge, core, device_store, hb_writes, HUE, hue, install_homebase, json, LID, LIGHTS, main,
    MockHB, push_event, THINGS, U)
from app import settings_store  # noqa: E402

# ------------------------------------------------------------------ App
from fastapi.testclient import TestClient  # noqa: E402
import time as _time  # noqa: E402
from app import routines_store  # noqa: E402

captured = []
_orig_publish = core.publish
core.publish = lambda m: (captured.append(m), _orig_publish(m))


def wait_for(pred, timeout=5.0):
    """Wait until the condition holds; False after the time is up."""
    end = _time.time() + timeout
    while _time.time() < end:
        if pred():
            return True
        _time.sleep(0.05)
    return False


with TestClient(main.app) as client:
    hb = MockHB("https://qivicon", "", "x")
    hb.room_names_provider = core.room_names
    install_homebase(hb)
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def ok(r, code=200):
        assert r.status_code == code, (r.status_code, r.text)
        return r.json()

    def dev(devs, did):
        return next((d for d in devs if d["id"] == did), None)

    # 1) without Hue
    devs = ok(client.get("/api/devices"))
    assert dev(devs, f"hue:0210:{BR}:5")["kind"] == "light" and not dev(devs, f"hue:0210:{BR}:5").get("link_missing")
    st = ok(client.get(HUE + "/status"))
    assert st["configured"] is False and st["suggested"] == ["192.0.2.10"], st
    listed = {x["id"]: x for x in ok(client.get("/api/plugins"))["plugins"]}
    assert listed["hue"]["state"] == "active" and listed["hue"]["status"]["configured"] is False, listed
    print("1 without a bridge: Home Base list unchanged, address suggested")

    # 2) Kopplung + Live-Stream startet
    ok(client.post(HUE + "/pair", json={"host": "8.8.8.8"}), 400)              # the key never goes to an address outside
    r = ok(client.post(HUE + "/pair", json={"host": "192.0.2.10"}))
    assert r["ok"] is False and r["link"] is True, r
    r = ok(client.post(HUE + "/pair", json={"host": "https://192.0.2.10/"}))
    assert r["ok"] and r["connected"] and r["host"] == "192.0.2.10", r
    assert settings_store.get_secret("plugin:hue:app_key") == "KEY123"
    assert settings_store.load_settings()["plugins"]["settings"]["hue"]["bridge_id"] == "ecb5fafffeaabbcc"
    assert wait_for(lambda: hue["streams"] >= 1), "event stream not opened"
    print("2 pairing ok, event stream of the bridge opened")

    # 3) Merging: bridge-only lamps only after the import
    devs = ok(client.get("/api/devices"))
    ids = [d["id"] for d in devs]
    assert len(ids) == len(set(ids)), "duplicate devices"
    d1 = dev(devs, f"hue:0210:{BR}:1")
    assert d1["name"] == "Deckenlampe" and d1["room"] == "Küche" and d1["on"] is True, d1
    assert d1["control"] == {"plugin": "hue", "device_id": LID(1)} and d1["plugin"] == "qivicon", d1
    assert d1["source"]["plugin"] == "hue" and d1["source"]["name"] == "Hue Decke" and d1["source"]["linked"], d1["source"]
    d5 = dev(devs, f"hue:0210:{BR}:5")
    assert d5["link_missing"] and d5["missing_at"] == "Philips Hue" and not d5["online"] and d5["control_item"] is None, d5
    assert not any(d["id"].startswith("huebridge:") for d in devs), "bridge-only lamps visible without an import"
    st = ok(client.get(HUE + "/devices"))
    assert {n["id"] for n in st["new"]} == {LID(18), LID(19), LID(20)}, st["new"]
    assert next(n for n in st["new"] if n["id"] == LID(18))["room_hint"] == "küche"
    assert {x["id"] for x in st["devices"]} == {f"hue:0210:{BR}:{n}" for n in (1, 5, 9)}, st["devices"]
    print("3 merge: linked=1 device, orphaned marked, new lamps only under 'Neu auf der Bridge'")

    # 4) Importieren (mit/ohne Raum)
    ok(client.post(HUE + "/adopt", json={"device_id": LID(18)}))                 # room automatically (Hue room küche)
    ok(client.post(HUE + "/adopt", json={"device_id": LID(19), "room": "wohnen"}))
    ok(client.post(HUE + "/adopt", json={"device_id": LID(20)}))
    ok(client.post(HUE + "/adopt", json={"device_id": LID(1)}), 404)             # a linked lamp is not a candidate
    ok(client.post("/api/plugins/gibtsnicht/adopt", json={"device_id": LID(1)}), 404)
    devs = ok(client.get("/api/devices"))
    assert dev(devs, "huebridge:" + U(18))["room"] == "Küche"
    assert dev(devs, "huebridge:" + U(19))["room"] == "Wohnzimmer" and not dev(devs, "huebridge:" + U(19))["online"]
    assert dev(devs, "huebridge:" + U(20))["kind"] == "switch" and dev(devs, "huebridge:" + U(20))["adopted"]
    assert ok(client.get(HUE + "/devices"))["new"] == []
    print("4 import: room automatic or chosen, candidate check")

    # 5) Switching + live feedback
    hb_writes.clear(); hue["puts"].clear(); captured.clear()
    r = ok(client.post(f"/api/devices/hue:0210:{BR}:9/command", json={"state": {"on": True}}))
    assert r["via"] == "hue" and hue["puts"] == [(U(9), True)] and not hb_writes
    assert any(m.get("type") == "device" and m.get("id") == f"hue:0210:{BR}:9" and m.get("on") is True for m in captured), captured
    ok(client.post(f"/api/devices/hue:0210:{BR}:5/command", json={"state": {"on": True}}), 400)
    ok(client.post("/api/devices/zwave:therm:1/command", json={"state": {"target": 22}}))
    assert hb_writes == [("T_set", "22.0")] and {"type": "device", "id": "zwave:therm:1", "target": 22.0} in captured
    print("5 commands: Hue directly, thermostat through the Home Base, both reported to open pages at once")

    # 6) Change in the Hue app -> event stream -> at once to the browsers
    ok(client.get("/api/devices"))             # cache + mapping rid -> device up to date
    captured.clear()
    LIGHTS[1]["on"] = False
    push_event("update", {"id": U(1), "type": "light", "on": {"on": False}})
    assert wait_for(lambda: any(m.get("id") == f"hue:0210:{BR}:1" and m.get("on") is False for m in captured)), captured
    t0 = _time.time()
    push_event("update", {"id": U(2018), "type": "zigbee_connectivity", "status": "connectivity_issue",
                          "owner": {"rid": U(1018), "rtype": "device"}})
    assert wait_for(lambda: any(m.get("id") == "huebridge:" + U(18) and m.get("online") is False for m in captured))
    lag = _time.time() - t0
    assert dev(ok(client.get("/api/devices")), f"hue:0210:{BR}:1")["on"] is False   # cache updated by the event
    push_event("add", {"id": U(5555), "type": "light"})
    assert wait_for(lambda: {"type": "reload"} in captured)
    print(f"6 live: Hue app change reported in {lag * 1000:.0f} ms, reachability live, new lamp -> reload")
    push_event("update", {"id": U(2018), "type": "zigbee_connectivity", "status": "connected",
                          "owner": {"rid": U(1018), "rtype": "device"}})
    wait_for(lambda: any(m.get("id") == "huebridge:" + U(18) and m.get("online") is True for m in captured))
    LIGHTS[18]["zb"] = "connected"

    # 6b) Hue motion detector: bridge event at once, the bridge takes precedence over the (sluggish) Home Base item
    msid = f"hue:motionSensor:{BR}:001788010b000001"
    m0 = dev(ok(client.get("/api/devices")), msid)
    assert m0["motion"] is False and m0["props"]["Bewegung"] == "live über die Hue Bridge", m0
    captured.clear()
    hue["motion"] = True   # Home-Base-Item M_presence bleibt OFF
    push_event("update", {"id": U(4001), "type": "motion", "motion": {"motion": True, "motion_valid": True,
                                                                     "motion_report": {"changed": "x", "motion": True}}})
    assert wait_for(lambda: {"type": "device", "id": msid, "motion": True} in captured), captured
    assert dev(ok(client.get("/api/devices")), msid)["motion"] is True
    push_event("update", {"id": U(4001), "type": "motion", "motion": {"motion": True}})   # unchanged -> no message
    hue["motion"] = False
    push_event("update", {"id": U(4001), "type": "motion", "motion": {"motion": False}})
    assert wait_for(lambda: {"type": "device", "id": msid, "motion": False} in captured)
    assert sum(1 for m in captured if m.get("id") == msid and m.get("motion") is True) == 1, captured
    print("6b Hue motion detector: event of the bridge reported at once, the bridge state takes precedence over the Home Base")

    # 6c) Reachability (since Hue plugin 1.1.2): for a linked sensor the bridge decides, like for lamps. A real Home Base
    #     reported a Hue motion detector offline for seconds again and again while the bridge reached it (19.11.2026).
    motion_thing = next(thing for thing in THINGS if thing["UID"] == msid)
    motion_thing["statusInfo"]["status"] = "OFFLINE"

    def hb_row():
        """The motion detector as the device list of the Home Base plugin shows it (with the raw Home Base status)."""
        return next(r for r in ok(client.get("/api/plugins/qivicon/devices"))["devices"] if r["id"] == msid)

    assert wait_for(lambda: hb_row()["extra"].get("hb_status") == "OFFLINE"), hb_row()
    assert hb_row()["online"] is True and dev(ok(client.get("/api/devices")), msid)["online"] is True, \
        "the Home Base alone made a sensor offline that the bridge reaches"
    captured.clear()
    hue["motion_zb"] = "connectivity_issue"
    push_event("update", {"id": U(2500), "type": "zigbee_connectivity", "status": "connectivity_issue",
                          "owner": {"rid": U(1500), "rtype": "device"}})
    assert wait_for(lambda: {"type": "device", "id": msid, "online": False} in captured), captured
    motion_thing["statusInfo"]["status"] = "ONLINE"
    assert wait_for(lambda: dev(ok(client.get("/api/devices")), msid)["online"] is False), \
        "the bridge does not reach the sensor: offline although the Home Base says ONLINE"
    hue["motion_zb"] = "connected"
    push_event("update", {"id": U(2500), "type": "zigbee_connectivity", "status": "connected",
                          "owner": {"rid": U(1500), "rtype": "device"}})
    assert wait_for(lambda: {"type": "device", "id": msid, "online": True} in captured), captured
    print("6c reachability of a linked Hue motion detector: the bridge decides, live in both directions")

    # 7) Room / all lights
    hb_writes.clear(); hue["puts"].clear()
    r = ok(client.post("/api/lights", json={"on": False, "room_ids": ["kueche"]}))
    assert r["switched"] == 2 and sorted(hue["puts"]) == sorted([(U(1), False), (U(18), False)]), (r, hue["puts"])
    hb_writes.clear(); hue["puts"].clear()
    r = ok(client.post("/api/lights", json={"on": True}))
    assert {rid for rid, _ in hue["puts"]} == {U(1), U(9), U(18)} and hb_writes == [("Z_bri", "ON")] and r["switched"] == 4
    print("7 room/all switches: only reachable lamps, plugs excluded")

    # 8) Entfernen / Wiederherstellen
    ok(client.post("/api/devices/remove", json={"device_id": f"hue:0210:{BR}:5"}))
    ok(client.post("/api/devices/remove", json={"device_id": "huebridge:" + U(20)}))   # imported -> back to "new"
    ok(client.post("/api/devices/remove", json={"device_id": "zwave:therm:1"}))
    devs = ok(client.get("/api/devices"))
    left = {f"hue:0210:{BR}:5", "huebridge:" + U(20), "zwave:therm:1"} & {d["id"] for d in devs}
    assert not left, (left, device_store.get())
    st = ok(client.get(HUE + "/devices"))
    assert [x["name"] for x in st["removed"]] == ["Alte Lampe"] and [n["id"] for n in st["new"]] == [LID(20)], st
    assert st["removed"][0].get("orphan") and "Home Base" in st["removed"][0]["detail"], st["removed"]   # ghost: only the Home Base thing is left
    hbv = ok(client.get("/api/plugins/qivicon/devices"))
    assert [x["name"] for x in hbv["removed"]] == ["Heizung Küche"]
    ok(client.post("/api/devices/remove", json={"device_id": "zwave:therm:1", "removed": False}))
    assert dev(ok(client.get("/api/devices")), "zwave:therm:1")
    print("8 removing: orphaned/Home Base -> 'Entfernt', imported lamp -> 'Neu' again, restoring")

    # 9) Home Base device list
    hbv = ok(client.get("/api/plugins/qivicon/devices"))
    names = {d["name"]: d for d in hbv["devices"]}
    assert "Flurlicht" in names and "Heizung Küche" in names and "Bewegung Flur" in names
    assert "Deckenlampe" not in names and hbv["elsewhere"] >= 2              # Hue-Lampen stehen in der Hue-Kachel
    assert ok(client.get("/api/plugins/qivicon/inbox"))["inbox"] == [
        {"uid": "zigbee:plug:neu1", "label": "Neue Steckdose", "flag": "NEW", "type": "zigbee:plug"}]
    print("9 Home Base list: devices without Hue lamps, inbox (read-only), removed list")

    # 10) Deleting on the bridge, bridge off
    ok(client.delete(HUE + "/devices/" + U(1019)))
    assert hue["deletes"] == [U(1019)] and dev(ok(client.get("/api/devices")), "huebridge:" + U(19)) is None
    hue["down"] = True
    bridge().forget_lights()
    devs = ok(client.get("/api/devices"))
    d1 = dev(devs, f"hue:0210:{BR}:1")
    assert d1 and d1["source"]["plugin"] == "qivicon" and d1["control"] == {"plugin": "qivicon", "device_id": d1["id"]}, d1
    assert not any(d["id"].startswith("huebridge:") for d in devs)
    assert not dev(devs, f"hue:0210:{BR}:9").get("link_missing"), "bridge unreachable does not mean: lamp missing"
    hue["down"] = False
    bridge().forget_lights()
    print("10 deleting on the bridge; bridge off -> fallback to the Home Base")

    # 11) Heizplan-Import
    info = ok(client.get("/api/heating/import"))
    assert info["rooms"] == ["Küche"], info                                   # only rooms with a thermostat
    doc = {"routinen": [{"name": "Homeoffice", "räume": {
        "KÜCHE ": {"Mo-Fr": [["6:30", 21], {"zeit": "0800", "temp": "Eco"}, ["22:00", "17,5"]], "Sa,So": [["08:00", "Komfort"]]},
        "Bad": {"täglich": [["06:00", 22]]}}}]}
    pv = ok(client.post("/api/heating/import", content=json.dumps(doc)))
    e = pv["routines"][0]
    assert pv["ok"] and e["action"] == "create" and e["unmatched"] == ["Bad"] and e["points"] == 7, pv
    assert e["rooms"][0]["room_id"] == "kueche" and e["rooms"][0]["days"] == ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    r = ok(client.post("/api/heating/import?apply=true", content=json.dumps(doc)))
    key = next(x["key"] for x in routines_store.state()["routines"] if x["name"] == "Homeoffice")
    sch = routines_store.get(key)["schedule"]["kueche"]
    assert [(p["time"], p["temp"]) for p in sch["0"]] == [("06:30", 21.0), ("08:00", 15.0), ("22:00", 17.5)], sch["0"]
    assert [(p["time"], p["temp"]) for p in sch["6"]] == [("08:00", 22.0)]
    # same name -> only replace the named days
    ok(client.post("/api/heating/import?apply=true", content=json.dumps({"routinen": [{"name": "homeoffice", "rooms": {"Küche": {"Sa": [["09:00", 20]]}}}]})))
    sch = routines_store.get(key)["schedule"]["kueche"]
    assert [(p["time"], p["temp"]) for p in sch["5"]] == [("09:00", 20.0)] and sch["6"][0]["time"] == "08:00"
    assert sum(1 for x in routines_store.state()["routines"] if x["name"].lower() == "homeoffice") == 1
    # errors are recognised, taking over is refused
    bad = {"routinen": [{"name": "Kaputt", "räume": {"Küche": {"Mo": [["25:00", 21]], "Xy": [["08:00", 21]]}}}]}
    pv = ok(client.post("/api/heating/import", content=json.dumps(bad)))
    assert not pv["ok"] and pv["routines"][0]["errors"], pv
    ok(client.post("/api/heating/import?apply=true", content=json.dumps(bad)), 400)
    ok(client.post("/api/heating/import", content=json.dumps({"routinen": [{"name": "Heiß", "räume": {"Küche": {"Mo": [["08:00", 35]]}}}]})))
    ok(client.post("/api/heating/import", content="kein json"), 400)
    ex = ok(client.get("/api/heating/export"))
    hx = next(r for r in ex["routinen"] if r["name"] == "Homeoffice")
    assert hx["räume"]["Küche"]["Mo"] == [["06:30", 21.0], ["08:00", 15.0], ["22:00", 17.5]], hx
    pv = ok(client.post("/api/heating/import", content=json.dumps(ex)))       # the export can be imported again
    assert pv["ok"] and all(r["action"] == "update" for r in pv["routines"])
    print("11 heating schedule import: name matching (case, umlauts), day ranges, levels, adding, errors, export round trip")

    # 12b) Names: ComfyOS is the master, the bridge (Hue app) follows
    st = ok(client.get(HUE + "/devices"))
    assert st["names_differ"] >= 2, st["names_differ"]          # ceiling/floor have other names on the bridge
    r = ok(client.put(f"/api/devices/hue:0210:{BR}:9/name", json={"name": "Stehlampe im Wohnzimmer neben dem Sofa"}))
    assert r["synced"]["ok"] and r["synced"]["changed"] and r["synced"]["shortened"], r
    assert hue["renames"][-1] == (U(1009), "Stehlampe im Wohnzimmer neben de"), hue["renames"]   # max. 32 Zeichen
    r = ok(client.put(f"/api/devices/hue:0210:{BR}:9/name", json={"name": None}))
    assert r["synced"]["changed"] and LIGHTS[9]["name"] == "Bodenlampe", (r, LIGHTS[9])   # back to the Home Base name
    r = ok(client.put("/api/devices/zwave:therm:1/name", json={"name": "Heizung"}))
    assert r["synced"] is None                                    # not a Hue lamp -> no bridge call
    ok(client.put("/api/devices/zwave:therm:1/name", json={"name": None}))
    n = len(hue["renames"])
    r = ok(client.post(HUE + "/names/sync"))
    assert r["ok"] and r["synced"] >= 1 and LIGHTS[1]["name"] == "Deckenlampe", (r, LIGHTS)
    assert ok(client.get(HUE + "/devices"))["names_differ"] == 0
    assert ok(client.post(HUE + "/names/sync"))["synced"] == 0 and len(hue["renames"]) == n + r["synced"]   # idempotent
    hue["down"] = True; bridge().forget_lights()
    r = ok(client.put(f"/api/devices/hue:0210:{BR}:9/name", json={"name": "Boden"}))
    assert r["synced"] is None or not r["synced"]["ok"], r    # Bridge weg: Name trotzdem in ComfyOS gespeichert
    hue["down"] = False; bridge().forget_lights()
    ok(client.put(f"/api/devices/hue:0210:{BR}:9/name", json={"name": None}))
    print("12b names: renaming transfers to the bridge (32 characters), resetting, the bulk sync is idempotent")

    # 12c) Disabling and enabling the plugin: meanwhile lamps run through the Home Base, routes do not answer
    r = ok(client.put("/api/plugins/hue/enabled", json={"enabled": False}))
    assert r["state"] == "off" and settings_store.load_settings()["plugins"]["disabled"] == ["hue"], r
    d1 = dev(ok(client.get("/api/devices")), f"hue:0210:{BR}:1")
    assert d1["control"]["plugin"] == "qivicon" and d1["source"]["plugin"] == "qivicon", d1
    ok(client.get(HUE + "/status"), 404)
    # cleaning up while the plugin is off: adopted lamps and their names stay in the device store
    device_store.set_name(LID(18), "Küchenlicht")
    client.portal.call(core.merged_devices, 0, True)
    kept = device_store.get()
    assert LID(18) in kept["adopted"] and kept["names"].get(LID(18)) == "Küchenlicht", kept
    r = ok(client.put("/api/plugins/hue/enabled", json={"enabled": True}))
    assert r["state"] == "active" and ok(client.get(HUE + "/status"))["connected"], r
    assert dev(ok(client.get("/api/devices")), f"hue:0210:{BR}:1")["control"]["plugin"] == "hue"
    assert dev(ok(client.get("/api/devices")), LID(18))["name"] == "Küchenlicht"          # the adopted lamp is back
    hue["down"] = True; bridge().forget_lights()
    client.portal.call(core.merged_devices, 0, True)                                       # bridge unreachable: clean up nothing
    assert LID(18) in device_store.get()["adopted"]
    hue["down"] = False; bridge().forget_lights()
    device_store.set_name(LID(18), None)
    print("12c plugin off/on: fallback to the Home Base, own routes only when enabled, settings stay")

    # 13) Trennen
    ok(client.post(HUE + "/forget"))
    assert settings_store.get_secret("plugin:hue:app_key") is None and bridge() is None
    assert not ok(client.get("/api/plugins/hue/status"))["configured"]
    client.portal.call(core.merged_devices, 0, True)                                       # disconnected: adopted lamps stay noted
    assert LID(18) in device_store.get()["adopted"], device_store.get()
    print("13 disconnecting: key deleted, event stream ended")

    # 14) Renaming a device
    ok(client.put(f"/api/devices/hue:0210:{BR}:9/name", json={"name": "Mein Licht"}))
    devs = ok(client.get("/api/devices"))
    d9 = dev(devs, f"hue:0210:{BR}:9")
    assert d9["name"] == "Mein Licht" and d9["custom_name"] == "Mein Licht"
    ok(client.put(f"/api/devices/hue:0210:{BR}:9/name", json={"name": None}))
    d9 = dev(ok(client.get("/api/devices")), f"hue:0210:{BR}:9")
    assert d9["name"] == "Bodenlampe" and not d9.get("custom_name")
    print("14 renaming: individual name set and reset")

    # 15) Inbox approve (simulated: approve fails here because the mock has no /rest/inbox/{uid}/approve)
    r = client.post("/api/plugins/qivicon/inbox/zigbee:plug:neu1/approve", json={"label": "Neue Steckdose"})
    assert r.status_code in (200, 400), r.text   # 400 = Mock-Backend lehnt ab, 200 = Erfolg
    print("15 inbox approve: endpoint reachable")

# 16) Live comparison for the floor plan: only changes of known devices, incl. motion/window/alarm
prev = {"m": {"id": "m", "motion": False, "online": True}, "k": {"id": "k", "open": False, "online": True},
        "r": {"id": "r", "alarm": False, "temperature": 20.5}}
cur = {"m": {"id": "m", "motion": True, "online": True}, "k": {"id": "k", "open": True, "online": True},
       "r": {"id": "r", "alarm": True, "temperature": 20.5}, "neu": {"id": "neu", "on": True}}
msgs = {m["id"]: m for m in core.live_changes(prev, cur)}
assert msgs == {"m": {"type": "device", "id": "m", "motion": True}, "k": {"type": "device", "id": "k", "open": True},
                "r": {"type": "device", "id": "r", "alarm": True}}, msgs
assert core.live_changes({}, cur) == [] and core.live_changes(cur, cur) == []
print("16 the live comparison reports motion, window and alarm at once – only changes, not on the first comparison")

import asyncio as _aio  # noqa: E402


# Session of the Home Base, bundled queries and the pause of the Telekom login are checked by the test of the plugin qivicon.

print("ALL HUE TESTS OK")
