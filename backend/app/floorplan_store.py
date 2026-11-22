"""Floor plan: walls, rooms (from walls), doors/windows/radiators, device positions, room names.

floorplan.json:
  { "width": 24, "height": 14, "address": "", "heatmap": false,
    "walls":    [ {"id", "pts": [[x, y], …], "bend": [null | [cx, cy], …]} ],
    "rooms":    [ {"id", "name", "room_id", "x", "y"} ],
    "openings": [ {"id", "type": "door"|"window"|"passage"|"radiator", "a": [x, y], "b": [x, y], "side": 1|-1, "device_id",
                    "extra_ids": [device_id, …]} ],   # device_id = first docked device, extra_ids = further ones (optional)
    "devices":  { "<device_id>": {"x", "y", "furniture", "size", "rot", "stretch"} },   # furniture = plan symbol
                # instead of the standard bubble, with its size and turn; stretch (since 0.28) = its width ÷ its height
                # against the drawn proportion (1 is not stored)
    "groups":   [ {"id", "name", "x", "y", "members": [device_id, …]} ],   # device groups (one circle for several devices)
    "home_name": "Wohnung",
    "shadow": true, "north": 0,    # shadow by the position of the sun; north = direction of north in degrees, clockwise from "up"
    "fx": true, "fx_level": "normal" | "subtle",    # weather effects around the floor plan (Pro, display only)
    "decks":    [ {"id", "pts": [[x, y], …], "bend": […], "surface"} ],   # surface: "tiles" | "plain", without it wood;   # balconies and terraces: a closed outline, drawn like a
                                                               # wall (first point repeated at the end); they are outside
    "stairs":   [ {"id", "a": [x, y], "b": [x, y], "width"} ],   # staircase from its foot `a` up to its head `b`; the
                                                                 # floor above shows the stairwell at the same place
    "floor_id": "ground", "floor_name": "",    # since 0.25: id and name of the floor stored at the top level (level 0)
    "floors":   [ {"id", "name", "level": ±n,  # the further floors: level > 0 above, < 0 below the one at the top level
                   "walls", "rooms", "openings", "devices", "groups", "decks", "stairs"} ] }

Several floors (since 0.25, purely additive): walls, rooms, openings, devices, groups, decks and stairs at the top level are
the floor with level 0 – a plan from before 0.25 is exactly that and stays valid unchanged. Every further floor
carries the same lists in `floors`. Size, address and all display settings are shared by all floors. `floors()`
returns all of them in one form, bottom to top – readers never look at the top level and `floors` separately.
**A device stands on exactly one floor:** `_clean` walks the floors (level 0 first, then upwards, then downwards)
and drops every device that an earlier floor already holds – as a position, as a member of a group or docked to an
element. Ids of floors, rooms and groups are unique across all floors.

Units are grid cells (freely chosen, e.g. 1 cell = 0.5 m).
Only walls are drawn (open polylines, bend[i] = control point of a
quadratic curve for edge i). Every area closed all around is a
room; the browser computes the areas. rooms only stores the name and the
Home Base link per room together with an anchor point inside the area.

rooms.json: own room names {"names": {room_id: name}} – they take precedence over the
names from the Home Base or the Magenta app – and {"order": [room_id, …]}, the
order of the room tiles from the time before 0.19.3 (only read any more, see card_order).
"""
from __future__ import annotations

import threading
from typing import Any

from . import jsonfile
from .paths import FLOORPLAN_FILE, ROOMS_FILE, ensure_dirs

_lock = threading.Lock()
MAX_ROOMS = 80
MAX_WALLS = 240
MAX_OPENINGS = 160
MAX_PTS = 64
MAX_DEVICES = 400
MAX_GROUPS = 80
MAX_MEMBERS = 24
MAX_DOCKED = 4              # devices at one window, door or radiator (first one + extra_ids)
MAX_DECKS = 40              # balconies and terraces per floor
DECK_SURFACES = ("tiles", "plain")   # floor of a deck other than planks of wood
MAX_STAIRS = 20             # staircases per floor
STAIR_WIDTH = (0.5, 6.0, 2.0)   # width of a staircase in grid cells: smallest, largest, standard
MAX_FLOORS = 8              # floors of a plan, the one at the top level included
GROUND_ID = "ground"        # id of the floor at the top level if the plan names none
CONTENT_KEYS = ("walls", "rooms", "openings", "devices", "groups", "decks", "stairs")   # what every floor holds of its own
SYMBOL_SIZE = (0.5, 4)      # size of a plan symbol (1 = standard), the same limits as in floorplan.js
SYMBOL_TURN = 45            # a plan symbol is turned in steps of 45 degrees
SYMBOL_STRETCH = (0.25, 8)  # width of a plan symbol against its height (1 = as drawn), the same limits as in floorplan.js


def _read(path, default):
    """Object from floorplan.json or rooms.json; `default` if the file is missing or unreadable."""
    return jsonfile.read(path, default)


def _write(path, data):
    """Atomar schreiben."""
    ensure_dirs()
    jsonfile.write(path, data)


def _num(v, lo, hi, default):
    """Number from any input, rounded to two places and limited to lo…hi; `default` if it is not a
    number."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, round(f, 2)))


def _pt(v, width, height):
    """Point [x, y] inside the plan (rounded to three places), or None. Accepts {x, y} too."""
    if isinstance(v, dict):
        v = [v.get("x"), v.get("y")]
    if not isinstance(v, (list, tuple)) or len(v) < 2:
        return None
    try:
        x, y = float(v[0]), float(v[1])
    except (TypeError, ValueError):
        return None
    return [max(0.0, min(width, round(x, 3))), max(0.0, min(height, round(y, 3)))]


def _wall(r: dict, width, height):
    """Wall as an open polyline (bend[i] = control point of a quadratic curve for edge i)."""
    pts = [p for p in (_pt(q, width, height) for q in (r.get("pts") or [])[:MAX_PTS]) if p]
    if len(pts) < 2:
        return None
    raw = r.get("bend") if isinstance(r.get("bend"), list) else []
    bend = [(_pt(raw[i], width, height) if i < len(raw) and raw[i] else None) for i in range(len(pts) - 1)]
    return {"pts": pts, "bend": bend}


def _docked(opening: dict) -> list[str]:
    """All devices docked to an opening: the first one (`device_id`) and the further ones (`extra_ids`)."""
    return ([opening["device_id"]] if opening.get("device_id") else []) + list(opening.get("extra_ids") or [])


def _set_docked(opening: dict, ids: list[str]) -> None:
    """Write the docked devices of an opening: `device_id` is the first, `extra_ids` only exists with further ones."""
    opening["device_id"] = ids[0] if ids else None
    opening.pop("extra_ids", None)
    if len(ids) > 1:
        opening["extra_ids"] = ids[1:]


def _room(r: dict, xy, width, height):
    """Check a room entry: id, name, link to the room of the hub and anchor point inside the area."""
    return {"id": str(r.get("id") or "")[:40] or "r", "name": str(r.get("name") or "Raum")[:40],
            "room_id": (str(r["room_id"])[:80] if r.get("room_id") else None),
            "x": _num(xy[0], 0, width, 0), "y": _num(xy[1], 0, height, 0)}


def _deck(r: dict, width, height):
    """Balcony or terrace: a closed outline of at least three corners, stored like a wall whose last point repeats
    the first."""
    shape = _wall(r, width, height)
    if not shape:
        return None
    pts, bend = shape["pts"], shape["bend"]
    if pts[0] != pts[-1]:
        if len(pts) >= MAX_PTS:
            pts, bend = pts[:MAX_PTS - 1], bend[:MAX_PTS - 2]
        pts, bend = pts + [list(pts[0])], bend + [None]
    return {"pts": pts, "bend": bend} if len(pts) >= 4 else None


def _name(value: Any) -> str:
    """Short name or id from any input: single spaces, at most 40 characters."""
    return " ".join(str(value or "").split())[:40]


def _unique(wanted: str, seen: set[str]) -> str:
    """`wanted`, or – if another floor already uses this id – the id with a counter appended. Remembers the result."""
    out, n = wanted, 1
    while out in seen:
        n += 1
        out = f"{wanted[:36]}_{n}"
    seen.add(out)
    return out


def _content(raw: dict[str, Any], width, height, taken: set[str], room_ids: set[str],
             group_ids: set[str]) -> dict[str, Any]:
    """Check and limit what one floor holds: walls, rooms, openings, device positions, groups, decks, stairs. A device is
    in at most one group; group members have no position of their own and dock nowhere. `taken` = devices that
    stand on another floor already – they are dropped here, and the devices of this floor are added to it;
    `room_ids`/`group_ids` = ids used on other floors."""
    walls, rooms, openings, decks, stairs, docked = [], [], [], [], [], set()
    for w in list(raw.get("walls") or []):
        if isinstance(w, dict) and len(walls) < MAX_WALLS:
            sh = _wall(w, width, height)
            if sh:
                walls.append({"id": str(w.get("id") or "")[:40] or f"w{len(walls)}", **sh})
    for r in (raw.get("rooms") or [])[:MAX_ROOMS]:
        if not isinstance(r, dict):
            continue
        if r.get("x") is not None and r.get("y") is not None:
            rooms.append(_room(r, (r.get("x"), r.get("y")), width, height))
    for i, r in enumerate(rooms):
        if r["id"] == "r":
            r["id"] = f"r{i}"
        r["id"] = _unique(r["id"], room_ids)
    for o in (raw.get("openings") or [])[:MAX_OPENINGS]:
        if not isinstance(o, dict):
            continue
        a, b = _pt(o.get("a"), width, height), _pt(o.get("b"), width, height)
        if not a or not b or ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) < 0.04:
            continue
        entry = {"id": str(o.get("id") or "")[:40] or f"o{len(openings)}",
                 "type": o.get("type") if o.get("type") in ("door", "window", "passage", "radiator") else "window",
                 "a": a, "b": b, "side": -1 if o.get("side") == -1 else 1, "device_id": None}
        # docked devices: the first one and the further ones – a device hangs on one element only
        ids = [o.get("device_id")] + (o["extra_ids"] if isinstance(o.get("extra_ids"), list) else [])
        for did in ids:
            did = str(did)[:160] if did else ""
            if did and did not in docked and did not in taken and len(_docked(entry)) < MAX_DOCKED:
                docked.add(did)
                _set_docked(entry, _docked(entry) + [did])
        openings.append(entry)
    devices = {}
    for did, pos in list((raw.get("devices") or {}).items())[:MAX_DEVICES]:
        if isinstance(pos, dict) and str(did)[:160] not in taken:
            entry = {"x": _num(pos.get("x"), 0, width, 0), "y": _num(pos.get("y"), 0, height, 0)}
            if pos.get("t") is not None:
                entry["t"] = _num(pos.get("t"), 0, 1, 0.5)
            if pos.get("s") in (1, -1):                     # side at the docked element (inside/outside)
                entry["s"] = pos["s"]
            if pos.get("furniture") and isinstance(pos["furniture"], str):
                # plan symbol (key of floorplan_symbols.js): only a symbol has a size and a turn of its own, the
                # standard values are not stored
                entry["furniture"] = str(pos["furniture"])[:30]
                size = _num(pos.get("size"), SYMBOL_SIZE[0], SYMBOL_SIZE[1], 1)
                if size != 1:
                    entry["size"] = size
                rot = int(_num(pos.get("rot"), 0, 359, 0)) // SYMBOL_TURN * SYMBOL_TURN
                if rot:
                    entry["rot"] = rot
                stretch = _num(pos.get("stretch"), SYMBOL_STRETCH[0], SYMBOL_STRETCH[1], 1)   # free scaling (0.28)
                if stretch != 1:
                    entry["stretch"] = stretch
            devices[str(did)[:160]] = entry
    groups, grouped = [], set()
    for g in (raw.get("groups") or [])[:MAX_GROUPS]:
        if not isinstance(g, dict):
            continue
        members = []
        for did in g.get("members") or []:
            did = str(did)[:160]
            if did and did not in grouped and did not in taken and len(members) < MAX_MEMBERS:
                grouped.add(did)
                members.append(did)
        if members:
            entry = {"id": _unique(str(g.get("id") or "")[:40] or f"g{len(groups)}", group_ids),
                     "name": _name(g.get("name")),
                     "x": _num(g.get("x"), 0, width, 0), "y": _num(g.get("y"), 0, height, 0), "members": members}
            if g.get("icon"):
                entry["icon"] = str(g["icon"])[:30]
            groups.append(entry)
    for did in grouped:                                     # group members have no position of their own
        devices.pop(did, None)
    for o in openings:                                      # … and dock nowhere
        _set_docked(o, [did for did in _docked(o) if did not in grouped])
    for d in list(raw.get("decks") or [])[:MAX_DECKS]:
        if isinstance(d, dict):
            sh = _deck(d, width, height)
            if sh:
                entry = {"id": str(d.get("id") or "")[:40] or f"d{len(decks)}", **sh}
                if d.get("surface") in DECK_SURFACES:       # planks of wood are the standard and are not stored
                    entry["surface"] = d["surface"]
                decks.append(entry)
    for t in list(raw.get("stairs") or [])[:MAX_STAIRS]:
        if not isinstance(t, dict):
            continue
        a, b = _pt(t.get("a"), width, height), _pt(t.get("b"), width, height)
        if a and b and ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) >= 0.25:
            stairs.append({"id": str(t.get("id") or "")[:40] or f"s{len(stairs)}", "a": a, "b": b,
                           "width": _num(t.get("width"), STAIR_WIDTH[0], STAIR_WIDTH[1], STAIR_WIDTH[2])})
    taken.update(devices)
    taken.update(grouped)
    for o in openings:
        taken.update(_docked(o))
    return {"walls": walls, "rooms": rooms, "openings": openings, "devices": devices, "groups": groups, "decks": decks,
            "stairs": stairs}


def _level(raw: dict) -> int:
    """Level of a further floor as stored: a whole number, 0 if it is missing or unreadable."""
    try:
        return int(raw.get("level"))
    except (TypeError, ValueError):
        return 0


def _clean(plan: dict[str, Any]) -> dict[str, Any]:
    """Check and limit the whole plan: the floor at the top level, the further floors, display. The further floors
    get levels without gaps (1, 2, … above, −1, −2, … below; one without a valid level goes on top). A device
    stands on one floor only – the floor at the top level wins, then the floors upwards, then downwards."""
    width = _num(plan.get("width"), 6, 80, 24)
    height = _num(plan.get("height"), 4, 60, 14)
    taken, room_ids, group_ids, floor_ids = set(), set(), set(), set()
    ground_id = _unique(_name(plan.get("floor_id")) or GROUND_ID, floor_ids)
    ground = _content(plan, width, height, taken, room_ids, group_ids)
    stored = plan.get("floors") if isinstance(plan.get("floors"), list) else []
    extra = [f for f in stored if isinstance(f, dict)][:MAX_FLOORS - 1]
    above = sorted((f for f in extra if _level(f) > 0), key=_level) + [f for f in extra if _level(f) == 0]
    below = sorted((f for f in extra if _level(f) < 0), key=_level, reverse=True)
    further = []
    for step, raws in ((1, above), (-1, below)):
        for n, raw in enumerate(raws, 1):
            further.append({"id": _unique(_name(raw.get("id")) or f"floor{len(further) + 1}", floor_ids),
                            "name": _name(raw.get("name")), "level": step * n,
                            **_content(raw, width, height, taken, room_ids, group_ids)})
    further.sort(key=lambda f: f["level"])
    address = " ".join(str(plan.get("address") or "").split())[:120]
    return {"width": width, "height": height, "address": address, "heatmap": bool(plan.get("heatmap")),
            "show_names": bool(plan.get("show_names", True)), "weather": bool(plan.get("weather", True)),
            "warnings": bool(plan.get("warnings", True)),
            "shadow": bool(plan.get("shadow", True)), "north": int(_num(plan.get("north"), 0, 359, 0)),
            "fx": bool(plan.get("fx", True)), "fx_level": "subtle" if plan.get("fx_level") == "subtle" else "normal",
            "walls": ground["walls"], "rooms": ground["rooms"],
            "openings": ground["openings"], "devices": ground["devices"], "groups": ground["groups"],
            "home_name": _name(plan.get("home_name")),
            "decks": ground["decks"], "stairs": ground["stairs"], "floor_id": ground_id, "floor_name": _name(plan.get("floor_name")),
            "floors": further}


def floors(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """All floors of a plan in one form, bottom to top: {"id", "name", "level", walls, rooms, openings, devices,
    groups, decks}. The floor at the top level shares its lists with the plan – a change to a room found here is a
    change to the plan."""
    ground = {"id": plan.get("floor_id") or GROUND_ID, "name": plan.get("floor_name") or "", "level": 0}
    for key in CONTENT_KEYS:
        if not isinstance(plan.get(key), dict if key == "devices" else list):
            plan[key] = {} if key == "devices" else []
        ground[key] = plan[key]
    extra = [f for f in plan.get("floors") or [] if isinstance(f, dict)]
    return sorted([ground] + extra, key=lambda f: f.get("level") or 0)


def rooms(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """The rooms of all floors."""
    return [r for f in floors(plan) for r in f.get("rooms") or [] if isinstance(r, dict)]


def groups(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """The device groups of all floors."""
    return [g for f in floors(plan) for g in f.get("groups") or [] if isinstance(g, dict)]


def clean(plan: Any) -> dict[str, Any]:
    """Check and limit a plan from a request without saving it."""
    return _clean(plan if isinstance(plan, dict) else {})


def get() -> dict[str, Any]:
    """Stored floor plan, checked – without a file an empty plan in the default size."""
    with _lock:
        raw = _read(FLOORPLAN_FILE, {})
    return _clean(raw if isinstance(raw, dict) else {})


def save(plan: dict[str, Any]) -> dict[str, Any]:
    """Check the floor plan and save it atomically. Returns the stored plan."""
    clean = _clean(plan if isinstance(plan, dict) else {})
    with _lock:
        _write(FLOORPLAN_FILE, clean)
    return clean


def room_names() -> dict[str, str]:
    """Eigene Raumnamen {Raum-ID: Name}."""
    with _lock:
        names = _read(ROOMS_FILE, {}).get("names")
    return names if isinstance(names, dict) else {}


MAX_ORDER = 200


def card_order() -> list[str]:
    """Order of the room tiles from rooms.json. Up to 0.19.2 the user dragged them into place by the handle; since
    0.19.3 "Anordnen" arranges the tiles (settings.json `layout.home`). The old order is only read any more – as
    the default as long as there is no own arrangement. It is no longer written."""
    with _lock:
        raw = _read(ROOMS_FILE, {}).get("order")
    out, seen = [], set()
    for rid in raw if isinstance(raw, list) else []:
        key = str(rid)[:80]
        if key and key not in seen:
            seen.add(key)
            out.append(key)
        if len(out) >= MAX_ORDER:
            break
    return out


def set_room_name(room_id: str, name: str | None) -> dict[str, str]:
    """Set the own name of a room (empty or None deletes it). Returns all own names."""
    with _lock:
        data = _read(ROOMS_FILE, {"names": {}})
        names = data.setdefault("names", {})
        if not isinstance(names, dict):
            names = data["names"] = {}
        if name and name.strip():
            names[room_id] = name.strip()[:40]
        else:
            names.pop(room_id, None)
        _write(ROOMS_FILE, data)
        return names
