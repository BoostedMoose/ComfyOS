"""Licence client: three sources, one result – the effective plan (Basic / Pro / Owner).

    Source A  APP_DIR/license.json          {"type": "owner", "token": "…"}   owner, no network, no expiry
    Source B  APP_DIR/gift.comfyos-license  Ed25519-signed licence            gifts, no network
    Source C  COMFYOS_LICENSE_URL           /activate | /validate | /deactivate   purchased keys

Principles:
* Every source is checked on its own, the highest valid plan applies. A broken file, a
  network error or an expired key only invalidates this one source – never the others.
* Licence errors mean Basic, never a lock-out: nothing is deleted and nothing is switched off here.
* The owner is never locked out: source A needs no network and has no expiry date. No route and
  no other source touches license.json; it can only be removed by hand on the device.
* The code contains no secrets (license_keys.py): of the owner token only the SHA-256 hash, of the
  signing key only the public part.
* A licence provider can at most grant the plans from EXTERNAL_PLANS – never Owner.
* COMFYOS_DEV=1 only switches the online check off (source C never calls the network). It grants no rights.

Preview: with an owner licence the installation can be viewed as Basic or Pro for a limited time
(`set_preview`). The plan is then really enforced; stored data stays untouched because all
gates only reject new things and the flow engine only pauses at run time. The preview ends by itself.

Contract with the licence provider (replaceable, a small service of its own or an adapter in front of one):
    POST <URL>/activate | /validate | /deactivate   {"license_key": "…", "instance_id": "<16 hex>"}
    Response 200  {"valid": true, "plan": "pro", "expires": null | <Unix time>}
    Response 4xx or {"valid": false, "error": "…"} = key rejected; 5xx / network error = offline.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import platform
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import jsonfile
from . import entitlements, events, license_keys
from .brand import env
from .settings_store import APP_DIR, delete_secret, get_secret, set_secret

OWNER_FILE = APP_DIR / "license.json"
GIFT_FILE = APP_DIR / "gift.comfyos-license"
CACHE_FILE = APP_DIR / "license_cache.json"
PREVIEW_FILE = APP_DIR / "license_preview.json"
ISSUED_FILE = APP_DIR / "license_issued.json"
INSTALL_ID_FILE = APP_DIR / "install_id"

KEY_SECRET = "license:key"            # purchased key (settings_store)
SIGN_SECRET = "license:sign_key"      # signing key, only on the owner's installation

LICENSE_URL = env("LICENSE_URL").strip().rstrip("/")
DEV = env("DEV") == "1"
CHECK_SECONDS = 24 * 3600
RETRY_SECONDS = 3600                  # after a network error
GRACE_SECONDS = 14 * 86400
TICK_SECONDS = 60
MAX_FILE = 16 * 1024
MAX_ISSUED = 500
GIFT_DOMAIN = b"comfyos-license-v1\n"
KEY_PREFIX = "COS1"
GIFT_PLANS = tuple(p for p in entitlements.PLANS if p != "basic")
EXTERNAL_PLANS = tuple(p for p in entitlements.PLANS if p not in ("basic", "owner"))
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{7,127}")
_IID_RE = re.compile(r"[0-9a-f]{16}")
_ID_RE = re.compile(r"[0-9a-f]{8,32}")
_UUID_RE = re.compile(r"(?:owner-)?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MAX_PREVIEW_SECONDS = 24 * 3600

REASONS = {
    "format": "Die Lizenz ist beschädigt oder unvollständig.",
    "signature": "Die Signatur der Lizenz ist ungültig.",
    "revoked": "Diese Lizenz wurde zurückgezogen.",
    "other_device": "Diese Lizenz gilt für ein anderes Gerät.",
    "expired": "Diese Lizenz ist abgelaufen.",
}

_lock = threading.RLock()
_iid: str | None = None
_applied: tuple[str, bool] | None = None    # zuletzt gesetzt: (Plan, Testansicht)
_last_attempt = 0.0


class LicenseError(Exception):
    """Licence error with a text for the user and the fitting HTTP status."""
    def __init__(self, message: str, status: int = 400):
        """`status` is the HTTP status the route answers with."""
        super().__init__(message)
        self.status = status


class LicenseOffline(Exception):
    """Licence provider unreachable – the stored state keeps applying (offline grace period)."""


# ---------------------------------------------------------------- Files
def _read_json(path: Path) -> Any:
    """Content of a licence file (any JSON type) or None; oversized files are not read. An unreadable
    file stays as it is: intermediate states are created anew, and ComfyOS never writes the owner file by itself."""
    try:
        if path.stat().st_size > MAX_FILE:
            return None
    except OSError:
        return None
    return jsonfile.read(path, None, expect=None, keep=False)


def _write_json(path: Path, data: Any) -> None:
    """Write a licence file atomically and readable only for the ComfyOS user."""
    jsonfile.write(path, data, private=True, durable=True)


def _unlink(path: Path) -> bool:
    """Delete a file. True = it is gone; False if it did not exist or it did not work."""
    try:
        path.unlink()
        return True
    except OSError:
        return False


def canonical(payload: dict[str, Any]) -> bytes:
    """Unambiguous byte form of an object (sorted keys, no spaces) – the basis for signature and
    checksum."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- Device id
def _raw_hw() -> str | None:
    """Serial number of the Raspberry Pi or machine id (Linux only; None = none found)."""
    if platform.system() != "Linux":
        return None
    for path in ("/sys/firmware/devicetree/base/serial-number", "/proc/device-tree/serial-number"):
        try:
            v = Path(path).read_text(encoding="ascii", errors="ignore").strip("\x00 \n\t")
            if v and set(v) != {"0"}:
                return "serial:" + v
        except OSError:
            pass   # does not exist on this device: next source
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="ascii", errors="ignore").splitlines():
            if line.lower().startswith("serial"):
                v = line.partition(":")[2].strip()
                if v and set(v) != {"0"}:
                    return "serial:" + v
    except OSError:
        pass   # does not exist on this device: next source
    try:
        v = Path("/etc/machine-id").read_text(encoding="ascii", errors="ignore").strip()
        if v:
            return "machine:" + v
    except OSError:
        pass   # does not exist on this device: next source
    return None


def hw_id() -> str:
    """Anonymous device id: 16 hex characters from the SHA-256 of the serial number. The serial number itself
    never leaves the device. Without a serial number (development machine) an id rolled once."""
    global _iid
    if _iid:
        return _iid
    raw = _raw_hw()
    if raw is None:
        try:
            raw = "install:" + INSTALL_ID_FILE.read_text(encoding="ascii").strip()
        except OSError:
            raw = ""
        if len(raw) < 24:
            value = secrets.token_hex(16)
            try:
                APP_DIR.mkdir(parents=True, exist_ok=True)
                INSTALL_ID_FILE.write_text(value, encoding="ascii")
            except OSError:
                pass   # not writable: the id then only holds until the restart
            raw = "install:" + value
    _iid = hashlib.sha256(("comfyos-iid:" + raw).encode("utf-8")).hexdigest()[:16]
    return _iid


# ---------------------------------------------------------------- Quelle A: Owner-Token
def owner_hash(token: str) -> str:
    """Hash of an owner token. The program only contains such hashes, never a token."""
    return hashlib.sha256(("comfyos-owner:" + token.strip()).encode("utf-8")).hexdigest()


def _owner_token_ok(token: Any) -> bool:
    """Does the token match one of the known owner hashes? Compared in constant time against all of them."""
    if not isinstance(token, str) or not 16 <= len(token.strip()) <= 200:
        return False
    digest = owner_hash(token)
    ok = False
    for known in license_keys.OWNER_TOKEN_HASHES:
        ok = hmac.compare_digest(digest, str(known)) or ok
    return ok


def _source_owner() -> dict[str, Any] | None:
    """Licence source "owner file": valid, invalid, or None if there is no file."""
    if not OWNER_FILE.exists():
        return None
    doc = _read_json(OWNER_FILE)
    if isinstance(doc, dict) and doc.get("type") == "owner" and _owner_token_ok(doc.get("token")):
        return {"source": "owner", "active": True, "plan": "owner", "expires": None, "name": "",
                "detail": "Owner-Lizenz auf diesem Gerät – ohne Ablauf, ohne Internet."}
    return {"source": "owner", "active": False, "plan": None, "expires": None, "name": "",
            "detail": "Die Owner-Datei passt nicht zu dieser ComfyOS-Version."}


# ---------------------------------------------------------------- Quelle B: signierte Lizenz
def _sig_ok(payload: dict[str, Any], sig_hex: Any) -> bool:
    """Is the signature of a licence file valid – created with one of the keys whose public part this
    version knows?"""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        sig = bytes.fromhex(sig_hex)
    except Exception:
        return False
    if len(sig) != 64:
        return False
    msg = GIFT_DOMAIN + canonical(payload)
    for pub in license_keys.SIGN_PUBS:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(str(pub))).verify(sig, msg)
            return True
        except Exception:
            continue   # does not match this key: try the next one
    return False


def verify_gift(doc: Any, now: float | None = None) -> tuple[dict[str, Any] | None, str]:
    """Check a signed licence -> (content, "") or (None, reason from REASONS)."""
    if not isinstance(doc, dict) or len(doc) > 16:
        return None, "format"
    payload = {k: v for k, v in doc.items() if k != "sig"}
    plan, exp, iid = payload.get("plan"), payload.get("exp"), payload.get("iid")
    if (payload.get("v") != 1 or plan not in GIFT_PLANS
            or not (isinstance(payload.get("id"), str) and _ID_RE.fullmatch(payload["id"]))
            or not (exp is None or (isinstance(exp, (int, float)) and not isinstance(exp, bool)))
            or not (iid is None or (isinstance(iid, str) and _IID_RE.fullmatch(iid)))
            or not isinstance(payload.get("name", ""), str) or len(payload.get("name", "")) > 80):
        return None, "format"
    if not _sig_ok(payload, doc.get("sig")):
        return None, "signature"
    if payload["id"] in license_keys.REVOKED:
        return None, "revoked"
    if iid is not None and not hmac.compare_digest(iid, hw_id()):
        return None, "other_device"
    if exp is not None and (now or time.time()) > float(exp):
        return None, "expired"
    return payload, ""


def parse_gift(text: str) -> Any:
    """Licence as file content (JSON) or as the short form COS1.<content>.<signature> -> dict (unchecked) or None."""
    text = (text or "").strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return None
    parts = text.split(".")
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        sig = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    except Exception:
        return None
    return {**payload, "sig": sig.hex()} if isinstance(payload, dict) and "sig" not in payload else None


def gift_key(doc: dict[str, Any]) -> str:
    """Short form of a signed licence: one line to copy and send."""
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")   # noqa: E731
    return ".".join((KEY_PREFIX, b64(canonical({k: v for k, v in doc.items() if k != "sig"})),
                     b64(bytes.fromhex(doc["sig"]))))


def _source_gift(now: float) -> dict[str, Any] | None:
    """Licence source "licence file": valid with plan and expiry, invalid with the reason, or None if there is no
    file."""
    if not GIFT_FILE.exists():
        return None
    payload, why = verify_gift(_read_json(GIFT_FILE), now)
    if payload is None:
        return {"source": "gift", "active": False, "plan": None, "expires": None, "name": "", "detail": REASONS[why]}
    return {"source": "gift", "active": True, "plan": payload["plan"], "expires": payload.get("exp"),
            "name": payload.get("name") or "", "id": payload["id"],
            "detail": "Lizenzdatei – funktioniert ohne Internet."}


# ---------------------------------------------------------------- Quelle C: Lizenzanbieter
def provider_host() -> str | None:
    """Host name of the licence provider, or None if none is configured (only HTTPS counts)."""
    u = urlsplit(LICENSE_URL)
    return u.hostname if u.scheme == "https" and u.hostname else None


def _cache_mac(key: str, cache: dict[str, Any]) -> str:
    """Checksum over the stored licence state, bound to key and device – a copied or hand-edited
    state is noticed that way."""
    secret = hashlib.sha256(("comfyos-cache:" + key + "|" + hw_id()).encode("utf-8")).digest()
    return hmac.new(secret, canonical({k: v for k, v in cache.items() if k != "mac"}), hashlib.sha256).hexdigest()


def load_cache() -> tuple[str | None, dict[str, Any] | None]:
    """(key, checked cache). A modified or foreign cache does not count."""
    try:
        key = get_secret(KEY_SECRET)
    except Exception:
        key = None
    cache = _read_json(CACHE_FILE)
    if not key or not isinstance(cache, dict):
        return key or None, None
    if not hmac.compare_digest(str(cache.get("mac") or ""), _cache_mac(key, cache)):
        return key, None
    return key, cache


def save_cache(key: str, cache: dict[str, Any]) -> None:
    """Save the licence state of the purchased key with its checksum."""
    cache = {k: v for k, v in cache.items() if k != "mac"}
    cache["mac"] = _cache_mac(key, cache)
    with _lock:
        _write_json(CACHE_FILE, cache)


def _source_key(now: float) -> dict[str, Any] | None:
    """Licence source "purchased key" from the stored state: valid, rejected, expired, not checked for too
    long – or None if no key is redeemed."""
    key, cache = load_cache()
    if not key:
        return None
    base = {"source": "key", "plan": None, "expires": None, "name": "", "key_hint": "…" + key[-4:]}
    if cache is None:
        return {**base, "active": False, "detail": "Der gespeicherte Lizenzstand ist unlesbar – bitte den Schlüssel erneut einlösen."}
    plan, exp = cache.get("plan"), cache.get("expires")
    base.update(validated_at=cache.get("validated_at"))
    if cache.get("status") != "active" or plan not in EXTERNAL_PLANS:
        return {**base, "active": False, "detail": "Der Lizenzanbieter hat diesen Schlüssel abgelehnt."}
    if isinstance(exp, (int, float)) and now > exp:
        return {**base, "active": False, "expires": exp, "detail": "Diese Lizenz ist abgelaufen."}
    try:
        validated = float(cache.get("validated_at") or 0)
        seen = float(cache.get("last_seen") or 0)
    except (TypeError, ValueError):
        validated = seen = 0.0
    grace_until = validated + GRACE_SECONDS
    if max(now, seen) > grace_until:   # setting the clock back does not extend the period
        return {**base, "active": False, "grace_until": grace_until,
                "detail": "Die Lizenz konnte länger als 14 Tage nicht geprüft werden. Sobald ComfyOS wieder online ist, gilt sie wieder."}
    return {**base, "active": True, "plan": plan, "expires": exp if isinstance(exp, (int, float)) else None,
            "grace_until": grace_until, "detail": "Gekaufter Schlüssel – wird etwa alle 24 Stunden geprüft."}


async def _call(action: str, key: str) -> dict[str, Any]:
    """Request to the licence provider. LicenseOffline on a network/server error."""
    import httpx

    from . import egress
    if provider_host() is None:
        raise LicenseError("Für diese Installation ist kein Lizenzanbieter eingerichtet.")
    if DEV:
        raise LicenseError("Im Entwicklungsmodus (COMFYOS_DEV=1) werden Lizenzen nicht online geprüft.")
    try:
        async with egress.client("license_provider", timeout=15, follow_redirects=False) as c:
            r = await c.post(f"{LICENSE_URL}/{action}", json={"license_key": key, "instance_id": hw_id()})
    except httpx.HTTPError as exc:
        raise LicenseOffline(type(exc).__name__)
    if r.status_code >= 500 or 300 <= r.status_code < 400:
        raise LicenseOffline(f"HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise LicenseOffline("Antwort unlesbar")
    if not isinstance(data, dict):
        raise LicenseOffline("Antwort unlesbar")
    return {**data, "valid": data.get("valid") is True and r.status_code == 200}


def _granted(data: dict[str, Any]) -> tuple[str, float | None] | None:
    """Plan and expiry from the response – only plans a provider may grant."""
    plan = data.get("plan")
    if not data.get("valid") or plan not in EXTERNAL_PLANS:
        return None
    exp = data.get("expires")
    return plan, (float(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None)


def _error_text(data: dict[str, Any]) -> str:
    """Error text of the licence provider for the user: limited to harmless characters and 160 characters, otherwise
    a sentence of our own."""
    msg = data.get("error")
    msg = re.sub(r"[^\w .,:;!?()äöüÄÖÜß-]", "", msg)[:160] if isinstance(msg, str) else ""
    return msg or "Der Lizenzanbieter hat den Schlüssel abgelehnt."


async def activate(key: str) -> dict[str, Any]:
    """Redeem a purchased key at the licence provider and save the state. Raises LicenseError if the
    key is invalid, is rejected or the provider is unreachable."""
    key = (key or "").strip()
    if not _KEY_RE.fullmatch(key):
        raise LicenseError("Das ist kein gültiger Lizenzschlüssel.")
    try:
        data = await _call("activate", key)
    except LicenseOffline as exc:
        raise LicenseError(f"Der Lizenzanbieter ist nicht erreichbar ({exc}). Bitte später erneut versuchen.", 502)
    got = _granted(data)
    if got is None:
        raise LicenseError(_error_text(data))
    now = time.time()
    with _lock:
        set_secret(KEY_SECRET, key)
        save_cache(key, {"status": "active", "plan": got[0], "expires": got[1], "validated_at": now, "last_seen": now})
    events.log("license", f"Lizenz aktiviert ({entitlements.PLANS[got[0]]['title']})")
    return apply()


async def validate() -> dict[str, Any]:
    """Check the purchased key at the provider. Network errors change nothing but the offline grace period."""
    global _last_attempt
    key, cache = load_cache()
    if not key or provider_host() is None or DEV:
        return apply()
    now = time.time()
    _last_attempt = now
    cache = dict(cache or {"status": "unknown", "plan": None, "expires": None, "validated_at": 0})
    was_active = cache.get("status") == "active"
    try:
        data = await _call("validate", key)
    except LicenseOffline:
        cache["last_seen"] = max(now, float(cache.get("last_seen") or 0))
        save_cache(key, cache)
        return apply()
    got = _granted(data)
    if got is not None:
        cache.update(status="active", plan=got[0], expires=got[1], validated_at=now, last_seen=now)
    else:
        cache.update(status="invalid", last_seen=now)
        if was_active:
            events.log("license", "Die Lizenz ist nicht mehr gültig – ComfyOS läuft als Basic weiter")
    save_cache(key, cache)
    return apply()


async def deactivate() -> dict[str, Any]:
    """Remove licence file and purchased key from this device. The owner file always stays."""
    key, _ = load_cache()
    if key and provider_host() is not None and not DEV:
        try:
            await _call("deactivate", key)   # releases the activation at the provider; errors do not matter
        except (LicenseOffline, LicenseError):
            pass   # provider unreachable: it is removed on this device anyway
    with _lock:
        removed = _unlink(GIFT_FILE)
        removed = _unlink(CACHE_FILE) or removed
        if key:
            delete_secret(KEY_SECRET)
            removed = True
    if removed:
        events.log("license", "Lizenz von diesem Gerät entfernt")
    return apply()


# ---------------------------------------------------------------- Redeeming
async def redeem(text: str) -> dict[str, Any]:
    """Redeem an owner token, a licence file/short form or a purchased key."""
    text = (text or "").strip()
    if not text or len(text) > 8192:
        raise LicenseError("Bitte einen Lizenzschlüssel oder den Inhalt einer Lizenzdatei eingeben.")
    doc = parse_gift(text) if text.startswith(("{", KEY_PREFIX + ".")) else None
    if isinstance(doc, dict) and doc.get("type") == "owner":
        text, doc = str(doc.get("token") or ""), None
        if not _owner_token_ok(text):
            raise LicenseError("Diese Owner-Datei passt nicht zu dieser ComfyOS-Version.")
    if doc is not None:
        payload, why = verify_gift(doc)
        if payload is None:
            raise LicenseError(REASONS[why])
        with _lock:
            _write_json(GIFT_FILE, doc)
        events.log("license", f"Lizenzdatei importiert ({entitlements.PLANS[payload['plan']]['title']})")
        return apply()
    if text.startswith(("{", KEY_PREFIX + ".")):
        raise LicenseError(REASONS["format"])
    if _owner_token_ok(text):
        with _lock:
            _write_json(OWNER_FILE, {"type": "owner", "token": text})
        events.log("license", "Owner-Lizenz auf diesem Gerät eingerichtet")
        return apply()
    if _UUID_RE.fullmatch(text):
        # Looks like a (mistyped) owner token: never send it to a provider as a supposed purchase key
        raise LicenseError("Dieses Owner-Token passt nicht zu dieser ComfyOS-Version.")
    return await activate(text)


# ---------------------------------------------------------------- Preview (owner only)
def _preview(now: float, licensed: str) -> dict[str, Any] | None:
    """Running preview (another plan for a time) or None. Only with an owner licence and never longer than the
    allowed maximum duration."""
    if licensed != "owner":
        return None
    doc = _read_json(PREVIEW_FILE)
    if not isinstance(doc, dict) or doc.get("plan") not in entitlements.PLANS or doc.get("plan") == "owner":
        return None
    try:
        until = float(doc.get("until") or 0)
    except (TypeError, ValueError):
        return None
    # longer than the allowed maximum duration (hand-edited file, changed clock) does not count
    return {"plan": doc["plan"], "until": until} if now < until <= now + MAX_PREVIEW_SECONDS + 60 else None


def set_preview(plan: str | None, minutes: int = 60) -> dict[str, Any]:
    """View the installation as another plan for a limited time. Only with an owner licence."""
    if status()["licensed_plan"] != "owner":
        raise LicenseError("Die Testansicht gibt es nur mit Owner-Lizenz.", 403)
    if plan in (None, "", "owner"):
        return clear_preview()
    if plan not in entitlements.PLANS:
        raise LicenseError("Unbekannter Plan.")
    minutes = max(5, min(MAX_PREVIEW_SECONDS // 60, int(minutes)))
    with _lock:
        _write_json(PREVIEW_FILE, {"plan": plan, "until": time.time() + minutes * 60})
    events.log("license", f"Testansicht: {entitlements.PLANS[plan]['title']} für {minutes} Minuten")
    return apply()


def clear_preview() -> dict[str, Any]:
    """End the preview – always allowed, tied to no condition."""
    with _lock:
        if _unlink(PREVIEW_FILE):
            events.log("license", "Testansicht beendet")
    return apply()


# ---------------------------------------------------------------- Lizenzen ausstellen
def _private_key(sign_key_hex: str):
    """Signing key from its hex form. Raises LicenseError if it is invalid."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        raw = bytes.fromhex((sign_key_hex or "").strip())
    except ValueError:
        raise LicenseError("Der Signaturschlüssel ist ungültig.")
    if len(raw) != 32:
        raise LicenseError("Der Signaturschlüssel ist ungültig.")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_of(sign_key_hex: str) -> str:
    """Public part of a signing key (hex)."""
    from cryptography.hazmat.primitives import serialization
    pub = _private_key(sign_key_hex).public_key()
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def mint_gift(sign_key_hex: str, plan: str, name: str = "", iid: str | None = None, days: int | None = None,
              now: float | None = None) -> dict[str, Any]:
    """Issue a signed licence (tools/mint_gift.py and the owner's generator)."""
    if plan not in GIFT_PLANS:
        raise LicenseError("Unbekannter Plan.")
    iid = (iid or "").strip().lower() or None
    if iid is not None and not _IID_RE.fullmatch(iid):
        raise LicenseError("Die Geräte-Kennung besteht aus 16 Zeichen (0–9, a–f).")
    if days is not None and not 1 <= int(days) <= 36500:
        raise LicenseError("Ungültige Laufzeit.")
    now = now or time.time()
    payload = {"v": 1, "id": secrets.token_hex(6), "plan": plan, "iid": iid, "name": " ".join(str(name or "").split())[:80],
               "issued": int(now), "exp": int(now + int(days) * 86400) if days else None}
    sig = _private_key(sign_key_hex).sign(GIFT_DOMAIN + canonical(payload)).hex()
    return {**payload, "sig": sig}


def issuer_available() -> bool:
    """Only the installation with the owner token (source A) may issue – not a gifted owner licence."""
    src = _source_owner()
    return bool(src and src["active"])


def _sign_key() -> str | None:
    """The stored signing key – only if its public part belongs to this version, otherwise None."""
    try:
        key = get_secret(SIGN_SECRET)
        return key if key and public_of(key) in license_keys.SIGN_PUBS else None
    except Exception:
        return None


def install_sign_key(sign_key_hex: str) -> None:
    """Store a signing key on this installation (licence generator). Only with the owner token and only a
    key this version knows."""
    if not issuer_available():
        raise LicenseError("Lizenzen ausstellen kann nur die Installation mit Owner-Token.", 403)
    key = (sign_key_hex or "").strip().lower()
    if public_of(key) not in license_keys.SIGN_PUBS:
        raise LicenseError("Dieser Signaturschlüssel gehört nicht zu dieser ComfyOS-Version.")
    set_secret(SIGN_SECRET, key)
    events.log("license", "Signaturschlüssel auf diesem Gerät hinterlegt (Lizenz-Generator)")


def remove_sign_key() -> None:
    """Remove the signing key from this installation."""
    delete_secret(SIGN_SECRET)
    events.log("license", "Signaturschlüssel von diesem Gerät entfernt")


def issued() -> list[dict[str, Any]]:
    """Recently issued licences (at most MAX_ISSUED)."""
    doc = _read_json(ISSUED_FILE)
    return [x for x in doc if isinstance(x, dict)][-MAX_ISSUED:] if isinstance(doc, list) else []


def issue(plan: str, name: str = "", iid: str | None = None, days: int | None = None) -> dict[str, Any]:
    """Issue a licence in the dashboard: signed with the key stored here, noted in the directory."""
    if not issuer_available():
        raise LicenseError("Lizenzen ausstellen kann nur die Installation mit Owner-Token.", 403)
    key = _sign_key()
    if key is None:
        raise LicenseError("Auf diesem Gerät ist kein Signaturschlüssel hinterlegt.")
    doc = mint_gift(key, plan, name, iid, days)
    with _lock:
        rows = issued() + [{k: doc[k] for k in ("id", "plan", "name", "iid", "issued", "exp")}]
        _write_json(ISSUED_FILE, rows[-MAX_ISSUED:])
    events.log("license", f"Lizenz ausgestellt: {entitlements.PLANS[plan]['title']}"
               + (f" für {doc['name']}" if doc["name"] else "") + f" (Nr. {doc['id']})")
    return {"license": doc, "key": gift_key(doc)}


# ---------------------------------------------------------------- Ergebnis
def status(now: float | None = None) -> dict[str, Any]:
    """Check all sources. Never contains token, key or signing key."""
    now = now or time.time()
    sources = []
    for load in (_source_owner, lambda: _source_gift(now), lambda: _source_key(now)):
        try:
            src = load()
        except Exception:      # a broken source must never take the others down with it
            src = None
        if src is not None:
            sources.append(src)
    active = [s for s in sources if s["active"] and s["plan"] in entitlements.PLANS]
    best = max(active, key=lambda s: entitlements.plan_rank(s["plan"]), default=None)
    licensed = best["plan"] if best else "basic"
    try:
        preview = _preview(now, licensed)
    except Exception:
        preview = None
    plan = preview["plan"] if preview else licensed
    titles = entitlements.PLANS
    return {"plan": plan, "plan_title": titles[plan]["title"], "licensed_plan": licensed,
            "licensed_title": titles[licensed]["title"], "source": best["source"] if best else None,
            "expires": best["expires"] if best else None, "name": (best or {}).get("name") or "",
            "preview": preview, "can_preview": licensed == "owner", "sources": sources,
            "instance_id": hw_id(), "provider": provider_host(), "dev": DEV,
            "issuer": {"available": issuer_available(), "ready": issuer_available() and _sign_key() is not None}}


def apply() -> dict[str, Any]:
    """Set the effective plan; on a change the flow engine takes it over at once."""
    global _applied
    with _lock:
        st = status()
        now_applied = (st["plan"], bool(st["preview"]))
        changed = _applied is not None and _applied != now_applied
        entitlements.use_plan(st["plan"], enforce=bool(st["preview"]))
        first, _applied = _applied is None, now_applied
    if changed or (first and now_applied != ("basic", False)):
        try:
            from . import flow_engine
            flow_engine.engine.reload()
        except Exception as exc:
            events.log("license", f"Flows konnten nach dem Planwechsel nicht neu geladen werden ({type(exc).__name__})")
    return st


def sync() -> dict[str, Any]:
    """Read the state and only set it anew if it has changed (expired preview/licence,
    owner file placed by hand). Without a change the set plan stays untouched."""
    st = status()
    if _applied is not None and (st["plan"], bool(st["preview"])) != _applied:
        return apply()
    return st


async def tick(now: float | None = None) -> None:
    """One step of the background check: take over a changed state, check the purchased key."""
    now = now or time.time()
    sync()
    key, cache = load_cache()
    if not key or provider_host() is None or DEV:
        return
    last = max(float((cache or {}).get("validated_at") or 0), 0.0)
    if now - last >= CHECK_SECONDS and now - _last_attempt >= RETRY_SECONDS:
        await validate()


async def check_loop() -> None:
    """Background task: checks the licence regularly. An error never stops ComfyOS – the last state then keeps
    applying."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            await tick()
        except Exception:
            pass   # the check must never stop ComfyOS; the last state keeps applying
