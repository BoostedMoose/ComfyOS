"""ComfyOS – security: files. Does someone with a valid session get at files that are none of their business?

Attacks (C = with a valid session):
  C1  read or delete foreign files: static files, backups, logs, images, update paths
  C2  manipulated packages: update ZIP with escape paths, backup with a bomb, foreign paths, wrong settings

    python tests/security/test_files.py [path-to-comfyos]
"""
import io
import json
import os
import time
import zipfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.attack import *  # noqa: E402,F401,F403 – app, safeguard and helpers of the security tests

with TestClient(main.app, raise_server_exceptions=False) as client:
    raw = raw_for(client)
    tok, own = login(client)
    flows_store.create("Hook", [{"id": "w", "type": "webhook", "x": 0, "y": 0, "config": {}}], [], enabled=True)      # flows.json exists – C2 checks that it stays intact

    # ============================================================ C  Attacker with a valid session
    # C1) Reading/deleting foreign files: static files, backups, logs, images, update package
    for p in ("/static/../main.py", "/static/..%2fmain.py", "/static/%2e%2e/main.py", "/static/..%5cmain.py", "/static/js/../../auth.py",
              "/static/%2e%2e%2f%2e%2e%2f%2e%2e%2fVERSION", "/static//etc/passwd", "/static/C:%5cWindows%5cwin.ini",
              "/static/..%252f..%252fmain.py", "/static/....//main.py", "/static/js/core.js%00.png", "/static/%00"):
        r = raw("GET", p, headers=own)
        check(r["status"] in (400, 404) and "from fastapi" not in r["text"] and "import " not in r["text"][:200], f"C1 source code/file through {p} ({r['status']})")
    outside = backup_store.BACKUP_DIR.parent / "comfyos-backup-fremd.zip"
    backup_store.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"PK-nicht-anfassen")
    ents.use_plan("owner")                                    # backup is a Pro feature – this is about the paths
    real = backup_store.create_backup()["file"]
    for name in ("..%5ccomfyos-backup-fremd.zip", "..%2fcomfyos-backup-fremd.zip", "%2e%2e%5ccomfyos-backup-fremd.zip",
                 "comfyos-backup-..%5c..%5cx.zip", "comfyos-backup-%0d%0aSet-Cookie:%20x=1.zip", 'comfyos-backup-".zip',
                 "comfyos-backup-x.zip%00.txt", "auth.json", "..", "comfyos-backup-" + "a" * 500 + ".zip", "HOMEOS-BACKUP-x.zip"):
        for m in ("GET", "DELETE"):
            r = raw(m, "/api/backups/" + name, headers=own)
            check(r["status"] in (400, 404, 405) and b"PK-nicht" not in r["body"] and "x=1" not in str(r["headers"].get("set-cookie")),
                  f"C1 backup path {m} {name[:40]} -> {r['status']}")
    check(outside.read_bytes() == b"PK-nicht-anfassen", "C1 a file outside the backup folder was deleted/changed")
    r = raw("GET", "/api/backups/" + real, headers=own)
    check(r["status"] == 200 and r["body"][:2] == b"PK", "C1 a real backup cannot be fetched")
    for cat in ("..%2fevents", "..%5cevents", "events%00", "events/../../auth", "*", "%2e%2e", XSS):
        for sub, m in (("stats", "GET"), ("export", "GET"), ("clear", "POST")):
            r = raw(m, f"/api/logs/{cat}/{sub}", headers=own)
            check(r["status"] in (400, 404, 405) and "<svg" not in (r["headers"].get("content-disposition") or [""])[0],
                  f"C1 log category {cat!r}/{sub} -> {r['status']}")
    for p in ("../../../etc/passwd", "..%2f..%2fauth.json", "localized/../../x", "http://evil.example.com/x.png", "//evil.example.com/x",
              "localized/x.png%00", "a" * 5000):
        r = raw("GET", "/api/plugins/qivicon/pairing/image?p=" + p, headers=own)
        check(r["status"] in (400, 404, 422), f"C1 catalogue image with path {p[:30]!r} -> {r['status']}")
    for rid in ("../x", "00000000-0000-4000-8000-00000000000g", "%2e%2e", "x" * 36):
        check(raw("GET", "/api/scenes/image/hue/" + rid, headers=own)["status"] == 404, f"C1 Hue-Bild {rid!r}")
    for rid in ("../x", "%2e%2e", "..%2f..%2fauth.json", "bild.png%00", "a" * 121, "0" * 24):
        for plugin_id in ("sonos", "hue", "..", "gibtsnicht"):
            check(raw("GET", f"/api/media/image/{plugin_id}/{rid}", headers=own)["status"] in (400, 404), f"C1 cover picture {plugin_id}/{rid[:20]!r}")
    (prog.parent / "fremd.zip").write_bytes(b"kein zip")
    for f in ("../fremd.zip", "..\\..\\fremd.zip", "/etc/passwd", "C:\\Windows\\win.ini", "fremd.zip", "", "x" * 4000):
        r = raw("POST", "/api/update/apply", headers={**own, "content-type": "application/json"}, body=json.dumps({"file": f, "force": True}).encode())
        check(r["status"] in (400, 422) and not restarts, f"C1 update from a foreign file {f[:20]!r} -> {r['status']}")
    print("C1 no access to foreign files: static files, backup names, log categories, catalogue, Hue and cover pictures, update paths")

    # C2) Manipulated packages: update ZIP with escape paths, backup with a bomb/foreign paths/forged settings
    def zip_bytes(entries, compress=zipfile.ZIP_DEFLATED):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compress) as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def post_bytes(path, data):
        return raw("POST", path, headers={**own, "content-type": "application/zip"}, body=data)

    base_pkg = {"comfyos/backend/app/main.py": "x = 1", "comfyos/VERSION": "99.0.0"}
    for evil_name in ("comfyos/../../ausbruch.py", "comfyos/..\\..\\ausbruch.py", "comfyos//etc/cron.d/x", "comfyos/C:/x.py",
                      "comfyos/backend/../../../ausbruch.py"):
        r = post_bytes("/api/update/upload", zip_bytes({**base_pkg, evil_name: "boese"}))
        check(r["status"] == 400 and "Unsicherer Pfad" in r["text"], f"C2 update package with {evil_name!r} accepted ({r['status']})")
    for junk in (b"", b"PK\x03\x04kaputt", b"kein zip", zip_bytes({"nichts": "x"}), zip_bytes({"comfyos/backend/app/main.py": "x"})):
        check(post_bytes("/api/update/upload", junk)["status"] == 400, "C2 an invalid update package was accepted")
    leftovers = [p.name for p in prog.parent.iterdir() if p.name.startswith("comfyos-upload")]
    check(not leftovers and not list(tmp.rglob("ausbruch.py")), f"C2 rejected uploads are left behind or escape: {leftovers}")
    marker_file = Path(os.environ["COMFYOS_HOME"]) / "data" / "flows.json"
    flows_before = marker_file.read_bytes()
    evil_backup = zip_bytes({"../../ausbruch.json": "{}", "data/../../ausbruch.json": "{}", "/abs/ausbruch.json": "{}",
                             "data/auth.json": '{"password": null}', "auth.json": '{"password": null}',
                             "secrets.json": "{}", "license.json": '{"type": "owner"}', "data/flows.json": "{kein json",
                             "settings.json": json.dumps({"mode": "homebase", "base_url": "https://evil.example.com", "username": "",
                                                          "allowed_hosts": ["evil.example.com", "bad host"], "https": "ja",
                                                          "unbekannt": {"x": 1}})})
    r = post_bytes("/api/backups/restore", evil_backup)
    check(r["status"] == 400 and marker_file.read_bytes() == flows_before, f"C2 a backup with broken content was restored ({r['status']})")
    with zipfile.ZipFile(io.BytesIO(evil_backup)) as zf:
        rest = {n: zf.read(n) for n in zf.namelist() if n != "data/flows.json"}
    r = post_bytes("/api/backups/restore", zip_bytes(rest))
    cfg = settings_store.load_settings()
    check(r["status"] == 200 and json.loads(r["text"])["restored"] == ["settings.json"], f"C2 backup response unexpected: {r['status']} {r['text'][:120]}")
    check(cfg.get("base_url") != "https://evil.example.com" and "unbekannt" not in cfg and cfg.get("allowed_hosts") == ["evil.example.com"],
          f"C2 settings from the backup taken over unchecked: {cfg}")
    check(not list(tmp.rglob("ausbruch.json")) and auth.verify_password(PW) and not lic.OWNER_FILE.exists()
          and not (settings_store.APP_DIR / "secrets.json").exists(), "C2 the backup wrote files outside the data list")
    settings_store.save_settings({**cfg, "allowed_hosts": [], "mode": ""})
    security.reset_cache()
    bomb = zip_bytes({"data/flows.json": b"[" + b" " * (120 * 1024 * 1024) + b"]"})
    check(len(bomb) < 1024 * 1024, "test setup: bomb not small enough")
    t0 = time.time()
    r = post_bytes("/api/backups/restore", bomb)
    check(r["status"] == 400 and marker_file.read_bytes() == flows_before and time.time() - t0 < 20, f"C2 a ZIP bomb was restored ({r['status']})")
    for junk in (b"", b"kein zip", b"PK\x03\x04", zip_bytes({})):
        r = post_bytes("/api/backups/restore", junk)
        check(r["status"] in (200, 400) and "Traceback" not in r["text"], f"C2 junk as a backup -> {r['status']}")
    print("C2 update packages with escape paths and backups with a bomb, foreign paths, wrong settings rejected or cleaned")

finish("FILES: all checks OK")
