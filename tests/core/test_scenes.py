"""ComfyOS – Hue light control and scenes against a mock Home Base + mock Hue Bridge.

    python tests/core/test_scenes.py [path-to-comfyos]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.world_scenes import (  # noqa: E402 – mocks, app and helpers of the shared test world
    auth, BR, bridge, core, effects_store, flow_engine, hb_writes, install_homebase, json, LIGHTS, lm, main,
    MockHB, plan, puts, scenes_store, _time, U)

# ------------------------------------------------------------------ App
from fastapi.testclient import TestClient  # noqa: E402

captured = []
_orig_publish = core.publish
core.publish = lambda m: (captured.append(m), _orig_publish(m))
real_step = core.scene_step


async def _no_step(now=None):
    """Replacement for the step of the effects: does nothing."""
    return 0
core.scene_step = _no_step       # background tick off; the test turns dynamic scenes further itself


def wait_for(pred, timeout=8.0):
    """Wait until the condition holds; False after the time is up."""
    end = _time.time() + timeout
    while _time.time() < end:
        if pred():
            return True
        _time.sleep(0.05)
    return False


def N(id_, type_, **config):
    """Baustein eines Flows."""
    return {"id": id_, "type": type_, "x": 0, "y": 0, "config": config}


def E(a, ap, b, bp):
    """Connection between two ports of a flow."""
    return {"from": a, "fromPort": ap, "to": b, "toPort": bp}


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

    STRIP, DECKE, AMB, WEISS, PLUG, STEH = (f"hue:0210:{BR}:1", f"hue:0210:{BR}:2", f"hue:0210:{BR}:3",
                                            f"hue:0210:{BR}:4", f"hue:0010:{BR}:5", "zigbee:bulb:z1")
    r = ok(client.post("/api/plugins/hue/pair", json={"host": "192.0.2.10"}))
    assert r["ok"] and r["connected"], r

    # 2) Capabilities and state per lamp
    s, d, a, w, p = dev(STRIP), dev(DECKE), dev(AMB), dev(WEISS), dev(PLUG)
    assert s["light"]["caps"]["gradient"] == 5 and s["light"]["caps"]["effects"] == ["candle", "fire", "prism"]
    assert s["light"]["mode"] == "xy" and s["light"]["xy"] == [0.5, 0.4], s["light"]
    assert d["light"]["mode"] == "ct" and d["light"]["mirek"] == 366 and d["brightness"] == 80
    assert a["light"]["caps"] == {"ct": [153, 454], "color": False, "gradient": 0, "gradient_modes": [], "effects": [],
                                  "effect_params": False}
    assert s["light"]["caps"]["effect_params"] is True and d["light"]["caps"]["effect_params"] is False
    assert w["light"]["caps"]["ct"] is None and w["light"]["caps"]["color"] is False
    assert p["kind"] == "switch" and p["light"] is None and dev(STEH).get("light") is None
    print("2 capabilities: gradient (5 points), effects, white tone range, only dimmable, plug without light control")

    # 3) Commands
    def cmd(did, state, code=200):
        return ok(client.post(f"/api/devices/{did}/command", json={"state": state}), code)

    puts.clear(); captured.clear()
    r = cmd(DECKE, {"brightness": 35, "kelvin": 2200})
    assert puts == [(2, {"on": {"on": True}, "dimming": {"brightness": 35.0}, "color_temperature": {"mirek": 455}})], puts
    assert "Weißton 2200 K" in r["command"] and "35 %" in r["command"], r
    m = next(m for m in captured if m.get("id") == DECKE)
    assert m["brightness"] == 35 and m["light"]["mirek"] == 455 and m["light"]["mode"] == "ct", m
    puts.clear()
    cmd(STRIP, {"on": True, "gradient": ["#ff0000", "#00ff00", "#0000ff"], "gradient_mode": "random_pixelated"})
    body = puts[0][1]
    assert len(body["gradient"]["points"]) == 3 and body["gradient"]["mode"] == "random_pixelated", body
    assert dev(STRIP)["light"]["mode"] == "gradient" and len(dev(STRIP)["light"]["points"]) == 3
    puts.clear()
    cmd(STRIP, {"effect": "candle"})
    assert puts == [(1, {"effects": {"effect": "candle"}})] and dev(STRIP)["light"]["effect"] == "candle"
    cmd(STRIP, {"effect": "no_effect"})
    puts.clear()
    cmd(AMB, {"on": True, "color": "#ff0000"})
    assert puts == [(3, {"on": {"on": True}, "color_temperature": {"mirek": 454}})], puts     # white tone instead of colour
    cmd(DECKE, {"color": "lila"}, 400)
    cmd(DECKE, {"effect": "disco"}, 400)
    cmd(PLUG, {"brightness": 50}, 400)
    cmd(STEH, {"color": "#ff0000"}, 400)
    cmd(STEH, {"on": True})
    assert hb_writes[-1] == ("Z_bri", "ON")
    cmd(STEH, {"on": False})
    LIGHTS[3]["on"] = False; LIGHTS[3]["mirek"] = 250
    bridge().forget_lights()
    print("3 commands: brightness + white tone, gradient with mode, effect, colour -> white tone, error for the impossible")

    # 4) Live event of a colour change from the Hue app
    dev(DECKE)
    lamp = bridge().cached_light(U(2))
    bridge().apply_event({"type": "light", "id": U(2), "color": {"xy": {"x": 0.2, "y": 0.1}},
                          "color_temperature": {"mirek": None, "mirek_valid": False}})
    st4 = lamp.light_state
    assert st4.mode == "xy" and st4.xy == [0.2, 0.1] and st4.mirek is None, st4
    seen4 = dev(DECKE)["light"]                    # this is how it arrives in the device row
    assert seen4["mode"] == "xy" and seen4["xy"] == [0.2, 0.1], seen4
    bridge().forget_lights()
    LIGHTS[2].update(mirek=366, bri=80.0)
    print("4 live event: colour taken over from the Hue app (mode colour)")

    # 5) Scenes: templates, creating, checking
    st = ok(client.get("/api/scenes"))
    assert st["bridge"] is True and {"hell", "lesen", "abendrot", "polarlicht"} <= {x["id"] for x in st["scenes"]}, st
    ok(client.post("/api/scenes", json={"name": "", "palette": [{"c": "#ff0000"}]}), 400)
    ok(client.post("/api/scenes", json={"name": "Leer", "palette": [{"c": "rot"}]}), 400)
    sc = ok(client.post("/api/scenes", json={"name": "  RGB   Test ", "palette": [{"c": "#FF0000", "b": 50}, {"c": "#00ff00", "b": 60},
                                                                                {"c": "#0000ff", "b": 70}], "speed": 15, "rooms": ["wohnen"]}))
    assert sc["name"] == "RGB Test" and sc["palette"][0] == {"c": "#ff0000", "b": 50} and sc["speed"] == 20 and sc["rooms"] == ["wohnen"]
    assert scenes_store.SCENES_FILE.exists()
    print("5 scenes: templates present, check of name/colours, speed rounded to a step")

    # 6) Scene on a room: distribution per lamp, Home Base lamp only on, plug stays off
    puts.clear(); hb_writes.clear(); captured.clear()
    before = {n: dict(li) for n, li in LIGHTS.items()}
    r = ok(client.post(f"/api/scenes/{sc['id']}/apply", json={"room_ids": ["wohnen"]}))
    assert r["ok"] and r["switched"] == 4, r                         # ceiling lamp, light strip, floor lamp, white
    by = {n: b for n, b in puts}
    assert set(by) == {1, 2, 4}, by
    assert by[2]["color"]["xy"] == dict(zip("xy", lm.hex_to_xy("#ff0000"))) and by[2]["dimming"]["brightness"] == 50.0, by[2]
    assert [p["color"]["xy"] for p in by[1]["gradient"]["points"]] == [dict(zip("xy", lm.hex_to_xy(c))) for c in ("#00ff00", "#0000ff", "#ff0000")]
    assert by[1]["dimming"]["brightness"] == 60.0
    assert by[4] == {"on": {"on": True}, "dimming": {"brightness": 50.0}, "dynamics": {"duration": 400}}, by[4]
    assert hb_writes == [("Z_bri", "ON")], hb_writes
    act = scenes_store.get()["active"]["room:wohnen"]
    assert act["scene"] == sc["id"] and act["devices"] == [DECKE, STRIP, STEH, WEISS], act
    assert act["snapshot"][STEH] == {"on": False} and act["snapshot"][DECKE]["mirek"] == 366, act["snapshot"]
    assert any(m.get("type") == "scenes" and "room:wohnen" in m["active"] for m in captured)
    ov = ok(client.get("/api/overview"))
    assert ov["scenes"]["active"]["room:wohnen"]["scene"] == sc["id"] and "snapshot" not in ov["scenes"]["active"]["room:wohnen"]
    # the home page knows the rooms -> first assignment: the templates now have all rooms
    assert scenes_store.get()["version"] == 2, scenes_store.get()["version"]
    assert set(scenes_store.scene("abendrot")["rooms"]) == {"wohnen", "flur"} and scenes_store.scene(sc["id"])["rooms"] == ["wohnen"]
    print("6 scene in the room: colours in turn, the gradient lamp gets several, Home Base lamp only on, remembered as active")

    # 7) A second scene in the same room keeps the original state; ending restores it
    ok(client.post("/api/scenes/abendrot/apply", json={"room_ids": ["wohnen"]}))
    assert scenes_store.get()["active"]["room:wohnen"]["snapshot"][STEH] == {"on": False}
    puts.clear(); hb_writes.clear()
    r = ok(client.post("/api/scenes/stop", json={"room_ids": ["wohnen"]}))
    assert r["stopped"] == 1 and r["restored"] == 4, r
    by = {n: b for n, b in puts}
    assert by[2]["color_temperature"] == {"mirek": 366} and by[2]["dimming"]["brightness"] == 80.0, by[2]
    assert by[1]["color"]["xy"] == {"x": 0.5, "y": 0.4} and by[4]["dimming"]["brightness"] == 100.0, by
    assert hb_writes == [("Z_bri", "OFF")] and "room:wohnen" not in scenes_store.get()["active"]
    print("7 ending: light as before the first scene (white tone, colour, brightness, off)")

    # 8) Switching the room off ends the scene without restoring; group and single lamp
    ok(client.post(f"/api/scenes/{sc['id']}/apply", json={"room_ids": ["wohnen"]}))
    ok(client.post("/api/lights", json={"on": False, "room_ids": ["wohnen"]}))
    assert "room:wohnen" not in scenes_store.get()["active"]
    puts.clear()
    ok(client.post("/api/lights", json={"on": True, "room_ids": ["wohnen"], "brightness": 30}))
    assert {n: b.get("dimming") for n, b in puts} == {n: {"brightness": 30.0} for n in (1, 2, 4)}, puts
    from app import floorplan_store
    plan = floorplan_store.get()
    plan["groups"] = [{"id": "g1", "name": "Sofa", "x": 1, "y": 1, "members": [STRIP, DECKE]}]
    floorplan_store.save(plan)
    puts.clear()
    ok(client.post("/api/scenes/abendrot/apply", json={"group_id": "g1"}))
    assert {n for n, _ in puts} == {1, 2} and "group:g1" in scenes_store.get()["active"]
    ok(client.post("/api/scenes/stop", json={"group_id": "g1"}))
    puts.clear()
    ok(client.post("/api/scenes/abendrot/apply", json={"device_ids": [AMB]}))
    assert [n for n, _ in puts] == [3] and not any(k.startswith("room:flur") for k in scenes_store.get()["active"])
    ok(client.post("/api/scenes/nichtda/apply", json={"room_ids": ["wohnen"]}), 404)
    ok(client.post(f"/api/scenes/{sc['id']}/apply", json={}), 400)
    ok(client.post(f"/api/scenes/{sc['id']}/apply", json={"room_ids": ["flur"], "group_id": "fehlt"}), 404)
    print("8 room off ends the scene, room brightness, scene on a device group and a single lamp, error cases")

    # 9) Dynamic scene: only turns lamps further that are on and not adjusted individually
    dyn = ok(client.post("/api/scenes", json={"name": "Dyn", "palette": [{"c": "#ff0000"}, {"c": "#00ff00"}, {"c": "#0000ff"}],
                                              "dynamic": True, "speed": 8}))
    ok(client.post(f"/api/scenes/{dyn['id']}/apply", json={"room_ids": ["wohnen"]}))
    cmd(WEISS, {"on": False})
    assert client.portal.call(real_step, _time.time() + 1) == 0          # interval not up yet
    puts.clear()
    sent = client.portal.call(real_step, _time.time() + 9)
    by = {n: b for n, b in puts}
    assert sent == 2 and set(by) == {1, 2}, (sent, puts)                  # white is off, the floor lamp has no colour
    assert by[2]["color"]["xy"] == dict(zip("xy", lm.hex_to_xy("#00ff00"))) and "on" not in by[2] and "dimming" not in by[2], by[2]
    assert by[2]["dynamics"]["duration"] == 7200
    cmd(DECKE, {"color": "#ffffff"})                                      # einzeln verstellt -> bleibt so
    assert DECKE in scenes_store.get()["active"]["room:wohnen"]["excluded"]
    puts.clear()
    client.portal.call(real_step, _time.time() + 30)
    assert [n for n, _ in puts] == [1], puts
    ok(client.post("/api/scenes/stop", json={}))
    assert scenes_store.get()["active"] == {}
    print("9 dynamic: colours move at the interval, only on + not adjusted individually, without changing on/brightness")

    # 10) Taking over scenes of the Hue Bridge
    br = ok(client.get("/api/scenes/bridge"))["scenes"]
    t = next(x for x in br if x["name"] == "Tropendämmerung")
    assert t["group"] == "Wohnzimmer" and t["dynamic"] and [e["b"] for e in t["palette"]] == [70, 50], t
    lesen = next(x for x in br if x["name"] == "Lesen")
    assert lesen["group"] == "Leseecke" and lesen["palette"] == [{"ct": 233, "b": 100}], lesen
    r = ok(client.post("/api/scenes/bridge/import", json={"ids": [U(7001), "fremd"]}))
    assert len(r["created"]) == 1
    imp = next(x for x in ok(client.get("/api/scenes"))["scenes"] if x["id"] == r["created"][0])
    assert imp["name"] == "Tropendämmerung" and imp["dynamic"] and len(imp["palette"]) == 2
    assert imp["rooms"] == [], imp["rooms"]                  # scenes taken over are assigned to no room at first
    # the same scene in several rooms of the bridge = one entry; origin and image stay when editing too
    assert [x["name"] for x in br].count("Tropendämmerung") == 1 and t["groups"] == ["Wohnzimmer", "Zimmer"], t
    assert t["image_rid"] == U(8001) and imp["source"] == "hue" and imp["image_rid"] == U(8001), imp
    ok(client.put(f"/api/scenes/{imp['id']}", json={"name": "Tropen", "palette": imp["palette"]}))
    assert {k: scenes_store.scene(imp["id"]).get(k) for k in ("name", "source", "image_rid")} == \
        {"name": "Tropen", "source": "hue", "image_rid": U(8001)}
    own = ok(client.post("/api/scenes", json={"name": "Eigen", "palette": [{"c": "#ff0000"}], "source": "hue"}))
    assert "source" not in own and "image_rid" not in own, own
    img = client.get(f"/api/scenes/image/hue/{U(8001)}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content.startswith(b"\x89PNG"), img
    for bad in (U(8002), "kein-bild"):
        assert client.get(f"/api/scenes/image/hue/{bad}").status_code == 404, bad
    assert client.get(f"/api/scenes/image/gibtsnicht/{U(8001)}").status_code == 404
    from app import events as _events
    assert sum("liefert keine Szenenbilder" in e["message"] for e in _events.recent(50)) == 1
    ok(client.get(f"/api/scenes/image/hue/{U(8002)}"), 404)
    assert sum("liefert keine Szenenbilder" in e["message"] for e in _events.recent(50)) == 1
    ok(client.delete(f"/api/scenes/{own['id']}"))
    print("10 bridge scenes: palette, room/zone, derived from actions, once per name, origin + image, image proxy")

    # 11) Editing, deleting (an active scene goes with it), templates stay deleted
    ok(client.put(f"/api/scenes/{sc['id']}", json={"name": "RGB", "palette": [{"ct": 1000, "b": 300}]}))
    assert scenes_store.scene(sc["id"])["palette"] == [{"ct": 500, "b": 100}]
    ok(client.post(f"/api/scenes/{sc['id']}/apply", json={"room_ids": ["wohnen"]}))
    ok(client.delete(f"/api/scenes/{sc['id']}"))
    assert "room:wohnen" not in scenes_store.get()["active"]
    ok(client.delete("/api/scenes/hell"))
    assert "hell" not in {x["id"] for x in scenes_store.get()["scenes"]}
    ok(client.delete("/api/scenes/hell"), 404)
    ok(client.put("/api/scenes/nichtda", json={"name": "x", "palette": [{"c": "#ffffff"}]}), 404)
    print("11 editing (values limited), deleting active scenes too, a deleted template does not come back")

    # 12) Flow-Bausteine
    def flow(name, nodes, edges):
        f = ok(client.post("/api/flows", json={"name": name, "nodes": nodes, "edges": edges}))
        ok(client.put(f"/api/flows/{f['id']}/enabled", json={"enabled": True}))
        return f["id"]

    def press(fid, node="b"):
        return ok(client.post(f"/api/flows/{fid}/play/{node}"))["trace"]

    WOHNEN = {"rooms": ["wohnen"]}
    fa = flow("Szene an", [N("b", "play"), N("s", "set_scene", do="start", scene="abendrot", target=WOHNEN)], [E("b", "out", "s", "in")])
    puts.clear()
    tr = press(fa)
    assert tr and tr[0].startswith("Szene Abendrot") and scenes_store.active_map() == {"room:wohnen": "abendrot"}, tr
    fc = flow("Prüfen", [N("b", "play"), N("c", "scene", role="condition", scene="abendrot", rooms=["wohnen"]), N("g", "when"),
                         N("y", "notify", text="aktiv"), N("n", "notify", text="nicht aktiv")],
              [E("b", "out", "g", "in"), E("c", "val", "g", "c1"), E("g", "yes", "y", "in"), E("g", "no", "n", "in")])
    assert press(fc) == ["„aktiv“"], press(fc)
    ft = flow("Gewechselt", [N("t", "scene", role="trigger", rooms=["wohnen"]), N("m", "notify", text="Szene weg", toast=True)],
              [E("t", "end", "m", "in")])
    flow_engine.engine.reload()
    client.portal.call(flow_engine.engine.tick)
    fs = flow("Szene aus", [N("b", "play"), N("s", "set_scene", do="stop", target=WOHNEN, restore=True)], [E("b", "out", "s", "in")])
    captured.clear()
    assert press(fs) == ["Szene beendet (1)"]
    assert scenes_store.active_map() == {}
    assert wait_for(lambda: any(m.get("type") == "toast" and m.get("text") == "Szene weg" for m in captured)), "the scene trigger 'ended' did not fire"
    assert press(fc) == ["„nicht aktiv“"]
    fl = flow("Licht", [N("b", "play"), N("l", "set_device", do="light", target={"devices": [DECKE, STEH, AMB]}, light_mode="color", color="#0000ff", brightness=40)],
              [E("b", "out", "l", "in")])
    puts.clear(); hb_writes.clear()
    assert press(fl) == ["Licht eingestellt (3)"]
    by = {n: b for n, b in puts}
    assert by[2]["color"]["xy"] == dict(zip("xy", lm.hex_to_xy("#0000ff"))) and by[2]["dimming"]["brightness"] == 40.0
    assert "color_temperature" in by[3] and hb_writes == [("Z_bri", "ON")], (by, hb_writes)
    fe = flow("Effekt", [N("b", "play"), N("l", "set_device", do="light", target={"devices": [DECKE, AMB]}, light_mode="effect", effect="fire", brightness=60)],
              [E("b", "out", "l", "in")])
    puts.clear()
    press(fe)
    by = {n: b for n, b in puts}
    assert by[2]["effects"] == {"effect": "fire"} and "effects" not in by[3], by     # Ambiance cannot do effects -> only on/bright
    fd = flow("Dimmen", [N("b", "play"), N("d", "set_device", do="dim", target={"devices": [DECKE, WEISS]}, step=-30)], [E("b", "out", "d", "in")])
    cmd(DECKE, {"brightness": 50}); cmd(WEISS, {"on": False})
    flow_engine.engine.cur = None                                          # force a fresh device state
    puts.clear()
    assert press(fd) == ["Helligkeit -30 % (1)"]
    assert puts == [(2, {"dimming": {"brightness": 20.0}, "on": {"on": True}})], puts
    fu = flow("Heller", [N("b", "play"), N("d", "set_device", do="dim", target={"devices": [WEISS]}, step=25)], [E("b", "out", "d", "in")])
    flow_engine.engine.cur = None
    puts.clear()
    press(fu)
    assert puts == [(4, {"dimming": {"brightness": 25.0}, "on": {"on": True}})], puts
    lst = ok(client.get("/api/flows"))
    assert lst["problems"] == [], lst["problems"]
    print("12 flow: start/end a scene, scene as condition and trigger, set the light (colour/effect), brighter/darker")

    # 13) First assignment (fresh installation, state 1): scenes without a room -> all rooms, assigned ones stay, idempotent
    assert scenes_store.scene(dyn["id"])["rooms"] == []                    # created in v2 without a room = assigned to no room
    raw = json.loads(scenes_store.SCENES_FILE.read_text(encoding="utf-8"))
    raw["version"] = 1
    raw["active"] = {}
    for s_ in raw["scenes"]:
        if s_["id"] == "abendrot":
            s_["rooms"] = []
    raw["scenes"].append({"id": "nur-flur", "name": "Nur Flur", "palette": [{"c": "#00ff00", "b": 70}], "rooms": ["flur"]})
    scenes_store.SCENES_FILE.write_text(json.dumps(raw), encoding="utf-8")
    assert scenes_store.get()["version"] == 1 and scenes_store.scene("abendrot")["rooms"] == []
    ov = ok(client.get("/api/overview"))
    assert scenes_store.get()["version"] == 2 and set(scenes_store.scene("abendrot")["rooms"]) == {"wohnen", "flur"}
    assert scenes_store.scene("nur-flur")["rooms"] == ["flur"] and set(scenes_store.scene(dyn["id"])["rooms"]) == {"wohnen", "flur"}
    hue_scenes = [s_ for s_ in scenes_store.get()["scenes"] if s_.get("source") == "hue"]
    assert hue_scenes and all(s_["rooms"] == [] for s_ in hue_scenes), "a Hue scene taken over got rooms in the first assignment"
    before = scenes_store.SCENES_FILE.read_text(encoding="utf-8")
    ok(client.get("/api/overview"))
    assert scenes_store.SCENES_FILE.read_text(encoding="utf-8") == before   # a second run changes nothing
    ok(client.put(f"/api/scenes/{dyn['id']}", json={"name": "Dyn", "palette": [{"c": "#ff0000"}], "rooms": []}))
    assert scenes_store.scene(dyn["id"])["rooms"] == []
    print("13 first assignment: empty room list = all rooms, assignments stay, a second run changes nothing")

    # 14) Room variant: save the current state for the room, on the next call exactly this state comes
    ok(client.post("/api/scenes/abendrot/apply", json={"room_ids": ["wohnen"]}))
    cmd(DECKE, {"brightness": 20})
    sv = ok(client.post("/api/scenes/abendrot/variants/wohnen"))
    var = sv["variants"]["wohnen"]["lamps"]
    assert set(var) == {DECKE, STRIP, STEH, WEISS} and var[DECKE]["brightness"] == 20 and "effect" not in var[DECKE], var
    ok(client.post("/api/scenes/stop", json={"room_ids": ["wohnen"]}))
    puts.clear()
    ok(client.post("/api/scenes/abendrot/apply", json={"room_ids": ["wohnen"]}))
    by = {n: b for n, b in puts}
    assert by[2]["dimming"]["brightness"] == 20.0, by[2]
    # variant with only one lamp: the others get the palette distribution as before
    scenes_store.set_variant("abendrot", "wohnen", {DECKE: {"on": True, "brightness": 10, "mirek": 400}})
    puts.clear()
    ok(client.post("/api/scenes/abendrot/apply", json={"room_ids": ["wohnen"]}))
    by = {n: b for n, b in puts}
    assert by[2]["color_temperature"] == {"mirek": 400} and by[2]["dimming"]["brightness"] == 10.0, by[2]
    assert by[4]["dimming"]["brightness"] == 60.0, by[4]                  # white: 4th colour of the palette (b 60)
    puts.clear()
    ok(client.post("/api/scenes/abendrot/apply", json={"room_ids": ["flur"]}))     # other room: no variant
    assert {n for n, _ in puts} == {3} and puts[0][1]["dimming"]["brightness"] == 75.0, puts
    assert ok(client.delete("/api/scenes/abendrot/variants/wohnen"))["removed"] is True
    assert "wohnen" not in scenes_store.scene("abendrot")["variants"]
    ok(client.delete("/api/scenes/nichtda/variants/wohnen"), 404)
    ok(client.post("/api/scenes/abendrot/variants/keller"), 400)
    print("14 room variant: the dimmed state comes back, lamps without an entry by palette, other rooms untouched, resetting")

    # 15) "Als neue Szene": palette from the lamp colours (without duplicates, max. 9), variant + assignment only for the room
    nw = ok(client.post("/api/scenes/from-room/wohnen", json={"name": "Mein Abend"}))
    assert nw["rooms"] == ["wohnen"] and set(nw["variants"]["wohnen"]["lamps"]) == {DECKE, STRIP, STEH, WEISS}, nw
    keys = [e.get("c") or ("ct", e.get("ct")) for e in nw["palette"]]
    assert 1 <= len(keys) <= 9 and len(keys) == len(set(keys)), nw["palette"]
    ok(client.post("/api/scenes/from-room/keller", json={"name": "x"}), 400)
    ok(client.post("/api/scenes/from-room/wohnen", json={"name": ""}), 400)
    assert lm.palette_from_lamps([{"on": False}]) == [{"ct": lm.WARM_WHITE, "b": 100}]
    many = [{"on": True, "brightness": 50, "light": {"mode": "xy", "xy": [0.1 + i * 0.05, 0.2]}} for i in range(12)]
    assert len(lm.palette_from_lamps(many + many)) == 9
    print("15 as a new scene: palette from the room without duplicates (max. 9), variant and assignment only for this room")

    # 16) House scene: all assigned rooms with their variant, rooms that are not assigned stay unchanged
    ok(client.post("/api/scenes/stop", json={}))
    puts.clear()
    r = ok(client.post("/api/scenes/abendrot/apply-house"))
    assert r["ok"] and scenes_store.active_map() == {"room:wohnen": "abendrot", "room:flur": "abendrot"}, scenes_store.active_map()
    assert 3 in {n for n, _ in puts} and 2 in {n for n, _ in puts}
    ok(client.post("/api/scenes/nur-flur/apply-house"))
    assert scenes_store.active_map() == {"room:wohnen": "abendrot", "room:flur": "nur-flur"}
    puts.clear()
    ok(client.post(f"/api/scenes/{nw['id']}/apply-house"))                 # living room only, with a variant
    assert scenes_store.active_map() == {"room:wohnen": nw["id"], "room:flur": "nur-flur"} and 3 not in {n for n, _ in puts}
    ok(client.post(f"/api/scenes/{dyn['id']}/apply-house"), 400)          # keinem Raum zugeordnet
    ok(client.post("/api/scenes/nichtda/apply-house"), 404)
    print("16 house scene: sets all assigned rooms (with variants), rooms that are not assigned stay, error cases")

    # 17) Effect independent of the scene: scene colour, a scene change lets it run, single lamps, own colour, off
    ok(client.post("/api/scenes/abendrot/apply", json={"room_ids": ["wohnen"]}))
    puts.clear()
    r = ok(client.put("/api/effects/wohnen", json={"effect": "candle", "color_source": "scene", "speed": 40}))
    by = {n: b for n, b in puts}
    assert r["switched"] == 2 and set(by) == {1, 2} and r["unable"] == 1, (r, puts)   # white cannot do effects
    p1 = by[1]["effects_v2"]["action"]
    assert p1["effect"] == "candle" and p1["parameters"]["speed"] == 0.4 and "color" in p1["parameters"], p1
    assert by[2]["effects"] == {"effect": "candle"}, by[2]                # lamp without effects_v2: colour/speed ignored
    assert effects_store.room("wohnen")["color_source"] == "scene"
    puts.clear()
    ok(client.post("/api/scenes/nur-flur/apply", json={"room_ids": ["wohnen"]}))   # scene change: the effect stays, new colour
    fx1 = [b for n, b in puts if n == 1 and "effects_v2" in b]
    assert fx1 and fx1[-1]["effects_v2"]["action"]["parameters"]["color"]["xy"] == dict(zip("xy", lm.hex_to_xy("#00ff00"))), puts
    assert effects_store.room("wohnen")["effect"] == "candle"
    puts.clear()
    ok(client.put("/api/effects/wohnen", json={"effect": "fire", "target": "devices", "devices": [STRIP],
                                               "color_source": "custom", "color": "#ff0000"}))
    by = {n: b for n, b in puts}
    assert by[1]["effects_v2"]["action"]["parameters"]["color"]["xy"] == dict(zip("xy", lm.hex_to_xy("#ff0000"))), by[1]
    assert by[2] == {"effects": {"effect": "no_effect"}}, by               # the ceiling lamp no longer belongs to the target
    ok(client.put("/api/effects/wohnen", json={"effect": "fire", "color_source": "custom"}), 400)   # own colour without a colour
    ok(client.put("/api/effects/keller", json={"effect": "fire"}), 400)
    ok(client.put("/api/effects/wohnen", json={"effect": "fire", "target": "devices", "devices": []}), 400)
    assert ok(client.get("/api/overview"))["effects"]["wohnen"]["effect"] == "fire"
    puts.clear()
    ok(client.delete("/api/effects/wohnen"))
    assert effects_store.room("wohnen") is None and puts == [(1, {"effects": {"effect": "no_effect"}})], puts
    ok(client.put("/api/effects/wohnen", json={"effect": "prism"}))
    ok(client.post("/api/lights", json={"on": False, "room_ids": ["wohnen"]}))
    assert effects_store.room("wohnen") is None                            # room off forgets the effect
    print("17 effect: scene colour + speed (effects_v2), a scene change lets it run, single lamps, own colour, off, room off")

print("ALL SCENE TESTS OK")
