"""Endpoints of the Matter plugin, mounted under ``/api/plugins/matter/``.

They serve the plugin's tile (static/settings.js): state, installing Node.js, the commissioning
window, the share list, and the controller (add and remove Matter devices).
Every route requires a signed-in user – the core enforces that for all plugin routes.

``GET /status`` is the only place that gives out the pairing code of the bridge. It is never
written to a log or an event.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from plugin_api import DeviceUnreachable, PluginError

if TYPE_CHECKING:
    from . import MatterPlugin


class WindowRequest(BaseModel):
    """Body of ``POST /commissioning``: open or close the commissioning window."""

    open: bool


class ShareRequest(BaseModel):
    """Body of ``PUT /shares``: switch "für Matter sichtbar" for one device (``key``) or several (``keys``)."""

    key: str | None = Field(default=None, min_length=1, max_length=170)
    keys: list[str] = Field(default_factory=list, max_length=2000)
    shared: bool


class NameRequest(BaseModel):
    """Body of ``PUT /controllers/{index}/name``: the user's own name for a controller (empty = the name it came with)."""

    name: str = Field(default="", max_length=80)


class PairRequest(BaseModel):
    """Body of ``POST /controller/pair``: the code another Matter app shows under "share"."""

    code: str = Field(min_length=1, max_length=260)


def build_router(plugin: "MatterPlugin") -> APIRouter:
    """Create the router for one plugin instance. Called once from ``MatterPlugin.routes()``."""
    router = APIRouter()

    async def guarded(action: Any) -> Any:
        """Run a plugin action and turn its errors into HTTP errors with the text for the user."""
        try:
            return await action
        except DeviceUnreachable as error:
            raise HTTPException(502, str(error))
        except PluginError as error:
            raise HTTPException(400, str(error))

    @router.get("/status")
    async def status() -> dict[str, Any]:
        await plugin.refresh_for_tile()
        return plugin.overview()

    @router.post("/runtime/install")
    async def install_runtime() -> dict[str, Any]:
        """Download Node.js from nodejs.org – only ever on this button."""
        await guarded(plugin.install_runtime())
        return plugin.overview()

    @router.post("/commissioning")
    async def commissioning(request: WindowRequest) -> dict[str, Any]:
        await guarded(plugin.open_window(request.open))
        return plugin.overview()

    @router.delete("/controllers/{index}")
    async def remove_controller(index: int) -> dict[str, Any]:
        if not 1 <= index <= 254:
            raise HTTPException(404, "Diesen Controller gibt es nicht.")
        await guarded(plugin.remove_controller(index))
        return {"ok": True}

    @router.put("/controllers/{index}/name")
    async def rename_controller(index: int, request: NameRequest) -> dict[str, Any]:
        """Give a paired controller a name of the user's own."""
        if not 1 <= index <= 254:
            raise HTTPException(404, "Diesen Controller gibt es nicht.")
        try:
            name = plugin.rename_controller(index, request.name)
        except PluginError as error:
            raise HTTPException(404, str(error))
        return {"ok": True, "name": name}

    @router.put("/shares")
    async def share(request: ShareRequest) -> dict[str, Any]:
        """Share or hide one device, or several at once (all, a room, a floor plan group)."""
        try:
            if request.keys:
                plugin.set_shared_many([key[:170] for key in request.keys], request.shared)
            elif request.key:
                plugin.set_shared(request.key, request.shared)
            else:
                raise PluginError("Kein Gerät angegeben.")
        except PluginError as error:
            raise HTTPException(400, str(error))
        return plugin.overview()

    @router.post("/controller/pair")
    async def pair(request: PairRequest) -> dict[str, Any]:
        result = await guarded(plugin.pair(request.code))
        return {"ok": True, **result}

    @router.delete("/controller/nodes/{node}")
    async def unpair(node: str) -> dict[str, Any]:
        if len(node) > 40:
            raise HTTPException(404, "Dieses Gerät gibt es nicht.")
        clean = await guarded(plugin.unpair(node))
        return {"ok": True, "clean": clean}

    return router
