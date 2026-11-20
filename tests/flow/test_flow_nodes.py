"""ComfyOS Flow – every node of the catalogue in every role: runs with default values, without devices and with
nonsense without the engine stumbling. Errors of a node end up at the flow (last_error), never as an exception in the round.

    python tests/flow/test_flow_nodes.py [path-to-comfyos]
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("nodes")

from app import flow_catalog as fc, flow_engine as fe, flows_store, scenes_store  # noqa: E402

DEV = {
    "m": {"id": "m", "name": "Melder", "kind": "motion", "room": "Flur", "room_id": "r1", "online": True, "motion": True, "battery": 5, "temperature": 19.5},
    "l": {"id": "l", "name": "Lampe", "kind": "light", "room": "Flur", "room_id": "r1", "online": True, "on": False, "brightness": 30, "control_item": "x",
          "light": {"caps": {"effects": []}}},
    "t": {"id": "t", "name": "Heizung", "kind": "thermostat", "room": "Bad", "room_id": "r2", "online": False, "temperature": 21.0, "target": 20.0,
          "control_item": "y", "errors": ["Gerätefehler E1"], "valve": 40},
    "c": {"id": "c", "name": "Rollladen", "kind": "cover", "room": "Bad", "room_id": "r2", "online": True, "position": 0, "control_item": "z"},
    "s": {"id": "s", "name": "Rauch", "kind": "smoke", "room": "Flur", "room_id": "r1", "online": True, "alarm": True},
    "p": {"id": "p", "name": "Lautsprecher", "kind": "media", "room": "Flur", "room_id": "r1", "online": True, "playing": False, "volume": 20,
          "control_item": "p"},
}
calls = []
NOW = [datetime(2026, 11, 16, 12, 0, 0).timestamp()]


async def fake_sleep(s):
    """Replacement for the waiting of the engine: waits until the test clock has been set that far."""
    until = NOW[0] + s
    while NOW[0] < until:
        await asyncio.sleep(0)


fe._sleep = fake_sleep
fe.clock = lambda: NOW[0]
scenes_store.active_map = lambda: {"room:r1": "abend"}
scenes_store.scene = lambda sid: {"id": sid, "name": "Abend"} if sid == "abend" else None
eng = fe.engine


async def hook(*a, **k):
    """Replacement for every action of the core: records the call."""
    calls.append(a)
    return {"switched": 1, "stopped": 1}


async def devices():
    """The devices of this test."""
    return [dict(d) for d in DEV.values()]


def hooks():
    """Connect the engine with the replacement functions of this test."""
    eng.hooks.update(devices=devices, publish=lambda m: None, push=lambda *a: calls.append(a),
                     **{k: hook for k in ("apply_device", "apply_lights", "apply_home", "apply_routine", "apply_scene", "stop_scene",
                                          "set_lock", "switch_locked")})


def N(id_, type_, **config):
    """Baustein eines Flows."""
    return {"id": id_, "type": type_, "x": 0, "y": 0, "config": config}


def E(a, ap, b, bp):
    """Connection between two ports of a flow."""
    return {"from": a, "fromPort": ap, "to": b, "toPort": bp}


def fresh():
    """Empty state: no flows, engine reset, no recorded calls."""
    flows_store.reset()
    eng.reset()
    hooks()
    calls.clear()


def mk(name, nodes, edges):
    """Create a flow and switch it on; returns its id."""
    nodes, edges = flows_store.clean_graph(nodes, edges)
    edges, _ = fc.normalize(nodes, edges)
    fid = flows_store.create(name, nodes, edges, enabled=True)["id"]
    eng.reload()
    return fid


async def settle():
    """Give the engine time to finish what was triggered."""
    for _ in range(40):
        await asyncio.sleep(0)


ALL = {"devices": list(DEV), "rooms": ["r1", "gibtsnicht"], "groups": ["gibtsnicht"]}
# sensible settings per node (everything else: defaults of the catalogue)
GOOD = {
    "device_state": {"target": ALL, "prop": "motion"}, "device_signal": {"target": {}}, "reachable": {"target": {}},
    "stale": {"target": ALL}, "scene": {"scene": "abend"}, "variable": {"name": "x"},
    "set_device": {"target": ALL}, "set_scene": {"scene": "abend", "target": ALL}, "set_routine": {"routine": "nacht"},
    "playback": {"target": ALL, "do": "favorite", "favorite": "Radio"},
    "lock": {"target": ALL, "switch": "off"}, "set_var": {"name": "x", "op": "add", "window": 10},
    "http": {"url": "https://example.com/x"}, "timer": {"wait": 5}, "rate": {"wait": 5}, "random": {"mode": "delay", "min": 1, "max": 2},
}
JUNK = {"target": "kein objekt", "prop": 7, "value": "abc", "wait": "x", "name": None, "scene": 5, "days": "mo", "at": "25:99",
        "from": [], "to": {}, "every": -1, "n": "viele", "p": None, "gap": "?", "times": 10 ** 9, "below": [], "state": 3,
        "text": 12, "url": "", "flow": "gibtsnicht", "routine": "", "do": "quatsch", "mode": "quatsch", "cmp": "quatsch", "signal": "quatsch"}


async def run():
    """Run every node of the catalogue in every role – with defaults, sensible and nonsensical
    settings."""
    seen_trigger, seen_cond, seen_signal = set(), set(), set()

    def http_client(allow_local=False):
        import httpx
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
    fe.http_client = http_client

    for label, cfg_of in (("Vorgaben", lambda t: {}), ("sinnvoll", lambda t: GOOD.get(t, {})), ("Unsinn", lambda t: dict(JUNK))):
        for t, sp in fc.SPECS.items():
            roles = fc.roles(sp)
            # 1) Trigger role, or logic with a query of its own: run a round, start by hand
            if "trigger" in roles or (not roles and "poll" in fe.HANDLERS[t]):
                fresh()
                nodes = [N("x", t, **cfg_of(t), **({"role": "trigger"} if roles else {})), N("a", "inspect")]
                edges = [E("x", p["id"], "a", "in") for p in fc.out_ports(nodes[0])]
                if not roles:        # hold time: needs a condition
                    nodes.append(N("c", "time", role="condition"))
                    edges.append(E("c", "val", "x", "c1"))
                fid = mk(f"{t}/{label}", nodes, edges)
                await eng.tick()
                NOW[0] += 61
                await eng.tick()
                for p in fc.out_ports(nodes[0]):
                    ctx = await eng.fire_node(fid, "x", p["id"])
                    assert ctx is not None and "last_error" not in eng.info(fid), (t, label, eng.info(fid))
                    assert eng.state[f"{fid}/a"].get("at"), (t, label, "the output does not reach the next node")
                await settle()
                seen_trigger.add(t)
            # 2) Condition role (and yes/no logic): returns yes, no or unknown – never an exception
            if "condition" in roles or (not roles and "state" in fe.HANDLERS[t]):
                fresh()
                nodes = [N("p", "play"), N("w", "when"), N("x", t, **cfg_of(t), **({"role": "condition"} if roles else {})), N("a", "inspect"),
                         N("c", "time", role="condition")]
                edges = [E("p", "out", "w", "in"), E("x", "val", "w", "c1"), E("w", "yes", "a", "in"), E("w", "no", "a", "in")]
                if not roles:
                    edges.append(E("c", "val", "x", "c"))
                fid = mk(f"{t}/{label}", nodes, edges)
                await eng.tick()
                ctx = await eng.fire_node(fid, "p")
                assert ctx is not None and "last_error" not in eng.info(fid), (t, label, eng.info(fid))
                assert [p for p in ctx.path if p[0] == "c"], (t, label, "the condition was not checked")
                seen_cond.add(t)
            # 3) Nodes with a signal input: every signal at every input; errors stay at the flow, the engine keeps running
            if not roles and "signal" in fe.HANDLERS[t]:
                fresh()
                x = N("x", t, **cfg_of(t))
                if t == "set_flow" and label == "sinnvoll":      # needs a real second flow as the target
                    x["config"]["flow"] = mk("Ziel", [N("p", "play")], [])
                nodes = [N("p", "play"), x, N("a", "inspect"), N("c", "time", role="condition")]
                edges = [E("x", p["id"], "a", "in") for p in fc.out_ports(x)]
                for i, p in enumerate(q for q in fc.ports_for(x, [])[0]):
                    if p["kind"] == "signal":
                        nodes.append(N(f"p{i}", "play"))
                        edges.append(E(f"p{i}", "out", "x", p["id"]))
                    else:
                        edges.append(E("c", "val", "x", p["id"]))
                fid = mk(f"{t}/{label}", nodes, edges)
                await eng.tick()
                for n in [n for n in nodes if n["type"] == "play" and n["id"] != "p"]:
                    ctx = await eng.fire_node(fid, n["id"])
                    assert ctx is not None, (t, label)
                    if label == "sinnvoll":
                        assert "last_error" not in eng.info(fid), (t, label, eng.info(fid))
                NOW[0] += 30
                await settle()
                await eng.tick()
                await settle()
                seen_signal.add(t)
            eng.flush()
    every = set(fc.SPECS)
    assert seen_trigger | seen_cond | seen_signal == every, every - (seen_trigger | seen_cond | seen_signal)
    both = [t for t, sp in fc.SPECS.items() if len(fc.roles(sp)) == 2]
    assert set(both) <= seen_trigger & seen_cond and len(both) == 13, both     # 13 since "Taster" (0.23)
    print(f"1 {len(seen_trigger)} triggers, {len(seen_cond)} conditions, {len(seen_signal)} logic/actions:"
          "with defaults, sensible values and nonsense without a crash")

    # 2) A node whose query fails does not hold up the other flows
    fresh()
    boom = mk("Kaputt", [N("x", "time", at="18:00")], [])
    good = mk("Gut", [N("i", "interval", every=10), N("a", "inspect")], [E("i", "out", "a", "in")])
    real = fe.HANDLERS["time"]["poll"]
    fe.HANDLERS["time"]["poll"] = lambda ctx, n: 1 / 0
    try:
        await eng.tick()
        NOW[0] += 12
        await eng.tick()
        await settle()
    finally:
        fe.HANDLERS["time"]["poll"] = real
    assert "division" in eng.info(boom)["last_error"] and eng.state[f"{good}/a"].get("at"), (eng.info(boom), eng.state.get(f"{good}/a"))
    print("2 if the query of a node fails, the error is at the flow – other flows keep running")


asyncio.run(run())
print("\nOK test_flow_nodes")
