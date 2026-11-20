# Backend: structure, storage, background tasks, tests

Area documentation of ComfyOS – part of the guide `CLAUDE.md` (which says when to read this file).
This file says how the area is built and what to watch out for when changing it; version notes ("since 0.x") name
when something was added.

## Since 0.24

### Widget view (`widgets_store.py`, routes `api/widgets.py`)

- **`data/widgets.json`** (format in the docstring of the store, `version: 1`): pages with widgets – per widget type, title, look
  (style, colour accent, scale, alignment), a place in the wide grid, optionally one for the phone, and `opts` (settings of the
  type: simple values only). The store only checks and limits: ids by a fixed pattern (a missing, invalid or repeated one is
  replaced), numbers in their range, texts cut, at most `MAX_PAGES` pages with `MAX_WIDGETS` widgets and `MAX_OPTS` settings each,
  unknown types (`TYPES`) and keys dropped. What a widget shows is decided in the browser.
- **Routes:** `GET /api/widgets` (pages, `outdated`, `limits`) and `PUT /api/widgets` (the whole view; first
  `entitlements.check_widgets(old, new)`, then save). No route per widget – the view is small and one writer at a time is enough.
- **Outdated format:** a file with another `version` is neither converted nor shown (`outdated: true`, the interface says so);
  the next save replaces it (rule "no conversion of old data").
- **Plans (`entitlements.py`):** `WIDGET_FEATURES` maps (type, setting) to the Pro feature it shows; `check_widgets` is monotonic
  like `check_floorplan` – only one more page beyond the first (`advanced_dashboard`) or one more Pro setting is refused.
- **Backup and demo:** `widgets.json` is a backup file (`backup_store._DATA_FILES`). In the demo it lies in `data/demo/` through
  `jsonfile`; `PUT /api/widgets` is an allowed write there (`demo.ALLOWED_WRITES`); `demo_world.seed` writes a sample page.
- Guards: `tests/core/test_widgets.py`, `test_stores` (sample file), `test_demo`, `security/test_inputs` (C10c).

### Connection state `connecting`

`plugins.Record.started_at` (set in `plugins._start`) and `core.CONNECT_GRACE`: `core.integrations()` marks a configured plugin
that is not connected within 45 s after its start as `connecting`; `connection_items` passes that on instead of `bad`. The mark
ends for good as soon as the plugin was connected once.

## Since 0.23.1

### Heating scheduler (`heating_scheduler.py`)

ComfyOS runs the active heating routine itself – before, a schedule only had an effect through the Magenta sync.

- **What it does:** per room of the active routine `plan()` finds the switching point in force (`point_in_force`: last point of
  today up to now, otherwise of the days before, up to a week back). `tick()` sets the target of every thermostat in that room
  (`row["room_id"]`, any plugin) through `core.apply_device(d, {"target": …}, "schedule")` – device lock and event log included,
  the log line ends in " (Heizplan)" (`core.source_tag`).
- **Who is left out:** thermostats of the plugin whose system runs the schedule itself – `heating_sync.synced_plugin()`, the
  plugin with `syncs_heating` whose sync is configured (so far the Home Base with the Magenta sync). Two schedulers on one
  thermostat would get in each other's way. Without a sync every thermostat is run by ComfyOS.
- **Only at a switching point:** what was set last per room is kept as `(routine key, day, time, temp)` in `_applied` – memory
  only, per home (real/demo). A room is set again only when that changes: next switching point, another routine active, a changed
  value in force. A target set by hand stays until the next point. A thermostat that already has the value gets no command; a
  locked one is skipped; an offline one keeps the room due (tried again each round). A room without any thermostat in the device
  list stays due too (right after the start a plugin may not have reported yet). After a restart every room gets its current
  value once.
- **When:** long runner `heizplan` (`tasks.spawn`, every `TICK` = 60 s, the first round one tick after the start);
  `heating_scheduler.kick()` (`tasks.fire`) right after activating a routine, home/away, a schedule change and an import. Two
  rounds never run side by side: a second one asks for one more round instead.
- **Demo:** the demo home runs its schedule in `core.demo_live_loop` (every `TICK`, while a page of the demo is open); `kick()`
  from a request of the demo inherits the mark. The demo never reaches a real thermostat and the real home never sees the demo.
- Guard: `tests/core/test_heating_scheduler.py`.

## Since 0.23

### Demo home (`demo.py`, `demo_world.py`, routes `api/demo.py`)

A sample home **per browser session** – never a global mode: while one session looks at the demo, ComfyOS keeps running
unchanged for the real home (flow engine, live comparison, heating sync, plugins, every other session).

- **Mark per session:** `POST /api/demo/start` puts the hash of the session token into `demo._sessions` (memory only – after a
  restart every session is back in its home; at most `MAX_SESSIONS`). `GET /api/demo` says whether this session is in the demo,
  `POST /api/demo/stop` ends it (also on logout), `POST /api/demo/reset` sets the sample home up anew (only from the demo).
- **The middleware** `main.require_login` sets the context variable `demo._flag` for every request of such a session
  (`demo.enter`/`demo.leave`). Before that it refuses what could reach the real home: `demo.allowed` – reading is allowed except
  `DENIED` (backups, updates, server setup, HTTPS, security, live audio, restart, log export, flow test runs and webhooks, the
  bridge import of scenes, power export and reset, push, every plugin route except `/api/plugins/demo/devices`), writing only what
  `ALLOWED_WRITES` names (device commands and names, light, effects, floor plan, flows except `/api/flows/vars`, home/away, rooms,
  routines, scenes, layout and accent colour, demo stop/reset, logout). Refused: 403 with `demo.REFUSED`.
- **Where the mark is looked at (`demo.active()`):**
  - `jsonfile.read`/`write` map files under `data/` to `data/demo/` (`demo.path`) – every store follows without a change. Stores with
    their own file access do the same (`device_store._load` caches per file). `data/demo/` is no backup file (`backup_store._DATA_FILES`).
  - `settings_store`: the demo keeps only `layout` and `accent_color` of its own (`data/demo/settings.json`, `DEMO_KEYS`); everything
    else is the real `settings.json`, which the demo never writes.
  - `plugins._registry`: the demo sees only the plugin "Demo-Wohnung" (`demo_world.DemoPlugin`, id `demo` – reserved for real plugins,
    `plugins.RESERVED_IDS`). `records`, `active`, `get`, `collect`, `having`, `owner_by_id`, `unsettled_prefixes`, `static_file` use it.
    So no real plugin can be listed, configured or switched from the demo, and no room hub or heating sync takes part.
  - `core`: the merged state of the demo is kept apart (`core.last_merge`, `_demo_merged` – the real `_index` for live messages of
    plugins stays untouched); `_observe` records nothing (history, consumption); `publish` delivers only to pages of the same side
    (`core.listen` marks a queue with `demo`) and never calls `plugins.notify` for the demo; the first page in the demo starts
    `core.demo_live_loop` (ends 30 s after the last one).
  - `events.log`/`recent`: an event log of the demo in memory (`demo.note`, `MAX_EVENTS`); `logstore` writes and reads nothing.
  - `power_store._load`: an empty state, never the one in memory. `weather.current`/`alerts`: sample weather, no query.
  - Flow engine: `reload`, `flush`, `flow_enabled` do nothing in the demo; `GET /api/flows` and `/api/flows/vars` show no real
    variables. Demo flows are stored and shown but never run.
  - `tasks.spawn`: a long runner never inherits the mark (`demo.outside` in `_supervise`); one-off tasks (`tasks.fire`) do – they
    finish what a request of the demo began.
- **The sample home (`demo_world`):** since 0.24.1 a flat with seven rooms (`ROOMS`: `wohnen`, `kueche`, `schlafen`, `kind`,
  `arbeit`, `bad`, `flur`, reported like a hub) and about 40 devices in memory (`_START`, ids `demo:…`) – lamps with colour, sockets
  with consumption, thermostats moving towards their set point, window contacts and the front door, motion detectors with ambient
  light following the time of day, climate sensors, smoke detectors, blinds, two speakers with favourites, a wall switch with four
  buttons, two presences. The floor plan (`PLAN`) shows what the editor can: a bay window as a curved wall, a slanted corner, a
  passage between kitchen and living room, docked contacts and thermostats, furniture symbols, a device group. `seed` (inside the
  demo) writes floor plan, scene rooms, a heating plan per room (`HEATING`), three flows and a widget page.
- **README and wiki pictures** come from this demo (`tools/readme_screenshot.py`, since 0.24.1); only the flow editor in a test run
  and the plugin page come from the test world `world_demo`, because the demo refuses test runs and shows no real plugins. A
  change to `PLAN` is a change to those pictures – generate them anew and look at them.
- **Rule for new code:** whatever reads or writes data, talks to plugins or the internet, or runs in the background has to fit the
  demo – through the existing seams above or by checking `demo.active()`. `tests/core/test_demo.py` guards the promise.

## Since 0.22.3

- **Live audio (`live_audio.py`, routes `api/audio.py`):** the sound of a browser as one endless WAV stream for
  speakers. The browser sends 16-bit samples (stereo, 44.1 or 48 kHz) to `POST /api/audio/live/chunk` (at most
  512 KiB per piece, with login); `live_audio.feed` hands each piece to every listener's queue (40 pieces, a slow
  listener loses the oldest) – nothing is stored. `…/start` `{rate}` begins a transmission, `…/play` `{device_id}`
  sends `{"live_audio": true}` to a device through `core.apply_device` (device lock, event log), `…/stop` ends it and
  sends `false` to the devices that played; `GET /api/audio/live` is the state. One transmission at a time; without
  a piece for `IDLE_SECONDS` (8 s) it is over.
- **The stream `GET /stream/live.wav` answers without login** (`main.live_audio_stream`, decision of 19.11.2026 for a
  fixed address). It lies outside `/api/` and is therefore not in `PUBLIC_API`; `test_access` holds the list of such
  pages exactly. Kept narrow in `live_audio.refusal`: 403 for a client outside the home network, 404 while nothing
  runs, 503 above `MAX_LISTENERS` (6); only GET and HEAD. **With HTTPS the HTTP port answers this one path itself**
  (`tls.redirect_app` → `live_audio.asgi`, same process, same relay) instead of redirecting – speakers do not accept
  a self-signed certificate.
- **`egress.own_address_towards(host)`:** which own address leads to a device in the home network (a UDP socket is
  aimed, nothing is sent). `live_audio.url_for` builds the address a speaker is told from it and from
  `COMFYOS_HTTP_PORT`. Tests replace both (`egress.own_address_towards`, `live_audio.http_port`).

## Since 0.22.1

- **English code base:** comments, docstrings, test labels, tool output and this documentation are English. Everything
  that reaches the interface stays German for now: error texts of the API (`HTTPException(detail=…)`), texts of the
  event log (`events.log`), labels in manifests and in `static/flow_nodes.json`, and all strings of the interface.
  Messages of the installer and the start scripts for the operator stay German too.
- **Labelled capabilities (`control.py`):** a command that addresses a capability with a `label` (plugin API revision 5)
  always takes the generic path through `plugins.command` – also for lamps and sockets, which otherwise go through
  `send_light`. `_extra_text` builds the text for the event log (`Modus: auto`). `device_merge._caps` and
  `plugins.device_view` carry the label to the interface.

## Since 0.22

- **UDP in the home network (`egress.py`):** `open_datagram()` and `ssdp_search()` for plugins (`context.open_datagram`,
  `context.discover`) – details and limits in `dev/docs/plugins.md`. The core itself uses no UDP.
- **New route:** `GET /api/media/image/{plugin_id}/{image_id}` (cover picture of the playback through the plugin; with
  login; `tests/routes.json`, attack in `security/test_files`).

## Since 0.21

- **Helper programs only through `helpers.py`:** the only place in the core that starts a process for a plugin
  (`context.start_helper`, only with `helper` in the manifest) – without a shell, with an empty environment, JSON lines
  over stdin/stdout, supervised by `tasks.py`. Details in `plugins.md`; guard `tests/security/test_helpers.py`.
- **`tasks.cancel(name)`:** ends a single long-running task and waits for it (helper of a plugin that is switched
  off).
- **`core.live_loop` also runs without an open page** as long as a switched-on plugin listens for device changes
  (`plugins.watching()`, e.g. the running Matter plugin). `core.publish` passes every live message on to these
  plugins too (`plugins.notify`).
- **`core.source_tag(source, device)`:** one place for the addition in the log that says who switched
  ("(Flow)", "(durch <Plugin>)") – used by `apply_device`, `apply_lights`, scenes and `apply_home`.

## Since 0.20

- **Data files only through `jsonfile.py`:** `read(path, default, expect=dict, keep=True)` returns the default for a
  missing, broken or wrongly typed file and keeps an unreadable file as `<name>.defekt-<time>` (at most 3, readable only
  for the owner, event in the log); `write()` writes atomically. No store opens its file itself.
  `tests/core/test_stores.py` checks with sample files (`tests/data/stores/`): load and write back unchanged.
- **Background tasks only through `tasks.py`:** `tasks.spawn(name, factory)` for long-running tasks (restart after a
  crash), `tasks.fire(coro, name)` for one-off work, `tasks.report()` for faults (throttled into the event log),
  `tasks.cancel_all()` on shutdown. No loose `asyncio.create_task` in the core (`test_tasks`).
- **`core.py` is facade and state**, `control.py` carries commands/light/scenes, `flow_nodes.py` the flow nodes. What
  tests replace (`core.publish`, `core.all_devices`, `core.scene_step`, `core.restart_soon`, `flow_engine.rand/clock/_sleep/
  http_client/in_window`, `egress._transport`) is read through the module at runtime – never by `from … import`.
- **Style rules enforced by `tests/core/test_style.py`:** every function, method and class has a description; a
  swallowed error (`except …: pass`) needs a comment with the reason; text, list and object fields of the
  input models carry `max_length`; source with LF, without tabs.
- **No automatic API description:** `/docs`, `/redoc`, `/openapi.json` are switched off (they would live outside of
  `/api/` and therefore before the login). `test_access` checks the list of all pages outside of `/api/`.
- **Shutdown:** `core.shutdown` says goodbye to open pages (a full live stream does not hold it up), cancels
  tasks, stops plugins and writes power consumption and flow state – each on its own.
- **Tests:** folders `tests/core`, `flow`, `plugins`, `security`, `ui`; common scaffolding `tests/support/` (`boot.isolate`,
  `homebase`, `attack`, `ui`, test worlds `world_*`). `tests/run_all.py --group <folder>`, `--only <names>`, `--ui-only`.
  `dev/test_inventory.py` compares the check points of two runs, `dev/measure.py` measures for `dev/perf.md`.

## Structure and rules

- **Routes & structure (since 0.11):** a new route goes into the fitting router `api/<area>.py` (`router = APIRouter()`, included through `main.ROUTERS` – the order counts for placeholder paths). Read/set shared state only through `core.<name>` at runtime (`core.publish` – tests replace exactly that; tests put the Home Base into the plugin through `tests/support/homebase.install_homebase`). Routers never import `main`. Life cycle only through `lifespan` (`core.startup`/`core.shutdown`), no `on_event`. New/removed route ⇒ adapt `tests/routes.json` (`test_structure.py` checks the snapshot and shadowed routes). List routes with `main.routes()` (FastAPI includes routers as a whole, `app.routes` does not show them individually).
- **Backend (`backend/app/`):** `async` FastAPI. `*_store.py`: validate/limit inputs (`_num`, lengths, `MAX_*`), write atomically (`.tmp` → `replace`), add new fields to `_clean()`. Old formats are not converted (see "No conversion of old data").
