"""ComfyOS Flow – the nodes: what a trigger, a condition, a logic node or an action does.

Every node is a small function, registered with the engine by `@node(type, kind)` (flow_engine.py):

    state(ctx, n)         -> bool | None      state/condition; None = unknown
    poll(ctx, n)          -> [event, …]       triggers that are checked every round (~3 s) or on an instant message
    signal(ctx, n, port)  -> [outputs]        async; a signal arrives at input `port`
    push                                      triggers that are fired from outside (play, webhook …)

A node only knows `ctx` (flow_engine.Ctx): device state, memory, wait markers, the switching helpers of the core
(`ctx.hooks`). With a missing or nonsensical setting it never raises: queries return `None` or `[]`, actions
report the shortcoming as a `FlowError`.

The specification (ports, fields, texts) lives in static/flow_nodes.json. New node = entry there +
function here; `flow_engine.check_specs()` checks that both fit together.

Up to 0.19 this was in flow_engine.py. The engine loads this module at its end. What tests replace
(`flow_engine.rand`, `in_window`, `http_client`, `check_url`) the nodes read at run time through `fe.` –
never as a name of their own, otherwise the replacement would not take effect here.
"""
from __future__ import annotations

import math
import urllib.parse
from typing import Any, Callable

import httpx

from . import device_store, egress, events, flow_catalog, flow_engine as fe, flows_store, home_store, scenes_store
from .flow_engine import (
    ALL_DAYS, cfg, Ctx, days, dur_text, FlowError, fnum, HANDLERS, hm, LOOP_MAX_TIMES, LOOP_MIN_GAP, LOOP_WATCHDOG,
    moment, moment_text, node, parse_ts, show, SWITCH_KINDS, target_empty, truthy, _err_text, _ev, _target)


# ---------------------------------------------------------------- State triggers: "begins" / "ends"
def _edge(ctx: Ctx, n: dict, raw: bool | None, describe: Callable[[bool], dict]) -> list[dict]:
    """Change of the state of this node. The memory (mem["on"]) holds what was reported last; a change
    is reported exactly once – whoever sees it first (query round or Hue event) consumes it.
    hold_on / hold_off: the new state must hold this long without interruption before it is reported."""
    if raw is None:
        return []                             # unknown: change nothing, a running wait time stays
    st = ctx.mem(n)
    if "on" not in st:
        st["on"] = raw                        # first look (new, edited, switched on): remember, do not trigger
        ctx.touch()
        ctx.eng.emit(ctx.flow.id, ev="state", node=n["id"], on=raw)
        return []
    if raw == st["on"]:
        if "since" in st:                     # the change resolved itself before the wait time was up
            st.pop("since", None)
            st.pop("until", None)
            ctx.touch()
            ctx.eng.emit(ctx.flow.id, ev="idle", node=n["id"])
        return []
    delay = max(0.0, fnum(cfg(n, "hold_on" if raw else "hold_off"), 0))
    if delay > 0:
        if "since" not in st:
            st["since"], st["until"] = ctx.now, ctx.now + delay
            ctx.touch()
            ctx.eng.emit(ctx.flow.id, ev="wait", node=n["id"], until=st["until"])
        if ctx.now < st["until"]:
            return []
    st["on"] = raw
    st.pop("since", None)
    st.pop("until", None)
    ctx.touch()
    ctx.eng.emit(ctx.flow.id, ev="state", node=n["id"], on=raw)
    return [{"port": "out" if raw else "end", **describe(raw)}]


def _device_edges(ctx: Ctx, n: dict, devs: list[dict], pred: Callable[[dict], bool | None],
                  make: Callable[[dict], dict]) -> list[dict]:
    """Per device: the signal appears anew (false -> true). Seeing a device for the first time does not trigger."""
    mem = ctx.mem(n).setdefault("dev", {})
    out = []
    for d in devs:
        v = pred(d)
        if v is None:
            continue
        did = str(d["id"])
        old = mem.get(did)
        if old is None or old != v:
            mem[did] = v
            ctx.touch()
            if old is not None and v:
                out.append(make(d))
    if len(mem) > 600:                         # do not drag vanished devices along forever
        for did in [k for k in mem if k not in ctx.devs]:
            mem.pop(did, None)
    return out


# ---------------------------------------------------------------- Triggers & conditions
@node("presence", "state")
def _presence(ctx, n):
    """Condition "Anwesenheit": is the chosen state (home/away) set right now?"""
    return home_store.get().get("state") == cfg(n, "state", "AWAY")


@node("presence", "poll")
def _presence_poll(ctx, n):
    """Trigger "Anwesenheit": fires when the chosen state begins or ends."""
    name = "Zuhause" if cfg(n, "state", "AWAY") == "PRESENT" else "Abwesend"
    return _edge(ctx, n, _presence(ctx, n),
                 lambda on: {"trigger": f"Anwesenheit: {name}" + ("" if on else " endet"), "device": "", "value": name})


for _t in ("play", "webhook", "flow_error", "startup", "button"):
    HANDLERS.setdefault(_t, {})["push"] = lambda *a: None


def _origin(ctx, n):
    """Condition of the event nodes: was the running pass started by such a node?"""
    return ctx.event.get("origin") == n["type"]


for _t in ("play", "webhook", "flow_error", "button"):
    node(_t, "state")(_origin)


def _scene_name(sid: Any) -> str:
    """Name of a scene for texts ("Szene" if it no longer exists)."""
    sc = scenes_store.scene(str(sid)) if sid else None
    return sc["name"] if sc else "Szene"


def _scene_keys(n: dict) -> set[str]:
    """The rooms chosen in the node as target keys ("room:<id>"); empty = everywhere."""
    return {"room:" + str(r) for r in (cfg(n, "rooms") or [])}


@node("scene", "state")
def _scene(ctx, n):
    """Condition "Szene": is the chosen scene (or any) running in the chosen rooms (or anywhere)?"""
    want, keys = cfg(n, "scene"), _scene_keys(n)
    return any((k in keys if keys else True) and (not want or v == want) for k, v in scenes_store.active_map().items())


@node("scene", "poll")
def _scene_poll(ctx, n):
    """Trigger "Szene": compares the active scenes before and after the tick and fires at output `out` (activated)
    or `end` (ended) – at most once per output per tick."""
    a, b = ctx.tick.scenes
    if a is None or b is None or a == b:
        return []
    want, keys = cfg(n, "scene"), _scene_keys(n)
    out: dict[str, dict] = {}
    for k in sorted(set(a) | set(b)):
        if (keys and k not in keys) or a.get(k) == b.get(k):
            continue
        if b.get(k) and (not want or b[k] == want):
            out.setdefault("out", _ev("Szene aktiviert", value=_scene_name(b[k]), port="out"))
        if a.get(k) and (not want or a[k] == want):
            out.setdefault("end", _ev("Szene beendet", value=_scene_name(a[k]), port="end"))
    return list(out.values())


@node("time", "poll")
def _time_poll(ctx, n):
    """Trigger "Zeitpunkt": fires in the minute of the chosen time of day (sunrise/sunset with an offset too) on
    the chosen weekdays. All minutes due since the last tick are checked – none is lost."""
    on, at = days(cfg(n, "days", ALL_DAYS)), cfg(n, "at", "18:00")
    for minute, weekday, day in ctx.tick.due:
        if weekday in on and moment(at, ctx.eng.sun(day)) == minute:
            return [_ev(f"Zeitpunkt {moment_text(at)}")]
    return []


@node("time", "state")
def _time_state(ctx, n):
    """Condition "Zeitfenster": is now inside the window from … to … on one of the chosen weekdays?"""
    if ctx.weekday not in days(cfg(n, "days", ALL_DAYS)):
        return False
    sun = ctx.sun()
    return fe.in_window(ctx.minute, moment(cfg(n, "from", "18:00"), sun), moment(cfg(n, "to", "23:00"), sun))


def _signal(n: dict, d: dict) -> bool | None:
    """Does the device report the chosen signal – weak battery (below the threshold), device fault or alarm? None
    if the device reports nothing about it."""
    kind = cfg(n, "signal", "battery")
    if kind == "battery":
        if d.get("low_battery") is True:
            return True
        if d.get("battery") is None:
            return None if d.get("low_battery") is None else False
        return fnum(d["battery"], 100) < fnum(cfg(n, "below"), 15)
    if kind == "error":
        return bool(d["errors"]) if isinstance(d.get("errors"), list) else None
    return bool(d["alarm"]) if d.get("alarm") is not None else None


def _signal_ev(n: dict, d: dict) -> dict:
    """Event for the signal of a device, with the value for the placeholders (battery level, first fault text)."""
    kind = cfg(n, "signal", "battery")
    if kind == "battery":
        return _ev("Batterie schwach", d, f"{d['battery']} %" if d.get("battery") is not None else "niedrig")
    if kind == "error":
        return _ev("Gerätefehler", d, (d.get("errors") or [""])[0])
    return _ev("Alarm", d)


@node("device_signal", "poll")
def _signal_poll(ctx, n):
    """Trigger "Gerätemeldung": fires per device as soon as the signal appears anew (without a selection: all devices)."""
    return _device_edges(ctx, n, ctx.targets(n, empty_all=True), lambda d: _signal(n, d), lambda d: _signal_ev(n, d))


@node("device_signal", "state")
def _signal_state(ctx, n):
    """Condition "Gerätemeldung": is the signal present on at least one device? None if none reports anything about it."""
    vals = [v for v in (_signal(n, d) for d in ctx.targets(n, empty_all=True)) if v is not None]
    return any(vals) if vals else None


def _prop_text(prop: dict, v: Any) -> str:
    """Value of a property as text: the words of the property for yes/no, otherwise a number with its unit."""
    if prop["type"] == "bool":
        return prop["yes"] if v else prop["no"]
    return f"{fnum(v):g} {prop.get('unit', '')}".strip()


def _prop_truth(ctx: Ctx, n: dict, prop: dict, d: dict) -> bool | None:
    """Does a device meet the condition of the node for the chosen property (yes/no, above, below, exactly)? With a
    switching threshold a state that was met once stays until the value has moved back by the distance. None if the
    device does not report the value."""
    v = d.get(prop["id"])
    if v is None:
        return None
    if prop["type"] == "bool":
        return bool(v) == (cfg(n, "is", "yes") == "yes")
    x, thr, op = fnum(v), fnum(cfg(n, "value"), fnum(prop.get("default"), 0)), cfg(n, "op", "gt")
    if op == "eq":
        return abs(x - thr) < 0.05
    raw = x > thr if op == "gt" else x < thr
    band = max(0.0, fnum(cfg(n, "band"), 0))
    if band <= 0:
        return raw
    # switching threshold: once met, it stays so until the value has moved back by the distance
    mem = ctx.mem(n).setdefault("band", {})
    did = str(d["id"])
    res = raw or (bool(mem.get(did)) and (x > thr - band if op == "gt" else x < thr + band))
    if mem.get(did) != res:
        mem[did] = res
        ctx.touch()
    return res


@node("device_state", "state")
def _device_state(ctx, n):
    """Condition "Gerätestatus": does at least one chosen device (or all, depending on the setting) meet the
    condition? Remembers device and value for the placeholders. None if no device reports the value."""
    prop = flow_catalog.PROPS.get(str(cfg(n, "prop") or ""))
    if prop is None:
        return None
    hits, vals = [], []
    for d in ctx.targets(n):
        v = _prop_truth(ctx, n, prop, d)
        if v is None:
            continue
        vals.append(v)
        if v:
            hits.append(d)
        last = d
    if not vals:
        return None
    d = hits[0] if hits else last
    ctx.notes[n["id"]] = {"device": d.get("name", ""), "value": _prop_text(prop, d.get(prop["id"]))}
    return all(vals) if cfg(n, "match", "any") == "all" else any(vals)


def _device_state_text(n: dict) -> str:
    """The condition of the node as a short text, e.g. "Temperatur über 22 °C"."""
    prop = flow_catalog.PROPS.get(str(cfg(n, "prop") or "")) or {"label": "Gerätestatus", "type": "bool", "yes": "", "no": ""}
    if prop["type"] == "bool":
        return prop["yes"] if cfg(n, "is", "yes") == "yes" else prop["no"]
    op = {"gt": "über", "lt": "unter", "eq": "genau"}.get(cfg(n, "op", "gt"), "über")
    return f"{prop['label']} {op} {fnum(cfg(n, 'value'), fnum(prop.get('default'), 0)):g} {prop.get('unit', '')}".strip()


@node("device_state", "poll")
def _device_state_poll(ctx, n):
    """Trigger "Gerätestatus": fires when the condition becomes true or ends."""
    raw = _device_state(ctx, n)
    note = ctx.notes.get(n["id"], {})
    text = _device_state_text(n)
    return _edge(ctx, n, raw, lambda on: {"trigger": text if on else f"{text} endet", **note})


@node("interval", "poll")
def _interval(ctx, n):
    """Trigger "Intervall": fires every … seconds (at least 10), optionally only inside a time window. The first
    tick only sets the starting point."""
    every = max(10.0, fnum(cfg(n, "every"), 900))
    rt = ctx.rt(n)
    if not fe.in_window(ctx.minute, hm(cfg(n, "from")), hm(cfg(n, "to"))):
        return []
    if rt.get("last") is None:
        rt["last"] = ctx.now
        return []
    if ctx.now - rt["last"] >= every - 0.5:
        rt["last"] = ctx.now
        return [_ev("Intervall")]
    return []


@node("interval", "state")
def _interval_state(ctx, n):
    """Als Bedingung: Liegt jetzt im Zeitfenster des Intervalls?"""
    return fe.in_window(ctx.minute, hm(cfg(n, "from")), hm(cfg(n, "to")))


@node("startup", "state")
def _startup_state(ctx, n):
    """Condition "Start": has the chosen time passed since the start of ComfyOS?"""
    return ctx.now - ctx.eng.started >= max(0.0, fnum(cfg(n, "offset"), 0))


def _reach(n: dict, d: dict) -> bool | None:
    """Is the device in the chosen state (reachable or offline)? None if the reachability is unknown."""
    if d.get("online") is None:
        return None
    return bool(d["online"]) == (cfg(n, "state", "offline") == "online")


@node("reachable", "poll")
def _reach_poll(ctx, n):
    """Trigger "Erreichbarkeit": fires per device as soon as it goes offline or becomes reachable again."""
    text = "wieder erreichbar" if cfg(n, "state", "offline") == "online" else "offline"
    return _device_edges(ctx, n, ctx.targets(n, empty_all=True), lambda d: _reach(n, d), lambda d: _ev(text, d))


@node("reachable", "state")
def _reach_state(ctx, n):
    """Condition "Erreichbarkeit": is at least one device in the chosen state? None if nothing is known about
    any."""
    vals = [v for v in (_reach(n, d) for d in ctx.targets(n, empty_all=True)) if v is not None]
    return any(vals) if vals else None


@node("stale", "state")
def _stale(ctx, n):
    """Condition "Keine Meldung": has at least one chosen device not reported or not changed for the configured
    time?"""
    limit, vals = fnum(cfg(n, "for"), 3600), []
    for d in ctx.targets(n):
        ts = parse_ts(d.get("last_seen")) or ctx.eng.changed_at.get(str(d["id"]))
        if ts is not None:
            vals.append(ctx.now - ts >= limit)
    return any(vals) if vals else None


def _var_num(v: Any) -> float | None:
    """Variable as a number (yes/no as 1/0, comma or dot) or None."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        f = float(str(v).replace(",", "."))
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


@node("variable", "state")
def _variable(ctx, n):
    """Condition "Variable": on/off, greater/less/equal to a number, equal to a text or contains it. Remembers the
    value for the placeholders."""
    v, c = ctx.eng.var(" ".join(str(cfg(n, "name", "")).split())[:40]), cfg(n, "cmp", "on")
    ctx.notes[n["id"]] = {"device": "", "value": show(v)}
    if c in ("on", "off"):
        return truthy(v) == (c == "on")
    if c in ("gt", "lt", "eq"):
        x, thr = _var_num(v), fnum(cfg(n, "value"), 0)
        if x is None:
            return False
        return x > thr if c == "gt" else x < thr if c == "lt" else abs(x - thr) < 1e-9
    have, want = show(v).casefold(), str(cfg(n, "text", "") or "").casefold()
    return have == want if c == "text" else bool(want) and want in have


@node("variable", "poll")
def _variable_poll(ctx, n):
    """Trigger "Variable": fires when the condition becomes true or ends."""
    raw = _variable(ctx, n)
    name, note = cfg(n, "name", ""), ctx.notes.get(n["id"], {})
    return _edge(ctx, n, raw, lambda on: {"trigger": f"Variable {name}" + ("" if on else " endet"), **note})


# ---------------------------------------------------------------- Logik
@node("when", "signal")
async def _when(ctx, n, port):
    """Node "Wenn": checks the connected conditions (all, one or exactly one) and passes on to `yes` or
    `no`. Without conditions `yes` applies."""
    vals, m = ctx.bools(n, "c"), cfg(n, "match", "all")
    ok = True if not vals else all(vals) if m == "all" else any(vals) if m == "any" else sum(vals) == 1
    return ["yes"] if ok else ["no"]


@node("not", "state")
def _not(ctx, n):
    """Node "Nicht": inverts the connected condition."""
    vals = ctx.bools(n, "c")
    return (not vals[0]) if vals else None


@node("hold", "poll")
def _hold(ctx, n):
    """Node "Haltezeit": fires when all connected conditions hold without interruption for as long as
    configured."""
    vals = ctx.bools(n, "c")
    return _edge(ctx, n, all(vals) if vals else None,
                 lambda on: {"trigger": "Haltezeit erreicht" if on else "Haltezeit endet", "device": "", "value": ""})


@node("random", "signal")
async def _random(ctx, n, port):
    """Node "Zufall": continue with a probability, continue after a random wait, or at a random one of the
    connected outputs."""
    mode = cfg(n, "mode", "pick")
    if mode == "chance":
        return ["o1"] if fe.rand() * 100 < fnum(cfg(n, "p"), 50) else []
    if mode == "delay":
        lo, hi = sorted((fnum(cfg(n, "min"), 60), fnum(cfg(n, "max"), 900)))
        ctx.wait(n, ctx.eng.unique("r"), lo + fe.rand() * (hi - lo), "o1")
        return []
    ports = [p["id"] for p in flow_catalog.out_ports(n) if ctx.connected(n, p["id"])]
    return [ports[min(len(ports) - 1, int(fe.rand() * len(ports)))]] if ports else []


@node("rate", "signal")
async def _rate(ctx, n, port):
    """Node "Begrenzen": lets at most one signal through per period (throttle) or waits until there has been quiet
    for the configured time (debounce)."""
    wait = max(0.0, fnum(cfg(n, "wait"), 300))
    if cfg(n, "mode", "throttle") == "debounce":
        ctx.wait(n, "d", wait, "out")           # every new signal replaces the wait marker: only after quiet does it continue
        return []
    st = ctx.mem(n)
    if st.get("last") is not None and 0 <= ctx.now - st["last"] < wait:
        return []
    st["last"] = ctx.now
    ctx.touch()
    return ["out"]


@node("toggle", "signal")
async def _toggle(ctx, n, port):
    """Node "Wechsel": passes on alternately to output a and b and remembers whose turn it was last."""
    st = ctx.mem(n)
    st["last"] = "b" if st.get("last") == "a" else "a"
    ctx.touch()
    return [st["last"]]


@node("timer", "signal")
async def _timer(ctx, n, port):
    """Node "Zeitschalter": waits the configured time and then continues. A new signal restarts,
    extends, is ignored or runs in addition – depending on the mode; the input `cancel` aborts."""
    if port == "cancel":
        if ctx.cancel(n):
            ctx.log("Zeitschalter abgebrochen")
        return []
    wait, mode = max(0.0, fnum(cfg(n, "wait"), 300)), cfg(n, "mode", "restart")
    cur = ctx.waiting(n, "t")
    if mode == "each":
        ctx.wait(n, ctx.eng.unique("t"), wait, "out")
    elif cur is None or mode == "restart":
        ctx.wait(n, "t", wait, "out")
    elif mode == "extend":
        ctx.wait(n, "t", max(0.0, cur["until"] - ctx.now) + wait, "out")
    return []                                    # "ignore" with a running time: do nothing


@node("loop", "signal")
async def _loop(ctx, n, port):
    """Every round is a short pass of its own; in between lies a wait marker (no blocking, no
    time limit). The state (round, beginning) lives in the memory of the node and survives a restart."""
    st, rt = ctx.mem(n), ctx.rt(n)
    lp = st.get("loop")
    if port == "in":
        if lp and (rt.get("busy") or ctx.waiting(n, "next")):
            ctx.log("Solange läuft bereits – Signal ignoriert")
            return []
        lp = st["loop"] = {"round": 0, "since": ctx.now}
        ctx.touch()
    elif port != "@next" or not lp:
        return []
    mode = cfg(n, "mode", "while")
    total = int(max(1, min(LOOP_MAX_TIMES, fnum(cfg(n, "times"), 3)))) if mode == "times" else 0
    if mode == "times":
        go = lp["round"] < total
    else:
        vals = ctx.bools(n, "c")
        if not vals:
            st.pop("loop", None)
            ctx.touch()
            raise FlowError("Solange: keine Bedingung angeschlossen")
        go = all(vals)
    if go:
        lp["round"] += 1
        ctx.touch()
        if mode == "while" and ctx.now - lp.get("warned", lp["since"]) >= LOOP_WATCHDOG:
            lp["warned"] = ctx.now
            events.log("flow", f"{ctx.flow.name}: Solange läuft seit {dur_text(ctx.now - lp['since'])} "
                               f"({lp['round']} Runden)", {"flow": ctx.flow.name})
        before = ctx.event
        ctx.event = {**before, "index": lp["round"], "count": total or lp["round"]}
        rt["busy"] = True
        try:
            await ctx.run_branch(n, "loop")
        except BaseException:
            st.pop("loop", None)
            ctx.touch()
            raise
        finally:
            rt["busy"] = False
            ctx.event = before
        if mode == "while" or lp["round"] < total:
            gap = fnum(cfg(n, "gap"), 5)
            ctx.wait(n, "next", max(LOOP_MIN_GAP, gap) if mode == "while" else max(0.0, gap), "@next", enter=True)
            return []
    st.pop("loop", None)
    ctx.touch()
    ctx.eng.emit(ctx.flow.id, ev="idle", node=n["id"])
    ctx.log(f"Solange: nach {lp['round']} Runde{'' if lp['round'] == 1 else 'n'} beendet")
    return ["out"]


# ---------------------------------------------------------------- Aktionen
LOCKED = object()   # return value of _hook: device locked, command skipped


async def _hook(ctx, name: str, *args):
    """Run an action through core. A locked device does not abort the flow: the hint
    "… ist gesperrt" ends up in the event log, the flow keeps running (returns LOCKED)."""
    fn = ctx.hooks.get(name)
    if fn is None:
        raise FlowError("ComfyOS ist noch nicht bereit.")
    try:
        return await fn(*args)
    except device_store.DeviceLocked as exc:
        msg = str(exc.detail).split(" – ")[0]
        ctx.log(msg)
        events.log("flow", f"{ctx.flow.name}: {msg}", {"flow": ctx.flow.name})
        return LOCKED


async def _each(ctx, devs: list[dict], title: str, make: Callable[[dict], dict | None]) -> int:
    """Set every device individually. Single errors do not abort; if all fail, it is an error."""
    if not devs:
        raise FlowError(f"{title}: keine passenden Geräte gewählt")
    ok, errs = 0, []
    for d in devs:
        st = make(d)
        if st is None:
            continue
        try:
            if await _hook(ctx, "apply_device", d, st) is not LOCKED:
                ok += 1
                for k in ("on", "brightness", "playing"):   # state for following nodes in the same pass
                    if k in st:
                        d[k] = st[k]
        except FlowError:
            raise
        except Exception as exc:
            errs.append(f"{d.get('name')}: {_err_text(exc)}")
    if errs and not ok:
        raise FlowError(f"{title}: {errs[0]}")
    for e in errs[:3]:
        ctx.log(e)
    return ok


async def _do_switch(ctx, n):
    """Action "Schalten": on, off or toggle. Rooms and "Alle Lampen" mean lamps only – sockets are switched
    only by those who choose them explicitly."""
    t, to = _target(n), cfg(n, "to", "on")
    if target_empty(t):
        raise FlowError("Gerät steuern: kein Ziel gewählt")
    count = 0
    # Rooms and "Alle Lampen" mean lamps only – sockets are never switched along through a room.
    wide = ctx.targets(n, kinds=("light",), part="wide")
    picked = [d for d in ctx.targets(n, kinds=SWITCH_KINDS, part="picked")
              if not any(w["id"] == d["id"] for w in wide)]
    if (t.get("all") or t.get("rooms")) and to in ("on", "off"):
        rooms = None if t.get("all") else [str(r) for r in t.get("rooms") or []]
        r = await _hook(ctx, "apply_lights", to == "on", rooms)
        count += int((r or {}).get("switched", 0)) if r is not LOCKED else 0
        for d in wide:
            if not d.get("locked"):
                d["on"] = to == "on"
    else:
        picked = picked + wide
    if picked:
        count += await _each(ctx, picked, "Gerät steuern",
                             lambda d: {"on": (not bool(d.get("on"))) if to == "toggle" else to == "on"})
    elif not wide:
        raise FlowError("Gerät steuern: keine passenden Geräte gefunden")
    ctx.log(f"{count} Gerät{'' if count == 1 else 'e'} {'an' if to == 'on' else 'aus' if to == 'off' else 'umgeschaltet'}")


async def _do_light(ctx, n):
    """Action "Licht einstellen": brightness with white tone, colour or effect. A lamp without colour control is only
    switched on."""
    mode = cfg(n, "light_mode", "white")
    base = {"on": True, "brightness": max(1.0, min(100.0, fnum(cfg(n, "brightness"), 80)))}
    if mode == "white":
        base["kelvin"] = fnum(cfg(n, "kelvin"), 2700)
    elif mode == "color":
        base["color"] = str(cfg(n, "color") or "#ffffff")
    elif mode == "effect":
        base["effect"] = str(cfg(n, "effect") or "candle")

    def make(d):
        info = d.get("light")
        if not info:
            return {"on": True}             # lamp without colour control: only switch on
        st = dict(base)
        if "effect" in st and st["effect"] != "no_effect" and st["effect"] not in ((info.get("caps") or {}).get("effects") or []):
            st.pop("effect")
        return st
    ok = await _each(ctx, ctx.targets(n, kinds=("light",)), "Licht einstellen", make)
    ctx.log(f"Licht eingestellt ({ok})")


async def _do_dim(ctx, n):
    """Action "Heller / dunkler": change the brightness by one step. Lamps that cannot be dimmed go on with "brighter"
    and off with "fully dark"."""
    step = fnum(cfg(n, "step"), 20)

    def make(d):
        if not d.get("light"):              # not dimmable: brighter = on, fully dark = off
            if step > 0 and not d.get("on"):
                return {"on": True}
            return {"on": False} if step <= -100 and d.get("on") else None
        cur = float(d.get("brightness") or 0) if d.get("on") else 0.0
        if cur <= 0 and step <= 0:
            return None
        new = max(0.0, min(100.0, cur + step))
        return {"brightness": new} if new > 0 else {"on": False}
    ok = await _each(ctx, ctx.targets(n, kinds=("light",)), "Heller / dunkler", make)
    ctx.log(f"Helligkeit {'+' if step >= 0 else ''}{step:g} % ({ok})")


async def _do_temp(ctx, n):
    """Action "Temperatur": set the target temperature of the chosen thermostats or change it by an amount."""
    v, delta = fnum(cfg(n, "temp"), 18), cfg(n, "temp_mode", "abs") == "delta"
    ok = await _each(ctx, ctx.targets(n, kinds=("thermostat",)), "Temperatur",
                     lambda d: {"delta": v} if delta else {"target": v})
    ctx.log(f"Temperatur {'±' if delta else '→'} {v:g} °C ({ok})")


async def _do_cover(ctx, n):
    """Action "Rollladen": up, down, stop or move to a position."""
    move = cfg(n, "cover", "down")
    st = {"position": max(0.0, min(100.0, fnum(cfg(n, "position"), 50)))} if move == "position" else {"cover": move}
    ok = await _each(ctx, ctx.targets(n, kinds=("cover",)), "Rollladen", lambda d: dict(st))
    ctx.log(f"Rollladen {'auf ' + format(st['position'], 'g') + ' %' if move == 'position' else move} ({ok})")


_DEVICE_ACTIONS = {"switch": _do_switch, "light": _do_light, "dim": _do_dim, "temp": _do_temp, "cover": _do_cover}


@node("set_device", "signal")
async def _set_device(ctx, n, port):
    """Node "Gerät steuern": runs the chosen action (switch, light, dim, temperature, cover)."""
    fn = _DEVICE_ACTIONS.get(cfg(n, "do", "switch"))
    if fn is None:
        raise FlowError("Gerät steuern: unbekannte Aktion")
    await fn(ctx, n)
    return ["out"]


PLAYBACK_TEXT = {"play": "abspielen", "pause": "pausieren", "toggle": "umschalten", "next": "nächster Titel",
                 "previous": "vorheriger Titel"}


@node("playback", "signal")
async def _playback(ctx, n, port):
    """Node "Wiedergabe": make speakers play or pause, set the volume, start a favourite
    or change the track. Meant is every device of the kind speaker, no matter from which plugin."""
    do = cfg(n, "do", "play")
    if target_empty(_target(n)):
        raise FlowError("Wiedergabe: kein Ziel gewählt")
    if do == "volume":
        level = int(max(0.0, min(100.0, round(fnum(cfg(n, "volume"), 20)))))
        fixed, text = {"volume": level}, f"Lautstärke {level} %"
    elif do == "favorite":
        name = " ".join(str(cfg(n, "favorite") or "").split())[:120]
        if not name:
            raise FlowError("Wiedergabe: kein Favorit eingetragen")
        fixed, text = {"favorite": name}, f"Favorit „{name}“"
    elif do in ("next", "previous"):
        fixed, text = {do: True}, PLAYBACK_TEXT[do]
    elif do in ("play", "pause", "toggle"):
        fixed, text = None, PLAYBACK_TEXT[do]
    else:
        raise FlowError("Wiedergabe: unbekannte Aktion")

    def make(d):
        if fixed is not None:
            return dict(fixed)
        return {"playing": (not bool(d.get("playing"))) if do == "toggle" else do == "play"}
    ok = await _each(ctx, ctx.targets(n, kinds=("media",)), "Wiedergabe", make)
    ctx.log(f"Wiedergabe: {text} ({ok})")
    return ["out"]


@node("set_scene", "signal")
async def _set_scene(ctx, n, port):
    """Node "Szene": starts a scene in the chosen rooms, groups or on lamps, or ends scenes (without a
    selection everywhere)."""
    t = _target(n)
    rooms = [str(r) for r in t.get("rooms") or []]
    groups = [str(g) for g in t.get("groups") or []]
    devices = [str(d) for d in t.get("devices") or []]
    everywhere = target_empty(t) or bool(t.get("all"))
    if cfg(n, "do", "start") == "stop":
        keys = None if everywhere else ["room:" + r for r in rooms] + ["group:" + g for g in groups]
        r = await _hook(ctx, "stop_scene", keys, bool(cfg(n, "restore", True)))
        ctx.log(f"Szene beendet ({(r or {}).get('stopped', 0) if r is not LOCKED else 0})")
        return ["out"]
    sid = cfg(n, "scene")
    if not sid or not scenes_store.scene(str(sid)):
        raise FlowError("Szene: Szene wählen" if not sid else "Szene: die Szene gibt es nicht mehr")
    if everywhere:
        rooms = sorted({str(d["room_id"]) for d in ctx.devs.values() if d.get("kind") == "light" and d.get("room_id")})
        if not rooms:
            raise FlowError("Szene: keine Räume mit Lampen")
    total = 0
    calls = ([(rooms, None, None)] if rooms else []) + [(None, None, g) for g in groups] + ([(None, devices, None)] if devices else [])
    for r_, d_, g_ in calls:
        r = await _hook(ctx, "apply_scene", str(sid), r_, d_, g_)
        total += int((r or {}).get("switched", 0)) if r is not LOCKED else 0
    ctx.log(f"Szene {_scene_name(sid)} ({total} Lampen)")
    return ["out"]


@node("set_routine", "signal")
async def _set_routine(ctx, n, port):
    """Node "Heizroutine": sets the chosen routine as the active one."""
    key = cfg(n, "routine")
    if not key:
        raise FlowError("Heizroutine: keine Routine gewählt")
    await _hook(ctx, "apply_routine", str(key))
    ctx.log(f"Heizroutine {key}")
    return ["out"]


@node("set_presence", "signal")
async def _set_presence(ctx, n, port):
    """Node "Anwesenheit setzen": switches to at home or away."""
    state = cfg(n, "state", "AWAY")
    await _hook(ctx, "apply_home", state)
    ctx.log("Zuhause" if state == "PRESENT" else "Abwesend")
    return ["out"]


@node("notify", "signal")
async def _notify(ctx, n, port):
    """Node "Benachrichtigen": text (with placeholders) into the event log, optionally as a hint on all
    open pages and as a push message."""
    text = ctx.fmt(cfg(n, "text", "")).strip()[:300] or ctx.flow.name
    events.log("flow", text, {"flow": ctx.flow.name})
    if bool(cfg(n, "toast", True)) and ctx.hooks.get("publish"):
        ctx.hooks["publish"]({"type": "toast", "text": text})
    if bool(cfg(n, "push", False)) and ctx.hooks.get("push"):
        try:
            ctx.hooks["push"](ctx.flow.name, text)     # returns at once; sending runs in the background
        except Exception as exc:
            ctx.log(f"Push nicht möglich ({type(exc).__name__})")
    ctx.log(f"„{text}“")
    return ["out"]


@node("set_var", "signal")
async def _set_var(ctx, n, port):
    """Node "Variable setzen": set on, off, toggle, a text or a number, count up/down – also in the
    sliding time window."""
    eng = ctx.eng
    name = " ".join(str(cfg(n, "name", "")).split())[:40]
    if not name:
        raise FlowError("Variable setzen: kein Name")
    if name not in eng.vars and len(eng.vars) >= flows_store.MAX_VARS:
        raise FlowError("Variable setzen: zu viele Variablen")
    op, cur = str(cfg(n, "op", "on")), eng.var(name)
    if op in ("on", "off"):
        eng.set_var(name, op == "on")
    elif op == "toggle":
        eng.set_var(name, not truthy(cur))
    elif op == "text":
        eng.set_var(name, ctx.fmt(cfg(n, "text", ""))[:flows_store.MAX_TEXT])
    elif op == "add":
        step, window = fnum(cfg(n, "value"), 1), max(0.0, fnum(cfg(n, "window"), 0))
        if window > 0:
            eng.add_step(name, step, window)
        else:
            eng.set_var(name, round((_var_num(cur) or 0.0) + step, 4))
    else:
        eng.set_var(name, fnum(cfg(n, "value"), 0))
    ctx.vars_changed = True
    ctx.log(f"Variable {name} = {show(eng.var(name))}")
    return ["out"]


@node("inspect", "signal")
async def _inspect(ctx, n, port):
    """Node "Prüfpunkt": remembers the last event that passed (time, trigger, device, value) for the display in the
    editor and passes on."""
    st = ctx.mem(n)
    st.clear()
    st.update(at=ctx.now, trigger=ctx.event.get("trigger"), device=ctx.event.get("device"), value=ctx.event.get("value"))
    ctx.touch()
    return ["out"]


@node("set_flow", "signal")
async def _set_flow(ctx, n, port):
    """Node "Flow an/aus": switches another flow on or off."""
    fid, on = cfg(n, "flow"), cfg(n, "to", "off") == "on"
    if not fid:
        raise FlowError("Flow an/aus: kein Flow gewählt")
    target = flows_store.get(str(fid))
    if target is None:
        raise FlowError("Flow an/aus: den Flow gibt es nicht mehr")
    if flows_store.outdated(target):
        raise FlowError("Flow an/aus: der Flow ist veraltet")
    was = bool(target.get("enabled"))
    f = flows_store.set_enabled(str(fid), on)
    ctx.eng.reload()
    if on and not was:
        ctx.eng.flow_enabled(f["id"])
    ctx.log(f"Flow „{f['name']}“ {'an' if on else 'aus'}")
    return ["out"]


@node("lock", "signal")
async def _lock(ctx, n, port):
    """Node "Gerät sperren": locks or unlocks the chosen devices, optionally switching them on or
    off along the way."""
    devs = ctx.targets(n)
    if not devs:
        raise FlowError("Gerät sperren: keine Geräte gewählt")
    lock, sw = cfg(n, "do", "lock") == "lock", cfg(n, "switch", "keep")
    fn_lock, fn_switch = ctx.hooks.get("set_lock"), ctx.hooks.get("switch_locked")
    if fn_lock is None:
        raise FlowError("ComfyOS ist noch nicht bereit.")
    count = 0
    for d in devs:
        if sw in ("on", "off") and fn_switch and d.get("kind") in SWITCH_KINDS and d.get("control_item"):
            try:
                await fn_switch(d, {"on": sw == "on"})
                d["on"] = sw == "on"
            except Exception as exc:
                ctx.log(f"{d.get('name')}: {_err_text(exc)}")
        if await fn_lock(d, lock, ctx.flow.id):
            count += 1
    ctx.log(f"{count} Gerät{'' if count == 1 else 'e'} {'gesperrt' if lock else 'entsperrt'}")
    return ["out"]


@node("http", "signal")
async def _http(ctx, n, port):
    """Node "HTTP-Aufruf": calls an address with GET or POST (placeholders in address and body). Targets in the
    home network only with explicit permission in the node; the error text only names the kind of the error, never
    the address."""
    allow_local = bool(cfg(n, "allow_local", False))
    url = fe.check_url(ctx.fmt(cfg(n, "url", ""), quote=True), allow_local)
    method = "POST" if cfg(n, "method", "POST") == "POST" else "GET"
    body = ctx.fmt(cfg(n, "body", "")) if method == "POST" else None
    try:
        async with fe.http_client(allow_local) as c:
            r = await c.request(method, url, content=body.encode("utf-8") if body is not None else None,
                                headers={"Content-Type": "text/plain; charset=utf-8"} if body is not None else None)
    except egress.EgressBlocked:
        raise FlowError("HTTP-Aufruf: Adresse zeigt ins Heimnetz – im Baustein erst ausdrücklich erlauben")
    except httpx.HTTPError as exc:
        raise FlowError(f"HTTP-Aufruf fehlgeschlagen: {type(exc).__name__}")
    if r.status_code >= 400:
        raise FlowError(f"HTTP-Aufruf: Antwort {r.status_code}")
    ctx.log(f"{method} {urllib.parse.urlsplit(url).hostname} → {r.status_code}")
    return ["out"]
