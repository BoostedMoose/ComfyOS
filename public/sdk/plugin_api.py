"""ComfyOS plugin API, version 1 (revision 7).

This module is the only part of ComfyOS a plugin may import. It contains types and
base classes, no behaviour of the core. A copy is published as ``public/sdk/plugin_api.py``.

A plugin is a folder with a ``manifest.json`` and a Python package whose entry point
returns a :class:`Plugin`::

    from plugin_api import Device, Plugin, PluginContext


    class MyPlugin(Plugin):
        async def devices(self) -> list[Device]:
            return [Device(id="my:lamp-1", name="Desk lamp", kind="light", state={"on": True})]


    def setup(context: PluginContext) -> Plugin:
        return MyPlugin(context)

Rules every plugin follows (see ``public/CONTRIBUTING.md``):

* Import only the standard library, this module, ``fastapi``, ``pydantic``, ``httpx``
  and modules of the plugin's own package. Never import another plugin or the core.
* Talk to the outside world only through :meth:`PluginContext.http` (HTTP),
  :meth:`PluginContext.open_stream` (plain TCP) and, inside the home network,
  :meth:`PluginContext.open_datagram` / :meth:`PluginContext.discover` (UDP). The targets
  must be declared in the manifest; anything else is blocked. Never open sockets or HTTP
  clients yourself.
* Start another program only through :meth:`PluginContext.start_helper`, and only when
  the manifest declares it (``"helper": {"desc": "…"}``). Never use ``subprocess`` yourself.
* Devices of other plugins, rooms, scenes and the home state are reachable only with a
  permission in the manifest (``"access": ["devices", "rooms", "scenes", "home"]``).
* Keep credentials in :attr:`PluginContext.secrets`, never in settings, logs or errors.
* Switch devices only inside :meth:`Plugin.command`. The core checks the device lock
  before it calls that method.

Versioning: ``API_VERSION`` only increases when a change breaks existing plugins.
New optional fields and methods do not change it; they raise ``API_REVISION`` instead.
A plugin that needs such an addition names the revision in its manifest
(``"api_revision": 2``), so an older ComfyOS refuses it with a clear message.

Revision history
    1   first public version (ComfyOS 0.18)
    2   ``PluginContext.open_stream``, ``ConnectionRefused``, device kind ``presence``,
        state keys ``humidity`` and ``present``; for hubs: ``"id_prefixes": ["*"]``,
        ``Device.room_id`` and further device fields, rooms (``Plugin.manages_rooms``),
        heating plans (``Plugin.syncs_heating``, ``PluginContext.heating``),
        ``Plugin.max_device_age``, ``PluginContext.device_gone``, ``status()["egress"]`` (ComfyOS 0.19)
    3   helper programs: manifest ``"helper"``, ``PluginContext.start_helper``, :class:`Helper`;
        access to everything ComfyOS controls, by permission: manifest ``"access"``,
        ``PluginContext.all_devices``, ``on_change``, ``command``, ``room_lights``, ``scenes``,
        ``house_scene``, ``home_state``, ``home``; :class:`DeviceView`, :class:`SceneView`,
        :class:`DeviceLocked` (ComfyOS 0.21)
    4   UDP in the home network: ``PluginContext.open_datagram``, :class:`Datagram`,
        ``PluginContext.discover`` (SSDP), :class:`SsdpAnswer` (manifest: ``"udp": true`` on a
        ``"scope": "lan"`` target); capability type ``"action"``; speakers: :class:`MediaState`,
        ``Device.media``, state keys ``playing``, ``volume`` and ``muted``,
        ``Plugin.media_image`` (ComfyOS 0.22)
    5   labelled capabilities: ``Capability.label`` for values without a well-known state key
        (a mode, a threshold, a button); ComfyOS lists them in the device sheet (ComfyOS 0.22.1)
    6   live audio: capability ``live_audio`` (``STATE_LIVE_AUDIO``) and
        ``PluginContext.live_audio_url`` – a speaker plays the sound a browser sends to ComfyOS (ComfyOS 0.22.3)
    7   buttons and remotes: device kind ``button``, :class:`Button`, ``Device.buttons``,
        ``PluginContext.button_pressed`` with ``PRESS_SHORT`` / ``PRESS_LONG`` / ``PRESS_DOUBLE``;
        state key ``illuminance`` (lux) (ComfyOS 0.23)
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Protocol, Sequence

API_VERSION = 1
API_REVISION = 7

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: What a device is. The user interface picks icon, tile and controls from this.
DEVICE_KINDS = (
    "light",        # lamp: on/off, optionally brightness, white tone, colour
    "switch",       # socket or relay: on/off
    "thermostat",   # radiator thermostat: temperature and target
    "sensor",       # temperature, humidity and similar readings
    "contact",      # door or window contact: open / closed
    "motion",       # motion detector
    "smoke",        # smoke detector
    "cover",        # roller shutter, blind, awning
    "media",        # speaker, TV
    "presence",     # a person or a device that is at home or away
    "button",       # wall switch or remote: reports presses (Device.buttons), keeps no state of its own
    "unknown",      # anything else – shown, but without controls
)

#: Value types a capability can have. An ``"action"`` has no state: it is something the device
#: does when asked, for example "next track". It is triggered like any other value, with
#: ``Plugin.command(device_id, {"next": True}, source)``, and is never reported in ``Device.state``.
CAPABILITY_TYPES = ("bool", "number", "enum", "color", "action")

#: Well-known state keys. Use these names so that tiles, flows and scenes understand the device.
STATE_ON = "on"                    # bool
STATE_BRIGHTNESS = "brightness"    # number, 0–100 %
STATE_TEMPERATURE = "temperature"  # number, °C (measured)
STATE_TARGET = "target"            # number, °C (set point)
STATE_OPEN = "open"                # bool, contact is open
STATE_MOTION = "motion"            # bool, motion is detected right now
STATE_POSITION = "position"        # number, 0 = open … 100 = closed (covers)
STATE_ALARM = "alarm"              # bool, smoke detector reports an alarm
STATE_HUMIDITY = "humidity"        # number, % relative humidity
STATE_PRESENT = "present"          # bool, the person or device is at home
STATE_PLAYING = "playing"          # bool, a speaker is playing right now
STATE_VOLUME = "volume"            # number, 0–100 %
STATE_MUTED = "muted"              # bool, the speaker is muted
STATE_LIVE_AUDIO = "live_audio"    # bool, write only: play the sound a browser sends to ComfyOS (True) or stop it (False)
STATE_ILLUMINANCE = "illuminance"  # number, lux: how bright the surroundings are (light sensor of a motion detector …)

#: How a button was pressed (``PluginContext.button_pressed``). Report only what the device really tells apart:
#: a remote that knows no long press only ever reports PRESS_SHORT.
PRESS_SHORT = "short"              # pressed and released
PRESS_LONG = "long"                # held down (reported once when the hold is recognised)
PRESS_DOUBLE = "double"            # pressed twice in a row
PRESS_KINDS = (PRESS_SHORT, PRESS_LONG, PRESS_DOUBLE)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PluginError(Exception):
    """A problem the user can understand and act on.

    The message is shown in the user interface, so write it in the language of the
    interface and never include credentials, tokens or internal paths.
    """


class DeviceUnreachable(PluginError):
    """The device or its hub did not answer (network down, hub restarting, key rejected)."""


class ConnectionRefused(DeviceUnreachable):
    """The host answered, but nothing listens on the port (raised by ``PluginContext.open_stream``).

    The host itself is switched on and in the network – a presence check counts this as "there".
    """


class DeviceLocked(PluginError):
    """The user has locked the device in ComfyOS (raised by ``PluginContext.command``).

    The device was not switched. Report that to whoever asked; never try another way.
    """


#: Permissions a manifest can ask for under ``"access"``. The tile of the plugin and the
#: approval dialog name them in plain words; without the permission the call raises PluginError.
ACCESS_DEVICES = "devices"         # see and switch every device of ComfyOS (all_devices, on_change, command)
ACCESS_ROOMS = "rooms"             # switch the lights of a whole room or of the home (room_lights)
ACCESS_SCENES = "scenes"           # list, start and stop house scenes (scenes, house_scene)
ACCESS_HOME = "home"               # read and set "at home" / "away" (home_state, home)
ACCESS_KINDS = (ACCESS_DEVICES, ACCESS_ROOMS, ACCESS_SCENES, ACCESS_HOME)


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Capability:
    """One thing a device can report or do, for example ``on`` or ``temperature``.

    Flows use capabilities to offer the right triggers and conditions for a device.

    A capability whose key is not one of the well-known state keys needs a ``label``: ComfyOS
    then lists it in the device sheet with a control that fits its type (a switch, a choice,
    a number field, a button). Tiles, scenes and flows only know the well-known keys.
    """

    key: str                              # state key, e.g. STATE_ON
    type: str = "bool"                    # one of CAPABILITY_TYPES
    unit: str | None = None               # "°C", "%", "W" …
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    options: tuple[str, ...] = ()         # allowed values when type is "enum"
    readable: bool = True                 # the device reports this value
    writable: bool = False                # Plugin.command accepts this key
    label: str | None = None              # name to show, in the language of the interface (see above)


@dataclass
class LightCapabilities:
    """What a lamp can show beyond on/off and brightness."""

    white_range: tuple[int, int] | None = None   # (coolest, warmest) white tone in mirek; None = no white tones
    color: bool = False                          # full colour (CIE xy)
    gradient_points: int = 0                     # how many colours a gradient strip shows at once; 0 = no gradient
    gradient_modes: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)   # built-in effects such as "candle"
    effect_parameters: bool = False              # effects accept their own colour and speed


@dataclass
class LightState:
    """Capabilities and current light of a dimmable lamp.

    Plugs and lamps that can only be switched have no ``LightState`` (``Device.light`` is None).
    """

    capabilities: LightCapabilities = field(default_factory=LightCapabilities)
    mode: str | None = None                      # "ct" (white tone), "xy" (colour) or "gradient"
    xy: list[float] | None = None                # current colour as CIE [x, y]
    mirek: int | None = None                     # current white tone (1 000 000 / kelvin)
    gradient: list[list[float]] = field(default_factory=list)   # colours of a gradient as [[x, y], …]
    gradient_mode: str | None = None
    effect: str | None = None                    # running effect, None = no effect


@dataclass
class MediaState:
    """What a speaker is playing, for the playback tile (the counterpart of :class:`LightState`).

    Everything here comes from the speaker and is foreign text: ComfyOS escapes it before
    showing it. Leave out what the speaker does not tell.
    """

    title: str | None = None                     # track or station
    artist: str | None = None
    album: str | None = None
    source: str | None = None                    # where the sound comes from, e.g. "Radio", "TV", "Line-In"
    image_id: str | None = None                  # cover picture, fetched with Plugin.media_image
    position: int | None = None                  # seconds into the track
    duration: int | None = None                  # length of the track in seconds; None for radio and TV
    group: list[str] = field(default_factory=list)   # names of the other rooms that play the same thing
    coordinator: str | None = None               # name of the room that leads the group; None = this speaker leads
    favorites: list[str] = field(default_factory=list)   # names accepted by Plugin.command(…, {"favorite": name})


@dataclass(frozen=True)
class Button:
    """One button of a wall switch or remote (``Device.buttons``).

    Flows offer the buttons by name; a press arrives through ``PluginContext.button_pressed``.
    """

    id: str                               # stable within the device, e.g. "1" or "up" (at most 40 characters)
    name: str                             # name to show, in the language of the interface, e.g. "Heller"


@dataclass(frozen=True)
class Reading:
    """One readable value of a device for the detail view, e.g. a channel of a thing on a hub."""

    key: str                              # technical name, e.g. "temperature"
    label: str                            # name to show
    value: str                            # the value as text, e.g. "21.5 °C"


@dataclass(frozen=True)
class Room:
    """A room in the room list of a hub (see :attr:`Plugin.manages_rooms`)."""

    id: str                               # stable id on the hub
    name: str                             # name the hub shows


@dataclass
class Device:
    """One device as a plugin reports it to ComfyOS.

    Only ``id``, ``name`` and ``kind`` are required. Fill in what the device really
    reports – ComfyOS never invents values.
    """

    id: str                                      # stable and unique; must start with an ``id_prefixes`` entry of the manifest
    name: str                                    # the name the device has in its own system
    kind: str                                    # one of DEVICE_KINDS
    online: bool | None = True                   # None = unknown (the plugin cannot tell)
    state: dict[str, Any] = field(default_factory=dict)             # current values by state key
    capabilities: list[Capability] = field(default_factory=list)
    light: LightState | None = None              # only for dimmable lamps
    media: MediaState | None = None              # only for speakers
    buttons: list[Button] = field(default_factory=list)   # buttons whose presses the plugin reports (kind "button" …)

    room_hint: str | None = None                 # room name in the device's own system (used as a suggestion)
    room_id: str | None = None                   # id of the device's room on a hub that manages rooms (Plugin.manages_rooms)
    vendor: str | None = None
    model: str | None = None
    type_name: str | None = None                 # technical type as the hub calls it, e.g. "dect:thermostat"
    battery: int | None = None                   # per cent
    low_battery: bool | None = None              # the device itself reports a weak battery
    last_seen: Any = None                        # last successful contact as the hub reports it (time stamp or text)
    problems: list[str] = field(default_factory=list)        # faults to show the user, e.g. "Gerätefehler E1"
    readings: list[Reading] = field(default_factory=list)    # every readable value, for the detail view
    power_w: float | None = None                 # current power draw
    energy_kwh: float | None = None              # meter reading
    status_detail: str | None = None             # short note when the device needs attention, e.g. why it is offline

    # The same physical device can be known to several plugins (a lamp on the Hue Bridge that
    # the Home Base also lists). Devices that share a link key are shown as one device.
    link_keys: list[str] = field(default_factory=list)
    # True when this plugin only relays the device and another system controls it directly.
    # A relayed device keeps its id, name and room; state and commands come from the direct device.
    relayed: bool = False
    # True for devices found on a hub that the user has not added to ComfyOS yet. They appear as
    # "new" in the plugin's settings and become real devices once the user adopts them.
    discovered: bool = False
    # True when the hub stores a name for this device. ComfyOS then pushes its own name with Plugin.rename.
    renamable: bool = False

    details: dict[str, str] = field(default_factory=dict)   # facts for the device sheet, e.g. {"MAC": "…"}
    extra: dict[str, Any] = field(default_factory=dict)     # plugin-specific data for the plugin's own pages


@dataclass
class DeviceView:
    """A device of ComfyOS as a plugin with the permission ``"devices"`` sees it.

    It can come from any plugin. The view holds what the user sees too – name, room, kind,
    state and capabilities – and never credentials or data of the plugin behind it.
    """

    id: str                                      # id in ComfyOS; pass it to PluginContext.command
    name: str                                    # the name the user gave the device
    kind: str                                    # one of DEVICE_KINDS
    plugin: str                                  # id of the plugin the device comes from
    room_id: str | None = None
    room: str | None = None                      # name of the room, None = not assigned
    online: bool = True
    locked: bool = False                         # the user locked it: PluginContext.command raises DeviceLocked
    controllable: bool = False                   # some plugin accepts commands for it
    state: dict[str, Any] = field(default_factory=dict)             # current values by state key
    capabilities: list[Capability] = field(default_factory=list)
    light: LightState | None = None              # only for dimmable lamps
    media: MediaState | None = None              # only for speakers


@dataclass(frozen=True)
class SceneView:
    """A lighting scene of ComfyOS as a plugin with the permission ``"scenes"`` sees it."""

    id: str
    name: str
    rooms: tuple[str, ...] = ()                  # ids of the rooms the scene is assigned to (empty = not a house scene)
    active: bool = False                         # the scene is running in at least one room


@dataclass
class ScenePalette:
    """A lighting scene a hub offers for import into ComfyOS (colours only, no fixed lamp list)."""

    id: str
    name: str
    palette: list[dict[str, Any]]                # [{"c": "#rrggbb", "b": 80}, {"ct": 370, "b": 100}], at most nine entries
    groups: list[str] = field(default_factory=list)   # rooms or zones the scene belongs to on the hub (information only)
    dynamic: bool = False                        # the colours keep rotating
    image_id: str | None = None                  # preview image, fetched with Plugin.scene_image


# ---------------------------------------------------------------------------
# Heating plans (for plugins that keep them in step with another system)
# ---------------------------------------------------------------------------
# ComfyOS owns the heating routines: each routine has a key, a name and a weekly schedule.
# A schedule in wire form is  {day: {room id: {"HHMM": set point}}}  with day "0" (Monday)
# to "6" (Sunday) and a set point that is a number ("21", "16.5") or a level name
# ("COMFORT", "ON", "ECO", "OFF").

WeeklySchedule = dict[str, dict[str, dict[str, str]]]


@dataclass
class HeatingChanges:
    """Changes made in ComfyOS that the other system does not know yet."""

    deleted: list[str] = field(default_factory=list)     # keys of routines deleted in ComfyOS
    created: list[str] = field(default_factory=list)     # local keys of routines created in ComfyOS
    updated: list[str] = field(default_factory=list)     # keys of routines whose schedule changed
    renamed: list[str] = field(default_factory=list)     # keys of routines with a new name
    activate: str | None = None                          # key of the routine that was activated in ComfyOS


@dataclass
class RemoteRoutine:
    """A heating routine as the other system has it."""

    key: str                                             # key in the other system
    name: str
    schedule: WeeklySchedule = field(default_factory=dict)
    last_run: Any = None                                 # when the other system last executed it, if it tells


@dataclass
class HomeMode:
    """At home or away, and the heating routine that belongs to each state."""

    state: str = "PRESENT"                               # "PRESENT" or "AWAY"
    state_changed: bool = False                          # changed in ComfyOS, not sent yet
    present_routine: str | None = None                   # key of the routine for "at home"
    away_routine: str | None = None                      # key of the routine for "away"
    mapping_changed: bool = False                        # the two routines were re-assigned in ComfyOS, not sent yet


class HeatingPlans(Protocol):
    """The heating routines of ComfyOS, as a syncing plugin sees them (``PluginContext.heating``).

    Typical round: send what :meth:`changes` lists and confirm each item with :meth:`done`
    or :meth:`created`; then fetch the other system's routines and hand them to :meth:`merge`.
    Changes that are not confirmed stay pending and are never overwritten by a merge.
    """

    def changes(self) -> HeatingChanges:
        """Return what still has to be transferred: deleted, created, updated and renamed routines, and a pending
        activation."""
        ...

    def routine_name(self, key: str) -> str | None:
        """Return the display name of a routine, or ``None`` if it does not exist."""
        ...

    def schedule(self, key: str) -> WeeklySchedule | None:
        """Return the weekly schedule of a routine in write form, or ``None`` if it does not exist."""
        ...

    def is_local(self, key: str) -> bool:
        """Return whether the routine exists only in ComfyOS and has not been created remotely yet."""
        ...

    def done(self, key: str, change: str) -> None:
        """Confirm one sent change: ``"deleted"``, ``"updated"``, ``"renamed"`` or ``"activate"``."""

    def created(self, local_key: str, remote_key: str) -> None:
        """A routine created in ComfyOS now exists in the other system under ``remote_key``."""

    def merge(self, routines: Sequence[RemoteRoutine], active_key: str | None, levels: Mapping[str, float],
              room_names: Mapping[str, str]) -> dict[str, int]:
        """Take over the other system's routines; returns ``{"added", "updated", "removed"}``.

        ``levels`` maps level names to temperatures, ``room_names`` room ids to names.
        """

    def touch(self) -> None:
        """Note that a round without merge finished just now."""

    def home(self) -> HomeMode:
        """Return the home/away state, the routines mapped to it and what of that is still pending."""
        ...

    def home_done(self, state: bool = True, mapping: bool = False) -> None:
        """Confirm that the home state and/or the routine mapping were sent."""

    def merge_home(self, state: str | None, present_routine: str | None, away_routine: str | None) -> None:
        """Take over home state and mapping of the other system (pending local changes win)."""


# ---------------------------------------------------------------------------
# What the core hands to a plugin
# ---------------------------------------------------------------------------

class KeyValueStore(Protocol):
    """Small persistent store that belongs to one plugin (settings or secrets)."""

    def get(self, key: str, default: Any = None) -> Any:
        """Return the stored value for ``key``, or ``default`` if nothing is stored."""
        ...

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``."""
        ...

    def delete(self, key: str) -> None:
        """Remove ``key``; a missing key is not an error."""
        ...


class Helper(Protocol):
    """A helper program the plugin runs next to ComfyOS (``PluginContext.start_helper``).

    Plugin and helper exchange JSON objects, one per line, over the helper's stdin and stdout.
    ComfyOS restarts a helper that ends, with a growing pause – so a helper should announce
    itself with a first message after every start, and the plugin answers with what it needs.
    """

    running: bool                     # the process is alive right now (False between an exit and the restart)

    async def send(self, message: Mapping[str, Any]) -> None:
        """Send one JSON object to the helper. Raises :class:`PluginError` while it is not running."""

    def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Iterate over the JSON objects the helper prints, across restarts, until :meth:`stop`.

        Everything that arrives here is foreign input: check type and limits of every field.
        """
        ...

    async def stop(self) -> None:
        """End the helper for good. ComfyOS also does this when the plugin stops."""


#: An address of a datagram: ``(host, port)``. The host is always an IP address, never a name.
DatagramAddress = tuple[str, int]
#: Signature of a callback for ``Datagram.on_datagram``: ``(data, sender)``.
DatagramCallback = Callable[[bytes, DatagramAddress], None]


class Datagram(Protocol):
    """A UDP socket inside the home network (``PluginContext.open_datagram``).

    ComfyOS drops datagrams from senders outside the home network before the plugin sees
    them, refuses to send anywhere else, and limits size and rate. Close it when done.
    """

    def send(self, data: bytes, address: DatagramAddress) -> None:
        """Send one datagram. Raises :class:`PluginError` when the address is not an IP address
        in the home network, the datagram is too large or the plugin sends too fast."""

    def on_datagram(self, callback: DatagramCallback) -> None:
        """Call ``callback(data, sender)`` for every datagram that arrives. Everything that
        arrives is foreign input. The callback runs inside the event loop and must return quickly."""

    def close(self) -> None:
        """Close the socket; a second call is not an error."""


@dataclass(frozen=True)
class SsdpAnswer:
    """One device that answered an SSDP search (``PluginContext.discover``)."""

    host: str                                    # IP address the answer came from (always in the home network)
    location: str                                # URL of the device description; its host is ``host``
    usn: str = ""                                # unique service name, e.g. "uuid:…::urn:schemas-upnp-org:device:…"
    search_target: str = ""                      # the "ST" header
    server: str = ""                             # the "SERVER" header (system and firmware, as the device words it)
    headers: Mapping[str, str] = field(default_factory=dict)    # every header, names in lower case


#: Signature of a callback for ``PluginContext.on_change``: ``(device_id, changes)``.
ChangeCallback = Callable[[str, Mapping[str, Any]], None]


class PluginContext(Protocol):
    """Everything a plugin may use from ComfyOS. Passed to ``setup(context)``."""

    plugin_id: str                    # ``id`` from the manifest
    api_version: int                  # API version of the running core
    api_revision: int                 # API revision of the running core (see the revision history above)
    data_dir: Path                    # private folder for the plugin's own files
    log: logging.Logger               # writes to the ComfyOS console
    settings: KeyValueStore           # values from the manifest's ``settings`` plus the plugin's own
    secrets: KeyValueStore            # credentials; only keys listed in the manifest's ``secrets``
    heating: HeatingPlans             # heating routines of ComfyOS (for plugins with ``syncs_heating``)

    def http(self, egress_key: str, **client_options: Any) -> Any:
        """Return an ``httpx.AsyncClient`` that only reaches the targets declared under
        ``egress`` → ``egress_key`` in the manifest (``scope: "lan"`` = addresses in the home network)."""

    async def open_stream(self, egress_key: str, host: str, port: int, *, tls: bool = False,
                          verify: bool = True, timeout: float = 5.0) -> tuple[Any, Any]:
        """Open a TCP connection and return ``(asyncio.StreamReader, asyncio.StreamWriter)``.

        For protocols that are not HTTP (MQTT, raw device protocols). The target must be
        declared under ``egress`` → ``egress_key`` like for :meth:`http`: with ``scope: "lan"``
        only addresses in the home network are reached. ``tls=True`` encrypts the connection;
        ``verify=False`` accepts a self-signed certificate and is honoured in the home network only.

        Raises :class:`ConnectionRefused` when the host answers but the port is closed,
        :class:`DeviceUnreachable` when nothing answers in ``timeout`` seconds and
        :class:`PluginError` when the target is not allowed. Close the writer when done.
        """

    async def open_datagram(self, egress_key: str, port: int = 0, *, multicast: str | None = None) -> Datagram:
        """Open a UDP socket in the home network and return it (a :class:`Datagram`).

        The manifest must declare the target under ``egress`` → ``egress_key`` with
        ``"scope": "lan"`` and ``"udp": true``; the tile of the plugin then says that it uses UDP.
        ``port`` is the local port (0 = any free one). ``multicast`` joins a group; allowed are
        the groups of SSDP (``239.255.255.250``) and mDNS (``224.0.0.251``, ``ff02::fb``).
        Raises :class:`PluginError` when that is not allowed or the port is taken.
        """

    async def discover(self, egress_key: str, protocol: str, search_target: str,
                       timeout: float = 3.0) -> list[SsdpAnswer]:
        """Search the home network for devices and return those that answered.

        ``protocol`` is ``"ssdp"`` (the only one so far); ``search_target`` is the SSDP search
        target, e.g. ``"urn:schemas-upnp-org:device:ZonePlayer:1"``. Answers from outside the
        home network, for another target or with a description elsewhere are dropped. Needs
        the same manifest entry as :meth:`open_datagram`. ``timeout`` is limited to 0.5–5 s.
        """

    def is_local_host(self, host: str) -> bool:
        """True if ``host`` is an address or name inside the home network.

        Check this before sending credentials anywhere the user typed in.
        """

    def device_changed(self, device_id: str, changes: Mapping[str, Any]) -> None:
        """Report new values of one device at once (open pages and flows react immediately).

        ``changes`` uses the same keys as ``Device.state``; ``online``, ``light`` and ``media``
        (a :class:`MediaState`) are allowed too.
        """

    def button_pressed(self, device_id: str, button: str, press: str = PRESS_SHORT) -> None:
        """Report that a button of one of the plugin's devices was pressed (flows with the trigger "Taster" start).

        ``button`` is the ``Button.id`` from ``Device.buttons``; ``press`` one of ``PRESS_KINDS``. A press is an
        event, not a state: report it once, when it happens – never again on the next query.
        """

    def devices_changed(self) -> None:
        """Report that devices were added or removed; open pages reload their device list."""

    def device_gone(self, device_id: str) -> None:
        """Report that one of the plugin's devices was deleted at its source for good.

        ComfyOS then forgets what it stored for that id (the user's own name, the "removed"
        mark), so a device that is paired again under the same id starts fresh.
        """

    def event(self, message: str) -> None:
        """Write a line to the event log of ComfyOS (visible to the user)."""

    def hints(self, kind: str) -> list[dict[str, Any]]:
        """Hints other plugins publish with :meth:`Plugin.hints`, e.g. addresses of hubs they know."""

    async def switch(self, device_id: str, state: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Switch one of the plugin's own devices through the core.

        Use this in the plugin's own routes instead of calling the hub directly: the core
        checks the device lock first, then calls :meth:`Plugin.command`.
        """

    # -- helper programs (manifest: "helper": {"desc": "…"}) ---------------------

    def start_helper(self, program: str | Path, args: Sequence[str] = (), *,
                     env: Mapping[str, str] | None = None) -> Helper:
        """Start the plugin's helper program and return it. One helper per plugin.

        ``program`` must lie inside the plugin's folder or its ``data_dir`` (symlinks are
        resolved first). It is started without a shell, with ``data_dir`` as working directory
        and an empty environment except ``HOME`` (= ``data_dir``), ``LANG`` and the variables
        in ``env``. A helper that prints a line longer than 1 MiB or more than 1000 lines a
        second is ended. What it prints to stderr is counted and discarded.

        The helper must exit when its stdin closes. Raises :class:`PluginError` when the
        manifest does not declare a helper or the program is not allowed.
        """

    # -- everything ComfyOS controls (manifest: "access": […]) --------------------

    async def all_devices(self) -> list[DeviceView]:
        """Return every device of ComfyOS, whatever plugin it comes from (permission ``"devices"``)."""

    def on_change(self, callback: ChangeCallback) -> None:
        """Call ``callback(device_id, changes)`` whenever a device changes (permission ``"devices"``).

        ``changes`` holds the new values by state key; ``online``, ``locked``, ``light``
        (a :class:`LightState`) and ``media`` (a :class:`MediaState`) can be among them. An empty ``device_id`` means that something
        else changed – the device list, a name, a room, the scenes, the home state: read again.
        The callback runs inside the event loop and must return quickly.
        """

    async def command(self, device_id: str, state: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Switch any device of ComfyOS the way the user would (permission ``"devices"``).

        The device lock and the event log apply as everywhere. Raises :class:`DeviceLocked`
        for a locked device, :class:`DeviceUnreachable` when it does not answer and
        :class:`PluginError` when it cannot do what was asked.
        """

    async def room_lights(self, room_id: str | None, on: bool, brightness: float | None = None) -> None:
        """Switch all lamps of a room – or of the whole home with ``room_id=None`` – on or off,
        optionally at ``brightness`` per cent (permission ``"rooms"``). Locked lamps are skipped."""

    async def scenes(self) -> list[SceneView]:
        """Return the lighting scenes of ComfyOS (permission ``"scenes"``)."""

    async def house_scene(self, scene_id: str, active: bool = True) -> None:
        """Start a scene in every room it is assigned to, or end it there and restore the light
        from before (permission ``"scenes"``). Raises :class:`PluginError` when that is not possible."""

    async def home_state(self) -> str:
        """Return ``"PRESENT"`` (at home) or ``"AWAY"`` (permission ``"home"``)."""

    async def home(self, state: str) -> None:
        """Set ``"PRESENT"`` or ``"AWAY"``; the heating routine mapped to it becomes active (permission ``"home"``)."""

    def live_audio_url(self, host: str) -> str:
        """Return the address under which the device at ``host`` fetches the sound a browser sends right now.

        It is an endless WAV stream (16 bit, stereo, 44.1 or 48 kHz) served over plain HTTP without login,
        only to the home network and only while a transmission runs. A device that can play it declares the
        writable capability ``STATE_LIVE_AUDIO`` (type ``"bool"``, ``readable=False``); ComfyOS then offers
        the button on its tile and sends ``{"live_audio": True}`` to start and ``{"live_audio": False}`` to stop.
        Raises :class:`PluginError` when no transmission runs or ``host`` is not in the home network.
        """


# ---------------------------------------------------------------------------
# The plugin itself
# ---------------------------------------------------------------------------

class Plugin:
    """Base class of every plugin. Override what the plugin supports.

    Life cycle: ``setup(context)`` creates the plugin, ``start()`` connects it, ``devices()``
    is called every few seconds while pages are open, ``stop()`` ends it. An exception in any
    of these marks the plugin as failed; ComfyOS keeps running.
    """

    #: Seconds ComfyOS waits between two commands to this plugin (hubs with a rate limit).
    command_interval: float = 0.0
    #: Set by ComfyOS before each ``devices()`` call: how old (in seconds) an answer from a cache
    #: may be. None = the plugin decides, 0 = ask the hub now.
    max_device_age: float | None = None
    #: True for a hub that keeps its own list of rooms. ComfyOS then writes rooms and room
    #: assignments of its floor plan to that list (``rooms``, ``create_room`` … ``move_device``).
    manages_rooms: bool = False
    #: True when the plugin keeps the heating routines of ComfyOS in step with another system
    #: (``heating_status``, ``sync_heating``; the routines are in ``PluginContext.heating``).
    syncs_heating: bool = False

    def __init__(self, context: PluginContext) -> None:
        """Keep the context; subclasses call ``super().__init__(context)`` first."""
        self.context = context

    async def start(self) -> None:
        """Connect to the hub and start background work. Called once after ``setup``."""

    async def stop(self) -> None:
        """Close connections and cancel background work."""

    async def devices(self) -> list[Device]:
        """Return all devices the plugin currently knows.

        Raise :class:`DeviceUnreachable` when the hub cannot be asked; ComfyOS then keeps
        what other plugins report and tries again later.
        """
        return []

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
        """Apply ``state`` to a device, e.g. ``{"on": True, "brightness": 60}``.

        ``source`` says who asked ("command" = user, "flow", "scene" …). Return the values
        the device has afterwards if they are known, otherwise None. Raise
        :class:`PluginError` when the device cannot do what was asked.
        """
        raise PluginError("Dieses Gerät lässt sich nicht schalten.")

    def status(self) -> dict[str, Any]:
        """Describe the connection for the settings page.

        Keys the core understands: ``configured`` (bool), ``connected`` (bool),
        ``live`` (bool, changes show up within seconds), ``message`` (str) and ``egress``:
        ``{egress key: [state, text]}`` says for the manifest's internet targets whether they
        are used right now (state ``"on"``, ``"off"`` or ``"on_demand"``; text for the user).
        """
        return {"configured": True, "connected": True}

    def routes(self) -> Any | None:
        """Return a ``fastapi.APIRouter`` with the plugin's own endpoints, or None.

        The routes are mounted under ``/api/plugins/<id>/`` and require a signed-in user.
        """
        return None

    def link_prefixes(self) -> list[str]:
        """Prefixes of link keys this plugin is responsible for.

        If another plugin relays a device with such a key and this plugin does not report
        it, the device no longer exists on the hub and is marked as missing.
        """
        return []

    def hints(self) -> list[dict[str, Any]]:
        """Facts that help other plugins, e.g. ``{"kind": "hue-bridge", "host": "192.168.1.20"}``."""
        return []

    async def rename(self, device_id: str, name: str) -> str | None:
        """Store ``name`` on the hub and return the name it kept (hubs may shorten it).

        Return None when the hub has no names. ComfyOS is the master of device names.
        """
        return None

    def clean_name(self, name: str) -> str:
        """Return ``name`` the way the hub would store it (used to detect differing names)."""
        return " ".join(str(name or "").split())

    async def scenes(self) -> list[ScenePalette]:
        """Return lighting scenes of the hub that the user can import."""
        return []

    async def scene_image(self, image_id: str) -> tuple[bytes, str] | None:
        """Return ``(bytes, mime type)`` of a scene preview image, or None."""
        return None

    async def media_image(self, image_id: str) -> tuple[bytes, str] | None:
        """Return ``(bytes, mime type)`` of a cover picture named in ``MediaState.image_id``, or None.

        The browser never loads pictures from elsewhere; ComfyOS fetches them through this method.
        """
        return None

    # -- rooms (only for hubs with ``manages_rooms = True``) -----------------

    async def rooms(self) -> list[Room]:
        """Return the hub's room list, empty rooms included."""
        return []

    async def create_room(self, name: str) -> str:
        """Create a room on the hub and return its id."""
        raise PluginError("Hier lassen sich keine Räume anlegen.")

    async def rename_room(self, room_id: str, name: str) -> None:
        """Give a room of the hub a new name."""
        raise PluginError("Hier lassen sich keine Räume umbenennen.")

    async def delete_room(self, room_id: str) -> None:
        """Delete an empty room from the hub. Raise :class:`PluginError` if it still holds devices."""
        raise PluginError("Hier lassen sich keine Räume löschen.")

    async def move_device(self, device_id: str, room_id: str) -> None:
        """Put one of the plugin's devices into a room of the hub."""
        raise PluginError("Hier lassen sich Geräte keinem Raum zuordnen.")

    # -- heating plans (only with ``syncs_heating = True``) ------------------

    def heating_status(self) -> dict[str, Any]:
        """Describe the sync: ``configured``, ``ok`` (None = never ran), ``last_sync``,
        ``last_error``, ``running`` and ``interval`` (seconds between two rounds)."""
        return {"configured": False}

    async def sync_heating(self, pull: bool = True, force: bool = False) -> dict[str, Any]:
        """Run one round and return :meth:`heating_status`.

        Always send pending local changes; with ``pull`` also fetch the other system's
        routines. Without ``force`` the plugin may skip a round that follows the last
        one too closely. Never raise for a failed round – report it in the status.
        """
        return self.heating_status()


# ---------------------------------------------------------------------------
# Colour helpers (pure functions)
# ---------------------------------------------------------------------------
# ComfyOS stores colours as "#rrggbb" and white tones in mirek (1 000 000 / kelvin).
# Lamps usually expect CIE xy coordinates.

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_WHITE_RANGE = (153, 500)    # mirek: 6500 K … 2000 K
WARM_WHITE = 370                    # mirek, about 2700 K


def _to_linear(channel: float) -> float:
    """Convert one sRGB channel (0–1) to linear light."""
    return ((channel + 0.055) / 1.055) ** 2.4 if channel > 0.04045 else channel / 12.92


def _to_gamma(channel: float) -> float:
    """Convert one linear-light channel (0–1) back to sRGB."""
    return 12.92 * channel if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """'#ff8000' -> (255, 128, 0). Raises ValueError for anything else."""
    if not isinstance(color, str) or not HEX_COLOR.match(color):
        raise ValueError("Farbe muss als #rrggbb angegeben sein.")
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def rgb_to_hex(red: float, green: float, blue: float) -> str:
    """(255, 128, 0) -> '#ff8000'. Values are rounded and clamped to 0–255."""
    return "#" + "".join(f"{max(0, min(255, int(round(value)))):02x}" for value in (red, green, blue))


def hex_to_xy(color: str) -> tuple[float, float]:
    """sRGB colour -> CIE xy (wide-gamut matrix, as Philips recommends for Hue)."""
    red, green, blue = (_to_linear(channel / 255) for channel in hex_to_rgb(color))
    x = red * 0.664511 + green * 0.154324 + blue * 0.162028
    y = red * 0.283881 + green * 0.668433 + blue * 0.047685
    z = red * 0.000088 + green * 0.072310 + blue * 0.986039
    total = x + y + z
    if total <= 0:
        return 0.3127, 0.3290       # black has no colour: use the white point
    return round(x / total, 4), round(y / total, 4)


def xy_to_hex(x: float, y: float) -> str:
    """CIE xy -> sRGB colour at full brightness (for display and for imported scenes)."""
    x, y = float(x), max(float(y), 1e-4)
    big_x, big_y, big_z = x / y, 1.0, (1 - x - y) / y
    red = big_x * 1.656492 - big_y * 0.354851 - big_z * 0.255038
    green = -big_x * 0.707196 + big_y * 1.655397 + big_z * 0.036152
    blue = big_x * 0.051713 - big_y * 0.121364 + big_z * 1.011530
    red, green, blue = (max(0.0, channel) for channel in (red, green, blue))
    brightest = max(red, green, blue) or 1.0
    return rgb_to_hex(*(_to_gamma(channel / brightest) * 255 for channel in (red, green, blue)))


def mirek_to_hex(mirek: float) -> str:
    """White tone -> display colour (approximation by Tanner Helland)."""
    hundreds_of_kelvin = 1_000_000 / max(100.0, float(mirek)) / 100
    if hundreds_of_kelvin <= 66:
        red = 255.0
        green = 99.4708025861 * math.log(hundreds_of_kelvin) - 161.1195681661
    else:
        red = 329.698727446 * (hundreds_of_kelvin - 60) ** -0.1332047592
        green = 288.1221695283 * (hundreds_of_kelvin - 60) ** -0.0755148492
    if hundreds_of_kelvin >= 66:
        blue = 255.0
    elif hundreds_of_kelvin <= 19:
        blue = 0.0
    else:
        blue = 138.5177312231 * math.log(hundreds_of_kelvin - 10) - 305.0447927307
    return rgb_to_hex(red, green, blue)


def xy_to_mirek(x: float, y: float, white_range: tuple[int, int] = DEFAULT_WHITE_RANGE) -> int:
    """Colour -> nearest white tone inside ``white_range``.

    Close to the white line the result is exact (McCamy's formula). Saturated colours are
    mapped by their red and blue share: red and orange become warm, blue becomes cool.
    """
    x, y = float(x), float(y)
    coolest, warmest = white_range
    near_white_line = 0.25 <= x <= 0.56 and abs(y - (-3.0 * x * x + 2.87 * x - 0.275)) <= 0.04
    if near_white_line:
        n = (x - 0.3320) / (0.1858 - y)
        kelvin = 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33
        mirek = 1_000_000 / kelvin if kelvin > 0 else warmest
    else:
        red, _, blue = hex_to_rgb(xy_to_hex(x, y))
        warmth = max(0.0, min(1.0, 0.5 + (red - blue) / 510))
        mirek = coolest + (warmest - coolest) * warmth
    return int(max(coolest, min(warmest, round(mirek))))


def kelvin_to_mirek(kelvin: float) -> int:
    """2700 K -> 370 mirek. The input is clamped to 1000–10000 K."""
    return int(round(1_000_000 / max(1000.0, min(10000.0, float(kelvin)))))
