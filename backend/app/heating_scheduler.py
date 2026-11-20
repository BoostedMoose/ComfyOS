"""Heating scheduler (since 0.23.1): ComfyOS runs the active heating routine itself.

Up to 0.23.0 a heating schedule only had an effect through the Magenta sync – the Home Base switched its thermostats,
without the sync the schedules were merely kept. Now the core switches by itself: at every switching point of the
active routine it sets the target temperature of the thermostats in that room – on the same path as a tap in the
interface (`core.apply_device`, device lock included).

Who switches which thermostat
    * Thermostats of the plugin whose system runs the schedule itself (`heating_sync.synced_plugin`, so far the
      Home Base with the Magenta sync configured) are left to it – two schedulers would get in each other's way.
    * Every other thermostat is switched by ComfyOS: FRITZ!DECT, MQTT, the Home Base without the sync …

Like a radiator thermostat it only switches at a switching point: a target set by hand (interface, flow) stays until
the next point. On start, after another routine became active or when the value of the section in force changes, the
rooms get their current value at once. What was set last per room is kept in memory only – after a restart every
room gets the current value of its schedule once (a thermostat that has it already gets no command).

The demo home has a state of its own: its live comparison (`core.demo_live_loop`) calls `tick` inside the demo, so the
real home never sees it and the demo never reaches a real thermostat.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from . import core, demo, heating_sync, routines_store, tasks

TICK = 60.0              # s – how often the loop compares the schedule with what was set
SOURCE = "schedule"      # switching source for the event log: " (Heizplan)" (core.source_tag)
TASK = "heizplan"        # name of the background task and of its reports

# Per home (False = real, True = demo): {room id: (routine key, day, time, temp)} – the switching point set last.
_applied: dict[bool, dict[str, tuple[str, int, str, float]]] = {False: {}, True: {}}
_running: set[bool] = set()     # homes whose round is running right now
_again: set[bool] = set()       # homes for which a further round was asked for while one was running


def _minutes(hhmm: Any) -> int | None:
    """'06:30' -> 390; None if it is not a time of day."""
    try:
        h, m = str(hhmm).split(":")
        value = int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value < 1440 else None


def _temp(value: Any) -> float | None:
    """Set point as a number, None for anything else (also an unresolved level)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return float(value)


def point_in_force(days: dict[str, Any], now: datetime) -> tuple[int, str, float] | None:
    """The switching point that applies to a room at `now`: (weekday, time, temp). That is the last point of today up
    to now, otherwise the last point of the days before (up to the same weekday a week ago). None without any point."""
    today, at = now.weekday(), now.hour * 60 + now.minute
    for back in range(8):
        day = (today - back) % 7
        best: tuple[int, str, float] | None = None
        best_min = -1
        for p in days.get(str(day)) or []:
            if not isinstance(p, dict):
                continue
            m, t = _minutes(p.get("time")), _temp(p.get("temp"))
            if m is None or t is None or (back == 0 and m > at):
                continue
            if m >= best_min:
                best, best_min = (day, p["time"], t), m
        if best is not None:
            return best
    return None


def plan(now: datetime | None = None) -> dict[str, tuple[str, int, str, float]]:
    """Per room of the active routine the switching point in force: {room id: (routine key, day, time, temp)}.
    Empty without an active routine."""
    key = routines_store.state().get("active")
    routine = routines_store.get(key) if key else None
    if not routine:
        return {}
    now = now or datetime.now()
    out = {}
    for room_id, days in (routine.get("schedule") or {}).items():
        point = point_in_force(days if isinstance(days, dict) else {}, now)
        if point is not None:
            out[str(room_id)] = (str(key), *point)
    return out


def _thermostat(d: dict, synced: str | None) -> bool:
    """A thermostat ComfyOS sets: switchable, still present – and not one whose own system runs the schedule."""
    if d.get("kind") != "thermostat" or not d.get("control") or d.get("link_missing"):
        return False
    return not (synced and synced in (d.get("plugin"), (d.get("control") or {}).get("plugin")))


def _same(current: Any, temp: float) -> bool:
    """Does the thermostat have this target already (to half a tenth)?"""
    value = _temp(current)
    return value is not None and abs(value - temp) < 0.05


async def _round(home: bool) -> int:
    """One comparison: set the target of every room whose switching point in force has not been set yet. Returns
    how many thermostats got a command. A room counts as done once each of its thermostats has the value (or is
    locked – the lock wins); an unreachable thermostat is tried again in the next round."""
    want, applied = plan(), _applied[home]
    for room in [r for r in applied if r not in want]:
        applied.pop(room)
    due = {room: point for room, point in want.items() if applied.get(room) != point}
    if not due:
        return 0
    synced = heating_sync.synced_plugin()
    devices = core.effective(await core.merged_devices(max_age=TICK / 2))
    switched = 0
    for room, point in due.items():
        temp = point[3]
        found, done = False, True
        for d in devices:
            if d.get("room_id") != room or d.get("kind") != "thermostat":
                continue
            found = True     # also a thermostat of the synced system: the room is known, nothing missing
            if not _thermostat(d, synced) or d.get("locked") or _same(d.get("target"), temp):
                continue
            if not d.get("online"):
                done = False
                continue
            try:
                await core.apply_device(d, {"target": temp}, SOURCE)
                switched += 1
            except Exception as exc:
                done = False
                tasks.report(TASK, type(exc).__name__, "Solltemperatur nicht gesetzt")
        # a room without any thermostat in the list stays due: right after the start a plugin may not have
        # reported its devices yet
        if found and done:
            applied[room] = point
    return switched


async def tick() -> int:
    """Compare the schedule with what was set and switch what is due – for the home of the running request or task
    (real or demo). If a round is running already, it is repeated once afterwards instead of running twice."""
    home = demo.active()
    if home in _running:
        _again.add(home)
        return 0
    _running.add(home)
    switched = 0
    try:
        while True:
            _again.discard(home)
            switched += await _round(home)
            if home not in _again:
                return switched
    finally:
        _running.discard(home)


def kick() -> None:
    """Switch at once after a change (routine activated, schedule changed, home/away) – in the background, the
    request does not wait. In the demo it switches the demo home."""
    tasks.fire(tick(), TASK)


async def loop() -> None:
    """Long runner of the real home (tasks.spawn): compare every TICK seconds – the first time one TICK after the
    start, when the plugins have reported their devices. A failed round is reported, the next one follows."""
    while True:
        await asyncio.sleep(TICK)
        try:
            await tick()
        except Exception as exc:
            tasks.report(TASK, type(exc).__name__)
