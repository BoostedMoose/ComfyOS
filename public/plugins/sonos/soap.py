"""Translate between the UPnP interface of a Sonos speaker and plain Python values (pure functions).

Every speaker offers UPnP services on port 1400: a request is an HTTP POST with a small XML
document (SOAP), the answer is XML again. This module builds those requests and reads the
answers; it never touches the network. Everything Sonos-specific about the wire format lives
here, so a change on Sonos' side is repaired in one file.

Answers are foreign input: documents are limited in size, documents with a DOCTYPE are refused
(no entities, no external references) and every value is read as text.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

SPEAKER_PORT = 1400
MAX_XML_BYTES = 2_000_000                       # favourites of a large household stay far below this
MAX_TITLE_LENGTH = 120                          # favourites are addressed by their name; longer names are cut
ZONE_PLAYER = "urn:schemas-upnp-org:device:ZonePlayer:1"      # SSDP search target of every Sonos speaker
DESCRIPTION_PATH = "/xml/device_description.xml"

# service name -> (control path on the speaker, service type)
SERVICES: dict[str, tuple[str, str]] = {
    "AVTransport": ("/MediaRenderer/AVTransport/Control", "urn:schemas-upnp-org:service:AVTransport:1"),
    "RenderingControl": ("/MediaRenderer/RenderingControl/Control", "urn:schemas-upnp-org:service:RenderingControl:1"),
    "ZoneGroupTopology": ("/ZoneGroupTopology/Control", "urn:schemas-upnp-org:service:ZoneGroupTopology:1"),
    "ContentDirectory": ("/MediaServer/ContentDirectory/Control", "urn:schemas-upnp-org:service:ContentDirectory:1"),
}

FAVORITES_OBJECT = "FV:2"                       # ContentDirectory container that holds the Sonos favourites
SPEAKER_ID = re.compile(r"^RINCON_[0-9A-F]{6,40}$")

# What a transport address says about the source of the sound.
RADIO_SCHEMES = ("x-sonosapi-stream:", "x-sonosapi-radio:", "x-sonosapi-hls:", "x-rincon-mp3radio:", "hls-radio:", "aac:")
TV_SCHEME = "x-sonos-htastream:"
LINE_IN_SCHEMES = ("x-rincon-stream:", "x-sonos-vli:")
SERVICE_STREAM_SCHEME = "x-sonosapi-stream:"    # a station of a radio service (TuneIn); "?sid=<service>" names the service
LEGACY_RADIO_SERVICE = "254"                    # the service number every speaker accepts for such a station (see legacy_radio_uri)
FOLLOWER_SCHEME = "x-rincon:"                   # this speaker plays what another one leads
QUEUE_SCHEME = "x-rincon-queue:"

PLAYING_STATES = ("PLAYING", "TRANSITIONING")   # GetTransportInfo: the speaker plays or is about to


class SoapError(Exception):
    """The speaker answered with something this module cannot read."""


class SoapFault(SoapError):
    """The speaker refused the action and named a UPnP error code (for example 701)."""

    def __init__(self, code: int) -> None:
        """Keep the UPnP error code."""
        super().__init__(f"UPnP error {code}")
        self.code = code


@dataclass(frozen=True)
class SoapRequest:
    """One action, ready to be posted to a speaker."""

    path: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class ZoneMember:
    """One speaker of the household as the topology lists it."""

    uuid: str                                   # "RINCON_…", the id of the speaker in its own system
    name: str                                   # name of the room
    host: str                                   # IP address
    port: int
    visible: bool                               # False for the second box of a stereo pair, surrounds and subs
    bonded: tuple[str, ...]                     # ids of the other boxes that form one room with this one
    channels: tuple[str, ...] = ()              # what each of those boxes plays, in the same order: "LR", "SW", "RF,RF"; "" = unknown


@dataclass(frozen=True)
class ZoneGroup:
    """Speakers that play the same thing; the coordinator takes the playback commands."""

    coordinator: str
    members: tuple[ZoneMember, ...]


@dataclass(frozen=True)
class Track:
    """What the speaker says about the current track."""

    uri: str = ""
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    art_uri: str | None = None                  # cover picture as the speaker names it (often a path on the speaker)
    stream_text: str | None = None              # what a radio station is playing right now
    position: int | None = None                 # seconds
    duration: int | None = None                 # seconds; None for radio and TV


@dataclass(frozen=True)
class Favorite:
    """A Sonos favourite that can be started on a speaker."""

    title: str
    uri: str
    metadata: str                               # the description the speaker needs to start it (DIDL-Lite)


@dataclass(frozen=True)
class SpeakerInfo:
    """Facts from the device description of a speaker."""

    uuid: str
    room: str
    model: str
    software: str
    generation: str                             # "1" = S1, "2" = S2


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

def build_request(service: str, action: str, arguments: Mapping[str, object]) -> SoapRequest:
    """Build the POST for one action. Raises ``KeyError`` for a service this module does not know."""
    path, service_type = SERVICES[service]
    fields = "".join(f"<{name}>{escape(str(value))}</{name}>" for name, value in arguments.items())
    body = ('<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service_type}">{fields}</u:{action}></s:Body></s:Envelope>')
    headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{service_type}#{action}"'}
    return SoapRequest(path=path, headers=headers, body=body.encode("utf-8"))


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def parse_xml(text: str) -> ElementTree.Element:
    """Parse a document from a speaker. Raises ``SoapError`` when it is too large, declares a DOCTYPE or is not XML."""
    if len(text) > MAX_XML_BYTES:
        raise SoapError("document too large")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise SoapError("document declares a DOCTYPE")
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise SoapError("not XML") from error


def local_name(tag: str) -> str:
    """'{namespace}title' -> 'title'."""
    return tag.rsplit("}", 1)[-1]


def _find(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    """Return the first descendant with this local name, whatever its namespace."""
    return next((child for child in element.iter() if local_name(child.tag) == name), None)


def _text(element: ElementTree.Element, name: str) -> str | None:
    """Return the stripped text of the first descendant with this local name, or None when it is missing or empty."""
    found = _find(element, name)
    text = (found.text or "").strip() if found is not None else ""
    return text or None


def parse_response(text: str, action: str) -> dict[str, str]:
    """Read the answer to an action as ``{field: text}``.

    Raises ``SoapFault`` when the speaker refused the action and ``SoapError`` for anything unreadable.
    """
    root = parse_xml(text)
    fault = _find(root, "Fault")
    if fault is not None:
        code = _text(fault, "errorCode") or "0"
        raise SoapFault(int(code) if code.isdigit() else 0)
    response = _find(root, action + "Response")
    if response is None:
        raise SoapError("no response element")
    return {local_name(child.tag): child.text or "" for child in response}


def seconds(clock: str | None) -> int | None:
    """'0:03:54' -> 234. 'NOT_IMPLEMENTED', an empty text or a zero length -> None."""
    parts = (clock or "").split(".")[0].split(":")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    hours, minutes, rest = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + rest


def speaker_ids(channel_map: str | None) -> list[str]:
    """'RINCON_A:LF,LF;RINCON_B:RF,RF' -> ['RINCON_A', 'RINCON_B'] (the boxes of a stereo pair or home cinema)."""
    ids = []
    for entry in (channel_map or "").split(";"):
        speaker_id = entry.split(":", 1)[0].strip().upper()
        if SPEAKER_ID.match(speaker_id) and speaker_id not in ids:
            ids.append(speaker_id)
    return ids


def speaker_channels(channel_map: str | None) -> dict[str, str]:
    """'RINCON_A:LF,RF;RINCON_B:LR;RINCON_C:SW' -> {'RINCON_A': 'LF,RF', 'RINCON_B': 'LR', 'RINCON_C': 'SW'}."""
    channels: dict[str, str] = {}
    for entry in (channel_map or "").split(";"):
        speaker_id, _, played = entry.partition(":")
        speaker_id = speaker_id.strip().upper()
        if SPEAKER_ID.match(speaker_id):
            channels.setdefault(speaker_id, played.strip().upper()[:20])
    return channels


def _member(element: ElementTree.Element) -> ZoneMember | None:
    """Read one ``ZoneGroupMember``. Returns None when it has no usable id or address."""
    uuid = (element.get("UUID") or "").upper()
    location = urlsplit(element.get("Location") or "")
    if not SPEAKER_ID.match(uuid) or not location.hostname:
        return None
    bonded = speaker_ids(element.get("ChannelMapSet")) + speaker_ids(element.get("HTSatChanMapSet"))
    bonded += [(satellite.get("UUID") or "").upper() for satellite in element if local_name(satellite.tag) == "Satellite"]
    others = tuple(dict.fromkeys(other for other in bonded if SPEAKER_ID.match(other) and other != uuid))
    played = {**speaker_channels(element.get("HTSatChanMapSet")), **speaker_channels(element.get("ChannelMapSet"))}
    return ZoneMember(uuid=uuid, name=(element.get("ZoneName") or "").strip() or uuid, host=location.hostname,
                      port=location.port or SPEAKER_PORT, visible=element.get("Invisible") != "1", bonded=others,
                      channels=tuple(played.get(other, "") for other in others))


def parse_zone_groups(state_xml: str) -> list[ZoneGroup]:
    """Read the ``ZoneGroupState`` of ``GetZoneGroupState``: every group with its members."""
    groups = []
    for group in parse_xml(state_xml).iter("ZoneGroup"):
        members = tuple(member for member in (_member(child) for child in group if local_name(child.tag) == "ZoneGroupMember")
                        if member is not None)
        if members:
            groups.append(ZoneGroup(coordinator=(group.get("Coordinator") or "").upper(), members=members))
    return groups


def parse_metadata(didl: str | None) -> Track:
    """Read a DIDL-Lite description (track or station). An empty or unreadable description gives an empty track."""
    if not didl or not didl.strip() or didl.strip() == "NOT_IMPLEMENTED":
        return Track()
    try:
        root = parse_xml(didl)
    except SoapError:
        return Track()
    return Track(title=_text(root, "title"), artist=_text(root, "creator") or _text(root, "albumArtist"),
                 album=_text(root, "album"), art_uri=_text(root, "albumArtURI"), stream_text=_text(root, "streamContent"))


def parse_position(fields: Mapping[str, str]) -> Track:
    """Read the answer of ``GetPositionInfo``: the current track with position and length."""
    track = parse_metadata(fields.get("TrackMetaData"))
    return Track(uri=fields.get("TrackURI") or "", title=track.title, artist=track.artist, album=track.album,
                 art_uri=track.art_uri, stream_text=track.stream_text, position=seconds(fields.get("RelTime")),
                 duration=seconds(fields.get("TrackDuration")) or None)


def parse_favorites(result_didl: str) -> list[Favorite]:
    """Read the Sonos favourites from a ``Browse`` result.

    Favourites without an address (shortcuts into a music service's app) cannot be started
    over the local interface and are left out.
    """
    favorites = []
    for item in parse_xml(result_didl):
        title, uri = _text(item, "title"), _text(item, "res")
        if title and uri:
            favorites.append(Favorite(title=title[:MAX_TITLE_LENGTH], uri=uri, metadata=_text(item, "resMD") or ""))
    return favorites


def parse_description(text: str) -> SpeakerInfo:
    """Read ``/xml/device_description.xml``. Raises ``SoapError`` when it does not describe a Sonos speaker."""
    root = parse_xml(text)
    uuid = (_text(root, "UDN") or "").removeprefix("uuid:").upper()
    if not SPEAKER_ID.match(uuid):
        raise SoapError("not a Sonos speaker")
    return SpeakerInfo(uuid=uuid, room=_text(root, "roomName") or uuid, model=_text(root, "modelName") or "Sonos",
                       software=_text(root, "softwareVersion") or "", generation=_text(root, "swGen") or "")


# ---------------------------------------------------------------------------
# What an address means
# ---------------------------------------------------------------------------

def is_radio(uri: str) -> bool:
    """Return whether the address is a radio stream (it is started directly, not through the queue)."""
    return uri.startswith(RADIO_SCHEMES)


def stream_metadata(url: str, title: str) -> str:
    """Describe an audio stream at an HTTP address (WAV) for ``SetAVTransportURI`` – the speaker shows ``title``."""
    return ('<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
            'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"><item id="comfyos-live" parentID="-1" restricted="true">'
            f'<dc:title>{escape(title)}</dc:title><upnp:class>object.item.audioItem.musicTrack</upnp:class>'
            f'<res protocolInfo="http-get:*:audio/wav:*">{escape(url)}</res></item></DIDL-Lite>')


def legacy_radio_uri(uri: str) -> str | None:
    """Return the address of a radio service's station under the old service number, or None when there is none.

    Favourites saved from the newer TuneIn service carry its number ("?sid=333"). A speaker refuses that
    address over the local interface (UPnP error 402), but plays the same station under the number of the
    old service - the station id is the same in both. Seen on a Beam with software 98.0 (19.11.2026).
    """
    station, mark, query = uri.partition("?")
    if not uri.startswith(SERVICE_STREAM_SCHEME) or not mark:
        return None
    fields = [field for field in query.split("&") if field]
    if f"sid={LEGACY_RADIO_SERVICE}" in fields or not any(field.startswith("sid=") for field in fields):
        return None
    # "sn" is the number of the account at the service; the old service is used without one.
    changed = [f"sid={LEGACY_RADIO_SERVICE}" if field.startswith("sid=") else "sn=0" if field.startswith("sn=") else field
               for field in fields]
    return station + "?" + "&".join(changed)


def source_name(uri: str) -> str | None:
    """Name the source of the sound for the user, or None for music from the queue or a service."""
    if uri.startswith(TV_SCHEME):
        return "TV"
    if uri.startswith(LINE_IN_SCHEMES):
        return "Line-In"
    if is_radio(uri):
        return "Radio"
    return None
