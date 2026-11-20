"""Settings and credentials (outside the program folder).

Place: %APPDATA%\\ComfyOS (Windows) or ~/.config/ComfyOS (Linux/Pi).

Passwords preferably go into the keyring of the operating system
(Windows Credential Manager, Secret Service). On a headless
Raspberry Pi there usually is no usable keyring; then they end up in
secrets.json with file permissions 0600 (only the ComfyOS user may read). That
is not encryption but access protection through the file system – other
home automation systems do it the same way.
"""
import base64
import json
import os
import re
from pathlib import Path

from . import demo, jsonfile

_BASE = Path(os.getenv("APPDATA") or Path.home() / ".config")
APP_DIR = _BASE / "ComfyOS"
SETTINGS_FILE = APP_DIR / "settings.json"
SECRETS_FILE = APP_DIR / "secrets.json"
SERVICE = "ComfyOS"

KEYS = ("https", "log_sample_seconds", "log_retention_months", "allowed_hosts", "accent_color", "plugins", "layout")
MAX_PLUGINS_BYTES = 200_000   # settings of all plugins together


def _clean_plugins(raw):
    """Plugin section (since 0.18): disabled and approved plugins, settings per plugin (simple values only)."""
    raw = raw if isinstance(raw, dict) else {}
    ids = lambda k: sorted({x for x in raw.get(k, []) if isinstance(x, str) and 0 < len(x) <= 31})[:200]         if isinstance(raw.get(k), list) else []   # noqa: E731
    settings = {}
    for pid, values in (raw.get("settings") if isinstance(raw.get("settings"), dict) else {}).items():
        if isinstance(pid, str) and 0 < len(pid) <= 31 and isinstance(values, dict):
            settings[pid] = {str(k)[:41]: v for k, v in values.items() if isinstance(v, (str, int, float, bool, list, dict))}
    out = {"disabled": ids("disabled"), "trusted": ids("trusted"), "settings": settings}
    return out if len(json.dumps(out)) <= MAX_PLUGINS_BYTES else {"disabled": out["disabled"], "trusted": out["trusted"], "settings": {}}


LAYOUT_PAGES = ("system", "plugins", "home")      # frei anordnbare Kacheln: System → Einstellungen/Plugins, Raumkacheln (seit 0.19.3)
LAYOUT_MAX_TILES = 80
_TILE_ID = re.compile(r"^[A-Za-z0-9_:.-]{1,64}$")


def clean_columns(columns, cols: int) -> list[list[str]] | None:
    """Columns of an arrangement: exactly `cols` lists of tile ids, every id at most once. None = invalid."""
    if not isinstance(columns, list) or len(columns) != cols:
        return None
    seen, out = set(), []
    for col in columns:
        if not isinstance(col, list):
            return None
        keep = []
        for tile in col:
            if isinstance(tile, str) and _TILE_ID.match(tile) and tile not in seen and len(seen) < LAYOUT_MAX_TILES:
                seen.add(tile)
                keep.append(tile)
        out.append(keep)
    return out


def _clean_layout(raw):
    """Arrangement of the tiles (since 0.19.2): {page: {"<columns>": [[id, …], …]}} – per number of columns an own
    arrangement dragged by the user. Display only; if it is missing, the interface distributes the tiles itself."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for page in LAYOUT_PAGES:
        per = raw.get(page) if isinstance(raw.get(page), dict) else {}
        clean = {}
        for key in ("1", "2", "3"):
            cols = clean_columns(per.get(key), int(key))
            if cols is not None:
                clean[key] = cols
        if clean:
            out[page] = clean
    return out


DEMO_KEYS = ("layout", "accent_color")     # what the demo keeps of its own (demo.py); everything else stays the real one


def load_settings():
    """Content of settings.json; empty if the file is missing or unreadable. In the demo, arrangement and accent colour
    come from the demo (data/demo/settings.json)."""
    data = jsonfile.read(SETTINGS_FILE, {})
    if demo.active():
        own = jsonfile.read(demo.DEMO_DIR / "settings.json", {})
        data = {**{k: v for k, v in data.items() if k not in DEMO_KEYS}, **{k: own[k] for k in DEMO_KEYS if k in own}}
    return data


def save_settings(data):
    """Check, limit and atomically save settings. What `data` does not name stays as it is stored."""
    current = load_settings()
    safe = {k: data.get(k, current.get(k)) for k in KEYS}      # known keys only – everything else drops out
    safe["https"] = bool(safe.get("https") or False)
    lss = safe.get("log_sample_seconds")
    safe["log_sample_seconds"] = max(60, min(3600, int(lss))) if lss not in (None, "") else None
    lrm = safe.get("log_retention_months")
    safe["log_retention_months"] = max(1, min(60, int(lrm))) if lrm not in (None, "") else None
    from .security import clean_hosts
    safe["allowed_hosts"] = clean_hosts(safe.get("allowed_hosts") if isinstance(safe.get("allowed_hosts"), list) else [])
    # Own accent colour (since 0.14, only stored since 0.16): only #rrggbb; None = default colour
    ac = safe.get("accent_color")
    if not (isinstance(ac, str) and len(ac) == 7 and ac[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in ac[1:])):
        safe.pop("accent_color", None)
    safe["plugins"] = _clean_plugins(safe.get("plugins"))
    safe["layout"] = _clean_layout(safe.get("layout"))
    if demo.active():     # the demo only keeps arrangement and accent colour of its own – the real file stays untouched
        jsonfile.write(demo.DEMO_DIR / "settings.json", {k: safe[k] for k in DEMO_KEYS if k in safe})
        return safe
    jsonfile.write(SETTINGS_FILE, safe, indent=2, ensure_ascii=True, private=True, durable=True)
    return safe


# ------------------------------------------------------------ Keyring
def _keyring():
    """Usable keyring module, or None (no backend / headless)."""
    try:
        import keyring
        backend = keyring.get_keyring()
        mod = type(backend).__module__
        if "fail" in mod or "null" in mod:
            return None
        return keyring
    except Exception:
        return None


def _file_load() -> dict:
    """Secrets from secrets.json (Base64 per value). Empty if the file is missing or unreadable."""
    raw = jsonfile.read(SECRETS_FILE, {})
    try:
        return {k: base64.b64decode(v).decode("utf-8") for k, v in raw.items()}
    except Exception:
        jsonfile.keep_broken(SECRETS_FILE)     # one value is unreadable: keep the file before it is overwritten
        return {}


def _file_save(data: dict):
    """Save secrets atomically and readable only for the ComfyOS user."""
    enc = {k: base64.b64encode(v.encode("utf-8")).decode("ascii") for k, v in data.items()}
    jsonfile.write(SECRETS_FILE, enc, ensure_ascii=True, private=True, durable=True)


def secret_backend() -> str:
    """Where the secrets live: `keyring` (keyring of the operating system) or `file` (secrets.json)."""
    return "keyring" if _keyring() else "file"


def set_secret(username, password):
    """Store a secret – in the keyring if there is one, otherwise in secrets.json. If it is in the
    keyring afterwards, an older version disappears from the file."""
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, username, password)
            data = _file_load()
            if data.pop(username, None) is not None:
                _file_save(data)
            return
        except Exception:
            pass   # the keyring does not accept the value: then into the file (below)
    data = _file_load()
    data[username] = password
    _file_save(data)


def get_secret(username):
    """Read a secret (keyring before file), or None."""
    kr = _keyring()
    if kr is not None:
        try:
            secret = kr.get_password(SERVICE, username)
            if secret is not None:
                return secret
        except Exception:
            pass   # keyring unreadable: then from the file (below)
    return _file_load().get(username)


def delete_secret(username):
    """Delete a secret in both places."""
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(SERVICE, username)
        except Exception:
            pass   # not there or cannot be deleted: the file is cleaned anyway
    data = _file_load()
    if data.pop(username, None) is not None:
        _file_save(data)
