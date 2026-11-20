"""UI check with Playwright: tiles of the plugins MQTT and network under System → Plugins and their devices on the
home page (desktop/Chromium and iPhone/WebKit). Screenshots in tests/shots/.

    python tests/ui/ui_plugins.py [path-to-comfyos]

The MQTT broker is the in-memory test broker of the plugin; the network plugin gets predefined answers.
The path of the user is checked: enter a broker → the tile becomes "Online" → adopt found devices (all or one by one
with a room) → create an own device → add a presence device → devices on the home page with state, live change
without a reload, switching.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import boot, ui  # noqa: E402

ROOT, tmp = boot.isolate("ui-integrations")
for extra in ("public/sdk", "public/plugins", "public/plugins/mqtt/tests"):
    sys.path.insert(0, str(ROOT / extra))

import httpx  # noqa: E402

from app import auth, core, egress, main  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402
from fake_broker import FakeBroker  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

egress._transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
egress._lan_transport = egress._transport
sys.modules["comfyos_plugin_mqtt"].SETTLE_SECONDS = 0.1
sys.modules["comfyos_plugin_mqtt.client"].FIRST_RETRY_SECONDS = 0.1

BROKER = "192.168.1.50"
broker = FakeBroker()
answering = {"192.168.1.60"}             # addresses that answer the network plugin
loop_of_app: dict = {}


async def fake_stream(host, port, tls):
    """Replacement for TCP connections: the broker answers, known devices refuse (so they are there), all others
    stay silent."""
    loop_of_app["loop"] = asyncio.get_running_loop()
    if host == BROKER:
        return await broker.connect(host, port)
    if host in answering:
        raise ConnectionRefusedError()
    raise asyncio.TimeoutError()


egress._stream_factory = fake_stream


def from_device(topic, payload, retain=False):
    """Message of a device at the broker – in the event thread of the app, the connections live there."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    if "loop" in loop_of_app:
        loop_of_app["loop"].call_soon_threadsafe(broker.publish, topic, text, retain)
    else:
        broker.publish(topic, text, retain)


PLUG_DEV = {"dev": {"ids": ["plug-wm"], "name": "Waschmaschine", "mf": "Nous", "mdl": "A1Z"}}
SENSOR_DEV = {"dev": {"ids": ["klima-bad"], "name": "Klima Bad", "sa": "Bad"}}
CONFIGS = {
    "homeassistant/light/lampe/light/config": {
        "~": "zigbee2mqtt/Stehlampe", "name": None, "schema": "json", "stat_t": "~", "cmd_t": "~/set", "brightness": True, "bri_scl": 254,
        "sup_clrm": ["xy", "color_temp"], "uniq_id": "lampe_light", "dev": {"ids": ["zigbee2mqtt_0x00158d0001a2b3c4"], "name": "Stehlampe", "sa": "Bad"}},
    "homeassistant/switch/plug-wm/switch/config": {**PLUG_DEV, "uniq_id": "wm_switch", "stat_t": "z/wm", "cmd_t": "z/wm/set", "val_tpl": "{{ value_json.state }}"},
    "homeassistant/sensor/plug-wm/power/config": {**PLUG_DEV, "uniq_id": "wm_power", "stat_t": "z/wm", "dev_cla": "power", "unit_of_meas": "W", "val_tpl": "{{ value_json.power }}"},
    "homeassistant/sensor/klima/t/config": {**SENSOR_DEV, "uniq_id": "klima_t", "stat_t": "z/klima", "dev_cla": "temperature", "unit_of_meas": "°C", "val_tpl": "{{ value_json.temperature }}"},
    "homeassistant/sensor/klima/h/config": {**SENSOR_DEV, "uniq_id": "klima_h", "stat_t": "z/klima", "dev_cla": "humidity", "unit_of_meas": "%", "val_tpl": "{{ value_json.humidity }}"},
    "homeassistant/sensor/klima/b/config": {**SENSOR_DEV, "uniq_id": "klima_b", "stat_t": "z/klima", "dev_cla": "battery", "ent_cat": "diagnostic", "val_tpl": "{{ value_json.battery }}"},
    "homeassistant/vacuum/sauger/config": {"dev": {"ids": ["sauger"], "name": "Saugroboter"}, "uniq_id": "sauger", "cmd_t": "z/sauger/set"},
}
for topic, config in CONFIGS.items():
    broker.publish(topic, json.dumps(config), retain=True)
PLUG_ID, SENSOR_ID, LAMP_ID, WINDOW_ID = "mqtt:plug-wm", "mqtt:klima-bad", "mqtt:zigbee2mqtt_0x00158d0001a2b3c4", "mqtt:fenster"


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
auth.set_password("comfyos-" + "test")
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = HERE.parent / "shots"
SHOTS.mkdir(exist_ok=True)
for f in SHOTS.glob("plug-*.png"):
    f.unlink()
problems = []


def run(p, engine, name, **ctx_args):
    """One pass of the tab "Plugins" in one browser: tiles, settings, MQTT and network plugin."""
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(**ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    external = []
    page.route(lambda url: not url.startswith(BASE), lambda r: (external.append(r.request.url), r.abort()))
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    shot = lambda n, **kw: page.screenshot(path=str(SHOTS / f"plug-{name}-{n}.png"), **kw)  # noqa: E731

    def check(cond, msg):
        if not cond:
            problems.append(f"{name}: {msg}")
        return cond

    def tile_shot(selector, n):
        page.locator(selector).scroll_into_view_if_needed()
        page.locator(selector).screenshot(path=str(SHOTS / f"plug-{name}-{n}.png"))

    def open_group(card, title):
        group = page.locator(f'{card} details.mgroup[data-g="{title}"]')
        if group.count() and not group.evaluate("el => el.open"):
            page.click(f'{card} details.mgroup[data-g="{title}"] > summary')

    page.goto(BASE + "/")
    page.wait_for_selector("#pageTitle")
    page.evaluate("HO.go('settings?tab=plugins')")
    page.wait_for_selector("#plugin-mqtt [data-plugin-body] *")       # the content of the tile comes from the plugin, shortly after the frame
    page.wait_for_selector("#plugin-network [data-plugin-body] *")

    # ---------- MQTT: Broker eintragen (erster Lauf) -> Online
    if "Kein Broker" in page.inner_text("#plugin-mqtt") or page.locator("#plugin-mqtt .card-head .badge.ok").count() == 0:
        check("Trage oben die Adresse" in page.inner_text("#plugin-mqtt"), "MQTT tile without a note on setting up")
        tile_shot("#plugin-mqtt", "1-mqtt-leer")
        page.fill('#plugin-mqtt [data-field="host"]', BROKER)
        check(page.input_value('#plugin-mqtt [data-field="port"]') == "1883", "the port is not preset with 1883")
        check(page.input_value('#plugin-mqtt [data-field="discovery_prefix"]') == "homeassistant", "discovery prefix not preset")
        page.click('#plugin-mqtt [data-plugin-form] button[type=submit]')
    try:
        page.wait_for_selector("#plugin-mqtt .card-head .badge.ok", timeout=8000)
    except Exception:
        check(False, f"the MQTT tile does not become „Online“: {page.inner_text('#plugin-mqtt .card-head')!r}")
        shot("x-mqtt-nicht-live")
    # the device list comes shortly after connecting – "Neu einlesen" until it is there
    for _ in range(20):
        if page.locator("#plugin-mqtt [data-adopt]").count():
            break
        page.wait_for_timeout(300)
        if page.locator('#plugin-mqtt [data-act="mqttReload"]').count():
            page.click('#plugin-mqtt [data-act="mqttReload"]')
            page.wait_for_timeout(300)
    check(page.locator("#plugin-mqtt .card-head .badge.ok").inner_text() == "Online", "the MQTT tile does not show „Online“")
    check("Spricht nur im Heimnetz" in page.inner_text("#plugin-mqtt"), "the tile does not name the home-network connection")
    found = page.locator("#plugin-mqtt [data-adopt]").count()
    if name == "desktop":
        check(found == 3, f"expected 3 found devices, there are {found}")
        open_group("#plugin-mqtt", "Nicht unterstützt")
        check("Saugroboter" in page.inner_text('#plugin-mqtt details.mgroup[data-g="Nicht unterstützt"]'), "'Nicht unterstützt' does not name the robot vacuum")
        open_group("#plugin-mqtt", "Neu gefunden")
        hint = page.locator(f'#plugin-mqtt [data-adopt-room="{SENSOR_ID}"]').input_value()
        check(hint == "bad", f"the room suggestion „Bad“ is not preselected for the sensor ({hint!r})")
        tile_shot("#plugin-mqtt", "2-mqtt-gefunden")
        page.click('#plugin-mqtt [data-act="mqttAdoptAll"]')
        page.wait_for_selector('#plugin-mqtt details.mgroup[data-g="Verbunden"], #plugin-mqtt details.mgroup[data-g="Zu prüfen"]', timeout=8000)
        check(page.locator("#plugin-mqtt [data-adopt]").count() == 0, "after „Alle übernehmen“ something is still under „Neu gefunden“")

        # create an own device: command topic only for switchable kinds
        open_group("#plugin-mqtt", "Eigenes Gerät anlegen")
        form = "#plugin-mqtt [data-manual-form]"
        page.select_option(f"{form} [name=kind]", "temperature")
        check(not page.locator(f"{form} [data-only-switchable]").is_visible(), "the command topic is visible for a temperature sensor")
        page.select_option(f"{form} [name=kind]", "switch")
        check(page.locator(f"{form} [data-only-switchable]").is_visible(), "the command topic is missing for a socket")
        page.fill(f"{form} [name=name]", "Shelly <b>Küche</b>")
        page.fill(f"{form} [name=state_topic]", "shellies/kueche/relay/0")
        page.fill(f"{form} [name=command_topic]", "shellies/kueche/relay/0/command")
        page.fill(f"{form} [name=payload_on]", "on")
        page.fill(f"{form} [name=payload_off]", "off")
        page.select_option(f"{form} [name=room]", "bad")
        tile_shot("#plugin-mqtt", "3-mqtt-eigenes")
        page.click(f"{form} button[type=submit]")
        page.wait_for_selector('#plugin-mqtt details.mgroup[data-g="Eigene Geräte"]', timeout=8000)
        open_group("#plugin-mqtt", "Eigene Geräte")
        check(page.locator("#plugin-mqtt b:has-text('Küche')").count() == 0, "the name of an own device is output as HTML")
        check("Shelly <b>Küche</b>" in page.inner_text('#plugin-mqtt details.mgroup[data-g="Eigene Geräte"]'), "the own device is missing in the list")
    else:
        # second pass: a new device reports -> adopt it individually with a room
        check(found == 1, f"expected 1 newly found device, there are {found}")
        open_group("#plugin-mqtt", "Neu gefunden")
        page.click(f'#plugin-mqtt .mrow:has([data-adopt="{WINDOW_ID}"]) > summary')      # open the row: details, room, adopting
        page.select_option(f'#plugin-mqtt [data-adopt-room="{WINDOW_ID}"]', "bad")
        page.click(f'#plugin-mqtt [data-adopt="{WINDOW_ID}"]')
        page.locator(f'#plugin-mqtt [data-adopt="{WINDOW_ID}"]').wait_for(state="hidden", timeout=8000)
    open_group("#plugin-mqtt", "Verbunden")
    tile_shot("#plugin-mqtt", "4-mqtt-verbunden")

    # ---------- Network: add a device (first pass), state, "Jetzt prüfen"
    if name == "desktop":
        check("Noch kein Gerät" in page.inner_text("#plugin-network"), "network tile without a note")
        open_group("#plugin-network", "Gerät hinzufügen")
        form = "#plugin-network [data-target-form]"
        page.fill(f"{form} [name=name]", "Handy Anna")
        page.fill(f"{form} [name=host]", "8.8.8.8")
        page.click(f"{form} button[type=submit]")
        page.wait_for_selector('.toast:has-text("Heimnetz")', timeout=5000)      # foreign address: rejected with a reason
        page.fill(f"{form} [name=host]", "192.168.1.60")
        page.fill(f"{form} [name=port]", "62078")
        page.select_option(f"{form} [name=room]", "bad")
        page.click(f"{form} button[type=submit]")
    page.wait_for_selector('#plugin-network .mrow:has-text("Handy Anna") .badge.ok', timeout=8000)
    check(page.locator('#plugin-network .mrow:has-text("Handy Anna") .badge.ok').inner_text() == "Anwesend", "the presence is not in the tile")
    page.click('#plugin-network .mrow:has-text("Handy Anna") > summary')
    page.click('#plugin-network [data-check]')
    page.wait_for_selector('.toast:has-text("antwortet")', timeout=5000)
    page.wait_for_selector("#plugin-network .mrow")
    tile_shot("#plugin-network", "5-netzwerk")
    wide = page.evaluate("""() => { const w = document.documentElement.clientWidth
      return [...document.querySelectorAll('#plugin-mqtt, #plugin-network')].filter(c => c.getBoundingClientRect().right > w + 1).length
        + (document.documentElement.scrollWidth > w + 1 ? 1 : 0) }""")
    check(not wide, "plugin tiles stick out over the screen")

    # ---------- Home page: devices of both plugins with state, live change, switching
    from_device("z/wm", {"state": "ON", "power": 812.4})
    from_device("z/klima", {"temperature": 21.5, "humidity": 55, "battery": 80})
    page.evaluate("HO.state.overview = null; HO.go('home')")
    page.wait_for_selector(".room-card")
    page.wait_for_selector(f'[data-dev="{PLUG_ID}"]', timeout=8000)

    def state_of(device_id):
        loc = page.locator(f'[data-dev="{device_id}"] .t-state, [data-dev="{device_id}"] .r-state').first
        return loc.inner_text() if loc.count() else ""

    try:
        page.wait_for_function("(id) => /812/.test((document.querySelector(`[data-dev=\"${id}\"]`) || {}).textContent || '')", arg=PLUG_ID, timeout=6000)
    except Exception:
        check(False, f"Steckdose zeigt Zustand/Leistung nicht: {state_of(PLUG_ID)!r}")
    check("An" in state_of(PLUG_ID) and "812" in state_of(PLUG_ID), f"Steckdose: {state_of(PLUG_ID)!r}")
    check("21,5" in state_of(SENSOR_ID) and "55 %" in state_of(SENSOR_ID), f"the sensor does not show temperature and humidity: {state_of(SENSOR_ID)!r}")
    phone = page.locator('[data-dev^="network:"]').first
    check(phone.count() == 1 and "Anwesend" in phone.inner_text(), "the presence device is missing on the home page or does not show „Anwesend“")
    page.wait_for_function("() => /MQTT/.test((document.querySelector('#connPill') || {}).title || '')", timeout=6000)
    check(page.inner_text("#connPill b") in ("Online", "Fehler", "Verbindet"), f"Verbindungsblase oben zeigt {page.inner_text('#connPill')!r} statt Online/Fehler/Verbindet")
    # live: the device switches itself off from outside -> visible without a reload
    from_device("z/wm", {"state": "OFF", "power": 0})
    try:
        page.wait_for_function("(id) => /Aus/.test((document.querySelector(`[data-dev=\"${id}\"]`) || {}).textContent || '')", arg=PLUG_ID, timeout=5000)
    except Exception:
        check(False, f"a change from the broker did not arrive live: {state_of(PLUG_ID)!r}")
    # switching from the interface -> message at the broker
    sent_before = len(broker.published_to("z/wm/set"))
    page.locator(f'[data-cmd="toggle"][data-id="{PLUG_ID}"]').first.click()
    deadline = time.time() + 5
    while len(broker.published_to("z/wm/set")) == sent_before and time.time() < deadline:
        page.wait_for_timeout(100)
    check(broker.published_to("z/wm/set")[sent_before:] == ["ON"], f"Schalten kam nicht am Broker an: {broker.published_to('z/wm/set')}")
    page.wait_for_timeout(400)
    shot("6-start", full_page=True)

    # device sheet of the sensor: battery and source
    page.locator(f'[data-dev="{SENSOR_ID}"]').first.click(position={"x": 12, "y": 12})
    try:
        page.wait_for_selector(".sheet", timeout=4000)
        check("Klima Bad" in page.inner_text(".sheet"), "the device sheet of the MQTT sensor does not open")
        shot("7-geraeteblatt")
        page.click(".sheet-bg [data-close]")
    except Exception:
        check(False, "the device sheet of the MQTT sensor does not open")

    print(f"{name}: Konsolenfehler: {len(errs)}")
    # the foreign address entered on purpose is rejected with 400 – the browser reports that in the console
    problems.extend(f"{name} Konsole: {e}" for e in errs if "status of 400" not in e)
    problems.extend(f"{name}: the browser loaded from outside: {u}" for u in external)
    browser.close()


with sync_playwright() as p:
    run(p, "chromium", "desktop", viewport={"width": 1366, "height": 900})
    # before the second pass a further device reports at the broker
    from_device("homeassistant/binary_sensor/fenster/contact/config", {
        "dev": {"ids": ["fenster"], "name": "Fenster Küche"}, "uniq_id": "fenster_contact", "stat_t": "z/fenster", "dev_cla": "window",
        "val_tpl": "{{ value_json.contact }}", "pl_on": False, "pl_off": True}, retain=True)
    time.sleep(0.6)
    run(p, "webkit", "safari", **p.devices["iPhone 13"])
server.should_exit = True
print("\n".join(problems) if problems else "UI OK – no problems, no console errors")
