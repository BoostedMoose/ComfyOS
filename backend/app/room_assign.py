"""Room assignment of the devices – the Home Base is the room master.

assignments.json (in data/):
  { "<device_id>": {"target": "<room key>", "base": "<room of the home base when assigning>", "ts": …} }

Room keys:
  * room id of the Home Base (hz_1, room2168…) – for floor plan rooms that are linked to a
    room of the Magenta app,
  * "plan:<id>" – floor plan room that does not exist in the Magenta app (yet).
    If the floor plan room is linked later, the assignment automatically points
    to the linked room.

An assignment is a *local deviation* from the Home Base. It resolves
itself as soon as the Home Base reports the device in the target room (e.g. after it
was moved in the Magenta app). If someone moves the device to a third room in the
app, the newer change from the app applies and the local
assignment is dropped.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from . import floorplan_store, jsonfile
from .paths import DATA_DIR, ensure_dirs

ASSIGN_FILE = DATA_DIR / "assignments.json"
PLAN_PREFIX = "plan:"
_lock = threading.Lock()


def _load() -> dict[str, Any]:
    """Assignments from assignments.json; empty if the file is missing or unreadable."""
    return jsonfile.read(ASSIGN_FILE, {})


def _save(data: dict[str, Any]) -> None:
    """Atomar nach assignments.json schreiben."""
    ensure_dirs()
    jsonfile.write(ASSIGN_FILE, data)


def _plan_rooms(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Rooms of the floor plan – of all its floors – by their id."""
    return {r["id"]: r for r in floorplan_store.rooms(plan) if r.get("id")}


def resolve(target: str | None, plan: dict[str, Any]) -> str | None:
    """plan:<id> -> linked Home Base room id, if there is one."""
    if target and target.startswith(PLAN_PREFIX):
        r = _plan_rooms(plan).get(target[len(PLAN_PREFIX):])
        if r is None:
            return None                         # the floor plan room no longer exists
        return r.get("room_id") or target
    return target


def target_name(key: str, plan: dict[str, Any], names: dict[str, str]) -> str:
    """Display name of a room key: room of the floor plan (`plan:<id>`) or room of the hub."""
    if key.startswith(PLAN_PREFIX):
        r = _plan_rooms(plan).get(key[len(PLAN_PREFIX):])
        return (r or {}).get("name") or "Raum"
    return names.get(key) or key


def assign(device_id: str, target: str | None, base: str | None, plan: dict[str, Any]) -> None:
    """target=None or equal to the room of the Home Base -> lift the assignment."""
    with _lock:
        data = _load()
        if not target or resolve(target, plan) == base:
            data.pop(device_id, None)
        else:
            data[device_id] = {"target": target, "base": base, "ts": int(time.time())}
        _save(data)


def apply(devices: list[dict[str, Any]], plan: dict[str, Any], names: dict[str, str],
          complete: bool = True) -> list[dict[str, Any]]:
    """Set the effective room per device (copies), clean up resolved assignments.

    complete=False: the list is incomplete (a source did not deliver just now) – then assignments of
    missing devices are not discarded, otherwise pending room changes would be lost only because the hub was briefly away.

    Every device gets  hb_room_id  (room according to the Home Base) and, if it deviates
    locally,  room_pending: True.
    """
    with _lock:
        data = _load()
        changed = False
        out = []
        known = set()
        for d in devices:
            d = dict(d)
            hb = d.get("room_id")
            d["hb_room_id"] = hb
            d["hb_room"] = d.get("room")
            a = data.get(d.get("id"))
            known.add(d.get("id"))
            if a:
                eff = resolve(a.get("target"), plan)
                if eff is None or eff == hb or (a.get("base") != hb):
                    # target gone / arrived in the Home Base / moved differently in the app
                    data.pop(d["id"], None)
                    changed = True
                else:
                    d["room_id"] = eff
                    d["room"] = target_name(eff, plan, names)
                    d["room_pending"] = True
            out.append(d)
        if devices and complete:   # assignments for devices that no longer exist
            for did in [k for k in data if k not in known]:
                data.pop(did, None)
                changed = True
        if changed:
            _save(data)
    return out


def pending(devices: list[dict[str, Any]], plan: dict[str, Any], magenta_names: dict[str, str],
            own_names: dict[str, str]) -> dict[str, Any]:
    """What still has to be brought up to date in the Magenta app (floor plan = master)."""
    moves = [{"device_id": d["id"], "device": d.get("name"), "from": d.get("hb_room") or "Nicht zugeordnet",
              "to": d.get("room")} for d in devices if d.get("room_pending")]
    creates = [{"plan_id": r["id"], "name": r.get("name")} for r in floorplan_store.rooms(plan) if not r.get("room_id")]
    renames = [{"room_id": rid, "from": magenta_names[rid], "to": name} for rid, name in own_names.items()
               if rid in magenta_names and name and name != magenta_names[rid]]
    return {"moves": moves, "creates": creates, "renames": renames,
            "count": len(moves) + len(creates) + len(renames)}
