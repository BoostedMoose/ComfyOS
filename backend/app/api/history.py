"""History, event log and logs (statistics, export, deleting)."""
from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .. import core, entitlements, events, history, logstore, power_store
from ..settings_store import load_settings, save_settings

router = APIRouter()


@router.get("/api/history")
async def api_history(device_id: str | None = None, hours: int = 24):
    """Heating history of a device. The period is limited to 31 days and to what the plan allows."""
    return history.series(device_id, entitlements.cap("history_hours", max(1, min(hours, 24 * 31))))


@router.get("/api/history/rooms")
async def api_history_rooms(hours: int = 24):
    """Heating history per room, with the same limits for the period."""
    return history.room_series(entitlements.cap("history_hours", max(1, min(hours, 24 * 31))))


@router.get("/api/history/devices")
async def api_history_devices():
    """Devices for which there is a heating history."""
    return history.devices_with_history()


@router.get("/api/power")
async def api_power():
    """Current power consumption: sum and one row per metering device (smart plugs, meters)."""
    devs = await core.all_devices()
    return {**core.power_summary(devs), "rows": core.power_rows(devs),
            "sample_seconds": history._sample_seconds()}


@router.post("/api/power/reset")
async def api_power_reset():
    """Set "consumption since …" to 0: the current meter reading becomes the base, estimates start anew."""
    devs = await core.all_devices()          # fresh meter reading for the new base (goes through _observe)
    at = power_store.reset()
    events.log("system", "Stromverbrauch zurückgesetzt")
    return {"ok": True, **core.power_summary(devs), "since": at}


@router.get("/api/power/series", dependencies=[entitlements.require_feature("energy_analytics")])
async def api_power_series(hours: int = 24, device_id: list[str] | None = Query(default=None)):
    """Sum (`points`) over all or the chosen devices; with device_id (several times) additionally one
    series per device (`series`). The series add up exactly to the sum per time bucket."""
    hours = entitlements.cap("history_hours", max(1, min(hours, 24 * 31)))
    ids = list(dict.fromkeys(str(i)[:160] for i in (device_id or []) if i))[:history.MAX_SERIES] or None
    return {**history.power_series(hours, ids), "devices": history.power_devices()}


@router.get("/api/power/export", response_class=PlainTextResponse,
            dependencies=[entitlements.require_feature("energy_analytics")])
async def api_power_export(hours: int = 168):
    """Consumption history as CSV (time, watts) – for a spreadsheet."""
    hours = entitlements.cap("history_hours", max(1, min(hours, 24 * 31)))
    data = history.power_series(hours)
    lines = ["zeit;watt"]
    for p in data["points"]:
        lines.append(f"{datetime.fromtimestamp(p['t']).isoformat(timespec='minutes')};{p['w']:.1f}".replace(".", ","))
    return PlainTextResponse("\n".join(lines) + "\n", headers={
        "Content-Disposition": f'attachment; filename="comfyos-verbrauch-{time.strftime("%Y%m%d")}.csv"'})


@router.get("/api/logs/{category}/stats")
async def api_log_stats(category: str):
    """Size of a log category (400 for an unknown one)."""
    try:
        return logstore.stats(category)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/logs/settings")
async def api_log_settings_get():
    """Sampling rate and retention period of the logs."""
    cfg = load_settings()
    return {
        "sample_seconds": cfg.get("log_sample_seconds") or 300,
        "retention_months": cfg.get("log_retention_months") or 13,
    }


class LogSettings(BaseModel):
    """Sampling rate (seconds) and retention (months) of the logs."""
    sample_seconds: int | None = Field(default=None, ge=1, le=10_000_000)
    retention_months: int | None = Field(default=None, ge=1, le=100_000)


@router.put("/api/logs/settings")
async def api_log_settings_put(body: LogSettings):
    """Save sampling rate (60–3600 s) and retention (1–60 months)."""
    cfg = load_settings()
    if body.sample_seconds is not None:
        cfg["log_sample_seconds"] = max(60, min(3600, body.sample_seconds))
    if body.retention_months is not None:
        cfg["log_retention_months"] = max(1, min(60, body.retention_months))
    save_settings(cfg)
    return {"ok": True}


@router.get("/api/logs/{category}/export", response_class=PlainTextResponse)
async def api_log_export(category: str):
    """Eine Log-Kategorie als JSONL-Datei herunterladen."""
    try:
        text = logstore.export_text(category)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return PlainTextResponse(text, headers={
        "Content-Disposition": f'attachment; filename="comfyos-{category}-{time.strftime("%Y%m%d")}.jsonl"'})


@router.post("/api/logs/{category}/clear")
async def api_log_clear(category: str):
    """Delete a log category. The deletion is written to the event log – unless that itself is deleted."""
    try:
        removed = logstore.clear(category)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if category != "events":
        events.log("system", f"Logs gelöscht: {category}")
    return {"ok": True, "removed": removed}
