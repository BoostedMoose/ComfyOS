"""UI check with Playwright: ComfyOS Flow (list, template, dragging/tapping the palette, roles, n+1 ports, connecting,
multi-selection with copy/paste/undo, negative number, live display, on/off, play, deleting) and two older
fixes (average in the heating history can be clicked, docked dots do not overlap).
Desktop/Chromium and iPhone/WebKit against a mock Home Base. Screenshots in tests/shots/.

    python tests/ui/ui_flow.py [path-to-comfyos]
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import boot, ui  # noqa: E402

ROOT, tmp = boot.isolate("uiflow")

from app import auth, core, events, floorplan_store, flows_store, history, main  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402

M, C, L, T, T2 = "dect:motion:1", "dect:contact:1", "dect:plug:1", "dect:thermo:1", "dect:thermo:2"
DEV = {
    M: {"id": M, "name": "Flur Bewegung", "kind": "motion", "room": "Flur", "room_id": "r1", "online": True, "motion": False},
    C: {"id": C, "name": "Bad Fenster", "kind": "contact", "room": "Bad", "room_id": "r2", "online": True, "open": True},
    L: {"id": L, "name": "Flur Lampe", "kind": "switch", "room": "Flur", "room_id": "r1", "online": True, "on": False, "control_item": "L1"},
    T: {"id": T, "name": "Bad Heizung", "kind": "thermostat", "room": "Bad", "room_id": "r2", "online": True,
        "temperature": 19.5, "target": 22.0, "control_item": "T1"},
    T2: {"id": T2, "name": "Wohnen Heizung", "kind": "thermostat", "room": "Wohnen", "room_id": "r3", "online": True,
         "temperature": 21.0, "target": 21.0, "control_item": "T2"},
}
sent = []


class MockHB(QiviconConnector):
    """Home Base of this test: devices from DEV, commands are recorded in sent."""
    def _needs_login(self):
        """The session is always valid."""
        return False

    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}

    async def list_devices(self, max_age=None):
        """The devices from DEV."""
        return [dict(d) for d in DEV.values()]

    async def list_rooms(self):
        """The three rooms of this test."""
        return [{"name": "r1", "label": "Flur"}, {"name": "r2", "label": "Bad"}, {"name": "r3", "label": "Wohnen"}]

    async def set_device_state(self, device_id, state):
        """Record a command and confirm it like the Home Base."""
        sent.append((device_id, state))
        return {"id": device_id, "name": DEV[device_id]["name"], "command": "ON" if state.get("on") else "OFF"}


# heating history of the last 24 h for two rooms (for the average)
now = time.time()
for k in range(96):
    t = now - 24 * 3600 + k * 900
    devs = [{**DEV[T], "temperature": 19 + (k % 12) * 0.25}, {**DEV[T2], "temperature": 21 - (k % 8) * 0.2}]
    history.record(devs, now=t)

# Floor plan: bathroom with window and radiator on the same wall, both docked (overlap case).
# The window is deliberately stored "the wrong way round" (side -1 = outwards) – it must still open into the room.
floorplan_store.save({
    "width": 14, "height": 12,
    "walls": [{"id": "w1", "pts": [[2, 2], [12, 2], [12, 10], [2, 10], [2, 2]], "bend": [None, None, None, None]}],
    "rooms": [{"id": "rb", "name": "Bad", "room_id": "r2", "x": 7, "y": 6}],
    "openings": [{"id": "ow", "type": "window", "a": [5, 2], "b": [8, 2], "side": -1, "device_id": C},
                 {"id": "or", "type": "radiator", "a": [5.2, 2], "b": [7.8, 2], "side": 1, "device_id": T}],
    "devices": {C: {"x": 6.5, "y": 2.5, "t": 0.5}, T: {"x": 6.5, "y": 2.5, "t": 0.5}},
})

from playwright.sync_api import sync_playwright  # noqa: E402

server, PORT = ui.serve(main.app)        # eigener freier Port je Lauf
install_homebase(MockHB("https://qivicon", "", "x"))
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = HERE.parent / "shots"
SHOTS.mkdir(exist_ok=True)
for f in SHOTS.glob("flow-*.png"):
    f.unlink()
problems = []


def center(page, sel):
    """Centre of the first element that matches the selector."""
    b = page.locator(sel).first.bounding_box()
    return (b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)


def node(label):
    """Selector of the node with this heading."""
    return f'.fl-node:has(.fl-head b:text-is("{label}"))'


def run(p, engine, name, mobile=False, **ctx_args):
    """One pass of the flow editor in one browser (desktop or phone): all checks, deviations end up in
    problems."""
    browser = getattr(p, engine).launch()
    ctx = browser.new_context(**ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    shot = lambda n: page.screenshot(path=str(SHOTS / f"flow-{name}-{n}.png"))  # noqa: E731
    tap = (lambda sel: page.locator(sel).first.tap()) if mobile else (lambda sel: page.locator(sel).first.click())

    def check(cond, msg):
        if not cond:
            problems.append(f"{name}: {msg}")
        return cond

    # ---------- Fix 1: the average of all rooms in the heating history can be clicked
    page.goto(BASE + "/#heating")
    page.wait_for_selector('[data-hroom="__avg__"]', timeout=10000)
    tap('[data-hroom="__avg__"]')
    page.wait_for_timeout(400)
    check("active" in (page.get_attribute('[data-hroom="__avg__"]', "class") or ""), "the average chip does not become active")
    check(page.locator("#hzChart svg path").count() > 0, "no chart for the average")
    page.locator("#hzChart").scroll_into_view_if_needed()
    shot("1-durchschnitt")

    # ---------- Fix 2: docked devices (window + radiator on the same wall) do not overlap
    page.goto(BASE + "/#plan")
    page.wait_for_selector(".fp-dev.dock-ind .bg", timeout=10000)
    page.wait_for_timeout(300)
    dots = page.evaluate("""[...document.querySelectorAll('.fp-dev.dock-ind .bg')].map(e => {
        const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2, r.width / 2] })""")
    if check(len(dots) == 2, f"nicht 2 Andock-Punkte: {len(dots)}"):
        (x1, y1, r1), (x2, y2, r2) = dots
        check(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 > r1 + r2, f"docking dots overlap: {dots}")
    # The window opens into the room (the room lies below y = 2). The window sits in the outer wall: contact dot centred
    # along the window but outside in front of it; thermostat dot in front of the radiator in the room (interior ones stay centred)
    sash_y = page.evaluate("+(document.querySelector('.fp-open.window .sash') || {getAttribute: () => NaN}).getAttribute('y2')")
    check(sash_y > 2.2, f"the window sash does not point into the room (y2={sash_y})")
    pos = page.evaluate(f"""(() => {{ const c = q => {{ const r = document.querySelector(q).getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2] }};
        return [c('.fp-dev.dock-ind[data-id="{C}"] .bg'), c('.fp-dev.dock-ind[data-id="{T}"] .bg'), c('.fp-open.window .cut')] }})()""")
    (cx, cy), (tx, ty), (wx, wy) = pos
    check(abs(cx - wx) < 2 and cy < wy - 2 and ty > wy,
          f"outer wall: contact dot not centred outside in front of the window / thermostat not in the room: {pos}")
    shot("2-andocken")

    # ---------- Flow-Liste & Vorlage
    page.goto(BASE + "/#flow")
    page.wait_for_selector('text=Automatisierungen')
    check(page.locator('[data-tab="flow"]').count() >= 1, "Flow fehlt in der Navigation")
    if mobile:
        tap('[data-act="new"]')
        page.wait_for_selector("#promptVal")
        page.fill("#promptVal", "Handy-Test")
        tap('.sheet .btn.primary')
    else:
        tap('[data-act="templates"]')
        page.wait_for_selector(".sheet [data-t]")
        shot("3-vorlagen")
        tap('.sheet [data-t]:has-text("Flurlicht bei Bewegung")')
    page.wait_for_selector(".fl-stage", timeout=10000)
    page.wait_for_timeout(500)
    fid = page.evaluate("HO.state.flowId")
    edges = lambda: page.locator(".fl-edges .fl-wire").count()  # noqa: E731
    nodes = lambda: page.locator(".fl-node").count()  # noqa: E731
    bool_ins = lambda: page.locator(f'{node("Wenn / Dann")} .fl-port.in.k-bool').count()  # noqa: E731
    disabled = lambda act: page.locator(f'#flEdit [data-act="{act}"]').first.is_disabled()  # noqa: E731

    def sheet_done():
        tap('.sheet .btn.primary:has-text("Fertig")')
        page.locator('.sheet-bg').wait_for(state='hidden')

    def minus_five():
        """Enter a negative number: invalid input is marked red, "±" flips the sign (iOS has no minus)."""
        tap(f'{node("Variable setzen")} .fl-head')
        page.wait_for_selector('.sheet .fl-field[data-k="op"] select')
        page.select_option('.sheet .fl-field[data-k="op"] select', "add")
        inp = '.sheet .fl-field[data-k="value"] input'
        page.wait_for_selector(inp)
        check(page.get_attribute(inp, "type") == "text" and page.get_attribute(inp, "inputmode") == "decimal", "a number field with a minus is no text field with a decimal keyboard")
        page.fill(inp, "abc")
        page.dispatch_event(inp, "change")
        check("invalid" in (page.get_attribute(inp, "class") or ""), "an invalid number is not marked red")
        page.fill(inp, "5")
        page.dispatch_event(inp, "change")
        tap('.sheet .fl-field[data-k="value"] [data-sign]')
        page.wait_for_timeout(200)
        check(page.input_value(inp) == "-5", f"„±“ does not give -5: {page.input_value(inp)!r}")
        sheet_done()

    def copy_paste(labels, want_nodes, want_edges):
        """Multi-selection in the mode "Auswählen", copy, paste, undo, redo."""
        n0, e0 = nodes(), edges()
        tap('#flEdit [data-act="pick"]')
        # in the mode "Auswählen" tapping toggles: first deselect the existing selection, then select the wanted ones
        for lab in page.locator(".fl-node.sel .fl-head b").all_inner_texts():
            tap(f'{node(lab)} .fl-head')
        page.wait_for_timeout(150)
        check(page.locator(".fl-node.sel").count() == 0 and disabled("copy") and disabled("del") and not disabled("undo"),
              "toolbar: without a selection copy/delete are active or undo is missing")
        for lab in labels:
            tap(f'{node(lab)} .fl-head')
        page.wait_for_timeout(150)
        check(page.locator(".fl-node.sel").count() == len(labels), f"multi-selection: {page.locator('.fl-node.sel').count()} instead of {len(labels)} selected")
        check(page.locator(".sheet-bg").count() == 0, "in the mode „Auswählen“ tapping opens the dialog")
        tap('#flEdit [data-act="copy"]')
        tap('#flEdit [data-act="paste"]')
        page.wait_for_timeout(250)
        check(nodes() == want_nodes and edges() == want_edges, f"paste: {nodes()} nodes / {edges()} lines instead of {want_nodes}/{want_edges}")
        shot("6-eingefuegt")
        tap('#flEdit [data-act="undo"]')
        page.wait_for_timeout(200)
        check(nodes() == n0 and edges() == e0, f"undo: {nodes()}/{edges()} instead of {n0}/{e0}")
        tap('#flEdit [data-act="redo"]')
        page.wait_for_timeout(200)
        check(nodes() == want_nodes and edges() == want_edges, "redo does not restore what was pasted")
        tap('#flEdit [data-act="undo"]')
        page.wait_for_timeout(200)
        tap('#flEdit [data-act="pick"]')
        check(nodes() == n0 and edges() == e0, "after undo the graph is wrong")

    if not mobile:
        check(nodes() == 5 and edges() == 4, f"Vorlage nicht 5 Knoten/4 Linien: {nodes()}/{edges()}")
        roles = page.locator(".fl-node .fl-role").all_inner_texts()
        check(sorted(roles) == ["Auslöser", "Bedingung"], f"roles are not on the cards: {roles}")
        check(bool_ins() == 2 and page.locator(f'{node("Wenn / Dann")} .fl-port.in.free').count() == 1, "Wenn/Dann zeigt nicht 1 belegten + 1 freien Bedingungs-Anschluss")
        check(page.locator(".fl-port.out.k-bool").count() == 1, "Ja/Nein-Ausgang der Bedingung fehlt")
        check(page.locator(f'{node("Gerätestatus")} .fl-port.out').count() == 2, "the state trigger does not have „beginnt“ and „endet“")
        check(page.locator(".fl-node .fl-gear").count() == 5, "Zahnrad (Hinweis auf Einstellungen) fehlt an Karten")
        check(page.locator(".fl-node.warn").count() == 0, f"template with open settings: {page.locator('.fl-node .fl-warn').all_inner_texts()}")
        shot("4-editor")
        # drag a node out of the palette – the ghost block follows the pointer
        page.locator('.fl-pal-item[data-type="presence"]').scroll_into_view_if_needed()
        sx, sy = center(page, '.fl-pal-item[data-type="presence"]')
        page.mouse.move(sx, sy)
        page.mouse.down()
        for k in range(1, 9):
            page.mouse.move(sx + (790 - sx) * k / 8, sy + (215 - sy) * k / 8)   # free place above "Wenn / Dann"
        check(page.locator(".fl-ghost").count() == 1, "kein Geister-Block beim Ziehen")
        shot("5-ziehen")
        page.mouse.up()
        page.wait_for_timeout(200)
        check(nodes() == 6 and page.locator(".fl-ghost").count() == 0, f"Ablegen fehlgeschlagen ({nodes()} Knoten)")
        # change the role: the trigger becomes a condition (yes/no output)
        tap(f'{node("Anwesenheit")} .fl-head')
        page.wait_for_selector('.sheet .fl-field[data-k="role"] [data-opt="condition"]')
        tap('.sheet .fl-field[data-k="role"] [data-opt="condition"]')
        page.wait_for_timeout(200)
        sheet_done()
        check(page.locator(f'{node("Anwesenheit")} .fl-port.out.k-bool[data-port="val"]').count() == 1, "a role change does not change the ports")
        # n+1: drag a line onto the free port (snaps) -> a new free one appears
        ax, ay = center(page, f'{node("Anwesenheit")} .fl-port.out[data-port="val"] i')
        bx, by = center(page, f'{node("Wenn / Dann")} .fl-port.in.free i')
        page.mouse.move(ax, ay)
        page.mouse.down()
        for k in range(1, 9):
            page.mouse.move(ax + (bx - 6 - ax) * k / 8, ay + (by + 4 - ay) * k / 8)
        check(page.locator(".fl-port.snap").count() == 1, "the line does not snap to the free condition port")
        page.mouse.up()
        page.wait_for_timeout(250)
        check(edges() == 5 and bool_ins() == 3, f"n+1: {edges()} lines / {bool_ins()} condition ports instead of 5/3")
        shot("6-verbunden")
        copy_paste(["Wenn / Dann", "Zeitpunkt", "Anwesenheit"], 9, 7)
        page.locator('.fl-pal-item[data-type="set_var"]').scroll_into_view_if_needed()
        page.locator('.fl-pal-item[data-type="set_var"]').click()
        page.wait_for_timeout(250)
        minus_five()
        # node dialog: properties come from the devices, "ends only after" is in the summary
        tap(f'{node("Gerätestatus")} .fl-head')
        page.wait_for_selector('.sheet .fl-field[data-k="prop"] select')
        check(page.input_value('.sheet .fl-field[data-k="prop"] select') == "motion", "property „Bewegung“ not preset")
        check(page.locator('.sheet .fl-field[data-k="target"] .fl-devitem input:checked').count() == 1, "the target selection does not show the chosen device")
        check(page.locator(".sheet [data-fire]").count() == 2, "test buttons for „beginnt“/„endet“ are missing")
        check(page.locator(".sheet .fl-cat .badge").first.inner_text().strip() == "Auslöser", "the role is missing in the dialog")
        check(page.locator(".sheet .fl-info .fl-sym").count() >= 1, "Anschluss-Legende fehlt im Dialog")
        page.fill('.sheet .fl-field[data-k="hold_off"] input', "5")
        page.dispatch_event('.sheet .fl-field[data-k="hold_off"] input', "change")
        page.wait_for_timeout(350)
        on_top = page.evaluate("""(() => { const r = document.querySelector('.sheet .fl-field[data-k="hold_off"] input').getBoundingClientRect();
            const el = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return !!(el && el.closest('.sheet')) })()""")
        check(on_top, "the settings sheet does not lie above the editor")
        shot("7-sheet")
        sheet_done()
        check("endet nach 5 Min." in page.inner_text(f'{node("Gerätestatus")} .fl-sum'), "summary on the node not updated")
        # the time display ("noch 0:45") hangs on the card as a badge: showing and hiding never changes its size
        jump = page.evaluate("""(() => { const n = document.querySelector('.fl-node'), b = n.querySelector('.fl-live'), h0 = n.offsetHeight
            b.classList.remove('hidden'); b.querySelector('span').textContent = 'Runde 3 · noch 0:45'
            const out = [h0, n.offsetHeight, getComputedStyle(b).position, b.offsetHeight > 0]; b.classList.add('hidden'); return out })()""")
        check(jump[0] == jump[1] and jump[2] == "absolute" and jump[3], f"the time display changes the card height: {jump}")
        # live: start a trigger by hand -> the path lights up, the condition shows yes/no
        tap(f'{node("Gerätestatus")} .fl-fire')
        page.wait_for_selector(".fl-node.run, .fl-wire.flow", timeout=4000)
        shot("8-live")
        page.wait_for_selector(".fl-node .fl-verdict", timeout=4000)
        check(page.locator("#toasts").inner_text().strip() != "", "the test of a trigger reports no result")
        want_nodes, want_edges, value = 7, 5, "-5"
    else:
        for t in ("play", "notify", "set_var"):
            tap('[data-act="pal"]')
            page.wait_for_selector(".fl-pal.open")
            page.wait_for_timeout(300)
            tap(f'.fl-pal-item[data-type="{t}"]')
            page.wait_for_timeout(350)
        check(nodes() == 3, f"tapping in the palette does not create 3 nodes ({nodes()})")
        check("open" not in (page.get_attribute(".fl-pal", "class") or ""), "the palette does not close after tapping")
        boxes = page.evaluate("[...document.querySelectorAll('.fl-node')].map(e => { const r = e.getBoundingClientRect(); return [r.x, r.y, r.right, r.bottom] })")
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                check(a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], f"new nodes overlap: {boxes}")
        shot("4-editor")
        # Antippen-zum-Verbinden
        tap(f'{node("Play")} .fl-port.out i')
        page.wait_for_timeout(150)
        check(page.locator(".fl-port.pending").count() == 1, "angetippter Anschluss nicht markiert")
        tap(f'{node("Benachrichtigung")} .fl-port.in i')
        page.wait_for_timeout(250)
        check(edges() == 1, f"Antippen-zum-Verbinden legt keine Linie an ({edges()})")
        shot("5-verbunden")
        copy_paste(["Play", "Benachrichtigung"], 5, 2)
        minus_five()
        tap(f'{node("Play")} .fl-head')
        page.wait_for_selector('.sheet .fl-field[data-k="label"] input')
        page.fill('.sheet .fl-field[data-k="label"] input', "Hallo")
        page.dispatch_event('.sheet .fl-field[data-k="label"] input', "change")
        page.wait_for_timeout(350)
        check(page.locator(".sheet .fl-cat .badge").first.inner_text().strip() == "Auslöser", "the role is missing in the dialog")
        shot("7-sheet")
        sheet_done()
        check("Hallo" in page.inner_text(f'{node("Play")} .fl-sum'), "Zusammenfassung am Knoten nicht aktualisiert")
        want_nodes, want_edges, value = 3, 1, "Hallo"

    # palette: one node per idea, Pro nodes with a subtle tag (Solange instead of Wiederholen/Solange)
    check(page.locator(".fl-pal-item").count() == 33, f"Palette hat {page.locator('.fl-pal-item').count()} statt 33 Bausteine")
    check(page.locator('.fl-pal-item[data-type="loop"] .fl-pro-tag').count() == 1, "Solange is missing in the palette or not marked as Pro")
    check(page.locator('.fl-pal-item[data-type="device_state"] .fl-pro-tag').count() == 0, "Gerätestatus is marked as Pro")

    # Einschalten, gespeichert?
    tap('[data-act="enable"]')
    page.wait_for_timeout(1500)   # async save – flows_store check below catches failures
    f = flows_store.get(fid) or {}
    check(f.get("enabled") is True, "Automatisierung nicht eingeschaltet")
    check(len(f.get("nodes", [])) == want_nodes and len(f.get("edges", [])) == want_edges,
          f"nicht gespeichert: {len(f.get('nodes', []))} Knoten / {len(f.get('edges', []))} Linien")
    var = next((n for n in f.get("nodes", []) if n["type"] == "set_var"), {"config": {}})
    check(var["config"].get("value") == -5 and var["config"].get("op") == "add", f"-5 nicht gespeichert: {var['config']}")
    check(any(value in str(n.get("config")) for n in f.get("nodes", [])), "Einstellung nicht gespeichert")
    if not mobile:
        state = next(n for n in f["nodes"] if n["type"] == "device_state")["config"]
        check(state.get("hold_off") == 300 and state.get("target") == {"devices": [M]}, f"Gerätestatus not saved: {state}")
    shot("9-aktiv")

    # back to the list: a triggered flow lights up
    tap('[data-act="back"]')
    page.wait_for_selector(f'[data-open="{fid}"]', timeout=8000)
    if mobile:
        tap(f'[data-play="{fid}"]')
        page.wait_for_timeout(800)
        check(any(e["message"] == "Hallo" and e["type"] == "flow" for e in events.recent(20)), "play in the list does not trigger the flow")
    else:
        page.evaluate(f"HO.api('flows/{fid}/run', {{ method: 'POST' }})")
        try:
            page.wait_for_selector(f'.fl-item.fired[data-flow="{fid}"]', timeout=4000)
        except Exception:
            check(False, "the triggered flow does not light up in the list")
    shot("10-liste")
    page.wait_for_timeout(1200)   # the list brings its state up to date after a trigger
    tap(f'[data-menu="{fid}"]')
    page.wait_for_selector(".menu-list")
    tap('.menu-list button:has-text("Löschen")')
    page.wait_for_selector('.sheet .btn.danger:has-text("Löschen")')
    tap('.sheet .btn.danger:has-text("Löschen")')
    page.locator(f'[data-open="{fid}"]').wait_for(state="hidden", timeout=6000)
    check(flows_store.get(fid) is None, "automation not deleted")

    print(f"{name}: Konsolenfehler: {len(errs)}")
    problems.extend(f"{name} Konsole: {e}" for e in errs)
    browser.close()


with sync_playwright() as p:
    run(p, "chromium", "desktop", viewport={"width": 1366, "height": 900})
    run(p, "webkit", "iphone", mobile=True, **p.devices["iPhone 13"])
server.should_exit = True
print("\n".join(problems) if problems else "UI OK – no problems, no console errors")
