"""Endpoints of the network plugin, mounted under ``/api/plugins/network/``.

They serve the plugin's settings tile (static/settings.js): list the watched devices with
their state, create and delete one, and check one right now.
Every route requires a signed-in user – the core enforces that for all plugin routes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from plugin_api import PluginError

if TYPE_CHECKING:
    from . import NetworkPlugin, Target


class TargetRequest(BaseModel):
    """Body of ``POST /targets``: a device in the home network to watch."""

    name: str = Field(min_length=1, max_length=60)
    host: str = Field(min_length=1, max_length=120)
    port: int = Field(80, ge=1, le=65535)


def build_router(plugin: "NetworkPlugin") -> APIRouter:
    """Create the router for one plugin instance. Called once from ``NetworkPlugin.routes()``."""
    router = APIRouter()

    def target_view(target: "Target") -> dict[str, Any]:
        """A target for the tile: what the user entered plus its current state."""
        state = plugin.state_of(target)
        return {"key": target.key, "device_id": "network:" + target.key, "name": target.name, "host": target.host,
                "port": target.port, "present": state.is_present, "last_answer_at": state.last_answer_at,
                "problem": state.problem}

    @router.get("/targets")
    async def list_targets() -> dict[str, Any]:
        return {"targets": [target_view(target) for target in plugin.targets],
                "interval_seconds": plugin.interval_seconds, "away_after_minutes": plugin.away_after_seconds // 60}

    @router.post("/targets")
    async def create_target(request: TargetRequest) -> dict[str, Any]:
        """Create a target and check it at once, so the user sees whether the address answers."""
        try:
            target = plugin.add_target(request.model_dump())
        except PluginError as error:
            raise HTTPException(400, str(error))
        answered = await plugin.check(target)
        plugin.context.event(f"Anwesenheit: „{target.name}“ wird beobachtet")
        return {"ok": True, "answered": answered, **target_view(target)}

    @router.post("/targets/{key}/check")
    async def check_target(key: str) -> dict[str, Any]:
        target = plugin.find_target(key)
        if target is None:
            raise HTTPException(404, "Dieses Gerät gibt es nicht.")
        answered = await plugin.check(target)
        return {"ok": True, "answered": answered, **target_view(target)}

    @router.delete("/targets/{key}")
    async def delete_target(key: str) -> dict[str, Any]:
        if not plugin.remove_target(key):
            raise HTTPException(404, "Dieses Gerät gibt es nicht.")
        return {"ok": True}

    return router
