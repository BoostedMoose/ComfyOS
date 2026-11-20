"""Node.js for the helper program: find it, or download one fixed version and unpack only the binary.

The helper (bridge/dist/bridge.mjs) needs Node.js. ComfyOS itself has no Node and installs
nothing silently, so the tile offers a button "Einrichten". It downloads **one fixed version**
from nodejs.org, checks the SHA-256 sum that is written down in this file, and unpacks exactly
one file from the archive: the ``node`` binary. Nothing else from the archive touches the disk,
and nothing is ever executed during the installation.

Where Node is looked for, in this order:

1. ``<plugin folder>/runtime/``  – put there when ComfyOS is built as an image (no download needed)
2. ``<data folder>/runtime/``    – where "Einrichten" puts it

Each holds ``bin/node`` (``node.exe`` on Windows) and a file ``VERSION`` with the version it was
unpacked from. A different version in ``VERSION`` counts as "not installed": the helper is tested
with exactly :data:`NODE_VERSION` (its permission model must be stable).

Updating Node: change :data:`NODE_VERSION` and the sums in :data:`ARCHIVES` together
(https://nodejs.org/dist/v<version>/SHASUMS256.txt).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import platform
import shutil
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from plugin_api import PluginContext, PluginError

NODE_VERSION = "24.22.3"
EGRESS_KEY = "runtime"                      # see manifest.json -> egress
DOWNLOAD_HOST = "nodejs.org"
RUNTIME_FOLDER = "runtime"
VERSION_FILE = "VERSION"
MAX_DOWNLOAD_BYTES = 120 * 1024 * 1024
MAX_BINARY_BYTES = 400 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300.0


class RuntimeProblem(PluginError):
    """Node.js cannot be installed (no build for this system, download failed, wrong checksum)."""


@dataclass(frozen=True)
class Archive:
    """One download of Node.js for one system."""

    file_name: str                              # name on nodejs.org
    sha256: str                                 # from SHASUMS256.txt of the release
    binary: str                                 # path of the binary inside the archive
    megabytes: int                              # rough size of the download, for the hint in the tile


def _archive(system: str, extension: str, sha256: str, megabytes: int) -> Archive:
    """Describe the archive of :data:`NODE_VERSION` for one system (e.g. ``linux-arm64``)."""
    folder = f"node-v{NODE_VERSION}-{system}"
    binary = f"{folder}/node.exe" if system.startswith("win-") else f"{folder}/bin/node"
    return Archive(f"{folder}.{extension}", sha256, binary, megabytes)


# (operating system, processor) -> archive. There is no official build for 32-bit ARM.
ARCHIVES: dict[tuple[str, str], Archive] = {
    ("linux", "arm64"): _archive("linux-arm64", "tar.xz", "6e51a2ada00f0011cc266f65d98922b403d7ad88f809ca2aa615a5fe1ce6e86c", 30),
    ("linux", "x64"): _archive("linux-x64", "tar.xz", "4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb", 31),
    ("darwin", "arm64"): _archive("darwin-arm64", "tar.gz", "ff6e17d4874b76d272212b908cc020d6e815fba5a6c7eaaa06add725840307fd", 50),
    ("darwin", "x64"): _archive("darwin-x64", "tar.gz", "977614af0504c81045e7fd85596dbca0e5f951b96681516dadafedcca8c447f7", 52),
    ("win32", "x64"): _archive("win-x64", "zip", "e612af99f626ab415ea225af6bce5c971feb448cb5cae250bb1c3fa50c5f7d9f", 36),
    ("win32", "arm64"): _archive("win-arm64", "zip", "b3cc74a295b39d4f273271e96f58c06dd2b35757ca12abec64c7d0598b434f14", 32),
}
_PROCESSORS = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64", "amd64": "x64"}


def system_key() -> tuple[str, str]:
    """Return (operating system, processor) of this machine in the terms of :data:`ARCHIVES`.

    A 64-bit ARM kernel with a 32-bit system (older Raspberry Pi OS) reports as ``arm32``: the
    64-bit build of Node does not run there.
    """
    system = "linux" if sys.platform.startswith("linux") else sys.platform
    processor = _PROCESSORS.get(platform.machine().lower(), platform.machine().lower())
    if processor == "arm64" and sys.maxsize <= 2 ** 32:
        processor = "arm32"
    return system, processor


def archive_for(key: tuple[str, str] | None = None) -> Archive | None:
    """Return the archive for a system (default: this one), or None when Node offers no build for it."""
    return ARCHIVES.get(key or system_key())


def download_url(archive: Archive) -> str:
    """Return the address of an archive on nodejs.org."""
    return f"https://{DOWNLOAD_HOST}/dist/v{NODE_VERSION}/{archive.file_name}"


def binary_in(base: Path, system: str | None = None) -> Path:
    """Return where the Node binary lies below ``base`` (a plugin or data folder)."""
    is_windows = (system or sys.platform) == "win32"
    return base / RUNTIME_FOLDER / ("node.exe" if is_windows else "bin/node")


def installed_in(base: Path) -> Path | None:
    """Return the Node binary below ``base`` if it is there in exactly :data:`NODE_VERSION`, else None."""
    binary = binary_in(base)
    try:
        version = (base / RUNTIME_FOLDER / VERSION_FILE).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return binary if version == NODE_VERSION and binary.is_file() else None


def find(plugin_dir: Path, data_dir: Path) -> Path | None:
    """Return the Node binary to use – shipped with the plugin (image) or installed by the user – or None."""
    return installed_in(plugin_dir) or installed_in(data_dir)


def verify(content: bytes, archive: Archive) -> None:
    """Raise RuntimeProblem unless ``content`` has the SHA-256 sum written down for the archive."""
    if hashlib.sha256(content).hexdigest() != archive.sha256:
        raise RuntimeProblem("Die Prüfsumme des Downloads stimmt nicht – Node.js wurde nicht installiert.")


def _open_binary(content: bytes, archive: Archive) -> tuple[io.BufferedIOBase, object]:
    """Open exactly the binary inside the archive for reading. Returns (stream, archive object to close).

    The member is looked up by its full expected name; nothing is extracted by a name the archive
    supplies, so a path like ``../../x`` in a manipulated archive is never written anywhere.
    """
    if archive.file_name.endswith(".zip"):
        package = zipfile.ZipFile(io.BytesIO(content))
        try:
            info = package.getinfo(archive.binary)
        except KeyError:
            package.close()
            raise RuntimeProblem("Im Download fehlt das Programm node.")
        if info.is_dir() or info.file_size > MAX_BINARY_BYTES:
            package.close()
            raise RuntimeProblem("Im Download fehlt das Programm node.")
        return package.open(info), package
    bundle = tarfile.open(fileobj=io.BytesIO(content), mode="r:*")
    for member in bundle:
        if member.name == archive.binary:
            stream = bundle.extractfile(member) if member.isreg() and member.size <= MAX_BINARY_BYTES else None
            if stream is None:
                break
            return stream, bundle
    bundle.close()
    raise RuntimeProblem("Im Download fehlt das Programm node.")


def unpack(content: bytes, archive: Archive, base: Path, system: str | None = None) -> Path:
    """Write the Node binary from a verified archive to ``base/runtime`` and note the version next to it.

    The binary is written under a temporary name and moved into place, so an interrupted
    installation never leaves a half-written program. Returns the path of the binary.
    """
    target = binary_in(base, system)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    stream, package = _open_binary(content, archive)
    try:
        with open(temporary, "wb") as handle:
            shutil.copyfileobj(stream, handle)
    finally:
        stream.close()
        package.close()                         # type: ignore[attr-defined]
    os.chmod(temporary, 0o755)
    os.replace(temporary, target)
    (base / RUNTIME_FOLDER / VERSION_FILE).write_bytes(NODE_VERSION.encode("ascii") + b"\n")
    return target


async def download(context: PluginContext, archive: Archive) -> bytes:
    """Download an archive from nodejs.org through the plugin's declared connection. Raises RuntimeProblem."""
    chunks: list[bytes] = []
    received = 0
    try:
        async with context.http(EGRESS_KEY, timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=False) as client:
            async with client.stream("GET", download_url(archive)) as response:
                if response.status_code != 200:
                    raise RuntimeProblem(f"nodejs.org antwortet mit Status {response.status_code}.")
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > MAX_DOWNLOAD_BYTES:
                        raise RuntimeProblem("Der Download ist größer als erwartet – abgebrochen.")
                    chunks.append(chunk)
    except PluginError:
        raise
    except Exception as error:                  # network errors of the HTTP client
        raise RuntimeProblem(f"nodejs.org ist nicht erreichbar ({type(error).__name__}).")
    return b"".join(chunks)


async def install(context: PluginContext, data_dir: Path) -> Path:
    """Download Node.js for this system, check it and unpack the binary into the plugin's data folder.

    Raises RuntimeProblem when there is no build for this system, the download fails or the
    checksum is wrong. Returns the path of the binary.
    """
    archive = archive_for()
    if archive is None:
        raise RuntimeProblem(unsupported_text())
    content = await download(context, archive)
    verify(content, archive)
    try:
        return await asyncio.to_thread(unpack, content, archive, data_dir)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise RuntimeProblem(f"Node.js ließ sich nicht entpacken ({type(error).__name__}).")


def unsupported_text() -> str:
    """Return the sentence the tile shows when Node offers no build for this system."""
    system, processor = system_key()
    return (f"Für dieses System ({system}, {processor}) gibt es keine Ausgabe von Node.js {NODE_VERSION}. "
            "Matter braucht ein 64-Bit-System.")
