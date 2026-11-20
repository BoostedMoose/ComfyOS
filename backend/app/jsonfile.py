"""Read and write JSON files – one way for all stores (since 0.20).

Reading:   If the file is missing, the default comes back. If it is there but unreadable (cut off, not JSON,
           wrong type), the default comes back as well – before that, the file is kept as a copy
           (`<name>.defekt-<time><suffix>` in the same folder). Otherwise the store would silently overwrite it
           the next time it saves, and whatever could still be rescued from it would be gone.
Writing:   atomic – first `<file>.tmp`, then replace. If writing is interrupted, the old file stays in place.

Nothing is converted: the copy is a lifeline for the user, not a migration. ComfyOS never reads it again.
Every store still checks and limits its own data (`_clean`); this module only deals with the file.

Demo (since 0.23): for a session in the demo, files under data/ are read and written in data/demo/ (demo.path) –
the real files of the user stay untouched.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import demo

MAX_KEPT = 3                                      # at most this many kept copies per file
_kept: dict[Path, tuple[float, int]] = {}         # copy already kept per file (modification time, size) – read() runs often
_noting = False                                   # the event log writes files itself: never report in a circle


def read(path: Path, default: Any = None, *, expect: type | tuple[type, ...] | None = dict, keep: bool = True) -> Any:
    """Content of a JSON file. `default` if it is missing or unreadable.

    `expect` is the type the store expects (default: an object). Valid JSON of the wrong type – a list where an
    object is required – counts as a damaged file. `expect=None` accepts any type.
    `keep=False` does not keep an unreadable file: for caches that refill by themselves, and for
    files ComfyOS never overwrites.
    """
    path = demo.path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except (OSError, UnicodeDecodeError):
        text = None
    try:
        data = json.loads(text) if text is not None else None
        readable = text is not None and (expect is None or isinstance(data, expect))
    except (ValueError, RecursionError):
        readable = False
    if readable:
        return data
    if keep:
        keep_broken(path)
    return default


def write(path: Path, data: Any, *, indent: int | None = 1, ensure_ascii: bool = False, private: bool = False,
          durable: bool = False) -> None:
    """Write data as JSON atomically.

    `indent`/`ensure_ascii` as in `json.dumps` – every store thereby keeps its former notation byte for byte.
    `private` sets the permissions to 0600 (login, secrets). `durable` waits until the data really is on the
    storage medium – for important files that are written rarely; frequently written ones go without (SD card).
    """
    path = demo.path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=ensure_ascii, indent=indent))
        if durable:
            handle.flush()
            os.fsync(handle.fileno())
    if private:
        _chmod_private(tmp)
    tmp.replace(path)
    if private:
        _chmod_private(path)


def _chmod_private(path: Path) -> None:
    """Only the ComfyOS user may read and write (no effect on Windows)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass   # file system without Unix permissions: there the protection comes from the user profile


def keep_broken(path: Path) -> None:
    """Keep an unreadable file as a copy – once per version, at most MAX_KEPT copies. Never raises.

    `read()` calls this itself. A store calls it directly when the file is valid JSON but its content is
    unusable (example: secrets.json with a value that cannot be decrypted)."""
    try:
        stat = path.stat()
        if stat.st_size == 0:
            return                                   # empty file: nothing to rescue
        version = (stat.st_mtime, stat.st_size)
        if _kept.get(path) == version:
            return
        _kept[path] = version
        pattern = f"{path.stem}.defekt-*{path.suffix}"
        copies = sorted(path.parent.glob(pattern))
        if copies and copies[-1].stat().st_size == stat.st_size and copies[-1].read_bytes() == path.read_bytes():
            return                                   # after a restart: this version is already there as a copy
        copy = path.with_name(f"{path.stem}.defekt-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
        shutil.copyfile(path, copy)
        _chmod_private(copy)                         # always private: the file may contain secrets (secrets.json)
        for old in sorted(path.parent.glob(pattern))[:-MAX_KEPT]:
            old.unlink()
    except OSError:
        return                                       # keeping a copy is an extra – reading must never fail because of it
    _note(path, copy)


def _note(path: Path, copy: Path) -> None:
    """Record in the event log that a file was unreadable and where its copy is."""
    global _noting
    if _noting:
        return
    _noting = True
    try:
        from . import events

        events.log("system", f"{path.name} war nicht lesbar – ComfyOS arbeitet ohne diese Daten weiter. "
                             f"Die Datei liegt als Kopie daneben: {copy.name}")
    except Exception:
        pass   # the log entry is only a hint; nothing is lost without it
    finally:
        _noting = False
