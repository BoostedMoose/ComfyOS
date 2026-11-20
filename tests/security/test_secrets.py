"""ComfyOS – security: secrets and rights. Do credentials leave the home network, can a licence be obtained by fraud,
is a secret in a response?

Attacks (C = with a valid session):
  C3  lure credentials outside: bend Home Base and Hue to a foreign server
  C5  obtain a licence by fraud: forgeries, planted files, developer switch
  C6  secrets in responses: every reading route is searched

C6 builds on C5: afterwards owner token and signing key exist, which C6 searches for too.

    python tests/security/test_secrets.py [path-to-comfyos]
"""
import json
import os
import re
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.attack import *  # noqa: E402,F401,F403 – app, safeguard and helpers of the security tests

with TestClient(main.app, raise_server_exceptions=False) as client:
    raw = raw_for(client)
    tok, own = login(client)

    def post_bytes(path, data):
        """Send a ZIP file to a route as the signed-in owner."""
        return raw("POST", path, headers={**own, "content-type": "application/zip"}, body=data)

    # C3) Luring credentials outside: bending Home Base / Hue to a foreign server
    hbp, HBC = homebase_plugin(), "/api/plugins/qivicon/connect"
    settings_store.set_secret("plugin:qivicon:password", SECRETS["hb"])
    hbp.context.settings.set("address", "https://192.168.1.46")
    outbound.clear()
    for url in ("https://evil.example.com", "evil.example.com", "https://93.184.216.34", "https://[2606:4700::1111]", "http://qivicon.evil.example.com",
                "https://qivicon@example.com", "https://192.168.1.46@example.com", "https://192.168.1.46.evil.example.com"):
        r = raw("POST", HBC, headers={**own, "content-type": "application/json"}, body=json.dumps({"base_url": url}).encode())
        check(r["status"] == 400, f"C3 Home Base switched to the foreign server {url!r} ({r['status']})")
    r = raw("POST", HBC, headers={**own, "content-type": "application/json"}, body=json.dumps({"base_url": "https://192.168.1.99"}).encode())
    check(r["status"] == 400 and "erneut" in r["text"], f"C3 the stored password goes to another address without asking ({r['status']})")
    check(outbound == [] and hbp.context.settings.get("address") == "https://192.168.1.46", f"C3 a connection to the outside was opened: {outbound}")
    # file bent by hand, or settings of the plugin from a restored backup
    r = post_bytes("/api/backups/restore", zip_bytes({"settings.json": json.dumps(
        {"plugins": {"settings": {"qivicon": {"address": "https://evil.example.com", "verify_tls": False}}}})}))
    check(r["status"] == 200 and hbp.context.settings.get("address") == "https://evil.example.com", f"test setup: backup not restored ({r['status']})")
    check(hbp.build_connector() is None, "C3 bent settings: a connection to a foreign server is opened")
    check(json.loads(raw("GET", "/api/plugins/qivicon/diagnose", headers=own)["text"]).get("ok") is False and outbound == [], "C3 the diagnosis contacts a foreign server")
    settings_store.set_secret("plugin:hue:app_key", SECRETS["hue"])
    for host in ("93.184.216.34", "evil.example.com", "https://evil.example.com/api", "192.168.1.9.evil.example.com", "[2606:4700::1111]", "8.8.8.8:443"):
        r = raw("POST", "/api/plugins/hue/pair", headers={**own, "content-type": "application/json"}, body=json.dumps({"host": host}).encode())
        check(r["status"] == 400, f"C3 Hue pairing with a foreign server {host!r} ({r['status']})")
    check(outbound == [], f"C3 the Hue key would have left the home network: {outbound}")
    hbp.context.settings.delete("address")
    print("C3 Home Base password and Hue key never go to addresses outside the home network – not through a bent file either")

    # C5) Lizenz erschleichen
    sign_raw = os.urandom(32).hex()
    token_o = "owner-" + os.urandom(16).hex()
    license_keys.SIGN_PUBS = (lic.public_of(sign_raw),)
    license_keys.OWNER_TOKEN_HASHES = (lic.owner_hash(token_o),)
    ents.use_plan("basic")
    lic.apply()
    forged_key = os.urandom(32).hex()
    good = lic.mint_gift(sign_raw, "pro", "Kunde")
    attempts = [json.dumps({"type": "owner", "token": "x" * 36}), json.dumps({"type": "owner"}), json.dumps(lic.mint_gift(forged_key, "owner")),
                json.dumps({**good, "plan": "owner"}), json.dumps({**good, "sig": "0" * 128}), json.dumps({"plan": "owner"}),
                lic.gift_key({**good, "plan": "owner"}), "COS1..", "COS1.e30.AA", json.dumps({**good, "sig": None}),
                json.dumps([good]), json.dumps({"license": good}), lic.owner_hash(token_o), license_keys.SIGN_PUBS[0]]
    for a in attempts:
        security._hits.clear()
        r = raw("POST", "/api/license", headers={**own, "content-type": "application/json"}, body=json.dumps({"license": a}).encode())
        check(r["status"] in (400, 502) and lic.status()["plan"] == "basic", f"C5 licence obtained by fraud with {a[:40]!r} ({r['status']})")
    check(not lic.OWNER_FILE.exists() and not lic.GIFT_FILE.exists(), "C5 a rejected licence was stored")
    for path, body in (("/api/license/preview", {"plan": "owner"}), ("/api/license/preview", {"plan": "basic"}),
                       ("/api/license/issue", {"plan": "owner", "password": PW}), ("/api/license/issuer/key", {"key": sign_raw, "password": PW})):
        m = "PUT" if path.endswith("/key") else "POST"
        r = raw(m, path, headers={**own, "content-type": "application/json"}, body=json.dumps(body).encode())
        check(r["status"] in (200, 403) and lic.status()["plan"] == "basic" and not ents.enforcing(), f"C5 {path} without an owner licence: {r['status']}")
    lic._write_json(lic.PREVIEW_FILE, {"plan": "owner", "until": time.time() + 9999})
    lic._write_json(lic.CACHE_FILE, {"status": "active", "plan": "owner", "validated_at": time.time(), "mac": "0" * 64})
    settings_store.set_secret(lic.KEY_SECRET, "GERATENER-SCHLUESSEL-1")
    check(lic.apply()["plan"] == "basic", "C5 a planted preview/cache file grants rights")
    settings_store.delete_secret(lic.KEY_SECRET)
    lic.CACHE_FILE.unlink(missing_ok=True)
    lic.PREVIEW_FILE.unlink(missing_ok=True)
    lic.DEV = True
    check(lic.apply()["plan"] == "basic", "C5 COMFYOS_DEV grants rights")
    lic.DEV = False
    security._hits.clear()
    r = raw("POST", "/api/license", headers={**own, "content-type": "application/json"}, body=json.dumps({"license": token_o}).encode())
    check(r["status"] == 200 and lic.status()["plan"] == "owner", "C5 a real owner token was rejected")
    leak = raw("GET", "/api/license", headers=own)["text"] + raw("GET", "/api/events", headers=own)["text"]
    check(token_o not in leak and sign_raw not in leak, "C5 owner token in a response")
    check(raw("DELETE", "/api/license", headers=own)["status"] == 200 and lic.OWNER_FILE.exists(), "C5 the owner file was deleted through the API")
    print(f"C5 {len(attempts)} licence forgeries rejected; preview/generator only with the owner token; the owner file cannot be deleted through the API")

    # C6) Secrets must not be in any response
    settings_store.set_secret("plugin:qivicon:telekom_password", SECRETS["tk"])
    settings_store.set_secret(lic.SIGN_SECRET, sign_raw)
    events.log("system", "harmlos")
    secrets_now = list(SECRETS.values()) + [PW, tok, token_o, sign_raw, auth._hash_token(tok), json.loads(auth.AUTH_FILE.read_text())["password"]["hash"]]
    swept = 0
    for r_ in main.routes():
        path, methods = getattr(r_, "path", ""), getattr(r_, "methods", None) or set()
        if "GET" not in methods or not path.startswith("/api/") or path == "/api/stream":
            continue
        resp = raw("GET", re.sub(r"\{[^}]+\}", "events", path), headers=own)
        swept += 1
        blob = resp["text"] + json.dumps(resp["headers"])
        for s in secrets_now:
            check(s not in blob, f"C6 secret in GET {path}")
    settings_store.delete_secret(lic.SIGN_SECRET)
    print(f"C6 {swept} reading routes searched: no password, key, token or hash in responses")

finish("SECRETS: all checks OK")
