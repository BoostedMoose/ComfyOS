"""ComfyOS Flow – engine (format 3): state triggers, wait markers, loops, actions, live steps, store, API.

    python tests/flow/test_flows.py [path-to-comfyos]

Section 3 is the case from the hallway: the light stays on as long as the motion detector reports motion – without a
time limit, without a lost or doubled trigger, even across a restart.
"""
import asyncio
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("flow")

import httpx  # noqa: E402

from app import (auth, core, device_store, events, flow_catalog as fc, flow_engine as fe, flows_store,  # noqa: E402
                 home_store, main, scenes_store)
from support.homebase import QiviconConnector, install_homebase  # noqa: E402

M, M2, C, L, L2, P, T, S = ("dect:motion:1", "dect:motion:2", "dect:contact:1", "dect:lamp:1", "hue:0210:abc:7",
                            "dect:plug:1", "dect:thermo:1", "dect:sensor:1")


def base_devices():
    """The devices of this test in their initial state."""
    return {
        M: {"id": M, "name": "Flur Bewegung", "kind": "motion", "room": "Flur", "room_id": "r1", "online": True, "motion": False, "battery": 80, "temperature": 19.0},
        M2: {"id": M2, "name": "Treppe Bewegung", "kind": "motion", "room": "Flur", "room_id": "r1", "online": True, "motion": False, "battery": 60},
        C: {"id": C, "name": "Bad Fenster", "kind": "contact", "room": "Bad", "room_id": "r2", "online": True, "open": False, "battery": 30},
        L: {"id": L, "name": "Flur Lampe", "kind": "light", "room": "Flur", "room_id": "r1", "online": True, "on": False, "control_item": "L1"},
        L2: {"id": L2, "name": "Stehlampe", "kind": "light", "room": "Wohnen", "room_id": "r3", "online": True, "on": False, "brightness": 40,
             "control_item": "hue:7", "light": {"caps": {"effects": ["candle"]}}},
        P: {"id": P, "name": "Aquarium", "kind": "switch", "room": "Flur", "room_id": "r1", "online": True, "on": True, "control_item": "P1", "power_w": 12.0},
        T: {"id": T, "name": "Bad Heizung", "kind": "thermostat", "room": "Bad", "room_id": "r2", "online": True, "temperature": 21.0, "target": 21.0, "control_item": "T1"},
        S: {"id": S, "name": "Wohnzimmer", "kind": "sensor", "room": "Wohnen", "room_id": "r3", "online": True, "temperature": 20.0},
    }


DEV = base_devices()
calls, published, pushed = [], [], []
LOCKED: set = set()
ACTIVE: dict = {}          # aktive Szenen (Ziel -> Szenen-ID)
SCENES = {"abend": {"id": "abend", "name": "Abend"}}
scenes_store.active_map = lambda: dict(ACTIVE)
scenes_store.scene = lambda sid: SCENES.get(sid)


async def h_devices():
    """Replacement for the device query."""
    return [dict(d) for d in DEV.values()]


async def h_apply_device(d, s):
    """Replacement for switching a device: record it and take over the state; locked devices refuse."""
    if d["id"] in LOCKED:
        raise device_store.DeviceLocked(d.get("name"))
    calls.append(("dev", d["id"], s))
    for k in ("on", "target", "brightness", "position"):
        if k in s:
            DEV[d["id"]][k] = s[k]


async def h_apply_lights(on, rooms):
    """Replacement for "Lichter schalten": record it and switch the affected lamps."""
    calls.append(("lights", on, rooms))
    hit = [d for d in DEV.values() if d["kind"] == "light" and (rooms is None or d["room_id"] in rooms)]
    for d in hit:
        d["on"] = on
    return {"switched": len(hit)}


async def h_apply_home(s):
    """Replacement for home/away: record it and set the state."""
    calls.append(("home", s))
    home_store.set_state(s)


async def h_apply_routine(k):
    """Replacement for activating a heating routine: record it."""
    calls.append(("routine", k))


async def h_apply_scene(sid, rooms, devs, group=None):
    """Replacement for starting a scene: record it."""
    calls.append(("scene", sid, rooms, devs, group))
    return {"switched": 2}


async def h_stop_scene(keys, restore=True):
    """Replacement for ending a scene: record it."""
    calls.append(("stop", keys, restore))
    return {"stopped": 1}


async def h_set_lock(d, locked, fid):
    """Replacement for locking a device: record it."""
    calls.append(("lock", d["id"], locked, fid))
    return True


async def h_switch_locked(d, s):
    """Replacement for switching despite the lock: record it."""
    calls.append(("force", d["id"], s))


NOW = [datetime(2026, 11, 16, 17, 58, 0).timestamp()]       # Montag


async def fake_sleep(s):
    """Wartet auf die Testuhr statt auf echte Zeit."""
    until = NOW[0] + s
    while NOW[0] < until:
        await asyncio.sleep(0)


fe._sleep = fake_sleep
fe.clock = lambda: NOW[0]
eng = fe.engine


def at(y, mo, d, h, mi, s=0):
    """Testuhr auf diesen Zeitpunkt stellen."""
    NOW[0] = datetime(y, mo, d, h, mi, s).timestamp()


def N(id_, type_, cfg=None, **config):
    """Node of a flow; settings as a dictionary and/or as keywords."""
    return {"id": id_, "type": type_, "x": 0, "y": 0, "config": {**(cfg or {}), **config}}


def E(a, ap, b, bp):
    """Connection between two ports of a flow."""
    return {"from": a, "fromPort": ap, "to": b, "toPort": bp}


def hooks():
    """Connect the engine with the replacement functions of this test."""
    eng.hooks.update(devices=h_devices, apply_device=h_apply_device, apply_lights=h_apply_lights,
                     apply_home=h_apply_home, apply_routine=h_apply_routine, apply_scene=h_apply_scene,
                     stop_scene=h_stop_scene, set_lock=h_set_lock, switch_locked=h_switch_locked,
                     publish=published.append, push=lambda title, text: pushed.append((title, text)))


def fresh():
    """Create the initial situation: no flows, devices in their initial state, at home, fixed time, no randomness."""
    flows_store.reset()
    eng.reset()
    hooks()
    for box in (calls, published, pushed, LOCKED, ACTIVE):
        box.clear()
    DEV.clear()
    DEV.update(base_devices())
    home_store.set_state("PRESENT")
    at(2026, 11, 16, 17, 58)
    fe.rand = lambda: 0.0


def mk(name, nodes, edges, enabled=True):
    """Create a flow; returns its id."""
    nodes, edges = flows_store.clean_graph(nodes, edges)
    edges, _ = fc.normalize(nodes, edges)
    f = flows_store.create(name, nodes, edges, enabled=enabled)
    eng.reload()
    return f["id"]


async def settle():
    """Give the engine time to finish what was triggered."""
    for _ in range(40):
        await asyncio.sleep(0)


async def run_for(seconds, step=3.0):
    """Advance the test clock and run a round every ~3 s like the real loop."""
    done = 0.0
    while done < seconds:
        d = min(step, seconds - done)
        NOW[0] += d
        done += d
        await eng.tick()
        await settle()


async def play(fid, nid="p", port=None):
    """Trigger a node (like a real event) and wait for the run."""
    ctx = await eng.fire_node(fid, nid, port, manual=False)
    await settle()
    return ctx


def took():
    """The actions recorded since the last call – and clear the list."""
    out = list(calls)
    calls.clear()
    return out


ON, OFF = ("dev", L, {"on": True}), ("dev", L, {"on": False})
MOTION = {"role": "trigger", "target": {"devices": [M]}, "prop": "motion", "is": "yes"}
MOTION_IS = {**MOTION, "role": "condition"}
LAMP_ON = dict(do="switch", target={"devices": [L]}, to="on")
LAMP_OFF = dict(do="switch", target={"devices": [L]}, to="off")


def flur(hold_off=120, **extra):
    """The flow "motion in the hallway switches the lamp" with a run-on time."""
    return mk("Flur", [N("m", "device_state", MOTION, hold_off=hold_off, **extra), N("on", "set_device", **LAMP_ON),
                       N("off", "set_device", **LAMP_OFF)], [E("m", "out", "on", "in"), E("m", "end", "off", "in")])


async def engine_tests():
    # 1) Parity: catalogue <-> engine; templates and widgets in the editor; palette 13 Basic + 18 Pro
    """All checks of the engine (numbered, with one line of result each)."""
    assert fe.check_specs() == [], fe.check_specs()
    js = (ROOT / "backend/app/static/js/flow.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\bN\('([a-z_]+)'", js))
    assert used and used <= set(fc.SPECS), used - set(fc.SPECS)
    assert "flow_nodes.json" in js, "the editor does not load the shared specification"
    widgets = set(re.findall(r"^\s{4}([a-z]+)\(f, v", js, re.M))
    assert set(fc.CATALOG["widgets"]) <= widgets, set(fc.CATALOG["widgets"]) - widgets
    basic = [t for t, s in fc.SPECS.items() if not s.get("pro")]
    assert len(basic) == 15 and len(fc.PRO_TYPES) == 18 and fc.FORMAT == 3, (len(basic), len(fc.PRO_TYPES))
    assert not (ROOT / "backend/app/flow_migrate.py").exists(), "the legacy flow_migrate.py is still there"
    print(f"1 parity: {len(fc.SPECS)} nodes (15 Basic since the button trigger in 0.23, 18 Pro) with engine logic, all widgets in the editor, templates valid")

    # 2) Store: create, save (the webhook address stays), on/off, rename, duplicate, delete; outdated flows
    fresh()
    fid = mk("Test", [N("w", "webhook"), N("a", "notify", text="hi")], [E("w", "out", "a", "in"), E("w", "out", "w", "in")], enabled=False)
    f = flows_store.get(fid)
    tok = f["nodes"][0]["config"]["token"]
    assert len(tok) >= 20 and len(f["edges"]) == 1 and f["format"] == 3, f
    g = flows_store.save(fid, "Neu", [N("w", "webhook"), N("a", "notify", text="hi")], f["edges"])
    assert g["nodes"][0]["config"]["token"] == tok and g["name"] == "Neu", "the webhook address changed on saving"
    assert flows_store.set_enabled(fid, True)["enabled"] and flows_store.rename(fid, "  Flur   Licht ")["name"] == "Flur Licht"
    dup = flows_store.duplicate(fid)
    assert dup["enabled"] is False and dup["nodes"][0]["config"]["token"] != tok
    flows_store.delete(dup["id"])
    assert flows_store.get(dup["id"]) is None
    cfg = flows_store.clean_graph([N("x", "set_device", target={"devices": [L, 5], "rooms": ["r1"], "all": True, "x": {"y": 1}}, bad=float("nan"))], [])[0][0]["config"]
    assert cfg == {"target": {"devices": [L], "rooms": ["r1"], "all": True}}, cfg
    # outdated: another format or an unknown node -> stays stored, never runs
    data = flows_store._load()
    data["flows"].append({"id": "falt", "name": "Alt", "enabled": True, "nodes": [N("m", "motion", device=M)], "edges": [], "created": 1, "updated": 1})
    data["flows"].append({"id": "fneu", "name": "Zukunft", "enabled": True, "format": 3, "nodes": [N("z", "zukunftstyp")], "edges": [], "created": 2, "updated": 2})
    flows_store._save(data)
    eng.reload()
    assert eng.outdated == {"falt", "fneu"} and set(eng.flows) == {fid}, (eng.outdated, set(eng.flows))
    assert flows_store.get("falt")["nodes"][0]["type"] == "motion", "an outdated flow was changed"
    print("2 store: format mark, stable webhook address, target selection as an object; outdated flows stay and do not run")

    # 3) The hallway case. a) State trigger with "ends only after": the light stays on as long as there is motion
    fresh()
    fid = flur(120)
    await eng.tick()                        # first look: remember the state, trigger nothing
    assert not calls
    DEV[M]["motion"] = True
    await run_for(3)
    assert took() == [ON], "motion did not switch the light on"
    await run_for(1800)                     # stand still for half an hour
    assert not calls, f"the light went off despite continuing motion: {calls}"
    assert eng.live(fid)["m"] == {"on": True}
    DEV[M]["motion"] = False
    await run_for(117)
    assert not calls and "until" in eng.live(fid)["m"] and eng.running(fid)
    DEV[M]["motion"] = True                 # motion again shortly before the time is up: the wait is done with
    await run_for(6)
    assert not calls and "until" not in eng.live(fid)["m"]
    DEV[M]["motion"] = False
    await run_for(117)
    assert not calls
    await run_for(6)
    assert took() == [OFF], "the light did not go off after 2 minutes without motion"
    DEV[M]["motion"] = True                 # and it starts over again
    await run_for(3)
    assert took() == [ON], "the flow was not triggered again after switching off"
    # b) "Solange" without a time limit: formerly it stopped after 10 rounds / 20 seconds
    fresh()
    fid = mk("Solange", [N("m", "device_state", MOTION), N("lp", "loop", mode="while", gap=5), N("c", "device_state", MOTION_IS),
                         N("on", "set_device", **LAMP_ON), N("off", "set_device", **LAMP_OFF)],
             [E("m", "out", "lp", "in"), E("c", "val", "lp", "c1"), E("lp", "loop", "on", "in"), E("lp", "out", "off", "in")])
    await eng.tick()
    DEV[M]["motion"] = True
    await run_for(600)
    rounds = calls.count(ON)
    assert rounds >= 60 and OFF not in calls, (rounds, OFF in calls)
    assert eng.live(fid)["lp"]["round"] == rounds and eng.running(fid)
    took()
    DEV[M]["motion"] = False
    await run_for(9)
    assert took() == [OFF] and not eng.running(fid), "'Solange' did not end with 'danach'"
    # c) Restart in the middle: memory and wait markers survive, "ends" comes anyway
    fresh()
    fid = flur(120)
    await eng.tick()
    DEV[M]["motion"] = True
    await run_for(3)
    assert took() == [ON]
    eng.flush()
    eng.reset()                             # ComfyOS startet neu …
    hooks()
    DEV[M]["motion"] = False                # … and meanwhile nobody is in the hallway any more
    eng.reload()
    eng.restore_waits()
    await run_for(117)
    assert not calls
    await run_for(6)
    assert took() == [OFF], "after a restart the light stayed on forever"
    # d) unknown is not "over": if the detector fails, nothing changes
    fresh()
    fid = flur(0)
    await eng.tick()
    DEV[M]["motion"] = True
    await run_for(3)
    DEV[M]["motion"] = None
    await run_for(30)
    eng.hooks["devices"] = None
    await run_for(30)
    hooks()
    assert took() == [ON], "a failed detector switched"
    print("3 hallway: the light stays on as long as there is motion (30 min), goes off after the wait, triggers again;"
          f"'Solange' runs {rounds} rounds without a time limit; restart and outage change nothing")

    # 4) Exactly once: Hue event and query round see the same change; an outdated response overwrites no event
    fresh()
    fid = flur(0)
    await eng.tick()
    DEV[M]["motion"] = True
    await eng.push_device(M, {"motion": True})
    await settle()
    await eng.tick()
    await settle()
    assert took() == [ON], "change triggered twice or not at all"
    gate = asyncio.Event()

    async def slow_devices():
        snap = [dict(d) for d in DEV.values()]      # state before the event
        await gate.wait()
        return snap
    eng.hooks["devices"] = slow_devices
    t = asyncio.ensure_future(eng.tick())
    await settle()
    DEV[M]["motion"] = False
    await eng.push_device(M, {"motion": False})     # arrives while the query is still running
    await settle()
    assert took() == [OFF]
    gate.set()
    await t
    await settle()
    assert not calls, f"an outdated response triggered again: {calls}"
    eng.hooks["devices"] = h_devices
    # triggers during a running pass wait instead of getting lost
    fresh()
    fid = mk("Stau", [N("p", "play"), N("a", "set_device", **LAMP_ON)], [E("p", "out", "a", "in")])
    hold = asyncio.Event()
    real = h_apply_device

    async def slow_apply(d, s):
        await hold.wait()
        await real(d, s)
    eng.hooks["apply_device"] = slow_apply
    tasks = [asyncio.ensure_future(eng.fire_node(fid, "p", manual=False)) for _ in range(5)]
    await settle()
    assert not calls and eng.queued[fid] == 4
    hold.set()
    await asyncio.gather(*tasks)
    assert len(took()) == 5 and eng.info(fid)["runs"] == 5
    print("4 exactly once: event + round = one trigger; an older response overwrites no event; five triggers wait one after the other")

    # 5) Point in time: time of day, weekdays, position of the sun, catching up after a hanging round; period as a condition
    fresh()
    fid = mk("Zeit", [N("t", "time", at="18:00", days=[0, 1, 2, 3, 4]), N("a", "set_device", **LAMP_ON)], [E("t", "out", "a", "in")])
    await eng.tick()
    await run_for(60)
    assert not calls
    await run_for(90)                       # 18:00 ist angebrochen
    assert took() == [ON]
    await run_for(120)
    assert not calls, "point in time triggered twice"
    at(2026, 11, 17, 17, 59, 50)
    await eng.tick()
    NOW[0] += 240                           # the round hung for four minutes: 18:00 is caught up
    await eng.tick()
    await settle()
    assert took() == [ON], "point in time skipped after a hanging round"
    at(2026, 11, 21, 17, 59, 50)            # Saturday: not chosen
    await eng.tick()
    await run_for(60)
    assert not calls
    sun = fe.sun_times(52.5, 13.4, datetime(2026, 6, 21).date(), 120)
    assert sun and 280 < sun[0] < 300 and 1280 < sun[1] < 1300, sun
    assert fe.moment("sunset+15", (400, 1000)) == 1015 and fe.moment("sunrise-30", (400, 1000)) == 370 and fe.moment("07:30", None) == 450
    assert fe.moment_text("sunset+15") == "Sonnenuntergang +15 Min." and fe.moment("unsinn", None) is None
    fresh()
    eng._sun = (datetime.fromtimestamp(NOW[0]).date(), (420, 1020))     # 7:00 / 17:00
    fid = mk("Sonne", [N("t", "time", at="sunset+60"), N("a", "set_device", **LAMP_ON),
                       N("p", "play"), N("w", "when"), N("c", "time", {"from": "22:00", "to": "06:00"}, role="condition", days=[0]),
                       N("b", "set_device", **LAMP_OFF)],
             [E("t", "out", "a", "in"), E("p", "out", "w", "in"), E("c", "val", "w", "c1"), E("w", "yes", "b", "in")])
    await eng.tick()
    await run_for(180)                      # 18:00 = Sonnenuntergang + 60 min
    assert took() == [ON], "sunset with an offset did not trigger"
    await play(fid)
    assert not calls                        # 18:01 is not between 22 and 6 o'clock
    at(2026, 11, 16, 23, 30)
    await play(fid)
    assert took() == [OFF]
    at(2026, 11, 17, 5, 0)                  # Tuesday morning: the period fits, the weekday does not
    await play(fid)
    assert not calls
    print("5 point in time: time of day, weekdays, position of the sun ± offset, catching up; period across midnight as a condition")

    # 6) Presence: begins/ends as a trigger, as a condition; "Anwesenheit setzen" triggers other flows
    fresh()
    f1 = mk("Weg", [N("a", "presence", state="AWAY"), N("off", "set_device", do="switch", target={"all": True}, to="off"),
                    N("on", "set_device", **LAMP_ON)], [E("a", "out", "off", "in"), E("a", "end", "on", "in")])
    f2 = mk("Setzen", [N("p", "play"), N("w", "when"), N("c", "presence", role="condition", state="PRESENT"),
                       N("s", "set_presence", state="AWAY"), N("s2", "set_presence", state="PRESENT")],
            [E("p", "out", "w", "in"), E("c", "val", "w", "c1"), E("w", "yes", "s", "in"), E("w", "no", "s2", "in")])
    await eng.tick()
    await play(f2)
    assert took() == [("home", "AWAY")]
    await run_for(3)
    assert took() == [("lights", False, None)], "away did not switch all lights off"
    await play(f2)                          # now "at home" does not hold -> otherwise
    await run_for(3)
    assert took() == [("home", "PRESENT"), ON]
    print("6 presence: trigger begins/ends, condition, setting triggers other flows; 'Alle Lampen' switches through apply_lights")

    # 7) Scene: activated/ended per room, condition "active"
    fresh()
    fid = mk("Szene", [N("s", "scene", scene="abend", rooms=["r1"]), N("a", "set_device", **LAMP_ON), N("b", "set_device", **LAMP_OFF),
                       N("p", "play"), N("w", "when"), N("c", "scene", role="condition", scene="abend"), N("r", "set_routine", routine="nacht")],
             [E("s", "out", "a", "in"), E("s", "end", "b", "in"), E("p", "out", "w", "in"), E("c", "val", "w", "c1"), E("w", "yes", "r", "in")])
    await eng.tick()
    ACTIVE["room:r3"] = "abend"             # other room: nothing
    await run_for(3)
    assert not calls
    ACTIVE["room:r1"] = "abend"
    await run_for(3)
    assert took() == [ON]
    await play(fid)
    assert took() == [("routine", "nacht")]
    del ACTIVE["room:r1"]
    await run_for(3)
    assert took() == [OFF]
    print("7 scene: trigger per room (activated/ended), condition 'active'")

    # 8) Device signal, online/offline (per device), no change since
    fresh()
    fid = mk("Signal", [N("b", "device_signal", signal="battery", below=25), N("n", "notify", text="{trigger}: {device} ({value})"),
                        N("o", "reachable", state="offline", target={"devices": [C, T]}), N("n2", "notify", text="{device} {trigger}"),
                        N("p", "play"), N("w", "when", match="any"), N("c1", "device_signal", role="condition", signal="battery", below=25),
                        N("c2", "stale", target={"devices": [S]}, **{"for": 3600}), N("a", "set_device", **LAMP_ON)],
             [E("b", "out", "n", "in"), E("o", "out", "n2", "in"), E("p", "out", "w", "in"), E("c1", "val", "w", "c1"),
              E("c2", "val", "w", "c2"), E("w", "yes", "a", "in")])
    await eng.tick()
    assert not published or all(m.get("type") != "toast" for m in published), "the first look triggered a signal"
    await play(fid)
    assert not calls
    DEV[C]["battery"] = 20
    DEV[T]["online"] = False
    DEV[M]["online"] = False                # not chosen
    await run_for(3)
    toasts = [m["text"] for m in published if m.get("type") == "toast"]
    assert toasts == ["Batterie schwach: Bad Fenster (20 %)", "Bad Heizung offline"], toasts
    await run_for(30)
    assert len([m for m in published if m.get("type") == "toast"]) == 2, "signal reported several times per device"
    await play(fid)
    assert took() == [ON]                   # battery condition met
    DEV[C]["battery"] = 90
    await run_for(3)
    await play(fid)
    assert not calls
    await run_for(3700)                     # the sensor has reported nothing new for over an hour
    await play(fid)
    assert took() == [ON], "'no change since' not recognised"
    print("8 device signal and online/offline trigger exactly once per device; conditions for them; no change since")

    # 9) Device state: number with a switching threshold, all/at least one, rooms and groups as the target
    fresh()
    fid = mk("Temp", [N("t", "device_state", role="trigger", target={"devices": [S]}, prop="temperature", op="gt", value=21, band=1),
                      N("a", "set_device", **LAMP_ON), N("b", "set_device", **LAMP_OFF)], [E("t", "out", "a", "in"), E("t", "end", "b", "in")])
    await eng.tick()
    for temp, want in ((21.5, [ON]), (20.6, []), (21.2, []), (19.9, [OFF]), (20.8, []), (21.1, [ON])):
        DEV[S]["temperature"] = temp
        await run_for(3)
        assert took() == want, (temp, want)
    assert fc.is_pro(flows_store.get(fid)["nodes"][0]), "the switching threshold does not count as a Pro setting"
    fresh()
    eng._groups = (NOW[0], {"g1": [M, M2]})
    fid = mk("Alle", [N("any", "device_state", role="trigger", target={"rooms": ["r1"]}, prop="motion", match="any"), N("a", "set_device", **LAMP_ON),
                      N("all", "device_state", role="trigger", target={"groups": ["g1"]}, prop="motion", match="all"), N("b", "set_device", **LAMP_OFF)],
             [E("any", "out", "a", "in"), E("all", "out", "b", "in")])
    await eng.tick()
    DEV[M2]["motion"] = True
    await run_for(3)
    assert took() == [ON]
    DEV[M]["motion"] = True
    await run_for(3)
    assert took() == [OFF], "'all' in the group not recognised"
    assert eng.last_runs[fid]["trigger"] == "Bewegung erkannt"
    print("9 device state: the switching threshold (Pro) holds the state, all/at least one, room and device group as the target")

    # 10) If/then: all, at least one, exactly one; n+1 ports; not; without a condition = then
    fresh()
    nodes = [N("p", "play"), N("w", "when"), N("a", "set_device", **LAMP_ON), N("b", "set_device", **LAMP_OFF),
             N("c1", "device_state", MOTION_IS), N("c2", "device_state", role="condition", target={"devices": [C]}, prop="open"),
             N("c3", "presence", role="condition", state="PRESENT"), N("n", "not")]
    edges = [E("p", "out", "w", "in"), E("w", "yes", "a", "in"), E("w", "no", "b", "in"),
             E("c1", "val", "w", "c1"), E("c2", "val", "w", "c2"), E("c3", "val", "n", "c"), E("n", "val", "w", "c3")]
    for match, table in (("all", {(0, 0): OFF, (1, 0): OFF, (1, 1): OFF}), ("any", {(0, 0): OFF, (1, 0): ON, (1, 1): ON}),
                         ("one", {(0, 0): OFF, (1, 0): ON, (1, 1): OFF})):
        fresh()
        nodes[1] = N("w", "when", match=match)
        fid = mk(match, nodes, edges)
        for (m, o), want in table.items():
            DEV[M]["motion"], DEV[C]["open"] = bool(m), bool(o)
            await eng.snapshot()
            await play(fid)
            assert took() == [want], (match, m, o)
    home_store.set_state("AWAY")            # "not at home" becomes true -> with "all" together with the other two: then
    fresh()
    home_store.set_state("AWAY")
    nodes[1] = N("w", "when", match="all")
    fid = mk("alle", nodes, edges)
    DEV[M]["motion"], DEV[C]["open"] = True, True
    await eng.snapshot()
    await play(fid)
    assert took() == [ON]
    conds = [p for p in eng.last_runs[fid]["path"] if p[0] == "c"]
    assert [p[2] for p in conds] == ["c1", "c2", "c3", "n"] or len(conds) == 4, conds
    fresh()
    fid = mk("leer", [N("p", "play"), N("w", "when"), N("a", "set_device", **LAMP_ON)], [E("p", "out", "w", "in"), E("w", "yes", "a", "in")])
    await play(fid)
    assert took() == [ON]
    print("10 if/then: all / at least one / exactly one through n+1 ports, not inverts, without a condition it continues at 'then'")

    # 11) Timer: restart, extend, ignore, individually, cancel – and survives a restart
    async def timer(mode):
        fresh()
        fid = mk("Timer", [N("p", "play"), N("x", "play", label="Stopp"), N("t", "timer", wait=300, mode=mode), N("off", "set_device", **LAMP_OFF)],
                 [E("p", "out", "t", "in"), E("x", "out", "t", "cancel"), E("t", "out", "off", "in")])
        await play(fid)
        await run_for(200)
        await play(fid)                     # zweites Signal nach 200 s
        return fid
    fid = await timer("restart")
    await run_for(290)
    assert not calls
    await run_for(15)
    assert took() == [OFF]
    fid = await timer("extend")             # Rest 100 s + 300 s
    await run_for(390)
    assert not calls
    await run_for(15)
    assert took() == [OFF]
    fid = await timer("ignore")
    await run_for(105)
    assert took() == [OFF]
    await run_for(400)
    assert not calls
    fid = await timer("each")
    await run_for(105)
    assert took() == [OFF]
    await run_for(200)
    assert took() == [OFF]
    fid = await timer("restart")
    await play(fid, "x")
    await run_for(400)
    assert not calls and not eng.running(fid), "cancelling did not stop the timer"
    fid = await timer("ignore")
    eng.flush()
    eng.reset()
    hooks()
    eng.reload()
    assert eng.restore_waits() == 1 and eng.running(fid)
    await run_for(90)
    assert not calls
    await run_for(15)
    assert took() == [OFF], "timer lost after a restart"
    # moving cancels nothing, rebuilding does
    fid = await timer("ignore")
    f = flows_store.get(fid)
    moved = [{**n, "x": n["x"] + 40} for n in f["nodes"]]
    flows_store.save(fid, f["name"], moved, f["edges"])
    eng.reload()
    assert eng.running(fid), "moving a node cancelled the running timer"
    changed = [{**n, "config": {**n["config"], "wait": 60}} if n["id"] == "t" else n for n in moved]
    flows_store.save(fid, f["name"], changed, f["edges"])
    eng.reload()
    assert not eng.running(fid)
    await run_for(400)
    assert not calls
    print("11 timer: restart / extend / ignore / individually / cancel; survives restart and moving, not the rebuild")

    # 12) Drosseln/Entprellen, Umschalter, Zufall, feste Anzahl, Haltezeit
    fresh()
    fid = mk("Rate", [N("p", "play"), N("r", "rate", mode="throttle", wait=60), N("a", "set_device", **LAMP_ON),
                      N("q", "play", label="q"), N("d", "rate", mode="debounce", wait=10), N("b", "set_device", **LAMP_OFF)],
             [E("p", "out", "r", "in"), E("r", "out", "a", "in"), E("q", "out", "d", "in"), E("d", "out", "b", "in")])
    await play(fid)
    await play(fid)
    assert took() == [ON]
    await run_for(63)
    await play(fid)
    assert took() == [ON]
    for _ in range(3):
        await play(fid, "q")
        await run_for(6)
    assert not calls
    await run_for(6)
    assert took() == [OFF], "debouncing did not pass on exactly once"
    fresh()
    fid = mk("Um", [N("p", "play"), N("t", "toggle"), N("a", "set_device", **LAMP_ON), N("b", "set_device", **LAMP_OFF)],
             [E("p", "out", "t", "in"), E("t", "a", "a", "in"), E("t", "b", "b", "in")])
    seq = []
    for _ in range(3):
        await play(fid)
        seq += took()
    assert seq == [ON, OFF, ON]
    fresh()
    fid = mk("Zufall", [N("p", "play"), N("r", "random", mode="pick", n=3), N("a", "set_device", **LAMP_ON), N("b", "set_device", **LAMP_OFF),
                        N("q", "play", label="q"), N("c", "random", mode="chance", p=50), N("d", "set_routine", routine="x"),
                        N("z", "play", label="z"), N("v", "random", mode="delay", min=10, max=20), N("e", "set_routine", routine="y")],
             [E("p", "out", "r", "in"), E("r", "o1", "a", "in"), E("r", "o3", "b", "in"), E("q", "out", "c", "in"), E("c", "o1", "d", "in"),
              E("z", "out", "v", "in"), E("v", "o1", "e", "in")])
    assert [p["id"] for p in fc.out_ports(flows_store.get(fid)["nodes"][1])] == ["o1", "o2", "o3"]
    fe.rand = lambda: 0.0
    await play(fid)
    assert took() == [ON]
    fe.rand = lambda: 0.99
    await play(fid)
    assert took() == [OFF]                  # only connected paths count
    await play(fid, "q")
    assert not calls
    fe.rand = lambda: 0.2
    await play(fid, "q")
    assert took() == [("routine", "x")]
    await play(fid, "z")                    # 10 + 0,2 · 10 = 12 s
    await run_for(9)
    assert not calls
    await run_for(6)
    assert took() == [("routine", "y")]
    fresh()
    fid = mk("Mal", [N("p", "play"), N("l", "loop", mode="times", times=3, gap=0), N("n", "notify", text="{index}/{count}"), N("a", "set_device", **LAMP_ON)],
             [E("p", "out", "l", "in"), E("l", "loop", "n", "in"), E("l", "out", "a", "in")])
    await play(fid)
    await run_for(3)
    assert [m["text"] for m in published if m.get("type") == "toast"] == ["1/3", "2/3", "3/3"] and took() == [ON]
    fid = mk("Ohne", [N("p", "play"), N("l", "loop", mode="while")], [E("p", "out", "l", "in")])
    await play(fid)
    assert "keine Bedingung" in eng.info(fid).get("last_error", ""), eng.info(fid)
    fresh()
    fid = mk("Halten", [N("h", "hold", hold_on=60), N("c", "device_state", role="condition", target={"devices": [C]}, prop="open"),
                        N("a", "set_device", **LAMP_ON), N("b", "set_device", **LAMP_OFF)],
             [E("c", "val", "h", "c1"), E("h", "out", "a", "in"), E("h", "end", "b", "in")])
    await eng.tick()
    DEV[C]["open"] = True
    await run_for(30)
    DEV[C]["open"] = False                  # zu kurz offen
    await run_for(6)
    DEV[C]["open"] = True
    await run_for(57)
    assert not calls
    await run_for(6)
    assert took() == [ON]
    DEV[C]["open"] = False
    await run_for(3)
    assert took() == [OFF]
    print("12 throttle/debounce, toggle A/B, random (path, probability, delay), fixed number with {index}/{count}, hold time")

    # 13) Actions: control a device (all kinds), rooms only switch lamps, locked device, scene, locking, notification
    fresh()
    fid = mk("Steuern", [N("p", "play"),
                         N("a", "set_device", do="switch", target={"rooms": ["r1"], "devices": [P]}, to="off"),
                         N("b", "set_device", do="switch", target={"rooms": ["r1", "r3"]}, to="toggle"),
                         N("c", "set_device", do="light", target={"devices": [L, L2]}, light_mode="color", color="#00ff00", brightness=50),
                         N("d", "set_device", do="dim", target={"devices": [L2]}, step=-20),
                         N("e", "set_device", do="temp", target={"rooms": ["r2"]}, temp_mode="delta", temp=-2),
                         N("f", "set_device", do="temp", target={"devices": [T, L]}, temp=17.5)],
             [E("p", "out", "a", "in"), E("a", "out", "b", "in"), E("b", "out", "c", "in"), E("c", "out", "d", "in"),
              E("d", "out", "e", "in"), E("e", "out", "f", "in")])
    await eng.snapshot()
    await play(fid)
    got = took()
    assert got[0] == ("lights", False, ["r1"]) and got[1] == ("dev", P, {"on": False}), got[:2]       # room = lamps only, the socket individually
    assert got[2:4] == [("dev", L, {"on": True}), ("dev", L2, {"on": True})], got[2:4]
    assert got[4] == ("dev", L, {"on": True}) and got[5] == ("dev", L2, {"on": True, "brightness": 50.0, "color": "#00ff00"}), got[4:6]
    assert got[6] == ("dev", L2, {"brightness": 30.0}), got[6]
    assert got[7:] == [("dev", T, {"delta": -2.0}), ("dev", T, {"target": 17.5})], got[7:]
    assert P not in [c[1] for c in got[2:]], "the socket was switched along through the room"
    LOCKED.add(L)
    fid = mk("Gesperrt", [N("p", "play"), N("a", "set_device", do="switch", target={"devices": [L, P]}, to="on"), N("n", "set_routine", routine="x")],
             [E("p", "out", "a", "in"), E("a", "out", "n", "in")])
    await play(fid)
    assert took() == [("dev", P, {"on": True}), ("routine", "x")] and "last_error" not in eng.info(fid), "a locked device aborted the flow"
    assert any("Flur Lampe ist gesperrt" in e["message"] for e in events.recent(10))
    fid = mk("Leer", [N("p", "play"), N("a", "set_device", do="temp", target={"devices": [L]}, temp=20)], [E("p", "out", "a", "in")])
    await play(fid)
    assert "keine passenden Geräte" in eng.info(fid)["last_error"]
    fresh()
    fid = mk("Szenen", [N("p", "play"), N("a", "set_scene", do="start", scene="abend", target={"rooms": ["r1"], "groups": ["g1"], "devices": [L2]}),
                        N("b", "set_scene", do="start", scene="abend"), N("c", "set_scene", do="stop", target={"rooms": ["r1"]}, restore=False),
                        N("d", "set_scene", do="stop"),
                        N("k", "lock", do="lock", target={"devices": [P]}, switch="off"), N("u", "lock", do="unlock", target={"devices": [P]}),
                        N("n", "notify", text="Fertig {flow}", toast=False, push=True)],
             [E("p", "out", "a", "in"), E("a", "out", "b", "in"), E("b", "out", "c", "in"), E("c", "out", "d", "in"),
              E("d", "out", "k", "in"), E("k", "out", "u", "in"), E("u", "out", "n", "in")])
    await eng.snapshot()
    await play(fid)
    assert took() == [("scene", "abend", ["r1"], None, None), ("scene", "abend", None, None, "g1"), ("scene", "abend", None, [L2], None),
                      ("scene", "abend", ["r1", "r3"], None, None), ("stop", ["room:r1"], False), ("stop", None, True),
                      ("force", P, {"on": False}), ("lock", P, True, fid), ("lock", P, False, fid)]
    assert pushed == [("Szenen", "Fertig Szenen")] and not [m for m in published if m.get("type") == "toast"]
    assert events.recent(1)[0]["message"] == "Fertig Szenen"
    print("13 actions: switch/light/dim/temperature, room = lamps only, a locked device is skipped, scene, locking, push")

    # 14) Variables: yes/no, number, text, counting window; the trigger "Variable" reacts at once
    fresh()
    fid = mk("Var", [N("p", "play"), N("s", "set_var", name="Zähler", op="add", value=1, window=60),
                     N("v", "variable", name="Zähler", cmp="gt", value=2), N("a", "set_device", **LAMP_ON), N("b", "set_device", **LAMP_OFF),
                     N("q", "play", label="q"), N("t", "set_var", name="Modus", op="text", text="Gast {trigger}"),
                     N("w", "when"), N("c", "variable", role="condition", name="Modus", cmp="contains", text="gast"), N("r", "set_routine", routine="g"),
                     N("u", "set_var", name="Urlaub", op="toggle")],
             [E("p", "out", "s", "in"), E("v", "out", "a", "in"), E("v", "end", "b", "in"), E("q", "out", "t", "in"), E("t", "out", "w", "in"),
              E("c", "val", "w", "c1"), E("w", "yes", "r", "in"), E("r", "out", "u", "in")])
    await eng.tick()
    for _ in range(3):
        await play(fid)
        NOW[0] += 5
    await settle()
    assert took() == [ON] and eng.var("Zähler") == 3, "three times in one minute not recognised"
    await run_for(63)                       # the steps drop out of the window
    assert eng.var("Zähler") == 0 and took() == [OFF]
    await play(fid, "q")
    assert took() == [("routine", "g")] and eng.var("Modus") == "Gast q" and eng.var("Urlaub") is True
    assert eng.set_vars({"Urlaub": None, "Zahl": 4, "Text": "x" * 500})["Text"] == "x" * 200 and "Urlaub" not in eng.vars
    print("14 variables: counting window (three times in 60 s), text with a placeholder, toggle; the trigger reacts without waiting")

    # 15) Errors: stay visible at the flow, trigger "Flow-Fehler" in other flows (optionally only for a certain one)
    fresh()
    bad = mk("Kaputt", [N("p", "play"), N("r", "set_routine")], [E("p", "out", "r", "in")])
    other = mk("Anderer", [N("p", "play"), N("r", "set_routine")], [E("p", "out", "r", "in")])
    watch = mk("Wache", [N("e", "flow_error"), N("n", "notify", text="{trigger}: {value}"),
                         N("e2", "flow_error", flow=other), N("a", "set_device", **LAMP_ON)],
               [E("e", "out", "n", "in"), E("e2", "out", "a", "in")])
    await play(bad)
    assert eng.info(bad)["last_error"] == "Heizroutine: keine Routine gewählt"
    assert [m["text"] for m in published if m.get("type") == "toast"] == ["Kaputt: Heizroutine: keine Routine gewählt"] and not calls
    await play(other)
    assert took() == [ON]
    print("15 errors: visible at the flow, 'Flow-Fehler' starts other flows – optionally only for a certain one")

    # 16) Visible: steps, conditions, waiting and end go out as messages; the last pass can still be fetched
    fresh()
    fid = mk("Live", [N("p", "play"), N("w", "when"), N("c", "device_state", MOTION_IS), N("t", "timer", wait=30), N("a", "set_device", **LAMP_ON)],
             [E("p", "out", "w", "in"), E("c", "val", "w", "c1"), E("w", "no", "t", "in"), E("t", "out", "a", "in")])
    await eng.snapshot()
    published.clear()
    await play(fid)
    evs = [(m["ev"], m.get("node")) for m in published if m.get("type") == "flow"]
    assert evs == [("run", "p"), ("step", "w"), ("cond", "c"), ("step", "t"), ("wait", "t"), ("end", None)], evs
    assert next(m for m in published if m.get("ev") == "cond")["val"] is False
    last = eng.last_runs[fid]
    assert [p[0] for p in last["path"]] == ["s", "c", "s"] and last["node"] == "p" and last["error"] is None
    assert list(eng.live(fid)) == ["t"] and eng.live(fid)["t"]["until"] == NOW[0] + 30
    published.clear()
    await run_for(33)
    evs = [(m["ev"], m.get("node")) for m in published if m.get("type") == "flow"]
    assert evs == [("idle", "t"), ("run", "t"), ("step", "a"), ("end", None)] and took() == [ON], evs
    assert eng.info(fid)["runs"] == 1, "the continuation after the timer counts as a new trigger"
    print("16 live: run/step/cond/wait/idle/end in the right order, path of the last pass, running wait time")

    # 17) Start: at the start of ComfyOS or when the flow is switched on, with an offset; flow on/off; inspector; HTTP block
    fresh()
    fid = mk("Start", [N("s", "startup", offset=30), N("a", "set_device", **LAMP_ON), N("i", "inspect")], [E("s", "out", "a", "in"), E("a", "out", "i", "in")], enabled=False)
    ctl = mk("Schalter", [N("p", "play"), N("f", "set_flow", flow=fid, to="on"), N("q", "play", label="q"), N("g", "set_flow", flow=fid, to="off")],
             [E("p", "out", "f", "in"), E("q", "out", "g", "in")])
    await play(ctl)
    assert flows_store.get(fid)["enabled"] and eng.running(fid)
    await run_for(27)
    assert not calls
    await run_for(6)
    assert took() == [ON] and eng.state[f"{fid}/i"]["trigger"] == "Aktivierung"
    await play(ctl)                          # already on: no second start
    assert not eng.running(fid)
    await play(ctl, "q")
    assert not flows_store.get(fid)["enabled"] and fid not in [f.id for f in eng.enabled()]
    for url, local in (("http://192.168.1.5/x", False), ("ftp://example.com", True), ("http://localhost:8123/api", False)):
        try:
            fe.check_url(url, local)
            raise AssertionError(f"{url} not rejected")
        except fe.FlowError:
            pass
    assert fe.check_url("http://192.168.1.5/x", True)
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.content.decode()))
        return httpx.Response(200)
    fe.http_client = lambda allow_local=False: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fid = mk("Http", [N("p", "play", label="Tür auf"), N("h", "http", url="https://ntfy.example.com/x?t={trigger}", body="{flow}: {trigger}")], [E("p", "out", "h", "in")])
    await play(fid)
    assert seen == [("POST", "https://ntfy.example.com/x?t=T%C3%BCr%20auf", "Http: Tür auf")], seen
    print("17 start with an offset on switching on, flow on/off, inspector, HTTP: home network blocked, placeholders in address and body")


asyncio.run(engine_tests())


# ------------------------------------------------------------------ API gegen Mock-Home-Base (gemeinsamer Aktuator)
class MockHB(QiviconConnector):
    """Home Base for the checks through the real routes: devices from DEV, commands are recorded in sent."""
    sent = []

    def _needs_login(self):
        """The session is always valid."""
        return False

    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}

    async def list_devices(self, max_age=None):
        """The devices from DEV – without the Hue lamps."""
        return [dict(d) for d in DEV.values() if not d["id"].startswith("hue:")]

    async def list_rooms(self):
        """No rooms."""
        return []

    async def set_device_state(self, device_id, state):
        """Record a command and confirm it like the Home Base."""
        self.sent.append((device_id, state))
        return {"id": device_id, "name": DEV[device_id]["name"], "command": "ON" if state.get("on") else "OFF"}


from fastapi.testclient import TestClient  # noqa: E402

fresh()
eng.hooks.clear()
fe._sleep = asyncio.sleep           # real time from here: the engine runs in the app
fe.clock = time.time
core.publish = lambda m: published.append(m)
with TestClient(main.app) as client:
    install_homebase(MockHB("https://qivicon", "", "x"))
    assert client.get("/api/flows").status_code == 401, "flows readable without login"
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def ok(r, code=200):
        assert r.status_code == code, (r.status_code, r.text)
        return r.json()

    # 18) Creating (an unfitting line drops out, an unknown node is rejected), play switches through the shared actuator
    ok(client.post("/api/flows", json={"name": "x", "nodes": [N("z", "zukunftstyp")]}), 400)
    f = ok(client.post("/api/flows", json={"name": "Gute Nacht", "nodes": [N("p", "play", label="Gute Nacht"),
           N("s", "set_device", do="switch", target={"devices": [P]}, to="off"), N("c", "time", role="condition"), N("w", "when")],
           "edges": [E("p", "out", "s", "in"), E("c", "val", "s", "in"), E("c", "out", "w", "in"), E("c", "val", "w", "c"), E("c", "val", "w", "c7")]}))
    assert sorted((e["from"], e["toPort"]) for e in f["edges"]) == [("c", "c1"), ("c", "c2"), ("p", "in")] and f["enabled"] is False, f["edges"]
    ok(client.post(f"/api/flows/{f['id']}/play/p"), 409)
    ok(client.post(f"/api/flows/{f['id']}/play/s"), 404)
    ok(client.put(f"/api/flows/{f['id']}/enabled", json={"enabled": True}))
    r = ok(client.post(f"/api/flows/{f['id']}/play/p"))
    assert MockHB.sent == [(P, {"on": False})] and r["trace"] == ["1 Gerät aus"], (MockHB.sent, r)
    assert any("Aquarium: OFF (Flow)" == e["message"] for e in events.recent(20)), "switching by the flow was not logged"
    lst = ok(client.get("/api/flows"))
    assert lst["flows"][0]["info"]["runs"] == 1 and lst["problems"] == [] and lst["flows"][0]["outdated"] is False
    assert [m["ev"] for m in published if m.get("type") == "flow" and m["flow"] == f["id"]] == ["run", "step", "end"]
    print("18 API: unknown node 400, lines are checked and numbered, switched off -> 409, play switches (mock Home Base), event log '(Flow)'")

    # 19) Webhook without login only with a valid token; as a condition it has no address
    w = ok(client.post("/api/flows", json={"name": "Hook", "nodes": [N("w", "webhook"), N("s", "set_device", do="switch", target={"devices": [P]}, to="on"),
                                                                    N("c", "webhook", role="condition")], "edges": [E("w", "out", "s", "in")]}))
    tok = w["nodes"][0]["config"]["token"]
    anon = TestClient(main.app)
    assert anon.post(f"/api/flows/hook/{tok}").status_code == 404, "a switched-off webhook was triggered"
    ok(client.put(f"/api/flows/{w['id']}/enabled", json={"enabled": True}))
    assert anon.post("/api/flows/hook/falsch-falsch-falsch-falsch").status_code == 404
    assert anon.post(f"/api/flows/hook/{w['nodes'][2]['config']['token']}").status_code == 404, "a webhook condition triggered"
    assert anon.post(f"/api/flows/hook/{tok}", content="hallo").status_code == 200
    assert MockHB.sent[-1] == (P, {"on": True}), MockHB.sent
    assert anon.get(f"/api/flows/{w['id']}").status_code == 401
    print("19 webhook: a valid token starts without login, wrong/switched off/condition -> 404, the rest stays protected")

    # 20) Saving, test of a single trigger ("ends" too), test run, variables, duplicating, export/import, deleting
    g = ok(client.put(f"/api/flows/{f['id']}", json={"name": "Gute Nacht 2", "nodes": [N("p", "play"), N("m", "device_state", MOTION),
           N("s", "set_device", do="switch", target={"devices": [P]}, to="off"), N("s2", "set_device", do="switch", target={"devices": [P]}, to="on"),
           N("i", "inspect")], "edges": [E("p", "out", "s", "in"), E("m", "out", "s2", "in"), E("m", "end", "s", "in"), E("s", "out", "i", "in")]}))
    assert g["name"] == "Gute Nacht 2" and len(g["edges"]) == 4
    r = ok(client.post(f"/api/flows/{f['id']}/fire/m", json={"port": "end"}))
    assert r["trace"] == ["1 Gerät aus"] and MockHB.sent[-1] == (P, {"on": False}), r
    ok(client.post(f"/api/flows/{f['id']}/fire/m", json={"port": "gibtsnicht"}), 404)
    ok(client.post(f"/api/flows/{f['id']}/fire/s"), 404)
    r = ok(client.post(f"/api/flows/{f['id']}/run"))
    assert r["trace"] == ["1 Gerät aus", "1 Gerät an"], r
    full = ok(client.get(f"/api/flows/{f['id']}"))
    assert full["state"]["i"]["trigger"] == "Testlauf" and full["last"]["node"] == "m", full["state"]
    assert ok(client.put("/api/flows/vars", json={"Urlaub": True, "Zähler": 3, "Modus": "Gast"})) == {"Urlaub": True, "Zähler": 3.0, "Modus": "Gast"}
    assert ok(client.put("/api/flows/vars", json={"Zähler": None})) == {"Urlaub": True, "Modus": "Gast"}
    d = ok(client.post(f"/api/flows/{w['id']}/duplicate"))
    assert d["nodes"][0]["config"]["token"] != tok and d["enabled"] is False
    ok(client.delete(f"/api/flows/{d['id']}"))
    ok(client.get(f"/api/flows/{d['id']}"), 404)
    exp = ok(client.get("/api/flows/export"))
    assert "token" not in str(exp) and {x["name"] for x in exp["flows"]} == {"Gute Nacht 2", "Hook"}, "the export contains webhook addresses"
    doc = {"flows": [{"name": "Hook", "nodes": exp["flows"][1]["nodes"], "edges": [{"von": "w", "ausgang": "out", "nach": "s", "eingang": "in"}]},
                     {"name": "Kaputt", "nodes": [N("x", "motion")]}]}
    r = ok(client.post("/api/flows/import", json=doc))
    assert r["ok"] is False and r["flows"][0]["action"] == "update" and "Unbekannter Baustein" in r["flows"][1]["errors"][0], r
    ok(client.post("/api/flows/import?apply=true", json=doc), 400)
    ok(client.post("/api/flows/import?apply=true", json={"flows": doc["flows"][:1]}))
    assert ok(client.get(f"/api/flows/{w['id']}"))["nodes"][0]["config"]["token"] == tok, "the import changed the webhook address"
    info = ok(client.get("/api/flows/import"))
    assert len(info["nodes"]) == 33 and info["properties"] and next(x for x in info["devices"] if x["id"] == M)["props"] == ["motion", "temperature", "battery"], info["devices"][:1]
    print("20 saving, single trigger ('ends' too), test run, variables, duplicating, export without addresses, the import checks nodes")

    # 21) Outdated flows: visible with a hint, do not run, can only be deleted
    data = flows_store._load()
    data["flows"].append({"id": "falt", "name": "Flur Steuerung", "enabled": True, "nodes": [N("m", "motion", device=M)], "edges": [], "created": 1, "updated": 1})
    flows_store._save(data)
    eng.reload()
    row = next(x for x in ok(client.get("/api/flows"))["flows"] if x["id"] == "falt")
    assert row["outdated"] is True and row["nodes"] == [] and row["name"] == "Flur Steuerung", row
    for call, code in ((lambda: client.post("/api/flows/falt/run"), 409), (lambda: client.put("/api/flows/falt", json={"name": "x", "nodes": []}), 409),
                       (lambda: client.post("/api/flows/falt/duplicate"), 409), (lambda: client.put("/api/flows/falt/name", json={"name": "x"}), 409)):
        ok(call(), code)
    ok(client.put("/api/flows/falt/enabled", json={"enabled": False}))
    ok(client.put("/api/flows/falt/enabled", json={"enabled": True}), 409)
    assert "falt" not in eng.flows
    ok(client.delete("/api/flows/falt"))
    assert flows_store.get("falt") is None
    print("21 outdated: with a hint in the list, no start, no editing, deleting works")

    # 22) Groups and order (since 0.21.1): field only with a group, the order of the list is that of the store
    ids = [x["id"] for x in ok(client.get("/api/flows"))["flows"]]
    assert len(ids) >= 2, ids
    raw_before = flows_store.FLOWS_FILE.read_bytes()
    g = ok(client.put(f"/api/flows/{ids[0]}/group", json={"group": "  Licht   abends  "}))
    assert g["group"] == "Licht abends" and flows_store.get(ids[0])["group"] == "Licht abends"
    ok(client.put(f"/api/flows/{ids[0]}/group", json={"group": ""}))
    assert "group" not in flows_store.get(ids[0]) and flows_store.FLOWS_FILE.read_bytes() == raw_before, "without a group the file does not change"
    ok(client.put(f"/api/flows/{ids[0]}/group", json={"group": "x" * 80}))
    assert len(flows_store.get(ids[0])["group"]) == flows_store.MAX_GROUP
    copy_id = ok(client.post(f"/api/flows/{ids[0]}/duplicate"))["id"]
    assert flows_store.get(copy_id)["group"] == flows_store.get(ids[0])["group"], "the copy stays in the group"
    ok(client.delete(f"/api/flows/{copy_id}"))
    ok(client.put("/api/flows/gibtsnicht/group", json={"group": "x"}), 404)
    turned = ok(client.put("/api/flows/order", json={"ids": [ids[-1], "gibtsnicht", ids[0], ids[-1]]}))["ids"]
    assert turned[:2] == [ids[-1], ids[0]] and sorted(turned) == sorted(ids), turned
    assert [x["id"] for x in ok(client.get("/api/flows"))["flows"]] == turned
    ok(client.put("/api/flows/order", json={"ids": ["x"] * 500}), 422)
    print("22 groups and order: group set, cleaned, limited and removed again; the copy stays in the group;"
          "order saved")

print("\nAll flow tests green.")
