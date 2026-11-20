"""ComfyOS – demo home (demo.py, demo_world.py, api/demo.py): a sample home per browser session that never touches the
real one.

    1  start and state: only the own session is in the demo, the sample home is set up, another session keeps its home
    2  the demo sees only the demo: devices (with a button and ambient light), plugins, floor plan, flows, weather without
       network, an event log of its own
    3  writing in the demo: commands, floor plan, accent colour and flows land in the demo – every real file stays the same
    4  refused in the demo: plugins, backups, system, restart, licence, flow test runs and variables, plugin routes
    5  separation in the core: live messages only to pages of the same side, plugins never hear of the demo, long-running
       tasks and the flow engine stay with the real home
    6  ending: reset sets the sample home up anew, stop and logout bring the own dashboard back

    python tests/core/test_demo.py [path-to-comfyos]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("demo")

import httpx  # noqa: E402

from app import auth, core, demo, demo_world, egress, flow_engine, flows_store, floorplan_store, main, plugins  # noqa: E402
from app import settings_store, tasks, widgets_store  # noqa: E402
from app.paths import DATA_DIR  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

outbound: list[str] = []


def _net(request: httpx.Request) -> httpx.Response:
    """Replacement for the network: records every target – the demo must send nothing."""
    outbound.append(request.url.host)
    return httpx.Response(500)


egress._transport = httpx.MockTransport(_net)
egress._lan_transport = httpx.MockTransport(_net)

# The real home of this test: an own floor plan, an own flow, an own accent colour and arrangement
floorplan_store.save({"width": 10, "height": 8, "home_name": "Echt", "address": "Echtweg 5, 99999 Echtstadt", "weather": False})
real_flow = flows_store.create("Echter Flow", [], [], enabled=False)
settings_store.save_settings({"accent_color": "#123456", "layout": {"home": {"1": [["raum-a", "raum-b"]]}}})
widgets_store.save({"pages": [{"name": "Echt", "widgets": [{"type": "clock"}]}]})
CHECKED = ("floorplan.json", "flows.json", "scenes.json", "routines.json", "home.json", "effects.json", "rooms.json", "widgets.json")


def snapshot() -> dict[str, bytes | None]:
    """Bytes of the real files a demo could reach: the stores under data/ and settings.json."""
    out = {name: (DATA_DIR / name).read_bytes() if (DATA_DIR / name).is_file() else None for name in CHECKED}
    out["settings.json"] = settings_store.SETTINGS_FILE.read_bytes()
    return out


def as_session(token: str) -> dict[str, str]:
    """Headers of one browser session (the test client itself keeps no cookie)."""
    return {"cookie": f"{auth.COOKIE}={token}"}


real_token, visitor_token = auth.create_session("echt"), auth.create_session("besuch")
REAL, VISITOR = as_session(real_token), as_session(visitor_token)

with TestClient(main.app) as client:
    def get(path: str, headers: dict) -> object:
        """GET that must succeed; returns the JSON."""
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.status_code, response.text[:200])
        return response.json()

    # ------------------------------------------------------------------ 1) start and state
    assert get("/api/demo", VISITOR)["active"] is False
    get("/api/flows", REAL)                                    # the flow engine loads the real flows
    real_engine_flows = set(flow_engine.engine.flows)
    before = snapshot()
    started = client.post("/api/demo/start", headers=VISITOR)
    assert started.status_code == 200 and started.json()["active"] and started.json()["fresh"] is True, started.text
    assert get("/api/demo", VISITOR)["active"] is True and get("/api/demo", REAL)["active"] is False
    assert demo.in_demo(visitor_token) and not demo.in_demo(real_token) and demo.sessions() == 1
    assert (demo.DEMO_DIR / "floorplan.json").is_file() and (demo.DEMO_DIR / "flows.json").is_file()
    assert client.post("/api/demo/start", headers=VISITOR).status_code == 403, "already in the demo: refused like every other write"
    print("1 start: only this session is in the demo, the sample home lies in data/demo/, the other session keeps its home")

    # ------------------------------------------------------------------ 2) the demo sees only the demo
    shown = get("/api/devices", VISITOR)
    assert shown and all(d["id"].startswith(demo_world.ID_PREFIX) for d in shown), [d["id"] for d in shown][:5]
    assert len(shown) == len(demo_world._START)
    taster = next(d for d in shown if d["id"] == "demo:taster")
    assert taster["kind"] == "button" and [b["name"] for b in taster["buttons"]] == ["An", "Heller", "Dunkler", "Aus"]
    motion = next(d for d in shown if d["id"] == "demo:bewegung")
    assert motion["illuminance"] is not None and any(c["key"] == "illuminance" for c in motion["caps"])
    assert {d["room_id"] for d in shown} >= {"wohnen", "kueche", "schlafen"}
    assert not any(d["id"].startswith(demo_world.ID_PREFIX) for d in get("/api/devices", REAL)), "the demo leaked into the home"
    demo_plugins = [p["id"] for p in get("/api/plugins", VISITOR)["plugins"]]
    real_plugins = [p["id"] for p in get("/api/plugins", REAL)["plugins"]]
    assert demo_plugins == ["demo"] and "demo" not in real_plugins and "hue" in real_plugins
    assert get("/api/floorplan", VISITOR)["home_name"] == "Demo-Wohnung" and get("/api/floorplan", REAL)["home_name"] == "Echt"
    assert {f["name"] for f in get("/api/flows", VISITOR)["flows"]} == {"Licht bei Bewegung", "Lüften", "Taster Wohnzimmer"}
    assert [f["name"] for f in get("/api/flows", REAL)["flows"]] == ["Echter Flow"]
    assert get("/api/flows", VISITOR)["vars"] == {}, "the variables belong to the real flow engine"
    weather = get("/api/weather", VISITOR)
    assert weather["available"] is True and weather["place"] == "Musterstadt" and outbound == [], outbound
    assert get("/api/settings", VISITOR).get("accent_color") != "#123456", "the demo shows its own colour, not the user's"
    assert get("/api/flows/vars", VISITOR) == {}
    demo_view, real_view = get("/api/widgets", VISITOR)["pages"], get("/api/widgets", REAL)["pages"]     # widget view (since 0.24)
    assert [p["name"] for p in demo_view] == ["Zuhause"] and len(demo_view[0]["widgets"]) > 8 and [p["name"] for p in real_view] == ["Echt"]
    print("2 the demo sees only the demo: devices with a button and ambient light, the plugin Demo-Wohnung, its floor plan "
          "and flows, sample weather without network")

    # ------------------------------------------------------------------ 3) writing in the demo
    switched = client.post("/api/devices/demo:decke/command", json={"state": {"on": False}}, headers=VISITOR)
    assert switched.status_code == 200, switched.text
    assert next(d for d in get("/api/devices", VISITOR) if d["id"] == "demo:decke")["on"] is False
    plan = get("/api/floorplan", VISITOR)
    assert client.put("/api/floorplan", json={**plan, "home_name": "Meine Demo"}, headers=VISITOR).status_code == 200
    assert client.put("/api/settings/accent", json={"color": "#ff0000"}, headers=VISITOR).status_code == 200
    assert get("/api/settings", VISITOR)["accent_color"] == "#ff0000"
    made = client.post("/api/flows", json={"name": "Demo-Flow", "nodes": [], "edges": []}, headers=VISITOR)
    assert made.status_code == 200, made.text
    changed = client.put("/api/widgets", json={"pages": [{"name": "Meine Demo-Seite", "widgets": []}]}, headers=VISITOR)
    assert changed.status_code == 200 and get("/api/widgets", VISITOR)["pages"][0]["name"] == "Meine Demo-Seite", changed.text
    assert get("/api/widgets", REAL)["pages"][0]["name"] == "Echt", "the demo changed the user's widget view"
    assert get("/api/floorplan", VISITOR)["home_name"] == "Meine Demo" and get("/api/floorplan", REAL)["home_name"] == "Echt"
    assert get("/api/settings", REAL)["accent_color"] == "#123456"
    assert snapshot() == before, [name for name, data in snapshot().items() if data != before[name]]
    assert set(flow_engine.engine.flows) == real_engine_flows, "a demo flow reached the flow engine"
    real_devices = DATA_DIR / "devices.json"
    assert not real_devices.is_file() or "demo:" not in real_devices.read_text(encoding="utf-8")
    demo_log = [e["message"] for e in get("/api/events", VISITOR)]
    real_log = [e["message"] for e in get("/api/events", REAL)]
    assert any("Deckenlicht" in m for m in demo_log) and not any("Deckenlicht" in m for m in real_log), (demo_log[:5], real_log[:5])
    print("3 writing in the demo: commands, floor plan, accent colour and flows stay in the demo – every real file is unchanged")

    # ------------------------------------------------------------------ 4) refused in the demo
    for method, path, body in (("PUT", "/api/plugins/hue/enabled", {"enabled": False}), ("POST", "/api/plugins/rescan", None),
                               ("GET", "/api/plugins/fritzbox/status", None), ("POST", "/api/backups", None),
                               ("GET", "/api/backups", None), ("POST", "/api/restart", None), ("GET", "/api/update", None),
                               ("PUT", "/api/flows/vars", {"a": 1}), ("POST", f"/api/flows/{real_flow['id']}/run", None),
                               ("POST", "/api/auth/password", {"old": "x", "new": "y" * 12}),
                               ("PUT", "/api/security/hosts", {"hosts": []}), ("POST", "/api/license", {"key": "x"}),
                               ("GET", "/api/logs/export", None), ("POST", "/api/power/reset", None),
                               ("PUT", "/api/plugins/fritzbox/settings", {"values": {}})):
        refused = client.request(method, path, json=body, headers=VISITOR)
        assert refused.status_code == 403 and refused.json()["detail"] == demo.REFUSED, (method, path, refused.status_code)
    assert demo.allowed("POST", "/api/scenes/abendrot/apply") and not demo.allowed("POST", "/api/scenes/bridge/import")
    assert demo.allowed("GET", "/api/plugins/demo/devices") and not demo.allowed("DELETE", "/api/backups/x.zip")
    print("4 refused in the demo: plugins and their routes, backups, updates, restart, password, hosts, licence, logs, "
          "flow test runs and variables")

    # ------------------------------------------------------------------ 5) separation in the core
    async def separation() -> None:
        """Live messages, plugins, long-running tasks and the flow engine – in the event loop of the app."""
        real_page, demo_page = asyncio.Queue(), asyncio.Queue()
        core.listen(real_page)
        mark = demo.enter()
        core.listen(demo_page)
        heard: list = []
        notify = plugins.notify
        plugins.notify = heard.append
        try:
            core.publish({"type": "device", "id": "demo:decke", "on": True})
            demo.leave(mark)
            core.publish({"type": "device", "id": "hue:1", "on": True})
        finally:
            plugins.notify = notify
        assert demo_page.get_nowait()["id"] == "demo:decke" and demo_page.empty()
        assert real_page.get_nowait()["id"] == "hue:1" and real_page.empty()
        assert [message["id"] for message in heard] == ["hue:1"], "a plugin heard of the demo"
        core._listeners.discard(real_page)
        core._listeners.discard(demo_page)

        seen: list = []

        async def runner() -> None:
            """A long runner that notes whether it runs in the demo."""
            seen.append(demo.active())
            await asyncio.sleep(3600)
        mark = demo.enter()
        task = tasks.spawn("demo-probe", runner)
        demo.leave(mark)
        await asyncio.sleep(0.05)
        task.cancel()
        assert seen == [False], "a long runner inherited the demo"

        mark = demo.enter()
        flow_engine.engine.reload()
        demo.leave(mark)
        assert set(flow_engine.engine.flows) == real_engine_flows
    client.portal.call(separation)
    print("5 separation: live messages only to pages of the same side, plugins never hear of the demo, long runners and the "
          "flow engine stay with the real home")

    # ------------------------------------------------------------------ 6) ending
    assert client.post("/api/demo/reset", headers=REAL).status_code == 409, "reset only from the demo"
    assert client.post("/api/demo/reset", headers=VISITOR).status_code == 200
    assert get("/api/floorplan", VISITOR)["home_name"] == "Demo-Wohnung"
    assert next(d for d in get("/api/devices", VISITOR) if d["id"] == "demo:decke")["on"] is True
    assert "Demo-Flow" not in {f["name"] for f in get("/api/flows", VISITOR)["flows"]}
    assert client.post("/api/demo/stop", headers=VISITOR).status_code == 200
    assert get("/api/demo", VISITOR)["active"] is False and not demo.in_demo(visitor_token)
    assert not any(d["id"].startswith(demo_world.ID_PREFIX) for d in get("/api/devices", VISITOR))
    assert get("/api/floorplan", VISITOR)["home_name"] == "Echt" and get("/api/settings", VISITOR)["accent_color"] == "#123456"
    assert client.post("/api/demo/start", headers=VISITOR).json()["active"] is True
    assert client.post("/api/auth/logout", headers=VISITOR).status_code == 200 and not demo.in_demo(visitor_token)
    assert snapshot() == before and outbound == []
    print("6 ending: reset sets the sample home up anew; stop and logout bring the own dashboard back – unchanged")

print("ALL DEMO TESTS OK")
