"""Scaffolding of the UI tests (Playwright, runs on Windows only – see CLAUDE.md, section Testing).

    server, port = ui.serve(main.app)      # app in the background on a free port
    for engine, name, args in ui.runs(p):  # the three usual runs: desktop, iPhone/Chromium, iPhone/WebKit
        ...

Every run gets its own port – that way UI tests can run side by side without disturbing each other.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

SHOTS = Path(__file__).resolve().parents[1] / "shots"      # tests/shots/ (gitignored)
DESKTOP = {"viewport": {"width": 1366, "height": 900}}
PHONE = "iPhone 13"


def free_port() -> int:
    """Einen freien TCP-Port auf dem eigenen Rechner erfragen."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def serve(app):
    """Start the app with uvicorn in a background thread. Returns (server, port); it is ended with
    `server.should_exit = True`."""
    import uvicorn

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    return server, port


def runs(playwright, only: str | None = None):
    """The three usual browser runs as (engine, name, context arguments). `only` (or the environment variable
    UI_ONLY) restricts to one run: desktop, mobil or safari."""
    only = only or os.environ.get("UI_ONLY")
    phone = playwright.devices[PHONE]
    for engine, name, args in (("chromium", "desktop", DESKTOP), ("chromium", "mobil", phone), ("webkit", "safari", phone)):
        if not only or only == name:
            yield engine, name, dict(args)


COMPARE_JS = """async ([a, b, tol]) => {
  const load = src => new Promise((ok, bad) => {
    const img = new Image(); img.onload = () => ok(img); img.onerror = () => bad(new Error('Bild nicht lesbar'))
    img.src = 'data:image/png;base64,' + src })
  const [ia, ib] = await Promise.all([load(a), load(b)])
  if (ia.width !== ib.width || ia.height !== ib.height) return { size: [ia.width, ia.height, ib.width, ib.height] }
  const w = ia.width, h = ia.height
  const pixels = img => { const c = document.createElement('canvas'); c.width = w; c.height = h
    const x = c.getContext('2d'); x.drawImage(img, 0, 0); return x.getImageData(0, 0, w, h).data }
  const da = pixels(ia), db = pixels(ib)
  const out = document.createElement('canvas'); out.width = w; out.height = h
  const ctx = out.getContext('2d'); ctx.globalAlpha = 0.35; ctx.drawImage(ib, 0, 0); ctx.globalAlpha = 1
  ctx.fillStyle = 'rgb(255, 0, 60)'
  let diff = 0, x0 = w, y0 = h, x1 = -1, y1 = -1
  for (let i = 0, p = 0; i < da.length; i += 4, p++) {
    if (Math.abs(da[i] - db[i]) > tol || Math.abs(da[i + 1] - db[i + 1]) > tol || Math.abs(da[i + 2] - db[i + 2]) > tol) {
      diff++
      const x = p % w, y = (p - x) / w
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y
      ctx.fillRect(x, y, 1, 1)
    }
  }
  return { diff, total: w * h, box: diff ? [x0, y0, x1, y1] : null, image: diff ? out.toDataURL('image/png').split(',')[1] : null }
}"""


def compare_images(page, ref: Path, now: Path, diff_to: Path | None = None, tolerance: int = 8) -> dict:
    """Compare two PNG images pixel by pixel – in the browser (canvas), without an additional dependency.

    `page` is an empty page of any browser. Result: {"diff": differing pixels, "total": all,
    "box": [x0, y0, x1, y1] of the differences} or {"size": [...]} for a different image size. `tolerance` =
    allowed difference per colour channel (anti-aliasing). With `diff_to` an image is created that shows the differences in red.
    """
    import base64

    res = page.evaluate(COMPARE_JS, [base64.b64encode(ref.read_bytes()).decode("ascii"),
                                     base64.b64encode(now.read_bytes()).decode("ascii"), tolerance])
    image = res.pop("image", None)
    if image and diff_to is not None:
        diff_to.parent.mkdir(parents=True, exist_ok=True)
        diff_to.write_bytes(base64.b64decode(image))
    return res


def shots_dir(prefix: str | None = None) -> Path:
    """Folder for screenshots; with `prefix` old images of this test are removed first."""
    SHOTS.mkdir(exist_ok=True)
    if prefix:
        for old in SHOTS.glob(f"{prefix}*.png"):
            old.unlink()
    return SHOTS
