"""Test world "Home Base + Sonos": a mock Home Base that lists Sonos speakers, and a Sonos household in memory
(UPnP on port 1400 with the answer to the SSDP search). Shared by tests/plugins/test_sonos.py and the UI tests.

    Wohnzimmer   sound bar with two surround speakers – the Home Base lists all three as things of their own
    Küche        single speaker the Home Base does not know
    Keller       only still exists at the Home Base (it no longer exists at Sonos)

On import the whole world is created: isolated environment, app, mocks. Whoever uses it imports the names they
need – only the contents (lists, dictionaries) are changed, never the names themselves. All ids and addresses
are made up.
"""
import re
from html import escape

from . import boot  # noqa: E402

ROOT, tmp = boot.isolate("sonos")

import httpx  # noqa: E402

from app import auth, core, device_store, egress, main, plugins  # noqa: E402
from support.homebase import QiviconConnector, build_devices, install_homebase  # noqa: E402

LIVING, REAR_LEFT, REAR_RIGHT = "RINCON_AAAAAAAAAAAA01400", "RINCON_AAAAAAAAAAAB01400", "RINCON_AAAAAAAAAAAC01400"
KITCHEN, CELLAR = "RINCON_CCCCCCCCCCCC01400", "RINCON_DDDDDDDDDDDD01400"
HOSTS = {LIVING: "192.0.2.31", KITCHEN: "192.0.2.32"}
NAMES = {LIVING: "Wohnzimmer", KITCHEN: "Küche"}
SONOS = "/api/plugins/sonos"
HB_LIVING, HB_REAR_LEFT, HB_REAR_RIGHT = f"sonos:BEAM:{LIVING}", f"sonos:One:{REAR_LEFT}", f"sonos:One:{REAR_RIGHT}"
HB_CELLAR = f"sonos:PLAY1:{CELLAR}"
ROOM_ID = lambda uuid: "sonosroom:" + uuid  # noqa: E731   device id of a room at the Sonos plugin
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


# ------------------------------------------------------------------ Mock Home Base
def speaker_thing(uid, label, location):
    """Sonos thing as the Home Base reports it."""
    return {"UID": uid, "thingTypeUID": ":".join(uid.split(":")[:2]), "label": label, "location": location,
            "statusInfo": {"status": "ONLINE"}, "channels": []}


THINGS = [
    speaker_thing(HB_LIVING, "Fernseher-Box", "wohnen"), speaker_thing(HB_REAR_LEFT, "Surround links", "wohnen"),
    speaker_thing(HB_REAR_RIGHT, "Surround rechts", "wohnen"), speaker_thing(HB_CELLAR, "Kellerbox", "flur"),
    {"UID": "zwave:therm:1", "thingTypeUID": "zwave:thermostat", "label": "Heizung", "location": "wohnen",
     "statusInfo": {"status": "ONLINE"},
     "channels": [{"id": "temperature", "itemType": "Number", "linkedItems": ["T_temp"]},
                  {"id": "setpointtemperature", "itemType": "Number", "linkedItems": ["T_set"]}]},
]
ITEMS = [{"name": "T_temp", "state": "20.5"}, {"name": "T_set", "state": "21"},
         {"name": "wohnen", "label": "Wohnzimmer", "type": "Group"}, {"name": "flur", "label": "Flur", "type": "Group"}]


class MockHB(QiviconConnector):
    """Home Base of the Sonos world: devices from THINGS/ITEMS and the speakers it knows."""
    async def list_devices(self, max_age=None):
        """Build devices from THINGS and ITEMS and report the Sonos speakers the Home Base knows."""
        from comfyos_plugin_qivicon import model
        self.sonos_speakers = model.sonos_speakers(THINGS)
        return build_devices(THINGS, ITEMS, [])

    async def list_rooms(self):
        """The two rooms of the Sonos world."""
        return [{"name": "wohnen", "label": "Wohnzimmer"}, {"name": "flur", "label": "Flur"}]

    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}


# ------------------------------------------------------------------ Sonos-Haushalt im Speicher
ENVELOPE = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>{body}</s:Body></s:Envelope>')
DIDL = ('<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">')
# State of the household: mode = "ok" | "upnp_off" (does not answer over UPnP) | "down" (does not answer at all)
sonos = {"mode": "ok", "calls": [], "playing": {LIVING: True, KITCHEN: False}, "volume": {LIVING: 26, KITCHEN: 12},
         "muted": {LIVING: False, KITCHEN: False}, "searches": 0, "title": "Lied <b>eins</b>", "source": {}}


def _topology():
    """Der Haushalt, wie GetZoneGroupState ihn meldet."""
    def member(uuid, extra=""):
        return (f'<ZoneGroupMember UUID="{uuid}" Location="http://{HOSTS[uuid]}:1400/xml/device_description.xml" '
                f'ZoneName="{NAMES[uuid]}" {extra}></ZoneGroupMember>')
    sats = f'HTSatChanMapSet="{LIVING}:LF,RF;{REAR_LEFT}:LR;{REAR_RIGHT}:RR"'
    return (f'<ZoneGroupState><ZoneGroups><ZoneGroup Coordinator="{LIVING}" ID="{LIVING}:1">{member(LIVING, sats)}</ZoneGroup>'
            f'<ZoneGroup Coordinator="{KITCHEN}" ID="{KITCHEN}:2">{member(KITCHEN)}</ZoneGroup></ZoneGroups></ZoneGroupState>')


def _favorites():
    """Ein Radiosender als Sonos-Favorit."""
    return (f'{DIDL}<item id="FV:2/1" parentID="FV:2"><dc:title>Radio Eins</dc:title>'
            '<res>x-sonosapi-stream:s1?sid=254</res><r:resMD>&lt;DIDL-Lite/&gt;</r:resMD></item></DIDL-Lite>')


def handler(req: httpx.Request) -> httpx.Response:
    """Responses of the speakers: description, cover picture and the UPnP actions the plugin uses."""
    host = req.url.host
    if sonos["mode"] == "down" or host not in HOSTS.values():
        raise httpx.ConnectError("down", request=req)
    if sonos["mode"] == "upnp_off":
        return httpx.Response(403, text="Forbidden")
    uuid = next(u for u, h in HOSTS.items() if h == host)
    if req.method == "GET":
        if req.url.path == "/xml/device_description.xml":
            return httpx.Response(200, text=f'<root xmlns="urn:schemas-upnp-org:device-1-0"><device><modelName>Sonos Test</modelName>'
                                            f'<UDN>uuid:{uuid}</UDN><roomName>{NAMES[uuid]}</roomName></device></root>')
        if req.url.path == "/getaa":
            return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})
        return httpx.Response(404)
    action = req.headers["SOAPACTION"].strip('"').split("#")[1]
    args = dict(re.findall(r"<(\w+)>([^<]*)</\1>", req.content.decode("utf-8")))
    sonos["calls"].append((uuid, action, args))

    def ok(fields=""):
        return httpx.Response(200, text=ENVELOPE.format(body=f'<u:{action}Response xmlns:u="urn:x">{fields}</u:{action}Response>'))

    if action == "GetZoneGroupState":
        return ok(f"<ZoneGroupState>{escape(_topology())}</ZoneGroupState>")
    if action == "Browse":
        return ok(f"<Result>{escape(_favorites())}</Result>")
    if action == "GetTransportInfo":
        return ok(f"<CurrentTransportState>{'PLAYING' if sonos['playing'][uuid] else 'PAUSED_PLAYBACK'}</CurrentTransportState>")
    if action == "GetPositionInfo":
        didl = (f'{DIDL}<item id="-1"><upnp:albumArtURI>/getaa?s=1&amp;u=x</upnp:albumArtURI><dc:title>{escape(sonos["title"])}</dc:title>'
                '<dc:creator>Die Band</dc:creator></item></DIDL-Lite>')
        uri = sonos["source"].get(uuid, "x-sonos-http:track.mp4")      # what was set last, otherwise a track
        return ok(f"<TrackDuration>0:03:30</TrackDuration><TrackMetaData>{escape(didl)}</TrackMetaData>"
                  f"<TrackURI>{escape(uri)}</TrackURI><RelTime>0:01:05</RelTime>")
    if action == "GetVolume":
        return ok(f"<CurrentVolume>{sonos['volume'][uuid]}</CurrentVolume>")
    if action == "GetMute":
        return ok(f"<CurrentMute>{1 if sonos['muted'][uuid] else 0}</CurrentMute>")
    if action in ("Play", "Pause", "Stop"):
        sonos["playing"][uuid] = action == "Play"
    elif action == "SetAVTransportURI" and args["CurrentURI"].startswith("http://"):
        sonos["source"][uuid] = args["CurrentURI"]      # a stream the speaker fetches itself (the sound of a browser)
    elif action == "SetVolume":
        sonos["volume"][uuid] = int(args["DesiredVolume"])
    elif action == "SetMute":
        sonos["muted"][uuid] = args["DesiredMute"] == "1"
    return ok()


def writes(uuid):
    """Writing actions a speaker has received."""
    return [action for u, action, _ in sonos["calls"] if u == uuid and not action.startswith(("Get", "Browse"))]


class _Wire:
    """Peer of a UDP socket: answers the SSDP search like the home network of the Sonos world."""

    def __init__(self, deliver):
        """`deliver` delivers a datagram to the socket."""
        self.deliver = deliver
        self.sent = []
        self.closed = False

    def sendto(self, data, addr):
        """Record a search request and deliver the answers: a speaker, a foreign UPnP device, a sender
        from outside the home network and one whose description is elsewhere."""
        self.sent.append((data, addr))
        if not data.startswith(b"M-SEARCH") or sonos["mode"] != "ok":
            return
        sonos["searches"] += 1

        def answer(location, target="urn:schemas-upnp-org:device:ZonePlayer:1"):
            return (f"HTTP/1.1 200 OK\r\nLOCATION: {location}\r\nST: {target}\r\nUSN: uuid:x-{location}\r\n"
                    "SERVER: Linux UPnP/1.0 Sonos/98.0 (ZPS14)\r\n\r\n").encode()
        self.deliver(answer("http://192.0.2.32:1400/xml/device_description.xml"), ("192.0.2.32", 1900))
        self.deliver(answer("http://192.0.2.20:80/description.xml", "upnp:rootdevice"), ("192.0.2.20", 1900))
        self.deliver(answer("http://8.8.8.8:1400/xml/device_description.xml"), ("8.8.8.8", 1900))
        self.deliver(answer("http://192.0.2.99:1400/xml/device_description.xml"), ("192.0.2.77", 1900))

    def close(self):
        """Close the socket."""
        self.closed = True


wires: list[_Wire] = []


def datagram_factory(port, multicast, deliver):
    """Instead of a real UDP socket: a peer in memory."""
    wires.append(_Wire(deliver))
    return wires[-1]


egress._lan_transport = httpx.MockTransport(handler)     # all LAN clients of the plugins
egress._datagram_factory = datagram_factory              # UDP der Plugins


def sonos_plugin():
    """Die laufende Instanz des Sonos-Plugins."""
    return plugins.active("sonos")

