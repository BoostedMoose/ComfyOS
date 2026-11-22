# Plugins and devices

Area documentation of ComfyOS – part of the guide `CLAUDE.md` (which says when to read this file).
This file says how the area is built and what to watch out for when changing it; version notes ("since 0.x") name
when something was added.

## Since 0.24

- **Widgets find devices by capability:** the widget view of the home page (`dev/docs/interface.md`) knows no plugin. A widget
  lists what a device row can do (`caps`, `kind`) – a new plugin shows up in "Wiedergabe", "Licht", "Messwert" … without a change
  to any widget. Plugins do not bring widgets of their own (decision of 19.11.2026; a later revision of the plugin API may add it).
- **Connection pill "Verbindet":** a configured plugin that is not connected yet counts as `connecting` for `core.CONNECT_GRACE`
  (45 s) after its start (`plugins.Record.started_at`: start of ComfyOS, switched on, settings saved); once it has been connected,
  a lost connection is `bad` at once. Nothing for a plugin to do – `status()["connected"]` stays the only source.
- **Matter plugin 1.0.1 – removed means gone at once:** `remove_controller` drops the fabrics of the controller from its own list
  right away and holds them back for `REMOVE_HOLD_SECONDS` against an older report of the helper (before, the tile redrawn after
  the click still showed the controller until the helper's next `fabrics` message – only a reload helped). `unpair` forgets the
  node as soon as the helper confirmed (`_on_node_removed`), not only with its later `node_removed` message.

## Since 0.23.2

- **Hue plugin 1.1.2 – reachability by one rule:** motion sensors and switches reported `online=None`, so for a linked device
  the Home Base's status counted (`device_merge._overlay` only takes a direct `online` that is not None). Seen on 19.11.2026: the
  Home Base reported a Hue motion detector offline for about one round (~10 s) some 95 times an hour while the bridge reached it
  all along (Zigbee `connected`, `reachable` in the v1 API) – the event log filled with "ist offline"/"ist wieder online". Now
  lights, plugs, motion sensors and switches take reachability from their `zigbee_connectivity` resource through
  `clip.is_connected` (only `connected` counts; no resource = reachable). A `zigbee_connectivity` event updates light, sensor or
  switch (`HueBridge._apply_connectivity`); for a sensor or switch it yields `clip.HueReachabilityChange` only when the
  reachability really changes, and the plugin reports `{"online": …}` alone (a motion event does not repeat it).
- **Audit of all plugins (0.23.2):** every direct device reports `online` from its source – FRITZ!Box `present`, Matter node and
  endpoint, MQTT `availability`, Netzwerk the probe, Sonos the answer of the speaker, Home Base the thing status, Hue the Zigbee
  status. `online=None` is left nowhere. The rule is in `public/CONTRIBUTING.md` ("Reachability").

## Since 0.23.1

- **Hue plugin 1.1.1 – load on the bridge:** with 1.1.0 every light list asked the bridge for nine resources at once, and
  while Matter keeps `core.live_loop` running that happened every three seconds – the bridge answered HTTP 429 ("ausgelastet")
  again and again, its lamps flickered offline/online and the connection pill said "Fehler" (seen on 19.11.2026 right after the
  update). Now: at most `MAX_PARALLEL_REQUESTS` (3) requests at once (`HueBridge._request_slots`); a 429 is tried once more after
  `BUSY_RETRY_SECONDS`; while the event stream is live `devices()` serves the cached list for up to `LIVE_CACHE_SECONDS` (60 s,
  `HueBridge.cache_age`) – the events keep it current, a light added or removed or a reconnected stream drops the cache. The
  tile (`routes.read_status`) still asks fresh. Rule for plugins of hubs: never poll a hub for its whole state every few seconds
  when it reports changes itself, and never send it a burst of requests.

## Since 0.27.4

- **Plugin API revision 8:** `Device.part_of` – a further part of another device of the same plugin without controls
  of its own (surround speaker, sub, second box of a stereo pair) names that device by its plugin id. `device_merge`
  keeps it as `(plugin, id)` while building and resolves it at the end through `index` to the id of the row that shows
  the main device (a hub may lend it its id); a main device that is not shown leaves the field out. `part_of` is a
  reserved row field. Used by the floor plan (sound waves, `dev/docs/floorplan.md`). **Sonos plugin 1.2.1** sets it for
  every further box (`api_revision` 8).

## Since 0.23

- **Plugin API revision 7** (additions only, `API_VERSION` stays 1): **buttons and ambient light.**
  - Device kind `button` (wall switch, remote), `plugin_api.Button(id, name)` and `Device.buttons`. Any device can carry
    buttons (a FRITZ!DECT 440 is a sensor with buttons); a button id matches `plugins.BUTTON_ID_RE`.
  - `context.button_pressed(device_id, button, press)` with `PRESS_SHORT`/`PRESS_LONG`/`PRESS_DOUBLE` (`PRESS_KINDS`). A press is
    an event, never a state: the plugin reports it once. An unknown kind or button id is dropped with a warning in the log
    (`plugins._Context.button_pressed`) – a press never breaks a plugin. SDK: `FakeContext.presses` (the fake raises instead).
  - Path in the core: `core._plugin_button_pressed` → live message `{"type": "button", "id", "button", "name", "press"}` to the
    open pages only (`publish(…, to_plugins=False)` – a bridge such as Matter would only read its whole view again), a line in
    the event log (`<device>: <button> kurz gedrückt`) and `flow_engine.engine.push_button` (trigger "Taster", `dev/docs/flow.md`).
    Only devices ComfyOS shows (`core._index`).
  - Rows carry `buttons: [{"id", "name"}]` (`device_merge.buttons_list`, at most `MAX_BUTTONS`; `buttons` is a reserved field).
    The interface shows the last press per device (`HO.notePress`, `HO.buttonSeen`; forgotten after a reload), kind label
    "Taster", icon `button`.
  - State key `illuminance` (`STATE_ILLUMINANCE`, lux): in `core.LIVE_FIELDS`, `core.VIEW_STATE`, the flow property "Umgebungslicht"
    and the status line of sensors and motion detectors (`HO.lux`).
  - Matter shares neither (the bridge knows no button and no light sensor endpoint).
- **Hue plugin 1.1.0:** the bridge resources `temperature`, `light_level` (lux = 10^((value − 1) / 10000)), `device_power` and
  `button` are read with the lights (`clip.attach_sensor_values`, `clip.parse_switches`; all optional – without them lights still
  work). Motion detectors carry temperature, ambient light and battery. Switches (dimmer switch, tap dial, smart button, wall
  switch module) are devices `huebridge:switch:<device id>` of the kind `button`, discovered like the motion detectors, linked by
  their Zigbee address; their buttons are named by model (`clip.BUTTON_NAMES`, otherwise "Taste n"). Events: `short_release` →
  short, `long_press` → long (once per hold), `double_short_release` → double; `initial_press`, `repeat` and `long_release` are no
  press of their own (`clip.BUTTON_EVENTS`). New values of temperature and light arrive live with the motion detector.
- **MQTT plugin 1.2.0:** `translate.EventTranslator` for the platform `event` (no longer silent): the `action` of a
  Zigbee2MQTT device (`zigbee2mqtt._event`, from the native device list) and Home Assistant event entities (`event_types`,
  short key `evt_typ`). `translate.split_action` turns an action into (button, kind): "1_single" → ("1", short), "on_hold" →
  ("on", long), "single" → ("taste", short); actions that end a press ("…_release", "…_stop") are left out. A held button repeats
  its action – within `HOLD_REPEAT_SECONDS` it counts once. **Never from a retained message** (`registry.handle_message(…, retained)`):
  that is an old state the broker keeps. The registry collects presses in `presses`; `__init__._on_message` hands them over with
  `take_presses`. A remote whose Home Assistant description has no event entity keeps the one of the native device list
  (fallback merge in `registry.rebuild`). Ambient light: device class `illuminance` → `illuminance`; from Zigbee2MQTT only
  `illuminance_lux`, or `illuminance` with the unit lx (older versions report a raw value there).
- **Home Base plugin 1.1.0:** humidity (`humidity`, `relativeHumidity`) and ambient light (`illuminance`, `luminance`) are read
  from the channels – read only, no new command. Wall buttons of the Home Base report no presses yet (no capture of a press).
- **FRITZ!Box plugin 1.0.0** – see the next paragraph.

### FRITZ!Box plugin (`public/plugins/fritzbox/`, since 0.23)

Smart home devices of a FRITZ!Box and presence through its home network. Only the home network (`scope: "lan"`, egress `box`).

- **Files:** `__init__.py` (plugin: login, query rhythm, devices, commands, presence), `aha.py` (login, device list, commands –
  pure functions), `tr064.py` (presence – pure functions), `routes.py` + `static/settings.js` (tile).
- **Login (`login_sid.lua?version=2`):** challenge answer with PBKDF2 (`2$…`, FRITZ!OS 7.24 and later) or MD5 over UTF-16LE (older);
  the password never travels. Without a user name the plugin takes the one the box offers (`last="1"`). Only one login at a time
  (`_login_lock`); after a refusal `LOGIN_BACKOFF_SECONDS` (60 s) or the box's own `BlockTime`, whichever is longer – a password is
  never tried in a loop. The address must be in the home network (`context.is_local_host`) before the password goes out. A 403 of
  the interface means an expired session: sign in once more, then retry once.
- **Device list (`getdevicelistinfos`)** every `interval` seconds (3–120, default 10) in a task of the plugin. `functionbitmask`
  decides the kind (`aha._kind`: blind → cover, radiator → thermostat, light/level/colour → light, socket/on-off → switch, alarm →
  contact, buttons → button, temperature/humidity → sensor). Units: `celsius` in tenths, `hkr` in half degrees (253 = off → shown as
  8 °C, 254 = on → 28 °C), power in mW, energy in Wh. A HAN-FUN device entry without functions of its own is left out (its units
  are listed separately); groups are left out. Device ids `fritzbox:<AIN without spaces>`.
- **Presses:** the box only tells `lastpressedtimestamp` per button. A newer time than in the last list is one press
  (`aha.new_presses`, always `PRESS_SHORT`; the DECT 400 lists its long press as a button of its own). A button seen for the first
  time is no press – nothing is reported twice after a start. Delay: at most one query interval.
- **Commands:** `setswitchon/off` (sockets), `setsimpleonoff` (lamps), `setlevelpercentage` (brightness, blind position 0 = open),
  `sethkrtsoll` (half degrees, 8–28 °C), `setcolortemperature` (2700–6500 K), `setunmappedcolor` (hue 0–359, saturation 0–255),
  `setblind open/close`. The plugin keeps the asked values until the next list confirms them (`_assume`).
- **Presence (setting `presence`, off by default):** TR-064 on port 49000 with HTTP digest authentication, service
  `Hosts:1`. The tile loads the host list (`X_AVM-DE_GetHostListPath` – only the known path form is followed) and stores the picked
  devices (`presence_hosts`, at most 20, by MAC). `GetSpecificHostEntry` per picked device at most every `PRESENCE_EVERY_SECONDS`;
  *away* only after `away_after` minutes inactive (as the network plugin), unknown after a start. Ids `fritzbox:host:<mac>`.
- **Security:** XML at most 2 MB, documents with a DOCTYPE refused; names are foreign text (`aha._clip`); the password is a secret of
  the manifest (moving package).
- **Not checked on a real box:** see `Roadmap.md`, section 3.
- **Tests:** `public/plugins/fritzbox/tests/test_fritzbox_plugin.py` (a rebuilt box, without ComfyOS).

## Since 0.22.3

- **Plugin API revision 6** (additions only, `API_VERSION` stays 1): **live audio.** A device that can play the sound
  a browser sends to ComfyOS declares the capability `live_audio` (`STATE_LIVE_AUDIO`, type `bool`, write only). The
  speaker tile then shows the button "Ton dieses Rechners" (`media.js`, only in browsers that can share sound); the
  core sends `{"live_audio": true}` / `false` through the normal switching path. In `command` the plugin asks
  `context.live_audio_url(host)` for the address of the stream as the device at `host` reaches it (`PluginError`
  without a running transmission or for a host outside the home network) and tells its device to fetch it. The
  core knows no plugin here: relay and routes are in `live_audio.py` / `api/audio.py` (`dev/docs/backend.md`).
  `public/sdk/testing.py`: `FakeContext.live_audio_address`.
- **Sonos plugin 1.2.0:** rooms carry `live_audio`. `True` → the leader of the group gets `SetAVTransportURI` with the
  plain `http://…/stream/live.wav` and a DIDL description (`soap.stream_metadata`, `http-get:*:audio/wav:*`), then
  `Play` (15 s); `False` → `Stop`, but only if the group still plays an address the plugin has set (`_live_urls`).
  While it plays, title "Ton vom Browser" and source "Rechner". **Confirmed on a Beam (19.11.2026, software 98.0):**
  it plays the endless WAV without a length over the plain `http://` address.
- **Firefox (0.22.4):** `getDisplayMedia` never carries sound there. `media.js` asks for a recording device instead
  (`getUserMedia`, echo cancelling, noise filter and level control off) and lets the device choose the sample rate –
  Firefox cannot connect a recording to an audio context with another rate. The user picks "Stereomix" or a virtual
  audio cable. Since 0.22.5 the button only needs `getUserMedia` there
  (`liveSupported`), so it also appears over `http://` once Firefox is set to hand out devices to insecure pages.
  **Known limitation (19.11.2026):** the way works technically, but in the operator's everyday use Firefox itself
  gets in the way. Not pursued further; Chrome and Edge are the recommended browsers. The Sonos plugin is
  considered finished.

## Since 0.22.1

- **Plugin API revision 5** (additions only, `API_VERSION` stays 1): **labelled capabilities** – `Capability.label`.
  A capability with a label is a function without a well-known key (a lock, a mode, a settable number, a button).
  The row carries it in `row["caps"]` with `label` (`device_merge._caps`, `plugins.device_view`), its value is in the
  row under the key of the capability. `control.apply_device` sends a command that addresses one through
  `plugins.command` – also for lamps and sockets (`_extra_caps`); the text for the log comes from `_extra_text`.
  The interface shows them in the device sheet under "Weitere Funktionen" (`dev/docs/interface.md`). Flows, scenes and
  the Matter bridge only know the well-known keys and do not see them. The label is user text (German).
- **MQTT plugin 1.1.0** – details in the MQTT paragraph below: Zigbee2MQTT's own device list, Tasmota's own
  discovery, state requests after connecting, fan/lock/select/number/button, packets up to 4 MiB.
- **Matter: controllers instead of fabrics** (`matter/__init__.py`, `Controller`, `group_controllers`): what the tile
  and the routes call a controller is one fabric – or the two fabrics Apple Home always creates (vendor 4937 for the
  home, 4996 for the iCloud keychain) when they follow each other directly. A controller has a `number` (position in the
  list, 1, 2, 3 … without gaps – the fabric index is never reused by Matter and is no number to show) and an `index`
  (lowest fabric index: the stable key in routes and in `controller_names`). The event "ist jetzt gekoppelt" is written
  once per controller, `remove_controller` removes all its fabrics. The overview returns `{"index", "number", "name"}`.
- **Language:** the whole code base is English now (comments, docstrings, tests, tools, documentation). Texts for the
  user stay German: manifest texts, error messages, events, labels.

## Since 0.22

- **Plugin API revision 4** (additions only, `API_VERSION` stays 1): UDP in the home network, actions, playback.
- **UDP in the home network (`egress.open_datagram`, `egress.ssdp_search`):** only for a home-network target with `"udp": true` in the
  manifest (`"scope": "lan"`; with fixed `hosts` it is a manifest error). The tile names it ("auch per UDP",
  `plugins.public` → `egress[].udp`).
  - `context.open_datagram(key, port=0, *, multicast=None)` → `plugin_api.Datagram` (`send(data, (address, port))`,
    `on_datagram(callback)`, `close()`). Sending only goes to **IP addresses** in the home network (never to names – those
    could be bent after the check), at most `egress.MAX_DATAGRAM` bytes and `DATAGRAM_SEND_PER_SECOND` datagrams per
    second and socket (no amplifier in the LAN). What arrives from outside the home network or above the size never
    reaches the plugin. `multicast` only from `egress.MULTICAST_GROUPS` (SSDP, mDNS for IPv4 and IPv6); bound with
    `SO_REUSEADDR`. IPv6 link-local with an interface (`fe80::1%eth0`) counts as home network (`egress.is_local_ip`).
    At most `plugins.MAX_DATAGRAM_SOCKETS` sockets per plugin; they close when the plugin stops.
  - `context.discover(key, "ssdp", search_target, timeout)` → list of `plugin_api.SsdpAnswer`: two search requests from a
    free port, answers until the time is up (0.5–5 s). Discarded is what does not come from the home network, names
    another search target or whose description does not live at the sender (`egress.parse_ssdp`).
  - Test switch `egress._datagram_factory(port, multicast, deliver)` – counterpart of `_stream_factory`.
- **Capability type `"action"`:** trigger without a state (`Capability("next", "action", readable=False, writable=True)`),
  switched like every property: `command(device, {"next": True}, source)`.
- **Playback:** `plugin_api.MediaState` on the `Device` (`media`), states `playing`, `volume`, `muted`. The row carries
  `row["media"]` (`device_merge.media_dict`: foreign text without control characters and shortened, lists limited); from
  `image_id` the address `/api/media/image/<plugin>/<identifier>` is made there – the core fetches the image through
  `Plugin.media_image(identifier)` (only JPEG/PNG/WebP up to 2 MB, identifier `[A-Za-z0-9_.-]{1,120}`), the browser never loads from
  the source. New live fields `playing`, `volume`, `muted`, `media` (`core.LIVE_FIELDS` = `HO.LIVE_FIELDS`); `playing`
  is in `FLOW_PUSH_KEYS`; `core.VIEW_STATE` and `DeviceView.media` pass them to plugins with the permission `devices`.
  Text for the log: `control._media_text`.
- **Parts of a device (`device_merge.merge`):** a direct device may carry several `link_keys` (room of a
  speaker including surround speakers). If a hub lists the parts individually, the device is filed under the relayed
  device with the **first** key; the others merge into it – they are in no list, but count towards
  `known_ids` (nothing is forgotten about them) and are never "orphaned".
- **`public/sdk/testing.py`:** `FakeContext(…, udp=True)`, `FakeDatagram` (`sent`, `feed()`), `ssdp_answers`, `searches`.

### Sonos plugin (`public/plugins/sonos/`, since 0.22)

Speakers locally through the UPnP services on port 1400 (SOAP over `context.http`), without an account and without the cloud. No
library; no event subscription (the speakers never call back, no route without login).

- **Files:** `soap.py` (pure translation: building requests, reading answers, topology, DIDL, favourites – all
  Sonos knowledge about the wire format is here), `player.py` (`Speaker`: one speaker, the only place with network),
  `household.py` (rooms, speakers that belong together, groups, storage), `__init__.py` (`SonosPlugin`), `routes.py`,
  `static/settings.js`.
- **Finding:** only at the push of a button – `POST /api/plugins/sonos/search` (`context.discover`, search target
  `urn:schemas-upnp-org:device:ZonePlayer:1`) or `POST …/connect` with the address of a speaker. One
  speaker names the whole household (`GetZoneGroupState`). Other plugins report what they know
  (`hints("sonos")` → `{uuid, host}`; the Home Base names the RINCON ids of its Sonos things) – the tile then
  offers the search. The found rooms are in the plugin settings (`rooms`, in the backup).
- **Devices:** one per visible room (`sonosroom:<RINCON>`, kind `media`, `discovered`) – it carries the capabilities.
  **Since 0.22.2 (plugin 1.1.0)** every further box of a room (second speaker of a stereo pair, surround speakers, sub;
  from `ChannelMapSet`, `HTSatChanMapSet`, `Satellite`) is a device of its own as well (`sonosroom:<RINCON of the box>`,
  `Room.boxes`): kind `media`, **no capabilities and no state** (the core takes a reported `playing` for a capability,
  `core._complete_caps`), only `media` of its room (without favourites) **while the room plays** – the interface reads
  "plays" from that (`HO.playingNow`); name
  "<room> (hinten links)" from the channels (`household.CHANNEL_LABELS`), numbered if the speaker names none. So a box
  can be placed on the floor plan and stands in its room, but the block "Wiedergabe", flows and Matter (all go by
  capabilities) only know the room; a command to a box is refused. In 0.22.0/0.22.1 the boxes were absorbed into the
  room – things of the Home Base that had been placed on the floor plan vanished from it.
  **The device id deliberately does not start with `sonos:`** –
  that is what the thing UIDs of the Home Base are called (`sonos:<model>:RINCON_…`), and an exact id prefix would have
  assigned them to the Sonos plugin (`plugins.owner_by_id`). Linking happens through `link_keys`: every room and every
  box carries exactly one key `sonos:<own RINCON>`;
  `qivicon/model.link_keys` forms the same key from the thing UID. `link_prefixes()` = `["sonos:"]`, but
  only as long as the household answers – silent speakers prove nothing.
- **Capabilities:** `playing`, `volume`, `muted` (writable), actions `next`/`previous`, choice `favorite` (names of the
  Sonos favourites), plus `MediaState` (track, artist, album, source, cover picture, position, duration, group,
  coordinator, favourites). Radio: station as the track (`GetMediaInfo`), the running programme as the artist.
- **Commands:** playback, track change and favourite go to the **coordinator** of the group, volume and mute to
  the room itself. Favourite: radio stations directly as the source (`SetAVTransportURI`), everything else through the queue
  (clear, enqueue, play from the start). A station of the newer TuneIn service (`x-sonosapi-stream:…?sid=333`) is
  refused by the speaker with UPnP error 402; the plugin then sets the same station under the old service number
  (`soap.legacy_radio_uri`: `sid=254`, `sn=0`, metadata unchanged). "Play" after a favourite may take 15 s
  (`player.START_TIMEOUT_SECONDS`) – for a radio station the speaker answers only once the stream runs. Grouping: `{"join": "<device id>"}` / `{"leave": true}` – the tile
  sends them through `context.switch`, so with device lock and log. UPnP errors of the speaker (701 for
  TV/line-in …) become a text for the user (`player.REFUSED_TEXTS`).
- **Queries instead of events:** `devices()` asks again if the state is older than `Plugin.max_device_age` (with an
  open page every ~3 s), otherwise a rhythm of its own looks every `IDLE_POLL_SECONDS` (15 s). Per round: playback per
  coordinator, volume per room; rooms, groups and favourites at most every `TOPOLOGY_SECONDS` (60 s).
  Changes go out through `context.device_changed` (each only once). If nothing answers, queries only happen in the
  15-second rhythm.
- **UPnP off or unreachable:** the rooms stay as devices (offline, `status_detail` names the
  menu path of the Sonos app), `status()` reports "nicht erreichbar", one entry in the event log per outage.
  `SpeakerSilent` = answers, but not over UPnP; `DeviceUnreachable` = does not answer at all ("Neu suchen").
- **Cover pictures:** only what a speaker of the household delivers itself (`/getaa?…`); addresses of foreign servers
  get no identifier and are never loaded (no egress entry).
- **Security:** XML at most 2 MB, documents with DOCTYPE are rejected; names and titles are foreign text;
  the plugin reads no credentials of music services.
- **Verified on the real device (19.11.2026, read-only):** S2, software 98.0 (app 27.3), UPnP switched on –
  SSDP answer, `device_description.xml`, `GetZoneGroupState`, `GetTransportInfo`, `GetPositionInfo`,
  `GetMediaInfo`, `GetVolume`, `GetMute`, `Browse FV:2`, error answer (HTTP 500 with `errorCode`). Surround speakers
  are only in `HTSatChanMapSet` there, not as members of their own. **Not checked on the real device:** all
  writing actions (playback, volume, favourite through the queue, grouping) and the behaviour with
  UPnP switched off – the procedure follows SoCo and the service description; add here after acceptance.
  **Verified on the real device with writing (19.11.2026, Beam with two surround speakers):** play, pause, track
  change, volume, mute; `SetAVTransportURI` for a TuneIn favourite with `sid=333` → 402 (with and without metadata),
  with `sid=254` → accepted and playing (the speaker itself turns it into `sid=303`); `x-sonosapi-radio` (Sonos
  Radio) accepted unchanged; `AddURIToQueue` with a radio station → 800; "Play" for a station answered after more than 6 s.
  Still unchecked: albums/playlists through the queue, grouping, stereo pair and sub, UPnP switched off.
- **Tests:** `public/plugins/sonos/tests/test_sonos_plugin.py` (without ComfyOS), `tests/plugins/test_sonos.py` (in the
  core, test world `tests/support/world_sonos.py`), `tests/ui/ui_media.py`.

## Since 0.21

- **Plugin API revision 3** (additions only, `API_VERSION` stays 1): helper programs and access to everything ComfyOS
  controls – both only with an entry in the manifest. Tile and approval prompt name both in plain words
  (`plugins.public` → `helper`, `access`; `plugins.js`).
- **Helper programs (`backend/app/helpers.py`):** manifest `"helper": {"desc": "…"}`, then `context.start_helper(program,
  args, *, env=None)` → object with `send(obj)`, `messages()` (asynchronous iterator across restarts), `running`,
  `stop()`. One helper per plugin. Limits the core sets:
  - Start without a shell (`asyncio.create_subprocess_exec`); after `resolve()` `program` must be in the folder of the plugin or in
    `data_dir` (not beyond it through symlinks either). Arguments and environment limited (number, length, names
    `^[A-Z_][A-Z0-9_]*$`). Environment empty except for `HOME` = `data_dir` and `LANG` (under Windows additionally `SystemRoot`,
    without which no program opens a socket); working folder `data_dir`.
  - Exchange only as JSON lines over stdin/stdout: at most 1 MiB per line, 1000 lines per second, 2000 unread
    messages – whoever exceeds that is terminated. stderr is read and discarded; the log only says "Beiprogramm
    meldet Fehler" with a counter.
  - Supervision through `tasks.spawn("beiprogramm-<id>")` (restart with a growing pause). `stop()`, switching off the plugin
    (`plugins._stop`) and shutdown end it: close stdin, terminate after 1 s, kill after 5 s. Contract: the
    helper ends itself when stdin closes, and reports with a first message after every start.
  - Guard: `tests/security/test_helpers.py`. Rule for when a helper is an option at all:
    `public/CONTRIBUTING.md`, "Helper programs".
- **Access with permission (`"access": ["devices", "rooms", "scenes", "home"]`):** without permission the calls raise
  `PluginError`. The core hooks the paths into `plugins.access_hooks` (`core._plugin_*`).
  - `context.all_devices()` → `plugin_api.DeviceView` (from `core.view_row`): id, name, kind, room, plugin, online,
    locked, switchable, state values (`core.VIEW_STATE`), capabilities, `LightState` – nothing of the plugin behind it
    (no control channels, addresses, source details).
  - `context.on_change(callback)`: `callback(device_id, changes)` on every live message (`core.publish` →
    `plugins.notify`); empty id = "read anew" (device list, scenes, home). **As long as a switched-on plugin
    listens, `core.live_loop` also runs without an open page** (`plugins.watching()`) – the device state is then
    reconciled permanently every 3 s. Callbacks end when the plugin stops.
  - `context.command(device_id, state)` → `core.apply_device(…, source="plugin:<id>")`: device lock
    (`plugin_api.DeviceLocked`) and log as everywhere; the log says "(durch <Plugin>)" (`core.source_tag`).
    `context.switch` stays for own devices in plugin routes.
  - `context.room_lights(room | None, on, brightness)`, `context.scenes()` / `context.house_scene(id, active)`,
    `context.home_state()` / `context.home(state)` → the same helpers as interface and flow (`apply_lights`,
    `apply_house`/`stop_scene`, `apply_home`). A plugin that sets home/away only waits 5 s for
    the heating sync, like a flow.
  - Errors of the core arrive as errors of the plugin API (`plugins._as_plugin_error`): 423 → `DeviceLocked`,
    502 → `DeviceUnreachable`, otherwise `PluginError` with the text of the interface.
- **`public/sdk/testing.py`:** `FakeContext(…, helper=True, access=(…))`, `FakeHelper` (`feed()`, `sent`, `sent_of()`),
  `device_views`, `scene_views`, `commands`, `change()`.
- **Plugins may import packages from `requires`** (`test_plugins` section 10) – so far only `cryptography` in the
  Matter plugin, which the core brings along anyway.
- **Release package:** `updater._JUNK_PARTS` knows `node_modules`; `dist/` is only a build folder at the root –
  `public/plugins/matter/bridge/dist/bridge.mjs` is shipped (`test_structure` section 4).

### Matter plugin (`public/plugins/matter/`, since 0.21)

Bridge (ComfyOS devices → Alexa, Apple Home, Google Home), the home as devices, controller (Matter devices → ComfyOS).
Matter is spoken by `matter.js` 0.18 as a helper under Node 24; the core knows nothing about it.

- **Files:** `__init__.py` (plugin: helper, shares, commands, controller devices), `protocol.py` (building and
  checking messages), `storage.py` (storage of `matter.js`: `matter.bin`, AES-GCM), `devices.py` (device ↔ Matter type
  and values, both directions), `home.py` (the home as devices), `shares.py` (shares, fixed endpoints), `runtime.py`
  (finding/downloading/checking Node), `routes.py` + `static/settings.js` (tile), `bridge/` (helper).
- **Helper (`bridge/bridge.mjs`, bundled to `bridge/dist/bridge.mjs`):** started with
  `node --permission --allow-fs-read=<dist/bridge.mjs> <dist/bridge.mjs>` – no writing, no child processes, no
  workers, no native extensions (Node 24 does not restrict the network; `matter.js` needs UDP and mDNS).
  `setup.mjs` runs before `matter.js` and switches off its self-configuration (no configuration file, no
  storage folder, no signal handlers). Two nodes in one process: `bridge` (aggregator on endpoint 1, one
  bridged node per share) and `controller` (own fabric). Without `COMFYOS_MATTER_LOG` it writes nothing to stderr.
  Building: `dev/build_matter.sh` (esbuild; `bun:sqlite` is replaced by `stubs/empty.mjs`). **After every change to
  `bridge/*.mjs` rebuild and check in the bundle.**
- **Messages** (JSON lines, `protocol.py`; values in Matter units): plugin → helper `storage`, `start`,
  `endpoints`, `update`, `commissioning`, `remove_fabric`, `pair`, `unpair`, `invoke`, `reply`; helper → plugin
  `ready`, `storage`, `started`, `pairing`, `fabrics`, `command`, `node`, `node_removed`, `result`, `error` (only an
  identifier, never free text). After every (re)start the helper reports `ready`; the plugin answers with the
  stored state and `start`. **Everything from the helper is foreign input** – `protocol.parse` checks every field.
- **The plugin enforces, not `matter.js`:** in the helper only shared endpoints exist. A command of a
  controller arrives as `command`, is carried out through `context.command` (or `home.run_command`) and answered with `reply`
  `ok`/`locked`/`error` – only on `ok` `matter.js` takes over the value. Exception target temperature: it is
  an attribute the controller writes, and `matter.js` decides about a write access without waiting.
  Therefore the helper knows the locked endpoints (`locked` in `endpoints`/`update`) and rejects there at once;
  every other write access is accepted, reported to the plugin and written back on rejection.
- **Shares (`shares.py`):** default off, devices of the own controller (`matter:`) never. Since 0.21.1 no limit for the user: from `ADVISED_ENDPOINTS` (50) on the tile only warns (Alexa becomes unreliable with very many devices on one bridge); `MAX_SHARED_ENDPOINTS` (1000) is a technical bound for the size of the settings (`plugins.MAX_SETTING_BYTES` = 64 000 per value). Bulk share through `PUT /api/plugins/matter/shares` with `keys` (all, a room, a group of the floor plan – the tile knows the members from `GET /api/floorplan`); keys ComfyOS does not currently offer are dropped. New endpoint numbers are stored once per round, not per device.
  Stored in the plugin settings (`shared`, `endpoints`, `next_endpoint` → in the backup). An endpoint stays assigned to its
  device forever, even if it is deselected in between. A sensor with temperature and humidity occupies
  two endpoints (`<id>` and `<id>#humidity`).
- **Sync to the bridge:** `on_change` → update the view → after 0.5 s bundled `update` per changed endpoint
  (at most two per second and device); with a changed list `endpoints`; every 60 s read everything anew.
- **Pairing:** numeric code (`passcode`, secret), discriminator and serial number are created on the first
  start. The bridge only announces itself while the window is open ("Kopplung öffnen", 15 minutes; a new
  controller closes it). The code is only output by `GET /api/plugins/matter/status`, and only with an open window.
  Test identifier (vendor `0xFFF1`, product `0x8000`), test certificates of `matter.js`. **Own names of the controllers
  (since 0.22):** `PUT /api/plugins/matter/controllers/{index}/name` (empty = the name the controller brings along);
  stored in the plugin settings (`controller_names`, per controller index = lowest fabric index), a removed controller takes its
  name with it (`_on_fabrics`). The tile shows controllers as rows like the device lists, numbered by position
  (since 0.22.1, see above). If the last controller removes the bridge, `matter.js` resets it (new keys, same endpoints).
- **Controller:** `pair` with the code from "Share" (eleven digits or `MT:…`), devices as `matter:<node>-<endpoint>`,
  all `discovered`. Attestation without a request on the internet; the findings (`warning:DclServiceUnavailable` …) are in
  the tile. `devices()` raises `DeviceUnreachable` while the helper is starting – that way the core cleans up nothing.
- **Node (`runtime.py`):** fixed version `NODE_VERSION` with SHA-256 per system in `ARCHIVES`; searched in
  `<plugin>/runtime/` (for the image) and `<data_dir>/runtime/`, each with a `VERSION` file. "Einrichten" downloads through
  `context.http("runtime")` from nodejs.org, checks the sum and writes exactly one file from the archive. No build
  for 32-bit ARM – the tile says so.
- **Ports:** 5540 (bridge), 5541 (controller); settings `bridge_port`/`controller_port` only for tests. Never
  expose them to the outside. mDNS runs in the helper (port 5353, shares the address with avahi).
- **Tests:** `public/plugins/matter/tests/` (without Node), `tests/plugins/test_matter.py` (in the core, `FakeHelper`),
  `tests/plugins/test_matter_node.py` (real `matter.js`, needs Node 24), `tests/ui/ui_matter.py`.
- **Known peculiarities:** about 220 MiB of memory for the helper (`dev/perf.md`); according to Matter 1.4 the smoke detector type
  requires a power source endpoint that the bridge does not deliver (`matter.js` warns, controllers
  accepted it in the test anyway); thermostats only report "heating".
- **Acceptance on a real controller – open: Alexa on an Echo Pop** (the most important open point before 1.0,
  `Roadmap.md` section 3). Apple Home is already paired; Alexa joins as a further controller (multi-admin).
  1. *Preparation:* live installation on the current version, Matter tile running. Under "Für Matter sichtbar" share
     only three devices at first – a dimmable lamp, a switch or socket, a temperature sensor or thermostat – so an
     error can be narrowed down. Phone with the Alexa app in the same Wi-Fi as the Pi (no guest Wi-Fi, no client
     isolation), Bluetooth on, Echo Pop online.
  2. *Pairing:* "Kopplung öffnen" in the tile (15 minutes, QR code and eleven-digit code). In the Alexa app add a
     device, category "Andere" → Matter (the wording changes between app versions), then scan the QR code or type
     the code.
  3. *Checkpoints:* (a) Alexa reports the bridge and the shared devices (a sensor with humidity counts twice);
     (b) the tile lists a new controller and the window closes; (c) switching and dimming in the Alexa app and by
     voice at the Echo Pop – the device follows, the event log names "durch Matter"; (d) switching in ComfyOS – the
     Alexa app shows the new state within a few seconds; (e) device locked in ComfyOS – the Alexa command has no
     effect; (f) a fourth device shared – it appears in Alexa (perhaps only after "Geräte suchen" there);
     (g) `sudo systemctl restart comfyos` – after about a minute Alexa controls again without a new pairing;
     (h) Apple Home still controls; (i) remove the controller in the tile or the bridge in Alexa – both sides agree.
  4. *Error patterns:* nothing found after the code → mDNS/IPv6 between phone, Echo and Pi; while the window is open
     `avahi-browse -rt _matterc._udp` on the Pi (package `avahi-utils`) must list the bridge. Pairing aborts after
     the code with "not certified" or a generic failure → the attestation of the test identifier (vendor `0xFFF1`)
     is refused – the known risk (`Roadmap.md`, risks). Counter-check: pair a certified bridge (the Matter function
     of the Hue Bridge) with the same Echo and remove it again; if that works, the attestation is the cause and the
     decision in `Roadmap.md` section 9 is due. Bridge paired but devices missing → note which device types are
     missing.
  5. *Record:* versions of the Alexa app and the Echo Pop, the step that fails, the exact error text. No screenshots
     with the pairing code or real names. Add the result here and in `Roadmap.md`.

## Since 0.20

- **Capabilities on the device row (`row["caps"]`):** every row from `core.all_devices()` carries the list of its capabilities
  `[{key, type, readable, writable, unit?, minimum?, maximum?, step?, options?, label?}]` – from `plugin_api.Capability`
  (`device_merge._caps`), supplemented by values a device reports without declaring them (`core._complete_caps`). Through it
  interface, flow and from 0.23 on the widgets find fitting devices, no matter which plugin delivers them (`HO.caps` in the frontend).
- **Errors from plugin code:** for the user only the own text of a `PluginError`, otherwise the kind of the exception –
  one place for it: `plugins.error_text(exc)`.
- **Device query:** `plugins.collect()` passes `max_age` per query; per plugin at most one query runs at a time, whoever
  does not need it fresher shares it (`plugins._devices`). No shared intermediate state – see `dev/perf.md`.
- **Credentials in the moving package:** exported is exactly what manifests name under `secrets`; only
  names of the form `plugin:<id>:<key>` are imported (`plugins.secret_name`, `plugins.is_secret_name`, `backup_store`).
- **Notes for installations before 0.19 are removed** (`LEGACY_KEYS`, `legacy_hub`, `legacy_note`): foreign keys in
  `settings.json` stay untouched and are never delivered.
- **Descriptions:** all functions of the plugins, the template and `public/sdk/testing.py` carry English docstrings
  (`test_plugins` section 10).

## Structure and rules

- **Plugin system (since 0.18, plugin API v1):** device connections are plugins – a folder with `manifest.json` and a Python package that delivers a `Plugin` through `plugin_api` (the only allowed core dependency).
  - **State 0.19.0:** all device connections are plugins – `hue`, `mqtt`, `network` and `qivicon` (Home Base including Magenta sync). The core knows no plugin by name and also runs without a Home Base; there is no built-in source any more (`homebase_source`, `core.connector`, `plugins.register_core` are removed). In the interface they are called **Plugin** everywhere (not "Integration" or "Erweiterung").
  - **Plugin API (`plugin_api.py`, public, copy `public/sdk/plugin_api.py` – `test_plugins.py` checks the equality; copy after every change):** `Plugin` (`start`, `stop`, `devices`, `command`, `status`, optional `routes`, `rename`/`clean_name`, `scenes`/`scene_image`, `link_prefixes`, `hints`, `command_interval`), `Device` (+ `Capability`, `LightState`/`LightCapabilities`), `ScenePalette`, `PluginContext` (`settings`, `secrets`, `http`, `data_dir`, `log`, `event`, `device_changed`, `devices_changed`, `is_local_host`, `hints`, `switch`; since revision 4 `open_datagram`, `discover`), `PluginError`/`DeviceUnreachable`, colour conversions (single source, `light_model` passes them on). **Semver:** `API_VERSION` only rises with breaking changes; new optional fields/methods increase `API_REVISION` (manifest: optional `"api_revision": n` – an older core then rejects the plugin with "bitte ComfyOS aktualisieren"). **Revision 2 (0.19):** `context.open_stream` (TCP), `ConnectionRefused` (port closed = the device answered), device kind `presence`, states `humidity` and `present`; **for hubs:** `"id_prefixes": ["*"]` (devices under their original ids; an exact prefix of another plugin takes precedence), `Device.room_id`/`type_name`/`low_battery`/`last_seen`/`problems`/`readings`, rooms (`Plugin.manages_rooms` + `rooms`/`create_room`/`rename_room`/`delete_room`/`move_device`), heating routines (`Plugin.syncs_heating` + `heating_status`/`sync_heating`, `context.heating`), `Plugin.max_device_age` (how old a state from the cache may be), `context.device_gone` (device deleted at the source → own name and "Entfernt" mark gone), `status()["egress"]`. `Device.state` may carry further simple values (Home Base: `hb_status`, `hb_detail`) – they are in the row and in the lists under `extra`. **Revision 5 (0.22.1):** `Capability.label` (see above).
  - **Manifest (schema 1, `plugins.read_manifest`):** `id` (= folder name, `[a-z][a-z0-9_]`), `name`, `description`, `version`, `api`, `entry` (`__init__:setup`), `id_prefixes` (mandatory; devices without a matching prefix are discarded; `"*"` = hub with arbitrary ids), `egress` (`{key, desc, scope: "lan"}` or `{key, desc, hosts, data, trigger}`), `settings` (`text|number|toggle|select|password`), `secrets`, `static`, `requires`. Example and table: `public/CONTRIBUTING.md`.
  - **Loader (`plugins.py`):** `discover()` (on import of `main`, only reads manifests; also at runtime: `POST /api/plugins/rescan`) → `load()` (import as `comfyos_plugin_<id>`, `setup(context)`, include routes) → `start_all()` in `core.startup`. Sources: `public/plugins/*` (built in; folders with `_` are templates and are never loaded) and `<DATA>/plugins/*` (added later). **Plugins added later are foreign code with all rights:** they are only imported after the user approved them with a password prompt (`plugins.trusted`, `core.step_up`); a user plugin cannot replace a built-in one. Settings in `settings.json` → `plugins: {disabled, trusted, settings: {<id>: …}}` (`settings_store._clean_plugins`), secrets as `plugin:<id>:<key>` (only those named in the manifest), own files in `<DATA>/plugin_data/<id>/`.
  - **A plugin drags nothing down:** exceptions from manifest, import, `setup`, `start` → state "Fehler" (tile, event); from `devices()` → the source drops out for this round (`Source.devices = None`, 20 s time limit), the other devices stay; from `command()` → 502. Error texts only name the type of the exception, never its content. The instance of a plugin survives off/on (its routes are included); switched off, the routes answer with 404 (`plugins.require_active`).
  - **Device list (`core.merged_devices`):** `plugins.collect()` → `device_merge.merge()` → `device_store` (names, removed, local rooms, adopted, locks) → device row. Rules in `device_merge.py`: a common `link_keys` entry = one device (the relayed one – `relayed`, view of the hub – keeps id, name, room; state, kind, switching from the direct device; `row["control"] = {plugin, device_id}`, `row["source"]`); a key in the `link_prefixes` range of a running plugin without a counterpart → `link_missing` (+ `missing_at`, `missing_plugin`); only `discovered` → candidate at the plugin until adopted (`adopted`); unreachable plugin → list of the others unchanged. The row carries `plugin` (origin). Devices without a room from their hub get it from `device_store.rooms` (otherwise the room of the same name as a suggestion); own room names (`core.room_names`) take precedence over the names of the hub. **No plugin is imported in the core or asked by name** – whoever needs something specific to Hue or the Home Base extends the API.
  - **Switching:** `core.send_light`/`apply_device` → `plugins.command(row, state, source)` → `Plugin.command` (returns the new values, e.g. `{"target": 21.5}`; the text for the log is formed by `core._command_text`). A row without `control` is not switchable (400). The log names the switching plugin behind it – unless it is the room hub (`core._via`). The loader checks the device lock once more itself; a plugin's own routes only switch through `context.switch` (→ `core._plugin_switch` → `apply_device`). Pause between commands: `Plugin.command_interval` (`plugins.command_interval(row)`).
  - **Live:** `context.device_changed(id, values)` → `core._plugin_device_changed` (index `(plugin, id) → device id` from the last merge) → SSE + `flow_engine.push_device` (`FLOW_PUSH_KEYS`); `context.devices_changed()` → SSE `reload`.
  - **Cleaning up (`device_store.prune`):** only when all sources delivered, and never for devices of plugins whose list proves nothing (off, error, unreachable, not set up – `plugins.unsettled_prefixes`). Otherwise adopted devices and names would be lost as soon as a bridge is gone briefly.
  - **Routes:** generic in `api/plugins.py` (`GET /api/plugins`, `POST …/rescan`, `PUT …/{id}/enabled|settings`, `GET …/{id}/devices`, `POST …/{id}/adopt`, `POST …/{id}/names/sync`, `GET /api/scenes/image/{id}/{image}`, static `GET /plugins/{id}/static/…` – only known file types, never outside the folder); a plugin's own routes under `/api/plugins/<id>/…` (behind the login; `tests/security/test_fuzz.py` fuzzes them too). The old paths `/api/hue/…` have not existed since 0.19.
  - **Interface:** `js/plugins.js` (tiles, switches, approval, "Neu suchen"), `js/fields.js` (fields from `manifest.settings`). If a plugin brings `static/settings.js`, it draws the content of its tile: `HO.plugins.register('<id>', { render(body, tools), deviceActions(device) })`. **Actions on devices:** `deviceActions` delivers `[{key, label, icon, danger, run(device, done)}]` – the device menu of the home page asks `HO.plugins.deviceActions(d)` (loads the script of the plugin on demand, details from `ov.integrations`), lists draw them with `tools.ui.actionButtons(…)`, tiles of other plugins with `tools.ui.sourceActions(row, ['delete'])` (e.g. Hue: a leftover entry "Von der Home Base löschen"). That way neither the core nor another plugin knows the Home Base.
  - **No conversion:** `data/hue.json` and the old settings `hue_*` are not taken over (rule since 0.17); the bridge has to be paired anew once after the update.
  - **Style (binding, explicitly demanded by the operator):** plugin code, template and `plugin_api.py` are human-readable and uniform – written-out names, type annotations, docstrings, comments on the why, data classes instead of nested dictionaries, pure conversions separated from the network. **Identifiers, comments, docstrings and documentation in English**, texts for the user in German (since 0.22.1 this holds for the whole code base, not only for plugins). The rules are in `public/CONTRIBUTING.md`; `test_plugins.py` section 10 checks for every built-in plugin: only allowed imports, module docstring, docstrings of public functions, type annotations, no one-letter names. Whoever turns an existing connector into a plugin brings it to this style in the process – not just moving.
  - **New plugin ⇒** folder from `_template`, manifest, tests in `tests/` (run without ComfyOS with `sdk/testing.FakeContext`; `run_all.py` collects `public/plugins/*/tests/test_*.py`), `README.md`. Tests: `tests/plugins/test_plugins.py` (loader, heating interface), `tests/plugins/test_hue.py` (Home Base + Hue together), `tests/plugins/test_integrations.py` (MQTT/network in the core, without a Home Base), `public/plugins/<id>/tests/…`. **Mock Home Base in core tests:** `tests/support/homebase.py` – derive the mock from `QiviconConnector`, put it into the plugin with `install_homebase(mock)` (`install_homebase(None)` = no Home Base).
- **The device row belongs to the core (since 0.19):** `Device.state` of a plugin only lands in the row with state keys – fields the core keeps (`device_merge.RESERVED`: id, name, room, kind, control, lock …) cannot be overwritten by a plugin; `context.device_changed` only passes `core.LIVE_FIELDS` (without `locked`) on to the pages. Devices of a plugin without a switchable capability answer commands with 400 "… lässt sich nicht schalten" (no passing on to the Home Base). New state keys: `core.LIVE_FIELDS` + list in `overview.js` (stream) + if needed a property in `flow_nodes.json` + `FLOW_PUSH_KEYS`.
- **Tiles of the plugins (`plugins.js`):** `tools.roomOptions(suggestion)`, `tools.adoptAll()` (`POST /api/plugins/<id>/adopt_all`); the tile names home-network targets ("Spricht nur im Heimnetz: …"). **Since 0.19.2 without a common wrapper** (`#pluginList` no longer exists): the tiles (`[data-plugin]`, closing `#pluginInfo`) stand freely between the other tiles of the system page; clicks/forms are bound by `HO.plugins.mount` once per page, `HO.plugins.refresh` replaces every tile in its place (new ones behind the last plugin) and calls `HO.plugins.onChange` (the system page sorts them in). **Forms in tiles belong to the plugin:** `page.onsubmit` of the system page skips everything in `[data-plugin]`/`#pluginInfo` (otherwise the page redraws and the input is gone). **Live (since 0.19.1):** the system page listens to `HO.live`; `device` with `online` or `reload` → `HO.plugins.reload` (only tile contents, bundled, never during an input in a tile). The system load asks every `SYS_POLL_MS` (5 s) while the page is open and visible; `destroy()` clears both away. **Two tabs (since 0.19.3):** the system page has "Einstellungen" (`#settings`) and "Plugins" (`#settings?tab=plugins`, `settings.js` `sysTab()`/`renderPlugins`) at the top – same header (`headHTML`: tabs, columns, Anordnen), each with its own column choice (`ho.cols.system`/`ho.cols.plugins`) and its own arrangement (`layout.system`/`layout.plugins`). Links that mean plugins (connection bubble, "Zu den Plugins" at "Noch nichts verbunden") lead into the tab Plugins; plugin tiles and `HO.plugins.mount` only exist there. A forced redraw keeps the tab (since 0.22.1, `app.js` `redrawPage()`). **Arranging tiles freely (since 0.19.2; since 0.19.3 the common building block `HO.tileGrid({page, box, key, saved, item})` in `core.js`, also for the room tiles of the home page):** `#sysGrid.mz` with 1–3 column stacks `.mz-col` (chosen per device, `HO.cols`, localStorage `ho.cols.system`; one on the phone) – flush at the top, every tile only as high as its content, distance only through `gap` (`margin-top: 0`, otherwise a tile sits lower; `ui_system` checks that). Without an own arrangement every tile goes into the shortest column in order (measured in an invisible measuring column `.mz-probe`); that only repeats in the first seconds while contents are loading, and never after an input on the page. "Anordnen" lays `.mz-over` over every tile (mouse: drag anywhere, finger: at the handle `.mz-grip`, arrows up/down/left/right; scrolling along at the edge), "Automatisch" forgets the arrangement of this column count. Stored per tab and column count in `settings.json` → `layout: {system|plugins|home: {"<n>": [[tile id, …], …]}}` (`settings_store._clean_layout`/`clean_columns`: only pages from `LAYOUT_PAGES`, ids `[A-Za-z0-9_:.-]{1,64}`, each once, at most `LAYOUT_MAX_TILES`; route `PUT /api/settings/layout`, `columns: null` = forget) – thus applies on all devices and is in the backup. Every tile of the system page needs a fixed `id`.
- **Home Base (plugin `public/plugins/qivicon/`, since 0.19; up to 0.18 `connectors/qivicon*.py` in the core):** login → JWT → JSON-RPC `smarthome.rest/proxy` on openHAB (`/rest/things`, `/rest/items`, `/rest/qivicon/rooms`). Reading **and** switching. Files: `connector.py` (`HomeBaseConnector`: session, proxy, devices, rooms, pairing calls, commands), `model.py` (openHAB → `HomeBaseDevice` → `plugin_api.Device`), `pairing.py` (preparing the catalogue), `telekom.py` + `magenta.py` (Magenta sync), `routes.py`, `static/settings.js` + `static/pairing.js`. Roles: `manages_rooms` (keeps the room list) and `syncs_heating` (Magenta sync). Device ids are the thing UIDs of the Home Base (manifest `"id_prefixes": ["*"]`) – that way floor plan, flows, scenes and locks from the time before the plugin stay valid. Settings `address`, `verify_tls`, `gateway_id`, `telekom_username`; secrets `password`, `telekom_password`. **No conversion:** the old connection (`settings.json` `mode/base_url/…`, secrets `homebase`/`telekom:<user>`) is not taken over – address and password have to be entered anew once.
  - **One path for all calls (since 0.13): `HomeBaseConnector._proxy`.** Reading (`_rpc_get`), writing (`_rpc_write`, `_rpc_call`) and switching (`_write_item`) run through it: expired session → sign in once more and repeat; network error → `HomeBaseUnreachable` (a `QiviconError` and `DeviceUnreachable`). Never call `_rpc_raw` directly – that is how a switching command used to get lost after an expired session.
  - `_login` runs under a lock (never two logins at once); `list_devices` bundles simultaneous queries (`_device_lock`, `_dev_started`) and caches nothing if something was switched in the meantime (`_dev_generation`).
- **Pairing/reconnecting/deleting devices (plugin `qivicon`: `pairing.py`, `routes.py`, `static/pairing.js`; routes under `/api/plugins/qivicon/pairing/…`, `…/inbox/…`, `…/things/<uid>/reconnect|state`; verified by a capture of the QIVICON web interface 11/2026, test `tests/plugins/test_pairing.py` checks the exact calls):** types `GET /rest/qivicon/pairing/devices`; search `POST /rest/discovery/bindings/<binding>/scan` (answer = seconds); found `GET /rest/inbox`; including `GET /rest/qivicon/flows/thing/<uid>/contexts/setup-inbox` → `POST …/setup-inbox/<flow>` `{"debugging":false,"label":…}` (answer `state=FINISHED`) → `DELETE /rest/qivicon/flows/<flow-uid>`, room afterwards by `PUT /rest/qivicon/devices/<uid>` (procedures with `steps` → QIVICON interface; without a procedure openHAB `approve`); reconnecting `GET /rest/qivicon/flows/thing/<uid>/contexts/setup-reconnect` → `POST …/setup-reconnect/<flow>` `{"debugging":false}` → `POST /rest/qivicon/flows/<flow-uid>/steps/<step>` `{}`; deleting `DELETE /rest/qivicon/devices/<uid>` (204). Device catalogue (makers, images, instructions) = Telekom backend `pis/hardware/devices[/<id>]` with `data-backend-token`/`-url` of the dashboard page (token only to `*.qivicon.com`); without the catalogue a replacement list from the types. UIDs always with `quote(uid, safe="")` (`:` → `%3A`).
- **Classifying and switching devices (plugin `qivicon`: `model.py`; tests `public/plugins/qivicon/tests/test_qivicon_plugin.py` and through the core `tests/core/test_devices.py`):** `classify` determines the kind, the channel helpers the switching path. **Control channels are not found through a fixed list of names** (that is where smart plugs with channel names like `switchState` used to fail) but through the item type plus a block list `_READ_ONLY` for channels that only report (button, tamper, smoke, contact, reachability …): `switch_channel` (Switch), `dim_channel` (Color/Dimmer), `cover_channel` (Rollershutter). Without a control channel no `control_item` – and the interface then deliberately shows no switch (never switch blindly). New device kind ⇒ channel helper + `classify` + `capabilities` (switchable only with a control channel) + `HO.kindLabel`/`HO.devState`/`HO.devClass`/icon + `set_device_state` + section in the test of the plugin.
  - **Roller shutter/awning/blind (`kind = "cover"`, since 0.13.1):** `position` 0 = open, 100 = closed; commands `{"cover": "up"|"down"|"stop"}` or `{"position": 0-100}`. Writing happens as for lamps through `POST /rest/items/<item>`; the command words are those of openHAB (UP/DOWN/STOP/percent). For this vocabulary there is **no** capture of a real Home Base yet – the path is the same as since 0.1, only the words come from the openHAB specification. If a capture arrives, add it here.
  - **Consumption metering:** every device that reports power or a meter reading gets `power_w` (Number channel with "power") and `energy_kwh` (channel with "energy"/"consumption"/"meter"; Wh is converted) – independent of the device kind, so that pure meters count too. ComfyOS never adds anything.
  - **Valve opening (since 0.16, read-only):** thermostats get `valve` (0–100 %) from `valve_channel` (Number/Dimmer channel with "valve" in the name or Homematic `LEVEL` 0…1, without adaption/status/error) and `valve_percent`. **There is no capture for the Home Base for this** – if one arrives, add the real channel name here. `history.record` logs `valve` along (heating log), `room_series` averages per room and window (`points[].valve`, `valve: bool` per room). `HO.tempChart(…, {lane: true})` draws a lane of its own below (same time axis, scale closed…open, token `--valve` – **not** `--heat`, that is the number of the radiator animation): measured as a step curve, otherwise hatched "Heizbedarf (abgeleitet)" = target > actual + 0.3 like `HO.devClass`. With a reported valve `heat` in `HO.devClass` = valve > 0. `valve` is in `LIVE_FIELDS`.
- **Rooms:** **the floor plan is the master.** If a hub keeps a room list of its own (plugin with `manages_rooms`, set up – `core.room_hub()`; so far the Home Base: openHAB groups `home-group`, `/rest/qivicon/rooms`), ComfyOS writes rooms and assignments there (`api/rooms.py` → `Plugin.rooms/create_room/rename_room/delete_room/move_device`); devices of the hub carry their room as `Device.room_id`. All other devices (another plugin, adopted, or there is no hub) have their room only in ComfyOS (`device_store.rooms`, row `local_room: true`). **Without a hub** rooms live in the floor plan alone (key `plan:<id>`), `room_pending` is empty (`hub: null`), `POST /api/rooms` creates nothing. The interface names the hub by name (`HO.roomHub()`).
- **Heating sync / Magenta sync (optional):** the heating routines belong to ComfyOS (`routines_store`, `home_store`). A plugin with `syncs_heating` syncs them with another system – so far the Magenta sync in the plugin `qivicon` (OAuth → cloud relay `ris-prod` → `SMHM/executeCommand`; only heating plans & home/away, **no** room commands). The core only knows `heating_sync.py`: `push_now()` after every change, `sync()` (`POST /api/sync`), `public_status()`; the plugin only sees the routines through `context.heating` (`plugin_api.HeatingPlans`: `changes()` → send → confirm with `done()`/`created()`, `merge()`/`merge_home()` for the state of the other side). **Never stacked:** a request waits at most `SYNC_WAIT`, and a round triggered from a page never queues behind a running one (`MagentaSync.sync`) – jammed syncs kept all connections busy on the iPhone.
- **Hue plugin (`public/plugins/hue/`, since 0.18; up to 0.17 `connectors/hue.py`):** `bridge.py` = CLIP v2 client (link button, lamp list with a cache, commands, event stream), `clip.py` = pure conversion (CLIP ↔ `HueLight`/`LightState`/`ScenePalette`, `build_command`), `__init__.py` = `HuePlugin`, `routes.py` = `status`, `pair`, `forget`, `devices/<id>` (delete), `static/settings.js` = tile. Settings `host`, `bridge_id`, `name`; secret `app_key` (only goes to home-network addresses: `context.is_local_host` + `scope: "lan"`). Device ids `huebridge:<light-rid>` or `huebridge:motion:<rid>`, all `discovered`; `link_keys`: lamp `hue:<bridge-mac>:light:<v1-no>`, motion detector `zigbee:<mac 16 hex>` – the Home Base carries the same keys for its Hue things (`qivicon/model.link_keys`). That way a lamp of the Home Base stays *one* device (id/name/room from the Home Base, state/switching through the bridge), bridge-only lamps only appear after adopting (room in `device_store.rooms`), without a counterpart = `link_missing`. Bridge gone → pure Home Base data. Names: ComfyOS is the master – `Plugin.rename` writes `device.metadata.name` by CLIP v2 (max. 32 characters, `clean_name`), `POST /api/plugins/hue/names/sync` syncs all. Live events (`HueBridge.listen`) → `context.device_changed` → SSE → `overview.js`/`lights.js` (`overrides`, double-tap lock). **No jumping back (since 0.12.4):** `HueBridge.lights()` replays commands and events since the last cached state onto the fresh answer (`_recent_changes`, `_replay_recent_changes`); `HomeBaseConnector.list_devices` caches no answer if `invalidate_devices` ran in the meantime (`_dev_generation`); in the browser `overrides` holds the tapped state at least `HOLD_MS` (3 s) against older answers. **Hue motion detectors:** linked through the Zigbee key, the bridge is the only source for `motion` (list, live reconciliation, flow) – otherwise the polling would see the sluggish Home Base state and trigger twice.
- **MQTT plugin (`public/plugins/mqtt/`, since 0.19, version 1.1.0 since 0.22.1, without a third-party library):**
  - **Files:** `protocol.py` = MQTT 3.1.1 (CONNECT, SUBSCRIBE, PUBLISH QoS 0/1, PING, DISCONNECT; no QoS 2, no MQTT 5), `client.py` = one connection through `context.open_stream` with keep-alive, reconnecting (1 s … 60 s), last will `comfyos/status`, limit `MAX_PACKET_BYTES` = 4 MiB per packet (the device list of a large Zigbee2MQTT network is one message; 64 KiB up to 0.22); `discovery.py` = Home Assistant discovery (`<prefix>/+/+/config` and with `node_id`; single and device form with `components`; abbreviations, `~`) and the common data classes `Entity`, `Announcement`, `StateRequest`; `zigbee2mqtt.py` = device list of a bridge; `tasmota.py` = Tasmota's own discovery and its translators; `translate.py` = one translator per platform between message and ComfyOS values/commands; `registry.py` = grouping functions into devices; `manual.py` = own devices (at most 40, in the plugin settings → in the backup); `routes.py` = `status`, `manual`.
  - **Three sources, one shape:** every source produces an `Announcement` (device + `Entity` list). Home Assistant discovery is authoritative; an announcement with `is_fallback` (Zigbee2MQTT's device list) is only used for devices no other source describes – a device announced both ways appears once, under the same id (`mqtt:<identifier>`, Zigbee2MQTT: `zigbee2mqtt_<ieee>`, the identifier its Home Assistant integration uses too), so name and room stay.
  - **Zigbee2MQTT (`zigbee2mqtt.parse_devices`):** reads `<base>/bridge/devices` (setting `zigbee2mqtt_topic`, default `zigbee2mqtt`) and turns the `exposes` into entities: `light`, `switch`, `climate`, `cover`, `lock`, `fan`, binary/numeric/enum values. State topic `<base>/<friendly name>` (JSON), commands to `<base>/<friendly name>/set` (or `…/set/<property>`). Coordinator, disabled and unsupported devices are skipped.
  - **Tasmota (`tasmota.TasmotaDiscovery`):** reads `tasmota/discovery/<mac>/config` and `…/sensors` (Tasmota's default, `SetOption19 0`); relays (`RelayTranslator`, `POWER<n>`), the light (`TasmotaLightTranslator`: dimmer, CT, HSBColor), shutters (`ShutterTranslator`) and sensors from `tele/<topic>/SENSOR`. Commands go to `cmnd/<topic>/…`. Platform-specific translators are chosen through `registry.PLATFORM_TRANSLATORS`.
  - **State requests (`StateRequest`, `registry.due_state_requests`):** devices of Zigbee2MQTT and Tasmota that reported no state `STATE_REQUEST_DELAY_SECONDS` (2 s) after the list was built are asked once (`<base>/<name>/get` with the readable keys, `cmnd/<topic>/STATE`), with `STATE_REQUEST_GAP_SECONDS` between two requests. Besides the command topics these are the only topics the plugin publishes to.
  - **Platforms (`translate.TRANSLATORS`):** light (JSON/basic), switch, binary_sensor, sensor, climate, cover – and since 1.1.0 fan (kind `switch`, speed and mode as labelled capabilities), lock, select, number, button (kind `unknown`, labelled capabilities; a lock that needs a code is "nicht unterstützt"). Keys of labelled capabilities come from `translate.value_key` (slug of the name + short digest of the unique id – stable across restarts). Options are limited (`MAX_OPTIONS`), foreign text is cut.
  - **Grouping (`registry.py`):** one device per `device.identifiers`; a second switch = a device of its own `mqtt:<id>:<unique_id>`; settings/diagnosis of a device are skipped; what is not supported is in `notes` → tile. Availability is read as plain text or as JSON `{"state": "online"}` (Zigbee2MQTT).
  - **Templates are never evaluated:** only `{{ value_json.path }}` is read as a path into the JSON, command templates only with plain insertion (`{{ value }}`/`{{ position }}`); everything else = "nicht unterstützt". ComfyOS does not pose as Home Assistant.
  - **Report the device list only when complete:** after connecting the plugin waits until the retained discovery messages are there (`SETTLE_SECONDS`, at the latest `LONGEST_SETTLE_SECONDS`), before that `devices()` raises `DeviceUnreachable` – otherwise cleaning up would forget names and rooms; without a broker known devices stay visible (offline). All devices are `discovered` (tile: one by one with a room or "Alle übernehmen"); Zigbee2MQTT devices carry `link_keys` `zigbee:<16 hex>`. Live: at most two messages per second and device.
  - **Settings** `host`, `port`, `username`, `tls`, `verify_tls`, `discovery_prefix`, `zigbee2mqtt_topic`, internal `client_id`, `manual_devices`; secret `password` (only goes into the home network).
  - **Not included (open):** a cloud broker (would be a new target on the internet – only after asking), locks with a code. Heating routines for MQTT thermostats: since 0.23.1 the scheduler of the core sets them like every thermostat outside a synced system (`heating_scheduler.py`, `dev/docs/backend.md`).
- **Network plugin (`public/plugins/network/`, since 0.19):** presence through reachability. Per target (name, address, port; at most 30, in the plugin settings) one device of the kind `presence` with `present`. Checking happens by TCP through `context.open_stream`: accepted **or** refused (`ConnectionRefused`) = present, silence = away only after `away_after` minutes; after a start the state is unknown until an answer comes or the time is up (a restart never looks like "left"). No ICMP, never addresses outside the home network (already rejected when creating). Device ids `network:<key>`, `discovered` – the tile adopts a new target at once. Flow: property `present` ("Anwesenheit") in "Gerätestatus", at once through `FLOW_PUSH_KEYS`.
