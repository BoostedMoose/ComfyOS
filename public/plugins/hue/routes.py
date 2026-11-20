"""Endpoints of the Hue plugin, mounted under ``/api/plugins/hue/``.

They serve the plugin's settings tile (static/settings.js): show the connection,
pair with a bridge, disconnect and delete a lamp from the bridge.
Every route requires a signed-in user – the core enforces that for all plugin routes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from plugin_api import DeviceUnreachable, PluginError

from . import bridge as hue_bridge

if TYPE_CHECKING:
    from . import HuePlugin


class PairRequest(BaseModel):
    """Body of ``POST /pair``: the address of the bridge as the user typed it."""

    host: str = Field(max_length=120)


def build_router(plugin: "HuePlugin") -> APIRouter:
    """Create the router for one plugin instance. Called once from ``HuePlugin.routes()``."""
    router = APIRouter()

    async def read_status() -> dict[str, Any]:
        """Connection details for the tile. Asks the bridge, so ``connected`` is current."""
        suggested_hosts = sorted({hint["host"] for hint in plugin.context.hints("hue-bridge") if hint.get("host")})
        bridge = plugin.bridge
        if bridge is None:
            return {"configured": False, "suggested": suggested_hosts}
        status = {"configured": True, "host": bridge.host, "name": bridge.name, "suggested": suggested_hosts,
                  "connected": False, "live": plugin.status()["live"]}
        try:
            await bridge.lights(max_age=0)
            status["connected"] = True
        except PluginError as error:
            status["error"] = str(error)
        return status

    @router.get("/status")
    async def get_status() -> dict[str, Any]:
        return await read_status()

    @router.post("/pair")
    async def pair(request: PairRequest) -> dict[str, Any]:
        """Pair with a bridge. Answers ``{"ok": false, "link": true}`` until the link button was pressed."""
        try:
            await plugin.pair(request.host)
        except hue_bridge.LinkButtonNotPressed as error:
            return {"ok": False, "link": True, "message": str(error)}
        except PluginError as error:
            raise HTTPException(400, str(error))
        return {"ok": True, **(await read_status())}

    @router.post("/forget")
    async def forget() -> dict[str, Any]:
        await plugin.forget()
        return {"ok": True}

    @router.delete("/devices/{device_resource_id}")
    async def delete_device(device_resource_id: str) -> dict[str, Any]:
        """Delete a lamp from the bridge (official Hue API) – the same as deleting it in the Hue app."""
        bridge = plugin.bridge
        if bridge is None:
            raise HTTPException(400, "Keine Hue Bridge verbunden.")
        try:
            lights = await bridge.lights(max_age=0)
            light = next((item for item in lights if item.device_id == device_resource_id), None)
            if light is None:
                raise HTTPException(404, "Lampe gibt es auf der Bridge nicht.")
            await bridge.delete_device(device_resource_id)
        except DeviceUnreachable as error:
            raise HTTPException(502, str(error))
        except PluginError as error:
            raise HTTPException(400, str(error))
        plugin.context.event(f"„{light.name}“ von der Hue Bridge entfernt")
        plugin.context.devices_changed()
        return {"ok": True}

    return router
