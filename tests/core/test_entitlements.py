"""ComfyOS Basic/Pro – what is unlocked (entitlements.py) with ENFORCE=True: route gate, floor plan, flow and
history policies (monotonic, never a loss of data), the flow engine pauses at run time, Pro/Owner, shadow mode.

    python tests/core/test_entitlements.py [path-to-comfyos]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("ents")

from app import auth, entitlements as ents, floorplan_store, flow_engine as fe, flows_store, history, main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SPECS = json.loads((ROOT / "backend" / "app" / "static" / "flow_nodes.json").read_text(encoding="utf-8"))["nodes"]
PRO_TYPES = sorted(n["type"] for n in SPECS if n.get("pro"))
eng = fe.engine


def N(id_, type_, **config):
    """Baustein eines Flows."""
    return {"id": id_, "type": type_, "x": 0, "y": 0, "config": config}


def plan(rooms=0, windows=0, doors=1, heatmap=False, furniture=False, groups=()):
    """Floor plan with the wanted number of rooms, windows and doors and the named Pro features."""
    return {"width": 30, "height": 20, "heatmap": heatmap,
            "walls": [{"id": "w1", "pts": [[1, 1], [29, 1], [29, 19], [1, 19], [1, 1]]}],
            "rooms": [{"id": f"r{i}", "name": f"Raum {i}", "x": 2 + i, "y": 2} for i in range(rooms)],
            "openings": [{"id": f"o{i}", "type": "window", "a": [2 + 2 * i, 1], "b": [3 + 2 * i, 1]} for i in range(windows)]
            + [{"id": f"d{i}", "type": "door", "a": [2 + 2 * i, 19], "b": [3 + 2 * i, 19]} for i in range(doors)],
            "devices": {"dev:1": {"x": 4, "y": 4, **({"furniture": "sofa"} if furniture else {})}, "dev:2": {"x": 5, "y": 5}},
            "groups": list(groups)}


ents.ENFORCE = True
ents.use_plan("basic")
assert ents.current_plan() == "basic"

with TestClient(main.app) as client:
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def code(r, want):
        assert r.status_code == want, (r.status_code, r.text[:300])
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text

    def pro_required(r, feature):
        d = code(r, 402)["detail"]
        assert d["code"] == "pro_required" and d["feature"] == feature, d

    # 1) Status for the interface
    st = code(client.get("/api/entitlements"), 200)
    assert st["enforce"] is True and st["plan"] == "basic" and st["limits"]["floorplan_rooms"] == 6, st
    assert not any(f["granted"] for f in st["features"].values()) and set(st["pro_nodes"]) == set(PRO_TYPES)
    print("1 /api/entitlements: Basic, limits and Pro nodes")

    # 2) Route gate: exactly the expected routes, all return 402 in Basic
    gated = {}
    for r in main.routes():
        for dep in getattr(getattr(r, "dependant", None), "dependencies", []) or []:
            f = getattr(dep.call, "feature", None)
            if f:
                gated[(sorted(r.methods)[0], r.path)] = f
    assert gated == {("POST", "/api/heating/import"): "ai_import",
                      ("POST", "/api/flows/import"): "flow_ai_import",
                      ("GET", "/api/power/series"): "energy_analytics",
                      ("GET", "/api/power/export"): "energy_analytics",
                      }, gated
    pro_required(client.post("/api/heating/import", content=b"{}"), "ai_import")
    code(client.get("/api/heating/import"), 200)   # Prompt-Vorschau bleibt frei
    pro_required(client.get("/api/power/export"), "energy_analytics")
    # backups and the log export are Basic since 0.24.5 – nobody loses their data because of the plan
    assert "backup_restore" not in ents.FEATURES
    code(client.get("/api/backups"), 200)
    code(client.get("/api/logs/export"), 200)
    print("2 route gates exact: AI import 402, energy export 402, prompt preview, backups and log export free")

    # 3) Floor plan: Basic up to 6 rooms, doors; windows/radiators, heat map, display = Pro (monotonic)
    code(client.put("/api/floorplan", json=plan(rooms=6)), 200)
    pro_required(client.put("/api/floorplan", json=plan(rooms=7)), "floorplan_rooms")
    pro_required(client.put("/api/floorplan", json=plan(rooms=6, windows=1)), "floorplan_elements")
    pro_required(client.put("/api/floorplan", json=plan(rooms=6, heatmap=True)), "floorplan_heatmap")
    pro_required(client.put("/api/floorplan", json=plan(rooms=6, furniture=True)), "custom_appearance")
    grp = {"id": "g1", "name": "Couch", "x": 6, "y": 6, "members": ["dev:1", "dev:2"]}
    saved = code(client.put("/api/floorplan", json=plan(rooms=6, groups=[grp])), 200)
    assert saved["groups"][0]["members"] == ["dev:1", "dev:2"] and "dev:1" not in saved["devices"], saved
    # Schatten nach Sonnenstand + Nordrichtung: Basic, Standard an/0°, Grad begrenzt
    assert saved["shadow"] is True and saved["north"] == 0, saved
    sh = code(client.put("/api/floorplan", json={**plan(rooms=6), "north": 725, "shadow": False}), 200)
    assert sh["north"] == 359 and sh["shadow"] is False, sh
    # existing Pro content (e.g. from a Pro period) stays: saving, moving, deleting works – more of it does not
    floorplan_store.save(plan(rooms=8, windows=2, heatmap=True, furniture=True))
    code(client.put("/api/floorplan", json=plan(rooms=8, windows=2, heatmap=True, furniture=True)), 200)
    code(client.put("/api/floorplan", json=plan(rooms=7, windows=1, heatmap=True, furniture=False)), 200)
    pro_required(client.put("/api/floorplan", json=plan(rooms=8, windows=1, heatmap=True)), "floorplan_rooms")
    pro_required(client.put("/api/floorplan", json=plan(rooms=7, windows=2, heatmap=True)), "floorplan_elements")
    assert len(floorplan_store.get()["rooms"]) == 7, "a rejected change was saved anyway"
    # several floors (since 0.25, `multi_floorplan`): Basic has one floor; a floor that exists stays, can be changed and
    # deleted – a further one needs Pro. Rooms of all floors count towards the limit. Balconies are Basic.
    stock = plan(rooms=7, windows=1, heatmap=True)
    upper = {"id": "og", "name": "Oben", "level": 1, "walls": [{"id": "wo", "pts": [[1, 1], [9, 1], [9, 9], [1, 9], [1, 1]]}]}
    pro_required(client.put("/api/floorplan", json={**stock, "floors": [upper]}), "multi_floorplan")
    assert floorplan_store.get()["floors"] == [], "a rejected floor was saved anyway"
    deck = {"id": "k1", "pts": [[1, 19], [5, 19], [5, 20], [1, 20], [1, 19]]}
    assert len(code(client.put("/api/floorplan", json={**stock, "decks": [deck]}), 200)["decks"]) == 1
    floorplan_store.save({**stock, "floors": [upper]})
    saved = code(client.put("/api/floorplan", json={**stock, "floors": [{**upper, "name": "Dach"}]}), 200)
    assert [f["name"] for f in saved["floors"]] == ["Dach"], saved["floors"]
    pro_required(client.put("/api/floorplan", json={**stock, "floors": [upper, {**upper, "id": "dg", "level": 2}]}), "multi_floorplan")
    room_up = {**upper, "rooms": [{"id": "ro", "name": "Oben", "x": 5, "y": 5}]}
    pro_required(client.put("/api/floorplan", json={**stock, "floors": [room_up]}), "floorplan_rooms")
    assert code(client.put("/api/floorplan", json=stock), 200)["floors"] == [], "deleting a floor must always work"
    print("3 floor plan: 6 rooms/doors/groups free, Pro elements 402, existing content stays editable")

    # 4) Verlauf: Basic sieht 7 Tage (Daten bleiben 13 Monate)
    seen = []
    orig_series, orig_rooms = history.series, history.room_series
    history.series = lambda did, hours: seen.append(hours) or {}
    history.room_series = lambda hours: seen.append(hours) or {}
    code(client.get("/api/history?hours=744"), 200)
    code(client.get("/api/history/rooms?hours=48"), 200)
    assert seen == [168, 48], seen
    print("4 history capped at 168 h")

    # 5) Flows in Basic: basic flows free, at most 5 active, no new Pro nodes
    ids = []
    for i in range(6):
        f = code(client.post("/api/flows", json={"name": f"Basis {i}", "nodes": [N("b", "play"), N("c", "time", role="condition")]}), 200)
        ids.append(f["id"])
    for fid in ids[:5]:
        code(client.put(f"/api/flows/{fid}/enabled", json={"enabled": True}), 200)
    pro_required(client.put(f"/api/flows/{ids[5]}/enabled", json={"enabled": True}), "flows_active")
    listing = code(client.get("/api/flows"), 200)
    assert listing["active"] == 5 and listing["limit"] == 5, listing
    base = flows_store.get(ids[0])
    pro_required(client.put(f"/api/flows/{ids[0]}", json={"name": "x", "nodes": base["nodes"] + [N("t", "toggle")], "edges": []}),
                 "flow_pro_nodes")
    code(client.post(f"/api/flows/{ids[0]}/run"), 200)
    # a Pro setting on a Basic node (switching threshold) counts like a Pro node
    pro_required(client.post("/api/flows", json={"name": "Schwelle", "nodes": [N("d", "device_state", prop="temperature", band=1.5)]}), "flow_pro_nodes")
    code(client.post("/api/flows", json={"name": "Ohne", "nodes": [N("d", "device_state", prop="temperature", band=0)]}), 200)
    print("5 Basic: 5 active flows, 6th -> 402; new Pro node -> 402; test run free")

    # 6) Every Pro node: creating 402; entered by hand in flows.json -> the engine does not run it
    for fid in ids[:5]:
        code(client.put(f"/api/flows/{fid}/enabled", json={"enabled": False}), 200)
    hand = {}
    for t in PRO_TYPES:
        pro_required(client.post("/api/flows", json={"name": t, "nodes": [N("b", "play"), N("p", t)]}), "flow_pro_nodes")
        hand[t] = flows_store.create(t, [N("b", "play"), N("p", t)], [], enabled=True)["id"]
    eng.reload()
    assert all(eng.paused.get(fid) == "pro" for fid in hand.values()), eng.paused
    assert not set(hand.values()) & {f.id for f in eng.enabled()}
    pro_required(client.post(f"/api/flows/{hand['toggle']}/run"), "flow_pro_nodes")
    pro_required(client.post(f"/api/flows/{hand['toggle']}/play/b"), "flow_pro_nodes")
    pro_required(client.post(f"/api/flows/{hand['toggle']}/duplicate"), "flow_pro_nodes")
    tok = next(n["config"]["token"] for n in flows_store.get(hand["webhook"])["nodes"] if n["type"] == "webhook")
    code(client.post(f"/api/flows/hook/{tok}", content=b"x"), 404)
    # removing a Pro node always works
    f = flows_store.get(hand["toggle"])
    code(client.put(f"/api/flows/{f['id']}", json={"name": f["name"], "nodes": [N("b", "play")], "edges": []}), 200)
    assert eng.paused.get(f["id"]) is None
    print(f"6 {len(PRO_TYPES)} Pro nodes: creating 402, enabled by hand -> paused, webhook 404, button/test run 402")

    # 7) Above the limit (switched on by hand): only the oldest 5 basic flows run
    for fid in ids:
        flows_store.set_enabled(fid, True)
    stored = json.dumps(flows_store.all_flows())
    eng.reload()
    assert [eng.paused.get(fid) for fid in ids] == [None] * 5 + ["limit"], eng.paused
    assert json.dumps(flows_store.all_flows()) == stored, "pausing changed stored flows"
    print("7 above the limit: the youngest flow pauses, data unchanged")

    # 8) Pro: everything free, paused flows run without saving again
    ents.use_plan("pro")
    eng.reload()
    assert not eng.paused, eng.paused
    code(client.post("/api/flows", json={"name": "Pro", "nodes": [N("b", "play"), N("p", "toggle")]}), 200)
    code(client.put("/api/floorplan", json=plan(rooms=12, windows=3, heatmap=True, furniture=True)), 200)
    code(client.get("/api/history?hours=744"), 200)
    assert seen[-1] == 744, seen
    assert code(client.get("/api/entitlements"), 200)["features"]["flow_pro_nodes"]["granted"] is True
    print("8 Pro: Pro nodes, floor plan elements, 31 days of history free; paused flows run at once")

    # 9) Owner: everything, future features too; unknown plan = Basic
    ents.use_plan("owner")
    assert ents.granted() == frozenset(ents.FEATURES) and ents.plan_limit("history_hours") is None
    ents.use_plan("gibt-es-nicht")
    assert ents.current_plan() == "basic"
    print("9 Owner = all features without limits; unknown plan -> Basic")

    # 10) Shadow mode: Basic blocks nothing, only remembers what it would block
    ents.ENFORCE = False
    ents.shadow_hits.clear()
    code(client.post("/api/flows", json={"name": "Schatten", "nodes": [N("b", "play"), N("p", "toggle")]}), 200)
    code(client.put("/api/floorplan", json=plan(rooms=14, windows=4)), 200)
    eng.reload()
    assert not eng.paused and {"flow_pro_nodes", "floorplan_rooms", "floorplan_elements", "flows_paused"} <= set(ents.shadow_hits), ents.shadow_hits
    assert code(client.get("/api/entitlements"), 200)["enforce"] is False
    history.series, history.room_series = orig_series, orig_rooms
    print("10 shadow mode: nothing blocked, hits logged")

print("OK test_entitlements")
