"""Plugins: overview, enabling/disabling, settings, devices of a plugin, static files.

Routes of a plugin's own live under /api/plugins/<id>/… (from `Plugin.routes()`, mounted through `mount`).
The routes here are the same for every plugin: what a plugin has "found" the user adopts here; ComfyOS owns
the names and transfers them to the source.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .. import core, device_merge, device_store, events, plugins
from ..plugin_api import API_REVISION, API_VERSION, PluginError

router = APIRouter()
MEDIA_IMAGE_BYTES = 2_000_000     # a cover picture delivered by a plugin must not be larger


class EnabledBody(BaseModel):
    """Enable or disable a plugin. The password is only needed when a user-added plugin is enabled for the first time."""
    enabled: bool
    password: str = Field("", max_length=200)   # only when a user-added plugin is enabled for the first time


class SettingsBody(BaseModel):
    """Settings of a plugin {key: value}."""
    values: dict[str, Any] = Field(default_factory=dict, max_length=60)


class AdoptBody(BaseModel):
    """Adopt a found device, optionally into a room right away."""
    device_id: str = Field(min_length=1, max_length=160)
    room: str | None = Field(None, max_length=80)


def mount(app: FastAPI):
    """Returns the function with which the loader mounts the routes of a plugin (at run time too)."""
    def _mount(rec: plugins.Record) -> None:
        app.include_router(rec.router, prefix=f"/api/plugins/{rec.id}", dependencies=[plugins.require_active(rec.id)])
    return _mount


@router.get("/api/plugins")
async def plugins_list():
    """All plugins with state, settings (from the manifest) and connection state."""
    return {"api": API_VERSION, "revision": API_REVISION, "plugins": [plugins.public(r) for r in plugins.records()]}


@router.post("/api/plugins/rescan")
async def plugins_rescan():
    """Scan the plugin folders again (after copying a plugin to data/plugins). New built-in plugins
    start at once; user-added ones wait for their approval."""
    found = plugins.discover()
    for rec in found:
        plugins.load(rec)
    await plugins.start_all()
    if found:
        events.log("system", "Neue Plugins gefunden: " + ", ".join(r.name for r in found))
    return await plugins_list()


@router.put("/api/plugins/{plugin_id}/enabled")
async def plugin_enabled(plugin_id: str, body: EnabledBody, request: Request):
    """Enable/disable a plugin. A user-added plugin (data/plugins) is foreign code with the rights of
    ComfyOS – enabling it for the first time therefore asks for the ComfyOS password again."""
    rec = plugins.get(plugin_id)
    if body.enabled and rec.origin == "user" and rec.state == "new":
        core.step_up(request, body.password, "Plugin-Freigabe")
        events.log("security", f"Nachgerüstetes Plugin „{rec.name}“ freigegeben")
    rec = await plugins.set_enabled(plugin_id, body.enabled)
    events.log("system", f"Plugin „{rec.name}“ {'eingeschaltet' if body.enabled else 'ausgeschaltet'}")
    return plugins.public(rec)


@router.put("/api/plugins/{plugin_id}/settings")
async def plugin_settings(plugin_id: str, body: SettingsBody):
    """Save the settings of the generic tile (only fields from the manifest) and restart the plugin."""
    rec = plugins.get(plugin_id)
    plugins.save_values(rec, body.values)
    await plugins.restart(rec)
    core.publish({"type": "reload"})
    return plugins.public(rec)


def _simple(values: dict[str, Any]) -> dict[str, Any]:
    """Let only simple values through (text, number, yes/no, empty) – details of a plugin never reach the interface
    nested."""
    return {str(k)[:60]: v for k, v in values.items() if isinstance(v, (str, int, float, bool)) or v is None}


@router.get("/api/plugins/{plugin_id}/devices")
async def plugin_devices(plugin_id: str):
    """Devices this plugin controls (linked, adopted, orphaned), found candidates and removed ones."""
    rec = plugins.get(plugin_id)
    out: dict[str, Any] = {"devices": [], "new": [], "removed": [], "names_differ": 0, "elsewhere": 0}
    try:
        devs = await core.all_devices()
    except HTTPException as exc:
        return {**out, "error": exc.detail}
    mine = [d for d in devs if core.owner_plugin(d) == rec.id]
    out["devices"] = sorted((core.dev_row(d) for d in mine), key=lambda r: (r["status"] == "ok", str(r["name"]).lower()))
    out["names_differ"] = sum(1 for d in mine if core.name_differs(d))
    # devices of this plugin that another one controls (e.g. lamps of the Home Base that run through the Hue Bridge)
    out["elsewhere"] = sum(1 for d in devs if d.get("plugin") == rec.id and core.owner_plugin(d) != rec.id)
    out["new"] = [{"id": c.id, "name": c.name, "kind": c.kind, "online": c.online, "room_hint": c.room_hint,
                   "model": c.model, "details": _simple(c.details), "extra": _simple(c.extra)} for c in core.candidates(rec.id)]
    names = {r.id: r.name for r in plugins.records()}
    for r in core.removed_devices(rec.id):
        if r.get("orphan"):
            hub = names.get(r.get("plugin") or "", "der anderen Quelle")
            r["detail"] = (f"Nur noch ein Eintrag bei {hub} – bei {rec.name} gibt es das Gerät nicht mehr. "
                           "Verschwindet hier von selbst, sobald er dort gelöscht ist.")
        out["removed"].append(r)
    return out


@router.post("/api/plugins/{plugin_id}/adopt")
async def plugin_adopt(plugin_id: str, body: AdoptBody):
    """Adopt a found device of a plugin into ComfyOS (optionally with a room right away)."""
    rec = plugins.get(plugin_id)
    await core.all_devices()
    cand = next((c for c in core.candidates(rec.id) if c.id == body.device_id), None)
    if cand is None:
        raise HTTPException(404, "Dieses Gerät gibt es dort nicht (mehr) oder es ist schon in ComfyOS.")
    try:
        device_store.set_adopted(cand.id, True, (body.room or "").strip() or None)
        device_store.set_hidden(cand.id, None, False)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    events.log(rec.id, f"„{cand.name}“ in ComfyOS übernommen")
    core.publish({"type": "reload"})
    return {"ok": True, "device_id": cand.id}


@router.post("/api/plugins/{plugin_id}/adopt_all")
async def plugin_adopt_all(plugin_id: str):
    """Adopt all found devices of a plugin (rooms can be assigned afterwards; the room suggestion of the
    source applies if there is a room of the same name)."""
    rec = plugins.get(plugin_id)
    await core.all_devices()
    adopted = 0
    for cand in core.candidates(rec.id):
        try:
            device_store.set_adopted(cand.id, True)
        except ValueError as exc:      # upper limit of the device store reached
            if not adopted:
                raise HTTPException(400, str(exc))
            break
        adopted += 1
    if adopted:
        events.log(rec.id, f"{adopted} Geräte in ComfyOS übernommen")
        core.publish({"type": "reload"})
    return {"ok": True, "adopted": adopted}


@router.post("/api/plugins/{plugin_id}/names/sync")
async def plugin_names_sync(plugin_id: str):
    """Transfer all differing device names from ComfyOS to the source (ComfyOS owns the names)."""
    rec = plugins.get(plugin_id)
    if not rec.active:
        raise HTTPException(400, f"{rec.name} ist nicht eingeschaltet.")
    synced, failed = 0, []
    for d in await core.all_devices():
        if core.owner_plugin(d) == rec.id and core.name_differs(d):
            r = await core.push_name(d)
            if r and r.get("ok"):
                synced += 1
            elif r:
                failed.append(f"{d['name']}: {r['error']}")
    if synced:
        events.log(rec.id, f"{synced} Gerätenamen an {rec.name} übertragen")
    core.publish({"type": "reload"})
    return {"ok": not failed, "synced": synced, "failed": failed}


@router.get("/api/scenes/image/{plugin_id}/{image_id}")
async def scene_image(plugin_id: str, image_id: str):
    """Load the preview image of an adopted scene from the plugin and pass it on (the browser never asks the source)."""
    plugin = plugins.active(plugin_id)
    if plugin is None or len(image_id) > 80:
        raise HTTPException(404, "Kein Bild.")
    try:
        img = await plugin.scene_image(image_id)
    except PluginError:
        img = None
    except Exception:
        img = None
    if not img or not isinstance(img[0], (bytes, bytearray)) or img[1] not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(404, "Kein Bild.")
    return Response(bytes(img[0]), media_type=img[1], headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/media/image/{plugin_id}/{image_id}")
async def media_image(plugin_id: str, image_id: str):
    """Load the cover picture of the playback from the plugin and pass it on (the browser never asks the speaker or a
    foreign server). The id comes from the device row (`media.image`)."""
    plugin = plugins.active(plugin_id)
    if plugin is None or not re.match(device_merge.IMAGE_ID, image_id):
        raise HTTPException(404, "Kein Bild.")
    try:
        img = await plugin.media_image(image_id)
    except PluginError:
        img = None
    except Exception:
        img = None   # foreign code (plugin): every exception here only means "no picture"
    if (not img or not isinstance(img[0], (bytes, bytearray)) or len(img[0]) > MEDIA_IMAGE_BYTES
            or img[1] not in ("image/jpeg", "image/png", "image/webp")):
        raise HTTPException(404, "Kein Bild.")
    return Response(bytes(img[0]), media_type=img[1], headers={"Cache-Control": "private, max-age=3600"})


@router.get("/plugins/{plugin_id}/static/{path:path}", include_in_schema=False)
async def plugin_static(plugin_id: str, path: str):
    """Static file of an enabled plugin (e.g. static/settings.js for its tile)."""
    hit = plugins.static_file(plugin_id, path)
    if hit is None:
        raise HTTPException(404, "Nicht gefunden.")
    return FileResponse(hit[0], media_type=hit[1], headers={"Cache-Control": "no-cache"})
