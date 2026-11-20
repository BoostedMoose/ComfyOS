"""Light scenes: manage, apply to rooms/groups/lamps/the whole house, end, room variants,
take over scenes of a plugin (Hue Bridge). Plus effects per room (independent of scenes)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import core, effects_store, events, plugin_api, plugins, scenes_store

router = APIRouter()


class SceneBody(BaseModel):
    """Scene from the editor: name, colours, dynamic or fixed, speed, rooms."""
    name: str = Field(max_length=80)
    palette: list[dict[str, Any]] = Field(max_length=20)
    dynamic: bool = False
    speed: int = Field(20, ge=1, le=3600)
    rooms: list[str] = Field(default_factory=list, max_length=50)


class SceneApply(BaseModel):
    """Start a scene: in rooms, in a group or on single lamps, optionally with a brightness."""
    room_ids: list[str] | None = Field(None, max_length=50)
    device_ids: list[str] | None = Field(None, max_length=100)
    group_id: str | None = Field(None, max_length=80)
    brightness: float | None = Field(None, ge=1, le=100)


class SceneStop(BaseModel):
    """End a scene: in rooms or a group (without an argument everywhere); `restore` brings back the state from
    before."""
    room_ids: list[str] | None = Field(None, max_length=50)   # None (without group_id) = all
    group_id: str | None = Field(None, max_length=80)
    restore: bool = True


class BridgeImport(BaseModel):
    """Take over scenes of a plugin: their ids there."""
    ids: list[str] = Field(max_length=60)


class FromRoom(BaseModel):
    """Name of the scene that is created from the current light of a room."""
    name: str = Field(max_length=80)


class EffectBody(BaseModel):
    """Light effect of a room: which effect, on which lamps, with which colour and which speed."""
    effect: str = Field(min_length=1, max_length=40)
    target: Literal["room", "devices"] = "room"
    devices: list[str] = Field(default_factory=list, max_length=100)   # only with target="devices"
    color_source: Literal["own", "scene", "custom"] = "own"
    color: str | None = Field(None, max_length=7)
    kelvin: float | None = Field(None, ge=2000, le=6500)
    speed: float | None = Field(None, ge=0, le=100)


def _valid_room(room_id: str) -> str:
    """Check a room key from the address (not empty, not overlong)."""
    if not room_id or len(room_id) > 120:
        raise HTTPException(400, "Ungültiger Raum.")
    return room_id


@router.get("/api/scenes")
async def scenes_list():
    """All scenes and what is active where; `bridge` says whether a plugin offers scenes to take over."""
    return {**scenes_store.public(), "bridge": bool(_scene_sources())}


@router.post("/api/scenes")
async def scene_create(body: SceneBody):
    """Create a scene and report it to all open pages."""
    try:
        sc = scenes_store.create(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    events.log("scene", f"Szene „{sc['name']}“ angelegt")
    core.publish_scenes()
    return sc


@router.post("/api/scenes/stop")
async def scene_stop(body: SceneStop):
    """End scenes – everywhere or only in the named rooms or the group."""
    keys: list[str] | None = None
    if body.room_ids is not None or body.group_id:
        keys = (core.room_keys(body.room_ids) or []) + (["group:" + body.group_id] if body.group_id else [])
    return await core.stop_scene(keys, body.restore)


def _scene_sources() -> list[plugins.Record]:
    """Configured plugins that offer scenes to take over (today: Hue Bridge)."""
    return [r for r in plugins.records() if r.origin != "core" and r.active
            and type(r.plugin).scenes is not plugin_api.Plugin.scenes and core.plugin_status(r.id)["configured"]]


async def _bridge_scenes() -> list[dict[str, Any]]:
    """Scenes of all sources in the form of the interface (name, room/zone, colours, image)."""
    sources = _scene_sources()
    if not sources:
        raise HTTPException(400, "Keine Hue Bridge verbunden.")
    out = []
    for rec in sources:
        try:
            found = await rec.plugin.scenes()
        except plugin_api.PluginError as exc:
            raise HTTPException(502, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"Szenen von {rec.name} nicht lesbar ({type(exc).__name__}).")
        for s in found:
            entry: dict[str, Any] = {"id": s.id, "name": s.name, "group": s.groups[0] if s.groups else None,
                                     "groups": list(s.groups), "palette": s.palette, "dynamic": bool(s.dynamic)}
            if s.image_id:
                entry["image_rid"] = s.image_id
            out.append(entry)
    return out


@router.get("/api/scenes/bridge")
async def scenes_bridge():
    """Scenes of the Hue Bridge (name, room/zone, colours) to take over."""
    return {"scenes": await _bridge_scenes()}


@router.post("/api/scenes/bridge/import")
async def scenes_bridge_import(body: BridgeImport):
    """Take over selected scenes of a plugin as ComfyOS scenes – without a room, that is assigned in the scene studio."""
    found = {s["id"]: s for s in await _bridge_scenes()}
    created = []
    for sid in body.ids:
        src = found.get(sid)
        if src is None:
            continue
        try:
            # Deliberately without a room: the bridge knows its own rooms, ComfyOS assigns itself (scene studio)
            sc_data: dict[str, Any] = {"name": src["name"], "palette": src["palette"], "dynamic": src["dynamic"], "speed": 20,
                                       "source": "hue", "rooms": []}
            if src.get("image_rid"):
                sc_data["image_rid"] = src["image_rid"]
            created.append(scenes_store.create(sc_data))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if created:
        events.log("scene", f"{len(created)} Szene(n) von der Hue Bridge übernommen")
        core.publish_scenes()
    return {"ok": True, "created": [s["id"] for s in created]}


@router.post("/api/scenes/from-room/{room_id}")
async def scene_from_room(room_id: str, body: FromRoom):
    """Current state of a room as a new scene (palette from the lamp colours + room variant)."""
    return await core.scene_from_room(_valid_room(room_id), body.name)


@router.put("/api/scenes/{scene_id}")
async def scene_update(scene_id: str, body: SceneBody):
    """Edit a scene and report it to all open pages."""
    try:
        sc = scenes_store.update(scene_id, body.model_dump())
    except KeyError:
        raise HTTPException(404, "Szene nicht gefunden.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    core.publish_scenes()
    return sc


@router.delete("/api/scenes/{scene_id}")
async def scene_delete(scene_id: str):
    """Delete a scene and report it to all open pages."""
    sc = scenes_store.scene(scene_id)
    if sc is None or not scenes_store.delete(scene_id):
        raise HTTPException(404, "Szene nicht gefunden.")
    events.log("scene", f"Szene „{sc['name']}“ gelöscht")
    core.publish_scenes()
    return {"ok": True}


@router.post("/api/scenes/{scene_id}/apply")
async def scene_apply(scene_id: str, body: SceneApply):
    """Start a scene – a target (room, group or lamps) must be named."""
    if not (body.room_ids or body.device_ids or body.group_id):
        raise HTTPException(400, "Bitte Raum, Gruppe oder Lampen angeben.")
    return await core.apply_scene(scene_id, body.room_ids, body.device_ids, body.group_id, body.brightness)


@router.post("/api/scenes/{scene_id}/apply-house")
async def scene_apply_house(scene_id: str):
    """House scene: in all assigned rooms, each room with its variant."""
    return await core.apply_house(scene_id)


@router.post("/api/scenes/{scene_id}/variants/{room_id}")
async def scene_variant_save(scene_id: str, room_id: str):
    """"Für diesen Raum speichern": current state of the lamps as the room variant of the scene."""
    return await core.save_room_variant(scene_id, _valid_room(room_id))


@router.delete("/api/scenes/{scene_id}/variants/{room_id}")
async def scene_variant_reset(scene_id: str, room_id: str):
    """Reset a room variant – the room gets the palette distribution again."""
    try:
        gone = scenes_store.clear_variant(scene_id, room_id)
    except KeyError:
        raise HTTPException(404, "Szene nicht gefunden.")
    if gone:
        core.publish_scenes()
    return {"ok": True, "removed": gone}


@router.put("/api/effects/{room_id}")
async def effect_set(room_id: str, body: EffectBody):
    """Start a light effect in a room and remember it for the room."""
    data = body.model_dump()
    data["target"] = [str(d)[:160] for d in data.pop("devices")] if body.target == "devices" else "room"
    return await core.apply_effect(_valid_room(room_id), data)


@router.delete("/api/effects/{room_id}")
async def effect_stop(room_id: str):
    """Lichteffekt eines Raums beenden."""
    return await core.apply_effect(_valid_room(room_id), None)


@router.get("/api/effects")
async def effects_list():
    """Gespeicherte Effekte je Raum."""
    return effects_store.get()
