"""General settings, accent colour, overall state of the connections and heating sync.

The connection to the Home Base and the Magenta sync have belonged to the plugin `qivicon` since 0.19
(routes under /api/plugins/qivicon/…).
"""
from __future__ import annotations

import re

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import auth, core, entitlements, heating_sync
from ..paths import app_version
from ..settings_store import KEYS, clean_columns, load_settings, save_settings, secret_backend

router = APIRouter()


@router.get("/api/settings")
async def get_settings():
    """Settings for the page System: known fields only, plus where the secrets are stored, version and
    sessions."""
    cfg = {k: v for k, v in load_settings().items() if k in KEYS}     # only what ComfyOS knows – never foreign keys of the file
    return {**cfg, "secret_backend": secret_backend(), "version": app_version(), "sessions": auth.session_count(),
            "session_idle_days": auth.SESSION_DAYS}


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@router.put("/api/settings/accent")
async def put_accent(body: dict):
    """Set the accent colour (empty = default). An own colour is a Pro feature."""
    color = body.get("color")
    color = color.strip() if isinstance(color, str) else ""
    if color and not _HEX_RE.match(color):
        raise HTTPException(400, "Ungültige Farbe (erwartet: #rrggbb).")
    if color:
        entitlements.gate("custom_accent")
    cfg = load_settings()
    cfg["accent_color"] = color or None      # None (not pop): otherwise save_settings would take over the old value
    saved = save_settings(cfg)
    return {"ok": True, "accent_color": saved.get("accent_color")}


class TileLayout(BaseModel):
    """Arrangement of the tiles of a page: number of columns and tiles per column."""
    page: Literal["system", "plugins", "home"]
    cols: int = Field(ge=1, le=3)
    columns: list[list[str]] | None = Field(default=None, max_length=3)    # None = Anordnung vergessen (automatisch)


@router.put("/api/settings/layout")
async def put_layout(body: TileLayout):
    """Arrangement of the tiles of a page for one number of columns (since 0.19.2, display only)."""
    cfg = load_settings()
    layout = cfg.get("layout") if isinstance(cfg.get("layout"), dict) else {}
    page = dict(layout.get(body.page) or {})
    if body.columns is None:
        page.pop(str(body.cols), None)
    else:
        if sum(len(c) for c in body.columns) > 200:
            raise HTTPException(400, "Zu viele Kacheln.")
        cols = clean_columns(body.columns, body.cols)
        if cols is None:
            raise HTTPException(400, "Spalten passen nicht zur Spaltenzahl.")
        page[str(body.cols)] = cols
    layout[body.page] = page
    cfg["layout"] = layout
    saved = save_settings(cfg)
    return {"ok": True, "layout": saved.get("layout", {}).get(body.page, {})}


@router.get("/api/sync/status")
async def sync_status():
    """State of the heating sync (plugin with `syncs_heating`, e.g. the Magenta sync)."""
    return heating_sync.public_status()


@router.post("/api/sync")
async def sync_now(force: bool = False):
    """Trigger the heating sync now (`force`: also without pending changes)."""
    return await heating_sync.sync(force=force)


@router.get("/api/status")
async def status():
    """Overall state of the configured plugins (connection dot in the header)."""
    return core.system_status()
