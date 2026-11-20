"""Backup & restore: automatic local snapshots, ZIP download, log export, migration package.

A **backup** contains the data files and the settings – never secrets, licence or sessions. The
**migration package** is a backup plus the credentials of the plugins, encrypted with a password the user
chooses on export (scrypt + AES-GCM). Licence, sessions and the push key never move along.

In both cases only what is on the fixed list `_DATA_FILES` is restored, only valid JSON and only
after everything has been checked. What the files contain is checked by every store itself on loading.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

from . import plugins
from .paths import DATA_DIR, LOGS_DIR, DATA_ROOT, app_version, ensure_dirs
from .settings_store import KEYS, SETTINGS_FILE, get_secret, load_settings, save_settings, set_secret

BACKUP_DIR = DATA_ROOT / "backups"
MAX_AUTO_BACKUPS = 10
BACKUP_PREFIX = "comfyos-backup-"
MAX_RESTORE_FILE = 20 * 1024 * 1024
MIN_PACKAGE_PASSWORD = 8                  # the password of a migration package must be at least this long
MAX_SECRETS = 200                         # a migration package takes over at most this many credentials
MAX_SECRET_LEN = 8000                     # … and each single one may be this long
_SECRET_PREFIX = "sec_"                   # names in the encrypted part: sec_<name in the secret store>
_NAME_RE = re.compile(r"comfyos-backup-[A-Za-z0-9._-]{1,60}\.zip")

_DATA_FILES = ("floorplan.json", "routines.json", "rooms.json", "home.json",
               "flows.json", "scenes.json", "egress.json", "devices.json", "effects.json", "power.json",
               "widgets.json")


class BackupError(ValueError):
    """Backup or migration package is unusable – the text is meant for the user."""


def _restored_data() -> None:
    """Make stores with a state in memory reload after a restore."""
    from . import power_store
    power_store.forget()


def _ts() -> str:
    """Time stamp for file names: YYYYMMDD-HHMMSS."""
    return time.strftime("%Y%m%d-%H%M%S")


def _backup_path(label: str | None = None) -> Path:
    """Path of a new backup in the backup folder (creates the folder)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{BACKUP_PREFIX}{label or _ts()}.zip"
    return BACKUP_DIR / name


def list_backups() -> list[dict]:
    """Existing backups, newest first: file name, size, time."""
    if not BACKUP_DIR.is_dir():
        return []
    out = []
    for p in sorted(BACKUP_DIR.glob(f"{BACKUP_PREFIX}*.zip"), reverse=True):
        try:
            st = p.stat()
            out.append({"file": p.name, "size": st.st_size, "created": st.st_mtime})
        except OSError:
            pass                          # deleted between listing and querying: no longer belongs in the list
    return out


def _add_data_files(zf: zipfile.ZipFile) -> None:
    """Put the data files of the fixed list into the archive (under `data/`), as far as they exist."""
    for name in _DATA_FILES:
        f = DATA_DIR / name
        if f.is_file():
            zf.write(f, f"data/{name}")


def create_backup(label: str | None = None) -> dict:
    """Create a backup: data files and settings as a ZIP – without secrets, licence and sessions. Older backups
    beyond MAX_AUTO_BACKUPS are deleted along the way."""
    ensure_dirs()
    path = _backup_path(label)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("version.txt", app_version())
        zf.writestr("timestamp.txt", time.strftime("%Y-%m-%dT%H:%M:%S"))
        _add_data_files(zf)
        if SETTINGS_FILE.is_file():
            zf.write(SETTINGS_FILE, "settings.json")
    _prune_auto()
    return {"file": path.name, "size": path.stat().st_size, "created": path.stat().st_mtime}


def _prune_auto() -> None:
    """Nur die neuesten MAX_AUTO_BACKUPS Backups behalten."""
    backups = list_backups()
    auto = [b for b in backups if b["file"].startswith(BACKUP_PREFIX)]
    for old in auto[MAX_AUTO_BACKUPS:]:
        try:
            (BACKUP_DIR / old["file"]).unlink()
        except OSError:
            pass                          # cannot be deleted right now: the next run tries again


def _existing(filename: str) -> Path | None:
    """Backup file for a name from the interface – simple names only, never a path."""
    if not _NAME_RE.fullmatch(filename or "") or ".." in filename:
        return None
    path = BACKUP_DIR / filename
    return path if path.is_file() else None


def backup_zip_bytes(filename: str) -> bytes | None:
    """Content of a backup for download, or None. The file name is checked against a fixed pattern
    (`_existing`) – never a path from the request."""
    path = _existing(filename)
    return path.read_bytes() if path else None


def delete_backup(filename: str) -> bool:
    """Delete a backup. True = it existed."""
    path = _existing(filename)
    if path is None:
        return False
    path.unlink()
    return True


# ---------------------------------------------------------------- Restoring: check, then write
def _open(data: bytes) -> zipfile.ZipFile:
    """Open uploaded bytes as a ZIP. Raises BackupError if it is not one."""
    try:
        return zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError, ValueError):
        raise BackupError("Die Datei ist kein gültiges ZIP-Archiv.")


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Unpack an entry, but at most MAX_RESTORE_FILE bytes (protection against ZIP bombs)."""
    if info.file_size > MAX_RESTORE_FILE:
        raise BackupError(f"{info.filename} ist zu groß.")
    try:
        with zf.open(info) as fh:
            raw = fh.read(MAX_RESTORE_FILE + 1)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):   # damaged, encrypted, unknown method
        raise BackupError(f"{info.filename} lässt sich nicht entpacken.")
    if len(raw) > MAX_RESTORE_FILE:
        raise BackupError(f"{info.filename} ist zu groß.")
    return raw


def _checked_files(zf: zipfile.ZipFile, infos: dict[str, zipfile.ZipInfo], what: str) -> dict[str, bytes]:
    """Read and check data files from the archive: only names of the fixed list, only valid JSON (object or
    list). The search is for fixed names – a path from the archive is never used. `what` names the archive in
    the error text ("Backup", "Paket")."""
    files: dict[str, bytes] = {}
    for name in _DATA_FILES:
        info = infos.get(f"data/{name}")
        if info is None:
            continue
        raw = _read_entry(zf, info)
        try:
            if not isinstance(json.loads(raw), (dict, list)):
                raise ValueError
        except (ValueError, RecursionError):
            raise BackupError(f"{name} im {what} ist beschädigt.")
        files[name] = raw
    return files


def _settings_from(zf: zipfile.ZipFile, infos: dict[str, zipfile.ZipInfo]) -> dict | None:
    """Settings from the archive, or None if they are missing or unreadable (then the previous ones stay)."""
    if "settings.json" not in infos:
        return None
    try:
        settings = json.loads(_read_entry(zf, infos["settings.json"]))
    except (ValueError, RecursionError):
        return None
    return settings if isinstance(settings, dict) else None


def _write_files(files: dict[str, bytes]) -> list[str]:
    """Write checked data files atomically to their place. Returns their names."""
    for name, raw in files.items():
        target = DATA_DIR / name
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(target)
    _restored_data()
    return list(files)


def _apply_settings(settings: dict | None, restored: list[str]) -> None:
    """Take over settings – through save_settings, that is only known fields and cleaned. Addresses of hubs live
    in the section of their plugins; those check themselves that they are in the home network. If it fails, the
    previous settings stay; the response then does not name `settings.json` under `restored`."""
    if settings is None:
        return
    try:
        save_settings(settings)
        restored.append("settings.json")
    except (TypeError, ValueError, OSError):
        pass   # the previous settings stay (see description)


def restore_backup(data: bytes) -> dict:
    """Restore a backup: only the known files, only valid JSON, check first, then write.
    Settings go through save_settings (only known fields, cleaned). Raises BackupError with a text for
    the user if the archive is unusable – then nothing has been written."""
    ensure_dirs()
    with _open(data) as zf:
        infos = {i.filename: i for i in zf.infolist()}
        files = _checked_files(zf, infos, "Backup")
        settings = _settings_from(zf, infos)
    restored = _write_files(files)
    _apply_settings(settings, restored)
    return {"restored": restored}


# ---------------------------------------------------------------- Umzugspaket
def _package_key(password: str, salt: bytes) -> bytes:
    """Derive the key from the password of the migration package (scrypt – deliberately expensive, so that guessing
    against the package does not pay off)."""
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode("utf-8"))


def _plugin_secrets() -> dict[str, str]:
    """Credentials of all known plugins {name in the secret store: value} – exactly what their manifests name under
    `secrets`. Licence and push key are never there and therefore do not move along."""
    found: dict[str, str] = {}
    for rec in plugins.records():
        for key in (rec.manifest.secrets if rec.manifest else []):
            name = plugins.secret_name(rec.id, key)
            value = get_secret(name)
            if value:
                found[name] = value
    return found


def _store_secrets(data: dict) -> int:
    """Take over credentials from a migration package and return their number. Accepted are exclusively
    names of the form `plugin:<id>:<key>` – so a package can set or overwrite neither the licence nor the push key
    nor any other secret of the core."""
    count = 0
    for packed, value in list(data.items())[:MAX_SECRETS]:
        if not (isinstance(packed, str) and packed.startswith(_SECRET_PREFIX) and isinstance(value, str)):
            continue
        name = packed[len(_SECRET_PREFIX):]
        if not plugins.is_secret_name(name) or not value or len(value) > MAX_SECRET_LEN:
            continue
        set_secret(name, value)
        count += 1
    return count


def export_migration(password: str) -> bytes:
    """Migration package as a ZIP in memory: data files, settings and – encrypted with the password
    (`secrets.enc` = salt + nonce + AES-GCM) – the credentials of the plugins. Raises BackupError if the password
    is too short."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(password) < MIN_PACKAGE_PASSWORD:
        raise BackupError(f"Das Passwort für das Umzugspaket braucht mindestens {MIN_PACKAGE_PASSWORD} Zeichen.")
    ensure_dirs()
    salt, nonce = os.urandom(16), os.urandom(12)
    packed = {_SECRET_PREFIX + name: value for name, value in _plugin_secrets().items()}
    enc = AESGCM(_package_key(password, salt)).encrypt(nonce, json.dumps(packed, ensure_ascii=False).encode("utf-8"), None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({
            "format": 1, "source": "comfyos", "version": app_version(),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False))
        _add_data_files(zf)
        if SETTINGS_FILE.is_file():
            known = {k: v for k, v in load_settings().items() if k in KEYS}
            zf.writestr("settings.json", json.dumps(known, ensure_ascii=False, indent=1))
        zf.writestr("secrets.enc", salt + nonce + enc)
    return buf.getvalue()


def _decrypt_secrets(blob: bytes, password: str) -> dict:
    """Open the encrypted part of a migration package. Raises BackupError on a wrong password or damaged
    data – with AES-GCM the two cannot be told apart."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 28:
        raise BackupError("Verschlüsselte Daten beschädigt.")
    salt, nonce, enc = blob[:16], blob[16:28], blob[28:]
    try:
        secrets = json.loads(AESGCM(_package_key(password, salt)).decrypt(nonce, enc, None))
    except (InvalidTag, ValueError, RecursionError):
        raise BackupError("Falsches Passwort oder beschädigte Daten.")
    return secrets if isinstance(secrets, dict) else {}


def import_migration(data: bytes, password: str) -> dict:
    """Restore a migration package: data files and settings as with a backup, plus the credentials of the plugins
    from the encrypted part. First everything is checked and decrypted, then written – on a wrong password
    or a damaged package (BackupError) the installation stays unchanged."""
    ensure_dirs()
    with _open(data) as zf:
        infos = {i.filename: i for i in zf.infolist()}
        if "manifest.json" not in infos:
            raise BackupError("Kein gültiges Umzugspaket (manifest.json fehlt).")
        try:
            manifest = json.loads(_read_entry(zf, infos["manifest.json"]))
        except (ValueError, RecursionError):
            manifest = None
        if not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("source") != "comfyos":
            raise BackupError("Unbekanntes Paketformat.")
        files = _checked_files(zf, infos, "Paket")
        settings = _settings_from(zf, infos)
        secrets = _decrypt_secrets(_read_entry(zf, infos["secrets.enc"]), password) if "secrets.enc" in infos else None
    restored = _write_files(files)
    _apply_settings(settings, restored)
    if secrets is not None:
        _store_secrets(secrets)
        restored.append("secrets")
    return {"restored": restored, "source": "comfyos", "version": str(manifest.get("version") or "")[:20]}


def export_logs_zip() -> bytes:
    """Alle Logdateien (Heizverlauf, Strom, Ereignisse, Meldungen) als ZIP im Speicher."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if LOGS_DIR.is_dir():
            for sub in ("heating", "power", "events", "messages"):
                d = LOGS_DIR / sub
                if d.is_dir():
                    for f in sorted(d.glob("*.jsonl")):
                        zf.write(f, f"logs/{sub}/{f.name}")
    return buf.getvalue()
