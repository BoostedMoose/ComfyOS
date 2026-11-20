"""Endpoints of the MQTT plugin, mounted under ``/api/plugins/mqtt/``.

They serve the plugin's settings tile (static/settings.js): show the connection and what is
not supported, and create or delete devices the user describes by hand.
Every route requires a signed-in user – the core enforces that for all plugin routes.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from plugin_api import PluginError

from . import manual
from .registry import DEVICE_ID_PREFIX

if TYPE_CHECKING:
    from . import MqttPlugin


class ManualDeviceRequest(BaseModel):
    """Body of ``POST /manual``: a device the user describes by its topics."""

    name: str = Field(min_length=1, max_length=manual.MAX_NAME_LENGTH)
    kind: str = Field(max_length=20)
    state_topic: str = Field("", max_length=manual.MAX_TOPIC_LENGTH)
    command_topic: str = Field("", max_length=manual.MAX_TOPIC_LENGTH)
    payload_on: str = Field("ON", max_length=manual.MAX_PAYLOAD_LENGTH)
    payload_off: str = Field("OFF", max_length=manual.MAX_PAYLOAD_LENGTH)
    json_key: str = Field("", max_length=170)


def build_router(plugin: "MqttPlugin") -> APIRouter:
    """Create the router for one plugin instance. Called once from ``MqttPlugin.routes()``."""
    router = APIRouter()

    def manual_device_view(device: manual.ManualDevice) -> dict[str, Any]:
        """A manual device for the tile, with the id it has as a ComfyOS device."""
        return {**asdict(device), "device_id": DEVICE_ID_PREFIX + manual.IDENTIFIER_PREFIX + device.key,
                "kind_label": manual.MANUAL_KINDS[device.kind][0]}

    @router.get("/status")
    async def get_status() -> dict[str, Any]:
        """Connection, what discovery could not use, the manual devices and the kinds a manual device can have."""
        return {
            **plugin.status(),
            "host": plugin.host,
            "not_supported": list(plugin.registry.notes),
            "manual": [manual_device_view(device) for device in plugin.manual_devices],
            "manual_kinds": [[kind, label] for kind, (label, _, _) in manual.MANUAL_KINDS.items()],
            "switchable_kinds": list(manual.SWITCHABLE_MANUAL_KINDS),
        }

    @router.post("/manual")
    async def create_manual_device(request: ManualDeviceRequest) -> dict[str, Any]:
        try:
            device = plugin.add_manual_device(request.model_dump())
        except PluginError as error:
            raise HTTPException(400, str(error))
        plugin.context.event(f"Eigenes MQTT-Gerät „{device.name}“ angelegt")
        return {"ok": True, **manual_device_view(device)}

    @router.delete("/manual/{key}")
    async def delete_manual_device(key: str) -> dict[str, Any]:
        if not plugin.remove_manual_device(key):
            raise HTTPException(404, "Dieses Gerät gibt es nicht.")
        return {"ok": True}

    return router
