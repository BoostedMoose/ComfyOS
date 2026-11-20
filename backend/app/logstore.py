"""Append-only logs under /comfyos/logs as JSONL, one file per month.

Four categories: 'heating' (temperature readings), 'power' (power readings),
'events' (events), 'messages' (free text/system). Every line is a JSON object with 'ts' (ISO time).
Reading is filtered by time window; deleting/exporting work per category.
"""
from __future__ import annotations

import json
import re
import threading
import time
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import demo
from .paths import EVENTS_LOG_DIR, HEATING_LOG_DIR, MESSAGES_LOG_DIR, POWER_LOG_DIR, ensure_dirs

_DIRS = {"heating": HEATING_LOG_DIR, "power": POWER_LOG_DIR, "events": EVENTS_LOG_DIR,
         "messages": MESSAGES_LOG_DIR}
_lock = threading.Lock()
_DEFAULT_RETENTION = max(12, int(os.getenv("COMFYOS_LOG_RETENTION_MONTHS", "13")))


def _retention_months() -> int:
    """How many months of logs are kept (setting, otherwise the default)."""
    from . import settings_store
    v = settings_store.load_settings().get("log_retention_months")
    return int(v) if v else _DEFAULT_RETENTION


RETENTION_MONTHS = _DEFAULT_RETENTION
# Upper limits per category as an additional safeguard for the SD card.
MAX_BYTES = {"heating": 256 * 1024 * 1024, "power": 256 * 1024 * 1024, "events": 64 * 1024 * 1024,
             "messages": 32 * 1024 * 1024}
_last_prune = 0.0


def _dir(category: str) -> Path:
    """Folder of a log category. Raises ValueError for an unknown category – the name never gets into a path
    unchecked."""
    if category not in _DIRS:
        raise ValueError(f"Unbekannte Log-Kategorie: {category}")
    return _DIRS[category]


def _month_file(category: str, when: float) -> Path:
    """Log file of a category for the month of a point in time."""
    stamp = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m")
    return _dir(category) / f"{category}-{stamp}.jsonl"


def prune(now: float | None = None) -> dict[str, int]:
    """Remove old monthly files; additionally keep the storage limits per log type."""
    global _last_prune
    now = now or time.time()
    cutoff = datetime.fromtimestamp(now, timezone.utc)
    month_index = cutoff.year * 12 + cutoff.month - _retention_months()
    cutoff_key = f"{month_index // 12:04d}-{month_index % 12 + 1:02d}"
    removed = {}
    with _lock:
        for category in _DIRS:
            files = _iter_files(category)
            gone = 0
            for path in files:
                if path.stem[-7:] < cutoff_key:
                    try:
                        path.unlink(); gone += 1
                    except OSError:
                        pass   # cannot be deleted right now: the next run tries again
            files = _iter_files(category)
            total = sum(p.stat().st_size for p in files if p.exists())
            # With an extremely high volume remove the oldest months first. Normally
            # this limit does not apply, so that at least 12 months are kept.
            for path in files:
                if total <= MAX_BYTES[category]:
                    break
                try:
                    size = path.stat().st_size
                    path.unlink(); total -= size; gone += 1
                except OSError:
                    pass   # cannot be deleted right now: the next run tries again
            removed[category] = gone
    _last_prune = now
    return removed


_SECRET_KEY = re.compile(r"(^|[_-])(pass|password|passwort|secret|token|authorization|cookie|api[_-]?key|"
                         r"private[_-]?key|credentials?)$", re.I)
_SECRET_TEXT = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "***"),            # JWT
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "***"),
    (re.compile(r"(?i)\b(password|passwort|token|secret|access_token|refresh_token)=([^&\s\"']+)"), r"\1=***"),
]


def redact(value: Any) -> Any:
    """Remove secrets from log entries: fields with a telling name and typical patterns in the text."""
    if isinstance(value, dict):
        return {k: ("***" if isinstance(k, str) and _SECRET_KEY.search(k) and v not in (None, "", [], {})
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for rx, repl in _SECRET_TEXT:
            value = rx.sub(repl, value)
    return value


def append(category: str, record: dict[str, Any], when: float | None = None) -> None:
    """Append an entry to the monthly log of the category. Texts go through `redact` (no secrets in the log);
    now and then old data is cleaned up along the way. The demo (demo.py) writes nothing here."""
    if demo.active():
        return
    ensure_dirs()
    when = when or time.time()
    if when - _last_prune > 6 * 3600:
        prune(when)
    if category not in ("heating", "power"):   # readings contain no texts; saves time with many entries
        record = redact(record)
    record = {"ts": datetime.fromtimestamp(when, timezone.utc).isoformat(), **record}
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        with open(_month_file(category, when), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _iter_files(category: str) -> list[Path]:
    """Monthly files of a category, oldest first (none in the demo: it shows nothing of the real logs)."""
    if demo.active():
        return []
    d = _dir(category)
    return sorted(d.glob(f"{category}-*.jsonl")) if d.exists() else []


def read(category: str, since: float | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Entries of a category in chronological order: from `since` (Unix time) and/or only the last `limit`. The demo
    (demo.py) has no history: it reads nothing of the real logs."""
    if demo.active():
        return []
    cutoff = None if since is None else datetime.fromtimestamp(since, timezone.utc).isoformat()
    out: list[dict[str, Any]] = []
    with _lock:
        for path in _iter_files(category):
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue   # damaged line (e.g. power failure while writing)
                    if cutoff and rec.get("ts", "") < cutoff:
                        continue
                    out.append(rec)
            except OSError:
                continue   # file unreadable right now
    out.sort(key=lambda r: r.get("ts", ""))
    if limit:
        out = out[-limit:]
    return out


def stats(category: str) -> dict[str, Any]:
    """Size of a category: files, entries, bytes, months."""
    files = _iter_files(category)
    count = 0
    size = 0
    for p in files:
        try:
            size += p.stat().st_size
            count += sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())
        except OSError:
            continue   # file unreadable right now
    return {"category": category, "files": len(files), "entries": count, "bytes": size,
            "months": [p.stem.split("-", 1)[1] for p in files]}


def clear(category: str) -> int:
    """Delete all log files of a category. Returns the number of deleted files."""
    removed = 0
    with _lock:
        for path in _iter_files(category):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue   # cannot be deleted: does not count
    return removed


def export_text(category: str) -> str:
    """All entries of a category as one JSONL text (for download)."""
    recs = read(category)
    if category not in ("heating", "power"):   # entries from older versions too, without a filter
        recs = [redact(r) for r in recs]
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in recs)
