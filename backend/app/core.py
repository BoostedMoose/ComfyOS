"""Shared state and helpers of ComfyOS: device list from all plugins, live updates (SSE), connection state,
power consumption, background loops and the life cycle (startup/shutdown). The switching paths – lock, switching,
light, scenes, effects – have lived in control.py since 0.20 and are attached here as core.<name> (see end of file).

Devices and commands go through the plugins (plugins.py): collect -> link (device_merge) ->
device store -> device row. Since 0.19 the Home Base is a plugin too (public/plugins/qivicon); the core knows
no plugin by name. Which plugin manages rooms (`room_hub`) or syncs heating routines (`heating_sync`) is told by
the plugin through the interface. ComfyOS also runs entirely without a Home Base.

The routers in api/ reach all of this at run time through `core.<name>` (tests replace e.g. core.publish).
"""
from __future__ import annotations

import asyncio
import os
import time

from fastapi import HTTPException, Request, Response

from . import (auth, demo, device_merge, device_store, events, floorplan_store, flow_catalog, flow_engine,
               heating_scheduler, heating_sync, history, home_store, license, plugins, power_store, push, room_assign,
               routines_store, scenes_store, tasks)
from .plugin_api import Plugin, PluginError
from .paths import app_version, ensure_dirs

RESTART_EXIT_CODE = 3
CONNECT_GRACE = 45.0      # s – this long after its start a plugin without a connection counts as "connecting", not as a fault


def client_ip(request: Request) -> str:
    """Address of the caller ("?" if it is not known)."""
    return request.client.host if request.client else "?"


def is_local(request: Request) -> bool:
    """Does the request come from the machine ComfyOS runs on? Only then does the first setup work without a code."""
    return client_ip(request) in ("127.0.0.1", "::1", "localhost")


def set_session_cookie(response: Response, token: str, secure: bool = False) -> None:
    """Set the session cookie: not readable for scripts, only for our own page, over HTTPS additionally `Secure`."""
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="strict", path="/", secure=secure)


def step_up(request: Request, password: str, what: str) -> None:
    """Sensitive actions ask for the ComfyOS password again – an open session alone is not enough."""
    ip = client_ip(request)
    wait = auth.locked_for(ip)
    if wait:
        raise HTTPException(429, f"Zu viele Versuche. Bitte {wait} s warten.")
    if not auth.verify_password(password):
        seconds = auth.register_failure(ip)
        if seconds:
            events.log("security", f"{what} von {ip[:45]} für {seconds} s gesperrt (Passwort falsch)")
        raise HTTPException(401, "Passwort falsch.")


# ---------------------------------------------------------------- Helfer
def room_names() -> dict[str, str]:
    """Room names: own names take precedence over the names from the synced system (Magenta app)."""
    names = dict(routines_store.room_names())
    names.update(floorplan_store.room_names())
    return names


def room_hub() -> tuple[str, Plugin] | None:
    """The hub that manages rooms (e.g. the Home Base): (plugin id, plugin) – only if it is configured.
    Without a hub, rooms live in the floor plan alone and the room assignment of devices in the device store."""
    for hit in plugins.having("manages_rooms"):
        try:
            if (hit[1].status() or {}).get("configured"):
                return hit
        except Exception:
            continue   # the plugin does not answer right now: does not count as room hub
    return None


def need_room_hub() -> tuple[str, Plugin]:
    """The hub that manages rooms (plugin id, plugin) – or 400 with a hint if none is connected."""
    hub = room_hub()
    if hub is None:
        raise HTTPException(400, "Kein Hub verbunden, der Räume führt – Räume legst du im Grundriss an.")
    return hub


async def hub_call(awaitable):
    """Call to the room hub: pass understandable errors of the plugin on as HTTP errors."""
    try:
        return await awaitable
    except HTTPException:
        raise
    except PluginError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Hub nicht erreichbar ({type(exc).__name__}).")


# ---------------------------------------------------------------- Device list from all plugins
def _room_resolvers(rows: list[dict]):
    """Room key/name -> (room id, name). Names: source (hub), above it the synced system and own names."""
    plan = floorplan_store.get()
    names = {d["room_id"]: d["room"] for d in rows if d.get("room_id") and d.get("room")}
    names.update(room_names())

    def for_key(key: str):
        rid = room_assign.resolve(key, plan)
        return (rid, room_assign.target_name(rid, plan, names)) if rid else None

    def for_name(name: str):
        want = " ".join(str(name).split()).casefold()
        for rid, n in names.items():
            if " ".join(str(n).split()).casefold() == want:
                return rid, n
        for r in plan.get("rooms", []):
            if " ".join(str(r.get("name") or "").split()).casefold() == want:
                return (r.get("room_id") or room_assign.PLAN_PREFIX + r["id"]), r.get("name")
        return None
    return for_key, for_name


_index: dict[tuple[str, str], str] = {}        # (plugin, device id at the plugin) -> id in ComfyOS (for live messages)
_merged: device_merge.Merged | None = None     # last state: candidates and removed devices for the lists under System
_demo_merged: device_merge.Merged | None = None   # the same for the demo (demo.py) – it never replaces the real one


def last_merge() -> device_merge.Merged | None:
    """Last merged device state of the running request: the real home or the demo."""
    return _demo_merged if demo.active() else _merged


async def merged_devices(max_age: float | None = None, prune: bool = False) -> list[dict]:
    """Devices of all plugins as one list (rules: device_merge). A plugin that does not answer right now
    leaves the other devices untouched."""
    global _index, _merged, _demo_merged
    sources = await plugins.collect({"max_age": max_age})
    m = device_merge.merge(sources, device_store.get(), _room_resolvers)
    if demo.active():
        _demo_merged = m
    else:
        _index, _merged = m.index, m
    names, hub = room_names(), room_hub()
    for row in m.rows:
        if row.get("room_id") and names.get(row["room_id"]):      # own room names take precedence over the hub's
            row["room"] = names[row["room_id"]]
        # the room lives only in ComfyOS when no hub manages it (no hub, device of another plugin, adopted)
        row["local_room"] = hub is None or row.get("plugin") != hub[0] or bool(row.get("adopted"))
        _complete_caps(row)
    if prune and m.complete and m.rows:
        device_store.prune(m.known_ids, plugins.unsettled_prefixes(sources))
    device_store.apply(m.rows)
    return m.rows


def _complete_caps(row: dict) -> None:
    """Complete the capabilities of the row with those that follow from reported values: if a device reports a value
    ComfyOS knows as a property (catalogue in flow_nodes.json) without declaring the capability, it counts as readable.
    That way `caps` is complete even if a plugin names its capabilities only in part."""
    caps = row.setdefault("caps", [])
    have = {c["key"] for c in caps}
    for key, prop in flow_catalog.PROPS.items():
        if key not in have and row.get(key) is not None and len(caps) < device_merge.MAX_CAPS:
            entry = {"key": key, "type": prop.get("type", "bool"), "readable": True, "writable": False}
            if prop.get("unit"):
                entry["unit"] = prop["unit"]
            caps.append(entry)


def candidates(plugin_id: str) -> list:
    """Devices the plugin found that are not adopted yet (state of the last query)."""
    m = last_merge()
    return list((m.candidates if m else {}).get(plugin_id, []))


# ---------------------------------------------------------------- Live-Updates an die Browser (SSE)
_listeners: set[asyncio.Queue] = set()


def publish(msg: dict, to_plugins: bool = True) -> None:
    """Send a message to all open pages (live stream). A page that cannot keep up misses it and reloads the next
    time. `to_plugins=False` for messages that are no change of a device (a button press) – plugins that listen
    would only read their whole view again.

    Since 0.23 the open pages are split: a page in the demo (demo.py) only gets messages of the demo, every other
    page only those of the real home – and plugins never hear of the demo."""
    in_demo = demo.active()
    for q in list(_listeners):
        if getattr(q, "demo", False) != in_demo:
            continue
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass   # slow client: misses an update, gets it with the next reload
    if to_plugins and not in_demo:
        plugins.notify(msg)   # plugins that listen with context.on_change (e.g. a bridge to another system)


def publish_device(d: dict, **state) -> None:
    """Report changed values of a device to all open pages."""
    publish({"type": "device", "id": d["id"], **state})


FLOW_PUSH_KEYS = ("on", "online", "motion", "open", "present", "playing")   # what flow triggers learn at once (instead of only in the next round)


def _plugin_device_changed(plugin_id: str, device_id: str, changes: dict) -> None:
    """A plugin reports new values (e.g. the event stream of the Hue Bridge): at once to all open pages and to Flow."""
    did = _index.get((plugin_id, device_id))
    if did is None:
        return   # not (or no longer) in ComfyOS, e.g. not adopted or removed
    # state fields only: a plugin can set neither "type"/"id" of the message nor the lock (kept by the core)
    changes = {k: v for k, v in changes.items() if k in LIVE_FIELDS and k != "locked"}
    if not changes:
        return
    if "online" in changes:
        changes["online"] = bool(changes["online"])
    publish({"type": "device", "id": did, **changes})
    state = {k: changes[k] for k in FLOW_PUSH_KEYS if k in changes}
    if state:
        tasks.fire(_flow_push_device(did, state, plugin_id), "flow-meldung")


async def _flow_push_device(did: str, state: dict, plugin_id: str) -> None:
    """Hand a state message of a plugin to the flow engine. An error there ends up in the event log and does not
    disturb the plugin."""
    try:
        await flow_engine.engine.push_device(did, state)
    except Exception as exc:
        events.log("flow", f"Flow-Laufzeit ({plugin_id}): {flow_engine._err_text(exc)}")


def _plugin_button_pressed(plugin_id: str, device_id: str, button: str, press: str) -> None:
    """A plugin reports a button press (since API revision 7): to the open pages (they show the last press), to the
    event log and to the flow trigger "Taster". Only for devices ComfyOS shows; a press is never stored as a state."""
    did = _index.get((plugin_id, device_id))
    if did is None:
        return   # not (or no longer) in ComfyOS, e.g. not adopted or removed
    row = next((r for r in (_merged.rows if _merged else []) if r.get("id") == did), {})
    name = next((b["name"] for b in row.get("buttons") or [] if b.get("id") == button), button)
    publish({"type": "button", "id": did, "button": button, "name": name, "press": press}, to_plugins=False)
    events.log("device", f"{row.get('name') or did}: {name} {flow_engine.PRESS_TEXT.get(press, press)} gedrückt")
    tasks.fire(_flow_push_button(did, button, press, plugin_id), "flow-taster")


async def _flow_push_button(did: str, button: str, press: str, plugin_id: str) -> None:
    """Hand a button press to the flow engine. An error there ends up in the event log and does not disturb the
    plugin."""
    try:
        await flow_engine.engine.push_button(did, button, press)
    except Exception as exc:
        events.log("flow", f"Flow-Laufzeit ({plugin_id}): {flow_engine._err_text(exc)}")


def _plugin_devices_changed() -> None:
    """A plugin reports a changed device list: all open pages reload."""
    publish({"type": "reload"})


def _plugin_device_gone(plugin_id: str, device_id: str) -> None:
    """A plugin has deleted a device at its source for good: forget its own name and the "removed" mark.
    Only for ids that belong to this plugin (a hub with arbitrary ids cannot reach the devices of other plugins)."""
    if plugins.owner_by_id(device_id) != plugin_id:
        return
    try:
        device_store.set_hidden(device_id, None, False)
        device_store.set_name(device_id, None)
    except ValueError:
        pass   # invalid id: then there was nothing to forget either


async def _plugin_switch(plugin_id: str, device_id: str, state) -> dict:
    """A plugin switches one of its devices from a route of its own: the same path as the interface (with the lock)."""
    did = _index.get((plugin_id, device_id))
    d = next((x for x in await all_devices() if x.get("id") == did), None) if did else None
    if d is None:
        raise HTTPException(404, "Gerät nicht gefunden")
    return await apply_device(d, dict(state), f"plugin:{plugin_id}")


# ---------------------------------------------------------------- Access for plugins with permission ("access")
VIEW_STATE = ("on", "brightness", "target", "temperature", "open", "motion", "alarm", "power_w", "position", "valve",
              "present", "humidity", "playing", "volume", "muted", "illuminance")    # what a plugin with "devices" sees


def view_row(d: dict) -> dict:
    """What a plugin with the permission "devices" sees of a device: name, room, kind, state, capabilities and whether
    it is switchable and locked – nothing of the plugin behind it (no addresses, control channels, source details)."""
    return {"id": d["id"], "name": d.get("name"), "kind": d.get("kind"), "plugin": d.get("plugin"),
            "room_id": d.get("room_id"), "room": d.get("room") if d.get("room_id") else None,
            "online": bool(d.get("online")), "locked": bool(d.get("locked")),
            "controllable": bool(d.get("control")) and not d.get("link_missing"),
            "state": {k: d[k] for k in VIEW_STATE if d.get(k) is not None},
            "caps": [dict(c) for c in d.get("caps") or []], "light": d.get("light"), "media": d.get("media")}


async def _plugin_all_devices() -> list[dict]:
    """All devices for a plugin with the permission "devices" (`context.all_devices`)."""
    return [view_row(d) for d in await all_devices() if d.get("id")]


async def _plugin_command(plugin_id: str, device_id: str, state: dict) -> dict:
    """A plugin with the permission "devices" switches any device (`context.command`): the same path as the
    interface – device lock and event log apply, the log names the plugin as the trigger."""
    d = next((x for x in await all_devices() if x.get("id") == device_id), None)
    if d is None:
        raise HTTPException(404, "Gerät nicht gefunden")
    return await apply_device(d, dict(state), f"plugin:{plugin_id}")


async def _plugin_room_lights(plugin_id: str, room_id: str | None, on: bool, brightness: float | None) -> dict:
    """A plugin with the permission "rooms" switches the light of a room or of the whole home."""
    return await apply_lights(on, None if room_id is None else [room_id], f"plugin:{plugin_id}", brightness)


async def _plugin_scenes() -> list[dict]:
    """Light scenes for a plugin with the permission "scenes": id, name, assigned rooms, currently running."""
    data = scenes_store.get()
    running = {entry.get("scene") for entry in data["active"].values()}
    return [{"id": s["id"], "name": s["name"], "rooms": list(s.get("rooms") or []), "active": s["id"] in running}
            for s in data["scenes"]]


async def _plugin_house_scene(plugin_id: str, scene_id: str, active: bool) -> dict:
    """A plugin with the permission "scenes" starts a house scene or ends it everywhere it runs (restoring
    the light from before)."""
    source = f"plugin:{plugin_id}"
    if active:
        return await apply_house(scene_id, source)
    keys = [key for key, entry in scenes_store.get()["active"].items() if entry.get("scene") == scene_id]
    return await stop_scene(keys, True, source)


async def _plugin_home_state() -> str:
    """At home ("PRESENT") or away ("AWAY") for a plugin with the permission "home"."""
    return str(home_store.get().get("state") or "PRESENT")


async def _plugin_home(plugin_id: str, state: str) -> dict:
    """A plugin with the permission "home" sets at home or away."""
    return await apply_home(state, f"plugin:{plugin_id}")


def source_tag(source: str, d: dict | None = None) -> str:
    """Suffix for the event log that says who switched: " (Flow)" or " (durch <plugin>)". If a plugin switches
    one of its own devices, it is already named as the switching path – then no suffix."""
    if source in ("flow", LOCK_SOURCE):
        return " (Flow)"
    if source == heating_scheduler.SOURCE:
        return " (Heizplan)"
    if not source.startswith("plugin:"):
        return ""
    pid = source[len("plugin:"):]
    if d is not None and (d.get("control") or {}).get("plugin") == pid:
        return ""
    return f" (durch {next((r.name for r in plugins.records() if r.id == pid), pid)})"


def plugin_status(plugin_id: str) -> dict:
    """Connection state of a plugin (configured, connected, live) – for the main tile and scenes."""
    plugin = plugins.active(plugin_id)
    if plugin is None:
        return {"configured": False, "connected": False, "live": False}
    try:
        st = plugin.status() or {}
    except Exception:
        st = {}
    return {"configured": bool(st.get("configured")), "connected": bool(st.get("connected")), "live": bool(st.get("live"))}


def integrations() -> list[dict]:
    """Configured plugins with their connection state (for the status line of the main tile). `script` = the plugin
    brings its own interface (tile, actions in the device menu)."""
    out = []
    for rec in plugins.records():
        if not rec.active:
            continue
        st = plugin_status(rec.id)
        if st["configured"]:
            # just started (start of ComfyOS, switched on, settings saved) and not connected yet: still coming up, no fault
            if st["connected"]:
                rec.started_at = 0.0          # it has come up: from now on a lost connection is a fault at once
            fresh = rec.started_at > 0 and time.monotonic() - rec.started_at < CONNECT_GRACE
            out.append({"id": rec.id, "name": rec.name, **st, "connecting": bool(fresh and not st["connected"]),
                        "script": bool(rec.manifest and rec.manifest.static and plugins.static_file(rec.id, "settings.js")),
                        "version": rec.manifest.version if rec.manifest else None})
    hub = room_hub()
    out.sort(key=lambda i: hub is None or i["id"] != hub[0])     # the room hub (main system) comes first, otherwise as found
    return out


def connection_items(items: list[dict]) -> list[dict]:
    """Entries of the connection pill in the header (since 0.19.2, replaces the status dot): per configured plugin
    `live`/`ok`/`bad` – or `connecting` while it is still coming up after its start (since 0.24, CONNECT_GRACE) –, the
    heating sync if configured, and enabled plugins that do not run (`error`).
    Disabled plugins do not count (since 0.19.3) – they are off on purpose."""
    out = [{"name": i["name"], "state": "connecting" if i.get("connecting") else "bad" if not i["connected"]
            else "live" if i.get("live") else "ok"} for i in items]
    sync = heating_sync.public_status()
    if sync.get("configured"):
        out.append({"name": sync.get("name") or "Abgleich", "state": "bad" if sync.get("ok") is False else "live"})
    out += [{"name": rec.name, "state": "error"} for rec in plugins.records() if rec.state == "error"]
    return out


def system_status() -> dict:
    """Overall state for the connection dot and the home page: configured = at least one plugin, connected =
    none of them is disconnected."""
    items = integrations()
    down = [i["name"] for i in items if not i["connected"]]
    if not items:
        message = "Noch kein Plugin eingerichtet. Bitte unter System einrichten."
    elif down and all(i.get("connecting") for i in items if not i["connected"]):
        message = "Verbindet: " + ", ".join(down)
    elif down:
        message = "Nicht erreichbar: " + ", ".join(down)
    else:
        message = "Verbunden: " + ", ".join(i["name"] for i in items)
    return {"configured": bool(items), "connected": bool(items) and not down, "message": message,
            "items": connection_items(items)}


def effective(devices: list[dict]) -> list[dict]:
    """Devices with their effective room (the local assignment from the floor plan takes precedence). If a source
    was missing in the last collection, pending room changes of its devices stay."""
    m = last_merge()
    return room_assign.apply(devices, floorplan_store.get(), room_names(), complete=bool(m is None or m.complete))


def rooms_overview(devices: list[dict]) -> list[dict]:
    """Rooms with names, temperatures and number of devices."""
    names = room_names()
    rooms: dict[str, dict] = {}
    for d in devices:
        rid = d.get("room_id") or "_none"
        r = rooms.setdefault(rid, {"room_id": d.get("room_id"), "name": d.get("room") or names.get(rid) or "Nicht zugeordnet",
                                   "temps": [], "targets": [], "devices": 0, "offline": 0, "open": 0, "thermostats": 0})
        r["devices"] += 1
        if not d.get("online"):
            r["offline"] += 1
        if d.get("kind") == "contact" and d.get("open"):
            r["open"] += 1
        if d.get("online") and d.get("temperature") is not None and d.get("kind") in ("thermostat", "sensor", "motion"):
            r["temps"].append(float(d["temperature"]))
        if d.get("kind") == "thermostat":
            r["thermostats"] += 1
            if d.get("online") and d.get("target") is not None:
                r["targets"].append(float(d["target"]))
    for rid, name in names.items():
        rooms.setdefault(rid, {"room_id": rid, "name": name, "temps": [], "targets": [], "devices": 0,
                               "offline": 0, "open": 0, "thermostats": 0})
    out = []
    for r in rooms.values():
        temps, targets = r.pop("temps"), r.pop("targets")
        r["temperature"] = round(sum(temps) / len(temps), 1) if temps else None
        r["target"] = round(sum(targets) / len(targets), 1) if targets else None
        out.append(r)
    out.sort(key=lambda r: (r["room_id"] is None, str(r["name"]).lower()))
    return out


# ---------------------------------------------------------------- Hintergrund
async def device_loop():
    """At the pace of the history (default 5 minutes): fetch all devices fresh, write history and events and
    clean up entries of vanished devices. If a round fails, the next one follows."""
    while True:
        await asyncio.sleep(history._sample_seconds())
        try:
            _observe(effective(await merged_devices(max_age=0, prune=True)))
        except Exception as exc:
            tasks.report("geräteverlauf", type(exc).__name__)


LIVE_TICK = 3.0
LIVE_FIELDS = ("online", "on", "brightness", "light", "target", "temperature", "open", "motion", "alarm",
               "power_w", "position", "locked", "valve", "present", "humidity", "playing", "volume", "muted", "media",
               "illuminance")


async def live_loop():
    """While a page is open (SSE): compare the device state every ~3 s and send only changes –
    so that motion, windows, heating and alarms appear in the floor plan at once, not only on reload."""
    prev: dict[str, dict] = {}
    while True:
        await asyncio.sleep(LIVE_TICK)
        if not _real_listeners() and not plugins.watching():     # nobody is watching: no page open, no plugin listening
            prev = {}
            continue
        try:
            cur = {str(d["id"]): d for d in effective(await merged_devices(max_age=LIVE_TICK - 0.5)) if d.get("id")}
        except Exception as exc:
            tasks.report("live-abgleich", type(exc).__name__)
            continue
        for msg in live_changes(prev, cur):
            publish(msg)
        prev = cur


DEMO_LIVE_IDLE = 30.0     # s – the live comparison of the demo ends this long after the last page in the demo closed


async def demo_live_loop():
    """Live comparison of the demo (demo.py): like live_loop, but for the pages in the demo and with the demo home –
    so its sample values (motion, temperatures) move. Started with the first page in the demo, ends by itself when
    none is open any more. It runs as a task of its own in the demo; the real home never sees it. It also runs the
    heating schedule of the demo (heating_scheduler) – as long as a page of the demo is open."""
    demo.enter()
    prev: dict[str, dict] = {}
    idle = 0.0
    since_schedule = heating_scheduler.TICK
    while idle < DEMO_LIVE_IDLE:
        await asyncio.sleep(LIVE_TICK)
        if not any(getattr(q, "demo", False) for q in _listeners):
            idle += LIVE_TICK
            continue
        idle = 0.0
        since_schedule += LIVE_TICK
        if since_schedule >= heating_scheduler.TICK:
            since_schedule = 0.0
            try:
                await heating_scheduler.tick()
            except Exception as exc:
                tasks.report("demo-heizplan", type(exc).__name__)
        try:
            cur = {str(d["id"]): d for d in effective(await merged_devices(max_age=LIVE_TICK - 0.5)) if d.get("id")}
        except Exception as exc:
            tasks.report("demo-abgleich", type(exc).__name__)
            continue
        for msg in live_changes(prev, cur):
            publish(msg)
        prev = cur


_demo_live: asyncio.Task | None = None   # the running live comparison of the demo


def listen(q: asyncio.Queue) -> None:
    """Register an open page for the live stream (api/system.py). A page in the demo gets the messages of the demo;
    the first one starts its live comparison."""
    global _demo_live
    q.demo = demo.active()      # type: ignore[attr-defined]
    _listeners.add(q)
    if q.demo and (_demo_live is None or _demo_live.done()):    # type: ignore[attr-defined]
        _demo_live = tasks.fire(demo_live_loop(), "demo-abgleich")


def _real_listeners() -> bool:
    """Is a page of the real home open (not in the demo)?"""
    return any(not getattr(q, "demo", False) for q in _listeners)


def live_changes(prev: dict[str, dict], cur: dict[str, dict]) -> list[dict]:
    """Only changed fields of known devices (send nothing on the first comparison)."""
    out = []
    for did, d in cur.items():
        a = prev.get(did)
        if a is None:
            continue
        changed = {k: d.get(k) for k in LIVE_FIELDS if a.get(k) != d.get(k)}
        if changed:
            out.append({"type": "device", "id": did, **changed})
    return out


async def startup():
    """Start of ComfyOS (from the `lifespan` in main.py): set the licence state, start plugins, launch background
    tasks, connect the flow engine with the switching helpers. None of this may abort the start."""
    ensure_dirs()
    try:
        license.apply()   # set the effective plan before the flow engine loads
    except Exception as exc:   # licence errors mean Basic, never an aborted start
        events.log("license", f"Lizenzstand nicht lesbar ({type(exc).__name__}) – ComfyOS läuft als Basic")
    tasks.spawn("lizenzprüfung", license.check_loop)
    await plugins.start_all()   # a plugin that does not start stays in the state "error" – the core keeps running
    tasks.spawn("geräteverlauf", device_loop)
    tasks.spawn("live-abgleich", live_loop)
    tasks.spawn("dynamische-szenen", scene_loop)
    tasks.spawn(heating_scheduler.TASK, heating_scheduler.loop)
    flow_engine.engine.hooks.update(
        devices=flow_devices, publish=lambda m: publish(m),
        apply_device=lambda d, s: apply_device(d, s, "flow"), apply_lights=lambda on, r: apply_lights(on, r, "flow"),
        apply_home=lambda s: apply_home(s, "flow"), apply_routine=lambda k: apply_routine(k, "flow"),
        apply_scene=lambda sid, rooms, devs, group=None: apply_scene(sid, rooms, devs, group, source="flow"),
        stop_scene=lambda keys, restore=True: stop_scene(keys, restore, source="flow"),
        set_lock=flow_set_lock, switch_locked=lambda d, s: apply_device(d, s, LOCK_SOURCE), push=flow_push)
    tasks.spawn("flow", flow_engine.engine.loop)
    code = auth.setup_code()
    print(f"\n  ComfyOS {app_version()} läuft.")
    if code:
        print(f"  Ersteinrichtung: Einrichtungscode {code}  (nur nötig, wenn du nicht am selben Rechner bist)\n")


async def shutdown():
    """Shutdown: say goodbye to open pages, cancel background tasks, stop plugins, write intermediate states
    (power consumption, flow state)."""
    for q in list(_listeners):
        try:
            q.put_nowait({"type": "bye"})
        except asyncio.QueueFull:
            pass   # slow client: notices the end by the dropped connection – the shutdown does not wait for it
    await tasks.cancel_all()
    await plugins.stop_all()
    for what, flush in (("Stromverbrauch", power_store.flush), ("Flow-Zustand", flow_engine.engine.flush)):
        try:
            flush()
        except Exception as exc:    # the other state is written anyway
            tasks.report("beenden", type(exc).__name__, f"{what} nicht gespeichert")


async def all_devices() -> list[dict]:
    """All visible devices as rows – the state of all plugins, merged and completed with what ComfyOS
    knows itself (lock, room, name). Along the way the values go to history and consumption. 502 if nothing
    can be fetched."""
    try:
        items = effective(await merged_devices())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Geräte lassen sich gerade nicht abrufen ({type(exc).__name__}).")
    _observe(items)
    return items


ACTIVE_W = 1.0   # below this a device counts as "nothing plugged in"


def measures_power(d: dict) -> bool:
    """Does the device report power or a meter reading?"""
    return isinstance(d.get("power_w"), (int, float)) or isinstance(d.get("energy_kwh"), (int, float))


def power_summary(devices: list[dict]) -> dict:
    """Power consumption: power now (sum of the devices that report power), consumption since reset and today
    (power_store: meters exact, devices without a meter estimated from watts × time)."""
    rows = [d for d in devices if measures_power(d)]
    live = [d for d in rows if d.get("online") and isinstance(d.get("power_w"), (int, float))]
    total = round(sum(float(d["power_w"]) for d in live), 1) if live else (0.0 if rows else None)
    use = power_store.summary(rows)
    return {"total_w": total, "devices": len(rows), "online": len(live),
            "active": sum(1 for d in live if float(d["power_w"]) >= ACTIVE_W),
            "since": use["since"], "kwh_since_reset": use["kwh_since_reset"], "kwh_today": use["kwh_today"],
            "estimated": use["estimated"]}


def power_rows(devices: list[dict]) -> list[dict]:
    """One row per device with consumption metering (for the consumption overview)."""
    use = power_store.summary(devices)["rows"]
    rows = []
    for d in devices:
        if not measures_power(d):
            continue
        u = use.get(str(d.get("id"))) or {}
        rows.append({"id": d.get("id"), "name": d.get("name"), "room": d.get("room"), "room_id": d.get("room_id"),
                     "kind": d.get("kind"), "online": bool(d.get("online")), "on": d.get("on"),
                     "power_w": d.get("power_w"), "energy_kwh": d.get("energy_kwh"),
                     "kwh_since_reset": u.get("kwh_since_reset"), "kwh_today": u.get("kwh_today"),
                     "estimated": bool(u.get("estimated"))})
    rows.sort(key=lambda r: (-(r["power_w"] or 0), str(r["name"]).lower()))
    return rows


def _observe(items: list[dict]) -> None:
    """Use every device state that was fetched anyway for history and event log. That way "Fenster
    geöffnet" or "Gerät offline" appear in the log promptly as soon as anything queries (interface, flows) –
    not only with the next 5-minute round of device_loop. Costs no call to the Home Base. The demo records
    nothing (demo.py)."""
    if demo.active():
        return
    # Recording must never make a query fail; it is reported anyway (at most now and then).
    try:
        history.record(items)
        history.record_power(items)
        history.watch(items)
    except Exception as exc:
        tasks.report("verlauf", type(exc).__name__, "Aufzeichnung gescheitert")
    try:
        power_store.observe(items, sample_seconds=history._sample_seconds())
    except Exception as exc:
        tasks.report("stromverbrauch", type(exc).__name__, "Aufzeichnung gescheitert")


def foreign(d: dict) -> bool:
    """Device controlled by a plugin other than the one it comes from (or that is missing there). It is listed
    under System in the tile of that plugin – e.g. a lamp of the Home Base that is switched through the Hue Bridge."""
    ctl = (d.get("control") or {}).get("plugin")
    return bool(d.get("link_missing") or (ctl and ctl != d.get("plugin")))


def owner_plugin(d: dict) -> str | None:
    """Plugin in whose tile the device is listed."""
    if d.get("link_missing"):
        return d.get("missing_plugin")
    return (d.get("control") or {}).get("plugin") or d.get("plugin")


def dev_row(d: dict) -> dict:
    """Technical row for the administration lists (System page)."""
    errs = list(d.get("errors") or [])
    if d.get("alarm"):
        errs.insert(0, "Alarm")
    if d.get("low_battery") or (d.get("battery") is not None and d["battery"] <= 20):
        errs.append("Batterie schwach" + (f" ({d['battery']} %)" if d.get("battery") is not None else ""))
    src = d.get("source") or {}
    return {"id": d["id"], "name": d.get("name"), "room": d.get("room"), "kind": d.get("kind"),
            "online": bool(d.get("online")), "detail": d.get("status_detail"), "issues": errs,
            "type": d.get("raw_type"), "vendor": d.get("vendor"), "battery": d.get("battery"),
            "last_seen": d.get("last_seen"), "props": d.get("props") or {}, "plugin": d.get("plugin"),
            "adopted": bool(d.get("adopted")),
            # further simple values the plugin passes along in the state (e.g. the status at the hub) – for its tile
            "extra": {k: v for k, v in d.items() if k not in device_merge.RESERVED and k not in LIVE_FIELDS
                      and (isinstance(v, (str, int, float, bool)) or v is None)},
            # device at its source (for actions of the plugin, e.g. "Von Bridge löschen")
            "source": {k: v for k, v in src.items() if k in ("plugin", "device_id", "name", "linked", "renamable")
                       or isinstance(v, (str, int, float, bool))} if src else None,
            "status": ("missing" if d.get("link_missing") else "unreachable" if not d.get("online")
                       else "check" if errs else "ok")}


def removed_devices(plugin_id: str) -> list[dict]:
    """Removed devices for the list of a plugin. An entry whose device no source reports right now is listed
    with the plugin its id belongs to (prefix; everything else with the hub with arbitrary ids)."""
    m = last_merge()
    if m is None:
        rows = [{"id": k, "name": v or k} for k, v in device_store.get()["hidden"].items()]
    else:
        rows = [dict(r) for r in m.removed]
    return [r for r in rows if (r.get("owner") or plugins.owner_by_id(str(r["id"]))) == plugin_id]


def name_differs(d: dict) -> bool:
    """The device has a different name at its source than in ComfyOS (ComfyOS owns the names)."""
    src = d.get("source") or {}
    plugin = plugins.active(src.get("plugin") or "")
    if plugin is None or not src.get("renamable") or not d.get("name"):
        return False
    try:
        return plugin.clean_name(d["name"]) != (src.get("name") or "")
    except Exception:
        return False


async def push_name(d: dict) -> dict | None:
    """Transfer ComfyOS names to the source (e.g. Hue Bridge -> Hue app). None = the source keeps no names."""
    src = d.get("source") or {}
    plugin = plugins.active(src.get("plugin") or "")
    if plugin is None or not src.get("renamable"):
        return None
    if not name_differs(d):
        return {"ok": True, "changed": False}
    try:
        sent = await plugin.rename(src["device_id"], d["name"])
    except PluginError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"Umbenennen fehlgeschlagen ({type(exc).__name__})"}
    if sent is None:
        return None
    return {"ok": True, "changed": True, "name": sent, "shortened": sent != " ".join(d["name"].split())}


def room_pending(devs: list[dict]) -> dict:
    """What still has to be brought up to date in the room hub. Without a hub there is nothing to do."""
    hub = room_hub()
    if hub is None:
        return {"moves": [], "creates": [], "renames": [], "count": 0, "hub": None}
    out = room_assign.pending(devs, floorplan_store.get(), routines_store.room_names(), floorplan_store.room_names())
    out["hub"] = next((r.name for r in plugins.records() if r.id == hub[0]), hub[0])
    return out


async def apply_home(state: str, source: str = "home") -> dict:
    """Set at home or away: save, activate the assigned heating routine, log and trigger the
    heating sync. `source` names the trigger (interface or flow)."""
    try:
        data = home_store.set_state(state)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    target = home_store.routine_for(data["state"])
    if target and routines_store.get(target):
        # The Home Base switches by itself on setHomeState -> only follow locally.
        routines_store.set_active(target, pending=not heating_sync.configured())
    events.log("home", ("Zuhause" if data["state"] == "PRESENT" else "Abwesend") + source_tag(source))
    plugins.notify({"type": "home"})
    heating_scheduler.kick()     # thermostats ComfyOS runs itself get the values of the routine now in force
    sync = await heating_sync.push_now(_push_wait(source))
    return {"home": home_store.get(), "sync": sync}


def _push_wait(source: str) -> float:
    """A flow waits only briefly for the Magenta app: the sync finishes in the background, the
    flow engine (and with it all other automations) must not stall on a sluggish Telekom server. The same holds
    when a plugin switches (e.g. a voice command through Matter that would otherwise run into the time limit)."""
    return 5.0 if source == "flow" or source.startswith("plugin:") else 25.0


async def apply_routine(key: str, source: str = "heating") -> dict:
    """Set a heating routine as the active one, log and sync. 404 if it does not exist."""
    try:
        routines_store.set_active(key)
    except KeyError:
        raise HTTPException(404, "Routine nicht gefunden")
    events.log("heating", f"Heizroutine aktiviert: {routines_store.get(key)['name']}" + (" (Flow)" if source == "flow" else ""))
    heating_scheduler.kick()
    return {"ok": True, "sync": await heating_sync.push_now(_push_wait(source))}


async def flow_devices() -> list[dict]:
    """Devices for the flow engine – always fetched fresh, never from an intermediate state."""
    items = effective(await merged_devices(max_age=0))
    _observe(items)
    return items


async def flow_set_lock(d: dict, locked: bool, flow_id: str) -> bool:
    """Flow node "Gerät sperren": set or lift the lock, log, report to open pages."""
    did = str(d.get("id"))
    try:
        changed = device_store.lock(did, f"flow:{flow_id}") if locked else device_store.unlock(did)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if changed:
        events.log("device", f"{d.get('name') or did} {'gesperrt' if locked else 'entsperrt'} durch Flow")
        publish({"type": "device", "id": did, "locked": locked})
    if locked:
        d["locked"] = True
    else:
        d.pop("locked", None)
    return changed


def flow_push(title: str, text: str) -> None:
    """Flow node "Benachrichtigung": send the push in the background – a sluggish push service holds up no flow."""
    tasks.fire(push.send_all(title, text), "push")


# The return paths of the plugins – registered at import already, so the device list works without the life cycle too.
plugins.on_device_changed, plugins.on_devices_changed, plugins.on_switch = (
    _plugin_device_changed, _plugin_devices_changed, _plugin_switch)
plugins.on_device_gone = _plugin_device_gone
plugins.on_button_pressed = _plugin_button_pressed
plugins.access_hooks.update(devices=_plugin_all_devices, command=_plugin_command, room_lights=_plugin_room_lights,
                            scenes=_plugin_scenes, house_scene=_plugin_house_scene, home_state=_plugin_home_state,
                            home=_plugin_home)


def restart_soon(delay: float = 1.0) -> None:
    """Restart ComfyOS in a moment: the process ends with a code of its own by which the starter recognises that it
    should restart. The delay lets the response still go out."""
    asyncio.get_event_loop().call_later(delay, os._exit, RESTART_EXIT_CODE)


# ---------------------------------------------------------------- Switching paths (control.py)
# Lock, switching, light, scenes and effects have lived in control.py since 0.20. They stay reachable as core.<name>:
# routers, Flow and tests keep using core.apply_device, core.apply_scene, core.scene_step … The import is at the
# end because control.py in turn accesses this module.
from .control import (  # noqa: E402,F401
    apply_device, apply_effect, apply_house, apply_lights, apply_scene, check_lock, LIGHT_KEYS, LOCK_SOURCE,
    publish_effects, publish_scenes, room_keys, room_lamp_states, save_room_variant, scene_from_room, scene_loop,
    scene_step, scene_targets, SCENE_TICK, seed_scene_rooms, send_light, stop_scene, _by_name, _command_text,
    _control, _effect_targets, _lamp, _run_states, _send_effect, _via, _with_variant)
