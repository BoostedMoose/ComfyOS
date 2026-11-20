"""Home/away and heating routines."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import core, heating_scheduler, heating_sync, home_store, routines_store

router = APIRouter()


class RoutineCreate(BaseModel):
    """New heating routine: name and optionally the routine it is a copy of."""
    name: str = Field(max_length=200)
    copy_from: str | None = Field(None, max_length=200)


class RoutineName(BaseModel):
    """New name of a heating routine."""
    name: str = Field(max_length=200)


class SwitchPoint(BaseModel):
    """Switching point in the weekly schedule: time and temperature."""
    time: str = Field(max_length=16)
    temp: float


class ScheduleChange(BaseModel):
    """New daily schedule for a room: weekday (0 = Monday) and switching points."""
    room: str = Field(max_length=200)
    day: int = Field(ge=0, le=6)
    points: list[SwitchPoint] = Field(default_factory=list, max_length=100)


class ScheduleBatch(BaseModel):
    """Several changes to the weekly schedule at once."""
    changes: list[ScheduleChange] = Field(max_length=1000)


class HomeState(BaseModel):
    """At home or away."""
    state: str = Field(max_length=20)


class HomeMapping(BaseModel):
    """Which heating routine belongs to at home and which to away."""
    present: str | None = Field(None, max_length=200)
    away: str | None = Field(None, max_length=200)


# ---------------------------------------------------------------- Zuhause / Abwesend
@router.get("/api/home")
async def get_home():
    """Home/away with the assigned heating routines."""
    return home_store.get()


@router.post("/api/home")
async def set_home(body: HomeState):
    """Set at home or away: the assigned heating routine becomes active and the heating sync is triggered."""
    return await core.apply_home(body.state)


@router.put("/api/home/mapping")
async def set_home_mapping(body: HomeMapping):
    """Assign heating routines to at home and away and sync right away."""
    for key in (body.present, body.away):
        if key and not routines_store.get(key):
            raise HTTPException(404, f"Routine {key} nicht gefunden")
    home_store.set_mapping(body.present, body.away)
    sync = await heating_sync.push_now()
    return {"home": home_store.get(), "sync": sync}


# ---------------------------------------------------------------- Heizroutinen
@router.get("/api/routines")
async def routines():
    """All heating routines with the state of the sync."""
    return {**routines_store.state(), "sync": heating_sync.public_status()}


@router.get("/api/routines/{key}")
async def routine_detail(key: str):
    """Eine Heizroutine mit Wochenplan."""
    r = routines_store.get(key)
    if r is None:
        raise HTTPException(404, "Routine nicht gefunden")
    return r


@router.post("/api/routines")
async def routine_create(data: RoutineCreate):
    """Create a heating routine (empty or as a copy) and sync right away. After the sync it carries its final
    key."""
    r = routines_store.create(data.name.strip() or "Neue Routine", data.copy_from)
    sync = await heating_sync.push_now()
    key = routines_store.resolve_key(r["key"])  # new key after it was created in the app
    return {"routine": routines_store.get(key) or r, "sync": sync}


@router.put("/api/routines/{key}/name")
async def routine_rename(key: str, data: RoutineName):
    """Rename a heating routine and sync."""
    try:
        routines_store.rename(key, data.name.strip())
    except KeyError:
        raise HTTPException(404, "Routine nicht gefunden")
    return {"ok": True, "sync": await heating_sync.push_now()}


@router.delete("/api/routines/{key}")
async def routine_delete(key: str):
    """Delete a heating routine and sync. 409 as long as it is assigned to at home or away."""
    if key in (home_store.get().get("present_routine"), home_store.get().get("away_routine")):
        raise HTTPException(409, "Diese Routine ist Zuhause/Abwesend zugeordnet. Bitte zuerst die Zuordnung ändern.")
    routines_store.delete(key)
    return {"ok": True, "sync": await heating_sync.push_now()}


@router.post("/api/routines/{key}/activate")
async def routine_activate(key: str):
    """Heizroutine als aktive setzen."""
    return await core.apply_routine(key)


@router.put("/api/routines/{key}/schedule")
async def routine_schedule(key: str, batch: ScheduleBatch):
    """Change the weekly schedule of a heating routine and sync."""
    if not batch.changes:
        raise HTTPException(400, "Keine Änderungen.")
    try:
        routine = routines_store.update_schedule(key, [c.model_dump() for c in batch.changes])
    except KeyError:
        raise HTTPException(404, "Routine nicht gefunden")
    heating_scheduler.kick()     # a changed value in force applies at once
    sync = await heating_sync.push_now()
    return {"routine": routines_store.get(key) or routine, "sync": sync}
