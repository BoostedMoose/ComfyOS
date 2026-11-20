"""UI image comparison: every page of the interface looks as in the reference state (Playwright against the demo home).

The test photographs every page on the desktop (Chromium) and on the iPhone (WebKit) and compares the images pixel by
pixel with reference images. That proves that a rebuild changed nothing about the appearance.

    python tests/ui/ui_visual.py [path-to-comfyos] --save-ref   # create reference images from the current state
    python tests/ui/ui_visual.py [path-to-comfyos]              # compare the current state with the reference images

Reference images live in tests/shots/ref/ (never in the repo). If they are missing, the test skips itself. For the
comparison with an older version: check out its state on the Pi (`git worktree add <folder> <tag>`) and start this test
with the path of the checkout and --save-ref. Then the app of the old state runs with the procedure and the test world
of today; the images end up in tests/shots/ref of this repo.

So that two captures of the same state are equal: fixed clock in the browser, fixed weather, no weather effects (they
move and are random – ui_home checks them), animations paused while photographing. What depends on the time or
the machine of the run is fixed in the image or hidden: times of the events, folder of the
installation, values of the system load; for the dialog the page behind it stands in a fixed place, without pointer
and focus. This can be checked with two reference runs in a row – their images must be equal.
Deviations above GRENZE (share of the pixels) are an error; the image for it is in tests/shots/diff/.
"""
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import ui  # noqa: E402

SAVE = "--save-ref" in sys.argv
REF, NOW, DIFF = ui.SHOTS / "ref", ui.SHOTS / "visual", ui.SHOTS / "diff"
GRENZE = 0.001                              # at most 0.1 % deviating pixels per image
if not SAVE and not any(REF.glob("*.png")):
    print("SKIPPED: no reference images in tests/shots/ref – create them from the reference state with --save-ref")
    print("UI OK – image comparison skipped")
    sys.exit(0)

from support import world_demo as demo  # noqa: E402
from support.world_demo import WEISS, MockHB, auth, floorplan_store, httpx, install_homebase, main, weather  # noqa: E402
from app import routines_store  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

FIXED = datetime(2026, 11, 18, 12, 0, 0)    # clock in the browser: greeting, date, position of the sun and shadows depend on it
HEAD = ".room-card .rc-head"                # head of the first room tile – it opens the dialog for the image
CENTER_JS = "sel => document.querySelector(sel).scrollIntoView({ block: 'center' })"
SETTLE_JS = """([sel, need]) => {
  const el = document.querySelector(sel)
  if (!el) return false
  const key = Math.round(el.getBoundingClientRect().top + window.scrollY) + ':' + document.documentElement.scrollHeight
  if (window.__settleKey !== key) { window.__settleKey = key; window.__settleCount = 0; return false }
  return ++window.__settleCount >= need
}"""


def settle(page, selector: str, need: int = 8) -> None:
    """Wait until an element has found its place: its position in the document and the height of the page have stayed the
    same for `need` looks 100 ms apart. Looks are counted, not time – the clock in the browser stands still."""
    page.wait_for_function(SETTLE_JS, arg=[selector, need], polling=100, timeout=20000)


def calm_weather(request):
    """Fixed weather without today's time of day: fair, little wind."""
    if request.url.host.startswith("geocoding"):
        return httpx.Response(200, json={"results": [{"name": "Berlin", "latitude": 52.52, "longitude": 13.41,
                                                      "country_code": "DE", "postcodes": ["10115"]}]})
    return httpx.Response(200, json={
        "current": {"time": "2026-11-18T12:00", "temperature_2m": 9.4, "apparent_temperature": 8.1,
                    "relative_humidity_2m": 70, "weather_code": 2, "is_day": 1, "cloud_cover": 30,
                    "wind_speed_10m": 6, "wind_gusts_10m": 9, "wind_direction_10m": 245},
        "daily": {"temperature_2m_max": [11.2], "temperature_2m_min": [5.1]}})


weather._client = lambda timeout=6.0: httpx.AsyncClient(transport=httpx.MockTransport(calm_weather))
floorplan_store.save({**floorplan_store.get(), "fx": False})

server, PORT = ui.serve(main.app)        # eigener freier Port je Lauf
install_homebase(MockHB("https://qivicon", "", "x"))
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
api = httpx.Client(base_url=BASE, cookies={auth.COOKIE: token}, timeout=15)
assert api.post("/api/plugins/hue/pair", json={"host": "192.0.2.10"}).json().get("connected")
api.post("/api/scenes/abendrot/apply", json={"room_ids": ["wohnen"]})
api.post(f"/api/devices/{WEISS}/command", json={"state": {"on": False}})   # all states visible: one lamp off
routine = routines_store.create("Winter")
routines_store.update_schedule(routine["key"], [
    {"room": room, "day": day, "points": [{"time": "06:30", "temp": 21}, {"time": "22:00", "temp": 17}]}
    for room in ("wohnen", "kueche") for day in range(7)])
routines_store.set_active(routine["key"])
nodes, edges = demo.demo_flow()
flow = api.post("/api/flows", json={"name": "Licht bei Bewegung", "description": "Beispiel", "nodes": nodes, "edges": edges})
assert flow.status_code == 200, flow.text
FLOW_ID = flow.json()["id"]

# name, jump there, what is waited for, wait time afterwards (ms), whole page?
PAGES = [
    ("start", "#home", ".room-card .rc-head", 1500, True),
    ("grundriss", "#plan", ".pv .fp-svg", 1200, False),
    ("szenen", "#light", ".section-head", 1200, True),
    ("heizplan", "#heating", ".section-head", 1200, True),
    ("flow-liste", "#flow", ".section-head", 800, True),
    ("flow-editor", None, ".fl-node", 1200, False),
    ("ereignisse", "#events", ".section, .card", 800, True),
    ("system", "#settings", "#sysGrid", 3500, True),       # the tiles redistribute in the first seconds
    ("plugins", "#settings?tab=plugins", "[data-plugin]", 3500, True),
]
problems, taken = [], []


def run(p, engine, name, **ctx_args):
    """Photograph all pages in one browser."""
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(reduced_motion="reduce", **ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    page.clock.set_fixed_time(FIXED)
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and "/api/scenes/image/" not in (m.location or {}).get("url", "") else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_selector(".room-card .rc-head")
    # New and intended since 0.20: the button "Ansicht zurücksetzen" above the floor plan. For the comparison with older
    # reference images it is hidden – everything else is compared.
    # Also new: the link to the diagnosis page at the end of the System page.
    page.add_style_tag(content='[data-act="fpFit"], .diag-link { display: none !important }')
    target = REF if SAVE else NOW
    target.mkdir(parents=True, exist_ok=True)
    for label, hash_, wait, pause, full in PAGES:
        if hash_ is None:
            page.evaluate("id => { HO.state.flowId = id; location.hash = 'flowEdit' }", FLOW_ID)
        else:
            page.evaluate("h => { location.hash = h }", hash_)
        page.wait_for_selector(wait)
        page.wait_for_timeout(pause)
        if label == "flow-editor":
            page.keyboard.press("0")           # ganzen Ablauf einpassen
            page.wait_for_timeout(500)
        if label == "system":
            # The page names the folder of the installation – the reference state lies elsewhere, and depending on the length of
            # the path the line wraps differently. For the image a fixed text stands there.
            page.evaluate("() => document.querySelectorAll('#updateCard code').forEach(c => { c.textContent = '/ordner/der/installation' })")
            # The system load shows values of the machine the test runs on (load, memory, uptime) –
            # they change from run to run. In the image the tile stays with its head, the values are hidden.
            page.add_style_tag(content="#sysInfo > :not(.card-head) { visibility: hidden !important } #sysInfo .notice { display: none !important }")
            page.wait_for_timeout(200)
        if label == "ereignisse":
            # Day and time of the entries come from the server (now) – compared is the structure of the page. The
            # day heading is hidden, as the time a fixed one stands in every entry (otherwise a few digits deviate
            # depending on the time of day of the capture – sometimes below, sometimes above the limit).
            page.add_style_tag(content=".li, .ev-list, .ev-day { visibility: hidden !important }")
            page.evaluate("() => document.querySelectorAll('.ev-meta').forEach(m => {"
                          " m.textContent = m.textContent.replace(/^[0-9]{2}:[0-9]{2}/, '12:00') })")
        file = target / f"{name}-{label}.png"
        page.screenshot(path=str(file), full_page=full, animations="disabled")
        taken.append(file.name)
    # a dialog: tap a device
    page.evaluate("h => { location.hash = h }", "#home")
    page.wait_for_selector(HEAD)
    # Bring the tile itself to the centre: otherwise Playwright decides per run how far it scrolls before the
    # click – and the page behind the dialog would stand in different places. Wait before until the page has come
    # to rest: floor plan, weather and tiles load later and otherwise push the room tile further.
    settle(page, HEAD)
    page.evaluate(CENTER_JS, HEAD)
    page.wait_for_timeout(300)
    page.click(HEAD)
    page.wait_for_selector(".sheet-bg .sheet")
    page.wait_for_timeout(600)
    # For the image the dialog counts, not the chance around it: the page behind it stands once more in the same
    # place, the pointer lies outside the dialog and no field carries the focus. (Whether the brightness slider kept
    # the focus depended up to 0.19.3 on whether a live message had just redrawn the dialog.)
    settle(page, HEAD)
    page.evaluate(CENTER_JS, HEAD)
    page.mouse.move(3, 3)
    page.evaluate("() => { const a = document.activeElement; if (a && a.blur) a.blur() }")
    page.wait_for_timeout(400)
    file = target / f"{name}-dialog.png"
    page.screenshot(path=str(file), animations="disabled")
    taken.append(file.name)
    problems.extend(f"{name} Konsole: {e}" for e in errs)
    print(f"{name}: {len(PAGES) + 1} images, console errors: {len(errs)}")
    browser.close()


with sync_playwright() as p:
    run(p, "chromium", "desktop", **ui.DESKTOP)
    run(p, "webkit", "safari", **p.devices[ui.PHONE])
    if not SAVE:
        browser = p.chromium.launch()
        page = browser.new_page()
        for name in taken:
            if not (REF / name).is_file():
                problems.append(f"{name}: no reference image")
                continue
            res = ui.compare_images(page, REF / name, NOW / name, DIFF / name)
            if "size" in res:
                problems.append(f"{name}: other size {res['size'][2]}×{res['size'][3]} instead of {res['size'][0]}×{res['size'][1]}")
            elif res["diff"] / res["total"] > GRENZE:
                problems.append(f"{name}: {res['diff']} pixels deviate ({100 * res['diff'] / res['total']:.2f} %), area {res['box']}")
            elif res["diff"]:
                print(f"  {name}: {res['diff']} pixels deviate (below the limit)")
        browser.close()

server.should_exit = True
time.sleep(0.2)
if SAVE:
    print(f"{len(taken)} reference images in {REF}")
print("\n".join(problems) if problems else "UI OK – no deviation, no console errors")
