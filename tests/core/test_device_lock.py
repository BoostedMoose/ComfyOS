"""ComfyOS – device lock (0.15) against a mock Home Base + mock Hue Bridge.

A locked device is switched neither by user nor flow, scene, "Alle Lichter", dynamic scene or effect –
the other devices switch normally. Only the source "lock_node" (flow node "Gerät sperren/entsperren") may.

    python tests/core/test_device_lock.py [path-to-comfyos]
"""
import json
import sys
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("lock")

import httpx  # noqa: E402

from app import auth, core, device_store, events, flow_engine, main, scenes_store  # noqa: E402
from app import egress, plugin_api, plugins  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402
from support.homebase import build_devices  # noqa: E402

BR = "ecb5faaabbcc"


def ch(cid, itype, item):
    """Channel of a device as the Home Base reports it."""
    return {"id": cid, "itemType": itype, "linkedItems": [item]}


def hue_thing(n, label, room):
    """A Hue lamp the way the Home Base reports it."""
    return {"UID": f"hue:0210:{BR}:{n}", "thingTypeUID": "hue:0210", "label": label, "location": room,
            "statusInfo": {"status": "ONLINE"}, "channels": [ch("color", "Color", f"L{n}_color")]}


THINGS = [hue_thing(1, "Decke", "wohnen"), hue_thing(2, "Lesen", "wohnen"), hue_thing(3, "Sofa", "wohnen"),
          {"UID": "zigbee:plug:p1", "thingTypeUID": "zigbee:plug", "label": "Kaffeemaschine", "location": "kueche",
           "statusInfo": {"status": "ONLINE"}, "channels": [ch("switch", "Switch", "P_sw")]}]
ITEMS = [{"name": f"L{n}_color", "state": "0,0,0"} for n in range(1, 4)] + [
    {"name": "P_sw", "state": "OFF"}, {"name": "wohnen", "label": "Wohnzimmer", "type": "Group"},
    {"name": "kueche", "label": "Küche", "type": "Group"}]
hb_writes = []


class MockHB(QiviconConnector):
    """Home Base of this test: devices from THINGS/ITEMS, write commands are recorded in hb_writes."""
    async def list_devices(self, max_age=None):
        """Build devices from THINGS and ITEMS and report the Hue Bridge the Home Base knows."""
        self.hue_bridges = [{"uid": "hue:bridge:" + BR, "mac": BR, "host": "192.0.2.10"}]
        return build_devices(THINGS, ITEMS, [], self.room_names_provider() if self.room_names_provider else {})

    async def list_rooms(self):
        """The two rooms of this test."""
        return [{"name": "wohnen", "label": "Wohnzimmer"}, {"name": "kueche", "label": "Küche"}]

    async def _write_item(self, item, command):
        """Record a command and change the state of the item."""
        hb_writes.append((item, command))
        for it in ITEMS:
            if it["name"] == item:
                it["state"] = command

    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}


U = lambda n: f"00000000-0000-4000-8000-{n:012d}"  # noqa: E731
LIGHTS = {n: dict(name=f"L{n}", on=True, bri=50.0, xy=(0.4, 0.4)) for n in (1, 2, 3)}
puts = []


def light_res(n, li):
    """A colour lamp as a resource of the Hue Bridge."""
    return {"id": U(n), "id_v1": f"/lights/{n}", "owner": {"rid": U(1000 + n), "rtype": "device"},
            "metadata": {"name": li["name"], "archetype": "sultan_bulb"}, "on": {"on": li["on"]},
            "dimming": {"brightness": li["bri"]}, "color": {"xy": {"x": li["xy"][0], "y": li["xy"][1]}, "gamut_type": "C"},
            "effects": {"effect_values": ["no_effect", "candle"], "status": "no_effect"}}


def handler(req: httpx.Request) -> httpx.Response:
    """Responses of the Hue Bridge: pairing, resources, commands to lamps and groups."""
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
    if path in ("/clip/v2/resource/room", "/clip/v2/resource/zone", "/clip/v2/resource/scene"):
        return ok([])
    if path.startswith("/clip/v2/resource/light/") and req.method == "PUT":
        rid, body = path.rsplit("/", 1)[1], json.loads(req.content)
        n = next(n for n in LIGHTS if U(n) == rid)
        puts.append((n, body))
        if "on" in body:
            LIGHTS[n]["on"] = body["on"]["on"]
        if "dimming" in body:
            LIGHTS[n]["bri"] = body["dimming"]["brightness"]
        if "color" in body:
            LIGHTS[n]["xy"] = (body["color"]["xy"]["x"], body["color"]["xy"]["y"])
        return ok([{"rid": rid, "rtype": "light"}])
    return httpx.Response(404, json={"errors": [{"description": "not found " + path}], "data": []})


transport = httpx.MockTransport(handler)
egress._lan_transport = transport      # Heimnetz-Client des Hue-Plugins

from fastapi.testclient import TestClient  # noqa: E402

captured = []
_orig_publish = core.publish
core.publish = lambda m: (captured.append(m), _orig_publish(m))
real_step = core.scene_step


async def _no_step(now=None):
    """Replacement for the step of the effects: does nothing."""
    return 0
core.scene_step = _no_step


def N(id_, type_, **config):
    """Baustein eines Flows."""
    return {"id": id_, "type": type_, "x": 0, "y": 0, "config": config}


def E(a, ap, b, bp):
    """Connection between two ports of a flow."""
    return {"from": a, "fromPort": ap, "to": b, "toPort": bp}


DECKE, LESEN, SOFA, PLUG = (f"hue:0210:{BR}:1", f"hue:0210:{BR}:2", f"hue:0210:{BR}:3", "zigbee:plug:p1")

with TestClient(main.app) as client:
    hb = MockHB("https://qivicon", "", "x")
    hb.room_names_provider = core.room_names
    install_homebase(hb)
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def ok(r, code=200):
        assert r.status_code == code, (r.status_code, r.text)
        return r.json()

    def dev(did):
        return next((d for d in ok(client.get("/api/devices")) if d["id"] == did), None)

    def fresh():
        plugins.active("hue").bridge.forget_lights()
        flow_engine.engine.cur = None

    r = ok(client.post("/api/plugins/hue/pair", json={"host": "192.0.2.10"}))
    assert r["ok"] and r["connected"], r

    # 1) Locking/unlocking through the API: store, event log, live message, device list
    captured.clear()
    ok(client.post(f"/api/devices/{DECKE}/lock"))
    ok(client.post(f"/api/devices/{PLUG}/lock"))
    assert set(device_store.locked()) == {DECKE, PLUG} and device_store.locked()[DECKE]["by"] == "user"
    assert dev(DECKE)["locked"] is True and dev(DECKE)["locked_by"] == "user" and not dev(LESEN).get("locked")
    assert any(m.get("type") == "device" and m.get("id") == DECKE and m.get("locked") is True for m in captured)
    assert any("Decke gesperrt durch Nutzer" in e["message"] for e in events.recent(20)), events.recent(5)
    ok(client.post(f"/api/devices/{DECKE}/lock"))                       # locking twice: no second entry
    assert sum("Decke gesperrt" in e["message"] for e in events.recent(30)) == 1
    ok(client.post("/api/devices/gibtsnicht/lock"), 404)
    assert device_store.DEVICES_FILE.exists() and json.loads(device_store.DEVICES_FILE.read_text(encoding="utf-8"))["version"] == 2
    print("1 locking: store (devices.json), device list, event log, live message, unknown device rejected")

    # 2) User command to a locked device: 423 with an understandable text, nothing sent
    puts.clear(); hb_writes.clear()
    r = client.post(f"/api/devices/{DECKE}/command", json={"state": {"on": False}})
    assert r.status_code == 423 and "gesperrt" in r.json()["detail"] and "entsperren" in r.json()["detail"], r.text
    assert client.post(f"/api/devices/{PLUG}/command", json={"state": {"on": True}}).status_code == 423
    assert client.post(f"/api/devices/{DECKE}/command", json={"state": {"brightness": 10}}).status_code == 423
    assert puts == [] and hb_writes == [], (puts, hb_writes)
    ok(client.post(f"/api/devices/{LESEN}/command", json={"state": {"brightness": 30}}))
    assert [n for n, _ in puts] == [2]
    print("2 user command: 423 '… ist gesperrt – im Gerätemenü entsperren', lamp and plug unchanged, others switch")

    # 3) "Alle Lichter aus" and room: the locked lamp stays, the others switch
    puts.clear()
    r = ok(client.post("/api/lights", json={"on": False}))
    assert r["ok"] and r["switched"] == 2 and {n for n, _ in puts} == {2, 3} and LIGHTS[1]["on"] is True, (r, puts)
    puts.clear()
    r = ok(client.post("/api/lights", json={"on": True, "room_ids": ["wohnen"], "brightness": 70}))
    assert r["switched"] == 2 and 1 not in {n for n, _ in puts}
    print("3 all lights off / room on: locked lamp skipped (no error), the others switch")

    # 4) Scene in the room: the locked lamp drops out, ending does not "restore" it
    LIGHTS[1].update(xy=(0.4, 0.4), bri=50.0)
    fresh()
    puts.clear()
    r = ok(client.post("/api/scenes/abendrot/apply", json={"room_ids": ["wohnen"]}))
    assert r["ok"] and r["switched"] == 2 and {n for n, _ in puts} == {2, 3} and LIGHTS[1]["xy"] == (0.4, 0.4), (r, puts)
    assert DECKE not in scenes_store.get()["active"]["room:wohnen"]["devices"]
    ok(client.post("/api/scenes/stop", json={"room_ids": ["wohnen"]}))
    assert 1 not in {n for n, _ in puts}
    ok(client.post("/api/scenes/abendrot/apply", json={"device_ids": [DECKE]}), 400)   # only the locked lamp: nothing to do
    # house scene over all assigned rooms
    ok(client.get("/api/overview"))                                          # scenes v1 -> v2 (rooms known)
    puts.clear()
    ok(client.post("/api/scenes/abendrot/apply-house"))
    assert 1 not in {n for n, _ in puts} and {2, 3} <= {n for n, _ in puts}
    ok(client.post("/api/scenes/stop", json={}))
    print("4 scene in the room, house scene: locked lamp unchanged, the others get the scene; ending leaves it alone")

    # 5) Dynamic scene: the locked lamp is not turned further – even if it was only locked after the start
    ok(client.post(f"/api/devices/{DECKE}/unlock"))
    dyn = ok(client.post("/api/scenes", json={"name": "Dyn", "palette": [{"c": "#ff0000"}, {"c": "#00ff00"}, {"c": "#0000ff"}],
                                              "dynamic": True, "speed": 8, "rooms": ["wohnen"]}))
    fresh()
    ok(client.post(f"/api/scenes/{dyn['id']}/apply", json={"room_ids": ["wohnen"]}))
    assert DECKE in scenes_store.get()["active"]["room:wohnen"]["devices"]
    ok(client.post(f"/api/devices/{DECKE}/lock"))
    fresh()
    puts.clear()
    sent = client.portal.call(real_step, _time.time() + 9)
    assert sent == 2 and {n for n, _ in puts} == {2, 3}, (sent, puts)
    ok(client.post("/api/scenes/stop", json={}))
    print("5 dynamic scene: the locked lamp stays, the others move on")

    # 6) Effekt im Raum: gesperrte Lampe bekommt keinen Effekt
    puts.clear()
    r = ok(client.put("/api/effects/wohnen", json={"effect": "candle"}))
    assert r["switched"] == 2 and {n for n, _ in puts} == {2, 3}, (r, puts)
    ok(client.delete("/api/effects/wohnen"))
    print("6 effect: the locked lamp stays without an effect")

    # 7) Flow: switching a locked device ends up in the event log, the flow keeps running; group/light setting skip it
    def flow(name, nodes, edges):
        f = ok(client.post("/api/flows", json={"name": name, "nodes": nodes, "edges": edges}))
        ok(client.put(f"/api/flows/{f['id']}/enabled", json={"enabled": True}))
        return f["id"]

    def press(fid, node="b"):
        return ok(client.post(f"/api/flows/{fid}/play/{node}"))["trace"]

    def switch(devs, to):
        return {"do": "switch", "target": {"devices": devs}, "to": to}

    fresh()
    fs = flow("Sperrtest", [N("b", "play"), N("s", "set_device", **switch([DECKE], "off")), N("t", "set_device", **switch([LESEN], "off"))],
              [E("b", "out", "s", "in"), E("s", "out", "t", "in")])
    puts.clear()
    tr = press(fs)
    assert tr == ["Decke ist gesperrt", "0 Geräte aus", "1 Gerät aus"], tr
    assert [n for n, _ in puts] == [2] and LIGHTS[1]["on"] is True, puts
    assert any(e["message"] == "Sperrtest: Decke ist gesperrt" for e in events.recent(20))
    assert "last_error" not in flow_engine.engine.state.get("_flow:" + fs, {})
    fg = flow("Mehrere", [N("b", "play"), N("g", "set_device", **switch([DECKE, SOFA, PLUG], "on"))], [E("b", "out", "g", "in")])
    fresh()
    puts.clear(); hb_writes.clear()
    tr = press(fg)
    assert tr[-1] == "1 Gerät an" and [n for n, _ in puts] == [3] and hb_writes == [], (tr, puts, hb_writes)
    fl = flow("Licht", [N("b", "play"), N("l", "set_device", do="light", target={"devices": [DECKE, SOFA]}, light_mode="color", color="#0000ff", brightness=40)],
              [E("b", "out", "l", "in")])
    fresh()
    puts.clear()
    assert press(fl)[-1] == "Licht eingestellt (1)" and [n for n, _ in puts] == [3]
    fa = flow("Alle aus", [N("b", "play"), N("a", "set_device", do="switch", target={"all": True}, to="off")], [E("b", "out", "a", "in")])
    fresh()
    puts.clear()
    press(fa)
    assert 1 not in {n for n, _ in puts} and LIGHTS[1]["on"] is True
    fr = flow("Raum aus", [N("b", "play"), N("a", "set_device", do="switch", target={"rooms": ["wohnen"]}, to="on")], [E("b", "out", "a", "in")])
    fresh()
    puts.clear()
    press(fr)
    assert {n for n, _ in puts} == {2, 3} and LIGHTS[1]["on"] is True, puts
    fsc = flow("Szene", [N("b", "play"), N("s", "set_scene", do="start", scene="abendrot", target={"rooms": ["wohnen"]})], [E("b", "out", "s", "in")])
    fresh()
    puts.clear()
    press(fsc)
    assert 1 not in {n for n, _ in puts} and {2, 3} <= {n for n, _ in puts}
    ok(client.post("/api/scenes/stop", json={}))
    print("7 flow: 'Decke ist gesperrt' in the event log, the flow keeps running; several devices, light setting, all lamps, room, scene skip it")

    # 8) Only the source "lock_node" (flow node Gerät sperren/entsperren) may switch a locked device
    fresh()
    d = dev(DECKE)
    try:
        client.portal.call(core.apply_device, d, {"on": False}, "flow")
        raise AssertionError("a flow was allowed to switch a locked device")
    except device_store.DeviceLocked:
        pass
    puts.clear()
    client.portal.call(core.apply_device, d, {"on": False}, device_store.LOCK_SOURCE)
    assert puts == [(1, {"on": {"on": False}})] and LIGHTS[1]["on"] is False, puts
    client.portal.call(core.send_light, d, {"on": True}, device_store.LOCK_SOURCE)
    assert LIGHTS[1]["on"] is True
    print("8 only source=\"lock_node\" switches a locked device")

    # 8b) Flow node "Gerät sperren": locks with the source flow:<id>, switches a device that is already locked along the way, unlocks again
    fresh()
    fk = flow("Sperren", [N("b", "play"), N("k", "lock", do="lock", target={"devices": [DECKE, LESEN]}, switch="on"),
                          N("u", "play", label="auf"), N("o", "lock", do="unlock", target={"devices": [LESEN]})],
              [E("b", "out", "k", "in"), E("u", "out", "o", "in")])
    puts.clear(); captured.clear()
    tr = press(fk)
    assert tr[-1] == "1 Gerät gesperrt" and {n for n, _ in puts} == {1, 2}, (tr, puts)      # Decke was already locked but is switched
    locked = device_store.locked()
    assert locked[LESEN]["by"] == "flow:" + fk and locked[DECKE]["by"] == "user", locked
    assert any("Lesen gesperrt durch Flow" in e["message"] for e in events.recent(20))
    assert any(m.get("type") == "device" and m.get("id") == LESEN and m.get("locked") is True for m in captured)
    try:
        client.portal.call(core.apply_device, dev(LESEN), {"on": False}, "command")
        raise AssertionError("a device locked by the flow could be switched")
    except device_store.DeviceLocked:
        pass
    assert press(fk, "u")[-1] == "1 Gerät entsperrt" and LESEN not in device_store.locked()
    print("8b node 'Gerät sperren': lock with the source flow:<id>, switches locked devices along the way, unlocks again")

    # 8c) Plugin route: a plugin that switches from a route of its own (context.switch) reaches no locked
    #     device – and the loader checks the lock itself once more before every command to a plugin
    hue_ctx = plugins.active("hue").context
    puts.clear()
    try:
        client.portal.call(hue_ctx.switch, "huebridge:" + U(1), {"on": False})     # DECKE is locked by the user
        raise AssertionError("a plugin switched a locked device")
    except device_store.DeviceLocked:
        pass
    try:
        client.portal.call(plugins.command, dev(DECKE), {"on": False}, "command")
        raise AssertionError("the loader passed a command on to a locked device")
    except device_store.DeviceLocked:
        pass
    assert not puts, puts
    r = client.portal.call(hue_ctx.switch, "huebridge:" + U(2), {"on": False})     # LESEN ist frei
    assert r["via"] == "hue" and [n for n, _ in puts] == [2], (r, puts)
    print("8c plugin route: context.switch and the loader reject commands to locked devices")

    # 8d) A plugin switches a foreign device (plugin API revision 3): a plugin with the permission "devices" – here the
    #     Matter plugin, through which Alexa & Co. switch – reaches every device with context.command, but no locked one.
    #     The error arrives as plugin_api.DeviceLocked, so that the plugin can report "locked" onwards.
    matter_ctx = plugins.active("matter").context
    puts.clear()
    try:
        client.portal.call(matter_ctx.command, DECKE, {"on": False})          # DECKE is locked by the user
        raise AssertionError("a plugin switched a locked foreign device")
    except plugin_api.DeviceLocked:
        pass
    assert not puts, puts
    r = client.portal.call(matter_ctx.command, LESEN, {"on": True})            # LESEN ist frei
    assert r["via"] == "hue" and [n for n, _ in puts] == [2], (r, puts)
    assert any("Lesen" in e["message"] and "(durch Matter" in e["message"] for e in events.recent(10)), \
        [e["message"] for e in events.recent(5)]                               # im Protokoll steht, wer geschaltet hat
    puts.clear()
    client.portal.call(matter_ctx.room_lights, "wohnen", False)                # room light: the locked lamp stays as it is
    assert puts and 1 not in {n for n, _ in puts}, puts
    print("8d plugin with the permission 'devices': context.command and room_lights switch no locked device; the event log names the plugin")

    # 9) Unlocking: everything switches again; event log; the lock for a vanished device is cleaned up
    captured.clear()
    ok(client.post(f"/api/devices/{DECKE}/unlock"))
    ok(client.post(f"/api/devices/{DECKE}/unlock"))                       # doppelt: harmlos
    assert any("Decke entsperrt durch Nutzer" in e["message"] for e in events.recent(20))
    assert any(m.get("type") == "device" and m.get("id") == DECKE and m.get("locked") is False for m in captured)
    puts.clear()
    ok(client.post(f"/api/devices/{DECKE}/command", json={"state": {"on": False}}))
    assert puts == [(1, {"on": {"on": False}})] and not dev(DECKE).get("locked")
    THINGS[:] = [t for t in THINGS if t["UID"] != PLUG]
    fresh()
    client.portal.call(core.merged_devices, 0, True)
    assert device_store.locked() == {}, device_store.locked()
    print("9 unlocking: the device switches again, event log + live message; locks of vanished devices are cleaned up")

print("ALL LOCK TESTS OK")
