"""ComfyOS – Matter plugin in the core: tile routes under attack, shares and commands through the real switching path.

    python tests/plugins/test_matter.py [path-to-comfyos]

Runs without Node: the helper program is a `FakeHelper` from the SDK, everything else is real – loader, access rights
of the plugin API (revision 3), device lock, event log, device list. Checked is what the tests of the plugin alone
cannot see:

    1  tile routes: without login 401, from a foreign page 403, broken input 4xx – never a server error
    2  without Node: state "not set up"; "Einrichten" only downloads from nodejs.org and installs nothing with a
       wrong checksum
    3  shares through the tile; the devices of the Home Base reach the helper program as Matter devices
    4  command of a controller: through core.apply_device – the event log names Matter, the change goes back to the bridge;
       a locked device answers "locked" and is not switched
    5  pairing code: only in the response of the tile route while pairing is open, never in event log, settings, overview
    6  device of the controller: appears as "Neu", is adopted and switched through the normal device route
    7  disabling the plugin: nothing listens any more
"""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("matter")
sys.path.insert(0, str(ROOT / "public" / "sdk"))

import httpx  # noqa: E402

from app import auth, egress, events, main, plugins, settings_store  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from testing import FakeHelper  # noqa: E402

PW = "comfyos-" + "test"
BASE = "/api/plugins/matter"
CODE = "34970112332"
outbound: list[str] = []


def _net(req: httpx.Request) -> httpx.Response:
    """Replacement for the network: records the target and returns something that is not a Node archive."""
    outbound.append(req.url.host)
    return httpx.Response(200, content=b"kein node-archiv")


egress._transport = httpx.MockTransport(_net)
egress._lan_transport = httpx.MockTransport(_net)
matter_module = sys.modules["comfyos_plugin_matter"]
runtime_module = sys.modules["comfyos_plugin_matter.runtime"]
matter_module.SYNC_DELAY_SECONDS = 0.05
matter_module.SAVE_DELAY_SECONDS = 0.05


class MockHB(QiviconConnector):
    """Home Base of this test: a thermostat and a socket; commands are recorded in writes."""
    writes: list = []
    plug_on = False

    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}

    async def list_devices(self, max_age=None):
        """Thermostat in the bathroom, socket in the kitchen."""
        return [{"id": "hb:therm", "name": "Heizung", "kind": "thermostat", "room": "Bad", "room_id": "bad", "online": True,
                 "temperature": 20.5, "target": 21.0, "control_item": "T1", "errors": [], "props": {}, "values": []},
                {"id": "hb:plug", "name": "Kaffeemaschine", "kind": "switch", "room": "Küche", "room_id": "kueche", "online": True,
                 "on": MockHB.plug_on, "control_item": "P1", "errors": [], "props": {}, "values": []}]

    async def list_rooms(self):
        """Two rooms."""
        return [{"name": "bad", "label": "Bad"}, {"name": "kueche", "label": "Küche"}]

    async def set_device_state(self, device_id, state):
        """Record a command and confirm it."""
        self.writes.append((device_id, dict(state)))
        if "on" in state:
            MockHB.plug_on = bool(state["on"])
            return {"on": MockHB.plug_on}
        return {"target": state.get("target")}


def until(condition, what: str, seconds: float = 8.0) -> None:
    """Wait until a condition holds – otherwise fail with a message."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(f"did not happen: {what}")


with TestClient(main.app, raise_server_exceptions=False) as client:
    install_homebase(MockHB("https://qivicon", "", "x"))
    auth.set_password(PW)
    token = auth.create_session("test")
    plugin = plugins.active("matter")
    assert plugin is not None, "the Matter plugin is not loaded"
    context = plugin.context
    ROUTES = [("GET", "/status", None), ("POST", "/runtime/install", None), ("POST", "/commissioning", {"open": True}),
              ("DELETE", "/controllers/1", None), ("PUT", "/shares", {"key": "hb:plug", "shared": True}),
              ("POST", "/controller/pair", {"code": CODE}), ("DELETE", "/controller/nodes/peer2", None)]

    def ok(r, code=200):
        assert r.status_code == code, (r.status_code, r.text[:300])
        return r.json()

    # ------------------------------------------------------------------ 1) Kachel-Routen unter Angriff
    for method, path, body in ROUTES:
        r = client.request(method, BASE + path, json=body)
        assert r.status_code == 401, f"{method} {path} without login: {r.status_code}"
    assert outbound == [] and not MockHB.writes, "something happened without login"
    client.cookies.set(auth.COOKIE, token)
    for method, path, body in ROUTES[1:]:
        r = client.request(method, BASE + path, json=body, headers={"origin": "https://evil.example.com"})
        assert r.status_code == 403, f"{method} {path} from a foreign page: {r.status_code}"
    assert outbound == [], "a foreign web page triggered 'Einrichten'"
    for method, path, body in (("PUT", "/shares", {"key": "x" * 500, "shared": True}), ("PUT", "/shares", {"key": "hb:plug", "shared": "vielleicht"}),
                               ("PUT", "/shares", {"keys": ["x" * 10] * 3000, "shared": True}), ("PUT", "/shares", [1, 2]), ("POST", "/commissioning", {"open": "auf"}),
                               ("POST", "/commissioning", {}), ("POST", "/controller/pair", {"code": "x" * 5000}),
                               ("POST", "/controller/pair", {"code": ""}), ("DELETE", "/controllers/abc", None)):
        r = client.request(method, BASE + path, json=body)
        assert r.status_code == 422, f"{method} {path} {str(body)[:30]}: {r.status_code}"
    for method, path, body, code in (("DELETE", "/controllers/0", None, 404), ("DELETE", "/controllers/999", None, 404),
                                     ("DELETE", "/controller/nodes/" + "n" * 60, None, 404), ("DELETE", "/controller/nodes/..%2f..%2fetc", None, 400),
                                     ("PUT", "/shares", {"key": "../../etc/passwd", "shared": True}, 400),
                                     ("PUT", "/shares", {"key": "<script>alert(1)</script>", "shared": True}, 400),
                                     ("PUT", "/shares", {"shared": True}, 400),
                                     ("POST", "/controller/pair", {"code": "<script>"}, 400), ("POST", "/controller/pair", {"code": "1234; rm -rf /"}, 400)):
        r = client.request(method, BASE + path, json=body)
        assert r.status_code in (code, 404), f"{method} {path}: {r.status_code} {r.text[:120]}"
        assert "<script>" not in r.text and "Traceback" not in r.text, f"{method} {path} echoes the input or a stack trace"
    print(f"1 tile routes: {len(ROUTES)} routes without login 401, from a foreign page 403, broken input 4xx")

    # ------------------------------------------------------------------ 2) without Node
    status = ok(client.get(BASE + "/status"))
    assert status["runtime"]["installed"] is False and status["runtime"]["host"] == "nodejs.org" and status["online"] is False
    assert status["pairing"] is None and status["controllers"] == [] and status["shares"] == {"used": 0, "advice": 50}
    assert {item["key"] for item in status["offered"]} >= {"hb:therm", "hb:plug", "home"}, status["offered"]
    tile = {p["id"]: p for p in ok(client.get("/api/plugins"))["plugins"]}["matter"]
    assert tile["state"] == "active" and tile["status"]["configured"] is False and tile["helper"] and len(tile["access"]) == 4, tile
    for method, path, body in (("POST", "/commissioning", {"open": True}), ("POST", "/controller/pair", {"code": CODE}),
                               ("DELETE", "/controller/nodes/peer2", None)):
        assert client.request(method, BASE + path, json=body).status_code in (400, 502), f"{method} {path} without a helper program"
    outbound.clear()
    r = client.post(BASE + "/runtime/install")
    assert r.status_code == 400 and "Prüfsumme" in r.text and outbound == ["nodejs.org"], (r.status_code, r.text[:200], outbound)
    assert not (context.data_dir / "runtime").exists() and ok(client.get(BASE + "/status"))["runtime"]["installed"] is False
    assert ok(client.get("/api/devices")) and not any(d["id"].startswith("matter:") for d in ok(client.get("/api/devices")))
    print("2 without Node: the tile shows 'not set up'; 'Einrichten' only contacts nodejs.org and installs nothing without a matching checksum")

    # ------------------------------------------------------------------ Beiprogramm als FakeHelper einsetzen
    box: dict = {}

    def fake_start(program, args=(), *, env=None):
        """Instead of a process: FakeHelper of the SDK (runs in the event loop of the app)."""
        box["helper"] = FakeHelper(str(program), args, env)
        return box["helper"]

    context.start_helper = fake_start
    binary = runtime_module.binary_in(context.data_dir)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"stellvertreter fuer node")
    (context.data_dir / "runtime" / "VERSION").write_text(runtime_module.NODE_VERSION + "\n", encoding="ascii")
    client.portal.call(plugin.stop)
    client.portal.call(plugin.start)
    helper = box["helper"]
    assert helper.args[0] == "--permission" and helper.args[1].startswith("--allow-fs-read=") and helper.env == {}

    def feed(message: dict) -> None:
        """Feed in a message of the helper program – in the event loop of the app."""
        client.portal.call(helper.feed, message)

    feed({"type": "ready", "protocol": 1})
    until(lambda: helper.sent_of("start"), "start sent")
    assert helper.sent_of("start")[0]["endpoints"] == [], "without a share Matter sees no device"
    feed({"type": "started"})
    feed({"type": "pairing", "manual": CODE, "qr": "MT:Y.K90AFN00KA0648G00", "rows": ["1" * 21] * 21, "commissioned": False})
    feed({"type": "fabrics", "fabrics": []})
    until(lambda: ok(client.get(BASE + "/status"))["online"], "Plugin online")

    # ------------------------------------------------------------------ 3) Shares through the tile
    ok(client.put(BASE + "/shares", json={"key": "hb:plug", "shared": True}))
    status = ok(client.put(BASE + "/shares", json={"key": "hb:therm", "shared": True}))
    assert status["shares"]["used"] == 2 and {i["key"] for i in status["offered"] if i["shared"]} == {"hb:plug", "hb:therm"}
    until(lambda: helper.sent_of("endpoints") and len(helper.sent_of("endpoints")[-1]["endpoints"]) == 2, "shares at the helper program")
    shared = {entry["name"]: entry for entry in helper.sent_of("endpoints")[-1]["endpoints"]}
    assert shared["Kaffeemaschine"]["kind"] == "on_off_plug" and shared["Kaffeemaschine"]["state"] == {"on": False}
    assert shared["Heizung"]["kind"] == "thermostat" and shared["Heizung"]["state"]["heating_setpoint"] == 2100
    assert shared["Heizung"]["state"]["local_temperature"] == 2050 and not shared["Heizung"]["locked"]
    plug_number, therm_number = shared["Kaffeemaschine"]["number"], shared["Heizung"]["number"]
    saved = settings_store.load_settings()["plugins"]["settings"]["matter"]
    assert saved["shared"] == ["hb:plug", "hb:therm"] and saved["endpoints"] == {"hb:plug": plug_number, "hb:therm": therm_number}, saved
    assert ok(client.put(BASE + "/shares", json={"key": "gibts:nicht", "shared": True}), 400)
    assert plugins.watching(), "the plugin does not listen for device changes"
    print("3 shares: per device through the tile; devices of the Home Base reach the helper program with Matter values and fixed endpoints")

    # ------------------------------------------------------------------ 4) Command of a controller
    feed({"type": "command", "id": 1, "number": plug_number, "state": {"on": True}, "fabric": 1})
    until(lambda: helper.sent_of("reply"), "answer to the controller")
    assert helper.sent_of("reply")[-1] == {"type": "reply", "id": 1, "status": "ok"} and MockHB.writes == [("hb:plug", {"on": True})]
    logged = [e["message"] for e in events.recent(10)]
    assert any("Kaffeemaschine: ON" in m and "(durch Matter" in m for m in logged), logged     # im Protokoll steht, wer geschaltet hat
    until(lambda: any(u["number"] == plug_number and u["state"] == {"on": True} for u in helper.sent_of("update")), "the new state goes to the bridge")
    ok(client.post("/api/devices/hb:plug/lock"))
    until(lambda: any(u["number"] == plug_number and u["locked"] for u in helper.sent_of("update")), "the lock goes to the bridge")
    feed({"type": "command", "id": 2, "number": plug_number, "state": {"on": False}, "fabric": 1})
    until(lambda: len(helper.sent_of("reply")) == 2, "answer for the locked device")
    assert helper.sent_of("reply")[-1] == {"type": "reply", "id": 2, "status": "locked"} and len(MockHB.writes) == 1, MockHB.writes
    ok(client.post("/api/devices/hb:plug/unlock"))
    feed({"type": "command", "id": 3, "number": therm_number, "state": {"heating_setpoint": 2250}, "fabric": 2})
    feed({"type": "command", "id": 4, "number": 4242, "state": {"on": True}, "fabric": 1})                  # nie freigegeben
    until(lambda: len(helper.sent_of("reply")) == 4, "answers for thermostat and unknown endpoint")
    replies = {reply["id"]: reply["status"] for reply in helper.sent_of("reply")}
    assert replies[3] == "ok" and replies[4] == "error" and MockHB.writes[-1] == ("hb:therm", {"target": 22.5}), (replies, MockHB.writes)
    # a change from the interface reaches the bridge too
    ok(client.post("/api/devices/hb:plug/command", json={"state": {"on": False}}))
    until(lambda: helper.sent_of("update")[-1]["number"] == plug_number and helper.sent_of("update")[-1]["state"] == {"on": False},
          "switching in ComfyOS appears in Matter")
    ok(client.put(BASE + "/shares", json={"key": "hb:plug", "shared": False}))
    until(lambda: len(helper.sent_of("endpoints")[-1]["endpoints"]) == 1, "share withdrawn")
    feed({"type": "command", "id": 5, "number": plug_number, "state": {"on": True}, "fabric": 1})
    until(lambda: len(helper.sent_of("reply")) == 5, "answer for the device that is no longer shared")
    assert helper.sent_of("reply")[-1]["status"] == "error" and MockHB.writes[-1] == ("hb:plug", {"on": False}), "a device that is not shared was switched"
    print("4 commands: through the switching path of the core (event log 'durch Matter'), state and lock go to the bridge;"
          "locked -> locked, not shared -> error")

    # ------------------------------------------------------------------ 5) Zahlencode
    assert "manual" not in ok(client.get(BASE + "/status"))["pairing"], "pairing code returned without open pairing"
    opened = ok(client.post(BASE + "/commissioning", json={"open": True}))
    assert opened["pairing"]["manual"] == CODE and opened["pairing"]["qr"].startswith("MT:") and opened["pairing"]["open_until"] > time.time()
    assert helper.sent_of("commissioning")[-1] == {"type": "commissioning", "open": True}
    passcode = settings_store.get_secret("plugin:matter:passcode")
    assert passcode and passcode == str(helper.sent_of("start")[0]["bridge"]["passcode"]) and settings_store.get_secret("plugin:matter:storage_key")
    elsewhere = "".join(client.get(url).text for url in ("/api/events", "/api/settings", "/api/plugins", "/api/overview", "/api/system/info",
                                                         BASE + "/devices")) + json.dumps(settings_store.load_settings())
    for secret, what in ((CODE, "pairing code"), ("MT:Y.K90", "QR payload"), (settings_store.get_secret("plugin:matter:storage_key"), "storage key")):
        assert secret not in elsewhere, f"{what} outside the tile route"
    assert f'"passcode": {passcode}' not in elsewhere and f'"{passcode}"' not in elsewhere, "pairing code of the bridge outside the secret store"
    feed({"type": "fabrics", "fabrics": [{"index": 1, "label": "", "vendor": 4631}]})
    until(lambda: ok(client.get(BASE + "/status"))["controllers"] == [{"index": 1, "number": 1, "name": "Amazon Alexa"}], "controller in the tile")
    assert "manual" not in ok(client.get(BASE + "/status"))["pairing"], "the window stays open after pairing"
    ok(client.delete(BASE + "/controllers/1"))
    assert helper.sent_of("remove_fabric")[-1] == {"type": "remove_fabric", "index": 1}
    assert any("Amazon Alexa" in e["message"] for e in events.recent(20))
    print("5 pairing code: only in the response of the tile route while pairing is open; never in event log, settings or overviews")

    # ------------------------------------------------------------------ 6) Device of the controller
    feed({"type": "node", "node": "peer2", "online": True, "name": "Fremde Bridge", "vendor": "Signify", "product": "BSB002", "serial": "x",
          "endpoints": [{"endpoint": 3, "types": [257, 19], "name": "Leselampe <b>", "reachable": True, "state": {"on": True, "level": 127}}]})
    until(lambda: [c["id"] for c in ok(client.get(BASE + "/devices"))["new"]] == ["matter:peer2-3"], "device of the controller as 'Neu'")
    assert "matter:peer2-3" not in {d["id"] for d in ok(client.get("/api/devices"))}, "a device that was not adopted is in the device list"
    ok(client.post(BASE + "/adopt", json={"device_id": "matter:peer2-3", "room": "bad"}))
    lamp = next(d for d in ok(client.get("/api/devices")) if d["id"] == "matter:peer2-3")
    assert (lamp["name"], lamp["kind"], lamp["plugin"], lamp["on"], lamp["brightness"]) == ("Leselampe <b>", "light", "matter", True, 50), lamp
    assert lamp["control"] == {"plugin": "matter", "device_id": "matter:peer2-3"}

    def answer_invoke() -> None:
        """Like the helper program: confirm the next command to the controller device."""
        until(lambda: helper.sent_of("invoke"), "invoke sent")
        feed({"type": "result", "id": helper.sent_of("invoke")[-1]["id"], "ok": True})

    worker = threading.Thread(target=answer_invoke)
    worker.start()
    r = ok(client.post("/api/devices/matter:peer2-3/command", json={"state": {"on": True, "brightness": 40}}))
    worker.join()
    sent = helper.sent_of("invoke")[-1]
    assert (sent["node"], sent["endpoint"], sent["state"]) == ("peer2", 3, {"level": 102}) and r["via"] == "matter", (sent, r)
    assert ok(client.put(BASE + "/shares", json={"key": "matter:peer2-3", "shared": True}), 400)        # never back through the bridge
    assert "matter:peer2-3" not in {item["key"] for item in ok(client.get(BASE + "/status"))["offered"]}
    assert [n["node"] for n in ok(client.get(BASE + "/status"))["nodes"]] == ["peer2"]
    feed({"type": "node_removed", "node": "peer2"})
    until(lambda: "matter:peer2-3" not in {d["id"] for d in ok(client.get("/api/devices"))}, "a removed Matter device disappears")
    print("6 controller: the device appears as 'Neu', is adopted, switched through the device route and cannot be shared back")

    # ------------------------------------------------------------------ 7) Ausschalten
    ok(client.put("/api/plugins/matter/enabled", json={"enabled": False}))
    assert not plugins.watching() and client.get(BASE + "/status").status_code == 404
    feed({"type": "command", "id": 9, "number": therm_number, "state": {"heating_setpoint": 1800}, "fabric": 1})
    time.sleep(0.3)
    assert MockHB.writes[-1] != ("hb:therm", {"target": 18.0}), "a disabled plugin switched"
    assert not any(e["type"] == "system" and "läuft nicht" in e["message"] and "Matter" in e["message"] for e in events.recent(30))
    print("7 disabling: routes do not answer, nothing listens, no command gets through any more")

print("ALL MATTER TESTS IN THE CORE OK")
