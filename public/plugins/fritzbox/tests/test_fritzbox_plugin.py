"""Tests for the FRITZ!Box plugin. They run without ComfyOS against a rebuilt box; nothing leaves the machine.

    python public/plugins/fritzbox/tests/test_fritzbox_plugin.py [path-to-comfyos]

Covered: the login (PBKDF2 and the old MD5 form, refusal with a pause, one login at a time, the password only to the
home network), the device list (kinds, values, HAN-FUN, groups), the commands, button presses from the press times,
a session that expired, presence through TR-064 with its waiting time, and the endpoints of the tile.
"""
import asyncio
import hashlib
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "public" / "sdk"))
sys.path.insert(0, str(ROOT / "public" / "plugins"))

from plugin_api import PRESS_SHORT, DeviceUnreachable, PluginError  # noqa: E402
from testing import FakeContext  # noqa: E402

import fritzbox  # noqa: E402
from fritzbox import aha, tr064  # noqa: E402

BOX = "192.0.2.1"
PASSWORD = "fritz-" + "geheim"                # put together at run time (secret scan)
SID = "0123456789abcdef"
SALT1, SALT2 = "a1b2c3d4", "e5f6a7b8"
CHALLENGE = f"2$10${SALT1}$10${SALT2}"


def expected_response(password: str) -> str:
    """The PBKDF2 answer, computed independently of the plugin."""
    first = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(SALT1), 10)
    return SALT2 + "$" + hashlib.pbkdf2_hmac("sha256", first, bytes.fromhex(SALT2), 10).hex()


def device_list(socket_on: str = "1", pressed: int = 1700000000) -> str:
    """A device list as the box sends it: socket, thermostat, colour lamp, DECT 440, HAN-FUN contact, blind, group."""
    return f"""<?xml version="1.0" encoding="utf-8"?><devicelist version="1" fwversion="7.57">
<device identifier="11657 0272633" id="16" functionbitmask="35712" fwversion="04.16" manufacturer="AVM" productname="FRITZ!DECT 200">
 <present>1</present><name>Stecker Küche</name><switch><state>{socket_on}</state><mode>manuell</mode><lock>0</lock></switch>
 <simpleonoff><state>{socket_on}</state></simpleonoff><powermeter><voltage>230051</voltage><power>84000</power><energy>31400</energy></powermeter>
 <temperature><celsius>225</celsius><offset>0</offset></temperature></device>
<device identifier="09995 0335100" id="17" functionbitmask="320" fwversion="05.16" manufacturer="AVM" productname="FRITZ!DECT 301">
 <present>1</present><name>Heizung Bad</name><temperature><celsius>210</celsius></temperature>
 <hkr><tist>42</tist><tsoll>43</tsoll><battery>70</battery><batterylow>0</batterylow><windowopenactiv>1</windowopenactiv></hkr></device>
<device identifier="12701 0034567-1" id="18" functionbitmask="237572" fwversion="34.10" manufacturer="AVM" productname="FRITZ!DECT 500">
 <present>1</present><name>Leselampe</name><simpleonoff><state>1</state></simpleonoff>
 <levelcontrol><level>128</level><levelpercentage>50</levelpercentage></levelcontrol>
 <colorcontrol supported_modes="5" current_mode="4"><hue>0</hue><saturation>0</saturation><temperature>2700</temperature></colorcontrol></device>
<device identifier="09995 1234567" id="19" functionbitmask="1048864" fwversion="05.10" manufacturer="AVM" productname="FRITZ!DECT 440">
 <present>1</present><name>Taster Flur</name><battery>90</battery><batterylow>0</batterylow>
 <temperature><celsius>195</celsius></temperature><humidity><rel_humidity>48</rel_humidity></humidity>
 <button identifier="09995 1234567-1" id="5000"><name>Taster Flur: Oben rechts</name><lastpressedtimestamp>{pressed}</lastpressedtimestamp></button>
 <button identifier="09995 1234567-3" id="5001"><name>Taster Flur: Unten rechts</name><lastpressedtimestamp>1600000000</lastpressedtimestamp></button></device>
<device identifier="11324 0012345" id="406" functionbitmask="1" fwversion="0.0" manufacturer="0x2c3c" productname="HAN-FUN">
 <present>1</present><name>Fensterkontakt</name></device>
<device identifier="11324 0012345-1" id="2000" functionbitmask="8208" fwversion="0.0" manufacturer="0x2c3c" productname="HAN-FUN">
 <present>1</present><name>Fenster Bad</name><alert><state>1</state></alert></device>
<device identifier="14080 0099999-1" id="2001" functionbitmask="335888" fwversion="0.0" manufacturer="0x0feb" productname="Rollotron">
 <present>0</present><name>Rollladen</name><levelcontrol><level>64</level><levelpercentage>25</levelpercentage></levelcontrol>
 <blind><endpositionsset>1</endpositionsset><mode>manuell</mode></blind></device>
<group identifier="grp303E4F-3F7D99BE3" id="900" functionbitmask="4160"><present>1</present><name>Alle Heizungen</name></group>
</devicelist>"""


HOSTS = """<?xml version="1.0"?><List><Item><Index>1</Index><IPAddress>192.0.2.40</IPAddress><MACAddress>AA:BB:CC:DD:EE:01</MACAddress>
<Active>1</Active><HostName>Handy-Anna</HostName></Item><Item><Index>2</Index><IPAddress>192.0.2.41</IPAddress>
<MACAddress>aa:bb:cc:dd:ee:02</MACAddress><Active>0</Active><HostName>Laptop</HostName></Item><Item><Index>3</Index>
<MACAddress></MACAddress><HostName>ohne MAC</HostName></Item></List>"""


class FakeBox:
    """A FRITZ!Box as far as the plugin talks to it. Inspect ``logins``, ``commands`` and ``soap`` afterwards."""

    def __init__(self) -> None:
        """Start with the right password, a valid session and everything switched on."""
        self.password = PASSWORD
        self.sid = SID
        self.logins = 0
        self.commands: list[tuple[str, str | None, dict]] = []
        self.soap: list[str] = []
        self.list_kwargs: dict = {}
        self.active = {"AA:BB:CC:DD:EE:01": "1"}
        self.expire_once = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request like the box."""
        assert request.url.host == BOX, f"request to {request.url.host}"
        if request.url.path == "/login_sid.lua":
            if request.method == "GET":
                return httpx.Response(200, text=f"<SessionInfo><SID>0000000000000000</SID><Challenge>{CHALLENGE}</Challenge>"
                                                 "<BlockTime>0</BlockTime><Users><User last=\"1\">comfyos</User></Users></SessionInfo>")
            self.logins += 1
            form = parse_qs(request.content.decode())
            ok = form.get("response") == [expected_response(self.password)] and form.get("username") == ["comfyos"]
            sid = self.sid if ok else "0000000000000000"
            return httpx.Response(200, text=f"<SessionInfo><SID>{sid}</SID><Challenge>{CHALLENGE}</Challenge>"
                                            f"<BlockTime>{0 if ok else 10}</BlockTime></SessionInfo>")
        if request.url.path == "/webservices/homeautoswitch.lua":
            query = dict(request.url.params)
            if query.get("sid") != self.sid or self.expire_once:
                self.expire_once = False
                return httpx.Response(403, text="")
            if query["switchcmd"] == "getdevicelistinfos":
                return httpx.Response(200, text=device_list(**self.list_kwargs))
            self.commands.append((query.pop("switchcmd"), query.pop("ain", None), {k: v for k, v in query.items() if k != "sid"}))
            return httpx.Response(200, text="1")
        if request.url.port == tr064.PORT and request.url.path == tr064.CONTROL_PATH:
            action = request.headers["SoapAction"].split("#")[1]
            self.soap.append(action)
            if action == "X_AVM-DE_GetHostListPath":
                return httpx.Response(200, text=f"<s:Envelope xmlns:s=\"x\"><s:Body><u:R xmlns:u=\"y\"><NewX_AVM-DE_HostListPath>"
                                                f"/devicehostlist.lua?sid={SID}</NewX_AVM-DE_HostListPath></u:R></s:Body></s:Envelope>")
            mac = request.content.decode().split("<NewMACAddress>")[1].split("<")[0]
            return httpx.Response(200, text=f"<s:Envelope xmlns:s=\"x\"><s:Body><u:R xmlns:u=\"y\"><NewActive>"
                                            f"{self.active.get(mac, '0')}</NewActive></u:R></s:Body></s:Envelope>")
        if request.url.port == tr064.PORT and request.url.path == "/devicehostlist.lua":
            return httpx.Response(200, text=HOSTS)
        return httpx.Response(404)


class FakeClock:
    """A clock tests can move forward."""

    def __init__(self) -> None:
        """Start at a fixed point in time."""
        self.now = 1_000_000.0

    def __call__(self) -> float:
        """Return the current fake time in seconds."""
        return self.now


# ---------------------------------------------------------------------------
# 1) login, device list and commands as pure functions
# ---------------------------------------------------------------------------
assert aha.challenge_response(CHALLENGE, PASSWORD) == expected_response(PASSWORD)
old = aha.challenge_response("1234567z", "äbc")
assert old == "1234567z-" + hashlib.md5("1234567z-äbc".encode("utf-16-le")).hexdigest()
for broken in ("2$x$zz$1$00", "2$10$a1", ""):
    try:
        aha.challenge_response(broken, PASSWORD)
        raise AssertionError(f"accepted the challenge {broken!r}")
    except aha.AhaError:
        pass
session = aha.parse_session(f"<SessionInfo><SID>{SID}</SID><Challenge>x</Challenge><BlockTime>12</BlockTime></SessionInfo>")
assert (session.sid, session.block_seconds) == (SID, 12)
for hostile in ("<!DOCTYPE x [<!ENTITY a 'b'>]><devicelist/>", "kein xml", "<other/>"):
    try:
        aha.parse_devices(hostile)
        raise AssertionError("read a hostile or foreign document")
    except aha.AhaError:
        pass

devices = {fritzbox.device_id_of(d.ain): d for d in aha.parse_devices(device_list())}
assert set(devices) == {"fritzbox:116570272633", "fritzbox:099950335100", "fritzbox:127010034567-1", "fritzbox:099951234567",
                        "fritzbox:113240012345-1", "fritzbox:140800099999-1"}, devices.keys()      # HAN-FUN device and group left out
socket, radiator, lamp = devices["fritzbox:116570272633"], devices["fritzbox:099950335100"], devices["fritzbox:127010034567-1"]
remote, contact, blind = devices["fritzbox:099951234567"], devices["fritzbox:113240012345-1"], devices["fritzbox:140800099999-1"]
assert socket.kind == "switch" and socket.state == {"on": True, "temperature": 22.5} and (socket.power_w, socket.energy_kwh) == (84.0, 31.4)
assert radiator.kind == "thermostat" and radiator.state == {"temperature": 21.0, "target": 21.5} and radiator.battery == 70
assert radiator.window_open is True and radiator.low_battery is False
assert lamp.kind == "light" and lamp.state == {"on": True, "brightness": 50} and lamp.kelvin == 2700
assert remote.kind == "button" and [b.id for b in remote.buttons] == ["5000", "5001"] and remote.state == {"temperature": 19.5, "humidity": 48}
assert contact.kind == "contact" and contact.state == {"open": True}
assert blind.kind == "cover" and blind.state == {"position": 25} and blind.present is False

assert aha.commands(socket, {"on": False}) == [("setswitchoff", {})]
assert aha.commands(lamp, {"on": True, "brightness": 30}) == [("setsimpleonoff", {"onoff": "1"}), ("setlevelpercentage", {"level": "30"})]
assert aha.commands(lamp, {"kelvin": 9000}) == [("setcolortemperature", {"temperature": "6500", "duration": "0"})]
assert aha.commands(lamp, {"color": "#ff0000"}) == [("setunmappedcolor", {"hue": "0", "saturation": "255", "duration": "0"})]
assert aha.commands(radiator, {"target": 21.3}) == [("sethkrtsoll", {"param": "43"})]
assert aha.commands(radiator, {"target": 40}) == [("sethkrtsoll", {"param": "56"})]          # limited to 28 °C
assert aha.commands(blind, {"position": 0}) == [("setblind", {"target": "open"})]
assert aha.commands(blind, {"position": 60}) == [("setlevelpercentage", {"level": "60"})]
for device, wanted in ((socket, {"brightness": 50}), (radiator, {"on": True}), (remote, {"on": True})):
    try:
        aha.commands(device, wanted)
        raise AssertionError(f"{device.name} accepted {wanted}")
    except ValueError:
        pass
assert aha.new_presses({}, list(devices.values())) == []                                  # first look: no press
times = aha.press_times(list(devices.values()))
later = {fritzbox.device_id_of(d.ain): d for d in aha.parse_devices(device_list(pressed=1700000100))}
assert aha.new_presses(times, list(later.values())) == [("09995 1234567", "5000")]
assert [h.mac for h in tr064.parse_hosts(HOSTS)] == ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]   # sorted by name, no MAC left out
assert tr064.clean_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF" and tr064.clean_mac("x") is None
try:
    tr064.host_list_path("<a><NewX_AVM-DE_HostListPath>" + "http://" + "evil.example/x</NewX_AVM-DE_HostListPath></a>")
    raise AssertionError("accepted a foreign host list path")
except aha.AhaError:
    pass
print("1 pure functions: PBKDF2 and MD5 answers, device list with kinds and units, HAN-FUN and groups left out, commands "
      "with limits, presses only from a newer press time, host list, hostile XML refused")


# ---------------------------------------------------------------------------
# 2) the plugin against the rebuilt box
# ---------------------------------------------------------------------------
async def call_route(plugin: fritzbox.FritzBoxPlugin, method: str, path: str, **arguments: object) -> dict:
    """Call one of the plugin's endpoints directly (without HTTP)."""
    route = next(route for route in plugin.routes().routes if route.path == path and method in route.methods)
    return await route.endpoint(**arguments)


async def check_plugin() -> None:
    """Walk the plugin through its life: setup, login, devices, commands, presses, expiry, refusal, presence."""
    box = FakeBox()
    context = FakeContext("fritzbox", http_handler=box, secrets=["password"])
    plugin = fritzbox.setup(context)
    clock = FakeClock()
    plugin.clock = clock
    assert plugin.status()["configured"] is False and await plugin.devices() == []

    # the password never goes to an address outside the home network
    context.settings.set("host", "box.example.com")
    context.secrets.set("password", PASSWORD)
    try:
        await plugin.devices()
        raise AssertionError("signed in outside the home network")
    except PluginError as error:
        assert "Heimnetz" in str(error)
    assert context.requests == [] and box.logins == 0

    context.settings.set("host", f"http://{BOX}/")
    assert plugin.host == BOX
    found = {d.id: d for d in await plugin.devices()}
    assert box.logins == 1 and len(found) == 6 and found["fritzbox:099951234567"].buttons[0].name == "Taster Flur: Oben rechts"
    assert found["fritzbox:140800099999-1"].online is False and found["fritzbox:127010034567-1"].light.mirek == 370
    assert all(not request.url.params.get("sid") or request.url.params["sid"] == SID for request in context.requests)

    # a command goes to the right AIN; the answer keeps what was asked for
    result = await plugin.command("fritzbox:116570272633", {"on": False}, "command")
    assert box.commands[-1] == ("setswitchoff", "11657 0272633", {}) and result["on"] is False
    try:
        await plugin.command("fritzbox:140800099999-1", {"position": 0}, "command")      # not present
        raise AssertionError("switched a device the box does not reach")
    except DeviceUnreachable:
        pass

    # a press time that grew is a press; a changed switch is a change; nothing twice
    box.list_kwargs = {"socket_on": "1", "pressed": 1700000050}
    await plugin.poll()
    assert context.presses == [("fritzbox:099951234567", "5000", PRESS_SHORT)]
    assert ("fritzbox:116570272633", {"on": True}) in context.changes
    await plugin.poll()
    assert len(context.presses) == 1

    # an expired session: signed in once more, then the request goes through
    box.expire_once = True
    await plugin.poll()
    assert box.logins == 2

    # a refused login: no second attempt during the pause, the user learns why
    box.sid = "fedcba9876543210"
    box.password = "anders"
    plugin._sid = None
    for _ in range(2):
        try:
            await plugin.poll()
            raise AssertionError("signed in with a wrong password")
        except PluginError:
            pass
    assert box.logins == 3, box.logins                                                     # the second try waited
    clock.now += fritzbox.LOGIN_BACKOFF_SECONDS + 1
    box.password = PASSWORD
    await plugin.poll()
    assert box.logins == 4 and plugin.status()["connected"] is True

    # presence: pick devices in the tile, present at once, away only after the waiting time
    context.settings.set("presence", True)
    listed = await call_route(plugin, "GET", "/hosts")
    assert [h["name"] for h in listed["hosts"]] == ["Handy-Anna", "Laptop"]
    kept = await call_route(plugin, "PUT", "/presence", request=fritzbox.routes.PresenceRequest(
        hosts=[{"mac": "AA:BB:CC:DD:EE:01", "name": "Handy Anna"}]))
    assert kept["presence_hosts"] == [{"mac": "AA:BB:CC:DD:EE:01", "name": "Handy Anna"}]
    await plugin.check_presence()
    assert context.changes[-1] == ("fritzbox:host:aabbccddee01", {"present": True})
    box.active = {}
    clock.now += 60
    await plugin.check_presence()
    assert context.changes[-1] == ("fritzbox:host:aabbccddee01", {"present": True})            # not away yet
    clock.now += plugin.away_after_seconds
    await plugin.check_presence()
    assert context.changes[-1] == ("fritzbox:host:aabbccddee01", {"present": False})
    assert any(d.id == "fritzbox:host:aabbccddee01" and d.kind == "presence" for d in await plugin.devices())
    status = await call_route(plugin, "GET", "/status")
    assert status["presence"] is True and status["interval_seconds"] == 10
    await plugin.stop()
    print("2 plugin: password only into the home network, one login, commands, presses once, expired session, pause "
          "after a refused login, presence with waiting time, endpoints of the tile")


asyncio.run(check_plugin())
print("ALL FRITZBOX PLUGIN TESTS OK")
