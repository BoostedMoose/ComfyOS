"""ComfyOS – security: fuzzing. Every route gets broken input – every new one too, without the test being adapted.

  C7  about 2400 broken requests: never a server error, never a stack trace, never an internal path; afterwards
      all data files are still valid JSON and no connection went to a target that is not registered

    python tests/security/test_fuzz.py [path-to-comfyos]
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
    # Initial situation as after a while of operation: one flow and one room name. At the end the data files must be intact.
    flows_store.create("Hook", [{"id": "w", "type": "webhook", "x": 0, "y": 0, "config": {}}], [], enabled=True)
    from app import floorplan_store
    floorplan_store.set_room_name("wohnen", "Wohnzimmer")

    # C7) Fuzzing: every route with broken input – never 500, never a stack trace, never internal paths
    lic.OWNER_FILE.unlink(missing_ok=True)
    lic.apply()
    install_homebase(None)
    deep = b"[" * 50_000 + b"]" * 50_000
    bodies = [b"", b"{", b"[]", b"null", b'"x"', b"12", b"{}", b'{"a":{"b":[1,2,{"c":null}]}}', deep, b"\xff\xfe\x00", b'{"__proto__":{"x":1}}',
              json.dumps({"name": "x" * 100_000}).encode(), b'{"a":1e999}', b'{"a":NaN}', b'[' + b'1,' * 200_000 + b'1]']
    path_values = ["x", "..%2f..%2fetc%2fpasswd", "..%5c..%5cx", "%00", "%0d%0aX-Injected:1", "<svg%20onload=alert(1)>", "a" * 3000,
                   "%F0%9F%92%A9", "'%20OR%201=1--", "{{7*7}}", "${7*7}", "-1", "null", "%2e%2e"]
    typed = [lambda f: [["x"]], lambda f: "x" * 20_000, lambda f: None, lambda f: -1, lambda f: {"$ne": 1}, lambda f: True, lambda f: 1e308,
             lambda f: XSS, lambda f: "../../etc/passwd", lambda f: "\x00"]
    skip = {("POST", "/api/auth/logout"), ("GET", "/api/stream")}
    fuzzed = typed_routes = 0
    errors500: dict[str, int] = {}
    outbound.clear()

    def judge(m, path, r, what):
        global fuzzed
        fuzzed += 1
        key = f"{m} {path}"
        if (r["status"] >= 500 and r["status"] not in (502, 504)) or r.get("crashed"):   # 502/504 = peer unreachable (intended)
            errors500.setdefault(f"{key} [{what}] -> {r['status']} {r['text'][:120]!r}", 0)
        check("Traceback" not in r["text"] and 'File "' not in r["text"], f"C7 stack trace in {key} [{what}]")
        if r["status"] >= 400:
            check(str(ROOT) not in r["text"] and "site-packages" not in r["text"] and str(tmp) not in r["text"], f"C7 internal path in the error of {key} [{what}]")
        ct = (r["headers"].get("content-type") or [""])[0]
        if "html" in ct:
            check("<svg onload" not in r["text"], f"C7 input unfiltered in the HTML of {key}")
        check((r["headers"].get("x-content-type-options") or [""])[0] == "nosniff", f"C7 protective headers missing at {key} [{what}]")

    for route in main.routes():
        path, methods = getattr(route, "path", ""), (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}
        if not path.startswith("/api/"):
            continue
        fields = body_fields(route)
        typed_routes += bool(fields)
        for m in sorted(methods):
            if (m, path) in skip:
                continue
            base_url = re.sub(r"\{[^}]+\}", "x", path)
            hdr = {**own, "content-type": "application/json"}
            if not auth.check_session(tok):
                tok = auth.create_session("pentest", "192.168.1.20")
                own["cookie"] = hdr["cookie"] = f"{auth.COOKIE}={tok}"
            if "{" in path:
                for v in path_values:
                    security._hits.clear()
                    auth._fails.clear()
                    judge(m, path, raw(m, re.sub(r"\{[^}]+\}", v, path), headers=hdr, body=b"{}" if m != "GET" else b""), f"Pfad {v[:16]}")
            if m == "GET":
                for q in ("?hours=abc", "?hours=-1&limit=-5", "?limit=99999999999999999999", "?device_id=" + "a" * 5000, "?p=..%2f..&types=%00",
                          "?hours[]=1&hours[]=2", "?" + "&".join(f"x{i}=1" for i in range(2000))):
                    judge(m, path, raw(m, base_url + q, headers=hdr), f"Query {q[:14]}")
                continue
            for b in bodies:
                security._hits.clear()
                auth._fails.clear()
                judge(m, path, raw(m, base_url, headers=hdr, body=b), f"Body {b[:12]!r}")
            for make in typed:
                if not fields:
                    break
                security._hits.clear()
                auth._fails.clear()
                payload = json.dumps({f: make(f) for f in fields}).encode()
                judge(m, path, raw(m, base_url, headers=hdr, body=payload), f"Typ {payload[:24]!r}")
    for msg in sorted(errors500):
        findings.append("C7 Serverfehler: " + msg)
    check(typed_routes > 25, f"test setup: only {typed_routes} routes with a recognised input model")
    check(auth.verify_password(PW), "C7 fuzzing changed the password")
    for f in (Path(os.environ["COMFYOS_HOME"]) / "data").glob("*.json"):
        try:
            strict_json(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            findings.append(f"C7 Datendatei nach dem Fuzzing kein gültiges JSON mehr: {f.name} ({exc})")
    registered = [e for e in egress.REGISTRY.values() if e.hosts != ("*",)]
    stray = sorted({o for o in outbound if not egress.is_local_host(o) and not any(egress.host_allowed(e, o) for e in registered)})
    check(not stray, f"C7 connection to a target that is not registered: {stray[:5]}")
    print(f"C7 {fuzzed} broken requests to all routes: no server error, no stack trace, no internal paths, data intact")

finish("FUZZING: all checks OK")
