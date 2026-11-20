"""ComfyOS – data files: every store reads its file and writes it back unchanged byte for byte.

The sample files in tests/data/stores/ are the state ComfyOS writes today (generated from mock data).
As long as this test is green, no file format has changed – neither fields nor notation. That protects
existing installations: there are no conversion routines, so a format must never change by accident.

    1  load every sample file and write it back: the same bytes
    2  loading changes nothing: loading twice gives the same, the file stays as it is
    3  damaged file (empty, cut off, not JSON, wrong type): no crash, state as without a file
    4  an unreadable file is kept as a copy before it is overwritten
    5  floor plan with several floors (since 0.25): a plan from before stays one floor, a device stands on one floor
       only, ids and levels are unambiguous, decks are closed, stairs have a foot, a head and a limited width

    python tests/core/test_stores.py [path-to-comfyos]
    python tests/core/test_stores.py [path-to-comfyos] --write   # regenerate the sample files – only after a
                                                                 # deliberate format change (then name it in Roadmap.md)
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("stores")

from app import (device_store as ds, effects_store as es, floorplan_store as fps, flows_store as fs,  # noqa: E402
                 home_store as hs, power_store as ps, routines_store as rs, scenes_store as ss, settings_store as st,
                 widgets_store as wst)
from app.paths import FLOORPLAN_FILE, HOME_FILE, ROOMS_FILE, ROUTINES_FILE, ensure_dirs  # noqa: E402

FIX = ROOT / "tests" / "data" / "stores"
WRITE = "--write" in sys.argv
T0 = 1_800_000_000.0
ensure_dirs()
st.APP_DIR.mkdir(parents=True, exist_ok=True)


def forget() -> None:
    """Discard states in memory – the test swaps files under the stores."""
    ps.forget()
    ds._cache = None


def power_load():
    """Read the power consumption fresh from the file (otherwise the store keeps its state in memory)."""
    ps.forget()
    return json.loads(json.dumps(ps._load()))


def power_save():
    """Load the power consumption and write it back."""
    ps.forget()
    ps._save(ps._load(), T0)


# file -> (path, load, load-and-write-back)
STORES = {
    "devices.json": (ds.DEVICES_FILE, ds._load, lambda: ds._save(ds._load())),
    "effects.json": (es.EFFECTS_FILE, es._load, lambda: es._save(es._load())),
    "home.json": (HOME_FILE, hs._load, lambda: hs._save(hs._load())),
    "floorplan.json": (FLOORPLAN_FILE, fps.get, lambda: fps.save(fps.get())),
    "rooms.json": (ROOMS_FILE, lambda: {"names": fps.room_names(), "order": fps.card_order()},
                   lambda: fps.set_room_name("wohnen", fps.room_names().get("wohnen"))),
    "flows.json": (fs.FLOWS_FILE, fs._load, lambda: fs._save(fs._load())),
    "power.json": (ps.POWER_FILE, power_load, power_save),
    "routines.json": (ROUTINES_FILE, rs._load, lambda: rs._save(rs._load())),
    "scenes.json": (ss.SCENES_FILE, ss._load, lambda: ss._save(ss._load())),
    "settings.json": (st.SETTINGS_FILE, st.load_settings, lambda: st.save_settings(st.load_settings())),
    "secrets.json": (st.SECRETS_FILE, st._file_load, lambda: st._file_save(st._file_load())),
    "widgets.json": (wst.WIDGETS_FILE, wst._load, lambda: wst._save(wst._load())),
}


def scenario() -> None:
    """Fill all stores through their public functions with mock data – with a fixed clock and fixed ids,
    so that the same files are created again."""
    import secrets
    import time
    import uuid

    counter = iter(range(1, 10_000))
    time.time = lambda: T0
    secrets.token_hex = lambda n=5: f"{next(counter):0{2 * n}x}"
    secrets.token_urlsafe = lambda n=24: "beispiel-token-" + f"{next(counter):020d}"
    uuid.uuid4 = lambda: uuid.UUID(int=next(counter))

    ds.lock("dect:plug:1", "user")
    ds.lock("huebridge:lamp-2", "flow:f0000000001")
    ds.set_name("dect:plug:1", "Kaffeemaschine")
    ds.set_hidden("mqtt:alter-sensor", "Alter Sensor", True)
    ds.set_room("huebridge:lamp-1", "wohnen")
    ds.set_adopted("huebridge:lamp-1", True, "plan:r1")

    es.set_room("wohnen", {"effect": "candle", "target": "room", "color_source": "custom", "color": "#FFAA33", "speed": 40})
    es.set_room("bad", {"effect": "fire", "target": ["huebridge:lamp-2"], "color_source": "own"})

    hs.set_mapping("routine-winter", "routine-urlaub")
    hs.set_state("away")

    fps.save({"width": 20, "height": 12, "address": "Musterstraße 1, 10115 Berlin", "heatmap": True, "north": 90,
              "home_name": "Wohnung", "fx_level": "subtle",
              "walls": [{"id": "w1", "pts": [[1, 1], [19, 1], [19, 11], [1, 11], [1, 1]]},
                        {"id": "w2", "pts": [[10, 1], [10, 11]], "bend": [[11, 6]]}],
              "rooms": [{"id": "r1", "name": "Küche", "room_id": "kueche", "x": 5, "y": 6},
                        {"id": "r2", "name": "Wohnzimmer", "room_id": "wohnen", "x": 15, "y": 6}],
              "openings": [{"id": "o1", "type": "window", "a": [3, 1], "b": [5, 1], "side": -1, "device_id": "dect:contact:1",
                            "extra_ids": ["dect:cover:1"]},
                           {"id": "o2", "type": "radiator", "a": [12, 11], "b": [14, 11]}],
              "devices": {"dect:plug:1": {"x": 4, "y": 4}, "huebridge:lamp-1": {"x": 15, "y": 3, "furniture": "sofa", "size": 1.5, "rot": 90}},
              "groups": [{"id": "g1", "name": "Leseecke", "x": 16, "y": 8, "members": ["huebridge:lamp-2", "huebridge:lamp-3"]}],
              # since 0.25: a balcony and a second floor with a roof terrace
              "decks": [{"id": "d1", "pts": [[19, 3], [20, 3], [20, 9], [19, 9]]}],
              "stairs": [{"id": "s1", "a": [8, 9], "b": [8, 5], "width": 1.5}],
              "floor_id": "eg", "floor_name": "",
              "floors": [{"id": "og", "name": "Oben", "level": 1,
                          "walls": [{"id": "w3", "pts": [[1, 1], [10, 1], [10, 11], [1, 11], [1, 1]]}],
                          "rooms": [{"id": "r3", "name": "Schlafzimmer", "room_id": "schlafen", "x": 5, "y": 6}],
                          "openings": [{"id": "o3", "type": "window", "a": [3, 1], "b": [5, 1], "device_id": "dect:contact:2"}],
                          "devices": {"dect:contact:2": {"x": 4, "y": 1.5}, "huebridge:lamp-4": {"x": 6, "y": 7}},
                          "groups": [],
                          "stairs": [{"id": "s2", "a": [8, 5], "b": [8, 9]}],   # above the stairs of the floor below
                          "decks": [{"id": "d2", "pts": [[10, 1], [19, 1], [19, 11], [10, 11], [10, 1]], "bend": [None, [20, 6], None, None],
                                     "surface": "tiles"}]}]})
    fps.set_room_name("wohnen", "Wohnzimmer")
    fps.set_room_name("kueche", "Küche")
    # order from the time before 0.19.3: ComfyOS no longer writes it but keeps reading it
    fps._write(ROOMS_FILE, {**fps._read(ROOMS_FILE, {}), "order": ["wohnen", "kueche"]})

    flow = fs.create("Flurlicht bei Bewegung",
                     [{"id": "a", "type": "device_signal", "x": 40, "y": 60, "config": {"target": {"devices": ["dect:motion:1"]}}},
                      {"id": "b", "type": "set_device", "x": 320, "y": 60, "config": {"target": {"rooms": ["flur"]}, "on": True}},
                      {"id": "w", "type": "webhook", "x": 40, "y": 220, "config": {}}],
                     [{"id": "e1", "from": "a", "fromPort": "out", "to": "b", "toPort": "in"}], enabled=True,
                     description="Schaltet das Licht im Flur, sobald sich etwas bewegt.")
    fs.create("Leer", [], [], enabled=False)
    fs.save_runtime({"Urlaub": True, "Zähler": 3, "Notiz": "Müll rausbringen"},
                    {flow["id"] + "/a": {"on": True}, "_flow:" + flow["id"]: {"last": T0, "error": None}})

    ps.observe([{"id": "dect:plug:1", "energy_kwh": 12.3456, "online": True},
                {"id": "huebridge:lamp-2", "power_w": 8.5, "online": True}], now=T0)
    ps.observe([{"id": "dect:plug:1", "energy_kwh": 12.4, "online": True},
                {"id": "huebridge:lamp-2", "power_w": 8.5, "online": True}], now=T0 + 120)
    ps.flush()

    routine = rs.create("Winter")
    rs.update_schedule(routine["key"], [{"room": "wohnen", "day": 0, "points": [{"time": "06:30", "temp": 21},
                                                                              {"time": "22:00", "temp": 17.5}]},
                                        {"room": "bad", "day": 5, "points": [{"time": "07:00", "temp": 23}]}])
    rs.set_active(routine["key"])
    rs.create("Urlaub", copy_from=routine["key"])

    scene = ss.create({"name": "Abendrot", "palette": [{"c": "#ff7a3c", "b": 80}, {"ct": 370, "b": 40}],
                       "rooms": ["wohnen"], "dynamic": True, "speed": 20})
    ss.set_variant(scene["id"], "wohnen", {"huebridge:lamp-1": {"on": True, "brightness": 60, "mirek": 350}})
    ss.activate("room:wohnen", scene["id"], ["huebridge:lamp-1"], {"huebridge:lamp-1": {"on": False}}, 70)

    st.save_settings({"https": False, "log_sample_seconds": 300, "log_retention_months": 13,
                      "allowed_hosts": ["comfyos.example.com"], "accent_color": "#3366cc",
                      "plugins": {"disabled": ["network"], "trusted": [],
                                  "settings": {"mqtt": {"host": "192.168.1.50", "port": 1883, "tls": False}}},
                      "layout": {"system": {"2": [["updateCard", "backupCard"], ["securityCard"]]},
                                 "home": {"1": [["rc-wohnen", "rc-kueche"]]}}})
    st.set_secret("plugin:mqtt:password", "nur-ein-" + "beispiel")

    # widget view of the home page (since 0.24): two pages, own phone places, settings of every value type
    wst.save({"pages": [
        {"id": "p000000001", "name": "Zuhause", "icon": "home", "cols": 12, "cols_narrow": 4, "row": "normal", "gap": 12,
         "compact": True, "backdrop": "glow", "widgets": [
             {"id": "w000000001", "type": "clock", "head": False, "align": "center", "wide": {"x": 0, "y": 0, "w": 3, "h": 2},
              "narrow": {"x": 0, "y": 0, "w": 4, "h": 2}, "opts": {"look": "both", "seconds": True}},
             {"id": "w000000002", "type": "room", "title": "Wohnen", "style": "glass", "tint": "s3", "scale": 120,
              "wide": {"x": 3, "y": 0, "w": 4, "h": 3}, "opts": {"room": "wohnen", "devices": True}},
             {"id": "w000000003", "type": "heating", "wide": {"x": 7, "y": 0, "w": 5, "h": 3},
              "opts": {"rooms": ["wohnen", "kueche"], "routine": False}},
             {"id": "w000000004", "type": "value", "wide": {"x": 0, "y": 2, "w": 3, "h": 2},
              "opts": {"device": "dect:plug:1", "prop": "power_w", "look": "gauge", "max": "2000"}}]},
        {"id": "p000000002", "name": "Garten", "icon": "sun", "cols": 8, "cols_narrow": 2, "row": "square", "gap": 0,
         "compact": False, "backdrop": "dots", "widgets": [
             {"id": "w000000005", "type": "note", "style": "bare", "wide": {"x": 1, "y": 4, "w": 6, "h": 2},
              "opts": {"text": "Gießen: Dienstag und Freitag\nSchlauch im Schuppen"}}]}]})


if WRITE:
    scenario()
    FIX.mkdir(parents=True, exist_ok=True)
    for name, (path, _load, _again) in STORES.items():
        assert path.is_file(), f"{name} was not created in the scenario"
        shutil.copyfile(path, FIX / name)
    print(f"{len(STORES)} sample files written to {FIX.relative_to(ROOT)}")
    sys.exit(0)

missing = sorted(set(STORES) - {f.name for f in FIX.glob("*.json")})
assert not missing, f"sample files are missing: {missing} (create them with --write)"

REPORT = "--report" in sys.argv      # run through all sections and only list the findings (for looking them up)
problems: list[str] = []
found: list[str] = []


def settle() -> None:
    """Findings of a section: abort – or with --report list them and continue."""
    if not problems:
        return
    if not REPORT:
        raise AssertionError("\n  " + "\n  ".join(problems))
    print("   BEFUNDE:\n     " + "\n     ".join(problems))
    found.extend(problems)
    problems.clear()


# 1) Load and write back: the same bytes
for name, (path, _load, again) in STORES.items():
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIX / name, path)
    forget()
    again()
    if path.read_bytes() != (FIX / name).read_bytes():
        problems.append(f"{name}: no longer the same bytes after loading and writing")
    if list(path.parent.glob(path.stem + "*.tmp")):
        problems.append(f"{name}: the temporary file is left behind")
settle()
print(f"1 {len(STORES)} data files: loading and writing back gives the same bytes")

# 2) Loading changes nothing
for name, (path, load, _again) in STORES.items():
    shutil.copyfile(FIX / name, path)
    forget()
    first = json.loads(json.dumps(load()))
    forget()
    if json.loads(json.dumps(load())) != first:
        problems.append(f"{name}: loading twice does not give the same")
    if path.read_bytes() != (FIX / name).read_bytes():
        problems.append(f"{name}: loading changed the file")
settle()
print("2 loading has no side effect: the same result, file unchanged")

# 3) Damaged file: no crash, state as without a file
BROKEN = {"empty": b"", "cut off": b'{"version": 2, "scenes": [{"id": "a', "not JSON": b"\x00\x01 kein json",
          "list instead of object": b"[1, 2, 3]", "null": b"null", "Text": b'"text"'}
for name, (path, load, _again) in STORES.items():
    path.unlink(missing_ok=True)
    forget()
    empty = json.loads(json.dumps(load()))
    for what, raw in BROKEN.items():
        path.write_bytes(raw)
        forget()
        try:
            got = json.loads(json.dumps(load()))
        except Exception as exc:
            problems.append(f"{name} ({what}): crash with {type(exc).__name__}")
            continue
        if got != empty:
            problems.append(f"{name} ({what}): another state than without a file")
    path.unlink(missing_ok=True)
    for stray in path.parent.glob(path.stem + ".defekt-*"):
        stray.unlink()
settle()
print(f"3 {len(BROKEN)} kinds of damaged files per store: no crash, state as without a file")

# 4) An unreadable file is kept as a copy before the store overwrites it
import os  # noqa: E402
import stat  # noqa: E402

from app import events, jsonfile  # noqa: E402


class Clock:
    """Clock for the names of the copies: every query one second further (otherwise fast copies would get the same name)."""

    def __init__(self):
        """Bei null Sekunden beginnen."""
        self.second = 0

    def strftime(self, _format):
        """One second later on every call – that way every kept file gets its own name."""
        self.second += 1
        return f"20270115-1200{self.second:02d}"


jsonfile.time = Clock()
RAW = b'{"abgeschnitten": '
for name in ("devices.json", "settings.json", "secrets.json", "routines.json"):
    path, load, again = STORES[name]
    copies = lambda: sorted(path.parent.glob(f"{path.stem}.defekt-*{path.suffix}"))   # noqa: E731
    path.write_bytes(RAW)
    forget()
    load()
    if len(copies()) != 1 or copies()[0].read_bytes() != RAW:
        problems.append(f"{name}: unreadable file not kept ({[c.name for c in copies()]})")
        continue
    again()                                              # now the store overwrites the file
    forget()
    load()
    load()
    if len(copies()) != 1:
        problems.append(f"{name}: the same version kept several times")
    if path.read_bytes() == RAW:
        problems.append(f"{name}: the store did not write after keeping the copy")
    if os.name != "nt" and name == "secrets.json" and stat.S_IMODE(copies()[0].stat().st_mode) != 0o600:
        problems.append("secrets.json: the copy is readable for others")
    for i in range(jsonfile.MAX_KEPT + 2):               # ever new broken versions: at most MAX_KEPT stay
        path.write_bytes(RAW + b"x" * (i + 1))           # other size = other version (the modification time is too coarse)
        forget()
        load()
    if len(copies()) != jsonfile.MAX_KEPT:
        problems.append(f"{name}: {len(copies())} copies instead of at most {jsonfile.MAX_KEPT}")
    for c in copies():
        c.unlink()
    path.unlink(missing_ok=True)
if not any("war nicht lesbar" in e["message"] and "defekt-" in e["message"] for e in events.recent(100)):
    problems.append("no entry in the event log about the kept file")
(ROOT / "tests").mkdir(exist_ok=True)
empty = tmp / "leer.json"
empty.write_bytes(b"")
if jsonfile.read(empty, {"x": 1}) != {"x": 1} or list(tmp.glob("leer.defekt-*")):
    problems.append("empty file: default expected, no copy")
cache = tmp / "cache.json"
cache.write_bytes(RAW)
if jsonfile.read(cache, {}, keep=False) != {} or list(tmp.glob("cache.defekt-*")):
    problems.append("keep=False: no copy must be created")
settle()
print(f"4 unreadable file: kept as a copy once (at most {jsonfile.MAX_KEPT}), secrets stay private, event in the log")

# 5) Several floors (since 0.25): purely additive, a device stands on exactly one floor
OLD = {"width": 20, "height": 12, "walls": [{"id": "w1", "pts": [[1, 1], [9, 1], [9, 9], [1, 9], [1, 1]]}],
       "rooms": [{"id": "r1", "name": "Küche", "room_id": "kueche", "x": 5, "y": 5}],
       "openings": [{"id": "o1", "type": "window", "a": [3, 1], "b": [5, 1], "device_id": "dev:fenster"}],
       "devices": {"dev:fenster": {"x": 4, "y": 1.5}, "dev:lampe": {"x": 5, "y": 5}},
       "groups": [{"id": "g1", "name": "Ecke", "x": 7, "y": 7, "members": ["dev:a", "dev:b"]}]}
one = fps.clean(OLD)
if one["floors"] or one["decks"] or one["floor_id"] != fps.GROUND_ID or len(fps.floors(one)) != 1:
    problems.append("a plan from before 0.25 is no longer exactly one floor")
if any(one[key] != fps.clean({**OLD, "floors": []})[key] for key in fps.CONTENT_KEYS) or sorted(one["devices"]) != ["dev:fenster", "dev:lampe"]:
    problems.append("a plan from before 0.25 reads differently than before")
several = fps.clean({**OLD, "floors": [
    # the same devices again – as a position, docked and in a group –, the same ids for floor, room and group
    {"id": "ground", "level": 5, "rooms": [{"id": "r1", "name": "Bad", "x": 2, "y": 2}],
     "devices": {"dev:lampe": {"x": 1, "y": 1}, "dev:oben": {"x": 2, "y": 2}},
     "openings": [{"id": "o9", "type": "window", "a": [1, 1], "b": [3, 1], "device_id": "dev:a", "extra_ids": ["dev:neu"]}],
     "groups": [{"id": "g1", "name": "Oben", "x": 3, "y": 3, "members": ["dev:fenster", "dev:c", "dev:d"]}],
     "decks": [{"id": "k1", "pts": [[9, 1], [12, 1], [12, 4]]}, {"id": "k2", "pts": [[1, 1], [2, 2]]}]},
    {"id": "keller", "level": -7, "devices": {"dev:oben": {"x": 3, "y": 3}, "dev:keller": {"x": 4, "y": 4}}},
    {"name": "Dach"}, "kein Stockwerk", None]})
levels = fps.floors(several)
where: dict[str, list[str]] = {}
for floor in levels:
    held = set(floor["devices"]) | {m for g in floor["groups"] for m in g["members"]} | {d for o in floor["openings"] for d in fps._docked(o)}
    for device in held:
        where.setdefault(device, []).append(floor["id"])
twice = {device: at for device, at in where.items() if len(at) > 1}
if twice:
    problems.append(f"devices stand on several floors: {twice}")
if where.get("dev:lampe") != ["ground"] or where.get("dev:fenster") != ["ground"] or where.get("dev:a") != ["ground"]:
    problems.append(f"the floor at the top level must keep its devices: {where}")
if where.get("dev:oben") != [levels[2]["id"]] or where.get("dev:keller") != ["keller"] or where.get("dev:neu") != [levels[2]["id"]]:
    problems.append(f"devices of the further floors are in the wrong place: {where}")
if [f["level"] for f in levels] != [-1, 0, 1, 2] or levels[3]["name"] != "Dach":
    problems.append(f"levels are not without gaps, bottom to top: {[(f['id'], f['level']) for f in levels]}")
for what, ids in (("floors", [f["id"] for f in levels]), ("rooms", [r["id"] for r in fps.rooms(several)]),
                  ("groups", [g["id"] for g in fps.groups(several)])):
    if len(set(ids)) != len(ids):
        problems.append(f"ids of {what} are not unique across the floors: {ids}")
decks = levels[2]["decks"]
if len(decks) != 1 or decks[0]["pts"][0] != decks[0]["pts"][-1] or len(decks[0]["pts"]) != 4 or len(decks[0]["bend"]) != 3:
    problems.append(f"a deck must be a closed outline of at least three corners: {decks}")
steps = fps.clean({"stairs": [{"id": "t1", "a": [2, 2], "b": [2, 6], "width": 99}, {"a": [3, 3], "b": [3, 3.2]}, {"a": [1, 1]}, "x"]})["stairs"]
if steps != [{"id": "t1", "a": [2.0, 2.0], "b": [2.0, 6.0], "width": 6}]:
    problems.append(f"stairs: only a staircase with foot, head and a limited width may stay: {steps}")
floors_ = fps.clean({"decks": [{"pts": [[1, 1], [3, 1], [3, 3]], "surface": s} for s in ("tiles", "plain", "wood", "gold", 7)]})["decks"]
if [d.get("surface") for d in floors_] != ["tiles", "plain", None, None, None]:
    problems.append(f"deck surface: only tiles and plain are stored, wood is the standard: {floors_}")
if fps.clean(several) != several:
    problems.append("cleaning a plan with several floors twice does not give the same")
if len(fps.clean({"floors": [{"level": n + 1} for n in range(30)]})["floors"]) != fps.MAX_FLOORS - 1:
    problems.append("the number of floors is not limited")
settle()
print("5 several floors: an old plan is one floor, a device stands on one floor only, ids and levels unambiguous, decks closed")

if found:
    sys.exit(f"{len(found)} Befunde")
print("ALL DATA FILES OK")
