"""Network plugin: presence by reachability in the home network.

What it does
    * The user names devices in the home network by address and port, for example a phone.
    * Every few seconds the plugin tries to open a TCP connection to each of them. Any answer
      counts: an accepted connection *and* a refused one both prove that the device is switched
      on and in the network. Only silence means "not there".
    * Each target is a ComfyOS device of the kind "presence" with the state ``present``.
      Flows can react to it ("when the phone comes home …").

Why "away" takes a while
    Phones sleep to save power and then do not answer every attempt. A target therefore counts
    as away only after it has been silent for ``away_after`` minutes; "present" is reported at
    the first answer. Right after a start the state is unknown until the first answer or until
    that time has passed, so a restart never looks like someone leaving.

What it cannot do
    It does not send ICMP pings (they need special rights on most systems) and it never contacts
    addresses outside the home network – ComfyOS blocks those for this plugin.

Files
    routes.py   endpoints for the settings tile (list, create, delete, check now)
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

from plugin_api import (
    STATE_PRESENT,
    Capability,
    ConnectionRefused,
    Device,
    DeviceUnreachable,
    Plugin,
    PluginContext,
    PluginError,
)

from .routes import build_router

EGRESS_KEY = "hosts"                            # see manifest.json -> egress
DEVICE_ID_PREFIX = "network:"                   # device id: network:<key of the target>
MAX_TARGETS = 30
MAX_NAME_LENGTH = 60
PROBE_TIMEOUT_SECONDS = 2.0
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_AWAY_AFTER_MINUTES = 10
DEFAULT_PORT = 80
MODEL_TEXT = "Erreichbarkeit im Heimnetz"

# Settings this plugin stores
SETTING_INTERVAL = "interval"
SETTING_AWAY_AFTER = "away_after"
SETTING_TARGETS = "targets"

_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,120}$")
_KEY_PATTERN = re.compile(r"^[a-z0-9]{4,16}$")


@dataclass(frozen=True)
class Target:
    """One device in the home network whose reachability is watched."""

    key: str                                    # short random id; part of the device id
    name: str
    host: str
    port: int


@dataclass
class TargetState:
    """What the plugin knows about a target right now."""

    is_present: bool | None = None              # None = not known yet (no answer since the start, wait time not over)
    last_answer_at: float | None = None         # time.time() of the last answer
    problem: str | None = None                  # why the target cannot be checked, e.g. "not in the home network"


def device_id_of(target: Target) -> str:
    """Return the ComfyOS device id of a target."""
    return DEVICE_ID_PREFIX + target.key


def checked_target(key: str, values: Mapping[str, Any]) -> Target:
    """Build a Target from user input or stored data. Raises PluginError with a message for the user."""
    name = " ".join(str(values.get("name") or "").split())[:MAX_NAME_LENGTH]
    host = str(values.get("host") or "").strip()
    port = values.get("port", DEFAULT_PORT)
    if not _KEY_PATTERN.match(key):
        raise PluginError("Ungültige Kennung.")
    if not name:
        raise PluginError("Bitte einen Namen angeben.")
    if not _HOST_PATTERN.match(host):
        raise PluginError("Bitte eine Adresse im Heimnetz angeben, z. B. 192.168.178.40.")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise PluginError("Der Port muss zwischen 1 und 65535 liegen.")
    return Target(key=key, name=name, host=host, port=port)


class NetworkPlugin(Plugin):
    """Watches devices in the home network and reports each as present or away."""

    def __init__(self, context: PluginContext) -> None:
        """Start without targets; ``start()`` loads them from the settings and begins watching."""
        super().__init__(context)
        self._targets: list[Target] = []
        self._states: dict[str, TargetState] = {}       # by target key
        self._watch_task: asyncio.Task[None] | None = None
        self._started_at = 0.0
        self.clock: Callable[[], float] = time.time     # tests replace it to let time pass

    # -- life cycle ---------------------------------------------------------

    async def start(self) -> None:
        """Load the targets and start checking them in the background."""
        self._targets = self._load_targets()
        self._states = {target.key: TargetState() for target in self._targets}
        self._started_at = self.clock()
        self._watch_task = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        """Stop checking."""
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        self._watch_task = None

    def status(self) -> dict[str, Any]:
        """Report how many of the watched devices are present."""
        present = sum(1 for state in self._states.values() if state.is_present)
        return {"configured": bool(self._targets), "connected": True, "live": True,
                "message": f"{present} von {len(self._targets)} anwesend" if self._targets else "Noch kein Gerät eingetragen."}

    # -- settings -----------------------------------------------------------

    def _number_setting(self, key: str, default: int, minimum: int, maximum: int) -> int:
        """Return a numeric setting within its limits, or the default if it is not a number."""
        value = self.context.settings.get(key)
        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        return int(max(minimum, min(maximum, value))) if is_number else default

    @property
    def interval_seconds(self) -> int:
        """Seconds between two rounds of checks."""
        return self._number_setting(SETTING_INTERVAL, DEFAULT_INTERVAL_SECONDS, 10, 600)

    @property
    def away_after_seconds(self) -> int:
        """Seconds of silence after which a target counts as away."""
        return self._number_setting(SETTING_AWAY_AFTER, DEFAULT_AWAY_AFTER_MINUTES, 1, 120) * 60

    def _load_targets(self) -> list[Target]:
        """Read the targets from the settings; entries that are no longer valid are skipped."""
        stored = self.context.settings.get(SETTING_TARGETS)
        targets: list[Target] = []
        for entry in stored if isinstance(stored, list) else []:
            if not isinstance(entry, Mapping) or len(targets) >= MAX_TARGETS:
                continue
            try:
                targets.append(checked_target(str(entry.get("key") or ""), entry))
            except PluginError:
                continue
        return targets

    def _store_targets(self, targets: list[Target]) -> None:
        """Save the watched devices and keep the state of those that remain."""
        self.context.settings.set(SETTING_TARGETS, [asdict(target) for target in targets])
        self._targets = targets
        self._states = {target.key: self._states.get(target.key, TargetState()) for target in targets}

    # -- targets ------------------------------------------------------------

    @property
    def targets(self) -> list[Target]:
        """The targets the user entered."""
        return list(self._targets)

    def state_of(self, target: Target) -> TargetState:
        """Return what is known about a target."""
        return self._states.setdefault(target.key, TargetState())

    def find_target(self, key: str) -> Target | None:
        """Return the target with this key, or None."""
        return next((target for target in self._targets if target.key == key), None)

    def add_target(self, values: Mapping[str, Any]) -> Target:
        """Create a target from user input. Raises PluginError when the input is not usable."""
        if len(self._targets) >= MAX_TARGETS:
            raise PluginError(f"Höchstens {MAX_TARGETS} Geräte.")
        target = checked_target(uuid.uuid4().hex[:8], values)
        if not self.context.is_local_host(target.host):
            raise PluginError("Die Adresse liegt nicht im Heimnetz. Geprüft werden nur Geräte zu Hause.")
        self._store_targets(self._targets + [target])
        self.context.devices_changed()
        return target

    def remove_target(self, key: str) -> bool:
        """Delete a target. Returns False when there is none with this key."""
        remaining = [target for target in self._targets if target.key != key]
        if len(remaining) == len(self._targets):
            return False
        self._store_targets(remaining)
        self.context.devices_changed()
        return True

    # -- checking -----------------------------------------------------------

    async def _watch(self) -> None:
        """Check every target again and again until the plugin stops."""
        while True:
            await self.check_all()
            await asyncio.sleep(self.interval_seconds)

    async def check_all(self) -> None:
        """Check every target once, all at the same time."""
        await asyncio.gather(*(self.check(target) for target in list(self._targets)))

    async def check(self, target: Target) -> bool | None:
        """Try to reach a target and update its state. Returns True (answered), False (silent) or None (not allowed)."""
        state = self.state_of(target)
        answered: bool | None
        try:
            _, writer = await self.context.open_stream(EGRESS_KEY, target.host, target.port, timeout=PROBE_TIMEOUT_SECONDS)
            writer.close()
            answered = True
        except ConnectionRefused:
            answered = True                         # the port is closed, but the device said so: it is there
        except DeviceUnreachable:
            answered = False
        except PluginError as error:                # ComfyOS refuses the address (not in the home network)
            answered = None
            state.problem = str(error)
        if answered is not None:
            state.problem = None
        self._note_answer(target, state, answered)
        return answered

    def _note_answer(self, target: Target, state: TargetState, answered: bool | None) -> None:
        """Work out present/away from an answer and tell ComfyOS when it changed."""
        now = self.clock()
        was_present = state.is_present
        if answered:
            state.last_answer_at = now
            state.is_present = True
        elif answered is False:
            silent_since = state.last_answer_at if state.last_answer_at is not None else self._started_at
            if now - silent_since >= self.away_after_seconds:
                state.is_present = False
        if state.is_present is not None and state.is_present != was_present:
            self.context.device_changed(device_id_of(target), {STATE_PRESENT: state.is_present})

    # -- devices ------------------------------------------------------------

    async def devices(self) -> list[Device]:
        """Return one presence device per watched target."""
        return [self._device(target) for target in self._targets]

    def _device(self, target: Target) -> Device:
        """Convert a target to a ComfyOS presence device with its last answer and any problem reaching it."""
        state = self.state_of(target)
        last_answer = "noch nie"
        if state.last_answer_at is not None:
            last_answer = time.strftime("%d.%m. %H:%M", time.localtime(state.last_answer_at))
        return Device(
            id=device_id_of(target),
            name=target.name,
            kind="presence",
            online=state.problem is None,
            status_detail=state.problem,
            state={STATE_PRESENT: state.is_present} if state.is_present is not None else {},
            capabilities=[Capability(STATE_PRESENT, "bool")],
            model=MODEL_TEXT,
            details={"Adresse": f"{target.host}:{target.port}", "Zuletzt erreichbar": last_answer},
            discovered=True,                        # the tile adopts a target right after it was created
        )

    # -- endpoints ----------------------------------------------------------

    def routes(self) -> Any:
        """Return the plugin's own routes (adding and removing watched devices)."""
        return build_router(self)


def setup(context: PluginContext) -> Plugin:
    """Entry point named in manifest.json. Checking starts later, in ``start()``."""
    return NetworkPlugin(context)
