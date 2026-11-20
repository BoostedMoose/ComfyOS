"""Helpers for testing a plugin without a running ComfyOS.

``FakeContext`` stands in for the context ComfyOS passes to ``setup(context)``. It keeps
settings and secrets in memory, records what the plugin reports, and hands out HTTP
clients that talk to a handler function instead of the network::

    import httpx
    from testing import FakeContext

    def bridge(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    context = FakeContext("hue", http_handler=bridge, secrets=["app_key"])
    plugin = setup(context)

Plugins that speak a TCP protocol get their connection from ``context.open_stream``.
In tests ``stream_handler`` supplies the other end; ``memory_stream_pair`` builds two
connected ends that live only in memory::

    async def fake_device(host: str, port: int) -> tuple[asyncio.StreamReader, MemoryWriter]:
        plugin_end, device_end = memory_stream_pair()
        asyncio.create_task(serve(*device_end))     # your fake device reads and answers here
        return plugin_end

    context = FakeContext("my_plugin", stream_handler=fake_device)

Plugins that run a helper program (``context.start_helper``) get a ``FakeHelper`` instead of a
process: ``feed()`` plays what the helper would print, ``sent`` records what the plugin sends::

    context = FakeContext("my_plugin", helper=True, access=("devices",))
    plugin = setup(context)
    await plugin.start()
    context.helper.feed({"type": "ready"})

Plugins with a permission (``"access"`` in the manifest) see the devices in ``context.device_views``;
``context.commands`` records what they switch, and ``context.change(...)`` plays a change.

Plugins that use UDP in the home network (``"udp": true`` in the manifest) get a ``FakeDatagram``
from ``context.open_datagram``: ``sent`` records what the plugin sends, ``feed()`` plays a datagram
that arrives. ``context.discover`` returns what the test put into ``context.ssdp_answers``::

    context = FakeContext("my_plugin", udp=True)
    context.ssdp_answers = [SsdpAnswer(host="192.0.2.30", location="http://192.0.2.30:1400/description.xml")]

Run plugin tests with ``public/sdk`` and ``public/plugins`` on the Python path
(``tests/run_all.py`` of ComfyOS does that for built-in plugins).
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Sequence

import httpx

from plugin_api import (ACCESS_KINDS, API_REVISION, API_VERSION, PRESS_KINDS, PRESS_SHORT, ConnectionRefused, DeviceLocked,
                        DeviceUnreachable, DeviceView, HeatingChanges, HomeMode, PluginError, RemoteRoutine, SceneView,
                        SsdpAnswer)

StreamHandler = Callable[[str, int], Awaitable[tuple[Any, Any]]]


class MemoryWriter:
    """Write end of an in-memory connection; everything written arrives at the peer's reader."""

    def __init__(self, peer_reader: asyncio.StreamReader) -> None:
        """Write into the reader of the other side of the connection."""
        self._peer_reader = peer_reader
        self._closed = False

    def write(self, data: bytes) -> None:
        """Hand the bytes to the peer's reader; ignored once closed."""
        if not self._closed:
            self._peer_reader.feed_data(data)

    async def drain(self) -> None:
        """Give the peer a chance to run, like a real socket would."""
        await asyncio.sleep(0)          # let the peer run, like a real socket would

    def close(self) -> None:
        """Close the connection: the peer reads end-of-file."""
        if not self._closed:
            self._closed = True
            self._peer_reader.feed_eof()

    def is_closing(self) -> bool:
        """Return whether the connection has been closed."""
        return self._closed

    async def wait_closed(self) -> None:
        """Return once the connection is closed (immediately, there is no real socket)."""
        await asyncio.sleep(0)


def memory_stream_pair() -> tuple[tuple[asyncio.StreamReader, MemoryWriter], tuple[asyncio.StreamReader, MemoryWriter]]:
    """Return two connected ``(reader, writer)`` ends. Call it inside a running event loop."""
    first_reader, second_reader = asyncio.StreamReader(), asyncio.StreamReader()
    return (first_reader, MemoryWriter(second_reader)), (second_reader, MemoryWriter(first_reader))


class MemoryStore:
    """In-memory replacement for ``context.settings`` and ``context.secrets``."""

    def __init__(self, allowed_keys: list[str] | None = None) -> None:
        """Create an empty store. ``allowed_keys`` restricts it to these keys, like the secrets declared in a
        manifest; ``None`` allows any key."""
        self.values: dict[str, Any] = {}
        self._allowed_keys = allowed_keys      # None = any key (settings); a list = only these (secrets)

    def _check(self, key: str) -> None:
        """Raise ``PluginError`` for a key the manifest does not declare - the same error the real secret store
        raises."""
        if self._allowed_keys is not None and key not in self._allowed_keys:
            raise PluginError(f"Geheimnis „{key}“ steht nicht im Manifest unter „secrets“.")

    def get(self, key: str, default: Any = None) -> Any:
        """Return the stored value for ``key``, or ``default``."""
        self._check(key)
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``."""
        self._check(key)
        self.values[key] = value

    def delete(self, key: str) -> None:
        """Remove ``key``; a missing key is not an error."""
        self._check(key)
        self.values.pop(key, None)


class FakeHeatingPlans:
    """Heating routines in memory (``PluginContext.heating``) for plugins that sync them.

    Fill ``routines`` (``{key: {"name": …, "schedule": …}}``), ``pending`` and ``home_mode`` before the
    test; inspect ``confirmed``, ``rekeyed``, ``merged`` and ``home_merged`` afterwards.
    """

    def __init__(self) -> None:
        """Start without routines or pending changes; every call the plugin makes is recorded in a list for
        assertions."""
        self.routines: dict[str, dict[str, Any]] = {}
        self.pending = HeatingChanges()
        self.home_mode = HomeMode()
        self.confirmed: list[tuple[str, str]] = []              # every done(key, change)
        self.rekeyed: list[tuple[str, str]] = []                # every created(local_key, remote_key)
        self.merged: list[dict[str, Any]] = []                  # every merge(...) call
        self.home_merged: list[tuple[Any, Any, Any]] = []       # every merge_home(...) call
        self.touched = 0

    def changes(self) -> HeatingChanges:
        """Return the pending changes the test has set up."""
        return self.pending

    def routine_name(self, key: str) -> str | None:
        """Return the name of a routine, or ``None`` if the test has not defined it."""
        return (self.routines.get(key) or {}).get("name")

    def schedule(self, key: str) -> dict[str, Any] | None:
        """Return the weekly schedule of a routine, or ``None``."""
        return (self.routines.get(key) or {}).get("schedule")

    def is_local(self, key: str) -> bool:
        """Return whether the key belongs to a routine that exists only in ComfyOS (keys starting with ``local-``)."""
        return str(key).startswith("local-")

    def done(self, key: str, change: str) -> None:
        """Record that a change was transferred and take it off the pending list."""
        self.confirmed.append((key, change))
        if change == "activate":
            self.pending.activate = None
        else:
            listed = getattr(self.pending, change)
            if key in listed:
                listed.remove(key)

    def created(self, local_key: str, remote_key: str) -> None:
        """Record that a local routine received its remote key and rename it accordingly."""
        self.rekeyed.append((local_key, remote_key))
        if local_key in self.pending.created:
            self.pending.created.remove(local_key)
        if local_key in self.routines:
            self.routines[remote_key] = self.routines.pop(local_key)

    def merge(self, routines: Sequence[RemoteRoutine], active_key: str | None, levels: Mapping[str, float],
              room_names: Mapping[str, str]) -> dict[str, int]:
        """Record the routines the plugin read from the remote system and report them all as added."""
        self.merged.append({"routines": list(routines), "active": active_key, "levels": dict(levels),
                            "room_names": dict(room_names)})
        return {"added": len(routines), "updated": 0, "removed": 0}

    def touch(self) -> None:
        """Count that a sync finished."""
        self.touched += 1

    def home(self) -> HomeMode:
        """Return the home/away state the test has set up."""
        return self.home_mode

    def home_done(self, state: bool = True, mapping: bool = False) -> None:
        """Mark the home state and/or the routine mapping as transferred."""
        if state:
            self.home_mode.state_changed = False
        if mapping:
            self.home_mode.mapping_changed = False

    def merge_home(self, state: str | None, present_routine: str | None, away_routine: str | None) -> None:
        """Record the home state and mapping the plugin read from the remote system."""
        self.home_merged.append((state, present_routine, away_routine))


class FakeHelper:
    """Stands in for a helper program (``PluginContext.start_helper``): no process, only two lists.

    ``sent`` holds every message the plugin sent; ``feed()`` hands the plugin a message as if the
    helper had printed it. Set ``running = False`` to play a helper that has just crashed.
    """

    def __init__(self, program: str = "", args: Sequence[str] = (), env: Mapping[str, str] | None = None) -> None:
        """Remember how the plugin wanted the helper started."""
        self.program = str(program)
        self.args = list(args)
        self.env = dict(env or {})
        self.running = True
        self.stopped = False
        self.sent: list[dict[str, Any]] = []
        self._incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def send(self, message: Mapping[str, Any]) -> None:
        """Record a message the plugin sends. Raises ``PluginError`` while the helper is not running."""
        if not self.running:
            raise PluginError("Das Beiprogramm läuft gerade nicht.")
        self.sent.append(dict(message))

    def feed(self, message: Mapping[str, Any]) -> None:
        """Hand the plugin a message as if the helper had printed it."""
        self._incoming.put_nowait(dict(message))

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield the fed messages until ``stop()``."""
        while True:
            message = await self._incoming.get()
            if message is None:
                return
            yield message

    async def stop(self) -> None:
        """End the helper: ``messages()`` finishes."""
        self.running = False
        self.stopped = True
        self._incoming.put_nowait(None)

    def sent_of(self, message_type: str) -> list[dict[str, Any]]:
        """Return the sent messages with this ``type``."""
        return [message for message in self.sent if message.get("type") == message_type]


class FakeDatagram:
    """Stands in for a UDP socket (``PluginContext.open_datagram``): nothing leaves the machine.

    ``sent`` holds every datagram the plugin sent as ``(data, (host, port))``; ``feed()`` hands the
    plugin a datagram as if it had arrived from ``sender``.
    """

    def __init__(self, port: int = 0, multicast: str | None = None,
                 is_local: Callable[[str], bool] = lambda host: True) -> None:
        """Remember how the plugin opened the socket; ``is_local`` decides which addresses count as home network."""
        self.port = port
        self.multicast = multicast
        self.closed = False
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self._callbacks: list[Callable[[bytes, tuple[str, int]], None]] = []
        self._is_local = is_local

    def send(self, data: bytes, address: tuple[str, int]) -> None:
        """Record a datagram. Like ComfyOS it refuses a closed socket and addresses outside the home network."""
        if self.closed:
            raise PluginError("Der UDP-Socket ist geschlossen.")
        if not self._is_local(str(address[0])):
            raise PluginError("UDP geht nur an IP-Adressen im Heimnetz.")
        self.sent.append((bytes(data), (str(address[0]), int(address[1]))))

    def on_datagram(self, callback: Callable[[bytes, tuple[str, int]], None]) -> None:
        """Register a callback; ``feed()`` calls it."""
        self._callbacks.append(callback)

    def feed(self, data: bytes, sender: tuple[str, int]) -> None:
        """Hand the plugin a datagram. Like ComfyOS it drops senders outside the home network."""
        if self.closed or not self._is_local(str(sender[0])):
            return
        for callback in list(self._callbacks):
            callback(bytes(data), sender)

    def close(self) -> None:
        """Close the socket; a second call is not an error."""
        self.closed = True


class FakeContext:
    """A ``PluginContext`` for tests. Inspect ``changes``, ``reloads`` and ``events`` afterwards."""

    def __init__(self, plugin_id: str, http_handler: Callable[[httpx.Request], httpx.Response] | None = None,
                 secrets: list[str] | None = None, local_hosts: tuple[str, ...] = ("192.0.2.",),
                 stream_handler: StreamHandler | None = None, helper: bool = False,
                 access: Sequence[str] = (), udp: bool = False) -> None:
        """Create a context for one plugin under test. ``http_handler`` answers HTTP requests, ``stream_handler``
        plays the peer of TCP connections, ``secrets`` are the keys the manifest declares, and hosts starting with
        ``local_hosts`` count as home network. ``helper=True`` stands for ``"helper"`` in the manifest, ``access``
        for its ``"access"`` list and ``udp=True`` for ``"udp": true`` on a home-network target. Everything the
        plugin reports is recorded in lists."""
        self.plugin_id = plugin_id
        self.api_version = API_VERSION
        self.api_revision = API_REVISION
        self.data_dir = Path(tempfile.mkdtemp(prefix=f"plugin-{plugin_id}-"))
        self.log = logging.getLogger(f"test.plugin.{plugin_id}")
        self.settings = MemoryStore()
        self.secrets = MemoryStore(allowed_keys=secrets or [])
        self.heating = FakeHeatingPlans()

        self.changes: list[tuple[str, dict[str, Any]]] = []     # every device_changed call
        self.presses: list[tuple[str, str, str]] = []           # every button_pressed call: (device id, button, press)
        self.reloads = 0                                        # how often devices_changed was called
        self.gone: list[str] = []                               # every device_gone call
        self.events: list[str] = []                             # event log lines
        self.switched: list[tuple[str, dict[str, Any]]] = []    # every switch call
        self.hint_list: list[dict[str, Any]] = []               # what other plugins would publish
        self.requests: list[httpx.Request] = []                 # every HTTP request the plugin sent
        self.streams: list[tuple[str, int, dict[str, Any]]] = []   # every open_stream call: (host, port, options)
        self.datagrams: list[FakeDatagram] = []                 # every socket the plugin opened with open_datagram
        self.live_audio_address: str | None = None              # what live_audio_url() returns; None = no transmission runs
        self.ssdp_answers: list[SsdpAnswer] = []                # what discover() returns (answers outside the home network are dropped)
        self.searches: list[tuple[str, str, float]] = []        # every discover call: (protocol, search target, timeout)

        self.helper: FakeHelper | None = None                   # the helper the plugin started last
        self.helpers: list[FakeHelper] = []                     # every helper the plugin started
        self.device_views: list[DeviceView] = []                # what all_devices() returns
        self.scene_views: list[SceneView] = []                  # what scenes() returns
        self.home_mode = "PRESENT"                              # what home_state() returns
        self.commands: list[tuple[str, dict[str, Any]]] = []    # every command() call that went through
        self.command_error: Exception | None = None             # raise this from the next command() calls
        self.room_light_calls: list[tuple[str | None, bool, float | None]] = []
        self.scene_calls: list[tuple[str, bool]] = []
        self.home_calls: list[str] = []

        self._http_handler = http_handler
        self._stream_handler = stream_handler
        self._local_hosts = local_hosts
        self._declares_helper = helper
        self._declares_udp = udp
        self._access = tuple(access)
        self._change_callbacks: list[Callable[[str, Mapping[str, Any]], None]] = []

    def http(self, egress_key: str, **client_options: Any) -> httpx.AsyncClient:
        """Return an HTTP client whose requests are recorded and answered by the test's handler - nothing leaves the
        machine. Raises ``PluginError`` if the test did not provide a handler."""
        if self._http_handler is None:
            raise PluginError(f"Ziel „{egress_key}“ steht nicht im Manifest unter „egress“.")

        def record_and_answer(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._http_handler(request)

        client_options.pop("verify", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(record_and_answer), **client_options)

    async def open_stream(self, egress_key: str, host: str, port: int, *, tls: bool = False, verify: bool = True,
                          timeout: float = 5.0) -> tuple[Any, Any]:
        """Hand out the connection ``stream_handler`` builds.

        Like ComfyOS it refuses hosts outside the home network and reports failures as
        ``ConnectionRefused`` (the handler raised ConnectionRefusedError) or ``DeviceUnreachable``.
        """
        self.streams.append((host, port, {"egress_key": egress_key, "tls": tls, "verify": verify, "timeout": timeout}))
        if not self.is_local_host(host):
            raise PluginError(f"{host} liegt nicht im Heimnetz.")
        if self._stream_handler is None:
            raise DeviceUnreachable(f"{host} antwortet nicht.")
        try:
            return await self._stream_handler(host, port)
        except ConnectionRefusedError:
            raise ConnectionRefused(f"{host}: Port {port} ist geschlossen.")
        except (OSError, asyncio.TimeoutError) as error:
            raise DeviceUnreachable(f"{host} antwortet nicht ({type(error).__name__}).")

    def is_local_host(self, host: str) -> bool:
        """Return whether the host counts as home network in this test."""
        return host.startswith(self._local_hosts) or host in ("localhost",) or host.endswith((".local", ".fritz.box"))

    def live_audio_url(self, host: str) -> str:
        """Return ``live_audio_address``. Raises ``PluginError`` while it is None or for a host outside the home network."""
        if not self.is_local_host(host):
            raise PluginError("Der Ton vom Browser geht nur an Geräte im Heimnetz.")
        if self.live_audio_address is None:
            raise PluginError("Gerade wird kein Ton vom Browser übertragen.")
        return self.live_audio_address

    def _need_udp(self, egress_key: str) -> None:
        """Raise ``PluginError`` when the test did not declare UDP – the same error ComfyOS raises."""
        if not self._declares_udp:
            raise PluginError(f"Ziel „{egress_key}“ erlaubt kein UDP (Manifest: \"scope\": \"lan\" und \"udp\": true).")

    async def open_datagram(self, egress_key: str, port: int = 0, *, multicast: str | None = None) -> FakeDatagram:
        """Return a ``FakeDatagram`` instead of opening a socket; it is also kept in ``datagrams``."""
        self._need_udp(egress_key)
        socket = FakeDatagram(port, multicast, self.is_local_host)
        self.datagrams.append(socket)
        return socket

    async def discover(self, egress_key: str, protocol: str, search_target: str, timeout: float = 3.0) -> list[SsdpAnswer]:
        """Return the answers in ``ssdp_answers`` that came from the home network and fit the search target."""
        self._need_udp(egress_key)
        if protocol != "ssdp":
            raise PluginError("Gesucht wird bisher nur per SSDP (protocol=\"ssdp\").")
        self.searches.append((protocol, search_target, timeout))
        return [answer for answer in self.ssdp_answers
                if self.is_local_host(answer.host) and answer.search_target in ("", search_target)]

    def device_changed(self, device_id: str, changes: Mapping[str, Any]) -> None:
        """Record a state change the plugin pushed."""
        self.changes.append((device_id, dict(changes)))

    def button_pressed(self, device_id: str, button: str, press: str = PRESS_SHORT) -> None:
        """Record a button press the plugin reported. ComfyOS drops a press kind it does not know; the fake raises,
        so that a test notices."""
        if press not in PRESS_KINDS:
            raise PluginError(f"Unbekannte Art des Drucks: {press}")
        self.presses.append((device_id, str(button), press))

    def devices_changed(self) -> None:
        """Count that the plugin announced a changed device list."""
        self.reloads += 1

    def device_gone(self, device_id: str) -> None:
        """Record a device the plugin reported as removed."""
        self.gone.append(device_id)

    def event(self, message: str) -> None:
        """Record a line the plugin wrote to the event log."""
        self.events.append(message)

    def hints(self, kind: str) -> list[dict[str, Any]]:
        """Return the hints of the given kind that the test has set up."""
        return [hint for hint in self.hint_list if hint.get("kind") == kind]

    async def switch(self, device_id: str, state: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Record a switch request the plugin made through the core."""
        self.switched.append((device_id, dict(state)))
        return None

    # -- helper program -----------------------------------------------------

    def start_helper(self, program: str | Path, args: Sequence[str] = (), *,
                     env: Mapping[str, str] | None = None) -> FakeHelper:
        """Return a ``FakeHelper`` instead of starting a process. Like ComfyOS it refuses when the manifest does not
        declare a helper (``helper=True``) or one is already running."""
        if not self._declares_helper:
            raise PluginError("Das Manifest erklärt kein Beiprogramm („helper“).")
        if self.helper is not None and not self.helper.stopped:
            raise PluginError("Für dieses Plugin läuft schon ein Beiprogramm.")
        self.helper = FakeHelper(str(program), args, env)
        self.helpers.append(self.helper)
        return self.helper

    # -- everything ComfyOS controls ----------------------------------------

    def _need(self, permission: str) -> None:
        """Raise ``PluginError`` when the test did not grant the permission – the same error ComfyOS raises."""
        if permission not in ACCESS_KINDS or permission not in self._access:
            raise PluginError(f"Das Manifest erlaubt das nicht („access“: {permission}).")

    async def all_devices(self) -> list[DeviceView]:
        """Return the devices the test put into ``device_views``."""
        self._need("devices")
        return list(self.device_views)

    def on_change(self, callback: Callable[[str, Mapping[str, Any]], None]) -> None:
        """Register a callback; ``change()`` calls it."""
        self._need("devices")
        self._change_callbacks.append(callback)

    def change(self, device_id: str, changes: Mapping[str, Any] | None = None) -> None:
        """Play a change of one device (or with an empty id: "read everything again") to the plugin's callbacks."""
        for callback in list(self._change_callbacks):
            callback(device_id, dict(changes or {}))

    async def command(self, device_id: str, state: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Record a command. Raises ``DeviceLocked`` for a view with ``locked=True``, ``PluginError`` for an unknown
        or not controllable device, and ``command_error`` if the test set one."""
        self._need("devices")
        view = next((candidate for candidate in self.device_views if candidate.id == device_id), None)
        if view is None:
            raise PluginError("Gerät nicht gefunden")
        if view.locked:
            raise DeviceLocked(f"{view.name} ist gesperrt.")
        if not view.controllable:
            raise PluginError(f"{view.name} lässt sich nicht schalten.")
        if self.command_error is not None:
            raise self.command_error
        self.commands.append((device_id, dict(state)))
        return None

    async def room_lights(self, room_id: str | None, on: bool, brightness: float | None = None) -> None:
        """Record a request to switch the lamps of a room (``None`` = the whole home)."""
        self._need("rooms")
        self.room_light_calls.append((room_id, bool(on), brightness))

    async def scenes(self) -> list[SceneView]:
        """Return the scenes the test put into ``scene_views``."""
        self._need("scenes")
        return list(self.scene_views)

    async def house_scene(self, scene_id: str, active: bool = True) -> None:
        """Record a request to start or end a house scene."""
        self._need("scenes")
        self.scene_calls.append((scene_id, bool(active)))

    async def home_state(self) -> str:
        """Return ``home_mode``."""
        self._need("home")
        return self.home_mode

    async def home(self, state: str) -> None:
        """Record a request to set home or away and remember the new state."""
        self._need("home")
        self.home_calls.append(state)
        self.home_mode = state
