"""ComfyOS – widget view of the home page (widgets_store.py, api/widgets.py, entitlements.check_widgets, since 0.24).

    1  store: checked and limited (ids, ranges, texts, lists, unknown types and keys), cleaning twice gives the same
    2  routes: read, save, read again; invalid bodies 4xx; nothing else of the data folder is touched
    3  outdated format: not converted, shown as outdated, replaced on saving
    4  Basic (enforced): the view and one page are free; a second page and Pro settings 402; what exists stays
       editable and deletable; a refused change saves nothing; Pro everything; shadow mode only remembers
    5  one catalogue: the type names, styles, colour accents and icons of the interface are those of the store; every
       Pro setting of the server is marked `pro` in the interface with the same feature
    6  backup and demo: widgets.json is a backup file; in the demo it is written to data/demo/, the real file stays

    python tests/core/test_widgets.py [path-to-comfyos]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("widgets")

from app import auth, backup_store, demo, entitlements as ents, jsonfile, main, widgets_store as ws  # noqa: E402
from app.paths import DATA_DIR, ensure_dirs  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ensure_dirs()
JS = ROOT / "backend" / "app" / "static" / "js"


def widget(kind="clock", **extra):
    """A widget as the interface sends it."""
    return {"id": extra.pop("id", ""), "type": kind, "wide": {"x": 0, "y": 0, "w": 3, "h": 2}, "opts": extra.pop("opts", {}), **extra}


def page(name="Zuhause", widgets=(), **extra):
    """A page with the given widgets."""
    return {"name": name, "widgets": list(widgets), **extra}


# 1) Store: checked and limited
doc = ws.clean({"pages": [page(widgets=[
    widget("clock", id="w-ok_1", title="  Uhr\nzeit " + "x" * 90, style="glass", tint="s3", scale=999, align="center",
           wide={"x": 50, "y": -3, "w": 99, "h": 0}, narrow={"x": 1, "y": 2, "w": 9, "h": 3},
           opts={"look": "analog", "seconds": True, "n": 3, "f": 2.456, "list": ["a", "a", 5, " ", "b" * 500], "Bad Key": 1,
                 "nested": {"x": 1}, "text": "a\x00b\nc" + "z" * 900}),
    widget("gibt-es-nicht"), widget(["clock"]), widget({"a": 1}), "kein objekt", widget("note", id="w-ok_1"),
    widget("value", id="../../etc/passwd")],
    cols=99, cols_narrow=0, row="riesig", gap=-5, backdrop="<script>", icon="nope", compact=0, id="p 1")]})
p = doc["pages"][0]
assert doc["version"] == ws.VERSION and re.match(r"^p[0-9a-f]{10}$", p["id"]), p["id"]
assert (p["cols"], p["cols_narrow"], p["row"], p["gap"], p["backdrop"], p["icon"], p["compact"]) == (24, 2, "normal", 0, "plain", "grid", True), p
assert [w["type"] for w in p["widgets"]] == ["clock", "note", "value"], "unknown types and non-objects drop out"
first, second, third = p["widgets"]
assert first["id"] == "w-ok_1" and second["id"] != "w-ok_1" and re.match(r"^w[0-9a-f]{10}$", second["id"]), "a repeated id is replaced"
assert re.match(r"^w[0-9a-f]{10}$", third["id"]), "an id outside the pattern is replaced"
assert first["title"].startswith("Uhr zeit xxx") and 40 < len(first["title"]) <= 60 and "\n" not in first["title"]
assert (first["style"], first["tint"], first["scale"], first["align"], first["head"]) == ("glass", "s3", 180, "center", True)
assert first["wide"] == {"x": 0, "y": 0, "w": 24, "h": 1} and first["narrow"] == {"x": 0, "y": 2, "w": 2, "h": 3}, (first["wide"], first["narrow"])
assert second["narrow"] is None, "no phone place given: the interface derives it"
opts = first["opts"]
assert set(opts) == {"look", "seconds", "n", "f", "list", "text"} and opts["f"] == 2.46 and opts["list"] == ["a", "b" * 160]
assert opts["text"].startswith("ab\nc") and len(opts["text"]) == ws.MAX_TEXT and "\x00" not in opts["text"]
assert ws.clean(doc) == doc and ws.clean(json.loads(json.dumps(doc))) == doc, "cleaning twice changes nothing"
many = ws.clean({"pages": [page(widgets=[widget("clock") for _ in range(200)])] * 40})
assert len(many["pages"]) == ws.MAX_PAGES and all(len(x["widgets"]) == ws.MAX_WIDGETS for x in many["pages"])
assert len({x["id"] for x in many["pages"]}) == ws.MAX_PAGES, "every page gets an id of its own"
big = ws.clean({"pages": [page(widgets=[widget("note", opts={f"k{i}": "v" for i in range(100)})])]})
assert len(big["pages"][0]["widgets"][0]["opts"]) == ws.MAX_OPTS
for junk in (None, [], "text", 5, {"pages": "x"}, {"pages": [None, 3, []]}):
    assert ws.clean(junk) == {"version": ws.VERSION, "pages": []}, junk
print("1 store: ids by pattern, numbers in range, texts and lists cut, unknown types and keys dropped; cleaning is stable")

ents.ENFORCE = False
with TestClient(main.app) as client:
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def code(response, want):
        """Status as expected; returns the JSON."""
        assert response.status_code == want, (response.status_code, response.text[:300])
        return response.json()

    def pro_required(response, feature):
        """402 with the expected feature."""
        detail = code(response, 402)["detail"]
        assert detail["code"] == "pro_required" and detail["feature"] == feature, detail

    # 2) Routes
    empty = code(client.get("/api/widgets"), 200)
    assert empty["pages"] == [] and empty["outdated"] is False and empty["limits"] == {"pages": ws.MAX_PAGES, "widgets": ws.MAX_WIDGETS}
    before = {f.name for f in DATA_DIR.iterdir()}
    saved = code(client.put("/api/widgets", json={"pages": [page(widgets=[widget("clock"), widget("weather", opts={"details": True})])]}), 200)
    assert [w["type"] for w in saved["pages"][0]["widgets"]] == ["clock", "weather"]
    assert code(client.get("/api/widgets"), 200)["pages"] == saved["pages"], "reading gives what was saved"
    created = {f.name for f in DATA_DIR.iterdir()} - before
    assert "widgets.json" in created and created <= {"widgets.json", "devices.json", "power.json", "egress.json"}, created
    again = code(client.put("/api/widgets", json={"pages": saved["pages"]}), 200)
    assert again["pages"] == saved["pages"], "saving what was read changes nothing (ids stay)"
    for body in ({}, {"pages": "x"}, {"pages": [1, 2]}, {"pages": [page()] * (ws.MAX_PAGES + 1)}, [1], "text"):
        assert client.put("/api/widgets", json=body).status_code == 422, body
    assert code(client.get("/api/widgets"), 200)["pages"] == saved["pages"], "a refused body saved nothing"
    print("2 routes: save and read back, ids stable, invalid bodies 422, only widgets.json is written")

    # 3) Outdated format: neither converted nor shown – and replaced by the next save
    jsonfile.write(ws.WIDGETS_FILE, {"version": 0, "boards": [{"tiles": ["alt"]}]})
    old_bytes = ws.WIDGETS_FILE.read_bytes()
    old = code(client.get("/api/widgets"), 200)
    assert old["pages"] == [] and old["outdated"] is True and ws.WIDGETS_FILE.read_bytes() == old_bytes, "reading must not rewrite the old file"
    fresh = code(client.put("/api/widgets", json={"pages": [page()]}), 200)
    assert fresh["outdated"] is False and len(fresh["pages"]) == 1 and json.loads(ws.WIDGETS_FILE.read_text(encoding="utf-8"))["version"] == ws.VERSION
    print("3 outdated format: marked, not converted, replaced on saving")

    # 4) Basic and Pro
    ents.ENFORCE = True
    ents.use_plan("basic")
    one = [page(widgets=[widget("clock"), widget("weather"), widget("power"), widget("floorplan")])]
    base = code(client.put("/api/widgets", json={"pages": one}), 200)["pages"]
    pro_required(client.put("/api/widgets", json={"pages": base + [page("Oben")]}), "advanced_dashboard")
    for kind, option, feature in (("weather", "forecast", "floorplan_weather"), ("power", "chart", "energy_analytics"),
                                  ("floorplan", "heat", "floorplan_heatmap"), ("floorplan", "fx", "floorplan_weather")):
        assert ents.WIDGET_FEATURES[(kind, option)] == feature
        pro_required(client.put("/api/widgets", json={"pages": [page(widgets=base[0]["widgets"] + [widget(kind, opts={option: True})])]}), feature)
        code(client.put("/api/widgets", json={"pages": [page(widgets=base[0]["widgets"] + [widget(kind, opts={option: False})])]}), 200)
    code(client.put("/api/widgets", json={"pages": base}), 200)
    stored = ws.WIDGETS_FILE.read_bytes()
    pro_required(client.put("/api/widgets", json={"pages": base + [page("Oben")]}), "advanced_dashboard")
    assert ws.WIDGETS_FILE.read_bytes() == stored, "a refused change was saved anyway"
    # what exists from a Pro period stays: moving, changing and deleting works – more of it does not
    kept = ws.save({"pages": [page("Unten", widgets=[widget("weather", opts={"forecast": True}), widget("power", opts={"chart": True})]),
                              page("Oben", widgets=[widget("floorplan", opts={"heat": True, "fx": True})]), page("Garten")]})["pages"]
    moved = json.loads(json.dumps(kept))
    moved[0]["widgets"][0]["wide"]["x"] = 5
    moved[0]["name"] = "Erdgeschoss"
    code(client.put("/api/widgets", json={"pages": moved}), 200)
    pro_required(client.put("/api/widgets", json={"pages": moved + [page("Keller")]}), "advanced_dashboard")
    more = json.loads(json.dumps(moved))
    more[2]["widgets"].append(widget("weather", opts={"forecast": True}))
    pro_required(client.put("/api/widgets", json={"pages": more}), "floorplan_weather")
    fewer = code(client.put("/api/widgets", json={"pages": moved[:2]}), 200)["pages"]
    assert len(fewer) == 2 and fewer[0]["widgets"][0]["opts"]["forecast"] is True, "existing Pro content stays as it is"
    assert ents.widget_usage({"pages": fewer}) == {"floorplan_weather": 2, "energy_analytics": 1, "floorplan_heatmap": 1}
    ents.use_plan("pro")
    code(client.put("/api/widgets", json={"pages": more + [page("Keller")]}), 200)
    ents.ENFORCE = False
    ents.use_plan("basic")
    ents.shadow_hits.clear()
    code(client.put("/api/widgets", json={"pages": more + [page("Keller"), page("Dach", widgets=[widget("power", opts={"chart": True})])]}), 200)
    assert {"advanced_dashboard", "energy_analytics"} <= set(ents.shadow_hits), ents.shadow_hits
    assert ents.FEATURES["advanced_dashboard"][2] == "available" and "advanced_dashboard" in ents.PLANS["pro"]["features"]
    print("4 Basic: one page free, further pages and Pro settings 402, existing content stays editable; Pro free; shadow mode remembers")

# 5) One catalogue for store and interface
types_js = (JS / "widget_types.js").read_text(encoding="utf-8")
board_js = (JS / "widgets.js").read_text(encoding="utf-8")
registered = re.findall(r"W\.register\('([a-z_]+)'", types_js)
assert sorted(registered) == sorted(ws.TYPES) and len(registered) == len(set(registered)), \
    f"widget types differ – store only: {sorted(ws.TYPES - set(registered))}, interface only: {sorted(set(registered) - ws.TYPES)}"


def js_list(name: str, pairs: bool = False) -> list[str]:
    """Values of a list constant of widgets.js: the strings between its brackets (`pairs`: the first entry of each
    [value, label] pair)."""
    body = re.search(rf"const {name} = \[(.*?)\]\n", board_js, flags=re.S).group(1)
    return re.findall(r"\['([a-z0-9_-]*)', '", body) if pairs else re.findall(r"'([a-z0-9_-]*)'", body)


assert js_list("STYLES", pairs=True) == list(ws.STYLES), js_list("STYLES", pairs=True)
assert [t for t in js_list("TINTS") if t] == list(ws.TINTS), js_list("TINTS")
assert js_list("ICONS") == list(ws.ICONS), js_list("ICONS")
for (kind, option), feature in ents.WIDGET_FEATURES.items():
    block = types_js.split(f"W.register('{kind}'", 1)[1].split("W.register(", 1)[0]
    assert re.search(rf"key: '{option}'[^\n]*pro: '{feature}'", block), f"{kind}.{option} is not marked pro: '{feature}' in the interface"
marked = re.findall(r"key: '([a-z_]+)'[^\n]*pro: '([a-z_]+)'", types_js)
assert len(marked) == len(ents.WIDGET_FEATURES), f"a Pro setting of the interface is unknown to the server: {marked}"
index = (ROOT / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")
order = re.findall(r'<script src="/static/js/([\w.]+)\?', index)
assert order.index("media.js") < order.index("widgets.js") < order.index("widget_types.js") < order.index("overview.js")
print(f"5 one catalogue: {len(registered)} widget types, styles, colour accents and icons as in the store; {len(marked)} Pro settings known to the server")

# 6) Backup and demo
assert "widgets.json" in backup_store._DATA_FILES
real = ws.WIDGETS_FILE.read_bytes()
mark = demo.enter()
try:
    assert ws.get()["pages"] == [], "the demo starts without the user's widget view"
    ws.save({"pages": [page("Demo")]})
    assert ws.get()["pages"][0]["name"] == "Demo" and (demo.DEMO_DIR / "widgets.json").is_file()
finally:
    demo.leave(mark)
assert ws.WIDGETS_FILE.read_bytes() == real and demo.allowed("PUT", "/api/widgets") and demo.allowed("GET", "/api/widgets")
demo.reset()
mark = demo.enter()
try:
    seeded = ws.get()["pages"]
finally:
    demo.leave(mark)
assert len(seeded) == 1 and {"clock", "floorplan", "room", "media"} <= {w["type"] for w in seeded[0]["widgets"]}
taken = [w["wide"] for w in seeded[0]["widgets"]]
overlap = [(a, b) for i, a in enumerate(taken) for b in taken[i + 1:]
           if a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]]
assert not overlap, f"widgets of the sample page overlap: {overlap[:2]}"
assert ws.WIDGETS_FILE.read_bytes() == real, "setting up the demo touched the real widget view"
print("6 backup file; the demo writes its own widgets.json, the sample page is seeded without overlaps, the real file stays")

print("OK test_widgets")
