"""Magenta sync (optional): keep heating routines and home/away in step with the Magenta app.

How it works
    * Every SYNC_SECONDS (and when the heating page asks, at most every MIN_GAP_SECONDS) a
      round runs: first push local changes, then pull the state of the app.
    * After every change in ComfyOS the core asks for a push at once.
    * The Telekom session is reused (token for 8 h, refresh token) – no password login per round.

The routines themselves belong to ComfyOS. This module only sees them through
``PluginContext.heating`` (``plugin_api.HeatingPlans``): it sends what is listed as changed,
confirms each item and hands the app's routines over for merging.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from plugin_api import HeatingPlans, PluginContext, PluginError, RemoteRoutine

from .telekom import TelekomCloud, TelekomError, application_id

SYNC_SECONDS = 300
MIN_GAP_SECONDS = 45
START_DELAY_SECONDS = 5
LEVEL_NAMES = ("COMFORT", "ON", "ECO", "OFF")
NON_ROOMS = ("SYSTEM",)                  # entries of the app's room list that are no real rooms
EGRESS_KEY = "magenta"                   # see manifest.json -> egress
SETTING_USERNAME = "telekom_username"
SETTING_GATEWAY_ID = "gateway_id"
SECRET_PASSWORD = "telekom_password"


def levels_from_dashboard(dashboard: Any) -> dict[str, float]:
    """Read the temperature of each level (COMFORT, ON, ECO, OFF) from getDashboardInfo."""
    result = dashboard.get("result") if isinstance(dashboard, dict) else None
    levels = {}
    if isinstance(result, dict):
        for name in LEVEL_NAMES:
            value = result.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                levels[name] = float(value)
    return levels


def room_names_from_rooms(list_rooms_answer: Any) -> dict[str, str]:
    """Read ``{room id: name}`` from the answer of listRooms."""
    result = list_rooms_answer.get("result", list_rooms_answer) if isinstance(list_rooms_answer, dict) else {}
    names = {}
    if isinstance(result, dict):
        for room_id, value in result.items():
            if room_id not in NON_ROOMS and isinstance(value, dict) and value.get("name"):
                names[room_id] = value["name"]
    return names


def remote_routines(heating: dict[str, Any]) -> list[RemoteRoutine]:
    """Turn the answer of ``TelekomCloud.get_heating`` into the routines of the plugin API."""
    schedules = heating.get("triggersByMode") or {}
    routines = []
    for mode in heating.get("modes") or []:
        key = mode.get("key") if isinstance(mode, dict) else None
        if key:
            schedule = schedules.get(key)
            routines.append(RemoteRoutine(key=key, name=mode.get("name") or key,
                                          schedule=schedule if isinstance(schedule, dict) else {},
                                          last_run=mode.get("lastExecutionTime")))
    return routines


class MagentaSync:
    """Runs the sync rounds and keeps their status."""

    def __init__(self, context: PluginContext, gateway_hint: Callable[[], str | None]) -> None:
        """Prepare the sync; nothing is sent until it is configured and started. ``gateway_hint`` returns the serial
        number the local connection already knows."""
        self.context = context
        self.gateway_hint = gateway_hint         # serial number the local connection already knows
        self.cloud: TelekomCloud | None = None
        self._credentials: tuple[str, str] | None = None
        self._lock = asyncio.Lock()
        self._last_run = 0.0
        self._task: asyncio.Task[None] | None = None
        self.status: dict[str, Any] = {"configured": False, "ok": None, "last_sync": None, "last_error": None,
                                       "running": False, "application_id": None}

    @property
    def plans(self) -> HeatingPlans:
        """The heating routines of ComfyOS."""
        return self.context.heating

    # -- account -------------------------------------------------------------

    def username(self) -> str:
        """The Telekom e-mail the user entered ('' = not set up)."""
        return str(self.context.settings.get(SETTING_USERNAME) or "").strip()

    def configured(self) -> bool:
        """True when e-mail and password are stored."""
        return bool(self.username() and self.context.secrets.get(SECRET_PASSWORD))

    async def _client(self) -> TelekomCloud | None:
        """Return the Telekom client for the stored account, or ``None`` if no account is set up. Changed credentials
        start a fresh client."""
        username, password = self.username(), self.context.secrets.get(SECRET_PASSWORD)
        if not username or not password:
            await self.reset()
            return None
        if self._credentials != (username, password) or self.cloud is None:
            await self.reset()
            self.cloud = TelekomCloud(username, password, lambda **options: self.context.http(EGRESS_KEY, **options))
            self._credentials = (username, password)
        return self.cloud

    async def reset(self) -> None:
        """Forget the Telekom session (after new credentials or when the sync is removed)."""
        if self.cloud is not None:
            await self.cloud.close()
        self.cloud = None
        self._credentials = None

    async def _gateway(self, cloud: TelekomCloud) -> str:
        """Return the gateway id to sync with: from the local connection, the settings, or the Telekom account (then
        remembered)."""
        gateway_id = self.gateway_hint() or self.context.settings.get(SETTING_GATEWAY_ID)
        if gateway_id:
            return str(gateway_id)
        await cloud.customer_self()
        gateway_ids = cloud.gateway_ids()
        gateway_id = gateway_ids[0] if gateway_ids else ""
        if gateway_id:
            self.context.settings.set(SETTING_GATEWAY_ID, gateway_id)
        return gateway_id

    # -- push ----------------------------------------------------------------

    async def _push(self, cloud: TelekomCloud, gateway_id: str) -> list[str]:
        """Send every pending local change. Returns the error messages (empty = all sent)."""
        errors: list[str] = []
        plans = self.plans
        changes = plans.changes()

        for key in changes.deleted:
            try:
                await cloud.delete_heating_mode(gateway_id, key)
                plans.done(key, "deleted")
            except TelekomError as error:
                errors.append(f"Löschen {key}: {error}")
                # a permanent refusal (e.g. a built-in mode) – trying again does not help
                if "validationError" in str(error) or "invalid" in str(error).lower():
                    plans.done(key, "deleted")
            except Exception as error:
                errors.append(f"Löschen {key}: {type(error).__name__}")

        for key in changes.created:
            name, schedule = plans.routine_name(key), plans.schedule(key)
            if name is None or schedule is None:
                continue
            try:
                new_key = await cloud.create_heating_mode(gateway_id, name, schedule)
                if not new_key:
                    raise TelekomError("App lieferte keinen Schlüssel für die neue Routine.")
                plans.created(key, new_key)
                self.context.event(f"Routine '{name}' in der Magenta-App angelegt")
            except Exception as error:
                errors.append(f"Anlegen '{name}': {_text(error)}")

        changes = plans.changes()                 # keys may have changed
        for key in changes.updated:
            schedule = plans.schedule(key)
            if schedule is None:
                continue
            try:
                await cloud.set_heating_triggers(gateway_id, key, schedule)
                plans.done(key, "updated")
            except Exception as error:
                errors.append(f"Plan {key}: {_text(error)}")

        for key in changes.renamed:
            name = plans.routine_name(key)
            if name is None:
                continue
            try:
                await cloud.rename_heating_mode(gateway_id, key, name)
                plans.done(key, "renamed")
            except Exception as error:
                errors.append(f"Umbenennen {key}: {_text(error)}")

        if changes.activate:
            try:
                await cloud.set_heating_mode(gateway_id, changes.activate)
                plans.done(changes.activate, "activate")
            except Exception as error:
                errors.append(f"Aktivieren {changes.activate}: {_text(error)}")

        errors += await self._push_home(cloud, gateway_id)
        return errors

    async def _push_home(self, cloud: TelekomCloud, gateway_id: str) -> list[str]:
        """Send home/away and the routine mapping if they changed in ComfyOS."""
        errors: list[str] = []
        plans = self.plans
        home = plans.home()
        if home.state_changed:
            try:
                await cloud.plugin_call("setHomeState", gateway_id, {"homeState": home.state})
                plans.home_done(state=True)
                self.context.event(f"Zuhause/Abwesend an Home Base: {home.state}")
            except Exception as error:
                errors.append(f"Zuhause/Abwesend: {_text(error)}")
        present, away = home.present_routine, home.away_routine
        if home.mapping_changed and present and away and not plans.is_local(present) and not plans.is_local(away):
            try:
                properties = (await cloud.plugin_call("getHomeStateProperties", gateway_id) or {}).get("result")
                params = {"heatingModePresent": present, "heatingModeAway": away}
                if isinstance(properties, dict):
                    for key in ("activationTimes", "activated"):
                        if key in properties:
                            params[key] = properties[key]
                await cloud.plugin_call("setHomeStateProperties", gateway_id, params)
                plans.home_done(state=False, mapping=True)
            except Exception as error:
                errors.append(f"Zuordnung Zuhause/Abwesend: {_text(error)}")
        return errors

    # -- pull ----------------------------------------------------------------

    async def _pull(self, cloud: TelekomCloud, gateway_id: str) -> dict[str, int]:
        """Fetch routines, levels, room names and home/away from the app and merge them."""
        heating = await cloud.get_heating(gateway_id)
        rooms = await cloud.plugin_call("listRooms", gateway_id)
        merged = self.plans.merge(remote_routines(heating), heating.get("active"),
                                  levels_from_dashboard(heating.get("dashboard")), room_names_from_rooms(rooms))
        dashboard = (heating.get("dashboard") or {}).get("result") or {}
        self.plans.merge_home(dashboard.get("homeState"), dashboard.get("heatingModePresent"), dashboard.get("heatingModeAway"))
        return merged

    # -- rounds --------------------------------------------------------------

    async def _run(self, pull: bool) -> dict[str, Any]:
        """Run one sync under the lock: push pending changes, optionally pull the remote state, and record the outcome
        in ``status``."""
        async with self._lock:
            cloud = await self._client()
            self.status["configured"] = cloud is not None
            if cloud is None:
                return dict(self.status)
            self.status["running"] = True
            try:
                gateway_id = await self._gateway(cloud)
                if not gateway_id:
                    raise TelekomError("Kein Gateway im Telekom-Konto gefunden.")
                errors = await self._push(cloud, gateway_id)
                if pull:
                    merged = await self._pull(cloud, gateway_id)
                    if merged["added"] or merged["removed"]:
                        self.context.event(f"Abgleich: {merged['added']} neu, {merged['removed']} entfernt, "
                                           f"{merged['updated']} aktualisiert")
                    self.status["last_sync"] = time.time()
                else:
                    self.plans.touch()
                self.status["application_id"] = application_id(cloud.access_token or "")
                self.status["ok"] = not errors
                self.status["last_error"] = "; ".join(errors) if errors else None
            except Exception as error:
                self.status["ok"] = False
                self.status["last_error"] = _text(error)
            finally:
                self.status["running"] = False
                self._last_run = time.time()
            return dict(self.status)

    async def sync(self, pull: bool = True, force: bool = False) -> dict[str, Any]:
        """Run one round. Without ``force`` a round that follows the last one too closely is skipped."""
        # Asked by a page that was just opened: never queue behind a running round and never run two in a row –
        # every waiting request keeps a connection of the browser busy (phones allow only a handful).
        if pull and not force and (self._lock.locked() or time.time() - self._last_run < MIN_GAP_SECONDS):
            return self.public_status()
        await self._run(pull)
        return self.public_status()

    def public_status(self) -> dict[str, Any]:
        """The status for the user interface."""
        return {**self.status, "configured": self.configured(), "interval": SYNC_SECONDS, "name": "Magenta"}

    # -- background ----------------------------------------------------------

    def start(self) -> None:
        """Start the periodic sync."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        """Stop the periodic sync and close the Telekom session."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self.reset()

    async def _loop(self) -> None:
        """Sync in the background at a fixed interval; a failed run is logged by its error type and the loop
        continues."""
        await asyncio.sleep(START_DELAY_SECONDS)
        while True:
            if self.configured():
                try:
                    await self._run(pull=True)
                except Exception as error:
                    self.context.log.warning("Magenta sync failed: %s", type(error).__name__)
            await asyncio.sleep(SYNC_SECONDS)


def _text(error: BaseException) -> str:
    """Message for the user: the text of our own errors, otherwise only the type (never internals)."""
    return str(error) if isinstance(error, PluginError) else type(error).__name__
