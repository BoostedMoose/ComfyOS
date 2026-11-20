"""UI check with Playwright: tile "Sonos" under System → Plugins and the block "Wiedergabe" of the home page
(desktop/Chromium and iPhone/WebKit). Screenshots in tests/shots/ (media-*.png).

    python tests/ui/ui_media.py [path-to-comfyos]

Runs against the test world "Home Base + Sonos" (tests/support/world_sonos.py): a household in memory, no speaker,
no network. The path of the user is checked:

    without speakers    the tile offers the search, names the speakers of the Home Base and "home network only, UDP too"
    after the search    rooms of the household, "Neu bei Sonos" with adopting
    home page           one tile per speaker with title, artist and cover picture from our own server; playback,
                        volume and favourite arrive at the right speaker; foreign text is not shown as HTML
    phone               the tiles do not stick out over the edge
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import ui  # noqa: E402
from support.world_sonos import (  # noqa: E402 – mocks, app and helpers of the shared test world
    auth, egress, HB_LIVING, install_homebase, KITCHEN, LIVING, main, MockHB, ROOM_ID, sonos, writes)

from playwright.sync_api import sync_playwright  # noqa: E402

sys.modules["comfyos_plugin_sonos"].SEARCH_SECONDS = 0.5      # in the test the search only waits briefly for answers

server, PORT = ui.serve(main.app)        # eigener freier Port je Lauf
install_homebase(MockHB("https://qivicon", "", "x"))
auth.set_password("comfyos-" + "test")
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = ui.shots_dir("media-")
TILE = "#plugin-sonos"
problems = []


def wait_until(page, condition, seconds=8.0):
    """Wait until the condition holds; False after the time is up. Waiting goes through the page: every request of the
    browser passes through `page.route` and only advances while it is Playwright's turn."""
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        page.wait_for_timeout(50)
    return False


def run(p, engine, name, first: bool, **ctx_args):
    """One pass in one browser. The first searches, adopts and controls; the second checks the home page on the phone."""
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(**ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    external = []
    page.route(lambda url: not url.startswith(BASE), lambda r: (external.append(r.request.url), r.abort()))
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    failed = []       # rejected calls of our own API – listed with a finding
    page.on("response", lambda r: failed.append(f"{r.status} {r.url.replace(BASE, '')}") if r.status >= 400 else None)

    def check(cond, msg):
        if not cond:
            problems.append(f"{name}: {msg}")
        return cond

    def card(device_id):
        return f'#mediaBox .media-card[data-media="{device_id}"]'

    page.goto(BASE + "/")
    page.wait_for_selector("#pageTitle")

    if first:
        # ---------- without speakers
        page.evaluate("() => HO.go('settings?tab=plugins')")
        page.wait_for_selector(f"{TILE} [data-plugin-body] *")
        text = page.inner_text(TILE)
        check(page.locator(f'{TILE} [data-act="sonosSearch"]').count() == 1, "the button 'Lautsprecher suchen' is missing")
        check("4 Sonos-Lautsprecher" in text, "the hint about the speakers of the Home Base is missing")
        check("Spricht nur im Heimnetz" in text and "UDP" in text, "the tile does not name home network and UDP")
        check(page.locator(f"{TILE} .card-head .badge").inner_text() == "nicht verbunden", "badge of the tile without speakers wrong")
        check(sonos["searches"] == 0, "a search happened without the push of a button")
        page.locator(TILE).screenshot(path=str(SHOTS / f"media-{name}-1-ohne.png"))

        # ---------- search and adopt
        page.click(f'{TILE} [data-act="sonosSearch"]')
        try:
            page.wait_for_selector(f'{TILE} [data-adopt="{ROOM_ID(KITCHEN)}"]', state="attached", timeout=12000)
            text = page.inner_text(TILE)
            check("Wohnzimmer" in text and "Küche" in text, "rooms of the household are missing in the tile")
            check(sonos["searches"] >= 1, "the search did not search")
            page.click(f'{TILE} details.mgroup[data-g="Neu bei Sonos"] > summary')
            page.click(f'{TILE} [data-adopt="{ROOM_ID(KITCHEN)}"]')
            page.wait_for_selector(f'{TILE} [data-adopt="{ROOM_ID(KITCHEN)}"]', state="detached", timeout=12000)
        except Exception:
            check(False, f"search or adopting fails: {page.inner_text(TILE)[:200]!r}")
        page.locator(TILE).screenshot(path=str(SHOTS / f"media-{name}-2-haushalt.png"))

        # ---------- home page: block "Wiedergabe"
        page.evaluate("() => HO.go('home')")
        try:
            page.wait_for_selector(card(HB_LIVING), timeout=12000)
            page.wait_for_selector(card(ROOM_ID(KITCHEN)), timeout=12000)
        except Exception:
            check(False, "speaker tiles are missing on the home page")
        living = card(HB_LIVING)
        check("Fernseher-Box" in page.inner_text(living), "the tile does not carry the name from the Home Base")
        check("Lied <b>eins</b>" in page.inner_text(living) and page.locator(f"{living} b").count() == 0, "the title is output as HTML")
        check("Die Band" in page.inner_text(living), "artist missing")
        image = page.locator(f"{living} .media-img").get_attribute("style") or ""
        check("/api/media/image/sonos/" in image, f"the cover picture does not come from our own server: {image!r}")
        sonos["calls"].clear()
        page.click(f'{living} [data-media-act="playing"]')
        check(wait_until(page, lambda: writes(LIVING) == ["Pause"]), f"pause did not arrive at the speaker: {writes(LIVING)}")
        try:
            page.wait_for_function("(sel) => { const b = document.querySelector(sel); return b && b.getAttribute('aria-pressed') === 'false' }",
                                   arg=f'{living} [data-media-act="playing"]', timeout=8000)
        except Exception:
            check(False, "the button still shows 'playing' after pausing")
        page.locator(f'{living} [data-media-vol]').evaluate(
            "(el) => { el.value = 40; el.dispatchEvent(new Event('change', { bubbles: true })) }")
        check(wait_until(page, lambda: sonos["volume"][LIVING] == 40), "the volume did not arrive at the speaker")
        page.select_option(f'{card(ROOM_ID(KITCHEN))} [data-media-fav]', "Radio Eins")
        check(wait_until(page, lambda: writes(KITCHEN)[-2:] == ["SetAVTransportURI", "Play"]), f"the favourite did not arrive in the kitchen: {writes(KITCHEN)} {failed}")
        if name == "desktop":
            # sound of this computer: the browser's sharing dialog is replaced by a tone, the speaker is told the stream
            egress.own_address_towards = lambda host: "192.0.2.1"
            page.evaluate("""() => { navigator.mediaDevices.getDisplayMedia = async () => {
                const context = new AudioContext(), tone = context.createOscillator(), out = context.createMediaStreamDestination()
                tone.connect(out); tone.start(); return out.stream } }""")
            page.evaluate("() => HO.go('home')")
            live_button = f'{living} [data-media-live]'
            try:
                page.wait_for_selector(live_button, timeout=8000)
            except Exception:
                check(False, "the button 'Ton dieses Rechners' is missing on the speaker tile")
            sonos["calls"].clear()
            page.click(live_button)
            check(wait_until(page, lambda: writes(LIVING)[-2:] == ["SetAVTransportURI", "Play"]),
                  f"the speaker was not told to play the sound of the browser: {writes(LIVING)} {failed}")
            told = [args["CurrentURI"] for u, action, args in sonos["calls"] if u == LIVING and action == "SetAVTransportURI"]
            check(bool(told) and told[-1].startswith("http://192.0.2.1") and told[-1].endswith("/stream/live.wav"),
                  f"the speaker got another address than the stream of ComfyOS: {told}")
            try:
                page.wait_for_function("(sel) => { const b = document.querySelector(sel); return b && b.getAttribute('aria-pressed') === 'true' }",
                                       arg=live_button, timeout=8000)
            except Exception:
                check(False, "the button does not show the running transmission")
            page.click(live_button)
            check(wait_until(page, lambda: writes(LIVING)[-1:] == ["Stop"]), f"ending the transmission did not stop the speaker: {writes(LIVING)}")
            sonos["source"].clear()
        page.locator("#mediaBox").scroll_into_view_if_needed()
        page.locator("#mediaBox").screenshot(path=str(SHOTS / f"media-{name}-3-wiedergabe.png"))

        # ---------- device dialog: the same tile, no on/off button
        page.click(f"{living} .media-room")
        try:
            page.wait_for_selector("#devMedia .media-card", timeout=8000)
            check(page.locator("#devMedia [data-media-act='playing']").count() == 1, "device dialog without a playback button")
            check(page.locator(".sheet [data-t]").count() == 0, "the device dialog shows a speaker the on/off button")
        except Exception:
            check(False, "the device dialog of a speaker does not show the playback")
    else:
        # ---------- Startseite am Handy
        try:
            page.wait_for_selector(card(HB_LIVING), timeout=12000)
        except Exception:
            check(False, "speaker tiles are missing on the phone")
        check(page.locator("#mediaBox .media-card").count() == 2, "not both speakers are shown on the phone")
        wide = page.evaluate("""() => { const w = document.documentElement.clientWidth
          return [...document.querySelectorAll('#mediaBox, #mediaBox *')].filter(c => c.getBoundingClientRect().right > w + 1).length }""")
        check(wide == 0, f"{wide} elements of the speaker tiles stick out over the edge on the phone")
        page.locator("#mediaBox").scroll_into_view_if_needed()
        page.locator("#mediaBox").screenshot(path=str(SHOTS / f"media-{name}-4-handy.png"))

    print(f"{name}: Konsolenfehler: {len(errs)}")
    problems.extend(f"{name} Konsole: {e}" for e in errs)
    problems.extend(f"{name}: the browser loaded from outside: {u}" for u in external)
    browser.close()


with sync_playwright() as p:
    run(p, "chromium", "desktop", True, viewport={"width": 1366, "height": 900})
    run(p, "webkit", "safari", False, **p.devices["iPhone 13"])
server.should_exit = True
print("\n".join(problems) if problems else "UI OK – no problems, no console errors")
