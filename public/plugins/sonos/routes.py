"""Endpoints of the Sonos plugin, mounted under ``/api/plugins/sonos/``.

They serve the plugin's settings tile (static/settings.js): show the household, search the
home network, take a speaker's address by hand, forget the speakers, and put rooms into a
group or take them out. Grouping goes through ``context.switch`` – so the device lock of
ComfyOS applies to it like to every other command.
Every route requires a signed-in user – the core enforces that for all plugin routes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from plugin_api import DeviceUnreachable, PluginError

if TYPE_CHECKING:
    from . import SonosPlugin

DEVICE_ID_PATTERN = r"^sonosroom:RINCON_[0-9A-F]{6,40}$"


class ConnectRequest(BaseModel):
    """Body of ``POST /connect``: the address of one speaker as the user typed it."""

    host: str = Field(min_length=1, max_length=120)


class GroupRequest(BaseModel):
    """Body of ``POST /group``: the room that should play along and the room it joins."""

    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    target_id: str = Field(pattern=DEVICE_ID_PATTERN)


class UngroupRequest(BaseModel):
    """Body of ``POST /ungroup``: the room that leaves its group."""

    device_id: str = Field(pattern=DEVICE_ID_PATTERN)


def build_router(plugin: "SonosPlugin") -> APIRouter:
    """Create the router for one plugin instance. Called once from ``SonosPlugin.routes()``."""
    router = APIRouter()

    def read_status() -> dict[str, Any]:
        """The household for the tile: every room with what it plays and whom it plays with."""
        rooms = []
        for room in plugin.rooms.values():
            state = plugin.states.get(room.uuid)
            leader = plugin.rooms.get(room.coordinator)
            rooms.append({
                "device_id": "sonosroom:" + room.uuid, "name": room.name, "model": room.model, "host": room.host,
                "boxes": len(room.bonded) + 1, "online": state is not None,
                "playing": bool(state.playing) if state is not None else False,
                "group": [plugin.rooms[other].name for other in room.group if other in plugin.rooms],
                "leader": None if room.leads or leader is None else leader.name})
        return {"configured": bool(plugin.rooms), "connected": plugin.problem is None, "silent": plugin.silent,
                "problem": plugin.problem, "rooms": rooms, "favorites": len(plugin.favorites),
                "suggested": plugin.suggestions()}

    async def run(action: Any) -> Any:
        """Await a plugin call and turn its errors into HTTP answers with the text for the user."""
        try:
            return await action
        except DeviceUnreachable as error:
            raise HTTPException(502, str(error))
        except PluginError as error:
            raise HTTPException(400, str(error))

    @router.get("/status")
    async def get_status() -> dict[str, Any]:
        await plugin.refresh(None)
        return read_status()

    @router.post("/search")
    async def search() -> dict[str, Any]:
        """Search the home network for speakers (SSDP) – the only moment the plugin uses UDP."""
        found = await run(plugin.search())
        return {"ok": True, "found": found, **read_status()}

    @router.post("/connect")
    async def connect(request: ConnectRequest) -> dict[str, Any]:
        """Take over the household of the speaker at the given address."""
        found = await run(plugin.connect(request.host))
        return {"ok": True, "found": found, **read_status()}

    @router.post("/forget")
    async def forget() -> dict[str, Any]:
        await plugin.forget()
        return {"ok": True}

    @router.post("/group")
    async def group(request: GroupRequest) -> dict[str, Any]:
        """Let a room play along with another one."""
        await run(plugin.context.switch(request.device_id, {"join": request.target_id}))
        return {"ok": True, **read_status()}

    @router.post("/ungroup")
    async def ungroup(request: UngroupRequest) -> dict[str, Any]:
        """Take a room out of its group."""
        await run(plugin.context.switch(request.device_id, {"leave": True}))
        return {"ok": True, **read_status()}

    return router
