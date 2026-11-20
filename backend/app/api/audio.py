"""Live audio: a browser sends the sound of its computer, ComfyOS passes it on to speakers (see live_audio.py).

All routes here require login. The stream the speakers fetch (`live_audio.PATH`) is mounted in main.py – it is
the one address of this function that answers without login.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import core, events, live_audio

router = APIRouter()

CAPABILITY = "live_audio"               # devices with this writable capability can play the transmission


class LiveStart(BaseModel):
    """Begin a transmission: the sample rate of the sound the browser will send."""
    rate: int = Field(ge=8000, le=192000)


class LivePlay(BaseModel):
    """Let a device play the running transmission."""
    device_id: str = Field(min_length=1, max_length=160)


async def _device(device_id: str) -> dict:
    """Find a device that can play a transmission. Raises 404 for an unknown device and 400 for one that cannot."""
    d = next((x for x in await core.all_devices() if x.get("id") == device_id), None)
    if d is None:
        raise HTTPException(404, "Gerät nicht gefunden")
    if not any(c.get("key") == CAPABILITY and c.get("writable") for c in d.get("caps") or []):
        raise HTTPException(400, f"{d.get('name')} kann keinen Ton vom Browser abspielen.")
    return d


@router.get("/api/audio/live")
async def live_status():
    """Whether a transmission runs, with which rate, which devices play it and how many fetch the stream."""
    return live_audio.status()


@router.post("/api/audio/live/start")
async def live_start(body: LiveStart):
    """Begin a transmission. After that the browser sends pieces of sound; devices are added with …/play."""
    try:
        live_audio.start(body.rate)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return live_audio.status()


@router.post("/api/audio/live/chunk")
async def live_chunk(request: Request):
    """One piece of sound: raw 16-bit samples, little endian, left and right in turn."""
    data = await request.body()
    try:
        live_audio.feed(data)
    except LookupError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@router.post("/api/audio/live/play")
async def live_play(body: LivePlay):
    """Let a device play the running transmission – through the shared switching path (device lock, event log)."""
    if not live_audio.active():
        raise HTTPException(409, "Gerade läuft keine Übertragung.")
    d = await _device(body.device_id)
    result = await core.apply_device(d, {CAPABILITY: True})
    live_audio.note_device(d["id"])
    return result


@router.post("/api/audio/live/stop")
async def live_stop():
    """End the transmission and tell the devices that played it to stop."""
    was_active = live_audio.active()
    device_ids = live_audio.stop()
    devices = {d.get("id"): d for d in await core.all_devices()} if device_ids else {}
    for device_id in device_ids:
        d = devices.get(device_id)
        if d is None:
            continue
        try:
            await core.apply_device(d, {CAPABILITY: False})
        except HTTPException:
            pass    # locked or unreachable meanwhile: the stream has ended anyway, the speaker falls silent by itself
    if was_active:
        events.log("device", "Ton vom Browser beendet")
    return live_audio.status()
