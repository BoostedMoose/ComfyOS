"""ComfyOS – security: input. What happens with oversized, invalid or malicious input of a signed-in user?

Basic protection:
  5   oversized request, missing length, invalid values
Attacks (C = with a valid session):
  C8   injected content: script text in names, forged log lines
  C10b the tile arrangement only writes checked tile ids
  C10c the widget view only writes known widget types, checked ids and limited texts
  C11  device lock, effects, room variants only for existing devices and rooms

    python tests/security/test_inputs.py [path-to-comfyos]
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.attack import *  # noqa: E402,F401,F403 – app, safeguard and helpers of the security tests

with TestClient(main.app, raise_server_exceptions=False) as client:
    raw = raw_for(client)
    tok, own = login(client)
    settings_store.save_settings(settings_store.load_settings())      # settings.json existiert – C10b vergleicht vorher/nachher

    # ============================================================ Grundschutz (bis 0.19 tests/test_security.py)

    # 5) Input limits: too large, without a length, invalid -> 4xx, never 500 or a stack trace
    r = client.post("/api/flows", content=b"x" * (security.MAX_BODY + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413, r.status_code
    r = client.post("/api/flows", content=iter([b'{"name":', b'"x"}']), headers={"content-type": "application/json"})
    assert r.status_code == 411, r.status_code
    for url, body in (("/api/flows", {"name": ["kein", "text"]}), ("/api/home", {"state": 5}),
                      ("/api/security/hosts", {"hosts": ["a"] * 50})):
        r = client.request("PUT" if "hosts" in url else "POST", url, json=body)
        assert 400 <= r.status_code < 500 and "Traceback" not in r.text, (url, r.status_code, r.text[:200])
    print("5 oversized request 413, chunked without a length 411, invalid input 4xx without a stack trace")

    # C8) Injected content: script text in names stays text; the log stays valid line by line
    auth._fails.clear()
    security._hits.clear()
    f = flows_store.create(XSS + '"><script>alert(1)</script>', [{"id": "b", "type": "play", "x": 0, "y": 0, "config": {"label": XSS}}], [])
    r = raw("GET", "/api/flows", headers=own)
    ct = (r["headers"].get("content-type") or [""])[0]
    check(r["status"] == 200 and ct.startswith("application/json") and json.loads(r["text"]), f"C8 flow list not delivered as JSON: {ct}")
    events.log("system", 'Zeile1\n{"type": "security", "message": "gefälscht"}\r\nZeile3', {"x": "a\nb"})
    logfile = next((Path(os.environ["COMFYOS_HOME"]) / "logs" / "events").glob("*.jsonl"))
    for line in logfile.read_text(encoding="utf-8").splitlines():
        try:
            json.loads(line)
        except ValueError:
            findings.append("C8 Protokollzeile durch eingeschleusten Zeilenumbruch zerbrochen")
            break
    check(not any(e.get("type") == "security" and e.get("message") == "gefälscht" for e in events.recent(50)), "C8 a forged log entry was injected")
    js = "".join(p.read_text(encoding="utf-8") for p in (APP_SRC / "static" / "js").glob("*.js"))
    check(not re.search(r"\.innerHTML\s*=\s*[a-zA-Z_.]*\.(message|detail|name|label)\s*(;|$)", js, re.M), "C8 foreign text is set as HTML without HO.esc")
    check("document.write(" not in js and "eval(" not in js and "new Function(" not in js and "setTimeout('" not in js and 'setTimeout("' not in js,
          "C8 dangerous JavaScript calls in the frontend")
    print("C8 script text in names stays data (JSON, nosniff); the log cannot be forged; no eval/document.write in the frontend")

    # C10b) The tile arrangement under System (0.19.2) writes to settings.json: only checked tile ids, nothing else
    cfg_before = {k: v for k, v in settings_store.load_settings().items() if k != "layout"}
    for cols in ([[".." + "/" * 3 + "etc/passwd"], []], [[XSS], [chr(0)]], [["x" * 5000], []], [["a"] * 300, []],
                 [["homeCard"], ["homeCard"]], [[{"https": True}], []], [["accent_color"], ["plugins"]]):
        r = raw("PUT", "/api/settings/layout", headers={**own, "content-type": "application/json"},
                body=json.dumps({"page": "system", "cols": 2, "columns": cols}).encode())
        check(r["status"] in (200, 400, 422), f"C10b Anordnung {str(cols)[:30]!r} -> {r['status']}")
        cfg = settings_store.load_settings()
        check({k: v for k, v in cfg.items() if k != "layout"} == cfg_before, "C10b the arrangement changes other settings")
        ids = [x for col in cfg.get("layout", {}).get("system", {}).get("2", []) for x in col]
        check(all(re.match(r"^[A-Za-z0-9_:.-]{1,64}$", x) for x in ids) and len(ids) == len(set(ids)) <= settings_store.LAYOUT_MAX_TILES,
              f"C10b arrangement saved unchecked: {ids[:3]}")
    r = raw("PUT", "/api/settings/layout", headers={**own, "content-type": "application/json"},
            body=json.dumps({"page": "../auth", "cols": 2, "columns": [[], []]}).encode())
    check(r["status"] == 422 and set(settings_store.load_settings().get("layout", {})) <= set(settings_store.LAYOUT_PAGES), f"C10b a foreign page was accepted ({r['status']})")
    print("C10b tile arrangement: only checked tile ids for known pages, the other settings stay untouched")

    # C10c) The widget view (0.24) writes to widgets.json: only known widget types, ids by a fixed pattern, limited
    #       texts – whatever is sent, nothing else is written, nothing comes back unchecked, never a 500
    from app import widgets_store as wgs
    hdr_json = {**own, "content-type": "application/json"}
    data_before = {x.name for x in wgs.WIDGETS_FILE.parent.iterdir()} if wgs.WIDGETS_FILE.parent.is_dir() else set()
    cfg_before = dict(settings_store.load_settings())
    evil_id = ".." + "/" * 3 + "auth"
    for pages in ([{"id": evil_id, "name": XSS, "icon": evil_id, "widgets": [{"id": evil_id, "type": "clock", "title": XSS, "style": XSS,
                                                                            "opts": {XSS: 1, "text": XSS, "list": [XSS] * 500, "__proto__": {"x": 1}}}]}],
                  [{"widgets": [{"type": evil_id}, {"type": "../clock"}, {"type": ["clock"]}, None, 7]}],
                  [{"widgets": [{"type": "note", "opts": {"text": "x" * 3000}, "wide": {"x": 1e308, "y": -1e308, "w": "breit", "h": None}}] * 80}],
                  [{"cols": 1e9, "gap": float(10 ** 6), "row": {"a": 1}, "widgets": "viele"}], [[]], [{"widgets": [{"type": "clock", "scale": [1]}]}]):
        r = raw("PUT", "/api/widgets", headers=hdr_json, body=json.dumps({"pages": pages}).encode())
        check(r["status"] in (200, 422), f"C10c Widget-Ansicht {str(pages)[:40]!r} -> {r['status']}")
        stored = wgs.get()
        for pg in stored["pages"]:
            check(re.match(r"^[A-Za-z0-9_-]{1,24}$", pg["id"]) and pg["icon"] in wgs.ICONS and len(pg["name"]) <= 30 and len(pg["widgets"]) <= wgs.MAX_WIDGETS,
                  f"C10c page saved unchecked: {str(pg)[:60]}")
            for wd in pg["widgets"]:
                check(wd["type"] in wgs.TYPES and re.match(r"^[A-Za-z0-9_-]{1,24}$", wd["id"]) and wd["style"] in wgs.STYLES and len(wd["title"]) <= 60
                      and all(re.match(r"^[a-z][a-z0-9_]{0,31}$", k) for k in wd["opts"]) and len(json.dumps(wd["opts"])) < 40_000,
                      f"C10c widget saved unchecked: {str(wd)[:80]}")
    check(dict(settings_store.load_settings()) == cfg_before, "C10c the widget view changes the settings")
    data_after = {x.name for x in wgs.WIDGETS_FILE.parent.iterdir()}
    check(data_after - data_before <= {"widgets.json", "devices.json", "power.json"}, f"C10c foreign files were created: {sorted(data_after - data_before)}")
    r = raw("GET", "/api/widgets", headers=own)
    ct = (r["headers"].get("content-type") or [""])[0]
    check(r["status"] == 200 and ct.startswith("application/json"), f"C10c widget view not delivered as JSON: {ct}")
    print("C10c widget view: only known widget types and checked ids, texts and lists limited, script text stays data, no foreign files")

    # C11) Device lock, effects, room variants (0.15) write to devices.json/effects.json/scenes.json:
    #      only for devices/rooms that exist – path tricks never end up as a file and never as a key
    from app import device_store as dvs, effects_store as efs, scenes_store as scs
    data_dir = dvs.DEVICES_FILE.parent
    before = {x.name for x in data_dir.iterdir()}
    scenes_before = scs.get()["scenes"]
    hdr = {**own, "content-type": "application/json"}
    for v in ("..%2f..%2fauth.json", "a" * 3000, "%0d%0aX-Injected:1", "..", "hell"):
        for m, p in (("POST", f"/api/devices/{v}/lock"), ("POST", f"/api/devices/{v}/unlock"), ("PUT", f"/api/effects/{v}"),
                     ("DELETE", f"/api/effects/{v}"), ("POST", f"/api/scenes/hell/variants/{v}"), ("DELETE", f"/api/scenes/{v}/variants/{v}"),
                     ("POST", f"/api/scenes/from-room/{v}"), ("POST", f"/api/scenes/{v}/apply-house")):
            security._hits.clear()
            auth._fails.clear()
            r = raw(m, p, headers=hdr, body=json.dumps({"effect": "candle", "name": "x"}).encode())
            check(r["status"] < 500, f"C11 {m} {p[:40]} -> {r['status']}")
    check(not dvs.locked(), f"C11 a lock was saved without a valid device: {list(dvs.locked())[:3]}")
    check(not efs.get()["rooms"], "C11 an effect was saved without a valid room")
    check(all(not s.get("variants") for s in scs.get()["scenes"]) and len(scs.get()["scenes"]) == len(scenes_before),
          "C11 a room variant or scene was created without a valid room")
    check({x.name for x in data_dir.iterdir()} - before <= {"devices.json", "effects.json", "power.json"},
          f"C11 foreign files were created: {sorted({x.name for x in data_dir.iterdir()} - before)}")
    print("C11 lock, effects, room variants: only for existing devices/rooms, no path tricks, no foreign files")

finish("INPUT: all checks OK")
