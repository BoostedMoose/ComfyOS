"""UI check with Playwright: add a device, reconnect, delete from the Home Base
(test world world_pairing; desktop/Chromium and iPhone/WebKit). Screenshots in tests/shots/.

    python tests/ui/ui_pairing.py [path-to-comfyos]
"""
import base64
import copy
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import ui, world_pairing as world  # noqa: E402

ROOT = world.ROOT
g = vars(world)        # mocks, app and helpers of the test world – the same as in tests/plugins/test_pairing.py
main, auth, QDEV, INBOX, calls = g["main"], g["auth"], g["QDEV"], g["INBOX"], g["calls"]
THERMO, GHOST, NEW_THERMO, ROOM = g["THERMO"], g["GHOST"], g["NEW_THERMO"], g["ROOM"]
QDEV0 = copy.deepcopy(QDEV)
from app import core  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

server, PORT = ui.serve(main.app)        # eigener freier Port je Lauf
hb = g["MockHB"]("https://qivicon", "", "x")
hb._remember_backend('<html data-backend-token="BT-TEST" data-backend-url="https://backend-prod.cloud.qivicon.com/services">')
g["install_homebase"](hb)
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = HERE.parent / "shots"
SHOTS.mkdir(exist_ok=True)
for f in SHOTS.glob("pair-*.png"):
    f.unlink()
g["PNG"] = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")  # real image for the catalogue mock
problems = []


def reset():
    """Ausgangslage der Kopplungs-Welt herstellen."""
    QDEV.clear()
    QDEV.update(copy.deepcopy(QDEV0))
    INBOX.clear()
    calls.clear()
    g["homebase_plugin"]().catalog_cache = None
    hb.invalidate_devices()


def run(p, engine, name, **ctx_args):
    """One pass "pairing devices" in one browser; the browser must never go outside itself along the way."""
    reset()
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(**ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    # catalogue images come through ComfyOS (/api/plugins/qivicon/pairing/image); the browser must never go outside itself
    external = []
    page.route(lambda url: not url.startswith(BASE),
               lambda r: (external.append(r.request.url), r.abort()))
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    shot = lambda n, **kw: page.screenshot(path=str(SHOTS / f"pair-{name}-{n}.png"), **kw)  # noqa: E731

    def check(cond, msg):
        if not cond:
            problems.append(f"{name}: {msg}")
        return cond

    # ---------- System page: Home Base list with new actions
    page.goto(BASE + "/")
    page.wait_for_selector("#pageTitle")
    page.evaluate("HO.go('settings?tab=plugins')")
    page.wait_for_selector("#plugin-qivicon .mgroup")
    # open the groups "Zu prüfen" and "Neu gefunden" (collapsed by default)
    for grp in ("Zu prüfen", "Neu gefunden"):
        summary = page.locator(f'#plugin-qivicon details.mgroup[data-g="{grp}"] > summary')
        if summary.count() and not page.locator(f'#plugin-qivicon details.mgroup[data-g="{grp}"]').evaluate("el => el.open"):
            summary.click()
    check(page.locator('#plugin-qivicon [data-hb-act="pair"]').count() == 1, "no button 'Gerät hinzufügen'")
    check(page.locator(f'#plugin-qivicon [data-action-key="reconnect"][data-id="{THERMO}"]').count() == 1, "getrenntes Thermostat ohne 'Wiederverbinden'")
    check(page.locator(f'#plugin-qivicon [data-action-key="delete"][data-id="{THERMO}"]').count() == 1, "disconnected thermostat without 'Von der Home Base löschen'")
    check("Von der Home Base getrennt" in page.inner_text("#plugin-qivicon"), "Status 'getrennt' fehlt in der Liste")
    page.locator("#plugin-qivicon").screenshot(path=str(SHOTS / f"pair-{name}-1-liste.png"))

    # ---------- Assistant: maker -> device -> instructions -> search now -> include -> confirmation
    page.click('#plugin-qivicon [data-hb-act="pair"]')
    page.wait_for_selector(".sheet [data-vendor]")
    vendors = page.locator(".sheet [data-vendor]").all_inner_texts()
    check(any("Magenta SmartHome" in v for v in vendors), f"Herstellerliste ohne Magenta SmartHome: {vendors}")
    shot("2-hersteller")
    page.click('.sheet [data-vendor]:has-text("Magenta SmartHome")')
    page.wait_for_selector('.sheet [data-dev="HIDTHERMO"]')
    check(page.locator(".sheet [data-dev]").count() == 3, "device list Magenta SmartHome does not have 3 entries")
    shot("3-geraete")
    page.click(".sheet [data-back]")                       # back + search
    page.wait_for_selector("#pairQ")
    page.fill("#pairQ", "heizk")
    page.wait_for_selector('.sheet #pairList [data-dev="HIDTHERMO"]')
    check(page.locator(".sheet #pairList [data-dev]").count() == 1, "the search 'heizk' does not find exactly one device")
    page.click('.sheet [data-dev="HIDTHERMO"]')
    page.wait_for_selector(".sheet [data-search]")
    body = page.inner_text(".sheet")
    check("Entferne die Abdeckung" in body and "Lege die Batterien ein" in body, "Anleitung fehlt")
    check(page.locator(".sheet-body > .pair-steps > li").count() == 2, "nicht 2 Anleitungsschritte")
    shot("4-anleitung")
    page.click(".sheet [data-search]")
    page.wait_for_selector(f'.sheet [data-approve="{NEW_THERMO}"]', timeout=10000)
    check(":" in page.inner_text("#pairCount"), "kein Countdown")
    page.fill(f'.sheet [data-name-for="{NEW_THERMO}"]', "Thermostat Bad")
    if check(page.locator(f'.sheet [data-room-for="{NEW_THERMO}"]').count() == 1, "no room choice for the found device"):
        page.select_option(f'.sheet [data-room-for="{NEW_THERMO}"]', ROOM)
    shot("5-gefunden")
    page.click(f'.sheet [data-approve="{NEW_THERMO}"]')
    page.wait_for_selector(".sheet .pair-done", timeout=10000)
    check("Thermostat Bad" in page.inner_text(".sheet .pair-done"), "confirmation without the device name")
    check(QDEV.get(NEW_THERMO, {}).get("label") == "Thermostat Bad" and QDEV[NEW_THERMO].get("room") == ROOM,
          f"device not included with name/room: {QDEV.get(NEW_THERMO)}")
    check(any(c[0] == "DELETE" and "/flows/" in c[1] for c in calls), "procedure not cleaned up after including")
    shot("6-verbunden")
    page.click('.sheet [data-close]:has-text("Fertig")')
    page.locator('.sheet-bg').wait_for(state='hidden')

    # ---------- Wiederverbinden
    page.wait_for_selector(f'#plugin-qivicon [data-action-key="reconnect"][data-id="{THERMO}"]', state="attached")
    page.click(f'#plugin-qivicon details.mrow:has([data-action-key="reconnect"][data-id="{THERMO}"]) > summary')   # open the device row
    page.click(f'#plugin-qivicon [data-action-key="reconnect"][data-id="{THERMO}"]')
    page.wait_for_selector(".sheet [data-go]")
    body = page.inner_text(".sheet")
    check("Werkzustand" in body and "Taste neben den Batterien" in body, "the instructions for resetting are missing")
    shot("7-wiederverbinden")
    calls.clear()
    page.click(".sheet [data-go]")
    page.wait_for_selector("#rcCount")
    check(any(c[1].endswith("/steps/reconnecting-step") for c in calls), "waiting step not carried out")
    QDEV[THERMO]["deviceStatus"] = {"status": "UNKNOWN", "statusDetail": "NONE", "statusDescription": None}  # the device reports
    page.wait_for_selector(".sheet .pair-done", timeout=10000)
    check("wiederverbunden" in page.inner_text(".sheet .pair-done"), "keine Erfolgsmeldung beim Wiederverbinden")
    shot("8-wiederverbunden")
    page.click('.sheet [data-close]:has-text("Fertig")')
    page.locator('.sheet-bg').wait_for(state='hidden')

    # ---------- No reconnecting possible (Hue) -> offer deleting, with a confirmation
    page.evaluate(f"HO.pairing.reconnect({{ id: '{GHOST}', name: 'Ensis Tisch', room: '' }})")
    page.wait_for_selector(".sheet [data-del]")
    check("kein Wiederverbinden" in page.inner_text(".sheet"), "Hinweis 'kein Wiederverbinden' fehlt")
    page.click(".sheet [data-del]")
    page.wait_for_selector('.sheet .btn.danger:has-text("Löschen")')
    shot("9-loeschen")
    page.click('.sheet .btn.danger:has-text("Löschen")')
    page.locator('.sheet-bg').wait_for(state='hidden')
    page.wait_for_timeout(300)
    check(GHOST not in QDEV, "ghost lamp not deleted from the Home Base")

    # ---------- Device dialog in the overview: reconnecting for disconnected devices
    QDEV[THERMO]["deviceStatus"] = {"status": "OFFLINE", "statusDetail": "GONE", "statusDescription": None}
    hb.invalidate_devices()
    page.evaluate("HO.state.overview = null; HO.go('home')")
    page.wait_for_selector(".room-card")
    page.wait_for_selector(f'[data-dev="{THERMO}"]', timeout=8000)
    page.locator(f'[data-dev="{THERMO}"]').first.click(position={"x": 12, "y": 12})
    try:
        page.wait_for_selector('.sheet [data-action-key="reconnect"]', timeout=4000)
        page.click('.sheet [data-action-key="reconnect"]')
        page.wait_for_selector('.sheet h3:has-text("Wiederverbinden"), .sheet [data-go]', timeout=6000)
        page.click(".sheet-bg [data-close]")
    except Exception:
        problems.append(f"{name}: device dialog of the overview without 'Wiederverbinden'")
        shot("x-geraetedialog")

    print(f"{name}: Konsolenfehler: {len(errs)}")
    problems.extend(f"{name} Konsole: {e}" for e in errs)
    problems.extend(f"{name}: the browser loaded from outside: {u}" for u in external)
    browser.close()


with sync_playwright() as p:
    run(p, "chromium", "desktop", viewport={"width": 1366, "height": 900})
    run(p, "webkit", "safari", **p.devices["iPhone 13"])
server.should_exit = True
print("\n".join(problems) if problems else "UI OK – no problems, no console errors")
