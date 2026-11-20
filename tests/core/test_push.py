"""ComfyOS – push notifications (Web Push for the flow node "Benachrichtigung").

    python tests/core/test_push.py [path-to-comfyos]

Checked without internet: sending goes against a mock transport. Section 6 (real encryption with
pywebpush) only runs if the package is installed – otherwise it is skipped with a hint.
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("push")

import httpx  # noqa: E402

from app import settings_store  # noqa: E402
from app import auth, backup_store, core, egress, events, flows_store, main, push  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def b64(raw: bytes) -> str:
    """URL-safe Base64 without padding."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def browser_keys():
    """Key pair as a browser creates it for a push subscription (only at run time, never in the source code)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"p256dh": b64(pub), "auth": b64(os.urandom(16))}


def sub(host="fcm.googleapis.com", path="/fcm/send/abc", scheme="https"):
    """Subscription of a browser at a push service."""
    return {"endpoint": f"{scheme}://{host}{path}", "keys": browser_keys()}


HAVE_LIB = push.available()
real_available = push.available
push.available = lambda: True            # the procedures do not depend on the installed package; section 6 checks the real one
sent = []                                # (Push-Adresse, Nutzlast)
codes = {}                               # Push-Adresse -> HTTP-Status


def fake_sender(s, payload, pem):
    """Replacement for sending: records the message and answers with the code stored per address."""
    sent.append((s["endpoint"], payload))
    code = codes.get(s["endpoint"], 201)
    if code == "boom":
        raise RuntimeError("Netz weg")
    return code


push.sender = fake_sender

# 1) Egress: own entry, only the push services of the browser makers; not in the backup
e = egress.REGISTRY["push"]
for host, ok_ in (("fcm.googleapis.com", True), ("web.push.apple.com", True), ("updates.push.services.mozilla.com", True),
                  ("db5p.notify.windows.com", True), ("push.apple.com.evil.example.com", False), ("example.com", False),
                  ("192.168.1.10", False), ("localhost", False)):
    assert egress.host_allowed(e, host) is ok_, host
assert "push.json" not in backup_store._DATA_FILES, "push subscriptions do not belong in the backup (the key for them is missing there)"
print("1 egress entry 'push': only Apple/Google/Mozilla/Microsoft; subscriptions and key are not part of the backup")

# 2) Signing up a subscription: only https, only allowed services (no SSRF through a subscription), plausible keys, maximum number
good = sub()
r = push.subscribe(good, "  iPhone   (Safari) ")
assert r["label"] == "iPhone (Safari)" and push.count() == 1
assert push.subscribe(good, "iPhone neu")["id"] == r["id"] and push.count() == 1, "the same device was signed up twice"
for bad in (sub(host="evil.example.com"), sub(scheme="http"), sub(host="192.168.1.10"), sub(host="localhost"),
            {"endpoint": good["endpoint"]}, {"endpoint": good["endpoint"], "keys": {"p256dh": "kurz", "auth": "x"}},
            {"endpoint": good["endpoint"], "keys": {**good["keys"], "auth": "<script>alert(1)</script>"}}, "kein objekt", None):
    try:
        push.subscribe(bad, "x")
        raise AssertionError(f"an invalid subscription was accepted: {str(bad)[:60]}")
    except push.PushError:
        pass
assert push.count() == 1
for i in range(push.MAX_SUBS - 1):
    push.subscribe(sub(path=f"/fcm/send/{i}"), f"Gerät {i}")
try:
    push.subscribe(sub(path="/fcm/send/zuviel"), "x")
    raise AssertionError("more devices than allowed")
except push.PushError:
    pass
for s in push.subscriptions():
    assert set(s) == {"id", "label", "created", "last_ok", "service"} and s["service"] == "fcm.googleapis.com", s
print(f"2 signing up: only https and allowed services, at most {push.MAX_SUBS} devices; the interface sees neither key nor address")

# 3) Key: lives in the secret store, never in push.json; the public key is stable
pub = push.public_key()
raw = base64.urlsafe_b64decode(pub + "=" * (-len(pub) % 4))
assert len(raw) == 65 and raw[0] == 4 and push.public_key() == pub
secret = settings_store.get_secret(push.SECRET)
assert secret and "PRIVATE KEY" in secret
stored = push.PUSH_FILE.read_text(encoding="utf-8")
assert "PRIVATE" not in stored and secret.splitlines()[1] not in stored
print("3 VAPID key in the secret store, public part stable, push.json without a secret")

# 4) Sending: success noted, expired subscriptions (404/410) removed, repeated errors remove the subscription, exceptions abort nothing
import asyncio  # noqa: E402

flows_store.reset()
push.forget()
assert push.count() == 0 and not settings_store.get_secret(push.SECRET)
a, b, c, d = (sub(path=f"/fcm/send/{x}") for x in "abcd")
for x, name in ((a, "A"), (b, "B"), (c, "C"), (d, "D")):
    push.subscribe(x, name)
codes.update({b["endpoint"]: 410, c["endpoint"]: 500, d["endpoint"]: "boom"})
sent.clear()
out = asyncio.run(push.send_all("Flur", "Bewegung erkannt"))
assert out == {"sent": 1, "failed": 3, "removed": 1}, out
assert len(sent) == 4 and json.loads(sent[0][1]) == {"title": "Flur", "body": "Bewegung erkannt", "url": "/#flow"}
left = {s["label"]: s for s in push.subscriptions()}
assert set(left) == {"A", "C", "D"} and left["A"]["last_ok"] > time.time() - 5 and left["C"]["last_ok"] == 0
for _ in range(push.MAX_FAILS - 1):
    asyncio.run(push.send_all("x", "y"))
assert {s["label"] for s in push.subscriptions()} == {"A"}, "dead subscriptions stay forever"
assert asyncio.run(push.send_all("x" * 500, "y" * 5000)) == {"sent": 1, "failed": 0, "removed": 0}
assert len(json.loads(sent[-1][1])["body"]) == 300 and len(json.loads(sent[-1][1])["title"]) == 80
push.sender = lambda *a_: 1 / 0
assert asyncio.run(push.send_all("x", "y")) == {"sent": 0, "failed": 1, "removed": 0}
push.sender = fake_sender
print("4 sending: 410 removes the subscription at once, permanent errors after some attempts; length limited; nothing raises")

# 5) Transport: pywebpush sends through the checked egress client – foreign targets never get out
seen = []


def net(request: httpx.Request) -> httpx.Response:
    """Replacement for the network: records the request to the push service."""
    seen.append((request.method, request.url.host, dict(request.headers), request.content))
    return httpx.Response(201, text="ok")


egress._sync_transport = httpx.MockTransport(net)
resp = push._Session().post("https://web.push.apple.com/abc", data=b"\x01\x02", headers={"TTL": 60, "Content-Encoding": "aes128gcm"})
assert resp.status_code == 201 and resp.text == "ok" and seen[-1][1] == "web.push.apple.com" and seen[-1][2]["ttl"] == "60"
try:
    push._Session().post("https://evil.example.com/abc", data=b"x", headers={})
    raise AssertionError("push was sent to a target that is not allowed")
except egress.EgressBlocked:
    pass
assert egress.last_used().get("push"), "use not noted"
print("5 transport: only through the egress entry 'push', a foreign target is blocked, use noted")

# 6) Real encryption (pywebpush): VAPID header, aes128gcm, nothing in plain text
if HAVE_LIB:
    seen.clear()
    target = sub(host="web.push.apple.com", path="/QGx")
    code = push._send_one(target, json.dumps({"title": "Flur", "body": "Streng geheim"}), push._private_pem())
    assert code == 201, code
    method, host, headers, body = seen[-1]
    assert method == "POST" and host == "web.push.apple.com" and headers["content-encoding"] == "aes128gcm" and headers["ttl"] == str(push.TTL)
    assert headers["authorization"].startswith("vapid t=") and f"k={push.public_key()}" in headers["authorization"]
    assert b"geheim" not in body and b"Flur" not in body and len(body) > 80
    claims = json.loads(base64.urlsafe_b64decode(headers["authorization"].split("t=")[1].split(",")[0].split(".")[1] + "=="))
    assert claims["aud"] == "https://web.push.apple.com" and claims["sub"] == push.CONTACT and claims["exp"] > time.time()
    print("6 pywebpush: VAPID header with the own key, content aes128gcm-encrypted, contact without user data")
else:
    print("6 skipped: pywebpush is not installed in this test environment (pip install -r backend/requirements.txt)")

# 7) API: only signed in; signing up/off, test, state without secrets; the flow node "Benachrichtigung" sends a push
push.forget()
codes.clear()
with TestClient(main.app) as client:
    for method, url in (("GET", "/api/push/status"), ("POST", "/api/push/subscribe"), ("POST", "/api/push/unsubscribe"), ("POST", "/api/push/test")):
        assert client.request(method, url, json={}).status_code == 401, f"{url} reachable without login"
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def ok(r, code=200):
        assert r.status_code == code, (r.status_code, r.text)
        return r.json()

    st = ok(client.get("/api/push/status"))
    assert st["available"] is True and st["subscriptions"] == [] and len(st["public_key"]) > 80
    ok(client.post("/api/push/test"), 409)
    mine = sub(host="web.push.apple.com", path="/meins")
    r = ok(client.post("/api/push/subscribe", json={"subscription": {**mine, "expirationTime": None}, "label": "iPhone"}))
    assert r["label"] == "iPhone" and len(r["subscriptions"]) == 1
    ok(client.post("/api/push/subscribe", json={"subscription": sub(host="evil.example.com"), "label": "x"}), 400)
    ok(client.post("/api/push/subscribe", json={"subscription": {"endpoint": "x"}, "label": "x"}), 422)
    text = json.dumps(ok(client.get("/api/push/status")))
    assert mine["keys"]["auth"] not in text and mine["keys"]["p256dh"] not in text and "/meins" not in text, "the state reveals the subscription"
    sent.clear()
    assert ok(client.post("/api/push/test"))["sent"] == 1 and "Test" in sent[-1][1]
    egr = {c_["id"]: c_ for c_ in ok(client.get("/api/system/egress"))["connections"]}
    assert egr["push"]["state"] == "on" and "1 Gerät" in egr["push"]["state_text"], egr["push"]
    # Flow: Benachrichtigung mit Push
    f = ok(client.post("/api/flows", json={"name": "Klingel", "nodes": [
        {"id": "p", "type": "play", "x": 0, "y": 0, "config": {"label": "Klingel"}},
        {"id": "n", "type": "notify", "x": 0, "y": 0, "config": {"text": "Es hat geklingelt ({trigger})", "toast": False, "push": True}},
        {"id": "q", "type": "notify", "x": 0, "y": 0, "config": {"text": "nur Protokoll", "toast": False, "push": False}}],
        "edges": [{"from": "p", "fromPort": "out", "to": "n", "toPort": "in"}, {"from": "n", "fromPort": "out", "to": "q", "toPort": "in"}]}))
    ok(client.put(f"/api/flows/{f['id']}/enabled", json={"enabled": True}))
    sent.clear()
    ok(client.post(f"/api/flows/{f['id']}/play/p"))
    for _ in range(100):
        if sent:
            break
        time.sleep(0.05)
    assert len(sent) == 1 and json.loads(sent[0][1]) == {"title": "Klingel", "body": "Es hat geklingelt (Klingel)", "url": "/#flow"}, sent
    assert [e_["message"] for e_ in events.recent(2)] == ["nur Protokoll", "Es hat geklingelt (Klingel)"]
    ok(client.post("/api/push/unsubscribe", json={"endpoint": mine["endpoint"]}))
    assert ok(client.get("/api/push/status"))["subscriptions"] == [] and ok(client.get("/api/system/egress"))["connections"]
    # service worker: at the root, JavaScript, without cache logic
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and "addEventListener('push'" in sw.text
    assert "addEventListener('fetch'" not in sw.text and "caches" not in sw.text, "the service worker must not serve the app from a cache"
    # without the package: a clear hint instead of an error
    push.available = lambda: False
    assert ok(client.get("/api/push/status"))["available"] is False
    ok(client.post("/api/push/subscribe", json={"subscription": sub(), "label": "x"}), 503)
    push.available = real_available
print("7 API: only signed in, state without key/address, test, egress display, the flow sends a push, service worker without a cache")
print("\nOK test_push")
