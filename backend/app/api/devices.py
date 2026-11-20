"""Devices: list, switching, names, removing, home page."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import (core, device_store, effects_store, events, floorplan_store, flow_engine, heating_sync, home_store,
                routines_store, scenes_store, settings_store, weather)

router = APIRouter()


class DeviceCommand(BaseModel):
    """Command to a device: the wanted values, e.g. {on: true, brightness: 60}."""
    state: dict = Field(default_factory=dict, max_length=40)


class LightsBody(BaseModel):
    """Switch lights: on/off, optionally only in certain rooms and with a brightness."""
    on: bool
    room_ids: list[str] | None = Field(None, max_length=100)   # None = all rooms
    brightness: float | None = Field(None, ge=1, le=100)   # dimmable lamps only, only when switching on


@router.get("/api/devices")
async def devices():
    """All devices with their current state."""
    return await core.all_devices()


@router.post("/api/devices/{device_id:path}/command")
async def command(device_id: str, cmd: DeviceCommand):
    """Send a command to a device. Lock, licence and limits are checked by core.apply_device."""
    d = next((x for x in await core.all_devices() if x.get("id") == device_id), None)
    if d is None:
        raise HTTPException(404, "Gerät nicht gefunden")
    return await core.apply_device(d, cmd.state)


@router.post("/api/devices/{device_id:path}/lock")
async def device_lock(device_id: str):
    """Lock a device: neither user nor flow, scene or "Alle Lichter" switch it until it is unlocked.
    Only affects ComfyOS – wall switches, the Hue app or the Magenta app keep switching."""
    d = next((x for x in await core.all_devices() if x.get("id") == device_id), None)
    if d is None:
        raise HTTPException(404, "Gerät nicht gefunden")
    try:
        changed = device_store.lock(device_id, "user")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if changed:
        events.log("device", f"{d.get('name') or device_id} gesperrt durch Nutzer")
        core.publish({"type": "device", "id": device_id, "locked": True})
    return {"ok": True, "locked": True}


@router.post("/api/devices/{device_id:path}/unlock")
async def device_unlock(device_id: str):
    """Lift the lock of a device and report that to all open pages."""
    if device_store.unlock(device_id):
        name = device_id
        try:
            d = next((x for x in await core.all_devices() if x.get("id") == device_id), None)
            name = (d or {}).get("name") or device_id
        except HTTPException:
            pass   # devices cannot be fetched right now: then the id is written to the event log
        events.log("device", f"{name} entsperrt durch Nutzer")
        core.publish({"type": "device", "id": device_id, "locked": False})
    return {"ok": True, "locked": False}


@router.post("/api/lights")
async def api_lights(body: LightsBody):
    """Switch the lights in the house or in single rooms on or off."""
    return await core.apply_lights(body.on, body.room_ids, brightness=body.brightness)


class DeviceRemove(BaseModel):
    """Remove a device or bring it back."""
    device_id: str = Field(min_length=1, max_length=160)
    removed: bool = True


class DeviceName(BaseModel):
    """Own name of a device (empty = name from the plugin)."""
    name: str | None = Field(None, max_length=200)


@router.post("/api/devices/remove")
async def device_remove(body: DeviceRemove):
    """Remove a device from ComfyOS or restore it. The source (Home Base, bridge) stays unchanged;
    an adopted device of a plugin goes back to its "Neu" list."""
    did = body.device_id
    if not body.removed:
        device_store.set_hidden(did, None, False)
        events.log("device", f"{did} in ComfyOS wiederhergestellt")
        core.publish({"type": "reload"})
        return {"ok": True}
    d = next((x for x in await core.all_devices() if x.get("id") == did), None)
    if d is None:
        raise HTTPException(404, "Gerät nicht gefunden.")
    if d.get("adopted"):
        device_store.set_adopted(did, False)
    else:
        device_store.set_hidden(did, d.get("name"), True)
    events.log("device", f"entfernt: {d.get('name', did)}")
    core.publish({"type": "reload"})
    return {"ok": True}


@router.put("/api/devices/{device_id:path}/name")
async def device_set_name(device_id: str, body: DeviceName):
    """Set or reset an individual name for a device (name=null).
    If the source of the device keeps names of its own (Hue Bridge → Hue app), the effective name is transferred there."""
    name = (body.name or "").strip()[:80] or None
    try:
        device_store.set_name(device_id[:160], name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    synced = None
    try:
        d = next((x for x in await core.all_devices() if x.get("id") == device_id), None)
    except HTTPException:
        d = None
    if d is not None:
        synced = await core.push_name(d)
    core.publish({"type": "reload"})
    return {"ok": True, "synced": synced}


@router.get("/api/overview")
async def overview():
    """Everything for the home page in one call (saves requests on the phone)."""
    devs: list[dict] = []
    err = None
    try:
        devs = await core.all_devices()
    except HTTPException as exc:
        err = exc.detail
    st = core.system_status()      # after the device query: the plugins then know their current state
    home = home_store.get()
    rs = routines_store.state()
    names = {r["key"]: r["name"] for r in rs["routines"]}
    room_list = core.rooms_overview(devs)
    hub = core.room_hub()
    if hub is not None:
        try:
            present = {r.get("room_id") for r in room_list}
            for rr in await hub[1].rooms():     # empty rooms of the hub too
                if rr.id not in present:
                    room_list.append({"room_id": rr.id, "name": rr.name or rr.id,
                                      "temps": [], "targets": [], "devices": 0, "offline": 0,
                                      "open": 0, "thermostats": 0, "temperature": None, "target": None})
        except Exception:
            pass   # hub unreachable: then only the rooms that contain devices
    hr = routines_store.heat_range()
    core.seed_scene_rooms([r["room_id"] for r in room_list if r.get("room_id")])   # assign the templates to the rooms once
    return {"status": st, "devices": devs, "devices_error": err, "rooms": room_list, "effects": effects_store.get()["rooms"],
            "floorplan": floorplan_store.get(), "home": {**home, "present_name": names.get(home.get("present_routine")),
                                                       "away_name": names.get(home.get("away_routine"))},
            "routines": rs, "sync": heating_sync.public_status(), "integrations": core.integrations(), "room_pending": core.room_pending(devs),
            "scenes": scenes_store.public(), "room_order": floorplan_store.card_order(),
            "layout_home": (settings_store.load_settings().get("layout") or {}).get("home", {}),
            "heat_range": list(hr) if hr else None, "geo": _geo(),
            "power": {k: v for k, v in core.power_summary(devs).items() if k in ("since", "kwh_since_reset", "kwh_today")}}


def _geo() -> list[float]:
    """Place for the position of the sun (shadow in the floor plan): the place found by the weather, otherwise the
    centre of Germany. Stays in the home network; rounded to 0.1°."""
    g = weather._read_cache()
    if isinstance(g.get("lat"), (int, float)) and isinstance(g.get("lon"), (int, float)):
        return [round(float(g["lat"]), 1), round(float(g["lon"]), 1)]
    return list(flow_engine.FALLBACK_GEO)
