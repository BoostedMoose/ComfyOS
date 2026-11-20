"""Demo home (since 0.23): start, end and reset the demo of the own session. How the demo stays apart from the real
home is described in demo.py."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import auth, core, demo, events

router = APIRouter()


def _state() -> dict:
    """What the interface needs: is this session in the demo, and the names it shows."""
    return {"active": demo.active(), "name": "Demo-Wohnung", "sessions": demo.sessions()}


@router.get("/api/demo")
async def demo_state():
    """Is this session in the demo right now?"""
    return _state()


@router.post("/api/demo/start")
async def demo_start(request: Request):
    """Switch this session to the demo home. The own home keeps running unchanged for every other session and for
    the automations; nothing of the user's data is changed."""
    try:
        fresh = demo.start(request.cookies.get(auth.COOKIE) or "")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    events.log("system", "Demo-Wohnung geöffnet" + ("" if fresh else " (läuft bereits in einer anderen Sitzung)"))
    return {"active": True, "fresh": fresh, "name": "Demo-Wohnung", "sessions": demo.sessions()}


@router.post("/api/demo/stop")
async def demo_stop(request: Request):
    """End the demo for this session: the own dashboard is back as it was."""
    demo.stop(request.cookies.get(auth.COOKIE))
    return {"active": False, "name": "Demo-Wohnung", "sessions": demo.sessions()}


@router.post("/api/demo/reset")
async def demo_reset():
    """Set up the demo home anew (everything changed in the demo is gone) – only from the demo itself."""
    if not demo.active():
        raise HTTPException(409, "Zurücksetzen geht nur in der Demo.")
    demo.reset()
    core.publish({"type": "reload"})
    return _state()
