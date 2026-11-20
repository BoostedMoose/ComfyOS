"""ComfyOS – history filtering: data is only recorded for rooms with a thermostat.

    python tests/core/test_history.py [path-to-comfyos]

Checks:
  1. recording only for rooms with a thermostat
  2. a sensor in a room WITH a thermostat is recorded, in a room WITHOUT one it is not
  3. room_series() hides deleted rooms and rooms without a thermostat
  4. devices_with_history() filters likewise
  5. before the first record() call (_active_rooms=None): no filtering
  6. the storage stats endpoint returns bytes/files/entries; the system load names the share of ComfyOS
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("hist")

from app import auth, history, logstore, main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def dev(id, room_id, kind="thermostat", online=True, temperature=20.5, target=21.0):
    """Device row of a thermostat (or of another kind) in a room."""
    d = {"id": id, "name": f"Dev {id}", "room": f"Room {room_id}",
         "room_id": room_id, "kind": kind, "online": online, "temperature": temperature}
    if target is not None:
        d["target"] = target
    return d


# ---------------------------------------------------------------- 1 + 2: Aufzeichnung
history.reset()
t0 = time.time() - 10  # a little in the past, so that the throttle check does not block

devices = [
    dev("t1", "room_A", kind="thermostat"),
    dev("s1", "room_A", kind="sensor", target=None),   # Sensor in room_A (hat Thermostat)
    dev("s2", "room_B", kind="sensor", target=None),   # sensor in room_B (no thermostat)
]
n = history.record(devices, now=t0)
assert n == 2, f"expected 2 (thermostat + sensor in a room with a thermostat), got {n}"
assert history._active_rooms == frozenset({"room_A"}), history._active_rooms
print("1+2 recording: sensor without a thermostat room not stored, _active_rooms correct")

# ---------------------------------------------------------------- 3: room_series filtert
history.reset()
# enter log entries for both rooms directly (simulates old data)
logstore.append("heating", {"device_id": "t1", "name": "Thermostat", "room": "Room A",
                             "room_id": "room_A", "kind": "thermostat", "value": 21.0, "target": 22.0},
                when=time.time() - 3600)
logstore.append("heating", {"device_id": "s2", "name": "Sensor", "room": "Room B",
                             "room_id": "room_B", "kind": "sensor", "value": 20.0},
                when=time.time() - 3600)
# _active_rooms still None → no filtering
rooms_unfiltered = history.room_series(hours=24)
ids_unfiltered = {r["room_id"] for r in rooms_unfiltered}
assert "room_A" in ids_unfiltered and "room_B" in ids_unfiltered, \
    f"before record() both rooms should be visible: {ids_unfiltered}"
print("3a room_series without _active_rooms (startup): both rooms visible")

# now record() with only room_A (has a thermostat) → _active_rooms = {"room_A"}
history.record([dev("t1", "room_A", kind="thermostat")], now=time.time() - 100)
rooms_filtered = history.room_series(hours=24)
ids_filtered = {r["room_id"] for r in rooms_filtered}
assert "room_A" in ids_filtered, f"room_A should be visible: {ids_filtered}"
assert "room_B" not in ids_filtered, f"room_B (no thermostat) should be filtered: {ids_filtered}"
print("3b room_series after record(): room_B (no thermostat) filtered out")

# ---------------------------------------------------------------- 4: devices_with_history filtert
devs = history.devices_with_history(hours=24)
dev_ids = {d["id"] for d in devs}
assert "t1" in dev_ids, f"t1 should appear: {dev_ids}"
assert "s2" not in dev_ids, f"s2 (room_B, no thermostat) should be filtered: {dev_ids}"
print("4 devices_with_history: device from room_B filtered out")

# ---------------------------------------------------------------- 5: Speicher-Stats-Endpunkt
with TestClient(main.app) as client:
    client.cookies.set(auth.COOKIE, auth.create_session("test"))
    r = client.get("/api/logs/heating/stats").json()
    assert r["category"] == "heating", r
    assert isinstance(r["bytes"], int) and r["bytes"] > 0, f"bytes must be > 0: {r}"
    assert isinstance(r["files"], int) and r["files"] >= 1, r
    assert isinstance(r["entries"], int) and r["entries"] >= 2, r
    print(f"5 /api/logs/heating/stats: {r['entries']} entries, {r['bytes']} bytes, {r['files']} file(s)")

    # the system load names the share of ComfyOS (since 0.20.1): CPU time of the service, its memory
    # (Linux only) and the space of its data, logs and backups – the logs just written count too
    own = client.get("/api/system/info").json()["own"]
    assert isinstance(own["cpu"], (int, float)) and 0 <= own["cpu"] <= 100, own
    assert own["ram"] is None or own["ram"] > 0, own
    assert isinstance(own["disk"], int) and own["disk"] > 0, own
    again = client.get("/api/system/info").json()["own"]
    assert again["cpu"] == own["cpu"] and again["disk"] == own["disk"], "two queries in quick succession: the same value"
    print(f"5b /api/system/info: share of ComfyOS – CPU {own['cpu']} %, data {own['disk']} bytes")

    # the endpoints return filtered data
    rooms_api = client.get("/api/history/rooms?hours=24").json()
    ids_api = {x["room_id"] for x in rooms_api}
    assert "room_B" not in ids_api, f"room_B must not appear in /api/history/rooms: {ids_api}"
    print(f"6 /api/history/rooms: {len(rooms_api)} room(s) (room_B filtered)")

# ---------------------------------------------------------------- 7: valve opening (since 0.16)
history.reset()
logstore.clear("heating")
now = time.time()
for k, (v1, v2) in enumerate([(0, 20), (40, 60), (None, 100)]):
    history._last_sample.clear()
    a = dev("v1", "room_V")
    b = dev("v2", "room_V")
    if v1 is not None:
        a["valve"] = v1
    b["valve"] = v2
    history.record([a, b], now=now - 3600 + k * 600)
recs = logstore.read("heating", since=now - 7200)
assert [r.get("valve") for r in recs if r["device_id"] == "v2"] == [20, 60, 100], recs
assert [r.get("valve") for r in recs if r["device_id"] == "v1"] == [0, 40, None], "the valve is logged along, only missing without a report"
room = next(r for r in history.room_series(hours=24) if r["room_id"] == "room_V")
assert room["valve"] is True and [p["valve"] for p in room["points"]] == [10, 50, 100], room
history._last_sample.clear()
history.record([dev("n1", "room_N")], now=now - 60)
plain = next(r for r in history.room_series(hours=24) if r["room_id"] == "room_N")
assert plain["valve"] is False and all(p["valve"] is None for p in plain["points"]), plain
print("7 valve: logged along, averaged per room; without a report no value (channel detection: test of the plugin qivicon)")

# ---------------------------------------------------------------- 8: span of the heat map from the heating schedule
import json  # noqa: E402

from app import routines_store as rs  # noqa: E402


def plan_with(temps, levels=None):
    """Write a heating schedule with these temperatures and return the temperature range computed from it."""
    data = {"active": "r1", "routines": {"r1": {"name": "R", "schedule": {
        "wohnen": {"MO": [{"time": f"{6 + i:02d}:00", "temp": t} for i, t in enumerate(temps)]}}}}}
    if levels is not None:
        data["levels"] = levels
    rs.ROUTINES_FILE.parent.mkdir(parents=True, exist_ok=True)
    rs.ROUTINES_FILE.write_text(json.dumps(data), encoding="utf-8")
    return rs.heat_range()


assert plan_with([6, 17, 21.5]) == (17.0, 22.0), "'off' (6 °C) must not pull the scale down"
assert plan_with([8, 18, 21], {"OFF": 8.0, "ECO": 15.0}) == (17.0, 22.0), "an own level off does not count (18–21 → at least 4 °C)"
assert plan_with([10.5, 18, 23], {"OFF": 10.5}) == (18.0, 23.0)
assert plan_with([21, 21]) == (19.0, 23.0), "a single schedule temperature: at least 4 °C span"
assert plan_with([6, 6]) is None and plan_with(["Eco", None, True]) is None, "only off/nonsense: no schedule span"
assert plan_with([15, 22.5]) == (15.0, 23.0)
rs.ROUTINES_FILE.unlink()
assert rs.heat_range() is None
print("8 heat map span: without off/frost protection, whole degrees, at least 4 °C")

print("ALL HISTORY TESTS OK")
