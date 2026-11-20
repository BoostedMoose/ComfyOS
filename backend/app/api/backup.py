"""Backup & Restore API and log export – Basic since 0.24.5: nobody should lose their data because of the plan."""
from __future__ import annotations

import asyncio
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .. import backup_store

router = APIRouter()


@router.get("/api/backups")
async def list_backups():
    """Existing backups."""
    return {"backups": await asyncio.to_thread(backup_store.list_backups)}


@router.post("/api/backups")
async def create_backup():
    """Create a backup now."""
    return await asyncio.to_thread(backup_store.create_backup)


@router.get("/api/backups/{filename}")
async def download_backup(filename: str):
    """Download a backup. The file name is checked against a fixed pattern in the store."""
    data = await asyncio.to_thread(backup_store.backup_zip_bytes, filename)
    if data is None:
        raise HTTPException(404, "Backup nicht gefunden")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/api/backups/restore")
async def restore_backup(request: Request):
    """Restore an uploaded backup. Unusable archives give 400, nothing has been written then."""
    data = await request.body()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "Datei zu groß (max. 50 MB)")
    try:
        result = await asyncio.to_thread(backup_store.restore_backup, data)
    except backup_store.BackupError as exc:          # own text for the user
        raise HTTPException(400, f"Backup konnte nicht wiederhergestellt werden: {exc}")
    except Exception as exc:                         # everything else: only the kind of the error, never its text
        raise HTTPException(400, f"Backup konnte nicht wiederhergestellt werden ({type(exc).__name__}).")
    return result


@router.delete("/api/backups/{filename}")
async def delete_backup(filename: str):
    """Delete a backup."""
    ok = await asyncio.to_thread(backup_store.delete_backup, filename)
    if not ok:
        raise HTTPException(404, "Backup nicht gefunden")
    return {"ok": True}


@router.post("/api/backups/migrate/export")
async def migrate_export(request: Request):
    """Create and download a migration package. The password from the request encrypts the credentials of the
    plugins in the package."""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON-Objekt erwartet.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Ungültige Anfrage.")
    password = body.get("password")
    if not isinstance(password, str) or not password.strip():
        raise HTTPException(400, "Passwort wird benötigt.")
    try:
        data = await asyncio.to_thread(backup_store.export_migration, password.strip()[:200])
    except backup_store.BackupError as exc:          # own text for the user (password too short)
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Umzugspaket konnte nicht erstellt werden ({type(exc).__name__}).")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="comfyos-migration.zip"'})


@router.post("/api/backups/migrate/import")
async def migrate_import(request: Request):
    """Restore a migration package. The password is URL-encoded in the header `X-Migration-Password`; on a wrong
    password or a damaged package the installation stays unchanged."""
    data = await request.body()
    if not data or len(data) < 4:
        raise HTTPException(400, "ZIP-Datei erwartet.")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "Datei zu groß (max. 50 MB)")
    # The password comes URL-encoded in the header: headers only carry Latin-1, a password may contain any character.
    password = unquote(request.headers.get("x-migration-password", ""))[:200]
    if not password:
        raise HTTPException(400, "Passwort fehlt (Header X-Migration-Password).")
    try:
        result = await asyncio.to_thread(backup_store.import_migration, data, password)
    except backup_store.BackupError as exc:          # own text for the user
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"Import fehlgeschlagen ({type(exc).__name__}).")
    return result


@router.get("/api/logs/export")
async def export_logs():
    """Download all logs as a ZIP."""
    data = await asyncio.to_thread(backup_store.export_logs_zip)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="comfyos-logs.zip"'})
