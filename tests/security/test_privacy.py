"""ComfyOS – privacy (S1, S3): no connection to the outside except registered and needed, no secrets in
logs/exports, no foreign resources in the browser, clean secret scan.

    python tests/security/test_privacy.py [path-to-comfyos]
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("priv")
sys.path.insert(0, str(ROOT / "tools"))

import httpx  # noqa: E402

from app import auth, egress, events, floorplan_store, logstore, main, weather  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import check_secrets  # noqa: E402

APP = ROOT / "backend" / "app"
URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# XML namespaces, not connections (SVG; SOAP and Dublin Core in the UPnP documents of the Sonos plugin)
NAMESPACES = {"www.w3.org", "schemas.xmlsoap.org", "purl.org"}

# 1) Every external host in the code is in the egress register; raw HTTP clients only in known places
unregistered = []
PLUGINS = ROOT / "public" / "plugins"     # built-in plugins and the template count too
# node_modules only exists on the development machine (for building the Matter helper program) – foreign code, never shipped
PLUGIN_FILES = [f for f in list(PLUGINS.rglob("*.py")) + list(PLUGINS.rglob("*.js")) if "node_modules" not in f.parts]
for f in (list(APP.rglob("*.py")) + list((APP / "static").rglob("*.js")) + [APP / "static" / "index.html"] + PLUGIN_FILES):
    for host in set(URL_RE.findall(f.read_text(encoding="utf-8"))):
        h = host.lower()
        if h in NAMESPACES or egress.is_local_host(h):
            continue
        if not any(egress.host_allowed(e, h) for e in egress.REGISTRY.values() if e.hosts != ("*",)):
            unregistered.append(f"{f.relative_to(ROOT)}: {h}")
assert not unregistered, unregistered
RAW = re.compile(r"httpx\.(AsyncClient|Client|get|post|request|stream)\(")
raw_users = {str(f.relative_to(APP)).replace("\\", "/") for f in APP.rglob("*.py") if RAW.search(f.read_text(encoding="utf-8"))}
# egress.py builds the register and LAN clients (for plugins too); flow HTTP uses GuardedTransport.
# Plugins never build clients of their own – they get theirs through context.http() (the Home Base too, a plugin since 0.19).
assert raw_users == {"egress.py", "flow_engine.py"}, raw_users
PLUGIN_CODE = [f for f in PLUGIN_FILES if f.suffix == ".py" and "tests" not in f.parts]
plugin_raw = [str(f.relative_to(ROOT)) for f in PLUGIN_CODE if RAW.search(f.read_text(encoding="utf-8"))]
assert not plugin_raw, f"a plugin builds its own HTTP client instead of context.http(): {plugin_raw}"
# No socket of its own either, no own TCP/TLS connection and no library that opens connections itself:
# TCP only exists through context.open_stream() (API revision 2), so that the limits from the manifest apply.
OWN_CONNECTION = re.compile(r"socket\.(socket|create_connection|create_server)\(|asyncio\.(open_connection|start_server|open_unix_connection)\("
                            r"|\.create_connection\(|ssl\.(create_default_context|SSLContext|wrap_socket)\("
                            r"|^\s*(import|from)\s+(urllib\.request|http\.client|smtplib|ftplib|telnetlib|requests|aiohttp|paho|aiomqtt|websockets|soco)\b",
                            re.MULTILINE)
plugin_own = [str(f.relative_to(ROOT)) for f in PLUGIN_CODE if OWN_CONNECTION.search(f.read_text(encoding="utf-8"))]
assert not plugin_own, f"a plugin opens a connection of its own instead of context.http()/context.open_stream(): {plugin_own}"
for probe in ("reader, writer = await asyncio.open_connection(host, 1883)", "import paho.mqtt.client as mqtt", "sock = socket.socket()",
              "from urllib.request import urlopen", "ctx = ssl.create_default_context()"):
    assert OWN_CONNECTION.search(probe), f"the check does not recognise: {probe}"
assert not OWN_CONNECTION.search("name = socket.gethostname()") and not OWN_CONNECTION.search("await self.context.open_stream(KEY, host, port)")
assert "GuardedTransport" in (APP / "flow_engine.py").read_text(encoding="utf-8")
print(f"1 all external hosts in the code are registered ({len(egress.REGISTRY)} entries); raw clients only home network/register")

# 2) Register clients only reach their hosts – not through redirects either
seen: list[str] = []


def handler(req: httpx.Request) -> httpx.Response:
    """Responses of all services ComfyOS may contact; records the target of every request."""
    seen.append(req.url.host)
    if req.url.path == "/redirect":
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})
    if req.url.host == "api.github.com":
        return httpx.Response(200, json={"tag_name": "v0.0.1", "name": "x", "assets": []})
    if "geocoding" in req.url.host:
        return httpx.Response(200, json={"results": [{"latitude": 52.52, "longitude": 13.4, "name": "Berlin",
                                                      "country_code": "DE", "postcodes": ["10115"]}]})
    if req.url.host == "maps.dwd.de":
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
    if req.url.host == "api.open-meteo.com":
        return httpx.Response(200, json={"current": {"temperature_2m": 5.0, "weather_code": 3, "is_day": 1},
                                         "daily": {"temperature_2m_max": [7], "temperature_2m_min": [1]}})
    return httpx.Response(200)


egress._transport = httpx.MockTransport(handler)
egress._sync_transport = httpx.MockTransport(handler)
with egress.sync_client("weather", follow_redirects=True) as c:
    for url in ("https://evil.example.com/x", "https://api.open-meteo.com/redirect"):
        try:
            c.get(url)
            raise AssertionError("not blocked:" + url)
        except egress.EgressBlocked:
            pass
assert "evil.example.com" not in seen, seen
assert egress.last_used().get("weather"), "use not noted"
print("2 the register client blocks foreign hosts and redirects to them; use is noted")

# 3) Fresh installation without optional features: zero connections to the outside
seen.clear()
with TestClient(main.app) as client:
    client.cookies.set(auth.COOKIE, auth.create_session("pytest"))
    for url in ("/", "/api/health", "/api/settings", "/api/status", "/api/overview", "/api/floorplan", "/api/weather",
                "/api/flows", "/api/events", "/api/update", "/api/system/info", "/api/system/egress", "/api/sync/status",
                "/api/network", "/api/hue", "/api/routines", "/api/home", "/api/security", "/api/auth/sessions"):
        r = client.get(url)
        assert r.status_code < 500, (url, r.status_code)
    assert seen == [], f"connection to the outside without an optional feature: {seen}"
    egr = {c["id"]: c for c in client.get("/api/system/egress").json()["connections"]}
    assert set(egr) == set(egress.REGISTRY) and all(c["state"] != "on" for c in egr.values()), egr
    # Matter plugin: Node.js comes from nodejs.org – registered, "on demand", and nothing flows without the push of a button
    node = egr["plugin.matter.runtime"]
    assert node["hosts"] == ["nodejs.org"] and node["state"] == "on_demand" and "IP-Adresse" in node["data"] and "Einrichten" in node["trigger"], node
    # the tile shows short titles (since 0.23.1): the description of a plugin target is split into title and purpose
    assert node["title"] == "Matter: Node.js laden" and node["purpose"] == "Laufzeit des Matter-Beiprogramms", node
    magenta = egr["plugin.qivicon.magenta"]
    assert magenta["title"] == "Home Base: Magenta-Sync" and magenta["purpose"].startswith("Heizroutinen"), magenta
    assert all(len(c["title"]) <= 40 and c["purpose"] and c["data"] and c["trigger"] for c in egr.values()), egr
    assert client.get("/api/plugins/matter/status").json()["runtime"]["installed"] is False and seen == [], seen
    print("3 fresh installation: not a single connection to the internet; the overview shows everything off/on demand")

    # with address + weather: only Open-Meteo, only postcode/town
    plan = floorplan_store.get()
    plan.update(address="Musterstraße 12, 10115 Berlin", weather=True)
    floorplan_store.save(plan)
    assert client.get("/api/weather").json().get("available"), "weather not loaded"
    assert set(seen) == {"geocoding-api.open-meteo.com", "api.open-meteo.com", "maps.dwd.de"}, seen
    egr = {c["id"]: c["state"] for c in client.get("/api/system/egress").json()["connections"]}
    assert egr["weather"] == "on" and egr["weather_warnings"] == "on", egr
    # weather warnings switched off: no connection to the DWD any more
    plan.update(warnings=False)
    floorplan_store.save(plan)
    weather.reset()
    seen.clear()
    assert client.get("/api/weather").json().get("available") and set(seen) == {"api.open-meteo.com"}, seen
    seen.clear()
    assert client.get("/api/update/github").status_code == 200 and set(seen) == {"api.github.com"}, seen
    seen.clear()
    # Button "Einrichten" of the Matter tile: exactly one connection, to nodejs.org. The response of the mock does not have
    # the stored checksum – so nothing is installed.
    setup = client.post("/api/plugins/matter/runtime/install")
    assert setup.status_code == 400 and "Prüfsumme" in setup.text and seen == ["nodejs.org"], (setup.status_code, setup.text[:200], seen)
    assert client.get("/api/plugins/matter/status").json()["runtime"]["installed"] is False
    print("3b with an address only Open-Meteo and DWD (warnings can be switched off); update search only GitHub; Node.js for Matter only at"
          "the push of a button and only with a matching checksum – each only on its trigger")

    # 4) No secrets in logs, events and exports
    jwt = "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 20
    pw = "Sup3r" + "Geheim!"
    events.log("system", f"Test mit Bearer {'t' * 24} und {jwt} sowie ?password={pw}",
               {"password": pw, "access_token": "tok" * 8, "backend_token": "bt" * 10, "passage": "Flur"})
    logstore.append("messages", {"type": "test", "message": "login", "cookie": "comfyos_session=" + "z" * 30})
    raw = "".join(p.read_text(encoding="utf-8") for p in (Path(os.environ["COMFYOS_HOME"]) / "logs").rglob("*.jsonl"))
    for secret in (jwt, pw, "tok" * 8, "bt" * 10, "t" * 24, "z" * 30):
        assert secret not in raw, f"secret in the log: {secret[:6]}…"
    assert '"passage": "Flur"' in raw, "a harmless field was redacted by mistake"
    export = client.get("/api/logs/events/export")
    if export.status_code == 200:
        assert jwt not in export.text and pw not in export.text
    api_texts = "".join(client.get(u).text for u in ("/api/settings", "/api/events", "/api/auth/sessions"))
    assert pw not in api_texts and jwt not in api_texts
    print("4 passwords, tokens, JWT, bearer, cookies redacted in logs/export/API; harmless fields stay")

    # 5) The browser loads nothing from third parties: CSP + no external URLs in the frontend
    csp = client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp and "img-src 'self' data: blob:" in csp, csp
    for f in list((APP / "static").rglob("*.js")) + list((APP / "static").rglob("*.html")) + list((APP / "static").rglob("*.css")):
        hosts = {h for h in URL_RE.findall(f.read_text(encoding="utf-8")) if h not in NAMESPACES}
        assert not hosts, (f.name, hosts)
    print("5 CSP default-src 'self'; the frontend references no external addresses")

# 6) Secret scan: repo clean, and an injected secret is found
hits = check_secrets.scan(ROOT)
assert not hits, hits
bad = tmp / "leak"
bad.mkdir()
(bad / "x.txt").write_text("-----BEGIN " + "PRIVATE KEY-----\nabc\n" + "AB" + "1234567-123456 " + "max@" + "beispiel.de",
                           encoding="utf-8")
kinds = {k for _, k, _ in check_secrets.scan(bad, [bad / "x.txt"])}
assert kinds == {"private key", "gateway id", "e-mail address"}, kinds
print("6 secret scan: repo clean; key, gateway id and e-mail are recognised")

print("ALL PRIVACY TESTS OK")
