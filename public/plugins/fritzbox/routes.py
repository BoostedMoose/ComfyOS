"""Endpoints of the FRITZ!Box plugin, mounted under ``/api/plugins/fritzbox/``.

They serve the plugin's tile (static/settings.js): the state of the connection, the devices of the home network
the box knows, and which of them report presence. Every route requires a signed-in user – the core enforces that
for all plugin routes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from plugin_api import PluginError

if TYPE_CHECKING:
    from . import FritzBoxPlugin


class PresenceHost(BaseModel):
    """One device of the home network whose presence ComfyOS reports."""

    mac: str = Field(min_length=17, max_length=17)
    name: str = Field(min_length=1, max_length=60)


class PresenceRequest(BaseModel):
    """Body of ``PUT /presence``: the picked devices."""

    hosts: list[PresenceHost] = Field(default_factory=list, max_length=20)


def build_router(plugin: "FritzBoxPlugin") -> APIRouter:
    """Create the router for one plugin instance. Called once from ``FritzBoxPlugin.routes()``."""
    router = APIRouter()

    @router.get("/status")
    async def status() -> dict[str, Any]:
        """Connection, query interval and the picked presence devices with their state."""
        return {**plugin.status(), "interval_seconds": plugin.interval_seconds, "presence": plugin.presence_enabled,
                "presence_hosts": plugin.presence_hosts}

    @router.get("/hosts")
    async def hosts() -> dict[str, Any]:
        """Every device the box knows in its home network, with a mark for the picked ones."""
        picked = {host["mac"] for host in plugin.presence_hosts}
        try:
            found = await plugin.network_hosts()
        except PluginError as error:
            raise HTTPException(502, str(error))
        return {"hosts": [{"mac": host.mac, "name": host.name, "ip": host.ip, "active": host.active,
                           "picked": host.mac in picked} for host in found]}

    @router.put("/presence")
    async def set_presence(request: PresenceRequest) -> dict[str, Any]:
        """Store which devices report presence."""
        try:
            kept = plugin.set_presence_hosts([host.model_dump() for host in request.hosts])
        except PluginError as error:
            raise HTTPException(400, str(error))
        return {"ok": True, "presence_hosts": kept}

    @router.post("/refresh")
    async def refresh() -> dict[str, Any]:
        """Ask the box at once (after changing something in the box itself)."""
        try:
            await plugin.poll()
        except PluginError as error:
            raise HTTPException(502, str(error))
        plugin.context.devices_changed()
        return {"ok": True, **plugin.status()}

    return router
