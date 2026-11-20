"""Updater: installs a comfyos.zip that lies next to the program folder.

The source is either a ZIP placed/uploaded by hand or the newest
GitHub release (see check_github / download_github). A release is only queried at the
push of a button – otherwise ComfyOS does not talk to the internet.

    …/comfyos/          <- installed app (APP_ROOT)
    …/comfyos.zip       <- new version (also "comfyos (1).zip", "comfyos-0.9.0.zip")

Procedure:
  1. Check the ZIP: contains backend/app/main.py and VERSION, no unsafe paths.
  2. Unpack into a staging folder.
  3. Back up the current app files as comfyos-backup-<version>-<time>.zip
     (the last 3 are kept).
  4. Delete outdated app files (according to the old MANIFEST list), copy new ones.
     Never touched: data/, logs/, .venv/ as well as settings, credentials and
     login, which live outside the program folder anyway.
  5. If requirements.txt changed: pip install.
  6. Rename the ZIP to comfyos-<version>.installed.zip; restart.
If step 4 fails, the backup is played back.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from . import events, logstore
from .paths import APP_ROOT, MANIFEST_FILE, PRESERVED_DIRS, app_version

MAX_ZIP_BYTES = 80 * 1024 * 1024
GITHUB_REPO = os.getenv("COMFYOS_UPDATE_REPO", "boostedmoose/comfyos")
GITHUB_API = os.getenv("COMFYOS_UPDATE_API", "https://api.github.com").rstrip("/")
_ASSET_RE = re.compile(r"^comfyos-\d+\.\d+\.\d+[\w.-]*\.zip$", re.I)
KEEP_BACKUPS = 3
_JUNK_PARTS = {"__pycache__", ".pytest_cache", ".git", ".github", "node_modules"}
# Build folders at the root. dist/ folders further down belong to the program: the bundled helper program
# of a plugin lives there (public/plugins/matter/bridge/dist/bridge.mjs).
_JUNK_TOP = {"dist"}
_JUNK_SUFFIXES = (".pyc", ".pyo", ".tmp")
_SECRET_SUFFIXES = (".env", ".comfyos-license")   # secrets/licences: never package, back up or delete


class UpdateError(RuntimeError):
    """An update is not possible – the text is meant for the user."""
    pass


def parse_version(v: str | None) -> tuple[int, int, int]:
    """Version "1.2.3" as a triple of numbers for comparing (missing places count as 0)."""
    nums = [int(x) for x in re.findall(r"\d+", v or "")[:3]]
    return tuple(nums + [0] * (3 - len(nums)))  # type: ignore[return-value]


def _is_preserved(rel: str) -> bool:
    """Does the file belong to what an update never touches: data folders, temporary files, key and
    secret files?"""
    parts = PurePosixPath(rel).parts
    return bool(parts) and (parts[0] in PRESERVED_DIRS or parts[0] in _JUNK_TOP or any(p in _JUNK_PARTS for p in parts)
                            or rel.endswith(_JUNK_SUFFIXES) or rel.lower().endswith(_SECRET_SUFFIXES))


def _is_safe(rel: str) -> bool:
    """Is this a harmless relative path – without a drive, without a leading slash, without ".."?"""
    if not rel or rel.startswith(("/", "\\")) or ":" in rel or "\\" in rel:
        return False
    return ".." not in PurePosixPath(rel).parts


def _app_prefix(names: list[str]) -> str | None:
    """Folder in the ZIP that holds ComfyOS ("" or "name/"), recognised by backend/app/main.py – or None if it is not
    a ComfyOS package."""
    for n in names:
        if n.endswith("backend/app/main.py"):
            prefix = n[: -len("backend/app/main.py")]
            if prefix == "" or prefix.count("/") == 1:
                return prefix
    return None


def inspect_zip(path: Path) -> dict[str, Any]:
    """Check an update package without unpacking it: size, structure, version, file list and harmless paths. `ok` says
    whether it can be installed; otherwise `error` names the reason."""
    info: dict[str, Any] = {"file": path.name, "path": str(path), "ok": False, "version": None,
                            "size": path.stat().st_size if path.exists() else 0}
    try:
        if info["size"] > MAX_ZIP_BYTES:
            raise UpdateError("ZIP ist zu groß.")
        if not zipfile.is_zipfile(path):
            raise UpdateError("Keine gültige ZIP-Datei.")
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            prefix = _app_prefix(names)
            if prefix is None:
                raise UpdateError("Kein ComfyOS-Paket (backend/app/main.py fehlt).")
            files = []
            for n in names:
                if not n.startswith(prefix) or n.endswith("/"):
                    continue
                rel = n[len(prefix):]
                if not _is_safe(rel):
                    raise UpdateError(f"Unsicherer Pfad im ZIP: {rel}")
                if not _is_preserved(rel):
                    files.append(rel)
            try:
                version = zf.read(prefix + "VERSION").decode("utf-8").strip()
            except KeyError:
                raise UpdateError("VERSION-Datei fehlt im Paket.")
        info.update(ok=True, version=version, prefix=prefix, files=sorted(files),
                    newer=parse_version(version) > parse_version(app_version()))
    except (UpdateError, zipfile.BadZipFile, OSError) as exc:
        info["error"] = str(exc)
    return info


def candidates() -> list[dict[str, Any]]:
    """Update packages lying next to the program folder – usable and newer ones first."""
    parent = APP_ROOT.parent
    out = []
    for p in sorted(parent.glob("comfyos*.zip")):
        name = p.name.lower()
        if ".installed" in name or name.startswith("comfyos-backup"):
            continue
        info = inspect_zip(p)
        info.pop("files", None)
        info["mtime"] = p.stat().st_mtime
        out.append(info)
    out.sort(key=lambda i: (i.get("ok", False), parse_version(i.get("version")), i.get("mtime", 0)), reverse=True)
    return out


def backups() -> list[dict[str, Any]]:
    """Backups of earlier program versions next to the program folder, newest first."""
    out = []
    for p in sorted(APP_ROOT.parent.glob("comfyos-backup-*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        out.append({"file": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime})
    return out


def scan_app_files(root: Path) -> set[str]:
    """All program files under `root` (relative paths), without what an update never touches."""
    files = set()
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if not _is_preserved(rel):
                files.add(rel)
    return files


def current_files() -> set[str]:
    """Files of the installed version: according to MANIFEST, otherwise everything except data."""
    try:
        listed = {ln.strip() for ln in MANIFEST_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()}
        return {f for f in listed if _is_safe(f) and not _is_preserved(f)} | {"MANIFEST"}
    except OSError:
        return scan_app_files(APP_ROOT)


_RELEASE_SKIP_DIRS = {"tests", "docs", "dev", "captures", ".claude", ".vscode", ".idea"}
_RELEASE_SKIP_SUFFIXES = (".md", ".har", ".env", ".comfyos-license")
# The licence tool and the owner's secrets never belong in a package.
_RELEASE_SKIP_FILES = {"tools/mint_gift.py", "tools/build_secrets.env.example", "license.json"}


def release_files(root: Path) -> set[str]:
    """Content of an update package: the app without tests, docs, captures, editor/agent configuration
    and without the licence tool and its secrets."""
    return {f for f in scan_app_files(root)
            if not set(PurePosixPath(f).parts) & _RELEASE_SKIP_DIRS and not f.lower().endswith(_RELEASE_SKIP_SUFFIXES)
            and f not in _RELEASE_SKIP_FILES}


def write_manifest(root: Path) -> list[str]:
    """Write the file MANIFEST: the list of all files of a delivery. By it a later update recognises which
    files have been dropped."""
    files = sorted(release_files(root) | {"MANIFEST"})
    (root / "MANIFEST").write_text("\n".join(files) + "\n", encoding="utf-8")
    return files


def _sha(path: Path) -> str | None:
    """SHA-256 of a file, or None if it is unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _make_backup(files: set[str]) -> Path:
    """Back up the affected program files as a ZIP next to the program folder; only the newest KEEP_BACKUPS
    backups stay."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = APP_ROOT.parent / f"comfyos-backup-{app_version()}-{stamp}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(files):
            src = APP_ROOT / rel
            if src.is_file():
                zf.write(src, "comfyos/" + rel)
    old = sorted(APP_ROOT.parent.glob("comfyos-backup-*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)
    for extra in old[KEEP_BACKUPS:]:
        try:
            extra.unlink()
        except OSError:
            pass   # cannot be deleted right now: the next run tries again
    return target


def _restore(backup: Path) -> None:
    """Write program files back from a backup (after a failed update)."""
    with zipfile.ZipFile(backup) as zf:
        for n in zf.namelist():
            if n.endswith("/") or not n.startswith("comfyos/"):
                continue
            rel = n[len("comfyos/"):]
            if not _is_safe(rel):
                continue
            dst = APP_ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(zf.read(n))


def _cleanup_dirs() -> None:
    """Clean up after an update: remove Python caches and folders that have become empty – never in the
    protected folders."""
    for p in sorted(APP_ROOT.rglob("__pycache__"), reverse=True):
        rel = p.relative_to(APP_ROOT).as_posix()
        if PurePosixPath(rel).parts[0] in PRESERVED_DIRS:
            continue
        shutil.rmtree(p, ignore_errors=True)
    for p in sorted((d for d in APP_ROOT.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
        rel = p.relative_to(APP_ROOT).as_posix()
        if PurePosixPath(rel).parts[0] in PRESERVED_DIRS:
            continue
        try:
            p.rmdir()  # only if empty
        except OSError:
            pass   # not empty: stays


def _pip_install(requirements: Path) -> dict[str, Any]:
    """Install Python packages from requirements.txt. The result is written to the message log; an error does not
    abort the update."""
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        ok = proc.returncode == 0
        logstore.append("messages", {"type": "update", "message": "pip install " + ("ok" if ok else "fehlgeschlagen"),
                                     "output": (proc.stdout + proc.stderr)[-4000:]})
        return {"ran": True, "ok": ok}
    except Exception as exc:
        logstore.append("messages", {"type": "update", "message": f"pip install nicht möglich: {exc}"})
        return {"ran": True, "ok": False, "error": str(exc)}


def apply(zip_path: Path, force: bool = False) -> dict[str, Any]:
    """Install an update: check the package, unpack into an intermediate folder, back up affected files, write new
    ones, delete dropped ones, install packages. If anything goes wrong, the backup comes back. Raises
    UpdateError with the reason."""
    zip_path = Path(zip_path)
    if zip_path.parent.resolve() != APP_ROOT.parent.resolve():
        raise UpdateError("Update-Pakete werden nur neben dem Programmordner akzeptiert.")
    info = inspect_zip(zip_path)
    if not info.get("ok"):
        raise UpdateError(info.get("error") or "Paket ungültig.")
    old_version = app_version()
    if not force and parse_version(info["version"]) < parse_version(old_version):
        raise UpdateError(f"Paket {info['version']} ist älter als die installierte Version {old_version}.")

    staging = Path(tempfile.mkdtemp(prefix=".comfyos-update-", dir=APP_ROOT.parent))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for rel in info["files"]:
                dst = staging / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(zf.read(info["prefix"] + rel))
        if not (staging / "backend" / "app" / "main.py").is_file():
            raise UpdateError("Entpacktes Paket ist unvollständig.")

        old_files = current_files()
        new_files = set(info["files"])
        req_old = _sha(APP_ROOT / "backend" / "requirements.txt")
        req_new = _sha(staging / "backend" / "requirements.txt")
        backup = _make_backup(old_files)

        removed = written = 0
        try:
            for rel in sorted(old_files - new_files):
                target = APP_ROOT / rel
                if target.is_file():
                    target.unlink()
                    removed += 1
            for rel in sorted(new_files):
                dst = APP_ROOT / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                # write next to it first, then swap atomically: running start
                # scripts thus keep reading their old file instead of garbage
                tmp = dst.with_name(dst.name + ".comfyos-new")
                shutil.copy2(staging / rel, tmp)
                os.replace(tmp, dst)
                written += 1
            _cleanup_dirs()
            (APP_ROOT / "MANIFEST").write_text("\n".join(sorted(new_files | {"MANIFEST"})) + "\n", encoding="utf-8")
        except Exception as exc:
            _restore(backup)
            raise UpdateError(f"Update abgebrochen, alter Stand wiederhergestellt: {exc}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    done = zip_path.with_name(f"comfyos-{info['version']}.installed.zip")
    if done.exists():
        done = zip_path.with_name(f"comfyos-{info['version']}-{int(time.time())}.installed.zip")
    try:
        zip_path.rename(done)
    except OSError:
        pass   # cannot be renamed: the package stays under its name

    pip = {"ran": False}
    if req_new and req_new != req_old:
        pip = _pip_install(APP_ROOT / "backend" / "requirements.txt")

    msg = f"Update {old_version} → {info['version']}: {written} Dateien, {removed} veraltete entfernt"
    events.log("update", msg)
    logstore.append("messages", {"type": "update", "message": msg, "backup": backup.name})
    return {"ok": True, "from": old_version, "to": info["version"], "written": written,
            "removed": removed, "backup": backup.name, "pip": pip}


# ---------------------------------------------------------------- GitHub-Releases
def _gh_headers() -> dict[str, str]:
    """Headers for requests to GitHub."""
    return {"Accept": "application/vnd.github+json", "User-Agent": f"ComfyOS-Updater/{app_version()}",
            "X-GitHub-Api-Version": "2022-11-28"}


def _pick_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    """The ComfyOS package among the files of a GitHub release, or None."""
    for a in release.get("assets") or []:
        if _ASSET_RE.match(a.get("name") or ""):
            return a
    return None


def check_github() -> dict[str, Any]:
    """Queries the newest release (metadata only, downloads nothing)."""
    import httpx  # only here: that way make_release.py (CI) needs no dependencies
    from . import egress
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/releases/latest"
    try:
        with egress.sync_client("updates", headers=_gh_headers(), timeout=15, follow_redirects=True) as client:
            r = client.get(url)
    except httpx.HTTPError as exc:
        raise UpdateError(f"GitHub nicht erreichbar: {exc}")
    if r.status_code == 404:
        raise UpdateError("Noch kein Release veröffentlicht.")
    if r.status_code == 403:
        raise UpdateError("GitHub-Abfragelimit erreicht – bitte später erneut versuchen.")
    if r.status_code != 200:
        raise UpdateError(f"GitHub antwortet mit HTTP {r.status_code}.")
    rel = r.json()
    tag = str(rel.get("tag_name") or "")
    version = tag.lstrip("vV")
    asset = _pick_asset(rel)
    current = app_version()
    return {"repo": GITHUB_REPO, "tag": tag, "version": version, "name": rel.get("name") or tag,
            "notes": (rel.get("body") or "")[:8000], "published": rel.get("published_at"),
            "url": rel.get("html_url"), "current": current,
            "newer": parse_version(version) > parse_version(current),
            "asset": {"name": asset["name"], "size": asset.get("size", 0)} if asset else None}


def download_github(tag: str | None = None) -> dict[str, Any]:
    """Downloads the package of the (newest) release next to the program folder and checks it."""
    import httpx
    from . import egress
    if tag and not re.fullmatch(r"[\w.-]{1,64}", tag):
        raise UpdateError("Ungültiger Release-Name.")
    path = f"tags/{tag}" if tag else "latest"
    try:
        with egress.sync_client("updates", headers=_gh_headers(), timeout=60, follow_redirects=True) as client:
            r = client.get(f"{GITHUB_API}/repos/{GITHUB_REPO}/releases/{path}")
            if r.status_code != 200:
                raise UpdateError(f"Release nicht gefunden (HTTP {r.status_code}).")
            rel = r.json()
            asset = _pick_asset(rel)
            if not asset:
                raise UpdateError("Das Release enthält kein comfyos-<version>.zip.")
            if (asset.get("size") or 0) > MAX_ZIP_BYTES:
                raise UpdateError("Release-Paket ist zu groß.")
            digest = str(asset.get("digest") or "")
            target = APP_ROOT.parent / Path(asset["name"]).name
            tmp = target.with_name(target.name + ".part")
            h = hashlib.sha256()
            size = 0
            try:
                with client.stream("GET", asset["browser_download_url"],
                                   headers={"Accept": "application/octet-stream"}) as resp:
                    if resp.status_code != 200:
                        raise UpdateError(f"Download fehlgeschlagen (HTTP {resp.status_code}).")
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_bytes(65536):
                            size += len(chunk)
                            if size > MAX_ZIP_BYTES:
                                raise UpdateError("Release-Paket ist zu groß.")
                            h.update(chunk)
                            fh.write(chunk)
                if digest.startswith("sha256:") and digest[7:].lower() != h.hexdigest():
                    raise UpdateError("Prüfsumme des Downloads stimmt nicht.")
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
    except httpx.HTTPError as exc:
        raise UpdateError(f"GitHub nicht erreichbar: {exc}")
    info = inspect_zip(target)
    if not info.get("ok"):
        target.unlink(missing_ok=True)
        raise UpdateError(info.get("error") or "Release-Paket ungültig.")
    info.pop("files", None)
    info.update(tag=rel.get("tag_name"), sha256=h.hexdigest(), verified=digest.startswith("sha256:"))
    logstore.append("messages", {"type": "update", "message": f"Release {info['tag']} geladen: {target.name}"})
    return info
