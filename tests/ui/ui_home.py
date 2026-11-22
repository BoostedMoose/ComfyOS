"""UI check of the home page with Playwright: floor plan, weather, weather effects, weather warning (test world world_hue).

    python tests/ui/ui_home.py [path-to-comfyos]

Checks per browser (desktop/Chromium, iPhone/WebKit): every preview renders by day and by night without
console errors, inside the rooms the canvas stays empty, outside it is drawn; from 10 km/h of wind
wind lines move in a flow field around the building (small grid, building as an obstacle); at night a soft
halo lies around the building instead of the cast shadow; with prefers-reduced-motion only one calm image is created
without animation frames (no requestAnimationFrame of the module); the button "Wetter" switches the effects. Since
0.20.1: wind arrow on the floor plan, leaves from 15 km/h, puddles and ripples in rain; the page keeps its scroll
position when the room tiles are rewritten.
Plus the official weather warning (DWD as a mock): hint above the main tile with an open window, details,
hiding. The frame of the floor plan reaches from tile edge to tile edge; scenes and key figures are
below, the overall state in the tile "Geräte online". Screenshots in tests/shots/.
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import ui, world_hue as world  # noqa: E402

ROOT = world.ROOT
g = vars(world)        # mocks, app and helpers of the test world – the same as in tests/plugins/test_hue.py
main, auth = g["main"], g["auth"]

from app import core, floorplan_store, weather  # noqa: E402
import httpx  # noqa: E402


def wx_handler(req):
    """Responses of place search and weather service: rain with wind, so that effects and forecast can be seen."""
    if req.url.host.startswith("geocoding"):
        return httpx.Response(200, json={"results": [{"name": "Berlin", "latitude": 52.52, "longitude": 13.41,
                                                      "country_code": "DE", "postcodes": ["10115"]}]})
    day0 = time.strftime("%Y-%m-%d")
    day1 = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
    hours = [f"{day0 if h < 24 else day1}T{h % 24:02d}:00" for h in range(48)]
    return httpx.Response(200, json={"current": {"time": time.strftime("%Y-%m-%dT%H:%M"), "temperature_2m": 7.4, "weather_code": 63,
                                                 "is_day": 1, "rain": 2.0, "cloud_cover": 90, "wind_speed_10m": 20,
                                                 "wind_gusts_10m": 46, "wind_direction_10m": 250,
                                                 "apparent_temperature": 4.9, "relative_humidity_2m": 81},
                                     "hourly": {"time": hours, "temperature_2m": [5 + (h % 24) / 4 for h in range(48)],
                                                "precipitation_probability": [(h * 7) % 100 for h in range(48)],
                                                "precipitation": [0.2] * 48, "weather_code": [61, 3, 2, 0] * 12,
                                                "is_day": [1 if 8 <= h % 24 < 17 else 0 for h in range(48)],
                                                "wind_speed_10m": [12 + h % 9 for h in range(48)], "wind_gusts_10m": [30] * 48,
                                                "wind_direction_10m": [250] * 48},
                                     "daily": {"temperature_2m_max": [9.1], "temperature_2m_min": [2.3]}})


def warn_handler(req):
    """Official warning of the DWD (mock): gusts, running for half an hour."""
    iso = lambda dt: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + dt))   # noqa: E731
    return httpx.Response(200, json={"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None, "properties": {
        "EVENT": "STURMBÖEN", "HEADLINE": "Amtliche WARNUNG vor STURMBÖEN", "SEVERITY": "Moderate", "EC_GROUP": "WIND",
        "ONSET": iso(-1800), "EXPIRES": iso(7200), "STATUS": "Actual", "MSGTYPE": "Alert", "IDENTIFIER": "ui-test-sturm",
        "DESCRIPTION": "Es treten Sturmböen mit Geschwindigkeiten um 70 km/h aus westlicher Richtung auf.",
        "INSTRUCTION": "Achten Sie besonders auf herabfallende Gegenstände."}}]})


weather._client = lambda timeout=6.0: httpx.AsyncClient(transport=httpx.MockTransport(wx_handler))
weather._warn_client = lambda timeout=6.0: httpx.AsyncClient(transport=httpx.MockTransport(warn_handler))
# small house in the middle of a large area: there is room for effects outside
floorplan_store.save({"width": 24, "height": 14, "address": "Musterstraße 1, 10115 Berlin",
                      "walls": [{"id": "w1", "pts": [[7, 3], [17, 3], [17, 11], [7, 11], [7, 3]]},
                                {"id": "w2", "pts": [[12, 3], [12, 11]]}],
                      "rooms": [{"id": "r1", "name": "Küche", "room_id": "kueche", "x": 9.5, "y": 7},
                                {"id": "r2", "name": "Wohnzimmer", "room_id": "wohnen", "x": 14.5, "y": 7}],
                      "openings": [], "devices": {}})

from playwright.sync_api import sync_playwright  # noqa: E402

server, PORT = ui.serve(main.app)        # eigener freier Port je Lauf
mock = g["MockHB"]("https://qivicon", "", "x")
mock.room_names_provider = core.room_names
g["install_homebase"](mock)
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = HERE.parent / "shots"
SHOTS.mkdir(exist_ok=True)
problems = []

# previews with wind from 10 km/h (fx.wind ≥ 0.125): wind lines move around the house there
WINDY = {"partly", "rain", "sleet", "snow", "breeze", "storm", "thunder", "hail"}

# alpha sum inside (centre of every room, 24 × 24 px) and number of drawn pixels outside
PIXELS_JS = """() => {
  const cv = document.querySelector('#fpHost canvas.fp-fx')
  if (!cv) return null
  const ctx = cv.getContext('2d'), base = cv.getBoundingClientRect(), k = cv.width / base.width
  let inside = 0
  for (const r of document.querySelectorAll('#fpHost .fp-room .body')) {
    const b = r.getBoundingClientRect()
    const x = Math.round((b.left + b.width / 2 - base.left) * k), y = Math.round((b.top + b.height / 2 - base.top) * k)
    const d = ctx.getImageData(x - 12, y - 12, 24, 24).data
    for (let i = 3; i < d.length; i += 4) inside += d[i]
  }
  const all = ctx.getImageData(0, 0, cv.width, cv.height).data
  let drawn = 0
  for (let i = 3; i < all.length; i += 4) if (all[i]) drawn++
  const s = HO.weatherFx.stats, wind = document.querySelector('#fpHost .fp-wind:not(.hidden)')
  return { inside, drawn, frames: s.frames, wisps: s.wisps, cells: s.cells, solid: s.solid, leaves: s.leaves, puddles: s.puddles,
    ripples: s.ripples, rays: s.rays, blocked: s.blocked, frost: s.frost, windows: s.windows, beams: s.beams, moon: s.moon,
    sound: s.sound, haze: s.haze, wind: wind ? wind.getAttribute('title') : null }
}"""
# previews with wind from 15 km/h and without snow: leaves drift there; previews with rain: puddles and ripples
LEAFY = {"partly", "rain", "breeze", "storm", "thunder", "hail"}
WET = {"drizzle", "rain", "sleet", "storm", "thunder", "hail"}
# previews of a clear sky: by day sun rays are always to be seen (since 0.26.1)
SUNNY_DAY = {"clear", "frost", "heat"}
# previews in which the moon of the preview night shines (since 0.27): a clear or partly cloudy sky – since 0.27.5 also
# through the gaps of a broken cover while a little falls (drizzle, the storm); not under a closed cover or heavy rain/snow
MOONLIT = {"clear", "partly", "breeze", "frost", "heat", "drizzle", "storm"}


def run(p, engine, name, reduced=False, **ctx_args):
    """One pass of the home page in one browser: floor plan, weather, effects, tiles; deviations end up in
    problems."""
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(reduced_motion="reduce" if reduced else "no-preference", **ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE + "/#home")
    page.wait_for_selector("#fpHost .fp-wrap svg .fp-room", timeout=20000)
    page.wait_for_function("() => HO.state.weather && HO.state.weather.data", timeout=10000)   # as a function: the CSP allows no eval
    # the frame of the floor plan reaches from tile edge to tile edge (since 0.19.1), the plan stays centred in it
    frame = page.evaluate("""() => { const s = document.querySelector('#fpHost .fp-svg').getBoundingClientRect()
      const c = document.querySelector('#fpHost').closest('.card').getBoundingClientRect()
      const rooms = [...document.querySelectorAll('#fpHost .fp-room .body')].map(r => r.getBoundingClientRect())
      const mid = (Math.min(...rooms.map(r => r.left)) + Math.max(...rooms.map(r => r.right))) / 2
      return { left: Math.round(s.left - c.left), right: Math.round(c.right - s.right), off: Math.round(mid - (s.left + s.right) / 2) } }""")
    if not (0 <= frame["left"] <= 2 and 0 <= frame["right"] <= 2) or abs(frame["off"]) > 3:
        problems.append(f"{name}: floor plan frame not across the whole tile width or plan not centred: {frame}")
    # heat map scale (one source for image and legend): schedule span + measured temperatures, at least 4 °C
    hs = page.evaluate("""() => [HO.floorplan.heatScale([17, 22], [{kind: 'thermostat', online: true, temperature: 24.3}]),
      HO.floorplan.heatScale([21, 21], []), HO.floorplan.heatScale(null, []),
      HO.floorplan.heatScale([18, 22], [{kind: 'thermostat', online: false, temperature: 30}, {kind: 'sensor', online: true, temperature: 5}])]""")
    if hs != [[17, 22], [19, 23], [10, 25], [18, 22]]:      # only the heating schedule counts – a measured 24.3 °C shifts nothing
        problems.append(f"{name}: Heatmap-Skala falsch: {hs}")
    # official warning (DWD mock): hint above the main tile; without a window contact -> general window hint
    page.wait_for_selector("#warnBox .wx-warn", timeout=8000)
    wtxt = page.inner_text("#warnBox .wx-warn")
    if "STURMBÖEN" not in wtxt or "Stufe 2 von 4" not in wtxt or "Deutscher Wetterdienst" not in wtxt or "Fenster" not in wtxt:
        problems.append(f"{name}: weather warning incomplete: {wtxt!r}")
    page.screenshot(path=str(SHOTS / f"weather-{name}-warnung.png"))
    page.click('#warnBox [data-act="warn"]')
    page.wait_for_selector(".sheet .ww-item")
    if "70 km/h" not in page.inner_text(".sheet") or "herabfallende" not in page.inner_text(".sheet"):
        problems.append(f"{name}: Details der Warnung fehlen")
    page.click(".sheet [data-close]")
    page.wait_for_selector(".sheet", state="detached")
    page.click('#warnBox [data-act="warnHide"]')
    page.wait_for_timeout(200)
    if page.locator("#warnBox .wx-warn").count() or not page.locator("#fpFoot .fp-warn").count():
        problems.append(f"{name}: hiding: the note stays or the warning sign next to the weather is missing")
    # Structure of the home page (since 0.19.2): header without a tile (greeting, date), main tile with floor plan and below it home/away,
    # then "Licht & Szenen" and the key figures as tiles of their own. Below the floor plan only the weather warning is shown as a pill – on
    # the phone in one line with the weather, below the address. The connections are one pill in the top bar.
    lay = page.evaluate("""() => { const top = s => document.querySelector(s).getBoundingClientRect().top
      const note = document.querySelector('#infoBox .stat-status .stat-note'), card = s => document.querySelector(s).closest('.card')
      const wx = document.querySelector('#fpFoot .fp-wx').getBoundingClientRect(), warn = document.querySelector('#fpFoot .fp-warn').getBoundingClientRect()
      const addr = document.querySelector('#fpFoot .fp-addr').getBoundingClientRect()
      return { order: top('#heroBox') < top('#fpHost') && top('#fpHost') < top('#homeBox') && top('#homeBox') < top('#infoBox'),
        oneCard: card('#homeBox') === card('#fpHost') && !card('#heroBox') && !document.querySelector('#heroBox [data-home]'),
        heroStats: document.querySelectorAll('#heroBox .stats, #heroBox .house-scenes, #heroBox .conn-pill').length,
        split: !!document.querySelector('#infoBox .house-light [data-lights]') && !document.querySelector('#infoBox .house-light .stats')
          && !!document.querySelector('#infoBox .hero-info .stats'),
        pills: document.querySelectorAll('#fpFoot .fp-pills .status-pill').length,
        wxRow: Math.abs((wx.top + wx.bottom) / 2 - (warn.top + warn.bottom) / 2) < 6 && wx.top >= addr.top,
        conn: /^(Online|Fehler|Verbindet)$/.test(document.querySelector('#connPill b').textContent) && / – zu den Plugins$/.test(document.querySelector('#connPill').title),
        oldStatus: document.querySelectorAll('#fpFoot [data-act=issues], #fpFoot .fp-issues').length,
        note: note ? note.textContent.trim() : null } }""")
    if (not lay["order"] or not lay["oneCard"] or lay["heroStats"] or not lay["split"] or lay["pills"] != 1 or not lay["wxRow"] or not lay["conn"]
            or lay["oldStatus"] or lay["note"] not in ("Alles OK", "Handlungsbedarf")):
        problems.append(f"{name}: Aufbau der Startseite falsch: {lay}")
    # title: "ComfyOS" on the phone (header with greeting below), greeting and date at the top on the desktop, header hidden
    head = page.evaluate("""() => { const t = document.querySelector('#pageTitle'), shown = s => { const e = t.querySelector(s); return e && getComputedStyle(e).display !== 'none' ? e.textContent.trim() : '' }
      return { wide: window.innerWidth >= 900, title: shown('.pt-long') || shown('.pt-short'), hero: !!document.querySelector('#heroBox').offsetParent } }""")
    if page.title() != "ComfyOS" or (head["title"] == "ComfyOS") == head["wide"] or head["hero"] == head["wide"] \
            or (head["wide"] and not any(w in head["title"] for w in ("Guten Morgen", "Guten Tag", "Guten Abend"))):
        problems.append(f"{name}: Titel der Startseite falsch: {page.title()!r} {head}")
    # tools above the floor plan: only icons on the phone
    tools = page.evaluate("""() => ({ narrow: window.innerWidth <= 560,
      labels: [...document.querySelectorAll('.fp-tools .fp-tool .lbl')].filter(l => l.offsetParent).length })""")
    if tools["narrow"] != (tools["labels"] == 0):
        problems.append(f"{name}: Beschriftung der Grundriss-Werkzeuge falsch: {tools}")
    # the scroll position stays when a live message rewrites the room tiles (since 0.20.1: the tile grid fixes
    # its height while it rebuilds – in Safari and on the iPhone the page otherwise jumped to the top)
    jump = page.evaluate("""() => new Promise(done => {
      const root = document.documentElement
      window.scrollTo(0, root.scrollHeight)
      const before = window.pageYOffset, d = HO.state.overview.devices[0]
      d.name = d.name + ' (neu)'
      HO.lights.onScenes()
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const after = window.pageYOffset
        window.scrollTo(0, 0)
        done({ before: Math.round(before), after: Math.round(after) }) })) })""")
    if jump["before"] < 150 or abs(jump["after"] - jump["before"]) > 2:
        problems.append(f"{name}: the page jumps when the room tiles are rewritten (or is too short to scroll): {jump}")
    page.evaluate("localStorage.removeItem('ho.warnHide')")
    # weather today: wind report and hourly forecast (tapping the weather below the floor plan)
    page.click('#fpFoot [data-act="wx"]')
    page.wait_for_selector(".sheet .wx-hours .wx-hour")
    sheet = page.inner_text(".sheet")
    hrs = page.locator(".sheet .wx-hour").count()
    if "20 km/h" not in sheet or "WSW" not in sheet or "Böen bis 46 km/h" not in sheet or "mäßige Brise" not in sheet or not 1 <= hrs <= 24:
        problems.append(f"{name}: Wetter heute incomplete ({hrs} hours): {sheet[:160]!r}")
    page.locator(".sheet").screenshot(path=str(SHOTS / f"weather-{name}-heute.png"))
    page.click(".sheet [data-close]")
    page.wait_for_selector(".sheet", state="detached")
    # real weather (mock: rain) -> effects run without a preview
    page.wait_for_selector("#fpHost canvas.fp-fx", timeout=8000)
    kinds = page.evaluate("HO.weatherFx.KINDS")
    if len(kinds) != 14:
        problems.append(f"{name}: erwartet 14 Vorschauen, gefunden {kinds}")
    for tm in ("day", "night"):
        for kind in kinds:
            page.evaluate("([k, t]) => { HO.state.fxPreview = k; HO.state.fxTime = t; HO.go('home', true) }", [kind, tm])
            page.wait_for_selector("#fpHost canvas.fp-fx", timeout=8000)
            page.wait_for_timeout(900)
            px = page.evaluate(PIXELS_JS)
            if px is None:
                problems.append(f"{name}: {kind}/{tm}: kein Canvas")
                continue
            # The weather never gets inside. Since 0.27 light and sound do: by day the sky light at every window, a
            # patch of sunlight on a sunny day and of moonlight in a clear night, the waves of a speaker that plays. If
            # none of that is there (a night without a moon, nothing plays), the rooms stay untouched.
            if px["inside"] and not (px["windows"] or px["sound"]):
                problems.append(f"{name}: {kind}/{tm}: Effekte im Inneren eines Raums (Alpha-Summe {px['inside']})")
            if tm == "day" and not px["windows"]:
                problems.append(f"{name}: {kind}/{tm}: by day no window lets light in")
            if kind in SUNNY_DAY and not px["beams"]:
                problems.append(f"{name}: {kind}/{tm}: no patch of {'sun' if tm == 'day' else 'moon'}light behind a window")
            if tm == "night" and (bool(px["moon"]) != (kind in MOONLIT) or (px["windows"] and kind not in MOONLIT)):
                problems.append(f"{name}: {kind}/{tm}: moonlight wrong (moon {px['moon']}, windows {px['windows']})")
            # every weather shows something outside – calm too (without particles) and at night too (stars, cloud veil)
            if not px["drawn"]:
                problems.append(f"{name}: {kind}/{tm}: nothing drawn outside")
            # wind (since 0.19.1): lines in the flow field from 10 km/h – the grid knows the building and stays small
            if bool(px["wisps"]) != (kind in WINDY):
                problems.append(f"{name}: {kind}/{tm}: Windlinien falsch ({px['wisps']})")
            if kind in WINDY and not (0 < px["solid"] < px["cells"] <= 3200):
                problems.append(f"{name}: {kind}/{tm}: flow grid wrong (cells {px['cells']}, building {px['solid']})")
            # since 0.20.1: wind arrow on the floor plan, leaves from 15 km/h, puddles in rain – and ripples only where
            # a drop lands (with reduced motion nothing falls, so no ripple and no leaf either)
            if not px["wind"] or "km/h" not in px["wind"]:
                problems.append(f"{name}: {kind}/{tm}: Windpfeil fehlt ({px['wind']!r})")
            if bool(px["leaves"]) != (kind in LEAFY and not reduced):
                problems.append(f"{name}: {kind}/{tm}: leaves wrong ({px['leaves']})")
            if bool(px["puddles"]) != (kind in WET):
                problems.append(f"{name}: {kind}/{tm}: puddles wrong ({px['puddles']})")
            if bool(px["ripples"]) != (kind in WET and not reduced):
                problems.append(f"{name}: {kind}/{tm}: Tropfenkreise falsch ({px['ripples']})")
            # since 0.26.1: hoarfrost patches exactly with frost; on a sunny day sun rays, and the house throws its
            # shadow shaft into them (no ray shines through the building); at night no ray
            if bool(px["frost"]) != (kind == "frost"):
                problems.append(f"{name}: {kind}/{tm}: hoarfrost wrong ({px['frost']})")
            if tm == "day" and kind in SUNNY_DAY and not (px["rays"] > 0 and px["blocked"] > 0):
                problems.append(f"{name}: {kind}/{tm}: sun rays or their shadow shaft missing (rays {px['rays']}, blocked {px['blocked']})")
            if tm == "night" and px["rays"]:
                problems.append(f"{name}: {kind}/{tm}: sun rays at night ({px['rays']})")
            # since 0.27.1: in the heat of the day the walls in the sun give their heat off (wavy lines; they only exist
            # moving, so not with reduced motion) – and only then
            if bool(px["haze"]) != (kind == "heat" and tm == "day" and not reduced):
                problems.append(f"{name}: {kind}/{tm}: heat haze wrong ({px['haze']})")
            if name == "desktop" or (kind, tm) in (("rain", "day"), ("clear", "night")):
                page.locator("#fpHost").screenshot(path=str(SHOTS / f"weather-{name}-{kind}-{tm}.png"))
        # shadow: the cast shadow by day, a soft halo around the building at night
        night = page.locator("#fpHost .fp-shadow.night").count()
        if not page.locator("#fpHost .fp-shadow").count() or bool(night) != (tm == "night"):
            problems.append(f"{name}: Schatten bei {tm} falsch (Hof: {night})")
    page.evaluate("() => { HO.state.fxTime = null }")
    frames = page.evaluate("HO.weatherFx.stats.frames")
    if reduced and frames:
        problems.append(f"{name}: reduzierte Bewegung, trotzdem {frames} Animationsbilder")
    if not reduced and not frames:
        problems.append(f"{name}: keine Animation")
    # switching off in the floor plan settings: no canvas any more
    page.evaluate("""async () => { const p = Object.assign({}, HO.state.overview.floorplan, {fx: false})
      HO.state.overview.floorplan = await HO.api('floorplan', {method: 'PUT', body: p}); HO.state.fxPreview = null; HO.go('home', true) }""")
    page.wait_for_selector("#fpHost .fp-wrap svg .fp-room")
    page.wait_for_timeout(400)
    if page.locator("#fpHost canvas.fp-fx").count():
        problems.append(f"{name}: Wettereffekte aus, Canvas bleibt")
    # … and the same switch as the button "Wetter" above the floor plan: on -> canvas there, off -> gone, saved
    btn = page.locator('[data-act="fpFx"]')
    if btn.count() != 1 or btn.get_attribute("aria-pressed") != "false":
        problems.append(f"{name}: the button „Wetter“ is missing or shows the wrong state")
    else:
        btn.click()
        page.wait_for_selector("#fpHost canvas.fp-fx", timeout=4000)
        page.wait_for_timeout(700)
        if btn.get_attribute("aria-pressed") != "true" or page.evaluate("async () => (await HO.api('floorplan')).fx") is not True:
            problems.append(f"{name}: the button „Wetter“ does not switch on or does not save")
        btn.click()
        page.wait_for_timeout(700)
        if page.locator("#fpHost canvas.fp-fx").count() or page.evaluate("async () => (await HO.api('floorplan')).fx") is not False:
            problems.append(f"{name}: the button „Wetter“ does not switch off")
    page.evaluate("""async () => { const p = Object.assign({}, HO.state.overview.floorplan, {fx: true})
      HO.state.overview.floorplan = await HO.api('floorplan', {method: 'PUT', body: p}) }""")
    # full screen: title, weather and warning are pills of equal height; opened, wind and hours are below
    page.goto(BASE + "/#plan")
    page.wait_for_selector(".pv .pv-chip.warn", timeout=10000)
    page.wait_for_selector(".pv .fp-title:not(.hidden)", timeout=10000)
    hs_ = page.evaluate("""() => ['.pv .fp-title', '#pvWx .pv-chip:not(.warn)', '#pvWx .pv-chip.warn'].map(s => {
        const e = document.querySelector(s); return e ? Math.round(e.getBoundingClientRect().height) : null })""")
    if None in hs_ or len(set(hs_)) != 1:
        problems.append(f"{name}: Blasen im Vollbild ungleich hoch (Titel, Wetter, Warnung): {hs_}")
    page.click('#pvWx .pv-chip:not(.warn)')
    page.wait_for_selector("#pvWx .pv-wx-more .wx-hour")
    page.screenshot(path=str(SHOTS / f"weather-{name}-vollbild.png"))
    page.goto(BASE + "/#home")
    page.wait_for_selector("#fpHost .fp-wrap svg .fp-room", timeout=20000)
    print(f"{name}: {frames} animation frames, console errors: {len(errs)}")
    problems.extend(f"{name} Konsole: {e}" for e in errs)
    browser.close()


with sync_playwright() as p:
    run(p, "chromium", "desktop", viewport={"width": 1366, "height": 900})
    run(p, "chromium", "ruhig", reduced=True, viewport={"width": 1366, "height": 900})
    run(p, "webkit", "safari", **p.devices["iPhone 13"])

server.should_exit = True
print("\n".join(problems) if problems else "UI OK – no problems, no console errors")
