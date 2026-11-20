"""Demo home (since 0.23): a sample home to look at and try out – per browser session, without touching anything real.

How it works
    A session switched to the demo (System → "Demo-Wohnung ansehen") carries a mark in memory. For each of its
    requests the middleware in main.py sets the context variable of this module, and these places look at it:
      * jsonfile.py      files under data/ are read and written in data/demo/ instead (floor plan, scenes, flows …)
      * settings_store   layout and accent colour of the demo lie in data/demo/settings.json; everything else of
                         settings.json stays the real one and is never written from the demo
      * plugins.py       the demo sees only the plugin "Demo-Wohnung" (demo_world.py) – never a real plugin
      * core.py          pages in the demo get only messages of the demo, plugins never hear of it, nothing of the
                         demo goes into history or consumption
      * events.py, logstore.py   the demo has an event log of its own in memory; the real logs stay untouched
      * weather.py       fixed sample weather, no query to the internet
    Everything else – the flow engine, the live comparison, the heating sync, the plugins – keeps running for the real
    home. Long-running background tasks never inherit the mark (tasks.spawn). What could reach the real home is
    refused in the demo (`allowed`): plugins, backups, updates, system settings, passwords, licence, flow test runs.

Starting fills data/demo/ anew with the sample home (`demo_world.seed`) unless another session is in the demo
already. Ending removes only the mark; the user's own settings were never touched, so the own dashboard is back at
once. After a restart of ComfyOS every session is back in its own home.
"""
from __future__ import annotations

import contextvars
import hashlib
import re
import shutil
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import DATA_DIR

DEMO_DIR = DATA_DIR / "demo"
MAX_SESSIONS = 20                 # this many sessions can be in the demo at once (memory only)
MAX_EVENTS = 200                  # the event log of the demo keeps this many entries
REFUSED = "Im Demo-Modus nicht möglich – unter System die Demo beenden."

_flag: contextvars.ContextVar[bool] = contextvars.ContextVar("comfyos_demo", default=False)
_sessions: dict[str, float] = {}                       # digest of the session token -> start time
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)

# Writing requests a session in the demo may send – everything that only changes data/demo/. Checked after DENIED.
ALLOWED_WRITES = tuple(re.compile(p) for p in (
    r"^/api/demo/(stop|reset)$",
    r"^/api/auth/logout$",
    r"^/api/devices/remove$",
    r"^/api/devices/.+/(command|lock|unlock|name|room)$",
    r"^/api/lights$",
    r"^/api/effects/[^/]+$",
    r"^/api/floorplan$",
    r"^/api/flows$",
    r"^/api/flows/(import|order)$",
    r"^/api/flows/[^/]+$",
    r"^/api/flows/[^/]+/(duplicate|enabled|group|name)$",
    r"^/api/home$",
    r"^/api/home/mapping$",
    r"^/api/rooms$",
    r"^/api/rooms/[^/]+$",
    r"^/api/rooms/[^/]+/name$",
    r"^/api/routines$",
    r"^/api/routines/[^/]+$",
    r"^/api/routines/[^/]+/(activate|name|schedule)$",
    r"^/api/scenes$",
    r"^/api/scenes/(stop|from-room/[^/]+)$",
    r"^/api/scenes/[^/]+$",
    r"^/api/scenes/[^/]+/(apply|apply-house)$",
    r"^/api/scenes/[^/]+/variants/[^/]+$",
    r"^/api/settings/(layout|accent)$",
    r"^/api/widgets$",
))
# Refused in the demo for every method: they touch the real home, the real system or the real logs. Test runs belong
# to the real flow engine; plugin routes belong to real plugins.
DENIED = tuple(re.compile(p) for p in (
    r"^/api/(backups|update|serversetup|https|security|audio|restart)(/|$)",
    r"^/api/logs/(export|[^/]+/export)$",
    r"^/api/flows/hook/",
    r"^/api/flows/[^/]+/(fire|play|run)(/|$)",
    r"^/api/rooms/push$",
    r"^/api/scenes/bridge(/|$)",
    r"^/api/power/(export|reset)$",
    r"^/api/push/(subscribe|unsubscribe|test)$",
    r"^/api/plugins/(rescan|[^/]+/(?!devices$).+)$",
))
# Refused only for writing although ALLOWED_WRITES would match: the variables belong to the real flow engine.
DENIED_WRITES = tuple(re.compile(p) for p in (r"^/api/flows/vars$",))
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def active() -> bool:
    """Does the running request (or task) belong to a session in the demo?"""
    return _flag.get()


def enter() -> contextvars.Token:
    """Mark the running request as one of the demo (middleware). Undo with `leave`."""
    return _flag.set(True)


def outside() -> contextvars.Token:
    """Mark the running task as one of the real home – for long-running background tasks (tasks.spawn), which must
    never inherit the demo of the request that happened to start them. Undo with `leave`."""
    return _flag.set(False)


def leave(token: contextvars.Token) -> None:
    """Undo `enter` or `outside`."""
    _flag.reset(token)


def _digest(token: str | None) -> str:
    """Session tokens are never kept as they are – only their hash (like auth.py)."""
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def in_demo(token: str | None) -> bool:
    """Is this session in the demo right now?"""
    return bool(token) and _digest(token) in _sessions


def start(token: str) -> bool:
    """Switch a session to the demo. Fills data/demo/ anew if no other session is in the demo. Returns whether the
    sample home was set up anew. Raises ValueError when too many sessions are in the demo."""
    key = _digest(token)
    if key in _sessions:
        return False
    if len(_sessions) >= MAX_SESSIONS:
        raise ValueError("Gerade sind zu viele Sitzungen in der Demo.")
    fresh = not _sessions
    if fresh:
        reset()
    _sessions[key] = time.time()
    return fresh


def stop(token: str | None) -> None:
    """End the demo for a session (also on logout). data/demo/ stays until the next start."""
    _sessions.pop(_digest(token), None)


def sessions() -> int:
    """How many sessions are in the demo right now."""
    return len(_sessions)


def reset() -> None:
    """Set up the sample home anew in data/demo/ (demo_world.seed) and clear the event log of the demo."""
    from . import demo_world
    token = enter()
    try:
        shutil.rmtree(DEMO_DIR, ignore_errors=True)
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        _events.clear()
        demo_world.reset_state()
        demo_world.seed()
    finally:
        leave(token)


def path(file: Path) -> Path:
    """Where a data file lies for the running request: in the demo, files under data/ come from data/demo/ – files
    elsewhere (settings, secrets, login) never move."""
    if not _flag.get():
        return file
    try:
        rel = file.relative_to(DATA_DIR)
    except ValueError:
        return file
    if rel.parts and rel.parts[0] == DEMO_DIR.name:
        return file
    return DEMO_DIR / rel


def allowed(method: str, url_path: str) -> bool:
    """May a session in the demo send this request? Reading is allowed except DENIED, writing only what ALLOWED_WRITES
    names – the demo can never change the real home."""
    if any(p.match(url_path) for p in DENIED):
        return False
    if method.upper() not in WRITE_METHODS:
        return True
    if any(p.match(url_path) for p in DENIED_WRITES):
        return False
    return any(p.match(url_path) for p in ALLOWED_WRITES)


# ---------------------------------------------------------------- Event log of the demo (memory only)
def note(record: dict[str, Any]) -> None:
    """Keep an entry of the event log of the demo (events.log in the demo). The real log never sees it."""
    _events.append({"ts": datetime.now(timezone.utc).isoformat(), **record})


def recent(limit: int = 100) -> list[dict[str, Any]]:
    """The last entries of the event log of the demo, newest first."""
    return list(reversed(list(_events)[-max(1, limit):]))
