"""Switching paths of ComfyOS: device lock, switching, light, scenes, room variants, effects, dynamic scenes.

Everything that sets a device passes through here – interface, Flow, scenes, "Alle Lichter", effects and plugins that
switch through `context.switch`. That is why the device lock sits here too: `check_lock` on every path (`apply_device`
and `send_light`), plus `_lamp`, which keeps locked lamps out of room and house commands.

    apply_device   switch or set one device (any kind)
    send_light     set a lamp/socket – last instance before the plugin
    apply_lights   all lamps (of a room) on, off, dim
    apply_scene / apply_house / stop_scene / scene_step     scenes
    apply_effect   effect per room

Up to 0.19 this was in core.py. There it is still reachable as `core.<name>`: core.py imports the names at
its end. Routers, Flow and tests use `core.apply_device`, `core.apply_scene` … – this module is never
imported directly (and never before core.py: the two depend on each other).

What stays in the core is called by this module at run time through `core.` (`core.publish`, `core.all_devices`,
`core.room_names` …). That way replacements in tests (`core.publish = …`, `core.scene_step = …`) work here too.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException

from . import core, device_store, effects_store, events, floorplan_store, light_model, plugins, scenes_store, tasks
from .device_store import DeviceLocked

LIGHT_KEYS = ("brightness", "kelvin", "mirek", "color", "xy", "gradient", "gradient_xy", "gradient_mode", "effect")
LOCK_SOURCE = device_store.LOCK_SOURCE   # the only source that may switch locked devices (flow node "Gerät sperren")


def check_lock(d: dict, source: str) -> None:
    """Enforce the device lock – on every switching path. Raises DeviceLocked (HTTP 423)."""
    if source != LOCK_SOURCE and device_store.is_locked(str(d.get("id"))):
        raise DeviceLocked(d.get("name"))


def _command_text(state: dict, new: dict | None = None, kind: str | None = None) -> str:
    """Short text of the command for the event log (and the response): OFF, ON, 21.5, UP, 50 …"""
    new = new or {}
    if kind == "thermostat" and isinstance(new.get("target"), (int, float)):
        return f"{float(new['target']):.1f}"
    if kind == "cover":
        if state.get("position") is not None:
            try:
                return str(int(round(float(state["position"]))))
            except (TypeError, ValueError):
                pass   # not a number: the direction below applies
        return str(state.get("cover") or ("down" if state.get("on") else "up")).upper()
    if kind == "media":
        return _media_text(state)
    if state.get("on") is False or state.get("brightness") == 0:
        return "OFF"
    parts = []
    if state.get("brightness") is not None:
        parts.append(f"{round(float(state['brightness']))} %")
    if state.get("kelvin") is not None or state.get("mirek") is not None:
        k = state.get("kelvin") or 1_000_000 / float(state["mirek"])
        parts.append(f"Weißton {round(float(k) / 100) * 100} K")
    if state.get("color") or state.get("xy"):
        parts.append("Farbe " + (state.get("color") or ""))
    if state.get("gradient") or state.get("gradient_xy"):
        parts.append("Farbverlauf")
    if state.get("effect"):
        parts.append("Effekt " + ("aus" if state["effect"] == "no_effect" else str(state["effect"])))
    return " · ".join(p.strip() for p in parts) or "ON"


def _media_text(state: dict) -> str:
    """Text of a command to a speaker for the event log: playback, pause, volume, track change, favourite."""
    parts = []
    if state.get("favorite"):
        parts.append("Favorit „" + "".join(c for c in str(state["favorite"]) if c.isprintable())[:60] + "“")
    elif state.get("playing") is not None:
        parts.append("Wiedergabe" if state["playing"] else "Pause")
    if state.get("next"):
        parts.append("nächster Titel")
    if state.get("previous"):
        parts.append("vorheriger Titel")
    if state.get("volume") is not None:
        try:
            parts.append(f"Lautstärke {round(float(state['volume']))} %")
        except (TypeError, ValueError):
            pass   # not a number: the plugin rejects the command, the log then only holds the rest
    if state.get("muted") is not None:
        parts.append("stumm" if state["muted"] else "Ton an")
    if state.get("live_audio") is not None:
        parts.append("Ton vom Browser" if state["live_audio"] else "Ton vom Browser beendet")
    if state.get("join"):
        parts.append("Gruppe beigetreten")
    if state.get("leave"):
        parts.append("Gruppe verlassen")
    return " · ".join(parts) or "Befehl"


def _extra_caps(d: dict, state: dict) -> list[dict]:
    """Labelled capabilities of the device that the command addresses: values without a well-known key (a mode, a
    threshold, a button) that a plugin offers under its own name (`Capability.label`)."""
    return [c for c in d.get("caps") or [] if c.get("label") and c.get("writable") and state.get(c["key"]) is not None]


def _extra_text(caps: list[dict], state: dict) -> str:
    """Short text of a command to labelled capabilities for the event log: `Modus: auto`."""
    parts = []
    for c in caps:
        value = state[c["key"]]
        if c.get("type") == "action":
            parts.append(str(c["label"]))
        elif isinstance(value, bool):
            parts.append(f"{c['label']}: {'ja' if value else 'nein'}")
        else:
            shown = "".join(ch for ch in str(value) if ch.isprintable())[:60]
            parts.append(f"{c['label']}: {shown}" + (f" {c['unit']}" if c.get("unit") else ""))
    return " · ".join(parts)


def _control(d: dict) -> dict:
    """Who controls the device: the plugin from the device row. Without an entry the device is not switchable."""
    ctl = d.get("control")
    if not ctl:
        raise HTTPException(400, f"{d.get('name') or 'Das Gerät'} lässt sich nicht schalten.")
    return ctl


def _via(d: dict) -> str | None:
    """Name of the plugin that switches (for the event log) – unless it is the hub that manages the rooms (the
    main system, e.g. the Home Base): there the suffix would be the same in almost every line."""
    pid = (d.get("control") or {}).get("plugin")
    hub = core.room_hub()
    if not pid or (hub is not None and pid == hub[0]):
        return None
    return next((r.name for r in plugins.records() if r.id == pid), pid)


async def send_light(d: dict, state: dict, source: str = "command") -> dict:
    """Set a lamp/socket – through the plugin that controls it (Hue: colour, white tone, gradient, effect; devices without
    a light state: only on/off). Reports the new state to open pages; raises HTTPException (locked: DeviceLocked, 423)."""
    check_lock(d, source)   # last instance: scenes, "Alle Lichter" and effects pass through here too
    ctl = _control(d)
    info = d.get("light") if d.get("kind") == "light" else None
    if info is None and any(state.get(k) is not None for k in LIGHT_KEYS):
        raise HTTPException(400, f"{d.get('name')} lässt sich nur ein- und ausschalten.")
    if info is None and "on" not in state:
        raise HTTPException(400, f"{d.get('name')} lässt sich nur ein- und ausschalten.")
    new = await plugins.command(d, state, source)
    if new:
        shown = {k: new[k] for k in ("on", "brightness", "light") if k in new}
        d.update(shown)
        core.publish_device(d, **shown)
    return {"id": d["id"], "name": d.get("name"), "command": _command_text(state), "via": ctl["plugin"]}


async def apply_device(d: dict, state: dict, source: str = "command") -> dict:
    """Switch/set one device – the common path for the interface and ComfyOS Flow."""
    check_lock(d, source)
    if d.get("link_missing"):
        raise HTTPException(400, f"{d.get('name')} gibt es bei {d.get('missing_at') or 'der Quelle'} nicht mehr.")
    if not d.get("control"):
        # device without a switchable capability (sensor, contact, presence)
        raise HTTPException(400, f"{d.get('name')} lässt sich nicht schalten.")
    tag = core.source_tag(source, d)
    via = _via(d)
    extras = _extra_caps(d, state)
    if d.get("kind") in ("light", "switch") and not extras:
        result = await send_light(d, state, source)
        if any(state.get(k) is not None for k in LIGHT_KEYS):
            scenes_store.exclude(d["id"])   # adjusted individually: a dynamic scene leaves this lamp alone
        events.log("command", f"{result.get('name', d['id'])}: {result.get('command')}" + (f" ({via})" if via else "") + tag)
        return result
    ctl = _control(d)
    new = await plugins.command(d, state, source) or {}
    shown = {k: v for k, v in new.items() if k in core.LIVE_FIELDS and k != "locked"}    # plugins report the new state
    if shown:
        d.update(shown)
        core.publish_device(d, **shown)
    d.update({c["key"]: new[c["key"]] for c in extras if c["key"] in new})       # labelled values are not sent live
    text = _extra_text(extras, state) if extras else _command_text(state, new, d.get("kind"))
    result = {"id": d["id"], "name": d.get("name"), "command": text, "via": ctl["plugin"]}
    events.log("command", f"{result.get('name', d['id'])}: {result.get('command')}" + (f" ({via})" if via else "") + tag)
    return result


def _lamp(d: dict) -> bool:
    """Switchable lamp for room/house commands, scenes and effects. Locked lamps drop out (skipped,
    no error); send_light still checks against the store once more."""
    return d.get("kind") == "light" and bool(d.get("online")) and bool(d.get("control_item")) and not d.get("locked")


async def apply_lights(on: bool, room_ids: list[str] | None, source: str = "command",
                       brightness: float | None = None) -> dict:
    """Switch all lamps (optionally only of certain rooms) on/off or dim them. Sockets stay untouched.
    Switching off ends active scenes of these rooms (without restoring)."""
    targets = [d for d in await core.all_devices() if _lamp(d) and (room_ids is None or d.get("room_id") in room_ids)]
    switched, failed = 0, []
    for d in targets:
        st = {"on": True, "brightness": brightness} if on and brightness is not None and d.get("light") else {"on": on}
        try:
            await send_light(d, st)
            pause = plugins.command_interval(d)   # e.g. Hue: at most ~10 lamp commands per second
            if pause:
                await asyncio.sleep(pause)
            switched += 1
        except DeviceLocked:
            continue   # locked just now: skipped, no error
        except HTTPException as exc:
            failed.append(f"{d.get('name')}: {exc.detail}")
    if not on and scenes_store.clear(room_keys(room_ids)):
        publish_scenes()
    if not on and effects_store.clear(room_ids):
        publish_effects()
    where = next((d.get("room") for d in targets), None) if room_ids else None
    if switched:
        n = "Lampe" if switched == 1 else "Lampen"
        what = f"auf {round(brightness)} %" if on and brightness is not None else "eingeschaltet" if on else "ausgeschaltet"
        events.log("command", f"{where or 'Alle Lichter'}: {switched} {n} {what}" + core.source_tag(source))
    return {"ok": not failed, "switched": switched, "failed": failed}


# ---------------------------------------------------------------- Szenen
def room_keys(room_ids: list[str] | None) -> list[str] | None:
    """Room ids as target keys for scenes ("room:<id>"); None stays None (= everywhere)."""
    return None if room_ids is None else ["room:" + str(r) for r in room_ids]


def publish_scenes() -> None:
    """Report the state of the scenes to all open pages."""
    core.publish({"type": "scenes", **scenes_store.public()})


def _by_name(devs: list[dict]) -> list[dict]:
    """Devices sorted by name (by id for equal names) – that way every lamp gets the same colour on every run."""
    return sorted(devs, key=lambda d: (str(d.get("name") or "").casefold(), str(d.get("id"))))


def scene_targets(devs: list[dict], room_ids: list[str] | None = None, device_ids: list[str] | None = None,
                  group_id: str | None = None) -> list[tuple[str | None, str, list[dict]]]:
    """(target key or None, display name, lamps) – rooms, a device group of the floor plan or single lamps."""
    out = []
    for rid in room_ids or []:
        lamps = _by_name([d for d in devs if _lamp(d) and d.get("room_id") == rid])
        name = next((d.get("room") for d in devs if d.get("room_id") == rid), None) or core.room_names().get(rid, rid)
        out.append(("room:" + rid, name, lamps))
    if group_id:
        g = next((g for g in floorplan_store.groups(floorplan_store.get()) if g.get("id") == group_id), None)
        if g is None:
            raise HTTPException(404, "Gruppe nicht gefunden.")
        members = set(g.get("members") or [])
        out.append(("group:" + group_id, g.get("name") or "Gruppe",
                    _by_name([d for d in devs if _lamp(d) and d["id"] in members])))
    if device_ids:
        want = set(device_ids)
        out.append((None, "", _by_name([d for d in devs if _lamp(d) and d["id"] in want])))
    return out


async def _run_states(plan: list[tuple[dict, dict]]) -> tuple[int, list[str]]:
    """Set lamps to their target states one after the other, with the pause the plugin asks for between commands.
    Locked lamps are skipped. Returns the number of switched lamps and the error texts."""
    ok, failed = 0, []
    for d, st in plan:
        try:
            await send_light(d, st)
            ok += 1
            pause = plugins.command_interval(d)
            if pause:
                await asyncio.sleep(pause)
        except DeviceLocked:
            continue   # locked: skipped, no error
        except HTTPException as exc:
            failed.append(f"{d.get('name')}: {exc.detail}")
    return ok, failed


def _with_variant(plan: list[tuple[dict, dict]], lamps: dict[str, dict],
                  brightness: float | None) -> list[tuple[dict, dict]]:
    """Lay the room variant over the palette distribution: lamps with an entry get their stored state."""
    out = []
    for d, st in plan:
        v = lamps.get(d["id"])
        if v is None:
            out.append((d, st))
        elif not v.get("on"):
            out.append((d, {"on": False}))
        elif d.get("light") is None:   # lamp without colour control: only switch on
            out.append((d, {"on": True}))
        else:
            s2 = {**v, "duration": 400}
            if brightness is not None:
                s2["brightness"] = brightness
            out.append((d, s2))
    return out


def seed_scene_rooms(room_ids: list[str]) -> None:
    """First assignment of the scenes to the rooms as soon as rooms are known (see scenes_store.seed_rooms)."""
    try:
        if scenes_store.seed_rooms(room_ids):
            events.log("scene", "Szenen-Vorlagen allen Räumen zugeordnet")
    except Exception as exc:
        events.log("scene", f"Szenen-Zuordnung übersprungen ({type(exc).__name__})")


async def apply_scene(sid: str, room_ids: list[str] | None = None, device_ids: list[str] | None = None,
                      group_id: str | None = None, brightness: float | None = None, source: str = "command") -> dict:
    """Apply a scene to rooms, a group or single lamps. Rooms/groups remember it as active
    (background, ending with restore, dynamic scenes)."""
    sc = scenes_store.scene(sid)
    if sc is None:
        raise HTTPException(404, "Szene nicht gefunden.")
    if brightness is not None:
        brightness = max(1.0, min(100.0, float(brightness)))
    targets = scene_targets(await core.all_devices(), room_ids, device_ids, group_id)
    if not any(lamps for _, _, lamps in targets):
        raise HTTPException(400, "Keine erreichbaren Lampen für diese Szene.")
    switched, failed, names, fx_rooms = 0, [], [], []
    for key, name, lamps in targets:
        if not lamps:
            continue
        snapshot = {d["id"]: light_model.restore_state(d) for d in lamps}
        plan = light_model.scene_states(sc["palette"], lamps, 0, brightness)
        rid = key[5:] if key and key.startswith("room:") else None
        variant = (sc.get("variants") or {}).get(rid) if rid else None
        if variant:   # state of this scene stored in the room
            plan = _with_variant(plan, variant["lamps"], brightness)
        ok, bad = await _run_states(plan)
        switched, failed = switched + ok, failed + bad
        if key:
            scenes_store.activate(key, sid, [d["id"] for d in lamps], snapshot, brightness)
        if rid and effects_store.room(rid):
            fx_rooms.append(rid)
        names.append(name or ", ".join(str(d.get("name") or "") for d in lamps[:3]))
    publish_scenes()
    if fx_rooms:   # a scene change leaves the effect in place (the scene colour is set anew)
        devs = await core.all_devices()
        for rid in fx_rooms:
            entry = effects_store.room(rid)
            if entry:
                await _send_effect(rid, entry, devs)
    events.log("scene", f"Szene „{sc['name']}“: {', '.join(names)}" + core.source_tag(source))
    return {"ok": not failed, "switched": switched, "failed": failed}


async def apply_house(sid: str, source: str = "command") -> dict:
    """House scene: set the scene in all assigned rooms, each room with its variant.
    Rooms that are not assigned stay unchanged."""
    sc = scenes_store.scene(sid)
    if sc is None:
        raise HTTPException(404, "Szene nicht gefunden.")
    if not sc["rooms"]:
        raise HTTPException(400, f"„{sc['name']}“ ist noch keinem Raum zugeordnet.")
    devs = await core.all_devices()
    rooms = [r for r in sc["rooms"] if any(_lamp(d) and d.get("room_id") == r for d in devs)]
    if not rooms:
        raise HTTPException(400, "In den zugeordneten Räumen gibt es keine erreichbaren Lampen.")
    return await apply_scene(sid, rooms, source=source)


async def room_lamp_states(room_id: str) -> tuple[list[dict], dict[str, dict]]:
    """Lamps of a room + their current state (for "Aktuellen Zustand speichern"). Effects do not count."""
    lamps = _by_name([d for d in await core.all_devices() if d.get("kind") == "light" and d.get("room_id") == room_id
                      and d.get("online") and d.get("control_item")])
    if not lamps:
        raise HTTPException(400, "Keine erreichbaren Lampen in diesem Raum.")
    states = {d["id"]: {k: v for k, v in light_model.restore_state(d).items() if k != "effect"} for d in lamps}
    return lamps, states


async def save_room_variant(sid: str, room_id: str) -> dict:
    """Save the current light of a room as the room variant of a scene."""
    sc = scenes_store.scene(sid)
    if sc is None:
        raise HTTPException(404, "Szene nicht gefunden.")
    _, states = await room_lamp_states(room_id)
    try:
        sc = scenes_store.set_variant(sid, room_id, states)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    publish_scenes()
    events.log("scene", f"Szene „{sc['name']}“: Zustand für {core.room_names().get(room_id, room_id)} gespeichert")
    return sc


async def scene_from_room(room_id: str, name: str) -> dict:
    """Current state of a room as a new scene: palette from the lamp colours + variant for the room."""
    lamps, states = await room_lamp_states(room_id)
    data = {"name": name, "palette": light_model.palette_from_lamps(lamps, scenes_store.MAX_COLORS),
            "rooms": [room_id], "variants": {room_id: {"lamps": states, "updated": time.time()}}}
    try:
        sc = scenes_store.create(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    publish_scenes()
    events.log("scene", f"Szene „{sc['name']}“ aus {core.room_names().get(room_id, room_id)} angelegt")
    return sc


# ---------------------------------------------------------------- Effects (independent of scenes)
def publish_effects() -> None:
    """Report the state of the light effects to all open pages."""
    core.publish({"type": "effects", "rooms": effects_store.get()["rooms"]})


def _effect_targets(room_id: str, entry: dict, devs: list[dict]) -> list[dict]:
    """Lamps on which the effect of a room runs: all lamps of the room or the selected ones, sorted by
    name."""
    lamps = [d for d in devs if _lamp(d) and d.get("room_id") == room_id and d.get("light")]
    if isinstance(entry.get("target"), list):
        want = set(entry["target"])
        lamps = [d for d in lamps if d["id"] in want]
    return _by_name(lamps)


async def _send_effect(room_id: str, entry: dict, devs: list[dict]) -> tuple[int, list[str], int]:
    """Send an effect to the target lamps of a room. Returns: (switched, errors, without this effect)."""
    lamps = _effect_targets(room_id, entry, devs)
    able = [d for d in lamps if entry["effect"] in ((d["light"].get("caps") or {}).get("effects") or [])]
    colours: dict[str, dict] = {}
    if entry["color_source"] == "scene":
        act = scenes_store.get()["active"].get("room:" + room_id)
        sc = scenes_store.scene(act["scene"]) if act else None
        if sc:
            colours = {d["id"]: st for d, st in light_model.scene_states(sc["palette"], able)}
    base: dict = {"on": True, "effect": entry["effect"]}
    if entry.get("speed") is not None:
        base["effect_speed"] = entry["speed"] / 100
    plan = []
    for d in able:
        st = dict(base)
        if entry["color_source"] == "custom":
            if entry.get("color"):
                st["effect_color"] = entry["color"]
            elif entry.get("kelvin"):
                st["effect_mirek"] = light_model.kelvin_to_mirek(entry["kelvin"])
        elif d["id"] in colours:
            c = colours[d["id"]]
            if c.get("color") or c.get("gradient"):
                st["effect_color"] = c.get("color") or c["gradient"][0]
            elif c.get("mirek"):
                st["effect_mirek"] = c["mirek"]
        plan.append((d, st))
    ok, failed = await _run_states(plan)
    return ok, failed, len(lamps) - len(able)


async def apply_effect(room_id: str, data: dict | None, source: str = "command") -> dict:
    """Set an effect in the room (data) or end it (None). Runs independently of the scene of the room."""
    devs = await core.all_devices()
    room_lamps = [d for d in devs if _lamp(d) and d.get("room_id") == room_id]
    where = core.room_names().get(room_id) or next((d.get("room") for d in room_lamps), None) or room_id
    tag = " (Flow)" if source == "flow" else ""
    if data is None:
        old = effects_store.room(room_id)
        effects_store.clear([room_id])
        stop = [d for d in (_effect_targets(room_id, old, devs) if old else room_lamps)
                if (d.get("light") or {}).get("effect")]
        ok, failed = await _run_states([(d, {"effect": "no_effect"}) for d in stop])
        publish_effects()
        if old:
            events.log("command", f"{where}: Effekt aus{tag}")
        return {"ok": not failed, "switched": ok, "failed": failed, "effect": None}
    if not room_lamps:
        raise HTTPException(400, "Keine erreichbaren Lampen in diesem Raum.")
    try:
        entry = effects_store.set_room(room_id, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    old_targets = {d["id"] for d in room_lamps if (d.get("light") or {}).get("effect")}
    ok, failed, unable = await _send_effect(room_id, entry, devs)
    # lamps that do not (or no longer) belong to the target but still show an effect: end the effect there
    now = {d["id"] for d in _effect_targets(room_id, entry, devs)}
    await _run_states([(d, {"effect": "no_effect"}) for d in room_lamps if d["id"] in old_targets - now])
    publish_effects()
    events.log("command", f"{where}: Effekt {entry['effect']} ({ok} Lampe{'' if ok == 1 else 'n'}){tag}")
    return {"ok": not failed, "switched": ok, "failed": failed, "unable": unable, "effect": entry}


async def stop_scene(keys: list[str] | None, restore: bool = True, source: str = "command") -> dict:
    """End active scenes (None = all) and restore the state from before."""
    data = scenes_store.get()
    keys = list(data["active"]) if keys is None else [k for k in keys if k in data["active"]]
    entries = [e for e in (scenes_store.deactivate(k) for k in keys) if e]
    restored, failed = 0, []
    if restore and entries:
        devs = {d["id"]: d for d in await core.all_devices()}
        for e in entries:
            plan = [(devs[did], {**st, "duration": 400} if st.get("on") else st)
                    for did, st in e.get("snapshot", {}).items()
                    if did in devs and devs[did].get("online") and devs[did].get("control_item")
                    and not devs[did].get("locked")]
            ok, bad = await _run_states(plan)
            restored, failed = restored + ok, failed + bad
    if entries:
        publish_scenes()
        names = {s["id"]: s["name"] for s in data["scenes"]}
        events.log("scene", "Szene beendet: " + ", ".join(names.get(e["scene"], "?") for e in entries)
                   + core.source_tag(source))
    return {"ok": not failed, "stopped": len(entries), "restored": restored, "failed": failed}


SCENE_TICK = 2.0


async def scene_step(now: float | None = None) -> int:
    """Turn dynamic scenes one colour further when their interval is up. Only lamps that are on and not adjusted
    individually; brightness and on/off stay as they are."""
    now = time.time() if now is None else now
    data = scenes_store.get()
    scenes = {s["id"]: s for s in data["scenes"]}
    due = [(k, v, scenes[v["scene"]]) for k, v in data["active"].items()
           if scenes.get(v["scene"], {}).get("dynamic") and now - v["step_at"] >= scenes[v["scene"]]["speed"]]
    if not due:
        return 0
    devs = {d["id"]: d for d in core.effective(await core.merged_devices(max_age=SCENE_TICK))}
    sent = 0
    for key, v, sc in due:
        offset = v["offset"] + 1
        lamps = [devs[i] for i in v["devices"] if i in devs]
        plan = []
        for d, st in light_model.scene_states(sc["palette"], lamps, offset, duration=min(60_000, int(sc["speed"] * 900))):
            if d["id"] in v["excluded"] or not d.get("on") or not d.get("online") or not d.get("light") \
                    or d.get("locked") or d["light"].get("effect"):   # locked or an effect is running: do not paint over
                continue
            plan.append((d, {k: x for k, x in st.items() if k not in ("on", "brightness")}))
        ok, _ = await _run_states(plan)
        sent += ok
        scenes_store.step(key, offset, now)
    return sent


async def scene_loop():
    """Every SCENE_TICK seconds check whether a dynamic scene should turn one colour further."""
    while True:
        await asyncio.sleep(SCENE_TICK)
        try:
            await core.scene_step()
        except Exception as exc:
            tasks.report("dynamische-szenen", type(exc).__name__)
