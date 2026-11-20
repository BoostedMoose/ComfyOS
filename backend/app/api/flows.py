"""ComfyOS Flow: automations, variables, webhook, play and test."""
from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import core, demo, entitlements, events, flow_catalog, flow_engine, flow_import, flows_store, security

router = APIRouter()
OUTDATED = ("Diese Automatisierung stammt aus einer älteren ComfyOS-Version und kann nicht mehr genutzt werden – "
            "bitte neu bauen und die alte löschen.")


class FlowName(BaseModel):
    """Neuer Name eines Flows."""
    name: str | None = Field(default=None, max_length=200)


class FlowGraph(BaseModel):
    """Graph of a flow: name, description, nodes and connections."""
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    nodes: list = Field(default_factory=list, max_length=flows_store.MAX_NODES)
    edges: list = Field(default_factory=list, max_length=flows_store.MAX_EDGES)


class FlowEnabled(BaseModel):
    """Switch a flow on or off."""
    enabled: bool


class FlowGroup(BaseModel):
    """Group of a flow (empty = without a group)."""
    group: str = Field(default="", max_length=200)


class FlowOrder(BaseModel):
    """Order of the flows in the list (ids from top to bottom)."""
    ids: list[str] = Field(max_length=flows_store.MAX_FLOWS)


class FlowFire(BaseModel):
    """Test run: which output (`port`) of the trigger should fire."""
    port: str | None = Field(default=None, max_length=40)


def _engine() -> flow_engine.Engine:
    """The flow engine – loaded if it is not yet (never from the demo: it runs the flows of the real home only)."""
    eng = flow_engine.engine
    if not eng.loaded and not demo.active():
        eng.reload()
    return eng


def _flow_out(f: dict, full: bool = False) -> dict:
    """Flow for the interface: with status (`info`), reason for a pause, live values of the nodes and the mark
    "outdated". `full` adds memory and last run for the editor. An outdated flow comes without its graph."""
    eng = flow_engine.engine
    old = flows_store.outdated(f)
    out = {**f, "info": eng.info(f["id"]), "paused": eng.paused.get(f["id"]), "outdated": old,
           "live": {} if old else eng.live(f["id"])}
    if old:   # outdated: only name and hint any more – the graph fits no node any longer
        out["nodes"], out["edges"] = [], []
    if full and not old:
        out["state"] = {k.split("/", 1)[1]: v for k, v in eng.state.items() if k.startswith(f["id"] + "/")}
        out["last"] = eng.last_runs.get(f["id"])
    return out


def _runnable_or_402(flow_id: str) -> None:
    """Raises "Pro required" (402) if the licence has paused the flow – with the reason in the text."""
    reason = flow_engine.engine.paused.get(flow_id)
    if reason:
        raise entitlements.ProRequired("flow_pro_nodes" if reason == "pro" else "flows_active",
                                       "Diese Automatisierung ist pausiert: " + (
                                           "sie enthält Pro-Bausteine." if reason == "pro" else "über dem Limit aktiver Flows."))


def _flow_or_404(flow_id: str, usable: bool = False) -> dict:
    """Flow by its id, or 404. `usable`: an outdated flow gives 409 because it can no longer be edited
    or started."""
    f = flows_store.get(flow_id)
    if f is None:
        raise HTTPException(404, "Automatisierung nicht gefunden")
    if usable and flows_store.outdated(f):
        raise HTTPException(409, OUTDATED)
    return f


def _graph(nodes: list, edges: list) -> tuple[list, list]:
    """Limit the shape, reject unknown nodes, check lines against the catalogue (roles, n+1)."""
    nodes, edges = flows_store.clean_graph(nodes, edges)
    unknown = flow_catalog.unknown_types(nodes)
    if unknown:
        raise HTTPException(400, "Unbekannter Baustein – bitte die Seite neu laden.")
    edges, _ = flow_catalog.normalize(nodes, edges)
    return nodes, edges


@router.get("/api/flows")
async def flows_list():
    """All flows with status, plus variables, problems of the engine and number and limit of active flows."""
    eng = _engine()
    flows = flows_store.all_flows()
    return {"flows": [_flow_out(f) for f in flows], "vars": {} if demo.active() else eng.all_vars(), "problems": eng.problems,
            "active": sum(1 for f in flows if f.get("enabled") and f["id"] not in eng.paused and f["id"] not in eng.outdated),
            "limit": entitlements.plan_limit("flows_active")}


@router.get("/api/flows/vars")
async def flows_vars():
    """All variables of the flows with their values (none in the demo – they belong to the real flow engine)."""
    return {} if demo.active() else _engine().all_vars()


@router.put("/api/flows/vars")
async def flows_vars_set(values: dict):
    """Set variables (checked by the engine); returns the new state."""
    return _engine().set_vars(values)


@router.post("/api/flows/hook/{token}")
async def flows_hook(token: str, request: Request):
    """Webhook trigger: the only flow endpoint without login; protected by the token of the node."""
    if not security.rate_ok("hook:" + core.client_ip(request), 30, 60):
        raise HTTPException(429, "Zu viele Anfragen.")
    eng = _engine()
    hit = None
    for f in eng.enabled():
        for n in f.nodes.values():
            tok = (n.get("config") or {}).get("token")
            if (n["type"] == "webhook" and f.roles.get(n["id"]) == "trigger" and isinstance(tok, str)
                    and hmac.compare_digest(tok.encode(), token.encode())):
                hit = (f.id, n["id"])
    if hit is None:
        raise HTTPException(404, "Nicht gefunden")
    body = (await request.body())[:200].decode("utf-8", "replace").strip()
    await eng.fire(hit[0], hit[1], "out", {"trigger": "Webhook", "value": body, "origin": "webhook"}, wait=True)
    return {"ok": True}


@router.post("/api/flows")
async def flows_create(body: FlowGraph):
    """Create a new flow. The graph is checked (catalogue) and held against the licence (Pro nodes)."""
    nodes, edges = _graph(body.nodes, body.edges)
    entitlements.check_flow([], nodes)
    try:
        f = flows_store.create(body.name or "", nodes, edges, description=body.description)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    flow_engine.engine.reload()
    return _flow_out(f, True)


@router.get("/api/flows/import")
async def flows_import_info():
    """Node catalogue, devices with their properties and existing flow names – for the AI template."""
    devices = []
    try:
        devices = [{"id": d.get("id"), "name": d.get("name"), "kind": d.get("kind"), "room": d.get("room"),
                    "room_id": d.get("room_id"), "props": flow_catalog.capabilities(d)}
                   for d in await core.all_devices() if d.get("id")]
    except Exception:
        pass   # devices cannot be fetched right now: the template comes without a device list
    return {**flow_catalog.summary(), "devices": devices,
            "flows": [f["name"] for f in flows_store.all_flows() if not flows_store.outdated(f)]}


@router.post("/api/flows/import", dependencies=[entitlements.require_feature("flow_ai_import")])
async def flows_import(request: Request, apply: bool = False):
    """Check JSON flows (apply=false) or take them over (apply=true)."""
    raw = await request.body()
    if len(raw) > 256 * 1024:
        raise HTTPException(413, "Datei zu groß (max. 256 KB).")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Keine gültige JSON-Datei.")
    except RecursionError:
        raise HTTPException(400, "Keine gültige JSON-Datei: zu tief verschachtelt.")
    existing = {f["id"]: f["name"] for f in flows_store.all_flows() if not flows_store.outdated(f)}
    try:
        result = flow_import.plan(doc, existing)
    except flow_import.ImportError_ as exc:
        raise HTTPException(400, str(exc))
    if not apply:
        for e in result["flows"]:
            e.pop("nodes", None)
            e.pop("edges", None)
        return result
    if not result["ok"]:
        raise HTTPException(400, "Die Datei enthält Fehler – bitte zuerst prüfen.")
    done, switched = [], []
    for e in result["flows"]:
        if not e.get("nodes"):
            continue
        nodes, edges = e["nodes"], e["edges"]
        fid = e["flow_id"]
        old = flows_store.get(fid) if fid else None
        entitlements.check_flow((old or {}).get("nodes") or [], nodes)
        if old:
            flows_store.save(fid, e["name"], nodes, edges, description=e["description"] or None)
            action = "update"
        else:
            try:
                fid = flows_store.create(e["name"], nodes, edges, enabled=False, description=e["description"] or None)["id"]
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            action = "create"
        was = bool(old and old.get("enabled"))
        if e["enabled"] and not was:   # switch on only if the plan allows it – otherwise the flow stays off
            try:
                entitlements.check_enable(flows_store.get(fid), [f for f in flows_store.all_flows() if not flows_store.outdated(f)])
                flows_store.set_enabled(fid, True)
                switched.append(fid)
            except entitlements.ProRequired:
                pass   # the plan does not allow it: the flow stays off
        elif was and not e["enabled"]:
            flows_store.set_enabled(fid, False)
        done.append({"name": e["name"], "action": action, "nodes": e["node_count"]})
    flow_engine.engine.reload()
    for fid in switched:
        flow_engine.engine.flow_enabled(fid)
    events.log("flow", f"Flows importiert: {', '.join(d['name'] for d in done)}")
    return {"ok": True, "done": done}


@router.get("/api/flows/export")
async def flows_export():
    """All flows in the import format – for a backup or for handing over to an AI."""
    return flow_import.export_flows(flows_store.all_flows())


@router.put("/api/flows/order")
async def flows_order(body: FlowOrder):
    """Save the order of the flows (arranging in the list, since 0.21.1)."""
    return {"ids": flows_store.reorder([i[:40] for i in body.ids])}


@router.get("/api/flows/{flow_id}")
async def flows_get(flow_id: str):
    """One flow with graph, memory and last run (for the editor)."""
    _engine()
    return _flow_out(_flow_or_404(flow_id), True)


@router.put("/api/flows/{flow_id}")
async def flows_save(flow_id: str, body: FlowGraph):
    """Save the graph of a flow; new Pro nodes need Pro. The engine reloads afterwards."""
    old = _flow_or_404(flow_id, usable=True)
    nodes, edges = _graph(body.nodes, body.edges)
    entitlements.check_flow(old.get("nodes") or [], nodes)
    f = flows_store.save(flow_id, body.name, nodes, edges, description=body.description)
    flow_engine.engine.reload()
    return _flow_out(f, True)


@router.put("/api/flows/{flow_id}/enabled")
async def flows_enabled(flow_id: str, body: FlowEnabled):
    """Switch a flow on or off. Switching on checks the limit of active flows and reports the start to the engine
    (trigger "Flow eingeschaltet")."""
    cur = _flow_or_404(flow_id, usable=body.enabled)
    was = cur.get("enabled")
    if body.enabled and not was:
        entitlements.check_enable(cur, [f for f in flows_store.all_flows() if not flows_store.outdated(f)])
    f = flows_store.set_enabled(flow_id, body.enabled)
    eng = flow_engine.engine
    eng.reload()
    if bool(was) != body.enabled:
        events.log("flow", f"„{f['name']}“ {'eingeschaltet' if body.enabled else 'ausgeschaltet'}")
    if body.enabled and not was:
        eng.flow_enabled(flow_id)
    return _flow_out(f)


@router.put("/api/flows/{flow_id}/name")
async def flows_rename(flow_id: str, body: FlowName):
    """Flow umbenennen."""
    _flow_or_404(flow_id, usable=True)
    f = flows_store.rename(flow_id, body.name)
    flow_engine.engine.reload()
    return _flow_out(f)


@router.put("/api/flows/{flow_id}/group")
async def flows_group(flow_id: str, body: FlowGroup):
    """Assign a flow to a group or take it out of one (outdated flows can be arranged too)."""
    _flow_or_404(flow_id)
    return _flow_out(flows_store.set_group(flow_id, body.group))


@router.post("/api/flows/{flow_id}/duplicate")
async def flows_duplicate(flow_id: str):
    """Copy a flow – the copy is switched off."""
    entitlements.check_flow([], _flow_or_404(flow_id, usable=True).get("nodes") or [])
    try:
        f = flows_store.duplicate(flow_id)
    except KeyError:
        raise HTTPException(404, "Automatisierung nicht gefunden")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    flow_engine.engine.reload()
    return _flow_out(f)


@router.delete("/api/flows/{flow_id}")
async def flows_delete(flow_id: str):
    """Delete a flow together with its run-time state."""
    _flow_or_404(flow_id)
    flows_store.delete(flow_id)
    flow_engine.engine.reload()
    return {"ok": True}


def _result(eng: flow_engine.Engine, flow_id: str, trace: list[str]) -> dict:
    """Response of a test run: trace (`trace`), status and live values."""
    return {"trace": trace, "info": eng.info(flow_id), "live": eng.live(flow_id)}


@router.post("/api/flows/{flow_id}/run")
async def flows_run(flow_id: str):
    """Test run: start every trigger once – conditions apply, actions really run."""
    _flow_or_404(flow_id, usable=True)
    eng = flow_engine.engine
    eng.reload()
    _runnable_or_402(flow_id)
    return _result(eng, flow_id, await eng.run_manual(flow_id))


@router.post("/api/flows/{flow_id}/fire/{node_id}")
async def flows_fire(flow_id: str, node_id: str, body: FlowFire | None = None):
    """Test in the editor: start a single trigger (also with the flow switched off), optionally a
    certain output – e.g. "ends" of a state trigger."""
    _flow_or_404(flow_id, usable=True)
    eng = flow_engine.engine
    eng.reload()
    _runnable_or_402(flow_id)
    try:
        ctx = await eng.fire_node(flow_id, node_id, body.port if body else None)
    except KeyError:
        raise HTTPException(404, "Auslöser nicht gefunden")
    return _result(eng, flow_id, ctx.trace if ctx else [])


@router.post("/api/flows/{flow_id}/play/{node_id}")
async def flows_play(flow_id: str, node_id: str):
    """Play button: starts the play trigger of an enabled flow."""
    f = _flow_or_404(flow_id, usable=True)
    n = next((x for x in f["nodes"] if x["id"] == node_id and x["type"] == "play"
              and flow_catalog.role(x) == "trigger"), None)
    if n is None:
        raise HTTPException(404, "Play-Knopf nicht gefunden")
    if not f.get("enabled"):
        raise HTTPException(409, "Diese Automatisierung ist ausgeschaltet.")
    eng = _engine()
    if flow_id not in eng.flows:
        eng.reload()
    _runnable_or_402(flow_id)
    try:
        ctx = await eng.fire_node(flow_id, node_id, "out", manual=False)
    except KeyError:
        raise HTTPException(404, "Play-Knopf nicht gefunden")
    return _result(eng, flow_id, ctx.trace if ctx else [])
