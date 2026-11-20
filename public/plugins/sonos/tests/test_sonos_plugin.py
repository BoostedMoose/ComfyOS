"""Tests for the Sonos plugin. They run without ComfyOS and without speakers.

    python public/plugins/sonos/tests/test_sonos_plugin.py [path-to-comfyos]

A household in memory answers the requests the way real speakers do (the shapes were taken
from a real S2 system; ids, addresses and names are made up):

    Wohnzimmer   sound bar with two surround boxes (one listed in the channel map, one as satellite)
    Küche        stereo pair – the second box is an invisible member
    Bad          a single speaker that plays along with Küche (Küche leads the group)

Covered: the wire format (requests, answers, faults, refused documents), rooms and bonded boxes,
the search, devices with link keys and capabilities, track and station, cover pictures only from
the speakers themselves, commands to the right speaker, favourites, groups, switched-off UPnP,
unreachable speakers, an address typed by hand and the endpoints of the tile.
"""
import asyncio
import re
import sys
from html import escape
from pathlib import Path

import httpx

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "public" / "sdk"))
sys.path.insert(0, str(ROOT / "public" / "plugins"))

from plugin_api import DeviceUnreachable, PluginError, SsdpAnswer  # noqa: E402
from testing import FakeContext  # noqa: E402

import sonos  # noqa: E402
from sonos import household, soap  # noqa: E402
from sonos.routes import GroupRequest, UngroupRequest  # noqa: E402

LIVING, LIVING_REAR_LEFT, LIVING_REAR_RIGHT = "RINCON_AAAAAAAAAAAA01400", "RINCON_AAAAAAAAAAAB01400", "RINCON_AAAAAAAAAAAC01400"
KITCHEN, KITCHEN_RIGHT, BATH = "RINCON_CCCCCCCCCCCC01400", "RINCON_CCCCCCCCCCCD01400", "RINCON_EEEEEEEEEEEE01400"
HOSTS = {LIVING: "192.0.2.31", KITCHEN: "192.0.2.32", KITCHEN_RIGHT: "192.0.2.33", BATH: "192.0.2.34"}
MODELS = {LIVING: "Sonos Beam", KITCHEN: "Sonos One", KITCHEN_RIGHT: "Sonos One", BATH: "Sonos Roam"}
ROOMS = {LIVING: "Wohnzimmer", KITCHEN: "Küche", KITCHEN_RIGHT: "Küche", BATH: "Bad"}
FOREIGN = "https://" + "cdn.example.com"         # a server outside the home network (never contacted)
ENVELOPE = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>{body}</s:Body></s:Envelope>')
DIDL_HEAD = ('<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
             'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">')
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def zone_group_state(bath_follows_kitchen: bool) -> str:
    """The household as ``GetZoneGroupState`` reports it."""
    def member(uuid: str, extra: str = "", children: str = "") -> str:
        return (f'<ZoneGroupMember UUID="{uuid}" Location="http://{HOSTS[uuid]}:1400/xml/device_description.xml" '
                f'ZoneName="{ROOMS[uuid]}" SoftwareVersion="98.0-82170" SWGen="2" {extra}>{children}</ZoneGroupMember>')

    living = member(LIVING, f'HTSatChanMapSet="{LIVING}:LF,RF;{LIVING_REAR_LEFT}:LR"',
                    f'<Satellite UUID="{LIVING_REAR_RIGHT}" Location="http://192.0.2.36:1400/xml/device_description.xml" '
                    f'ZoneName="Wohnzimmer" Invisible="1"/>')
    pair = f'ChannelMapSet="{KITCHEN}:LF,LF;{KITCHEN_RIGHT}:RF,RF"'
    kitchen = member(KITCHEN, pair) + member(KITCHEN_RIGHT, pair + ' Invisible="1"')
    bath = member(BATH)
    groups = [f'<ZoneGroup Coordinator="{LIVING}" ID="{LIVING}:1">{living}</ZoneGroup>']
    if bath_follows_kitchen:
        groups.append(f'<ZoneGroup Coordinator="{KITCHEN}" ID="{KITCHEN}:2">{kitchen}{bath}</ZoneGroup>')
    else:
        groups += [f'<ZoneGroup Coordinator="{KITCHEN}" ID="{KITCHEN}:2">{kitchen}</ZoneGroup>',
                   f'<ZoneGroup Coordinator="{BATH}" ID="{BATH}:3">{bath}</ZoneGroup>']
    return f'<ZoneGroupState><ZoneGroups>{"".join(groups)}</ZoneGroups><VanishedDevices/></ZoneGroupState>'


def track_didl(title: str, artist: str, album: str, art: str, stream: str = "") -> str:
    """A DIDL-Lite description of a track, as the speaker puts it into ``TrackMetaData``."""
    return (f'{DIDL_HEAD}<item id="-1" parentID="-1"><res duration="0:03:30">x-sonos-http:track.mp4</res>'
            f'<upnp:albumArtURI>{escape(art)}</upnp:albumArtURI><dc:title>{escape(title)}</dc:title>'
            f'<dc:creator>{escape(artist)}</dc:creator><upnp:album>{escape(album)}</upnp:album>'
            f'<r:streamContent>{escape(stream)}</r:streamContent></item></DIDL-Lite>')


FAVORITES_DIDL = (
    f'{DIDL_HEAD}'
    '<item id="FV:2/1" parentID="FV:2"><dc:title>Deutschlandfunk</dc:title><res protocolInfo="x-sonosapi-stream:*:*:*">'
    'x-sonosapi-stream:s42828?sid=254&amp;flags=8224&amp;sn=0</res><r:type>instantPlay</r:type>'
    '<r:resMD>&lt;DIDL-Lite&gt;&lt;item id="s42828"&gt;&lt;dc:title&gt;Deutschlandfunk&lt;/dc:title&gt;&lt;/item&gt;&lt;/DIDL-Lite&gt;</r:resMD></item>'
    '<item id="FV:2/4" parentID="FV:2"><dc:title>Radio Neu</dc:title><res protocolInfo="x-sonosapi-stream:*:*:*">'
    'x-sonosapi-stream:s850?sid=333&amp;flags=8224&amp;sn=12</res><r:type>instantPlay</r:type>'
    '<r:resMD>&lt;DIDL-Lite&gt;&lt;item id="s850"&gt;&lt;dc:title&gt;Radio Neu&lt;/dc:title&gt;&lt;/item&gt;&lt;/DIDL-Lite&gt;</r:resMD></item>'
    '<item id="FV:2/2" parentID="FV:2"><dc:title>Aktuell angesagt</dc:title><res></res><r:type>shortcut</r:type><r:resMD>x</r:resMD></item>'
    '<item id="FV:2/3" parentID="FV:2"><dc:title>Kochen &amp; Essen</dc:title><res protocolInfo="x-rincon-cpcontainer:*:*:*">'
    'x-rincon-cpcontainer:1006206cplaylist%3a1</res><r:type>instantPlay</r:type><r:resMD>&lt;DIDL-Lite/&gt;</r:resMD></item>'
    '</DIDL-Lite>')


class FakeHousehold:
    """Speakers in memory. ``mode`` says how they behave: "ok", "upnp_off" (no UPnP answers) or "down" (no answer at all)."""

    def __init__(self) -> None:
        """Start with Bad playing along with Küche, the kitchen playing a track and the living room playing radio."""
        self.mode = "ok"
        self.bath_follows_kitchen = True
        self.calls: list[tuple[str, str, dict[str, str]]] = []      # (host, action, arguments)
        self.playing = {LIVING: True, KITCHEN: True, BATH: False}
        self.volume = {LIVING: 26, KITCHEN: 12, KITCHEN_RIGHT: 12, BATH: 40}
        self.muted = {LIVING: False, KITCHEN: False, KITCHEN_RIGHT: False, BATH: True}
        self.source = {LIVING: "radio", KITCHEN: "track", BATH: "tv"}
        self.art = "/getaa?s=1&u=x-sonos-http%3atrack.mp4"

    def uuid_of(self, host: str) -> str:
        """Return the id of the speaker at an address."""
        return next(uuid for uuid, address in HOSTS.items() if address == host)

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Answer one HTTP request the way a speaker does."""
        host = request.url.host
        if self.mode == "down" or host not in HOSTS.values():
            raise httpx.ConnectError("no route to host", request=request)
        if self.mode == "upnp_off":
            return httpx.Response(403, text="Forbidden")
        uuid = self.uuid_of(host)
        if request.method == "GET":
            if request.url.path == "/xml/device_description.xml":
                return httpx.Response(200, text=(
                    '<?xml version="1.0" encoding="utf-8" ?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
                    f'<modelName>{MODELS[uuid]}</modelName><softwareVersion>98.0-82170</softwareVersion><swGen>2</swGen>'
                    f'<UDN>uuid:{uuid}</UDN><roomName>{ROOMS[uuid]}</roomName></device></root>'))
            if request.url.path == "/getaa":
                return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})
            if request.url.path == "/notapicture":
                return httpx.Response(200, text="<html>", headers={"Content-Type": "text/html"})
            return httpx.Response(404)
        action = request.headers["SOAPACTION"].strip('"').split("#")[1]
        arguments = dict(re.findall(r"<(\w+)>([^<]*)</\1>", request.content.decode("utf-8")))
        self.calls.append((host, action, arguments))
        return self.run(uuid, action, arguments)

    def run(self, uuid: str, action: str, arguments: dict[str, str]) -> httpx.Response:
        """Carry out one action on a speaker and build its answer."""
        def ok(fields: str = "") -> httpx.Response:
            return httpx.Response(200, text=ENVELOPE.format(body=f'<u:{action}Response xmlns:u="urn:x">{fields}</u:{action}Response>'))

        def fault(code: int) -> httpx.Response:
            return httpx.Response(500, text=ENVELOPE.format(
                body='<s:Fault><faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>'
                     f'<UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>{code}</errorCode></UPnPError></detail></s:Fault>'))

        source = self.source.get(uuid, "track")
        if action == "GetZoneGroupState":
            return ok(f"<ZoneGroupState>{escape(zone_group_state(self.bath_follows_kitchen))}</ZoneGroupState>")
        if action == "Browse":
            return ok(f"<Result>{escape(FAVORITES_DIDL)}</Result><NumberReturned>3</NumberReturned>")
        if action == "GetTransportInfo":
            state = "PLAYING" if self.playing.get(uuid) else "PAUSED_PLAYBACK"
            return ok(f"<CurrentTransportState>{state}</CurrentTransportState><CurrentTransportStatus>OK</CurrentTransportStatus>")
        if action == "GetPositionInfo":
            if source == "radio":
                didl = track_didl("stream.aac", "", "", FOREIGN + "/logo.png", "Nachrichten – Das Wichtigste")
                return ok(f"<TrackDuration>0:00:00</TrackDuration><TrackMetaData>{escape(didl)}</TrackMetaData>"
                          "<TrackURI>x-sonosapi-stream:s42828?sid=254</TrackURI><RelTime>0:12:00</RelTime>")
            if source == "tv":
                return ok("<TrackDuration>0:00:00</TrackDuration><TrackMetaData>NOT_IMPLEMENTED</TrackMetaData>"
                          f"<TrackURI>x-sonos-htastream:{uuid}:spdif</TrackURI><RelTime>0:00:00</RelTime>")
            if source.startswith("http://"):        # a stream the speaker fetches from an address in the home network
                return ok("<TrackDuration>0:00:00</TrackDuration><TrackMetaData>NOT_IMPLEMENTED</TrackMetaData>"
                          f"<TrackURI>{escape(source)}</TrackURI><RelTime>0:00:07</RelTime>")
            didl = track_didl("Lied <eins>", "Die Band", "Das Album", self.art)
            return ok(f"<TrackDuration>0:03:30</TrackDuration><TrackMetaData>{escape(didl)}</TrackMetaData>"
                      "<TrackURI>x-sonos-http:track.mp4</TrackURI><RelTime>0:01:05</RelTime>")
        if action == "GetMediaInfo":
            station = f'{DIDL_HEAD}<item id="-1" parentID="-1"><dc:title>Deutschlandfunk</dc:title></item></DIDL-Lite>'
            return ok(f"<CurrentURIMetaData>{escape(station)}</CurrentURIMetaData>")
        if action == "GetVolume":
            return ok(f"<CurrentVolume>{self.volume[uuid]}</CurrentVolume>")
        if action == "GetMute":
            return ok(f"<CurrentMute>{1 if self.muted[uuid] else 0}</CurrentMute>")
        if action in ("Pause", "Next", "Previous") and source == "tv":
            return fault(701)
        if action == "SetAVTransportURI" and "sid=333" in arguments["CurrentURI"]:
            return fault(402)       # a real speaker refuses stations of the newer radio service like this
        if action == "Play":
            self.playing[uuid] = True
        elif action in ("Pause", "Stop"):
            self.playing[uuid] = False
        elif action == "SetVolume":
            self.volume[uuid] = int(arguments["DesiredVolume"])
        elif action == "SetMute":
            self.muted[uuid] = arguments["DesiredMute"] == "1"
        elif action == "SetAVTransportURI" and arguments["CurrentURI"].startswith("x-rincon:"):
            self.bath_follows_kitchen = uuid == BATH
        elif action == "SetAVTransportURI" and arguments["CurrentURI"].startswith("http://"):
            self.source[uuid] = arguments["CurrentURI"]
        elif action == "SetAVTransportURI":
            self.source[uuid] = "radio" if arguments["CurrentURI"].startswith("x-sonosapi-stream:") else "track"
        elif action == "BecomeCoordinatorOfStandaloneGroup":
            self.bath_follows_kitchen = False
        return ok()

    def actions(self, host: str) -> list[str]:
        """Return the writing actions a speaker received since the last ``calls.clear()``."""
        return [action for address, action, _ in self.calls if address == host and not action.startswith(("Get", "Browse"))]


# ---------------------------------------------------------------------------
# 1) the wire format
# ---------------------------------------------------------------------------
request = soap.build_request("RenderingControl", "SetVolume", {"InstanceID": 0, "Channel": "Master", "DesiredVolume": 20})
assert request.path == "/MediaRenderer/RenderingControl/Control"
assert request.headers["SOAPACTION"] == '"urn:schemas-upnp-org:service:RenderingControl:1#SetVolume"'
assert b"<DesiredVolume>20</DesiredVolume>" in request.body and request.body.startswith(b"<?xml")
escaped = soap.build_request("AVTransport", "SetAVTransportURI", {"CurrentURI": "a&b<c>", "CurrentURIMetaData": "<DIDL-Lite/>"})
assert b"a&amp;b&lt;c&gt;" in escaped.body and b"&lt;DIDL-Lite/&gt;" in escaped.body, "values must be escaped"

answer = ENVELOPE.format(body='<u:GetVolumeResponse xmlns:u="urn:x"><CurrentVolume>26</CurrentVolume></u:GetVolumeResponse>')
assert soap.parse_response(answer, "GetVolume") == {"CurrentVolume": "26"}
fault = ENVELOPE.format(body='<s:Fault><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>701</errorCode>'
                             '</UPnPError></detail></s:Fault>')
try:
    soap.parse_response(fault, "Pause")
    raise AssertionError("a fault was read as an answer")
except soap.SoapFault as error:
    assert error.code == 701
for unreadable in ("not xml at all", '<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>', "<x>" + "a" * (soap.MAX_XML_BYTES + 1) + "</x>",
                   ENVELOPE.format(body="<u:OtherResponse/>")):
    try:
        soap.parse_response(unreadable, "GetVolume")
        raise AssertionError("an unreadable document was accepted")
    except soap.SoapFault:
        raise AssertionError("an unreadable document is not a fault")
    except soap.SoapError:
        pass

assert soap.seconds("0:03:54") == 234 and soap.seconds("1:00:01") == 3601
assert soap.seconds("NOT_IMPLEMENTED") is None and soap.seconds("") is None and soap.seconds(None) is None
assert soap.speaker_ids(f"{KITCHEN}:LF,LF;{KITCHEN_RIGHT}:RF,RF;nonsense;") == [KITCHEN, KITCHEN_RIGHT] and soap.speaker_ids(None) == []
assert soap.source_name("x-sonos-htastream:RINCON_X:spdif") == "TV" and soap.source_name("x-rincon-stream:RINCON_X") == "Line-In"
assert soap.source_name("x-sonosapi-stream:s1") == "Radio" and soap.source_name("x-sonos-http:track.mp4") is None
assert soap.is_radio("x-rincon-mp3radio://example") and not soap.is_radio("x-rincon-cpcontainer:1")

favorites = soap.parse_favorites(FAVORITES_DIDL)
assert [favorite.title for favorite in favorites] == ["Deutschlandfunk", "Radio Neu", "Kochen & Essen"], "a shortcut without address is left out"
assert soap.legacy_radio_uri("x-sonosapi-stream:s850?sid=333&flags=8224&sn=12") == "x-sonosapi-stream:s850?sid=254&flags=8224&sn=0"
assert soap.legacy_radio_uri("x-sonosapi-stream:s850?sid=254&flags=8224&sn=0") is None, "already the old service"
assert soap.legacy_radio_uri("x-sonosapi-stream:s850") is None and soap.legacy_radio_uri("x-sonosapi-radio:a?sid=303&sn=7") is None
assert soap.speaker_channels(f"{LIVING}:LF,RF;{LIVING_REAR_LEFT}:LR;junk:SW;") == {LIVING: "LF,RF", LIVING_REAR_LEFT: "LR"}
assert favorites[0].uri.startswith("x-sonosapi-stream:s42828?sid=254&flags") and favorites[0].metadata.startswith("<DIDL-Lite>")
assert soap.parse_metadata("NOT_IMPLEMENTED") == soap.Track() and soap.parse_metadata("<broken") == soap.Track()
try:
    soap.parse_description("<root><device><UDN>uuid:something-else</UDN></device></root>")
    raise AssertionError("a foreign UPnP device was taken for a Sonos speaker")
except soap.SoapError:
    pass
print("1 wire format: requests with escaped values, answers, faults, refused documents (DOCTYPE, size), times, sources, favourites")

# ---------------------------------------------------------------------------
# 2) rooms, bonded boxes and groups
# ---------------------------------------------------------------------------
rooms = household.build_rooms(soap.parse_zone_groups(zone_group_state(True)), {LIVING: "Sonos Beam"})
assert [(room.name, room.uuid) for room in rooms] == [("Bad", BATH), ("Küche", KITCHEN), ("Wohnzimmer", LIVING)], rooms
by_name = {room.name: room for room in rooms}
assert by_name["Wohnzimmer"].bonded == (LIVING_REAR_LEFT, LIVING_REAR_RIGHT) and by_name["Wohnzimmer"].model == "Sonos Beam"
assert by_name["Küche"].bonded == (KITCHEN_RIGHT,) and by_name["Küche"].leads and by_name["Küche"].group == (BATH,)
assert by_name["Bad"].coordinator == KITCHEN and not by_name["Bad"].leads and by_name["Bad"].group == (KITCHEN,)
# every further box of a room has a name of its own: by what it plays, numbered when the speaker does not tell
assert [(box.uuid, box.name) for box in by_name["Wohnzimmer"].boxes] == [(LIVING_REAR_LEFT, "Wohnzimmer (hinten links)"),
                                                                         (LIVING_REAR_RIGHT, "Wohnzimmer (Box 3)")]
assert [(box.uuid, box.name) for box in by_name["Küche"].boxes] == [(KITCHEN_RIGHT, "Küche (rechts)")] and by_name["Bad"].boxes == ()
assert by_name["Wohnzimmer"].leads and by_name["Wohnzimmer"].group == ()
stored = household.to_setting(rooms)
restored = household.from_setting(stored + ["junk", {"uuid": "nope", "host": "192.0.2.9", "port": 1400}, {"uuid": BATH, "host": "", "port": 1400},
                                           {"uuid": BATH, "host": "192.0.2.9", "port": "1400"}])
assert [(room.uuid, room.host, room.bonded) for room in restored] == [(room.uuid, room.host, room.bonded) for room in rooms]
assert [room.boxes for room in restored] == [room.boxes for room in rooms], "the names of the boxes survive a restart"
older = household.from_setting([{key: value for key, value in entry.items() if key != "channels"} for entry in stored])
assert [box.name for room in older for box in room.boxes] == ["Küche (Box 2)", "Wohnzimmer (Box 2)", "Wohnzimmer (Box 3)"], \
    "rooms stored by plugin version 1.0 name no channels: the boxes are numbered"
assert all(room.leads and room.group == () for room in restored), "who plays with whom is not stored"
assert household.from_setting(None) == [] and household.from_setting({"a": 1}) == []
print("2 rooms: one per visible speaker, stereo pair and surrounds bonded and named, coordinator and group, stored form")


# ---------------------------------------------------------------------------
# 3) the plugin against the household in memory
# ---------------------------------------------------------------------------
async def call_route(plugin: sonos.SonosPlugin, method: str, path: str, **arguments: object) -> dict:
    """Call one of the plugin's endpoints directly (without HTTP)."""
    route = next(route for route in plugin.routes().routes if route.path == path and method in route.methods)
    return await route.endpoint(**arguments)


async def expect_error(action: object, kind: type, word: str) -> None:
    """Await ``action`` and check that it raises ``kind`` with ``word`` in its text."""
    try:
        await action
    except kind as error:
        assert word in str(error), f"{word!r} not in {error}"
        return
    raise AssertionError(f"expected {kind.__name__} ({word})")


async def plugin_against_household() -> None:
    """Walk the plugin through its life: search, devices, commands, groups, pictures, outages, restart."""
    speakers = FakeHousehold()
    context = FakeContext("sonos", http_handler=speakers.answer, udp=True)
    plugin = sonos.setup(context)
    await plugin.start()
    assert plugin.status()["configured"] is False and await plugin.devices() == [] and plugin.link_prefixes() == []

    # another plugin knows speakers: the tile offers to look for them
    context.hint_list = [{"kind": "sonos", "uuid": LIVING, "host": None}, {"kind": "sonos", "uuid": KITCHEN, "host": "192.0.2.32"},
                         {"kind": "hue-bridge", "host": "192.0.2.10"}]
    assert plugin.suggestions() == {"count": 2, "hosts": ["192.0.2.32"]}

    # the search finds nothing: a clear text, nothing stored
    await expect_error(plugin.search(), PluginError, "UPnP")
    assert context.searches == [("ssdp", soap.ZONE_PLAYER, sonos.SEARCH_SECONDS)] and plugin.rooms == {}

    # an address by hand must be in the home network and belong to a Sonos speaker
    await expect_error(plugin.connect("203.0.113.5"), PluginError, "Heimnetz")
    await expect_error(plugin.connect("192.0.2.99"), DeviceUnreachable, "antwortet nichts")
    assert not any(request.url.host == "203.0.113.5" for request in context.requests), "nothing may be sent outside the home network"

    # the search finds one speaker – it tells about the whole household
    context.ssdp_answers = [SsdpAnswer(host="192.0.2.34", location="http://192.0.2.34:1400/xml/device_description.xml",
                                       search_target=soap.ZONE_PLAYER)]
    assert await plugin.search() == 3
    assert context.reloads == 1 and "3 Räume" in context.events[-1]
    assert [room["uuid"] for room in context.settings.get("rooms")] == [BATH, KITCHEN, LIVING]
    assert plugin.status() == {"configured": True, "connected": True, "live": True, "message": None, "name": "Sonos"}
    assert plugin.link_prefixes() == ["sonos:"]

    devices = {device.name: device for device in await plugin.devices()}
    assert sorted(devices) == ["Bad", "Küche", "Küche (rechts)", "Wohnzimmer", "Wohnzimmer (Box 3)", "Wohnzimmer (hinten links)"]
    living, kitchen, bath = devices["Wohnzimmer"], devices["Küche"], devices["Bad"]
    assert living.id == "sonosroom:" + LIVING and living.kind == "media" and living.discovered and living.online
    assert living.link_keys == ["sonos:" + LIVING] and kitchen.link_keys == ["sonos:" + KITCHEN] and bath.link_keys == ["sonos:" + BATH]
    # a further box is a device of its own that shows what its room plays; nothing can be sent to it
    rear, right = devices["Wohnzimmer (hinten links)"], devices["Küche (rechts)"]
    assert rear.id == "sonosroom:" + LIVING_REAR_LEFT and rear.link_keys == ["sonos:" + LIVING_REAR_LEFT] and rear.discovered
    assert rear.kind == "media" and rear.online and rear.capabilities == [] and rear.room_hint == "Wohnzimmer"
    assert rear.state == {} and rear.media.title == "Deutschlandfunk" and rear.media.favorites == [], "no state: no capability"
    assert right.media.title == "Lied <eins>" and right.details["Gehört zu"] == "Küche", "each box shows the track of its own room"
    assert living.model == "Sonos Beam" and living.room_hint == "Wohnzimmer" and living.details["Boxen in diesem Raum"] == "3"
    capabilities = {capability.key: capability for capability in living.capabilities}
    assert sorted(capabilities) == ["favorite", "live_audio", "muted", "next", "playing", "previous", "volume"]
    assert capabilities["live_audio"].type == "bool" and capabilities["live_audio"].writable and not capabilities["live_audio"].readable
    assert capabilities["next"].type == "action" and capabilities["next"].writable and not capabilities["next"].readable
    assert capabilities["favorite"].options == ("Deutschlandfunk", "Radio Neu", "Kochen & Essen") and capabilities["volume"].maximum == 100

    # radio: the station is the title, what it plays stands where the artist would; its logo lies elsewhere and is not loaded
    assert living.state == {"playing": True, "volume": 26, "muted": False}
    assert (living.media.title, living.media.artist, living.media.source) == ("Deutschlandfunk", "Nachrichten – Das Wichtigste", "Radio")
    assert living.media.image_id is None and living.media.duration is None and living.media.position == 720
    assert living.media.favorites == ["Deutschlandfunk", "Radio Neu", "Kochen & Essen"] and living.media.group == []
    assert living.media.coordinator is None
    # a track: title, artist, album, length and a cover the speaker serves itself
    assert (kitchen.media.title, kitchen.media.artist, kitchen.media.album) == ("Lied <eins>", "Die Band", "Das Album")
    assert (kitchen.media.position, kitchen.media.duration, kitchen.media.source) == (65, 210, None)
    assert kitchen.media.group == ["Bad"] and kitchen.media.coordinator is None
    # a room that follows shows what its leader plays, with its own volume
    assert bath.state == {"playing": True, "volume": 40, "muted": True}
    assert bath.media.title == "Lied <eins>" and bath.media.group == ["Küche"] and bath.media.coordinator == "Küche"
    assert bath.media.image_id == kitchen.media.image_id and re.match(r"^[0-9a-f]{24}$", kitchen.media.image_id)

    # cover pictures: only what a speaker of the household serves
    assert await plugin.media_image(kitchen.media.image_id) == (PNG, "image/png")
    assert context.requests[-1].url.host == "192.0.2.32" and context.requests[-1].url.path == "/getaa"
    assert await plugin.media_image("0" * 24) is None
    kitchen_room = plugin.rooms[KITCHEN]
    assert plugin._image_id(kitchen_room, FOREIGN + "/cover.jpg") is None
    assert plugin._image_id(kitchen_room, "http://203.0.113.5:1400/getaa?x=1") is None
    assert plugin._image_id(kitchen_room, "//cdn.example.com/cover.jpg") is None and plugin._image_id(kitchen_room, "getaa") is None
    own = plugin._image_id(kitchen_room, "http://192.0.2.34:1400/getaa?s=1")
    assert own and await plugin.media_image(own) == (PNG, "image/png") and context.requests[-1].url.host == "192.0.2.34"
    assert await plugin.media_image(plugin._image_id(kitchen_room, "/notapicture")) is None, "only pictures are passed on"

    # commands: playback goes to the leader of the group, volume and mute to the room itself
    speakers.calls.clear()
    result = await plugin.command(bath.id, {"playing": False}, "command")
    assert speakers.actions("192.0.2.32") == ["Pause"] and speakers.actions("192.0.2.34") == []
    assert result["playing"] is False and result["volume"] == 40 and result["media"].title == "Lied <eins>"
    speakers.calls.clear()
    result = await plugin.command(bath.id, {"volume": 15.4, "muted": False}, "command")
    assert speakers.actions("192.0.2.34") == ["SetVolume", "SetMute"] and speakers.actions("192.0.2.32") == []
    assert (result["volume"], result["muted"]) == (15, False) and speakers.volume[KITCHEN] == 12
    speakers.calls.clear()
    await plugin.command(kitchen.id, {"playing": True, "next": True}, "flow")
    await plugin.command(kitchen.id, {"previous": True}, "flow")
    assert speakers.actions("192.0.2.32") == ["Play", "Next", "Previous"]

    # favourites: a station is set as the source, anything else goes through the queue
    speakers.calls.clear()
    result = await plugin.command(kitchen.id, {"favorite": "  deutschlandfunk "}, "flow")
    assert speakers.actions("192.0.2.32") == ["SetAVTransportURI", "Play"]
    sent = next(arguments for _, action, arguments in speakers.calls if action == "SetAVTransportURI")
    assert sent["CurrentURI"] == "x-sonosapi-stream:s42828?sid=254&amp;flags=8224&amp;sn=0" and "DIDL-Lite" in sent["CurrentURIMetaData"]
    assert result["media"].title == "Deutschlandfunk" and result["media"].source == "Radio"
    # a station of the newer radio service: the speaker refuses its address (402) and gets it under the old service number
    speakers.calls.clear()
    await plugin.command(kitchen.id, {"favorite": "Radio Neu"}, "flow")
    assert speakers.actions("192.0.2.32") == ["SetAVTransportURI", "SetAVTransportURI", "Play"]
    tried = [arguments["CurrentURI"] for _, action, arguments in speakers.calls if action == "SetAVTransportURI"]
    assert tried == ["x-sonosapi-stream:s850?sid=333&amp;flags=8224&amp;sn=12", "x-sonosapi-stream:s850?sid=254&amp;flags=8224&amp;sn=0"]
    speakers.calls.clear()
    await plugin.command(bath.id, {"favorite": "Kochen & Essen"}, "flow")
    assert speakers.actions("192.0.2.32") == ["RemoveAllTracksFromQueue", "AddURIToQueue", "SetAVTransportURI", "Play"]
    queued = [arguments for _, action, arguments in speakers.calls if action in ("AddURIToQueue", "SetAVTransportURI")]
    assert queued[0]["EnqueuedURI"] == "x-rincon-cpcontainer:1006206cplaylist%3a1" and queued[1]["CurrentURI"] == f"x-rincon-queue:{KITCHEN}#0"

    # the sound of a browser: the leader of the group is told the address of the stream at ComfyOS
    await expect_error(plugin.command(bath.id, {"live_audio": True}, "command"), PluginError, "kein Ton")     # no transmission runs
    context.live_audio_address = "http://192.0.2.1:8000/stream/live.wav"
    speakers.calls.clear()
    result = await plugin.command(bath.id, {"live_audio": True}, "command")
    assert speakers.actions("192.0.2.32") == ["SetAVTransportURI", "Play"] and speakers.actions("192.0.2.34") == []
    sent = next(arguments for _, action, arguments in speakers.calls if action == "SetAVTransportURI")
    assert sent["CurrentURI"] == context.live_audio_address and "http-get:*:audio/wav:*" in sent["CurrentURIMetaData"]
    assert (result["media"].title, result["media"].artist, result["media"].source) == ("Ton vom Browser", None, "Rechner")
    assert result["playing"] is True
    speakers.calls.clear()
    result = await plugin.command(bath.id, {"live_audio": False}, "command")
    assert speakers.actions("192.0.2.32") == ["Stop"] and result["playing"] is False
    speakers.source[KITCHEN] = "track"
    await plugin.refresh(0, force=True)
    speakers.calls.clear()
    await plugin.command(bath.id, {"live_audio": False}, "command")
    assert speakers.actions("192.0.2.32") == [], "a group that plays something else is not stopped"
    await expect_error(plugin.command(bath.id, {"live_audio": "ja"}, "command"), PluginError, "an oder aus")
    await plugin.command(kitchen.id, {"playing": True}, "command")
    assert "audio/wav" in soap.stream_metadata("http://192.0.2.1/a?b=1&c=2", "Ton <1>") and "&amp;c=2" in soap.stream_metadata(
        "http://192.0.2.1/a?b=1&c=2", "Ton <1>") and "Ton &lt;1&gt;" in soap.stream_metadata("http://192.0.2.1/a", "Ton <1>")

    # what a speaker cannot do or what makes no sense is refused with a text for the user
    speakers.calls.clear()
    await expect_error(plugin.command(kitchen.id, {"favorite": "Gibt es nicht"}, "command"), PluginError, "Favoriten")
    await expect_error(plugin.command(kitchen.id, {"volume": 101}, "command"), PluginError, "Lautstärke")
    await expect_error(plugin.command(kitchen.id, {"volume": True}, "command"), PluginError, "Lautstärke")
    await expect_error(plugin.command(kitchen.id, {"playing": "ja"}, "command"), PluginError, "an oder aus")
    await expect_error(plugin.command("sonosroom:RINCON_FFFFFFFFFFFF01400", {"playing": True}, "command"), PluginError, "nicht (mehr)")
    await expect_error(plugin.command("fremd:1", {"playing": True}, "command"), PluginError, "nicht (mehr)")
    await expect_error(plugin.command(rear.id, {"playing": True}, "command"), PluginError, "über ihn gesteuert")
    assert speakers.actions("192.0.2.32") == [], "a refused command must not reach a speaker"

    # groups: leave and join through the same command path
    context.reloads = 0
    speakers.calls.clear()
    await plugin.command(bath.id, {"leave": True}, "plugin:sonos")
    assert speakers.actions("192.0.2.34") == ["BecomeCoordinatorOfStandaloneGroup"]
    alone = {device.name: device for device in await plugin.devices()}["Bad"]
    assert alone.media.group == [] and alone.media.source == "TV" and plugin.rooms[BATH].leads
    await expect_error(plugin.command(bath.id, {"playing": False}, "command"), PluginError, "Quelle")     # TV: the speaker refuses to pause
    await expect_error(plugin.command(bath.id, {"join": bath.id}, "command"), PluginError, "nicht (mehr)")
    await expect_error(plugin.command(bath.id, {"join": "sonosroom:RINCON_FFFFFFFFFFFF01400"}, "command"), PluginError, "nicht (mehr)")
    speakers.calls.clear()
    await plugin.command(bath.id, {"join": kitchen.id}, "plugin:sonos")
    joined = next(arguments for _, action, arguments in speakers.calls if action == "SetAVTransportURI")
    assert joined["CurrentURI"] == f"x-rincon:{KITCHEN}" and plugin.rooms[BATH].coordinator == KITCHEN

    # changes are pushed once: here the volume set in the Sonos app
    context.changes.clear()
    speakers.volume[LIVING] = 31
    await plugin.refresh(0)
    assert [(device_id, changes["volume"]) for device_id, changes in context.changes] == [("sonosroom:" + LIVING, 31)]
    assert context.changes[0][1]["media"].title == "Deutschlandfunk" and context.changes[0][1]["online"] is True
    await plugin.refresh(0)
    assert len(context.changes) == 1, "the same state was reported twice"
    # playback stops: the room and its boxes report it, each once
    context.changes.clear()
    speakers.playing[LIVING] = False
    await plugin.refresh(0)
    reported = dict(context.changes)
    assert sorted(reported) == sorted("sonosroom:" + uuid for uuid in (LIVING, LIVING_REAR_LEFT, LIVING_REAR_RIGHT))
    assert len(context.changes) == 3 and reported["sonosroom:" + LIVING]["playing"] is False
    assert reported["sonosroom:" + LIVING_REAR_LEFT] == {"online": True, "media": None}, "a box carries a track only while its room plays"
    paused = {device.id: device for device in await plugin.devices()}["sonosroom:" + LIVING_REAR_LEFT]
    assert paused.online and paused.media is None and paused.state == {}
    speakers.playing[LIVING] = True
    await plugin.refresh(0)

    # the endpoints of the tile
    status = await call_route(plugin, "GET", "/status")
    assert status["configured"] and status["connected"] and not status["silent"] and status["favorites"] == 3
    assert [(room["name"], room["boxes"], room["group"], room["leader"]) for room in status["rooms"]] == \
        [("Bad", 1, ["Küche"], "Küche"), ("Küche", 2, ["Bad"], None), ("Wohnzimmer", 3, [], None)]
    await call_route(plugin, "POST", "/group", request=GroupRequest(device_id=bath.id, target_id=living.id))
    assert context.switched[-1] == (bath.id, {"join": living.id}), "grouping goes through the core (device lock)"
    await call_route(plugin, "POST", "/ungroup", request=UngroupRequest(device_id=bath.id))
    assert context.switched[-1] == (bath.id, {"leave": True})
    for bad_id in ("sonosroom:../x", "hb:lamp", "sonosroom:RINCON_zz"):
        try:
            UngroupRequest(device_id=bad_id)
            raise AssertionError("an invalid device id was accepted")
        except ValueError:
            pass

    # UPnP switched off: the rooms stay, offline, and the tile says what to do
    context.events.clear()
    context.changes.clear()
    speakers.mode = "upnp_off"
    await plugin.refresh(0, force=True)
    silent = await plugin.devices()
    assert len(silent) == 6 and all(device.online is False and "UPnP" in device.status_detail for device in silent)
    assert all(device.state == {} and device.media is None and device.link_keys for device in silent)
    assert plugin.status()["connected"] is False and "UPnP" in plugin.status()["message"] and plugin.silent
    assert plugin.link_prefixes() == [], "silent speakers prove nothing about the entries of another hub"
    assert sorted(device_id for device_id, changes in context.changes if changes == {"online": False}) == sorted(device.id for device in silent)
    assert len(context.events) == 1 and "UPnP" in context.events[0]
    before = len(context.requests)
    await plugin.devices()
    await plugin.devices()
    assert len(context.requests) == before and len(context.events) == 1, "silent speakers are not asked with every device list"
    await expect_error(plugin.command(silent[0].id, {"playing": True}, "command"), DeviceUnreachable, "UPnP")

    # not reachable at all: another text
    speakers.mode = "down"
    await plugin.refresh(0, force=True)
    assert not plugin.silent and "Neu suchen" in plugin.status()["message"]
    await expect_error(plugin.search(), DeviceUnreachable, "antwortet nicht")
    assert len(plugin.rooms) == 3, "a failed search forgets nothing"

    # they answer again
    speakers.mode = "ok"
    await plugin.refresh(0, force=True)
    assert plugin.status()["connected"] and context.events[-1] == "Sonos-Lautsprecher wieder erreichbar"
    assert all(device.online for device in await plugin.devices())

    # a restart keeps the rooms; forgetting removes them
    await plugin.stop()
    restarted = sonos.setup(context)
    await restarted.start()
    assert sorted(device.name for device in await restarted.devices()) == [
        "Bad", "Küche", "Küche (rechts)", "Wohnzimmer", "Wohnzimmer (Box 3)", "Wohnzimmer (hinten links)"]
    assert await call_route(restarted, "POST", "/forget") == {"ok": True}
    assert await restarted.devices() == [] and context.settings.get("rooms") is None and restarted.status()["configured"] is False
    await restarted.stop()

    # without "udp" in the manifest there is no search
    plain = sonos.setup(FakeContext("sonos", http_handler=speakers.answer))
    await plain.start()
    await expect_error(plain.search(), PluginError, "UDP")
    assert await plain.connect(" 192.0.2.31 ") == 3, "one address by hand is enough"
    await plain.stop()


asyncio.run(plugin_against_household())
print("3 plugin: search and address by hand, one device per room and per further box, track and station, covers only "
      "from the speakers, commands to leader or room, favourites, groups, pushed changes, endpoints, UPnP off, unreachable, restart")

print("ALL SONOS PLUGIN TESTS OK")
