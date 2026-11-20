"""Updates by upload or GitHub release."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import core, updater
from ..paths import APP_ROOT, app_version

router = APIRouter()


class UpdateApply(BaseModel):
    """Install an uploaded update package (`force`: an older version too)."""
    file: str = Field(min_length=1, max_length=200)
    force: bool = False


class UpdateGithub(BaseModel):
    """Update from GitHub: which version, install right away or only download."""
    tag: str | None = Field(default=None, max_length=64)
    install: bool = True
    force: bool = False


# ---------------------------------------------------------------- Update
@router.get("/api/update")
async def update_status():
    """Current version, folder of the installation, packages lying ready and backups of earlier versions."""
    return {"current": app_version(), "folder": str(APP_ROOT.parent), "candidates": updater.candidates(),
            "backups": updater.backups()}


@router.post("/api/update/upload")
async def update_upload(request: Request):
    """Upload an update package. It is limited in size on reception and checked afterwards – an unusable
    package is deleted at once."""
    target = APP_ROOT.parent / f"comfyos-upload-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    size = 0
    with open(target, "wb") as fh:
        async for chunk in request.stream():
            size += len(chunk)
            if size > updater.MAX_ZIP_BYTES:
                fh.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, "Datei zu groß.")
            fh.write(chunk)
    info = updater.inspect_zip(target)
    if not info.get("ok"):
        target.unlink(missing_ok=True)
        raise HTTPException(400, info.get("error") or "Kein gültiges ComfyOS-Paket.")
    info.pop("files", None)
    return info


@router.get("/api/update/github")
async def update_github_check():
    """Ask GitHub for a newer version (only at the user's request)."""
    try:
        return await asyncio.to_thread(updater.check_github)
    except updater.UpdateError as exc:
        raise HTTPException(502, str(exc))


@router.post("/api/update/github")
async def update_github_install(body: UpdateGithub):
    """Download a version from GitHub, check it and – if wanted – install it; ComfyOS restarts afterwards."""
    try:
        info = await asyncio.to_thread(updater.download_github, body.tag)
        if not body.install:
            return info
        result = await asyncio.to_thread(updater.apply, Path(info["path"]), body.force)
    except updater.UpdateError as exc:
        raise HTTPException(400, str(exc))
    core.restart_soon()
    return {**result, "release": info.get("tag"), "verified": info.get("verified"), "restarting": True}


@router.post("/api/update/apply")
async def update_apply(body: UpdateApply):
    """Install an uploaded package and restart. Of the request only the file name counts, never a path."""
    path = APP_ROOT.parent / Path(body.file).name
    try:
        result = await asyncio.to_thread(updater.apply, path, body.force)
    except updater.UpdateError as exc:
        raise HTTPException(400, str(exc))
    core.restart_soon()
    return {**result, "restarting": True}
