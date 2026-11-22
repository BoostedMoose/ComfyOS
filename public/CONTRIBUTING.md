# Writing plugins for ComfyOS

A plugin connects devices or a hub to ComfyOS. It is a folder with a manifest and some Python – without a build step, without access to the inside of ComfyOS. This document describes how a plugin is built and **what its code should look like**. The style is binding: all plugins should read the same, so that everybody (a language model too) understands a plugin quickly, can review it and extend it in the same style.

- Template to copy: [`plugins/_template/`](plugins/_template/)
- Complete example (HTTP): [`plugins/hue/`](plugins/hue/)
- Complete example (TCP, own protocol): [`plugins/mqtt/`](plugins/mqtt/)
- Small example with a tile of its own: [`plugins/network/`](plugins/network/)
- Interface: [`sdk/plugin_api.py`](sdk/plugin_api.py) (the only file of ComfyOS a plugin imports)
- Test helpers: [`sdk/testing.py`](sdk/testing.py)

## In five minutes

1. Copy `plugins/_template/` to `<data>/plugins/<your_id>/` (for an installation from the repo: `data/plugins/`).
2. In `manifest.json` set `id` to the folder name and choose your own `id_prefixes`.
3. In ComfyOS tap **Neu suchen** under *System → Plugins*. The plugin appears as a tile.
4. **Approve and switch on** – ComfyOS asks for the password, because a plugin is program code with all rights of ComfyOS.
5. Replace the simulated lamp by real calls.

## Structure of a plugin

```
my_plugin/
  manifest.json        profile: id, version, network targets, settings
  __init__.py          setup(context) and the plugin class – the overview of what the plugin does
  client.py            (optional) connection to the device or hub
  translate.py         (optional) pure conversions between device and ComfyOS, without network
  routes.py            (optional) endpoints of its own for the tile
  static/settings.js   (optional) own content of the tile
  tests/test_*.py      tests that run without ComfyOS
  README.md            what the plugin can do and what it needs
```

Small plugins stay one file (`__init__.py`). Larger ones split by task as above – the Hue plugin does it that way (`bridge.py`, `clip.py`, `routes.py`).

### manifest.json (schema 1)

```json
{
  "id": "my_plugin",
  "name": "Mein Plugin",
  "description": "Ein Satz, was es anbindet.",
  "version": "1.0.0",
  "api": 1,
  "entry": "__init__:setup",
  "id_prefixes": ["my:"],
  "egress": [
    {"key": "hub", "desc": "Hub im Heimnetz", "scope": "lan"},
    {"key": "cloud", "desc": "Hersteller-Cloud", "hosts": ["api.example.com"], "data": "Gerätezustand", "trigger": "bei jedem Abgleich"}
  ],
  "settings": [
    {"key": "host", "widget": "text", "label": "Adresse des Hubs"},
    {"key": "token", "widget": "password", "label": "Zugangstoken"}
  ],
  "secrets": ["token"],
  "static": "static/",
  "requires": []
}
```

| Field | Meaning |
|---|---|
| `id` | lower-case letters, digits, `_`; equal to the folder name |
| `api` | version of the plugin API the plugin is built for (today `1`) |
| `api_revision` | optional: smallest revision of the API the plugin needs (today up to `8`). Whoever uses `context.open_stream` writes `2`, whoever needs a helper program or access to all devices `3`, whoever uses UDP in the home network, actions or playback (`MediaState`) `4`, whoever reports labelled capabilities (`Capability.label`) `5`, live audio `6`, buttons or ambient light (`Device.buttons`, `button_pressed`, `STATE_ILLUMINANCE`) `7`, a further part of another device (`Device.part_of`) `8` – an older ComfyOS then rejects the plugin with a clear note |
| `entry` | `module:function`, usually `__init__:setup` |
| `id_prefixes` | every device id of the plugin starts with one of these prefixes; ComfyOS discards devices without a matching prefix. Only for hubs that carry devices of other systems under their own ids: `["*"]` (any id; the exact prefix of another plugin takes precedence) |
| `egress` | everything the plugin talks to. `"scope": "lan"` = only addresses in the home network; otherwise fixed `hosts`. Without an entry the plugin cannot send anything. Targets on the internet are visible to the user under *System → Externe Verbindungen*. Only on a home-network target (revision 4): `"udp": true` allows UDP in the home network (`open_datagram`, `discover`); the tile names it |
| `settings` | fields of the tile: `text`, `number` (`min`, `max`, `step`), `toggle`, `select` (`options`: `[[value, text], …]`), `password` |
| `secrets` | keys the plugin may put into the secret store; every `password` field is listed here |
| `static` | subfolder with files for the interface (empty = none) |
| `requires` | additional Python packages; if they are missing, the plugin does not start (ComfyOS installs nothing) |
| `helper` | optional (revision 3): `{"desc": "…"}` – the plugin starts a helper program (see "Helper programs"). Tile and approval prompt show the description; without the field nothing starts |
| `access` | optional (revision 3): access to what ComfyOS controls as a whole – `"devices"` (see and switch all devices), `"rooms"` (light of whole rooms), `"scenes"` (house scenes), `"home"` (home/away). Tile and approval prompt name the rights in plain words; without permission the calls raise `PluginError` |

Texts in the manifest (`name`, `description`, `desc`, `label`) are shown to the user and are German for now.

### The plugin class

```python
from plugin_api import Device, Plugin, PluginContext


class MyPlugin(Plugin):
    async def start(self) -> None: ...                 # connect
    async def stop(self) -> None: ...                  # clean up
    async def devices(self) -> list[Device]: ...       # all known devices
    async def command(self, device_id, state, source): ...   # switch
    def status(self) -> dict: ...                      # for the tile


def setup(context: PluginContext) -> Plugin:
    return MyPlugin(context)
```

What else there is (all optional, described in `plugin_api.py`): `routes()`, `rename()`/`clean_name()`, `scenes()`/`scene_image()`, `link_prefixes()`, `hints()`, `max_device_age` (set by ComfyOS before every `devices()`: how old a state from the cache may be) and the two roles for hubs further down.

From the `context` the plugin gets: `settings`, `secrets`, `http(egress_key)`, `open_stream(egress_key, host, port)` (TCP, from revision 2), `data_dir`, `log`, `event(text)`, `device_changed(id, values)`, `devices_changed()`, `device_gone(id)` (device finally deleted at the source), `is_local_host(host)`, `hints(kind)`, `switch(id, state)`, `heating` (heating routines, only for syncing plugins), from revision 7 `button_pressed(id, button, press)`. From revision 4, only with `"udp": true` on the home-network target: `open_datagram(egress_key, port, multicast=…)` (UDP socket in the home network) and `discover(egress_key, "ssdp", search_target)` (searching for devices). From revision 3, each only with an entry in the manifest: `start_helper(program, arguments)` (helper program) and the access to everything ComfyOS controls – `all_devices()`, `on_change(callback)`, `command(id, state)`, `room_lights(room, on, brightness)`, `scenes()`, `house_scene(id, active)`, `home_state()`, `home(state)`.

### Devices

A `Device` needs `id`, `name`, `kind`. Everything else only if the device really reports it – ComfyOS invents no values.

- **State** is in `state` under fixed names (`on`, `brightness`, `temperature`, `target`, `open`, `motion`, `position`, `alarm`, `humidity`, `present`). Fields ComfyOS keeps itself (id, name, room, kind, control, lock) cannot be set that way. ComfyOS passes further simple values through (e.g. a status at the hub) – the plugin finds them in its lists under `extra`.
- **`command()` returns what the device has afterwards** (e.g. `{"on": true}` or `{"target": 21.5}`) – ComfyOS shows it at once. A device without a switchable capability (`Capability(..., writable=True)`) never gets a command.
- **Reachability (`online`):** report it for every device whose source knows it – `True`/`False`, and with `False` a `status_detail` that says why in German. That holds for battery devices too (sensors, remotes) when the hub keeps a status for them; one rule per plugin for all its kinds (example: `clip.is_connected` in `plugins/hue`). `None` only if the source really cannot tell – then a hub that relays the same device decides. Report a change at once with `context.device_changed(id, {"online": …})`, only when it really changes. With `link_keys` the direct plugin's `online` wins over the hub's.
- **For the device list under System:** `type_name`, `low_battery`, `last_seen`, `problems` (messages for the user), `readings` (all readable values for "Alle Werte").
- **Kinds** (`kind`): `light`, `switch`, `thermostat`, `sensor`, `contact`, `motion`, `smoke`, `cover`, `media`, `presence`, `unknown`.
- **Report the device list only when it is complete.** If the list arrives in pieces (as with MQTT after connecting), `devices()` raises `DeviceUnreachable` until then. To ComfyOS half a list would look as if the other devices no longer existed – names and rooms would be lost.
- **Capabilities** (`capabilities`) say what is readable and what is switchable. Flows offer triggers and conditions from them.
- **Live audio** (from revision 6): a speaker that can fetch an audio stream over HTTP may play the sound of the
  user's computer. Declare `Capability(STATE_LIVE_AUDIO, "bool", readable=False, writable=True)`; on
  `{"live_audio": True}` ask `context.live_audio_url(<address of your device>)` and tell the device to play that
  address (an endless WAV, 16 bit, stereo), on `False` stop it. ComfyOS records and serves the sound; the plugin opens
  nothing.
- **Labelled capabilities** (from revision 5): a capability with a `label` is a function ComfyOS has no fixed name for – a lock, an operating mode, a settable number, a button. ComfyOS shows it in the device sheet under "Weitere Funktionen" with a control that fits its `type` (`bool`, `number` with `min`/`max`/`step`, `enum` with `options`, `action`), keeps its value in `state` under the key of the capability and sends changes through `command()` like every other value. Flows, scenes and voice assistants do not see labelled capabilities. The label is shown to the user and is German. Example: `plugins/mqtt/translate.py`.
- **Dimmable lamps** carry a `LightState` (white tone, colour, gradient, effects); sockets and plain switches none.
- **Speakers** (kind `media`, from revision 4) report `playing`, `volume` (0–100) and `muted` and carry a `MediaState` (track, artist, source, group, favourites). The plugin names a cover picture only as an identifier (`image_id`) and delivers it through `media_image(identifier)` – the browser never loads from the source. Whoever can do `playing` appears by itself in the speaker tile and in the flow node "Wiedergabe".
- **Buttons** (from revision 7): a wall switch or remote is a device of the kind `button` with its buttons in `Device.buttons` – `[Button("1", "An"), Button("2", "Aus")]` (id: letters, digits, `_ . : -`, at most 40 characters; the name is shown and German). Any other device can carry buttons too. Report a press **once, when it happens**: `context.button_pressed(device_id, "1", PRESS_SHORT)` – `PRESS_LONG` for a held button (once per hold, not per repeat), `PRESS_DOUBLE` for a double press; report only what the device really tells apart. A press is no state: never put it into `Device.state`, never report it again on the next query, never from a message the device sent before the plugin started. Flows react with the trigger "Taster". Examples: `plugins/hue` (event stream), `plugins/mqtt` (actions), `plugins/fritzbox` (press times of a polled device list).
- **Ambient light** (from revision 7): `STATE_ILLUMINANCE` in lux, with `Capability(STATE_ILLUMINANCE, "number", unit="lx")`. Convert raw sensor values to lux in the plugin – flows compare lux.
- **Actions** (from revision 4) are capabilities without a state: `Capability("next", "action", readable=False, writable=True)`. They are triggered like every value – `command(id, {"next": True}, source)` – and are never in `Device.state`.
- **The same device at two plugins:** a common entry in `link_keys`. If a hub only relays a foreign device, it sets `relayed=True` – ComfyOS then shows one device: name and room from the hub, state and switching from the direct plugin.
- **Found devices** (`discovered=True`) only appear once the user adopts them in the tile.

## Rules

1. **Imports:** only the standard library, `plugin_api`, `fastapi`, `pydantic`, `httpx`, modules of the own package and packages the manifest names under `requires`. Never another plugin, never the core.
2. **Network:** only through `context.http("<key>")` (HTTP), `context.open_stream("<key>", host, port)` (TCP) and – in the home network, with `"udp": true` – `context.open_datagram(…)`/`context.discover(…)` (UDP) with a target declared in the manifest. No `httpx.AsyncClient` of its own, no sockets, no library that opens connections itself (`requests`, `paho`, `aiomqtt` …) – a protocol that is not HTTP is spoken by the plugin itself over the stream (`plugins/mqtt/protocol.py` does it that way).
3. **Credentials:** only in `context.secrets`. Never in settings, logs, events or error messages. Before a key goes to an address entered by the user: check `context.is_local_host(...)`.
4. **Switching:** only in `command()`. ComfyOS checks the device lock beforehand. Own routes switch through `context.switch(...)`, never directly at the hub. A plugin only switches devices of other plugins with the permission `"devices"` through `context.command(...)` – device lock (`DeviceLocked`) and log apply there as everywhere.
5. **Errors:** understandable and without internals – `PluginError("…")` if the device cannot do something, `DeviceUnreachable("…")` if device or hub do not answer. Texts for the user in German.
6. **Block nothing:** all network is `async`; `devices()` answers from a short cache if it is asked often.
7. **Own routes** live under `/api/plugins/<id>/…` and require a login automatically. Check input through Pydantic models with limits.
8. **Interface** (`static/settings.js`): output foreign text only through `HO.esc`, no `eval`, no inline handlers, load nothing from outside. At most ES2017 (no `?.`, no `??`) – ComfyOS runs from Safari 13 on. Never set distances between elements as `style="…gap…"` (Safari 13 does not know `gap` in flex containers) but through the classes of the interface: `row` (line) with `g2`, `g6` or `g8` for the distance, `wrap` for wrapping. The tile registers with `HO.plugins.register('<id>', { render(body, tools), deviceActions(device) })`. `deviceActions` is optional and delivers actions for a device of the plugin – `[{ key, label, icon, danger, run(device, done) }]`; ComfyOS shows them in the device menu and in the lists (`tools.ui.actionButtons`). In the interface a plugin is always called "Plugin".
9. **No process of its own:** never `subprocess`, `os.system` or similar. If a plugin needs another program, then as a helper program through `context.start_helper(...)` (next section).

## Helper programs

A helper program is a process of its own that a plugin runs next to ComfyOS – for protocols that cannot be implemented sensibly in Python. Example: `plugins/matter/` starts `matter.js` under Node.js.

**When a helper program is an option** (all three points must hold):

1. In Python there is no maintained way – neither a library without connections of its own nor a protocol that can be spoken over `context.http`/`context.open_stream`.
2. The foreign implementation is clearly more mature than a home-made one would ever be (for Matter: several thousand lines of cryptography).
3. It can be locked in: no file access, no processes of its own, fixed versions, start without downloading.

**What ComfyOS lays down** (`context.start_helper(program, args, *, env=None)`):

- Only with `"helper": {"desc": "…"}` in the manifest; one helper program per plugin.
- The program lives in the folder of the plugin or in `context.data_dir` (symlinks are resolved). Start without a shell.
- The environment is empty except for `HOME` (= `data_dir`), `LANG` and what the plugin passes in `env`. The working folder is `data_dir`.
- Exchange only as JSON lines over stdin/stdout – at most 1 MiB per line and 1000 lines per second, otherwise the helper is terminated. What it writes to stderr is counted and discarded.
- ComfyOS restarts an ended helper with a growing pause. It should therefore report with a first message after every start; the plugin answers with everything it needs.
- **The helper ends itself as soon as its stdin closes** – that way nothing is left behind when ComfyOS ends.

**What the plugin makes of it:**

- Everything the helper sends is foreign input: check every field for type, range and length (model: `plugins/matter/protocol.py`). Never write free text of the helper into the log.
- Lock the helper in as far as its runtime allows (Node: `--permission`) and give it no files: what it wants to keep, the plugin stores (model: `plugins/matter/storage.py`).
- Enforce rights in the plugin, not in the helper: what is not shared, the helper never learns.
- A foreign runtime (Node, …) is only downloaded by an explicit button in the tile, from a target declared in the manifest, in a fixed version with a verified checksum (model: `plugins/matter/runtime.py`).
- Tests run without the helper: `FakeContext(..., helper=True)` delivers a `FakeHelper` with `feed()` and `sent`.

## Style – what plugin code looks like

The yardstick is: somebody who does not know the plugin reads a file from top to bottom and then knows what it does.

**Language.** Identifiers, comments, docstrings and documentation in English. Texts the user sees (error messages, events, labels) in German – the interface is German for now.

**File header.** Every module starts with a docstring: one sentence what the module is, then the most important things to know (protocol, peculiarities). The docstring of `__init__.py` names "What it does" and – with several files – "Files".

**Names.** Written out and telling: `light`, `device_id`, `bridge`, `brightness`, `resource_id` – not `l`, `did`, `br`, `b`, `rid`. One-letter names only for coordinates (`x`, `y`, `z`) and counters (`n`). Booleans are named like a statement (`is_plug`, `is_dimmable`, `reachable`). Constants in `UPPER_CASE` with the unit in the name or comment (`LIGHT_CACHE_SECONDS = 2.0`).

**Types.** Every function has type annotations for all parameters and the return value. Data that travels through the plugin are `@dataclass` objects with named fields, not nested dictionaries. Raw data of the device is translated into such objects in exactly one place.

**Docstrings.** Every public class and function has a docstring: one sentence in the imperative, what it does; if needed, what it returns and when it raises which exception. Overridden methods of the base class need none of their own if they do nothing special.

**Comments** explain the *why*, not the *what*: a peculiarity of the device, a limit, a case that would otherwise surprise.

```python
# good
light.reachable = connection_status in (None, "connected")   # a light without a zigbee resource counts as reachable

# superfluous
light.on = True   # set on to True
```

**Structure of a file.** Imports (standard library, then third-party packages, then `plugin_api`, then own modules) → constants → data classes → functions or class. In the plugin class the methods are in this order, separated by section comments `# -- life cycle ---`, `# -- devices ---`, `# -- commands ---`, `# -- live updates ---`, `# -- endpoints ---`.

**Functions.** Short, one task. Early return instead of deep nesting. No nested one-liners, no multiply nested expressions with `if/else` – rather two lines with a named intermediate result:

```python
# good
wants_light_features = any(state.get(key) is not None for key in LIGHT_ONLY_KEYS)
if light.light_state is None and wants_light_features:
    raise PluginError(f"{light.name} lässt sich nur ein- und ausschalten.")

# hard to read
if l.ls is None and any(st.get(k) is not None for k in LK): raise PluginError(...)
```

**Format.** PEP 8, four spaces, lines up to about 120 characters, double quotes, `from __future__ import annotations` in every file.

**Pure logic separated from the network.** Conversions (device data ↔ ComfyOS) are in functions without network access. They can be tested without a device and are the part one most wants to check.

## Tests

Every plugin brings tests that get by without a running ComfyOS:

```python
from testing import FakeContext

context = FakeContext("my_plugin", http_handler=fake_hub, secrets=["token"])
plugin = setup(context)
```

`FakeContext` keeps settings and secrets in memory, remembers what the plugin reports (`changes`, `events`, `requests`, `streams`) and answers HTTP calls from a function. With `helper=True` and `access=(…)` it stands for the manifest entries `helper` and `access`: `context.helper` is then a `FakeHelper` (`feed()` plays in messages of the helper, `sent` records those of the plugin), `context.device_views` are the devices of ComfyOS, `context.commands` the switched ones. For TCP `stream_handler` delivers the remote end; `memory_stream_pair()` builds two connected ends in memory (example: `plugins/mqtt/tests/fake_broker.py`). Template: `plugins/_template/tests/test_example.py`, detailed example: `plugins/hue/tests/test_hue_plugin.py`.

Running: `python plugins/<id>/tests/test_<name>.py` with `sdk/` and `plugins/` on the Python path. In ComfyOS `tests/run_all.py` collects the tests of all built-in plugins. `tests/plugins/test_plugins.py` additionally checks for every built-in plugin: only allowed imports, docstrings (overridden methods inherit the one of the base class in the same file), type annotations, no one-letter names. `tests/core/test_style.py` furthermore demands a description for every function, method and class – in the tests of the plugin too.

## Hubs: rooms and heating routines

Two roles are meant for installations that are more than a device source (example: `plugins/qivicon`, the Home Base):

- **Keeping rooms – `manages_rooms = True`:** the hub has a room list of its own. Its devices name their room as
  `Device.room_id` (and `room_hint` = name). ComfyOS writes rooms and assignments from the floor plan there through
  `rooms()`, `create_room()`, `rename_room()`, `delete_room()`, `move_device()`. Devices of other plugins keep
  their room in ComfyOS. Without such a hub rooms live in the floor plan alone.
- **Syncing heating routines – `syncs_heating = True`:** the routines belong to ComfyOS. The plugin gets them through
  `context.heating`: `changes()` names what has not been transferred yet; after sending it confirms every change with
  `done(key, kind)` or `created(local, remote)`; it returns the state of the other side with `merge(...)` and
  `merge_home(...)`. ComfyOS calls `sync_heating(pull, force)` after every change and asks `heating_status()` for the
  display. A round never raises – errors are in the status. Triggered from a page, a round must never queue behind a
  running one (otherwise every waiting request keeps a connection of the browser busy).

A plugin with targets on the internet says in `status()["egress"]` whether it currently uses them
(`{"<key>": ["on" | "off" | "on_demand", "Text"]}`) – that way the true state is shown under *System → Externe Verbindungen*.

## Versions

- `version` in the manifest is the version of the plugin (semver).
- `api` is the version of the plugin API. It only rises when a change breaks existing plugins. New optional fields and methods do not change it – a plugin for API 1 runs with every ComfyOS that offers API 1.
- Additions are counted by `API_REVISION` (history in the header of `plugin_api.py`). A plugin that needs an addition names the revision as `api_revision` in the manifest.
