"""Heating routines – ComfyOS is the source of truth (data/routines.json).

The Magenta sync (optional) syncs automatically in both directions:
  * Pull: take over routines, schedules, room names, active routine from the app.
  * Push: write local changes to the app.

So that a pull does not overwrite a local change that has not been transferred yet,
every routine carries marks:
  dirty           schedule changed -> setHeatingTriggers
  pending_create  created in ComfyOS -> createNewHeatingMode (key 'local-…')
  pending_rename  renamed -> changeNameOfHeatingMode
In addition, globally: pending_deletes (deleted app routines) and active_pending.

Without the Magenta sync the marks simply stay and are worked off on the
first sync after it is enabled.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from . import jsonfile
from . import events, heating_model as heating
from .paths import ROUTINES_FILE, ensure_dirs

_lock = threading.Lock()


def _empty() -> dict[str, Any]:
    """Empty store with the default temperature levels."""
    return {"routines": {}, "room_names": {}, "levels": dict(heating.DEFAULT_LEVELS),
            "active": None, "active_pending": False, "pending_deletes": [], "last_sync": None}


def _load() -> dict[str, Any]:
    """Content of routines.json, completed with missing sections."""
    data = jsonfile.read(ROUTINES_FILE, None)
    if data is None:
        return _empty()
    for k, v in _empty().items():
        data.setdefault(k, v)
    return data


def _save(data: dict[str, Any]) -> None:
    """Atomar nach routines.json schreiben."""
    ensure_dirs()
    jsonfile.write(ROUTINES_FILE, data)


def is_local_key(key: str) -> bool:
    """Was the routine created in ComfyOS and not transferred to the synced system yet (key `local-…`)?"""
    return str(key).startswith("local-")


def _summary(r: dict[str, Any], active_key: str | None = None) -> dict[str, Any]:
    """Summary of a routine for the list, completed with `active` and `pending` (not synced yet)."""
    s = heating.routine_summary(r)
    s["active"] = r.get("key") == active_key   # data["active"] is decisive, not the field in the routine
    s["pending"] = bool(r.get("dirty") or r.get("pending_create") or r.get("pending_rename"))
    return s


# ---------------------------------------------------------------- lesen
def state() -> dict[str, Any]:
    """Overview for the interface: routines (active one first), active routine, room names, levels, last sync,
    number of pending changes."""
    with _lock:
        data = _load()
    routines = [_summary(r, data["active"]) for r in data["routines"].values()]
    routines.sort(key=lambda r: (not r["active"], str(r["name"]).lower()))
    pending = sum(1 for r in routines if r["pending"]) + len(data["pending_deletes"]) + (1 if data["active_pending"] else 0)
    return {"routines": routines, "active": data["active"], "room_names": data["room_names"],
            "levels": data["levels"], "last_sync": data["last_sync"], "pending": pending}


def get(key: str) -> dict[str, Any] | None:
    """A routine with room names and levels (as a copy), or None."""
    with _lock:
        data = _load()
        r = data["routines"].get(key)
        if r is None:
            return None
        out = json.loads(json.dumps(r))
        out["room_names"] = data["room_names"]
        out["levels"] = data["levels"]
        out["active"] = (key == data["active"])
        return out


HEAT_MIN_SPAN = 4.0   # °C – narrower spans turn a 0.5 °C difference into the full colour gradient
HEAT_FLOOR = 10.0     # °C – below that is "off"/frost protection, not a heating target


def heat_range() -> tuple[float, float] | None:
    """Span of the heat map from the active heating routine: lowest to highest planned room temperature, rounded to
    whole degrees. "Off"/frost protection (≤ level OFF or below HEAT_FLOOR) does not count – otherwise the scale started
    at ~6 °C and all inhabited rooms looked equally warm. At least HEAT_MIN_SPAN wide."""
    import math
    with _lock:
        data = _load()
    key = data.get("active")
    r = data["routines"].get(key) if key else None
    if not r:
        return None
    off = (data.get("levels") or {}).get("OFF")
    off = float(off) if isinstance(off, (int, float)) and not isinstance(off, bool) else None
    temps = []
    for days in (r.get("schedule") or {}).values():
        for pts in (days if isinstance(days, dict) else {}).values():
            for p in (pts if isinstance(pts, list) else []):
                t = p.get("temp") if isinstance(p, dict) else None
                if not isinstance(t, (int, float)) or isinstance(t, bool) or t != t:
                    continue
                if t < HEAT_FLOOR or (off is not None and t <= off + 0.01):
                    continue
                temps.append(float(t))
    if not temps:
        return None
    lo, hi = min(temps), max(temps)
    if hi - lo < HEAT_MIN_SPAN:
        c = (lo + hi) / 2
        lo, hi = c - HEAT_MIN_SPAN / 2, c + HEAT_MIN_SPAN / 2
    return (float(math.floor(lo)), float(math.ceil(hi)))


def set_remote_room_name(room_id: str, name: str | None) -> None:
    """Update the cached room name of the Magenta app (after an own change)."""
    with _lock:
        data = _load()
        names = data.setdefault("room_names", {})
        if name:
            names[room_id] = name
        else:
            names.pop(room_id, None)
        _save(data)


def room_names() -> dict[str, str]:
    """Room names from the synced system {room id: name}."""
    with _lock:
        return dict(_load()["room_names"])


def levels() -> dict[str, float]:
    """Temperaturstufen (Komfort, Eco, Aus …) in °C."""
    with _lock:
        return dict(_load()["levels"])


def write_payload(key: str) -> dict[str, Any] | None:
    """Weekly schedule of a routine in the form in which it is written to the synced system."""
    r = get(key)
    return heating.schedule_for_write(r) if r else None


# ---------------------------------------------------------------- changing locally
def update_schedule(key: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace several room/day schedules in one step (edit + copy).
    changes: [{room, day, points:[{time,temp}]}]"""
    with _lock:
        data = _load()
        routine = data["routines"].get(key)
        if routine is None:
            raise KeyError(key)
        for ch in changes:
            heating.apply_room_day(routine, str(ch["room"]), int(ch["day"]), ch.get("points") or [])
        if not is_local_key(key):
            routine["dirty"] = True
        _save(data)
        names = data["room_names"]
    rooms = sorted({str(c["room"]) for c in changes})
    days = sorted({int(c["day"]) for c in changes})
    events.log("heating", f"Heizplan '{routine.get('name', key)}' geändert: "
                          f"{', '.join(names.get(r, r) for r in rooms)} · {', '.join(heating.DAYS[d] for d in days)}")
    return get(key)  # type: ignore[return-value]


def create(name: str, copy_from: str | None = None) -> dict[str, Any]:
    """Create a new routine, empty or as a copy of an existing one. It counts as not transferred yet
    (`pending_create`)."""
    with _lock:
        data = _load()
        key = "local-" + uuid.uuid4().hex[:8]
        base = data["routines"].get(copy_from) if copy_from else None
        data["routines"][key] = {
            "key": key, "name": name, "source": "local", "pending_create": True,
            "rooms": list(base.get("rooms", [])) if base else [],
            "schedule": json.loads(json.dumps(base.get("schedule", {}))) if base else {}}
        _save(data)
    events.log("heating", f"Heizroutine '{name}' angelegt")
    return get(key)  # type: ignore[return-value]


def rename(key: str, name: str) -> None:
    """Rename a routine and mark the change for the sync. Raises KeyError if it does not exist."""
    with _lock:
        data = _load()
        r = data["routines"].get(key)
        if r is None:
            raise KeyError(key)
        r["name"] = name
        if not is_local_key(key):
            r["pending_rename"] = True
        _save(data)


def delete(key: str) -> None:
    """Delete a routine. One that was already transferred is marked for deletion in the synced system."""
    with _lock:
        data = _load()
        r = data["routines"].pop(key, None)
        if r is None:
            return
        if not is_local_key(key) and key not in data["pending_deletes"]:
            data["pending_deletes"].append(key)
        if data["active"] == key:
            data["active"] = None
        _save(data)
    events.log("heating", f"Heizroutine '{r.get('name', key)}' gelöscht")


def set_active(key: str, pending: bool = True) -> None:
    """Set a routine as the active one. `pending`: the switch still has to be transferred (not for local routines).
    Raises KeyError if it does not exist."""
    with _lock:
        data = _load()
        if key not in data["routines"]:
            raise KeyError(key)
        data["active"] = key
        data["active_pending"] = pending and not is_local_key(key)
        _save(data)


# ---------------------------------------------------------------- Sync-Hilfen
def pending_work() -> dict[str, Any]:
    """What the heating sync still has to transfer: deletions, new routines, changed schedules, renames,
    the switch."""
    with _lock:
        data = _load()
    rs = data["routines"]
    return {"deletes": list(data["pending_deletes"]),
            "creates": [k for k, r in rs.items() if r.get("pending_create")],
            "updates": [k for k, r in rs.items() if r.get("dirty") and not is_local_key(k)],
            "renames": [k for k, r in rs.items() if r.get("pending_rename") and not is_local_key(k)],
            "activate": data["active"] if data["active_pending"] else None}


def clear_flag(key: str, flag: str) -> None:
    """Delete a mark after a successful transfer (`delete`, `activate` or a flag on the routine)."""
    with _lock:
        data = _load()
        if flag == "delete":
            data["pending_deletes"] = [k for k in data["pending_deletes"] if k != key]
        elif flag == "activate":
            data["active_pending"] = False
        elif key in data["routines"]:
            data["routines"][key].pop(flag, None)
        _save(data)


def rekey(old: str, new: str) -> None:
    """Switch a local routine to the app's key after it was created there."""
    with _lock:
        data = _load()
        r = data["routines"].pop(old, None)
        if r is None:
            return
        r["key"] = new
        r["source"] = "telekom"
        r.pop("pending_create", None)
        r.pop("dirty", None)
        data["routines"][new] = r
        if data["active"] == old:
            data["active"] = new
        aliases = data.setdefault("aliases", {})
        aliases[old] = new
        while len(aliases) > 50:
            aliases.pop(next(iter(aliases)))
        _save(data)


def resolve_key(key: str) -> str:
    """Map the local key to the new key after the routine was created in the app."""
    with _lock:
        data = _load()
    return data.get("aliases", {}).get(key, key)


def merge_remote(routines: list[Any], remote_active: str | None, levels: dict[str, float],
                 room_names: dict[str, str]) -> dict[str, int]:
    """Take over the state of the synced system (routines: plugin_api.RemoteRoutine). Local changes that have
    not been transferred yet stay."""
    lv = dict(heating.DEFAULT_LEVELS)
    lv.update({k: float(v) for k, v in (levels or {}).items()
               if k in heating.DEFAULT_LEVELS and isinstance(v, (int, float)) and not isinstance(v, bool)})
    room_names = {str(k): str(v) for k, v in (room_names or {}).items() if k not in heating.NON_ROOMS and v}
    added = updated = removed = 0
    with _lock:
        data = _load()
        rs = data["routines"]
        remote_keys = set()
        for remote in routines:
            key = getattr(remote, "key", None)
            if not key or key in data["pending_deletes"]:
                continue
            remote_keys.add(key)
            fresh = heating.build_routine(key, getattr(remote, "name", None), getattr(remote, "last_run", None),
                                          getattr(remote, "schedule", None) or {}, lv, remote_active)
            local = rs.get(key)
            if local is None:
                fresh.pop("active", None)
                rs[key] = fresh
                added += 1
                continue
            local["last_execution"] = fresh.get("last_execution")
            local["source"] = "telekom"
            if not local.get("pending_rename") and local.get("name") != fresh["name"]:
                local["name"] = fresh["name"]
                updated += 1
            if not local.get("dirty") and local.get("schedule") != fresh["schedule"]:
                local["schedule"] = fresh["schedule"]
                local["rooms"] = fresh["rooms"]
                updated += 1
        for key in [k for k, r in rs.items() if not is_local_key(k) and k not in remote_keys]:
            if rs[key].get("dirty"):
                # deleted in the app but changed locally -> keep as a new routine
                r = rs.pop(key)
                nk = "local-" + uuid.uuid4().hex[:8]
                r.update(key=nk, source="local", pending_create=True)
                r.pop("dirty", None)
                rs[nk] = r
            else:
                rs.pop(key)
                removed += 1
        if remote_active and not data["active_pending"]:
            data["active"] = remote_active
        data["levels"] = lv
        if room_names:
            data["room_names"] = room_names
        data["last_sync"] = time.time()
        _save(data)
    return {"added": added, "updated": updated, "removed": removed}


def mark_synced_now() -> None:
    """Zeitpunkt des letzten Abgleichs auf jetzt setzen."""
    with _lock:
        data = _load()
        data["last_sync"] = time.time()
        _save(data)
