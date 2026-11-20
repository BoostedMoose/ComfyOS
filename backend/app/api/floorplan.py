"""Floor plan and weather for the address."""
from __future__ import annotations

from fastapi import APIRouter

from .. import entitlements, floorplan_store, weather

router = APIRouter()


# ---------------------------------------------------------------- Grundriss
@router.get("/api/floorplan")
async def get_floorplan():
    """Der gespeicherte Grundriss."""
    return floorplan_store.get()


@router.put("/api/floorplan")
async def put_floorplan(plan: dict):
    """Save the floor plan. Before that the licence checks whether the change is within the plan (e.g. number of rooms)."""
    entitlements.check_floorplan(floorplan_store.get(), floorplan_store.clean(plan))
    return floorplan_store.save(plan)


@router.get("/api/weather")
async def api_weather():
    """Outside temperature/weather for the address in the floor plan (only postcode/town go to Open-Meteo) and, if
    switched on, official warnings for the same place (`warnings`; only rounded coordinates go to the DWD)."""
    plan = floorplan_store.get()
    if not plan.get("weather", True):
        return {"enabled": False, "available": False}
    if not plan.get("address"):
        return {"enabled": True, "available": False, "reason": "Keine Adresse im Grundriss eingetragen."}
    out = {"enabled": True, **(await weather.current(plan["address"]))}
    # Wind report and hourly forecast belong to the weather effects (Pro); the weather itself stays for everyone
    if entitlements.enforcing() and "floorplan_weather" not in entitlements.granted():
        out.pop("wind_report", None)
        out.pop("today", None)
    if plan.get("warnings", True):
        out["warnings"] = await weather.alerts(plan["address"])
        out["warnings_source"] = weather.WARN_SOURCE
    return out
