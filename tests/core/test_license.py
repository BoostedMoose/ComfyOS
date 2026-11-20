"""ComfyOS – licence client (license.py): owner token, signed licence file, purchased key at the provider,
offline grace period, manipulation, the owner's preview, licence generator. Core promise: the owner is never locked out.

All keys are created at run time; the licence provider is a mock (no network).

    python tests/core/test_license.py [path-to-comfyos]
"""
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("lic")
os.environ["COMFYOS_LICENSE_URL"] = "https://license.example.com/v1"
os.environ.pop("COMFYOS_DEV", None)

import httpx  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from app import settings_store  # noqa: E402
from app import (auth, egress, entitlements as ents, events, floorplan_store, flow_engine as fe, flows_store,  # noqa: E402
                 license as lic, license_keys, main, security, updater)
from fastapi.testclient import TestClient  # noqa: E402

HEX64 = re.compile(r"[0-9a-f]{64}")

# 0) Shipped check values: only public values, well-formed (or empty = source A/B off)
shipped = (ROOT / "backend" / "app" / "license_keys.py").read_text(encoding="utf-8")
assert all(HEX64.fullmatch(v) for v in license_keys.OWNER_TOKEN_HASHES + license_keys.SIGN_PUBS), "license_keys.py malformed"
assert "PRIVATE" not in shipped and "OWNER_SIGN_KEY=" not in shipped and "OWNER_TOKEN=" not in shipped
files = updater.release_files(ROOT)
assert "backend/app/license.py" in files and "backend/app/license_keys.py" in files
assert not [f for f in files if "mint_gift" in f or "build_secrets" in f or f.endswith((".env", ".comfyos-license"))], \
    "licence tool or secrets in the release package"
ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
assert "tools/build_secrets.env" in ignore and "*.comfyos-license" in ignore
print("0 only hash and public key in the code; licence tool and secrets neither in the package nor in the repo")


def new_key() -> tuple[str, str]:
    """New key pair: (private key as hex, public key)."""
    k = Ed25519PrivateKey.generate()
    raw = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()).hex()
    return raw, lic.public_of(raw)


SIGN, PUB = new_key()
FOREIGN, _ = new_key()
TOKEN = "owner-" + str(uuid.uuid4())
license_keys.SIGN_PUBS = (PUB,)
license_keys.OWNER_TOKEN_HASHES = (lic.owner_hash(TOKEN),)
license_keys.REVOKED = frozenset()
lic._raw_hw = lambda: "serial:TESTGERAET-1"
lic._iid = None
IID = lic.hw_id()
assert re.fullmatch(r"[0-9a-f]{16}", IID) and "TESTGERAET" not in IID

GOOD = "COMFYOS-" + "TEST-" + "0001-ABCD"
prov = {"mode": "ok", "plan": "pro", "expires": None}
calls: list[tuple] = []


def provider(req: httpx.Request) -> httpx.Response:
    """Responses of the licence provider: valid, unknown, maintenance, redirect or outage – depending on prov["mode"]."""
    body = json.loads(req.content)
    calls.append((req.url.host, req.url.path, body))
    if prov["mode"] == "down":
        raise httpx.ConnectError("offline", request=req)
    if prov["mode"] == "503":
        return httpx.Response(503, text="wartung")
    if prov["mode"] == "redirect":
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})
    if req.url.path.endswith("/deactivate"):
        return httpx.Response(200, json={"valid": False})
    if body["license_key"] == GOOD and prov["mode"] == "ok":
        return httpx.Response(200, json={"valid": True, "plan": prov["plan"], "expires": prov["expires"]})
    return httpx.Response(404, json={"valid": False, "error": "Schlüssel unbekannt <script>alert(1)</script>"})


egress._transport = httpx.MockTransport(provider)


def N(id_, type_, **config):
    """Baustein eines Flows."""
    return {"id": id_, "type": type_, "x": 0, "y": 0, "config": config}


def plan(rooms=0, windows=0):
    """Floor plan with the wanted number of rooms and windows."""
    return {"width": 30, "height": 20, "walls": [{"id": "w1", "pts": [[1, 1], [29, 1], [29, 19], [1, 19], [1, 1]]}],
            "rooms": [{"id": f"r{i}", "name": f"Raum {i}", "x": 2 + i, "y": 2} for i in range(rooms)],
            "openings": [{"id": f"o{i}", "type": "window", "a": [2 + 2 * i, 1], "b": [3 + 2 * i, 1]} for i in range(windows)],
            "devices": {}, "groups": []}


def wipe():
    """Remove all licence sources (like a fresh installation)."""
    for p in (lic.OWNER_FILE, lic.GIFT_FILE, lic.CACHE_FILE, lic.PREVIEW_FILE):
        p.unlink(missing_ok=True)
    settings_store.delete_secret(lic.KEY_SECRET)
    return lic.apply()


with TestClient(main.app) as client:
    auth.set_password("comfyos-test")
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def code(r, want=200):
        assert r.status_code == want, (r.status_code, r.text[:300])
        return r.json()

    def post(text):
        security._hits.clear()                     # Rate-Limit der Route (eigener Test in Abschnitt 4)
        return client.post("/api/license", json={"license": text})

    def redeem(text, want=200):
        return code(post(text), want)

    def state():
        return code(client.get("/api/license"))

    # 1) Fresh installation: Basic, no source, no connection to the outside
    st = state()
    assert st["plan"] == "basic" and st["source"] is None and st["sources"] == [] and st["preview"] is None, st
    assert st["instance_id"] == IID and st["provider"] == "license.example.com" and st["enforce"] is False
    assert ents.current_plan() == "basic" and calls == []
    egr = {c["id"]: c for c in code(client.get("/api/system/egress"))["connections"]}
    assert egr["license_provider"]["state"] == "on_demand" and egr["license_provider"]["hosts"] == ["license.example.com"]
    print("1 fresh installation: Basic, no licence source, no call to the provider")

    # 2) Source A – owner token: local, without network, without expiry; the token shows up nowhere again
    st = redeem(json.dumps({"type": "owner", "token": TOKEN}))
    assert st["plan"] == "owner" and st["source"] == "owner" and st["expires"] is None and st["can_preview"], st
    assert ents.current_plan() == "owner" and ents.granted() == frozenset(ents.FEATURES) and calls == []
    assert json.loads(lic.OWNER_FILE.read_text(encoding="utf-8")) == {"type": "owner", "token": TOKEN}
    lic.OWNER_FILE.unlink()
    assert lic.apply()["plan"] == "basic"
    assert redeem(TOKEN)["plan"] == "owner"                                   # the token alone works too
    leak = client.get("/api/license").text + client.get("/api/events").text + client.get("/api/entitlements").text
    assert TOKEN not in leak, "owner token in an API response"
    lic._write_json(lic.OWNER_FILE, {"type": "owner", "token": "owner-" + str(uuid.uuid4())})   # token of another version
    st = lic.apply()
    assert st["plan"] == "basic" and st["sources"][0]["active"] is False, st
    assert redeem(json.dumps({"type": "owner", "token": "x" * 40}), 400)["detail"]
    wipe()
    print("2 owner token: owner without network and expiry; foreign token = Basic; token never in responses or event log")

    # 3) Source B – signed licence file: offline, file and short form, every manipulation is noticed
    gift = lic.mint_gift(SIGN, "pro", "Anna  Beispiel", None, None)
    assert gift["name"] == "Anna Beispiel" and lic.verify_gift(gift)[0]["plan"] == "pro"
    st = redeem(json.dumps(gift))
    assert st["plan"] == "pro" and st["source"] == "gift" and st["name"] == "Anna Beispiel" and not st["can_preview"], st
    wipe()
    short = lic.gift_key(gift)
    assert short.startswith("COS1.") and "\n" not in short and lic.parse_gift(short) == gift
    assert redeem(short)["plan"] == "pro"
    wipe()
    bad = {
        "plan raised": {**gift, "plan": "owner"},
        "expiry removed": {**lic.mint_gift(SIGN, "pro", "", None, 30), "exp": None},
        "signature missing": {k: v for k, v in gift.items() if k != "sig"},
        "signature empty": {**gift, "sig": ""},
        "signature zeroed": {**gift, "sig": "00" * 64},
        "foreign key": lic.mint_gift(FOREIGN, "owner", "Angreifer"),
        "Zusatzfeld": {**gift, "extra": 1},
        "other device": lic.mint_gift(SIGN, "pro", "", "0123456789abcdef"),
        "abgelaufen": lic.mint_gift(SIGN, "pro", "", None, 1, now=time.time() - 3 * 86400),
        "Basic-Lizenz": {**gift, "plan": "basic"},
        "wrong version": {**gift, "v": 2},
    }
    for name, doc in bad.items():
        for text in (json.dumps(doc), lic.gift_key(doc) if doc.get("sig") else json.dumps(doc)):
            r = post(text)
            assert r.status_code == 400 and not lic.GIFT_FILE.exists(), (name, r.status_code, r.text)
    for junk in ("{", "{}", "[]", "COS1.x.y", "COS1." + "A" * 4000 + ".B", '{"sig": {"a": 1}}', "null"):
        assert post(junk).status_code == 400, junk
    assert calls == [], f"junk reached the provider: {calls}"
    assert state()["plan"] == "basic"
    license_keys.REVOKED = frozenset({gift["id"]})
    assert "zurückgezogen" in redeem(json.dumps(gift), 400)["detail"]
    license_keys.REVOKED = frozenset()
    mine = lic.mint_gift(SIGN, "owner", "Mein Pi", IID, 365)
    st = redeem(json.dumps(mine))
    assert st["plan"] == "owner" and st["expires"] == mine["exp"] and st["can_preview"], st
    assert not st["issuer"]["available"], "a gifted owner licence must not issue licences"
    stored = json.loads(lic.GIFT_FILE.read_text(encoding="utf-8"))
    lic._write_json(lic.GIFT_FILE, {**stored, "exp": stored["exp"] + 86400})   # file changed on the device
    st = lic.apply()
    assert st["plan"] == "basic" and "Signatur" in st["sources"][0]["detail"], st
    lic.GIFT_FILE.write_text("{kaputt", encoding="utf-8")
    assert lic.apply()["plan"] == "basic"
    assert calls == [], f"the licence file contacted the provider: {calls}"
    wipe()
    print(f"3 licence file + short form offline; {len(bad)} manipulations, junk, revocation and foreign device rejected")

    # 4) Source C – purchased key: only key + anonymous id go out, never more than Pro
    for junk in ("kurz", "mit leerzeichen drin", "ä" * 20, "-anfang-mit-strich"):
        redeem(junk, 400)
    for typo in ("owner-" + str(uuid.uuid4()), str(uuid.uuid4()), TOKEN[:-1] + ("0" if TOKEN[-1] != "0" else "1")):
        assert "Owner-Token" in redeem(typo, 400)["detail"]      # a mistyped owner token stays on the device
    assert calls == [], "an invalid key or a mistyped owner token reached the provider"
    security._hits.clear()
    codes = [client.post("/api/license", json={"license": "kurz"}).status_code for _ in range(12)]
    assert codes[:10] == [400] * 10 and codes[10:] == [429, 429], codes   # redeeming is throttled
    assert "unbekannt" in redeem("FALSCHER-SCHLUESSEL-1", 400)["detail"] and "<" not in redeem("FALSCHER-SCHLUESSEL-1", 400)["detail"]
    prov["mode"] = "down"
    redeem(GOOD, 502)
    prov["mode"] = "redirect"
    redeem(GOOD, 502)
    assert all(c[0] == "license.example.com" for c in calls), calls
    prov.update(mode="ok", plan="owner")
    redeem(GOOD, 400)                                                # a provider can never grant Owner
    assert state()["plan"] == "basic" and not lic.CACHE_FILE.exists() and settings_store.get_secret(lic.KEY_SECRET) is None
    prov["plan"] = "pro"
    calls.clear()
    st = redeem(GOOD)
    assert st["plan"] == "pro" and st["source"] == "key" and st["sources"][0]["key_hint"] == "…ABCD", st
    assert calls == [("license.example.com", "/v1/activate", {"license_key": GOOD, "instance_id": IID})], calls
    leak = client.get("/api/license").text + client.get("/api/events").text + lic.CACHE_FILE.read_text(encoding="utf-8")
    assert GOOD not in leak, "licence key in a response, the event log or the cache"
    egr = {c["id"]: c for c in code(client.get("/api/system/egress"))["connections"]}
    assert egr["license_provider"]["state"] == "on" and egr["license_provider"]["last_used"]
    print("4 key: the provider only sees key + id; rejection/outage/redirect change nothing; never Owner")

    # 5) Offline grace period 14 days; setting the clock back does not help; a modified cache does not count
    key, cache = lic.load_cache()
    now = time.time()
    prov["mode"] = "down"
    assert code(client.post("/api/license/check"))["plan"] == "pro"   # Ausfall: Stand gilt weiter
    lic.save_cache(key, {**cache, "validated_at": now - 13 * 86400, "last_seen": now})
    assert lic.apply()["plan"] == "pro"
    lic.save_cache(key, {**cache, "validated_at": now - 15 * 86400, "last_seen": now})
    st = lic.apply()
    assert st["plan"] == "basic" and "14 Tage" in st["sources"][0]["detail"], st
    lic.save_cache(key, {**cache, "validated_at": now - 86400, "last_seen": now + 20 * 86400})
    assert lic.apply()["plan"] == "basic", "setting the clock back extends the period"
    lic.save_cache(key, {**cache, "validated_at": now - 15 * 86400, "last_seen": now})
    prov["mode"] = "ok"
    lic._last_attempt = 0
    asyncio.run(lic.tick())                                           # online again: the background check brings it back
    assert lic.status()["plan"] == "pro" and ents.current_plan() == "pro"
    good_cache = lic.CACHE_FILE.read_text(encoding="utf-8")
    for change in ({"plan": "owner"}, {"validated_at": now + 10 ** 9}, {"status": "active", "expires": None, "mac": "0" * 64}):
        lic.CACHE_FILE.write_text(json.dumps({**json.loads(good_cache), **change}), encoding="utf-8")
        assert lic.apply()["plan"] == "basic", change
    lic.CACHE_FILE.write_text(good_cache, encoding="utf-8")
    assert lic.apply()["plan"] == "pro"
    lic._iid = "f" * 16                                               # cache copied to another device
    assert lic.apply()["plan"] == "basic"
    lic._iid = IID
    assert lic.apply()["plan"] == "pro"
    lic.save_cache(key, {**cache, "expires": now - 60})
    assert lic.apply()["plan"] == "basic"
    lic.save_cache(key, cache)
    prov["mode"] = "reject"
    assert code(client.post("/api/license/check"))["plan"] == "basic"  # the provider revokes the key
    assert any("nicht mehr gültig" in e["message"] for e in events.recent(30))
    prov["mode"] = "ok"
    assert code(client.post("/api/license/check"))["plan"] == "pro"
    calls.clear()
    st = code(client.delete("/api/license"))
    assert st["plan"] == "basic" and [c[1] for c in calls] == ["/v1/deactivate"]
    assert not lic.CACHE_FILE.exists() and settings_store.get_secret(lic.KEY_SECRET) is None
    print("5 offline grace period 14 days, clock trick and modified/copied cache without effect; revocation = Basic; removing cleans up")

    # 6) The highest valid plan applies; removing leaves the owner file in place
    redeem(GOOD)
    redeem(json.dumps(lic.mint_gift(SIGN, "pro", "Zweitlizenz")))
    st = redeem(TOKEN)
    assert st["plan"] == "owner" and {s["source"] for s in st["sources"]} == {"owner", "gift", "key"}, st
    st = code(client.delete("/api/license"))
    assert st["plan"] == "owner" and lic.OWNER_FILE.exists() and [s["source"] for s in st["sources"]] == ["owner"], st
    print("6 several sources: the highest plan applies; removing only affects file and key, never the owner file")

    # 7) Owner nie aussperren: kaputte andere Quellen, toter Anbieter, Dev-Modus – Owner bleibt Owner
    redeem(GOOD)
    lic.GIFT_FILE.write_text("\x00\xff kaputt", encoding="latin-1")
    lic.CACHE_FILE.write_text("[1, 2", encoding="utf-8")
    lic.PREVIEW_FILE.write_text("kein json", encoding="utf-8")
    lic.ISSUED_FILE.write_text("{", encoding="utf-8")
    prov["mode"] = "down"
    lic._last_attempt = 0
    asyncio.run(lic.tick())
    asyncio.run(lic.validate())
    st = state()
    assert st["plan"] == "owner" and st["source"] == "owner" and ents.current_plan() == "owner", st
    orig_gift = lic._source_gift
    lic._source_gift = lambda now: 1 / 0                              # even a programming error in a source
    assert lic.apply()["plan"] == "owner"
    lic._source_gift = orig_gift
    lic.DEV = True
    assert lic.apply()["plan"] == "owner"
    calls.clear()
    asyncio.run(lic.validate())
    assert calls == [], "dev mode contacted the provider"
    lic.OWNER_FILE.rename(lic.OWNER_FILE.with_suffix(".weg"))
    wipe()
    assert lic.apply()["plan"] == "basic", "dev mode must not grant rights"
    redeem(GOOD, 400)
    lic.DEV = False
    lic.OWNER_FILE.with_suffix(".weg").rename(lic.OWNER_FILE)
    prov["mode"] = "ok"
    assert lic.apply()["plan"] == "owner"
    print("7 the owner stays owner with broken files, a dead provider and a programming error; dev mode grants nothing")

    # 8) Preview: the owner sees Basic/Pro really enforced – data stays, the way back is always open
    floorplan_store.save(plan(rooms=9, windows=2))
    pro_flow = flows_store.create("Pro-Flow", [N("b", "play"), N("p", "toggle")], [], enabled=True)["id"]
    basics = [flows_store.create(f"Basis {i}", [N("b", "play")], [], enabled=True)["id"] for i in range(6)]
    fe.engine.reload()
    before = (json.dumps(flows_store.all_flows(), sort_keys=True), json.dumps(floorplan_store.get(), sort_keys=True))
    assert not fe.engine.paused and ents.ENFORCE is False and not ents.enforcing()
    st = code(client.post("/api/license/preview", json={"plan": "basic", "minutes": 30}))
    assert st["plan"] == "basic" and st["licensed_plan"] == "owner" and st["preview"]["plan"] == "basic" and st["enforce"], st
    assert 29 * 60 < st["preview"]["until"] - time.time() <= 30 * 60 and st["paused_flows"] == 2, st
    assert fe.engine.paused == {pro_flow: "pro", basics[5]: "limit"}, fe.engine.paused
    e = code(client.get("/api/entitlements"))
    assert e["enforce"] is True and e["plan"] == "basic" and not e["features"]["energy_analytics"]["granted"]
    assert client.put("/api/floorplan", json=plan(rooms=9, windows=3)).status_code == 402      # mehr Pro-Inhalt: nein
    assert client.put("/api/floorplan", json=plan(rooms=9, windows=2)).status_code == 200      # Bestand speichern: ja
    assert client.get("/api/power/export").status_code == 402
    assert client.get("/api/backups").status_code == 200                                     # backups are Basic
    assert client.post("/api/flows", json={"name": "x", "nodes": [N("b", "play"), N("p", "toggle")]}).status_code == 402
    assert (json.dumps(flows_store.all_flows(), sort_keys=True), json.dumps(floorplan_store.get(), sort_keys=True)) == before, \
        "the preview changed stored data"
    lic._applied = None                                               # restart: the preview keeps applying until it expires
    assert lic.apply()["preview"]["plan"] == "basic"
    st = code(client.post("/api/license/preview", json={"plan": "pro"}))
    assert st["plan"] == "pro" and not fe.engine.paused and client.get("/api/power/export").status_code == 200
    st = code(client.delete("/api/license/preview"))
    assert st["plan"] == "owner" and st["preview"] is None and st["enforce"] is False and not ents.enforcing()
    assert not lic.PREVIEW_FILE.exists() and not fe.engine.paused
    code(client.post("/api/license/preview", json={"plan": "basic", "minutes": 5}))
    assert ents.current_plan() == "basic" and ents.enforcing()
    lic._write_json(lic.PREVIEW_FILE, {"plan": "basic", "until": time.time() - 1})   # Zeit um
    asyncio.run(lic.tick())                                           # the background check restores Owner
    assert ents.current_plan() == "owner" and not ents.enforcing() and not fe.engine.paused
    lic._write_json(lic.PREVIEW_FILE, {"plan": "basic", "until": time.time() + 30 * 86400})   # by hand "forever"
    assert lic.apply()["plan"] == "owner", "an overlong preview locks the owner into Basic"
    assert code(client.post("/api/license/preview", json={"plan": "owner"}))["preview"] is None
    code(client.post("/api/license/preview", json={"plan": "gold"}), 400)
    code(client.post("/api/license/preview", json={"plan": "basic", "minutes": 100000}), 422)
    assert (json.dumps(flows_store.all_flows(), sort_keys=True), json.dumps(floorplan_store.get(), sort_keys=True)) == before
    # without an owner licence there is no preview – and a planted file has no effect
    lic.OWNER_FILE.rename(lic.OWNER_FILE.with_suffix(".weg"))
    redeem(json.dumps(lic.mint_gift(SIGN, "pro", "Kunde")))
    code(client.post("/api/license/preview", json={"plan": "basic"}), 403)
    for doc in ({"plan": "owner", "until": time.time() + 3600}, {"plan": "basic", "until": time.time() + 3600}):
        lic._write_json(lic.PREVIEW_FILE, doc)
        st = lic.apply()
        assert st["plan"] == "pro" and st["preview"] is None and not ents.enforcing(), st
    code(client.delete("/api/license/preview"))                       # beenden geht in jedem Plan
    wipe()
    lic.OWNER_FILE.with_suffix(".weg").rename(lic.OWNER_FILE)
    assert lic.apply()["plan"] == "owner"
    print("8 preview: plan enforced, 2 flows paused, data unchanged, ends by itself; only with an owner licence")

    # 9) Licence generator: owner token only, key stored deliberately, every issue with the password
    st = state()
    assert st["issuer"] == {"available": True, "ready": False, "issued": []}, st["issuer"]
    code(client.post("/api/license/issue", json={"plan": "pro", "password": "comfyos-test"}), 400)   # no key
    code(client.put("/api/license/issuer/key", json={"key": SIGN, "password": "falsch"}), 401)
    code(client.put("/api/license/issuer/key", json={"key": FOREIGN, "password": "comfyos-test"}), 400)
    code(client.put("/api/license/issuer/key", json={"key": "zz", "password": "comfyos-test"}), 400)
    assert code(client.put("/api/license/issuer/key", json={"key": SIGN, "password": "comfyos-test"}))["issuer"]["ready"]
    code(client.post("/api/license/issue", json={"plan": "pro", "password": "falsch"}), 401)
    code(client.post("/api/license/issue", json={"plan": "basic", "password": "comfyos-test"}), 400)
    code(client.post("/api/license/issue", json={"plan": "pro", "instance_id": "kurz", "password": "comfyos-test"}), 400)
    out = code(client.post("/api/license/issue", json={"plan": "pro", "name": "Bert", "days": 30, "password": "comfyos-test"}))
    assert lic.verify_gift(out["license"])[0]["name"] == "Bert" and lic.parse_gift(out["key"]) == out["license"]
    assert 29 * 86400 < out["license"]["exp"] - time.time() <= 30 * 86400
    row = state()["issuer"]["issued"][0]
    assert row["id"] == out["license"]["id"] and row["name"] == "Bert" and "sig" not in row, row
    leak = "".join(client.get(u).text for u in ("/api/license", "/api/events", "/api/settings", "/api/entitlements"))
    assert SIGN not in leak and TOKEN not in leak, "signing key or token in an API response"
    auth.clear_failures("testclient")
    for _ in range(auth.FAILS_PER_LOCK):
        client.post("/api/license/issue", json={"plan": "pro", "password": "falsch"})
    code(client.post("/api/license/issue", json={"plan": "pro", "password": "comfyos-test"}), 429)   # block as with the login
    auth.clear_failures("testclient")
    assert not code(client.delete("/api/license/issuer/key"))["issuer"]["ready"]
    code(client.post("/api/license/issue", json={"plan": "pro", "password": "comfyos-test"}), 400)
    # the issued licence works on an installation without the owner token; there is no generator there
    lic.OWNER_FILE.unlink()
    st = redeem(out["key"])
    assert st["plan"] == "pro" and st["issuer"] == {"available": False, "ready": False}, st
    code(client.post("/api/license/issue", json={"plan": "owner", "password": "comfyos-test"}), 403)
    code(client.put("/api/license/issuer/key", json={"key": SIGN, "password": "comfyos-test"}), 403)
    print("9 generator: only with owner token + stored key + password; the licence is valid elsewhere, the key stays secret")

    # 10) Basic: no weather effects, no new own accent colour – one that is already set is kept
    e = code(client.get("/api/entitlements"))
    assert e["plan"] == "pro" and e["features"]["floorplan_weather"]["granted"] and e["features"]["custom_accent"]["granted"], e["plan"]
    assert code(client.put("/api/settings/accent", json={"color": "#123456"}))["accent_color"] == "#123456"
    ents.use_plan("basic", enforce=True)
    try:
        e = code(client.get("/api/entitlements"))
        assert e["enforce"] and not e["features"]["floorplan_weather"]["granted"] and not e["features"]["custom_accent"]["granted"]
        assert "floorplan_weather" not in ents.granted("basic") and "floorplan_weather" in ents.granted("pro")
        r = client.put("/api/settings/accent", json={"color": "#654321"})
        assert r.status_code == 402 and r.json()["detail"]["code"] == "pro_required", r.text[:200]
        assert code(client.get("/api/settings")).get("accent_color") == "#123456", "gesetzte Akzentfarbe ging verloren"
        fp = floorplan_store.get()
        saved = code(client.put("/api/floorplan", json={**fp, "fx": True, "fx_level": "subtle"}))   # display only, no gate
        assert saved["fx"] is True and saved["fx_level"] == "subtle"
        assert code(client.put("/api/floorplan", json={**saved, "fx_level": "kaputt"}))["fx_level"] == "normal"
        assert code(client.put("/api/settings/accent", json={"color": ""}))["accent_color"] is None   # back to the default colour: always
        assert "accent_color" not in code(client.get("/api/settings")), "Zurücksetzen der Akzentfarbe wirkt nicht"
    finally:
        lic.apply()
    print("10 Basic: weather effects not unlocked, new accent colour 402, a set colour stays; the setting fx can still be saved")

print("ALL LICENCE TESTS OK")
