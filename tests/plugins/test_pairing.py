"""ComfyOS – pairing assistant, reconnecting and deleting against a mock Home Base.

The expected calls (method, path, body, content type) are those from the capture of the
QIVICON web interface (captures/qivicon.clean.har, Nov 2026).

    python tests/plugins/test_pairing.py [path-to-comfyos]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.world_pairing import (  # noqa: E402 – mocks, app and helpers of the shared test world
    auth, BACKEND, calls, CAMERA, cloud, core, device_store, GHOST, homebase_plugin, IMG, install_homebase, JS,
    json, main, MockHB, NEW_THERMO, PLUG, PNG, q, QDEV, ROOM, THERMO, TP)

# ------------------------------------------------------------------ Tests
from fastapi.testclient import TestClient  # noqa: E402

published = []
core.publish = lambda m: published.append(m)

with TestClient(main.app) as client:
    hb = MockHB("https://qivicon", "", "x")
    install_homebase(hb)
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def ok(r, code=200):
        assert r.status_code == code, (r.status_code, r.text)
        return r.json()

    # 1) Catalogue access from the dashboard page (only Telekom servers, no placeholders)
    hb._remember_backend('<html data-access-token="x" data-backend-token="{{BACKEND_TOKEN}}" data-backend-url="' + BACKEND + '">')
    assert hb.backend_token is None
    hb._remember_backend('<html data-backend-token="BT-TEST" data-backend-url="https://evil.example.com/services">')
    assert hb.backend_token is None, "the token may only go to *.qivicon.com"
    hb._remember_backend('<html class="aurora" data-access-token="eyJx" data-backend-token="BT-TEST" data-backend-url="' + BACKEND + '/">')
    assert hb.backend_token == "BT-TEST" and hb.backend_url == BACKEND
    print("1 catalogue access taken over from the dashboard page, foreign servers/placeholders rejected")

    # 2) Catalogue: makers -> devices, only what the Home Base knows
    cat = ok(client.get("/api/plugins/qivicon/pairing/catalog"))
    assert cat["source"] == "cloud" and cat["notice"] is None, cat
    vendors = {v["name"]: v["devices"] for v in cat["vendors"]}
    assert list(vendors) == ["D-Link", "Magenta SmartHome", "Philips Hue"], list(vendors)
    msh = {d["id"]: d for d in vendors["Magenta SmartHome"]}
    assert set(msh) == {"HIDTHERMO", "HIDPLUG", "HIDWIZ"}, msh.keys()   # without EndOfLife/hidden
    t = msh["HIDTHERMO"]
    assert t["setup"] == "auto" and t["binding"] == "dect" and t["image"] == IMG + "aaa", t
    assert t["subtitle"] == "DT_HKR_461_03_RF_460_03, SmartHome Heizkörperthermostat", t["subtitle"]
    assert msh["HIDPLUG"]["image"] is None and msh["HIDPLUG"]["beta"] is True, "a foreign image URL was passed through"
    assert msh["HIDWIZ"]["setup"] == "app" and vendors["D-Link"][0]["setup"] == "steps"
    assert vendors["Philips Hue"][0]["types"] == ["hue:0210"]
    print("2 catalogue as in QIVICON: makers/devices, unknown/discontinued/hidden ones filtered")

    # 3) Instructions for the device
    det = ok(client.get("/api/plugins/qivicon/pairing/devices/HIDTHERMO"))
    assert [s["text"] for s in det["steps"]] == ["Entferne die Abdeckung.", "Lege die Batterien ein. Starte die Suche mit Jetzt Suchen."]
    assert det["steps"][0]["image"] == IMG + "s1" and det["reset"][0]["text"].startswith("Taste neben")
    assert det["hints"][0]["title"] == "Batterie" and det["vendor"] == "Magenta SmartHome"
    ok(client.get("/api/plugins/qivicon/pairing/devices/HIDGIBTSNICHT"), 404)
    print("3 step-by-step instructions, resetting and hints from the catalogue")

    # 3b) Catalogue image through ComfyOS (cache), foreign content and path tricks rejected
    n0 = len(cloud["requests"])
    r = client.get(IMG + "aaa")
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png", (r.status_code, r.text)
    assert client.get(IMG + "aaa").content == PNG and len(cloud["requests"]) == n0 + 1, "image not from the cache"
    assert client.get(IMG + "evil").status_code == 404, "HTML passed through as an image"
    for bad in ("../x", "a/../../b", "a b", "https://evil.example.com/x.png", ""):
        assert client.get("/api/plugins/qivicon/pairing/image", params={"p": bad}).status_code in (404, 422), bad
    print("3b catalogue images through ComfyOS with a cache; HTML/path tricks rejected")

    # 4) Search now -> exactly the call from the capture
    calls.clear()
    r = ok(client.post("/api/plugins/qivicon/pairing/scan", json={"binding": "dect"}))
    assert r["timeout"] == 120, r
    assert calls == [("POST", "/rest/discovery/bindings/dect/scan", None, TP)], calls
    ok(client.post("/api/plugins/qivicon/pairing/scan", json={"binding": "../rest/x"}), 400)
    ok(client.post("/api/plugins/qivicon/pairing/scan", json={"binding": "bridge"}), 400)
    assert len(calls) == 1, "an invalid search reached the Home Base"
    print("4 search now: POST /rest/discovery/bindings/dect/scan, duration 120 s; unknown things rejected")

    # 5) Found devices + including
    found = ok(client.get("/api/plugins/qivicon/pairing/found", params={"types": "dect:eurotronic_heatingThermostat_v1,dect:sercomm_indoorSmartPlug_v1"}))
    assert found == [{"uid": NEW_THERMO, "label": "Heating Valve", "type": "dect:eurotronic_heatingThermostat_v1"}], found
    assert ok(client.get("/api/plugins/qivicon/pairing/found", params={"types": "hue:0210"})) == []
    calls.clear()
    r = ok(client.post(f"/api/plugins/qivicon/inbox/{NEW_THERMO}/approve", json={"label": "Thermostat", "room": ROOM}))
    assert r["room_error"] is None, r
    flow_calls = [c for c in calls if c[0] != "PUT"]
    assert flow_calls == [
        ("GET", f"/rest/qivicon/flows/thing/{q(NEW_THERMO)}/contexts/setup-inbox", None, None),
        ("POST", f"/rest/qivicon/flows/thing/{q(NEW_THERMO)}/contexts/setup-inbox/dect-pairing-flow",
         '{"debugging": false, "label": "Thermostat"}', JS),
        ("DELETE", f"/rest/qivicon/flows/{q('dect-pairing-flow:' + NEW_THERMO)}", None, TP),
    ], flow_calls
    put = [c for c in calls if c[0] == "PUT"]
    assert len(put) == 1 and put[0][1] == f"/rest/qivicon/devices/{q(NEW_THERMO)}" and put[0][3] == JS, put
    assert json.loads(put[0][2])["room"] == ROOM and QDEV[NEW_THERMO]["label"] == "Thermostat"
    calls.clear()
    r = client.post(f"/api/plugins/qivicon/inbox/{CAMERA}/approve", json={"label": "Kamera"})
    assert r.status_code == 400 and "QIVICON" in r.text and not [c for c in calls if c[0] != "GET"], (r.text, calls)
    calls.clear()
    ok(client.post("/api/plugins/qivicon/inbox/zigbee:plug:neu1/approve", json={"label": "Steckdose"}))
    assert calls[-1] == ("POST", f"/rest/inbox/{q('zigbee:plug:neu1')}/approve", "Steckdose", "text/plain"), calls
    print("5 the found device appears; including as in QIVICON (setup-inbox with the name, cleaning up the flow, room by PUT);"
          " devices with extra steps rejected, without a flow openHAB approve")

    # 5b) A device the Home Base includes directly without the inbox reports as new since "Jetzt suchen"
    DIRECT = "dect:eurotronic_heatingThermostat_v1:direkt1"
    QDEV[DIRECT] = {"id": DIRECT, "label": "Heating Valve", "thingTypeId": "dect:eurotronic_heatingThermostat_v1", "room": None,
                    "deviceStatus": {"status": "ONLINE", "statusDetail": "NONE"}, "condition": {}}
    calls.clear()
    new = {d["id"]: d for d in ok(client.get("/api/plugins/qivicon/pairing/new"))}
    assert set(new) == {NEW_THERMO, DIRECT} and THERMO not in new, new
    assert new[DIRECT]["name"] == "Heating Valve" and not [c for c in calls if c[0] != "GET"], (new, calls)
    QDEV.pop(DIRECT)
    print("5b devices included directly since the start of the search are reported (naming + room in the assistant), read-only")

    # 6) Reconnecting: offer of the Home Base + instructions from the catalogue
    info = ok(client.get(f"/api/plugins/qivicon/things/{THERMO}/reconnect"))
    assert info["available"] and info["flow"] == "dect-reconnecting-flow", info
    assert info["steps"] == [{"id": "reconnecting-step", "label": "Bitte setzen Sie das Gerät auf den Werkzustand zurück", "timeout": 180}]
    assert info["reset"][0]["image"] == IMG + "r1" and info["status"]["detail"] == "GONE"
    ghost = ok(client.get(f"/api/plugins/qivicon/things/{GHOST}/reconnect"))
    assert ghost["available"] is False, ghost
    ok(client.get("/api/plugins/qivicon/things/dect:gibtsnicht:1/reconnect"), 404)
    print("6 reconnecting offered (DECT), not for the Hue ghost lamp; instructions from the catalogue")

    # 7) Weiter -> Ablauf starten + Warte-Schritt, exakt wie im Mitschnitt
    calls.clear()
    r = ok(client.post(f"/api/plugins/qivicon/things/{THERMO}/reconnect"))
    assert r["timeout"] == 180 and r["flow_id"] == f"dect-reconnecting-flow:{THERMO}", r
    assert calls == [
        ("GET", f"/rest/qivicon/flows/thing/{q(THERMO)}/contexts/setup-reconnect", None, None),
        ("POST", f"/rest/qivicon/flows/thing/{q(THERMO)}/contexts/setup-reconnect/dect-reconnecting-flow", '{"debugging": false}', JS),
        ("POST", f"/rest/qivicon/flows/{q('dect-reconnecting-flow:' + THERMO)}/steps/reconnecting-step", "{}", JS),
    ], calls
    assert json.loads(calls[1][2]) == {"debugging": False}
    calls.clear()
    ok(client.post(f"/api/plugins/qivicon/things/{GHOST}/reconnect"), 400)
    assert all(c[0] == "GET" for c in calls), "a flow was started for a device that is not supported"
    print("7 next: flow + reconnecting-step as in QIVICON; Hue ghost lamp rejected")

    # 8) Waiting for the device
    st = ok(client.get(f"/api/plugins/qivicon/things/{THERMO}/state"))
    assert st["connected"] is False and st["detail"] == "GONE"
    published.clear()
    QDEV[THERMO]["deviceStatus"] = {"status": "UNKNOWN", "statusDetail": "NONE", "statusDescription": None}
    st = ok(client.get(f"/api/plugins/qivicon/things/{THERMO}/state"))
    assert st["connected"] is True and {"type": "reload"} in published, st
    print("8 success recognised as soon as the Home Base no longer lists the device as offline")

    # 9) Deleting from the Home Base (ghost lamp) – exactly the call from the capture
    device_store.set_hidden(GHOST, "Ensis Tisch", True)
    device_store.set_name(GHOST, "Alter Name")
    calls.clear()
    ok(client.delete(f"/api/plugins/qivicon/things/{GHOST}"))
    assert calls == [("DELETE", f"/rest/qivicon/devices/{q(GHOST)}", None, TP)], calls
    assert GHOST not in QDEV and GHOST not in device_store.get()["hidden"] and GHOST not in device_store.get()["names"]
    calls.clear()
    ok(client.delete("/api/plugins/qivicon/things/dect:gibtsnicht:1"), 404)
    assert calls == [], "deleting an unknown device reached the Home Base"
    print("9 deleting: DELETE /rest/qivicon/devices/hue%3A0210%3A…%3A12, leftovers in ComfyOS cleaned up")

    # 10) Device list: raw status for the interface
    QDEV[THERMO]["deviceStatus"] = {"status": "OFFLINE", "statusDetail": "GONE", "statusDescription": None}
    hb.invalidate_devices()
    rows = {r["id"]: r for r in ok(client.get("/api/plugins/qivicon/devices"))["devices"]}
    assert rows[THERMO]["extra"]["hb_status"] == "OFFLINE" and rows[THERMO]["extra"]["hb_detail"] == "GONE"
    assert "Wiederverbinden" in rows[THERMO]["detail"], rows[THERMO]["detail"]
    assert rows[PLUG]["extra"]["hb_status"] == "ONLINE"
    print("10 the device list knows OFFLINE/GONE ('Gerät wurde entfernt')")

    # 11) Catalogue unreachable -> technical type list of the Home Base
    homebase_plugin().catalog_cache = None
    cloud["mode"] = "down"
    cat = ok(client.get("/api/plugins/qivicon/pairing/catalog"))
    assert cat["source"] == "homebase" and "nicht erreichbar" in cat["notice"], cat
    names = [v["name"] for v in cat["vendors"]]
    assert names == ["D-Link", "Magenta SmartHome (DECT)", "Philips Hue", "WiZ (Magenta SmartHome)"], names
    assert all(d["id"] != "dect:bridge" for v in cat["vendors"] for d in v["devices"]), "an internal type is shown"
    det = ok(client.get("/api/plugins/qivicon/pairing/devices/dect:eurotronic_heatingThermostat_v1"))
    assert det["steps"][0]["text"].startswith("Bringe das Gerät") and det["image"] is None
    print("11 without the catalogue: type list of the Home Base by radio technology, general instructions")

    # 12) Abgelaufener Katalog-Zugang -> verworfen, Ersatzliste statt Fehler
    homebase_plugin().catalog_cache = None
    cloud["mode"] = "expired"
    cat = ok(client.get("/api/plugins/qivicon/pairing/catalog"))
    assert cat["source"] == "homebase" and hb.backend_token is None, cat
    print("12 an expired catalogue access is discarded, the assistant stays usable")

print("ALL PAIRING TESTS OK")
