"""Power consumption since reset and today (data/power.json).

    { "version": 1,
      "reset_at": <unix time>,                  # "consumption since …" (without a reset: first reading)
      "baselines": { "<device id>": <kWh> },    # meter reading at the reset or when first seen
      "day": "YYYY-MM-DD",                      # local calendar day of the daily values
      "day_base": { "<device id>": <kWh> },     # meter reading at 0:00 (last reading before midnight)
      "seen": { "<device id>": <kWh> },         # meter reading seen last
      "estimate": { "<device id>": <kWh> },     # devices without a meter: watts × time since reset
      "estimate_day": { "<device id>": <kWh> } }

Devices with a meter (`energy_kwh`) are computed exactly: reading − base. Devices that only report power (`power_w`)
are summed up from watts × time between two queries and marked as "estimated". Gaps longer than
MAX_GAP (ComfyOS off, device offline) do not count – rather too little than invented.
If a device resets its meter itself (the reading drops), the consumption since reset continues without a jump.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from . import demo, jsonfile
from .paths import DATA_DIR, ensure_dirs

POWER_FILE = DATA_DIR / "power.json"
VERSION = 1
MAX_ENTRIES = 500
MAX_GAP = 900          # Sekunden; mindestens – bei langsamer Abtastung gilt 2,5 × Abtastintervall
SAVE_EVERY = 60        # seconds; write estimates at most this often (spare the SD card)
_MAPS = ("baselines", "day_base", "seen", "estimate", "estimate_day")
_lock = threading.Lock()
_state: dict[str, Any] | None = None
_last: dict[str, tuple[float, float]] = {}     # device id -> (time, watts) of the last query (in memory only)
_saved_at = 0.0


def _day(now: float) -> str:
    """Lokaler Kalendertag eines Zeitpunkts als JJJJ-MM-TT."""
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _kwh(v: Any) -> float | None:
    """Meter reading or power as a number; None for everything that is not a usable number."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1e9:
        return None
    return float(v)


def _empty() -> dict[str, Any]:
    """Leerer Stand."""
    return {"version": VERSION, "reset_at": None, "day": None, **{k: {} for k in _MAPS}}


def _load() -> dict[str, Any]:
    """State from power.json, checked. It stays in memory – written only on base changes and at most
    every SAVE_EVERY seconds."""
    global _state
    if demo.active():
        return _empty()              # the demo records no consumption and never touches the state of the real home
    if _state is not None:
        return _state
    raw = jsonfile.read(POWER_FILE, {})
    data = _empty()
    if isinstance(raw, dict):
        # A time stamp, not a meter reading: with the limit of _kwh (1e9) every date after 2001 would be lost on loading –
        # that is why "consumption since …" started anew after every restart up to 0.19.
        at = raw.get("reset_at")
        data["reset_at"] = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) and 0 < at < 1e11 else None
        day = raw.get("day")
        data["day"] = day if isinstance(day, str) and len(day) == 10 else None
        for k in _MAPS:
            src = raw.get(k) if isinstance(raw.get(k), dict) else {}
            data[k] = {str(i)[:160]: v for i, v in ((i, _kwh(v)) for i, v in list(src.items())[:MAX_ENTRIES]) if v is not None}
    _state = data
    return data


def _save(data: dict[str, Any], now: float) -> None:
    """Write the state atomically to power.json (values rounded to four places) and remember the time."""
    global _saved_at
    ensure_dirs()
    jsonfile.write(POWER_FILE, {**data, **{k: {i: round(v, 4) for i, v in data[k].items()} for k in _MAPS}})
    _saved_at = now


def observe(devices: list[dict], now: float | None = None, sample_seconds: int = 300) -> None:
    """Book every device state that was fetched anyway (core._observe). Writes at once only on changes of the
    base, estimates at most every SAVE_EVERY seconds."""
    now = now or time.time()
    gap = max(MAX_GAP, 2.5 * sample_seconds)
    with _lock:
        data = _load()
        urgent = changed = False
        if data["reset_at"] is None:
            data["reset_at"] = now
            urgent = True
        today = _day(now)
        if data["day"] != today:
            # New day: the last reading before midnight is the base of the day
            data["day"], data["day_base"], data["estimate_day"] = today, dict(data["seen"]), {}
            urgent = True
        for d in devices:
            did = d.get("id")
            if not isinstance(did, str) or not did:
                continue
            did = did[:160]
            kwh = _kwh(d.get("energy_kwh"))
            if kwh is not None:
                prev = data["seen"].get(did)
                if prev is not None and kwh < prev - 0.0005:
                    # meter reset in the device (counts from 0 again): the consumption so far stays, the new reading is added
                    for base in (data["baselines"], data["day_base"]):
                        if did in base:
                            base[did] = base[did] - prev
                    urgent = True
                if len(data["seen"]) < MAX_ENTRIES or did in data["seen"]:
                    for base in (data["baselines"], data["day_base"]):
                        if did not in base:
                            base[did] = kwh
                            urgent = True
                    if prev != kwh:
                        data["seen"][did] = kwh
                        changed = True
                continue
            w = _kwh(d.get("power_w"))
            if w is None or not d.get("online", True):
                _last.pop(did, None)
                continue
            last = _last.get(did)
            _last[did] = (now, w)
            if last and 0 < now - last[0] <= gap and last[1] > 0:
                e = last[1] * (now - last[0]) / 3_600_000
                if len(data["estimate"]) < MAX_ENTRIES or did in data["estimate"]:
                    data["estimate"][did] = data["estimate"].get(did, 0.0) + e
                    data["estimate_day"][did] = data["estimate_day"].get(did, 0.0) + e
                    changed = True
        if urgent or (changed and now - _saved_at >= SAVE_EVERY):
            _save(data, now)


def reset(now: float | None = None) -> float:
    """Consumption since reset to 0: base = current meter reading of all known devices, clear the estimates."""
    now = now or time.time()
    with _lock:
        data = _load()
        data["reset_at"] = now
        data["baselines"] = dict(data["seen"])
        data["estimate"] = {}
        _save(data, now)
        return now


def flush() -> None:
    """Write unsaved estimates (on shutdown)."""
    with _lock:
        if _state is not None:
            _save(_state, time.time())


def summary(devices: list[dict], now: float | None = None) -> dict[str, Any]:
    """Per device kWh since reset and today; sum over all. 'estimated' = estimated from watts × time."""
    with _lock:
        data = _load()
        today = data["day"] == _day(now or time.time())   # no reading from today yet: today = 0
        rows: dict[str, dict[str, Any]] = {}
        for d in devices:
            did = str(d.get("id") or "")[:160]
            kwh = _kwh(d.get("energy_kwh"))
            if kwh is not None:
                base, day = data["baselines"].get(did, kwh), data["day_base"].get(did, kwh)
                rows[did] = {"kwh_since_reset": round(max(0.0, kwh - base), 3),
                             "kwh_today": round(max(0.0, kwh - day), 3) if today else 0.0,
                             "estimated": False}
            elif _kwh(d.get("power_w")) is not None:
                rows[did] = {"kwh_since_reset": round(data["estimate"].get(did, 0.0), 3),
                             "kwh_today": round(data["estimate_day"].get(did, 0.0), 3) if today else 0.0,
                             "estimated": True}
        return {"since": data["reset_at"],
                "kwh_since_reset": round(sum(r["kwh_since_reset"] for r in rows.values()), 3),
                "kwh_today": round(sum(r["kwh_today"] for r in rows.values()), 3),
                "estimated": any(r["estimated"] for r in rows.values()), "rows": rows}


def forget() -> None:
    """Zwischenstand im Speicher verwerfen (Tests, Umzugspaket/Backup eingespielt)."""
    global _state, _saved_at
    with _lock:
        _state = None
        _saved_at = 0.0
        _last.clear()
