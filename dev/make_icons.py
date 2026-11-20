"""Regenerate the app icons (PNG) from the SVG templates in backend/app/static/img – with Playwright/Chromium.

    python dev/make_icons.py [path-to-comfyos]

- apple-touch-icon.png (180×180): borderless and opaque (iOS rounds it itself; transparency would turn black)
- icon-192.png / icon-512.png: logo with round corners (favicon.svg)
- icon-maskable-512.png: borderless with a safe zone (icon-maskable.svg)
- favicon-32.png
Run again after changes to the logo. Checks at the end that every PNG has the expected size and is not empty.
"""
import struct
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
IMG = ROOT / "backend" / "app" / "static" / "img"
JOBS = [("apple-touch-icon.png", "icon-maskable.svg", 180, False),
        ("icon-192.png", "favicon.svg", 192, True),
        ("icon-512.png", "favicon.svg", 512, True),
        ("icon-maskable-512.png", "icon-maskable.svg", 512, False),
        ("favicon-32.png", "favicon.svg", 32, True)]


def png_size(data: bytes) -> tuple[int, int]:
    """Read width and height of a PNG from its header."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "kein PNG"
    return struct.unpack(">II", data[16:24])


with sync_playwright() as p:
    browser = p.chromium.launch()
    for out, src, size, transparent in JOBS:
        svg = (IMG / src).read_text(encoding="utf-8")
        page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
        page.set_content(f"<!doctype html><html><body style='margin:0;background:transparent'>"
                         f"<div style='width:{size}px;height:{size}px'>{svg.replace('<svg ', f'<svg width=\"{size}\" height=\"{size}\" ', 1)}</div>"
                         f"</body></html>")
        data = page.screenshot(clip={"x": 0, "y": 0, "width": size, "height": size}, omit_background=transparent)
        page.close()
        assert png_size(data) == (size, size), (out, png_size(data))
        assert len(data) > 300, (out, len(data))
        (IMG / out).write_bytes(data)
        print(f"{out}: {size}×{size}, {len(data)} Bytes aus {src}")
    browser.close()
