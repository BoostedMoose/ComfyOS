"""UI check of the widget view of the home page with Playwright (test world world_hue), since 0.24.

    python tests/ui/ui_widgets.py [path-to-comfyos]

Checks per browser (desktop/Chromium, iPhone/Chromium, iPhone/WebKit): the home page starts classic; the switch leads to
the widget view and the device remembers it across a reload; "Mit Vorschlag starten" fills the grid without overlaps
and without sticking out over the screen; the editor adds a widget from the catalogue, its settings apply at once; on
the desktop a widget is dragged to another cell and resized with the keys and the sidebar can be hidden; everything is saved on the server; a widget
switches a lamp through the handlers of the home page; the phone uses its own narrow grid; back to classic the floor
plan is there again. Screenshots in tests/shots/.
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import ui, world_hue as world  # noqa: E402

ROOT = world.ROOT
g = vars(world)        # mocks, app and helpers of the test world – the same as in tests/plugins/test_hue.py
main, auth = g["main"], g["auth"]

from app import core, floorplan_store, widgets_store  # noqa: E402

floorplan_store.save({"width": 24, "height": 14, "weather": False,
                      "walls": [{"id": "w1", "pts": [[7, 3], [17, 3], [17, 11], [7, 11], [7, 3]]},
                                {"id": "w2", "pts": [[12, 3], [12, 11]]}],
                      "rooms": [{"id": "r1", "name": "Küche", "room_id": "kueche", "x": 9.5, "y": 7},
                                {"id": "r2", "name": "Wohnzimmer", "room_id": "wohnen", "x": 14.5, "y": 7}],
                      "openings": [], "devices": {}})

from playwright.sync_api import sync_playwright  # noqa: E402

server, PORT = ui.serve(main.app)
mock = g["MockHB"]("https://qivicon", "", "x")
mock.room_names_provider = core.room_names
g["install_homebase"](mock)
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = ui.shots_dir("widgets-")
problems = []

# rectangles of all widgets, overlaps between them and whether anything sticks out over the screen
LAYOUT_JS = """() => {
  const rects = [...document.querySelectorAll('#wgHost .wg')].map(e => e.getBoundingClientRect())
  const hit = (a, b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1
  let overlaps = 0
  rects.forEach((a, i) => rects.slice(i + 1).forEach(b => { if (hit(a, b)) overlaps++ }))
  const host = document.querySelector('#wgHost').getBoundingClientRect()
  return { count: rects.length, overlaps, outside: rects.filter(r => r.left < host.left - 1 || r.right > host.right + 1).length,
    wide: document.documentElement.scrollWidth > window.innerWidth + 1, tiny: rects.filter(r => r.width < 60 || r.height < 40).length,
    empty: [...document.querySelectorAll('#wgHost .wg .wg-body')].filter(b => !b.innerHTML.trim()).length }
}"""
STORED_JS = "async () => (await HO.api('widgets')).pages"


def stored_when(page, ready, what, name):
    """The pages stored on the server as soon as `ready(pages)` holds (saving runs a moment after a change)."""
    pages = []
    for _ in range(40):
        pages = page.evaluate(STORED_JS)
        if ready(pages):
            return pages
        page.wait_for_timeout(150)
    problems.append(f"{name}: not saved on the server: {what}")
    return pages


def run(p, engine, name, **ctx_args):
    """One pass of the widget view in one browser; deviations end up in problems."""
    widgets_store.save({"pages": []})
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(**ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE + "/#home")
    # classic is the default; the switch is there and the classic view marked
    page.wait_for_selector("#fpHost .fp-wrap svg .fp-room", timeout=20000)
    if page.get_attribute('#viewBar [data-homeview="classic"]', "aria-selected") != "true" or page.locator("#wgHost").count():
        problems.append(f"{name}: the home page does not start in the classic view")
    page.click('#viewBar [data-homeview="widgets"]')
    page.wait_for_selector("#wgHost .wg-empty", timeout=8000)
    if page.evaluate("() => localStorage.getItem('ho.homeView')") != "widgets" or page.locator("#fpHost").count():
        problems.append(f"{name}: the widget view is not remembered or the classic view stays")
    page.screenshot(path=str(SHOTS / f"widgets-{name}-1-leer.png"))
    # suggestion: widgets that fit the home, on the grid without overlaps, nothing outside the screen
    page.click('#wgHost [data-wg-bar="starter"]')
    page.wait_for_selector("#wgHost.editing .wg .wg-edit", timeout=8000)
    page.wait_for_selector("#wgHost .wg-type-floorplan .fp-room", timeout=10000)
    lay = page.evaluate(LAYOUT_JS)
    if lay["count"] < 6 or lay["overlaps"] or lay["outside"] or lay["wide"] or lay["tiny"] or lay["empty"]:
        problems.append(f"{name}: suggestion laid out wrong: {lay}")
    narrow = page.evaluate("() => window.innerWidth < 700")
    mode = page.evaluate("() => document.querySelector('#wgHost .wg-grid').clientWidth < 700 ? 'narrow' : 'wide'")
    if (mode == "narrow") != narrow:
        problems.append(f"{name}: wrong grid for this width: {mode}")
    made = stored_when(page, lambda pages: len(pages) == 1 and len(pages[0]["widgets"]) == lay["count"], "the suggestion", name)
    page.screenshot(path=str(SHOTS / f"widgets-{name}-2-editor.png"), full_page=True)
    # add a note from the catalogue: its settings open at once and apply while typing
    page.click('#wgBar [data-wg-bar="add"]')
    page.wait_for_selector(".sheet .wg-cat-grid")
    groups = page.locator(".sheet .wg-cat-head").count()
    types = page.locator(".sheet .wg-cat").count()
    if groups != 4 or types != len(widgets_store.TYPES):
        problems.append(f"{name}: catalogue incomplete: {groups} groups, {types} widgets")
    page.locator(".sheet").screenshot(path=str(SHOTS / f"widgets-{name}-3-katalog.png"))
    page.click('.sheet .wg-cat[data-type="note"]')
    page.wait_for_selector('.sheet [data-f="text"]')
    page.fill('.sheet [data-f="text"]', "Müll: Dienstag <b>früh</b>")
    page.wait_for_function("() => [...document.querySelectorAll('#wgHost .wg-note')].some(n => n.textContent.includes('Dienstag <b>früh</b>'))")
    if page.locator("#wgHost .wg-note b").count():
        problems.append(f"{name}: text of a note is interpreted as HTML")
    page.select_option('.sheet [data-g="style"]', "glass")
    page.click('.sheet [data-tint="s3"]')
    page.wait_for_selector("#wgHost .wg-type-note.wg-s-glass.wg-t-s3")
    page.locator(".sheet").screenshot(path=str(SHOTS / f"widgets-{name}-4-einstellungen.png"))
    page.click(".sheet .sheet-foot .btn.primary")
    page.wait_for_selector(".sheet", state="detached")
    after = stored_when(page, lambda pages: any(w["type"] == "note" and w["opts"].get("text", "").startswith("Müll") and w["style"] == "glass"
                                                 and w["tint"] == "s3" for w in pages[0]["widgets"]), "the note with its settings", name)
    lay = page.evaluate(LAYOUT_JS)
    if lay["overlaps"] or lay["outside"] or lay["wide"]:
        problems.append(f"{name}: layout broken after adding a widget: {lay}")
    if not narrow:
        # desktop: drag the first widget three cells to the right – it snaps to the grid, the others make room
        first = made[0]["widgets"][0]["id"]
        page.evaluate("() => window.scrollTo(0, 0)")      # adding the note scrolled down to it
        page.wait_for_function("() => window.pageYOffset === 0")
        box = page.locator(f'#wgHost .wg[data-wg="{first}"]').bounding_box()
        pitch = page.evaluate("() => { const g = document.querySelector('#wgHost .wg-grid'); return (g.clientWidth + 12) / 12 }")
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + pitch * 3, box["y"] + box["height"] / 2, steps=8)
        page.mouse.up()
        moved = stored_when(page, lambda pages: next(w for w in pages[0]["widgets"] if w["id"] == first)["wide"]["x"] == 3, "the dragged widget", name)
        lay = page.evaluate(LAYOUT_JS)
        if lay["overlaps"] or lay["outside"]:
            problems.append(f"{name}: widgets overlap after dragging: {lay}")
        # keys: Shift+arrow makes the selected widget one cell wider
        width = next(w for w in moved[0]["widgets"] if w["id"] == first)["wide"]["w"]
        page.keyboard.press("Shift+ArrowRight")
        stored_when(page, lambda pages: next(w for w in pages[0]["widgets"] if w["id"] == first)["wide"]["w"] == width + 1, "the widened widget", name)
        page.keyboard.press("Escape")
        # the sidebar can be hidden in the widget view; the grid takes the room, and the device remembers it
        wide_before = page.evaluate("() => document.querySelector('#wgHost .wg-grid').clientWidth")
        page.click("#viewBar .nav-toggle")
        page.wait_for_function("() => getComputedStyle(document.querySelector('.sidebar')).display === 'none'")
        page.wait_for_function("(w) => document.querySelector('#wgHost .wg-grid').clientWidth > w", arg=wide_before)
        lay = page.evaluate(LAYOUT_JS)
        if lay["overlaps"] or lay["outside"] or page.evaluate("() => localStorage.getItem('ho.navHidden')") != "1":
            problems.append(f"{name}: hiding the sidebar breaks the layout or is not remembered: {lay}")
        page.click("#viewBar .nav-toggle")
        page.wait_for_function("() => getComputedStyle(document.querySelector('.sidebar')).display !== 'none'")
    else:
        if page.locator("#viewBar .nav-toggle").is_visible():
            problems.append(f"{name}: the button for the sidebar is shown on the phone")
        # phone: the wide arrangement is untouched by the automatic phone arrangement
        if any(w["narrow"] is not None for w in after[0]["widgets"] if w["type"] != "note"):
            problems.append(f"{name}: the phone arrangement was written down although nothing was moved there")
        if page.locator("#wgBar .wg-tool .lbl").first.is_visible():
            problems.append(f"{name}: the buttons of the editor carry labels on the phone")
    page.click('#wgBar [data-wg-bar="done"]')
    page.wait_for_selector("#wgHost:not(.editing)")
    if page.locator("#wgHost .wg-edit").count():
        problems.append(f"{name}: surfaces of the editor stay after „Fertig“")
    # a widget switches through the handlers of the home page: all lights off and on again
    button = page.locator("#wgHost .wg-type-lights .wg-power")
    if button.count() == 1:
        state = button.get_attribute("aria-pressed")
        button.click()
        page.wait_for_function("(was) => { const b = document.querySelector('#wgHost .wg-type-lights .wg-power'); return b && b.getAttribute('aria-pressed') !== was }",
                               arg=state, timeout=8000)
    else:
        problems.append(f"{name}: the widget „Licht“ is missing in the suggestion ({button.count()})")
    page.screenshot(path=str(SHOTS / f"widgets-{name}-5-ansicht.png"), full_page=True)
    # the view survives a reload (remembered on this device) and comes back from the server
    page.reload()
    page.wait_for_selector("#wgHost .wg .wg-body", timeout=20000)
    count = page.locator("#wgHost .wg").count()
    if count != len(after[0]["widgets"]) or page.locator("#fpHost").count():
        problems.append(f"{name}: after a reload {count} widgets instead of {len(after[0]['widgets'])} (or the classic view)")
    # back to classic: the floor plan is there again, the choice is remembered
    page.click('#viewBar [data-homeview="classic"]')
    page.wait_for_selector("#fpHost .fp-wrap svg .fp-room", timeout=10000)
    if page.evaluate("() => localStorage.getItem('ho.homeView')") != "classic" or page.locator("#wgHost").count():
        problems.append(f"{name}: back to the classic view does not work")
    print(f"{name}: {count} widgets, console errors: {len(errs)}")
    problems.extend(f"{name} Konsole: {e}" for e in errs)
    browser.close()


with sync_playwright() as p:
    for engine, name, args in ui.runs(p):
        run(p, engine, name, **args)

server.should_exit = True
print("\n".join(problems) if problems else "UI OK – no problems, no console errors")
