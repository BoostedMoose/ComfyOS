"""Light effects per room (data/effects.json) – independent of scenes.

    { "version": 1,
      "rooms": { "<room id>": { "effect": "candle", "target": "room" | [<device id>, …],
                                "color_source": "own" | "scene" | "custom",
                                "color": "#rrggbb" | null, "kelvin": <K> | null, "speed": 0–100 | null,
                                "since": <unix time> } } }

own    = effect with the lamp's own colours (default)
scene  = colour of the active scene in the room (distributed per lamp as with the scene; a scene change sets it anew)
custom = a chosen colour or a white tone

A scene change leaves the effect in place. "Licht im Raum aus" forgets it (the lamps are off then).
"""
from __future__ import annotations

import threading
import time
from typing import Any

from . import jsonfile
from .light_model import HEX_RE
from .paths import DATA_DIR, ensure_dirs

EFFECTS_FILE = DATA_DIR / "effects.json"
VERSION = 1
MAX_ROOMS = 60
MAX_DEVICES = 100
COLOR_SOURCES = ("own", "scene", "custom")
_EFFECT_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")
_lock = threading.Lock()


def _num(v: Any, lo: float, hi: float) -> float | None:
    """Limit a number to the range lo…hi; None for everything that is not a finite number."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return float(max(lo, min(hi, v)))


def clean_entry(data: Any) -> dict[str, Any]:
    """Check and limit the effect entry of a room. Raises ValueError with a text for the user if no
    effect, no lamp or no colour is chosen."""
    if not isinstance(data, dict):
        raise ValueError("Ungültiger Effekt.")
    eff = str(data.get("effect") or "")[:40]
    if not eff or eff == "no_effect" or not set(eff) <= _EFFECT_CHARS:
        raise ValueError("Bitte einen Effekt wählen.")
    tgt = data.get("target", "room")
    if isinstance(tgt, list):
        tgt = list(dict.fromkeys(str(x)[:160] for x in tgt if isinstance(x, str) and x))[:MAX_DEVICES]
        if not tgt:
            raise ValueError("Bitte mindestens eine Lampe wählen.")
    else:
        tgt = "room"
    src = data.get("color_source") if data.get("color_source") in COLOR_SOURCES else "own"
    color = data.get("color") if isinstance(data.get("color"), str) and HEX_RE.match(data["color"]) else None
    kelvin = _num(data.get("kelvin"), 2000, 6500)
    if src == "custom" and color is None and kelvin is None:
        raise ValueError("Bitte eine Farbe wählen.")
    speed = _num(data.get("speed"), 0, 100)
    since = data.get("since")
    return {"effect": eff, "target": tgt, "color_source": src,
            "color": color.lower() if src == "custom" and color else None,
            "kelvin": round(kelvin) if src == "custom" and kelvin is not None and color is None else None,
            "speed": None if speed is None else round(speed),
            "since": float(since) if isinstance(since, (int, float)) and since == since and abs(since) < 1e11 else time.time()}


def _load() -> dict[str, Any]:
    """Content of effects.json, checked: invalid entries drop out, at most MAX_ROOMS rooms."""
    data = jsonfile.read(EFFECTS_FILE, {})
    rooms = {}
    raw = data.get("rooms") if isinstance(data.get("rooms"), dict) else {}
    for k, v in list(raw.items())[:MAX_ROOMS]:
        if not isinstance(k, str) or not k:
            continue
        try:
            rooms[k[:120]] = clean_entry(v)
        except ValueError:
            continue   # an invalid entry drops out
    return {"version": VERSION, "rooms": rooms}


def _save(data: dict[str, Any]) -> None:
    """Atomar nach effects.json schreiben."""
    ensure_dirs()
    jsonfile.write(EFFECTS_FILE, data)


def get() -> dict[str, Any]:
    """Alle gespeicherten Raum-Effekte."""
    with _lock:
        return _load()


def room(room_id: str) -> dict[str, Any] | None:
    """Effect of a room, or None."""
    return get()["rooms"].get(str(room_id))


def set_room(room_id: str, data: Any) -> dict[str, Any]:
    """Save an effect for a room (replaces an existing one). Returns the checked entry."""
    entry = clean_entry({**data, "since": None} if isinstance(data, dict) else data)
    with _lock:
        cur = _load()
        if room_id not in cur["rooms"] and len(cur["rooms"]) >= MAX_ROOMS:
            raise ValueError(f"Höchstens {MAX_ROOMS} Räume mit Effekt.")
        cur["rooms"][str(room_id)[:120]] = entry
        _save(cur)
        return entry


def clear(room_ids: list[str] | None) -> list[str]:
    """Forget effects (None = all). Returns the affected rooms."""
    with _lock:
        cur = _load()
        gone = [k for k in cur["rooms"] if room_ids is None or k in room_ids]
        for k in gone:
            cur["rooms"].pop(k)
        if gone:
            _save(cur)
        return gone
