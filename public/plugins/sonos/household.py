"""The Sonos household: which rooms exist, where they are and who plays together.

One speaker knows the whole household (``GetZoneGroupState``). From its answer this module
builds the rooms ComfyOS shows:

    * one room per *visible* speaker – the second box of a stereo pair, surround speakers and
      subs belong to the room of their main speaker. The room knows them as its boxes
      (``Room.boxes``), so each can be shown as a device of its own, e.g. on the floor plan;
    * for every room its coordinator: playback commands go there, volume stays with the room.

Rooms are stored in the plugin's settings in a plain form (``to_setting`` / ``from_setting``),
so they stay visible – offline – while the speakers do not answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import soap
from .soap import ZoneGroup

MAX_ROOMS = 64
MAX_NAME_LENGTH = 80

# What a bonded box plays (channels as the topology names them) -> how the user calls it.
CHANNEL_LABELS = {"LF,LF": "links", "RF,RF": "rechts", "LR": "hinten links", "RR": "hinten rechts", "LR,RR": "hinten",
                  "SW": "Subwoofer"}


@dataclass(frozen=True)
class Box:
    """One further box of a room: the second one of a stereo pair, a surround speaker or a sub."""

    uuid: str                                   # id of the box ("RINCON_…")
    name: str                                   # "<room> (hinten links)"; "<room> (Box 2)" when the role is unknown


@dataclass(frozen=True)
class Room:
    """One room of the household: a visible speaker with the boxes bonded to it."""

    uuid: str                                   # id of the main speaker ("RINCON_…")
    name: str
    host: str
    port: int = soap.SPEAKER_PORT
    bonded: tuple[str, ...] = ()                # ids of the other boxes of this room
    channels: tuple[str, ...] = ()              # what each of them plays, in the same order ("LR", "SW" …; may be shorter)
    coordinator: str = ""                       # id of the room that leads the group (its own id when it leads)
    group: tuple[str, ...] = ()                 # ids of the other rooms in the same group
    model: str = ""

    @property
    def leads(self) -> bool:
        """Return whether this room takes the playback commands itself."""
        return self.coordinator in ("", self.uuid)

    @property
    def boxes(self) -> tuple[Box, ...]:
        """Return the further boxes of this room, each with a name that tells them apart."""
        boxes = []
        for index, uuid in enumerate(self.bonded):
            played = self.channels[index] if index < len(self.channels) else ""
            label = CHANNEL_LABELS.get(played) or f"Box {index + 2}"
            boxes.append(Box(uuid=uuid, name=f"{self.name} ({label})"[:MAX_NAME_LENGTH]))
        return tuple(boxes)


def build_rooms(groups: list[ZoneGroup], models: dict[str, str] | None = None) -> list[Room]:
    """Turn the topology into rooms, sorted by name. ``models`` maps speaker ids to model names already known."""
    rooms = []
    for group in groups:
        visible = [member for member in group.members if member.visible]
        ids = [member.uuid for member in visible]
        # The coordinator is always a visible speaker; if the answer names none of them, the first one leads.
        coordinator = group.coordinator if group.coordinator in ids else (ids[0] if ids else "")
        for member in visible:
            rooms.append(Room(uuid=member.uuid, name=member.name[:MAX_NAME_LENGTH], host=member.host, port=member.port,
                              bonded=member.bonded, channels=member.channels, coordinator=coordinator,
                              group=tuple(other for other in ids if other != member.uuid),
                              model=(models or {}).get(member.uuid, "")))
    rooms.sort(key=lambda room: (room.name.casefold(), room.uuid))
    return rooms[:MAX_ROOMS]


def to_setting(rooms: list[Room]) -> list[dict[str, Any]]:
    """Rooms in the form stored in the plugin's settings (who plays with whom is not stored – it changes all the time)."""
    return [{"uuid": room.uuid, "name": room.name, "host": room.host, "port": room.port, "bonded": list(room.bonded),
             "channels": list(room.channels), "model": room.model} for room in rooms]


def from_setting(stored: Any) -> list[Room]:
    """Read rooms from the settings. Entries that do not look like a room are skipped."""
    rooms = []
    for entry in stored if isinstance(stored, list) else []:
        if not isinstance(entry, dict):
            continue
        uuid, host, port = str(entry.get("uuid") or ""), str(entry.get("host") or ""), entry.get("port")
        if not soap.SPEAKER_ID.match(uuid) or not host or isinstance(port, bool) or not isinstance(port, int):
            continue
        bonded = tuple(str(other) for other in entry.get("bonded") or [] if soap.SPEAKER_ID.match(str(other)))
        # "channels" is missing in rooms stored before plugin version 1.1; the boxes are then numbered until the next look.
        channels = tuple(str(played)[:20] for played in entry.get("channels") or [])[:len(bonded)]
        rooms.append(Room(uuid=uuid, name=str(entry.get("name") or uuid)[:MAX_NAME_LENGTH], host=host, port=port,
                          bonded=bonded, channels=channels, coordinator=uuid,
                          model=str(entry.get("model") or "")[:MAX_NAME_LENGTH]))
    return rooms[:MAX_ROOMS]
