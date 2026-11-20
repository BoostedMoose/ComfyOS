# License, Basic and Pro

Area documentation of ComfyOS – part of the guide `CLAUDE.md` (which says when to read this file).
This file says how the area is built and what to watch out for when changing it; version notes ("since 0.x") name
when something was added.

## Since 0.26

- **The gates are armed:** `entitlements.ENFORCE = True`. An installation without a licence runs as Basic: Pro routes answer
  402, flows with Pro nodes and active flows beyond the limit pause, the history is capped. Data stays untouched (all gates
  are monotonic), Owner and Pro keep everything. The note about the free period in the licence card is removed.
- **Test suite:** `boot.isolate` starts every test in shadow mode (`ENFORCE = False`), because a test world has no licence;
  tests of the gates arm them themselves. `core/test_entitlements` section 0 checks that the source ships `ENFORCE = True`.
  The screenshot and measuring tools run through the test worlds and therefore still show everything.
- **Not yet there:** a way to buy (`BUY_URL` is empty, no licence provider). Until then licences come from the owner's
  generator.

## Since 0.24.5

- **Backups are Basic:** `backup_restore` is removed from `FEATURES` and from the plan Pro; the routes `/api/backups…` and
  `/api/logs/export` carry no gate any more (the migration package never had one). Reason: nobody should lose their data
  because of the plan. Additive for every plan – Pro and Owner keep everything. `core/test_entitlements` section 2 checks that
  backups and the log export answer in Basic.

## Since 0.24

- **Widget view of the home page:** the view itself and one page are Basic (every widget type too). Pro is what was Pro before,
  now inside a widget – and further pages:

  | What | Feature |
  |---|---|
  | more than one page | `advanced_dashboard` (now `available`, part of the plan Pro – additive) |
  | widget "Wetter": hourly forecast and wind | `floorplan_weather` |
  | widget "Stromverbrauch": history as a curve | `energy_analytics` |
  | widget "Grundriss": heat map / weather effects | `floorplan_heatmap` / `floorplan_weather` |

  Enforced when saving (`entitlements.check_widgets`, table `WIDGET_FEATURES`), monotonic: pages and widgets that exist stay
  editable and deletable, only more Pro usage is refused; nothing is removed, also not by the interface. `core/test_widgets`
  section 4 holds this; a Pro setting of the interface that the server does not know fails section 5.

## Since 0.20

- No change in substance in 0.20. `entitlements.node_feature` (unused) is removed; the license files are read through
  `jsonfile` (unreadable files are deliberately not kept there, `keep=False`).

## Structure and rules

- **License system (`license.py`, since 0.13):** three sources, one result: the effective plan. Every source is checked on its own, the highest valid plan applies; a broken source never drags the others down.
  - **Source A – owner token (no network, no expiry):** `APP_DIR/license.json` with `{"type":"owner","token":"…"}`. The code only contains the SHA-256 hash of the token (`license_keys.OWNER_TOKEN_HASHES`), comparison by `hmac.compare_digest`. **No route and no other source writes or deletes this file after redeeming** – removing only works by hand on the device.
  - **Source B – signed license (no network):** `APP_DIR/gift.comfyos-license`, Ed25519 against `license_keys.SIGN_PUBS` (only the public key in the code). Formats: file (JSON) or short form `COS1.<content>.<signature>`. Fields `v, id, plan, iid|null, name, issued, exp|null, sig`; revocation through `license_keys.REVOKED`.
  - **Source C – license provider:** `COMFYOS_LICENSE_URL/activate|validate|deactivate` (empty = no service, no target in the egress register). Only `license_key` and `instance_id` are sent (`hw_id()`: 16 hex from the SHA-256 of the Pi serial number). Key in the secret store (`license:key`), state in `APP_DIR/license_cache.json` with a MAC (key + device), 14 days offline grace period (setting the clock back does not help). A provider can **never grant Owner** (`EXTERNAL_PLANS`). Contract: see the docstring of `license.py`.
  - **No secrets in the code.** `tools/build_secrets.env` (gitignored, only local + password manager): `OWNER_TOKEN`, `OWNER_SIGN_KEY`. `tools/mint_gift.py --init` writes `license_keys.py` from it (only hash + public key). `mint_gift.py`, `*.env` and `*.comfyos-license` never get into the release package (`updater._RELEASE_SKIP_*`, `_SECRET_SUFFIXES`).
  - **Test view (owner only):** `license.set_preview(plan, minutes)` → `APP_DIR/license_preview.json`. Sets `entitlements.use_plan(plan, enforce=True)`: the plan is enforced on this installation although `ENFORCE` is off. It only restricts, expires by itself, `clear_preview()` is tied to no condition. Data stays untouched because all gates are monotonic – **that is the reason why gates must never change data.**
  - **License generator (only an installation with the owner token, not a gifted owner license):** signing key in the secret store (`license:sign_key`), `license.issue()` + register `APP_DIR/license_issued.json`; routes require the ComfyOS password.
  - **`COMFYOS_DEV=1`** only switches off the online check (source C never calls into the network). It grants no rights – there is no switch that unlocks without a license.
  - **Never lock the owner out – checklist for every change to `license.py`:** source A needs neither network nor clock; exceptions in a source are caught in `status()`; `core.startup` catches everything ("Basic, never an aborted start"); `check_loop` catches everything; `test_license.py` section 7 must stay green.
  - Set the plan only through `license.apply()` (calls `flow_engine.engine.reload()` on change); `license.sync()`/`tick()` only intervene when the license state changed (tests that set `entitlements.use_plan` directly stay untouched).
  - Tests: `tests/core/test_license.py` (three sources, offline grace period, tampering, test view, generator, owner promise).
- **ComfyOS Pro conversion:** step plan and release state in `Roadmap.md`. One release per step (current plan there). The documentation (`dev/docs/`, `dev/README.md`) is updated with every step.
- **Feature matrix (sources of truth: `entitlements.py` + `flow_nodes.json`):** for every change to plans/features/limits: `entitlements.py` (`PLANS`, `FEATURES`, `LIMITS`) → `test_entitlements.py` → this matrix.

  **Plans:** Basic (default, no license) · Pro (paying) · Owner (own Pi/friends, `license.json`).

  **Pro features** (Basic = `–`, Pro/Owner = `✓`):

  | ID | Feature | Status |
  |---|---|---|
  | `ai_import` | AI import of heating plans | available |
  | `floorplan_elements` | windows & radiators in the floor plan | available |
  | `floorplan_heatmap` | heat map | available |
  | `floorplan_weather` | weather effects around the floor plan | available |
  | `custom_appearance` | plan symbols for devices in the floor plan (scalable) | available |
  | `flow_pro_nodes` | Pro nodes in Flow | available |
  | `multi_floorplan` | further floors in the floor plan (since 0.25; the first floor and decks are Basic) | available |
  | `advanced_dashboard` | several pages in the widget view of the home page | available |
  | `energy_analytics` | energy analysis (history graph, CSV export) | available |
  | `custom_icons` | own device symbols | planned |

  **Limits** (Basic → Pro → Owner):

  | ID | Basic | Pro | Owner |
  |---|---|---|---|
  | `floorplan_rooms` | 6 | ∞ | ∞ |
  | `flows_active` | 5 | ∞ | ∞ |
  | `history_hours` | 168 h | 744 h | ∞ |

  **Pro nodes** (`"pro": true` in `flow_nodes.json`, the engine pauses them automatically): `webhook` · `flow_error` · `interval` · `startup` · `reachable` · `stale` · `variable` · `random` · `rate` · `toggle` · `loop` · `hold` · `http` · `set_flow` · `set_var` · `inspect` · `lock` · `notify`. **Pro setting** on a Basic node (`"pro": true` on the field): switching threshold (`band`) on `device_state` – when used, the node counts like a Pro node (`flow_catalog.is_pro`).

  `floorplan_weather` has also covered the wind report and the hourly forecast since 0.18.

  **Deliberately Basic** (free as in the Hue app): backup & restore, migration package and log export (since 0.24.5), lamp control (colour, white tone, gradient, effects incl. room effects), scenes incl. scene manager, room variants, house scene, dynamic scenes and Hue import, the manual device lock, the playback node (`playback`) and the 13 Basic nodes of Flow from before 0.22: `presence` · `play` · `scene` · `time` · `device_signal` · `device_state` · `when` · `not` · `set_presence` · `set_scene` · `set_device` · `set_routine` · `timer`.

  New Pro feature: `FEATURES` entry + `dependencies=[entitlements.require_feature("<id>")]` on the route + adapt `test_entitlements.py` section 2.
- **Basic/Pro (`entitlements.py`, since 0.11 in shadow mode):** plans `PLANS` (Basic/Pro/Owner), features `FEATURES`, limits `LIMITS` are only there. Armed since 0.26 (`ENFORCE = True`). Shadow mode (`ENFORCE = False`) only remains for the test suite: gates then only remember what Basic would block (`shadow_hits`, `/api/entitlements`). Rules:
  - New Pro route ⇒ `dependencies=[entitlements.require_feature("<id>")]` + entry in `FEATURES` + expected list in `test_entitlements.py` (section 2).
  - Mixed endpoints through content policies (`check_floorplan`, `check_flow`, `check_enable`, `cap`), always **monotonic**: only reject what increases Pro usage; existing things stay editable and deletable, never remove anything automatically.
  - New Pro flow node ⇒ only `"pro": true` in `flow_nodes.json`, on the node or on a single field (gate, engine pause and test take effect automatically). The engine asks `paused_flows` at runtime – a hand-edited `flows.json` achieves nothing.
  - No env switch, no dev bypass in the code (`COMFYOS_DEV=1` only switches off the online license check, see license system). Tests switch `ENFORCE` on and choose the plan with `entitlements.use_plan(...)`.
  - Gates ask `entitlements.enforcing()` (= `ENFORCE` or the test view of the owner), never `ENFORCE` directly.
  - Frontend only presentation: `HO.can(id)`, `HO.proTag(id)` (only visible with `enforce`), 402 `pro_required` opens `HO.upsell`. `HO.api` errors carry `err.status`/`err.detail`. After every license change `HO.refreshLicense()` (reloads plan + license, shows the test view bar `#previewBar`). **The frontend never removes content because a plan does not cover it** – otherwise the test view would destroy data on save.
