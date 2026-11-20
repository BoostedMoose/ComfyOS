"""Heating schedule core logic: weekly schedule in wire form <-> internal model of the heating routines.

The wire form is that of the plugin interface (`plugin_api.WeeklySchedule`); it corresponds to what the
Home Base returns through getHeatingTriggers (verified against real data):

    result = {
        "<day 0..6>": {               # 0 = Monday ... 6 = Sunday
            "<roomId>": {             # hz_1, room2168..., SYSTEM, ...
                "0600": "21",         # time HHMM -> set point
                "2230": "ECO"         #            or level name
            },
            ...
        }, ...
    }

Set points are either a number ("21", "16.5") or a level name
(COMFORT/ON/ECO/OFF). The levels are resolved through getDashboardInfo
(COMFORT=22, ON=20, ECO=15, OFF=6; other values per Home Base are possible).

A routine (= heating mode of the app) has a key ("AUTO"), a name
("Komfort") and per room and weekday a list of switching points.

All functions here are pure (no I/O), so that they can be tested without a Home Base.
"""
from __future__ import annotations

from typing import Any

DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]          # Index 0..6
DEFAULT_LEVELS = {"COMFORT": 22.0, "ON": 20.0, "ECO": 15.0, "OFF": 6.0}
# Rooms that are not a real heating area and are ignored.
NON_ROOMS = {"SYSTEM"}


def parse_time(hhmm: str) -> str:
    """'0600' -> '06:00'. Also tolerates values that are already formatted."""
    s = str(hhmm).strip()
    if ":" in s:
        return s
    s = s.zfill(4)
    return f"{s[:2]}:{s[2:]}"


def fmt_time(hm: str) -> str:
    """'06:00' -> '0600'."""
    return str(hm).replace(":", "").zfill(4)


def resolve_temp(value: Any, levels: dict[str, float]) -> float | None:
    """Set point (number or level name) -> temperature in °C."""
    if value is None:
        return None
    s = str(value).strip()
    if s.upper() in levels:
        return levels[s.upper()]
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def is_level(value: Any, levels: dict[str, float]) -> bool:
    """Is the value the name of a temperature level (e.g. COMFORT)?"""
    return isinstance(value, str) and value.strip().upper() in levels


def parse_triggers(result: dict[str, Any], levels: dict[str, float]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Raw result -> {roomId: {"day": [ {time, raw, temp}, ... sorted ] }}.
    Days as text keys "0".."6" (stable in JSON)."""
    schedule: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if not isinstance(result, dict):
        return schedule
    for day_key, rooms in result.items():
        try:
            day = str(int(day_key))
        except (TypeError, ValueError):
            continue   # not a weekday
        if not isinstance(rooms, dict):
            continue
        for room_id, points in rooms.items():
            if room_id in NON_ROOMS or not isinstance(points, dict):
                continue
            room = schedule.setdefault(room_id, {})
            day_list = room.setdefault(day, [])
            for hhmm, value in points.items():
                day_list.append({"time": parse_time(hhmm), "raw": value,
                                 "temp": resolve_temp(value, levels), "level": is_level(value, levels)})
            day_list.sort(key=lambda p: p["time"])
    return schedule


def serialize_triggers(schedule: dict[str, dict[int, dict[str, Any]]]) -> dict[str, dict[str, dict[str, str]]]:
    """Internal schedule -> raw result for writing (setHeatingTriggers).

    Expects schedule[roomId][day] = { 'HH:MM': <value>, ... } where <value> is a
    number or a level name. Returns {day: {roomId: {HHMM: '<value>'}}}."""
    out: dict[str, dict[str, dict[str, str]]] = {str(d): {} for d in range(7)}
    for room_id, days in schedule.items():
        for day, points in days.items():
            day_bucket = out.setdefault(str(day), {})
            room_bucket = day_bucket.setdefault(room_id, {})
            for hm, value in points.items():
                room_bucket[fmt_time(hm)] = _as_setpoint(value)
    return out


def _as_setpoint(value: Any) -> str:
    """Normalise a set point: level names stay, numbers are formatted tersely."""
    s = str(value).strip()
    if s.upper() in DEFAULT_LEVELS or not _is_number(s):
        return s.upper() if s.upper() in DEFAULT_LEVELS else s
    f = float(s.replace(",", "."))
    return f"{f:.1f}".rstrip("0").rstrip(".") if "." in f"{f}" else str(int(f))


def _is_number(s: str) -> bool:
    """Can the text be read as a number (comma or dot)?"""
    try:
        float(s.replace(",", "."))
        return True
    except ValueError:
        return False


def build_routine(key: str, name: str | None, last_run: Any, triggers_result: dict[str, Any],
                  levels: dict[str, float], active_key: str | None) -> dict[str, Any]:
    """Routine of the synced system (key, name, weekly schedule in wire form) -> routine object."""
    schedule = parse_triggers(triggers_result, levels)
    rooms = sorted(schedule.keys())
    return {
        "key": key,
        "name": name or key,
        "active": key == active_key,
        "last_execution": last_run,
        "rooms": rooms,
        "schedule": schedule,          # {raumId: {tag: [points]}}
        "source": "telekom",
    }


def routine_summary(routine: dict[str, Any]) -> dict[str, Any]:
    """Compact overview without the full schedule (for the routine list)."""
    schedule = routine.get("schedule", {})
    switch_points = sum(len(pts) for days in schedule.values() for pts in days.values())
    return {"key": routine.get("key"), "name": routine.get("name"), "active": routine.get("active", False),
            "rooms": len(routine.get("rooms", [])), "switch_points": switch_points,
            "source": routine.get("source", "local")}


def apply_room_day(routine: dict[str, Any], room_id: str, day: int,
                   points: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the switching points of a room on one day (for editing in ComfyOS).
    points: [{'time': 'HH:MM', 'temp': float}] -> stored as numeric set points."""
    schedule = routine.setdefault("schedule", {})
    room = schedule.setdefault(room_id, {})
    clean = []
    for p in sorted(points, key=lambda x: x.get("time", "")):
        t = parse_time(p.get("time", ""))
        temp = p.get("temp")
        if temp is None:
            continue
        clean.append({"time": t, "raw": _as_setpoint(temp), "temp": float(temp), "level": False})
    room[str(int(day))] = clean
    if room_id not in routine.get("rooms", []):
        routine.setdefault("rooms", []).append(room_id)
        routine["rooms"].sort()
    return routine


def schedule_for_write(routine: dict[str, Any]) -> dict[str, dict[str, dict[str, str]]]:
    """Routine -> raw result for setHeatingTriggers (uses the 'raw' values)."""
    flat: dict[str, dict[int, dict[str, Any]]] = {}
    for room_id, days in routine.get("schedule", {}).items():
        for day, pts in days.items():
            bucket = flat.setdefault(room_id, {}).setdefault(int(day), {})
            for p in pts:
                bucket[p["time"]] = p.get("raw", p.get("temp"))
    return serialize_triggers(flat)
