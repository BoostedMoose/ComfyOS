"""ComfyOS – security: access. Who gets in, and who does not?

Attacks (A = without login in the home network, C = with a valid session):
  A1  take over a fresh installation          A3  forge sessions
  A2  bypass the login with path tricks       A4  guess the password
  C9  sessions after a password change
Basic protection:
  1   every route requires login, the list without login is exact
  3   block after failed attempts, cookie attributes
  8   session list, signing out individually, expiry

The order matters: A1 needs the installation that is not set up yet and sets it up at the end.

    python tests/security/test_access.py [path-to-comfyos]
"""
import json
import re
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.attack import *  # noqa: E402,F401,F403 – app, safeguard and helpers of the security tests

PUBLIC = {"/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/setup"}


def routes():
    """Alle API-Routen als (Pfad, Methoden)."""
    for r in main.routes():
        path, methods = getattr(r, "path", ""), getattr(r, "methods", None) or set()
        if path.startswith("/api/"):
            yield path, methods


with TestClient(main.app, raise_server_exceptions=False) as client:
    raw = raw_for(client)
    # ============================================================ A  Attacker without login
    # A1) Fresh installation that is not set up yet: nothing readable, takeover only with the setup code
    assert not auth.is_configured()
    code = auth.setup_code()
    for path in ("/api/settings", "/api/overview", "/api/devices", "/api/license", "/api/events", "/api/auth/sessions"):
        check(raw("GET", path)["status"] == 401, f"A1 {path} readable without setup")
    for path in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect", "/api/openapi.json", "/api/docs"):
        r = raw("GET", path)
        check(r["status"] in (401, 404) and "/api/settings" not in r["text"] and "swagger" not in r["text"].lower(),
              f"A1 list of all routes readable without login through {path} ({r['status']})")
    open_pages = sorted({getattr(route, "path", "") for route in main.app.routes} - {""})
    open_pages = [p for p in open_pages if not p.startswith("/api/")]
    check(open_pages == ["/", "/apple-touch-icon.png", "/favicon.ico", "/manifest.webmanifest", "/static", "/stream/live.wav", "/sw.js"],
          f"A1 pages outside /api/ (reachable without login) have changed: {open_pages}")
    r = raw("POST", "/api/auth/setup", **jbody({"password": "angreifer-pw"}))
    check(r["status"] == 403, f"A1 takeover without the setup code: {r['status']}")
    for hdr in ({"x-forwarded-for": "127.0.0.1"}, {"forwarded": "for=127.0.0.1"}, {"x-real-ip": "127.0.0.1"},
                {"x-forwarded-host": "localhost"}, {"host": "localhost"}, {"host": "127.0.0.1"}):
        auth.clear_failures("192.168.1.66")
        r = raw("POST", "/api/auth/setup", body=json.dumps({"password": "angreifer-pw"}).encode(),
                headers={"content-type": "application/json", **hdr})
        check(r["status"] == 403 and not auth.is_configured(), f"A1 takeover with a forged header {hdr}: {r['status']}")
    auth.clear_failures("192.168.1.66")
    guesses = [raw("POST", "/api/auth/setup", **jbody({"password": "angreifer-pw", "code": f"AAAA-{i:04d}"}))["status"] for i in range(8)]
    check(guesses[:auth.FAILS_PER_LOCK] == [403] * auth.FAILS_PER_LOCK and set(guesses[auth.FAILS_PER_LOCK:]) == {429},
          f"A1 the setup code can be brute-forced: {guesses}")
    r = raw("POST", "/api/auth/setup", **jbody({"password": "angreifer-pw", "code": code}))
    check(r["status"] == 429, "A1 the block does not apply to the right code (time window for attackers)")
    for p in ("/static/../../setup_code.txt", "/static/..%2f..%2f..%2fsetup_code.txt", "/setup_code.txt", "/static/setup_code.txt"):
        r = raw("GET", p)
        check(r["status"] in (400, 401, 404) and code not in r["text"], f"A1 setup code retrievable through {p}")
    r = raw("POST", "/api/auth/setup", client=("127.0.0.1", 5555), **jbody({"password": "kurz"}))
    check(r["status"] == 400 and not auth.is_configured(), "A1 a password that is too short was accepted")
    r = raw("POST", "/api/auth/setup", client=("127.0.0.1", 5555), **jbody({"password": PW}))
    check(r["status"] == 200 and auth.is_configured(), f"A1 setup on the device itself fails: {r['status']}")
    r = raw("POST", "/api/auth/setup", client=("127.0.0.1", 5555), **jbody({"password": "zweites-passwort"}))
    check(r["status"] == 409 and auth.verify_password(PW), "A1 a set-up ComfyOS can be set up again")
    auth._fails.clear()
    auth._global_fails.clear()
    print("A1 before the setup: nothing readable; takeover only on the device or with the code; code cannot be guessed or retrieved")

    # A2) Bypassing the login: path tricks past the login check
    tok = auth.create_session("pentest", "192.168.1.20")
    client.cookies.set(auth.COOKIE, tok)
    marker = "secret_backend"            # only appears in the real response of /api/settings
    tricks = ["/api/settings/", "/api//settings", "//api/settings", "/API/settings", "/Api/Settings", "/api/settings%00",
              "/api/settings;a=b", "/api/settings.json", "/api/health/../settings", "/api/flows/hook/../../settings",
              "/api/flows/hook/%2e%2e/%2e%2e/settings", "/api/flows/hook/x/../../settings", "/api/auth/login/../../settings",
              "/api/auth/state/..%2f..%2fsettings", "/./api/settings", "/static/../api/settings", "/static/..%2fapi%2fsettings",
              "/api/health%2f..%2fsettings", "/api/flows/hook/..%5c..%5csettings", "/api/health?x=/api/settings",
              "/api/health#/../settings", "/%61pi/settings", "/api/%73ettings", "/api/flows/hook/"]
    for t in tricks:
        r = raw("GET", t)
        check(marker not in r["text"] and not (200 <= r["status"] < 300 and "password_saved" in r["text"]),
              f"A2 login bypassed with {t} ({r['status']})")
        check(r["status"] != 500, f"A2 {t} -> 500")
    for m in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE", "CONNECT", "FOO"):
        r = raw(m, "/api/settings", headers={"x-http-method-override": "GET", "x-original-url": "/api/health",
                                             "x-rewrite-url": "/api/health"})
        check(marker not in r["text"] and r["status"] in (401, 405), f"A2 {m} /api/settings without login: {r['status']}")
    r = raw("POST", "/api/flows/hook/x", headers={"x-original-url": "/api/restart"})
    check(r["status"] == 404 and not restarts, "A2 webhook path as a springboard")
    print(f"A2 {len(tricks)} path tricks and 9 methods: no way past the login")

    # A3) Forging sessions
    stored_hash = auth._hash_token(tok)
    forged = ["", "x", "null", "undefined", "0", stored_hash, stored_hash[:16], tok[:-1], tok + "x", tok.upper(), tok.lower() + " ",
              "a" * 100_000, tok + "\x00", "../../auth.json", "' OR '1'='1", '{"$ne": null}', "true"]
    for f in forged:
        if f == tok:
            continue
        r = raw("GET", "/api/settings", headers={"cookie": f"{auth.COOKIE}={f}"})
        check(r["status"] == 401, f"A3 a forged session was accepted: {f[:24]!r}")
    r = raw("GET", "/api/settings", headers={"cookie": f"other={tok}; {auth.COOKIE}x={tok}"})
    check(r["status"] == 401, "A3 a session from a wrong cookie name was accepted")
    r = raw("GET", "/api/settings", headers={"authorization": f"Bearer {tok}", "x-api-key": tok})
    check(r["status"] == 401, "A3 a session through the Authorization header was accepted")
    check(raw("GET", "/api/settings", headers={"cookie": f"{auth.COOKIE}={tok}"})["status"] == 200, "A3 a real session was rejected")
    r = raw("POST", "/api/auth/login", scheme="https", **jbody({"password": PW}))
    cookie = (r["headers"].get("set-cookie") or [""])[0].lower()
    check(r["status"] == 200 and "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie and "path=/" in cookie,
          f"A3 session cookie over HTTPS without HttpOnly/Strict/Secure: {cookie[:120]}")
    fresh = re.search(auth.COOKIE + r"=([^;]+)", (r["headers"].get("set-cookie") or [""])[0]).group(1)
    check(fresh != tok and len(fresh) >= 40, "A3 session token too short or reused")
    raw("POST", "/api/auth/logout", headers={"cookie": f"{auth.COOKIE}={fresh}"})
    check(raw("GET", "/api/settings", headers={"cookie": f"{auth.COOKIE}={fresh}"})["status"] == 401, "A3 the session lives on after signing out")
    print(f"A3 {len(forged) + 2} forged sessions rejected (the stored hash too); cookie HttpOnly+Strict+Secure; signing out works")

    # A4) Guessing the password: block per address, distributed attacks only slow down – the owner still gets in
    codes = [raw("POST", "/api/auth/login", client=("192.168.1.77", 1), **jbody({"password": f"versuch-{i}"}))["status"] for i in range(7)]
    check(codes == [401] * 5 + [429] * 2, f"A4 no block after 5 failed attempts: {codes}")
    check(raw("POST", "/api/auth/login", client=("192.168.1.77", 1), **jbody({"password": PW}))["status"] == 429,
          "A4 a blocked address gets in at once with the right password")
    check(raw("POST", "/api/auth/login", client=("192.168.1.20", 1), **jbody({"password": PW}))["status"] == 200,
          "A4 the block of an attacker locks the owner out too")
    r1 = raw("POST", "/api/auth/login", client=("192.168.1.78", 1), **jbody({"password": "x"}))
    r2 = raw("POST", "/api/auth/login", client=("192.168.1.79", 1), **jbody({"password": PW[:-1]}))
    check(r1["text"] == r2["text"], "A4 the response reveals how close the password was")
    for body in (b"", b"{}", b'{"password": null}', b'{"password": ["a"]}', b'{"password": {"$ne": ""}}', b'{"password": true}',
                 json.dumps({"password": "x" * 2_000_000}).encode()):
        r = raw("POST", "/api/auth/login", client=("192.168.1.80", 1), body=body, headers={"content-type": "application/json"})
        check(400 <= r["status"] < 500 and "set-cookie" not in r["headers"], f"A4 login with junk: {r['status']} {body[:30]!r}")
    auth._fails.clear()
    auth._global_fails.clear()
    print("A4 password guessing: block per address, the owner stays able to act, responses reveal nothing")

    own = {"cookie": f"{auth.COOKIE}={tok}", "host": "comfyos.local"}       # der angemeldete Besitzer

    # C9) Sessions after a password change: whoever had the old password is thrown out
    stolen = auth.create_session("angreifer", "192.168.1.66")
    r = raw("POST", "/api/auth/password", headers={**own, "content-type": "application/json"}, body=json.dumps({"old": "falsch", "new": "neues-passwort-1"}).encode())
    check(r["status"] == 401 and auth.verify_password(PW), "C9 password change without the old password")
    r = raw("POST", "/api/auth/password", headers={**own, "content-type": "application/json"}, body=json.dumps({"old": PW, "new": "neues-passwort-1"}).encode())
    check(r["status"] == 200 and auth.check_session(tok) and not auth.check_session(stolen), "C9 a foreign session survives the password change")
    auth._fails.clear()
    tries = [raw("POST", "/api/auth/password", headers={**own, "content-type": "application/json"},
                 body=json.dumps({"old": f"rate-{i}", "new": "angreifer-pw-1"}).encode())["status"] for i in range(7)]
    check(tries[-1] == 429 and auth.verify_password("neues-passwort-1"), f"C9 the old password can be brute-forced with an open session: {tries}")
    auth._fails.clear()
    print("C9 a password change signs out all other devices; the old password cannot be brute-forced with an open session")

    # ============================================================ Grundschutz (bis 0.19 tests/test_security.py)
    auth.set_password("geheim-123")
    auth._fails.clear()
    auth._global_fails.clear()
    security._hits.clear()
    anon = TestClient(main.app)          # a second visitor without a session

    # 1) Every /api route outside the allowlist requires login; allowlist exactly as expected
    assert main.PUBLIC_API == PUBLIC, main.PUBLIC_API
    assert main.HOOK_PREFIX == "/api/flows/hook/"
    checked = 0
    for path, methods in routes():
        if path in PUBLIC or path.startswith(main.HOOK_PREFIX):
            continue
        url = re.sub(r"\{[^}]+\}", "x", path)
        for m in methods - {"HEAD", "OPTIONS"}:
            r = anon.request(m, url)
            assert r.status_code == 401, (m, path, r.status_code)
            checked += 1
    assert checked > 80, checked
    print(f"1 {checked} routes/methods without login -> 401; public only {sorted(PUBLIC)} + Webhook")

    # 3) Login-Sperre mit wachsender Dauer, Ereignis-Log, Cookie-Attribute
    anon = TestClient(main.app)
    for _ in range(auth.FAILS_PER_LOCK):
        assert anon.post("/api/auth/login", json={"password": "falsch"}).status_code == 401
    r = anon.post("/api/auth/login", json={"password": "geheim-123"})
    assert r.status_code == 429, r.status_code
    first = auth.locked_for("testclient")
    auth._fails["testclient"][1] = 0          # Sperre "abgelaufen"
    for _ in range(auth.FAILS_PER_LOCK):
        anon.post("/api/auth/login", json={"password": "falsch"})
    assert auth.locked_for("testclient") > first + 30, "second block not longer"
    auth.clear_failures("testclient")
    r = anon.post("/api/auth/login", json={"password": "geheim-123"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and f"max-age={30 * 86400}" in cookie, cookie
    from app import events
    assert any("gesperrt" in e["message"] for e in events.recent(50)), "block not in the event log"
    print("3 block after 5 failed attempts, repetition longer; cookie HttpOnly + SameSite=Strict + 30 days")

    tok = auth.create_session("pytest", "127.0.0.1")
    client.cookies.set(auth.COOKIE, tok)

    # 8) Sessions: list, signing out individually, expiry after 30 days without use
    other = auth.create_session("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/605.1", "192.168.1.20")
    lst = client.get("/api/auth/sessions").json()["sessions"]
    assert lst[0]["current"] and any(s["device"] == "Safari · iPhone" and s["ip"] == "192.168.1.20" for s in lst), lst
    sid = next(s["id"] for s in lst if s["ip"] == "192.168.1.20")
    assert client.delete(f"/api/auth/sessions/{sid}").status_code == 200 and not auth.check_session(other)
    assert client.delete("/api/auth/sessions/kurz").status_code == 404
    old = auth.create_session("alt", "1.2.3.4")
    data = auth._load()
    data["sessions"][auth._hash_token(old)]["last_seen"] = time.time() - 31 * 86400
    auth._save(data)
    assert not auth.check_session(old) and all(s["ip"] != "1.2.3.4" for s in auth.list_sessions(tok))
    print("8 session list with device/IP, signing out individually, expiry after 30 days without use")

finish("ACCESS: all checks OK")
