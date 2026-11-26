"""Pictures for the README and the wiki, with sample data only: no real data, no connection to the internet. Needs
Playwright – so start it on Windows with the Python of the fixed test environment (Playwright never runs on the Pi;
the path of the test environment is in CLAUDE.md and dev/README.md, section Testing):

    <python-of-the-test-environment> tools/readme_screenshot.py [repo] [target.png]

The app runs in its own temp directories (test world tests/support/world_demo.py). Two sessions look at it:

  * a session in the demo home (demo.py, demo_world.py – the same sample flat a user sees under System → Demo-Wohnung
    ansehen). The home is set to an evening (`cosy`): the scene "Polarlicht" in the living room (blue, violet and a warm
    floor lamp next to the pink night light, the warm bedside lamp and the cool kitchen light), kitchen, bedside and
    hallway lamps on, the speaker in the living room playing – so the floor plan shows the light of the lamps in their
    colours and the sound waves (button "Effekte"). Weather and time of day are previews of the effects (HO.state.fxPreview,
    HO.state.fxTime – the same switches as `#home?fx=rain&time=night`), so every picture looks the same at every time
    of day: HERO_FX/HERO_TIME (rain at night – the lamps stand out most against the dark surroundings) for the pictures
    with the lamps, "day" with the demo's own weather for the heat map.
      screenshot.png – home page, classic view: the floor plan with lamp light, sound waves and the weather around the
                       house (the heat map is off here – it has its own picture)
      heatmap.png    – the floor plan tile with the heat map (lamp light and sound off, so the warmth stands alone)
      widgets.png    – widget view of the home page
      editor.png     – floor plan editor
      light.png      – light setting of the living room (lamps, scenes, effects)
      scenes.png     – page "Szenen" (scene library)
      heating.png    – heating plan
      mobile.png     – home page on a phone (like screenshot.png, device names off)
  * a session of the test world itself (Home Base and Hue Bridge as mocks), because the demo refuses flow test runs and
    shows no real plugins:
      flow.png       – flow editor with a flow of eleven nodes in the middle of a test run: the path glows, the
                       conditions show yes, the timer counts down (the time condition always counts as met)
      plugins.png    – System → Plugins

All pictures land next to the target (default docs/screenshot.png).
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "screenshot.png"
sys.argv = [sys.argv[0], str(ROOT)]             # the test world reads the root from the first argument
sys.path.insert(0, str(ROOT / "tests"))         # shared scaffolding of the tests (support)
from support import world_demo as demo  # noqa: E402
from support.world_demo import WEISS, MockHB, auth, flow_engine, httpx, install_homebase, main  # noqa: E402

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PORT = 8771
WIDE = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
HERO_FX, HERO_TIME = "rain", "night"            # weather preview and time of day of the pictures with the lamps
SIDEBAR_JS = "() => { const s = document.querySelector('.sidebar'); return s ? Math.ceil(s.getBoundingClientRect().right) : 0 }"
HOME_CARD_BOTTOM_JS = """() => { const c = document.querySelector('.home-card')
  return c ? c.getBoundingClientRect().bottom + scrollY + 24 : 1300 }"""


def shot(name: str) -> str:
    """Path of one picture next to the target."""
    return str(OUT.with_name(name))


def cosy(api) -> None:
    """Set the demo home to an evening: the scene "Polarlicht" in the living room, three more lamps on in their white
    tones, the speaker in the living room a little louder (sound waves). `api` is a signed-in client in the demo."""
    applied = api.post("/api/scenes/polarlicht/apply", json={"room_ids": ["wohnen"]})
    assert applied.status_code == 200, applied.text
    for dev, state in (("kueche", {"on": True}), ("nachttisch", {"on": True}), ("flurlicht", {"on": True}),
                       ("lautsprecher", {"volume": 32})):
        r = api.post(f"/api/devices/demo:{dev}/command", json={"state": state})
        assert r.status_code == 200, (dev, r.text)


def classic_home(page, fx=HERO_FX, tm=HERO_TIME, effects=True, heat=False) -> None:
    """Open the classic home page with a weather preview (`fx`, None = the demo's own weather) at the time of day `tm`,
    switch the heat map with its button (the plan of the demo lies in data/demo/ – the real one stays untouched) and
    wait until floor plan, shadow and weather effects are drawn – with `effects` also until a lamp shines."""
    page.goto(BASE + "/#home")
    page.wait_for_selector(".room-card .rc-head", timeout=20000)
    page.evaluate("([fx, tm, on]) => { localStorage.setItem('ho.fpEffects', on ? '1' : '0'); HO.state.fxPreview = fx; HO.state.fxTime = tm; HO.go('home', true) }",
                  [fx, tm, effects])
    page.wait_for_selector("#fpHost .fp-shadow", timeout=15000)
    button = page.locator('[data-act="fpHeat"]')
    if (button.get_attribute("aria-pressed") == "true") != heat:
        button.click()
    page.wait_for_selector("#fpHost .fp-heat", timeout=15000, state="visible" if heat else "detached")
    page.wait_for_function("() => !!(HO.state.weather && HO.state.weather.data && HO.state.weather.data.fx)")   # as a function: the CSP allows no eval
    page.wait_for_selector("#fpHost canvas.fp-fx", timeout=15000)
    if effects:
        page.wait_for_function("() => HO.weatherFx.stats.lamps >= 1", timeout=10000)   # the lamps shine
    page.wait_for_timeout(5000)   # rain, ripples, wind lines and sound waves are under way, the lamp light has eased in


server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
hb = MockHB("https://qivicon", "", "x")
install_homebase(hb)
BASE = f"http://127.0.0.1:{PORT}"
token, demo_token = auth.create_session("shot"), auth.create_session("shot-demo")
api = httpx.Client(base_url=BASE, cookies={auth.COOKIE: token}, timeout=15)
assert api.post("/api/plugins/hue/pair", json={"host": "192.0.2.10"}).json().get("connected")
api.post("/api/scenes/abendrot/apply", json={"room_ids": ["wohnen"]})
api.post(f"/api/devices/{WEISS}/command", json={"state": {"on": False}})   # all states visible: one lamp off

# the demo home for the second session: sample flat on an evening
demo_api = httpx.Client(base_url=BASE, cookies={auth.COOKIE: demo_token}, timeout=15)
started = demo_api.post("/api/demo/start")
assert started.status_code == 200 and started.json().get("active"), started.text
cosy(demo_api)

with sync_playwright() as p:
    b = p.chromium.launch()

    # ---------------------------------------------------------------- demo home, wide screen
    ctx = b.new_context(viewport=WIDE, device_scale_factor=2)
    ctx.add_cookies([{"name": auth.COOKIE, "value": demo_token, "url": BASE}])
    page = ctx.new_page()
    classic_home(page)
    left = page.evaluate(SIDEBAR_JS)
    # down to the end of the main tile: floor plan with lamp light and weather, address, consumption, home/away
    bottom = page.evaluate(HOME_CARD_BOTTOM_JS)
    page.screenshot(path=shot("screenshot.png"), full_page=True, clip={"x": left, "y": 0, "width": WIDE["width"] - left, "height": bottom})
    print("home ok", left, bottom)

    # light setting of the living room
    page.click('#devBox [data-room-head="wohnen"]')
    page.wait_for_selector(".sheet-bg .sheet", timeout=8000)
    page.wait_for_timeout(1200)
    page.screenshot(path=shot("light.png"))
    page.click(".sheet-bg [data-close]")
    page.wait_for_timeout(400)
    print("light ok")

    # floor plan editor
    page.evaluate("() => window.scrollTo(0, 0)")
    page.click('[data-act="fpEdit"]')
    page.wait_for_timeout(2000)
    box = page.evaluate("() => { const r = document.querySelector('.home-card').getBoundingClientRect(); return {x: r.left, y: r.top + scrollY, w: r.width, h: r.height} }")
    page.screenshot(path=shot("editor.png"), full_page=True,
                    clip={"x": box["x"] - 16, "y": max(0, box["y"] - 16), "width": box["w"] + 32, "height": min(box["h"], 830) + 32})
    page.click('[data-act="fpEdit"]')               # "Fertig"
    page.wait_for_timeout(800)
    print("editor ok", box)

    # widget view through the switch above the home page (the choice is remembered per device)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.click('#viewBar [data-homeview="widgets"]')
    page.wait_for_selector("#wgHost .wg-type-floorplan .fp-room", timeout=20000)
    page.wait_for_timeout(4000)
    page.screenshot(path=shot("widgets.png"), full_page=True)
    page.click('#viewBar [data-homeview="classic"]')
    page.wait_for_timeout(800)
    print("widgets ok")

    # the heat map by day in the demo's own weather, without lamp light and sound: the floor plan tile from its head
    # (button "Heatmap" active) to the end of the plan; afterwards the heat map is off again for the phone
    classic_home(page, fx=None, tm="day", effects=False, heat=True)
    card = page.evaluate("""() => { const c = document.querySelector('.home-card').getBoundingClientRect(), f = document.querySelector('#fpHost').getBoundingClientRect()
      return {x: c.left, y: c.top + scrollY, w: c.width, h: f.bottom - c.top} }""")
    page.screenshot(path=shot("heatmap.png"), full_page=True,
                    clip={"x": card["x"] - 16, "y": max(0, card["y"] - 16), "width": card["w"] + 32, "height": card["h"] + 32})
    page.click('[data-act="fpHeat"]')
    page.wait_for_selector("#fpHost .fp-heat", timeout=15000, state="detached")
    print("heatmap ok", card)

    # scenes and the weekly plan of the heating (the history above it is empty in the demo: it records nothing)
    page.evaluate("HO.go('light')")
    page.wait_for_timeout(2500)
    page.screenshot(path=shot("scenes.png"))
    page.evaluate("HO.go('heating')")
    page.wait_for_timeout(2500)
    top = page.evaluate("""() => { const h = [...document.querySelectorAll('.section-head h3')].find(e => e.textContent.trim() === 'Wochenplan')
      return h ? h.getBoundingClientRect().top + scrollY - 24 : 0 }""")
    page.screenshot(path=shot("heating.png"), full_page=True, clip={"x": left, "y": top, "width": WIDE["width"] - left, "height": 820})
    print("scenes, heating ok")
    ctx.close()

    # ---------------------------------------------------------------- demo home on a phone
    # device names off: on a phone the symbols speak for themselves (the demo plan only, data/demo/)
    plan = demo_api.get("/api/floorplan").json()
    assert demo_api.put("/api/floorplan", json={**plan, "show_names": False}).status_code == 200
    phone = b.new_context(viewport=PHONE, device_scale_factor=3, is_mobile=True, has_touch=True)
    phone.add_cookies([{"name": auth.COOKIE, "value": demo_token, "url": BASE}])
    page = phone.new_page()
    classic_home(page)
    page.screenshot(path=shot("mobile.png"))
    print("mobile ok")
    phone.close()

    # ---------------------------------------------------------------- test world: plugins and the flow editor
    ctx = b.new_context(viewport=WIDE, device_scale_factor=2)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    page.goto(BASE + "/#home")
    page.wait_for_selector(".room-card .rc-head", timeout=20000)
    page.evaluate("HO.go('settings?tab=plugins')")
    page.wait_for_selector('[data-plugin="hue"]', timeout=10000)
    page.click('[data-cols="3"]')                  # three columns: more plugins at a glance
    page.wait_for_timeout(2500)
    page.screenshot(path=shot("plugins.png"), full_page=True, clip={"x": 0, "y": 0, "width": WIDE["width"], "height": 1250})
    print("plugins ok")

    flow_engine.in_window = lambda *a, **k: True   # "after sunset" always holds for the picture
    nodes, edges = demo.demo_flow()
    r = api.post("/api/flows", json={"name": "Licht bei Bewegung – abends und nur zuhause",
                                     "description": "Dazu: Heizung runter, solange das Küchenfenster offen ist", "nodes": nodes, "edges": edges})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    first = r.json()["nodes"][0]["id"]
    assert api.put(f"/api/flows/{fid}/enabled", json={"enabled": True}).status_code == 200
    page.evaluate("id => { HO.state.flowId = id; location.hash = 'flowEdit' }", fid)
    page.wait_for_function("() => document.querySelectorAll('.fl-node').length === 11")   # as a function: the CSP allows no eval
    page.keyboard.press("0")                       # fit the whole flow
    page.wait_for_timeout(500)
    # 1) trigger "ends": the timer starts counting
    page.evaluate("([f, n]) => HO.api(`flows/${f}/fire/${n}`, { method: 'POST', body: { port: 'end' } })", [fid, first])
    page.wait_for_selector(".fl-node .fl-live:not(.hidden)")
    page.wait_for_timeout(2200)                    # let the glow fade, toast gone
    # 2) "begins" with ▶ on the trigger: the conditions are checked, the light goes on
    page.click(f'.fl-node[data-node="{first}"] .fl-fire')
    page.wait_for_function("() => document.querySelectorAll('.fl-wire.flow').length >= 4 && document.querySelectorAll('.fl-node.run').length >= 3", timeout=8000)
    page.screenshot(path=shot("flow.png"))
    print("flow ok", fid)
    b.close()
server.should_exit = True
