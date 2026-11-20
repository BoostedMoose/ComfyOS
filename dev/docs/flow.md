# ComfyOS Flow and push

Area documentation of ComfyOS – part of the guide `CLAUDE.md` (which says when to read this file).
This file says how the area is built and what to watch out for when changing it; version notes ("since 0.x") name
when something was added.

## Since 0.23

- **Node "Taster" (`button`, Basic):** trigger with the fields device (target with `"buttons": true` – only devices that carry
  buttons), button (widget `button`: the buttons of the chosen devices, empty = any) and press (short, long, double, any); condition
  role "per Taster gestartet" (`_origin`, like play and webhook). A press is an **event**: `Engine.push_button` (called by
  `core._plugin_button_pressed`) fires every matching trigger once, at once – nothing is kept or caught up after a restart. The event
  names button and press ("Taster: An kurz"), `{device}` and `{value}` (the button's name) work as placeholders. Without a device
  state the engine fetches one first (`snapshot`).
- **Property "Umgebungslicht" (`illuminance`, lx, 0–100 000):** for "Gerätestatus" as trigger and condition – e.g. motion and darker
  than 20 lx.
- **Demo home:** demo flows are stored in `data/demo/flows.json` and shown, the engine never loads them (`dev/docs/backend.md`).

## Since 0.22

- **Node "Wiedergabe" (`playback`, Basic):** play, pause, toggle, set the volume, start a favourite (name as in the
  speaker tile), next/previous track – for devices of the kind `media`
  (`flow_nodes._playback`, through `apply_device`, so with the device lock). The catalogue thus has 32 nodes
  (14 Basic, 18 Pro); the format stays 3, existing flows are untouched.
- **Properties:** `playing` ("Wiedergabe"), `muted` ("Stumm"), `volume` ("Lautstärke") for "Gerätestatus".
  `playing` arrives at once (`core.FLOW_PUSH_KEYS`), the others with the next round.

## Since 0.20

- **`flow_nodes.py` (split off from `flow_engine.py`):** all nodes (`@node(type, 'poll' | 'signal' | 'state')`) live there,
  `flow_engine.py` keeps runtime, helpers, `Flow`/`Tick`/`Ctx` and `Engine`. The nodes read `fe.rand()`,
  `fe.in_window()`, `fe.http_client()`, `fe.check_url()` through the module – tests replace `flow_engine.rand`, `clock`,
  `_sleep`, `http_client`, `in_window`. `Engine.problems` is only computed on first access (the nodes are loaded
  by then).
- **New node:** specification in `static/flow_nodes.json` + function in `flow_nodes.py` (otherwise
  `tests/flow/` complains).

## Structure and rules

- **ComfyOS Flow (automations, format 3 since 0.17):** the engine runs in ComfyOS (`flow_engine.py`, task from `core.startup` in the `lifespan`), not on the Home Base. Store `data/flows.json` (`flows_store.py`: flows with `format`, variables `vars` = yes/no, number or text, runtime `state`). Catalogue `static/flow_nodes.json` + `flow_catalog.py` = **one source** for engine, store, import, entitlements and editor (`role`, `ports_for`, `normalize`, `is_pro`, `capabilities`, `summary`). Actions use the same helpers as the API (`apply_device`/`apply_lights`/`apply_home`/`apply_routine`/`apply_scene`/`stop_scene` in `core.py`), log with "(Flow)". The webhook `/api/flows/hook/<token>` is the only flow endpoint without login (token per node, `hmac.compare_digest`, only in the trigger role; stays stable on save/import, is never in the export). The HTTP action blocks home-network/localhost targets without `allow_local`. Frontend: `js/flow.js` (list `#flow`, editor `#flowEdit` chromeless, field widgets, templates, SSE) + `js/flow_canvas.js` (`HO.flowSpec` = the same catalogue rules as `flow_catalog.py`, camera, grid, port snapping, multi-selection, copy/paste, undo, live display).
- **Flow model (do not soften it again):**
  - **Roles:** nodes with `roles` are, depending on `config.role`, a **trigger** (signal outputs, no inputs) or a **condition** (one yes/no output `val`). One node = one idea, the same fields in both roles.
  - **State triggers** (`presence`, `device_state`, `variable`, logic `hold`) have `out` = "begins" and `end` = "ends". `_edge` remembers per node in `flows.json` (`state["<flow>/<node>"]["on"]`) whether the state holds, and only reports the change – **computed from the current state, never from comparing two queries**. That is why no change is lost and none comes twice, even if a Hue event (`push_device`) arrives in the middle of a round; after a restart a due "ends" still comes. The first look (new, rebuilt, switched on) only remembers. `None` = unknown never changes anything (device reports nothing, Home Base gone). `hold_on`/`hold_off`: the new state has to hold that long without interruption.
  - **Per-device triggers** (`device_signal`, `reachable`): `_device_edges`, memory per device, only fire on occurrence (`{device}` names the device).
  - **Event triggers** (`time`, `interval`, `scene`, `play`, `webhook`, `flow_error`, `startup`): `time` evaluates `tick.due` (minutes begun since the last round, at most `CATCH_UP_MINUTES` back – that way no point in time is dropped when a round hangs). As a condition `play`/`webhook`/`flow_error` check whether the run was started that way (`event["origin"]`).
  - **Signals are pushed** (per run every node/input at most once, `MAX_STEPS`), **yes/no values are pulled** (`ctx.bools`). Conditions hang on growing inputs `c1…cn` (`grow`: always exactly one free, at most 10, numbered without gaps) of `when` ("Wenn / Dann": all / at least one / exactly one; outputs `yes`/`no`), `loop` and `hold`; `not` inverts a condition. A single yes/no input takes exactly one line.
  - **Nothing blocks:** timer, debounce, random delay and "Solange" never wait within a run but put down a **wait marker** (`Engine.wait` → `state["_waits"]`, a task of its own). Wait markers survive restarts (`restore_waits`, overdue ones run at once). "Solange" (`loop`) therefore has **no time or round limit**: every round is a short run of its own (`enter=True`, port `@next`), minimum distance `LOOP_MIN_GAP`, hourly note (`LOOP_WATCHDOG`), without a condition an error; a new signal during the loop is ignored. **Never again build a loop with a time limit that then continues at "danach"** – exactly that made the hallway light go out despite motion in real operation (`test_flows.py` section 3).
  - A flow never runs in parallel with itself (lock per flow); triggers in the meantime wait (up to `QUEUE_MAX`, above that discarded with a log entry). A rebuilt, switched-off or paused flow loses wait markers and trigger memory (`_stop_flow`, `TRANSIENT`); moving a node does not change `Flow.sig` and cancels nothing.
  - **Device state:** `snapshot()` per round (~3 s), Hue at once through `push_device`. Events that came during a query are newer than the answer and are laid on top (`pushed`/`seq`). If the query fails, the old state stays. Actions from flows only wait briefly for slow remote ends (`core._push_wait`).
  - **Targets:** field widget `target` = `{devices, groups, rooms, all}`, `ctx.targets` resolves it. With "Gerät steuern" rooms and "Alle Lampen" only switch lamps (through `apply_lights`); sockets are chosen individually or through a device group.
  - **Properties** (`properties` in the catalogue): a device has a property if it reports a value for it (`flow_catalog.capabilities`). New device kinds need no new node, at most an entry there. Labelled capabilities of a plugin (`Capability.label`) are not properties – flows do not see them.
- **Flow visible (since 0.17):** every step goes out as SSE `{"type": "flow", flow, ev}`: `run` (start; `cont` = continuation after a wait marker), `step` (line + node), `cond` (condition with `val`), `wait`/`idle` (wait marker), `state` (state holds), `end` (error, trace, info). `Engine.live(fid)` = what is waiting or holding right now, `last_runs[fid]` = path of the last run (`GET /api/flows/{id}` → `live`, `last`). Editor: `canvas.live(msg)` plays the steps one after the other, `replay(last)` shows the last run; waiting time, round and "holds right now" hang as a badge on the card (`.fl-live`, positioned absolutely – **the card never changes its size when it is shown or hidden**, `ui_flow` checks that); list: `.fl-item.fired`. Test of a trigger: `POST /api/flows/{id}/fire/{node}` (also with the flow switched off, optionally `port`), play button `…/play/{node}`, all triggers `…/run`.
- **Outdated flows instead of migration (since 0.17):** `flows_store.outdated(f)` = a `format` other than `flow_catalog.FORMAT` or an unknown node. Such flows stay stored, never run (`Engine.outdated`), the API delivers them without a graph with `outdated: true` and rejects everything except switching off and deleting with 409. **Nothing is ever converted** – the same holds for every future format change in the project.
- **New flow node = specification + engine function, otherwise `test_flows.py`/`test_flow_nodes.py` fail:** (a) entry in `static/flow_nodes.json` (ports `signal|bool`; `roles` for trigger/condition; `grow`/`count`; fields only with existing widgets, `when` also with `role` and `@ptype`, `advanced`, `pro` – the editor renders everything from it; texts there are German, they are the interface), (b) `@node("<type>", "state"|"poll"|"signal"|"push")` in `flow_nodes.py` that only uses `ctx` and never raises on a missing or nonsensical setting (queries return `None` or `[]`; actions report the lack as `FlowError`). New field kind = widget in `WIDGETS` (flow.js) + name in `widgets` of the JSON. The API rejects unknown nodes on save (400).
- **Push (since 0.17, `push.py`, `api/push.py`, `static/sw.js`):** flow node "Benachrichtigung" → `core.flow_push` → `push.send_all` (in the background, never raises). `pywebpush` does encryption and VAPID; sending runs through `egress.sync_client("push")` (`push._Session`), and the push address of a subscription is already checked against the egress entry when subscribing (no SSRF through a subscription). VAPID keys in the secret store (`push:vapid`), subscriptions in `data/push.json` (**not** in the backup – they belong to the key), 404/410 or `MAX_FAILS` remove a subscription. If the package is missing: `available() = False`, never an aborted start. Service worker `/sw.js` without a fetch handler (the app is never served from a cache). UI: System → Benachrichtigungen (per device; needs HTTPS, iOS only as an installed PWA). Tests: `tests/core/test_push.py`.
