"""Supervised background tasks (since 0.20).

Up to 0.19 the loops of the core ran as loose `asyncio.create_task(...)`: without a held reference (asyncio holds
tasks only weakly – a task could vanish in the middle of a run), without a report when one crashed, and without
being cancelled on shutdown. There are two ways for that here:

    tasks.spawn("live", core.live_loop)      long runner: restarted when it ends or crashes
    tasks.fire(push.send_all(...), "push")   one-off task: runs to its end in the background

Both hold the reference, write a crash to the event log (only the error type, never its content) and are
cancelled by `cancel_all()` on shutdown. New background work in the core always goes through here – never through
a bare `create_task`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Coroutine

from . import demo

log = logging.getLogger("comfyos.tasks")

FIRST_PAUSE = 1.0        # seconds until the first restart …
MAX_PAUSE = 60.0         # … doubles up to this value while the task keeps failing right away
STABLE_AFTER = 60.0      # lief sie so lange, beginnt die Pause wieder vorn
REPORT_EVERY = 600.0     # the same report at most this often in the event log (seconds)

_loops: dict[str, asyncio.Task] = {}          # long runners by name
_once: set[asyncio.Task] = set()              # one-off tasks until they are done
_reported: dict[tuple[str, str], float] = {}  # (Aufgabe, Grund) -> zuletzt gemeldet


def spawn(name: str, factory: Callable[[], Awaitable[object]]) -> asyncio.Task:
    """Start a long runner. `factory` returns the coroutine on every (re)start, e.g. `core.live_loop`.

    If one is already running under this name in the same event loop, that one stays. If the task ends or
    raises, it restarts after a growing pause – a long runner must never be missing silently.
    """
    loop = asyncio.get_running_loop()
    current = _loops.get(name)
    if current is not None and not current.done() and current.get_loop() is loop:
        return current
    task = loop.create_task(_supervise(name, factory), name=f"comfyos:{name}")
    _loops[name] = task
    return task


async def _supervise(name: str, factory: Callable[[], Awaitable[object]]) -> None:
    """Run a long runner and restart it after it ends or crashes (cancelling ends it for good).

    A long runner always works for the real home – even when a request of a session in the demo happened to start it
    (demo.py). The task has a context of its own, so the mark is reset here for this task only."""
    demo.outside()
    pause = FIRST_PAUSE
    while True:
        started = time.monotonic()
        try:
            await factory()
            reason = "beendet"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = type(exc).__name__
        if time.monotonic() - started >= STABLE_AFTER:
            pause = FIRST_PAUSE
        report(name, reason, "neu gestartet")
        await asyncio.sleep(pause)
        pause = min(MAX_PAUSE, pause * 2)


def fire(coro: Coroutine, name: str = "aufgabe", quiet: bool = False) -> asyncio.Task:
    """Run a one-off task in the background without waiting for it. An error in it ends up in the event log.

    `quiet=True` when the caller evaluates the result itself (it waits a while for the task and reports
    an error on its own path) – then only the reference is held here.
    """
    task = asyncio.get_running_loop().create_task(coro, name=f"comfyos:{name}")
    _once.add(task)
    task.add_done_callback(lambda done: _finished(done, name, quiet))
    return task


def _finished(task: asyncio.Task, name: str, quiet: bool) -> None:
    """Remove a finished one-off task; report an error instead of swallowing it."""
    _once.discard(task)
    if task.cancelled():
        return
    exc = task.exception()          # fetch it – otherwise asyncio reports "exception was never retrieved"
    if exc is not None and not quiet:
        report(name, type(exc).__name__, "abgebrochen")


def report(name: str, reason: str, what: str = "Fehler") -> None:
    """Record a failure of a background task: always in the log, in the event log at most every REPORT_EVERY
    seconds per task and reason. Loops also call this themselves when a round fails and they keep running."""
    log.warning("Hintergrundaufgabe %s: %s (%s)", name, what, reason)
    now = time.monotonic()
    if now - _reported.get((name, reason), -REPORT_EVERY) < REPORT_EVERY:
        return
    _reported[(name, reason)] = now
    try:
        from . import events

        events.log("system", f"Hintergrundaufgabe „{name}“: {what} ({reason})")
    except Exception:
        pass   # the event log itself is disturbed – the line in the log above remains


def running() -> list[str]:
    """Names of the running long runners (for tests and diagnosis)."""
    return sorted(name for name, task in _loops.items() if not task.done())


async def cancel(name: str) -> None:
    """End a long runner and wait until it is done (e.g. the helper program of a plugin that is being switched
    off). If it does not exist, nothing happens."""
    task = _loops.pop(name, None)
    if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def cancel_all() -> None:
    """Cancel all background tasks of this event loop and wait until they have ended (on shutdown)."""
    loop = asyncio.get_running_loop()
    mine = [t for t in list(_loops.values()) + list(_once) if not t.done() and t.get_loop() is loop]
    for task in mine:
        task.cancel()
    if mine:
        await asyncio.gather(*mine, return_exceptions=True)
    for name in [n for n, t in _loops.items() if t.done()]:
        _loops.pop(name, None)
