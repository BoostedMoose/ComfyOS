"""Light scenes of ComfyOS (data/scenes.json) – a palette instead of fixed lamp values, so that a scene fits every room.

    { "version": 2, "seeded": true,
      "scenes": [ { "id", "name", "palette": [ {"c": "#rrggbb" | "ct": <mirek>, "b": 1–100} ],
                    "dynamic": bool, "speed": <s>,
                    "rooms": [<room id>] (assignment; empty = offered in no room),
                    "variants": { <room id>: { "lamps": { <device id>: {on, brightness, mirek | xy | gradient_xy | color} },
                                               "updated": <unix time> } },
                    optional "source": "hue", "image_rid": <public_image of the bridge> } ],
      "active": { "room:<room id>" | "group:<group id>":
                  { "scene", "since", "devices": [<device id>], "snapshot": {<device id>: <command>},
                    "excluded": [<device id>], "offset", "step_at", "brightness" } } }

snapshot = state before the first scene (ending the scene restores it). excluded = lamps adjusted
individually that a dynamic scene no longer touches.

Room variant = stored lamp state of a scene for a room ("Für diesen Raum speichern"). Lamps
without an entry get the palette distribution when applied, as without a variant.

First assignment: a freshly set up installation knows no rooms yet when the templates are created
(state `version: 1`). As soon as the rooms are known (core.seed_scene_rooms when the home page loads),
scenes without a room get all rooms entered once – otherwise the templates would be offered in no room. After
that `version: 2` is stored, and an empty room list means "in no room".
"""
from __future__ import annotations

import math
import re
import secrets
import threading
import time
from typing import Any

from . import jsonfile
from .light_model import HEX_RE
from .paths import DATA_DIR, ensure_dirs

SCENES_FILE = DATA_DIR / "scenes.json"
VERSION = 2
MAX_SCENES = 60
MAX_ROOMS = 50
MAX_COLORS = 9
MAX_DEVICES = 100
SPEEDS = (8, 20, 60)
KEEP_ON_UPDATE = ("image_rid", "source", "variants")
_ID_RE = re.compile(r"^[a-z0-9_-]{2,24}$")
_KEY_RE = re.compile(r"^(room|group):.{1,120}$")
_RID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_lock = threading.Lock()


def _c(c: str, b: int) -> dict[str, Any]:
    """Paletteneintrag: Farbe (#rrggbb) mit Helligkeit in Prozent."""
    return {"c": c, "b": b}


def _w(ct: int, b: int) -> dict[str, Any]:
    """Palette entry: white tone (mirek) with brightness in per cent."""
    return {"ct": ct, "b": b}


PRESETS = [
    ("hell", "Hell", [_w(233, 100)], False),
    ("lesen", "Lesen", [_w(290, 100)], False),
    ("fokus", "Konzentrieren", [_w(215, 100)], False),
    ("entspannen", "Entspannen", [_w(447, 56)], False),
    ("gedimmt", "Gedimmt", [_w(400, 30)], False),
    ("nacht", "Nachtlicht", [_c("#ff7a1a", 3)], False),
    ("abendrot", "Abendrot", [_c("#ff5e3a", 75), _c("#ff9a3c", 70), _c("#ffc46b", 65), _c("#c2335a", 60)], False),
    ("polarlicht", "Polarlicht", [_c("#1de9b6", 60), _c("#00b0ff", 55), _c("#7c4dff", 55), _c("#64ffda", 60)], True),
    ("ozean", "Ozean", [_c("#0077b6", 60), _c("#00b4d8", 60), _c("#90e0ef", 55), _c("#023e8a", 50)], False),
    ("beere", "Beere", [_c("#e20074", 60), _c("#ff6fb3", 60), _c("#7a1fa2", 55)], False),
    ("kerze", "Kerzenschein", [_c("#ff8a2a", 35), _c("#ffb347", 30), _c("#ff6a00", 25)], True),
]


def _presets() -> list[dict[str, Any]]:
    """The scene templates shipped with ComfyOS, not assigned to any room yet."""
    return [{"id": i, "name": n, "palette": p, "dynamic": d, "speed": 60 if d else 20, "rooms": []}
            for i, n, p, d in PRESETS]


def clean_palette(pal: Any) -> list[dict[str, Any]]:
    """Check a palette: at most MAX_COLORS entries, each a colour or a white tone (153–500 mirek) with brightness
    1–100."""
    out = []
    for e in pal if isinstance(pal, list) else []:
        if not isinstance(e, dict):
            continue
        try:
            b = int(max(1, min(100, round(float(e.get("b", 100))))))
        except (TypeError, ValueError):
            b = 100
        if isinstance(e.get("c"), str) and HEX_RE.match(e["c"]):
            out.append({"c": e["c"].lower(), "b": b})
        elif isinstance(e.get("ct"), (int, float)) and not isinstance(e.get("ct"), bool):
            out.append({"ct": int(max(153, min(500, round(e["ct"])))), "b": b})
        if len(out) >= MAX_COLORS:
            break
    return out


def _finite(v: Any) -> bool:
    """Is the value a finite number (no yes/no, no NaN)?"""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def clean_lamp_state(st: Any) -> dict[str, Any] | None:
    """Lamp state of a room variant: on/off, brightness, exactly one light colour. Effects are not part of it."""
    if not isinstance(st, dict):
        return None
    if not st.get("on"):
        return {"on": False}
    out: dict[str, Any] = {"on": True}
    if _finite(st.get("brightness")):
        out["brightness"] = int(max(1, min(100, round(st["brightness"]))))
    if _finite(st.get("mirek")):
        out["mirek"] = int(max(153, min(500, round(st["mirek"]))))
    elif _finite(st.get("kelvin")):
        out["mirek"] = int(max(153, min(500, round(1_000_000 / max(1000.0, min(10000.0, float(st["kelvin"])))))))
    elif isinstance(st.get("xy"), list) and len(st["xy"]) == 2 and all(_finite(c) for c in st["xy"]):
        out["xy"] = [round(max(0.0, min(1.0, float(c))), 4) for c in st["xy"]]
    elif isinstance(st.get("gradient_xy"), list):
        pts = [[round(max(0.0, min(1.0, float(c))), 4) for c in p] for p in st["gradient_xy"][:10]
               if isinstance(p, list) and len(p) == 2 and all(_finite(c) for c in p)]
        if len(pts) >= 2:
            out["gradient_xy"] = pts
    elif isinstance(st.get("color"), str) and HEX_RE.match(st["color"]):
        out["color"] = st["color"].lower()
    return out


def clean_variants(v: Any) -> dict[str, Any]:
    """Check room variants: per room the stored lamp states, limited to MAX_ROOMS rooms and MAX_DEVICES
    lamps."""
    out: dict[str, Any] = {}
    for rid, var in (v.items() if isinstance(v, dict) else []):
        if not (isinstance(rid, str) and rid and isinstance(var, dict) and isinstance(var.get("lamps"), dict)):
            continue
        lamps = {}
        for did, st in list(var["lamps"].items())[:MAX_DEVICES]:
            c = clean_lamp_state(st)
            if isinstance(did, str) and did and c is not None:
                lamps[did[:160]] = c
        if lamps:
            out[rid[:120]] = {"lamps": lamps, "updated": float(var["updated"]) if _finite(var.get("updated")) else 0.0}
        if len(out) >= MAX_ROOMS:
            break
    return out


def clean_scene(data: Any, sid: str | None = None) -> dict[str, Any]:
    """Check and limit a scene. Raises ValueError with a text for the user if name or colours are missing. `sid`
    keeps an existing id, otherwise a new one is created."""
    if not isinstance(data, dict):
        raise ValueError("Ungültige Szene.")
    name = " ".join(str(data.get("name") or "").split())[:40]
    if not name:
        raise ValueError("Bitte einen Namen für die Szene eingeben.")
    pal = clean_palette(data.get("palette"))
    if not pal:
        raise ValueError("Eine Szene braucht mindestens eine Farbe.")
    speed = data.get("speed", 20)
    speed = min(SPEEDS, key=lambda s: abs(s - float(speed))) if isinstance(speed, (int, float)) else 20
    rooms = [str(r)[:120] for r in (data.get("rooms") or []) if isinstance(r, str) and r][:MAX_ROOMS] \
        if isinstance(data.get("rooms"), list) else []
    sc: dict[str, Any] = {"id": sid or secrets.token_hex(5), "name": name, "palette": pal,
                          "dynamic": bool(data.get("dynamic")), "speed": speed, "rooms": list(dict.fromkeys(rooms)),
                          "variants": clean_variants(data.get("variants"))}
    rid = data.get("image_rid")
    if isinstance(rid, str) and _RID_RE.match(rid):
        sc["image_rid"] = rid
    if data.get("source") == "hue":     # origin: taken over from the bridge, otherwise created in ComfyOS
        sc["source"] = "hue"
    return sc


def _clean_active(act: Any, ids: set[str]) -> dict[str, Any]:
    """Check active scenes: only valid targets (`room:…`/`group:…`) with a scene that still exists."""
    out = {}
    for k, v in (act.items() if isinstance(act, dict) else []):
        if not (isinstance(k, str) and _KEY_RE.match(k) and isinstance(v, dict) and v.get("scene") in ids):
            continue
        snap = v.get("snapshot") if isinstance(v.get("snapshot"), dict) else {}
        out[k] = {"scene": v["scene"], "since": float(v.get("since") or 0),
                  "devices": [str(x)[:160] for x in (v.get("devices") or [])][:MAX_DEVICES],
                  "snapshot": {str(d)[:160]: s for d, s in list(snap.items())[:MAX_DEVICES] if isinstance(s, dict)},
                  "excluded": [str(x)[:160] for x in (v.get("excluded") or [])][:MAX_DEVICES],
                  "offset": int(v.get("offset") or 0), "step_at": float(v.get("step_at") or 0),
                  "brightness": v.get("brightness") if isinstance(v.get("brightness"), (int, float)) else None}
    return out


def _load() -> dict[str, Any]:
    """Content of scenes.json, checked. On the first load the templates are added; the stored version is
    kept (it controls the first assignment, see the head of the file)."""
    data = jsonfile.read(SCENES_FILE, {})
    scenes = []
    for s in (data.get("scenes") if isinstance(data.get("scenes"), list) else [])[:MAX_SCENES]:
        try:
            sid = s.get("id") if isinstance(s, dict) and _ID_RE.match(str(s.get("id") or "")) else None
            scenes.append(clean_scene(s, sid))
        except ValueError:
            continue   # an invalid scene drops out
    if not data.get("seeded"):
        have = {s["id"] for s in scenes}
        scenes = [p for p in _presets() if p["id"] not in have] + scenes
    ids = {s["id"] for s in scenes}
    ver = data.get("version")
    ver = ver if isinstance(ver, int) and not isinstance(ver, bool) else 1
    return {"version": max(1, min(ver, VERSION)), "seeded": True, "scenes": scenes,
            "active": _clean_active(data.get("active"), ids)}


def _save(data: dict[str, Any]) -> None:
    """Atomar nach scenes.json schreiben."""
    ensure_dirs()
    jsonfile.write(SCENES_FILE, data)


def get() -> dict[str, Any]:
    """The whole store: scenes and what is active where (with snapshots)."""
    with _lock:
        return _load()


def scene(sid: str) -> dict[str, Any] | None:
    """A scene by its id, or None."""
    return next((s for s in get()["scenes"] if s["id"] == sid), None)


def public() -> dict[str, Any]:
    """For the interface: scenes + what is active where (without snapshots)."""
    data = get()
    return {"scenes": data["scenes"],
            "active": {k: {"scene": v["scene"], "since": v["since"]} for k, v in data["active"].items()}}


def seed_rooms(room_ids: list[str]) -> bool:
    """First assignment: scenes without a room get all known rooms once. Runs as soon as rooms are known;
    without rooms the state stays and is caught up the next time. Idempotent.
    Scenes taken over from the Hue Bridge are left out – they are always assigned by hand."""
    ids = list(dict.fromkeys(str(r)[:120] for r in room_ids if isinstance(r, str) and r))[:MAX_ROOMS]
    if not ids:
        return False
    with _lock:
        cur = _load()
        if cur["version"] >= VERSION:
            return False
        for s in cur["scenes"]:
            if not s["rooms"] and s.get("source") != "hue":
                s["rooms"] = list(ids)
        cur["version"] = VERSION
        _save(cur)
        return True


def set_variant(sid: str, room_id: str, lamps: dict[str, Any]) -> dict[str, Any]:
    """Save a room variant (current state of the lamps in the room). The room is assigned along the way."""
    var = clean_variants({room_id: {"lamps": lamps, "updated": time.time()}})
    if not var:
        raise ValueError("Keine Lampen zum Speichern.")
    with _lock:
        cur = _load()
        sc = next((s for s in cur["scenes"] if s["id"] == sid), None)
        if sc is None:
            raise KeyError(sid)
        if room_id not in sc["variants"] and len(sc["variants"]) >= MAX_ROOMS:
            raise ValueError(f"Höchstens {MAX_ROOMS} Raumvarianten je Szene.")
        sc["variants"].update(var)
        if room_id not in sc["rooms"] and len(sc["rooms"]) < MAX_ROOMS:
            sc["rooms"].append(room_id)
        _save(cur)
        return sc


def clear_variant(sid: str, room_id: str) -> bool:
    """Delete the room variant of a scene. True = it existed. Raises KeyError if the scene does not exist."""
    with _lock:
        cur = _load()
        sc = next((s for s in cur["scenes"] if s["id"] == sid), None)
        if sc is None:
            raise KeyError(sid)
        if sc["variants"].pop(room_id, None) is None:
            return False
        _save(cur)
        return True


def active_map() -> dict[str, str]:
    """Target -> active scene id (for flow triggers/conditions)."""
    return {k: v["scene"] for k, v in get()["active"].items()}


def create(data: Any) -> dict[str, Any]:
    """Create a new scene (checked; at most MAX_SCENES)."""
    with _lock:
        cur = _load()
        if len(cur["scenes"]) >= MAX_SCENES:
            raise ValueError(f"Höchstens {MAX_SCENES} Szenen.")
        sc = clean_scene(data)
        cur["scenes"].append(sc)
        _save(cur)
        return sc


def update(sid: str, data: Any) -> dict[str, Any]:
    """Edit a scene. Origin, image and room variants do not come from the editor and are kept. Raises
    KeyError if it does not exist."""
    with _lock:
        cur = _load()
        i = next((i for i, s in enumerate(cur["scenes"]) if s["id"] == sid), None)
        if i is None:
            raise KeyError(sid)
        if isinstance(data, dict):   # origin and image do not come from the editor – keep them when editing
            data = {**{k: cur["scenes"][i][k] for k in KEEP_ON_UPDATE if k in cur["scenes"][i]}, **data}
        cur["scenes"][i] = clean_scene(data, sid)
        _save(cur)
        return cur["scenes"][i]


def delete(sid: str) -> bool:
    """Delete a scene and end it everywhere it is active. True = it existed."""
    with _lock:
        cur = _load()
        n = len(cur["scenes"])
        cur["scenes"] = [s for s in cur["scenes"] if s["id"] != sid]
        if len(cur["scenes"]) == n:
            return False
        cur["active"] = {k: v for k, v in cur["active"].items() if v["scene"] != sid}
        _save(cur)
        return True


def activate(key: str, sid: str, devices: list[str], snapshot: dict[str, dict], brightness: float | None = None) -> None:
    """Remember a scene as active for a target. A target that is already active keeps its original snapshot."""
    if not _KEY_RE.match(key):
        raise ValueError("Ungültiges Ziel.")
    now = time.time()
    with _lock:
        cur = _load()
        old = cur["active"].get(key)
        snap = dict(old["snapshot"]) if old else {}
        for did, st in snapshot.items():
            snap.setdefault(did, st)
        cur["active"][key] = {"scene": sid, "since": now, "devices": devices[:MAX_DEVICES], "snapshot": snap,
                              "excluded": [], "offset": 0, "step_at": now, "brightness": brightness}
        _save(cur)


def deactivate(key: str) -> dict[str, Any] | None:
    """Remove the active scene of a target. Returns the entry (with the snapshot for restoring) or None."""
    with _lock:
        cur = _load()
        entry = cur["active"].pop(key, None)
        if entry is not None:
            _save(cur)
        return entry


def clear(keys: list[str] | None) -> list[str]:
    """Forget active scenes without restoring (e.g. light in the room off). None = all."""
    with _lock:
        cur = _load()
        gone = [k for k in cur["active"] if keys is None or k in keys]
        for k in gone:
            cur["active"].pop(k)
        if gone:
            _save(cur)
        return gone


def exclude(device_id: str) -> None:
    """Take an individually adjusted lamp out of dynamic scenes (it stays the way the user wants it)."""
    with _lock:
        cur = _load()
        hit = False
        for v in cur["active"].values():
            if device_id in v["devices"] and device_id not in v["excluded"]:
                v["excluded"].append(device_id)
                hit = True
        if hit:
            _save(cur)


def step(key: str, offset: int, at: float) -> None:
    """For a dynamic scene, remember how far the colours have been turned and when last."""
    with _lock:
        cur = _load()
        if key in cur["active"]:
            cur["active"][key]["offset"], cur["active"][key]["step_at"] = offset, at
            _save(cur)
