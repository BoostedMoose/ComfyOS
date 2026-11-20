"""Check images of the weather effects on the floor plan – to see what every weather looks like without waiting for the weather.

    <python-of-the-test-environment> dev/fx_shots.py [path-to-comfyos] [--only part,part]

Needs Playwright, so it runs on Windows with the Python of the fixed test environment (path: CLAUDE.md and
dev/README.md, section Testing) – never on the Pi. The images end up in tests/shots/effects/ (gitignored); old
images there are removed first. Shown is the demo home (tests/support/world_demo.py), as a smaller
house on a larger plot, so that there is room for the effects outside – sample data only, no internet.

Every image sets a weather through the preview (HO.state.fxPreview, as in the floor plan editor), waits until
the effects have developed, and photographs the floor plan. `--only` only takes images whose name contains one of
the parts (e.g. `--only regen,wind`).

What the images prove (stage 0.20.1):
  Rain        every drop ends in a ripple; puddles grow with the rain strength (drizzle, rain, heavy rain)
  Wind        lines from 10 km/h, more of them with more wind; leaves in the accent colour from 15 km/h; wind arrow top right
  Clouds      move in the wind direction (an image alone only shows the position)
  Sun         straight, white gradient from the sunny side, delicate rays; below clouds one breaks through now and then
  North       the same wind with a rotated plan: arrow, lines and rain rotate along
  Full screen the weather keeps running while panning and fills the frame (image in the middle of the gesture and after)
  Heat map    scale 15–22 °C from the heating schedule: kitchen 17 °C almost transparent, living room 22 °C full opacity
  System      system load with the hatched share of ComfyOS (sample values, because Windows reports no load)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARGS = [a for a in sys.argv[1:]]
ONLY: list[str] = []
if "--only" in ARGS:
    at = ARGS.index("--only")
    ONLY = [part.strip().lower() for part in (ARGS[at + 1] if at + 1 < len(ARGS) else "").split(",") if part.strip()]
    del ARGS[at:at + 2]
sys.argv = [sys.argv[0]] + ARGS                         # the test world reads the root from the first argument
sys.path.insert(0, str(HERE.parent / "tests"))          # shared scaffolding of the tests (support)

from support import ui  # noqa: E402
from support.world_demo import ITEMS, WEISS, MockHB, auth, httpx, install_homebase, main  # noqa: E402

from app import floorplan_store, routines_store  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = ui.SHOTS / "effects"
SHIFT = (7, 4)          # the house moves this far to the bottom right on the larger plot
PLOT = (36, 21)         # width and height of the plot in grid cells
KMH = 1 / 80            # fx.wind ist km/h ÷ 80

# (name, preview, time of day, what is waited for) – preview: name of a weather kind or an object with own values.
# Waiting: number = milliseconds, "rays" = until a sun ray breaks through the clouds.
SCENES = [
    ("01-klar-tag", "clear", "day", 1200),
    ("02-klar-daemmerung", "clear", "dusk", 1200),
    ("03-klar-nacht", "clear", "night", 1500),
    ("04-teilweise-bewoelkt-sonnenstrahl", "partly", "day", "rays"),
    ("05-bedeckt-sonnenstrahl", "overcast", "day", "rays"),
    ("06-bedeckt-nacht", "overcast", "night", 2000),
    ("07-hitze", "heat", "day", 1200),
    ("08-nebel", "fog", "day", 2500),
    ("09-regen-niesel", "drizzle", "day", 3500),
    ("10-regen-mittel", "rain", "day", 3500),
    ("11-regen-stark", {"base": "rain", "rain": 1.0, "wind": 30 * KMH}, "day", 3500),
    ("12-regen-nacht", "rain", "night", 3500),
    ("13-schneeregen", "sleet", "day", 3500),
    ("14-schnee-tag", "snow", "day", 3500),
    ("15-schnee-nacht", "snow", "night", 3500),
    ("16-wind-10kmh", {"base": "breeze", "wind": 10 * KMH, "gust": 14 * KMH}, "day", 4000),
    ("17-wind-15kmh-erste-blaetter", {"base": "breeze", "wind": 15 * KMH, "gust": 22 * KMH}, "day", 4000),
    ("18-wind-25kmh", {"base": "breeze", "wind": 25 * KMH, "gust": 38 * KMH}, "day", 4000),
    ("19-wind-45kmh", {"base": "breeze", "wind": 45 * KMH, "gust": 62 * KMH}, "day", 4000),
    ("20-wind-70kmh", {"base": "breeze", "wind": 70 * KMH, "gust": 80 * KMH}, "day", 4000),
    ("21-sturm", "storm", "day", 4000),
    ("22-gewitter", "thunder", "day", 3500),
    ("23-hagel", "hail", "day", 3500),
    ("24-frost", "frost", "day", 1500),
]


def wanted(name: str) -> bool:
    """Should this image be taken? (Without --only: all.)"""
    return not ONLY or any(part in name.lower() for part in ONLY)


def bigger_plot() -> None:
    """Put the demo home as a smaller house on a larger plot: there is room for the effects outside."""
    plan = floorplan_store.get()
    dx, dy = SHIFT
    move = lambda p: [p[0] + dx, p[1] + dy]   # noqa: E731
    for wall in plan["walls"]:
        wall["pts"] = [move(p) for p in wall["pts"]]
    for opening in plan["openings"]:
        opening["a"], opening["b"] = move(opening["a"]), move(opening["b"])
    for entry in list(plan["rooms"]) + list(plan["groups"]) + list(plan["devices"].values()):
        entry["x"], entry["y"] = entry["x"] + dx, entry["y"] + dy
    plan.update({"width": PLOT[0], "height": PLOT[1], "north": 0, "fx": True, "show_names": False})
    floorplan_store.save(plan)


def heating_example() -> None:
    """The example for the heat map: heating schedule from 15 to 22 °C; kitchen 17 °C, living room 22 °C, bedroom 15.5 °C."""
    for item in ITEMS:
        state = {"TW_t": "22.0", "TW_s": "22", "TK_t": "17.0", "TK_s": "17", "TS_t": "15.5", "TS_s": "15"}.get(item["name"])
        if state:
            item["state"] = state
    routine = routines_store.create("Winter")
    routines_store.update_schedule(routine["key"], [
        {"room": room, "day": day, "points": [{"time": "06:30", "temp": warm}, {"time": "22:00", "temp": 15}]}
        for room, warm in (("wohnen", 22), ("kueche", 17)) for day in range(7)])
    routines_store.set_active(routine["key"])


def show(page, preview, daytime: str, wait) -> None:
    """Set a weather as the preview, rebuild the home page and wait until it has developed."""
    page.evaluate("([p, t]) => { HO.state.fxPreview = p; HO.state.fxTime = t; HO.go('home', true) }", [preview, daytime])
    page.wait_for_selector("#fpHost canvas.fp-fx", timeout=10000)
    if wait == "rays":
        page.wait_for_function("() => HO.weatherFx.stats.rays > 0.75", timeout=60000)   # as a function: the CSP allows no eval
        page.wait_for_timeout(250)
    else:
        page.wait_for_timeout(int(wait))


def numbers(page) -> str:
    """Numbers of the weather module for the image (for the output): wind lines, leaves, puddles, ripples, rays."""
    s = page.evaluate("() => HO.weatherFx.stats")
    return (f"wind lines {s['wisps']}, leaves {s['leaves']}, puddles {s['puddles']}, ripples {s['ripples']},"
            f"Strahlen {s['rays']:.2f}, Stufe {s['step']}")


def plan_shots(page) -> None:
    """Photograph every weather of the list on the floor plan of the home page."""
    for name, preview, daytime, wait in SCENES:
        if not wanted(name):
            continue
        show(page, preview, daytime, wait)
        page.locator("#fpHost .fp-wrap").screenshot(path=str(OUT / f"{name}.png"))
        print(f"{name}: {numbers(page)}")


def north_shots(page, api) -> None:
    """The same west wind with a rotated plan (north 0°, 90°, 200°): arrow, wind lines and rain slant rotate along."""
    for north in (0, 90, 200):
        name = f"25-norden-{north:03d}-grad-westwind"
        if not wanted(name):
            continue
        plan = dict(floorplan_store.get(), north=north)
        assert api.put("/api/floorplan", json=plan).status_code == 200
        page.evaluate("async () => { HO.state.overview.floorplan = await HO.api('floorplan') }")
        show(page, {"base": "rain", "rain": 0.5, "wind": 35 * KMH, "wind_dir": 270}, "day", 3500)
        page.locator("#fpHost .fp-wrap").screenshot(path=str(OUT / f"{name}.png"))
        print(f"{name}: Pfeil {page.locator('#fpHost .fp-wind').get_attribute('title')!r}")
    assert api.put("/api/floorplan", json=dict(floorplan_store.get(), north=0)).status_code == 200
    page.evaluate("async () => { HO.state.overview.floorplan = await HO.api('floorplan') }")


def fullscreen_shots(page, base: str) -> None:
    """Full screen: rain with wind, then pan the plan with the mouse – one image in the middle of the gesture (the
    weather keeps running and fills the frame) and one after (nothing jumps back)."""
    if not wanted("26-vollbild"):
        return
    page.evaluate("() => { HO.state.fxPreview = 'rain'; HO.state.fxTime = 'day' }")
    page.goto(base + "/#plan")
    page.wait_for_selector(".pv canvas.fp-fx", timeout=15000)
    page.wait_for_timeout(3500)
    page.screenshot(path=str(OUT / "26-vollbild-regen.png"))
    page.mouse.move(700, 480)
    page.mouse.down()
    page.mouse.move(430, 330, steps=14)
    page.wait_for_timeout(900)
    page.screenshot(path=str(OUT / "27-vollbild-waehrend-verschieben.png"))
    page.mouse.up()
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "28-vollbild-nach-verschieben.png"))
    print(f"26–28 Vollbild: {numbers(page)}")
    page.goto(base + "/#home")
    page.wait_for_selector("#fpHost .fp-wrap svg .fp-room", timeout=20000)


def heatmap_shot(page, api) -> None:
    """Heat map without weather effects: scale 15–22 °C, the kitchen (17 °C) almost transparent, the living room (22 °C) full."""
    name = "29-heatmap-skala-15-22"
    if not wanted(name):
        return
    assert api.put("/api/floorplan", json=dict(floorplan_store.get(), fx=False, show_names=True)).status_code == 200
    page.evaluate("async () => { HO.state.overview.floorplan = await HO.api('floorplan'); HO.state.fxPreview = null; HO.state.fxTime = 'day'; HO.go('home', true) }")
    page.wait_for_selector("#fpHost .fp-heat", timeout=10000)
    page.wait_for_timeout(800)
    page.locator("#fpHost .fp-wrap").screenshot(path=str(OUT / f"{name}.png"))
    scale = page.evaluate("() => [document.querySelector('#fpHost .fp-lo').textContent, document.querySelector('#fpHost .fp-hi').textContent]")
    print(f"{name}: Legende {scale}, Spanne aus dem Heizplan {page.evaluate('() => HO.state.overview.heat_range')}")
    assert api.put("/api/floorplan", json=dict(floorplan_store.get(), fx=True, show_names=False)).status_code == 200


def system_shot(page, base: str) -> None:
    """Tile "Systemauslastung" with sample values (Windows reports neither load nor memory): the share of
    ComfyOS lies hatched at the start of every bar."""
    name = "30-systemauslastung-beispielwerte"
    if not wanted(name):
        return
    sample = {"platform": "Linux", "machine": "aarch64", "cpu_count": 4, "load": [1.48, 1.2, 1.1], "uptime": 912345.0,
              "cpu_temp": 51.2, "ram": {"total": 4 * 1024 ** 3, "used": int(1.9 * 1024 ** 3), "available": int(2.1 * 1024 ** 3)},
              "disk": {"total": 59 * 1024 ** 3, "used": 21 * 1024 ** 3, "free": 38 * 1024 ** 3},
              "own": {"cpu": 6.4, "ram": 310 * 1024 ** 2, "disk": int(2.6 * 1024 ** 3)}}
    page.route("**/api/system/info", lambda route: route.fulfill(json=sample))
    page.goto(base + "/#settings")
    page.wait_for_selector("#sysInfo .sys-own", timeout=15000)
    page.wait_for_timeout(600)
    page.locator("#sysInfo").screenshot(path=str(OUT / f"{name}.png"))
    print(f"{name}: {page.locator('#sysInfo .sys-row').count()} Balken")
    page.unroute("**/api/system/info")


def phone_shots(browser, playwright, base: str, token: str) -> None:
    """Three weathers on the phone (iPhone size): rain, wind, clear day."""
    scenes = [("31-handy-regen", "rain", "day", 3500), ("32-handy-wind-45kmh", {"base": "breeze", "wind": 45 * KMH}, "day", 4000),
              ("33-handy-klar", "clear", "day", 1200)]
    if not any(wanted(name) for name, *_ in scenes):
        return
    ctx = browser.new_context(**playwright.devices[ui.PHONE])
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(base + "/#home")
    page.wait_for_selector("#fpHost .fp-wrap svg .fp-room", timeout=20000)
    for name, preview, daytime, wait in scenes:
        if wanted(name):
            show(page, preview, daytime, wait)
            page.locator("#fpHost .fp-wrap").screenshot(path=str(OUT / f"{name}.png"))
            print(f"{name}: {numbers(page)}")
    ctx.close()


def run() -> None:
    """Start the demo home and take all images."""
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        if wanted(old.stem):
            old.unlink()
    bigger_plot()
    heating_example()
    server, port = ui.serve(main.app)
    install_homebase(MockHB("https://qivicon", "", "x"))
    token = auth.create_session("fx-shots")
    base = f"http://127.0.0.1:{port}"
    api = httpx.Client(base_url=base, cookies={auth.COOKIE: token}, timeout=15)
    assert api.post("/api/plugins/hue/pair", json={"host": "192.0.2.10"}).json().get("connected")
    api.post(f"/api/devices/{WEISS}/command", json={"state": {"on": False}})
    errors: list[str] = []
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, device_scale_factor=2)
        ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": base}])
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/#home")
        page.wait_for_selector("#fpHost .fp-wrap svg .fp-room", timeout=20000)
        page.wait_for_function("() => !!(HO.state.weather && HO.state.weather.data)", timeout=10000)
        plan_shots(page)
        north_shots(page, api)
        fullscreen_shots(page, base)
        heatmap_shot(page, api)
        system_shot(page, base)
        phone_shots(browser, p, base, token)
        browser.close()
    server.should_exit = True
    count = len(list(OUT.glob("*.png")))
    print(f"{count} Bilder in {OUT} ({time.time() - started:.0f} s), Konsolenfehler: {len(errors)}")
    for line in errors[:20]:
        print("  Konsole:", line)


if __name__ == "__main__":
    run()
