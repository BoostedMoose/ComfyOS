"""ComfyOS – power consumption: power now, consumption since reset and today, history per device.

    python tests/core/test_power.py [path-to-comfyos]

Checks power_store (base at the reset and when first seen, change of day, meter reset in the device,
estimate from watts × time without a meter, gaps do not count), the API (/api/power, POST /api/power/reset with
an event, /api/power/series with several devices) and that the series per device add up exactly to the sum.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("power")

from app import auth, core, events, history, logstore, main, power_store as ps  # noqa: E402


def at(day, hour, minute=0):
    """Local time on day `day` (1 = yesterday …) – change of day independent of the time zone of the test machine."""
    base = time.localtime(time.time() - 86400 * 3)
    return time.mktime((base.tm_year, base.tm_mon, base.tm_mday + day, hour, minute, 0, 0, 0, -1))


def meter(did, kwh, w=None, online=True):
    """Device row with a meter reading (kWh) and optionally the current power."""
    d = {"id": did, "name": did.title(), "energy_kwh": kwh, "online": online}
    if w is not None:
        d["power_w"] = w
    return d


def plug(did, w, online=True):
    """Device row that only reports the current power."""
    return {"id": did, "name": did.title(), "power_w": w, "online": online}


# 1) First reading: base = current meter reading, "since" = first reading
t0 = at(1, 12)
ps.observe([meter("kuehl", 100.0, 80)], now=t0)
data = json.loads(ps.POWER_FILE.read_text(encoding="utf-8"))
assert data["reset_at"] == t0 and data["baselines"] == {"kuehl": 100.0} and data["day_base"] == {"kuehl": 100.0}, data
ps.observe([meter("kuehl", 100.5, 80)], now=t0 + 600)
s = ps.summary([meter("kuehl", 100.5)])
assert s["since"] == t0 and s["rows"]["kuehl"]["kwh_since_reset"] == 0.5 and s["rows"]["kuehl"]["estimated"] is False, s
print("1 first reading: the meter reading becomes the base, consumption since the first reading exactly from the meter")

# 2) A device that only shows up later gets its reading as the base when first seen
ps.observe([meter("kuehl", 101.0), meter("wasch", 5000.0)], now=t0 + 1200)
s = ps.summary([meter("kuehl", 101.0), meter("wasch", 5002.0)])
assert s["rows"]["wasch"]["kwh_since_reset"] == 2.0 and s["rows"]["kuehl"]["kwh_since_reset"] == 1.0, s
assert s["kwh_since_reset"] == 3.0, s
print("2 new device: base when first seen, not its lifetime meter")

# 3) Without a meter: watts × time, only across queries without a gap; offline does not count
ps.observe([plug("tv", 100)], now=t0, sample_seconds=900)   # start (sampling 15 min: gap from 37.5 min)
ps.observe([plug("tv", 100)], now=t0 + 1800, sample_seconds=900)   # 100 W × 0,5 h = 0,05 kWh
ps.observe([plug("tv", 0)], now=t0 + 3600, sample_seconds=900)   # a further 0.05 kWh (value of the previous query)
ps.observe([plug("tv", 200)], now=t0 + 3600 * 5, sample_seconds=900)   # 4 h gap (ComfyOS off) -> does not count
ps.observe([plug("tv", 200, online=False)], now=t0 + 3600 * 5 + 300)
ps.observe([plug("tv", 200)], now=t0 + 3600 * 5 + 600)       # started anew after offline -> does not count
s = ps.summary([plug("tv", 200)])
assert s["rows"]["tv"]["estimated"] is True and s["rows"]["tv"]["kwh_since_reset"] == 0.1, s
assert s["estimated"] is True
print("3 without a meter: power × time (estimated), gaps and offline do not count")

# 4) Change of day: base of the day = last reading before midnight, estimates "today" start anew
ps.observe([meter("kuehl", 104.0), plug("tv", 200)], now=at(1, 23, 58))
ps.observe([meter("kuehl", 104.2)], now=at(2, 0, 3))
d = json.loads(ps.POWER_FILE.read_text(encoding="utf-8"))
assert d["day_base"]["kuehl"] == 104.0 and d["estimate_day"] == {}, d
ps.forget()            # restart: the state comes back from power.json
ps.observe([meter("kuehl", 105.0)], now=at(2, 8))
s = ps.summary([meter("kuehl", 105.0)], now=at(2, 8))
assert s["rows"]["kuehl"]["kwh_today"] == 1.0 and s["rows"]["kuehl"]["kwh_since_reset"] == 5.0, s
print("4 change of day: today from the reading before midnight, survives a restart")

# 5) Meter reset in the device (the reading drops): the consumption continues without a jump
ps.observe([meter("kuehl", 0.3)], now=at(2, 9))
s = ps.summary([meter("kuehl", 0.3)], now=at(2, 9))
assert s["rows"]["kuehl"]["kwh_since_reset"] == 5.3 and s["rows"]["kuehl"]["kwh_today"] == 1.3, s
print("5 the device resets its meter: no jump, no negative consumption")

# 6) Reset: everything to 0, base = current reading, estimates empty
r_at = ps.reset(now=at(2, 10))
s = ps.summary([meter("kuehl", 0.3), meter("wasch", 5000.0), plug("tv", 200)])
assert s["since"] == r_at and s["kwh_since_reset"] == 0 and all(v["kwh_since_reset"] == 0 for v in s["rows"].values()), s
ps.observe([meter("kuehl", 0.8)], now=at(2, 11))
assert ps.summary([meter("kuehl", 0.8)])["rows"]["kuehl"]["kwh_since_reset"] == 0.5
print("6 reset: consumption since reset 0, rises with the meter afterwards")

# 7) Broken power.json: no error, nothing infinite
ps.POWER_FILE.write_text('{"reset_at": "x", "baselines": {"a": "1e999", "b": [1]}, "seen": {"c": 1e400}}', encoding="utf-8")
ps.forget()
s = ps.summary([meter("a", 1.0)])
assert s["rows"]["a"]["kwh_since_reset"] == 0 and s["since"] is None, s
ps.POWER_FILE.unlink()
ps.forget()
print("7 damaged power.json: discarded, no exception")

# 8) API: /api/power with since/today, reset with an event, history per device = sum
DEVS = [meter("zaehler", 40.0, 230.0), plug("tv", 60.0), meter("lampe", 1.0)]
for d in DEVS:
    d.update({"room": "Wohnzimmer", "room_id": "wohnen", "kind": "switch"})


async def fake_devices():
    """Replacement for the device query: returns DEVS and lets the core observe them."""
    core._observe(DEVS)
    return [dict(d) for d in DEVS]


core.all_devices = fake_devices
auth.set_password("comfyos" + "-test")
from starlette.testclient import TestClient  # noqa: E402

client = TestClient(main.app)
assert client.post("/api/auth/login", json={"password": "comfyos" + "-test"}).status_code == 200


def ok(r):
    """The response must be 200; returns its JSON content."""
    assert r.status_code == 200, (r.status_code, r.text[:300])
    return r.json()


p = ok(client.get("/api/power"))
assert p["total_w"] == 290.0 and p["since"] and "kwh_today" in p and "kwh_since_reset" in p, p
row = {r["id"]: r for r in p["rows"]}
assert row["tv"]["estimated"] is True and row["zaehler"]["estimated"] is False and row["zaehler"]["kwh_since_reset"] == 0, row
DEVS[0]["energy_kwh"] = 41.5
p = ok(client.get("/api/power"))
assert p["kwh_since_reset"] >= 1.5 and {r["id"]: r for r in p["rows"]}["zaehler"]["kwh_since_reset"] == 1.5, p
r = ok(client.post("/api/power/reset"))
assert r["ok"] and r["kwh_since_reset"] == 0 and r["since"] >= p["since"], r
assert any("Stromverbrauch zurückgesetzt" in (e.get("message") or e.get("text") or "") for e in logstore.read("events", since=time.time() - 60)), \
    "the reset is missing in the event log"
ov = core.power_summary(DEVS)
assert ov["kwh_since_reset"] == 0 and ov["since"] == r["since"]
assert client.post("/api/power/reset", headers={"Origin": "https://boese.example.com"}).status_code in (400, 403), "Reset von fremder Seite"
print("8 API: power now, since reset, today; reset with an event; foreign page rejected")

history.reset()
logstore.clear("power")
now = time.time()
for k, (z, tv) in enumerate([(200, 50), (220, 0), (230, 60)]):
    history._last_power.clear()
    history.record_power([plug("zaehler", z), plug("tv", tv)], now=now - 3600 + k * 900)
ser = ok(client.get("/api/power/series?hours=24&device_id=zaehler&device_id=tv"))
assert [s["id"] for s in ser["series"]] == ["zaehler", "tv"], ser
for i, pt in enumerate(ser["points"]):
    assert abs(sum(s["points"][i]["w"] for s in ser["series"]) - pt["w"]) < 0.11, (i, ser)   # curve per device = sum
assert [p["w"] for p in ser["series"][1]["points"]] == [50, 0, 60] and ser["peak_w"] == 290, ser
one = ok(client.get("/api/power/series?hours=24&device_id=tv"))
assert [p["w"] for p in one["points"]] == [50, 0, 60] and len(one["series"]) == 1
total = ok(client.get("/api/power/series?hours=24"))
assert "series" not in total and [p["w"] for p in total["points"]] == [250, 220, 290], total
assert ok(client.get("/api/power/series?hours=744"))["bucket"] == 3600, "31 Tage: gröbere Fenster"
many = "&".join(f"device_id=d{i}" for i in range(200))
assert client.get(f"/api/power/series?hours=24&{many}").status_code == 200
print("9 history: one series per device, the series add up to the sum, the sum without devices as before")

print("ALL POWER TESTS OK")
