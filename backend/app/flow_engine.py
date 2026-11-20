"""ComfyOS Flow – run time of the automations (runs in ComfyOS, not on the Home Base).

Model (format 3)
* A node describes one idea. Nodes with roles are, depending on their setting,
  - a trigger: start a pass (signal outputs), or
  - a condition: say yes/no and attach to "Wenn / Dann", "Solange", "Haltezeit" or "Nicht".
* State triggers (presence, device state, variable, hold time) have two outputs: "begins" and "ends".
  The engine remembers per node whether the state holds right now and reports only the change. The change is
  computed from the *current state* – never from comparing two queries. Therefore none is lost and none
  comes twice, no matter whether a Hue event arrives in the middle of a query round. The memory lives in
  flows.json and survives a restart: if the light was on and the motion has ended meanwhile, "ends" still
  comes after the start. Unknown (the device reports nothing) never changes anything.
* Signals are pushed: per pass every (node, input) combination is reached at most once.
* Yes/no values are pulled: they are checked against the current state when the signal arrives.
* Nothing blocks: timers, delays and "Solange" never wait inside a pass. They leave
  a wait marker (Engine.wait) – a task of its own continues later. Wait markers are stored in flows.json and
  continue after a restart. That is why "Solange" has no time limit: every round is a short pass of its
  own, in between the flow is free.
* A flow never runs in parallel with itself (lock per flow); triggers that arrive meanwhile wait.
* Visible: every step goes to open pages (editor, flow list) as an SSE message {"type": "flow", …}.

Extending: the specification (ports, fields, texts) lives once in static/flow_nodes.json (flow_catalog.py). Register
here per type: @node(type, kind) with
    state(ctx, n)         -> bool | None      state/condition; None = unknown
    poll(ctx, n)          -> [event, ...]     triggers that are checked every round (~3 s) or on a Hue push
    signal(ctx, n, port)  -> [outputs]        async; a signal arrives at input 'port'
    push                                       triggers that are fired from outside (play, webhook …)
check_specs() (start + test) makes sure that catalogue and registry match.
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
import urllib.parse
from datetime import date, datetime
from typing import Any, Callable

import httpx

from . import demo, egress, entitlements, events, floorplan_store, flow_catalog, flows_store, scenes_store, tasks, weather

TICK = 3.0                    # Sekunden je Runde
FRESH = 5.0                   # the device state may be at most this old when triggering
QUEUE_MAX = 50                # this many triggers wait per flow (flood protection; above that they are dropped with a log entry)
CATCH_UP_MINUTES = 10         # catch up time triggers at most this far when a round hung
FALLBACK_GEO = (51.16, 10.45)  # centre of Germany, if there is no address in the floor plan
FALLBACK_SUN = (7 * 60, 19 * 60)   # Polartag/-nacht: feste Zeiten statt keiner
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
DEVICE_FIELDS = tuple(flow_catalog.PROPS) + ("online",)
SWITCH_KINDS = ("light", "switch")
MAX_STEPS = 400               # Schritte je Durchgang (Schutz vor Endlosketten)
LOOP_MIN_GAP = 1.0            # "Solange": minimum distance between two rounds
LOOP_MAX_TIMES = 100          # feste Anzahl: Obergrenze
LOOP_WATCHDOG = 3600.0        # "Solange" runs this long in one go: hint in the event log (then hourly)
VAR_HOPS = 4                  # this often a variable change may trigger further variable triggers at once
WINDOW_STEPS = 500            # steps per counting window
GROUPS_TTL = 10.0
PRESS_TEXT = {"short": "kurz", "long": "lang", "double": "doppelt"}   # kinds of a button press (plugin_api.PRESS_KINDS)
# Memory that is discarded when a flow is edited or switched off/on (no triggering from an old state)
TRANSIENT = ("on", "since", "until", "dev", "band", "loop")

# replaceable for tests
clock: Callable[[], float] = time.time
rand: Callable[[], float] = random.random
_sleep = asyncio.sleep


def http_client(allow_local: bool = False) -> httpx.AsyncClient:
    """Targets in the home network are blocked without allow_local – through DNS names too (GuardedTransport)."""
    return httpx.AsyncClient(transport=egress.GuardedTransport(allow_local), timeout=httpx.Timeout(8.0, connect=4.0),
                             headers={"User-Agent": "ComfyOS-Flow"}, follow_redirects=False)


HANDLERS: dict[str, dict[str, Callable]] = {}


class FlowError(Exception):
    """Error in a flow that the user can fix – the text is shown like this in the flow and in the event log."""
    pass


def node(type_: str, kind: str):
    """Decorator: register a function as the logic of a node type. `kind` is the role of the function – `poll`
    (trigger, asked every tick), `signal` (a signal arrives), `state` (condition: does it hold right now?)."""
    def deco(fn):
        HANDLERS.setdefault(type_, {})[kind] = fn
        return fn
    return deco


# ---------------------------------------------------------------- Hilfen
cfg = flow_catalog.value


def label(n: dict) -> str:
    """Display name of a node from the catalogue (otherwise its type)."""
    return (flow_catalog.spec(n.get("type")) or {}).get("label", str(n.get("type")))


def fnum(v: Any, default: float = 0.0) -> float:
    """Value as a finite number; `default` for everything else (NaN and Infinity too)."""
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def hm(v: Any) -> int | None:
    """Time of day "HH:MM" as minutes since midnight, or None if it is invalid."""
    try:
        h, m = str(v).split(":")[:2]
        h, m = int(h), int(m)
        return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None
    except (ValueError, AttributeError):
        return None


_MOMENT = re.compile(r"^(sunrise|sunset)([+-]\d{1,3})?$")


def moment(v: Any, sun: tuple[int, int] | None) -> int | None:
    """"18:00", "sunrise", "sunset+15", "sunrise-30" -> minute of the day."""
    s = str(v or "").strip()
    m = _MOMENT.match(s)
    if not m:
        return hm(s)
    rise, sset = sun or FALLBACK_SUN
    return ((rise if m.group(1) == "sunrise" else sset) + int(m.group(2) or 0)) % 1440


def moment_text(v: Any) -> str:
    """Point in time readable for the user: "Sonnenaufgang +15 Min." instead of "sunrise+15"; a time of day stays as it is."""
    m = _MOMENT.match(str(v or "").strip())
    if not m:
        return str(v or "")
    off = int(m.group(2) or 0)
    return ("Sonnenaufgang" if m.group(1) == "sunrise" else "Sonnenuntergang") + (f" {off:+d} Min." if off else "")


def days(v: Any) -> set[int]:
    """Weekdays from a setting as a set 0…6 (0 = Monday); without a list all days."""
    return {int(x) for x in (v if isinstance(v, list) else ALL_DAYS) if str(x).lstrip("-").isdigit() and 0 <= int(x) <= 6}


def in_window(m: int, a: int | None, b: int | None) -> bool:
    """Is minute `m` inside the time window a…b? The window may cross midnight; without bounds or with
    equal bounds it always holds."""
    if a is None or b is None or a == b:
        return True
    return a <= m < b if a < b else (m >= a or m < b)


def dur_text(s: float) -> str:
    """Duration in seconds as a short text ("5 Min.", "2 Std.", "1 Min. 30 s")."""
    s = int(round(s))
    if s and s % 3600 == 0:
        return f"{s // 3600} Std."
    if s and s % 60 == 0:
        return f"{s // 60} Min."
    return f"{s} s" if s < 60 else f"{s // 60} Min. {s % 60} s"


def parse_ts(v: Any) -> float | None:
    """Read a time stamp: Unix time in seconds or milliseconds or an ISO date → seconds; None if there is nothing
    readable."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e11 else float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        f = fnum(v, 0)
        return (f / 1000 if f > 1e11 else f) or None


def sun_times(lat: float, lon: float, day: date, tz_minutes: float | None = None) -> tuple[int, int] | None:
    """Sunrise/sunset in minutes after midnight (local time), NOAA approximation. None = polar day/night."""
    if tz_minutes is None:
        off = datetime(day.year, day.month, day.day, 12).astimezone().utcoffset()
        tz_minutes = off.total_seconds() / 60 if off else 0
    g = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    eq = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                   - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    dec = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
           + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    x = math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(dec)) - math.tan(la) * math.tan(dec)
    if not -1 <= x <= 1:
        return None
    ha = math.degrees(math.acos(x))
    rise = 720 - 4 * (lon + ha) - eq + tz_minutes
    sset = 720 - 4 * (lon - ha) - eq + tz_minutes
    return int(round(rise)) % 1440, int(round(sset)) % 1440


def check_specs() -> list[str]:
    """Parity and shape check: catalogue <-> registered logic."""
    probs = flow_catalog.check()
    for t, sp in flow_catalog.SPECS.items():
        h = HANDLERS.get(t) or {}
        rl = flow_catalog.roles(sp)
        if "trigger" in rl and not ("poll" in h or "push" in h):
            probs.append(f"{t}: Auslöser ohne Engine-Logik")
        if "condition" in rl and "state" not in h:
            probs.append(f"{t}: Bedingung ohne Engine-Logik")
        if not rl:
            if sp.get("inputs") and any(p["kind"] == "signal" for p in sp["inputs"]) and "signal" not in h:
                probs.append(f"{t}: keine Engine-Logik für Signale")
            if any(p["kind"] == "bool" for p in sp.get("outputs", [])) and "state" not in h:
                probs.append(f"{t}: Ja/Nein-Ausgang ohne Engine-Logik")
            if not h:
                probs.append(f"{t}: keine Engine-Logik registriert")
    for t in HANDLERS:
        if t not in flow_catalog.SPECS:
            probs.append(f"{t}: Engine-Logik ohne Spezifikation")
    return probs


def _err_text(exc: BaseException) -> str:
    """Error text for flow and event log: the text of the error (for HTTP errors their `detail`), at most 300 characters."""
    d = getattr(exc, "detail", None)
    return str(d or exc or type(exc).__name__)[:300]


def _target(n: dict, key: str = "target") -> dict:
    """Device selection of a node ({devices, groups, rooms, all}) – always an object."""
    t = cfg(n, key)
    return t if isinstance(t, dict) else {}


def target_empty(t: dict) -> bool:
    """Is nothing chosen in the device selection?"""
    return not (t.get("devices") or t.get("groups") or t.get("rooms") or t.get("all"))


def truthy(v: Any) -> bool:
    """Read a value as yes/no: texts such as "nein", "aus", "0", "false" and the empty text count as no."""
    if isinstance(v, str):
        return v.strip().casefold() not in ("", "0", "nein", "aus", "false", "off")
    return bool(v)


def show(v: Any) -> str:
    """Value for texts and event log: Ja/Nein, numbers without superfluous zeros, otherwise the text."""
    if isinstance(v, bool):
        return "Ja" if v else "Nein"
    if isinstance(v, (int, float)):
        return f"{v:g}"
    return "" if v is None else str(v)


def check_url(url: str, allow_local: bool) -> str:
    """Check the address of an HTTP call: only http/https; targets in the home network only if the node explicitly
    allows them. Raises FlowError with the reason."""
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FlowError("HTTP-Aufruf: Adresse muss mit http:// oder https:// beginnen")
    if egress.is_local_host(u.hostname) and not allow_local:
        raise FlowError("HTTP-Aufruf: Adresse im Heimnetz – im Baustein erst ausdrücklich erlauben")
    return url.strip()


# ---------------------------------------------------------------- Flow (kompiliert)
class Flow:
    """A loaded flow in the form the engine works with: nodes by id, connections by output and
    input, roles of the nodes."""
    def __init__(self, raw: dict):
        """Build from the stored flow. `sig` is the fingerprint of the logic – by it the engine recognises whether
        a flow was really rebuilt or only moved."""
        self.id, self.name, self.enabled = raw["id"], raw.get("name", ""), bool(raw.get("enabled"))
        self.nodes = {n["id"]: n for n in raw.get("nodes", [])}
        # Fingerprint of the logic (without position and name): only when it changes does the flow count as rebuilt –
        # a moved node does not cancel a running timer.
        self.sig = json.dumps([sorted(([n["id"], n["type"], n.get("config") or {}] for n in self.nodes.values()), key=lambda x: x[0]),
                               sorted([e["from"], e["fromPort"], e["to"], e["toPort"]] for e in raw.get("edges", []))],
                              sort_keys=True, ensure_ascii=False, default=str)
        self.roles = {nid: flow_catalog.role(n) for nid, n in self.nodes.items()}
        self.out: dict[tuple, list] = {}
        self.inc: dict[str, list] = {}
        for e in raw.get("edges", []):
            if e["from"] in self.nodes and e["to"] in self.nodes:
                self.out.setdefault((e["from"], e["fromPort"]), []).append(e)
                self.inc.setdefault(e["to"], []).append(e)
        self.needs_devices = any(f.get("widget") == "target" for n in self.nodes.values()
                                 for f in (flow_catalog.spec(n["type"]) or {}).get("fields", []))

    def is_trigger(self, n: dict) -> bool:
        """Node that starts by itself: trigger role, or logic with a query of its own (hold time)."""
        r = self.roles.get(n["id"])
        return r == "trigger" or (r is None and "poll" in HANDLERS.get(n["type"], {}))


class Tick:
    """What only a query round knows: minutes that have begun and the change of the active scenes."""

    def __init__(self, due: list[tuple[int, int, date]] | None = None, scenes: tuple = (None, None)):
        """`due`: minutes that became due since the last tick (minute, weekday, date). `scenes`: active scenes
        before and now."""
        self.due, self.scenes = list(due or []), scenes


class Ctx:
    """Everything a node needs – without knowledge of loop, storage or other nodes."""

    def __init__(self, eng: "Engine", flow: Flow, event: dict | None = None, tick: Tick | None = None):
        """Context for one pass through a flow. The time is fixed here once – all nodes of one
        pass see the same."""
        self.eng, self.flow, self.event = eng, flow, dict(event or {})
        self.tick = tick or Tick()
        self.now = clock()
        self.dt = datetime.fromtimestamp(self.now)
        self.minute = self.dt.hour * 60 + self.dt.minute
        self.weekday = self.dt.weekday()
        self.trace: list[str] = []
        self.path: list[list] = []
        self.notes: dict[str, dict] = {}     # node id -> {"device", "value"} of the last state check
        self.run: int | None = None          # set while a pass is running (then live steps go out)
        self.depth = 0
        self.steps = 0
        self.vars_changed = False

    @property
    def hooks(self) -> dict:
        """The connections of the engine to the core (fetch devices, switch, start scenes …)."""
        return self.eng.hooks

    @property
    def devs(self) -> dict[str, dict]:
        """The freshest device state {id: row}; empty as long as none has been fetched."""
        return self.eng.cur or {}

    def dev(self, did: Any) -> dict | None:
        """A device by its id, or None."""
        return self.devs.get(str(did)) if did else None

    def targets(self, n: dict, kinds: tuple | None = None, wide_kinds: tuple | None = None, part: str | None = None,
                empty_all: bool = False, key: str = "target") -> list[dict]:
        """Devices of the target selection. kinds: allowed kinds for individually chosen devices and group members;
        wide_kinds: kinds that rooms and "all" mean (default = kinds). part: only 'picked' or only 'wide'."""
        t, devs = _target(n, key), self.devs
        wide = kinds if wide_kinds is None else wide_kinds
        out: list[dict] = []
        seen: set[str] = set()

        def add(d: dict | None, ks: tuple | None) -> None:
            if d and str(d["id"]) not in seen and (ks is None or d.get("kind") in ks):
                seen.add(str(d["id"]))
                out.append(d)
        if target_empty(t):
            if empty_all and part != "picked":
                for d in devs.values():
                    add(d, wide)
            return out
        if part != "wide":
            for did in t.get("devices") or []:
                add(devs.get(str(did)), kinds)
            groups = self.eng.groups()
            for gid in t.get("groups") or []:
                for did in groups.get(str(gid), []):
                    add(devs.get(str(did)), kinds)
        rooms = {str(r) for r in t.get("rooms") or []}
        if part != "picked" and (t.get("all") or rooms):
            for d in devs.values():
                if t.get("all") or str(d.get("room_id")) in rooms:
                    add(d, wide)
        return out

    def mem(self, n: dict) -> dict:
        """Memory of the node in flows.json (survives restarts). Call touch() after a change."""
        return self.eng.state.setdefault(f"{self.flow.id}/{n['id']}", {})

    def touch(self) -> None:
        """Note that memory or variables have changed and must be saved."""
        self.eng.dirty = True

    def rt(self, n: dict) -> dict:
        """Volatile memory (only until the restart)."""
        return self.eng.rt.setdefault(f"{self.flow.id}/{n['id']}", {})

    def truth(self, src: dict) -> bool | None:
        """Does the condition of the node hold right now? None if it is not a condition (trigger, action) or the
        chain gets too deep."""
        fn = HANDLERS.get(src["type"], {}).get("state")
        if fn is None or self.flow.roles.get(src["id"]) == "trigger" or self.depth > 32:
            return None
        self.depth += 1
        try:
            return fn(self, src)
        finally:
            self.depth -= 1

    def bools(self, n: dict, base: str = "c") -> list[bool]:
        """All yes/no values connected to the input (or to the growing inputs c1 … cn)."""
        edges = []
        for e in self.flow.inc.get(n["id"], []):
            b, no = flow_catalog._split(str(e["toPort"]))
            if b == base:
                edges.append((no if no is not None else 999, e))
        out = []
        for _, e in sorted(edges, key=lambda x: x[0]):
            src = self.flow.nodes.get(e["from"])
            val = bool(self.truth(src)) if src else False
            out.append(val)
            if self.run is not None:
                self.path.append(["c", e["id"], e["from"], val])
                self.eng.emit(self.flow.id, ev="cond", run=self.run, edge=e["id"], node=e["from"], val=val)
        return out

    def connected(self, n: dict, port: str) -> bool:
        """Is anything connected to this output of the node?"""
        return bool(self.flow.out.get((n["id"], port)))

    def wait(self, n: dict, key: str, delay: float, port: str, enter: bool = False) -> None:
        """Pause the pass at this node and continue at output `port` after `delay` seconds. `key`
        distinguishes several waiters at the same node."""
        self.eng.wait(self.flow.id, n["id"], key, delay, port, self.event, enter)

    def waiting(self, n: dict, key: str) -> dict | None:
        """The running waiter of this node with this key, or None."""
        return self.eng.waits.get(f"{self.flow.id}/{n['id']}/{key}")

    def cancel(self, n: dict, key: str | None = None) -> int:
        """Cancel waiters of this node (a certain one or all). Returns their number."""
        return self.eng.cancel_waits(self.flow.id, n["id"], key)

    async def run_branch(self, n: dict, port: str) -> None:
        """Run a branch as a round of its own (loops): fresh visit list, so that the same path runs again."""
        await self.eng.propagate(self, n["id"], port, set())

    def fmt(self, text: Any, quote: bool = False) -> str:
        """Replace placeholders in a text: {trigger}, {device}, {value}, {flow}, {index}, {count}. `quote` encodes
        the values for an address."""
        vals = {"trigger": self.event.get("trigger", ""), "device": self.event.get("device", ""),
                "value": self.event.get("value", ""), "flow": self.flow.name,
                "index": self.event.get("index", ""), "count": self.event.get("count", "")}
        s = str(text or "")
        for k, v in vals.items():
            v = "" if v is None else str(v)
            s = s.replace("{" + k + "}", urllib.parse.quote(v) if quote else v)
        return s

    def sun(self) -> tuple[int, int] | None:
        """Today's sunrise and sunset as minutes since midnight (or None)."""
        return self.eng.sun(self.dt.date())

    def log(self, text: str) -> None:
        """Write a step into the trace that test run and event log show."""
        self.trace.append(text)


def _ev(trigger: str, d: dict | None = None, value: Any = None, port: str = "out") -> dict:
    """Event of a trigger: at which output it fires, its name, the device and the value (for the
    placeholders)."""
    return {"port": port, "trigger": trigger, "device": (d or {}).get("name", ""), "value": "" if value is None else value}


# ---------------------------------------------------------------- Laufzeit
_EVENT_KEYS = ("trigger", "device", "value", "origin", "manual", "index", "count", "hops")


def _slim(event: dict) -> dict:
    """Event for a wait marker: simple values only (ends up in flows.json)."""
    return {k: (v if isinstance(v, (bool, int, float)) or v is None else str(v)[:200])
            for k, v in event.items() if k in _EVENT_KEYS}


class Engine:
    """The run time of the flows: loads them, asks the triggers every tick, sends signals through the graphs, keeps
    waiters and variables and talks to the core through `hooks`."""
    def __init__(self):
        """Empty state. Flows, variables and memory come from the store with the first `reload`."""
        self.hooks: dict[str, Callable] = {}
        self.flows: dict[str, Flow] = {}
        self.paused: dict[str, str] = {}              # flow id -> "pro" | "limit" (the plan does not run it)
        self.outdated: set[str] = set()               # flows from another format: never run
        self.needs_devices = False
        self.cur: dict[str, dict] | None = None       # freshest device state
        self.cur_at = 0.0
        self.seq = 0                                  # order of queries and Hue events
        self.pushed: dict[tuple[str, str], tuple[int, Any]] = {}
        self.changed_at: dict[str, float] = {}
        self.scenes: dict[str, str] | None = None     # target -> active scene (for the scene trigger)
        self.vars: dict[str, Any] = {}
        self.state: dict[str, dict] = {}
        self.rt: dict[str, dict] = {}
        self.wait_tasks: dict[str, asyncio.Task] = {}
        self.dirty = False
        self.loaded = False
        self.locks: dict[str, asyncio.Lock] = {}
        self.queued: dict[str, int] = {}              # flow id -> waiting triggers
        self.dropped: dict[str, float] = {}
        self.last_minute: int | None = None           # zuletzt ausgewertete Minute (Unix-Zeit // 60)
        self.last_runs: dict[str, dict] = {}          # flow id -> last pass (path for the editor)
        self.run_seq = 0
        self.uniq = 0
        self.started = clock()
        self._sun: tuple[date, Any] | None = None
        self._groups: tuple[float, dict[str, list[str]]] | None = None
        self._problems: list[str] | None = None

    @property
    def problems(self) -> list[str]:
        """Differences between catalogue and registered nodes (empty = everything fits). Checked on the first
        look – by then the nodes from flow_nodes.py are certainly loaded."""
        if self._problems is None:
            self._problems = check_specs()
        return self._problems

    # ----- Laden
    @property
    def waits(self) -> dict[str, dict]:
        """Waiting passes {"flow/node/key": entry} – part of the stored state, so that they survive a
        restart."""
        return self.state.setdefault("_waits", {})

    def unique(self, prefix: str) -> str:
        """Unique id for a run or a waiter."""
        self.uniq += 1
        return f"{prefix}{self.uniq}-{int(clock() * 1000) % 100000}"

    def reload(self) -> None:
        """Load the flows anew from the store. Waiters and memory of a flow stay as long as its logic has not
        changed; when a flow is rebuilt, switched off or deleted, they end. Outdated flows and those
        the plan pauses do not run. The engine runs the flows of the real home only: a request in the demo
        (demo.py) never reloads it."""
        if demo.active():
            return
        first = not self.loaded
        if first:
            self.vars, self.state = flows_store.runtime()
            self.loaded = True
        old = self.flows
        raw = flows_store.all_flows()
        self.outdated = {f["id"] for f in raw if flows_store.outdated(f)}
        usable = [f for f in raw if f["id"] not in self.outdated]
        self.paused = entitlements.paused_flows(usable)
        self.flows = {f["id"]: Flow(f) for f in usable}
        for fid in set(old) | set(self.flows):
            of, nf = old.get(fid), self.flows.get(fid)
            runs = nf is not None and nf.enabled and fid not in self.paused
            if of is not None and (not runs or nf.sig != of.sig):
                self._stop_flow(fid)          # deleted, off, paused or rebuilt: nothing continues from an old state
        live = {f"{f.id}/{nid}" for f in self.flows.values() for nid in f.nodes}
        waits = {k: v for k, v in self.waits.items() if k.rsplit("/", 1)[0] in live and isinstance(v, dict)}
        self.state = {k: v for k, v in self.state.items()
                      if k in live or k.startswith("_win:") or (k.startswith("_flow:") and k[6:] in self.flows)}
        self.state["_waits"] = waits
        running = {f.id for f in self.enabled()}
        if first:                              # restart: wait markers of flows that no longer run expire
            for wid in [w for w in waits if w.split("/", 1)[0] not in running]:
                waits.pop(wid, None)
        self.needs_devices = any(f.needs_devices for f in self.enabled())

    def _stop_flow(self, fid: str) -> None:
        """End everything running of a flow: wait markers, volatile memory, trigger memory."""
        pre = fid + "/"
        for wid in [w for w in self.waits if w.startswith(pre)]:
            self.waits.pop(wid, None)
            t = self.wait_tasks.pop(wid, None)
            if t is not None:
                t.cancel()
        self.rt = {k: v for k, v in self.rt.items() if not k.startswith(pre)}
        for k, st in self.state.items():
            if k.startswith(pre):
                for key in TRANSIENT:
                    st.pop(key, None)
        self.dirty = True

    def enabled(self) -> list[Flow]:
        """Flows that are running right now: switched on and not paused."""
        return [f for f in self.flows.values() if f.enabled and f.id not in self.paused]

    def flush(self) -> None:
        """Save variables and memory if anything has changed (never from the demo – its files are not the engine's)."""
        if self.dirty and not demo.active():
            self.dirty = False
            flows_store.save_runtime(self.vars, self.state)

    def info(self, fid: str) -> dict:
        """Status eines Flows (letzter Lauf, letzter Fehler)."""
        return self.state.get("_flow:" + fid, {})

    def groups(self) -> dict[str, list[str]]:
        """Device groups of the floor plan: id -> members (cached briefly, otherwise the floor plan is read per node)."""
        if self._groups is None or abs(clock() - self._groups[0]) > GROUPS_TTL:
            try:
                gs = {str(g.get("id")): [str(m) for m in g.get("members") or []] for g in floorplan_store.groups(floorplan_store.get())}
            except Exception:
                gs = {}
            self._groups = (clock(), gs)
        return self._groups[1]

    def sun(self, day: date) -> tuple[int, int] | None:
        """Sunrise and sunset of a day as minutes since midnight. The place comes from the weather, otherwise
        a fallback place applies; computed once per day."""
        if self._sun is None or self._sun[0] != day:
            geo = weather._read_cache()
            lat, lon = (geo["lat"], geo["lon"]) if isinstance(geo.get("lat"), (int, float)) else FALLBACK_GEO
            self._sun = (day, sun_times(lat, lon, day))
        return self._sun[1]

    # ----- Variablen
    def var(self, name: str) -> Any:
        """Value of a variable. With a counting window: sum of the steps of the recent time (sliding)."""
        w = self.state.get("_win:" + name)
        if not w:
            return self.vars.get(name)
        now = clock()
        steps = [s for s in w.get("steps", []) if 0 <= now - s[0] <= w.get("window", 0)]
        if len(steps) != len(w.get("steps", [])):
            w["steps"] = steps
            self.dirty = True
        v = round(sum(s[1] for s in steps), 4)
        if self.vars.get(name) != v:
            self.vars[name] = v
            self.dirty = True
        return v

    def all_vars(self) -> dict[str, Any]:
        """Alle Variablen mit ihren aktuellen Werten."""
        return {k: self.var(k) for k in list(self.vars)}

    def set_var(self, name: str, value: Any) -> None:
        """Set a variable (checked); an invalid value deletes it. A running time window of the variable ends
        with that."""
        self.state.pop("_win:" + name, None)
        clean = flows_store.clean_vars({name: value})
        if name in clean:
            self.vars[name] = clean[name]
        else:
            self.vars.pop(name, None)
        self.dirty = True

    def add_step(self, name: str, amount: float, window: float) -> None:
        """Add an amount to the variable in the sliding time window (counters such as "three times in ten minutes")."""
        w = self.state.setdefault("_win:" + name, {"steps": [], "window": window})
        w["window"] = window
        w["steps"] = (w.get("steps", []) + [[clock(), amount]])[-WINDOW_STEPS:]
        self.vars.setdefault(name, 0)
        self.dirty = True
        self.var(name)

    def set_vars(self, values: dict) -> dict:
        """Set variables from outside (flow list, API); None deletes. Triggers the variable triggers."""
        for k, v in list((values or {}).items())[:flows_store.MAX_VARS]:
            name = " ".join(str(k).split())[:40]
            if not name:
                continue
            if v is None:
                self.vars.pop(name, None)
                self.state.pop("_win:" + name, None)
                self.dirty = True
            elif name in self.vars or len(self.vars) < flows_store.MAX_VARS:
                self.set_var(name, v)
        self.flush()
        self._kick_vars(0)
        return self.all_vars()

    def _kick_vars(self, hops: int) -> None:
        """After a changed variable, ask the triggers "Variable" right away instead of only in the next tick – limited
        to VAR_HOPS rounds, so that flows do not trigger each other endlessly."""
        if hops >= VAR_HOPS:
            return                             # the next round sees the change at the latest
        try:
            self.spawn(self._poll(Tick(), only=("variable",), hops=hops + 1))
        except RuntimeError:
            pass                               # no running loop (call outside the app)

    # ----- Sichtbarkeit
    def emit(self, fid: str, **msg: Any) -> None:
        """Send a live message of a flow to the open pages (editor). Errors in that do not disturb the flow."""
        fn = self.hooks.get("publish")
        if fn is None:
            return
        try:
            fn({"type": "flow", "flow": fid, **msg})
        except Exception:
            pass   # the display in the editor must never disturb a flow

    def live(self, fid: str) -> dict[str, dict]:
        """What is running or holding in this flow right now: node id -> {until, on, round} (editor, flow list)."""
        out: dict[str, dict] = {}
        pre = fid + "/"
        for wid, w in self.waits.items():
            if wid.startswith(pre):
                nid = wid.split("/", 2)[1]
                cur = out.setdefault(nid, {})
                cur["until"] = min(w.get("until", 0), cur.get("until", float("inf")))
        flow = self.flows.get(fid)
        for nid, n in (flow.nodes.items() if flow else ()):
            st = self.state.get(pre + nid) or {}
            if st.get("until") is not None:
                out.setdefault(nid, {})["until"] = st["until"]
            if st.get("on") is True and flow.is_trigger(n):
                out.setdefault(nid, {})["on"] = True
            if st.get("loop"):
                out.setdefault(nid, {})["round"] = st["loop"].get("round", 0)
        return out

    def running(self, fid: str) -> bool:
        """Is anything waiting in this flow (timer, loop, hold time)?"""
        return any("until" in v or "round" in v for v in self.live(fid).values())

    # ----- Wait markers (timers, delays, loop rounds)
    def wait(self, fid: str, nid: str, key: str, delay: float, port: str, event: dict, enter: bool = False) -> None:
        """Continue at output `port` after `delay` seconds (enter: enter the node itself again).
        A marker with the same key replaces the old one. Markers are stored in flows.json."""
        wid = f"{fid}/{nid}/{key}"
        old = self.wait_tasks.pop(wid, None)
        if old is not None:
            old.cancel()
        entry = {"until": clock() + max(0.0, float(delay)), "port": port, "enter": bool(enter), "event": _slim(event)}
        self.waits[wid] = entry
        self.dirty = True
        self._spawn_wait(wid, entry)
        self.emit(fid, ev="wait", node=nid, until=entry["until"])

    def cancel_waits(self, fid: str, nid: str, key: str | None = None) -> int:
        """Cancel waiters of a node (a certain key or all). Returns their number."""
        pre = f"{fid}/{nid}/"
        hit = [w for w in self.waits if (w == pre + key if key is not None else w.startswith(pre))]
        for wid in hit:
            self.waits.pop(wid, None)
            t = self.wait_tasks.pop(wid, None)
            if t is not None:
                t.cancel()
        if hit:
            self.dirty = True
            self.emit(fid, ev="idle", node=nid)
        return len(hit)

    def _spawn_wait(self, wid: str, entry: dict) -> None:
        """Start a task that continues a waiter at the remembered output once its time is up – unless it
        was replaced or cancelled meanwhile."""
        async def run():
            await _sleep(max(0.0, entry["until"] - clock()))
            if self.waits.get(wid) is not entry:
                return                         # replaced or cancelled
            self.waits.pop(wid, None)
            self.wait_tasks.pop(wid, None)     # before continuing: a cancellation of this flow does not hit the running task
            self.dirty = True
            fid, nid, _ = wid.split("/", 2)
            if not any(w.startswith(f"{fid}/{nid}/") for w in self.waits):
                self.emit(fid, ev="idle", node=nid)
            ev = {**entry.get("event", {}), "continued": True}
            await self.fire(fid, nid, entry["port"], ev, manual=bool(ev.get("manual")), wait=True,
                            enter=bool(entry.get("enter")))
            self.flush()
        self.wait_tasks[wid] = self.spawn(run())

    def restore_waits(self) -> int:
        """After the start: launch stored wait markers again (overdue ones run at once)."""
        n = 0
        for wid, entry in list(self.waits.items()):
            if wid not in self.wait_tasks and isinstance(entry, dict) and "until" in entry and "port" in entry:
                self._spawn_wait(wid, entry)
                n += 1
        return n

    def flow_enabled(self, fid: str, trigger: str = "Aktivierung") -> None:
        """Schedule the start triggers of a flow (start of ComfyOS, switching the flow on)."""
        flow = self.flows.get(fid)
        if flow is None or not flow.enabled or fid in self.paused or demo.active():
            return
        for n in flow.nodes.values():
            if n["type"] == "startup" and flow.roles.get(n["id"]) == "trigger":
                self.wait(fid, n["id"], "start", max(0.0, fnum(cfg(n, "offset"), 0)), "out",
                          {"trigger": trigger, "origin": "startup"})

    # ----- Device state
    async def snapshot(self) -> dict | None:
        """Fetch a fresh device state and remember it. State messages of plugins that arrived during the query are
        newer and take precedence. If nothing is reachable, the old state stays (unknown is not "off")."""
        fn = self.hooks.get("devices")
        if fn is None:
            return None
        self.seq += 1
        started = self.seq
        try:
            devs = await fn()
        except Exception:
            return None                        # unreachable: the old state stays (unknown is not "off")
        cur = {str(d["id"]): d for d in devs if d.get("id")}
        # Hue events that arrived during the query are newer than the response
        for (did, key), (seq, val) in list(self.pushed.items()):
            if seq > started and did in cur:
                cur[did][key] = val
            elif seq <= started:
                self.pushed.pop((did, key), None)
        self._track(self.cur, cur)
        self.cur, self.cur_at = cur, clock()
        return cur

    async def ensure_devices(self, flow: Flow) -> None:
        """For a flow that needs devices, fetch a state if the existing one is missing or too old."""
        if flow.needs_devices and (self.cur is None or abs(clock() - self.cur_at) > FRESH):
            await self.snapshot()

    def _track(self, prev: dict | None, cur: dict) -> None:
        """Remember when each device last changed (for "unchanged since …")."""
        now = clock()
        for did, d in cur.items():
            a = (prev or {}).get(did)
            if a is None or any(a.get(k) != d.get(k) for k in DEVICE_FIELDS):
                if a is not None or did not in self.changed_at:
                    self.changed_at[did] = now

    # ----- Runden
    def _due_minutes(self) -> list[tuple[int, int, date]]:
        """Minutes that have begun since the last round (at most CATCH_UP_MINUTES back). If a round hangs
        longer than a minute (the Home Base does not answer, a long action), no point in time is skipped this way."""
        now_min = int(clock() // 60)
        last, self.last_minute = self.last_minute, now_min
        if last is None:
            return []
        out = []
        for m in range(max(last + 1, now_min - CATCH_UP_MINUTES + 1), now_min + 1):
            dt = datetime.fromtimestamp(m * 60)
            out.append((dt.hour * 60 + dt.minute, dt.weekday(), dt.date()))
        return out

    async def tick(self) -> None:
        """One tick: fetch the device state (only if a flow needs it), ask all triggers, save the state."""
        due = self._due_minutes()   # count along even without active flows: a flow switched on later catches up on nothing
        if not self.enabled():
            self.scenes = None
            return
        if self.needs_devices:
            await self.snapshot()
        prev, self.scenes = self.scenes, scenes_store.active_map()
        await self._poll(Tick(due, (prev, self.scenes)))
        self.flush()

    async def push_device(self, did: str, fields: dict) -> None:
        """Hue event: take over the state at once and check the triggers without waiting."""
        did = str(did)
        if self.cur is None or did not in self.cur or not self.needs_devices:
            return
        new = {k: v for k, v in fields.items() if v is not None}
        self.seq += 1
        for k, v in new.items():
            self.pushed[(did, k)] = (self.seq, v)
        old = self.cur[did]
        self.cur = {**self.cur, did: {**old, **new}}
        self._track({did: old}, {did: self.cur[did]})
        await self._poll(Tick())
        self.flush()

    async def push_button(self, did: str, button: str, press: str) -> int:
        """A button was pressed (plugin: context.button_pressed): start every trigger "Taster" that watches this
        device, this button (or any) and this kind of press (or any). A press is an event, not a state – it fires
        exactly once and is never caught up on later. Returns how many triggers fired."""
        did = str(did)
        flows = [f for f in self.enabled()
                 if any(n["type"] == "button" and f.is_trigger(n) for n in f.nodes.values())]
        if not flows:
            return 0
        if self.cur is None or did not in self.cur:
            await self.snapshot()
        d = (self.cur or {}).get(did)
        if d is None:
            return 0
        name = next((b.get("name") for b in d.get("buttons") or [] if b.get("id") == button), None) or button
        fired = 0
        for flow in flows:
            ctx = Ctx(self, flow)
            for n in list(flow.nodes.values()):
                if n["type"] != "button" or not flow.is_trigger(n):
                    continue
                if did not in {str(x["id"]) for x in ctx.targets(n)}:
                    continue
                want_button, want_press = cfg(n, "button"), cfg(n, "press", "short")
                if (want_button and str(want_button) != button) or want_press not in ("any", press):
                    continue
                ev = _ev(f"Taster: {name} {PRESS_TEXT.get(press, press)}", d, name)
                ev.pop("port", None)
                if await self.fire(flow.id, n["id"], "out", {**ev, "origin": "button"}):
                    fired += 1
        return fired

    async def _poll(self, tick: Tick, only: tuple | None = None, hops: int = 0) -> None:
        """Ask all triggers of the running flows and let fire what is due. `only` restricts to
        node types; conditions with a switching threshold carry their state along."""
        for flow in self.enabled():
            ctx = Ctx(self, flow, tick=tick)
            for n in list(flow.nodes.values()):
                if only is not None and n["type"] not in only:
                    continue
                fn = HANDLERS.get(n["type"], {}).get("poll")
                if fn is None or not flow.is_trigger(n):
                    if n["type"] == "device_state" and fnum(cfg(n, "band"), 0) > 0:
                        try:
                            HANDLERS["device_state"]["state"](ctx, n)   # carry the switching threshold of a condition along
                        except Exception:
                            pass   # the condition reports its error itself as soon as a signal asks it
                    continue
                try:
                    evs = fn(ctx, n) or []
                except Exception as exc:
                    self._error(flow, exc, {})
                    continue
                for ev in evs:
                    port = ev.pop("port", "out")
                    await self.fire(flow.id, n["id"], port, {**ev, "origin": n["type"], "hops": hops})

    # ----- Triggering
    async def fire(self, fid: str, node_id: str, port: str, event: dict, manual: bool = False,
                   wait: bool = False, enter: bool = False) -> Ctx | None:
        """Start a pass: continue at output `port` of the node (enter: enter the node itself)."""
        flow = self.flows.get(fid)
        if flow is None or fid in self.paused or (not flow.enabled and not manual):
            return None
        lock = self.locks.setdefault(fid, asyncio.Lock())
        # If the flow is running right now, the trigger waits instead of getting lost. Continuations always run as
        # tasks of their own – so waiting cannot deadlock.
        if lock.locked() and not wait and self.queued.get(fid, 0) >= QUEUE_MAX:
            if clock() - self.dropped.get(fid, 0) > 60:
                self.dropped[fid] = clock()
                events.log("flow", f"{flow.name}: zu viele Auslösungen auf einmal – einige wurden verworfen",
                           {"flow": flow.name, "error": True})
            return None
        self.queued[fid] = self.queued.get(fid, 0) + 1
        entered = False
        ctx = None
        try:
            async with lock:
                self.queued[fid] -= 1
                entered = True
                flow = self.flows.get(fid)   # could be off, paused or edited by now
                if flow is None or fid in self.paused or (not flow.enabled and not manual):
                    return None
                n = flow.nodes.get(node_id)
                if n is None:
                    return None
                await self.ensure_devices(flow)
                ev = {**event, "manual": manual}
                ctx = Ctx(self, flow, event=ev)
                info = self.state.setdefault("_flow:" + fid, {})
                if not ev.get("continued"):
                    info.update(last_run=ctx.now, last_trigger=str(ev.get("trigger") or "")[:80],
                                runs=int(info.get("runs", 0)) + 1)
                self.dirty = True
                self.run_seq += 1
                ctx.run = self.run_seq
                self.emit(fid, ev="run", run=ctx.run, node=node_id, port=port, cont=bool(ev.get("continued")),
                          trigger=str(ev.get("trigger") or "")[:80])
                error = None
                try:
                    if enter:
                        fn = HANDLERS.get(n["type"], {}).get("signal")
                        for p in (await fn(ctx, n, port) if fn else None) or ():
                            await self.propagate(ctx, node_id, p, set())
                    else:
                        await self.propagate(ctx, node_id, port, set())
                    info.pop("last_error", None)
                except Exception as exc:
                    error = _err_text(exc)
                    self._error(flow, exc, ev)
                self.last_runs[fid] = {"at": ctx.now, "node": node_id, "port": port, "enter": enter,
                                       "trigger": str(ev.get("trigger") or "")[:80], "path": ctx.path[:MAX_STEPS],
                                       "trace": ctx.trace[:20], "error": error}
                self.emit(fid, ev="end", run=ctx.run, error=error, trace=ctx.trace[:20], info=dict(info))
        finally:
            if not entered:
                self.queued[fid] -= 1
        self.flush()
        if ctx is not None and ctx.vars_changed:
            self._kick_vars(int(fnum(event.get("hops"), 0)))
        return ctx

    async def propagate(self, ctx: Ctx, src: str, port: str, visited: set) -> None:
        """Pass a signal from an output on to all connected nodes. Every input is served only once per
        pass; after MAX_STEPS steps the pass aborts with an error."""
        for e in ctx.flow.out.get((src, port), []):
            key = (e["to"], e["toPort"])
            if key in visited:
                continue
            visited.add(key)
            n = ctx.flow.nodes.get(e["to"])
            fn = n and HANDLERS.get(n["type"], {}).get("signal")
            if not fn:
                continue
            ctx.steps += 1
            if ctx.steps > MAX_STEPS:
                raise FlowError("Zu viele Schritte in einem Durchgang – Ablauf abgebrochen")
            if ctx.run is not None:
                ctx.path.append(["s", e["id"], e["to"]])
                self.emit(ctx.flow.id, ev="step", run=ctx.run, edge=e["id"], node=e["to"])
            for p in await fn(ctx, n, e["toPort"]) or ():
                await self.propagate(ctx, n["id"], p, visited)

    def _error(self, flow: Flow, exc: BaseException, ev: dict) -> None:
        """Record an error of a flow: at the flow, in the event log and as the trigger "Flow-Fehler" for other
        flows – an error in the error flow triggers no chain."""
        msg = _err_text(exc)
        info = self.state.setdefault("_flow:" + flow.id, {})
        info.update(last_error=msg, error_at=clock())
        self.dirty = True
        events.log("flow", f"{flow.name}: {msg}", {"flow": flow.name, "error": True})
        if ev.get("origin") != "flow_error":      # an error in the error flow triggers no chain
            try:
                self.spawn(self.fire_type("flow_error", {"trigger": flow.name, "value": msg}, skip=flow.id,
                                          source=flow.id))
            except RuntimeError:
                pass   # no running loop (call outside the app)

    async def fire_type(self, type_: str, event: dict, only: str | None = None, skip: str | None = None,
                        source: str | None = None) -> int:
        """Start all triggers of a type that is fired from outside (flow error). source: the flow that caused it."""
        n_fired = 0
        for flow in self.enabled():
            if (only and flow.id != only) or flow.id == skip:
                continue
            for n in list(flow.nodes.values()):
                if n["type"] != type_ or flow.roles.get(n["id"]) != "trigger":
                    continue
                want = cfg(n, "flow") if type_ == "flow_error" else None
                if want and source and want != source:
                    continue
                if await self.fire(flow.id, n["id"], "out", {**event, "origin": type_}):
                    n_fired += 1
        return n_fired

    async def fire_node(self, fid: str, node_id: str, port: str | None = None, manual: bool = True,
                        trigger: str | None = None) -> Ctx | None:
        """Start a trigger by hand (play button, test in the editor). KeyError: node/output does not exist."""
        flow = self.flows.get(fid)
        n = flow.nodes.get(node_id) if flow else None
        if n is None or not flow.is_trigger(n):
            raise KeyError(node_id)
        ports = [p["id"] for p in flow_catalog.out_ports(n)]
        port = port or (ports[0] if ports else "out")
        if port not in ports:
            raise KeyError(port)
        text = trigger or (str(cfg(n, "label") or "Play") if n["type"] == "play" else f"Test: {label(n)}")
        return await self.fire(fid, node_id, port, {"trigger": text, "origin": n["type"]}, manual=manual, wait=True)

    async def run_manual(self, fid: str) -> list[str]:
        """Test run: start every trigger of the flow once (conditions apply), even if the flow is off."""
        flow = self.flows.get(fid)
        if flow is None:
            raise KeyError(fid)
        trace: list[str] = []
        for n in list(flow.nodes.values()):
            if flow.is_trigger(n):
                ctx = await self.fire_node(fid, n["id"], trigger="Testlauf")
                if ctx:
                    trace += ctx.trace
        return trace

    def spawn(self, coro) -> asyncio.Task:
        """Run work of the engine in the background (wait markers, follow-up triggers) – under the supervision of
        tasks.py: the reference stays, an error is written to the event log."""
        return tasks.fire(coro, "flow")

    async def loop(self) -> None:
        """Background task of the engine: load, restore waiters, fire the trigger "Start" and then work at the
        tick. An error in a tick ends up in the event log, the loop keeps running."""
        self.reload()
        if self.problems:
            events.log("flow", "Flow-Bausteine unvollständig: " + "; ".join(self.problems[:5]))
        self.restore_waits()
        for f in self.enabled():
            self.flow_enabled(f.id, "Start von ComfyOS")
        while True:
            await asyncio.sleep(TICK)
            try:
                await self.tick()
            except Exception as exc:
                events.log("flow", f"Flow-Laufzeit: {_err_text(exc)}")

    def reset(self) -> None:
        """Forget everything (tests): cancel waiters and clear the state; the connections to the core stay."""
        for t in self.wait_tasks.values():
            t.cancel()
        hooks = self.hooks
        self.__init__()
        self.hooks = hooks


engine = Engine()

# The nodes register themselves on loading through @node (HANDLERS). The import is at the end because
# flow_nodes.py in turn uses helpers from this module.
from . import flow_nodes  # noqa: E402,F401
