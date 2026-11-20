# Matter plugin

Turns ComfyOS into a **Matter bridge** and a **Matter controller** – locally in the home network, without a skill, without a developer account, without port forwarding.

## What it can do

- **Bridge:** devices of ComfyOS – no matter from which plugin (Home Base, Hue, MQTT …) – appear in Alexa, Apple Home, Google Home and every other Matter controller. You choose per device what is visible (default: nothing) – one by one, all at once, per room or per group from the floor plan. There is no fixed limit; from 50 devices on the tile points out that Alexa can become unreliable with very many devices on one bridge.
- **The home as devices:** "\<room\> Licht", "Alle Lichter", house scenes and "Zuhause" can be shared as devices. That way "Alexa, switch off Wohnzimmer Licht" works without a group in the Alexa app.
- **Controller:** you bring in Matter devices of other makers with the code from "Share" in their app. They appear as `matter:<node>-<endpoint>` and are adopted in the tile like every found device.
- **Device lock:** a locked device stays visible for controllers but cannot be switched from there. The event log says that Matter switched.

| ComfyOS | Matter device type |
|---|---|
| lamp on/off · dimmable · white tone · colour | On/Off Light · Dimmable Light · Color Temperature Light · Extended Color Light |
| socket, actuator | On/Off Plug-in Unit |
| thermostat (heating only, 5–30 °C) | Thermostat |
| cover | Window Covering |
| window contact · motion detector · smoke detector | Contact Sensor · Occupancy Sensor · Smoke CO Alarm |
| temperature, humidity | Temperature Sensor, Humidity Sensor (one device each) |

## How it works

Matter itself is spoken by [`matter.js`](https://github.com/project-chip/matter.js) (Apache-2.0). It runs as a **helper program** under Node.js next to ComfyOS:

```
ComfyOS (Python) ── JSON lines over stdin/stdout ── node --permission bridge/dist/bridge.mjs
```

- **Locked in:** the helper starts with `node --permission --allow-fs-read=<bridge.mjs>`. It can read nothing but its own file, write nothing, start no programs and no native extensions. Its environment is empty.
- **No storage of its own:** `matter.js` stores through the plugin. The plugin keeps one file `matter.bin` in its data folder, encrypted with AES-GCM; the key lives in the secret store of ComfyOS.
- **The plugin decides, not `matter.js`:** the helper only knows the shared devices. Every command of a controller is carried out through ComfyOS (`context.command`) – with device lock and log.
- **Everything from the helper is foreign input:** every message is checked against schema and limits (`protocol.py`).

## Setting up

1. *System → Plugins → Matter* → **Einrichten**. Downloads a fixed version of Node.js from `nodejs.org` once (about 30 MB), checks the SHA-256 sum from `runtime.py` and unpacks only the program `node`. Nothing else is ever downloaded. Needs a 64-bit system.
2. Under **Für Matter sichtbar** switch on the devices Alexa & co. should see.
3. **Kopplung öffnen** – the bridge announces itself in the home network for 15 minutes and shows the numeric code and the QR code. Choose "Add device → Matter" in the Alexa, Home or Google Home app.
4. Pair further controllers the same way; each gets keys of its own and can be removed individually.

**Bringing in Matter devices:** choose "Share" / "Pair with another app" in the app of the device, enter the code under **Matter-Geräte einbinden**, then adopt the device under "Neu über Matter".

## Controllers and their numbers

Matter counts *fabrics*, not apps, and some apps join with more than one: **Apple Home always creates two** (one for the home, one for the iCloud keychain). The tile shows them as **one** controller; removing it removes both fabrics.

The number of a controller is its position in the list (1, 2, 3 …), not the fabric index of `matter.js`, which is never reused. After removing a controller the next one takes the first free number again.

## Limits

- The bridge is **not certified** (test vendor id, test certificates of `matter.js`). Controllers show a note while pairing; some Echo models reject uncertified bridges.
- Thermostats only report "heating". Colour works as xy and white tone; Matter knows no gradients and effects.
- The controller only pairs over the network – no Bluetooth, no Thread of its own. Thread devices need a border router in the house.
- The attestation of foreign devices is not checked online; the tile says so.
- Separated networks (VLAN) need IPv6; mDNS shares port 5353 with avahi.
- The moving package does not take the pairings along (data folders of plugins are not part of it): after a move you pair again.
- **The Matter ports (5540 bridge, 5541 controller) never belong on the outside.**

## Files

| File | Content |
|---|---|
| `__init__.py` | plugin: starting the helper, shares, commands of the controllers, devices of the controller |
| `protocol.py` | building and checking the messages between plugin and helper |
| `storage.py` | storage of `matter.js`: one encrypted file, bundled and written atomically |
| `devices.py` | ComfyOS device ↔ Matter device type and values, in both directions |
| `home.py` | the home as devices |
| `shares.py` | shares and permanent endpoint numbers |
| `runtime.py` | finding Node.js, downloading it, checking the checksum, unpacking |
| `routes.py`, `static/settings.js` | tile |
| `bridge/bridge.mjs`, `bridge/setup.mjs` | source of the helper |
| `bridge/dist/bridge.mjs` | the bundled helper that is shipped and runs |
| `bridge/package.json`, `bridge/package-lock.json`, `bridge/build.mjs` | fixed versions and the bundling (`dev/build_matter.sh`) |

## Rebuilding the helper

Only needed when `matter.js` is updated or `bridge.mjs` is changed (needs Node and npm on the development machine):

```
bash dev/build_matter.sh
```

The script installs exactly the versions from `package-lock.json`, bundles with esbuild to `bridge/dist/bridge.mjs` and runs `npm audit`. The bundle is checked in; users need neither npm nor a build step.

## Tests

```
python public/plugins/matter/tests/test_matter_protocol.py
python public/plugins/matter/tests/test_matter_devices.py
python public/plugins/matter/tests/test_matter_plugin.py
```

They run without ComfyOS and without Node (`FakeHelper` from the SDK). The whole way with the real `matter.js` – two runs pair with each other – is checked by `tests/plugins/test_matter_node.py`; without Node the test skips itself.
