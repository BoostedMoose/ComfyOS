"""Measurements for dev/perf.md – the same measurements before and after an optimisation (rule: measure first, then change).

    python dev/measure.py backend [path-to-comfyos]     # on the Pi: response times, device queries, load when idle
    python dev/measure.py frontend [path-to-comfyos]    # on Windows (Playwright): frame times, live messages

Measured against the demo home (tests/support/world_demo.py) – no real devices, no internet.
With a second checkout as the path an older state can be measured (`git worktree add <folder> <tag>`).
The output is a Markdown table to paste into dev/perf.md.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODE = sys.argv[1] if len(sys.argv) > 1 else ""
sys.argv = [sys.argv[0]] + sys.argv[2:]                 # the test world reads the root from the first argument
sys.path.insert(0, str(HERE.parent / "tests"))          # shared scaffolding of the tests (support)


def spread(values: list[float]) -> str:
    """Median and 95th percentile in milliseconds."""
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return f"{statistics.median(ordered):.1f} ms (95 %: {p95:.1f} ms)"


def backend() -> None:
    """Response times of the most important routes, device queries per minute and CPU time when idle."""
    from support import world_demo as demo
    from support.world_demo import MockHB, auth, core, install_homebase, main
    from app import flow_engine, flows_store, plugins, scenes_store
    from fastapi.testclient import TestClient

    calls: dict[str, int] = {}

    def count(rec):
        """Count every call of Plugin.devices()."""
        original = rec.plugin.devices

        async def devices():
            calls[rec.id] = calls.get(rec.id, 0) + 1
            return await original()
        rec.plugin.devices = devices

    rows = []
    with TestClient(main.app) as client:
        install_homebase(MockHB("https://qivicon", "", "x"))
        client.cookies.set(auth.COOKIE, auth.create_session("messung"))
        assert client.post("/api/plugins/hue/pair", json={"host": "192.0.2.10"}).json().get("connected")
        for rec in plugins.records():
            if rec.active:
                count(rec)
        for path in ("/api/overview", "/api/devices", "/api/rooms", "/api/power"):
            for _ in range(3):
                client.get(path)
            times = []
            for _ in range(40):
                t0 = time.perf_counter()
                assert client.get(path).status_code == 200, path
                times.append((time.perf_counter() - t0) * 1000)
            rows.append((f"Antwortzeit `GET {path}`", spread(times)))

        # operation: one open page, one enabled flow, one dynamic scene in the living room
        nodes, edges = demo.demo_flow()
        flow = flows_store.create("Messung", nodes, edges, enabled=True)
        client.portal.call(flow_engine.engine.reload) if hasattr(flow_engine.engine, "reload") else None
        client.post("/api/scenes/polarlicht/apply", json={"room_ids": ["wohnen"]})
        listener = __import__("asyncio").Queue(maxsize=100)
        core._listeners.add(listener)
        calls.clear()
        window = 30.0
        cpu0, t0 = time.process_time(), time.time()
        time.sleep(window)
        cpu, wall = time.process_time() - cpu0, time.time() - t0
        core._listeners.discard(listener)
        for pid, n in sorted(calls.items()):
            rows.append((f"Device queries per minute, plugin `{pid}`", f"{n * 60 / wall:.0f}"))
        rows.append(("CPU time in operation (one page open, one flow, one dynamic scene)", f"{100 * cpu / wall:.1f} % of one core"))
        flows_store.delete(flow["id"])
        scenes_store.clear(None)
    print(f"\n| Messung (Backend, {time.strftime('%d.%m.%Y %H:%M')}) | Wert |\n|---|---|")
    for name, value in rows:
        print(f"| {name} | {value} |")


FRAME_JS = """() => {
  window.__frames = []; let last = 0
  window.__framesOn = true
  const tick = t => { if (last) window.__frames.push(t - last); last = t; if (window.__framesOn) requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
}"""
FRAME_STOP_JS = "() => { window.__framesOn = false; return window.__frames }"
SLOW_FRAME = 25.0        # ms – from here a frame counts as a stutter (60 frames per second would be 16.7 ms)


class Probe:
    """Measures inside a page what an action costs: CPU time of the main thread (through the DevTools protocol of
    Chromium) and the distances between the frames (requestAnimationFrame)."""

    def __init__(self, page):
        """Switch the measurement on for this page."""
        self.page = page
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.send("Performance.enable")

    def throttle(self, rate: float) -> None:
        """Throttle the processor of the browser (1 = normal, 4 = four times slower – about an old tablet)."""
        self.cdp.send("Emulation.setCPUThrottlingRate", {"rate": rate})

    def _metrics(self) -> dict[str, float]:
        """Running counters of the browser (seconds since the page was loaded)."""
        return {m["name"]: m["value"] for m in self.cdp.send("Performance.getMetrics")["metrics"]}

    def run(self, action) -> dict[str, float]:
        """Run `action()` and measure. Result in milliseconds: `task` (main thread in total), `script`,
        `layout` (style + layout), plus `p50`/`p95` of the frame distances and `slow` (number of stutters)."""
        self.page.evaluate(FRAME_JS)
        before = self._metrics()
        action()
        after = self._metrics()
        frames = sorted(self.page.evaluate(FRAME_STOP_JS)) or [0.0]
        delta = lambda key: (after[key] - before[key]) * 1000     # noqa: E731
        return {"task": delta("TaskDuration"), "script": delta("ScriptDuration"),
                "layout": delta("LayoutDuration") + delta("RecalcStyleDuration"),
                "p50": statistics.median(frames), "p95": frames[min(len(frames) - 1, int(len(frames) * 0.95))],
                "slow": sum(1 for f in frames if f > SLOW_FRAME), "frames": len(frames)}


def frontend() -> None:
    """Frame times and CPU time in the browser: pan and zoom the floor plan, live messages, weather effects,
    building the home page – on the desktop and on a tablet with a throttled processor."""
    from support import ui
    from support.world_demo import WEISS, MockHB, auth, httpx, install_homebase, main
    from playwright.sync_api import sync_playwright

    server, port = ui.serve(main.app)
    install_homebase(MockHB("https://qivicon", "", "x"))
    token = auth.create_session("messung")
    base = f"http://127.0.0.1:{port}"
    api = httpx.Client(base_url=base, cookies={auth.COOKIE: token}, timeout=15)
    assert api.post("/api/plugins/hue/pair", json={"host": "192.0.2.10"}).json().get("connected")
    steps = 90

    def drag(page, box):
        """Drag across the surface with the mouse held – one step per frame."""
        x, y = box["x"] + box["width"] * 0.65, box["y"] + box["height"] * 0.6
        page.mouse.move(x, y)
        page.mouse.down()
        for i in range(1, steps + 1):
            page.mouse.move(x - i * 2.5, y - i * 1.5)
            page.wait_for_timeout(8)
        page.mouse.up()
        page.wait_for_timeout(300)

    def per_step(res, count):
        """Result of measuring a gesture as text."""
        return (f"{res['task'] / count:.1f} ms je Schritt (Layout {res['layout'] / count:.1f} ms) · "
                f"Bild {res['p50']:.0f} ms, 95 %: {res['p95']:.0f} ms · {res['slow']} Ruckler")

    def measure(playwright, label, rate, **ctx_args) -> dict[str, str]:
        """All measurements in one browser. Returns {measurement: value as text}."""
        browser = playwright.chromium.launch()
        ctx = browser.new_context(**ctx_args)
        ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": base}])
        page = ctx.new_page()
        page.goto(base + "/")
        page.wait_for_selector(".room-card .rc-head")
        page.wait_for_selector("#fpHost .fp-svg")
        page.wait_for_timeout(1500)
        probe = Probe(page)
        probe.throttle(rate)
        out: dict[str, str] = {}

        # Leerlauf der Startseite (Wettereffekte laufen): Rechenzeit je Sekunde
        idle = probe.run(lambda: page.wait_for_timeout(4000))
        out["Startseite im Leerlauf mit Wettereffekten (Regen, Wind)"] = (
            f"{idle['task'] / 4:.0f} ms Rechenzeit je Sekunde · Bild {idle['p50']:.0f} ms, 95 %: {idle['p95']:.0f} ms · "
            f"{idle['slow']} Ruckler in 4 s")

        # the same after ten seconds: by then the weather effects have adjusted to the device (since 0.20)
        page.wait_for_timeout(10000)
        idle = probe.run(lambda: page.wait_for_timeout(4000))
        out["… after adjusting (10 s later)"] = (
            f"{idle['task'] / 4:.0f} ms Rechenzeit je Sekunde · Bild {idle['p50']:.0f} ms, 95 %: {idle['p95']:.0f} ms · "
            f"{idle['slow']} Ruckler in 4 s")

        # Live messages: 20 state changes of a lamp. Given is the whole CPU time in this period –
        # next to it what the same period costs when idle (the difference is the messages).
        def toggles():
            for i in range(20):
                api.post(f"/api/devices/{WEISS}/command", json={"state": {"on": i % 2 == 0}})
                page.wait_for_timeout(180)
        t0 = time.time()
        live = probe.run(toggles)
        seconds = time.time() - t0
        out["20 Live-Meldungen auf der Startseite (Lampe schaltet)"] = (
            f"{live['task']:.0f} ms Rechenzeit in {seconds:.1f} s (Leerlauf gleicher Dauer: {idle['task'] / 4 * seconds:.0f} ms) · "
            f"{live['slow']} Ruckler")

        # rebuild the home page: whole CPU time in the 1.5 seconds after (idle of the same duration next to it)
        def rebuild():
            page.evaluate("() => HO.go('home', true)")
            page.wait_for_selector("#fpHost .fp-svg")
            page.wait_for_timeout(1500)
        build = probe.run(rebuild)
        out["Startseite neu aufbauen (1,5 s)"] = (
            f"{build['task']:.0f} ms Rechenzeit (Leerlauf gleicher Dauer: {idle['task'] / 4 * 1.5:.0f} ms)")

        # view on the home page: zoom in, then pan
        plan = page.locator("#fpHost .fp-svg")
        plan.scroll_into_view_if_needed()
        for _ in range(3):
            page.click('[data-act="fpZoomIn"]')
            page.wait_for_timeout(150)
        page.wait_for_timeout(400)
        box = plan.bounding_box()
        out["Pan the floor plan on the home page (zoomed in)"] = per_step(probe.run(lambda: drag(page, box)), steps)

        # full screen: pan and zoom with the mouse wheel
        page.evaluate("() => { location.hash = '#plan' }")
        page.wait_for_selector(".pv .fp-svg")
        page.wait_for_timeout(1200)
        box = page.locator(".pv .fp-wrap").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.wheel(0, -500)
        page.wait_for_timeout(500)
        out["Pan the floor plan in full screen (zoomed in)"] = per_step(probe.run(lambda: drag(page, box)), steps)

        def wheel():
            for i in range(40):
                page.mouse.wheel(0, -30 if i < 20 else 30)
                page.wait_for_timeout(16)
            page.wait_for_timeout(400)
        out["Grundriss im Vollbild zoomen (Mausrad, 40 Schritte)"] = per_step(probe.run(wheel), 40)
        browser.close()
        print(f"  gemessen: {label}", file=sys.stderr)
        return out

    with sync_playwright() as p:
        desktop = measure(p, "Desktop", 1, viewport={"width": 1366, "height": 900})
        tablet = measure(p, "Tablet, gedrosselt", 4, viewport={"width": 1024, "height": 768}, device_scale_factor=2,
                         has_touch=True)
    server.should_exit = True
    stamp = time.strftime("%d.%m.%Y %H:%M")
    print(f"\n| Messung (Frontend, Chromium, {stamp}) | Desktop 1366×900 | Tablet 1024×768 @2x, Prozessor 4× gedrosselt |")
    print("|---|---|---|")
    for name in desktop:
        print(f"| {name} | {desktop[name]} | {tablet[name]} |")


if MODE == "backend":
    backend()
elif MODE == "frontend":
    frontend()
else:
    sys.exit(__doc__)
