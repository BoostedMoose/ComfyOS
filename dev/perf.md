# Measurements

Rule from `CLAUDE.md`: **measure first, then change.** Every optimisation enters before and after here; what
brings no measurable gain is not built. Measuring happens with `dev/measure.py` against the sample home
(`tests/support/world_demo.py`) – no real devices, no internet.

```
python dev/measure.py backend  [path]     # on the Pi
python dev/measure.py frontend [path]     # under Windows (Playwright)
```

The comparison state is a second checkout of the version before (`git worktree add <folder> v0.19.3`) whose path
is passed as the last argument. That way before and after run on the same machine with the same measuring code.

## Backend (Raspberry Pi, Python 3.13)

Both columns measured on 18.11.2026 one after the other on the same Pi (40 calls per route: median and 95th percentile).

| Measurement | 0.19.3 | 0.20.0 | Goal |
|---|---|---|---|
| Response time `GET /api/overview` | 9.1 ms (95 %: 11.1 ms) | 9.5 ms (95 %: 10.2 ms) | not worse |
| Response time `GET /api/devices` | 5.9 ms (95 %: 6.5 ms) | 6.3 ms (95 %: 7.1 ms) | not worse |
| Response time `GET /api/rooms` | 4.5 ms (95 %: 5.1 ms) | 4.6 ms (95 %: 5.3 ms) | not worse |
| Response time `GET /api/power` | 4.6 ms (95 %: 5.4 ms) | 4.7 ms (95 %: 5.5 ms) | not worse |
| Device queries per minute and plugin | 40 | 40 | at most one at a time per plugin |
| CPU time in operation (one page open, one flow, one dynamic scene) | 0.4 % of a core | 0.4 % of a core | not worse |

**Reading:** the backend was never the bottleneck. The deviations are within the scatter of the measurement; `/api/devices`
now carries the list `caps` (capabilities) per device, that costs about 0.4 ms.

**What was therefore not built:** a common, cached device state for all callers. 40
queries per minute cost 0.4 % of a core; a cache would have gained nothing measurable but created a new
source of stale states. Only the bug behind it is fixed: whoever needs it fresher
now passes their `max_age` per query instead of setting it as an attribute on the plugin (where simultaneous
callers overwrote each other); simultaneous callers share one query per plugin.

## Frontend

Measured with `dev/measure.py frontend` in Chromium (Playwright) against the sample home: rain with a fresh wind, one
window open, heat map on – so with everything that makes the interface expensive. Two devices: a desktop (1366 × 900) and
a tablet (1024 × 768, double pixel density) with a processor throttled four times as a stand-in for an old device.
Both columns per device measured on 18.11.2026 one after the other on the same machine.

"CPU time" is the time the main thread of the browser is busy (1000 ms per second = fully loaded, nothing is left for
operation). "Stutters" are frames that come later than after 25 ms (60 frames per second would be 16.7 ms).

### Desktop

| Measurement | 0.19.3 | 0.20.0 | Goal |
|---|---|---|---|
| Panning the floor plan in full screen | 69.8 ms per step · 161 stutters | 4.0 ms per step · 4 stutters | below 20 ms per step |
| Zooming the floor plan in full screen (mouse wheel) | 216.9 ms per step · 251 stutters | 11.0 ms per step · 8 stutters | below 20 ms per step |
| Panning the floor plan on the home page (zoomed in) | 11.8 ms per step · 5 stutters | 2.2 ms per step · 0 stutters | not worse |
| Home page idle with weather effects | 156 ms CPU time per second | 131 ms CPU time per second | not worse |
| 20 live messages in 4 s (a lamp switches) | 796 ms (idle of the same duration: 606 ms) | 717 ms (idle: 518 ms) | not worse |
| Rebuilding the home page (1.5 s afterwards) | 322 ms (idle: 233 ms) | 297 ms (idle: 196 ms) | not worse |

### Tablet with a throttled processor (old device)

| Measurement | 0.19.3 | 0.20.0 | Goal |
|---|---|---|---|
| Panning the floor plan in full screen | 261.4 ms per step · 269 stutters | 8.1 ms per step · 0 stutters | below 20 ms per step |
| Zooming the floor plan in full screen (mouse wheel) | 340.3 ms per step · 123 stutters | 12.6 ms per step · 0 stutters | below 20 ms per step |
| Panning the floor plan on the home page (zoomed in) | 110.2 ms per step · 274 stutters | 8.7 ms per step · 0 stutters | below 20 ms per step |
| Home page idle with weather effects, right after loading | 967 ms per second · 101 stutters in 4 s | 554 ms per second · 22 stutters in 4 s | – |
| … ten seconds later (settled) | 970 ms per second · 100 stutters in 4 s | 332 ms per second · 2 stutters in 4 s | below 500 ms per second |
| 20 live messages in 4 s (a lamp switches) | 3842 ms (idle: 3888 ms) · 73 stutters | 2160 ms (idle: 1279 ms) · 22 stutters | not worse |
| Rebuilding the home page (1.5 s afterwards) | 2002 ms (idle: 1455 ms) | 887 ms (idle: 499 ms) | not worse |

**Reading:** in 0.19.3 a slow device was already fully loaded when idle (970 of 1000 ms) – every operation had to
wait. Now two thirds of the time stay free there, and gestures on the floor plan cost a thirtieth.

### Re-measurement at the end of the step

Measured once more on 18.11.2026 after the last work packages (blocks of the home page, descriptions, documentation) –
the shipped state 0.20.0. All values are at or slightly below those of the tables above; the differences
are scatter of the measurement.

| Measurement | Desktop | Tablet, throttled |
|---|---|---|
| Panning the floor plan in full screen | 3.4 ms per step · 3 stutters | 7.8 ms per step · 0 stutters |
| Zooming the floor plan in full screen (mouse wheel) | 10.0 ms per step · 8 stutters | 11.7 ms per step · 0 stutters |
| Panning the floor plan on the home page (zoomed in) | 2.0 ms per step · 0 stutters | 7.6 ms per step · 0 stutters |
| Home page idle with weather effects | 123 ms per second | 505 ms per second, settled 298 ms |
| 20 live messages in 4 s | 640 ms (idle: 468 ms) | 2023 ms (idle: 1238 ms) |
| Rebuilding the home page (1.5 s afterwards) | 263 ms (idle: 182 ms) | 763 ms (idle: 448 ms) |

Backend on the same day: `GET /api/overview` 9.5 ms, `/api/devices` 6.4 ms, `/api/rooms` 4.6 ms, `/api/power` 4.9 ms,
40 device queries per minute and plugin, 0.4 % of a core – unchanged.

### Where the time went – and what was done about it

Every measure rests on an experiment in which one part was switched off and measured again (panning in full screen on the
desktop, CPU time per step):

| Experiment | per step | Conclusion |
|---|---|---|
| as in 0.19.3 | 69.7 ms | – |
| without the weather canvas | 57.4 ms | the weather picture costs about 10 ms |
| without SVG blur (shadows, device glow) | 11.1 ms | the filters cost about 58 ms – the main share |
| without both | 1.4 ms | the own JavaScript is not the problem |
| new camera, but with the cold-air pulse (SMIL animation) | 30.4 ms | the animation makes everything repaint 60 times per second |
| new camera, pulse paused | 1.5 ms | – |

1. **Gestures without repainting** (`js/floorplan_camera.js`): during panning and zooming the already painted picture is only
   moved by CSS transform; the `viewBox` and with it the repaint follow once at the end.
2. **Own rhythm for the cold-air pulse** (`floorplan.js`, `pulse`): 8 instead of 60 frames per second (low-power devices: 4),
   pause during gestures and with a hidden tab.
3. **Rebuild only on change** (`renderSVG`): when idle the floor plan was rebuilt completely every 1–2 seconds
   (2–3 times in 4 s) although nothing had changed – now 0 times.
4. **Weather effects regulate themselves** (`weather_fx.js`, `STEPS`): if drawing needs more than a quarter of the time
   between two frames or the frames come too late, the view takes back one step – first pixels, then
   frame rate and amount. The throttled tablet is at step 2 after three seconds (20 frames per second, single
   pixel density, 60 % of the particles); a fast device stays at step 0.
5. **Low-power level** (`HO.perf`, `html.perf-low`): no blur behind surfaces, shadows in the floor plan without SVG filters.
   Applies to browsers without flex `gap` (Safari up to 14.0), to devices that reached step 2 of the weather effects, or
   by choice under System → Diagnose.

**Limits of the measurement:** Chromium with a throttled processor is no iPad. The throttling only hits the main thread;
it does not show how fast the graphics card of an old device paints. Safari also computes SVG filters more slowly than
Chromium – the gain on the real device is likely to be larger for the gestures, but remains to be checked on the device
(`Roadmap.md`, section 3; the diagnosis page `#diag` measures the frame rate there).

### 0.28 – a pass over the frame time of the effects

The operator found the floor plan slow with every effect on. The browser can only be measured on Windows
(`dev/measure.py frontend`); this pass was made on the Pi by reading the drawing path frame by frame, and only
changes without a visible difference were made. The measurement now prints what one frame of the effects costs in the
script (`stats.drawMs`, of it the inside layer `stats.indoorMs`) – the next measurement on Windows fills in the numbers
(before: `git worktree add` with `v0.27.5`, after: this state).

| Change | Why it costs less |
|---|---|
| sound waves alone at `SOUND_FPS` 20 instead of the fast pace (30) | a playing speaker drove the whole weather image at 30 frames/s; the rings are slow – a third fewer frames while only they move |
| colour tokens read only when the root element changed | `update()` ran `getComputedStyle` (a forced style computation) with every live message and every redraw of the plan |
| all door thresholds in one clip | the light of every window (and lamp) was laid in once per door |
| middle of the building once per outline | before from all corners of all rooms in every frame |
| halo of a lamp as a ready image | six wide strokes per lamp and frame while rain or wind animate |
| light of a lamp needs no frame | static lamp light is a still image; frames only while a change eases or a light effect runs (calm pace) |

## Helper program of the Matter plugin (0.21)

Measured on 19.11.2026 on the development Pi (Raspberry Pi 5, 8 GB) with Node 24.22.3 and the bundled
`bridge/dist/bridge.mjs` (5.6 MiB, `matter.js` 0.18.0), locked in with `--permission`. Measured was the process itself
(`/proc/<pid>/status`, `/proc/<pid>/stat`) – without ComfyOS, with 30 shared lamps, bridge and controller online,
no controller paired. `dev/measure.py` has no run of its own for this yet.

| | Value |
|---|---|
| Memory after loading `matter.js` | 204 MiB |
| Memory with 30 endpoints, bridge and controller online | 221 MiB |
| Start until "online" | 2.8 s (3.8 s CPU time) |
| Idle | 0.10 s CPU time in 60 s = 0.17 % of a core |
| 200 value changes on endpoints | 0.09 s CPU time |

**Assessment:** the CPU time is negligible. The memory is not – about 220 MiB are noticeable on a Pi
with 1 GB. It arises almost entirely when loading the bundle (204 of 221 MiB), not through devices. Not yet
tried: a smaller V8 heap (`--max-old-space-size`), `--jitless`, a bundle without the unused clusters. Measure
first, then change – and only when it bothers on a small Pi.

**Still open:** measurement with a paired Echo (subscriptions, sessions) and the additional load in the core through the running
live reconciliation: as long as the Matter plugin listens (`context.on_change`), `core.live_loop` reconciles the device state every
3 s – as before only with an open page.
