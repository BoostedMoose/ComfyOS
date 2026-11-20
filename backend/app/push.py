"""Web Push for the flow node "Benachrichtigung" (data/push.json).

    { "version": 1,
      "subs": [ { "id", "endpoint", "keys": {"p256dh", "auth"}, "label", "created", "last_ok", "fails" } ] }

Push necessarily goes through the service of the browser maker (Apple, Google, Mozilla, Microsoft) – hence the
egress entry `push`. The text is end-to-end encrypted (RFC 8291); the service only sees the push address
of the device. This is only active if a device has explicitly switched notifications on.

* Encryption and VAPID (RFC 8292) are done by pywebpush – no crypto is rebuilt here.
* Sending itself goes through egress.sync_client("push"): only the registered hosts, after redirects too.
  The push address of a subscription is checked against the same list on sign-up already (no SSRF through a subscription).
* The VAPID key lives in the secret store (`push:vapid`) and never leaves ComfyOS; backups do not contain it.
* Expired subscriptions (HTTP 404/410) are removed automatically.
* If pywebpush is missing (e.g. before the first `pip install`), push simply is not available – never an aborted start.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from . import jsonfile
from . import egress, events
from .paths import DATA_DIR, ensure_dirs
from .settings_store import delete_secret, get_secret, set_secret

PUSH_FILE = DATA_DIR / "push.json"
VERSION = 1
SECRET = "push:vapid"
MAX_SUBS = 20
MAX_FAILS = 10                 # this many failed attempts in a row: the subscription counts as dead
SEND_TIMEOUT = 12.0
TTL = 3600                     # the push service keeps a message this long for a device that is switched off
# Contact for the push service (VAPID "sub"). Deliberately not a real address – ComfyOS passes on no user data.
CONTACT = os.getenv("COMFYOS_PUSH_CONTACT", "mailto:comfyos@example.com")
_lock = threading.Lock()


class PushError(ValueError):
    """Error while setting up a push subscription – the text is meant for the user."""
    pass


# ---------------------------------------------------------------- Speicher
def _load() -> dict[str, Any]:
    """Subscriptions from push.json, checked: only valid addresses of allowed push services, at most MAX_SUBS."""
    data = jsonfile.read(PUSH_FILE, {})
    subs = []
    for s in (data.get("subs") if isinstance(data.get("subs"), list) else [])[:MAX_SUBS]:
        if isinstance(s, dict) and _valid(s):
            subs.append({"id": str(s.get("id") or secrets.token_hex(6))[:24], "endpoint": s["endpoint"],
                         "keys": {"p256dh": s["keys"]["p256dh"], "auth": s["keys"]["auth"]},
                         "label": " ".join(str(s.get("label") or "").split())[:60],
                         "created": float(s.get("created") or 0), "last_ok": float(s.get("last_ok") or 0),
                         "fails": int(s.get("fails") or 0)})
    return {"version": VERSION, "subs": subs}


def _save(data: dict[str, Any]) -> None:
    """Atomar nach push.json schreiben."""
    ensure_dirs()
    jsonfile.write(PUSH_FILE, data)


def _b64(v: Any, lo: int, hi: int) -> bool:
    """Base64url value of plausible length (key of the browser)."""
    return isinstance(v, str) and lo <= len(v) <= hi and all(c.isalnum() or c in "-_=" for c in v)


def _valid(sub: dict) -> bool:
    """Is the subscription usable? The address must be https and belong to a push service from the egress register –
    that way no foreign server can be addressed through a subscription."""
    ep, keys = sub.get("endpoint"), sub.get("keys")
    if not isinstance(ep, str) or len(ep) > 1000 or not isinstance(keys, dict):
        return False
    u = urlsplit(ep)
    if u.scheme != "https" or not egress.host_allowed(egress.REGISTRY["push"], u.hostname):
        return False
    return _b64(keys.get("p256dh"), 80, 100) and _b64(keys.get("auth"), 16, 32)


def subscriptions() -> list[dict[str, Any]]:
    """Subscriptions for the interface – without keys and without the full push address."""
    with _lock:
        subs = _load()["subs"]
    return [{"id": s["id"], "label": s["label"], "created": s["created"], "last_ok": s["last_ok"],
             "service": urlsplit(s["endpoint"]).hostname} for s in subs]


def count() -> int:
    """Number of devices with notifications."""
    with _lock:
        return len(_load()["subs"])


def subscribe(sub: Any, label: str = "") -> dict[str, Any]:
    """Save or renew the subscription of a device. Raises PushError if it is invalid or the maximum number is
    reached."""
    if not isinstance(sub, dict) or not _valid(sub):
        raise PushError("Dieses Push-Abo ist ungültig oder der Push-Dienst ist nicht freigegeben.")
    with _lock:
        data = _load()
        cur = next((s for s in data["subs"] if s["endpoint"] == sub["endpoint"]), None)
        if cur is None:
            if len(data["subs"]) >= MAX_SUBS:
                raise PushError(f"Höchstens {MAX_SUBS} Geräte mit Benachrichtigungen.")
            cur = {"id": secrets.token_hex(6), "endpoint": sub["endpoint"], "created": time.time(), "last_ok": 0.0, "fails": 0}
            data["subs"].append(cur)
        cur["keys"] = {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}
        cur["label"] = " ".join(str(label or "").split())[:60] or cur.get("label") or "Gerät"
        cur["fails"] = 0
        _save(data)
        return {"id": cur["id"], "label": cur["label"]}


def unsubscribe(endpoint: str | None = None, sub_id: str | None = None) -> bool:
    """Remove a subscription – by its address or its id. True = it existed."""
    with _lock:
        data = _load()
        keep = [s for s in data["subs"] if not ((endpoint and s["endpoint"] == endpoint) or (sub_id and s["id"] == sub_id))]
        if len(keep) == len(data["subs"]):
            return False
        data["subs"] = keep
        _save(data)
        return True


# ---------------------------------------------------------------- Keys (VAPID)
def available() -> bool:
    """Is the package for Web Push installed? Without it there are no notifications, ComfyOS runs anyway."""
    try:
        import pywebpush  # noqa: F401
        return True
    except Exception:
        return False


def _private_pem() -> bytes:
    """Fetch the private VAPID key or create it once (secret store)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = get_secret(SECRET)
    if not pem:
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode("ascii")
        set_secret(SECRET, pem)
    return pem.encode("ascii")


def public_key() -> str:
    """Public VAPID key for the browser (applicationServerKey, base64url without padding)."""
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(_private_pem(), password=None)
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def forget() -> None:
    """Discard key and subscriptions (e.g. after a restored migration package: the subscriptions belong to the old key)."""
    delete_secret(SECRET)
    with _lock:
        if PUSH_FILE.exists():
            _save({"version": VERSION, "subs": []})


# ---------------------------------------------------------------- Versand
class _Response:
    """Response in the form pywebpush expects (from an httpx response)."""
    def __init__(self, r):
        """Take over status, reason, shortened text and headers of the httpx response."""
        self.status_code, self.reason, self.text, self.headers = r.status_code, r.reason_phrase, r.text[:300], r.headers


class _Session:
    """Transport for pywebpush: the same checked path as all fixed targets (only registered push services)."""

    def post(self, endpoint, data=None, headers=None, timeout=None, **_kw):
        """Send a push message – through the egress client `push`, which only reaches allowed push services."""
        with egress.sync_client("push", timeout=10.0, follow_redirects=False) as c:
            return _Response(c.post(endpoint, content=data, headers={str(k): str(v) for k, v in (headers or {}).items()}))


def _send_one(sub: dict, payload: str, pem: bytes) -> int:
    """Deliver one message (blocking, runs in a thread). Returns the HTTP status; 0 = not delivered."""
    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush

    try:
        r = webpush({"endpoint": sub["endpoint"], "keys": dict(sub["keys"])}, data=payload,
                    vapid_private_key=Vapid.from_pem(pem), vapid_claims={"sub": CONTACT},
                    ttl=TTL, timeout=10, requests_session=_Session())
        return int(getattr(r, "status_code", 201))
    except WebPushException as exc:
        return int(exc.status_code or 0) if getattr(exc, "status_code", None) else 0
    except Exception:
        return 0


# replaceable for tests: (subscription, payload, key) -> HTTP status
sender = _send_one


async def send_all(title: str, body: str, url: str = "/#flow") -> dict[str, int]:
    """Message to all devices with notifications switched on. Never raises – a flow does not depend on push."""
    out = {"sent": 0, "failed": 0, "removed": 0}
    try:
        with _lock:
            subs = _load()["subs"]
        if not subs or not available():
            return out
        pem = _private_pem()
        payload = json.dumps({"title": str(title or "ComfyOS")[:80], "body": str(body or "")[:300], "url": url},
                             ensure_ascii=False)
        results: dict[str, int] = {}
        for s in subs:
            try:
                results[s["id"]] = await asyncio.wait_for(asyncio.to_thread(sender, s, payload, pem), SEND_TIMEOUT)
            except Exception:
                results[s["id"]] = 0
        gone = []
        with _lock:
            data = _load()
            for s in data["subs"]:
                code = results.get(s["id"])
                if code is None:
                    continue
                if 200 <= code < 300:
                    s["last_ok"], s["fails"] = time.time(), 0
                    out["sent"] += 1
                else:
                    s["fails"] += 1
                    out["failed"] += 1
                    if code in (404, 410) or s["fails"] >= MAX_FAILS:
                        gone.append(s["id"])
            data["subs"] = [s for s in data["subs"] if s["id"] not in gone]
            out["removed"] = len(gone)
            _save(data)
        if gone:
            events.log("flow", f"Benachrichtigungen: {len(gone)} abgelaufene Geräte-Anmeldung(en) entfernt")
    except Exception as exc:
        events.log("flow", f"Push-Benachrichtigung nicht gesendet ({type(exc).__name__})")
    return out
