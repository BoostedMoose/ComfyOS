# ComfyOS – dev folder

Tools for local development. **Maintained with every release.**

**Language (since 0.22.1):** code, comments, tests, tools and this documentation are English. Everything the user sees
in the app stays German for now – API error texts, event log, interface strings, manifest and flow node labels.
`CLAUDE.md`, `Roadmap.md` and the top-level `README.md` are German as well.

---

## Starting the test suite

**Backend tests run on the Pi, Playwright UI tests under Windows** (since 0.19.1). Playwright never runs on the
Pi – Chromium and WebKit max out the dev board and take two to four times as long there. Both may run
at the same time (two machines, every test with its own port and its own temp directories).

**Backend (Pi)** – locally on the Pi (or by `ssh <user>@<pi>`), in `cd ~/Development/comfyos`:
```bash
bash dev/test.sh                                  # all backend tests
bash dev/test.sh --group security                 # one folder: core, flow, plugins, security, public (several with a comma)
bash dev/test.sh --only test_weather,test_privacy # single tests by name
```
The script sets `PYTHONIOENCODING=utf-8` and calls `~/.venvs/comfyos-test/bin/python tests/run_all.py` (another
Python through `COMFYOS_TEST_PYTHON`). It rejects `--ui`, `--ui-only` and `ui_…`.

**UI (Windows, Git Bash)** – in the project folder `P:\home\<user>\Development\comfyos`:
```bash
cd /p/home/<user>/Development/comfyos
PYTHONIOENCODING=utf-8 ~/.venvs/homeos-test/Scripts/python.exe tests/run_all.py --ui-only                   # all UI tests
PYTHONIOENCODING=utf-8 ~/.venvs/homeos-test/Scripts/python.exe tests/run_all.py --only ui_system,ui_home   # single ones
```
Do not use `--ui`: it would additionally run all backend tests under Windows.

All tests run isolated in their own temp directories and need no running app. Every test is a process of its own
and can also be started directly: `python tests/<folder>/<test>.py [path-to-comfyos]`.

**While UI tests are running, change no files under `backend/app/static` or `public/plugins/*/static`** – the
browser reloads them in the middle of the run and the test sees a half state.

**After a change to `backend/requirements.txt`** (since 0.17: `pywebpush` for push) – in both environments:
```bash
~/.venvs/comfyos-test/bin/python -m pip install -r backend/requirements.txt                    # Pi
~/.venvs/homeos-test/Scripts/python -m pip install -r backend/requirements.txt   # Windows
```
If `pywebpush` is missing, `test_push.py` only skips the section with the real encryption (with a note).

**Rebuilding the venv** (only if broken):
```bash
# Pi (backend tests, without Playwright)
python3 -m venv ~/.venvs/comfyos-test
~/.venvs/comfyos-test/bin/python -m pip install -r backend/requirements.txt pytest
# Windows (UI tests)
python -m venv ~/.venvs/homeos-test
~/.venvs/homeos-test/Scripts/python -m pip install -r backend/requirements.txt playwright pytest
~/.venvs/homeos-test/Scripts/python -m playwright install chromium webkit
```

---

## Regenerating the app icons

```bash
~/.venvs/homeos-test/Scripts/python dev/make_icons.py      # needs Playwright (Chromium) – under Windows
```
Renders `apple-touch-icon.png` (180, borderless, opaque – iOS rounds itself), `icon-192/512.png`,
`icon-maskable-512.png` and `favicon-32.png` from `favicon.svg` or `icon-maskable.svg` into
`backend/app/static/img/` and checks size and content. Run it after every logo change
(up to 0.15 the PNGs were empty placeholders – the home screen icon on the iPhone was missing).

---

## Tools in the dev folder (since 0.20)

| File | What for |
|---|---|
| `dev/test.sh` | starts the backend tests on the Pi (see above) |
| `dev/make_compat_css.py` | Generates `backend/app/static/compat.css` from `app.css`: distances in flex containers for browsers without flex `gap` (Safari up to 14.0, iPadOS 13). **Run it after every change to a `gap` in `app.css`** – `tests/core/test_compat.py` reports when that was forgotten (`--check` only checks). No build step: the file lives in the repo. |
| `dev/measure.py` | Measurements for `dev/perf.md`: `backend` (Pi: response times, device queries, idle load) and `frontend` (Windows, Playwright: frame times while panning and zooming, idle load, also with a throttled CPU; since 0.28 also what one frame of the effects costs in the script, all of it and the inside layer – `HO.weatherFx.stats.drawMs`/`indoorMs`; since 0.30.3 the frames per second of the effects, the time from a click on a lamp switch until the new state is painted and the colour wheel dragged with the floor plan in view). `frontend-gpu` runs the same with the graphics card of the machine – the browser of the tests otherwise paints in software, a stand-in for weak devices. Rule: measure first, then change. With a second checkout as the path an older state can be measured. |
| `dev/perf.md` | The measurements: baseline 0.19.3, state 0.20.0, experiments and limits |
| `dev/test_inventory.py` | Check point inventory: records every output line of every test and compares two states – that way no check point is lost when tests are rebuilt |
| `dev/make_icons.py` | app icons from the SVG templates (see above) |
| `dev/build_matter.sh` | *(since 0.21, Pi, needs Node and npm)* Bundles the helper program of the Matter plugin to `public/plugins/matter/bridge/dist/bridge.mjs` (esbuild, one file with `matter.js`) and runs `npm audit`. The bundle is checked in – the run is only needed when `bridge.mjs`/`setup.mjs` is changed or `matter.js` is updated (`--update` resolves `package-lock.json` anew). `node_modules/` stays local (`.gitignore`). **The only build step in the project, and only for this plugin.** |
| `dev/fx_shots.py` | Check pictures of the weather effects (since 0.20.1, Windows/Playwright): photographs every weather situation on the floor plan of the sample home to `tests/shots/effects/` – all 14 previews, plus rain in three intensities (puddles), wind from 10 to 70 km/h (lines, leaves), rotated north (wind arrow), full screen during and after panning, heat map with scale 15–22 °C, system load with the share of ComfyOS (sample values), three pictures on the phone. `--only regen,wind` only takes pictures whose name contains one of the parts (the picture names are German). No test: nothing is checked, the pictures are for looking at. |
| `dev/fxlab.py` | **Effect lab** (since 0.27, Pi or any computer, standard library only): `python dev/fxlab.py` serves `http://127.0.0.1:8765` – the real floor plan (the scripts straight from `backend/app/static`, the demo home read from `demo_world.py` without importing the app) with every value that drives its effects next to it: weather preset and single values, time of day and day of the year (sun and moon), moon phase, north, floor, speakers (tap a device to let it play), roller shutters, heat map, full screen camera, performance tier, time lapse. The scene stands in the address (`#fx=rain&hour=14`), the page reloads when a script or style changes. `--plan data/floorplan.json` shows another plan, `--host 0.0.0.0` opens it to the home network. `--shot out.png --scene "fx=frost&hour=10"` takes one picture with a headless Firefox or Chromium (no Playwright; the lab ticks the effects by hand there, because a headless browser gives no animation frames). No login, no data, serves only `dev/fxlab/` and `backend/app/static/`. No test: for looking and tuning. |
| `dev/docs/` | Area documentation: `backend`, `plugins`, `floorplan`, `lights-scenes`, `flow`, `license`, `interface` – the details that used to be in `CLAUDE.md`. Read before working on an area. |

**Comparison with an older version** (measurement or image comparison): put its state next to the repo on the Pi and
pass it as the path – under Windows it is reachable through the same drive.
```bash
git worktree add /tmp/comfyos-ref v0.19.3          # Pi
python tests/ui/ui_visual.py P:/tmp/comfyos-ref --save-ref   # Windows: reference pictures of the old state, land in tests/shots/ref of this repo
python dev/measure.py frontend P:/tmp/comfyos-ref            # Windows: measurements of the old state
git worktree remove /tmp/comfyos-ref               # Pi, when done
```

---

## Licenses (owner only)

Secrets are in `tools/build_secrets.env` (gitignored): `OWNER_TOKEN` and `OWNER_SIGN_KEY`.
**This file belongs in the password manager.** The code (`backend/app/license_keys.py`) only contains
the hash of the token and the public key. `tools/mint_gift.py` and the secrets are
private: gitignored, not in the public repository and not in the release package. Start the tool with the Python of the ComfyOS environment (needs
`cryptography`); on the Pi: `.venv/bin/python tools/mint_gift.py …`.

```bash
python tools/mint_gift.py --init                      # once: create secrets, write license_keys.py
python tools/mint_gift.py --owner                     # write the owner file for the device it runs on
python tools/mint_gift.py --owner --out license.json  # … for copying to another device
python tools/mint_gift.py --plan pro --name "Anna"    # issue a license (file content + short form)
python tools/mint_gift.py --plan pro --iid <16 hex> --days 365 --out anna.comfyos-license
python tools/mint_gift.py --plan pro --batch 20 --out licenses/
python tools/mint_gift.py --verify anna.comfyos-license
```

**Making your own Pi the owner** – one of these ways:
1. Run `tools/mint_gift.py --owner` on the Pi as the ComfyOS user (writes
   `~/.config/ComfyOS/license.json`), or
2. paste `OWNER_TOKEN` from `build_secrets.env` under *System → Lizenz* and "Einlösen".

ComfyOS shows the plan after one minute at the latest or after reloading the license card.
No route and no update deletes the owner file – removing only works by hand.

**Issuing in the dashboard:** *System → Lizenz → Lizenz-Generator* (only with the owner token).
Deposit `OWNER_SIGN_KEY` once; every issue requires the ComfyOS password.

**Test view:** *System → Lizenz → Testansicht* switches the own installation to Basic or Pro for a limited
time. Data stays unchanged; flows the plan does not run pause for that long.

**Revoking a license:** enter the number (`id`) in `REVOKED` in `backend/app/license_keys.py` –
takes effect from the release that contains it.

---

## Structure of the tests (since 0.20)

| Folder | Content |
|---|---|
| `tests/core/` | devices, lock, scenes, power, history, weather, push, license, plans, structure, data files, background tasks, style, old browsers (static) |
| `tests/flow/` | engine, nodes, ports |
| `tests/plugins/` | loader and interface, interplay of the plugins with the core |
| `tests/security/` | attacks by attack surface and privacy – **must always be green** |
| `tests/ui/` | interface in the browser, per page (Playwright, only under Windows) |
| `tests/support/` | common scaffolding, no test (see below) |
| `tests/data/stores/` | sample files of all data files for `test_stores` (mock data only; specifically excepted in `.gitignore`) |
| `public/plugins/*/tests/` | tests of the shipped plugins – run without ComfyOS |

**Scaffolding `tests/support/`:** `boot.py` (sealed-off environment – every test starts with `ROOT, tmp = boot.isolate("name")`,
only then `app` is imported), `homebase.py` (mock Home Base, formerly `tests/homebase_mock.py`), `attack.py`
(security tests: outside world cut off, raw requests to the ASGI app, collecting findings), `ui.py` (app on a free port,
the three browser runs, image comparison) and the test worlds `world_hue.py`, `world_scenes.py`, `world_pairing.py`,
`world_sonos.py` (since 0.22: Home Base with Sonos things and a Sonos household in memory including SSDP answers),
`world_demo.py` (sample home – also for the README pictures and the measurements). A test world builds its
whole environment on import; backend and UI test of the same world share it instead of running the other's header.

**Language of the tests (since 0.22.1):** descriptions, comments and the output lines (check points) are English. German
stays where a test deals with what the user sees: fixture data (device and room names of the test worlds), texts of the
interface and of the API a test matches against, and the event log.

**Old → new** (older versions of the roadmap and the Git history still name the old names):

| Up to 0.19.3 | Since 0.20 |
|---|---|
| `tests/test_<name>.py` (devices, lock, scenes, power, history, weather, push, license, plans, structure, brand) | `tests/core/test_<name>.py` |
| `tests/test_flows.py`, `test_flow_ports.py`, `test_flow_nodes.py` | `tests/flow/…` |
| `tests/test_plugins.py`, `test_hue.py`, `test_integrations.py`, `test_mqtt_broker.py`, `test_pairing.py` | `tests/plugins/…` |
| `tests/test_security.py` (sections 1–8) and `tests/test_pentest.py` (A1–C11) | `tests/security/`: `test_access`, `test_browser`, `test_files`, `test_secrets`, `test_ssrf`, `test_fuzz`, `test_inputs` |
| `tests/test_privacy.py` | `tests/security/test_privacy.py` |
| `tests/homebase_mock.py` | `tests/support/homebase.py` |
| `ui_weather` | `ui/ui_home` |
| `ui_lights` | `ui/ui_scenes` |
| `ui_hue` | `ui/ui_system` |
| `ui_integrations` | `ui/ui_plugins` |
| `ui_flow`, `ui_pairing` | `ui/ui_flow`, `ui/ui_pairing` |
| – | new: `core/test_stores`, `core/test_tasks`, `core/test_style`, `core/test_compat`, `ui/ui_camera`, `ui/ui_compat`, `ui/ui_visual` |

Pentest C10 (tile order) no longer exists – the route `PUT /api/rooms/order` is gone.

---

## Backend tests

Every test runs in a process of its own against mock backends (no real Home Base, no real bridge, no network).
Order in the runner: folders `core`, `flow`, `plugins`, `security`, alphabetical within; then the tests of the plugins.
Every test prints one line per section – these lines are the list of check points (`dev/test_inventory.py`).
With 0.22.1 these lines became English: an inventory of an older state does not compare line by line with a new one.

### core/test_stores.py *(8 sections, since 0.20)*
Every store reads its sample file from `tests/data/stores/` and writes it back **byte for byte unchanged**.
As long as that is green, no file format changed – there are no conversion routines, so a format must never
change by accident. In addition: loading changes nothing; a damaged file (empty, cut off, no JSON, wrong type)
leads to no crash; an unreadable file is kept as a copy before overwriting (`jsonfile.py`).
Section 5 (since 0.25) holds the rules of several floors in `floorplan_store`: a plan from before 0.25 is exactly one
floor, a device stands on one floor only, ids of floors, rooms and groups and the levels are unambiguous, a deck is
a closed outline, the number of floors is limited. Section 6 (since 0.28): `stretch` of a plan symbol is kept only with a
symbol, limited to `SYMBOL_STRETCH`, 1 is not stored; since 0.29 the turn `rot` in whole degrees (rounded, 0 and 360 not
stored, only with a symbol). Section 7 (since 0.29): loads ("Verbraucher") are stored per floor only when there are any,
kind, light, device and symbol checked, the standards (warm, 100 %) not stored, ids unique across floors, at most `MAX_LOADS`;
since 0.29.1 an own colour (then no tone), candle light as the standard of candles, a symbol of the bubble only without a plan symbol.
Section 8 (since 0.29.1): the place of a docked dot along its element (`along`) only for a docked device, the middle not stored.
After a **deliberate** format change: `python tests/core/test_stores.py --write` and name the change in `Roadmap.md`.

### core/test_widgets.py *(6 sections, since 0.24)*
The widget view of the home page (`widgets_store.py`, `api/widgets.py`, `entitlements.check_widgets`):
1. Store: ids by a fixed pattern (missing, invalid and repeated ones replaced), numbers in their range, texts and lists cut,
   unknown types and keys dropped, limits for pages, widgets and settings; cleaning twice changes nothing
2. Routes: save and read back, ids stay, invalid bodies 422 without saving, only `widgets.json` is written
3. Outdated format (another `version`): marked, never converted or rewritten by reading, replaced by the next save
4. Plans with `ENFORCE`: one page and every widget type free in Basic; a second page 402 (`advanced_dashboard`), each Pro setting
   402 with its feature; content from a Pro period stays editable and deletable, more of it is refused, a refused change saves
   nothing; Pro everything; shadow mode only remembers
5. One catalogue: the type names of `widget_types.js`, and styles, colour accents and icons of `widgets.js`, are those of the
   store; every Pro setting of the server is marked `pro` in the interface with the same feature (and none more); script order
6. Backup file; the demo writes its own `widgets.json` and seeds a sample page without overlaps – the real file stays

The sample file `tests/data/stores/widgets.json` belongs to `test_stores`; the attack on the route is `security/test_inputs` C10c,
the interface `ui/ui_widgets`.

### core/test_live_audio.py *(4 sections, since 0.22.3)*
The sound of a browser as a stream for speakers (`live_audio.py`, `api/audio.py`):
1. Relay: WAV header, pieces in order to every listener, slow listeners skip ahead, invalid pieces and rates refused,
   at most `MAX_LISTENERS`, end by stopping and by silence of the browser
2. Address for speakers: only with a transmission, own address in the network of the device, HTTP port
3. The stream without login: 404 without a transmission, 403 from outside the home network, HEAD, no writing – also on
   the port that otherwise redirects to HTTPS (`tls.redirect_app`)
4. Routes for the browser: only with login, rate and size limited, unknown device refused, stopping ends everything

The speaker's side is in `plugins/test_sonos` (section 4b: capability only on rooms, address to the speaker through
the shared path with lock and event log, stopping) and in the plugin's own test; `ui_media` replaces the browser's
sharing dialog with a tone and checks that the tile's button reaches the speaker.

### core/test_demo.py *(6 sections, since 0.23)*
The demo home per browser session (`demo.py`, `demo_world.py`, `api/demo.py`) – the promise that it never touches the real home:
1. Start: only this session is in the demo, the sample home lies in `data/demo/`, another session keeps its home
2. The demo sees only the demo: devices (a button with four named buttons, ambient light), the plugin "Demo-Wohnung", its floor
   plan and flows, sample weather without any network, no real flow variables
3. Writing in the demo (command, floor plan, accent colour, a flow) stays in the demo – the real stores and `settings.json` are
   byte for byte the same, the flow engine keeps its flows, the event logs are apart
4. Refused in the demo (403 with the reason): plugins and their routes, backups, updates, restart, password, hosts, licence, log
   export, power reset, flow test runs and variables
5. Separation in the core (in the event loop of the app): live messages only to pages of the same side, plugins never hear of
   the demo, a long runner never inherits the mark, the flow engine never loads demo flows
6. Ending: reset sets the sample home up anew; stop and logout bring the own dashboard back unchanged; nothing went to the internet

### core/test_buttons.py *(3 sections, since 0.23)*
Buttons and ambient light (plugin API revision 7) with a plugin of the test registered directly in the loader:
1. Rows: kind `button` with `buttons`, `illuminance` as value, capability, live field and flow property; plugins with access see it
2. Presses (`context.button_pressed`): live message `button` to the pages, a line in the event log, never to listening plugins;
   the trigger "Taster" fires only for the right button and kind of press, once per press
3. Dropped: unknown kinds of press, invalid button ids, devices ComfyOS does not show

### core/test_heating_scheduler.py *(5 sections, since 0.23.1)*
The heating scheduler of the core (`heating_scheduler.py`) with two plugins of the test registered in the loader and the
device list replaced (`core.merged_devices`, `core.effective`):
1. Switching point in force: last point of today up to now, otherwise of the days before (up to a week back); broken points skipped
2. Without a sync: every thermostat of the room gets the value in force on the switching path of the interface (event log
   " (Heizplan)"); locked and offline ones not, nothing twice
3. A value set by hand stays until the next point; a changed value in force and a thermostat back online are set at once
4. With a sync: the thermostats of the syncing plugin are left to it, all others follow the newly activated routine; two rounds at
   once switch once
5. Real home and demo keep their state apart; the loop runs under supervision, the demo runs it in its live comparison

### core/test_tasks.py *(6 sections, since 0.20)*
Background tasks under supervision (`tasks.py`): long-running tasks restart after a crash, errors of one-off tasks
are in the event log (only the kind), `cancel_all` cancels everything. Device query: one query per plugin at a time, whoever
needs it fresher gets one of their own. Shutdown also saves with a full live stream; errors while saving and
recording are reported.

### core/test_style.py *(7 sections, since 0.20)*
Building rules, checked statically: (1) every file, class, function and method has a description – core, tests, tools,
SDK, plugins; (2) no silent error handling without a reason; (3) input models with length limits; (4) facade: what
tests replace through `core.` or `flow_engine.` is never imported by name; (5) LF line endings, no tabs; (6) one
source for live fields (`HO.LIVE_FIELDS` = `core.LIVE_FIELDS`) and switchability (`HO.canControl`), blocks of the home page
with a contract; (7) every script of the interface has a header, every function of five lines or more a description.
**New function without a description ⇒ this test turns red.**

### core/test_compat.py *(4 sections, since 0.20)*
Old browsers, static: nothing in CSS and scripts that Safari 13 (iPadOS 13.4) does not understand; `-webkit-` prefixes;
`compat.css` matches `app.css` (otherwise `python dev/make_compat_css.py`), `compat.js` loads first and only attaches it
when needed; no inline distances (`style="…gap…"`) in flex containers. The effect in the browser is checked by `ui/ui_compat.py`.

### core/test_brand.py *(5 sections)*
No "homeos" in the source, license format `COS1`, `brand.py`; **moving package** (since 0.20 with the credentials of the
plugins): export, import, forged and broken packages, minimum length of the password; license and push keys never move along.

### core/test_devices.py *(6 sections, since 0.13.1)*
Checks that every device that can be paired is classified correctly **and operable** – since 0.19 along the whole path
plugin `qivicon` → core → device row (`/api/devices`); the pure model checks are with the plugin:
1. Smart plug (channels `switchState`, `relay`), flush-mounted actuator (`on_off`), roller shutter, dimmable
   lamp, pure electricity meter: right kind, right control channel, power/meter reading (Wh → kWh)
2. Pure detectors (wall button, tamper, smoke) **never** become a switch
3. Switching against the mock Home Base: on/off, shutter up/down/stop/position; nonsense and detectors → 400
4. Consumption: `/api/power` (sum, rows), `/api/power/series`, CSV export (both Pro) – details on reset/today in `test_power.py`
5. The old tile order (`rooms.json`) is read and limited, room names leave it untouched; it is no longer
   written (the route `PUT /api/rooms/order` has not existed since 0.20 – "Anordnen" saves in `layout.home`)
6. Capabilities (since 0.20): every device row names in `caps` what it reports and accepts; switchable = one capability is writable

New device kind or new channel name → add a section or a mock thing here.

### core/test_entitlements.py
Checks the Basic/Pro gate system (`entitlements.py`) with `ENFORCE=True`:
- Section 0 (since 0.26): the source ships `ENFORCE = True`; `boot.isolate` starts the suite in shadow mode
- Route gates: 402 for Basic, 200 for Pro/Owner
- Floor plan policy: room limit and elements (monotonic, no data loss); since 0.25 a further floor needs
  `multi_floorplan`, the rooms of all floors count, existing floors stay editable and deletable, decks are Basic;
  since 0.29 a load ("Verbraucher") as a bubble is Basic, as a plan symbol it counts towards `custom_appearance`
- Flow policy: active flows, Pro nodes paused at runtime; a Pro setting on a Basic node
  (switching threshold on the device state) counts like a Pro node
- History policy: hour limit
- Shadow mode (`ENFORCE=False`): no block, but `shadow_hits` is filled

**Must always be green.**
New: `FEATURES` entry → `dependencies=[require_feature(…)]` → adapt the test list.

### flow/test_flows.py *(22 sections, new in 0.17)*
Checks the flow engine in format 3, the store and the API (test clock instead of real time; `run_for` drives rounds like the engine):
1. Parity: catalogue ↔ engine logic, templates and widgets in the editor, Basic and Pro nodes, `flow_migrate.py` is gone
2. Store: format mark, stable webhook address, target selection as an object; outdated flows stay put and do not run
3. **The hallway case:** the light stays on as long as there is motion (30 min), only goes off after the waiting time and triggers
   again afterwards; "Solange" runs without a time limit (formerly 10 rounds / 20 s); restart in the middle; a failed
   detector switches nothing
4. Exactly once: Hue event + query round = one trigger; an older answer overwrites no event;
   triggers during a running run wait
5. Point in time: time of day, weekdays, sun position ± offset, catching up after a hanging round; period across midnight
6. Presence (begins/ends, condition, setting triggers other flows) · 7. Scene · 8. Device signal, online/offline,
   no change since · 9. Device state (switching threshold, all/at least one, room, group)
10. If/then: all / at least one / exactly one over n+1 ports, not
11. Timer: from the start, extend, ignore, single, cancel; survives restart and moving, not the rebuild
12. Throttle/debounce, toggle, random, fixed count, hold time · 13. Actions (room = lamps only, locked device,
    scene, locking, push) · 14. Variables with a counting window · 15. Errors and "Flow-Fehler"
16. Live messages (run/step/cond/wait/idle/end) and path of the last run · 17. Start, flow on/off, inspector, HTTP block
18–21. API against the mock Home Base: create/check, play, webhook, single trigger (`/fire`), variables, export/import,
    outdated flows (note, no start, delete only)

New: new node → `flow_nodes.json` + engine function + test. Format change → no conversion: flows in the old format
count as outdated (sections 2 and 21).

22. (since 0.21.1) Groups and order: set a group, clean, limit, remove (without a group the file stays byte for byte the same); the copy stays in the group; `PUT /api/flows/order` saves the order

### flow/test_flow_ports.py *(7 sections, since 0.17)*
Catalogue rules from `flow_catalog.py`: the role determines the outputs, n+1 ports (always exactly one free, at most 10),
renumbering without gaps, unfitting lines are dropped (kind, role, settings), counting outputs of random,
Pro setting on a Basic node, properties from device values, AI template. Section 6 keeps the editor together
(`HO.flowSpec` has the same rules; no syntax newer than ES2017 in `flow_canvas.js`, `flow.js`, `settings.js`).

### flow/test_flow_nodes.py *(2 sections, since 0.17)*
Every node of the catalogue in every role – with defaults, sensible values and nonsense: triggers can be started at every
output, conditions return yes/no/unknown, logic and actions get a signal at every input. Nothing
raises; errors are on the flow. If the query of a node fails, the other flows keep running.

### core/test_push.py *(7 sections, since 0.17)*
Push notifications without internet: egress entry `push` (only Apple/Google/Mozilla/Microsoft), subscription only https and only
approved services (no SSRF), keys in the secret store and never in `push.json`, sending (410 removes the subscription,
permanent error after 10 attempts), transport only through `egress.sync_client("push")`, real encryption with `pywebpush`
(only if installed), API only signed in and without secrets, flow node sends push, service worker without a cache.

### core/test_history.py
Checks the history recording (`history.py`):
1. A sensor without a thermostat room is not stored, `_active_rooms` correct
2. `room_series` before/after `record()`: only rooms with a thermostat visible
3. `devices_with_history`: devices without a room filtered out
4. Storage stats and `/api/history/rooms` filtered; `/api/system/info` names the share of ComfyOS (`own`: CPU, RAM, data – since 0.20.1) and the utilisation (`cpu`, never below the share – since 0.27.5)
5. Valve opening (since 0.16): channel recognised (`valvePosition` in %, Homematic `LEVEL` 0…1, dimmer), logged along, averaged per room; without a report no value
6. Heat map span (`routines_store.heat_range`): "Aus"/frost protection does not count, whole degrees, at least 4 °C

### plugins/test_hue.py *(16 sections)*
Checks Home Base and Hue plugin **together** (linking through `link_keys`, lists of the tiles, inbox). Session of the
Home Base, bundled queries and the login pause of Telekom have been checked by the test of the plugin `qivicon` since 0.19:
1–15. Light model (colour spaces, `command_body`, capabilities per lamp, events), Home Base merge,
      motion detectors, import, renaming, scene images; 6c (since 0.23.2): reachability of a linked Hue motion detector –
      the bridge decides, not the Home Base (Home Base OFFLINE + bridge connected = online, and the other way round), live;
      7b (since 0.30): the room of a linked lamp – the Home Base gets the id of the row (its Thing-UID), never
      `source.device_id`, no warning (`MockHB.move_device` accepts known things and records in `hb_moves`)
16. Live reconciliation: motion/window/alarm only on changes

Sections 17–20 of the former version (race query ↔ command, session of the Home Base, Telekom pause) are now with the
plugins: `hue/tests/test_hue_plugin.py` and `qivicon/tests/test_qivicon_plugin.py`.

### core/test_license.py *(11 sections, since 0.13)*
Checks the license client (`license.py`). All keys are created at runtime, the license provider
is a mock:
0. In the code only hash and public key; license tool/secrets neither in the package nor in the repo
1. Fresh installation: Basic, no call to the provider
2. Owner token: Owner without network and expiry; token never in answers or log
3. License file + short form offline; 11 manipulations, garbage, revocation, foreign device rejected
4. Provider key: only key + identifier go out; rejection/outage/redirect; never Owner
5. Offline grace period 14 days, clock trick, changed/copied cache, revocation, removing
6. Several sources: the highest plan applies; removing leaves the owner file in place
7. **Never lock the owner out:** broken files, dead provider, program error in a source, dev mode
8. **Test view:** plan enforced, flows paused, data unchanged, ends by itself
9. License generator: only owner token + deposited key + password
10. Basic: weather effects not unlocked, new accent colour → 402, a set colour stays; `fx` setting stays storable

**Must always be green.** Every change to `license.py` → keep an eye on sections 7 and 8.

### core/test_power.py *(9 sections, since 0.16)*
Checks "consumption since reset" and "today" (`power_store.py`) and the history per device:
1. First reading: the meter reading becomes the base, consumption exactly from the meter
2. A device shows up later: base when first seen, not its lifetime counter
3. Without a meter: power × time (estimated); gaps (ComfyOS off) and offline do not count
4. Change of day: "today" from the last reading before midnight, survives a restart
5. A device resets its meter: no jump, no negative consumption
6. Reset: everything to 0, rises with the meter afterwards
7. Damaged `power.json`: discarded, no exception
8. API: `/api/power` (now, since reset, today, estimated), `POST /api/power/reset` with an event, foreign page rejected
9. `/api/power/series` with several `device_id`: one series per device, the series add up exactly to the sum; 31 days coarser windows

### Security: `tests/security/` *(8 files, by attack surface; since 0.20)*
Up to 0.19.3 these checks were in `test_security.py` (basic protection, sections 1–8) and `test_pentest.py` (attacks
A1–C11). The identifiers stayed – every check point is now with its attack surface. Three attackers:
**A** without login in the home network, **B** a foreign web page in the browser of the owner, **C** with a valid session.
Raw requests go directly to the ASGI app (`support/attack.py`, an HTTP client would smooth out path tricks).

| File | Question | Check points |
|---|---|---|
| `test_access.py` | Who gets in? | 1 every route requires login, `PUBLIC_API` exact · 3 lockout after failed attempts, cookie attributes · 8 session list, logout, expiry · A1 taking over a fresh installation (since 0.20 also: `/docs`, `/redoc`, `/openapi.json` do not exist; list of all pages outside of `/api/`) · A2 bypassing the login (path tricks, methods) · A3 forging sessions · A4 guessing the password · C9 sessions after a password change |
| `test_browser.py` | What can a foreign web page do? | 2 host and origin check · 4 protection headers, no `eval`, no inline scripts · B1 cross-site requests in every wrapping · B2 nothing writing by GET, no CORS release, framing blocked · B3 DNS rebinding |
| `test_files.py` | Does somebody get at foreign files? | C1 static files, backups, logs, images, update paths · C2 manipulated packages (ZIP escape, bomb, wrong settings) |
| `test_secrets.py` | Do credentials leave the home network? | C3 bending Home Base/Hue to a foreign server · C5 obtaining a license by fraud · C6 secrets in answers (every reading route) |
| `test_ssrf.py` | Can ComfyOS be used as a stepping stone? | 6 flow HTTP connects exactly the checked address · 7 webhook: same answer for unknown and wrong tokens, rate limit · A5 webhook token · C4 flow HTTP into the home network (spellings, schemes, DNS tricks, redirects) |
| `test_fuzz.py` | Do all routes withstand broken input? | C7 about 2400 broken requests to **every** route: never 500, no stack trace, no internal path; afterwards all data files are valid JSON and nothing went to an unregistered target |
| `test_inputs.py` | What happens with input that is too large or malicious? | 5 request too large, missing length, invalid values · C8 injected content · C10b tile arrangement only writes checked tile ids · C10c *(since 0.24)* the widget view only writes known widget types, checked ids and limited texts, never a foreign file · C11 device lock, effects, room variants only for what exists |
| `test_privacy.py` | Does something go out that should not? | see below |

Every test collects all findings and prints them at the end (`N FINDINGS:`) instead of aborting at the first.
**Must always be green.** `test_fuzz` covers new routes automatically; for new file, network or secret paths
add a targeted attack in the fitting file. Findings are fixed, not excepted in the test.
Dangerous actions (restart, server setup, update, Home Base login) are cut off.
New public route → adapt `PUBLIC_API` → snapshot in `test_access.py` (only after asking the operator).

### security/test_helpers.py *(10 sections, since 0.21)*
Guard of the invariant "helper programs only through `helpers.py`". A small Python script serves as the helper – the
test does not need Node (Linux/macOS only: the script starts through its shebang line).
1. Path: only files in the plugin or data folder; outside, by `..` and through symlinks rejected
2. Arguments and environment limited; `HOME` and `LANG` are only set by the core
3. Environment in the process: only `HOME`, `LANG` and what is declared; working folder = data folder; no variable of the core arrives
4. JSON lines: objects arrive, unreadable ones are counted and discarded
5. stderr: counted and reported ("Beiprogramm meldet Fehler"), the content is neither in the log nor in the event log
6. A line that is too long and a flood end the helper; restarted under supervision
7. Crash: restart with a pause
8. Stopping and shutdown: process gone, `messages()` ends; stdin closed → the helper ends by itself
9. Through a plugin: without `helper` in the manifest no start, only programs from the own area; switching off ends it
10. Static: in the core only `helpers.py` starts helper programs (without a shell); plugins never start a process themselves

### plugins/test_pairing.py
Checks pairing assistant, reconnecting and deleting against a mock:
- Exact call sequence (method, path, body) from the QIVICON capture (Nov 2026)
- Routes of the plugin: `/api/plugins/qivicon/pairing/…`, `…/inbox/<uid>/approve`, `…/things/<uid>/reconnect|state`;
  the catalogue runs through the egress entry `plugin.qivicon.catalog` (the test sets `egress._transport`)
- 5b: `GET …/pairing/new` reports devices that are new on the Home Base since "Jetzt suchen"
  (also ones included directly without the inbox); the assistant offers name + room for them
- Deleting from the Home Base: device removed from QDEV + `hue_store` cleaned up
- Test world: `support/world_pairing.py`; the interface for it is checked by `ui/ui_pairing.py`

### security/test_privacy.py
Checks privacy and secret hygiene:
1. All external hosts in the code are in the egress register; raw `httpx` clients only home network or register
2. The register client blocks foreign hosts and redirects there; usage is noted
3. Fresh installation: not a single connection to the internet; the entry `plugin.matter.runtime` (nodejs.org) is
   "on demand" · 3b with an address only Open-Meteo and DWD, update search only GitHub, Node.js for Matter only on the
   button "Einrichten" and only with a matching checksum – each only on a trigger
4. Passwords, tokens, JWT, bearer, cookies redacted in logs, export and API
5. CSP `default-src 'self'`; the frontend references no external addresses
6. Secret scan (`tools/check_secrets.py`): the repo must contain no real keys, gateway ids
   or e-mails (assemble test secrets only at runtime)

**Must always be green.** New external connection → adapt `egress.py` + test.

### core/test_scenes.py *(17 sections)*
Checks Hue light control and scenes against mock Home Base + mock bridge (mock lamp 1 can do `effects_v2`):
1. Light model: colour spaces, `command_body`, palette distribution
2. Capabilities per lamp (gradient, effects, CT range, dimmer, plug)
3. Commands: brightness + white tone, gradient, effect, fallback colour → white tone
4. Live event from the Hue app (colour mode)
5. Scene store: templates, creating, checking fields
6. Scene on a room: lamp distribution, Home Base lamp only ON, snapshot stored
7. A second scene keeps the original state; ending restores it
8. Switching the room off ends the scene without restoring
9. Adjusted individually → `excluded`; the scene does not rotate this lamp further
10. Dynamic scene: `scene_step` rotates colours; holds for active lamps
11. Bridge import: palette from a bridge scene, several rooms = one entry, imported scenes without room assignment
12. Flow: start/end a scene (`set_scene`), scene as a condition and as the trigger "ended" (`scene`),
    set and dim light (`set_device`)
13. First assignment (fresh installation, state 1): scenes without a room get all rooms once,
    assignments stay, a second run changes nothing; afterwards empty means "no room"
14. Room variant: the dimmed state comes back on the next call, lamps without an entry by
    palette, other rooms untouched, reset
15. "Als neue Szene": palette from the room without duplicates (max. 9), variant + assignment only there
16. House scene: all assigned rooms with variants, unassigned ones stay
17. Effect per room: scene colour + speed through `effects_v2`, a scene change keeps it running (new
    colour), single lamps, own colour, off, "Raum aus" forgets it

### core/test_device_lock.py *(10 sections, since 0.15)*
Checks the device lock (`device_store.py`, `core.check_lock`) against mock Home Base + mock bridge:
1. Locking through the API: `devices.json`, device list (`locked`), event log, live message
2. User command → 423 with "… ist gesperrt – im Gerätemenü entsperren", nothing sent
3. "Alle Lichter aus" / room: locked lamp skipped, no error
4. Scene in the room + house scene: locked lamp unchanged, ending leaves it alone
5. A dynamic scene does not rotate a lamp locked afterwards
6. An effect does not reach the locked lamp
7. Flow: switching → "… ist gesperrt" in the log, the flow keeps running; several devices, set light,
   all lamps, room, scene skip
8. Only `source="lock_node"` may switch a locked device
8b. (since 0.17) Flow node "Gerät sperren": lock with source `flow:<id>`, also switches an already
   locked device in the process, unlocks again
8c. Plugin route: `context.switch` and the loader reject commands to locked devices
8d. (since 0.21) A plugin switches a foreign device: `context.command` and `context.room_lights` of a plugin with the
   permission "devices" (here: Matter) reach no locked device (`plugin_api.DeviceLocked`); the log names
   the plugin ("durch …")
9. Unlocking; locks of vanished devices are cleaned up during the merge

Commands to plugin devices and `context.switch` are checked by `plugins/test_plugins.py` (section 5).

### core/test_structure.py *(5 sections)*
Checks package structure and route completeness:
1. All routes present in `tests/routes.json` (snapshot)
2. No shadowed routes (placeholder conflicts)
3. All routers included, none imports `main`
4. Release package (`make_release.py`): no tests/docs/captures included; since 0.21 with the bundled
   helper of the Matter plugin (`bridge/dist/bridge.mjs`), never with `node_modules`
5. (since 0.20) No source and no test file falls under `.gitignore` – otherwise it would be missing after the next clone
   (that is how the sample files in `tests/data/` were almost missing, because `data/` is ignored)

**New/removed route → adapt `tests/routes.json`.**

### plugins/test_plugins.py
Checks the plugin system (since 0.18):
1. Manifest: mandatory fields, id = folder, API version and revision (`api_revision`), entry, prefixes, egress, settings, secrets; since 0.21 helper program (`helper`) and access rights (`access`); since 0.23 the id `demo` is reserved and the FRITZ!Box manifest only reaches the home network
2. Built-in plugins load on start; folders with `_` at the beginning are templates
3. Copied `_template`: appears as a tile, only loads after approval with the password, delivers its device
4. Plugin device: switching, settings from the manifest, own route, own name, remove/restore
5. Device lock: commands to plugin devices and `context.switch` from plugin routes are rejected (423)
6. Broken plugins (manifest, `setup`, `start`, `devices`, `command`, wrong prefix, duplicate id): core and others keep running
7. Switching off: no devices, own routes do not answer; switching on brings everything back; the Home Base can be switched off too – ComfyOS keeps running without it
8. Egress: home-network target only into the home network, internet target only to declared hosts and in the register – for HTTP and for TCP (`context.open_stream`, also through DNS names that point to the internet); "port closed" and "no answer" are distinguished; secrets only as declared
8b. A plugin overwrites neither id, name, room, control nor the lock of a device row; live messages only carry state fields; "Alle übernehmen"
8d. (since 0.21) Access with permission (`access`): view of all devices without internals (`DeviceView`), switching through the core with device lock and log "durch …", changes arrive at listening plugins (`on_change`), room light, scenes, home/away; without permission and switched off none of that
8c. Heating sync (`context.heating`, since 0.19): a plugin with `syncs_heating` gets local changes as a list (created, changed, renamed, activated, deleted, home/away) and confirms them; routines, levels, room names of the other side are taken over; without a plugin everything stays local
9. Static files: only from the `static` folder of switched-on plugins, no path tricks
10. SDK copy identical (API revision 7 since 0.23); style rules of the built-in plugins (allowed imports – plus what the manifest names under `requires` –, docstrings, type annotations, telling names); the release package contains the plugins without their tests; the Matter helper is bundled in one file

### Tests of the plugins (`public/plugins/*/tests/test_*.py`)
Every built-in plugin brings tests of its own that run **without ComfyOS** (only `public/sdk` and `public/plugins` on the Python path, `sdk/testing.FakeContext` instead of the real context). `run_all.py` collects them.
- `hue/tests/test_hue_plugin.py`: helpers, light command → CLIP body, reading resources and applying updates (since 0.23 also temperature, ambient light, battery, switches with named buttons and button events), race query ↔ command, plugin against a rebuilt bridge (pairing only in the home network, devices with keys, sensor values and switches, commands, names, live events and presses, bridge gone, disconnecting); since 0.23.2 reachability of sensors and switches by the rule of the lamps (`clip.is_connected`), live and only on a change
- `fritzbox/tests/test_fritzbox_plugin.py` *(2 sections, since 0.23)*: pure functions (PBKDF2 and MD5 answers, session, device list with kinds and units, HAN-FUN and groups left out, commands with limits, presses only from a newer press time, host list, hostile XML refused) · plugin against a rebuilt box (password only into the home network, one login, commands, presses once, expired session, pause after a refused login, presence with waiting time, endpoints of the tile)
- `_template/tests/test_example.py`: the device follows the settings, commands, the state survives a restart
- `mqtt/tests/test_mqtt_plugin.py` *(11 sections, since 0.19; 8–10 new in 0.22.1)*: 1 protocol (length field, every packet there and back, size limit, broken packets, topic filters) · 2 discovery (abbreviations, `~`, device form, removal) · 3 templates (only a path into the JSON, never evaluated) · 4 lamps (JSON and basic schema, colour as xy/rgb/hs) · 5 switch/sensors/thermostat/cover · 6 grouping into devices (second switch = own device, availability, notes) · 7 own devices · 8 fan, lock, select, number, button as labelled capabilities (stable keys, ranges and options checked) · 9 Zigbee2MQTT device list (exposes → devices, settings left out, state request) · 9b (since 0.23) remotes – actions become presses of named buttons (short, long once while held, double), never from retained messages, Home Assistant events, ambient light in lux · 10 Tasmota discovery (relays, light, shutter, sensors, availability, state request; since 0.30 the energy reset on the device: labelled action `energy_reset` → `cmnd/<topic>/EnergyTotal 0`, only on the metering device) · 11 plugin against the broker in memory (`tests/fake_broker.py`: waiting for the complete list, live bundled, commands, outage and reconnecting, packet too large, password, prefix, nothing outside the home network; native Zigbee2MQTT and Tasmota devices, state requests)
- `qivicon/tests/test_qivicon_plugin.py` *(9 sections, since 0.19)*: things → devices (control channels, detectors never switches, meters, valve; since 0.23 humidity and ambient light, read only), mapping to the plugin API (room, capabilities, link keys), login against a rebuilt web interface (one login for parallel calls, a rejected password goes out once), session (a command survives an expired session, network errors, bundled queries, commands and rooms), plugin (home network only), routes of the tile, Telekom pause, Magenta sync (push/confirm/take over), pairing catalogue
- `network/tests/test_network_plugin.py` *(2 sections, since 0.19)*: inputs; open and refused = present, away only after the waiting time, unknown after the start, nothing outside the home network, endpoints, stored targets
- `sonos/tests/` *(since 0.22)*: `test_sonos_plugin.py` (3 sections against a household in memory with a stereo pair, surround speakers and a group: wire format – requests with escaped values, answers, errors, rejected documents (DOCTYPE, size), times, sources, favourites; rooms – one per visible speaker, speakers that belong together and their names (`Room.boxes`), coordinator, storage; plugin – search and address by hand, one device per room and per further box, a station of the newer TuneIn service (402 → old service number), link keys, track and station, cover pictures only from the speakers, commands to coordinator or room, favourites, groups, reported changes, routes, UPnP off, unreachable, restart)
- `matter/tests/` *(since 0.21, all without Node)*: `test_matter_protocol.py` (4 sections: every message of the helper as a typed object; foreign input – wrong types, ranges, lengths, garbage – rejected; messages to the helper, numeric codes; storage: AES-GCM, wrong key, damaged file, atomic, size limit), `test_matter_devices.py` (6 sections: units there and back; every device kind → Matter device; wish of a controller → ComfyOS command; Matter device types → ComfyOS devices; the home as devices; shares with fixed endpoints, advised limit 50, no devices of the own controller), `test_matter_plugin.py` (8 sections: Node.js – fixed version, wrong checksum, only the program leaves the archive, paths to the outside are never written; plugin with `FakeHelper`: without Node, start locked in, shares, commands – locked → `locked`, not shared → `error` –, pairing window and numeric code, storage across a restart, controllers – since 0.22.1 Apple Home's two fabrics as one controller, numbers by position, removing removes both –, garbage from the helper)

`tests/plugins/test_hue.py` checks Home Base and Hue plugin **together** (linking, adopting, switching, live, names, removing, plugin off/on, cleaning up protects adopted lamps).

**Mock Home Base in core tests (`tests/support/homebase.py`, since 0.19):** the Home Base is a plugin. Tests derive their
mock class from `support.homebase.QiviconConnector` (needs no login; `list_devices` may deliver finished
device rows as a dict) and put it into the plugin with `install_homebase(mock)`; `install_homebase(None)` =
no Home Base connected. First `from app import main` (loads the plugins), then import `support.homebase`.

### plugins/test_integrations.py *(12 sections, since 0.19)*
Checks the plugins MQTT and network **through the core** (broker in memory through `egress._stream_factory`):
1. Fresh: both switched on, but no connection as long as nothing is entered
2. Entering the broker: connection through `open_stream`, password only in the secret store, devices found, unsupported things named (since 0.22.1 a vacuum robot – fans are supported now), no entry in the egress register
3. Adopting: one by one with a room, "alle übernehmen"
4. States: live messages, device list, properties for flows
5. Switching through `/api/devices` (lamp, socket, thermostat), limits, device lock, "Alle Lichter aus"
6. Own devices: create, adopt, switch; wildcard topics and templates rejected
7. Broker fails / plugin off / foreign broker address: names, rooms and adopted things are kept
8. Network: presence as a device, unknown instead of away, foreign address rejected
9. Routes of the plugins only with login; own devices and targets are in the settings
10. Real TCP connection over 127.0.0.1 (`egress.open_stream` without the test switch): connect, find devices, switch, receive state; a closed port is reported and counts as an answer for the presence
11. Without a Home Base: devices of the other plugins stay, rooms live in the floor plan (`POST /api/rooms` creates nothing, nothing to catch up), room assignment in ComfyOS, heating routines local; with a Home Base it keeps the rooms again
12. (since 0.19.2) Tile arrangement of the system page per column count (`PUT /api/settings/layout`: duplicates and invalid entries are dropped, wrong column count/page rejected, other saving keeps it, `null` forgets); tab "Plugins" with its own arrangement (since 0.19.3); a switched-off plugin does not count in the connection bubble (`status.items`, since 0.19.3)

### plugins/test_sonos.py *(7 sections, since 0.22)*
Checks the Sonos plugin **in the core** against the test world `world_sonos.py` (mock Home Base with Sonos things, household in
memory through `egress._lan_transport`, SSDP through `egress._datagram_factory`):
1. Without Sonos: list of the Home Base unchanged, the plugin knows the number of its speakers, no search by itself
2. Search only in the home network (foreign sender, other search target, description at another address discarded); one room =
   one device (name and room from the Home Base, state and control direct), surround speakers are devices of their own
   without control that show what their room plays (since 0.22.2; `part_of` points to the row of their room since 0.27.4),
   missing things are orphaned, unknown things are adopted; view for plugins (`DeviceView.media`)
3. Switching: playback, volume, mute, favourite, track change at the right speaker; text in the log,
   live message with playback; nonsense and orphaned devices rejected
4. Device lock: no command to a locked device, not even through grouping from the tile
5. Cover picture through `/api/media/image`: only known identifiers, only from the plugin of the device, only with login
6. UPnP off: devices stay (offline), nothing orphaned, the tile names the reason
7. UDP (plugin API revision 4): only with `"udp": true`, only to and from home-network addresses, size, rate and number of
   sockets limited, only SSDP/mDNS groups; switched off closes everything; search answers read strictly

### plugins/test_matter.py *(7 sections, since 0.21)*
Checks the Matter plugin **in the core** – helper as `FakeHelper`, everything else real (loader, access rights,
device lock, log):
1. Tile routes under attack: without login 401, from a foreign page 403, broken input 4xx
2. Without Node: "nicht eingerichtet"; "Einrichten" only talks to nodejs.org and installs nothing without a matching checksum
3. Shares through the tile; devices of the Home Base reach the helper with Matter values and fixed endpoints
4. Command of a controller along the switching path of the core (log "durch Matter"); state and lock go to the
   bridge; locked → `locked`, not shared → `error`
5. Numeric code only in the answer of the tile route with open pairing – never in log, settings, overviews; the
   overview lists controllers with `index`, `number` and `name` (since 0.22.1)
6. Device of the controller: "Neu", adopt, switch through the device route, never back through the bridge
7. Switching off: nothing listens any more

### plugins/test_matter_node.py *(8 sections, since 0.21)*
The whole way with the **real `matter.js`**: two runs of the plugin each start a helper through `helpers.py`
(locked in with `node --permission`), the controller of one pairs the bridge of the other – pair, read,
switch (locked device rejected), subscribe, restart with the stored pairing, remove; plus the probe
that a program under `--permission` reads nothing, writes nothing and starts nothing in the data folder. Needs
**Node from version 24** (`node` in the path, `public/plugins/matter/runtime/` or `COMFYOS_TEST_NODE=<path>`); otherwise
the test skips itself (return value 0). The Matter ports of the test are chosen at random (20000–40000).
Since 19.11.2026 the development Pi has Node 24.22.3 in the path – the same version as `NODE_VERSION` in
`public/plugins/matter/runtime.py`, installed with `sudo n 24.22.3`. When `NODE_VERSION` changes, update it there the
same way.

### plugins/test_mqtt_broker.py *(5 sections, since 0.19)*
Checks the MQTT plugin against a **real broker** (Mosquitto on 127.0.0.1:1883) and foreign remote ends:
devices announce themselves with `mosquitto_pub`, commands are read along with `mosquitto_sub`. Without a broker or without the
two programs the test skips itself (return value 0). Setting up: `sudo apt install mosquitto mosquitto-clients`
(only listens locally by default). All topics are under a random prefix and are deleted.
1. Connect, find retained discovery messages, sign of life `comfyos/status = online`
2. Adopting: state from retained messages
3. Switching: text command to a socket, JSON with brightness to a lamp
4. State, brightness, temperature and "offline" from the device
5. Unregistered device; after switching off `comfyos/status = offline`

### core/test_weather.py
Checks weather query and geocoding:
1. Place from the address: postcode/town recognised, a bare street without a town → no weather
2. Weather: temperature correct, the request only contains rounded coordinates (no PII)
3. Cache: 15 minutes for the weather, permanent for the place
4. Outage/unknown place: no crash, `available=False` with a reason
5. Endpoint `/api/weather`: default on, can be switched off in the floor plan (then no request)
6. Weather effects (since 0.16): WMO code + readings → `fx` (rain, snow, fog, cloud cover, thunderstorm with rain, storm with rain), all intensities 0..1; since 0.17.1 hail, sleet, frost, heat; since 0.19.1 gusts (`gust`, never smaller than the mean wind); since 0.20.1 `wind_kmh` (measured wind for the wind arrow, not the value raised in a storm)
7. Reading weather warnings (since 0.17.1, `parse_warnings`): only valid messages (not expired, cancelled or a test), without duplicates, running ones first by level, window hint only for wind/rain/thunderstorm/hail/snow, broken data without an error, texts limited
8. Loading weather warnings (`alerts`): only rounded coordinates go to the DWD (latitude before longitude), 10 minutes cache, on failure known things stay until they expire, place abroad without a query
9. Endpoint: `/api/weather` delivers `warnings`; can be switched off in the floor plan (then no connection to the DWD); `/api/system/egress` shows the state
10. Wind (since 0.18): Beaufort, compass direction, wind report; hourly forecast: 24 hours from now across midnight, broken data without an error
11. Wind report and hourly forecast belong to the weather effects (Pro); the weather itself stays for everybody

The DWD is a mock in all tests (`weather._warn_client`), like Open-Meteo (`weather._client`). Whoever writes a test with an address in the floor plan replaces both – otherwise the test would go to the internet.

---

## Playwright UI tests (`--ui-only`, under Windows)

Only run on explicit request (slow, need Chromium + WebKit) and only under Windows – never on the Pi.
They start the app against the mock backends (on a free port per run, `support/ui.py`) and check the interface by
browser. Screenshots land in `tests/shots/`. The tests are in `tests/ui/`, one per page; up to 0.19.3 they were called
`ui_weather`, `ui_lights`, `ui_hue` and `ui_integrations` (table "Old → new").

| Test | What is checked |
|---|---|
| `ui_home` | Frame of the floor plan from tile edge to tile edge, plan centred (since 0.19.1). Weather warning (DWD mock): note above the main tile, details, hiding with the warning sign; layout of the home page (since 0.19.2: header line without a tile, main tile with the floor plan and below it home/away, "Licht & Szenen" and key figures separate, overall state in "Geräte online", below the floor plan only the weather warning – in one line with the weather –, connection bubble at the top, title "ComfyOS", on the phone floor plan tools only as symbols); "Wetter heute" with wind report and hourly forecast; full screen with bubbles of equal height. Weather effects on the floor plan: all 14 previews by day and night without errors, no weather inside the rooms (since 0.27 light and sound are inside: by day every weather lets sky light in at the windows, a patch of sunlight on a sunny day and of moonlight in a clear preview night, moonlight exactly in the previews with an open sky – since 0.27.5 also through the gaps in `drizzle` and `storm`; the rooms stay untouched when neither light nor a playing speaker is there), always something drawn outside, wind lines exactly from 10 km/h and a small flow grid with the building as an obstacle (since 0.19.1), since 0.20.1 wind arrow on the floor plan, leaves exactly from 15 km/h, puddles and drop rings exactly in rain (no ring without a drop), since 0.26.1 hoarfrost patches exactly with frost, sun rays with the shadow shaft of the house on a clear day and none at night and the scroll position stays when a live message rewrites the room tiles (WebKit otherwise jumped up), at night a soft halo instead of the cast shadow, `prefers-reduced-motion` without animation frames, switching off removes the canvas, the button "Wetter" switches and saves; desktop + iPhone/WebKit. Screenshots per preview as a section of the floor plan (`weather-desktop-<kind>-<day|night>.png`) Since 0.27.1: heat haze (`stats.haze`) exactly in the preview `heat` by day, not with reduced motion. Since 0.28: the light of a lamp that is on counts as light inside (`stats.lamps`); the button "Effekte" switches lamp light and sound waves and remembers it per browser (`ho.fpEffects`), without weather the canvas runs only for them. Since 0.29: a load ("Verbraucher") on a device that is on is drawn as on and shines with "Effekte". Since 0.30.1 the test plan has two windows (north and south, no contacts): the checks of the window light need them – written in 0.27 against a plan without windows, they could never pass before. |
| `ui_widgets` | *(since 0.24)* Widget view of the home page against the test world `world_hue.py`: the page starts classic, the switch leads to the widget view and the device remembers it across a reload; "Mit Vorschlag starten" fills the grid without overlaps, nothing sticks out over the screen, the floor plan runs as a widget; the catalogue shows four groups and every widget type; a note is added, its settings (text, style, colour accent) apply at once and its text is not output as HTML; desktop: a widget is dragged three cells and widened with Shift+arrow, the sidebar hides and comes back (the grid takes the room, the device remembers it); phone: narrow grid, the phone arrangement is only written down for what was placed there, buttons of the editor without labels; everything is saved on the server; the widget "Licht" switches through the handlers of the home page; the effects switch in the frame of the floor plan widget switches `ho.fpEffects` in both directions (since 0.30); back to classic the floor plan is there again; desktop + iPhone/Chromium + iPhone/WebKit. Screenshots `widgets-*.png` |
| `ui_scenes` | Room cards (headers of equal height, always open, no sort handle any more; since 0.19.3 arranging with the arrow, stored in `layout.home`), scene chips, lamp rows, scene manager (since 0.19.2: studio only after "Neu", closes after save/cancel, import through the menu in the header), page title "ComfyOS" on the home page, floor plan; desktop + iPhone |
| `ui_flow` | Flow list, template, palette (nodes, Pro tag) drag/tap, roles on the cards, role change, n+1 ports, connecting (dragging and tapping), multi-selection with copy/paste/undo/redo, negative number with "±", node dialog (target selection, property, test buttons), the time display on the node does not change the card height, live display after ▶, lighting up in the list, on/off, play, delete; plus two older fixes (heating history average, docking dots) |
| `ui_system` | Hue pairing in the plugin tile (state, switch, content from the plugin), lamp UI, scene chips, colour picker; tabs "Plugins" and "Einstellungen" (since 0.19.3: each only its tiles, none wider than the screen, the connection bubble leads into the tab Plugins; since 0.22.1: the refresh button keeps the tab Plugins and the tab buttons keep working afterwards); tab Einstellungen in columns (since 0.19.2: desktop two, phone one, flush at the top, about equally long, no tile with a distance to the top), arranging with the arrow and – on the desktop – with the mouse including scrolling along, saving per column count, column choice 1/2, "Automatisch" forgets the arrangement, column choice hidden on the phone; stored passwords as dots; desktop + iPhone Since 0.29.2: the demo home is opened from the tab "Einstellungen" and left again with "Demo beenden" (since 0.30.1 the test waits for the reloaded page's own state `HO.demo` before it navigates on – asking the server answered before the reload, and the next navigation ran into it). |
| `ui_plugins` | Tiles of the plugins MQTT and network (since 0.19): entering the broker → "Online" (up to 0.23 "Live"), found devices (room suggestion preselected), "Alle übernehmen" or one by one with a room, "Nicht unterstützt", creating an own device (the name is not output as HTML, a started input stays), adding a presence device (foreign address rejected), "Jetzt prüfen"; the connection bubble at the top shows "Online"/"Fehler" (or "Verbindet" while a plugin is still coming up, since 0.24) and names MQTT in the hint; home page: socket with power, sensor with temperature and humidity, presence, live change without reloading, switching arrives at the broker; desktop + iPhone/WebKit. Screenshots `plug-*.png` |
| `ui_media` | *(since 0.22)* Sonos tile and block "Wiedergabe" against the test world `world_sonos.py`: without speakers the tile offers the search and names the speakers of the Home Base and "nur im Heimnetz, auch per UDP" (no search without pushing the button); after the search rooms of the household, adopting "Neu bei Sonos"; home page with one tile per speaker (name from the Home Base, track not as HTML, cover picture from the own server); playback, volume and favourite arrive at the right speaker; the device dialog shows the playback instead of on/off; on the phone nothing sticks out over the edge; desktop + iPhone/WebKit. Screenshots `media-*.png` |
| `ui_matter` | *(since 0.21)* Tile of the plugin Matter, without Node (helper as `FakeHelper`, "Einrichten" loads a stand-in archive from the cut-off network): without Node the tile names helper, rights and the download (target, version, size); after "Einrichten" it becomes "Online"; share per device with the counter "x freigegeben", search, "Alle"/"Keine", overview at the top (since 0.21.1); "Kopplung öffnen" shows the numeric code and the QR code as SVG, then the paired controller; device of the controller under "Neu über Matter" (the name is not output as HTML); on the phone nothing sticks out over the edge; desktop + iPhone/WebKit. Screenshots `matter-*.png` |
| `ui_pairing` | Tile of the plugin Home Base: add a device (the assistant is loaded on demand), reconnect, delete from the Home Base – in the list and in the device menu (actions of the plugin); desktop + iPhone |
| `ui_camera` | *(since 0.20, 7 sections)* Floor plan camera in view and full screen: + and −, Ctrl+mouse wheel zooms around the pointer (the mouse wheel alone scrolls the page), dragging pans, "Ansicht zurücksetzen"; **panning does not repaint** (the `viewBox` stays put during the gesture; since 0.30.3: afterwards the effect canvas carries no transform either); tapping in the enlarged plan still opens the device; full screen with mouse wheel, dragging, "Ganzen Grundriss zeigen"; two-finger zoom (touch through the DevTools protocol, therefore Chromium only); no rebuild when idle despite fresh data; toolbar on the phone one line and scrollable sideways |
| `ui_compat` | *(since 0.20)* Old browsers: every page once normally and once as if the browser knew no `gap` in flex containers (`?compat=nogap`, then `compat.css` takes effect). Compared is the position of all visible elements relative to their parent element; more than 16 px deviation is an error, smaller ones are counted (known cases in the header of the test). Plus the diagnosis page `#diag`. `--list` shows every deviation individually |
| `ui_visual` | *(since 0.20)* Image comparison: every page on the desktop (Chromium) and on the iPhone (WebKit) against reference pictures in `tests/shots/ref/` (never in the repo; if they are missing, the test skips itself). `--save-ref` creates them from the current state; for the comparison with an older version see "Tools". Fixed clock, fixed weather, no weather effects, animations paused; what depends on the point in time or on the machine (times of the events, system load, focus in the dialog) is fixed in the picture; more than 0.1 % deviating pixels is an error, the picture for it is in `tests/shots/diff/`. **Create reference pictures before a rebuild of the interface, compare afterwards** |

**Reproducing a single browser:** `UI_ONLY=safari python tests/ui/ui_system.py` only drives one of the three runs (`desktop`, `mobil`, `safari`) – that applies to every test that gets its runs through `support/ui.runs`. The double-tap test in `ui_system` taps as one action (`dblclick`) and measures the reaction in the page (protection: 700 ms).

**Note on room cards:** room cards of the home page are always open (no folding any more). `ui_scenes` still carries a function `expand_rooms()` from older states; it does no harm, new tests do not need it.

**Known flaky tests (timing of the test, no error of the app):** `ui_plugins` occasionally fails in a full run because a message of the test broker does not arrive in time; `ui_home` rarely at the weather button not switching off (race between toggling and refreshing). Repeat individually – if the error stays, it is real.

Starting individually (Git Bash, in the project folder):
```bash
PYTHONIOENCODING=utf-8 ~/.venvs/homeos-test/Scripts/python.exe tests/run_all.py --only ui_pairing
PYTHONIOENCODING=utf-8 ~/.venvs/homeos-test/Scripts/python.exe tests/run_all.py --only ui_system,ui_scenes
```

---

## Order of a release

1. Finish the changes
2. Set `VERSION`
3. Update `README.md` (new features, strike what is outdated), a line in the release table of `Roadmap.md`
4. Update `dev/README.md` and the area documentation in `dev/docs/` if tests, scripts or rules changed
5. If a `gap` was changed in `app.css`: `python dev/make_compat_css.py`
6. Pi: `bash dev/test.sh` → all backend tests green
7. For UI changes, at the same time under Windows: `… tests/run_all.py --ui-only` → 0 console errors, check the screenshots;
   `ui_visual` shows what changed in the look (intended changes: `--save-ref` afterwards)
8. Copy the commit commands from the chat message and run them

---

## Common problems

**`routes.json` mismatch** → new or removed route without a snapshot update.
Remedy: the message of `core/test_structure.py` names what is missing and what is new – adapt `tests/routes.json` by hand (sorted, `"METHOD /path"`).

**The secret scan reports a hit** → test secret directly in the code. Assemble secrets only
at runtime: `"secret_" + "part2"` instead of `"secret_part2"`.

**`test_compat` reports that `compat.css` does not match `app.css`** → run `python dev/make_compat_css.py` (after a change to `app.css`).

**`test_stores` reports different bytes** → a store writes its file differently than before. If that was intended, regenerate the sample files (`--write`) and name the format change in `Roadmap.md`; otherwise it is a bug – existing installations have the old form.

**`test_style` reports a function without a description** → add a docstring (Python) or a comment directly above the function (JavaScript).

**CRLF line endings** (`test_style` section 5) → a file written with Python under Windows. Write bytes: `path.write_bytes(text.encode("utf-8"))`. `.gitattributes` keeps all text files at LF.

**`test_plugins` reports that the helper must be bundled in one file / `test_matter_node` fails after a
change to `bridge.mjs`** → `bash dev/build_matter.sh` (Pi) and check in the new `bridge/dist/bridge.mjs`.

**`test_matter_node` is skipped** → Node from version 24 is missing. Either install it on the Pi
(`sudo n <NODE_VERSION from runtime.py>`) or `COMFYOS_TEST_NODE=<path to node> bash dev/test.sh --only test_matter_node`.

**Playwright browser missing** (Windows) → `~/.venvs/homeos-test/Scripts/python -m playwright install chromium webkit`
