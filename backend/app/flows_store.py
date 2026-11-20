"""ComfyOS Flow – automations as node graphs in data/flows.json.

    { "version": 3,
      "flows": [ {id, name, enabled, format, nodes:[{id,type,x,y,config}], edges:[{id,from,fromPort,to,toPort}],
                  created, updated, description?, group?} ],   order of the list = order in the interface
      "vars":  { "<name>": bool|number|text }, variables, across flows
      "state": { "<flow>/<node>": {...},     run-time state per node (trigger memory, toggle, loop, inspector)
                 "_flow:<id>": {...},        last trigger, error
                 "_waits": {...},            running timers/loops – survive a restart
                 "_win:<name>": {...} } }    counting window of a variable

Every flow carries the catalogue format it was built with (`format`). If it does not match the running version or
the flow contains an unknown node, it counts as outdated: it stays stored but does not run and can
only be deleted (outdated()). Nothing is converted – an outdated flow is built anew.
"""
from __future__ import annotations

import copy
import json
import math
import re
import secrets
import threading
import time
from typing import Any

from . import jsonfile
from . import flow_catalog
from .paths import DATA_DIR, ensure_dirs

FLOWS_FILE = DATA_DIR / "flows.json"
MAX_FLOWS, MAX_NODES, MAX_EDGES, MAX_VARS = 200, 200, 400, 200
MAX_CONFIG = 8000          # Zeichen JSON je Node-Konfiguration
MAX_TEXT = 200             # characters of a text variable
MAX_GROUP = 40             # characters of a group name (since 0.21.1; without a group the field is missing entirely)
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
_lock = threading.Lock()


VERSION = 3                # file format; the format of the individual flows is in flow_catalog.FORMAT


def _empty() -> dict[str, Any]:
    """Leerer Store."""
    return {"version": VERSION, "flows": [], "vars": {}, "state": {}}


def _load() -> dict[str, Any]:
    """Content of flows.json; missing or wrongly typed sections are replaced by empty ones."""
    data = jsonfile.read(FLOWS_FILE, None)
    if data is None:
        return _empty()
    for k, v in _empty().items():
        if not isinstance(data.get(k), type(v)):
            data[k] = v
    return data


def outdated(flow: dict[str, Any]) -> bool:
    """Flow from another catalogue format or with an unknown node: does not run, can only be deleted."""
    return flow.get("format") != flow_catalog.FORMAT or bool(flow_catalog.unknown_types(flow.get("nodes")))


def _save(data: dict[str, Any]) -> None:
    """Write atomically to flows.json (with the current file version)."""
    ensure_dirs()
    data["version"] = VERSION
    jsonfile.write(FLOWS_FILE, data)


def _num(v: Any, lo: float, hi: float, default: float) -> float:
    """Number rounded to two places and limited to lo…hi; `default` for everything that is not a finite number."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if f != f else round(min(hi, max(lo, f)), 2)


def new_id(prefix: str) -> str:
    """New random id with a prefix (f = flow, n = node, e = connection)."""
    return prefix + secrets.token_hex(5)


def _clean_id(v: Any, prefix: str) -> str:
    """Take over an id from a request if it matches the pattern – otherwise assign a new one."""
    s = str(v or "")
    return s if _ID.match(s) else new_id(prefix)


def _clean_config(cfg: Any) -> dict[str, Any]:
    """Limit the settings of a node: only simple values, lists of simple values and the target selection; never
    NaN/Infinity. Settings that have grown too large are discarded entirely."""
    if not isinstance(cfg, dict):
        return {}
    out = {}
    finite = lambda x: not isinstance(x, float) or math.isfinite(x)   # noqa: E731 – NaN/Infinity nie speichern
    for k, v in list(cfg.items())[:40]:
        if (isinstance(v, (str, int, float, bool)) or v is None) and finite(v):
            out[str(k)[:40]] = v[:2000] if isinstance(v, str) else v
        elif isinstance(v, list):
            out[str(k)[:40]] = [x[:200] if isinstance(x, str) else x for x in v[:50]
                                if isinstance(x, (str, int, float, bool)) and finite(x)]
        elif isinstance(v, dict):       # Ziel-Auswahl: {"devices": [...], "groups": [...], "rooms": [...], "all": true}
            out[str(k)[:40]] = {str(a)[:20]: (b if isinstance(b, bool) else [str(x)[:200] for x in b[:100] if isinstance(x, str)])
                                for a, b in list(v.items())[:6] if isinstance(b, (list, bool))}
    return out if len(json.dumps(out, ensure_ascii=False)) <= MAX_CONFIG else {}


def clean_graph(nodes: Any, edges: Any) -> tuple[list[dict], list[dict]]:
    """Check and limit the shape. Semantics (ports, roles, n+1) are checked by flow_catalog.normalize()."""
    out_nodes, seen = [], set()
    for n in (nodes if isinstance(nodes, list) else [])[:MAX_NODES]:
        if not isinstance(n, dict) or not str(n.get("type") or "").strip():
            continue
        nid = _clean_id(n.get("id"), "n")
        if nid in seen:
            nid = new_id("n")
        seen.add(nid)
        out_nodes.append({"id": nid, "type": str(n["type"])[:40], "x": _num(n.get("x"), -1e5, 1e5, 0),
                          "y": _num(n.get("y"), -1e5, 1e5, 0), "config": _clean_config(n.get("config"))})
    out_edges, pairs = [], set()
    for e in (edges if isinstance(edges, list) else [])[:MAX_EDGES]:
        if not isinstance(e, dict):
            continue
        key = tuple(str(e.get(k) or "")[:40] for k in ("from", "fromPort", "to", "toPort"))
        if key[0] not in seen or key[2] not in seen or key[0] == key[2] or not key[1] or not key[3] or key in pairs:
            continue
        pairs.add(key)
        out_edges.append({"id": _clean_id(e.get("id"), "e"), "from": key[0], "fromPort": key[1], "to": key[2], "toPort": key[3]})
    return out_nodes, out_edges


def _ensure_tokens(nodes: list[dict]) -> None:
    """Webhook triggers get a token that cannot be guessed (once, it stays when saving)."""
    for n in nodes:
        if n["type"] == "webhook":
            tok = n["config"].get("token")
            if not isinstance(tok, str) or len(tok) < 20:
                n["config"]["token"] = secrets.token_urlsafe(24)


def _clean_name(name: Any) -> str:
    """Name of a flow: whitespace cleaned, at most 60 characters, never empty."""
    return " ".join(str(name or "").split())[:60] or "Neue Automatisierung"


# ---------------------------------------------------------------- Flows
def all_flows() -> list[dict[str, Any]]:
    """All stored flows (outdated ones too)."""
    with _lock:
        return _load()["flows"]


def get(flow_id: str) -> dict[str, Any] | None:
    """A flow by its id, or None."""
    return next((f for f in all_flows() if f.get("id") == flow_id), None)


def _clean_desc(v: Any) -> str:
    """Description of a flow: whitespace cleaned, at most 200 characters."""
    return " ".join(str(v or "").split())[:200]


def create(name: str, nodes: Any = None, edges: Any = None, enabled: bool = False,
           description: str | None = None) -> dict[str, Any]:
    """Create a new flow. Shape and limits are checked here, the meaning (ports, roles) before by
    flow_catalog.normalize. Webhook triggers get their token."""
    n, e = clean_graph(nodes or [], edges or [])
    _ensure_tokens(n)
    now = time.time()
    desc = _clean_desc(description)
    flow = {"id": new_id("f"), "name": _clean_name(name), "enabled": bool(enabled), "format": flow_catalog.FORMAT,
            "nodes": n, "edges": e, "created": now, "updated": now}
    if desc:
        flow["description"] = desc
    with _lock:
        data = _load()
        if len(data["flows"]) >= MAX_FLOWS:
            raise ValueError(f"Höchstens {MAX_FLOWS} Automatisierungen.")
        data["flows"].append(flow)
        _save(data)
    return flow


def save(flow_id: str, name: Any, nodes: Any, edges: Any, description: str | None = None) -> dict[str, Any]:
    """Replace the graph of a flow. Webhook tokens stay stable; the memory of removed nodes is deleted.
    Raises KeyError if the flow does not exist."""
    n, e = clean_graph(nodes, edges)
    with _lock:
        data = _load()
        f = next((x for x in data["flows"] if x["id"] == flow_id), None)
        if f is None:
            raise KeyError(flow_id)
        # webhook addresses stay stable even if the editor or an import does not send the token along
        tokens = {x.get("id"): (x.get("config") or {}).get("token") for x in f.get("nodes") or [] if x.get("type") == "webhook"}
        for x in n:
            if x["type"] == "webhook" and not x["config"].get("token") and tokens.get(x["id"]):
                x["config"]["token"] = tokens[x["id"]]
        _ensure_tokens(n)
        desc = _clean_desc(description)
        f.update(name=_clean_name(name if name is not None else f.get("name")), nodes=n, edges=e, updated=time.time(),
                 format=flow_catalog.FORMAT)
        if desc:
            f["description"] = desc
        elif description is not None:
            f.pop("description", None)
        live = {x["id"] for x in n}
        data["state"] = {k: v for k, v in data["state"].items() if not k.startswith(flow_id + "/") or k.split("/", 1)[1] in live}
        _save(data)
        return f


def set_enabled(flow_id: str, enabled: bool) -> dict[str, Any]:
    """Switch a flow on or off. Raises KeyError if it does not exist."""
    with _lock:
        data = _load()
        f = next((x for x in data["flows"] if x["id"] == flow_id), None)
        if f is None:
            raise KeyError(flow_id)
        f["enabled"] = bool(enabled)
        _save(data)
        return f


def rename(flow_id: str, name: Any) -> dict[str, Any]:
    """Rename a flow. Raises KeyError if it does not exist."""
    with _lock:
        data = _load()
        f = next((x for x in data["flows"] if x["id"] == flow_id), None)
        if f is None:
            raise KeyError(flow_id)
        f.update(name=_clean_name(name), updated=time.time())
        _save(data)
        return f


def duplicate(flow_id: str) -> dict[str, Any]:
    """Copy a flow (switched off, with new webhook tokens, in the same group)."""
    src = get(flow_id)
    if src is None:
        raise KeyError(flow_id)
    nodes = copy.deepcopy(src["nodes"])
    for n in nodes:
        n["config"].pop("token", None)       # new token: one webhook address never starts two flows
    copied = create(src["name"] + " (Kopie)", nodes, src["edges"], enabled=False)
    return set_group(copied["id"], src.get("group")) if src.get("group") else copied


def clean_group(v: Any) -> str:
    """Group name of a flow: whitespace cleaned, at most MAX_GROUP characters; empty = no group."""
    return " ".join(str(v or "").split())[:MAX_GROUP]


def set_group(flow_id: str, group: Any) -> dict[str, Any]:
    """Assign a flow to a group (empty = take it out of the group; the field then drops out, older files stay
    the same byte for byte). Raises KeyError if the flow does not exist."""
    name = clean_group(group)
    with _lock:
        data = _load()
        f = next((x for x in data["flows"] if x["id"] == flow_id), None)
        if f is None:
            raise KeyError(flow_id)
        if name:
            f["group"] = name
        else:
            f.pop("group", None)
        _save(data)
        return f


def reorder(flow_ids: list[str]) -> list[str]:
    """Bring the flows into this order. Unknown ids drop out, flows not named keep their order and
    come after. Returns the new order."""
    with _lock:
        data = _load()
        by_id = {f["id"]: f for f in data["flows"]}
        wanted = [by_id[i] for i in dict.fromkeys(str(x) for x in flow_ids) if i in by_id]
        seen = {f["id"] for f in wanted}
        data["flows"] = wanted + [f for f in data["flows"] if f["id"] not in seen]
        _save(data)
        return [f["id"] for f in data["flows"]]


def delete(flow_id: str) -> None:
    """Delete a flow together with its run-time state."""
    with _lock:
        data = _load()
        data["flows"] = [f for f in data["flows"] if f["id"] != flow_id]
        data["state"] = {k: v for k, v in data["state"].items()
                         if not k.startswith(flow_id + "/") and k != "_flow:" + flow_id}
        _save(data)


# ---------------------------------------------------------------- Variablen & Laufzeitzustand
def clean_vars(v: Any) -> dict[str, Any]:
    """Check variables: name cleaned, value yes/no, text (limited) or a finite number – everything else drops out."""
    out = {}
    for k, x in list((v if isinstance(v, dict) else {}).items())[:MAX_VARS]:
        name = " ".join(str(k).split())[:40]
        if not name:
            continue
        if isinstance(x, bool):
            out[name] = x
        elif isinstance(x, str):
            out[name] = x[:MAX_TEXT]
        elif isinstance(x, (int, float)):
            try:
                value = float(x)
            except OverflowError:
                continue   # too large for a float: drops out
            if math.isfinite(value):          # NaN/Infinity are not valid JSON for the browser
                out[name] = round(value, 4)
    return out


def runtime() -> tuple[dict[str, Any], dict[str, Any]]:
    """Variables and run-time state as the engine loads them at the start."""
    with _lock:
        data = _load()
        return data["vars"], data["state"]


def save_runtime(vars_: dict[str, Any], state: dict[str, Any]) -> None:
    """Save variables and run-time state of the engine (flows stay untouched)."""
    with _lock:
        data = _load()
        data["vars"] = clean_vars(vars_)
        data["state"] = {str(k)[:90]: v for k, v in state.items() if isinstance(v, dict)}
        _save(data)


def reset() -> None:
    """Delete everything: flows, variables, state."""
    with _lock:
        _save(_empty())
