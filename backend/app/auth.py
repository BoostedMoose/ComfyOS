"""Login for the dashboard (needed as soon as ComfyOS is reachable in the home network).

* One password for ComfyOS (PBKDF2-SHA256, salted) in APP_DIR/auth.json.
* Sessions as a random token in an HttpOnly cookie; only the
  SHA-256 hash is stored. Sessions survive restarts and updates.
* First setup: without a password one may be set – from the machine itself
  (localhost) directly, from the network only with the setup code that is printed on the
  console at the start and lies in APP_DIR/setup_code.txt. That way nobody in the
  WLAN can "take over" ComfyOS before you have set it up.
* After 5 failed attempts the login from this address is blocked – 60 s, on
  repetition twice as long each time (at most 1 h). Many failed attempts from
  different addresses additionally slow down every login.
* Sessions expire after 30 days without use and can be signed out individually.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Any

from . import jsonfile
from .settings_store import APP_DIR

AUTH_FILE = APP_DIR / "auth.json"
SETUP_CODE_FILE = APP_DIR / "setup_code.txt"
COOKIE = "comfyos_session"
SESSION_DAYS = 30            # expiry after this many days without use
ITERATIONS = 240_000
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
FAILS_PER_LOCK = 5
LOCK_BASE, LOCK_MAX = 60, 3600
GLOBAL_WINDOW, GLOBAL_LIMIT = 600, 30

_lock = threading.Lock()
_fails: dict[str, list[float]] = {}   # ip -> [fehlversuche, gesperrt_bis, sperren]
_global_fails: list[float] = []
_seen_write: dict[str, float] = {}    # sitzungs-hash -> zuletzt last_seen geschrieben
_cookie_at: dict[str, float] = {}     # sitzungs-hash -> Cookie zuletzt erneuert


def _load() -> dict[str, Any]:
    """Login data from auth.json; without a readable file: no password, no sessions."""
    data = jsonfile.read(AUTH_FILE, None)
    return data if data is not None else {"password": None, "sessions": {}}


def _save(data: dict[str, Any]) -> None:
    """Save the login data atomically and readable only for the ComfyOS user."""
    jsonfile.write(AUTH_FILE, data, ensure_ascii=True, private=True)


def _hash_pw(password: str, salt: bytes) -> str:
    """Password hash (PBKDF2-SHA256 with salt) as hex text."""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS).hex()


def _hash_token(token: str) -> str:
    """SHA-256 of a session token. Only this hash is stored – whoever reads auth.json has no session with it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_configured() -> bool:
    """Ist ein Passwort gesetzt (ComfyOS eingerichtet)?"""
    return bool(_load().get("password"))


def setup_code() -> str | None:
    """Setup code (only as long as no password is set)."""
    if is_configured():
        try:
            SETUP_CODE_FILE.unlink()
        except OSError:
            pass   # already gone
        return None
    try:
        code = SETUP_CODE_FILE.read_text(encoding="utf-8").strip()
        if code:
            return code
    except OSError:
        pass   # no code stored yet: one is created below
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    code = f"{raw[:4]}-{raw[4:]}"
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETUP_CODE_FILE.write_text(code, encoding="utf-8")
    try:
        os.chmod(SETUP_CODE_FILE, 0o600)
    except OSError:
        pass   # file permissions do not exist here (Windows)
    return code


def check_setup_code(code: str | None) -> bool:
    """Is the entered setup code correct? Compared in constant time, spaces and hyphens
    do not count."""
    expected = setup_code()
    given = (code or "").strip().upper().replace(" ", "")
    if not expected or not given:
        return False
    return hmac.compare_digest(expected.replace("-", ""), given.replace("-", ""))


def set_password(password: str) -> None:
    """Set the password (at least 6 characters). The setup code is used up after that. Sessions stay
    – whoever wants to sign them out calls revoke_all."""
    if len(password or "") < 6:
        raise ValueError("Das Passwort muss mindestens 6 Zeichen haben.")
    salt = secrets.token_bytes(16)
    with _lock:
        data = _load()
        data["password"] = {"salt": salt.hex(), "hash": _hash_pw(password, salt), "iterations": ITERATIONS}
        _save(data)
    try:
        SETUP_CODE_FILE.unlink()
    except OSError:
        pass   # already gone


def verify_password(password: str) -> bool:
    """Is the password correct? Comparison in constant time; always false without a password set."""
    pw = _load().get("password")
    if not pw:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                               bytes.fromhex(pw["salt"]), int(pw.get("iterations", ITERATIONS))).hex()
    return hmac.compare_digest(calc, pw["hash"])


# ------------------------------------------------------------ Fehlversuche
def locked_for(ip: str) -> int:
    """Seconds an address is still blocked after too many failed attempts (0 = not blocked)."""
    entry = _fails.get(ip)
    if not entry:
        return 0
    return max(0, int(entry[1] - time.time()))


def register_failure(ip: str) -> int:
    """Count a failed attempt. Returns: block duration in s if a block starts right now, otherwise 0."""
    now = time.time()
    _global_fails.append(now)
    while _global_fails and now - _global_fails[0] > GLOBAL_WINDOW:
        _global_fails.pop(0)
    entry = _fails.setdefault(ip, [0, 0.0, 0])
    entry[0] += 1
    if entry[0] < FAILS_PER_LOCK:
        return 0
    entry[0] = 0
    entry[2] += 1
    seconds = min(LOCK_MAX, LOCK_BASE * 2 ** (int(entry[2]) - 1))
    entry[1] = now + seconds
    if len(_fails) > 1000:   # Speicher begrenzen
        for k in [k for k, v in _fails.items() if v[1] < now][:500]:
            _fails.pop(k, None)
    return seconds


def under_attack() -> bool:
    """Many failed attempts in total (also spread over many addresses)."""
    now = time.time()
    return sum(1 for t in _global_fails if now - t <= GLOBAL_WINDOW) >= GLOBAL_LIMIT


def clear_failures(ip: str) -> None:
    """Forget the failed attempts of an address (after a successful login)."""
    _fails.pop(ip, None)


# ------------------------------------------------------------ Sitzungen
def _expired(s: dict[str, Any], now: float) -> bool:
    """Has the session expired? It expires SESSION_DAYS after its last use."""
    return now - s.get("last_seen", 0) > SESSION_DAYS * 86400


def create_session(user_agent: str = "", ip: str = "") -> str:
    """Create a new session and return its token. Expired sessions are cleaned up along the way."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        data = _load()
        sessions = data.setdefault("sessions", {})
        for h in [h for h, s in sessions.items() if _expired(s, now)]:
            sessions.pop(h, None)
        sessions[_hash_token(token)] = {"created": now, "last_seen": now, "ua": (user_agent or "")[:160],
                                        "ip": (ip or "")[:45]}
        _save(data)
    return token


def check_session(token: str | None) -> bool:
    """Does the token belong to a valid session? The last use is updated along the way – at most every ten
    minutes, to spare the file."""
    if not token:
        return False
    h = _hash_token(token)
    data = _load()
    s = data.get("sessions", {}).get(h)
    if not s:
        return False
    now = time.time()
    if _expired(s, now):
        return False
    if now - _seen_write.get(h, 0) > 600:  # write last_seen per session only now and then
        with _lock:
            data = _load()
            if h in data.get("sessions", {}):
                data["sessions"][h]["last_seen"] = now
                _save(data)
        _seen_write[h] = now
    return True


def needs_cookie_refresh(token: str) -> bool:
    """Renew the cookie at most once a day (sliding expiry, current cookie attributes)."""
    h = _hash_token(token)
    now = time.time()
    if now - _cookie_at.get(h, 0) < 86400:
        return False
    _cookie_at[h] = now
    return True


def _device_label(ua: str) -> str:
    """Short name of a device from the user agent, e.g. "Safari · iPhone"."""
    u = ua or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                           ("Safari/", "Safari")) if key in u), "Browser")
    system = next((name for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                          ("Windows", "Windows"), ("Mac OS X", "Mac"), ("Linux", "Linux")) if key in u), "")
    if not u:
        return "Unbekanntes Gerät"
    return f"{browser} · {system}" if system else browser


def list_sessions(current: str | None) -> list[dict[str, Any]]:
    """Valid sessions for display (without tokens): the own one first, then by last use."""
    cur = _hash_token(current) if current else None
    now = time.time()
    out = [{"id": h[:16], "device": _device_label(s.get("ua", "")), "ip": s.get("ip") or "",
            "created": s.get("created"), "last_seen": s.get("last_seen"), "current": h == cur}
           for h, s in _load().get("sessions", {}).items() if not _expired(s, now)]
    out.sort(key=lambda x: (not x["current"], -(x["last_seen"] or 0)))
    return out


def revoke_by_id(sid: str) -> bool:
    """Sign out a session by its short id (beginning of the hash). True = it existed."""
    if not sid or len(sid) < 16:
        return False
    with _lock:
        data = _load()
        sessions = data.get("sessions", {})
        hit = next((h for h in sessions if h.startswith(sid)), None)
        if hit is None:
            return False
        sessions.pop(hit, None)
        _save(data)
    return True


def revoke_session(token: str | None) -> None:
    """Die Sitzung dieses Tokens abmelden."""
    if not token:
        return
    with _lock:
        data = _load()
        if data.get("sessions", {}).pop(_hash_token(token), None) is not None:
            _save(data)


def revoke_all(except_token: str | None = None) -> int:
    """Sign out all sessions, optionally except one (the own). Returns the number."""
    keep = _hash_token(except_token) if except_token else None
    with _lock:
        data = _load()
        sessions = data.get("sessions", {})
        removed = [h for h in sessions if h != keep]
        for h in removed:
            sessions.pop(h, None)
        _save(data)
    return len(removed)


def session_count() -> int:
    """Number of valid sessions."""
    now = time.time()
    return sum(1 for s in _load().get("sessions", {}).values() if not _expired(s, now))
