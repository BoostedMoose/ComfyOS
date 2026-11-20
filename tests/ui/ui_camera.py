"""UI test of the floor plan camera (since 0.20): pan, zoom and reset smoothly – in the view and in full screen.

    1  view (home page, without full screen): + zooms in, Ctrl+mouse wheel zooms around the pointer, the mouse wheel alone
       scrolls the page, dragging pans, "Ansicht zurücksetzen" restores the overall view
    2  panning does not repaint: during the gesture the viewBox stays, the image moves along by a transform
    3  tapping in the zoomed plan still opens the device; dragging opens nothing
    4  full screen: the mouse wheel zooms around the pointer, dragging pans, "Ganzen Grundriss zeigen" resets
    5  two fingers zoom in the view (touch); one finger on the plan that is not zoomed does not change it
    6  when idle the floor plan is not rebuilt although fresh data keeps arriving
    7  toolbar on the phone: one line, can be pushed sideways – the tile does not get higher

    python tests/ui/ui_camera.py [path-to-comfyos]

Runs in Chromium (two-finger gestures go through the DevTools protocol there) on the desktop and as a touch device.
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import ui  # noqa: E402
from support import world_demo as demo  # noqa: E402
from support.world_demo import MockHB, auth, httpx, install_homebase, main  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

server, PORT = ui.serve(main.app)
install_homebase(MockHB("https://qivicon", "", "x"))
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
api = httpx.Client(base_url=BASE, cookies={auth.COOKIE: token}, timeout=15)
demo.steady(api)
problems = []

BOX = "sel => (document.querySelector(sel).getAttribute('viewBox') || '').split(' ').map(Number)"
# count changes to the viewBox and rebuilds of the SVG
WATCH = """sel => {
  const svg = document.querySelector(sel)
  window.__cam = { boxes: 0, builds: 0 }
  new MutationObserver(list => list.forEach(m => {
    if (m.type === 'attributes') window.__cam.boxes++
    else window.__cam.builds++
  })).observe(svg, { attributes: true, attributeFilter: ['viewBox'], childList: true })
}"""
COUNTS = "() => Object.assign({}, window.__cam)"


def check(ok, text):
    """Check: on failure into the list of problems."""
    if not ok:
        problems.append(text)


def open_page(browser, **ctx_args):
    """Open the home page in a new context; console errors are collected."""
    ctx = browser.new_context(**ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and "/api/scenes/image/" not in (m.location or {}).get("url", "") else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_selector("#fpHost .fp-svg")
    page.wait_for_timeout(1200)
    return ctx, page, errs


def drag(page, box, dx, dy, steps=24):
    """Drag with the mouse held; returns the measurements from the middle of the gesture."""
    x, y = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5
    page.mouse.move(x, y)
    page.mouse.down()
    mid = None
    for i in range(1, steps + 1):
        page.mouse.move(x + dx * i / steps, y + dy * i / steps)
        page.wait_for_timeout(10)
        if i == steps // 2:
            mid = page.evaluate("""() => ({ moving: !!document.querySelector('.fp-wrap.fp-moving'),
              transform: getComputedStyle(document.querySelector('.fp-wrap.fp-moving .fp-svg') || document.body).transform })""")
    page.mouse.up()
    page.wait_for_timeout(350)
    return mid


def desktop(p):
    """Sections 1 to 4 and 6 with the mouse."""
    browser = p.chromium.launch()
    ctx, page, errs = open_page(browser, viewport={"width": 1366, "height": 900})
    plan = "#fpHost .fp-svg"
    page.locator(plan).scroll_into_view_if_needed()
    home = page.evaluate(BOX, plan)

    # 6) idle: three seconds with fresh data (a lamp does not switch) – no rebuild, no new viewBox
    page.evaluate(WATCH, plan)
    page.wait_for_timeout(3200)
    idle = page.evaluate(COUNTS)
    check(idle["builds"] == 0 and idle["boxes"] == 0, f"6 the floor plan rebuilds itself when idle: {idle}")

    # 1) zoom in, pan, reset
    check(page.locator('[data-act="fpFit"]').is_visible(), "1 the button 'Ansicht zurücksetzen' is missing above the floor plan")
    for _ in range(3):
        page.click('[data-act="fpZoomIn"]')
        page.wait_for_timeout(120)
    zoomed = page.evaluate(BOX, plan)
    check(zoomed[2] < home[2] * 0.65, f"1 + does not zoom in: {home} -> {zoomed}")
    page.evaluate(WATCH, plan)
    mid = drag(page, page.locator(plan).bounding_box(), -160, -90)
    moved, counts = page.evaluate(BOX, plan), page.evaluate(COUNTS)
    check(moved[0] > zoomed[0] + 0.5 and moved[1] > zoomed[1] + 0.2 and abs(moved[2] - zoomed[2]) < 0.01,
          f"1 dragging does not pan the section: {zoomed} -> {moved}")
    # 2) during the gesture: transform instead of viewBox; at the end at most two writes, no rebuild
    check(mid and mid["moving"] and mid["transform"] not in ("none", ""), f"2 no transform while dragging: {mid}")
    check(counts["boxes"] <= 2 and counts["builds"] == 0, f"2 panning repaints: {counts}")
    check(page.locator(".fp-wrap.fp-moving").count() == 0 and page.evaluate("s => document.querySelector(s).style.transform", plan) == "",
          "2 the transform stays after the gesture")

    # 3) tapping in the zoomed plan opens the device; dragging has opened nothing
    check(page.locator(".sheet-bg").count() == 0, "3 dragging opened a dialog")
    page.click('[data-act="fpFit"]')
    page.wait_for_timeout(250)
    back = page.evaluate(BOX, plan)
    check(all(abs(a - b) < 0.01 for a, b in zip(back, home)), f"1 'Ansicht zurücksetzen' does not restore the overall view: {home} / {back}")
    # 1) the mouse wheel alone scrolls the page and leaves the plan alone; with Ctrl it zooms around the pointer
    spot = page.locator(plan).bounding_box()
    page.mouse.move(spot["x"] + spot["width"] * 0.5, spot["y"] + spot["height"] * 0.5)
    y0 = page.evaluate("() => scrollY")
    page.mouse.wheel(0, 120)
    page.wait_for_timeout(300)
    check(page.evaluate("() => scrollY") > y0 and all(abs(a - b) < 0.01 for a, b in zip(page.evaluate(BOX, plan), home)),
          "1 the mouse wheel alone no longer scrolls the page or changes the plan")
    page.mouse.wheel(0, -120)
    page.wait_for_timeout(300)
    spot = page.locator(plan).bounding_box()
    page.mouse.move(spot["x"] + spot["width"] * 0.5, spot["y"] + spot["height"] * 0.5)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -60)
    page.wait_for_timeout(50)
    page.mouse.wheel(0, -60)
    page.keyboard.up("Control")
    page.wait_for_timeout(450)
    ctrl = page.evaluate(BOX, plan)
    check(ctrl[2] < home[2] * 0.85, f"1 Ctrl+mouse wheel does not zoom in the view: {home} -> {ctrl}")
    page.click('[data-act="fpFit"]')
    page.wait_for_timeout(250)
    page.click('[data-act="fpZoomIn"]')
    page.wait_for_timeout(250)
    dev = page.locator('#fpHost .fp-dev[data-kind="dev"]:not(.docked)').first
    dev.click()
    try:
        page.wait_for_selector(".sheet-bg .sheet", timeout=3000)
    except Exception:
        problems.append("3 tapping a device in the zoomed plan opens nothing")
    if page.locator(".sheet-bg").count():
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        if page.locator(".sheet-bg").count():
            page.mouse.click(5, 5)
            page.wait_for_timeout(300)

    # 4) Vollbild
    page.evaluate("() => { location.hash = '#plan' }")
    page.wait_for_selector(".pv .fp-svg")
    page.wait_for_timeout(1000)
    full = ".pv .fp-svg"
    box = page.locator(".pv .fp-wrap").bounding_box()
    start = page.evaluate(BOX, full)
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.4)
    page.mouse.wheel(0, -400)
    page.wait_for_timeout(500)
    wheeled = page.evaluate(BOX, full)
    check(wheeled[2] < start[2] * 0.75, f"4 the mouse wheel does not zoom: {start} -> {wheeled}")
    # the point under the pointer stays (30 % / 40 % of the surface)
    under = lambda b: (b[0] + b[2] * 0.3, b[1] + b[3] * 0.4)   # noqa: E731
    check(abs(under(start)[0] - under(wheeled)[0]) < 0.15 and abs(under(start)[1] - under(wheeled)[1]) < 0.15,
          f"4 zooming with the mouse wheel does not keep the point under the pointer: {under(start)} / {under(wheeled)}")
    page.evaluate(WATCH, full)
    mid = drag(page, box, 220, 120, steps=30)
    panned, counts = page.evaluate(BOX, full), page.evaluate(COUNTS)
    check(panned[0] < wheeled[0] - 0.5 and abs(panned[2] - wheeled[2]) < 0.01, f"4 dragging does not pan in full screen: {wheeled} -> {panned}")
    check(mid and mid["moving"] and counts["boxes"] <= 2 and counts["builds"] == 0, f"4 panning in full screen repaints: {mid} {counts}")
    page.click('[data-act="pvFit"]')
    page.wait_for_timeout(300)
    fitted = page.evaluate(BOX, full)
    check(all(abs(a - b) < 0.05 for a, b in zip(fitted, start)), f"4 'Ganzen Grundriss zeigen' does not reset: {start} / {fitted}")
    problems.extend(f"desktop Konsole: {e}" for e in errs)
    print(f"desktop: view {home[2]:.1f} -> {zoomed[2]:.1f} units wide, full screen {start[2]:.1f} -> {wheeled[2]:.1f};"
          f"writes while dragging: {counts['boxes']}, console errors: {len(errs)}")
    browser.close()


def touch(p):
    """Sections 5 and 7 as a touch device (phone size)."""
    browser = p.chromium.launch()
    ctx, page, errs = open_page(browser, viewport={"width": 390, "height": 760}, device_scale_factor=2, has_touch=True, is_mobile=True)
    plan = "#fpHost .fp-svg"
    # 7) toolbar: one line, and what does not fit can be reached sideways
    tools = page.evaluate("""() => { const t = document.querySelector('.card-head .fp-tools'), last = t.lastElementChild
      const before = last.getBoundingClientRect().right > t.getBoundingClientRect().right + 1
      t.scrollLeft = t.scrollWidth
      const after = last.getBoundingClientRect().right <= t.getBoundingClientRect().right + 1
      t.scrollLeft = 0
      const tops = new Set([...t.children].filter(c => c.offsetParent).map(c => Math.round(c.getBoundingClientRect().top)))
      return { height: Math.round(t.getBoundingClientRect().height), rows: tops.size, wider: t.scrollWidth > t.clientWidth, before, after,
               count: [...t.children].filter(c => c.offsetParent).length } }""")
    check(tools["rows"] == 1 and tools["height"] <= 40, f"7 the toolbar wraps: {tools}")
    check(not tools["wider"] or tools["after"], f"7 tools at the end of the bar cannot be reached: {tools}")
    page.locator(plan).scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    box = page.locator(plan).bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    home = page.evaluate(BOX, plan)
    cdp = ctx.new_cdp_session(page)

    def fingers(kind, points):
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(points)]})

    # 5) one finger, short horizontal movement on the plan that is not zoomed: the plan stays as it is
    fingers("touchStart", [(cx, cy)])
    for i in range(1, 6):
        fingers("touchMove", [(cx + i * 6, cy)])
        page.wait_for_timeout(16)
    fingers("touchEnd", [])
    page.wait_for_timeout(300)
    still = page.evaluate(BOX, plan)
    check(all(abs(a - b) < 0.01 for a, b in zip(still, home)), f"5 one finger changes the plan that is not zoomed: {home} -> {still}")
    check(page.locator(".sheet-bg").count() == 0, "5 swiping over the plan opens a dialog")
    # spreading two fingers: zooms in, and the page does not scroll meanwhile
    scroll_before = page.evaluate("() => scrollY")
    fingers("touchStart", [(cx - 30, cy), (cx + 30, cy)])
    for i in range(1, 13):
        fingers("touchMove", [(cx - 30 - i * 6, cy - i), (cx + 30 + i * 6, cy + i)])
        page.wait_for_timeout(16)
    during = page.evaluate("() => !!document.querySelector('.fp-wrap.fp-moving')")
    fingers("touchEnd", [])
    page.wait_for_timeout(400)
    pinched = page.evaluate(BOX, plan)
    check(pinched[2] < home[2] * 0.7, f"5 two fingers do not zoom: {home} -> {pinched} (gesture recognised: {during})")
    check(abs(page.evaluate("() => scrollY") - scroll_before) < 4, "5 the page scrolls during the two-finger gesture")
    check(page.locator(".fp-wrap.fp-moving").count() == 0, "5 the plan stays in motion after the two-finger gesture")
    # now one finger pans the zoomed plan
    fingers("touchStart", [(cx, cy)])
    for i in range(1, 11):
        fingers("touchMove", [(cx - i * 8, cy - i * 3)])
        page.wait_for_timeout(16)
    fingers("touchEnd", [])
    page.wait_for_timeout(400)
    panned = page.evaluate(BOX, plan)
    check(panned[0] > pinched[0] + 0.3, f"5 one finger does not pan the zoomed plan: {pinched} -> {panned}")
    page.evaluate("() => document.querySelector('[data-act=fpFit]').click()")
    page.wait_for_timeout(300)
    check(all(abs(a - b) < 0.01 for a, b in zip(page.evaluate(BOX, plan), home)), "5 resetting after the two-finger gesture does not restore the overall view")
    problems.extend(f"touch Konsole: {e}" for e in errs)
    print(f"touch: bar with {tools['count']} tools in {tools['rows']} line, can be pushed: {tools['wider']};"
          f"two fingers {home[2]:.1f} -> {pinched[2]:.1f} units wide, console errors: {len(errs)}")
    browser.close()


with sync_playwright() as p:
    desktop(p)
    touch(p)

server.should_exit = True
time.sleep(0.2)
print("\n".join(problems) if problems else "UI OK – no problems, no console errors")
