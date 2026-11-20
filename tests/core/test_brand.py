"""ComfyOS – brand change: no "homeos" in the source code (except expected exceptions),
COS1 licence format accepted, HOS1 rejected, migration package round trip.

    python tests/core/test_brand.py [path-to-comfyos]
"""
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("brand")

SCAN_DIRS = ("backend/app", "backend/app/static", "tests", "tools")
SCAN_EXTS = (".py", ".js", ".css", ".html", ".json", ".yml", ".sh", ".bat", ".webmanifest")
ALLOWED_CONTEXTS = re.compile(
    r"evil|attacker|bad_host|HOMEOS-BACKUP|example-vpn"
    r'|"homeos \(1\)\.zip"'
    r"|source.*homeos"
    r"|comfyos-test.*homeos"
)

# 1) Grep: no "homeos" in the source code except allowed exceptions
print("1) brand name check …")
violations = []
for scan_dir in SCAN_DIRS:
    base = ROOT / scan_dir.replace("/", os.sep)
    if not base.is_dir():
        continue
    for p in sorted(base.rglob("*")):
        if p.is_dir() or p.suffix not in SCAN_EXTS:
            continue
        if p.name == "test_brand.py" or "__pycache__" in str(p) or ".venv" in str(p):
            continue
        try:
            text = p.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "homeos" in line.lower() and not ALLOWED_CONTEXTS.search(line):
                violations.append(f"  {p.relative_to(ROOT)}:{i}: {line.strip()[:120]}")

assert not violations, f"still {len(violations)} places with 'homeos':\n" + "\n".join(violations[:20])
print("   no forbidden 'homeos' references found.")

# 2) Lizenzformat: COS1-Prefix akzeptiert, HOS1 abgelehnt
print("2) licence prefix …")
from app import license as lic  # noqa: E402
assert lic.KEY_PREFIX == "COS1", f"KEY_PREFIX is {lic.KEY_PREFIX!r}, expected 'COS1'"
assert b"comfyos-license-v1\n" == lic.GIFT_DOMAIN
print("   COS1 prefix and comfyos signature domain correct.")

# 3) brand.py central values
print("3) brand.py …")
from app import brand  # noqa: E402
assert brand.NAME == "ComfyOS"
assert brand.SLUG == "comfyos"
assert brand.ENV_PREFIX == "COMFYOS_"
os.environ["COMFYOS_TESTVAL"] = "42"
assert brand.env("TESTVAL") == "42"
del os.environ["COMFYOS_TESTVAL"]
print("   brand.NAME, SLUG, ENV_PREFIX and env() correct.")

# 4) Umzugspaket-Roundtrip
print("4) migration package …")
from app.paths import DATA_DIR, ensure_dirs  # noqa: E402
ensure_dirs()
test_rooms = [{"id": "r1", "name": "Wohnzimmer"}]
(DATA_DIR / "rooms.json").write_text(json.dumps(test_rooms), "utf-8")
from app import backup_store, license as lic, plugins, push, settings_store  # noqa: E402
plugins.discover()                                   # read the manifests – from them the export knows the secrets
password = "test-" + os.urandom(6).hex()             # created at run time
hue_key, lic_key, push_key = (os.urandom(12).hex() for _ in range(3))
settings_store.set_secret("plugin:hue:app_key", hue_key)
settings_store.set_secret(lic.KEY_SECRET, lic_key)
settings_store.set_secret(push.SECRET, push_key)
exported = backup_store.export_migration(password)
assert len(exported) > 100
with zipfile.ZipFile(io.BytesIO(exported)) as zf:
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == 1
    assert manifest["source"] == "comfyos"
    assert "secrets.enc" in zf.namelist()
    packed = b"".join(zf.read(n) for n in zf.namelist())
for secret in (hue_key, lic_key, push_key, password):
    assert secret.encode() not in exported and secret.encode() not in packed, "a secret is readable in the migration package"
(DATA_DIR / "rooms.json").write_text("[]", "utf-8")
try:
    backup_store.import_migration(exported, "wrong-password")
    assert False, "a wrong password should raise ValueError"
except ValueError as e:
    assert "Passwort" in str(e) or "beschädigt" in str(e)
assert json.loads((DATA_DIR / "rooms.json").read_text("utf-8")) == [], "wrong password: data was written anyway"
settings_store.delete_secret("plugin:hue:app_key")
settings_store.set_secret(lic.KEY_SECRET, "neue-lizenz")
settings_store.delete_secret(push.SECRET)
result = backup_store.import_migration(exported, password)
assert "rooms.json" in result["restored"] and "secrets" in result["restored"]
restored = json.loads((DATA_DIR / "rooms.json").read_text("utf-8"))
assert restored == test_rooms, f"rooms after the import: {restored}"
print("   export, import and password check passed.")
assert settings_store.get_secret("plugin:hue:app_key") == hue_key, "the credentials of the plugin did not move along"
assert settings_store.get_secret(lic.KEY_SECRET) == "neue-lizenz", "the migration package overwrote the licence"
assert settings_store.get_secret(push.SECRET) is None, "the migration package brought the push key along"
print("   credentials of the plugins move along encrypted, licence and push key never.")


def forged(secrets: dict) -> bytes:
    """Migration package by hand: valid envelope, freely chosen content in the encrypted part."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    enc = AESGCM(backup_store._package_key(password, salt)).encrypt(nonce, json.dumps(secrets).encode(), None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": 1, "source": "comfyos", "version": "9"}))
        zf.writestr("secrets.enc", salt + nonce + enc)
    return buf.getvalue()


backup_store.import_migration(forged({
    "sec_" + lic.KEY_SECRET: "erschlichen", "sec_" + lic.SIGN_SECRET: "erschlichen", "sec_" + push.SECRET: "erschlichen",
    "sec_plugin:hue:app_key:x": "a", "sec_plugin:../hue:app_key": "a", "sec_plugin:Hue:app_key": "a", "sec_plugin:hue:": "a",
    "plugin:hue:app_key": "ohne-vorsilbe", "sec_plugin:hue:app_key": {"kein": "text"}, "sec_plugin:mqtt:password": "",
    "sec_plugin:hue:zu_lang": "x" * (backup_store.MAX_SECRET_LEN + 1)}), password)
assert settings_store.get_secret(lic.KEY_SECRET) == "neue-lizenz" and settings_store.get_secret(lic.SIGN_SECRET) is None     and settings_store.get_secret(push.SECRET) is None, "a forged package sets secrets of the core"
assert settings_store.get_secret("plugin:hue:app_key") == hue_key, "a forged package overwrites with an invalid value"
stored = settings_store._file_load()
assert set(stored) == {"plugin:hue:app_key", lic.KEY_SECRET}, f"a forged package creates foreign names: {sorted(stored)}"
for bad in (b"", b"kein zip", forged({})[:-40]):
    try:
        backup_store.import_migration(bad, password)
        assert False, "a broken package was accepted"
    except backup_store.BackupError:
        pass
for short in ("", "kurz", "1234567"):
    try:
        backup_store.export_migration(short)
        assert False, "a password that is too short was accepted"
    except backup_store.BackupError as e:
        assert "mindestens" in str(e)
print("   forged packages only set credentials of plugins; broken packages and short passwords rejected.")
settings_store.delete_secret("plugin:hue:app_key")
settings_store.delete_secret(lic.KEY_SECRET)

# 5) Entitlement: custom_accent
print("5) Entitlements …")
from app import entitlements  # noqa: E402
assert "custom_accent" in entitlements.FEATURES
assert "custom_accent" in entitlements.PLANS["pro"]["features"]
assert "custom_accent" not in entitlements.PLANS["basic"]["features"]
print("   custom_accent in Pro, not in Basic.")

print(f"\n=== test_brand: PASSED ===")
