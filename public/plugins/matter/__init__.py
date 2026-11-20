"""Matter plugin: ComfyOS as a Matter bridge and as a Matter controller.

What it does
    * **Bridge.** Devices of ComfyOS – from any plugin – appear in Alexa, Apple Home, Google Home
      and every other Matter controller. The user chooses per device what is shared; a locked
      device stays visible but cannot be switched. No skill, no account, no port forwarding:
      controllers talk to ComfyOS inside the home network.
    * **The home as devices.** Room lights, "all lights", house scenes and at home/away can be
      shared as virtual devices (home.py).
    * **Controller.** Matter devices of other makers are commissioned with the code their own
      app shows under "share" and then appear in ComfyOS as ``matter:<node>-<endpoint>``.

How it works
    Matter itself is spoken by matter.js, which runs as a helper program under Node.js
    (bridge/dist/bridge.mjs). The helper is locked in (``node --permission``): it can read
    nothing but its own file, write nothing and start nothing. Plugin and helper exchange JSON
    lines (protocol.py). The helper owns no data: matter.js stores through the plugin, which
    keeps one encrypted file (storage.py).

    The plugin – not matter.js – enforces what a controller may do: only endpoints of the share
    list exist in the helper, and every command of a controller is carried out through
    ``context.command`` of ComfyOS, where the device lock and the event log apply.

Files
    protocol.py   messages between plugin and helper: build, check
    storage.py    storage of matter.js: one encrypted file
    devices.py    ComfyOS device <-> Matter device type and values
    home.py       the home as devices
    shares.py     what is shared, permanent endpoint numbers
    runtime.py    Node.js: find, download, verify, unpack
    routes.py     endpoints of the tile
    bridge/       the helper program (JavaScript, bundled to bridge/dist/bridge.mjs)
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from plugin_api import (
    Device,
    DeviceLocked,
    DeviceUnreachable,
    DeviceView,
    Helper,
    Plugin,
    PluginContext,
    PluginError,
    SceneView,
)

from . import devices as matter_devices
from . import home, protocol, runtime
from .routes import build_router
from .shares import ShareList, share_key_of
from .storage import MatterStore, StorageError, new_key

PLUGIN_DIR = Path(__file__).resolve().parent
BUNDLE = PLUGIN_DIR / "bridge" / "dist" / "bridge.mjs"
STORE_FILE = "matter.bin"

DEFAULT_BRIDGE_PORT = 5540                      # the Matter port; never forward it to the internet
DEFAULT_CONTROLLER_PORT = 5541
VENDOR_ID = 0xFFF1                              # test vendor id – the bridge is not certified
PRODUCT_ID = 0x8000
BRIDGE_NAME = "ComfyOS"
WINDOW_SECONDS = 15 * 60                        # how long "Kopplung öffnen" announces the bridge
SYNC_DELAY_SECONDS = 0.5                        # changes are bundled: at most two updates a second per device
REFRESH_SECONDS = 60.0                          # read everything again this often, in case a change was missed
SAVE_DELAY_SECONDS = 1.0                        # storage changes are written bundled
REMOVE_HOLD_SECONDS = 20.0                      # a removed controller stays hidden this long against older reports
REQUEST_TIMEOUT_SECONDS = 12.0
PAIR_TIMEOUT_SECONDS = 150.0
INVALID_PASSCODES = (0, 11111111, 22222222, 33333333, 44444444, 55555555, 66666666, 77777777, 88888888, 99999999,
                     12345678, 87654321)

# Secrets and settings this plugin stores
SECRET_STORAGE_KEY = "storage_key"
SECRET_PASSCODE = "passcode"
SETTING_DISCRIMINATOR = "discriminator"
SETTING_SERIAL = "serial"
SETTING_BRIDGE_PORT = "bridge_port"
SETTING_CONTROLLER_PORT = "controller_port"
SETTING_ATTESTATION = "attestation"
SETTING_CONTROLLER_NAMES = "controller_names"   # the user's own names for paired controllers: {Controller.index: name}
MAX_CONTROLLER_NAME = 40

# Apple Home always joins a Matter device twice: once as the home itself and once for the iCloud
# keychain, which lets the other Apple devices of the household reach it. The two fabrics appear
# one right after the other and belong together; ComfyOS shows them as one controller.
APPLE_HOME_VENDOR = 4937
APPLE_KEYCHAIN_VENDOR = 4996
# Names of well-known controllers by the vendor id of their fabric
CONTROLLER_NAMES = {4631: "Amazon Alexa", APPLE_HOME_VENDOR: "Apple Home", APPLE_KEYCHAIN_VENDOR: "Apple Home (Schlüsselbund)",
                    24582: "Google Home", 4362: "Samsung SmartThings", VENDOR_ID: "Test-Controller"}
VIEW_STATE_KEYS = ("on", "brightness", "target", "temperature", "open", "motion", "alarm", "position", "humidity")


def new_passcode() -> int:
    """Return a random setup passcode that Matter allows (1–99999998 without the trivial ones)."""
    while True:
        passcode = secrets.randbelow(99999998) + 1
        if passcode not in INVALID_PASSCODES:
            return passcode


@dataclass(frozen=True)
class Controller:
    """One paired controller as the user sees it: one fabric, or the two fabrics of Apple Home."""

    number: int                                 # position in the list, counted from 1 without gaps
    fabrics: tuple[protocol.Fabric, ...]        # in the order of their fabric index

    @property
    def index(self) -> int:
        """The lowest fabric index: the stable key of the controller in routes and stored names."""
        return self.fabrics[0].index

    @property
    def name(self) -> str:
        """The name the controller brings along: its own label, else the maker behind its vendor id."""
        naming = next((fabric for fabric in self.fabrics if fabric.vendor != APPLE_KEYCHAIN_VENDOR), self.fabrics[0])
        return naming.label or CONTROLLER_NAMES.get(naming.vendor) or f"Controller {self.number}"


def group_controllers(fabrics: list[protocol.Fabric]) -> list[Controller]:
    """Turn the fabrics of the bridge into controllers, numbered 1, 2, 3 … in the order they were paired.

    Matter never gives the index of a removed fabric to the next one, so the fabric index
    is no number to show: after removing two controllers the next one would be "Controller 4".
    The two fabrics of Apple Home (see APPLE_HOME_VENDOR) become one controller when they
    follow each other directly.
    """
    groups: list[list[protocol.Fabric]] = []
    for fabric in sorted(fabrics, key=lambda fabric: fabric.index):
        apple_pair = {APPLE_HOME_VENDOR, APPLE_KEYCHAIN_VENDOR}
        previous = groups[-1] if groups else None
        if previous is not None and len(previous) == 1 and {previous[0].vendor, fabric.vendor} == apple_pair:
            previous.append(fabric)
        else:
            groups.append([fabric])
    return [Controller(number=position, fabrics=tuple(group)) for position, group in enumerate(groups, start=1)]


class MatterPlugin(Plugin):
    """Runs the Matter helper, feeds it the shared devices and carries out what controllers ask for."""

    def __init__(self, context: PluginContext) -> None:
        """Start idle; ``start()`` looks for Node.js and launches the helper."""
        super().__init__(context)
        self.shares = ShareList(context.settings)
        self.node_path: Path | None = None
        self.problem = ""                               # why the plugin is not working, for the tile
        self.clock: Callable[[], float] = time.time     # tests replace it to let time pass
        self._helper: Helper | None = None
        self._store: MatterStore | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._online = False                            # the helper reported `started`
        self._pairing: protocol.Pairing | None = None
        self._fabrics: list[protocol.Fabric] = []
        self._removed: dict[int, float] = {}            # fabric index -> when the user removed it (held back briefly)
        self._window_open_until = 0.0
        self._nodes: dict[str, protocol.RemoteNode] = {}
        self._views: dict[str, DeviceView] = {}
        self._scenes: list[SceneView] = []
        self._home_state: str | None = None
        self._sent: dict[int, protocol.EndpointEntry] = {}      # what the helper knows, by endpoint number
        self._number_keys: dict[int, str] = {}                  # endpoint number -> endpoint key, shared right now
        self._requests: dict[int, asyncio.Future[protocol.Result]] = {}
        self._request_count = 0
        self._wake = asyncio.Event()
        self._needs_refresh = True
        self._save_pending = False
        self._protocol_errors = 0

    # -- life cycle ---------------------------------------------------------

    async def start(self) -> None:
        """Launch the helper if Node.js is installed; otherwise wait for "Einrichten" in the tile."""
        self.problem = ""
        self.node_path = runtime.find(PLUGIN_DIR, self.context.data_dir)
        if self.node_path is not None:
            self._launch()

    async def stop(self) -> None:
        """Stop background work and write what matter.js stored. ComfyOS ends the helper itself."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._helper = None
        self._online = False
        for request in self._requests.values():
            request.cancel()
        self._requests = {}
        await self._save_store()

    def _launch(self) -> None:
        """Open the storage, start the helper locked in and begin listening to it."""
        if not BUNDLE.is_file():
            self.problem = "Das Beiprogramm fehlt (bridge/dist/bridge.mjs) – bitte ComfyOS neu installieren."
            return
        if self.node_path is None:
            return
        self._store = self._open_store()
        # --permission: no file access except reading the bundle itself, no child processes, no workers, no add-ons
        arguments = ["--permission", f"--allow-fs-read={BUNDLE}", str(BUNDLE)]
        try:
            self._helper = self.context.start_helper(self.node_path, arguments)
        except PluginError as error:
            self.problem = str(error)
            return
        self.context.on_change(self._on_change)
        self._tasks = [asyncio.create_task(self._read_helper(self._helper)), asyncio.create_task(self._sync_loop())]

    def _open_store(self) -> MatterStore:
        """Open the encrypted storage file. An unreadable file is set aside and the user is told to pair again."""
        key = self.context.secrets.get(SECRET_STORAGE_KEY)
        if not key:
            key = new_key()
            self.context.secrets.set(SECRET_STORAGE_KEY, key)
        store = MatterStore(self.context.data_dir / STORE_FILE, str(key))
        try:
            store.load()
        except StorageError:
            store.set_aside()
            self.context.event("Matter: Die gespeicherten Kopplungen sind nicht lesbar – bitte neu koppeln.")
        return store

    async def _save_store(self) -> None:
        """Write the storage file if matter.js changed something (in a worker thread: it waits for the disk)."""
        if self._store is None or not self._store.dirty:
            return
        try:
            await asyncio.to_thread(self._store.save)
        except OSError as error:
            self.context.log.warning("storage not written (%s)", type(error).__name__)

    async def _save_later(self) -> None:
        """Write the storage file after a short pause, so a burst of changes becomes one write."""
        await asyncio.sleep(SAVE_DELAY_SECONDS)
        self._save_pending = False
        await self._save_store()

    def status(self) -> dict[str, Any]:
        """Describe the state for the tile head: Node.js missing, helper starting, or how much is shared."""
        egress = {runtime.EGRESS_KEY: ["on_demand", "nur beim Einrichten von Node.js (Knopf in der Kachel)"]}
        if self.node_path is None or self._helper is None:
            message = self.problem or "Noch nicht eingerichtet – Node.js fehlt."
            return {"configured": False, "connected": False, "message": message, "egress": egress}
        controllers = len(self.controllers)
        message = (f"{len(self._sent)} Geräte freigegeben, "
                   f"{controllers} Controller gekoppelt") if self._online else "Matter startet …"
        return {"configured": True, "connected": self._online, "live": self._online, "message": message, "egress": egress}

    # -- helper: identity and messages --------------------------------------

    def _number_setting(self, key: str, default: int, minimum: int, maximum: int) -> int:
        """Return a numeric setting within its limits, or the default if it is not a usable number."""
        value = self.context.settings.get(key)
        is_number = isinstance(value, int) and not isinstance(value, bool)
        return value if is_number and minimum <= value <= maximum else default

    def _bridge_options(self) -> protocol.BridgeOptions:
        """Identity of the bridge. Passcode, discriminator and serial are created once and kept."""
        passcode = str(self.context.secrets.get(SECRET_PASSCODE) or "")
        if not passcode.isdigit() or int(passcode) in INVALID_PASSCODES or not 0 < int(passcode) < 99999999:
            passcode = str(new_passcode())
            self.context.secrets.set(SECRET_PASSCODE, passcode)
        discriminator = self._number_setting(SETTING_DISCRIMINATOR, -1, 0, 4095)
        if discriminator < 0:
            discriminator = secrets.randbelow(4096)
            self.context.settings.set(SETTING_DISCRIMINATOR, discriminator)
        return protocol.BridgeOptions(
            port=self._number_setting(SETTING_BRIDGE_PORT, DEFAULT_BRIDGE_PORT, 1024, 65535),
            passcode=int(passcode), discriminator=discriminator, name=BRIDGE_NAME, serial=self._serial(),
            vendor_id=VENDOR_ID, product_id=PRODUCT_ID)

    def _controller_options(self) -> protocol.ControllerOptions:
        """Identity of the controller node."""
        return protocol.ControllerOptions(
            port=self._number_setting(SETTING_CONTROLLER_PORT, DEFAULT_CONTROLLER_PORT, 1024, 65535),
            serial=self._serial(), vendor_id=VENDOR_ID, product_id=PRODUCT_ID)

    def _serial(self) -> str:
        """Return the serial number of this installation's bridge (random, created once)."""
        serial = self.context.settings.get(SETTING_SERIAL)
        if not isinstance(serial, str) or not 8 <= len(serial) <= 20 or not serial.isalnum():
            serial = secrets.token_hex(6)
            self.context.settings.set(SETTING_SERIAL, serial)
        return serial

    async def _send(self, message: Mapping[str, Any]) -> bool:
        """Send one message to the helper. Returns False when it is not running right now."""
        if self._helper is None:
            return False
        try:
            await self._helper.send(message)
        except PluginError:
            return False
        return True

    async def _read_helper(self, helper: Helper) -> None:
        """Handle everything the helper sends until the plugin stops. Each message is checked first;
        a malformed one is counted and dropped."""
        async for raw in helper.messages():
            try:
                message = protocol.parse(raw)
            except protocol.ProtocolError:
                self._protocol_errors += 1
                if self._protocol_errors in (1, 10, 100):
                    self.context.log.warning("helper sent %d malformed messages", self._protocol_errors)
                continue
            try:
                await self._handle(message)
            except Exception as error:      # one bad message must not end the reader
                self.context.log.warning("message %s failed (%s)", type(message).__name__, type(error).__name__)

    async def _handle(self, message: protocol.HelperMessage) -> None:
        """React to one checked message of the helper."""
        if isinstance(message, protocol.Ready):
            await self._on_ready(message)
        elif isinstance(message, protocol.StorageOps):
            self._on_storage(message)
        elif isinstance(message, protocol.Started):
            self._online = True
            self.problem = ""
            self.context.devices_changed()
        elif isinstance(message, protocol.Pairing):
            self._pairing = message
        elif isinstance(message, protocol.Fabrics):
            self._on_fabrics(message)
        elif isinstance(message, protocol.Command):
            self._tasks.append(asyncio.create_task(self._run_command(message)))
            self._tasks = [task for task in self._tasks if not task.done()]
        elif isinstance(message, protocol.RemoteNode):
            self._on_node(message)
        elif isinstance(message, protocol.NodeRemoved):
            self._on_node_removed(message.node)
        elif isinstance(message, protocol.Result):
            request = self._requests.pop(message.id, None)
            if request is not None and not request.done():
                request.set_result(message)
        elif isinstance(message, protocol.HelperProblem):
            self.context.log.warning("helper reports %s", message.code)

    async def _on_ready(self, message: protocol.Ready) -> None:
        """The helper has (re)started: hand back its storage and start both Matter nodes."""
        self._online = False
        self._sent = {}
        if message.protocol != protocol.PROTOCOL_VERSION:
            self.problem = "Das Beiprogramm passt nicht zu dieser Version des Plugins."
            return
        await self._refresh_views()
        entries = self._entries()
        for storage_message in protocol.storage_messages(self._store.stored if self._store else {}):
            await self._send(storage_message)
        await self._send(protocol.start_message(self._bridge_options(), self._controller_options(), entries))
        self._sent = {entry.number: entry for entry in entries}

    def _on_storage(self, message: protocol.StorageOps) -> None:
        """Take over what matter.js stored and write the file soon."""
        if self._store is None:
            return
        try:
            self._store.apply(message.ops)
        except StorageError:
            self.problem = "Der Speicher von Matter ist voll."
            return
        if not self._save_pending:
            self._save_pending = True
            self._tasks.append(asyncio.create_task(self._save_later()))

    @property
    def controllers(self) -> list[Controller]:
        """The paired controllers as the user sees them (see :func:`group_controllers`)."""
        return group_controllers(self._fabrics)

    def _controller(self, index: int) -> Controller:
        """Return the controller with this index. Raises PluginError for an unknown one."""
        controller = next((known for known in self.controllers if known.index == index), None)
        if controller is None:
            raise PluginError("Diesen Controller gibt es nicht.")
        return controller

    def _on_fabrics(self, message: protocol.Fabrics) -> None:
        """Remember the controllers; a new one is written to the event log and closes the commissioning window."""
        known = {fabric.index for fabric in self._fabrics}
        # A fabric removed a moment ago stays gone although an older report still names it (see remove_controller).
        now, reported = time.monotonic(), {fabric.index for fabric in message.fabrics}
        self._removed = {index: at for index, at in self._removed.items() if index in reported and now - at < REMOVE_HOLD_SECONDS}
        self._fabrics = sorted((fabric for fabric in message.fabrics if fabric.index not in self._removed),
                               key=lambda fabric: fabric.index)
        for controller in self.controllers:
            if any(fabric.index not in known for fabric in controller.fabrics):
                self._window_open_until = 0.0
            if all(fabric.index not in known for fabric in controller.fabrics):     # Apple's second fabric is no new controller
                self.context.event(f"Matter: Controller „{controller.name}“ ist jetzt gekoppelt")
        # A removed controller takes its own name with it.
        names = self._controller_names()
        kept = {index: name for index, name in names.items() if index in {str(controller.index) for controller in self.controllers}}
        if kept != names:
            self.context.settings.set(SETTING_CONTROLLER_NAMES, kept)

    def _controller_names(self) -> dict[str, str]:
        """Return the user's own names for controllers by their index (as text); anything else stored there is ignored."""
        stored = self.context.settings.get(SETTING_CONTROLLER_NAMES)
        if not isinstance(stored, dict):
            return {}
        return {str(index): name for index, name in stored.items() if isinstance(name, str) and name}

    def shown_name(self, controller: Controller) -> str:
        """Return the name ComfyOS shows for a controller: the user's own name, else the one it brings along."""
        return self._controller_names().get(str(controller.index)) or controller.name

    def rename_controller(self, index: int, name: str) -> str:
        """Give a controller a name of the user's own; an empty name brings back the one it came with.

        Returns the name shown afterwards. Raises PluginError for an unknown controller.
        """
        controller = self._controller(index)
        cleaned = " ".join("".join(char for char in str(name) if char.isprintable()).split())[:MAX_CONTROLLER_NAME]
        names = self._controller_names()
        if cleaned and cleaned != controller.name:
            names[str(index)] = cleaned
        else:
            names.pop(str(index), None)
        self.context.settings.set(SETTING_CONTROLLER_NAMES, names)
        return self.shown_name(controller)

    # -- bridge: what is shared ---------------------------------------------

    def _on_change(self, device_id: str, changes: Mapping[str, Any]) -> None:
        """A device of ComfyOS changed (callback of the core, must not block): note it and wake the sync loop."""
        view = self._views.get(device_id)
        if view is None:
            self._needs_refresh = True          # device list, scenes or home state changed – or a device we do not know
        else:
            for key in VIEW_STATE_KEYS:
                if key in changes:
                    view.state[key] = changes[key]
            if "online" in changes:
                view.online = bool(changes["online"])
            if "locked" in changes:
                view.locked = bool(changes["locked"])
            if "light" in changes and changes["light"] is not None:
                view.light = changes["light"]
        self._wake.set()

    async def _refresh_views(self) -> None:
        """Read devices, scenes and home state of ComfyOS again. What cannot be read keeps its last state."""
        self._needs_refresh = False
        try:
            self._views = {view.id: view for view in await self.context.all_devices()}
        except PluginError:
            self._needs_refresh = True          # ComfyOS could not list its devices just now: try again next round
        try:
            self._scenes = await self.context.scenes()
            self._home_state = await self.context.home_state()
        except PluginError:
            self._needs_refresh = True

    def offered(self) -> list[matter_devices.SharedEndpoint]:
        """Everything that could be shared right now: devices of ComfyOS and the virtual devices of the home."""
        found: list[matter_devices.SharedEndpoint] = []
        for view in self._views.values():
            found += matter_devices.endpoints_of(view)
        return found + home.home_endpoints(list(self._views.values()), self._scenes, self._home_state)

    def _entries(self) -> list[protocol.EndpointEntry]:
        """The shared endpoints as the helper needs them; also remembers which key each number stands for."""
        selected = self.shares.select(self.offered())
        self._number_keys = {number: endpoint.key for number, endpoint in selected}
        return [protocol.EndpointEntry(number=number, kind=endpoint.kind, name=endpoint.name, serial=f"comfyos-{number}",
                                       reachable=endpoint.reachable, locked=endpoint.locked, state=endpoint.state)
                for number, endpoint in selected]

    async def _push(self) -> None:
        """Bring the helper in line with ComfyOS: the whole list when devices came or went, else only changed values."""
        entries = self._entries()
        current = {entry.number: entry for entry in entries}

        def shape(entry: protocol.EndpointEntry) -> tuple[Any, ...]:
            fixed = [entry.state.get(key) for key in ("min_mireds", "max_mireds", "min_heat", "max_heat")]
            return (entry.kind, entry.name, *fixed)

        same_devices = current.keys() == self._sent.keys() and all(
            shape(entry) == shape(self._sent[number]) for number, entry in current.items())
        if not same_devices:
            if await self._send(protocol.endpoints_message(entries)):
                self._sent = current
            return
        for number, entry in current.items():
            if entry != self._sent[number]:
                if await self._send(protocol.update_message(number, entry.state, entry.reachable, entry.locked)):
                    self._sent[number] = entry

    async def _sync_loop(self) -> None:
        """Pass changes on to the helper, bundled, and re-read everything from time to time."""
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), REFRESH_SECONDS)
            except asyncio.TimeoutError:
                self._needs_refresh = True
            self._wake.clear()
            await asyncio.sleep(SYNC_DELAY_SECONDS)
            self._close_window_when_due()
            if not self._online:
                continue
            try:
                if self._needs_refresh:
                    await self._refresh_views()
                await self._push()
            except Exception as error:          # a failed round must not end the loop; the next one tries again
                self.context.log.warning("sync failed (%s)", type(error).__name__)

    def sync_soon(self, refresh: bool = False) -> None:
        """Ask the sync loop to run now (after the user changed the share list)."""
        self._needs_refresh = self._needs_refresh or refresh
        self._wake.set()

    # -- bridge: commands of controllers ------------------------------------

    async def _run_command(self, command: protocol.Command) -> None:
        """Carry out what a controller asked for and answer the helper: ok, locked or error.

        Only endpoints of the share list are accepted, and the device is switched through ComfyOS
        (``context.command``): the device lock applies and the event log names this plugin.
        """
        key = self._number_keys.get(command.number)
        status = "error"
        try:
            if key is None:
                raise PluginError("Dieses Gerät ist nicht freigegeben.")
            if home.is_home_key(key):
                await home.run_command(self.context, key, command.state)
            else:
                await self.context.command(share_key_of(key), matter_devices.command_state(command.state))
            status = "ok"
        except DeviceLocked:
            status = "locked"
        except PluginError:
            status = "error"
        except Exception as error:              # whatever goes wrong: the controller gets an answer
            self.context.log.warning("command failed (%s)", type(error).__name__)
        await self._send(protocol.reply_message(command.id, status))

    # -- bridge: commissioning ----------------------------------------------

    @property
    def window_open_until(self) -> float:
        """Until when (time stamp) the commissioning window is open; 0 = closed."""
        return self._window_open_until if self._window_open_until > self.clock() else 0.0

    async def open_window(self, is_open: bool) -> None:
        """Open the commissioning window for 15 minutes, or close it. Raises PluginError while Matter is not running."""
        if not self._online or not await self._send(protocol.commissioning_message(is_open)):
            raise DeviceUnreachable("Matter läuft gerade nicht.")
        self._window_open_until = self.clock() + WINDOW_SECONDS if is_open else 0.0
        self.context.event("Matter: Kopplung geöffnet (15 Minuten)" if is_open else "Matter: Kopplung geschlossen")
        self._wake.set()

    def _close_window_when_due(self) -> None:
        """Close the commissioning window once its time is over."""
        if self._window_open_until and self._window_open_until <= self.clock():
            self._window_open_until = 0.0
            self._tasks.append(asyncio.create_task(self._send_quietly(protocol.commissioning_message(False))))

    async def _send_quietly(self, message: Mapping[str, Any]) -> None:
        """Send a message whose delivery nobody waits for."""
        await self._send(message)

    async def remove_controller(self, index: int) -> None:
        """Remove a controller – every fabric it consists of – from the bridge. Raises PluginError for an unknown
        controller or while Matter is not running."""
        controller = self._controller(index)
        for fabric in controller.fabrics:
            if not self._online or not await self._send(protocol.remove_fabric_message(fabric.index)):
                raise DeviceUnreachable("Matter läuft gerade nicht.")
        self.context.event(f"Matter: „{self.shown_name(controller)}“ entfernt")
        # Gone at once for the interface: the helper only reports the new list a moment later, and the tile is redrawn
        # right after this call. Should the removal fail, the helper's reports bring the controller back.
        gone = {fabric.index for fabric in controller.fabrics}
        self._removed.update({index: time.monotonic() for index in gone})
        self._fabrics = [fabric for fabric in self._fabrics if fabric.index not in gone]

    # -- controller: devices of other makers --------------------------------

    def _on_node(self, node: protocol.RemoteNode) -> None:
        """A commissioned device reports itself: new devices reload the list, changed values go out at once."""
        previous = self._nodes.get(node.node)
        self._nodes[node.node] = node
        before = {device.id: device for device in self._devices_of(previous)} if previous else {}
        now = {device.id: device for device in self._devices_of(node)}
        if before.keys() != now.keys():
            self.context.devices_changed()
            return
        for identifier, device in now.items():
            old = before[identifier]
            changes: dict[str, Any] = {key: value for key, value in device.state.items() if old.state.get(key) != value}
            if device.online != old.online:
                changes["online"] = device.online
            if device.light != old.light and device.light is not None:
                changes["light"] = device.light
            if changes:
                self.context.device_changed(identifier, changes)

    def _on_node_removed(self, node_id: str) -> None:
        """The controller forgot a device: its ComfyOS devices disappear for good."""
        node = self._nodes.pop(node_id, None)
        for device in self._devices_of(node) if node else []:
            self.context.device_gone(device.id)
        self._forget_attestation(node_id)
        self.context.devices_changed()

    def _devices_of(self, node: protocol.RemoteNode | None) -> list[Device]:
        """The ComfyOS devices one commissioned Matter device stands for."""
        found: list[Device] = []
        for endpoint in node.endpoints if node else []:
            device = matter_devices.device_of(node, endpoint)
            if device is not None:
                found.append(device)
        return found

    async def devices(self) -> list[Device]:
        """Return the devices the controller commissioned.

        While the helper is starting the list is not known yet – then DeviceUnreachable, so ComfyOS
        keeps what it stored about these devices instead of treating them as gone.
        """
        if self.node_path is None or self._helper is None:
            return []
        if not self._online:
            raise DeviceUnreachable("Matter startet gerade.")
        found: list[Device] = []
        for node in self._nodes.values():
            found += self._devices_of(node)
        return found

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
        """Switch a device the controller commissioned."""
        node_id, endpoint = matter_devices.split_device_id(device_id)
        if node_id not in self._nodes:
            raise PluginError("Dieses Gerät gibt es nicht mehr.")
        wanted = matter_devices.invoke_state(state)
        result = await self._request(lambda request_id: protocol.invoke_message(request_id, node_id, endpoint, wanted))
        if not result.ok:
            raise DeviceUnreachable("Das Gerät hat den Befehl nicht angenommen.")
        return matter_devices.values_after(state)

    async def _request(self, build: Callable[[int], dict[str, Any]],
                       timeout: float = REQUEST_TIMEOUT_SECONDS) -> protocol.Result:
        """Send a request to the helper and wait for its result. Raises DeviceUnreachable without an answer."""
        if not self._online:
            raise DeviceUnreachable("Matter läuft gerade nicht.")
        self._request_count += 1
        request_id = self._request_count
        future: asyncio.Future[protocol.Result] = asyncio.get_running_loop().create_future()
        self._requests[request_id] = future
        try:
            if not await self._send(build(request_id)):
                raise DeviceUnreachable("Matter läuft gerade nicht.")
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise DeviceUnreachable("Matter antwortet nicht.")
        finally:
            self._requests.pop(request_id, None)

    async def pair(self, code: str) -> dict[str, Any]:
        """Commission a Matter device with the code another app shows under "share".

        Raises PluginError with a text for the user when the code is malformed or commissioning fails.
        """
        try:
            clean_code = protocol.clean_pairing_code(code)
        except protocol.ProtocolError:
            raise PluginError("Das ist kein Matter-Code. Erwartet wird der elfstellige Zahlencode oder der Text des QR-Codes (MT:…).")
        result = await self._request(lambda request_id: protocol.pair_message(request_id, clean_code), PAIR_TIMEOUT_SECONDS)
        if not result.ok:
            raise PluginError("Das Gerät ließ sich nicht koppeln. Ist der Code noch gültig und das Gerät im selben Netz? "
                              f"(Kennung: {result.error or 'unbekannt'})")
        self._remember_attestation(result.node, result.attestation)
        self.context.event("Matter: Gerät eingebunden")
        return {"node": result.node, "attestation": result.attestation}

    async def unpair(self, node_id: str) -> bool:
        """Remove ComfyOS from a device's fabric and forget the device. Returns False when the device did not confirm."""
        if node_id not in self._nodes:
            raise PluginError("Dieses Gerät gibt es nicht.")
        result = await self._request(lambda request_id: protocol.unpair_message(request_id, node_id), 40.0)
        if not result.ok:
            raise PluginError("Das Gerät ließ sich nicht entfernen.")
        self.context.event("Matter: Gerät entfernt")
        self._on_node_removed(node_id)      # gone at once for the interface; the helper's own report changes nothing more
        return result.clean

    def _attestations(self) -> dict[str, list[str]]:
        """Findings of the device attestation by node, as stored in the settings."""
        stored = self.context.settings.get(SETTING_ATTESTATION)
        return {str(node): [str(finding) for finding in findings][:10] for node, findings in stored.items()
                if isinstance(findings, list)} if isinstance(stored, dict) else {}

    def _remember_attestation(self, node_id: str, findings: list[str]) -> None:
        """Keep what the attestation of a newly commissioned device found (shown in the tile)."""
        stored = self._attestations()
        stored[node_id] = findings
        self.context.settings.set(SETTING_ATTESTATION, dict(list(stored.items())[-50:]))

    def _forget_attestation(self, node_id: str) -> None:
        """Drop the attestation findings of a removed device."""
        stored = self._attestations()
        if stored.pop(node_id, None) is not None:
            self.context.settings.set(SETTING_ATTESTATION, stored)

    # -- Node.js ------------------------------------------------------------

    async def install_runtime(self) -> None:
        """Download and unpack Node.js (button "Einrichten"), then launch the helper. Raises RuntimeProblem."""
        if self._helper is not None:
            return
        await runtime.install(self.context, self.context.data_dir)
        self.context.event(f"Matter: Node.js {runtime.NODE_VERSION} eingerichtet")
        self.node_path = runtime.find(PLUGIN_DIR, self.context.data_dir)
        self.problem = ""
        self._launch()

    # -- for the tile -------------------------------------------------------

    def set_shared(self, share_key: str, is_shared: bool) -> None:
        """Switch "für Matter sichtbar" for one device. Raises PluginError (own controller device, unknown, bound)."""
        needed = [endpoint for endpoint in self.offered() if share_key_of(endpoint.key) == share_key]
        if is_shared and not needed:
            if share_key.startswith(matter_devices.DEVICE_ID_PREFIX):
                self.shares.set_shared(share_key, True, 0)      # raises with the explanation for own devices
            raise PluginError("Dieses Gerät lässt sich nicht für Matter freigeben.")
        self.shares.set_shared(share_key, is_shared, len(self._entries()), len(needed))
        self.sync_soon()

    def set_shared_many(self, share_keys: list[str], is_shared: bool) -> int:
        """Switch "für Matter sichtbar" for several devices at once (all, a room, a floor plan group).

        Only keys ComfyOS offers right now count – a stale key from the browser is skipped, never shared.
        Returns how many devices changed. Raises PluginError for the technical bound.
        """
        offered: dict[str, int] = {}
        for endpoint in self.offered():
            key = share_key_of(endpoint.key)
            offered[key] = offered.get(key, 0) + 1
        keys = [key for key in dict.fromkeys(share_keys) if key in offered or not is_shared]
        if not keys:
            return 0
        needed = sum(offered.get(key, 0) for key in keys if not self.shares.is_shared(key))
        changed = self.shares.set_many(keys, is_shared, len(self._entries()), needed)
        self.sync_soon()
        return changed

    async def refresh_for_tile(self) -> None:
        """Read the devices of ComfyOS again before the tile is drawn, if nothing is cached or a change is pending
        (while the helper is not running, nobody else does)."""
        if self._needs_refresh or not self._views:
            await self._refresh_views()

    def overview(self) -> dict[str, Any]:
        """Everything the tile shows. While the commissioning window is open the pairing code is part of it –
        it is given out here and nowhere else."""
        archive = runtime.archive_for()
        entries = self._entries()
        numbers = {key: number for number, key in self._number_keys.items()}
        rooms = {view.id: view.room for view in self._views.values()}
        offered = []
        for endpoint in self.offered():
            share_key = share_key_of(endpoint.key)
            if endpoint.key != share_key:
                continue                        # the second endpoint of a sensor follows its device
            offered.append({"key": share_key, "name": endpoint.name, "kind": endpoint.kind,
                            "group": "home" if home.is_home_key(share_key) else "device", "room": rooms.get(share_key),
                            "shared": self.shares.is_shared(share_key), "number": numbers.get(endpoint.key),
                            "locked": endpoint.locked, "reachable": endpoint.reachable})
        attestations = self._attestations()
        pairing = None
        if self._pairing is not None:
            pairing = {"commissioned": self._pairing.commissioned, "open_until": self.window_open_until}
            if self.window_open_until:          # the code is only given out while the window is open
                pairing.update(manual=self._pairing.manual, qr=self._pairing.qr, rows=self._pairing.rows)
        return {
            "runtime": {"installed": self.node_path is not None, "version": runtime.NODE_VERSION,
                        "supported": archive is not None, "megabytes": archive.megabytes if archive else 0,
                        "file": archive.file_name if archive else "", "sha256": archive.sha256 if archive else "",
                        "host": runtime.DOWNLOAD_HOST, "unsupported_text": "" if archive else runtime.unsupported_text()},
            "online": self._online, "problem": self.problem,
            "pairing": pairing,
            "controllers": [{"index": controller.index, "number": controller.number, "name": self.shown_name(controller)}
                            for controller in self.controllers],
            "shares": self.shares.summary(len(entries)),
            "offered": offered,
            "nodes": [{"node": node.node, "name": node.name or node.product or "Matter-Gerät", "vendor": node.vendor,
                       "product": node.product, "online": node.online, "devices": len(self._devices_of(node)),
                       "attestation": attestations.get(node.node, [])} for node in self._nodes.values()],
            "ports": {"bridge": self._number_setting(SETTING_BRIDGE_PORT, DEFAULT_BRIDGE_PORT, 1024, 65535),
                      "controller": self._number_setting(SETTING_CONTROLLER_PORT, DEFAULT_CONTROLLER_PORT, 1024, 65535)},
        }

    # -- endpoints ----------------------------------------------------------

    def routes(self) -> Any:
        """Return the plugin's own routes (tile: setup, pairing, sharing, controller)."""
        return build_router(self)


def setup(context: PluginContext) -> Plugin:
    """Entry point named in manifest.json. Nothing starts here; ``start()`` launches the helper."""
    return MatterPlugin(context)
