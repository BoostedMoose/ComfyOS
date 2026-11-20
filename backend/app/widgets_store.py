"""Widget view of the home page (data/widgets.json, since 0.24): pages with freely placed widgets.

    { "version": 1,
      "pages": [ { "id": "p1a2b3c4d5", "name": "Zuhause", "icon": "home",
                   "cols": 12, "cols_narrow": 4,           # grid columns on a wide screen / on the phone
                   "row": "normal",                         # height of a grid row: flat | normal | square
                   "gap": 12,                               # distance between widgets in pixels
                   "compact": true,                         # close gaps upwards after moving
                   "backdrop": "plain",                     # plain | dots | glow (display only)
                   "widgets": [ { "id": "w1a2b3c4d5", "type": "clock",
                                  "title": "",              # own title ("" = the name of the widget type)
                                  "head": true,             # show the title line
                                  "style": "card",          # card | glass | accent | bare
                                  "tint": "",               # colour accent: "" or one of TINTS
                                  "scale": 100,             # size of the content in per cent (60–180)
                                  "align": "start",         # content at the top, in the middle or at the bottom
                                  "wide": {"x": 0, "y": 0, "w": 4, "h": 3},      # place in the wide grid (cells)
                                  "narrow": {"x": 0, "y": 0, "w": 4, "h": 3},    # place on the phone, null = automatic
                                  "opts": { … } } ] } ] }  # settings of the widget type (simple values only)

The store knows the widget types by name (TYPES) – the catalogue with titles, sizes and settings lives in the
interface (static/js/widgets.js; tests/core/test_widgets.py compares both lists). It only checks and limits: ids by a
fixed pattern, numbers in their range, texts cut, unknown types and keys dropped. What a widget shows is decided in the
browser from the devices' capabilities – nothing here names a plugin.

Nothing is converted: a file with another `version` counts as outdated, the interface starts with an empty view and
says so (`outdated`); saving replaces it.
"""
from __future__ import annotations

import re
import secrets
import threading
from typing import Any

from . import jsonfile
from .paths import DATA_DIR, ensure_dirs

WIDGETS_FILE = DATA_DIR / "widgets.json"
VERSION = 1
MAX_PAGES = 12
MAX_WIDGETS = 60                 # per page
MAX_OPTS = 24                    # settings per widget
MAX_TEXT = 400                   # longest text of a setting (the note widget)
MAX_LIST = 60                    # entries of a list setting (chosen devices, rooms, scenes)
MAX_ROWS = 400                   # the grid ends here – nothing is placed further down
COLS_WIDE = (4, 24)              # smallest and largest number of columns on a wide screen
COLS_NARROW = (2, 6)             # … and on the phone
ROWS = ("flat", "normal", "square")
BACKDROPS = ("plain", "dots", "glow")
STYLES = ("card", "glass", "accent", "bare")
ALIGNS = ("start", "center", "end")
TINTS = ("accent", "ok", "warn", "bad", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8")
ICONS = ("home", "grid", "light", "flame", "bolt", "media", "map", "star", "sun", "shield", "layers", "palette",
         "calendar", "gauge", "sparkle", "presence")
TYPES = frozenset({
    # overview
    "floorplan", "room", "status", "openings", "people", "events", "connections", "clock",
    # data
    "weather", "value", "power", "heating",
    # actions
    "presence", "lights", "house_scene", "scenes", "device", "flow", "media",
    # decoration
    "note", "heading",
})

_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_OPT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_lock = threading.Lock()


def new_id(prefix: str) -> str:
    """Fresh id for a page ("p") or a widget ("w")."""
    return prefix + secrets.token_hex(5)


def _int(value: Any, lo: int, hi: int, default: int) -> int:
    """Whole number in the range lo…hi; `default` for everything that is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) > 1e9:
        return default
    return max(lo, min(hi, int(round(value))))


def _text(value: Any, limit: int) -> str:
    """Text without control characters, cut to `limit`; "" for everything that is not a text."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch == "\n" or ch >= " ")[:limit].strip()


def _place(raw: Any, cols: int) -> dict[str, int] | None:
    """Place of a widget in a grid with `cols` columns: {x, y, w, h} in cells, inside the grid. None = not given."""
    if not isinstance(raw, dict):
        return None
    w = _int(raw.get("w"), 1, cols, min(cols, 3))
    return {"x": _int(raw.get("x"), 0, cols - w, 0), "y": _int(raw.get("y"), 0, MAX_ROWS, 0),
            "w": w, "h": _int(raw.get("h"), 1, 24, 2)}


def _opts(raw: Any) -> dict[str, Any]:
    """Settings of a widget: keys by a fixed pattern; values are a text, a number, yes/no or a list of short texts."""
    out: dict[str, Any] = {}
    for key, value in (raw.items() if isinstance(raw, dict) else ()):
        if len(out) >= MAX_OPTS:
            break
        if not isinstance(key, str) or not _OPT_KEY.match(key):
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            if value == value and abs(value) < 1e9:
                out[key] = round(float(value), 2) if isinstance(value, float) else value
        elif isinstance(value, str):
            out[key] = _text(value, MAX_TEXT)
        elif isinstance(value, list):
            out[key] = list(dict.fromkeys(_text(x, 160) for x in value if isinstance(x, str) and x.strip()))[:MAX_LIST]
    return out


def clean_widget(raw: Any, cols: int, cols_narrow: int, seen: set[str]) -> dict[str, Any] | None:
    """Check one widget. None if it has no known type. `seen` holds the ids already used on the page – a missing,
    invalid or repeated id is replaced by a fresh one."""
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str) or raw["type"] not in TYPES:
        return None
    wid = raw.get("id") if isinstance(raw.get("id"), str) and _ID.match(raw["id"]) and raw["id"] not in seen else new_id("w")
    seen.add(wid)
    return {"id": wid, "type": raw["type"], "title": _text(raw.get("title"), 60).replace("\n", " "),
            "head": raw.get("head") is not False,
            "style": raw.get("style") if raw.get("style") in STYLES else "card",
            "tint": raw.get("tint") if raw.get("tint") in TINTS else "",
            "scale": _int(raw.get("scale"), 60, 180, 100),
            "align": raw.get("align") if raw.get("align") in ALIGNS else "start",
            "wide": _place(raw.get("wide"), cols) or {"x": 0, "y": MAX_ROWS, "w": min(cols, 3), "h": 2},
            "narrow": _place(raw.get("narrow"), cols_narrow),
            "opts": _opts(raw.get("opts"))}


def clean_page(raw: Any, seen: set[str]) -> dict[str, Any] | None:
    """Check one page with its widgets. None if it is not an object."""
    if not isinstance(raw, dict):
        return None
    pid = raw.get("id") if isinstance(raw.get("id"), str) and _ID.match(raw["id"]) and raw["id"] not in seen else new_id("p")
    seen.add(pid)
    cols = _int(raw.get("cols"), COLS_WIDE[0], COLS_WIDE[1], 12)
    cols_narrow = _int(raw.get("cols_narrow"), COLS_NARROW[0], COLS_NARROW[1], 4)
    widgets, ids = [], set()
    for item in (raw.get("widgets") if isinstance(raw.get("widgets"), list) else [])[:MAX_WIDGETS * 2]:
        widget = clean_widget(item, cols, cols_narrow, ids)
        if widget is not None and len(widgets) < MAX_WIDGETS:
            widgets.append(widget)
    return {"id": pid, "name": _text(raw.get("name"), 30).replace("\n", " ") or "Seite",
            "icon": raw.get("icon") if raw.get("icon") in ICONS else "grid",
            "cols": cols, "cols_narrow": cols_narrow,
            "row": raw.get("row") if raw.get("row") in ROWS else "normal",
            "gap": _int(raw.get("gap"), 0, 32, 12),
            "compact": raw.get("compact") is not False,
            "backdrop": raw.get("backdrop") if raw.get("backdrop") in BACKDROPS else "plain",
            "widgets": widgets}


def clean(raw: Any) -> dict[str, Any]:
    """Check and limit the whole document: at most MAX_PAGES pages, each with at most MAX_WIDGETS widgets."""
    pages, seen = [], set()
    for item in (raw.get("pages") if isinstance(raw, dict) and isinstance(raw.get("pages"), list) else [])[:MAX_PAGES]:
        page = clean_page(item, seen)
        if page is not None:
            pages.append(page)
    return {"version": VERSION, "pages": pages}


def outdated() -> bool:
    """Is there a file in a format this version does not read (another `version`)? It is neither converted nor run."""
    data = jsonfile.read(WIDGETS_FILE, {})
    return bool(data) and data.get("version") != VERSION


def _load() -> dict[str, Any]:
    """Content of widgets.json, checked. A missing, unreadable or outdated file gives an empty view."""
    data = jsonfile.read(WIDGETS_FILE, {})
    if data.get("version") != VERSION:
        return {"version": VERSION, "pages": []}
    return clean(data)


def _save(data: dict[str, Any]) -> None:
    """Write widgets.json atomically."""
    ensure_dirs()
    jsonfile.write(WIDGETS_FILE, data)


def get() -> dict[str, Any]:
    """All pages of the widget view."""
    with _lock:
        return _load()


def save(raw: Any) -> dict[str, Any]:
    """Check the document and save it (replaces everything). Returns what was stored."""
    data = clean(raw)
    with _lock:
        _save(data)
    return data
