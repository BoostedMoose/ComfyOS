"""Heating history and device watching.

* record_power(): power (watts) per metering device under logs/power; power_series()
  combines it into a consumption curve.
* record(): measured and target temperature per device under logs/heating (JSONL per month),
  at most every SAMPLE_SECONDS per device. Only rooms with at least one thermostat
  are recorded. The Home Base itself stores no history.
* room_series(): combines the readings per room into measured/target curves; filters out
  deleted rooms and rooms without a thermostat as soon as _active_rooms is known.
* watch(): compares the device state with the previous one and writes events
  (window opened/closed, device offline/online again, battery weak).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from . import events, logstore, settings_store

_DEFAULT_SAMPLE = 300
BUCKET_SECONDS = 300


def _sample_seconds() -> int:
    """Sampling rate of the history in seconds (setting, otherwise the default of 5 minutes)."""
    v = settings_store.load_settings().get("log_sample_seconds")
    return int(v) if v else _DEFAULT_SAMPLE


SAMPLE_SECONDS = _DEFAULT_SAMPLE

_lock = threading.Lock()
_last_sample: dict[str, float] = {}
_last_state: dict[str, dict[str, Any]] = {}
# Rooms with at least one thermostat; None as long as record() has never been called.
_active_rooms: frozenset[str] | None = None


def record(devices: list[dict[str, Any]], now: float | None = None) -> int:
    """Write temperature, target and valve opening of the devices to the heating log – per device at most once per
    sampling interval. Returns the number of new entries."""
    global _active_rooms
    now = now or time.time()
    added = 0
    # determine the rooms with at least one thermostat (offline ones too)
    rooms_with_thermostat: frozenset[str] = frozenset(
        d["room_id"] for d in devices if d.get("kind") == "thermostat" and d.get("room_id")
    )
    _active_rooms = rooms_with_thermostat
    sample = _sample_seconds()   # once per call – do not read the settings file per device
    with _lock:
        for d in devices:
            value = d.get("temperature")
            did = d.get("id")
            if value is None or did is None or not d.get("online", True):
                continue
            if d.get("room_id") not in rooms_with_thermostat:
                continue  # no thermostat in the room → do not record
            if now - _last_sample.get(did, 0) < sample:
                continue
            _last_sample[did] = now
            rec = {"device_id": did, "name": d.get("name"), "room": d.get("room"),
                   "room_id": d.get("room_id"), "kind": d.get("kind"), "value": round(float(value), 2)}
            if d.get("target") is not None:
                rec["target"] = round(float(d["target"]), 1)
            if isinstance(d.get("valve"), (int, float)) and not isinstance(d.get("valve"), bool):
                rec["valve"] = int(max(0, min(100, d["valve"])))   # valve opening in %
            logstore.append("heating", rec, when=now)
            added += 1
    return added


_last_power: dict[str, float] = {}


def record_power(devices: list[dict[str, Any]], now: float | None = None) -> int:
    """Power (watts) per metering device under logs/power, at most every SAMPLE_SECONDS per device.
    Only devices that meter themselves (smart plugs, meters) – ComfyOS extrapolates nothing."""
    now = now or time.time()
    added = 0
    sample = _sample_seconds()
    with _lock:
        for d in devices:
            did, value = d.get("id"), d.get("power_w")
            if did is None or not isinstance(value, (int, float)) or not d.get("online", True):
                continue
            if now - _last_power.get(did, 0) < sample:
                continue
            _last_power[did] = now
            rec = {"device_id": did, "name": d.get("name"), "room": d.get("room"), "room_id": d.get("room_id"),
                   "watt": round(float(value), 2)}
            if isinstance(d.get("energy_kwh"), (int, float)):
                rec["kwh"] = round(float(d["energy_kwh"]), 3)
            logstore.append("power", rec, when=now)
            added += 1
    return added


def power_devices(hours: int = 168) -> list[dict[str, Any]]:
    """Devices for which power was recorded in the period, with the number of data points and the peak value."""
    seen: dict[str, dict[str, Any]] = {}
    for r in logstore.read("power", since=time.time() - hours * 3600):
        did = r.get("device_id")
        if did is None:
            continue
        e = seen.setdefault(did, {"id": did, "name": r.get("name"), "room": r.get("room"), "samples": 0, "max_w": 0.0})
        e["samples"] += 1
        e["max_w"] = max(e["max_w"], float(r.get("watt") or 0))
    return sorted(seen.values(), key=lambda e: (-e["max_w"], str(e["name"])))


MAX_SERIES = 40   # at most this many single series per query


def _bucket_for(hours: int) -> int:
    """Bucket width: 5 min up to 24 h, coarser above – a month stays at ~750 points per series."""
    return BUCKET_SECONDS if hours <= 24 else (900 if hours <= 168 else 3600)


def power_series(hours: int = 24, device_id: str | list[str] | None = None) -> dict[str, Any]:
    """Consumption history in time buckets: sum of all (or the chosen) devices per bucket.
    Per bucket one value per device counts (the highest). With device_id there is additionally one series per device
    (`series`), which add up exactly to the sum in every bucket. 'kwh' estimates the energy (power × time)."""
    ids = [device_id] if isinstance(device_id, str) else list(device_id or [])
    want = set(ids)
    bucket = _bucket_for(hours)
    buckets: dict[int, dict[str, float]] = {}
    names: dict[str, str] = {}
    for r in logstore.read("power", since=time.time() - hours * 3600):
        did = str(r.get("device_id"))
        if want and did not in want:
            continue
        try:
            ts = datetime.fromisoformat(r["ts"]).timestamp()
        except Exception:
            continue   # entry without a readable time
        key = int(ts // bucket) * bucket
        b = buckets.setdefault(key, {})
        b[did] = max(b.get(did, 0.0), float(r.get("watt") or 0))   # one value per device per bucket
        if r.get("name"):
            names[did] = str(r["name"])
    points = [{"t": key, "w": round(sum(b.values()), 1)} for key, b in sorted(buckets.items())]
    kwh = round(sum(p["w"] for p in points) * bucket / 3600 / 1000, 3) if points else 0.0
    peak = max((p["w"] for p in points), default=0.0)
    avg = round(sum(p["w"] for p in points) / len(points), 1) if points else 0.0
    out = {"hours": hours, "bucket": bucket, "points": points, "kwh": kwh, "peak_w": peak, "avg_w": avg}
    if ids:
        out["series"] = []
        for did in ids:
            # buckets without a reading of the device count as 0 W as long as other devices have measured –
            # only that way does the sum of the series give the total curve
            pts = [{"t": key, "w": round(b.get(did, 0.0), 1)} for key, b in sorted(buckets.items())]
            if any(did in b for b in buckets.values()):
                out["series"].append({"id": did, "name": names.get(did, did), "points": pts,
                                      "kwh": round(sum(p["w"] for p in pts) * bucket / 3_600_000, 3)})
    return out


def watch(devices: list[dict[str, Any]]) -> int:
    """Log state changes as events. The first pass only remembers."""
    logged = 0
    first = not _last_state
    for d in devices:
        did = d.get("id")
        if did is None:
            continue
        now_state = {"online": d.get("online"), "open": d.get("open"), "low": bool(d.get("low_battery"))}
        prev = _last_state.get(did)
        _last_state[did] = now_state
        if first or prev is None:
            continue
        name = f"{d.get('name')} ({d.get('room')})"
        if prev["online"] and not now_state["online"]:
            events.log("device", f"{name} ist offline"); logged += 1
        elif prev["online"] is False and now_state["online"]:
            events.log("device", f"{name} ist wieder online"); logged += 1
        if d.get("kind") == "contact" and prev["open"] is not None and now_state["open"] is not None \
                and prev["open"] != now_state["open"]:
            events.log("window", f"{name} {'geöffnet' if now_state['open'] else 'geschlossen'}"); logged += 1
        if now_state["low"] and not prev["low"]:
            events.log("battery", f"{name}: Batterie schwach"); logged += 1
    return logged


def _recent(hours: int) -> list[dict[str, Any]]:
    """Entries of the heating log from the last `hours` hours."""
    return logstore.read("heating", since=time.time() - hours * 3600)


def devices_with_history(hours: int = 168) -> list[dict[str, Any]]:
    """Devices with a heating history in the period, most data points first – only from rooms that still have a
    thermostat."""
    active = _active_rooms  # local snapshot; None = not known yet, do not filter
    seen: dict[str, dict[str, Any]] = {}
    for r in _recent(hours):
        did = r.get("device_id")
        if did is None:
            continue
        if active is not None and r.get("room_id") not in active:
            continue  # the room no longer exists / has no thermostat
        e = seen.setdefault(did, {"id": did, "name": r.get("name"), "room": r.get("room"), "samples": 0})
        e["samples"] += 1
    return sorted(seen.values(), key=lambda e: (-e["samples"], str(e["name"])))


def series(device_id: str | None, hours: int = 24) -> list[dict[str, Any]]:
    """Data series of a device (without an argument: of the device with the most data points): time, value, target."""
    if not device_id:
        devs = devices_with_history()
        device_id = devs[0]["id"] if devs else None
    if not device_id:
        return []
    return [{"time": r.get("ts"), "value": r.get("value"), "target": r.get("target"),
             "name": r.get("name"), "room": r.get("room")}
            for r in _recent(hours) if r.get("device_id") == device_id]


def room_series(hours: int = 24) -> list[dict[str, Any]]:
    """Per room: measured (mean of the readings) and target (mean of the thermostat set points)
    in time buckets of BUCKET_SECONDS. Sensors without a set point only count towards measured.
    Rooms without a thermostat and deleted rooms are filtered out (as soon as
    _active_rooms is known after the first record() call)."""
    active = _active_rooms  # local snapshot; None = not known yet, do not filter
    rooms: dict[str, dict[str, Any]] = {}
    for r in _recent(hours):
        key = r.get("room_id") or r.get("room") or "?"
        if active is not None and r.get("room_id") not in active:
            continue  # the room no longer exists / has no thermostat
        try:
            ts = datetime.fromisoformat(r["ts"]).timestamp()
        except Exception:
            continue   # entry without a readable time
        bucket = int(ts // BUCKET_SECONDS) * BUCKET_SECONDS
        room = rooms.setdefault(key, {"room_id": r.get("room_id"), "room": r.get("room"), "buckets": {}})
        room["room"] = r.get("room") or room["room"]
        b = room["buckets"].setdefault(bucket, {"ist": [], "soll": [], "valve": {}})
        if r.get("value") is not None and r.get("kind") in ("thermostat", "sensor", "motion", None):
            b["ist"].append(float(r["value"]))
        if r.get("target") is not None:
            b["soll"].append(float(r["target"]))
        if isinstance(r.get("valve"), (int, float)):
            b["valve"][str(r.get("device_id"))] = float(r["valve"])   # one value per thermostat per bucket
    out = []
    for key, room in rooms.items():
        pts = []
        has_valve = False
        for bucket in sorted(room["buckets"]):
            b = room["buckets"][bucket]
            v = list(b["valve"].values())
            has_valve = has_valve or bool(v)
            pts.append({"t": bucket,
                        "ist": round(sum(b["ist"]) / len(b["ist"]), 2) if b["ist"] else None,
                        "soll": round(sum(b["soll"]) / len(b["soll"]), 1) if b["soll"] else None,
                        # valve opening: mean of the thermostats in the room (0–100 %), None = not reported
                        "valve": round(sum(v) / len(v)) if v else None})
        if pts:
            out.append({"room_id": room["room_id"], "room": room["room"], "points": pts, "valve": has_valve})
    out.sort(key=lambda x: str(x["room"]))
    return out


def reset() -> None:
    """Reset the module state (for tests)."""
    global _active_rooms
    with _lock:
        _last_sample.clear()
        _last_power.clear()
        _last_state.clear()
    _active_rooms = None
