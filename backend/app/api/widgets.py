"""Widget view of the home page: read and save the pages with their widgets (since 0.24)."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import entitlements, widgets_store

router = APIRouter()


class WidgetPages(BaseModel):
    """The whole widget view: every page with its widgets (checked and limited in widgets_store.clean)."""
    pages: list[dict] = Field(max_length=widgets_store.MAX_PAGES)


def _out(data: dict) -> dict:
    """Document for the interface: the pages plus the limits the editor shows and whether an old file was set aside."""
    return {**data, "outdated": widgets_store.outdated(),
            "limits": {"pages": widgets_store.MAX_PAGES, "widgets": widgets_store.MAX_WIDGETS}}


@router.get("/api/widgets")
async def get_widgets():
    """The stored widget view."""
    return _out(widgets_store.get())


@router.put("/api/widgets")
async def put_widgets(body: WidgetPages):
    """Save the widget view. Before that the licence checks whether the change is within the plan (further pages and
    widgets that show a Pro feature) – what is already there stays editable and deletable."""
    new = widgets_store.clean({"pages": body.pages})
    entitlements.check_widgets(widgets_store.get(), new)
    return _out(widgets_store.save(new))
