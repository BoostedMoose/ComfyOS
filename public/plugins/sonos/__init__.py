"""Sonos plugin: speakers in the home network – local, without a Sonos account and without the cloud.

What it does
    * Finds the speakers with a search in the home network (SSDP, only when the user asks for
      it) or from one address typed in by hand. One speaker tells about the whole household.
    * Reports one device per room (kind "media"): playing or paused, volume, mute, the current
      track with its cover, the rooms that play along and the Sonos favourites.
    * Reports every further box of a room (second box of a stereo pair, surround speaker, sub) as
      a device of its own that only shows what its room plays right now – so each box can be
      placed on the floor plan and stands in its room. It is controlled through its room.
    * Starts and pauses playback, skips tracks, sets the volume, starts a favourite and
      puts rooms into a group or takes them out.
    * Plays the sound a browser sends to ComfyOS ("Ton dieses Rechners"): the speaker fetches it
      as a stream from ComfyOS (``context.live_audio_url``), a few seconds behind the computer.

How it talks to the speakers
    Over the UPnP interface every speaker offers on port 1400 (see soap.py). It is not an
    official interface, but the way local control of Sonos has worked for years. The plugin
    only asks – it does not subscribe to events, so the speakers never call ComfyOS back.
    While a page is open ComfyOS asks every few seconds, otherwise the plugin looks every
    15 seconds so that flows notice when music starts or stops.

How devices are linked
    A hub such as the Home Base may list the same speakers. Every room and every further box
    (device id ``sonosroom:<id>``) therefore carries the link key ``sonos:<id>``. ComfyOS then
    shows one device per speaker: name and room come from the hub, state and commands go
    straight to the speaker.

When the speakers stay silent
    If "UPnP" is switched off in the Sonos app (Konto → Datenschutz & Sicherheit), the speakers
    do not answer. The rooms then stay visible as offline and the tile says what to do.

Files
    soap.py        translate between the UPnP interface and Python values (pure functions)
    player.py      one speaker: send actions, read what it plays
    household.py   rooms, bonded boxes and groups
    routes.py      endpoints for the settings tile
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import time
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from plugin_api import (
    STATE_LIVE_AUDIO,
    STATE_MUTED,
    STATE_PLAYING,
    STATE_VOLUME,
    Capability,
    Device,
    DeviceUnreachable,
    MediaState,
    Plugin,
    PluginContext,
    PluginError,
)

from . import household, soap
from .household import Box, Room
from .player import PlayerState, Speaker, SpeakerSilent
from .routes import build_router
from .soap import Favorite

DEVICE_ID_PREFIX = "sonosroom:"                 # device id: sonosroom:<RINCON id of the room's main speaker>
LINK_PREFIX = "sonos:"                          # link key: sonos:<RINCON id> – what a hub derives from its own entry
EGRESS_KEY = "speakers"                         # see manifest.json -> egress
HINT_KIND = "sonos"                             # other plugins publish the speakers they know under this kind
REQUEST_TIMEOUT_SECONDS = 4.0
SEARCH_SECONDS = 3.0
IDLE_POLL_SECONDS = 15.0                        # how often the plugin looks when no page is open
TOPOLOGY_SECONDS = 60.0                         # how long rooms, groups and favourites are kept before asking again
TOPOLOGY_ATTEMPTS = 3                           # how many rooms are asked for the household before giving up for this round
NEVER = float("-inf")                           # time stamp of something that has not happened yet
MAX_FAVORITES = 40
MAX_IMAGES = 60                                 # cover pictures the plugin remembers the address of

ACTION_NEXT = "next"
ACTION_PREVIOUS = "previous"
KEY_FAVORITE = "favorite"
KEY_JOIN = "join"                               # {"join": "<device id of the room to play along with>"}
KEY_LEAVE = "leave"                             # {"leave": True} takes the room out of its group

SETTING_ROOMS = "rooms"                         # the rooms found last (see household.to_setting)

SILENT_TEXT = ("Die Lautsprecher antworten nicht über UPnP. In der Sonos-App unter Konto → Datenschutz & Sicherheit "
               "„UPnP“ einschalten.")
UNREACHABLE_TEXT = "Die Lautsprecher sind nicht erreichbar. Haben sie eine neue Adresse, hilft „Neu suchen“."
OFFLINE_DETAIL = "Lautsprecher antwortet nicht"
LIVE_AUDIO_TITLE = "Ton vom Browser"            # what the speaker and the tile show while the sound of a computer plays
LIVE_AUDIO_SOURCE = "Rechner"
MAX_LIVE_URLS = 4                               # addresses of the live stream the plugin remembers having set
BOX_COMMAND_TEXT = "Diese Box gehört zu einem Raum mit mehreren Boxen und wird über ihn gesteuert."


class SonosPlugin(Plugin):
    """Connects ComfyOS to one Sonos household."""

    def __init__(self, context: PluginContext) -> None:
        """Start without speakers; ``start()`` loads the rooms found earlier."""
        super().__init__(context)
        self.rooms: dict[str, Room] = {}                    # by speaker id
        self.states: dict[str, PlayerState] = {}            # by speaker id; a room without state is offline
        self.favorites: list[Favorite] = []
        self.problem: str | None = None                     # why the speakers cannot be asked right now
        self.silent = False                                 # they answer, but not over UPnP
        self._client: httpx.AsyncClient | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._refresh_lock = asyncio.Lock()
        self._states_at = NEVER                             # time.monotonic() of the last look
        self._topology_at = NEVER
        self._images: dict[str, tuple[str, int, str]] = {}  # image id -> (host, port, path on the speaker)
        self._reported: dict[str, tuple[Any, ...]] = {}     # what was last pushed per room and box (to report changes only)
        self._was_down = False                              # so an outage is written to the event log once
        self._live_urls: list[str] = []                     # addresses of the live stream set on speakers (to recognise it)

    # -- life cycle ---------------------------------------------------------

    async def start(self) -> None:
        """Load the rooms found earlier and start looking at them in the background."""
        self._client = self.context.http(EGRESS_KEY, timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": "ComfyOS"})
        for room in household.from_setting(self.context.settings.get(SETTING_ROOMS)):
            self.rooms[room.uuid] = room
        self._watch_task = asyncio.get_running_loop().create_task(self._watch())

    async def stop(self) -> None:
        """Stop looking and close the connection."""
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.states.clear()
        self._reported.clear()
        self._states_at = self._topology_at = NEVER       # a later start looks at the speakers right away

    def status(self) -> dict[str, Any]:
        """Report whether speakers are known and whether they answer."""
        if not self.rooms:
            return {"configured": False, "connected": False, "live": False}
        return {"configured": True, "connected": self.problem is None, "live": self.problem is None,
                "message": self.problem, "name": "Sonos"}

    def routes(self) -> Any:
        """Return the plugin's own routes (search, address by hand, forget)."""
        return build_router(self)

    def link_prefixes(self) -> list[str]:
        """Return the link-key range of this plugin while the household answers.

        While the speakers are silent nothing is proven, so another hub's speakers are not marked as missing.
        """
        return [LINK_PREFIX] if self.rooms and self.problem is None else []

    def suggestions(self) -> dict[str, Any]:
        """What other plugins know about Sonos speakers: how many there are and the addresses, if they tell."""
        hints = self.context.hints(HINT_KIND)
        hosts = sorted({str(hint["host"]) for hint in hints if hint.get("host")})
        return {"count": len(hints), "hosts": hosts}

    # -- finding speakers (called from routes.py) -----------------------------

    def _speaker(self, host: str, port: int = soap.SPEAKER_PORT) -> Speaker:
        """Return a speaker object for an address. Raises ``DeviceUnreachable`` while the plugin is stopped."""
        if self._client is None:
            raise DeviceUnreachable("Das Plugin läuft nicht.")
        return Speaker(self._client, host, port)

    async def search(self) -> int:
        """Search the home network for speakers and take over the household. Returns the number of rooms."""
        answers = await self.context.discover(EGRESS_KEY, "ssdp", soap.ZONE_PLAYER, SEARCH_SECONDS)
        hosts = list(dict.fromkeys(answer.host for answer in answers))
        if not hosts:
            raise PluginError("Kein Sonos-Lautsprecher gefunden. Ist „UPnP“ in der Sonos-App eingeschaltet "
                              "(Konto → Datenschutz & Sicherheit)? Sonst hilft die Adresse eines Lautsprechers von Hand.")
        return await self._adopt_household(hosts)

    async def connect(self, host: str) -> int:
        """Take over the household of the speaker at ``host`` (typed in by the user). Returns the number of rooms."""
        host = host.strip().strip("[]")
        if not host or not self.context.is_local_host(host):
            raise PluginError("Der Lautsprecher muss im Heimnetz liegen (IP-Adresse oder Name im Heimnetz).")
        await self._speaker(host).description()          # raises with a clear text when this is not a Sonos speaker
        return await self._adopt_household([host])

    async def _adopt_household(self, hosts: list[str]) -> int:
        """Read the household from the first address that answers and store its rooms."""
        last_error: PluginError = DeviceUnreachable(UNREACHABLE_TEXT)
        for host in hosts:
            try:
                rooms = await self._load_rooms(self._speaker(host))
            except PluginError as error:
                last_error = error
                continue
            if rooms:
                self._set_rooms(rooms)
                self._topology_at = time.monotonic()
                await self._load_favorites()
                await self.refresh(0, force=True)
                self.context.event(f"Sonos: {len(rooms)} {'Raum' if len(rooms) == 1 else 'Räume'} gefunden")
                self.context.devices_changed()
                return len(rooms)
        raise last_error

    async def forget(self) -> None:
        """Forget all speakers."""
        self.rooms.clear()
        self.states.clear()
        self.favorites = []
        self._reported.clear()
        self._images.clear()
        self.problem, self.silent = None, False
        self._states_at = self._topology_at = NEVER
        self.context.settings.delete(SETTING_ROOMS)
        self.context.event("Sonos-Lautsprecher vergessen")
        self.context.devices_changed()

    def _set_rooms(self, rooms: list[Room]) -> None:
        """Take over a new room list and store it when rooms, names or addresses changed."""
        before = household.to_setting(list(self.rooms.values()))
        self.rooms = {room.uuid: room for room in rooms}
        after = household.to_setting(rooms)
        if after != before:
            self.context.settings.set(SETTING_ROOMS, after)
        for gone in [uuid for uuid in self.states if uuid not in self.rooms]:
            self.states.pop(gone, None)
            self._reported.pop(gone, None)

    async def _load_rooms(self, speaker: Speaker) -> list[Room]:
        """Ask one speaker for the household. Model names are read once per speaker and kept."""
        groups = await speaker.zone_groups()
        models = {uuid: room.model for uuid, room in self.rooms.items() if room.model}
        rooms = household.build_rooms(groups, models)
        named = []
        for room in rooms:
            if not room.model:
                try:
                    info = await self._speaker(room.host, room.port).description()
                    room = dataclasses.replace(room, model=info.model)
                except PluginError:
                    pass        # the model is only a detail for the device sheet
            named.append(room)
        return named

    async def _load_favorites(self) -> None:
        """Read the Sonos favourites from the first room that answers; keep the old ones when none does."""
        for room in list(self.rooms.values()):
            try:
                self.favorites = (await self._speaker(room.host, room.port).favorites())[:MAX_FAVORITES]
                return
            except PluginError:
                continue

    # -- looking at the speakers ----------------------------------------------

    async def _watch(self) -> None:
        """Look at the speakers every few seconds, so flows notice changes while no page is open."""
        while True:
            await asyncio.sleep(IDLE_POLL_SECONDS)
            try:
                await self.refresh(IDLE_POLL_SECONDS)
            except Exception as error:      # the watcher must survive anything a speaker sends
                self.context.log.warning("Sonos: Abfrage gescheitert (%s)", type(error).__name__)

    async def refresh(self, max_age: float | None, force: bool = False) -> None:
        """Bring rooms and states up to date unless they are younger than ``max_age`` seconds. Never raises.

        While the speakers do not answer they are asked only every ``IDLE_POLL_SECONDS``, however often
        ComfyOS wants the device list – ``force`` asks anyway (after a search or a command).
        """
        if not self.rooms or self._client is None:
            return
        limit = IDLE_POLL_SECONDS if max_age is None else max(0.0, float(max_age))
        if self.problem is not None and not force:
            limit = max(limit, IDLE_POLL_SECONDS)
        async with self._refresh_lock:
            now = time.monotonic()
            if now - self._states_at < limit and not force:
                return
            await self._refresh_topology(now)
            states, error = await self._read_states()
            self.states = states
            self._states_at = time.monotonic()
            self._note_outcome(error if not states else None)
            self._report_changes()

    async def _refresh_topology(self, now: float) -> None:
        """Ask for rooms, groups and favourites again when the last answer is old.

        Skipped while the speakers do not answer: their old addresses could not tell anything new,
        and once they answer again the topology is read in the next round.
        """
        if self.problem is not None or now - self._topology_at < TOPOLOGY_SECONDS:
            return
        # Rooms that answered last time first, so a switched-off speaker does not hold up the round.
        candidates = sorted(self.rooms.values(), key=lambda room: room.uuid not in self.states)[:TOPOLOGY_ATTEMPTS]
        for room in candidates:
            try:
                rooms = await self._load_rooms(self._speaker(room.host, room.port))
            except PluginError:
                continue
            if rooms:
                if {known.uuid for known in rooms} != set(self.rooms):
                    self.context.devices_changed()
                self._set_rooms(rooms)
                self._topology_at = now
                await self._load_favorites()
                return

    async def _read_states(self) -> tuple[dict[str, PlayerState], PluginError | None]:
        """Read every room: playback from the coordinator of its group, volume from the room itself."""
        rooms = list(self.rooms.values())
        leaders = {room.coordinator or room.uuid for room in rooms}

        async def read_playback(uuid: str) -> PlayerState | PluginError:
            leader = self.rooms.get(uuid)
            if leader is None:
                return DeviceUnreachable(OFFLINE_DETAIL)
            try:
                return await self._speaker(leader.host, leader.port).playback()
            except PluginError as error:
                return error

        async def read_volume(room: Room) -> tuple[int | None, bool | None] | PluginError:
            try:
                return await self._speaker(room.host, room.port).volume()
            except PluginError as error:
                return error

        leader_ids = sorted(leaders)
        playbacks = dict(zip(leader_ids, await asyncio.gather(*(read_playback(uuid) for uuid in leader_ids))))
        volumes = await asyncio.gather(*(read_volume(room) for room in rooms))
        states: dict[str, PlayerState] = {}
        last_error: PluginError | None = None
        for room, volume in zip(rooms, volumes):
            playback = playbacks.get(room.coordinator or room.uuid)
            if isinstance(volume, PluginError):
                last_error = volume
                continue
            if isinstance(playback, PluginError) or playback is None:
                # The room answers, its group leader does not: show the room without a track.
                last_error = playback if isinstance(playback, PluginError) else last_error
                playback = PlayerState()
            states[room.uuid] = PlayerState(playing=playback.playing, volume=volume[0], muted=volume[1],
                                            track=playback.track, station=playback.station)
        return states, last_error

    def _note_outcome(self, error: PluginError | None) -> None:
        """Remember whether the household answers and write an outage and its end to the event log once each."""
        if error is None:
            if self._was_down:
                self.context.event("Sonos-Lautsprecher wieder erreichbar")
                self._topology_at = NEVER                # addresses and groups may have changed meanwhile
            self.problem, self.silent, self._was_down = None, False, False
            return
        self.silent = isinstance(error, SpeakerSilent)
        self.problem = SILENT_TEXT if self.silent else UNREACHABLE_TEXT
        if not self._was_down:
            self.context.event("Sonos: " + self.problem)
        self._was_down = True

    def _report_changes(self) -> None:
        """Push what changed since the last look, so open pages and flows react at once."""
        for uuid, room in self.rooms.items():
            state = self.states.get(uuid)
            media = self._media(room, state) if state is not None else None
            self._report_boxes(room, state, media)
            seen = (state is not None, state.playing if state else None, state.volume if state else None,
                    state.muted if state else None, media.title if media else None, media.artist if media else None,
                    media.image_id if media else None, tuple(media.group) if media else ())
            before = self._reported.get(uuid)
            self._reported[uuid] = seen
            if before is None or before == seen:
                continue                    # first look or nothing new
            changes: dict[str, Any] = {"online": state is not None}
            if state is not None:
                changes.update({STATE_PLAYING: state.playing, STATE_VOLUME: state.volume, STATE_MUTED: state.muted,
                                "media": media})
            self.context.device_changed(DEVICE_ID_PREFIX + uuid, changes)

    def _report_boxes(self, room: Room, state: PlayerState | None, media: MediaState | None) -> None:
        """Push what changed for the further boxes of a room: whether they answer, whether they play and which track."""
        seen = (state is not None, state.playing if state else None, media.title if media else None,
                media.artist if media else None, media.image_id if media else None)
        for box in room.boxes:
            before = self._reported.get(box.uuid)
            self._reported[box.uuid] = seen
            if before is None or before == seen:
                continue                    # first look or nothing new
            changes: dict[str, Any] = {"online": state is not None}
            if state is not None:
                changes["media"] = self._box_media(room, state)
            self.context.device_changed(DEVICE_ID_PREFIX + box.uuid, changes)

    # -- devices --------------------------------------------------------------

    async def devices(self) -> list[Device]:
        """Return one device per room and one per further box of a room. What does not answer stays in the list as offline."""
        await self.refresh(self.max_device_age)
        devices = []
        for room in self.rooms.values():
            devices.append(self._room_as_device(room))
            devices.extend(self._box_as_device(room, box) for box in room.boxes)
        return devices

    def _room_as_device(self, room: Room) -> Device:
        """Convert a room to a ComfyOS device with its capabilities and the keys another hub knows it by."""
        state = self.states.get(room.uuid)
        favorite_names = tuple(favorite.title for favorite in self.favorites)
        capabilities = [
            Capability(STATE_PLAYING, "bool", writable=True),
            Capability(STATE_VOLUME, "number", unit="%", minimum=0, maximum=100, step=1, writable=True),
            Capability(STATE_MUTED, "bool", writable=True),
            Capability(ACTION_NEXT, "action", readable=False, writable=True),
            Capability(ACTION_PREVIOUS, "action", readable=False, writable=True),
            Capability(STATE_LIVE_AUDIO, "bool", readable=False, writable=True),
        ]
        if favorite_names:
            capabilities.append(Capability(KEY_FAVORITE, "enum", options=favorite_names, readable=False, writable=True))
        values: dict[str, Any] = {}
        if state is not None:
            values = {STATE_PLAYING: state.playing, STATE_VOLUME: state.volume, STATE_MUTED: state.muted}
        details = {"Modell": room.model, "Adresse": room.host, "Sonos-ID": room.uuid,
                   "Boxen in diesem Raum": str(len(room.bonded) + 1) if room.bonded else ""}
        return Device(
            id=DEVICE_ID_PREFIX + room.uuid,
            name=room.name,
            kind="media",
            online=state is not None,
            state=values,
            capabilities=capabilities,
            media=self._media(room, state) if state is not None else None,
            room_hint=room.name,
            vendor="Sonos",
            model=room.model or None,
            status_detail=None if state is not None else (SILENT_TEXT if self.silent else OFFLINE_DETAIL),
            link_keys=[LINK_PREFIX + room.uuid],
            discovered=True,        # rooms no hub knows appear after the user adopts them
            details={label: value for label, value in details.items() if value},
        )

    def _box_as_device(self, room: Room, box: Box) -> Device:
        """Convert a further box of a room to a device that shows what the room plays.

        It has no capabilities and reports no state: the tile "Wiedergabe" and flows only know the room
        (ComfyOS takes a reported ``playing`` for a capability), and nothing can be sent to the box. It
        exists so the box can stand in its room and on the floor plan; while the room plays it carries
        the track (``media``), otherwise nothing.
        """
        state = self.states.get(room.uuid)
        return Device(
            id=DEVICE_ID_PREFIX + box.uuid,
            name=box.name,
            kind="media",
            online=state is not None,
            state={},
            capabilities=[],
            media=self._box_media(room, state),
            room_hint=room.name,
            vendor="Sonos",
            status_detail=None if state is not None else (SILENT_TEXT if self.silent else OFFLINE_DETAIL),
            link_keys=[LINK_PREFIX + box.uuid],
            discovered=True,
            details={"Gehört zu": room.name, "Sonos-ID": box.uuid},
        )

    def _box_media(self, room: Room, state: PlayerState | None) -> MediaState | None:
        """Describe what a further box plays: the track of its room while it plays, without favourites; otherwise None."""
        if state is None or not state.playing:
            return None
        return dataclasses.replace(self._media(room, state), favorites=[])

    def _media(self, room: Room, state: PlayerState) -> MediaState:
        """Describe what the room plays for the playback tile."""
        track = state.track
        title, artist = track.title, track.artist
        if state.station:
            # Radio: the station is the title, what it plays right now stands where the artist would.
            title, artist = state.station, track.stream_text or None
        leader = self.rooms.get(room.coordinator)
        source = soap.source_name(track.uri)
        if track.uri in self._live_urls:
            title, artist, source = LIVE_AUDIO_TITLE, None, LIVE_AUDIO_SOURCE
        return MediaState(
            title=title, artist=artist, album=track.album, source=source,
            image_id=self._image_id(room if room.leads or leader is None else leader, track.art_uri),
            position=track.position, duration=track.duration,
            group=[self.rooms[other].name for other in room.group if other in self.rooms],
            coordinator=None if room.leads or leader is None else leader.name,
            favorites=[favorite.title for favorite in self.favorites])

    def _image_id(self, room: Room, art_uri: str | None) -> str | None:
        """Give a cover picture an id, if a speaker of the household serves it itself.

        Pictures on other servers are never loaded – that would be a connection to the internet.
        """
        if not art_uri:
            return None
        address = urlsplit(art_uri)
        if address.scheme or address.netloc:
            speaker = next((known for known in self.rooms.values() if known.host == address.hostname), None)
            if address.scheme != "http" or speaker is None or (address.port or 80) != speaker.port:
                return None
            room, art_uri = speaker, address.path + ("?" + address.query if address.query else "")
        if not art_uri.startswith("/"):
            return None
        image_id = hashlib.sha256(f"{room.uuid}|{art_uri}".encode("utf-8")).hexdigest()[:24]
        self._images.pop(image_id, None)                 # re-insert: the newest pictures stay
        self._images[image_id] = (room.host, room.port, art_uri)
        while len(self._images) > MAX_IMAGES:
            self._images.pop(next(iter(self._images)))
        return image_id

    async def media_image(self, image_id: str) -> tuple[bytes, str] | None:
        """Fetch a cover picture from the speaker that serves it."""
        where = self._images.get(image_id)
        if where is None or self._client is None:
            return None
        return await self._speaker(where[0], where[1]).image(where[2])

    # -- commands -------------------------------------------------------------

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
        """Apply a command to a room: playback, track, volume, mute, favourite, join or leave a group.

        Playback commands go to the coordinator of the room's group, volume and mute to the room itself.
        ``{"live_audio": True}`` lets the group play the sound a browser sends to ComfyOS, ``False`` stops it.
        """
        speaker_id = device_id[len(DEVICE_ID_PREFIX):] if device_id.startswith(DEVICE_ID_PREFIX) else ""
        room = self.rooms.get(speaker_id)
        if room is None:
            if any(speaker_id in known.bonded for known in self.rooms.values()):
                raise PluginError(BOX_COMMAND_TEXT)
            raise PluginError("Diesen Lautsprecher gibt es bei Sonos nicht (mehr).")
        speaker = self._speaker(room.host, room.port)
        leader_room = self.rooms.get(room.coordinator) or room
        leader = self._speaker(leader_room.host, leader_room.port)

        if state.get(KEY_LEAVE):
            await speaker.leave()
        if state.get(KEY_JOIN) is not None:
            await speaker.join(self._join_target(room, state[KEY_JOIN]))
        if state.get(STATE_LIVE_AUDIO) is not None:
            await self._live_audio(leader, leader_room, _as_bool(state[STATE_LIVE_AUDIO], "Ton vom Browser"))
        elif state.get(KEY_FAVORITE) is not None:
            await leader.play_favorite(self._favorite(state[KEY_FAVORITE]), leader_room.uuid)
        elif state.get(STATE_PLAYING) is not None:
            await (leader.play() if _as_bool(state[STATE_PLAYING], "Wiedergabe") else leader.pause())
        if state.get(ACTION_NEXT):
            await leader.next_track()
        if state.get(ACTION_PREVIOUS):
            await leader.previous_track()
        if state.get(STATE_VOLUME) is not None:
            await speaker.set_volume(_as_level(state[STATE_VOLUME]))
        if state.get(STATE_MUTED) is not None:
            await speaker.set_mute(_as_bool(state[STATE_MUTED], "Stumm"))

        if state.get(KEY_LEAVE) or state.get(KEY_JOIN) is not None:
            self._topology_at = NEVER                    # the groups changed: read them again
        await self.refresh(0, force=True)
        after = self.states.get(room.uuid)
        if after is None:
            return None
        return {STATE_PLAYING: after.playing, STATE_VOLUME: after.volume, STATE_MUTED: after.muted,
                "media": self._media(self.rooms.get(room.uuid, room), after)}

    async def _live_audio(self, leader: Speaker, leader_room: Room, on: bool) -> None:
        """Let a group play the sound a browser sends to ComfyOS, or stop it if that is what the group plays."""
        if on:
            url = self.context.live_audio_url(leader_room.host)      # raises when no transmission runs
            self._live_urls = ([known for known in self._live_urls if known != url] + [url])[-MAX_LIVE_URLS:]
            await leader.play_stream(url, LIVE_AUDIO_TITLE)
            return
        state = self.states.get(leader_room.uuid)
        if state is not None and state.track.uri in self._live_urls:
            await leader.stop()

    def _favorite(self, name: Any) -> Favorite:
        """Find a favourite by its name. Raises ``PluginError`` when there is none with that name."""
        wanted = " ".join(str(name).split()).casefold()
        favorite = next((known for known in self.favorites if " ".join(known.title.split()).casefold() == wanted), None)
        if favorite is None:
            raise PluginError("Diesen Favoriten gibt es bei Sonos nicht (mehr).")
        return favorite

    def _join_target(self, room: Room, device_id: Any) -> str:
        """Return the id of the group leader a room should follow. Raises ``PluginError`` for an unknown room or itself."""
        target = self.rooms.get(str(device_id)[len(DEVICE_ID_PREFIX):]) if str(device_id).startswith(DEVICE_ID_PREFIX) else None
        if target is None or target.uuid == room.uuid:
            raise PluginError("Diesen Raum gibt es bei Sonos nicht (mehr).")
        return target.coordinator or target.uuid


def _as_bool(value: Any, what: str) -> bool:
    """Accept only a real yes or no. Raises ``PluginError`` otherwise."""
    if not isinstance(value, bool):
        raise PluginError(f"{what}: nur an oder aus.")
    return value


def _as_level(value: Any) -> int:
    """Accept a volume from 0 to 100. Raises ``PluginError`` otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not 0 <= value <= 100:
        raise PluginError("Die Lautstärke muss zwischen 0 und 100 liegen.")
    return int(round(value))


def setup(context: PluginContext) -> Plugin:
    """Entry point named in manifest.json."""
    return SonosPlugin(context)
