"""One Sonos speaker: send actions to it and read what it plays.

``Speaker`` is the only place of the plugin that talks to the network. It gets its HTTP client
from the plugin (``context.http`` – home network only) and turns everything that can go wrong
into the errors of the plugin API:

    DeviceUnreachable   the speaker does not answer at all (switched off, new address)
    SpeakerSilent       it answers, but not over UPnP (the "UPnP" switch in the Sonos app is off)
    SpeakerRefused      it refused the action (a ``PluginError`` with a text for the user and the UPnP error code)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from plugin_api import DeviceUnreachable, PluginError

from . import soap
from .soap import Favorite, SoapError, SoapFault, SpeakerInfo, Track, ZoneGroup

MAX_IMAGE_BYTES = 2_000_000
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
FAVORITES_LIMIT = 100
START_TIMEOUT_SECONDS = 15.0                    # a speaker answers "Play" for a radio station only once the stream runs
INVALID_ARGUMENTS = 402                         # UPnP error code: the speaker does not accept an argument (here: the address)

# UPnP error codes a speaker answers with when an action does not fit the current source.
REFUSED_TEXTS = {
    402: "Der Lautsprecher kann das nicht abspielen.",
    701: "Das geht bei dieser Quelle gerade nicht.",
    711: "Hier gibt es keinen weiteren Titel.",
    712: "Das geht bei dieser Quelle gerade nicht.",
    714: "Dieser Favorit lässt sich hier nicht abspielen.",
    800: "Der Lautsprecher kann das gerade nicht.",
}


class SpeakerRefused(PluginError):
    """The speaker refused an action. ``code`` is the UPnP error code it named (0 when it named none)."""

    def __init__(self, code: int) -> None:
        """Keep the code and choose the text for the user."""
        super().__init__(REFUSED_TEXTS.get(code, f"Der Lautsprecher lehnt das ab (UPnP-Fehler {code})."))
        self.code = code


class SpeakerSilent(DeviceUnreachable):
    """The speaker is there but does not answer over UPnP – usually because UPnP is switched off in the Sonos app."""


@dataclass
class PlayerState:
    """What one room is doing right now."""

    playing: bool = False
    volume: int | None = None                   # 0–100
    muted: bool | None = None
    track: Track = field(default_factory=Track)
    station: str | None = None                  # name of the radio station, when a station is playing


class Speaker:
    """Talks to one speaker at a fixed address."""

    def __init__(self, client: httpx.AsyncClient, host: str, port: int = soap.SPEAKER_PORT) -> None:
        """Use ``client`` (from ``context.http``) for the speaker at ``host``."""
        self._client = client
        self.host = host
        self.port = port

    @property
    def base_url(self) -> str:
        """Address of the speaker's UPnP interface."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    # -- transport ----------------------------------------------------------

    async def call(self, service: str, action: str, *, timeout: float | None = None, **arguments: Any) -> dict[str, str]:
        """Run one action and return the fields of the answer. ``timeout`` (seconds) replaces the client's own."""
        request = soap.build_request(service, action, arguments)
        try:
            response = await self._client.post(self.base_url + request.path, content=request.body, headers=request.headers,
                                               timeout=httpx.USE_CLIENT_DEFAULT if timeout is None else timeout)
        except (httpx.HTTPError, OSError) as error:
            raise DeviceUnreachable(f"Der Lautsprecher antwortet nicht ({type(error).__name__}).")
        if response.status_code not in (200, 500):
            raise SpeakerSilent("Der Lautsprecher antwortet nicht über UPnP.")
        try:
            return soap.parse_response(response.text, action)
        except SoapFault as fault:
            raise SpeakerRefused(fault.code)
        except SoapError:
            raise SpeakerSilent("Der Lautsprecher antwortet nicht über UPnP.")

    async def description(self) -> SpeakerInfo:
        """Read the device description (room, model, software)."""
        try:
            response = await self._client.get(self.base_url + soap.DESCRIPTION_PATH)
        except (httpx.HTTPError, OSError) as error:
            raise DeviceUnreachable(f"Unter dieser Adresse antwortet nichts ({type(error).__name__}).")
        try:
            if response.status_code != 200:
                raise SoapError("no description")
            return soap.parse_description(response.text)
        except SoapError:
            raise SpeakerSilent("Unter dieser Adresse antwortet kein Sonos-Lautsprecher über UPnP.")

    # -- reading ------------------------------------------------------------

    async def zone_groups(self) -> list[ZoneGroup]:
        """Return the whole household as this speaker sees it."""
        fields = await self.call("ZoneGroupTopology", "GetZoneGroupState")
        try:
            return soap.parse_zone_groups(fields.get("ZoneGroupState") or "")
        except SoapError:
            raise SpeakerSilent("Der Lautsprecher nennt seinen Haushalt nicht.")

    async def favorites(self) -> list[Favorite]:
        """Return the Sonos favourites of the household that can be started locally."""
        fields = await self.call("ContentDirectory", "Browse", ObjectID=soap.FAVORITES_OBJECT,
                                 BrowseFlag="BrowseDirectChildren", Filter="*", StartingIndex=0,
                                 RequestedCount=FAVORITES_LIMIT, SortCriteria="")
        try:
            return soap.parse_favorites(fields.get("Result") or "<DIDL-Lite/>")
        except SoapError:
            return []

    async def playback(self) -> PlayerState:
        """Read what the speaker plays (ask the coordinator of a group): state, track and, for radio, the station."""
        transport = await self.call("AVTransport", "GetTransportInfo", InstanceID=0)
        track = soap.parse_position(await self.call("AVTransport", "GetPositionInfo", InstanceID=0))
        state = PlayerState(playing=transport.get("CurrentTransportState") in soap.PLAYING_STATES, track=track)
        if soap.is_radio(track.uri):
            media = await self.call("AVTransport", "GetMediaInfo", InstanceID=0)
            state.station = soap.parse_metadata(media.get("CurrentURIMetaData")).title
        return state

    async def volume(self) -> tuple[int | None, bool | None]:
        """Read volume (0–100) and mute of this speaker."""
        volume = await self.call("RenderingControl", "GetVolume", InstanceID=0, Channel="Master")
        mute = await self.call("RenderingControl", "GetMute", InstanceID=0, Channel="Master")
        level = volume.get("CurrentVolume", "")
        return (max(0, min(100, int(level))) if level.isdigit() else None,
                mute.get("CurrentMute") == "1" if mute.get("CurrentMute") in ("0", "1") else None)

    async def image(self, path: str) -> tuple[bytes, str] | None:
        """Fetch a cover picture the speaker serves itself. Returns None for anything that is not a small picture."""
        try:
            response = await self._client.get(self.base_url + path)
        except (httpx.HTTPError, OSError):
            return None
        media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if response.status_code != 200 or media_type not in IMAGE_TYPES or len(response.content) > MAX_IMAGE_BYTES:
            return None
        return response.content, media_type

    # -- playback (send these to the coordinator of a group) ------------------

    async def play(self, timeout: float | None = None) -> None:
        """Start or continue playback."""
        await self.call("AVTransport", "Play", timeout=timeout, InstanceID=0, Speed=1)

    async def pause(self) -> None:
        """Pause playback."""
        await self.call("AVTransport", "Pause", InstanceID=0)

    async def next_track(self) -> None:
        """Skip to the next track."""
        await self.call("AVTransport", "Next", InstanceID=0)

    async def previous_track(self) -> None:
        """Go back to the previous track."""
        await self.call("AVTransport", "Previous", InstanceID=0)

    async def play_favorite(self, favorite: Favorite, own_id: str) -> None:
        """Start a favourite. ``own_id`` is the id of this speaker (its queue is addressed by it).

        Radio stations are set as the source directly. Everything else (albums, playlists,
        single tracks) replaces the queue and plays from its start.
        """
        if soap.is_radio(favorite.uri):
            try:
                await self.call("AVTransport", "SetAVTransportURI", InstanceID=0, CurrentURI=favorite.uri,
                                CurrentURIMetaData=favorite.metadata)
            except SpeakerRefused as refused:
                # A station saved from the newer TuneIn service: the speaker takes it under the old service number.
                legacy_uri = soap.legacy_radio_uri(favorite.uri)
                if refused.code != INVALID_ARGUMENTS or legacy_uri is None:
                    raise
                await self.call("AVTransport", "SetAVTransportURI", InstanceID=0, CurrentURI=legacy_uri,
                                CurrentURIMetaData=favorite.metadata)
        else:
            await self.call("AVTransport", "RemoveAllTracksFromQueue", InstanceID=0)
            await self.call("AVTransport", "AddURIToQueue", InstanceID=0, EnqueuedURI=favorite.uri,
                            EnqueuedURIMetaData=favorite.metadata, DesiredFirstTrackNumberEnqueued=0, EnqueueAsNext=0)
            await self.call("AVTransport", "SetAVTransportURI", InstanceID=0,
                            CurrentURI=f"{soap.QUEUE_SCHEME}{own_id}#0", CurrentURIMetaData="")
        await self.play(timeout=START_TIMEOUT_SECONDS)

    async def play_stream(self, url: str, title: str) -> None:
        """Play an endless audio stream (WAV) the speaker fetches itself from ``url`` – an address in the home network."""
        await self.call("AVTransport", "SetAVTransportURI", InstanceID=0, CurrentURI=url,
                        CurrentURIMetaData=soap.stream_metadata(url, title))
        await self.play(timeout=START_TIMEOUT_SECONDS)

    async def stop(self) -> None:
        """Stop playback and let go of the source (unlike pause, which a stream without an end does not know)."""
        await self.call("AVTransport", "Stop", InstanceID=0)

    # -- this speaker only ----------------------------------------------------

    async def set_volume(self, level: int) -> None:
        """Set the volume of this speaker (0–100)."""
        await self.call("RenderingControl", "SetVolume", InstanceID=0, Channel="Master", DesiredVolume=level)

    async def set_mute(self, muted: bool) -> None:
        """Mute or unmute this speaker."""
        await self.call("RenderingControl", "SetMute", InstanceID=0, Channel="Master", DesiredMute=1 if muted else 0)

    async def join(self, coordinator_id: str) -> None:
        """Let this speaker play what the group of ``coordinator_id`` plays."""
        await self.call("AVTransport", "SetAVTransportURI", InstanceID=0,
                        CurrentURI=f"{soap.FOLLOWER_SCHEME}{coordinator_id}", CurrentURIMetaData="")

    async def leave(self) -> None:
        """Take this speaker out of its group; it then stands alone."""
        await self.call("AVTransport", "BecomeCoordinatorOfStandaloneGroup", InstanceID=0)
