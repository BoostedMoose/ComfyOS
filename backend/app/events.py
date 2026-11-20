"""Event log on top of logstore (category 'events')."""
from __future__ import annotations

import time
from typing import Any

from . import demo, logstore


def log(kind: str, message: str, data: dict[str, Any] | None = None, when: float | None = None) -> None:
    """Write an entry to the event log. `kind` is the kind (command, security, flow, system …), `data` are
    optional extra details. Secrets never belong in the text. In the demo the entry goes to the log of the demo in
    memory (demo.py) – the real log never sees it."""
    rec: dict[str, Any] = {"type": kind, "message": message}
    if data:
        rec["data"] = data
    if demo.active():
        demo.note(rec)
        return
    logstore.append("events", rec, when=when)


def recent(limit: int = 100, hours: int | None = None) -> list[dict[str, Any]]:
    """The last entries, newest first – at most `limit`, optionally only from the last `hours` hours (in the demo
    those of the demo)."""
    if demo.active():
        return demo.recent(limit)
    since = None if hours is None else time.time() - hours * 3600
    recs = logstore.read("events", since=since, limit=limit)
    recs.reverse()  # neueste zuerst
    return recs
