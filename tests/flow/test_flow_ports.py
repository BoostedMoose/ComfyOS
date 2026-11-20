"""ComfyOS Flow – catalogue rules: roles, n+1 ports, renumbering, counted outputs, Pro settings.

    python tests/flow/test_flow_ports.py [path-to-comfyos]

The same rules live in the editor (js/flow_canvas.js, HO.flowSpec) – section 6 keeps both sides together.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("ports")

from app import auth, flow_catalog as fc, flows_store, main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def N(id_, type_, **config):
    """Baustein eines Flows."""
    return {"id": id_, "type": type_, "x": 0, "y": 0, "config": config}


def E(a, ap, b, bp):
    """Connection between two ports of a flow, with an id."""
    return {"id": f"{a}-{b}-{bp}", "from": a, "fromPort": ap, "to": b, "toPort": bp}


def ids(ports):
    """Ids of a list of ports."""
    return [p["id"] for p in ports]


def norm(nodes, edges):
    """Have connections normalised: (from, output, to, input) per connection and the hints about them."""
    out, notes = fc.normalize(nodes, edges)
    return [(e["from"], e["fromPort"], e["to"], e["toPort"]) for e in out], notes


COND = [N(f"c{i}", "time", role="condition") for i in range(1, 13)]
W = N("w", "when")

# 1) The catalogue is formally in order; roles and defaults
assert fc.check() == [], fc.check()
assert fc.role(N("a", "presence")) == "trigger" and fc.role(N("a", "presence", role="condition")) == "condition"
assert fc.role(N("a", "presence", role="quatsch")) == "trigger" and fc.role(N("a", "stale")) == "condition" and fc.role(W) is None
assert fc.value(N("a", "timer"), "wait") == 300 and fc.value(N("a", "timer", wait=""), "wait") == 300 and fc.value(N("a", "timer", wait=5), "wait") == 5
assert fc.value(N("a", "device_state"), "hold_off") == 0 and fc.value(N("a", "gibtsnicht"), "x", 7) == 7
assert ids(fc.out_ports(N("a", "device_state"))) == ["out", "end"] and ids(fc.out_ports(N("a", "device_state", role="condition"))) == ["val"]
assert ids(fc.out_ports(N("a", "time"))) == ["out"] and fc.ports_for(N("a", "time"))[0] == [], "triggers have no inputs"
print("1 catalogue formally in order; the role determines the outputs (trigger: signals, condition: yes/no)")

# 2) n+1: one per port in use, plus exactly one free one – up to the maximum
for used in (0, 1, 3, 9, 10):
    edges = [E(f"c{i}", "val", "w", f"c{i}") for i in range(1, used + 1)]
    ins, outs = fc.ports_for(W, edges)
    want = ["in"] + [f"c{i}" for i in range(1, min(10, used + 1) + 1)]
    assert ids(ins) == want and ids(outs) == ["yes", "no"], (used, ids(ins))
    assert [p["id"] for p in ins if p.get("free")] == ([f"c{used + 1}"] if used < 10 else []), used
print("2 n+1: 0 conditions -> c1 free; 3 -> c4 free; 10 -> no free port any more")

# 3) Renumbering: close gaps, the order stays, "c" without a number is appended, above the maximum it drops out
nodes = [W] + COND
got, _ = norm(nodes, [E("c1", "val", "w", "c1"), E("c2", "val", "w", "c3"), E("c3", "val", "w", "c7")])
assert [g[3] for g in got] == ["c1", "c2", "c3"], got
got, _ = norm(nodes, [E("c3", "val", "w", "c7"), E("c1", "val", "w", "c2")])            # after removing c1
assert sorted((g[0], g[3]) for g in got) == [("c1", "c1"), ("c3", "c2")], got
got, _ = norm(nodes, [E("c1", "val", "w", "c"), E("c2", "val", "w", "c1")])
assert sorted((g[0], g[3]) for g in got) == [("c1", "c2"), ("c2", "c1")], got
got, notes = norm(nodes, [E(f"c{i}", "val", "w", f"c{i}") for i in range(1, 13)])
assert len(got) == 10 and [g[3] for g in got] == [f"c{i}" for i in range(1, 11)] and any("Zu viele" in n for n in notes), (got, notes)
print("3 renumbering without gaps, stable order, at most 10 conditions")

# 4) Unfitting lines drop out: wrong kind, the port does not exist (in this role); a yes/no input takes one line
nodes = [W, N("p", "play"), N("t", "device_state"), N("c", "device_state", role="condition"), N("n", "not"),
         N("a", "set_device"), N("l", "loop", mode="times"), N("lw", "loop", mode="while"), N("r", "random", mode="pick", n=3),
         N("r1", "random", mode="chance")]
got, notes = norm(nodes, [
    E("p", "out", "w", "in"), E("p", "out", "a", "in"),          # Signale: mehrere Linien am selben Ausgang sind in Ordnung
    E("t", "val", "w", "c1"),                                    # a trigger has no yes/no output
    E("c", "out", "a", "in"),                                    # Bedingung hat keinen Signal-Ausgang
    E("c", "val", "a", "in"),                                    # yes/no to a signal input
    E("p", "out", "w", "c1"),                                    # signal to a yes/no input
    E("c", "val", "n", "c"), E("t", "end", "a", "in"),
    E("c", "val", "l", "c1"),                                    # fixed number: no condition ports
    E("c", "val", "lw", "c1"),
    E("r", "o3", "a", "in"), E("r", "o4", "a", "in"),            # 3 paths: o4 does not exist
    E("r1", "o1", "a", "in"), E("r1", "o2", "a", "in"),          # probability: only one output
    E("a", "out", "p", "in"),                                    # triggers have no input
])
assert got == [("p", "out", "w", "in"), ("p", "out", "a", "in"), ("c", "val", "n", "c"), ("t", "end", "a", "in"),
               ("c", "val", "lw", "c1"), ("r", "o3", "a", "in"), ("r1", "o1", "a", "in")], got
assert len(notes) == 8, notes
got, _ = norm(nodes, [E("c", "val", "n", "c"), E("t", "val", "n", "c"), E("w", "yes", "a", "in")])
assert got == [("c", "val", "n", "c"), ("w", "yes", "a", "in")], got
nodes2 = [N("n", "not"), N("c1", "time", role="condition"), N("c2", "presence", role="condition")]
got, _ = norm(nodes2, [E("c1", "val", "n", "c"), E("c2", "val", "n", "c")])
assert got == [("c2", "val", "n", "c")], "single yes/no input: the last line counts"
assert ids(fc.out_ports(N("r", "random", mode="pick", n=4))) == ["o1", "o2", "o3", "o4"] and ids(fc.out_ports(N("r", "random", mode="delay", n=4))) == ["o1"]
assert ids(fc.out_ports(N("r", "random", n=99))) == [f"o{i}" for i in range(1, 11)] and ids(fc.out_ports(N("r", "random", n="x"))) == ["o1"]
print("4 lines only between existing ports of the same kind; role changes and settings take ports away")

# 5) Pro: Pro node or Pro setting on a Basic node; properties from the device values
assert fc.is_pro(N("a", "toggle")) and not fc.is_pro(N("a", "when")) and not fc.is_pro("kein dict") and not fc.is_pro(N("a", "gibtsnicht"))
assert not fc.is_pro(N("a", "device_state", prop="temperature")) and not fc.is_pro(N("a", "device_state", prop="temperature", band=0))
assert fc.is_pro(N("a", "device_state", prop="temperature", band=0.5))
assert not fc.is_pro(N("a", "device_state", prop="motion", band=0.5)), "the switching threshold only counts for number properties"
assert fc.capabilities({"on": False, "brightness": None, "temperature": 0, "motion": True, "name": "x"}) == ["on", "motion", "temperature"]
assert fc.unknown_types([N("a", "when"), N("b", "motion"), N("c", "xyz"), "x"]) == ["motion", "xyz"]
s = fc.summary()
when = next(n for n in s["nodes"] if n["type"] == "when")
dev = next(n for n in s["nodes"] if n["type"] == "device_state")
assert when["inputs"] == ["in", "c1…n"] and dev["roles"] == {"trigger": ["out", "end"], "condition": ["val"]} and len(s["properties"]) == len(fc.PROPS)
assert all(f["widget"] != "token" for n in s["nodes"] for f in n["fields"]), "the AI template contains the webhook token field"
print("5 a Pro setting counts like a Pro node; properties = what the device reports; AI template with roles and n+1")

# 6) Editor: the same rules in HO.flowSpec (role, when, n+1, counted outputs, renumbering)
js = (ROOT / "backend/app/static/js/flow_canvas.js").read_text(encoding="utf-8")
for name in ("role(n)", "when(n, cond)", "outs(n)", "inPort(n, port)", "ins(n, edges)", "normalize(nodes, edges)", "split(port)"):
    assert name in js, f"HO.flowSpec.{name} is missing"
assert r"/^([a-z_]+?)(\d{1,2})$/" in js and fc._PORT_NO.pattern == r"^([a-z_]+?)(\d{1,2})$", "the port pattern differs"
assert "'@ptype'" in js and "count_when" in js and "used + 1" in js
for bad in ("?.", "??"):
    for f in ("flow_canvas.js", "flow.js", "settings.js"):
        src = (ROOT / "backend/app/static/js" / f).read_text(encoding="utf-8")
        code = re.sub(r"'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|/\*.*?\*/|//[^\n]*", "", src, flags=re.S)
        assert bad not in code, f"{f}: syntax newer than ES2017 ({bad}) – old iPads then show nothing"
print("6 editor: HO.flowSpec with the same rules; no syntax newer than ES2017")

# 7) API: a third condition creates a fourth free port, removing lets the number shrink again
with TestClient(main.app) as client:
    client.cookies.set(auth.COOKIE, auth.create_session("test"))
    nodes = [N("p", "play"), W, N("a", "set_presence")] + COND[:3]
    base = [E("p", "out", "w", "in"), E("w", "yes", "a", "in")]
    r = client.post("/api/flows", json={"name": "Ports", "nodes": nodes, "edges": base + [E(f"c{i}", "val", "w", f"c{i}") for i in (1, 2, 3)]})
    assert r.status_code == 200, r.text
    f = r.json()
    w = next(n for n in f["nodes"] if n["id"] == "w")
    assert ids(fc.ports_for(w, f["edges"])[0]) == ["in", "c1", "c2", "c3", "c4"]
    keep = [e for e in f["edges"] if e["from"] != "c2"]
    f = client.put(f"/api/flows/{f['id']}", json={"name": "Ports", "nodes": f["nodes"], "edges": keep}).json()
    assert sorted(e["toPort"] for e in f["edges"] if e["to"] == "w") == ["c1", "c2", "in"], f["edges"]
    assert ids(fc.ports_for(w, f["edges"])[0]) == ["in", "c1", "c2", "c3"]
    assert flows_store.get(f["id"])["format"] == fc.FORMAT
    # Rollenwechsel: die Linie am weggefallenen Ausgang verschwindet
    nodes = [dict(n, config={**n["config"], "role": "trigger"}) if n["id"] == "c1" else n for n in f["nodes"]]
    f = client.put(f"/api/flows/{f['id']}", json={"name": "Ports", "nodes": nodes, "edges": f["edges"]}).json()
    assert sorted(e["toPort"] for e in f["edges"] if e["to"] == "w") == ["c1", "in"], f["edges"]
print("7 API: third condition -> fourth free port; removing shrinks; a role change takes the line along")
print("\nOK test_flow_ports")
