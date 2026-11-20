"""Tests for the Home Base plugin. They run without ComfyOS and without a Home Base.

    python public/plugins/qivicon/tests/test_qivicon_plugin.py [path-to-comfyos]

Covered: how things become devices (kinds, control channels, meters, valve), the conversion to
the plugin API (link keys, capabilities, room), login and session handling (password sent once,
pause after a rejection, expired session, bundled device fetches), the plugin itself (home network
only, devices, commands, rooms, status), its endpoints, the Telekom login pause, the Magenta sync
rounds and the pairing catalog.
"""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "public" / "sdk"))
sys.path.insert(0, str(ROOT / "public" / "plugins"))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from plugin_api import DeviceUnreachable, HeatingChanges, HomeMode, PluginError  # noqa: E402
from testing import FakeContext  # noqa: E402

import qivicon  # noqa: E402
from qivicon import connector as hb_connector  # noqa: E402
from qivicon import magenta, model, pairing, telekom  # noqa: E402

PASSWORD = "ge" + "heim"                      # assembled at run time: no secrets in the source
TELEKOM_PASSWORD = "tk-" + "geheim"
HOST = "192.0.2.46"
GATEWAY = "AB" + "1234567-890"
LOGIN_PAGE = '<html><form method="post"><input type="hidden" name="csrf" value="c1"><input name="p" type="password"></form></html>'
DASHBOARD = ('<html class="aurora" data-access-token="sitzung-{n}" data-backend-token="BT-TEST" '
             'data-backend-url="https://backend-prod.cloud.qivicon.com/services/"></html>')
BRIDGE_MAC = "ecb5faaabbcc"
OUTSIDE = "evil.example" + ".com"                # a host outside the home network, assembled at run time


def channel(channel_id: str, item_type: str, item: str) -> dict:
    """Build the channel of a thing the way the Home Base reports it."""
    return {"id": channel_id, "itemType": item_type, "linkedItems": [item]}


THINGS = [
    # plug with metering: the channel is called "switchState", not "switch"
    {"UID": "dect:sercomm_indoorSmartPlug_v1:aa", "thingTypeUID": "dect:sercomm_indoorSmartPlug_v1", "label": "TV",
     "location": "wohnen", "statusInfo": {"status": "ONLINE"}, "properties": {"vendor": "Sercomm", "serialNumber": "S-1"},
     "channels": [channel("switchState", "Switch", "P1_sw"), channel("powerConsumption", "Number", "P1_pw"),
                  channel("energyConsumption", "Number", "P1_en")]},
    # plug of another vendor: channel "relay", meter reading in Wh
    {"UID": "zigbee:plug:bb", "thingTypeUID": "zigbee:plug", "label": "Stehlampe", "location": "wohnen",
     "statusInfo": {"status": "ONLINE"}, "channels": [channel("relay", "Switch", "P2_sw"), channel("meterReading", "Number", "P2_en")]},
    # flush-mounted actuator without a telling type name – only the channel reveals the switch
    {"UID": "hmip:actor:cc", "thingTypeUID": "hmip:HmIP-FSM", "label": "Terrasse", "location": "flur",
     "statusInfo": {"status": "ONLINE"}, "channels": [channel("on_off", "Switch", "A1_sw")]},
    {"UID": "schellenberg:shutter:dd", "thingTypeUID": "schellenberg:rollershutter", "label": "Rollo Küche",
     "location": "kueche", "statusInfo": {"status": "ONLINE"}, "channels": [channel("position", "Rollershutter", "R1")]},
    # wall button: switch channels that only report -> never a switch
    {"UID": "dect:button:ee", "thingTypeUID": "dect:wallButton", "label": "Taster Flur", "location": "flur",
     "statusInfo": {"status": "ONLINE"}, "channels": [channel("button", "Switch", "B1"), channel("alertState-tamper", "Switch", "B1_tamper")]},
    {"UID": "dect:smoke:ff", "thingTypeUID": "dect:smokeDetector", "label": "Rauch Flur", "location": "flur",
     "statusInfo": {"status": "ONLINE"}, "channels": [channel("alertState-smoke", "Switch", "S1")]},
    # a pure meter: consumption yes, switch no
    {"UID": "smappee:meter:gg", "thingTypeUID": "smappee:electricity", "label": "Zähler", "location": "flur",
     "statusInfo": {"status": "ONLINE"}, "channels": [channel("power", "Number", "M1_pw")]},
    {"UID": "zigbee:bulb:hh", "thingTypeUID": "zigbee:dimmableLight", "label": "Esstisch", "location": "kueche",
     "statusInfo": {"status": "ONLINE"}, "channels": [channel("brightness", "Dimmer", "L1_bri")]},
    {"UID": "hmip:etrv:ii", "thingTypeUID": "hmip:etrv", "label": "Heizkörper", "location": "wohnen",
     "statusInfo": {"status": "OFFLINE", "statusDetail": "GONE"},
     "channels": [channel("temperature", "Number", "V_t"), channel("setpointTemperature", "Number", "V_sp"),
                  channel("valvePosition", "Number", "V_vp"), channel("alertState-E1", "Switch", "V_e1")]},
    # things of a Hue Bridge that the Home Base relays, and the bridge itself (no device)
    {"UID": f"hue:0210:{BRIDGE_MAC}:7", "thingTypeUID": "hue:0210", "label": "Sofa", "location": "wohnen",
     "statusInfo": {"status": "ONLINE"}, "channels": [channel("color", "Color", "H7_color")]},
    {"UID": f"hue:0107:{BRIDGE_MAC}:0017880100aabbcc", "thingTypeUID": "hue:0107", "label": "Bewegung Flur", "location": "flur",
     "statusInfo": {"status": "ONLINE"}, "channels": [channel("presence", "Switch", "H_motion")]},
    {"UID": f"hue:bridge:{BRIDGE_MAC}", "thingTypeUID": "hue:bridge", "label": "Hue Bridge",
     "configuration": {"ipAddress": "192.0.2.10"}, "statusInfo": {"status": "ONLINE"}, "channels": []},
]
ITEMS = [{"name": "P1_sw", "state": "ON"}, {"name": "P1_pw", "state": "42.5 W"}, {"name": "P1_en", "state": "12.5 kWh"},
         {"name": "P2_sw", "state": "OFF"}, {"name": "P2_en", "state": "1500 Wh"}, {"name": "A1_sw", "state": "OFF"},
         {"name": "R1", "state": "100"}, {"name": "B1", "state": "OFF"}, {"name": "B1_tamper", "state": "OFF"},
         {"name": "S1", "state": "OFF"}, {"name": "M1_pw", "state": "230"}, {"name": "L1_bri", "state": "60"},
         {"name": "V_t", "state": "19.5"}, {"name": "V_sp", "state": "21"}, {"name": "V_vp", "state": "60 %"},
         {"name": "V_e1", "state": "ON"}, {"name": "H7_color", "state": "10,20,80"}, {"name": "H_motion", "state": "ON"},
         {"name": "wohnen", "label": "Wohnzimmer", "type": "Group"}, {"name": "kueche", "label": "Küche", "type": "Group"},
         {"name": "flur", "label": "Flur", "type": "Group"}]
ROOMS = [{"name": "wohnen", "label": "Wohnzimmer"}, {"name": "kueche", "label": "Küche"}, {"name": "flur", "label": "Flur"}]

# ------------------------------------------------------------------ 1) things -> devices
built = {device.id: device for device in model.build_devices(THINGS, ITEMS, [])}
plug = built["dect:sercomm_indoorSmartPlug_v1:aa"]
assert plug.kind == "switch" and plug.control_item == "P1_sw" and plug.state["on"] is True, plug
assert plug.power_w == 42.5 and plug.energy_kwh == 12.5 and plug.vendor == "Sercomm" and plug.details["Seriennummer"] == "S-1"
second_plug = built["zigbee:plug:bb"]
assert second_plug.control_item == "P2_sw" and second_plug.state["on"] is False
assert second_plug.energy_kwh == 1.5 and second_plug.power_w is None           # Wh -> kWh, no power reported
assert built["hmip:actor:cc"].kind == "switch" and built["hmip:actor:cc"].control_item == "A1_sw"
cover = built["schellenberg:shutter:dd"]
assert cover.kind == "cover" and cover.control_item == "R1" and cover.state["position"] == 100
bulb = built["zigbee:bulb:hh"]
assert bulb.kind == "light" and bulb.control_item == "L1_bri" and bulb.state["brightness"] == 60 and bulb.state["on"] is True
meter = built["smappee:meter:gg"]
assert meter.power_w == 230 and meter.control_item is None
button, smoke = built["dect:button:ee"], built["dect:smoke:ff"]
assert button.control_item is None, f"a button became a switch: {button}"
assert smoke.kind == "smoke" and smoke.control_item is None and smoke.state["alarm"] is False
thermostat = built["hmip:etrv:ii"]
assert thermostat.kind == "thermostat" and thermostat.control_item == "V_sp" and thermostat.state["target"] == 21.0
assert thermostat.state["temperature"] == 19.5 and thermostat.state["valve"] == 60
assert thermostat.online is False and thermostat.status_code == "GONE" and thermostat.status_text == model.GONE_TEXT
assert thermostat.problems == ["Gerätefehler E1"] and thermostat.room_id == "wohnen" and thermostat.room_name == "Wohnzimmer"
assert f"hue:bridge:{BRIDGE_MAC}" not in built, "a bridge is infrastructure, not a device"


def valve(channel_id: str, item_type: str, state: str, item: str | None = "x") -> model.Channel:
    """Build a parsed channel with a known state."""
    return model.Channel(channel_id, channel_id, item_type, item, model.known(state))


percent, find_valve = model.valve_percent, model.valve_channel
assert percent(find_valve([valve("temperature", "Number", "20.5"), valve("valvePosition", "Number:Dimensionless", "40 %")])) == 40
assert percent(find_valve([valve("LEVEL", "Number", "0.35")])) == 35, "Homematic LEVEL is a share 0…1"
assert percent(find_valve([valve("valve_position", "Dimmer", "100")])) == 100 and percent(find_valve([valve("valve_position", "Number", "0")])) == 0
assert percent(find_valve([valve("valveAdaptation", "Number", "3"), valve("valveState", "String", "ADAPTION_DONE")])) is None
assert percent(find_valve([valve("valvePosition", "Number", "NULL")])) is None
assert percent(find_valve([valve("valvePosition", "Number", "40", item=None)])) is None
assert percent(find_valve([valve("valvePosition", "Number", "250")])) == 100 and percent(find_valve([valve("battery_level", "Number", "0.5")])) is None
without_valve = model.build_devices([dict(THINGS[8], channels=THINGS[8]["channels"][:2])], ITEMS, [])[0]
assert "valve" not in without_valve.state, "no valve channel, no valve value"
# read only (since 1.1.0): humidity of a climate sensor, ambient light of a motion detector – nothing else gains these values
climate, motion_detector = model.build_devices(
    [{"UID": "hmip:climate:jj", "thingTypeUID": "hmip:HmIP-STH", "label": "Klima Bad", "location": "flur",
      "statusInfo": {"status": "ONLINE"}, "channels": [channel("temperature", "Number", "C_t"), channel("humidity", "Number:Dimensionless", "C_h")]},
     {"UID": "hmip:motion:kk", "thingTypeUID": "hmip:HmIP-SMI", "label": "Bewegung Bad", "location": "flur",
      "statusInfo": {"status": "ONLINE"}, "channels": [channel("motion", "Switch", "K_m"), channel("illuminance", "Number", "K_lx")]}],
    ITEMS + [{"name": "C_t", "state": "21.4"}, {"name": "C_h", "state": "55 %"}, {"name": "K_m", "state": "OFF"},
             {"name": "K_lx", "state": "37.5"}], [])
assert climate.state["humidity"] == 55 and climate.state["temperature"] == 21.4 and motion_detector.state["illuminance"] == 37.5
assert "humidity" not in thermostat.state and "illuminance" not in plug.state and climate.control_item is None
print("1 things become devices: plugs, actuator, shutter, lamp and thermostat can be switched; buttons, smoke and meters cannot; "
      "humidity and ambient light are read")

# ------------------------------------------------------------------ 2) conversion to the plugin API
converted = {device.id: model.to_device(device) for device in built.values()}
api_plug = converted["dect:sercomm_indoorSmartPlug_v1:aa"]
assert api_plug.room_id == "wohnen" and api_plug.room_hint == "Wohnzimmer" and api_plug.type_name == "dect:sercomm_indoorSmartPlug_v1"
assert [(cap.key, cap.writable) for cap in api_plug.capabilities] == [("on", True)] and api_plug.power_w == 42.5
assert api_plug.state["hb_status"] == "ONLINE" and not api_plug.relayed and not api_plug.discovered
assert converted["dect:button:ee"].capabilities == [] and converted["smappee:meter:gg"].capabilities == []
api_thermostat = converted["hmip:etrv:ii"]
assert api_thermostat.capabilities[0].key == "target" and api_thermostat.capabilities[0].maximum == 30.0
assert api_thermostat.problems == ["Gerätefehler E1"] and api_thermostat.state["hb_detail"] == "GONE" and api_thermostat.online is False
assert any(reading.key == "valvePosition" and reading.value == "60 %" for reading in api_thermostat.readings)
hue_lamp = converted[f"hue:0210:{BRIDGE_MAC}:7"]
assert hue_lamp.link_keys == [f"hue:{BRIDGE_MAC}:light:7"] and hue_lamp.relayed, hue_lamp
hue_motion = converted[f"hue:0107:{BRIDGE_MAC}:0017880100aabbcc"]
assert hue_motion.kind == "motion" and hue_motion.link_keys == ["zigbee:0017880100aabbcc"] and hue_motion.state["motion"] is True
print("2 plugin API: room, type, capabilities only with a control item, link keys for relayed Hue things")


# ------------------------------------------------------------------ a Home Base that lives in a handler function
class FakeHomeBase:
    """Answers like the web UI of a Home Base: login form, dashboard with the session token, REST proxy."""

    def __init__(self) -> None:
        """Start with the example rooms and devices, without a session."""
        self.password = PASSWORD
        self.session_valid = False
        self.down = False
        self.logins = 0                 # how often a password arrived
        self.calls: list[tuple[str, str, object]] = []
        self.rooms = [dict(room) for room in ROOMS]
        self.records = [{"id": thing["UID"], "room": thing.get("location")} for thing in THINGS]

    def rest(self, method: str, path: str, body: object) -> object:
        """Answer one REST call that arrived through the JSON-RPC tunnel."""
        if path == "/rest/things":
            return THINGS
        if path == "/rest/items":
            return ITEMS
        if path == "/rest/qivicon/devices":
            return self.records
        if path == "/rest/qivicon/system":
            return {"label": "QIVICON Home Base", "internet": {"status": "online"}}
        if path == "/rest/qivicon/rooms":
            if method == "POST":
                self.rooms = [{"name": room["name"] or "room-neu", "label": room["label"]} for room in json.loads(body)]
            return self.rooms
        if method == "PUT" and path.startswith("/rest/qivicon/devices/"):
            record = json.loads(body)
            self.records = [record if entry["id"] == record["id"] else entry for entry in self.records]
        return ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one HTTP request like a Home Base: system info, login and the JSON-RPC tunnel (401 without a
        session)."""
        if self.down:
            raise httpx.ConnectError("offline")
        assert request.url.host == HOST, f"request left the Home Base: {request.url}"
        path = request.url.path
        if path.startswith("/system/info"):
            return httpx.Response(200, json={"serialNumber": GATEWAY})
        if path == "/remote/json-rpc":
            if not self.session_valid:
                return httpx.Response(401)
            call = json.loads(request.content)[0]["params"][0]
            self.calls.append((call["method"], call["path"], call["body"]))
            answer = self.rest(call["method"], call["path"], call["body"])
            return httpx.Response(200, json=[{"result": {"Status": 200, "Body": json.dumps(answer)}}])
        if request.method == "POST":                      # the login form comes back
            self.logins += 1
            form = parse_qs(request.content.decode(), keep_blank_values=True)
            assert form["u"] == [""] and form["csrf"] == ["c1"] and form["t"][0].isdigit(), form
            if form["p"] != [self.password]:
                return httpx.Response(401, text=LOGIN_PAGE)
            self.session_valid = True
            return httpx.Response(200, text=DASHBOARD.format(n=self.logins))
        return httpx.Response(401, text=LOGIN_PAGE)


def connector_for(home_base: FakeHomeBase, password: str = PASSWORD) -> hb_connector.HomeBaseConnector:
    """Create a connector that talks to the fake Home Base, with a small delay per answer."""
    async def answer_slowly(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.002)          # like a real network: other calls get their turn in the meantime
        return home_base(request)

    return hb_connector.HomeBaseConnector(
        f"https://{HOST}/system/http/login/", password,
        open_client=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(answer_slowly), **options))


# ------------------------------------------------------------------ 3) login
async def login_tests() -> None:
    """Login: one login for parallel calls, a rejected password is not repeated, an unreachable Home Base is reported."""
    home_base = FakeHomeBase()
    hub = connector_for(home_base)
    assert hub.host == HOST and hub.base_url == f"https://{HOST}/", "a pasted path is dropped"
    await asyncio.gather(*(hub._rpc_get("/rest/things") for _ in range(5)))
    assert home_base.logins == 1, f"{home_base.logins} logins for five parallel calls"
    assert hub.access_token == "sitzung-1" and hub.gateway_id == GATEWAY and hub.backend_token == "BT-TEST"
    assert hub.backend_url == "https://backend-prod.cloud.qivicon.com/services" and hub.last_ok is True
    status = await hub.status()
    assert status["connected"] and "Internetstatus: online" in status["message"], status
    await hub.close()

    # a rejected password is sent once, then nothing for five minutes – repeated attempts can lock the Home Base
    home_base = FakeHomeBase()
    hub = connector_for(home_base, "falsch")
    for _ in range(3):
        try:
            await hub.list_devices()
            raise AssertionError("rejected login was not reported")
        except hb_connector.QiviconError as error:
            assert "abgelehnt" in str(error), error
    assert home_base.logins == 1 and hub.last_ok is False, home_base.logins
    assert (await hub.status())["connected"] is False
    hub.password = PASSWORD
    await hub.connect(force=True)                     # "Verbinden" lifts the pause
    assert home_base.logins == 2
    await hub.close()

    for bad in ("", "https://", "http://[::1"):
        try:
            hb_connector.normalize_origin(bad)
            raise AssertionError(bad)
        except hb_connector.QiviconError:
            pass
    assert hb_connector.catalog_access('<html data-backend-token="{{T}}" data-backend-url="https://x.cloud.qivicon.com/s">') is None
    assert hb_connector.catalog_access(f'<html data-backend-token="BT" data-backend-url="https://{OUTSIDE}/s">') is None
    assert hb_connector.catalog_access('<html data-backend-token="BT" data-backend-url="http://x.cloud.qivicon.com/s">') is None


asyncio.run(login_tests())
print("3 login: form with the page's hidden fields, one login for parallel calls, a rejected password goes out once")


# ------------------------------------------------------------------ 4) session and device list
async def session_tests() -> None:
    """Session: a dropped session is renewed once for all waiting calls, outages are reported, writes arrive
    unchanged."""
    home_base = FakeHomeBase()
    hub = connector_for(home_base)
    await hub.connect()
    home_base.session_valid = False                   # the Home Base dropped the session (e.g. restart)
    await hub._write_item("P1_sw", "ON")
    assert home_base.calls[-1] == ("POST", "/rest/items/P1_sw", "ON") and home_base.logins == 2, home_base.calls[-1]
    home_base.session_valid = False
    await asyncio.gather(*(hub._write_item(f"L{n}", "OFF") for n in range(4)), hub._rpc_get("/rest/items"))
    assert home_base.logins == 3 and sum(1 for call in home_base.calls if call[2] == "OFF") == 4

    home_base.down = True
    for call in (hub._write_item("L1", "ON"), hub._rpc_get("/rest/things"), hub._rpc_write("POST", "/rest/qivicon/rooms", [])):
        try:
            await call
            raise AssertionError("network failure was not reported")
        except hb_connector.QiviconError as error:
            assert isinstance(error, DeviceUnreachable) and "nicht erreichbar" in str(error), error
    assert hub.last_ok is False
    home_base.down = False

    # bundled fetches: six parallel requests cause at most two fetches
    home_base.calls.clear()
    results = await asyncio.gather(*(hub.list_devices(max_age=0) for _ in range(6)))
    fetches = sum(1 for call in home_base.calls if call[1] == "/rest/things")
    assert len(results) == 6 and 1 <= fetches <= 2, f"{fetches} fetches for six parallel requests"
    assert hub.hue_bridges == [{"uid": f"hue:bridge:{BRIDGE_MAC}", "mac": BRIDGE_MAC, "host": "192.0.2.10"}]

    # an answer requested before a command is not cached
    gate, slow = asyncio.Event(), hub._rpc_get

    async def wait_for_gate(path: str) -> object:
        if path == "/rest/things":
            await gate.wait()
        return await slow(path)

    hub._rpc_get = wait_for_gate
    hub.invalidate_devices()
    fetch = asyncio.ensure_future(hub.list_devices(max_age=0))
    await asyncio.sleep(0.01)
    hub.invalidate_devices()                          # = a command was written
    gate.set()
    await fetch
    assert hub._dev_cache is None
    await hub.list_devices(max_age=0)
    assert hub._dev_cache is not None
    hub._rpc_get = slow

    # commands: words of openHAB, limits, devices without a control item
    after = await hub.set_device_state("dect:sercomm_indoorSmartPlug_v1:aa", {"on": False})
    assert after == {"on": False} and home_base.calls[-1] == ("POST", "/rest/items/P1_sw", "OFF")
    assert await hub.set_device_state("hmip:etrv:ii", {"target": 22.3}) == {"target": 22.5}
    assert home_base.calls[-1] == ("POST", "/rest/items/V_sp", "22.5")
    assert await hub.set_device_state("hmip:etrv:ii", {"delta": -1}) == {"target": 20.0}
    assert await hub.set_device_state("schellenberg:shutter:dd", {"position": 40}) == {"position": 40}
    await hub.set_device_state("schellenberg:shutter:dd", {"cover": "stop"})
    assert home_base.calls[-1][2] == "STOP"
    for device_id, state in (("hmip:etrv:ii", {"target": 45}), ("schellenberg:shutter:dd", {"position": 140}),
                             ("schellenberg:shutter:dd", {"cover": "fly"}), ("dect:button:ee", {"on": True}),
                             ("zigbee:bulb:hh", {"brightness": 40}), ("gibt:es:nicht", {"on": True})):
        writes = len(home_base.calls)
        try:
            await hub.set_device_state(device_id, state)
            raise AssertionError((device_id, state))
        except hb_connector.QiviconError:
            assert not [call for call in home_base.calls[writes:] if call[0] == "POST"], "a refused command was written"

    # rooms: create, rename, move, delete only when empty
    new_room = await hub.create_room("  Arbeitszimmer  ")
    assert new_room == "room-neu" and {"name": "room-neu", "label": "Arbeitszimmer"} in home_base.rooms
    await hub.rename_room("room-neu", "Büro")
    await hub.move_device("zigbee:plug:bb", "room-neu")
    assert next(entry for entry in home_base.records if entry["id"] == "zigbee:plug:bb")["room"] == "room-neu"
    for refused in (hub.delete_room("room-neu"), hub.delete_room("gibt-es-nicht"), hub.move_device("zigbee:plug:bb", "gibt-es-nicht"),
                    hub.move_device("gibt:es:nicht", "flur"), hub.rename_room("gibt-es-nicht", "X"), hub.create_room("  ")):
        try:
            await refused
            raise AssertionError("refused room change went through")
        except hb_connector.QiviconError:
            pass
    await hub.move_device("zigbee:plug:bb", "wohnen")
    await hub.delete_room("room-neu")
    assert not any(room["name"] == "room-neu" for room in home_base.rooms)
    await hub.close()


asyncio.run(session_tests())
print("4 session: a command survives an expired session, network failures are reported, fetches are bundled, "
      "commands and rooms are checked before anything is written")


# ------------------------------------------------------------------ 5) the plugin
async def plugin_tests() -> None:
    """Walk the plugin through its life against the fake Home Base: not set up, devices, commands, rooms, outage."""
    home_base = FakeHomeBase()
    context = FakeContext("qivicon", http_handler=home_base, secrets=["password", "telekom_password"], local_hosts=("192.0.2.", "qivicon"))
    plugin = qivicon.setup(context)
    assert plugin.manages_rooms and plugin.syncs_heating
    await plugin.start()
    assert plugin.status()["configured"] is False and await plugin.devices() == [], "not set up: no devices, no connection"
    assert home_base.logins == 0 and not context.requests
    try:
        await plugin.command("zigbee:plug:bb", {"on": True}, "command")
        raise AssertionError("command without a Home Base")
    except PluginError as error:
        assert "Keine Home Base" in str(error)

    # an address outside the home network never gets the password (e.g. from a restored backup)
    context.secrets.set("password", PASSWORD)
    context.settings.set("address", f"https://{OUTSIDE}")
    assert plugin.build_connector() is None and "nicht im Heimnetz" in context.events[-1]
    context.settings.set("address", f"https://{HOST}")
    await plugin.use_connector(plugin.build_connector())
    plugin.max_device_age = 0
    devices = {device.id: device for device in await plugin.devices()}
    assert len(devices) == 11 and devices["zigbee:plug:bb"].room_id == "wohnen" and home_base.logins == 1
    assert plugin.status()["configured"] and plugin.status()["connected"]
    assert plugin.status()["egress"] == {"catalog": ["on_demand", "nur beim Anlernen"], "magenta": ["off", "nicht eingerichtet"]}
    assert plugin.hints() == [{"kind": "hue-bridge", "host": "192.0.2.10"}]
    assert await plugin.command("zigbee:plug:bb", {"on": True}, "command") == {"on": True}
    assert [(room.id, room.name) for room in await plugin.rooms()] == [("wohnen", "Wohnzimmer"), ("kueche", "Küche"), ("flur", "Flur")]
    room_id = await plugin.create_room("Keller")
    await plugin.move_device("zigbee:plug:bb", room_id)
    await plugin.rename_room(room_id, "Hobbykeller")
    await plugin.move_device("zigbee:plug:bb", "wohnen")
    await plugin.delete_room(room_id)
    home_base.down = True
    try:
        await plugin.devices()
        raise AssertionError("Home Base down was not reported")
    except DeviceUnreachable:
        assert plugin.status()["connected"] is False
    home_base.down = False
    await plugin.stop()
    assert plugin.connector is None


asyncio.run(plugin_tests())
print("5 plugin: nothing without setup, password only into the home network, devices, commands, rooms, status")

# ------------------------------------------------------------------ 6) endpoints of the tile
home_base = FakeHomeBase()
context = FakeContext("qivicon", http_handler=home_base, secrets=["password", "telekom_password"], local_hosts=("192.0.2.", "qivicon"))
plugin = qivicon.setup(context)
app = FastAPI()
app.include_router(plugin.routes())
with TestClient(app) as client:
    assert client.get("/status").json()["configured"] is False
    assert client.get("/inbox").status_code == 400 and client.get("/explore").status_code == 400
    assert client.get("/diagnose").json()["ok"] is False
    for outside in (f"https://{OUTSIDE}", "93.184.216.34", f"https://qivicon@{OUTSIDE}"):
        answer = client.post("/connect", json={"base_url": outside, "password": PASSWORD})
        assert answer.status_code == 400 and home_base.logins == 0, (outside, answer.text)
    assert client.post("/connect", json={"base_url": f"https://{HOST}"}).status_code == 400, "no password"
    answer = client.post("/connect", json={"base_url": f"https://{HOST}/dashboard", "password": PASSWORD}).json()
    assert answer["connected"] and context.settings.get("address") == f"https://{HOST}" and context.settings.get("gateway_id") == GATEWAY
    assert context.reloads == 1 and context.events[-1] == "Verbindung zur Home Base hergestellt"
    status = client.get("/status").json()
    assert status["connected"] and status["password_saved"] and PASSWORD not in json.dumps(status)
    # a stored password is never sent to a different address
    answer = client.post("/connect", json={"base_url": "https://192.0.2.99"})
    assert answer.status_code == 400 and "erneut eingeben" in answer.json()["detail"] and home_base.logins == 1
    assert client.post("/test").json()["connected"] and client.get("/diagnose").json()["ok"]
    assert client.get("/explore").json()["results"][0]["path"] == "/rest/"
    assert client.post("/magenta", json={"username": "kunde@example.com"}).status_code == 400, "no Telekom password"
    assert client.get("/pairing/image", params={"p": "../../etc/passwd"}).status_code == 404
    assert client.get("/things/gibt:es:nicht/reconnect").status_code == 404
    assert client.post("/forget").json()["ok"] and client.get("/status").json()["configured"] is False
    assert context.secrets.get("password") is None and context.settings.get("address") is None
print("6 endpoints: connect only inside the home network, a stored password stays with its address, forget removes it")


# ------------------------------------------------------------------ 7) Telekom: a rejected password is not sent again
async def telekom_tests() -> None:
    """A rejected Telekom login is reported, sent only once and never repeated on its own."""
    posts: list[str] = []

    def rejecting(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        return httpx.Response(401, json={"error_description": "Zugangsdaten falsch"})

    cloud = telekom.TelekomCloud("kunde@example.com", "falsch",
                                 lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(rejecting), **options))
    for call in (cloud.token, cloud.token, lambda: cloud.plugin_call("listRooms", "GW"), cloud.customer_self, cloud.refresh):
        try:
            await call()
            raise AssertionError("rejected login was not reported")
        except telekom.TelekomError as error:
            assert "falsch" in str(error), error
    assert len(posts) == 1, f"password sent {len(posts)} times instead of once"
    cloud._login_blocked_until = 0                    # pause over: exactly one new attempt
    try:
        await cloud.token()
    except telekom.TelekomError:
        pass
    assert len(posts) == 2
    await cloud.close()


asyncio.run(telekom_tests())
print("7 Telekom: a rejected password goes out once, then 30 minutes pause (protects the account)")


# ------------------------------------------------------------------ 8) Magenta sync
class FakeTelekom:
    """Token endpoint and cloud relay with the heating plugin of one Home Base."""

    def __init__(self) -> None:
        """Start without recorded commands."""
        self.commands: list[tuple[str, dict]] = []
        self.gateways: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer like the Telekom login and the heating service; every command is recorded."""
        if request.url.host == "global.telekom.com":
            form = parse_qs(request.content.decode())
            assert form["username"] == ["kunde@example.com"] and form["password"] == [TELEKOM_PASSWORD]
            return httpx.Response(200, json={"access_token": "app-token", "refresh_token": "r1", "expires_in": 28800})
        assert request.url.host == "ris-prod.cloud.qivicon.com" and request.headers["authorization"] == "Bearer app-token"
        self.gateways.append(request.headers["QIVICON-ServiceGatewayId"])
        arguments = json.loads(request.content)[0]["params"][0]
        command = arguments["command"]
        self.commands.append((command, arguments))
        results = {
            "getHeatingMode": {"heatingMode": "AUTO"},
            "getHeatingModes": {"heatingModes": [{"key": "AUTO", "name": "Komfort", "lastExecutionTime": 5}, {"key": "ECO1", "name": "Sparen"}]},
            "getDashboardInfo": {"COMFORT": 22.5, "ECO": 16, "homeState": "AWAY", "heatingModePresent": "AUTO", "heatingModeAway": "ECO1"},
            "getHeatingTriggers": {"0": {"hz_1": {"0600": "21", "2200": "ECO"}, "SYSTEM": {"0000": "OFF"}}},
            "listRooms": {"hz_1": {"name": "Bad"}, "SYSTEM": {"name": "System"}},
            "createNewHeatingMode": {"key": "NEU7"},
            "getHomeStateProperties": {"activated": True, "activationTimes": []},
        }
        return httpx.Response(200, json=[{"result": {"state": "ok", "command": command, "result": results.get(command, {})}}])


async def sync_tests() -> None:
    """Heating plans: local changes are sent in the right order, the answer of the service is taken over, failures are
    reported."""
    cloud = FakeTelekom()
    context = FakeContext("qivicon", http_handler=cloud, secrets=["password", "telekom_password"])
    plugin = qivicon.setup(context)
    assert plugin.heating_status()["configured"] is False
    assert (await plugin.sync_heating())["ok"] is None and not cloud.commands, "not set up: no connection to Telekom"
    context.settings.set("telekom_username", "kunde@example.com")
    context.secrets.set("telekom_password", TELEKOM_PASSWORD)
    context.settings.set("gateway_id", GATEWAY)
    plans = context.heating
    plans.routines = {"local-1": {"name": "Urlaub", "schedule": {"0": {"hz_1": {"0700": "18"}}}},
                      "AUTO": {"name": "Komfort neu", "schedule": {"1": {"hz_1": {"0600": "22"}}}}}
    plans.pending = HeatingChanges(deleted=["ALT"], created=["local-1"], updated=["AUTO"], renamed=["AUTO"], activate="AUTO")
    plans.home_mode = HomeMode(state="AWAY", state_changed=True, present_routine="AUTO", away_routine="ECO1", mapping_changed=True)
    status = await plugin.sync_heating(pull=False, force=True)
    assert status["ok"] is True and status["configured"] and status["interval"] == 300, status
    sent = [command for command, _ in cloud.commands]
    assert sent == ["deleteHeatingMode", "createNewHeatingMode", "setHeatingTriggers", "changeNameOfHeatingMode", "setHeatingMode",
                    "setHomeState", "getHomeStateProperties", "setHomeStateProperties"], sent
    assert set(cloud.gateways) == {GATEWAY}
    assert plans.rekeyed == [("local-1", "NEU7")] and plans.touched == 1
    assert sorted(plans.confirmed) == [("ALT", "deleted"), ("AUTO", "activate"), ("AUTO", "renamed"), ("AUTO", "updated")]
    assert not plans.home_mode.state_changed and not plans.home_mode.mapping_changed
    assert dict(cloud.commands)["setHomeStateProperties"]["activated"] is True, "existing properties are kept"

    cloud.commands.clear()
    status = await plugin.sync_heating(pull=True, force=True)
    assert status["ok"] is True and status["last_sync"], status
    merged = plans.merged[-1]
    assert [(routine.key, routine.name, routine.last_run) for routine in merged["routines"]] == [("AUTO", "Komfort", 5), ("ECO1", "Sparen", None)]
    assert merged["active"] == "AUTO" and merged["levels"] == {"COMFORT": 22.5, "ECO": 16.0} and merged["room_names"] == {"hz_1": "Bad"}
    assert merged["routines"][0].schedule["0"]["hz_1"] == {"0600": "21", "2200": "ECO"}
    assert plans.home_merged[-1] == ("AWAY", "AUTO", "ECO1")
    before = len(cloud.commands)
    await plugin.sync_heating(pull=True)              # too soon after the last round: skipped
    assert len(cloud.commands) == before
    assert plugin.status()["egress"]["magenta"] == ["on", "eingerichtet"]
    await plugin.stop()


asyncio.run(sync_tests())
print("8 Magenta sync: nothing without setup, pending changes are sent and confirmed, the app's routines are handed over")

# ------------------------------------------------------------------ 9) pairing catalog
BACKEND = "https://backend-prod.cloud.qivicon.com/services"
PAIRABLE = [{"thingTypeUID": "dect:thermo", "label": "Heating Valve", "setupInbox": "flow", "setupInboxHasSteps": False},
            {"thingTypeUID": "dlink-camera:cam", "label": "Camera", "setupInbox": "flow", "setupInboxHasSteps": True},
            {"thingTypeUID": "ble:lock", "label": "Lock", "setupManual": "app"},
            {"thingTypeUID": "dect:bridge", "label": "DECT (internal)", "setupInbox": None, "setupManual": None}]
CATALOG = [{"hardwareId": "H1", "name": "Thermostat", "vendor": "Eurotronic", "thingTypeIds": ["dect:thermo"],
            "image": BACKEND + "/pis/blob/localized/t.png", "models": ["Comet"]},
           {"hardwareId": "H2", "name": "Kamera", "thingTypeIds": ["dlink-camera:cam"], "image": f"https://{OUTSIDE}/x.png"},
           {"hardwareId": "H3", "name": "Versteckt", "thingTypeIds": ["dect:thermo"], "hidden": True},
           {"hardwareId": "H4", "name": "Unbekannt", "thingTypeIds": ["zwave:x"]}]
vendors = pairing.build_catalog(CATALOG, PAIRABLE, BACKEND)
assert [vendor["name"] for vendor in vendors] == ["D-Link", "Eurotronic"], vendors
thermostat_entry = pairing.find_entry(vendors, "H1")
assert thermostat_entry["image"] == "/api/plugins/qivicon/pairing/image?p=localized/t.png" and thermostat_entry["setup"] == pairing.SETUP_AUTO
camera_entry = pairing.find_entry(vendors, "H2")
assert camera_entry["image"] is None and camera_entry["setup"] == pairing.SETUP_STEPS, "pictures only from the catalog itself"
fallback = pairing.catalog_from_types(PAIRABLE)
assert sum(len(vendor["devices"]) for vendor in fallback) == 3, "internal types without a pairing flow drop out"
details = pairing.device_details(thermostat_entry, {"preparationSteps": [{"text": " Taste drücken "}], "resetSteps": [{"text": ""}]}, BACKEND)
assert details["steps"] == [{"title": None, "text": "Taste drücken", "image": None}] and details["reset"] == []
assert pairing.device_details(thermostat_entry, None, BACKEND)["steps"][0]["text"] == pairing.GENERIC_STEP
assert pairing.valid_blob_path("localized/a/b.png") and not pairing.valid_blob_path("../x") and not pairing.valid_blob_path("a/../../b")
assert not pairing.valid_blob_path("a" * 301) and not pairing.valid_blob_path("a b")
inbox = [{"thingUID": "dect:thermo:1", "thingTypeUID": "dect:thermo"}, {"thingUID": "dect:thermo:2", "thingTypeUID": "dect:thermo", "flag": "IGNORED"}]
assert [entry["uid"] for entry in pairing.found_in_inbox(inbox, ["dect:thermo"])] == ["dect:thermo:1"]
assert pairing.reconnect_info([]) is None
info = pairing.reconnect_info([{"id": "f1", "steps": [{"id": "wait", "label": " Warten ", "timeout": 120}, {"label": "ohne id"}]}])
assert info == {"flow": "f1", "label": "Neu verbinden", "steps": [{"id": "wait", "label": "Warten", "timeout": 120}]}
assert pairing.reconnected({"status": "UNKNOWN"}) and not pairing.reconnected({"status": "OFFLINE"}) and not pairing.reconnected(None)
print("9 pairing catalog: only what the Home Base can pair, pictures only through the plugin's own route, fallback without catalog")

print("ALLE QIVICON-PLUGIN-TESTS OK")
