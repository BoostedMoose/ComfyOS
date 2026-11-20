"""UI check with Playwright: tile "Matter" under System → Plugins (desktop/Chromium and iPhone/WebKit).
Screenshots in tests/shots/ (matter-*.png).

    python tests/ui/ui_matter.py [path-to-comfyos]

Runs without Node: "Einrichten" loads a stand-in archive from the cut-off network (with a matching checksum), the
helper program is a `FakeHelper` from the SDK. The path of the user is checked:

    without Node     the tile names helper program and rights, explains the download (target, version, size, checksum)
    set up           after "Einrichten" the plugin runs; share per device, counter "x freigegeben", "Alle" and search
    pairing open     pairing code and QR code (drawn as SVG), afterwards the paired controller
    controller       an included Matter device is listed under "Neu über Matter"; foreign text is not shown as HTML
"""
import asyncio
import hashlib
import io
import sys
import tarfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import boot, ui  # noqa: E402

ROOT, tmp = boot.isolate("ui-matter")
sys.path.insert(0, str(ROOT / "public" / "sdk"))

import httpx  # noqa: E402

from app import auth, egress, main, plugins  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402
from testing import FakeHelper  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

CODE = "34970112332"
matter_module = sys.modules["comfyos_plugin_matter"]
runtime_module = sys.modules["comfyos_plugin_matter.runtime"]
matter_module.SYNC_DELAY_SECONDS = 0.05


def stand_in_archive() -> tuple[bytes, str, str]:
    """Stand-in for the Node archive of this system: (content, file name, path of the program in the archive)."""
    content = b"stellvertreter fuer node\n"
    buffer = io.BytesIO()
    if sys.platform == "win32":
        with zipfile.ZipFile(buffer, "w") as package:
            package.writestr("node-test/node.exe", content)
        return buffer.getvalue(), "node-test.zip", "node-test/node.exe"
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo("node-test/bin/node")
        info.size = len(content)
        bundle.addfile(info, io.BytesIO(content))
    return buffer.getvalue(), "node-test.tar.xz", "node-test/bin/node"


ARCHIVE, ARCHIVE_NAME, ARCHIVE_BINARY = stand_in_archive()
runtime_module.ARCHIVES[runtime_module.system_key()] = runtime_module.Archive(
    file_name=ARCHIVE_NAME, sha256=hashlib.sha256(ARCHIVE).hexdigest(), binary=ARCHIVE_BINARY, megabytes=30)
requested: list[str] = []


def _net(req: httpx.Request) -> httpx.Response:
    """Cut-off network: nodejs.org delivers the stand-in archive, everything else nothing."""
    requested.append(req.url.host)
    return httpx.Response(200, content=ARCHIVE) if req.url.host == "nodejs.org" else httpx.Response(200, json={})


egress._transport = httpx.MockTransport(_net)
egress._lan_transport = egress._transport
box: dict = {}


def fake_start(program, args=(), *, env=None):
    """Instead of a process: FakeHelper of the SDK. Runs in the event loop of the app – we remember that one."""
    box["loop"] = asyncio.get_running_loop()
    box["helper"] = FakeHelper(str(program), args, env)
    return box["helper"]


def feed(message: dict) -> None:
    """Feed in a message of the helper program – in the event thread of the app."""
    box["loop"].call_soon_threadsafe(box["helper"].feed, message)


class MockHB(QiviconConnector):
    """Home Base dieses Tests: ein Thermostat im Bad."""
    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}

    async def list_devices(self, max_age=None):
        """Ein Thermostat."""
        return [{"id": "hb:therm", "name": "Heizung", "kind": "thermostat", "room": "Bad", "room_id": "bad", "online": True,
                 "temperature": 20.5, "target": 21.0, "control_item": "T1", "errors": [], "props": {}, "values": []}]

    async def list_rooms(self):
        """Ein Raum."""
        return [{"name": "bad", "label": "Bad"}]


server, PORT = ui.serve(main.app)        # eigener freier Port je Lauf
install_homebase(MockHB("https://qivicon", "", "x"))
plugins.active("matter").context.start_helper = fake_start
auth.set_password("comfyos-" + "test")
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = ui.shots_dir("matter-")
TILE = "#plugin-matter"
problems = []


def run(p, engine, name, first: bool, **ctx_args):
    """One pass of the Matter tile in one browser. The first sets up, pairs and includes a device;
    the second checks the running tile on the phone."""
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(**ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    external = []
    page.route(lambda url: not url.startswith(BASE), lambda r: (external.append(r.request.url), r.abort()))
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))

    def check(cond, msg):
        if not cond:
            problems.append(f"{name}: {msg}")
        return cond

    def tile_shot(n):
        page.locator(TILE).scroll_into_view_if_needed()
        page.locator(TILE).screenshot(path=str(SHOTS / f"matter-{name}-{n}.png"))

    def open_group(title):
        group = page.locator(f'{TILE} details.mgroup[data-g="{title}"]')
        if group.count() and not group.evaluate("el => el.open"):
            page.click(f'{TILE} details.mgroup[data-g="{title}"] > summary')

    page.goto(BASE + "/")
    page.wait_for_selector("#pageTitle")
    page.evaluate("() => HO.go('settings?tab=plugins')")
    page.wait_for_selector(f"{TILE} [data-plugin-body] *")

    if first:
        # ---------- without Node
        text = page.inner_text(TILE)
        check("Startet ein Beiprogramm" in text, "Kachel nennt das Beiprogramm nicht")
        check("alle Geräte sehen und schalten" in text and "Zuhause/Abwesend" in text, "the tile does not name the access rights")
        check(page.locator(f'{TILE} [data-act="install"]').count() == 1, "the button „Einrichten“ is missing")
        check("nodejs.org" in text and runtime_module.NODE_VERSION in text and "30 MB" in text, "note on the download incomplete (target, version, size)")
        check(page.locator(f"{TILE} .card-head .badge").inner_text() == "nicht verbunden", "badge of the tile without Node wrong")
        check(requested == [], f"ohne Knopfdruck wurde geladen: {requested}")
        tile_shot("1-ohne-node")

        # ---------- einrichten
        page.click(f'{TILE} [data-act="install"]')
        deadline = time.time() + 10
        while "helper" not in box and time.time() < deadline:
            page.wait_for_timeout(100)
        if not check("helper" in box, "no helper starts after „Einrichten“"):
            browser.close()
            return
        check(requested == ["nodejs.org"], f"„Einrichten“ contacted: {requested}")
        feed({"type": "ready", "protocol": 1})
        deadline = time.time() + 10
        while not box["helper"].sent_of("start") and time.time() < deadline:
            page.wait_for_timeout(100)
        feed({"type": "started"})
        feed({"type": "pairing", "manual": CODE, "qr": "MT:Y.K90AFN00KA0648G00", "rows": ["1010101" * 3] * 21, "commissioned": False})
        feed({"type": "fabrics", "fabrics": []})
        try:
            page.wait_for_selector(f'{TILE} [data-act="openWindow"]', timeout=20000)
        except Exception:
            check(False, f"the tile does not go online after setting up: {page.inner_text(TILE)[:200]!r}")
            browser.close()
            return
        try:
            page.wait_for_selector(f"{TILE} .card-head .badge.ok", timeout=12000)
        except Exception:
            check(False, f"the head of the tile does not show „Online“ after setting up: {page.inner_text(TILE + ' .card-head')!r}")

        # ---------- Freigabe
        open_group("Freigaben")
        check("0 freigegeben" in page.inner_text(f"{TILE} [data-share-count]"), "the counter of the shares is missing")
        check(page.locator(f'{TILE} [data-share-search]').count() == 1 and page.locator(f'{TILE} [data-bulk="all"]').count() == 2,
              "search or „Alle“/„Keine“ are missing in the shares")
        page.fill(f'{TILE} [data-share-search]', 'heiz')
        check(page.locator(f'{TILE} [data-share-row]:visible').count() == 1, "Suche in den Freigaben filtert nicht")
        page.fill(f'{TILE} [data-share-search]', '')
        check(page.locator(f'{TILE} [data-share="home"]').count() == 1, "„Zuhause“ is not offered as a device")
        open_group("Raum Bad")
        page.click(f'{TILE} [data-share="hb:therm"]')
        try:
            page.wait_for_function("() => /1 freigegeben/.test((document.querySelector('#plugin-matter [data-share-count]') || {}).textContent || '')",
                                   timeout=8000)
        except Exception:
            check(False, "the share is not counted")
        check(page.locator(f'{TILE} [data-share="hb:therm"]').get_attribute("aria-checked") == "true", "the switch of the share is not on")
        deadline = time.time() + 5
        while not box["helper"].sent_of("endpoints") and time.time() < deadline:
            page.wait_for_timeout(100)
        shared = box["helper"].sent_of("endpoints")
        check(bool(shared) and [e["name"] for e in shared[-1]["endpoints"]] == ["Heizung"], f"the share did not reach the helper: {shared[-1:]}")
        tile_shot("2-freigaben")

        # ---------- Kopplung offen
        page.click(f'{TILE} [data-act="openWindow"]')
        try:
            page.wait_for_selector(f"{TILE} [data-code]", timeout=8000)
            check(page.inner_text(f"{TILE} [data-code]") == "3497-011-2332", f"Zahlencode falsch dargestellt: {page.inner_text(TILE + ' [data-code]')!r}")
            drawn = page.evaluate("() => { const p = document.querySelector('#plugin-matter svg[role=img] path'); return p ? p.getAttribute('d').length : 0 }")
            check(drawn > 500, "the QR code is not drawn as SVG")
            check("Kopplung offen bis" in page.inner_text(TILE), "Dauer der Kopplung fehlt")
        except Exception:
            check(False, "numeric code and QR code do not appear after „Kopplung öffnen“")
        tile_shot("3-kopplung")
        feed({"type": "fabrics", "fabrics": [{"index": 1, "label": "", "vendor": 4631}]})
        try:
            page.wait_for_selector(f'{TILE} [data-controller="1"]', state="attached", timeout=12000)
            check("Amazon Alexa" in page.inner_text(TILE), "the paired controller is not named")
            check(page.locator(f"{TILE} [data-code]").count() == 0, "the numeric code is still there after pairing")
        except Exception:
            check(False, "the paired controller does not appear in the tile")

        # ---------- device of the controller
        feed({"type": "node", "node": "peer2", "online": True, "name": "Fremde Bridge", "vendor": "Signify", "product": "BSB002", "serial": "x",
              "endpoints": [{"endpoint": 3, "types": [257, 19], "name": "Leselampe <b>fett</b>", "reachable": True, "state": {"on": True, "level": 127}}]})
        try:
            page.wait_for_selector(f'{TILE} [data-adopt="matter:peer2-3"]', state="attached", timeout=12000)
            open_group("Matter-Geräte")
            open_group("Neu über Matter")
            check("Fremde Bridge" in page.inner_text(TILE) and page.locator(f'{TILE} [data-node="peer2"]').count() == 1, "the included Matter device is missing")
            check(page.locator(f"{TILE} b:has-text('fett')").count() == 0, "the name of a Matter device is output as HTML")
            check("Leselampe <b>fett</b>" in page.inner_text(TILE), "the device of the controller is missing under „Neu über Matter“")
        except Exception:
            check(False, "the device of the controller does not appear in the tile")
        tile_shot("4-controller")
    else:
        # ---------- laufende Kachel am Handy
        try:
            page.wait_for_selector(f'{TILE} [data-controller="1"]', state="attached", timeout=12000)
        except Exception:
            check(False, f"the running tile does not show the controller: {page.inner_text(TILE)[:200]!r}")
        open_group("Freigaben")
        check("1 freigegeben" in page.inner_text(TILE), "the counter of the shares is wrong on the phone")
        check(page.locator(f"{TILE} .mt-stats").count() == 1, "the overview at the top of the tile is missing")
        wide = page.evaluate("""() => { const w = document.documentElement.clientWidth
          return [...document.querySelectorAll('#plugin-matter, #plugin-matter *')].filter(c => c.getBoundingClientRect().right > w + 1).length }""")
        check(wide == 0, f"{wide} elements of the tile stick out over the edge on the phone")
        tile_shot("5-handy")

    print(f"{name}: Konsolenfehler: {len(errs)}")
    problems.extend(f"{name} Konsole: {e}" for e in errs)
    problems.extend(f"{name}: the browser loaded from outside: {u}" for u in external)
    browser.close()


with sync_playwright() as p:
    run(p, "chromium", "desktop", True, viewport={"width": 1366, "height": 900})
    run(p, "webkit", "safari", False, **p.devices["iPhone 13"])
server.should_exit = True
print("\n".join(problems) if problems else "UI OK – no problems, no console errors")
