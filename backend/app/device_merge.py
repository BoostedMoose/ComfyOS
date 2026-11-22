"""Merge the devices of all plugins into ONE device list (pure, without I/O) – the core step "link devices".

No plugin is imported here; the work is done with `plugin_api.Device` only. Rules so that no device shows up
twice and it stays clear who owns name and room:

  * **Linking:** the same device can be known to several plugins (a lamp on the Hue Bridge that the Home
    Base also lists). Devices with a common `link_keys` entry are one. The **relayed** device (`relayed`,
    the hub's view) keeps id, name and room – floor plan, flows, scenes and locks point to it. State,
    kind and switching come from the **direct** device of the other plugin.
  * **Parts of a device (since 0.22):** a direct device can carry several keys – a speaker room
    those of its surround speakers or of the second speaker of a stereo pair. If a hub lists these parts one by one,
    the device still stays one: it appears under the relayed device with the **first** key (the
    main device); if the hub does not have that one, under the first part it reports. The other parts are absorbed
    into it (`absorbed`) – they are in no list but count as known, so that nothing about them is forgotten.
  * **Missing:** if a running plugin owns the key range (`link_prefixes`) but does not report the device,
    the device no longer exists there -> `link_missing` (orphaned, not switchable; can be removed).
  * **Found:** a device reported only as `discovered` without a link only becomes a device of its own after it is
    adopted (`device_store.adopted`); until then it is listed as a candidate with the plugin ("Neu"). Its room
    lives locally in the store, otherwise the room of the same name at the source counts as a suggestion.
  * **Removed:** hidden devices (`device_store.hidden`) drop out entirely.
  * If a plugin is unreachable (`devices is None`), the list of the others stays unchanged – a lamp
    then runs through the hub that relays it again.
  * **Room:** a hub that manages rooms reports the room of its devices itself (`Device.room_id`). All other devices
    get their room from the device store (`rooms`), otherwise the room of the same name at the source is a suggestion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .plugin_api import Device, LightState, MediaState

_ORDER = {"thermostat": 0, "contact": 1, "sensor": 2, "smoke": 3, "motion": 4, "presence": 4, "switch": 5, "light": 6,
          "cover": 7, "media": 8, "button": 9, "unknown": 10}
# Fields of the device row that the core owns: a plugin cannot overwrite them through `Device.state`
# (otherwise a device could e.g. take on a foreign id, a room or the control of another plugin).
RESERVED = frozenset({"id", "name", "room", "room_id", "kind", "online", "status_detail", "raw_type", "vendor", "battery",
                      "low_battery", "last_seen", "control", "control_item", "errors", "light", "values", "props", "plugin",
                      "source", "locked", "locked_by", "adopted", "custom_name", "link_missing", "missing_at",
                      "missing_plugin", "power_w", "energy_kwh", "local_room", "hb_room_id", "hb_room", "room_pending",
                      "caps", "media", "buttons", "part_of"})
MEDIA_TEXT = 200                          # a text from the playback may be this long (title, artist …)
MEDIA_LIST = 40                           # a row carries at most this many favourites or group members
IMAGE_ID = r"^[A-Za-z0-9_.-]{1,120}$"     # form of an image id (route /api/media/image/<plugin>/<id>)
MAX_CAPS = 40                             # a device row carries at most this many capabilities
MAX_BUTTONS = 24                          # a device row carries at most this many buttons (since 0.23)
BUTTON_ID = r"^[A-Za-z0-9_.:-]{1,40}$"    # form of a button id (as plugins.BUTTON_ID_RE)
NO_ROOM = "Nicht zugeordnet"
UNNAMED_ROOM = "Raum ohne Namen"
RoomLookup = Callable[[str], "tuple[str, str] | None"]   # key or name -> (room id, name)


@dataclass
class Source:
    """What a plugin delivered in this round."""
    plugin_id: str
    name: str
    devices: list[Device] | None            # None = unreachable or error
    link_prefixes: list[str] = field(default_factory=list)


@dataclass
class Merged:
    """Result of the merge: visible devices, found devices per plugin, removed ones and what is needed for cleaning
    up and for the return paths."""
    rows: list[dict[str, Any]]                                   # visible devices
    candidates: dict[str, list[Device]]                          # per plugin: found, not adopted yet
    index: dict[tuple[str, str], str]                            # (plugin, device id at the plugin) -> id in ComfyOS
    removed: list[dict[str, Any]]                                # removed devices (for the lists under System)
    known_ids: set[str]                                          # alles, was irgendeine Quelle kennt
    complete: bool                                               # all sources delivered (then cleaning up is allowed)


def light_dict(light: LightState | None) -> dict[str, Any] | None:
    """Light state of the plugin API -> the form core and interface work with (light_model, lights.js)."""
    if light is None:
        return None
    caps = light.capabilities
    return {"caps": {"ct": list(caps.white_range) if caps.white_range else None, "color": bool(caps.color),
                     "gradient": int(caps.gradient_points or 0), "gradient_modes": list(caps.gradient_modes),
                     "effects": list(caps.effects), "effect_params": bool(caps.effect_parameters)},
            "mode": light.mode, "xy": list(light.xy) if light.xy else None, "mirek": light.mirek,
            "points": [list(p) for p in light.gradient], "gmode": light.gradient_mode, "effect": light.effect}


def _text(value: Any) -> str | None:
    """Foreign text from the playback: as text, without control characters, shortened; empty = None."""
    if value is None:
        return None
    text = "".join(c for c in str(value) if c.isprintable()).strip()[:MEDIA_TEXT]
    return text or None


def _seconds(value: Any) -> int | None:
    """Number of seconds from the playback: whole number from 0 up to one day, otherwise None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return int(value) if 0 <= value <= 86400 else None


def media_dict(media: MediaState | None, plugin_id: str) -> dict[str, Any] | None:
    """Playback of the plugin API -> the form core and interface work with. Everything in it is foreign text and is
    limited; the image id becomes the address under which the core fetches the cover picture from the plugin (the
    browser never loads from the source)."""
    if media is None:
        return None
    image = str(media.image_id or "")
    return {"title": _text(media.title), "artist": _text(media.artist), "album": _text(media.album),
            "source": _text(media.source),
            "image": f"/api/media/image/{plugin_id}/{image}" if plugin_id and re.match(IMAGE_ID, image) else None,
            "position": _seconds(media.position), "duration": _seconds(media.duration),
            "group": [t for t in (_text(name) for name in list(media.group)[:MEDIA_LIST]) if t],
            "coordinator": _text(media.coordinator),
            "favorites": [t for t in (_text(name) for name in list(media.favorites)[:MEDIA_LIST]) if t]}


def plain(changes: dict[str, Any], plugin_id: str = "") -> dict[str, Any]:
    """State change of a plugin in row form (LightState and MediaState -> dict)."""
    return {k: (light_dict(v) if isinstance(v, LightState) else media_dict(v, plugin_id) if isinstance(v, MediaState) else v)
            for k, v in changes.items()}


def _writable(d: Device) -> bool:
    """Does the device accept a command at all? Only then does the row get a control."""
    return any(c.writable for c in d.capabilities)


def _caps(d: Device) -> list[dict[str, Any]]:
    """Capabilities of a plugin device in row form: what it reports (`readable`) and what it accepts (`writable`).

    Through them interface, Flow and widgets find the fitting devices – no matter which plugin delivers them
    (a speaker is whatever can do `playing`). The row carries the list as `caps`.
    """
    out = []
    for c in list(d.capabilities)[:MAX_CAPS]:
        entry: dict[str, Any] = {"key": str(c.key)[:40], "type": str(c.type), "readable": bool(c.readable),
                                 "writable": bool(c.writable)}
        for name in ("unit", "minimum", "maximum", "step"):
            if getattr(c, name) is not None:
                entry[name] = getattr(c, name)
        if c.options:
            entry["options"] = [str(o) for o in c.options][:40]
        if c.label:
            entry["label"] = str(c.label)[:60]
        out.append(entry)
    return out


def buttons_list(d: Device) -> list[dict[str, str]]:
    """Buttons of a plugin device in row form: [{"id", "name"}], each id once, names as foreign text (limited).
    Flows offer them by name; a press arrives through `context.button_pressed`."""
    out, seen = [], set()
    for button in list(d.buttons or [])[:MAX_BUTTONS]:
        bid = str(getattr(button, "id", ""))
        if not re.match(BUTTON_ID, bid) or bid in seen:
            continue
        seen.add(bid)
        out.append({"id": bid, "name": _text(getattr(button, "name", "")) or bid})
    return out


def _merge_caps(base: list[dict[str, Any]], over: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lay the capabilities of the direct device over those of the relayed one (per key the direct device wins)."""
    keys = {c["key"] for c in over}
    return (over + [c for c in base if c["key"] not in keys])[:MAX_CAPS]


def _state(d: Device) -> dict[str, Any]:
    """State values of a plugin device – without the fields the core owns."""
    return {k: v for k, v in d.state.items() if isinstance(k, str) and k not in RESERVED and not k.startswith("_")}


def _source_info(src: Source, d: Device, linked: bool) -> dict[str, Any]:
    """Details about one source of a device for the detail view: which plugin, under which id and which name
    there, direct or linked through a hub."""
    return {**d.extra, "plugin": src.plugin_id, "device_id": d.id, "name": d.name, "linked": linked,
            "renamable": bool(d.renamable), "online": d.online}


def _row(src: Source, d: Device) -> dict[str, Any]:
    """Device row as core, Flow and interface know it."""
    online = True if d.online is None else bool(d.online)
    values = [{"id": str(r.key), "label": str(r.label), "state": str(r.value)} for r in d.readings][:20]
    row: dict[str, Any] = {
        "id": d.id, "name": d.name or d.id, "room": (d.room_hint or UNNAMED_ROOM) if d.room_id else NO_ROOM,
        "room_id": d.room_id or None, "kind": d.kind, "online": online,
        "status_detail": d.status_detail, "raw_type": d.type_name or f"{src.plugin_id}:{d.kind}", "vendor": d.vendor,
        "battery": d.battery, "low_battery": d.low_battery, "last_seen": d.last_seen, "temperature": None, "on": None,
        "brightness": None, "power_w": d.power_w, "energy_kwh": d.energy_kwh, "control_item": None,
        "errors": [str(p) for p in d.problems],
        "light": light_dict(d.light) if d.kind == "light" else None,
        "values": values or ([{"id": "model", "label": "Modell", "state": d.model}] if d.model else []),
        "props": dict(d.details), "plugin": src.plugin_id, "source": _source_info(src, d, False), "caps": _caps(d)}
    if d.media is not None:
        row["media"] = media_dict(d.media, src.plugin_id)
    if d.buttons:
        row["buttons"] = buttons_list(d)
    if d.part_of:
        row["part_of"] = (src.plugin_id, str(d.part_of))     # resolved to the id of a row at the end of merge()
    row.update(_state(d))
    if _writable(d):
        row["control"] = {"plugin": src.plugin_id, "device_id": d.id}
        row["control_item"] = f"{src.plugin_id}:{d.id}"
    return row


def _overlay(row: dict[str, Any], src: Source, d: Device) -> None:
    """Lay the direct device over the relayed one: id, name, room stay; state and switching change."""
    row["kind"] = d.kind
    if d.online is not None:
        row["online"] = bool(d.online)
        row["status_detail"] = d.status_detail
    row.update(_state(d))
    if d.kind in ("light", "switch"):
        row["light"] = light_dict(d.light) if d.kind == "light" else None
    if d.media is not None:
        row["media"] = media_dict(d.media, src.plugin_id)
    if d.buttons:
        row["buttons"] = buttons_list(d)
    if d.part_of:
        row["part_of"] = (src.plugin_id, str(d.part_of))
    if _writable(d):
        row["control"] = {"plugin": src.plugin_id, "device_id": d.id}
        row["control_item"] = f"{src.plugin_id}:{d.id}"
    row["props"] = {**(row.get("props") or {}), **d.details}
    row["source"] = _source_info(src, d, True)
    row["caps"] = _merge_caps(row.get("caps") or [], _caps(d))


def merge(sources: list[Source], store: dict[str, Any],
          make_resolvers: Callable[[list[dict[str, Any]]], tuple[RoomLookup, RoomLookup]]) -> Merged:
    """Merge the devices of all plugins into one list.

    If a hub reports a device that another plugin addresses directly, it becomes one row (the direct device
    leads, the hub delivers what only it knows). The steps: collect direct devices by key, link relayed ones
    or mark them as orphaned, take over independent ones, list found ones as a device or as a candidate depending on
    the user's decision. `store` is the content of devices.json, `make_resolvers` returns the assignment to
    rooms."""
    hidden, adopted = store.get("hidden") or {}, set(store.get("adopted") or [])
    local_rooms, names = store.get("rooms") or {}, store.get("names") or {}
    live = [s for s in sources if s.devices is not None]

    # 1) Direct devices by key; key ranges that a running plugin owns
    direct: dict[str, tuple[Source, Device]] = {}
    for s in live:
        for d in s.devices or []:
            if not d.relayed:
                for key in d.link_keys:
                    direct.setdefault(key, (s, d))
    owners = [(s, p) for s in live for p in s.link_prefixes if p]

    rows: list[dict[str, Any]] = []
    index: dict[tuple[str, str], str] = {}
    used: set[tuple[str, str]] = set()
    seen: set[str] = set()

    def add(row: dict[str, Any], key: tuple[str, str]) -> bool:
        if row.get("id") in seen:      # two sources with the same id: the first one counts
            return False
        seen.add(row["id"])
        index[key] = row["id"]
        rows.append(row)
        return True

    # 2) Relayed devices: link them or mark them as orphaned. The main device (first key of the
    #    direct device) comes first; further parts of the same device are absorbed into it.
    def link_rank(pair: tuple[Source, Device]) -> int:
        s, d = pair
        ranks = [direct[k][1].link_keys.index(k) for k in d.link_keys if k in direct and direct[k][0].plugin_id != s.plugin_id]
        return min(ranks) if ranks else 0

    relayed = sorted(((s, d) for s in live for d in s.devices or [] if d.relayed), key=link_rank)   # stabil sortiert
    absorbed: set[str] = set()
    for s, d in relayed:
        hit = next((direct[k] for k in d.link_keys if k in direct and direct[k][0].plugin_id != s.plugin_id), None)
        if hit is not None and (hit[0].plugin_id, hit[1].id) in used:
            absorbed.add(d.id)                           # part of a device that is already in the list
            continue
        row = _row(s, d)
        if hit is not None:
            _overlay(row, *hit)
            used.add((hit[0].plugin_id, hit[1].id))
            index[(hit[0].plugin_id, hit[1].id)] = row["id"]
        else:
            owner = next((o for o, p in owners if o.plugin_id != s.plugin_id and any(k.startswith(p) for k in d.link_keys)), None)
            if owner is not None:
                row.update(online=False, on=None, control_item=None, link_missing=True, missing_at=owner.name,
                           missing_plugin=owner.plugin_id, status_detail=f"Gibt es bei {owner.name} nicht mehr")
                row.pop("control", None)
        add(row, (s.plugin_id, d.id))

    # 3) Independent devices (not "found")
    roomless: list[tuple[dict[str, Any], Device]] = []
    for s in live:
        for d in s.devices or []:
            if d.relayed or d.discovered or (s.plugin_id, d.id) in used:
                continue
            row = _row(s, d)
            if add(row, (s.plugin_id, d.id)) and not row.get("room_id"):
                roomless.append((row, d))

    # devices without a room from their hub: room from the device store, otherwise the room of the same name as a suggestion
    for_key, for_name = make_resolvers(rows)

    def place(row: dict[str, Any], d: Device) -> None:
        key = local_rooms.get(d.id)
        room = for_key(key) if key else for_name(d.room_hint) if d.room_hint else None
        row.update(room=room[1] if room else NO_ROOM, room_id=room[0] if room else None)

    for row, d in roomless:
        place(row, d)

    # 4) Found devices: adopted -> own device with a local room, otherwise a candidate with the plugin
    candidates: dict[str, list[Device]] = {}
    known = set(seen) | absorbed
    for s in live:
        for d in s.devices or []:
            if d.relayed or not d.discovered or (s.plugin_id, d.id) in used:
                continue
            known.add(d.id)
            if d.id not in adopted:
                if d.id not in hidden:
                    candidates.setdefault(s.plugin_id, []).append(d)
                continue
            row = _row(s, d)
            place(row, d)
            row["adopted"] = True
            add(row, (s.plugin_id, d.id))

    removed = []
    by_id = {r["id"]: r for r in rows}
    for did, name in hidden.items():
        r = by_id.get(did)
        r = r or {}
        ctl = (r.get("control") or {}).get("plugin")
        # "owner" = plugin in whose list the device appears: where it is missing, who controls it, otherwise its source
        removed.append({"id": did, "name": name or r.get("name") or did, "plugin": r.get("plugin"),
                        "owner": r.get("missing_plugin") or ctl or r.get("plugin"),
                        "orphan": bool(r.get("link_missing")), "missing_at": r.get("missing_at")})
    rows = [r for r in rows if r["id"] not in hidden]

    # a further part names its main device as its plugin knows it: point to the row that shows that device now
    # (a hub may lend it its id) – or to nothing, if it is not shown
    shown = {r["id"] for r in rows}
    for r in rows:
        part = r.pop("part_of", None)
        main = index.get(part) if isinstance(part, tuple) else None
        if main in shown and main != r["id"]:
            r["part_of"] = main

    for r in rows:
        custom = names.get(r["id"])
        if custom:
            r["custom_name"] = custom
            r["name"] = custom

    rows.sort(key=lambda x: (not x.get("online"), _ORDER.get(x.get("kind"), 9), x.get("room") or "", x.get("name") or ""))
    return Merged(rows, candidates, index, removed, known, complete=len(live) == len(sources))
