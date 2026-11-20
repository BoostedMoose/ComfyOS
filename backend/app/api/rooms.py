"""Rooms and room assignment of the devices (floor plan = master).

If a hub keeps a room list of its own (e.g. the Home Base; `core.room_hub`), ComfyOS writes rooms and assignments
there – the app of the hub then shows the same room list. Without a hub, rooms live in the floor plan alone and the
assignment of the devices in the device store.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import core, device_store, events, floorplan_store, plugins, room_assign, routines_store

router = APIRouter()


class RoomName(BaseModel):
    """Name eines Raums."""
    name: str | None = Field(None, max_length=200)


def _same(a: object, b: object) -> bool:
    """Are two room names equal? Upper/lower case and whitespace do not count."""
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


@router.get("/api/rooms")
async def api_rooms():
    """Rooms with their devices and key figures (room tiles)."""
    return core.rooms_overview(await core.all_devices())


@router.get("/api/rooms/manage")
async def api_rooms_manage():
    """Complete room list of the hub, including empty rooms. Without a hub: the rooms known in ComfyOS."""
    devices = await core.all_devices()
    counts: dict[str, int] = {}
    for d in devices:
        rid = d.get("hb_room_id")
        if rid:
            counts[rid] = counts.get(rid, 0) + 1
    hub = core.room_hub()
    if hub is None:
        return [{"room_id": r["room_id"], "name": r["name"], "devices": r["devices"]}
                for r in core.rooms_overview(devices) if r.get("room_id")]
    return [{"room_id": r.id, "name": r.name or r.id, "devices": counts.get(r.id, 0)}
            for r in await core.hub_call(hub[1].rooms())]


@router.post("/api/rooms")
async def api_room_create(body: RoomName):
    """Create a room. If a hub manages the rooms, it is created there – if it already exists, the existing one comes
    back. Without a hub the room lives in the floor plan alone, there is nothing to create here then."""
    name = " ".join((body.name or "").split())[:40]
    if not name:
        raise HTTPException(400, "Raumname fehlt.")
    if core.room_hub() is None:
        # No hub manages rooms: the room lives in the floor plan alone (key plan:<id>), nothing to create here
        return {"ok": True, "created": False, "room_id": None, "name": name}
    _, hub = core.need_room_hub()
    match = next((r for r in await core.hub_call(hub.rooms()) if _same(r.name, name)), None)
    if match:
        return {"ok": True, "created": False, "room_id": match.id, "name": match.name or name}
    try:
        rid = await hub.create_room(name)
        routines_store.set_remote_room_name(rid, name)
        events.log("room", f"Raum „{name}“ angelegt")
        return {"ok": True, "created": True, "room_id": rid, "name": name}
    except Exception as exc:
        raise HTTPException(502, f"Raum konnte nicht angelegt werden: {plugins.error_text(exc)}")


@router.delete("/api/rooms/{room_id}")
async def api_room_delete(room_id: str):
    """Delete a room in the hub and remove it everywhere in ComfyOS: own name, heating routines, link in the floor plan."""
    _, hub = core.need_room_hub()
    try:
        await hub.delete_room(room_id)
        floorplan_store.set_room_name(room_id, None)
        routines_store.set_remote_room_name(room_id, None)
        plan = floorplan_store.get()
        for r in floorplan_store.rooms(plan):
            if r.get("room_id") == room_id:
                r["room_id"] = None
        floorplan_store.save(plan)
        events.log("room", f"Raum {room_id} gelöscht")
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(409, plugins.error_text(exc))


class RoomAssign(BaseModel):
    """Room a device should go to (empty = none)."""
    target: str | None = Field(None, max_length=200)


async def _hub_room_for(target: str, plan: dict) -> str:
    """Room key -> room id of the hub. Floor plan rooms without a link are looked up there by name and linked in
    the floor plan (floor plan = master)."""
    if not target.startswith(room_assign.PLAN_PREFIX):
        return target
    pid = target[len(room_assign.PLAN_PREFIX):]
    room = next((r for r in floorplan_store.rooms(plan) if r.get("id") == pid), None)
    if room is None:
        raise HTTPException(400, "Diesen Raum gibt es im Grundriss nicht.")
    if room.get("room_id"):
        return room["room_id"]
    # A device assignment must never create a room. Only the explicit
    # room dialog creates rooms; here rooms are exclusively linked by name.
    hub_id, hub = core.need_room_hub()
    match = next((r for r in await core.hub_call(hub.rooms()) if _same(r.name, room.get("name"))), None)
    if not match:
        raise HTTPException(409, "Raum existiert im Hub nicht. Bitte zuerst in der Raumverwaltung anlegen.")
    fresh = floorplan_store.get()
    for r in floorplan_store.rooms(fresh):
        if r.get("id") == pid:
            r["room_id"] = match.id
    floorplan_store.save(fresh)
    return match.id


@router.put("/api/devices/{device_id:path}/room")
async def api_device_room(device_id: str, body: RoomAssign):
    """Assign a device to a room (room id of the hub or plan:<id>; None = as in the hub).
    Devices of the room hub are moved there directly – its app reads the same room list. If that fails,
    the assignment stays local and is listed as a pending item. All other devices have their room in ComfyOS only."""
    devs = await core.all_devices()
    d = next((x for x in devs if x.get("id") == device_id), None)
    if d is None:
        raise HTTPException(404, "Gerät nicht gefunden.")
    plan = floorplan_store.get()
    target = (body.target or "").strip() or None
    if target and target.startswith(room_assign.PLAN_PREFIX) and room_assign.resolve(target, plan) is None:
        raise HTTPException(400, "Diesen Raum gibt es im Grundriss nicht.")
    hub = core.room_hub()
    if d.get("local_room"):
        # No hub manages the room of this device -> room in ComfyOS only (no pending item)
        try:
            device_store.set_room(d["id"], target)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        new = next((x for x in await core.all_devices() if x.get("id") == device_id), d)
        events.log("room", f"{d.get('name')}: {d.get('room')} → {new.get('room')} (nur ComfyOS)")
        note = "Raum in ComfyOS gespeichert" + (" – der Hub kennt dieses Gerät nicht." if hub else ".")
        return {"ok": True, "device": new, "pending": False, "note": note}
    warning = None
    if target:
        try:
            rid = await _hub_room_for(target, plan)
            if rid != d.get("hb_room_id"):
                await hub[1].move_device((d.get("source") or {}).get("device_id") or device_id, rid)
            room_assign.assign(device_id, None, None, plan)          # the hub is up to date now
        except HTTPException:
            raise
        except Exception as exc:
            warning = f"Hub nicht aktualisiert ({plugins.error_text(exc)}) – Zuordnung vorerst nur in ComfyOS."
            room_assign.assign(device_id, target, d.get("hb_room_id"), floorplan_store.get())
    else:
        room_assign.assign(device_id, None, None, plan)
    new = next((x for x in await core.all_devices() if x.get("id") == device_id), d)
    events.log("room", f"{d.get('name')}: {d.get('room')} → {new.get('room')}" + (" (nur lokal)" if warning else ""))
    return {"ok": True, "device": new, "pending": bool(new.get("room_pending")), "warning": warning}


@router.post("/api/rooms/push")
async def api_rooms_push():
    """Write pending room changes to the hub (link rooms, rename, move devices)."""
    _, hub = core.need_room_hub()
    devs = await core.all_devices()
    p = core.room_pending(devs)
    done, errors = {"creates": 0, "renames": 0, "moves": 0}, []
    for cr in p["creates"]:
        try:
            await _hub_room_for(room_assign.PLAN_PREFIX + cr["plan_id"], floorplan_store.get())
            done["creates"] += 1
        except Exception as exc:
            errors.append(f"Raum {cr['name']}: {plugins.error_text(exc)}")
    for rn in p["renames"]:
        try:
            await hub.rename_room(rn["room_id"], rn["to"])
            routines_store.set_remote_room_name(rn["room_id"], rn["to"])
            done["renames"] += 1
        except Exception as exc:
            errors.append(f"Umbenennen {rn['from']}: {plugins.error_text(exc)}")
    plan = floorplan_store.get()
    for d in await core.all_devices():
        if not d.get("room_pending"):
            continue
        try:
            rid = await _hub_room_for(d["room_id"], plan) if str(d["room_id"]).startswith(room_assign.PLAN_PREFIX) else d["room_id"]
            await hub.move_device((d.get("source") or {}).get("device_id") or d["id"], rid)
            room_assign.assign(d["id"], None, None, plan)
            done["moves"] += 1
        except Exception as exc:
            errors.append(f"{d.get('name')}: {plugins.error_text(exc)}")
    if any(done.values()):
        events.log("room", f"Raumänderungen übertragen: {done['creates']} angelegt, {done['renames']} umbenannt, {done['moves']} verschoben")
    return {"ok": not errors, "done": done, "errors": errors, "pending": core.room_pending(await core.all_devices())}


@router.get("/api/rooms/pending")
async def api_rooms_pending():
    """Room assignments that have not been transferred to the hub yet."""
    return core.room_pending(await core.all_devices())


@router.put("/api/rooms/{room_id}/name")
async def api_room_name(room_id: str, body: RoomName):
    """Rename a room. The name applies in ComfyOS at once; if a hub manages the room, it is transferred there – if the
    hub is unreachable, that stays as a pending item. 409 if another room already has this name."""
    name = (body.name or "").strip()
    hub = core.room_hub() if name and not room_id.startswith(room_assign.PLAN_PREFIX) else None
    if hub is not None:
        try:
            rooms = await hub[1].rooms()
        except Exception:
            rooms = []      # hub unreachable right now: the name stays as a pending item
        if any(r.id != room_id and _same(r.name, name) for r in rooms):
            raise HTTPException(409, "Ein Raum mit diesem Namen existiert bereits.")
    floorplan_store.set_room_name(room_id, body.name)
    synced = False
    if hub is not None:
        try:
            await hub[1].rename_room(room_id, name)
            routines_store.set_remote_room_name(room_id, name[:40])
            synced = True
        except Exception:
            synced = False   # bleibt als offener Punkt stehen
    return {"ok": True, "names": core.room_names(), "synced": synced}
