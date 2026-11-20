"""ComfyOS Flow – node catalogue. One source for engine, store, import, entitlements and editor.

static/flow_nodes.json describes every node exactly once (ports, fields, texts). This module reads the
file and answers everything that depends on the catalogue:

* Role: nodes with "roles" are, depending on the setting, a trigger (signal outputs) or a condition (yes/no).
  role(node) returns the effective role, ports_for(node, edges) the ports of this one node.
* n+1 ports: an input with "grow" is called c1 … c(n+1) in the node – n in use, always exactly one free.
  normalize() renumbers without gaps and discards lines that fit no port.
* Outputs with "count": o1 … on by a field of the node (random: number of paths).
* Properties ("properties"): what "Gerätestatus" can compare. A device has a property if it
  reports a value for it – new device kinds therefore need no new node.

The same rules live in js/flow_canvas.js (HO.flowPorts); tests/test_flow_ports.py keeps both equal.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SPEC_FILE = Path(__file__).resolve().parent / "static" / "flow_nodes.json"
CATALOG: dict[str, Any] = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
FORMAT: int = int(CATALOG["version"])           # Flows tragen dieses Format; andere gelten als veraltet
SPECS: dict[str, dict] = {n["type"]: n for n in CATALOG["nodes"]}
PROPS: dict[str, dict] = {p["id"]: p for p in CATALOG["properties"]}
PRO_TYPES: frozenset[str] = frozenset(t for t, n in SPECS.items() if n.get("pro"))
GROW_MAX = 10
_PORT_NO = re.compile(r"^([a-z_]+?)(\d{1,2})$")


def spec(type_: Any) -> dict | None:
    """Catalogue entry of a node type, or None."""
    return SPECS.get(type_) if isinstance(type_, str) else None


def field(sp: dict | None, key: str) -> dict | None:
    """A field of a node by its key, or None."""
    return next((f for f in (sp or {}).get("fields", []) if f["key"] == key), None)


def value(node: dict, key: str, default: Any = None) -> Any:
    """Configuration value of a node; empty/missing = default from the catalogue."""
    v = (node.get("config") or {}).get(key)
    if v is None or v == "":
        f = field(spec(node.get("type")), key)
        return f["default"] if f and f.get("default") not in (None, "") else default
    return v


def roles(sp: dict | None) -> list[str]:
    """Roles of the device selection of a node (e.g. target, condition)."""
    return list((sp or {}).get("roles") or {})


def role(node: dict) -> str | None:
    """Effective role: the chosen role, otherwise the first of the catalogue (trigger). None = node without roles."""
    have = roles(spec(node.get("type")))
    if not have:
        return None
    want = (node.get("config") or {}).get("role")
    return want if want in have else have[0]


def prop_type(node: dict) -> str | None:
    """Data type of the property a node has chosen (bool, number, text …), or None."""
    p = PROPS.get(str((node.get("config") or {}).get("prop") or ""))
    return p["type"] if p else None


def when_ok(node: dict, cond: dict | None) -> bool:
    """"when" of a field or port: all keys must match. Special keys: role, @ptype."""
    for k, want in (cond or {}).items():
        have = role(node) if k == "role" else prop_type(node) if k == "@ptype" else value(node, k)
        if (have not in want) if isinstance(want, list) else (have != want):
            return False
    return True


def _count(node: dict, p: dict) -> int:
    """How many ports a growing port has right now: from the field that determines its number, limited to
    1…maximum."""
    if "count" not in p:
        return 0
    if not when_ok(node, p.get("count_when")):
        return 1
    try:
        n = int(float(value(node, p["count"], 1)))
    except (TypeError, ValueError):
        n = 1
    return max(1, min(int(p.get("max", GROW_MAX)), n))


def _split(port: str) -> tuple[str, int | None]:
    """Split a port id into base name and number ("out3" → ("out", 3)); without a number None stays."""
    m = _PORT_NO.match(port or "")
    return (m.group(1), int(m.group(2))) if m else (port, None)


def in_port(node: dict, port: str) -> dict | None:
    """Catalogue input for a port name of the node (c3 -> growing input c). None = does not exist."""
    sp = spec(node.get("type"))
    if sp is None or roles(sp):
        return None                                   # triggers/conditions have no inputs
    base, no = _split(port)
    for p in sp.get("inputs", []):
        if not when_ok(node, p.get("when")):
            continue
        if p.get("grow"):
            if p["id"] == port or (p["id"] == base and no is not None and 1 <= no <= 99):
                return p
        elif p["id"] == port:
            return p
    return None


def out_ports(node: dict) -> list[dict]:
    """Outputs of this node: by role, counted outputs rolled out (o1 … on)."""
    sp = spec(node.get("type"))
    if sp is None:
        return []
    r = role(node)
    raw = sp["roles"][r].get("outputs", []) if r else sp.get("outputs", [])
    out = []
    for p in raw:
        n = _count(node, p)
        if n:
            out += [{**p, "id": f"{p['id']}{i}", "label": p.get("label") or (str(i) if n > 1 else "")} for i in range(1, n + 1)]
        else:
            out.append(p)
    return out


def out_port(node: dict, port: str) -> dict | None:
    """An output of a node by its id, or None."""
    return next((p for p in out_ports(node) if p["id"] == port), None)


def ports_for(node: dict, edges: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """(inputs, outputs) of this node, the way the editor draws them. Growing inputs: one per port in
    use, plus exactly one free one (up to the maximum)."""
    sp = spec(node.get("type"))
    if sp is None:
        return [], []
    ins: list[dict] = []
    if not roles(sp):
        for p in sp.get("inputs", []):
            if not when_ok(node, p.get("when")):
                continue
            if not p.get("grow"):
                ins.append(p)
                continue
            used = sum(1 for e in edges or [] if e.get("to") == node.get("id") and _split(str(e.get("toPort")))[0] == p["id"])
            n = min(int(p.get("max", GROW_MAX)), used + 1)
            ins += [{**p, "id": f"{p['id']}{i}", "free": i > used} for i in range(1, n + 1)]
    return ins, out_ports(node)


def is_pro(node: Any) -> bool:
    """Pro node – or a Basic node in which a Pro setting differs from the default."""
    if not isinstance(node, dict):
        return False
    sp = spec(node.get("type"))
    if sp is None:
        return False
    if sp.get("pro"):
        return True
    cfg = node.get("config") or {}
    return any(f.get("pro") and cfg.get(f["key"]) not in (None, "", f.get("default")) and when_ok(node, f.get("when"))
               for f in sp.get("fields", []))


def unknown_types(nodes: Any) -> list[str]:
    """Node types of a graph that the catalogue does not know (sorted) – by them ComfyOS recognises outdated flows."""
    return sorted({str(n.get("type")) for n in nodes or [] if isinstance(n, dict) and n.get("type") not in SPECS})


def normalize(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[str]]:
    """Check lines against the catalogue: only existing ports of the same kind; a yes/no input takes one
    line (the last one wins); growing inputs are numbered c1 … cn without gaps. Returns (lines, hints)."""
    by_id = {n["id"]: n for n in nodes}
    notes: list[str] = []
    kept: list[tuple[dict, dict]] = []
    for e in edges:
        a, b = by_id.get(e.get("from")), by_id.get(e.get("to"))
        if a is None or b is None:
            continue
        pa, pb = out_port(a, str(e.get("fromPort"))), in_port(b, str(e.get("toPort")))
        if pa is None or pb is None or pa["kind"] != pb["kind"]:
            la = (spec(a.get("type")) or {}).get("label", a.get("type"))
            lb = (spec(b.get("type")) or {}).get("label", b.get("type"))
            notes.append(f"Verbindung {la} → {lb} passt nicht und wurde entfernt")
            continue
        kept.append((e, pb))
    # single yes/no input: only the last line counts
    single: dict[tuple, int] = {}
    for i, (e, pb) in enumerate(kept):
        if pb["kind"] == "bool" and not pb.get("grow"):
            single[(e["to"], e["toPort"])] = i
    kept = [(e, pb) for i, (e, pb) in enumerate(kept)
            if pb.get("grow") or pb["kind"] != "bool" or single[(e["to"], e["toPort"])] == i]
    # growing inputs: reassigned without gaps by number (then order)
    groups: dict[tuple, list[tuple[int, int, dict]]] = {}
    for i, (e, pb) in enumerate(kept):
        if pb.get("grow"):
            no = _split(str(e["toPort"]))[1]
            groups.setdefault((e["to"], pb["id"]), []).append((no if no is not None else 999, i, pb))
    renamed: dict[int, str | None] = {}
    for (_to, base), items in groups.items():
        for k, (_no, i, pb) in enumerate(sorted(items, key=lambda x: (x[0], x[1]))):
            renamed[i] = f"{base}{k + 1}" if k < int(pb.get("max", GROW_MAX)) else None
    out = []
    for i, (e, _pb) in enumerate(kept):
        if i in renamed:
            if renamed[i] is None:
                notes.append("Zu viele Bedingungen an einem Baustein – überzählige entfernt")
                continue
            e = {**e, "toPort": renamed[i]}
        out.append(e)
    return out, notes


def check() -> list[str]:
    """Shape check of the catalogue (start + tests)."""
    probs: list[str] = []
    kinds, widgets, cats = set(CATALOG["kinds"]), set(CATALOG["widgets"]), set(CATALOG["categories"])
    seen: set[str] = set()
    for n in CATALOG["nodes"]:
        t = n.get("type")
        if t in seen:
            probs.append(f"{t}: doppelt")
        seen.add(t)
        if n.get("category") not in cats:
            probs.append(f"{t}: unbekannte Kategorie {n.get('category')}")
        rl = n.get("roles") or {}
        if rl:
            if n.get("inputs") or n.get("outputs"):
                probs.append(f"{t}: Rollen-Baustein mit festen Anschlüssen")
            for r, d in rl.items():
                if r not in CATALOG["roles"]:
                    probs.append(f"{t}: unbekannte Rolle {r}")
                want = "bool" if r == "condition" else "signal"
                if not d.get("outputs") or any(p.get("kind") != want for p in d["outputs"]):
                    probs.append(f"{t}: Rolle {r} braucht {want}-Ausgänge")
        ports = n.get("inputs", []) + n.get("outputs", []) + [p for d in rl.values() for p in d.get("outputs", [])]
        for p in ports:
            if p.get("kind") not in kinds:
                probs.append(f"{t}: Anschluss {p.get('id')} mit unbekannter Art {p.get('kind')}")
            if not re.match(r"^[a-z_]+$", str(p.get("id") or "")):
                probs.append(f"{t}: Anschlussname {p.get('id')} (nur Buchstaben, Zahlen sind für n+1 reserviert)")
        keys = [f.get("key") for f in n.get("fields", [])]
        if len(keys) != len(set(keys)) or "role" in keys:
            probs.append(f"{t}: Feldschlüssel doppelt oder reserviert")
        for f in n.get("fields", []):
            if f.get("widget") not in widgets:
                probs.append(f"{t}: Feld {f.get('key')} mit unbekanntem Widget {f.get('widget')}")
        sm = n.get("summary")
        for text in (sm.values() if isinstance(sm, dict) else [sm or ""]):
            for k in re.findall(r"\{(\w+)\}", text):
                if k not in keys:
                    probs.append(f"{t}: Zusammenfassung nennt unbekanntes Feld {k}")
    for p in CATALOG["properties"]:
        if p.get("type") not in ("bool", "number"):
            probs.append(f"Eigenschaft {p.get('id')}: unbekannter Typ")
    return probs


def summary() -> dict[str, Any]:
    """Compact description of all nodes and properties – for the AI template."""
    def ports(ps: list[dict]) -> list[str]:
        return [p["id"] + ("1…n" if p.get("grow") or "count" in p else "") for p in ps]

    nodes = []
    for t, n in SPECS.items():
        row: dict[str, Any] = {"type": t, "label": n.get("label"), "category": n.get("category"), "pro": bool(n.get("pro")),
                               "help": n.get("help") or "",
                               "fields": [{"key": f["key"], "widget": f.get("widget"), "default": f.get("default"),
                                           **({"options": [o[0] for o in f["options"]]} if f.get("options") else {}),
                                           **({"when": f["when"]} if f.get("when") else {})}
                                          for f in n.get("fields", []) if f.get("widget") != "token"]}
        if n.get("roles"):
            row["roles"] = {r: ports(d.get("outputs", [])) for r, d in n["roles"].items()}
        else:
            row["inputs"], row["outputs"] = ports(n.get("inputs", [])), ports(n.get("outputs", []))
        nodes.append(row)
    props = [{"id": p["id"], "label": p["label"], "type": p["type"], **({"unit": p["unit"]} if p.get("unit") else {})}
             for p in CATALOG["properties"]]
    return {"nodes": nodes, "properties": props}


def capabilities(device: dict) -> list[str]:
    """Properties for which the device reports a value (automatic, without a list per device kind)."""
    return [pid for pid in PROPS if device.get(pid) is not None]
