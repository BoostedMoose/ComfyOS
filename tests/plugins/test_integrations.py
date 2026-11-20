"""ComfyOS – built-in plugins MQTT and network in interplay with the core.

    python tests/plugins/test_integrations.py [path-to-comfyos]

The plugins themselves are checked by their own tests (public/plugins/*/tests). This is about the path through ComfyOS:
settings of the tile, connection through `context.open_stream` (egress), adopting found devices (one by one, all),
room, state and live messages, switching through /api/devices with the device lock, properties for flows, behaviour on
an outage of the broker and on disabling (nothing is forgotten), own MQTT devices, presence through the network plugin.
The MQTT broker is the in-memory test broker of the plugin. The last section lets the same broker listen on a real
port on 127.0.0.1 and thus checks the actual connection path (`egress.open_stream` without the test switch).
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("integrations")
for extra in ("public/sdk", "public/plugins", "public/plugins/mqtt/tests"):      # Test-Broker des MQTT-Plugins
    sys.path.insert(0, str(ROOT / extra))

import httpx  # noqa: E402

from app import settings_store  # noqa: E402
from app import auth, core, device_store, egress, flow_catalog, main, plugins  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402
from fake_broker import FakeBroker  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PW = "comfyos-" + "test"
BROKER = "192.168.1.50"
outbound: list[str] = []
egress._transport = httpx.MockTransport(lambda req: (outbound.append(req.url.host), httpx.Response(200, json={}))[1])
egress._lan_transport = egress._transport

# After connecting, the plugins wait until the discovery messages are there, and before reconnecting – short in the test
mqtt_module = sys.modules["comfyos_plugin_mqtt"]
mqtt_module.SETTLE_SECONDS = 0.05
sys.modules["comfyos_plugin_mqtt.client"].FIRST_RETRY_SECONDS = 0.05

broker = FakeBroker(user_name="comfyos", password="broker-" + "geheim")
streams: list[tuple] = []           # every TCP connection a plugin opens through ComfyOS
answering: set[str] = set()         # addresses that answer a connection attempt (network plugin)


async def fake_stream(host, port, tls):
    """Replacement for TCP connections: the broker answers, known devices refuse (so they are there), all others
    stay silent."""
    streams.append((host, port, tls))
    if host == BROKER:
        return await broker.connect(host, port)
    if host in answering:
        raise ConnectionRefusedError()      # port closed, but the device answered
    raise asyncio.TimeoutError()


egress._stream_factory = fake_stream

LAMP = {"~": "zigbee2mqtt/Stehlampe", "name": None, "schema": "json", "stat_t": "~", "cmd_t": "~/set", "brightness": True, "bri_scl": 254,
        "sup_clrm": ["xy", "color_temp"], "uniq_id": "0x00158d0001a2b3c4_light",
        "dev": {"ids": ["zigbee2mqtt_0x00158d0001a2b3c4"], "name": "Stehlampe", "mf": "Philips", "sa": "Bad"}}
PLUG_DEV = {"dev": {"ids": ["plug-wm"], "name": "Waschmaschine"}}
CONFIGS = {
    "homeassistant/light/0x00158d0001a2b3c4/light/config": LAMP,
    "homeassistant/switch/plug-wm/switch/config": {**PLUG_DEV, "uniq_id": "wm_switch", "stat_t": "z/wm", "cmd_t": "z/wm/set", "val_tpl": "{{ value_json.state }}"},
    "homeassistant/sensor/plug-wm/power/config": {**PLUG_DEV, "uniq_id": "wm_power", "stat_t": "z/wm", "dev_cla": "power", "unit_of_meas": "W", "val_tpl": "{{ value_json.power }}"},
    "homeassistant/binary_sensor/fenster/contact/config": {"dev": {"ids": ["fenster"], "name": "Fenster Küche"}, "uniq_id": "fenster_contact", "stat_t": "z/fenster",
                                                           "dev_cla": "window", "val_tpl": "{{ value_json.contact }}", "pl_on": False, "pl_off": True},
    "homeassistant/climate/trv/config": {"dev": {"ids": ["trv"], "name": "Heizung Flur"}, "uniq_id": "trv_climate", "curr_temp_t": "z/trv",
                                         "curr_temp_tpl": "{{ value_json.local_temperature }}", "temp_stat_t": "z/trv",
                                         "temp_stat_tpl": "{{ value_json.occupied_heating_setpoint }}", "temp_cmd_t": "z/trv/set/setpoint"},
    "homeassistant/vacuum/sauger/config": {"dev": {"ids": ["sauger"], "name": "Saugroboter"}, "uniq_id": "sauger", "cmd_t": "z/sauger/set"},
}
for topic, config in CONFIGS.items():
    broker.publish(topic, json.dumps(config), retain=True)
LAMP_ID, PLUG_ID, WINDOW_ID, TRV_ID = "mqtt:zigbee2mqtt_0x00158d0001a2b3c4", "mqtt:plug-wm", "mqtt:fenster", "mqtt:trv"


class MockHB(QiviconConnector):
    """Home Base dieses Tests: ein Thermostat im Bad."""
    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}

    async def list_devices(self, max_age=None):
        """Ein Thermostat."""
        return [{"id": "hb:therm", "name": "Heizung", "kind": "thermostat", "room": "Bad", "room_id": "bad", "online": True,
                 "temperature": 20.5, "target": 21.0, "control_item": "T1", "errors": [], "props": {}, "values": []}]

    async def list_rooms(self):
        """Ein Raum."""
        return [{"name": "bad", "label": "Bad"}]


with TestClient(main.app) as client:
    install_homebase(MockHB("https://qivicon", "", "x"))
    auth.set_password(PW)
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def ok(r, code=200):
        assert r.status_code == code, (r.status_code, r.text[:400])
        return r.json()

    def devs():
        return {d["id"]: d for d in ok(client.get("/api/devices"))}

    def tile(pid):
        return next(p for p in ok(client.get("/api/plugins"))["plugins"] if p["id"] == pid)

    def wait(condition, what, seconds=6.0):
        deadline = time.time() + seconds
        while not condition():
            assert time.time() < deadline, f"timed out: {what}"
            time.sleep(0.05)

    def from_device(topic, payload):
        """Message of a device at the broker (in the event thread of the app, the connections live there)."""
        client.portal.call(broker.publish, topic, payload if isinstance(payload, str) else json.dumps(payload))

    # ------------------------------------------------------------------ 1) Without a broker: enabled but silent
    assert tile("mqtt")["state"] == "active" and tile("mqtt")["status"]["configured"] is False
    assert [s["key"] for s in tile("mqtt")["settings"]] == ["host", "port", "username", "password", "tls", "verify_tls", "discovery_prefix", "zigbee2mqtt_topic"]
    assert tile("mqtt")["egress"] == [{"key": "broker", "desc": "MQTT-Broker im Heimnetz", "scope": "lan", "hosts": [], "udp": False}]
    assert not any(k.startswith(("mqtt:", "network:")) for k in devs()) and streams == []
    assert not any(i["id"] in ("mqtt", "network") for i in ok(client.get("/api/overview"))["integrations"])
    print("1 fresh: MQTT and network are enabled but open no connection as long as nothing is entered")

    # ------------------------------------------------------------------ 2) Entering a broker: connect, find devices
    r = ok(client.put("/api/plugins/mqtt/settings", json={"values": {"host": BROKER, "port": 1883, "username": "comfyos",
                                                                      "password": "broker-" + "geheim"}}))
    assert r["values"]["password"] is True and r["values"]["host"] == BROKER
    wait(lambda: tile("mqtt")["status"].get("connected"), "MQTT connects")
    assert streams[0] == (BROKER, 1883, False) and (broker.connects[-1].user_name, broker.connects[-1].password) == ("comfyos", "broker-" + "geheim")
    assert "geheim" not in json.dumps(settings_store.load_settings()) and "geheim" not in client.get("/api/plugins").text
    assert settings_store.get_secret("plugin:mqtt:password") == "broker-" + "geheim"
    wait(lambda: len(ok(client.get("/api/plugins/mqtt/devices"))["new"]) == 4, "device list complete")
    found = {c["name"]: c for c in ok(client.get("/api/plugins/mqtt/devices"))["new"]}
    assert set(found) == {"Stehlampe", "Waschmaschine", "Fenster Küche", "Heizung Flur"} and found["Stehlampe"]["room_hint"] == "Bad"
    assert not any(k.startswith("mqtt:") for k in devs()), "found devices only appear after adopting"
    status = ok(client.get("/api/plugins/mqtt/status"))
    assert status["connected"] and status["not_supported"] == ["Saugroboter: vacuum – Art „vacuum“"], status
    assert {"id": "mqtt", "name": "MQTT", "configured": True, "connected": True, "live": True}.items() <= \
        next(i for i in ok(client.get("/api/overview"))["integrations"] if i["id"] == "mqtt").items()
    conn = {c["id"] for c in ok(client.get("/api/system/egress"))["connections"]}
    assert not any(c.startswith("plugin.mqtt") or c.startswith("plugin.network") for c in conn), "the home network is not in the egress register"
    assert outbound == [], f"MQTT opened an HTTP connection: {outbound}"
    print("2 broker entered: connection through ComfyOS (open_stream), password only in the secret store, 4 devices found,"
          "what is not supported is named")

    # ------------------------------------------------------------------ 3) Adopting: one with a room, then all
    ok(client.post("/api/plugins/mqtt/adopt", json={"device_id": LAMP_ID, "room": "bad"}))
    lamp = devs()[LAMP_ID]
    assert (lamp["kind"], lamp["room"], lamp["plugin"], lamp["adopted"], lamp["vendor"]) == ("light", "Bad", "mqtt", True, "Philips"), lamp
    assert lamp["light"]["caps"]["color"] is True and lamp["control"] == {"plugin": "mqtt", "device_id": LAMP_ID}
    assert ok(client.post("/api/plugins/mqtt/adopt_all")) == {"ok": True, "adopted": 3}
    d = devs()
    assert {LAMP_ID, PLUG_ID, WINDOW_ID, TRV_ID} <= set(d) and (d[PLUG_ID]["kind"], d[WINDOW_ID]["kind"], d[TRV_ID]["kind"]) == ("switch", "contact", "thermostat")
    assert d[PLUG_ID]["on"] is None and d[PLUG_ID]["room"] == "Nicht zugeordnet"          # no message yet: state unknown
    assert ok(client.get("/api/plugins/mqtt/devices"))["new"] == []
    print("3 adopting: one with a room, 'alle übernehmen' for the rest; kind, maker, light capabilities arrive")

    # ------------------------------------------------------------------ 4) States and live messages
    live: list[dict] = []
    original_publish = core.publish
    core.publish = lambda event: (live.append(event), original_publish(event))[1]
    from_device("z/wm", {"state": "ON", "power": 812.4})
    from_device("z/fenster", {"contact": False})
    from_device("z/trv", {"local_temperature": 19.5, "occupied_heating_setpoint": 21})
    from_device("zigbee2mqtt/Stehlampe", {"state": "ON", "brightness": 127, "color_temp": 370, "color_mode": "color_temp"})
    wait(lambda: {e.get("id") for e in live if e.get("type") == "device"} >= {PLUG_ID, WINDOW_ID, TRV_ID, LAMP_ID}, "Live-Meldungen")
    by_id = {e["id"]: e for e in live if e.get("type") == "device"}
    assert by_id[PLUG_ID] == {"type": "device", "id": PLUG_ID, "on": True, "power_w": 812.4}, by_id[PLUG_ID]
    assert by_id[WINDOW_ID]["open"] is True and by_id[TRV_ID]["temperature"] == 19.5 and by_id[TRV_ID]["target"] == 21.0
    assert by_id[LAMP_ID]["brightness"] == 50 and by_id[LAMP_ID]["light"]["mirek"] == 370 and by_id[LAMP_ID]["light"]["mode"] == "ct"
    d = devs()
    assert d[PLUG_ID]["on"] is True and d[PLUG_ID]["power_w"] == 812.4 and d[WINDOW_ID]["open"] is True and d[TRV_ID]["target"] == 21.0
    # flows see the properties (Gerätestatus offers them)
    assert {"on", "power_w"} <= set(flow_catalog.capabilities(d[PLUG_ID])) and "open" in flow_catalog.capabilities(d[WINDOW_ID])
    assert {"temperature", "target"} <= set(flow_catalog.capabilities(d[TRV_ID])) and "brightness" in flow_catalog.capabilities(d[LAMP_ID])
    core.publish = original_publish
    print("4 states: messages of the devices arrive at once as a live message and are in the device list; flows see the properties")

    # ------------------------------------------------------------------ 5) Switching through ComfyOS, device lock
    r = ok(client.post(f"/api/devices/{LAMP_ID}/command", json={"state": {"on": True, "brightness": 40, "kelvin": 2700}}))
    assert r["via"] == "mqtt" and json.loads(broker.published_to("zigbee2mqtt/Stehlampe/set")[-1]) == {"state": "ON", "brightness": 102, "color_temp": 370}
    assert devs()[LAMP_ID]["brightness"] == 40
    ok(client.post(f"/api/devices/{LAMP_ID}/command", json={"state": {"color": "#ff0000"}}))
    assert json.loads(broker.published_to("zigbee2mqtt/Stehlampe/set")[-1]) == {"state": "ON", "color": {"x": 0.7006, "y": 0.2993}}
    ok(client.post(f"/api/devices/{PLUG_ID}/command", json={"state": {"on": False}}))
    assert broker.published_to("z/wm/set") == ["OFF"] and devs()[PLUG_ID]["on"] is False
    ok(client.post(f"/api/devices/{TRV_ID}/command", json={"state": {"target": 22.5}}))
    assert broker.published_to("z/trv/set/setpoint") == ["22.5"] and devs()[TRV_ID]["target"] == 22.5
    ok(client.post(f"/api/devices/{TRV_ID}/command", json={"state": {"target": 80}}), 400)
    ok(client.post(f"/api/devices/{WINDOW_ID}/command", json={"state": {"on": True}}), 400)        # a contact cannot be switched
    ok(client.post(f"/api/devices/{PLUG_ID}/lock"))
    ok(client.post(f"/api/devices/{PLUG_ID}/command", json={"state": {"on": True}}), 423)
    assert broker.published_to("z/wm/set") == ["OFF"], "a locked device was switched"
    ok(client.post(f"/api/devices/{PLUG_ID}/unlock"))
    ok(client.post("/api/lights", json={"on": False}))                                             # "Alle Lichter aus" reaches the MQTT lamp
    assert json.loads(broker.published_to("zigbee2mqtt/Stehlampe/set")[-1]) == {"state": "OFF"} and broker.published_to("z/wm/set") == ["OFF"]
    print("5 switching: lamp (brightness, white tone, colour), socket, thermostat through /api/devices; limits and device lock apply;"
          "'Alle Lichter aus' reaches MQTT lamps but no sockets")

    # ------------------------------------------------------------------ 6) Own devices
    r = ok(client.post("/api/plugins/mqtt/manual", json={"name": "Shelly Küche", "kind": "switch", "state_topic": "shellies/kueche/relay/0",
                                                         "command_topic": "shellies/kueche/relay/0/command", "payload_on": "on", "payload_off": "off"}))
    SHELLY_ID = r["device_id"]
    ok(client.post("/api/plugins/mqtt/adopt", json={"device_id": SHELLY_ID, "room": "bad"}))
    assert devs()[SHELLY_ID]["room"] == "Bad" and devs()[SHELLY_ID]["kind"] == "switch"
    ok(client.post(f"/api/devices/{SHELLY_ID}/command", json={"state": {"on": True}}))
    assert broker.published_to("shellies/kueche/relay/0/command") == ["on"]
    for bad in ({"name": "X", "kind": "switch", "state_topic": "a/#"}, {"name": "X", "kind": "rakete", "state_topic": "a"},
                {"name": "X", "kind": "temperature", "state_topic": "a", "json_key": "{{ 7*7 }}"}):
        ok(client.post("/api/plugins/mqtt/manual", json=bad), 400)
    assert len(ok(client.get("/api/plugins/mqtt/status"))["manual"]) == 1
    print("6 own devices: create, adopt, switch; placeholder topics and templates in the JSON key are rejected")

    # ------------------------------------------------------------------ 7) Broker fails / plugin off: nothing is forgotten
    ok(client.put(f"/api/devices/{PLUG_ID}/name", json={"name": "Wama"}))
    broker.is_down = True
    client.portal.call(broker.drop_connections)
    wait(lambda: tile("mqtt")["status"].get("connected") is False, "outage noticed")
    d = devs()
    assert {LAMP_ID, PLUG_ID, WINDOW_ID, TRV_ID, SHELLY_ID} <= set(d) and not any(d[k]["online"] for k in (LAMP_ID, PLUG_ID, TRV_ID))
    assert d[PLUG_ID]["name"] == "Wama" and d[LAMP_ID]["room"] == "Bad" and "Broker" in d[LAMP_ID]["status_detail"]
    ok(client.post(f"/api/devices/{PLUG_ID}/command", json={"state": {"on": True}}), 502)
    stored = device_store.get()
    assert LAMP_ID in stored["adopted"] and stored["rooms"][LAMP_ID] == "bad" and stored["names"][PLUG_ID] == "Wama"
    broker.is_down = False
    wait(lambda: tile("mqtt")["status"].get("connected"), "reconnects")
    wait(lambda: devs()[LAMP_ID]["online"], "devices reachable again")

    ok(client.put("/api/plugins/mqtt/enabled", json={"enabled": False}))
    assert not any(k.startswith("mqtt:") for k in devs()) and not broker.sessions
    stored = device_store.get()
    assert LAMP_ID in stored["adopted"] and stored["names"][PLUG_ID] == "Wama", "disabling forgot devices"
    ok(client.put("/api/plugins/mqtt/enabled", json={"enabled": True}))
    wait(lambda: LAMP_ID in devs(), "devices back after enabling")
    assert devs()[PLUG_ID]["name"] == "Wama" and devs()[LAMP_ID]["room"] == "Bad"

    # an address outside the home network is never contacted – the password does not leave the house
    before = len(streams)
    ok(client.put("/api/plugins/mqtt/settings", json={"values": {"host": "8.8.8.8"}}))
    wait(lambda: "Heimnetz" in (tile("mqtt")["status"].get("message") or ""), "foreign address rejected")
    assert streams[before:] == [] and LAMP_ID in device_store.get()["adopted"]
    ok(client.put("/api/plugins/mqtt/settings", json={"values": {"host": BROKER}}))
    wait(lambda: LAMP_ID in devs() and devs()[LAMP_ID]["online"], "connected again")
    print("7 outage of the broker: devices stay (offline), commands fail understandably, reconnecting by itself; plugin off/on"
          "and a foreign broker address: names, rooms and adopted devices are kept")

    # ------------------------------------------------------------------ 8) Netzwerk-Plugin: Anwesenheit
    ok(client.post("/api/plugins/network/targets", json={"name": "Fremd", "host": "8.8.8.8", "port": 80}), 400)
    answering.add("192.168.1.60")
    r = ok(client.post("/api/plugins/network/targets", json={"name": "Handy Anna", "host": "192.168.1.60", "port": 62078}))
    PHONE_ID = r["device_id"]
    assert r["answered"] is True and r["present"] is True and ("192.168.1.60", 62078, False) in streams
    assert PHONE_ID not in devs()
    ok(client.post("/api/plugins/network/adopt", json={"device_id": PHONE_ID, "room": "bad"}))
    phone = devs()[PHONE_ID]
    assert (phone["kind"], phone["present"], phone["room"], phone["plugin"], phone["online"]) == ("presence", True, "Bad", "network", True), phone
    assert phone.get("control") is None and "present" in flow_catalog.capabilities(phone)
    ok(client.post(f"/api/devices/{PHONE_ID}/command", json={"state": {"on": True}}), 400)        # nothing to switch
    assert next(i for i in ok(client.get("/api/overview"))["integrations"] if i["id"] == "network")["live"] is True
    r = ok(client.post("/api/plugins/network/targets", json={"name": "Tablet", "host": "192.168.1.61", "port": 80}))
    assert r["answered"] is False and r["present"] is None                                         # stays silent: still unknown, not "away"
    ok(client.post("/api/plugins/network/adopt", json={"device_id": r["device_id"]}))
    assert devs()[r["device_id"]].get("present") is None
    listed = ok(client.get("/api/plugins/network/targets"))
    assert [(t["name"], t["present"]) for t in listed["targets"]] == [("Handy Anna", True), ("Tablet", None)] and listed["away_after_minutes"] == 10
    ok(client.put("/api/plugins/network/settings", json={"values": {"away_after": 3, "interval": 20}}))
    assert ok(client.get("/api/plugins/network/targets"))["away_after_minutes"] == 3
    assert PHONE_ID in devs() and device_store.get()["rooms"][PHONE_ID] == "bad"                   # changing settings forgets nothing
    ok(client.delete(f"/api/plugins/network/targets/{r['key']}"))
    assert r["device_id"] not in devs() and PHONE_ID in devs()
    ok(client.delete("/api/plugins/network/targets/gibtsnicht"), 404)
    print("8 network: address outside the home network rejected; device answers -> present, stays silent -> unknown;"
          "presence as a device with room and flow property; deleting")

    # ------------------------------------------------------------------ 9) Login, backup
    anon = TestClient(main.app)
    for method, path in (("GET", "/api/plugins/mqtt/status"), ("POST", "/api/plugins/mqtt/manual"), ("GET", "/api/plugins/network/targets"),
                         ("POST", "/api/plugins/network/targets"), ("POST", "/api/plugins/mqtt/adopt_all")):
        assert anon.request(method, path, json={}).status_code == 401, (method, path)
    cfg = settings_store.load_settings()["plugins"]["settings"]
    assert cfg["mqtt"]["manual_devices"][0]["name"] == "Shelly Küche" and cfg["network"]["targets"][0]["name"] == "Handy Anna"
    assert cfg["mqtt"]["client_id"].startswith("comfyos-") and "password" not in cfg["mqtt"]
    print("9 routes of the plugins only with login; own devices and presence targets live in the settings (and thus in the backup)")

    # ------------------------------------------------------------------ 10) Echte TCP-Verbindung (127.0.0.1)
    egress._stream_factory = None           # from here ComfyOS really opens sockets – only to its own machine
    real = FakeBroker()
    real.publish("homeassistant/switch/echt/config", json.dumps({"dev": {"ids": ["echt"], "name": "Echte Steckdose"}, "uniq_id": "echt",
                                                                 "stat_t": "echt/state", "cmd_t": "echt/set"}), retain=True)

    async def listen():
        server = await asyncio.start_server(real._serve, "127.0.0.1", 0)
        return server, server.sockets[0].getsockname()[1]

    async def closed_port():
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        return port

    server, port = client.portal.call(listen)
    ok(client.put("/api/plugins/mqtt/settings", json={"values": {"host": "127.0.0.1", "port": port}}))
    wait(lambda: tile("mqtt")["status"].get("connected"), "real connection to the broker")
    wait(lambda: any(c["id"] == "mqtt:echt" for c in ok(client.get("/api/plugins/mqtt/devices"))["new"]), "device through the real connection")
    ok(client.post("/api/plugins/mqtt/adopt", json={"device_id": "mqtt:echt"}))
    ok(client.post("/api/devices/mqtt:echt/command", json={"state": {"on": True}}))
    wait(lambda: real.published_to("echt/set") == ["ON"], "command through the real connection")
    client.portal.call(real.publish, "echt/state", "ON")
    wait(lambda: devs()["mqtt:echt"]["on"] is True, "state through the real connection")
    assert real.retained["comfyos/status"] == b"online"

    # a closed port: the broker "does not answer" for MQTT but counts as an answer for the presence
    shut = client.portal.call(closed_port)
    ok(client.put("/api/plugins/mqtt/settings", json={"values": {"port": shut}}))
    wait(lambda: "geschlossen" in (tile("mqtt")["status"].get("message") or ""), "closed port reported")
    assert "mqtt:echt" in device_store.get()["adopted"]
    r = ok(client.post("/api/plugins/network/targets", json={"name": "Dieser Rechner", "host": "127.0.0.1", "port": shut}))
    assert r["answered"] is True and r["present"] is True, r
    r = ok(client.post("/api/plugins/network/targets", json={"name": "Dieser Rechner (offen)", "host": "127.0.0.1", "port": port}))
    assert r["answered"] is True
    client.portal.call(server.close)
    print("10 real connection through 127.0.0.1: connect, find devices, switch, receive state; a closed port is"
          "reported as such and counts as an answer for the presence")

    # ------------------------------------------------------------------ 11) Without a Home Base (a plugin like any other since 0.19)
    install_homebase(None)
    net = ok(client.get("/api/plugins/network/devices"))
    nid = (net["new"] + net["devices"])[0]["id"]
    if any(c["id"] == nid for c in net["new"]):
        ok(client.post("/api/plugins/network/adopt", json={"device_id": nid}))
    ov = ok(client.get("/api/overview"))
    ids = {d["id"] for d in ov["devices"]}
    assert ov["devices_error"] is None and "hb:therm" not in ids and nid in ids, ids
    assert ov["status"]["configured"] is True and not any(i["id"] == "qivicon" for i in ov["integrations"]), ov["status"]
    assert ov["room_pending"] == {"moves": [], "creates": [], "renames": [], "count": 0, "hub": None}, ov["room_pending"]
    # rooms live in the floor plan alone; the room assignment of a device lives in ComfyOS
    assert ok(client.post("/api/rooms", json={"name": "Werkstatt"})) == {"ok": True, "created": False, "room_id": None, "name": "Werkstatt"}
    ok(client.delete("/api/rooms/bad"), 400)
    ok(client.post("/api/rooms/push"), 400)
    plan = ok(client.get("/api/floorplan"))
    plan["rooms"] = [{"id": "w1", "name": "Werkstatt", "room_id": None, "x": 1, "y": 1}]
    ok(client.put("/api/floorplan", json=plan))
    r = ok(client.put(f"/api/devices/{nid}/room", json={"target": "plan:w1"}))
    assert r["pending"] is False and r["device"]["room"] == "Werkstatt" and r["device"]["room_id"] == "plan:w1", r
    assert r["device"]["local_room"] is True and device_store.get()["rooms"][nid] == "plan:w1"
    assert ok(client.get("/api/rooms/pending"))["count"] == 0, "without a hub there is nothing to bring up to date"
    # heating routines: without a plugin that syncs, everything stays local
    assert ok(client.get("/api/sync/status"))["configured"] is False
    assert ok(client.post("/api/home", json={"state": "AWAY"}))["sync"] == {"synced": False, "reason": "Abgleich nicht aktiv"}
    assert ok(client.post("/api/sync"))["configured"] is False
    # Foreign keys in settings.json (e.g. connection data from the time before 0.19, when the Home Base sat firmly in
    # the core): the API only shows what ComfyOS knows, and they drop out on the next save
    old_file = json.loads(settings_store.SETTINGS_FILE.read_text(encoding="utf-8"))
    old_file.update(mode="homebase", base_url="https://192.168.1.46", username="", telekom_username="kunde@example.com", gateway_id="XX" + "0000000-000")
    settings_store.SETTINGS_FILE.write_text(json.dumps(old_file), encoding="utf-8")
    shown = ok(client.get("/api/settings"))
    assert not {"base_url", "gateway_id", "telekom_username", "mode"} & set(shown), "foreign keys in /api/settings"
    assert set(ok(client.get("/api/plugins"))) == {"api", "revision", "plugins"}
    ok(client.put("/api/settings/accent", json={"color": ""}))
    assert not {"base_url", "gateway_id", "telekom_username", "mode"} & set(settings_store.load_settings()), "foreign keys survive saving"
    # Home Base connected: its thermostat is there, it manages the rooms; the network device keeps its room in ComfyOS
    install_homebase(MockHB("https://qivicon"))
    ov = ok(client.get("/api/overview"))
    assert "hb:therm" in {d["id"] for d in ov["devices"]} and ov["room_pending"]["hub"] == "Home Base", ov["room_pending"]
    assert any(i["id"] == "qivicon" and i["live"] for i in ov["integrations"])
    now = devs()
    assert now["hb:therm"]["local_room"] is False and now[nid]["local_room"] is True and now[nid]["room"] == "Werkstatt"
    print("11 without a Home Base: devices of the other plugins stay, rooms live in the floor plan, room assignment in ComfyOS,"
          "heating routines local; with a Home Base it manages the rooms again")

    # 12 Tile arrangement under System (since 0.19.2) and disabled plugins in the overview
    lay = ok(client.put("/api/settings/layout", json={"page": "system", "cols": 2, "columns": [["homeCard", "plugin-hue", "homeCard"], ["sysInfo", "bad id!"]]}))
    assert lay["layout"] == {"2": [["homeCard", "plugin-hue"], ["sysInfo"]]}, lay      # duplicates and invalid ones drop out
    assert client.put("/api/settings/layout", json={"page": "system", "cols": 3, "columns": [["a"], ["b"]]}).status_code == 400
    assert client.put("/api/settings/layout", json={"page": "flow", "cols": 2, "columns": [[], []]}).status_code == 422
    assert client.put("/api/settings/layout", json={"page": "system", "cols": 4, "columns": None}).status_code == 422
    ok(client.put("/api/settings/accent", json={"color": ""}))                       # saving something else keeps the arrangement
    assert ok(client.get("/api/settings"))["layout"] == {"system": {"2": [["homeCard", "plugin-hue"], ["sysInfo"]]}}
    assert ok(client.put("/api/settings/layout", json={"page": "system", "cols": 2, "columns": None}))["layout"] == {}
    # the tab "Plugins" (since 0.19.3) has an arrangement of its own, independent of "Einstellungen"
    ok(client.put("/api/settings/layout", json={"page": "plugins", "cols": 1, "columns": [["plugin-hue", "pluginInfo"]]}))
    assert ok(client.get("/api/settings"))["layout"] == {"plugins": {"1": [["plugin-hue", "pluginInfo"]]}}
    assert ok(client.put("/api/settings/layout", json={"page": "plugins", "cols": 1, "columns": None}))["layout"] == {}
    net_name = next(p["name"] for p in ok(client.get("/api/plugins"))["plugins"] if p["id"] == "network")
    ok(client.put("/api/plugins/network/enabled", json={"enabled": False}))
    st = ok(client.get("/api/status"))                                                        # Verbindungsblase oben (seit 0.19.3):
    assert not any(i["name"] == net_name for i in st["items"]), st["items"]                       # disabled does not count
    assert all(i["state"] in ("live", "ok", "bad", "error", "connecting") for i in st["items"])
    assert not any(i["id"] == "network" for i in ok(client.get("/api/overview"))["integrations"])
    ok(client.put("/api/plugins/network/enabled", json={"enabled": True}))
    print("12 tile arrangement saved and cleaned per tab and number of columns; disabled plugins do not count in the connection pill")

print("ALL INTEGRATION TESTS OK")
