"""Push notifications: sign devices up and off, send a test (flow node "Benachrichtigung")."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import events, push

router = APIRouter()


class PushKeys(BaseModel):
    """Keys of a push subscription as the browser delivers them."""
    p256dh: str = Field(min_length=80, max_length=100)
    auth: str = Field(min_length=16, max_length=32)


class PushSubscription(BaseModel):
    """Push subscription of the browser: address at the push service and keys."""
    endpoint: str = Field(min_length=12, max_length=1000)
    keys: PushKeys


class PushSubscribe(BaseModel):
    """Switch notifications on for this device: the subscription and a name for the device."""
    subscription: PushSubscription
    label: str = Field(default="", max_length=60)


class PushUnsubscribe(BaseModel):
    """Switch notifications off – by the address of the subscription or its id."""
    endpoint: str | None = Field(default=None, max_length=1000)
    id: str | None = Field(default=None, max_length=24)


@router.get("/api/push/status")
async def push_status():
    """State for the settings: available, public key, signed-up devices (without keys)."""
    ok = push.available()
    key = None
    if ok:
        try:
            key = push.public_key()
        except Exception:
            ok = False
    return {"available": ok, "public_key": key, "subscriptions": push.subscriptions()}


@router.post("/api/push/subscribe")
async def push_subscribe(body: PushSubscribe):
    """Switch notifications on for this device. 503 if the package for Web Push is missing; 400 for an
    invalid subscription."""
    if not push.available():
        raise HTTPException(503, "Push ist auf dieser Installation nicht verfügbar (Paket pywebpush fehlt).")
    try:
        sub = push.subscribe(body.subscription.model_dump(), body.label)
    except push.PushError as exc:
        raise HTTPException(400, str(exc))
    events.log("flow", f"Benachrichtigungen eingeschaltet: {sub['label']}")
    return {"ok": True, **sub, "subscriptions": push.subscriptions()}


@router.post("/api/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubscribe):
    """Switch notifications off on a device."""
    if push.unsubscribe(body.endpoint, body.id):
        events.log("flow", "Benachrichtigungen auf einem Gerät ausgeschaltet")
    return {"ok": True, "subscriptions": push.subscriptions()}


@router.post("/api/push/test")
async def push_test():
    """Send a test message to all devices with notifications."""
    if not push.count():
        raise HTTPException(409, "Auf keinem Gerät sind Benachrichtigungen eingeschaltet.")
    return await push.send_all("ComfyOS", "Test: Benachrichtigungen funktionieren.")
