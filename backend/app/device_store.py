"""Device settings of the core (data/devices.json) – for devices of all plugins, independent of their source.

    { "version": 2,
      "locked":  { "<device id>": { "at": <unix time>, "by": "user" | "flow:<flow id>" } },
      "names":   { "<device id>": "<own name>" },                # overrides the name of the source
      "hidden":  { "<device id>": "<name when removed>" },       # removed from ComfyOS (the source stays untouched)
      "rooms":   { "<device id>": "<room key>" },                # room for devices whose source manages no rooms
      "adopted": [ "<device id>", … ] }                          # adopted devices (only "found" by plugins)

Room keys as in room_assign: room id of the Home Base or "plan:<id>".

ComfyOS does not switch locked devices – neither interface nor flow, scene, "Alle Lichter" or effect.
The only exception is the source LOCK_SOURCE (flow node "Gerät sperren/entsperren"). The lock is enforced
in control.py on every switching path (apply_device, send_light, _lamp) and in the plugin loader. The lock
only affects ComfyOS: wall switches, the Hue app or the Magenta app keep switching.

Since 0.18 (plugin system) names, "removed", local rooms and adopted devices live here too.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import demo, jsonfile
from .paths import DATA_DIR, ensure_dirs

DEVICES_FILE = DATA_DIR / "devices.json"
VERSION = 2
MAX_ENTRIES = 500
MAX_ID = 160
MAX_NAME = 80
LOCK_SOURCE = "lock_node"
_BY_RE = re.compile(r"^(user|flow:[A-Za-z0-9_-]{1,40})$")
_lock = threading.Lock()
_cache: tuple[Path, float, dict[str, Any]] | None = None   # (file, mtime, data) – is_locked runs per lamp and tick


class DeviceLocked(HTTPException):
    """Command to a locked device. As an HTTPException it ends up in the API as 423 with an understandable text."""

    def __init__(self, name: str | None):
        """Error 423 with the name of the device in the text."""
        super().__init__(423, f"{name or 'Gerät'} ist gesperrt – im Gerätemenü entsperren.")


def _empty() -> dict[str, Any]:
    """Leerer Store."""
    return {"version": VERSION, "locked": {}, "names": {}, "hidden": {}, "rooms": {}, "adopted": []}


def _text_map(raw: Any, limit: int, keep_empty: bool = False) -> dict[str, str]:
    """{id: text} with limited lengths; empty values only where they mean something (removed without a name)."""
    out = {}
    for k, v in list(raw.items())[:MAX_ENTRIES] if isinstance(raw, dict) else []:
        if isinstance(k, str) and k and (v or keep_empty):
            out[k[:MAX_ID]] = str(v or "")[:limit]
    return out


def _load() -> dict[str, Any]:
    """Content of devices.json, checked and limited. Stays in memory as long as the file does not change – the
    lock is queried per lamp and tick."""
    global _cache
    file = demo.path(DEVICES_FILE)      # in the demo: data/demo/devices.json (demo.py)
    try:
        mtime = file.stat().st_mtime
    except OSError:
        return _empty()
    if _cache is not None and _cache[0] == file and _cache[1] == mtime:
        return json.loads(json.dumps(_cache[2]))
    data = jsonfile.read(DEVICES_FILE, {})
    raw = data.get("locked") if isinstance(data.get("locked"), dict) else {}
    locked = {}
    for k, v in list(raw.items())[:MAX_ENTRIES]:
        if not isinstance(k, str) or not k:
            continue
        v = v if isinstance(v, dict) else {}
        at = v.get("at")
        by = v.get("by") if isinstance(v.get("by"), str) and _BY_RE.match(v["by"]) else "user"
        locked[k[:MAX_ID]] = {"at": float(at) if isinstance(at, (int, float)) and at == at and abs(at) < 1e11 else 0.0,
                              "by": by}
    adopted = data.get("adopted")
    out = {"version": VERSION, "locked": locked, "names": _text_map(data.get("names"), MAX_NAME),
           "hidden": _text_map(data.get("hidden"), MAX_NAME, keep_empty=True), "rooms": _text_map(data.get("rooms"), MAX_NAME),
           "adopted": list(dict.fromkeys(str(x)[:MAX_ID] for x in adopted[:MAX_ENTRIES] if x)) if isinstance(adopted, list) else []}
    _cache = (file, mtime, out)
    return json.loads(json.dumps(out))


def _save(data: dict[str, Any]) -> None:
    """Write atomically to devices.json and discard the cache."""
    global _cache
    ensure_dirs()
    jsonfile.write(DEVICES_FILE, data)
    _cache = None


def get() -> dict[str, Any]:
    """The whole store: locks, names, removed, local rooms, adopted."""
    with _lock:
        return _load()


# ---------------------------------------------------------------- Sperre
def locked() -> dict[str, dict[str, Any]]:
    """Locked devices: {device id: {at, by}}."""
    return get()["locked"]


def is_locked(device_id: str) -> bool:
    """Is the device locked?"""
    return str(device_id) in get()["locked"]


def lock(device_id: str, by: str = "user") -> bool:
    """Lock a device. True = was not locked before."""
    if not _BY_RE.match(by or ""):
        raise ValueError("Ungültige Quelle.")
    did = str(device_id)[:MAX_ID]
    with _lock:
        data = _load()
        if did in data["locked"]:
            return False
        if len(data["locked"]) >= MAX_ENTRIES:
            raise ValueError(f"Höchstens {MAX_ENTRIES} gesperrte Geräte.")
        data["locked"][did] = {"at": time.time(), "by": by}
        _save(data)
        return True


def unlock(device_id: str) -> bool:
    """Sperre aufheben. True = war gesperrt."""
    with _lock:
        data = _load()
        if data["locked"].pop(str(device_id), None) is None:
            return False
        _save(data)
        return True


def apply(devices: list[dict]) -> None:
    """Attach the lock state to the device list (interface, flows)."""
    lk = locked()
    for d in devices:
        if d.get("id") in lk:
            d["locked"] = True
            d["locked_by"] = lk[d["id"]]["by"]
        else:
            d.pop("locked", None)
            d.pop("locked_by", None)


# ---------------------------------------------------------------- Names, removed, rooms, adopted
def _set_text(section: str, device_id: str, value: str | None, keep_empty: bool = False) -> None:
    """Set or delete a text entry (name, removed mark, local room). `keep_empty`: an empty text is
    a valid entry (removed without a name)."""
    did = str(device_id)[:MAX_ID]
    with _lock:
        data = _load()
        if value or (keep_empty and value is not None):
            if did not in data[section] and len(data[section]) >= MAX_ENTRIES:
                raise ValueError(f"Höchstens {MAX_ENTRIES} Einträge.")
            data[section][did] = str(value)[:MAX_NAME]
        elif data[section].pop(did, None) is None:
            return
        _save(data)


def set_name(device_id: str, name: str | None) -> None:
    """Set an own name (None/empty = the name of the source again)."""
    _set_text("names", device_id, name)


def set_hidden(device_id: str, name: str | None, hidden: bool) -> None:
    """Remove a device from ComfyOS or restore it; the name is shown in the list "Entfernt"."""
    _set_text("hidden", device_id, (name or "") if hidden else None, keep_empty=True)


def set_room(device_id: str, key: str | None) -> None:
    """Set the local room of a device whose source manages no rooms (None = automatic)."""
    _set_text("rooms", device_id, key)


def set_adopted(device_id: str, adopted: bool, room: str | None = None) -> None:
    """Adopt a found device or give it back (the local room then goes with it)."""
    did = str(device_id)[:MAX_ID]
    with _lock:
        data = _load()
        if adopted:
            if did not in data["adopted"]:
                if len(data["adopted"]) >= MAX_ENTRIES:
                    raise ValueError(f"Höchstens {MAX_ENTRIES} übernommene Geräte.")
                data["adopted"].append(did)
            if room:
                data["rooms"][did] = str(room)[:MAX_NAME]
        else:
            data["adopted"] = [x for x in data["adopted"] if x != did]
            data["rooms"].pop(did, None)
        _save(data)


def prune(known_ids: set[str], keep_prefixes: tuple[str, ...] = ()) -> None:
    """Remove entries for devices that no longer exist at any source. `keep_prefixes` protects the devices of
    plugins whose list proves nothing right now (disabled, unreachable, not configured) – otherwise
    adopted devices, names and locks would be lost only because a bridge was briefly away or disconnected."""
    def keep(k: str) -> bool:
        return k in known_ids or (bool(keep_prefixes) and k.startswith(keep_prefixes))

    with _lock:
        data = _load()
        clean = {**data, "locked": {k: v for k, v in data["locked"].items() if keep(k)},
                 "names": {k: v for k, v in data["names"].items() if keep(k)},
                 "hidden": {k: v for k, v in data["hidden"].items() if keep(k)},
                 "rooms": {k: v for k, v in data["rooms"].items() if keep(k)},
                 "adopted": [x for x in data["adopted"] if keep(x)]}
        if clean != data:
            _save(clean)
