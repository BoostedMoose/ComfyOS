"""UI check light, scenes & floor plan with Playwright (test world world_scenes; desktop, iPhone/Chromium,
iPhone/WebKit).

    python tests/ui/ui_scenes.py [path-to-comfyos]

Checks page title, room cards of the home page (heads of equal height, glowing background, scene chips, lamp rows,
room light sheet), group badges and shadows in the floor plan, lamp sheet (gradient, colour wheel, effects, brightness, white tone),
page "Szenen" (studio, library with tabs, Hue import without duplicates) and the floor plan editor (grid on/off,
select all, scale: symbols stay in the room, rooms keep name and link). Screenshots: tests/shots/lights-*.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import ui, world_scenes as world  # noqa: E402

ROOT = world.ROOT
g = vars(world)        # mocks, app and helpers of the test world – the same as in tests/core/test_scenes.py
main, auth, core, puts, LIGHTS, BR = g["main"], g["auth"], g["core"], g["puts"], g["LIGHTS"], g["BR"]
hb_writes = g["hb_writes"]
ch, THINGS, ITEMS = g["ch"], g["THINGS"], g["ITEMS"]

from app import floorplan_store, plugins  # noqa: E402
import httpx  # noqa: E402

# In addition: a thermostat with a reported fault E1 and a window contact without connection (for the group badges)
THINGS += [{"UID": "dect:thermostat:t1", "thingTypeUID": "dect:thermostat", "label": "Heizung", "location": "wohnen",
            "statusInfo": {"status": "ONLINE"}, "channels": [ch("temperature", "Number", "T_temp"),
                                                             ch("heatingTemperature", "Number", "T_set"),
                                                             ch("alertState-E1", "Switch", "T_e1")]},
           {"UID": "zigbee:contact:c1", "thingTypeUID": "zigbee:contact", "label": "Fenster", "location": "wohnen",
            "statusInfo": {"status": "OFFLINE"}, "channels": [ch("contact", "Contact", "C_st")]}]
ITEMS += [{"name": "T_temp", "state": "21.5"}, {"name": "T_set", "state": "20"}, {"name": "T_e1", "state": "ON"},
          {"name": "C_st", "state": "CLOSED"}]

STRIP, DECKE, AMB = f"hue:0210:{BR}:1", f"hue:0210:{BR}:2", f"hue:0210:{BR}:3"
PLAN = {"width": 20, "height": 12, "heatmap": False,
        "walls": [{"id": "w1", "pts": [[1, 1], [19, 1], [19, 11], [1, 11], [1, 1]]},
                  {"id": "w2", "pts": [[10, 1], [10, 11]]}],
        "rooms": [{"id": "r1", "name": "Wohnzimmer", "room_id": "wohnen", "x": 5, "y": 6},
                  {"id": "r2", "name": "Flur", "room_id": "flur", "x": 15, "y": 6}],
        "openings": [], "devices": {AMB: {"x": 15, "y": 4}},
        "groups": [{"id": "g1", "name": "Heizecke", "x": 4, "y": 8, "members": ["dect:thermostat:t1", "zigbee:contact:c1"]},
                   {"id": "g2", "name": "Licht", "x": 6, "y": 3, "members": [STRIP, DECKE]}]}
floorplan_store.save(PLAN)


async def _no_step(now=None):
    """Replacement for the step of the effects: does nothing."""
    return 0
core.scene_step = _no_step   # do not turn dynamic scenes further in the background (deterministic PUT list)

from playwright.sync_api import sync_playwright  # noqa: E402

server, PORT = ui.serve(main.app)        # eigener freier Port je Lauf
hb = g["MockHB"]("https://qivicon", "", "x")
hb.room_names_provider = core.room_names
g["install_homebase"](hb)
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
api = httpx.Client(base_url=BASE, cookies={auth.COOKIE: token}, timeout=15)
r = api.post("/api/plugins/hue/pair", json={"host": "192.0.2.10"}).json()
assert r.get("connected"), r

SHOTS = HERE.parent / "shots"
SHOTS.mkdir(exist_ok=True)
for f in SHOTS.glob("lights-*.png"):
    f.unlink()
problems = []


def wait_for(pred, timeout=6.0):
    """Wait until the condition holds; False after the time is up."""
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def scenes():
    """The scenes as the API returns them."""
    return api.get("/api/scenes").json()


def run(p, engine, name, **ctx_args):
    # The mock bridge remembers effects (since 0.15) – every browser pass starts without a running effect
    """One pass "light & scenes" in one browser: light control, scenes, effects, studio, Hue import."""
    for li in LIGHTS.values():
        li.pop("fx_status", None)
    plugins.active("hue").bridge.forget_lights()
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(**ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    errs = []
    # scene images the bridge does not deliver answer with 404 on purpose (fallback: colour gradient) – not an error
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and "/api/scenes/image/" not in (m.location or {}).get("url", "") else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    shot = lambda n: page.screenshot(path=str(SHOTS / f"lights-{name}-{n}.png"))  # noqa: E731
    bad = lambda msg: problems.append(f"{name}: {msg}")  # noqa: E731
    overflow = lambda: page.evaluate("document.documentElement.scrollWidth - window.innerWidth")  # noqa: E731
    close_sheet = lambda: (page.click(".sheet [data-close]"), page.wait_for_selector(".sheet", state="detached"))  # noqa: E731

    # room tiles have always been open since 0.13.1 (no longer collapsible); since 0.19.3 without a sort handle (arranging in the head "Geräte")
    def expand_rooms():
        closed = page.evaluate("document.querySelectorAll('.room-card .rc-body').length"
                               " - [...document.querySelectorAll('.room-card .rc-body')]"
                               ".filter(b => b.offsetHeight > 0).length")
        if closed:
            bad(f"{closed} room tiles are collapsed – tiles should always be open")
        if page.locator(".room-card [data-grip], .rc-grip").count():
            bad("room tiles still carry the old sort handle")

    # ---------- Home page: title, room cards (heads of equal height, glowing background), group badges
    page.goto(BASE + "/")
    page.wait_for_selector(".room-card .rc-head")
    expand_rooms()
    if page.title() != "ComfyOS":
        bad(f"Seitentitel '{page.title()}' statt 'ComfyOS'")
    if not page.locator(".rc-head.lit").count():
        bad("Raumkopf ohne Leuchthintergrund, obwohl Lampen an sind")
    heights = page.eval_on_selector_all(".rc-head", "els => els.map(e => Math.round(e.getBoundingClientRect().height))")
    if len(set(heights)) != 1:
        bad(f"room heads of different height: {heights}")
    room = page.locator(".room-card", has=page.locator('[data-room-head="wohnen"]'))
    if room.locator(".lt-lamp").count() < 4:
        bad(f"Raumkarte Wohnzimmer zeigt nur {room.locator('.lt-lamp').count()} Lampen")
    if not room.locator(".tiles.sm .tile").count():
        bad("other devices (socket) are missing as small tiles")
    if overflow() > 1:
        bad(f"Startseite scrollt horizontal ({overflow()} px)")
    # arranging room tiles (since 0.19.3 as on the System page): arrow "down" on the first tile, saved per number of columns
    tiles = lambda: page.evaluate("() => [...document.querySelectorAll('#devBox > .mz-col')].map(c => [...c.children].map(t => t.id))")  # noqa: E731
    if sum(len(c) for c in tiles()) > 1:
        page.wait_for_timeout(1500)       # right after loading "reload" and lamp events redraw the cards
        page.click('[data-act="arrange"]')
        col = next((c for c in tiles() if len(c) > 1), None)     # a column with at least two tiles (otherwise: to the right)
        first = (col or tiles()[0])[0]
        page.locator(f'#{first} [data-mz="{"down" if col else "right"}"]').click()
        page.wait_for_timeout(900)
        now = tiles()
        saved = page.evaluate("() => fetch('/api/settings').then(r => r.json()).then(s => ((s.layout || {}).home) || {})")
        if (col and not any(c[1:2] == [first] for c in now)) or (not col and first not in now[-1]):
            bad(f"the down arrow does not rearrange the room tiles: {now}")
        elif now not in saved.values():
            bad(f"the arrangement of the room tiles did not reach the server: {saved}")
        page.click('[data-act="arrangeAuto"]')
        page.click('[data-act="arrange"]')
        if page.locator("#devBox .mz-over").count():
            bad("drag areas stay after „Fertig“")

    # floor plan: shadows by day, device shadows only inside rooms
    el = page.evaluate("(() => { const g = HO.state.overview.geo; return HO.sunPos(new Date(), g[0], g[1]).el })()")
    if el > 1 and not page.locator("#fpHost .fp-shadow rect[mask]").count():
        bad(f"sun {el:.0f}° high, but no shadow in the floor plan")
    # a bubble casts a disc, a plan symbol its own outline (since 0.29, `.sym`) – both clipped to their room
    if not page.locator("#fpHost .fp-devshadow circle[clip-path], #fpHost .fp-devshadow .sym[clip-path]").count():
        bad("device symbols without a clipped shadow")
    if page.locator("#fpHost .fp-dev.symbol").count() and not page.locator("#fpHost .fp-devshadow .sym[clip-path] path").count():
        bad("a plan symbol casts no shadow of its own outline")
    page.wait_for_selector('#fpHost .fp-group[data-id="g1"]')
    badge = page.evaluate("""() => { const g = document.querySelector('#fpHost .fp-group[data-id="g1"]'),
        l = document.querySelector('#fpHost .fp-group[data-id="g2"]');
      const has = (el, c) => !!el.querySelector('.fp-badge.' + c)
      return { count: (g.querySelector('.fp-badge.count text') || {}).textContent, err: has(g, 'error'), off: has(g, 'offline'),
               lcount: (l.querySelector('.fp-badge.count text') || {}).textContent, lerr: has(l, 'error') || has(l, 'offline') } }""")
    if badge["count"] != "2" or not badge["err"] or not badge["off"]:
        bad(f"Gruppe mit Fehler + offline: Badges {badge}")
    if badge["lcount"] != "2" or badge["lerr"]:
        bad(f"Lampengruppe: Badges {badge}")
    wait_for(lambda: page.evaluate("[...document.images].every(i => i.complete)"))
    shot("1-home")

    # zoom: only the section changes, the surface on the page stays the same size
    box = lambda: page.evaluate("""(() => { const s = document.querySelector('#fpHost svg.fp-svg'), r = s.getBoundingClientRect()
      return [Math.round(r.width), Math.round(r.height), s.viewBox.baseVal.width] })()""")  # noqa: E731
    b0 = box()
    page.click('[data-act="fpZoomIn"]'); page.click('[data-act="fpZoomIn"]')
    b1 = box()
    if b1[:2] != b0[:2] or not b1[2] < b0[2] * 0.8:
        bad(f"zoom enlarges the area instead of the floor plan: before {b0}, after {b1}")
    page.click('[data-act="fpZoomOut"]'); page.click('[data-act="fpZoomOut"]'); page.click('[data-act="fpZoomOut"]')
    b2 = box()
    if b2[:2] != b0[:2] or not b2[2] > b0[2]:
        bad(f"zooming out changes the area: before {b0}, after {b2}")
    page.click('[data-act="fpZoomIn"]')

    # switching a lamp on: the switch does not jump back (not even if a reload happens meanwhile), exactly one command
    tog = room.locator('[data-lt-toggle][aria-pressed="false"]').first
    if tog.count():
        lid = tog.get_attribute("data-lt-toggle")
        sel = f'.room-card [data-lt-toggle="{lid}"]'
        n0 = len(puts) + len(hb_writes)   # bridge or Home Base lamp
        page.evaluate("""sel => { window.__seq = []; const t0 = Date.now()
          const iv = setInterval(() => { const b = document.querySelector(sel); window.__seq.push(b ? b.getAttribute('aria-pressed') : '-')
            if (Date.now() - t0 > 3500) clearInterval(iv) }, 50) }""", sel)
        page.click(sel)
        for _ in range(3):
            time.sleep(0.5)
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")   # Nachladen mitten im Schalten
        time.sleep(2.2)
        seq = [x for x in page.evaluate("window.__seq") if x != '-']
        first = seq.index("true") if "true" in seq else -1
        if first < 0 or "false" in seq[first:]:
            bad(f"the lamp jumps back after switching on: {''.join('1' if x == 'true' else '0' for x in seq)}")
        if len(puts) + len(hb_writes) - n0 != 1:
            bad(f"Ein Tippen schickte {len(puts) + len(hb_writes) - n0} Befehle")
        page.click(sel)
        if not wait_for(lambda: page.get_attribute(sel, "aria-pressed") == "false", 4):
            bad("the lamp cannot be switched off again")
    else:
        bad("no switched-off lamp in the Wohnzimmer for the switching test")

    # list view: compact room cards, all devices as rows
    page.click('[data-view="list"]')
    page.wait_for_selector(".room-card.compact .rc-list .dev-row")
    lroom = page.locator(".room-card", has=page.locator('[data-room-head="wohnen"]'))
    if lroom.locator(".lt-lamp, .tiles.sm, input.lt-bri").count():
        bad("the list view still shows lamp rows, tiles or brightness")
    if lroom.locator(".rc-list .dev-row").count() < 5:
        bad(f"list view Wohnzimmer with only {lroom.locator('.rc-list .dev-row').count()} rows")
    lh = page.eval_on_selector_all(".rc-head", "els => els.map(e => Math.round(e.getBoundingClientRect().height))")
    if not lh or max(lh) >= heights[0]:
        bad(f"list view: heads not more compact ({lh} instead of < {heights[0]})")
    if overflow() > 1:
        bad(f"Listenansicht scrollt horizontal ({overflow()} px)")
    shot("1b-liste")
    page.click('[data-view="tiles"]')
    page.wait_for_selector(".room-card:not(.compact) .lt-lamp")

    # the group sheet of a lamp group offers scenes
    page.evaluate("""() => { const el = document.querySelector('#fpHost .fp-group[data-id="g2"]'), b = el.getBoundingClientRect()
      const o = { bubbles: true, clientX: b.x + b.width / 2, clientY: b.y + b.height / 2, pointerId: 1, pointerType: 'mouse', isPrimary: true, button: 0 }
      el.dispatchEvent(new PointerEvent('pointerdown', { ...o, buttons: 1 })); el.dispatchEvent(new PointerEvent('pointerup', o)); el.dispatchEvent(new MouseEvent('click', o)) }""")
    try:
        page.wait_for_selector(".sheet .sheet-scenes [data-sc-group]", timeout=4000)
        close_sheet()
    except Exception:
        bad("group sheet of the lamp group without scene chips")
        if page.locator(".sheet").count():
            close_sheet()

    # tapping the room head: light settings of the room with lamps and their own brightness
    room.locator(".rc-head").click(position={"x": 30, "y": 20})
    try:
        page.wait_for_selector(".sheet .rs-lamps .rs-lamp", timeout=4000)
        if "Wohnzimmer" not in page.inner_text(".sheet .sheet-head h3"):
            bad(f"the room light sheet is called '{page.inner_text('.sheet .sheet-head h3')}'")
        if not page.locator(".sheet [data-rs-lbri]").count():
            bad("Raum-Lichtsheet ohne Helligkeitsregler je Lampe")
        shot("2-raumlicht")
        close_sheet()
    except Exception:
        bad("tapping the room head opens no light setting")
        if page.locator(".sheet").count():
            close_sheet()

    # start a scene in the room card and end it by tapping again
    chip = room.locator('[data-sc-apply="entspannen"][data-sc-room="wohnen"]')
    if not chip.count():
        chip = room.locator('[data-sc-room="wohnen"]').first
    sid = chip.get_attribute("data-sc-apply")
    puts.clear()
    chip.click()
    if not wait_for(lambda: (scenes()["active"].get("room:wohnen") or {}).get("scene") == sid):
        bad(f"scene {sid} did not become active in the Wohnzimmer")
    if not wait_for(lambda: len(puts) >= 2):
        bad(f"the scene only sent {len(puts)} commands to the bridge")
    page.wait_for_selector(f'.room-card [data-sc-apply="{sid}"][data-sc-room="wohnen"].active')
    sname = next(s["name"] for s in scenes()["scenes"] if s["id"] == sid)
    if not wait_for(lambda: any(sname in t for t in page.locator(".room-card .rc-sub").all_inner_texts())):
        bad(f"Raumkarte nennt die aktive Szene '{sname}' nicht")
    if len(set(page.eval_on_selector_all(".rc-head", "els => els.map(e => Math.round(e.getBoundingClientRect().height))"))) != 1:
        bad("room heads with an active scene of different height")
    seen = page.evaluate("""sid => { const a = document.querySelector(`.room-card [data-sc-room="wohnen"][data-sc-apply="${sid}"]`),
        row = a.closest('[data-sc-row]'), r = row.getBoundingClientRect(), b = a.getBoundingClientRect()
      return b.left >= r.left - 1 && b.right <= r.right + 1 }""", sid)
    if not seen:
        bad("the active scene chip lies outside the visible chip row")
    if any(t.strip().replace(" ", " ") == "An · 0 %" for t in room.locator(".lt-lamp .muted").all_inner_texts()):
        bad("Lampe zeigt 'An · 0 %'")
    shot("3-szene-aktiv")
    room.locator(f'[data-sc-apply="{sid}"][data-sc-room="wohnen"]').click()
    if not wait_for(lambda: "room:wohnen" not in scenes()["active"]):
        bad("Antippen der aktiven Szene beendet sie nicht")
    page.wait_for_selector(f'.room-card [data-sc-apply="{sid}"][data-sc-room="wohnen"]:not(.active)')

    # Lampen-Sheet: Lightstrip mit Verlauf, Farbrad, Effekten
    page.click(f'[data-lt-open="{STRIP}"]')
    page.wait_for_selector(".sheet [data-lc]")
    tabs = page.eval_on_selector_all(".sheet [data-lc-tab]", "els => els.map(e => e.dataset.lcTab)")
    if tabs != ["white", "color", "fx"]:
        bad(f"Lightstrip-Tabs {tabs}")
    page.click('.sheet [data-lc-tab="color"]')
    page.wait_for_selector(".sheet [data-lc-wheel] canvas")
    if page.locator(".sheet .lc-seg").count() != 5:
        bad(f"Lightstrip zeigt {page.locator('.sheet .lc-seg').count()} statt 5 Segmente")
    page.click('.sheet [data-lc-seg="0"]')
    box = page.locator(".sheet [data-lc-wheel] canvas").bounding_box()
    if not box or box["width"] < 150:
        bad(f"Farbrad zu klein: {box}")
    puts.clear()
    page.mouse.click(box["x"] + box["width"] * 0.9, box["y"] + box["height"] / 2)   # rechts = Rot
    if not wait_for(lambda: any(n == 1 and "gradient" in b for n, b in puts)):
        bad(f"Farbrad auf Segment 1 schickt keinen Verlauf: {puts}")
    else:
        pts = next(b for n, b in puts if n == 1 and "gradient" in b)["gradient"]["points"]
        if len(pts) != 5 or pts[0]["color"]["xy"] == pts[1]["color"]["xy"]:
            bad(f"segment 1 not coloured individually: {pts}")
    if overflow() > 1:
        bad(f"Lampen-Sheet scrollt horizontal ({overflow()} px)")
    shot("4-strip-farbe")
    page.click('.sheet [data-lc-tab="fx"]')
    puts.clear()
    page.click('.sheet [data-lc-fx="candle"]')
    if not wait_for(lambda: any(n == 1 and (b.get("effects") or {}).get("effect") == "candle" for n, b in puts)):
        bad(f"Effekt Kerze nicht gesendet: {puts}")
    page.wait_for_selector('.sheet [data-lc-fx="candle"].active')
    shot("5-strip-effekt")
    puts.clear()
    page.evaluate("""() => { const r = document.querySelector('.sheet [data-lc-bri]'); r.value = 30
      r.dispatchEvent(new Event('input', { bubbles: true })); r.dispatchEvent(new Event('change', { bubbles: true })) }""")
    if not wait_for(lambda: any(n == 1 and (b.get("dimming") or {}).get("brightness") == 30 for n, b in puts)):
        bad(f"Helligkeit 30 % nicht gesendet: {puts}")
    if page.inner_text(".sheet [data-lc-val]").strip() != "30 %":
        bad(f"Helligkeitsanzeige '{page.inner_text('.sheet [data-lc-val]')}'")
    close_sheet()

    # white tone lamp: no colour wheel, quick choice warm white
    page.click(f'[data-lt-open="{AMB}"]')
    page.wait_for_selector(".sheet [data-lc]")
    if page.locator('.sheet [data-lc-tab="color"], .sheet [data-lc-wheel]').count():
        bad("a white-tone lamp shows a colour wheel")
    puts.clear()
    page.click('.sheet [data-lc-k="2700"]')
    if not wait_for(lambda: any(n == 3 and (b.get("color_temperature") or {}).get("mirek") == 370 for n, b in puts)):
        bad(f"warm white not sent as 370 mirek: {puts}")
    close_sheet()

    # ---------- Page "Szenen": head with import/new, studio only after "Neu", loading a card, Hue import with tabs
    page.evaluate("HO.go('light')")
    page.wait_for_selector('.section-head [data-act="scNew"]')
    if page.title() != "ComfyOS · Szenen":
        bad(f"Seitentitel '{page.title()}' statt 'ComfyOS · Szenen'")
    if page.locator(".room-card, .lt-room").count():
        bad("Szenen-Seite zeigt noch Raumkacheln")
    if page.locator("#scStudio").is_visible():
        bad("the studio is already open without „Neu“")
    page.click('[data-act="scNew"]')
    page.wait_for_selector("#scStudio #scName")
    page.fill("#scStudio #scName", f"UI {name}")
    page.click('#scStudio [data-add="ct"]')
    page.wait_for_selector("#scStudio #scCt")
    if page.locator("#scStudio .sc-sw").count() != 4:
        bad(f"Studio zeigt {page.locator('#scStudio .sc-sw').count()} statt 4 Farben")
    page.check("#scStudio #scDyn")
    if page.locator("#scStudio #scSpeed").is_hidden():
        bad("Tempo-Auswahl bleibt bei dynamischer Szene verborgen")
    page.click('#scStudio #scRooms [data-room="wohnen"]')
    if overflow() > 1:
        bad(f"Szenen-Seite scrollt horizontal ({overflow()} px)")
    shot("6-studio")
    hit = page.evaluate("""() => { const b = document.querySelector('#scStudio [data-st="save"]'); b.scrollIntoView({ block: 'center' })
      const r = b.getBoundingClientRect(), t = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
      return t && (t.closest('[data-st]') || t).outerHTML.slice(0, 120) }""")
    page.click('#scStudio [data-st="save"]')
    try:
        page.wait_for_selector(f'.sc-card:has-text("UI {name}")', timeout=8000)
    except Exception:
        print(f"DIAG {name}: Treffer {hit!r}; Name '{page.input_value('#scStudio #scName')}', Toasts {page.locator('.toast').all_inner_texts()}")
        shot("6b-speichern-fehlt")
        bad(f"scene not in the library after saving; name in the field '{page.input_value('#scStudio #scName')}', "
            f"Toasts {page.locator('.toast').all_inner_texts()}, API {[s['name'] for s in scenes()['scenes']]}")
    made = next((s for s in scenes()["scenes"] if s["name"] == f"UI {name}"), None)
    if not made or len(made["palette"]) != 4 or not made["dynamic"] or made["rooms"] != ["wohnen"] or made.get("source"):
        bad(f"Gespeicherte Szene falsch: {made}")
    if page.locator("#scStudio").is_visible():
        bad("studio not closed after creating")
    page.click(f'.sc-card:has-text("UI {name}")')
    page.wait_for_selector('#scStudio [data-st="delete"]')
    if not wait_for(lambda: page.input_value("#scStudio #scName") == f"UI {name}" and page.locator(".sc-card.editing").count() == 1):
        bad("tapping a scene card does not load it into the studio")
    page.click('#scStudio [data-st="new"]')
    if page.locator("#scStudio").is_visible():
        bad("cancel does not close the studio")
    if made:
        api.delete(f"/api/scenes/{made['id']}")

    page.click('[data-act="scImport"]')
    page.click('.sheet .menu-list button:has-text("Hue")')
    page.wait_for_selector(".sheet .sc-imp")
    names = page.eval_on_selector_all(".sheet .sc-imp b", "els => els.map(e => e.textContent)")
    if names.count("Tropendämmerung") != 1:
        bad(f"import list: Tropendämmerung {names.count('Tropendämmerung')}× instead of once: {names}")
    shot("7-import")
    page.click(".sheet .sheet-foot .btn.primary")
    page.wait_for_selector('[data-sc-tab="hue"]')
    page.click('[data-sc-tab="hue"]')
    page.wait_for_selector('.sc-card:has-text("Tropendämmerung") .sc-src')
    n_hue = sum(1 for s in scenes()["scenes"] if s.get("source") == "hue")
    if not wait_for(lambda: page.locator(".sc-card").count() == n_hue):
        bad(f"Reiter Hue zeigt {page.locator('.sc-card').count()} Karten statt {n_hue}")
    shot("8-hue-reiter")
    page.click('[data-sc-tab="all"]')
    for s in scenes()["scenes"]:
        if s.get("source") == "hue":
            api.delete(f"/api/scenes/{s['id']}")

    # back to the home page: the title follows the page
    page.evaluate("HO.go('home')")
    page.wait_for_selector(".room-card .rc-head")
    if page.title() != "ComfyOS":
        bad(f"title after returning '{page.title()}'")

    # ---------- Floor plan editor (desktop): grid on/off, select all, scale to 50 %
    if name == "desktop":
        page.click('[data-act="fpEdit"]')
        page.wait_for_selector('.fp-tools [data-act="selall"]')
        page.click('[data-act="gridsnap"]')
        if page.evaluate("localStorage.getItem('ho.fpSnap')") != "0" or page.locator('[data-act="gridsnap"].primary').count():
            bad("Raster-Schalter schaltet nicht aus")
        page.click('[data-act="gridsnap"]')
        if not page.locator('[data-f="north"]').count() or not page.locator('[data-f="shadow"]').count():
            bad("editor without settings for north/shadow")
        page.click('[data-act="selall"]')
        page.wait_for_selector("#fpHost .fp-mbox")
        wait_for(lambda: not page.locator(".toast").count(), 8)   # toasts otherwise lie over the corner handle
        page.evaluate("document.querySelector('#fpHost').scrollIntoView({ block: 'start' })")
        if page.locator("#fpHost .fp-mhandle").count() != 8:
            bad(f"Auswahlrahmen mit {page.locator('#fpHost .fp-mhandle').count()} statt 8 Griffen")
        shot("9-editor-auswahl")
        to_screen = lambda x, y: page.evaluate("""([x, y]) => { const m = document.querySelector('#fpHost svg.fp-svg').getScreenCTM()
            return [m.a * x + m.e, m.d * y + m.f] }""", [x, y])  # noqa: E731
        hb_ = page.locator('#fpHost .fp-mhandle[data-c="se"]').bounding_box()
        h = [hb_["x"] + hb_["width"] / 2, hb_["y"] + hb_["height"] / 2]
        corner, target = to_screen(19, 11), to_screen(10, 6)
        page.mouse.move(*h)
        page.mouse.down()
        for k in range(1, 9):
            f = k / 8
            page.mouse.move(h[0] + (target[0] - corner[0]) * f, h[1] + (target[1] - corner[1]) * f)
        page.mouse.up()

        def plan_ok():
            fp = api.get("/api/floorplan").json()
            return fp if abs(fp["devices"].get(AMB, {}).get("x", 0) - 8) < 0.3 else None
        if not wait_for(plan_ok, 4):
            bad(f"Skalieren: Ambiance nicht mitgewandert: {api.get('/api/floorplan').json()['devices']}")
        else:
            fp = plan_ok()
            amb, g1 = fp["devices"][AMB], next(g for g in fp["groups"] if g["id"] == "g1")
            w1 = next(w for w in fp["walls"] if w["id"] == "w1")["pts"]
            if abs(amb["y"] - 2.5) > 0.3 or abs(g1["x"] - 2.5) > 0.3 or abs(g1["y"] - 4.5) > 0.3:
                bad(f"symbols not at their place in the room: Ambiance {amb}, Heizecke {g1}")
            if abs(w1[2][0] - 10) > 0.3 or abs(w1[2][1] - 6) > 0.3:
                bad(f"outer wall not scaled: {w1}")
            links = sorted((r["name"], r["room_id"]) for r in fp["rooms"])
            if links != [("Flur", "flur"), ("Wohnzimmer", "wohnen")]:
                bad(f"rooms lost name/link: {links}")
        shot("10-editor-skaliert")
        page.click('[data-act="fpEdit"]')
        time.sleep(0.8)
        floorplan_store.save(PLAN)   # next passes with the initial plan

    errs = [e for e in errs if "favicon" not in e]
    print(f"{name}: Konsolenfehler: {len(errs)}")
    for e in errs[:5]:
        bad(f"Konsole: {e[:200]}")
    browser.close()


with sync_playwright() as p:
    run(p, "chromium", "desktop", viewport={"width": 1366, "height": 900})
    run(p, "chromium", "mobil", **p.devices["iPhone 13"])
    run(p, "webkit", "safari", **p.devices["iPhone 13"])

server.should_exit = True
if problems:
    print("PROBLEME:")
    for x in problems:
        print("  - " + x)
else:
    print("UI OK – no problems, no console errors")
