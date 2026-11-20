"""UI test for old browsers: without `gap` in flex containers (Safari up to 14.0, iPadOS 13) everything is in the same place.

The test opens every page twice side by side – once normally, once as if the browser did not know `gap` in
flex containers (every flex container gets `gap: 0`, the page is loaded with ?compat=nogap). Then the
fallback rules from compat.css apply. Compared is the position of all visible elements relative to their parent.
That proves that compat.css really replaces the gaps – not only that there is a way out for every rule
(that is checked by tests/core/test_compat.py).

    python tests/ui/ui_compat.py [path-to-comfyos] [--list]     # --list shows every deviation individually

What counts: more than GROB pixels is an error – a gap is missing there or something lies wrong. Smaller
deviations (above TOLERANZ) are only counted and reported. They are known and accepted:
  * below and next to wrapping rows with helper classes (`.row.wrap`) the gap stays once more;
  * containers with an inline gap (`style="gap:…"`) get the gap of their class;
  * if a head line wraps, the second line is indented by the gap.
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))        # tests/ – shared scaffolding (support)
from support import ui  # noqa: E402
from support import world_demo as demo  # noqa: E402
from support.world_demo import MockHB, auth, httpx, install_homebase, main  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

LIST = "--list" in sys.argv
TOLERANZ = 2.0                              # pixels: below this counts as "in the same place"
GROB = 16.0                                 # pixels: above this it is an error
FIXED = datetime(2026, 11, 18, 12, 0, 0)
# elements whose size says nothing – with the reason
ERLAUBT = {
    "mz-col": "column of the tile grid: its height depends on which tiles gather in it",
}

# This is how the browser pretends not to know `gap` in flex containers: every flex container gets gap: 0.
NO_FLEX_GAP = """() => {
  const sweep = () => {
    for (const el of document.querySelectorAll('body *')) {
      if (el.__nogap) continue
      const d = getComputedStyle(el).display
      if (d === 'flex' || d === 'inline-flex') { el.style.setProperty('gap', '0px', 'important'); el.__nogap = true }
    }
  }
  let queued = false
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; sweep() }) } }
  const start = () => {
    document.documentElement.classList.add('no-flexgap')
    new MutationObserver(later).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'] })
    sweep()
  }
  window.__sweepGaps = sweep
  // the script runs before <html> exists – then only start once the document is there
  if (document.documentElement && document.body) start()
  else document.addEventListener('DOMContentLoaded', start)
}"""

RECTS = """() => {
  if (window.__sweepGaps) window.__sweepGaps()
  const out = {}
  // sx/sy: scroll position of the parent elements (taken out – a bar may be scrolled differently far);
  // anchor: nearest identifier above (room, device, id) – tiles spread over the columns by height, so at
  // the same place in the tree there can be another tile
  const walk = (el, path, sx, sy, anchor) => {
    const cs = getComputedStyle(el)
    if (cs.display === 'none' || cs.visibility === 'hidden') return
    const r = el.getBoundingClientRect()
    const own = el.id || el.getAttribute('data-room') || el.getAttribute('data-id') || el.getAttribute('data-plugin') || el.getAttribute('data-tile')
    if (own) anchor = own
    // containers with compensation (negative margin right/bottom) are larger by the distance – only their position counts
    const evened = parseFloat(cs.marginRight) < 0 || parseFloat(cs.marginBottom) < 0
    if (r.width > 0 && r.height > 0) out[path] = [r.left + scrollX + sx, r.top + scrollY + sy, evened ? 0 : r.width, evened ? 0 : r.height,
      (typeof el.className === 'string' ? el.className.slice(0, 50) : '') + '|' + anchor]
    if (el.tagName === 'svg' || el.tagName === 'CANVAS' || el.tagName === 'SELECT') return
    let i = 0
    for (const c of el.children) walk(c, path + '/' + c.tagName.toLowerCase() + (i++), sx + el.scrollLeft, sy + el.scrollTop, anchor)
  }
  walk(document.body, 'body', 0, 0, '')
  return out
}"""

server, PORT = ui.serve(main.app)
install_homebase(MockHB("https://qivicon", "", "x"))
token = auth.create_session("ui")
BASE = f"http://127.0.0.1:{PORT}"
api = httpx.Client(base_url=BASE, cookies={auth.COOKIE: token}, timeout=15)
FLOW_ID = demo.steady(api)

# name, jump there, what is waited for, wait time afterwards (ms)
PAGES = [
    ("start", "#home", ".room-card .rc-head", 1500),
    ("szenen", "#light", ".section-head", 1200),
    ("heizplan", "#heating", ".section-head", 1200),
    ("flow-liste", "#flow", ".section-head", 800),
    ("flow-editor", None, ".fl-node", 1200),
    ("ereignisse", "#events", ".section, .card", 800),
    ("system", "#settings", "#sysGrid", 3500),
    ("plugins", "#settings?tab=plugins", "[data-plugin]", 3500),
    ("grundriss", "#plan", ".pv .fp-svg", 1200),
]
problems, notes = [], []


def open_page(browser, emulate, **ctx_args):
    """Open a page of the app in a context of its own – normally or without flex gap."""
    ctx = browser.new_context(reduced_motion="reduce", **ctx_args)
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    page.clock.set_fixed_time(FIXED)
    if emulate:
        page.add_init_script(f"({NO_FLEX_GAP})()")
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and "/api/scenes/image/" not in (m.location or {}).get("url", "") else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE + ("/?compat=nogap" if emulate else "/"))
    page.wait_for_selector(".room-card .rc-head")
    return page, errs


def goto(page, hash_, wait, pause):
    """Jump to the page and wait until it stands."""
    if hash_ is None:
        page.evaluate("id => { HO.state.flowId = id; location.hash = 'flowEdit' }", FLOW_ID)
    else:
        page.evaluate("h => { location.hash = h }", hash_)
    page.wait_for_selector(wait)
    page.wait_for_timeout(pause)


def allowed(cls: str) -> bool:
    """Does the element belong to those whose size says nothing (ERLAUBT)?"""
    return any(name in cls.split("|")[0].split() for name in ERLAUBT)


def run(p, engine, name, **ctx_args):
    """Compare all pages in one browser."""
    browser = getattr(p, engine).launch()
    normal, errs_a = open_page(browser, False, **ctx_args)
    old, errs_b = open_page(browser, True, **ctx_args)
    assert old.evaluate("() => document.documentElement.classList.contains('no-flexgap')")
    total = 0
    for label, hash_, wait, pause in PAGES:
        goto(normal, hash_, wait, pause)
        goto(old, hash_, wait, pause)
        if label == "flow-editor":
            for page in (normal, old):
                page.keyboard.press("0")
                page.wait_for_timeout(500)
        a, b = normal.evaluate(RECTS), old.evaluate(RECTS)
        for path, rb in b.items():                       # hide the size of compensated containers on both sides
            if path in a and rb[2] == 0 and rb[3] == 0:
                a[path][2] = a[path][3] = 0

        def inner(rects, path):
            """Position relative to the parent and own size – that way a shift only counts where it arises."""
            r, parent = rects[path], rects.get(path.rsplit("/", 1)[0])
            return [r[0] - parent[0], r[1] - parent[1], r[2], r[3]] if parent else r[:4]

        off = []
        for path, ra in a.items():
            if path not in b or b[path][4] != ra[4]:
                continue                                 # another element in this place (tiles distribute by height)
            ia, ib = inner(a, path), inner(b, path)
            delta = max(abs(ia[i] - ib[i]) for i in range(4))
            if delta > TOLERANZ and not allowed(ra[4]):
                off.append((delta, path, ia, ib, ra[4]))
        # the cause is the innermost deviating element: if a child grows, its parents only grow along
        causes = sorted((o for o in off if not any(other[1].startswith(o[1] + "/") for other in off)), reverse=True)
        gross = [c for c in causes if c[0] > GROB]
        total += len(gross)
        print(f"  {name} {label}: {len(a)} elements, {len(causes)} small deviations, {len(gross)} gross ones")
        if gross:
            problems.append(f"{name} {label}: {len(gross)} places deviate by more than {GROB:.0f} px")
        for delta, path, ia, ib, cls in (causes if LIST else gross[:8]):
            line = (f"    {delta:5.1f} px  .{cls.split('|')[0] or path.rsplit('/', 1)[-1]}  normal {[round(v) for v in ia]}  "
                    f"ohne gap {[round(v) for v in ib]}  ({path.split('/', 4)[-1]})")
            (problems if delta > GROB else notes).append(line)
    # diagnosis page: says whether the fallback rules are active; the performance tier can be chosen by hand
    for page, want in ((normal, "vorhanden"), (old, "ausgeglichen")):
        page.evaluate("() => { location.hash = '#diag' }")
        page.wait_for_selector("#diagFeatures .li")
        first = page.locator("#diagFeatures .li").first.inner_text()
        if want not in first:
            problems.append(f"{name} diagnosis: flex gaps should be '{want}': {first!r}")
    old.click('#diagMode [data-mode="low"]')
    if not old.evaluate("() => document.documentElement.classList.contains('perf-low') && HO.perf.low && HO.perf.mode === 'low'"):
        problems.append(f"{name} diagnosis: 'Sparsam' does not switch the performance tier")
    old.click('#diagMode [data-mode="auto"]')
    if old.evaluate("() => HO.perf.mode") != "auto":
        problems.append(f"{name} diagnosis: 'Automatisch' does not reset the choice")
    old.click('[data-act="frames"]')
    old.wait_for_timeout(2600)
    if "Bilder je Sekunde" not in old.locator("#diagFrames").inner_text():
        problems.append(f"{name} diagnosis: the frame rate probe returns no result")
    problems.extend(f"{name} Konsole: {e}" for e in errs_a + errs_b)
    browser.close()
    return total


ONLY = os.environ.get("UI_ONLY")
with sync_playwright() as p:
    if ONLY in (None, "", "desktop"):
        run(p, "chromium", "desktop", **ui.DESKTOP)
    if ONLY in (None, "", "tablet"):
        run(p, "webkit", "tablet", viewport={"width": 1024, "height": 768}, device_scale_factor=2, has_touch=True)

server.should_exit = True
time.sleep(0.2)
if LIST and notes:
    print("Small deviations:\n" + "\n".join(notes))
print("\n".join(problems) if problems else "UI OK – without flex gap no gap is missing, no console errors")
