"""Home/away (home state) – global for ComfyOS.

As in the Magenta app: PRESENT = at home, AWAY = away. Each state is
linked to a heating routine (present_routine / away_routine). With an active
Magenta sync the state is written to the Home Base with setHomeState;
the Home Base then switches to the linked heating routine by itself.

'pending' marks a local change that has not been synced to the Home Base
yet – until then a sync does not overwrite it.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from . import jsonfile
from .paths import HOME_FILE, ensure_dirs

STATES = ("PRESENT", "AWAY")
_lock = threading.Lock()


def _load() -> dict[str, Any]:
    """State from home.json; without a readable file: at home, no routines assigned."""
    return jsonfile.read(HOME_FILE, {"state": "PRESENT", "present_routine": None, "away_routine": None,
                                     "changed_at": None, "pending": False, "mapping_pending": False})


def _save(data: dict[str, Any]) -> None:
    """Stand atomar nach home.json schreiben."""
    ensure_dirs()
    jsonfile.write(HOME_FILE, data, ensure_ascii=True)


def get() -> dict[str, Any]:
    """Current state: state (PRESENT/AWAY), assigned heating routines, changes not synced yet."""
    with _lock:
        return _load()


def set_state(state: str) -> dict[str, Any]:
    """Set at home or away. The change counts as not synced yet (`pending`) until a heating sync
    has transferred it."""
    state = state.upper()
    if state not in STATES:
        raise ValueError("Zustand muss PRESENT oder AWAY sein.")
    with _lock:
        data = _load()
        data.update(state=state, changed_at=time.time(), pending=True)
        _save(data)
        return data


def set_mapping(present: str | None, away: str | None) -> dict[str, Any]:
    """Set the heating routine per state (None leaves the previous one). The assignment then counts as not
    synced yet."""
    with _lock:
        data = _load()
        if present:
            data["present_routine"] = present
        if away:
            data["away_routine"] = away
        data["mapping_pending"] = True
        _save(data)
        return data


def mark_synced(state: bool = True, mapping: bool = False) -> None:
    """After a successful sync: state and/or assignment count as transferred."""
    with _lock:
        data = _load()
        if state:
            data["pending"] = False
        if mapping:
            data["mapping_pending"] = False
        _save(data)


def apply_remote(state: str | None, present: str | None, away: str | None) -> None:
    """Take over the state of the Home Base without overwriting pending local changes."""
    with _lock:
        data = _load()
        if state in STATES and not data.get("pending"):
            data["state"] = state
        if not data.get("mapping_pending"):
            if present:
                data["present_routine"] = present
            if away:
                data["away_routine"] = away
        _save(data)


def rekey(old: str, new: str) -> None:
    """Replace a routine key in the assignment – a locally created routine got its final
    key during the sync."""
    with _lock:
        data = _load()
        changed = False
        for k in ("present_routine", "away_routine"):
            if data.get(k) == old:
                data[k] = new
                changed = True
        if changed:
            _save(data)


def routine_for(state: str | None = None) -> str | None:
    """Key of the heating routine that belongs to a state (without an argument: to the current one)."""
    data = get()
    state = (state or data.get("state") or "PRESENT").upper()
    return data.get("present_routine") if state == "PRESENT" else data.get("away_routine")
