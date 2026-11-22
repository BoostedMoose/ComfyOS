"""UI check of the System page with Playwright: tabs "Einstellungen" and "Plugins", pairing and importing Hue,
tiles in columns and freely arranged, passwords as dots, plus lamps and scene chips of the home page; since 0.29.2 the
demo home is opened and left again with "Demo beenden" (test world world_hue; desktop, iPhone/Chromium, iPhone/WebKit).

    python tests/ui/ui_system.py [path-to-comfyos]

Screenshots end up in tests/shots/.
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import ui, world_hue as world  # noqa: E402

ROOT = world.ROOT
g = vars(world)        # mocks, app and helpers of the test world – the same as in tests/plugins/test_hue.py
main, auth, hue, LIGHTS, U, push_event = g["main"], g["auth"], g["hue"], g["LIGHTS"], g["U"], g["push_event"]

from app import core, device_store, floorplan_store, plugins, weather  # noqa: E402
import httpx  # noqa: E402

# weather service (Open-Meteo) as a mock – the UI test never goes to the internet
wx_calls = []


def wx_handler(req):
    """Responses of place search and weather service; records every request."""
    wx_calls.append(str(req.url))
    if req.url.host.startswith("geocoding"):
        return httpx.Response(200, json={"results": [{"name": "Berlin", "latitude": 52.52, "longitude": 13.41,
                                                      "country_code": "DE", "postcodes": ["10115"]}]})
    return httpx.Response(200, json={"current": {"temperature_2m": 7.4, "apparent_temperature": 4.9, "relative_humidity_2m": 81,
                                                 "weather_code": 2, "is_day": 1, "wind_speed_10m": 14.2},
                                     "daily": {"temperature_2m_max": [9.1], "temperature_2m_min": [2.3]}})


weather._client = lambda timeout=6.0: httpx.AsyncClient(transport=httpx.MockTransport(wx_handler))
# weather warnings (DWD) as a mock too: no warning
weather._warn_client = lambda timeout=6.0: httpx.AsyncClient(transport=httpx.MockTransport(
    lambda req: httpx.Response(200, json={"type": "FeatureCollection", "features": []})))

# floor plan: rectangle with a partition wall -> two rooms, linked to Home Base rooms
floorplan_store.save({"width": 20, "height": 12, "address": "Musterstraße 1, 10115 Berlin", "heatmap": False,
                      "walls": [{"id": "w1", "pts": [[1, 1], [19, 1], [19, 11], [1, 11], [1, 1]]},
                                {"id": "w2", "pts": [[10, 1], [10, 11]]}],
                      "rooms": [{"id": "r1", "name": "Küche", "room_id": "kueche", "x": 5, "y": 6},
                                {"id": "r2", "name": "Wohnzimmer", "room_id": "wohnen", "x": 15, "y": 6}],
                      "openings": [], "devices": {"hue:0210:ecb5faaabbcc:1": {"x": 4, "y": 4}}})

from playwright.sync_api import sync_playwright  # noqa: E402

server, PORT = ui.serve(main.app)        # eigener freier Port je Lauf
mock = g["MockHB"]("https://qivicon", "", "x")
mock.room_names_provider = core.room_names
g["install_homebase"](mock)
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = HERE.parent / "shots"
SHOTS.mkdir(exist_ok=True)
for f in SHOTS.glob("*.png"):
    f.unlink()
problems = []
dev_px = {}
# synthetic touch pointers (Playwright knows no multi-touch): pointerdown on the element under the finger, the rest to the surface
PTR_JS = """() => {
  window.__wrap = () => document.querySelector('.pv .fp-wrap.cam') || document.querySelector('#fpHost .fp-wrap')
  window.__tgt = {}
  window.__ptr = (type, id, x, y) => {
    const w = __wrap(), cx = x, cy = y
    // like real touch input: follow-up events go to the element the finger was put down on
    if (type === 'pointerdown') __tgt[id] = document.elementFromPoint(cx, cy) || w
    const el = __tgt[id] && __tgt[id].isConnected ? __tgt[id] : w
    el.dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: 'touch', isPrimary: id === 1, clientX: cx, clientY: cy,
      bubbles: true, cancelable: true, button: 0, buttons: type === 'pointerup' || type === 'pointercancel' ? 0 : 1 }))
  }
  window.__box = () => (document.querySelector('.pv .fp-svg').getAttribute('viewBox') || '').split(' ').map(Number)
}"""
B9 = 'hue:0210:ecb5faaabbcc:9'
B1 = 'hue:0210:ecb5faaabbcc:1'


def run(p, engine, name, **ctx_args):
    """One pass of the System page in one browser: plugins, settings, arranging tiles, backup, licence."""
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(**ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    ctx.grant_permissions(["clipboard-read", "clipboard-write"]) if engine == "chromium" else None
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    shot = lambda n, **kw: page.screenshot(path=str(SHOTS / f"{name}-{n}.png"), **kw)  # noqa: E731

    # ---------- System: Hue koppeln/importieren, Home-Base-Liste
    page.goto(BASE + "/")
    page.wait_for_selector("#pageTitle")
    page.wait_for_selector("#page .card, #page section")
    page.evaluate("HO.go('settings?tab=plugins')")          # plugins have a tab of their own under System (since 0.19.3)
    try:
        page.wait_for_selector("#plugin-hue", timeout=15000)
    except Exception:
        shot("0-system-fehlt")
        print(f"DIAG {name}: url={page.url} titel={page.title()!r} seite={page.inner_text('#page')[:400]!r}")
        raise
    if "nicht verbunden" in page.inner_text("#plugin-hue"):
        hue["pair_tries"] = 0
        page.click("#plugin-hue [data-act=huePair]")
        page.wait_for_selector("#plugin-hue .badge.ok", timeout=15000)
    page.wait_for_selector("#plugin-qivicon .mgroup")
    # System page: no tile sticks out over the screen (on the phone the widest content used to determine the column)
    WIDE_JS = """() => { const w = document.documentElement.clientWidth
      const out = [...document.querySelectorAll('#page .card')].filter(c => c.getBoundingClientRect().right > w + 1).map(c => (c.querySelector('h3') || c).textContent.trim().slice(0, 30))
      if (!out.length && document.documentElement.scrollWidth > w + 1) {
        const leaf = [...document.querySelectorAll('#page *')].filter(e => !e.children.length && e.getBoundingClientRect().right > w + 1)
        return leaf.slice(0, 5).map(e => e.tagName + ': ' + (e.textContent || '').trim().slice(0, 40)).concat(['Seite breiter als der Bildschirm'])
      }
      return out }"""
    wide = page.evaluate(WIDE_JS)
    if wide:
        problems.append(f"{name}: tab Plugins sticks out over the screen: {wide}")
    if page.locator("#homeCard, #updateCard").count():
        problems.append(f"{name}: tab Plugins shows tiles of the settings")
    # tab "Einstellungen": no plugin tile, not wider than the screen either
    page.click('[data-systab="system"]')
    page.wait_for_selector("#sysGrid #updateCard")
    if page.locator("#sysGrid [data-plugin], #pluginInfo").count() or "tab=plugins" in page.url:
        problems.append(f"{name}: tab Einstellungen shows plugins")
    wide = page.evaluate(WIDE_JS)
    if wide:
        problems.append(f"{name}: tab Einstellungen sticks out over the screen: {wide}")
    # Tile "Externe Verbindungen" (short since 0.23.1): the state is a dot (green = active, otherwise grey – since 0.24 no
    # yellow; the word in its label), no
    # badge; title short, the text of an entry uses the whole width of the tile next to the dot (seen squeezed on the phone
    # with Matter up to 0.22.0)
    page.wait_for_selector("#egressCard .li")
    narrow = page.evaluate("""() => [...document.querySelectorAll('#egressCard .li')].filter(li => {
        const text = li.querySelector('.small'), dot = li.querySelector('.statusdot'), title = li.querySelector('b')
        return !dot || !dot.getAttribute('aria-label') || li.querySelector('.badge') || !title || title.textContent.length > 40
            || text.getBoundingClientRect().width < li.clientWidth - 40
    }).map(li => (li.querySelector('b') || li).textContent.slice(0, 60))""")
    if narrow:
        problems.append(f"{name}: external connections: dot, short title or full width missing: {narrow}")
    page.locator("#egressCard").screenshot(path=str(SHOTS / f"{name}-0c-egress.png"))
    # System page in columns (since 0.19.2): two on the desktop (default), one on the phone; columns flush at the top, about equally long,
    # no tile with a gap at the top (0.19.1: `.card + .card` gave plugin tiles 16 px)
    # The tiles load their content later and redistribute in the first seconds (HO.tileGrid): measuring only
    # happens once the column heights have not changed for half a second (wait at most five seconds).
    heights_now = lambda: page.evaluate(  # noqa: E731
        "() => [...document.querySelectorAll('#sysGrid > .mz-col')].map(c => Math.round(c.getBoundingClientRect().height))")
    page.wait_for_timeout(600)
    seen = heights_now()
    for _ in range(9):
        page.wait_for_timeout(500)
        if heights_now() == seen:
            break
        seen = heights_now()
    lay = page.evaluate("""() => { const cols = [...document.querySelectorAll('#sysGrid > .mz-col')]
      const tiles = cols.flatMap(c => [...c.children])
      return { n: cols.length, tops: cols.map(c => Math.round(c.getBoundingClientRect().top)), heights: cols.map(c => c.getBoundingClientRect().height),
        tallest: Math.max(...tiles.map(t => t.getBoundingClientRect().height)), stray: document.querySelectorAll('#sysGrid > .card').length,
        shifted: tiles.filter(t => parseFloat(getComputedStyle(t).marginTop) !== 0).map(t => t.id) } }""")
    want = 2 if name == "desktop" else 1
    if lay["n"] != want or lay["stray"] or lay["shifted"] or len(set(lay["tops"])) != 1 \
            or (want == 2 and abs(lay["heights"][0] - lay["heights"][1]) > lay["tallest"] + 20):
        problems.append(f"{name}: Spalten der Systemseite falsch: {lay}")
    tile_ids = lambda: page.evaluate("() => [...document.querySelectorAll('#sysGrid > .mz-col')].map(c => [...c.children].map(t => t.id))")  # noqa: E731
    saved_layout = lambda: page.evaluate("() => fetch('/api/settings').then(r => r.json()).then(s => (s.layout || {}).system || {})")  # noqa: E731
    # arranging: the arrow "down" moves the first tile, the arrangement ends up in the settings per number of columns
    page.click('[data-act="arrange"]')
    if page.locator("#sysGrid .mz-over").count() != sum(len(c) for c in tile_ids()):
        problems.append(f"{name}: Anordnen does not show the drag area over every tile")
    first = tile_ids()[0][0]
    page.locator(f'#{first} [data-mz="down"]').click()
    page.wait_for_timeout(900)
    after = tile_ids()
    if after[0][1] != first or saved_layout().get(str(want)) != after:
        problems.append(f"{name}: the down arrow does not move or does not save: {after[0][:3]} / {saved_layout()}")
    if name == "desktop":
        # dragging with the mouse: last tile of the right column all the way to the top left
        mover = after[1][-1]
        page.locator(f"#{mover}").scroll_into_view_if_needed()
        box = page.locator(f"#{mover} .mz-over").bounding_box()
        # a tile can be higher than the screen: grab a visible place below the fixed header
        page.mouse.move(box["x"] + 40, min(max(box["y"], 0) + 160, box["y"] + box["height"] - 20))
        page.mouse.down()
        if not page.locator(".mz-dragging").count():
            problems.append(f"{name}: Ziehen beginnt nicht ({mover}, {box})")
        target = page.locator(f"#{after[0][0]}")
        for _ in range(150):                          # with scrolling along at the top edge (the page is several screens long)
            tb = target.bounding_box()
            page.mouse.move(tb["x"] + 30, max(tb["y"] + 10, 5), steps=4)
            if tile_ids()[0][0] == mover:
                break
            page.wait_for_timeout(60)
        page.mouse.up()
        page.wait_for_timeout(900)
        if tile_ids()[0][0] != mover or saved_layout().get("2") != tile_ids():
            problems.append(f"{name}: dragging with the mouse does not arrange: {tile_ids()[0][:3]}, scrollY {page.evaluate('scrollY')}, "
                            f"{mover} in Spalte {[i for i, c in enumerate(tile_ids()) if mover in c]}")
        # number of columns selectable: one column, then two again (remembered per device)
        page.click('[data-act="arrange"]')
        page.click('[data-cols-seg="system"] [data-cols="1"]')
        if len(tile_ids()) != 1:
            problems.append(f"{name}: the choice „1 Spalte“ has no effect")
        page.click('[data-cols-seg="system"] [data-cols="2"]')
        if tile_ids()[0][0] != mover:
            problems.append(f"{name}: arrangement for two columns lost after the change")
        page.click('[data-act="arrange"]')
    elif page.locator('[data-cols-seg="system"]').is_visible():
        problems.append(f"{name}: Spaltenwahl am Handy sichtbar")
    page.click('[data-act="arrangeAuto"]')
    page.wait_for_timeout(500)
    if saved_layout().get(str(want)):
        problems.append(f"{name}: „Automatisch“ does not forget the arrangement")
    page.click('[data-act="arrange"]')
    if page.locator("#sysGrid .mz-over").count():
        problems.append(f"{name}: drag areas stay after „Fertig“")
    shot("0b-system-spalten")
    # back to the plugins through the connection pill at the top (leads into the tab "Plugins")
    page.click("#connPill")
    page.wait_for_selector("#plugin-qivicon .mgroup")
    if not page.url.endswith("#settings?tab=plugins") or page.locator('[data-systab="plugins"].active').count() != 1:
        problems.append(f"{name}: the connection bubble does not lead into the tab Plugins ({page.url})")
    # "Aktualisieren" in the header redraws the open tab: it must stay on "Plugins" (it used to fall back to
    # "Einstellungen" while the address still said "Plugins", so the tab button did nothing afterwards)
    page.click('[data-act="refresh"]')
    page.wait_for_selector("#plugin-qivicon .mgroup")
    if page.locator('[data-systab="plugins"].active').count() != 1:
        problems.append(f"{name}: after 'Aktualisieren' the page left the tab Plugins")
    page.click('[data-systab="system"]')
    page.wait_for_selector('[data-systab="system"].active')
    page.click('[data-systab="plugins"]')
    page.wait_for_selector("#plugin-qivicon .mgroup")
    if not page.url.endswith("#settings?tab=plugins"):
        problems.append(f"{name}: the tab buttons do not switch after 'Aktualisieren' ({page.url})")
    hb_text = page.inner_text("#plugin-qivicon")
    for want in ("Verbunden", "Neu gefunden", "Entfernt" if False else "Verbunden"):
        if want not in hb_text:
            problems.append(f"{name}: Home-Base-Liste ohne '{want}'")
    if page.locator('[data-import]').count():
        # "Neu auf der Bridge" is collapsed by default – open it
        new_grp = page.locator('#plugin-hue details.mgroup[data-g="Neu auf der Bridge"]')
        if new_grp.count() and not new_grp.evaluate("el => el.open"):
            page.click('#plugin-hue details.mgroup[data-g="Neu auf der Bridge"] > summary')
            page.wait_for_selector('[data-import-room]', state="visible", timeout=5000)
        lamp = "huebridge:" + U(18)                    # device id of the lamp at the Hue plugin
        sel = page.locator(f'[data-import-room="{lamp}"]')
        if sel.count():
            sel.select_option("kueche")
            page.click(f'[data-import="{lamp}"]')
            page.locator(f'[data-import="{lamp}"]').wait_for(state='hidden', timeout=8000)
        elif lamp not in device_store.get()["adopted"]:
            problems.append(f"{name}: new lamp {lamp} is missing under „Neu auf der Bridge“")
    card = page.inner_text("#plugin-hue")
    for want in ("Zu prüfen", "Verbunden", "Neu einlesen"):
        if want not in card:
            problems.append(f"{name}: Hue-Kachel ohne '{want}'")
    # order: address + buttons before the lists
    order = page.evaluate("""() => { const c = document.querySelector('#plugin-hue');
        const a = c.querySelector('#hueHost'), l = c.querySelector('.mgroups');
        return !!(a && l && (a.compareDocumentPosition(l) & Node.DOCUMENT_POSITION_FOLLOWING)) }""")
    if not order:
        problems.append(f"{name}: the address line is not above the lists")
    # plugin tile: state and switch from the core, content from the plugin (static/settings.js); password dots
    head = page.evaluate("""() => { const c = document.querySelector('#plugin-hue')
        return { badge: (c.querySelector('.card-head .badge') || {}).textContent, toggle: !!c.querySelector('[data-plugin-toggle]'),
                 info: !!document.querySelector('#pluginInfo [data-plugin-rescan]') && !document.querySelector('#sysGrid #pluginInfo')
                   && !!(document.querySelector('#pluginInfo').compareDocumentPosition(document.querySelector('#sysGrid')) & Node.DOCUMENT_POSITION_FOLLOWING),
                 pw: [HO.pwAttrs(true).indexOf('data-saved') > 0, HO.pwAttrs(false), HO.pwValue({ value: HO.PW_SAVED }), HO.pwValue({ value: 'neu' })] } }""")
    if head["badge"] not in ("Online", "Verbunden") or not head["toggle"] or not head["info"]:
        problems.append(f"{name}: Hue tile without state/switch, or the head tile „Neu suchen“ is missing or not above the grid: {head}")
    if head["pw"] != [True, "", "", "neu"]:
        problems.append(f"{name}: stored passwords are not treated as dots: {head['pw']}")
    page.locator("#plugin-hue").scroll_into_view_if_needed()
    page.locator("#plugin-hue").screenshot(path=str(SHOTS / f"{name}-1-huecard.png"))
    page.locator("#plugin-qivicon").screenshot(path=str(SHOTS / f"{name}-2-hblist.png"))

    # ---------- Home page: immediate switching, double tap, live update from outside
    LIGHTS[9]["on"] = False             # Ausgangszustand serverseitig: Lampe 9 aus
    plugins.active("hue").bridge.forget_lights()
    page.evaluate("HO.go('home')")
    page.wait_for_selector(".room-card")
    # lamps have been rows in the room card since 0.12.3 (switch data-lt-toggle, state aria-pressed)
    page.wait_for_selector(f'[data-lt-toggle="{B9}"][aria-pressed="false"]')
    hue["puts"].clear()
    btn = f'[data-lt-toggle="{B9}"]'
    # times of the taps and of the state changes in the page (for the message if the double tap gets through)
    page.evaluate("""(id) => { window.__taps = []; const t0 = performance.now(), ms = () => Math.round(performance.now() - t0)
      document.addEventListener('click', e => { if (e.target.closest && e.target.closest('[data-lt-toggle]')) __taps.push('tipp ' + ms()) }, true)
      new MutationObserver(() => { const b = document.querySelector(`[data-lt-toggle="${id}"]`), v = b && b.getAttribute('aria-pressed')
        if (v !== window.__pressed) { window.__pressed = v; __taps.push(v + ' ' + ms()) } }).observe(document.body, { subtree: true, childList: true, attributes: true }) }""", B9)
    t0 = time.time()
    # double tap as one action: two single clicks are about 800 ms apart in WebKit on the Pi –
    # that would no longer be an accidental double tap (protection: 700 ms) but switching twice
    page.dblclick(btn)
    page.wait_for_selector(f'[data-lt-toggle="{B9}"][aria-pressed="true"]', timeout=2000)
    # reaction of the app: from the tap to the visible state, measured in the page (without the waiting time of Playwright)
    marks = {m.split()[0]: int(m.split()[1]) for m in reversed(page.evaluate("window.__taps")) if m.split()[0] in ("tipp", "true")}
    react_ms = marks["true"] - marks["tipp"] if len(marks) == 2 else (time.time() - t0) * 1000
    page.wait_for_timeout(800)
    if len([x for x in hue["puts"] if x[0] == U(9)]) != 1:
        problems.append(f"{name}: double tap sent {hue['puts']} (expected exactly 1); sequence in the page: {page.evaluate('window.__taps')}")
    page.wait_for_timeout(1800)          # after confirmation nothing may jump back
    if not page.locator(f'[data-lt-toggle="{B9}"][aria-pressed="true"]').count():
        problems.append(f"{name}: the lamp jumps back after switching")

    page.evaluate("window.__sse = []; (() => { const es = new EventSource('/api/stream'); es.onmessage = e => window.__sse.push(e.data) })()")
    page.wait_for_timeout(500)
    print('  listeners', len(core._listeners), 'devices in the index', len(core._index))
    LIGHTS[1]["on"] = not LIGHTS[1]["on"]
    want_on = LIGHTS[1]["on"]
    t0 = time.time()
    push_event("update", {"id": U(1), "type": "light", "on": {"on": want_on}})
    try:
        page.locator(f'[data-lt-toggle="{B1}"][aria-pressed="{str(want_on).lower()}"]').wait_for(state='visible', timeout=3000)
        live_ms = (time.time() - t0) * 1000
    except Exception:
        live_ms = None
        problems.append(f"{name}: a change from the Hue app did not arrive live")
        print('  sse:', page.evaluate("window.__sse"), 'dom:', page.evaluate("(id) => [...document.querySelectorAll(`[data-lt-toggle=\"${id}\"]`)].map(e => e.className + ' ' + e.getAttribute('aria-pressed'))", B1))
    shot("3-start", full_page=True)

    # ---------- Floor plan in the card: swiping opens nothing, tapping opens the room (the dialog stays open)
    touch = bool(ctx_args.get("has_touch"))
    tap = page.tap if touch else page.click            # Handy: echtes Antippen
    page.locator('#fpHost').scroll_into_view_if_needed()
    try:
        page.wait_for_selector('#fpFoot .fp-wx', timeout=5000)
        if "7°" not in page.inner_text('#fpFoot .fp-wx'):
            problems.append(f"{name}: Wetter unter dem Grundriss falsch: {page.inner_text('#fpFoot .fp-wx')!r}")
    except Exception:
        problems.append(f"{name}: no weather next to the address")
    if any("Muster" in u for u in wx_calls):
        problems.append(f"{name}: the street was sent to the weather service: {wx_calls}")
    page.evaluate(PTR_JS)
    r2 = page.evaluate("() => { const b = document.querySelector('#fpHost .fp-room[data-id=r2] path').getBoundingClientRect(); return [b.x + b.width * .5, b.y + b.height * .7] }")
    page.evaluate("([x, y]) => { __ptr('pointerdown', 1, x, y); __ptr('pointermove', 1, x, y + 30); __ptr('pointercancel', 1, x, y + 30) }", r2)
    page.wait_for_timeout(250)
    if page.locator('.sheet-bg').count():
        problems.append(f"{name}: scrolling over the floor plan opened a room")
        page.evaluate("document.querySelectorAll('.sheet-bg').forEach(e => e.remove())")
    tap('#fpHost .fp-room[data-id=r2] path', position={"x": 20, "y": 20})
    page.wait_for_timeout(500)
    if not page.locator('.sheet-bg .sheet h3:has-text("Wohnzimmer")').count():
        problems.append(f"{name}: tapping a room in the plan opens no room dialog (or it closes at once)")
    else:
        page.click('.sheet-bg [data-close]')

    # ---------- Grundriss im Vollbild: eigene Ansicht #plan
    page.locator('[data-act="fpFull"]').scroll_into_view_if_needed()
    tap('[data-act="fpFull"]')
    page.wait_for_selector('.pv .fp-wrap.cam svg.fp-svg .fp-room')
    page.wait_for_timeout(400)
    page.evaluate(PTR_JS)
    vp = page.viewport_size
    geo = page.evaluate("""() => { const w = document.querySelector('.pv .fp-wrap.cam').getBoundingClientRect();
        const nav = [...document.querySelectorAll('.bottomnav, .topbar, .sidebar')].some(e => e.offsetParent !== null);
        const c = document.querySelector('.pv .fp-dev circle.bg').getBoundingClientRect();
        return { hash: location.hash, wrap: [w.left, w.top, w.width, w.height], nav, chromeless: document.body.classList.contains('chromeless'),
                 wx: (document.querySelector('#pvWx .pv-chip') || {}).innerText || '', dev: c.width, box: __box() } }""")
    shot("4-plan")
    if geo["hash"] != "#plan" or not geo["chromeless"] or geo["nav"]:
        problems.append(f"{name}: full screen is no view of its own without navigation {geo}")
    wl, wt, ww, wh = geo["wrap"]
    if abs(wl) > 1 or abs(wt) > 1 or ww < vp["width"] - 2 or wh < vp["height"] - 2:
        problems.append(f"{name}: full screen does not cover the screen {geo}")
    if "7°" not in geo["wx"] or "Berlin" not in geo["wx"]:
        problems.append(f"{name}: Wetter fehlt im Vollbild: {geo['wx']!r}")
    dev_px[name] = geo["dev"]
    print(f"  {name} full screen: device symbol {geo['dev']:.1f} px, viewBox {[round(v, 2) for v in geo['box']]}")

    # panning with one finger/mouse over a room: the camera moves, nothing opens
    b0 = geo["box"]
    c = page.evaluate("() => { const b = document.querySelector('.pv .fp-room[data-id=r1] path').getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2] }")
    if touch:
        page.evaluate("([x, y]) => { __ptr('pointerdown', 1, x, y); for (let i = 1; i <= 6; i++) __ptr('pointermove', 1, x + i * 12, y + i * 4); __ptr('pointerup', 1, x + 72, y + 24); __wrap().click() }", c)
    else:
        page.mouse.move(*c); page.mouse.down(); page.mouse.move(c[0] + 72, c[1] + 24, steps=6); page.mouse.up()
    page.wait_for_timeout(300)
    b1 = page.evaluate("__box()")
    if abs(b1[0] - b0[0]) < 0.2 and abs(b1[1] - b0[1]) < 0.2:
        problems.append(f"{name}: panning does not move the camera {b0} -> {b1}")
    if page.locator('.sheet-bg').count() or page.locator('#pvPeek:not(.hidden)').count():
        problems.append(f"{name}: panning opened a room/dialog")

    # zooming: two fingers (phone) or mouse wheel (desktop)
    if touch:
        page.evaluate("""() => { const w = __wrap().getBoundingClientRect(), x = w.width / 2, y = w.height / 2;
            __ptr('pointerdown', 1, x - 30, y); __ptr('pointerdown', 2, x + 30, y);
            for (let i = 1; i <= 8; i++) { __ptr('pointermove', 1, x - 30 - i * 12, y); __ptr('pointermove', 2, x + 30 + i * 12, y) }
            __ptr('pointerup', 2, x + 126, y); __ptr('pointerup', 1, x - 126, y) }""")
    else:
        page.mouse.move(vp["width"] / 2, vp["height"] / 2); page.mouse.wheel(0, -400)
    page.wait_for_timeout(350)
    b2 = page.evaluate("__box()")
    if not b2[2] < b1[2] * 0.8:
        problems.append(f"{name}: zooming (pinch/mouse wheel) does not enlarge {b1} -> {b2}")
    if page.locator('.sheet-bg').count():
        problems.append(f"{name}: the pinch opened a dialog")

    # limits: push far away and zoom far – the plan stays in the picture
    page.evaluate("""() => { __ptr('pointerdown', 1, 50, 300); for (let i = 1; i <= 20; i++) __ptr('pointermove', 1, 50 + i * 400, 300 + i * 300); __ptr('pointerup', 1, 8050, 6300) }""")
    page.wait_for_timeout(250)
    for _ in range(14):
        page.click('[data-act="pvOut"]')
    page.wait_for_timeout(250)
    bo = page.evaluate("__box()")
    W, H = 20, 12
    if bo[0] + bo[2] < W * 0.5 or bo[0] > W * 0.5 or bo[1] + bo[3] < H * 0.5 or bo[1] > H * 0.5:
        problems.append(f"{name}: Kamera kann den Grundriss verlassen {bo}")
    fit = min(ww / W, wh / H) * 0.9                    # overall view; further out at most 0.8×
    if bo[2] > ww / (fit * 0.8) * 1.02:
        problems.append(f"{name}: zu weit herausgezoomt {bo} (max. Breite {ww / (fit * 0.8):.2f})")
    for _ in range(25):
        page.click('[data-act="pvIn"]')
    page.wait_for_timeout(250)
    bi = page.evaluate("__box()")
    page.click('[data-act="pvIn"]'); page.wait_for_timeout(150)
    if abs(page.evaluate("__box()")[2] - bi[2]) > 1e-3:
        problems.append(f"{name}: the zoom has no upper limit")
    page.click('[data-act="pvFit"]')
    page.wait_for_timeout(250)
    if abs(page.evaluate("__box()")[2] - b0[2]) > 0.05:
        problems.append(f"{name}: 'Ganzer Grundriss' does not restore the view")

    # tapping: room -> compact card (no large dialog), device -> device dialog that stays open
    tap('.pv .fp-room[data-id=r2] path', position={"x": 20, "y": 20})
    page.wait_for_timeout(450)
    if not page.locator('#pvPeek:not(.hidden) h3:has-text("Wohnzimmer")').count():
        problems.append(f"{name}: tapping a room shows no room card")
    if not page.locator('.pv .fp-room.hl[data-id=r2]').count():
        problems.append(f"{name}: the tapped room is not highlighted")
    if page.locator('.sheet-bg').count():
        problems.append(f"{name}: tapping a room opens the big dialog in full screen")
    shot("5-plan-raum")
    tap('[data-act="pvPeekClose"]')
    page.wait_for_timeout(200)
    if page.locator('#pvPeek:not(.hidden)').count() or page.locator('.pv .fp-room.hl').count():
        problems.append(f"{name}: the room card does not close")
    tap(f'.pv .fp-dev[data-id="{B1}"] circle.bg')
    page.wait_for_timeout(700)
    if not page.locator('.sheet-bg .sheet').count():
        problems.append(f"{name}: tapping a device opens no dialog (or it closes again at once)")
    else:
        page.click('.sheet-bg [data-close]')
    tap('#pvWx .pv-chip')
    page.wait_for_timeout(150)
    if "Gefühlt" not in page.inner_text('#pvWx'):
        problems.append(f"{name}: the weather details do not open")
    shot("6-plan-wetter")

    # back to the home page
    tap('.pv-top [data-act="pvBack"]')
    page.wait_for_selector('#fpHost .fp-svg')
    if page.evaluate("location.hash") != "#home" or page.evaluate("document.body.classList.contains('chromeless')"):
        problems.append(f"{name}: back does not lead to the home page with navigation")

    # ---------- Heizplan-Import
    page.evaluate("HO.go('heating')")
    page.wait_for_selector('[data-act="import"]')
    page.click('[data-act="import"]')
    page.wait_for_selector("#impText")
    doc = {"routinen": [{"name": f"UI-Test {name}", "räume": {"küche": {"Mo-Fr": [["06:30", 21], ["22:00", "Eco"]]}, "Keller": {"täglich": [["08:00", 18]]}}}]}
    page.fill("#impText", json.dumps(doc))
    page.click('[data-imp="check"]')
    page.wait_for_selector("#impResult .li")
    res = page.inner_text("#impResult")
    if "wird angelegt" not in res or "Keller" not in res:
        problems.append(f"{name}: import preview incomplete: {res!r}")
    shot("5-import")
    page.click('[data-imp="apply"]')
    page.wait_for_selector(f'.chip:has-text("UI-Test {name}")', timeout=8000)

    # demo home: open it from the tab "Einstellungen" and leave it with "Demo beenden" (since 0.29.2 – before, the click
    # handler of the page replaced the one of the demo tile and the button did nothing)
    def demo_is(active):
        """Wait until the server says this session is (not) in the demo – across the reload the buttons start."""
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if page.evaluate("async () => (await HO.api('demo')).active") is active:
                    return True
            except Exception:
                pass                     # the page is just reloading: ask again
            page.wait_for_timeout(200)
        return False

    page.goto(BASE + "/#settings")
    page.wait_for_selector('[data-act="demoStart"]', timeout=10000)
    page.click('[data-act="demoStart"]')
    if not demo_is(True):
        problems.append(f"{name}: „Demo-Wohnung ansehen“ does not open the demo")
    else:
        page.goto(BASE + "/#settings")
        page.wait_for_selector('[data-act="demoStop"]', timeout=10000)
        page.click('[data-act="demoStop"]')
        if not demo_is(False):
            problems.append(f"{name}: the demo home cannot be left with „Demo beenden“")
            page.evaluate("() => HO.api('demo/stop', {method: 'POST'})")       # the next pass starts outside the demo

    print(f"{name}: Reaktion beim Tippen {react_ms:.0f} ms, Hue-App -> Seite {live_ms and round(live_ms)} ms, Konsolenfehler: {len(errs)}")
    problems.extend(f"{name} Konsole: {e}" for e in errs)
    browser.close()


with sync_playwright() as p:
    only = os.environ.get("UI_ONLY")       # single run for reproducing: UI_ONLY=safari python tests/ui/ui_system.py
    for engine, name, args in (("chromium", "desktop", {"viewport": {"width": 1366, "height": 900}}),
                               ("chromium", "mobil", p.devices["iPhone 13"]), ("webkit", "safari", p.devices["iPhone 13"])):
        if not only or only == name:
            run(p, engine, name, **args)

# phone: symbols in the full-screen overview daintier than on the large screen
if dev_px.get("mobil") and dev_px.get("desktop") and not dev_px["mobil"] < dev_px["desktop"] * 0.85:
    problems.append(f"device symbols do not scale along on the phone: {dev_px}")
server.should_exit = True
print("\n".join(problems) if problems else "UI OK – no problems, no console errors")
