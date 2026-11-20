"""ComfyOS – security: springboard. Can ComfyOS be used to address the home network from inside – through the
flow node "HTTP" or through the webhook, the only flow endpoint without login?

Attacks:
  A5  webhook: token cannot be guessed, no jump to other flows
  C4  flow HTTP as a springboard into the home network (SSRF): notations, schemes, DNS tricks, redirects
Basic protection:
  6   flow HTTP connects to exactly the checked address
  7   webhook: the same response for unknown and wrong tokens, rate limit

    python tests/security/test_ssrf.py [path-to-comfyos]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.attack import *  # noqa: E402,F401,F403 – app, safeguard and helpers of the security tests

with TestClient(main.app, raise_server_exceptions=False) as client:
    raw = raw_for(client)
    tok, own = login(client)
    anon = TestClient(main.app)          # a second visitor without a session

    # A5) Webhook (the only open flow endpoint): token cannot be guessed, no jump to other flows
    hook = flows_store.create("Hook", [{"id": "w", "type": "webhook", "x": 0, "y": 0, "config": {}}], [], enabled=True)
    off = flows_store.create("Aus", [{"id": "w", "type": "webhook", "x": 0, "y": 0, "config": {}}], [], enabled=False)
    token = next(n for n in flows_store.get(hook["id"])["nodes"] if n["type"] == "webhook")["config"]["token"]
    off_token = next(n for n in flows_store.get(off["id"])["nodes"] if n["type"] == "webhook")["config"]["token"]
    fe.engine.reload()
    check(len(token) >= 24 and token != off_token, "A5 webhook token too short or assigned several times")
    security._hits.clear()
    check(raw("POST", "/api/flows/hook/" + token)["status"] == 200, "A5 a valid webhook fails")
    for t in (off_token, token[:-1], token + "/", token.upper(), hook["id"], "w", "%2e%2e", token + "%00", "*", ".*"):
        security._hits.clear()
        r = raw("POST", "/api/flows/hook/" + t)
        check(r["status"] in (307, 400, 404, 405), f"A5 webhook with {t[:12]!r}: {r['status']}")
    security._hits.clear()
    for m in ("GET", "PUT", "DELETE"):
        check(raw(m, "/api/flows/hook/" + token)["status"] in (401, 405), f"A5 Webhook per {m}")
    r = raw("POST", "/api/flows/hook/" + token, body=b"x" * (security.MAX_BODY + 1))
    check(r["status"] == 413, f"A5 the webhook accepts oversized requests: {r['status']}")
    security._hits.clear()
    print("A5 webhook: only the exact token of an enabled flow; other methods and huge requests rejected")

    # C4) Flow-HTTP als Sprungbrett ins Heimnetz (SSRF)
    for url in ("http://127.0.0.1/", "http://localhost/", "http://LOCALHOST./", "http://2130706433/", "http://0x7f000001/", "http://0/",
                "http://0.0.0.0/", "http://[::1]/", "http://[::]/", "http://[::ffff:127.0.0.1]/", "http://[::ffff:7f00:1]/", "http://[fe80::1]/",
                "http://[fd00::1]/", "http://169.254.169.254/latest/meta-data/", "http://100.64.0.1/", "http://10.0.0.1/", "http://172.16.0.1/",
                "http://192.168.178.1/", "http://user:pw@192.168.1.1/", "http://192.168.1.1:8080/x", "http://router/", "http://nas.local/",
                "http://fritz.box/", "http://comfyos.home.arpa/", "http://224.0.0.1/", "http://255.255.255.255/"):
        try:
            fe.check_url(url, False)
            findings.append(f"C4 Heimnetz-Ziel nicht gesperrt: {url}")
        except fe.FlowError:
            pass
    for url in ("file:///etc/passwd", "gopher://192.168.1.1/", "ftp://192.168.1.1/", "//192.168.1.1/", "javascript:alert(1)", "", "http://",
                "data:text/plain,x", "\\\\192.168.1.1\\share"):
        try:
            fe.check_url(url, True)
            findings.append(f"C4 fremdes Schema angenommen: {url}")
        except fe.FlowError:
            pass
    seen_inner: list[str] = []

    async def ssrf():
        dns = {"127.1": ["127.0.0.1"], "0x7f.1": ["127.0.0.1"], "017700000001": ["127.0.0.1"], "rebind.example.com": ["192.168.1.1"],
               "v6.example.com": ["fd00::1"], "mapped.example.com": ["::ffff:10.0.0.1"], "mix.example.com": ["93.184.216.34", "169.254.169.254"],
               "leer.example.com": [], "ok.example.com": ["93.184.216.34"]}

        async def fake_resolve(host, port):
            if host not in dns:
                raise OSError("unbekannt")
            return list(dns[host])
        egress.resolve = fake_resolve

        def inner(req):
            seen_inner.append(str(req.url))
            if req.url.path == "/weiter":
                return httpx.Response(302, headers={"location": "http://192.168.1.1/admin"})
            return httpx.Response(200)
        tr = egress.GuardedTransport(False, inner=httpx.MockTransport(inner))
        async with httpx.AsyncClient(transport=tr, follow_redirects=True) as c:
            for host in ("127.1", "0x7f.1", "rebind.example.com", "v6.example.com", "mapped.example.com", "mix.example.com", "leer.example.com"):
                try:
                    await c.get(f"http://{host}/x")
                    findings.append(f"C4 Name zeigt ins Heimnetz, nicht gesperrt: {host}")
                except (egress.EgressBlocked, httpx.ConnectError):
                    pass
            try:
                await c.get("http://ok.example.com/weiter")       # Weiterleitung ins Heimnetz
                findings.append("C4 Weiterleitung ins Heimnetz wird verfolgt")
            except egress.EgressBlocked:
                pass
        async with fe.http_client(False) as c:
            check(c.follow_redirects is False, "C4 flow HTTP follows redirects")
    asyncio.run(ssrf())
    check(not any("192.168." in u or "127.0.0.1" in u or "169.254" in u or "fd00" in u for u in seen_inner), f"C4 a home network target was reached: {seen_inner}")
    print("C4 flow HTTP: 26 home network notations, foreign schemes, DNS tricks and redirects into the home network blocked")

    # ============================================================ Grundschutz (bis 0.19 tests/test_security.py)

    # 6) SSRF: flow HTTP blocks the home network through names/DNS too and connects to exactly the checked IP
    for url in ("http://nas/x", "http://192.168.1.5/", "http://100.101.102.103/", "http://[::ffff:10.0.0.1]/",
                "http://router.fritz.box/", "http://localhost:8123/"):
        try:
            fe.check_url(url, False)
            raise AssertionError("not blocked:" + url)
        except fe.FlowError:
            pass
    assert fe.check_url("http://nas/x", True) == "http://nas/x"
    seen = []

    def inner_handler(req):
        seen.append((str(req.url), req.headers.get("host"), req.extensions.get("sni_hostname")))
        return httpx.Response(200)

    async def ssrf():
        dns = {"rebind.example.com": ["192.168.1.10"], "ok.example.com": ["93.184.216.34", "2606:2800::1"],
               "mixed.example.com": ["93.184.216.34", "10.0.0.1"]}

        async def fake_resolve(host, port):
            return list(dns[host])
        egress.resolve = fake_resolve
        inner = httpx.MockTransport(inner_handler)
        async with httpx.AsyncClient(transport=egress.GuardedTransport(False, inner=inner)) as c:
            for host in ("rebind.example.com", "mixed.example.com"):
                try:
                    await c.get(f"https://{host}/x")
                    raise AssertionError("DNS into the home network not blocked:" + host)
                except egress.EgressBlocked:
                    pass
            r = await c.post("https://ok.example.com/hook?a=1", content=b"hi")
            assert r.status_code == 200
        assert seen == [("https://93.184.216.34/hook?a=1", "ok.example.com", "ok.example.com")], seen
        async with httpx.AsyncClient(transport=egress.GuardedTransport(True, inner=inner)) as c:
            assert (await c.get("http://192.168.1.10/")).status_code == 200
    asyncio.run(ssrf())
    print("6 flow HTTP: home network/CGNAT/IPv4-mapped/names blocked, DNS rebinding blocked, connection to the checked IP with SNI")

    # 7) Webhook: unknown and wrong token -> identical 404
    f = flows_store.create("Hook", [{"id": "w", "type": "webhook", "x": 0, "y": 0, "config": {}}], [], enabled=True)
    real = next(n for n in flows_store.get(f["id"])["nodes"] if n["type"] == "webhook")["config"]["token"]
    fe.engine.reload()
    r1, r2 = anon.post("/api/flows/hook/gibtsnicht"), anon.post("/api/flows/hook/" + real[:-1] + ("x" if real[-1] != "x" else "y"))
    assert r1.status_code == r2.status_code == 404 and r1.text == r2.text, (r1.status_code, r2.status_code)
    assert anon.post("/api/flows/hook/" + real, headers={"origin": "http://evil.example.com"}).status_code != 403, \
        "the webhook must not fail at the origin check"
    codes = [anon.post("/api/flows/hook/gibtsnicht").status_code for _ in range(35)]
    assert 429 in codes and codes.count(404) <= 30, codes
    print("7 webhook: unknown/wrong token equally 404; webhook without an origin check; rate limit 429")

finish("SPRINGBOARD: all checks OK")
