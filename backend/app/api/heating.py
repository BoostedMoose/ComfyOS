"""Heating schedule import and export."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from .. import core, entitlements, events, heating_import, heating_scheduler, heating_sync, room_assign, routines_store

router = APIRouter()


async def _heating_rooms() -> dict[str, str]:
    """Rooms for which a heating schedule makes sense: rooms with a thermostat, otherwise all known rooms."""
    names = core.room_names()
    rooms: dict[str, str] = {}
    try:
        for r in core.rooms_overview(await core.all_devices()):
            if r.get("room_id") and r.get("thermostats") and not str(r["room_id"]).startswith(room_assign.PLAN_PREFIX):
                rooms[r["room_id"]] = r.get("name") or names.get(r["room_id"]) or r["room_id"]
    except HTTPException:
        pass   # devices cannot be fetched right now: then the known room names (below)
    return rooms or {k: v for k, v in names.items() if not k.startswith(room_assign.PLAN_PREFIX)}


@router.get("/api/heating/import")
async def routines_import_info():
    """Room names and levels – for the template or the AI prompt."""
    return {"rooms": sorted((await _heating_rooms()).values(), key=str.casefold), "levels": routines_store.levels(),
            "routines": [r["name"] for r in routines_store.state()["routines"]]}


@router.post("/api/heating/import", dependencies=[entitlements.require_feature("ai_import")])
async def routines_import(request: Request, apply: bool = False):
    """Check JSON heating schedules (apply=false) or take them over (apply=true)."""
    raw = await request.body()
    if len(raw) > 256 * 1024:
        raise HTTPException(413, "Datei zu groß (max. 256 KB).")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"Keine gültige JSON-Datei: {exc}")
    except RecursionError:
        raise HTTPException(400, "Keine gültige JSON-Datei: zu tief verschachtelt.")
    existing = {r["key"]: r["name"] for r in routines_store.state()["routines"]}
    try:
        result = heating_import.plan(doc, await _heating_rooms(), existing, routines_store.levels())
    except heating_import.ImportError_ as exc:
        raise HTTPException(400, str(exc))
    if not apply:
        for e in result["routines"]:
            e["points"] = len(e.pop("changes"))
        return result
    if not result["ok"]:
        raise HTTPException(400, "Die Datei enthält Fehler – bitte zuerst prüfen.")
    done = []
    for e in result["routines"]:
        if not e["changes"]:
            continue
        key = e["key"] or routines_store.create(e["name"])["key"]
        routines_store.update_schedule(key, e["changes"])
        done.append({"name": e["name"], "action": e["action"], "rooms": len(e["rooms"])})
    events.log("heating", f"Heizpläne importiert: {', '.join(d['name'] for d in done)}")
    heating_scheduler.kick()
    return {"ok": True, "done": done, "sync": await heating_sync.push_now()}


@router.get("/api/heating/export")
async def routines_export():
    """All heating routines as a file to take along (format of heating_import), with room names."""
    rooms = {**core.room_names(), **(await _heating_rooms())}
    full = [routines_store.get(r["key"]) for r in routines_store.state()["routines"]]
    return heating_import.export([r for r in full if r], rooms)
