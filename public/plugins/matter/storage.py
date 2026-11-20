"""Storage of matter.js, kept by the plugin: one encrypted file in the plugin's data folder.

The helper program cannot write files. matter.js therefore reports every change of its storage
(fabrics, keys, sessions, endpoint numbers, what it knows about commissioned devices) to the
plugin, and gets everything back when it starts.

The file holds the keys that make ComfyOS a member of the user's Matter fabrics. It is encrypted
with AES-GCM; the key lives in the secret store of ComfyOS (``storage_key``), never next to the
file. Writes are bundled (many small changes become one write) and atomic (a new file replaces
the old one), so a crash never leaves half a file.

File layout: ``MAGIC`` (4 bytes) + nonce (12 bytes) + AES-GCM(zlib(JSON)).
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import zlib
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .protocol import NAMESPACES, StorageOp

MAGIC = b"CMS1"                             # ComfyOS Matter Storage, format 1
KEY_BYTES = 32
NONCE_BYTES = 12
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_STORED_BYTES = 32 * 1024 * 1024         # all values together; more is refused instead of filling the disk

Stored = dict[str, dict[str, dict[str, str]]]   # {namespace: {context: {key: json text}}}


class StorageError(Exception):
    """The storage file cannot be read (wrong key, damaged) or would grow beyond its limit."""


def new_key() -> str:
    """Return a fresh random key as text, ready for the secret store."""
    return base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii")


def _key_bytes(key: str) -> bytes:
    """Decode a key from the secret store. Raises StorageError if it is not a key."""
    try:
        raw = base64.urlsafe_b64decode(str(key).encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise StorageError("key is not usable")
    if len(raw) != KEY_BYTES:
        raise StorageError("key is not usable")
    return raw


def encrypt(stored: Stored, key: str) -> bytes:
    """Return the file content for ``stored``, encrypted with ``key``."""
    plain = zlib.compress(json.dumps(stored, separators=(",", ":")).encode("utf-8"))
    nonce = secrets.token_bytes(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(_key_bytes(key)).encrypt(nonce, plain, MAGIC)


def decrypt(content: bytes, key: str) -> Stored:
    """Return what a storage file holds. Raises StorageError for a wrong key or a damaged file."""
    if len(content) < len(MAGIC) + NONCE_BYTES + 16 or not content.startswith(MAGIC):
        raise StorageError("not a storage file")
    nonce = content[len(MAGIC):len(MAGIC) + NONCE_BYTES]
    try:
        plain = AESGCM(_key_bytes(key)).decrypt(nonce, content[len(MAGIC) + NONCE_BYTES:], MAGIC)
        raw = json.loads(zlib.decompress(plain).decode("utf-8"))
    except (InvalidTag, zlib.error, ValueError):
        raise StorageError("wrong key or damaged file")
    return _checked(raw)


def _checked(raw: Any) -> Stored:
    """Keep only the agreed structure: known namespaces, text keys, text values."""
    stored: Stored = {}
    if not isinstance(raw, dict):
        raise StorageError("unexpected content")
    for namespace in NAMESPACES:
        contexts = raw.get(namespace)
        if not isinstance(contexts, dict):
            continue
        for context, values in contexts.items():
            if isinstance(context, str) and isinstance(values, dict):
                clean = {key: value for key, value in values.items() if isinstance(key, str) and isinstance(value, str)}
                stored.setdefault(namespace, {})[context] = clean
    return stored


class MatterStore:
    """The storage of both matter.js nodes in memory, loaded from and saved to one encrypted file."""

    def __init__(self, path: Path, key: str) -> None:
        """Use ``path`` as the file and ``key`` (from the secret store) to encrypt it. Nothing is read yet."""
        self.path = path
        self._key = key
        self._stored: Stored = {}
        self._size = 0
        self.dirty = False

    # -- reading ------------------------------------------------------------

    def load(self) -> None:
        """Read the file. A missing file is an empty store. Raises StorageError if it cannot be decrypted."""
        try:
            if self.path.stat().st_size > MAX_FILE_BYTES:
                raise StorageError("storage file is too large")
            content = self.path.read_bytes()
        except FileNotFoundError:
            self._stored, self._size, self.dirty = {}, 0, False
            return
        except OSError:
            raise StorageError("storage file is not readable")
        self._stored = decrypt(content, self._key)
        self._size = sum(len(value) for contexts in self._stored.values() for values in contexts.values()
                         for value in values.values())
        self.dirty = False

    @property
    def stored(self) -> Stored:
        """Everything stored, as ``{namespace: {context: {key: json text}}}``."""
        return self._stored

    @property
    def is_empty(self) -> bool:
        """True when matter.js has stored nothing yet (a fresh installation)."""
        return not any(values for contexts in self._stored.values() for values in contexts.values())

    # -- changing -----------------------------------------------------------

    def apply(self, ops: Iterable[StorageOp]) -> None:
        """Apply storage changes of matter.js in order. Raises StorageError when the store would exceed its limit."""
        for op in ops:
            contexts = self._stored.setdefault(op.namespace, {})
            if op.action == "set" and op.key is not None and op.value is not None:
                values = contexts.setdefault(op.context, {})
                self._size += len(op.value) - len(values.get(op.key, ""))
                if self._size > MAX_STORED_BYTES:
                    raise StorageError("storage limit reached")
                values[op.key] = op.value
            elif op.action == "delete" and op.key is not None:
                removed = contexts.get(op.context, {}).pop(op.key, None)
                self._size -= len(removed or "")
            elif op.action == "clear":
                self._clear(contexts, op.context)
            self.dirty = True

    def _clear(self, contexts: dict[str, dict[str, str]], context: str) -> None:
        """Remove a context and everything below it (``a.b`` also removes ``a.b.c``); an empty name removes all."""
        prefix = context + "." if context else ""
        for name in [name for name in contexts if name == context or name.startswith(prefix)]:
            self._size -= sum(len(value) for value in contexts[name].values())
            del contexts[name]

    def forget(self, namespace: str) -> None:
        """Drop everything one node has stored (the user resets the bridge or the controller)."""
        if self._stored.pop(namespace, None) is not None:
            self.dirty = True
        self._size = sum(len(value) for contexts in self._stored.values() for values in contexts.values()
                         for value in values.values())

    # -- writing ------------------------------------------------------------

    def save(self) -> bool:
        """Write the file if something changed: encrypt, write next to it, then replace atomically.

        Returns True when a file was written. Runs in a worker thread (it blocks on the disk).
        """
        if not self.dirty:
            return False
        content = encrypt(self._stored, self._key)
        self.dirty = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with open(temporary, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        return True

    def set_aside(self) -> Path | None:
        """Move an unreadable file out of the way (kept as ``….unreadable``) so a fresh store can start."""
        if not self.path.exists():
            return None
        target = self.path.with_name(self.path.name + ".unreadable")
        os.replace(self.path, target)
        self._stored, self._size, self.dirty = {}, 0, False
        return target
