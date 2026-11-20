"""ComfyOS – security: browser. What can a foreign web page do through the signed-in browser of the owner?

Basic protection:
  2   host check against DNS rebinding, origin check against cross-site requests
  4   protective headers on all responses; no eval, no inline scripts in the frontend
Attacks (B = foreign web page):
  B1  cross-site requests in every packaging
  B2  nothing writing by GET, no CORS release, framing blocked
  B3  DNS rebinding through the Host header

    python tests/security/test_browser.py [path-to-comfyos]
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.attack import *  # noqa: E402,F401,F403 – app, safeguard and helpers of the security tests

STATIC_JS = ROOT / "backend" / "app" / "static" / "js"

with TestClient(main.app, raise_server_exceptions=False) as client:
    raw = raw_for(client)
    auth.set_password(PW)

    # 2) Host check against DNS rebinding, origin check against cross-site requests
    for host in ("evil.example.com", "homeos.attacker.net", "192.168.1.5.nip.io"):
        r = client.get("/api/health", headers={"host": host})
        assert r.status_code == 400 and "nicht freigegeben" in r.text, (host, r.status_code)
        assert client.get("/", headers={"host": host}).status_code == 400
    for host in ("192.168.1.5", "192.168.1.5:8080", "[fd00::5]:80", "comfyos", "comfyos.local", "comfyos.fritz.box",
                 "localhost:8000", "testserver"):
        assert client.get("/api/health", headers={"host": host}).status_code == 200, host
    r = client.put("/api/security/hosts", json={"hosts": ["comfyos.example-vpn.net"]}, headers={"origin": "http://testserver"})
    assert r.status_code == 401   # without login
    tok = auth.create_session("pytest", "127.0.0.1")
    client.cookies.set(auth.COOKIE, tok)
    r = client.put("/api/security/hosts", json={"hosts": ["comfyos.example-vpn.net", "bad host", "x" * 300]})
    assert r.status_code == 200 and r.json()["allowed_hosts"] == ["comfyos.example-vpn.net"], r.text
    assert client.get("/api/health", headers={"host": "comfyos.example-vpn.net"}).status_code == 200
    client.put("/api/security/hosts", json={"hosts": []})

    body = {"hosts": []}
    assert client.put("/api/security/hosts", json=body, headers={"origin": "http://evil.example.com"}).status_code == 403
    assert client.put("/api/security/hosts", json=body, headers={"origin": "null"}).status_code == 403
    assert client.put("/api/security/hosts", json=body, headers={"referer": "http://evil.example.com/x"}).status_code == 403
    assert client.put("/api/security/hosts", json=body, headers={"origin": "http://testserver"}).status_code == 200
    assert client.put("/api/security/hosts", json=body, headers={"origin": "https://comfyos:8443", "host": "comfyos:8443"}).status_code == 200
    assert client.put("/api/security/hosts", json=body).status_code == 200   # not a browser (script) -> no CSRF
    assert client.post("/api/auth/login", json={"password": "x"}, headers={"origin": "http://evil.example.com"}).status_code == 403
    print("2 foreign host names 400 (IPs/home network names/entered names ok); foreign/null origin 403")

    marker = "secret_backend"            # only appears in the real response of /api/settings

    # ============================================================ B  Foreign web page in the browser of the owner
    # B1) Cross-site requests: a foreign origin triggers nothing – no matter how it is packaged
    own = {"cookie": f"{auth.COOKIE}={tok}", "host": "comfyos.local"}
    evil_origins = ["http://evil.example.com", "https://homeos.local.evil.example.com", "http://homeos.local.evil.example.com",
                    "http://evilhomeos.local", "http://homeos.local:8080", "null", "", "file://", "http://", "javascript:alert(1)",
                    "http://homeos.local@example.com", "http://evil.example.com/homeos.local", "http://homeos.localx",
                    "chrome-extension://abcdef", "http://xn--comfyos-.local"]
    before = settings_store.load_settings().get("allowed_hosts")
    for o in evil_origins:
        for ctype in ("application/json", "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
            r = raw("PUT", "/api/security/hosts", body=b'{"hosts": ["evil.example.com"]}',
                    headers={**own, "origin": o, "content-type": ctype})
            check(r["status"] == 403, f"B1 foreign origin {o!r} ({ctype}) -> {r['status']}")
    for ref in ("http://evil.example.com/", "https://evil.example.com/?http://homeos.local/", "http://homeos.local.evil.example.com/x"):
        r = raw("POST", "/api/restart", headers={**own, "referer": ref})
        check(r["status"] == 403, f"B1 foreign referer {ref!r} -> {r['status']}")
    check(settings_store.load_settings().get("allowed_hosts") == before and not restarts, "B1 a cross-site request changed something")
    r = raw("PUT", "/api/security/hosts", body=b'{"hosts": []}', headers={**own, "origin": "http://comfyos.local", "content-type": "application/json"})
    check(r["status"] == 200, f"B1 own origin rejected: {r['status']}")
    print(f"B1 {len(evil_origins)} foreign origins × 4 formats and 3 referers: all 403, nothing changed")

    # B2) Nothing writing by GET (image/link trick), no release for foreign JavaScript (CORS)
    getters = {(r.path, m) for r in main.routes() if getattr(r, "path", "").startswith("/api/") for m in (r.methods or set())}
    for path, m in sorted(getters):
        if m != "GET":
            check((path, "GET") in getters or raw("GET", re.sub(r"\{[^}]+\}", "x", path), headers=own)["status"] in (404, 405),
                  f"B2 {m} {path} also reachable by GET")
    state_words = ("restart", "logout", "delete", "remove", "clear", "apply", "activate", "import", "push", "forget", "pair", "stop")
    for path, m in sorted(getters):
        if m == "GET" and any(w in path.rsplit("/", 1)[-1] for w in state_words):
            check(path in ("/api/heating/import", "/api/flows/import", "/api/rooms/pending"), f"B2 a GET route sounds like an action: {path}")
    for path in ("/api/settings", "/api/license", "/api/health", "/"):
        r = raw("GET", path, headers={**own, "origin": "http://evil.example.com"})
        check("access-control-allow-origin" not in r["headers"] and "access-control-allow-credentials" not in r["headers"],
              f"B2 CORS release on {path}")
    r = raw("OPTIONS", "/api/settings", headers={**own, "origin": "http://evil.example.com", "access-control-request-method": "POST",
                                                 "access-control-request-headers": "content-type"})
    check("access-control-allow-origin" not in r["headers"], "B2 the CORS preflight allows a foreign origin")
    for path in ("/", "/api/health", "/api/settings", "/static/js/core.js", "/gibt-es-nicht", "/api/gibt-es-nicht"):
        h = raw("GET", path, headers=own)["headers"]
        csp = (h.get("content-security-policy") or [""])[0]
        check("frame-ancestors 'none'" in csp and "default-src 'self'" in csp and "'unsafe-eval'" not in csp
              and "script-src 'self'" in csp and (h.get("x-frame-options") or [""])[0] == "DENY"
              and (h.get("x-content-type-options") or [""])[0] == "nosniff", f"B2 protective headers missing on {path}")
    print("B2 nothing writing by GET, no CORS release, framing/script injection blocked by headers")

    # B3) DNS-Rebinding: fremde Namen im Host-Header
    bad_hosts = ["evil.example.com", "evil.example.com:80", "homeos.local.evil.example.com", "localhost.evil.example.com",
                 "127.0.0.1.evil.example.com", "192.168.1.5.nip.io", "192-168-1-5.sslip.io", "example.com.", "", " ",
                 "evil.example.com#@homeos.local", "homeos.local/evil.example.com", "homeos.local\tevil.example.com",
                 "xn--80ak6aa92e.com", "evil.example.com.local.evil.example.com", "[evil.example.com]", "evil.example.com, homeos.local"]
    for h in bad_hosts:
        r = raw("GET", "/api/health", headers={"host": h})
        check(r["status"] == 400, f"B3 foreign host {h!r} accepted ({r['status']})")
        r = raw("GET", "/api/settings", headers={"host": h, "cookie": f"{auth.COOKIE}={tok}"})
        check(marker not in r["text"], f"B3 data readable through the foreign host {h!r}")
    r = raw("GET", "/", headers={"host": XSS + ".example.com"})
    check(r["status"] == 400 and "<svg" not in r["text"] and "alert(1)" not in r["text"], "B3 the Host header is written unfiltered into the error page")
    r = raw("GET", "/api/health", headers={"host": "comfyos.local", "x-forwarded-host": "evil.example.com", "forwarded": "host=evil.example.com"})
    check(r["status"] == 200 and "evil" not in r["text"], "B3 X-Forwarded-Host influences the response")
    for h in ("192.168.1.5", "192.168.1.5:8443", "[fd00::5]", "[fd00::5]:8443", "comfyos", "comfyos.local", "comfyos.fritz.box", "COMFYOS.LOCAL", "localhost"):
        check(raw("GET", "/api/health", headers={"host": h})["status"] == 200, f"B3 own host {h!r} rejected")
    print(f"B3 {len(bad_hosts)} foreign host names rejected (DNS rebinding), error page without injection, own addresses work")

    # ============================================================ Grundschutz (bis 0.19 tests/test_security.py)

    # 4) Security headers; no eval/new Function/inline handlers in the frontend
    for url in ("/", "/api/health", "/static/js/core.js"):
        h = client.get(url).headers
        assert "default-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"], url
        assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer" and h["x-frame-options"] == "DENY"
    bad = re.compile(r"\beval\(|new Function\(|\son(click|change|input|load|error|submit|mouseover)=\"")
    for js in list(STATIC_JS.glob("*.js")) + [ROOT / "backend" / "app" / "static" / "index.html"]:
        assert not bad.search(js.read_text(encoding="utf-8")), js.name
    assert "<script>" not in (ROOT / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")
    print("4 CSP/nosniff/no-referrer/DENY on all responses; no eval, no inline scripts/handlers")

finish("BROWSER: all checks OK")
