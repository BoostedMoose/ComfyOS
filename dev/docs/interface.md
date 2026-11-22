# Interface

Area documentation of ComfyOS – part of the guide `CLAUDE.md` (which says when to read this file).
This file says how the area is built and what to watch out for when changing it; version notes ("since 0.x") name
when something was added.

## Since 0.24.5

- **Nothing makes the page wider than the window:** `html { overflow-x: hidden }` (only on html – on body as well, body would
  become a scroll container of its own and the sticky `.topbar` would stop sticking). Background: Safari on the iPad zooms a page
  out whose content is wider than the screen, or lets it slide sideways; the bars that stay put while scrolling then end up offset
  until a reload. `compat.js` adds `shrink-to-fit=no` to the viewport where `GestureEvent` exists (browsers on iPhone/iPad) – not
  in `index.html`, because Chrome reports the unknown key as a console error.
- **Locking the window in full screen** (`#plan`, Flow editor) is now `html.chromeless` (set by `app.js` alongside
  `body.chromeless`): since html carries an overflow itself, an overflow on body no longer reaches the window.
- **Sidebar fixed** (`position: fixed; top: 0; bottom: 0`, `.main` with `margin-left: 248px`, `0` under `body.nav-hidden` and
  `body.chromeless`) instead of `sticky` with `height: 100vh` – 100vh is taller than the visible area on the iPad. It scrolls on
  its own if the window is too low.
- **Finding on the diagnosis page:** `HO.checkWidth(tab)` (`diag.js`) runs two seconds after every page (`app.js`). If the page
  is wider than the window it notes the outermost elements sticking out on the right (not those inside a scrolling row); `#diag`
  shows it under "Seitenbreite" next to the zoom of the visible area (`visualViewport.scale`), and "Angaben kopieren" includes it.
  A finding there is a bug to fix at its source – the overflow guard only hides it.

## Since 0.24

### Widget view of the home page (`js/widgets.js`, `js/widget_types.js`)

- **Two views, one page:** the home page is either `classic` (floor plan, key figures, room tiles – unchanged) or `widgets`. The
  switch (`.view-seg`, `viewBarHTML` in `overview.js`) is at the same place top right in both; the choice belongs to the device
  (`localStorage ho.homeView`, the shown page in `ho.homePage`) and survives leaving the page and the app. Both views share the
  data flow of `overview.js` (`refresh`, live stream, optimistic states) and **its click handlers**: a widget emits the same data
  attributes as the classic page (`data-cmd`, `data-dev`, `data-home`, `data-lights`, `data-room-lights`, `data-room-bri`,
  `data-house`, `data-sc-apply`, `data-media-act`, `data-act="power|wx|warn"`, `data-goto`). So every switching path stays the one
  of the home page (device lock, event log). Only `data-wg-play` (flow button) is carried out by the board itself.
- **The board (`HO.widgets.mount(host, { bar, home, overview, doc })`):** a page is a grid of cells; widgets lie on it absolutely
  positioned and snap to it – the look of the floor plan and Flow surfaces (dot grid in the editor or as the page background).
  Per page: columns wide (4–24) and on the phone (2–6), row height (flat/normal/square), gap, "close gaps upwards", background.
  Below `NARROW_BELOW` (700 px container width) the phone grid applies. **Two arrangements per widget:** `wide` and `narrow`; a
  widget without `narrow` gets a place derived from the wide one (`deriveNarrow`: reading order, width in proportion, first free
  place). Moving something on the phone writes the phone places down (`freezeNarrow`); "Handy-Anordnung neu berechnen" forgets them.
- **Editor ("Bearbeiten"):** every widget carries a surface (`.wg-edit`) with a handle, tools and a corner for its size. Mouse and
  pen drag anywhere, a finger only at handle and corner (`touch-action: none` there – everywhere else the finger scrolls the
  page; no `setPointerCapture`). A drag only moves elements (`tryPlace` → `layout`), nothing is rebuilt; the others make room
  (`settle`: what is covered moves down). Arrow keys move, Shift+arrows resize, Delete removes, Enter or a double click opens the
  settings; a tap on the free grid adds a widget there. "Rückgängig" keeps 40 steps. Saving: the whole view with
  `PUT /api/widgets`, half a second after a change; a 402 brings the last stored state back.
- **Settings of a widget:** content (the `fields` of its type), title, title line, style (Kachel/Glas/Akzent/ohne Rahmen), colour
  accent (`--wg-tint`: accent, ok/warn/bad, the eight series colours – no new colour values), size of the content (60–180 %,
  `--wg-z`), alignment, and place and size as numbers. Content also scales by itself with the room it has (`--wg-k`, size classes
  `data-sz`/`data-hz` = xs/s/m/l – set by `layout()`, since Safari 13 knows no container queries).
- **Widget types (21, `widget_types.js`):** Übersicht – Uhr, Grundriss, Raum, Geräte und Probleme, Fenster und Türen, Anwesenheit,
  Ereignisse, Verbindungen · Daten – Wetter, Messwert, Stromverbrauch, Heizung · Aktionen – Zuhause/Abwesend, Licht, Hausszene,
  Szenen-Schnellwahl, Gerät, Flow-Knopf, Wiedergabe · Gestaltung – Notiz, Überschrift. Contract and rules: heads of both files.
  A type has `render(widget, ctx)` (HTML, written through `HO.setHTML` only when it changes) or `mount(body, widget, ctx)` for
  content with a life of its own (clock: own timer; floor plan). The type names are the same as `widgets_store.TYPES`
  (`core/test_widgets` compares; a new type goes into both).
- **Floor plan as a widget:** `HO.floorplan.mount` with `camera: true` already measured itself at its container; new are
  `embed` (labels at the edge of the tile instead of below the top bar) and `still` (no panning or zooming, taps still count, the
  page scrolls over it – default; "Verschieben und Zoomen erlauben" switches it off). The home page hands in data, dialogs and
  saving (`HO.home.mountPlan`).
- **Look:** a widget is a tile like every other (`.card`: surface, border, radius; title line = `.eyebrow`). Light is only used
  for a state, the way the room tiles do it: lamps that are on (`.wg-glow`), the mood of the weather, a colour accent the user
  chose (`.wg-aura`). Blurred halos are dropped by `html.perf-low`. Gaps come from grid, so `compat.css` gets no new rules
  (only `.wg-foot` joins the exceptions for `margin: auto`).
- **Basic/Pro:** the view and one page are Basic; further pages are the Pro feature `advanced_dashboard`; a field marked `pro`
  needs its feature (weather forecast, power curve, heat map and weather effects in the floor plan widget). Shown only
  (`HO.proTag`, `HO.upsell`); the server enforces (`entitlements.check_widgets`). Without the plan a widget keeps its setting and
  shows a short hint (`proHint`) – it never asks the server for what would answer 402, and nothing stored is removed.
- **Sidebar:** in the widget view a button in the bar (`.nav-toggle`, `toggleNav` in `overview.js`) hides the sidebar on wide
  screens (`body.nav-hidden`, remembered in `localStorage ho.navHidden`). The class is only set while the widget view is shown –
  every other page keeps its sidebar, so nobody gets lost without navigation. The phone has no sidebar and no button.
- **First start:** an empty view offers "Mit Vorschlag starten" (`HO.widgets.starter(overview)`: widgets that fit what the home
  has) or "Leer beginnen". The demo home brings a sample page (`demo_world._widget_page`).
- **Tests:** `core/test_widgets` (store, routes, plans, one catalogue), `security/test_inputs` C10c, `ui/ui_widgets`.
- **Since 0.24.3 – nothing is cut off for good:**
  - **One scroller per widget:** `.wg-body` scrolls up and down when its content does not fit (no scrollbar); lists, scene
    tiles and notes inside no longer scroll on their own, they just take their height (`.wg > .wg-body > * { flex-shrink: 0 }`).
    Rows of chips still scroll sideways. Alignment "Mitte/Unten" works through free margins, so content that is too tall
    stays reachable from its start. Glows cover exactly the body (anything larger would make it scroll).
  - **Fading edges:** `markCuts` in `widgets.js` sets `cut-t`/`cut-b` on the body and `cut-l`/`cut-r` on rows (`FADE_ROWS`)
    after drawing, after a new size and while scrolling; `app.css` fades those edges with a mask (`.wg-fade`, `.wg-fade-x`).
  - **Grab and drag:** the mouse drags a widget's content like every scrolling row of the app – one handler in `lights.js`
    (`DRAG_ROWS` sideways, `DRAG_COLS` up and down, the first movement decides; `DRAG_NEVER` lists what is left out).
  - **Title line:** fixed height (`HEAD` in `widgets.js` = `.wg-head` in `app.css`), icon and title centred on one axis.
  - **Narrow widgets (phone):** size class `s` stacks instead of squeezing – home/away with the word below the icon, key
    figure above its line, rows without the state text, small rings without the second line.
  - **The switch stays put:** `.view-bar` stands top right in both views (on the phone to the right of the greeting, on a
    wide screen in the line of `#wgBar`, which is now a sibling of the switch and follows the greeting).

### Smaller changes

- **"Online" instead of "Live":** the state word of a connected service is "Online" – in the connection pill, in the head of a plugin tile (`plugins.js`), in the Magenta sync tile and in the widget "Verbindungen". The state key stays `live`.
- **Connection pill:** "Verbindet" (pulsing dot, class `wait`) while a plugin is still coming up after its start (state
  `connecting` from `core.connection_items`) – no longer "Fehler". The pill asks again every 4 s until that is over.
- **Tile "Externe Verbindungen":** the dot is green for an active service and grey otherwise – "only on demand" is no longer
  yellow (`EGRESS_DOT` in `settings.js`).

## Since 0.23.1

- **Demo in the page title instead of a bar:** in the demo every page title ends in "– DEMO" (`HO.demoTitle()` in `HO.go` and in
  the title of the home page, `overview.js` `setTitle`; class `.pt-demo`), the browser tab too (`HO.demoTitle(true)`). The bar
  `#demoBar` is gone; "Zurücksetzen" and "Demo beenden" are on the System page (`renderDemo` → `HO.demoAction`).
- **Tile "Externe Verbindungen" (System, eyebrow "Datenschutz") kept short:** per target a status dot instead of a badge
  (`.statusdot` green = `on`, yellow = `on_demand`, grey = `off`; the state text is its `aria-label`/tooltip), a short title, the
  purpose in one line and one line "Sendet … · <trigger> · zuletzt …"; the hosts are in the tooltip of the entry. The texts of the
  register are short phrases (`egress.REGISTRY`); a plugin's `desc` is split into title and purpose (`plugins._egress_title`:
  "Magenta-Sync: …" → title "Home Base: Magenta-Sync"; the plugin name loses its part in brackets, "Matter: Node.js laden"). `ui_system` checks dot, title length and full text width.
- **Heating page without a sync:** the sync line says "ComfyOS führt den Heizplan aus · Magenta-Sync aus" (`HO.syncLine`), below
  it a short note how the scheduler switches (`heating_scheduler.py`, `dev/docs/backend.md`). The old warning that schedules
  without the sync stay without effect is gone.

## Since 0.23

- **Demo home:** tile "Demo-Wohnung" under *System → Einstellungen* (`settings.js`: `demoCardHTML`, button `demoStart` →
  `HO.startDemo`, then a reload on the home page). In the demo `HO.demo.active` is true (`HO.refreshDemo` on boot), every page
  title says "– DEMO" (since 0.23.1; up to 0.23.0 a bar `#demoBar` above every page), the tab
  "Einstellungen" shows only the card of the demo with "Zurücksetzen" and "Demo beenden" (`renderDemo` – system tiles belong to the
  real home and are refused by the server anyway), "Ton dieses Rechners" is hidden (`media.js` `liveSupported`). `body.in-demo` is
  set for styles that need it.
- **Buttons:** kind label "Taster", icon `button`; the status line of a button device shows the last press for ten minutes
  ("An kurz · vor 5 s", `HO.notePress` on every live message `button`, `HO.buttonSeen`) – otherwise its number of buttons.
- **Ambient light:** sensors and motion detectors show it in their status line (`HO.lux`, "12 lx"); `illuminance` is a live field.
- **Flow editor:** field widget `button` (`WIDGETS.button`, `buttonsOf`) and the target filter `buttons` (only devices with buttons).

## Since 0.22.1

- **Language:** comments and file headers of the scripts are English; every text the user sees stays German for now
  (strings in the scripts, `index.html`, `flow_nodes.json`). An English interface comes later as localisation –
  do not translate interface strings in passing.
- **"Weitere Funktionen" in the device sheet (`overview.js`, `extrasHTML`/`bindExtras`):** capabilities with a `label`
  (plugin API revision 5) get a control by their `type` – switch (`bool`), number field with unit (`number`, `min`/`max`/`step`),
  selection (`enum`) or a button (`action`); a capability that is only readable shows its value. Changes go through
  `POST /api/devices/{id}/command` like every value. The big power button of the sheet only appears if the device can do
  `on` or has no further functions. Every yes/no value asks for confirmation before switching – it can be a door lock.
- **Forced redraw keeps the tab (`app.js`, `redrawPage()`):** the refresh button, the end of the test view and "retry"
  redraw the current page with its `HO.hashParams`. Before, `System?tab=plugins` fell back to the system settings and the
  tab button did nothing until a reload, because the hash had not changed.

## Since 0.22

- **Block "Wiedergabe" (`js/media.js`, `HO.media`):** speaker tiles on the home page (`#mediaBox`, block
  `media` with `needs: ['playing']`) – every device with the capability `playing`, no matter from which plugin. Per tile
  cover picture (only from `/api/media/image/…`, otherwise the symbol), room with group, track, artist, play/pause,
  track forward/back, volume with mute, "Favorit starten". Only what the device accepts is shown
  (`HO.caps.has(d, key, true)`); a locked device shows the padlock. Tapped values apply at once and for 3 s against
  older messages (`held` in `media.js`). `HO.media.handle(event, done)` carries out click and change – the
  home page calls it first in `page.onclick`/`page.onchange`; while the volume is being dragged, `partial()` does
  not rewrite the block (`sliding`). The same building block is in the device dialog (`#devMedia`) – a
  speaker never gets the on/off button there. Without speakers `#mediaBox` stays empty (and invisible).
- **State:** `HO.devState` shows "Spielt · Titel" or "Pausiert", `HO.devClass` reports `on` for a playing
  speaker (symbol in the floor plan and in lists). The small device tile in the room carries a playback button.
- **Style:** `.media-*` in `app.css` – grid instead of flex `gap`, so that `compat.css` stays untouched.

## Since 0.20.1

- **The tile grid holds its height (`HO.tileGrid`, `layout()`):** when the page rewrites the container, the
  tiles first lie loosely next to each other, and `layout()` empties it once more for distributing – measuring happens in between. Safari
  (iPhone, Mac) computes in the middle of that, the page was much shorter for a moment and jumped up as soon as one
  stood further down: on the home page with every live message of a device. Therefore `layout()` sets the last known height as `min-height`
  **first of all, before any measurement** (`lastHeight`, tracked by a `ResizeObserver`)
  and restores the scroll position. Whoever rebuilds `layout()`: measure nothing before the height is held
  (`ui_home` checks the scroll position in Chromium and WebKit).
- **System load with the share of ComfyOS:** `/api/system/info` delivers `cpu` = utilisation of all cores in percent
  (busy ticks from `/proc/stat`, Linux only, since 0.27.5) and `own` = `{cpu, ram, disk}` – CPU time of the
  service in percent of all cores (`time.process_time`), both measured over the same intervals from the start of the
  service and smoothed over one minute alike (`api/system._cpu`), the share never above the utilisation. The CPU bar
  shows the utilisation, the text names it with the load average (`12 % · Last 0.30 / 4 Kerne`); without `/proc/stat`
  the load stays the measure. Before 0.27.5 the bar was the load average – it counts waiting processes, not
  computing time, so on an idle machine the share of ComfyOS filled the whole bar. Further: its
  memory (`/proc/self/statm`, Linux only) and the space of its data, logs and backups (counted at most once per
  minute). In the tile the share lies hatched at the start of every bar (`.sys-own`); what the hatching means is said by
  a legend below the bars (`.sys-legend`) – nothing about it is written behind the individual bars.

## Since 0.20

- **Lower bound Safari 13 (iPadOS 13.4):** `tests/core/test_compat.py` forbids what is missing there (`inset`, `:is()`, `dvh`,
  `?.`, `??`, `replaceAll`, `scrollTo({…})` …). `user-select`, `backdrop-filter`, `appearance` always also come with
  `-webkit-`. Hover rules are in `@media (hover: hover)`. To the top of the page: `HO.toTop()`.
- **Safari up to 14.0 does not know distances in flex containers (`gap`).** `compat.js` measures that on start, sets
  `html.no-flexgap`, loads `compat.css` and marks elements next to plain text (`.fg-a`/`.fg-b`). `compat.css` is generated by
  `dev/make_compat_css.py` from `app.css` and the templates (lives in the repo; run it after changes to `gap` –
  otherwise `test_compat` reports it). Distances therefore **never as an inline style** but as a class (`.row.g2/.g6/.g8`).
  Reproducing on a new browser: `/?compat=nogap` (together with `tests/ui/ui_compat.py`).
- **Performance level `HO.perf`** (full or low power; `html.perf-low`): chosen under System → Diagnose, otherwise measured
  (weather effects) or assumed (browser without flex `gap`). Stored in the browser (`localStorage ho.perf`). Low power means:
  no blur behind surfaces, shadows in the floor plan without filters, weather effects with fewer pixels.
- **Diagnosis page `#diag` (`js/diag.js`):** browser, rendering (what is missing and how it is compensated), performance level
  with choice, frame rate probe, test area for pointer events. Link at the end of the system page.
- **Blocks (`js/blocks.js`; the classic view – the widget view of 0.24 has its own contract, `HO.widgets.register`):** `HO.blocks.register(id, {title, icon, needs, render, after})`,
  `HO.blocks.draw(id, el, ctx)`. The home page assembles `hero`, `planFoot`, `presence`, `stats`, `rooms` in a fixed order
  (`HOME_BLOCKS` in `overview.js`). `needs` names capabilities (`caps`), not plugins.
- **Only write what changes:** `HO.setHTML(el, html)` only writes when the HTML changed (or after an input in the
  element). That way `partial()` only redraws affected blocks; scroll positions and unfolded areas stay.
- **One source:** `HO.LIVE_FIELDS` (= `core.LIVE_FIELDS`, `test_style` section 6 compares), `HO.canControl(d)`,
  `HO.caps.has/devices`.
- **`app.css`** starts with a table of contents and the rules of the file.
- **Descriptions in the scripts:** every file starts with a header (purpose, public interface `HO.*`), every
  named function of five lines or more carries a comment directly above it that says what it does – not how.
  `test_style` section 7 reports every function without one. The loading order of the scripts is in `index.html`
  (`compat.js` first, then `core.js`, `blocks.js`, the pages, `app.js` last).
- **Tests:** `tests/ui/ui_compat.py` (position of all elements with and without flex `gap`), `ui_visual.py` (image comparison; reference from
  a second checkout: `python tests/ui/ui_visual.py <checkout> --save-ref`), `ui_camera.py`.

## Structure and rules

- **Frontend (`backend/app/static/js/`):** classic scripts `(function (HO) { ... })(window.HO)` on `window.HO` (loading order in `index.html`, `core.js` first). Cache busting through `?v=__V__` (set by `main.py`; include new static files & icon/theme/manifest with it on a version change). Use the helpers: `HO.api|icon|esc|toast|sheet|confirm|devClass|devState|devIssues|temp|debounce`; icons in `HO.iconPaths` (24×24). Keep generation guards (`gen`/`S.gen`) on timers. `HO.go` passes page parameters behind `?` in the hash (e.g. `#home?fx=rain`) through as `HO.hashParams` (since 0.16); a changed parameter re-renders the page. Styling: CSS variables from `app.css` (magenta `#e20074`, text `#ff6fb3`), no new hex values. **Page headers (since 0.19.2):** home, scenes, heating plan, flow and system start with a header without a tile (`.section-head` with `h2`, a short explanation and buttons on the right – Import/Neu or columns/Anordnen); new pages likewise. **Horizontal rows** (`.chips.scroll`, `.wx-hours`) scroll with the mouse wheel and – since 0.19.2 – by dragging with the mouse button held (`lights.js`; from 5 px on it counts as dragging, the click afterwards is swallowed; not in `.fp-tray`). **Stored passwords (since 0.18):** fields show dots instead of being empty (`HO.pwAttrs(stored)`), reading happens with `HO.pwValue(field)` (`''` = unchanged) – the real value never leaves the server. **Grid:** system page in column stacks (`.mz`, see "Arranging tiles freely"), plugin tiles without a wrapper in between.
- **Live states in the UI:** as long as a page is connected by SSE, `live_loop` (`core.py`) reconciles the devices every ~3 s and only sends changes (`LIVE_FIELDS`). Add new device fields relevant for display there and in the SSE handlers of `overview.js` and `lights.js`. Motion keeps glowing in the UI for `HO.MOTION_HOLD` (60 s). **One connection for the whole app (since 0.19): `HO.live.on(fn)`** (returns the unsubscribe function) – never again `new EventSource` per page: on the iPhone (few connections per server) pages got stuck after switching quickly. The server notices a closed page after at most `STREAM_CHECK` seconds.
- **Page change without flicker (since 0.19):** home, scenes and heating draw at once from the last state on a change (`HO.state.overview` or `S.routines`) and then refresh in the background; the loading spinner only appears without a state. Pages trigger the heating sync through `HO.kickSync()` (never two requests at once). Whoever builds a new page: no blank picture between two pages, no permanent connection of its own, no request that stays open for long with a sluggish remote end.
- **Connection bubble (since 0.19.2, replaces the status dot at the top and the badges of the main tile):** at the top in the bar next to "Aktualisieren" (`#connPill`, `app.js` `HO.setConn(status)` / `HO.refreshConn()`; older callers with `setConn(true, …)` only trigger a reload) – since 0.19.3 plain: dot and "Online" (up to 0.23 "Live") if all switched-on services are connected, otherwise "Fehler" ("Nichts verbunden" without a plugin); the individual services are only in the `title`. Tap → System, tab "Plugins". Entries from `core.connection_items` in `core.system_status()["items"]` (`/api/status` and `ov.status`): per set-up plugin `live`/`ok`/`bad`, the heating sync (Magenta) if set up, and switched-on plugins that do not run (`error`). **Switched-off plugins do not count** (wish of the operator). `core.integrations()` = set-up plugins with `configured`/`connected`/`live`; `configured` = at least one plugin set up (otherwise the home page shows "Noch nichts verbunden"), `connected` = none of them is disconnected. After switching a plugin on/off or saving it, `plugins.js` calls `HO.refreshConn()`.
- **App icons:** PNGs in `static/img/` are generated from the SVGs with `dev/make_icons.py` (never replace by hand).
- **Power consumption (since 0.13.1):** `core.power_summary`/`power_rows` summarise the current state (`GET /api/power`), `history.record_power` writes watts per device to `logs/power` (JSONL per month, the same sampling rate as the heating history), `history.power_series` condenses them into windows of `BUCKET_SECONDS` (`GET /api/power/series`, CSV through `/api/power/export` – both Pro feature `energy_analytics`). The line below the floor plan computes the sum in the browser from `ov.devices` so that it stays live over SSE. New log category ⇒ `paths.py`, `logstore._DIRS`/`MAX_BYTES`, backup export and log card in `events.js`.
- **Consumption since reset and today (since 0.16, `power_store.py` → `data/power.json`):** `{version: 1, reset_at, baselines, day, day_base, seen, estimate, estimate_day}`. `core._observe` calls `power_store.observe` for every device state fetched anyway (no request of its own). With a meter (`energy_kwh`) exact: reading − base; base = reading at the reset or when a device is first seen; day base = last reading before midnight (`seen` at the change of day). Without a meter (only `power_w`): watts × time between two queries, `estimated: true` (UI "≈"); gaps above `max(MAX_GAP, 2.5 × sampling interval)` and offline do not count – ComfyOS invents nothing. If a meter reading drops (the device reset its meter), the base is shifted, no jump. Writing: base changes at once, estimates at most every `SAVE_EVERY` s, `flush()` in `core.shutdown`. `POST /api/power/reset` (Basic, confirmation in the UI, event "Stromverbrauch zurückgesetzt"). `/api/power` delivers `since`, `kwh_since_reset`, `kwh_today`, `estimated` in total and per row; `/api/overview` delivers `power` for the tile ("Heute"). `/api/power/series` with `device_id` (several, ≤ `history.MAX_SERIES`) additionally delivers `series` per device – windows without a reading count as 0 W so that the series add up exactly to the sum `points`; window width by period (`history._bucket_for`). `power.json` is part of backup/moving package (afterwards `power_store.forget()`). Chart `HO.powerChart(el, points, {series, hidden, onToggle})`: sum as an area or one line per device in `--series-1…8` (fixed order by device name, the colour follows the device; at most 8), legend tappable, tooltip over all visible series. Tests: `tests/core/test_power.py`.
- **Heating plan import:** `heating_import.py`, endpoints `/api/heating/import|export` (never under `/api/routines/…` → route conflict). Page Heizplan (since 0.19.2): header without a tile with "Import" and "Neu" as for Flow, below it the tile with routine selection and active routine (`.hz-head`, `.hz-routine`).
