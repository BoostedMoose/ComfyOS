# Philips Hue

Connects a Hue Bridge in the home network directly to ComfyOS – through the official local CLIP API v2, without the Hue cloud.

## What the plugin can do

- **Pairing** through the link button of the bridge. The application key lives in the secret store and never goes to an address outside the home network.
- **Lamps and sockets** of the bridge as devices: on/off, brightness, white tone, colour, gradients and effects.
- **Motion detectors** of the bridge: motion arrives at once, not only with the next query – together with temperature, ambient light (lux) and battery (since 1.1.0).
- **Switches** (dimmer switch, tap dial, smart button, wall switch module, since 1.1.0): devices of the kind *button* with named buttons; every press – short, long (once per hold) or double – goes to the flows (trigger "Taster"). Like the motion detectors they appear under "Neu auf der Bridge" until you adopt them.
- **Live:** the bridge reports changes through its event stream – what happens in the Hue app or at the wall switch is in ComfyOS at once.
- **Gentle on the bridge (since 1.1.1):** while the event stream runs, the device list comes from the cache the events keep current (asked anew at most once a minute); at most three requests go to the bridge at once, and one the bridge refuses as busy (HTTP 429) is tried once more after a second. Version 1.1.0 asked for everything every three seconds while Matter kept the live comparison running – the bridge then answered "ausgelastet" again and again and its lamps flickered between offline and online.
- **Reachability (since 1.1.2):** lamps, sockets, motion detectors and switches all report whether the bridge reaches them – from the Zigbee status the bridge keeps for each device, live through the event stream. An unreachable device shows why ("Batterie leer oder zu weit entfernt?").
- **Names:** ComfyOS owns the device names and transfers them to the bridge (at most 32 characters).
- **Scenes** of the bridge can be taken over as colour palettes, with their preview image.

## Interplay with the Home Base

The Home Base lists Hue lamps through its own Hue binding. So that no lamp appears twice, every lamp carries the key `hue:<bridge-mac>:light:<number>` and every motion detector `zigbee:<address>`. ComfyOS then shows one device: name and room come from the Home Base, state, reachability and switching directly from the bridge – the Home Base sometimes reports a Hue motion detector offline for seconds although the bridge reaches it, and that no longer shows. Lamps only the bridge knows appear in the tile under "Neu auf der Bridge" and only become devices after they are imported.

If the bridge is unreachable, the lamps the Home Base knows run through the Home Base again.

## Network

| Target | What for | Sent |
|---|---|---|
| Hue Bridge in the home network (`"scope": "lan"`) | pairing, reading and switching lamps, event stream | application key, light commands, device names |

The plugin opens no connection to the internet.

## Files

| File | Content |
|---|---|
| `__init__.py` | plugin class: pairing, devices, commands, live messages |
| `clip.py` | conversion between the CLIP API and ComfyOS (pure, without network) |
| `bridge.py` | HTTP client: pairing, lamp list with a cache, commands, event stream |
| `routes.py` | endpoints of the tile: status, pairing, disconnecting, deleting a lamp from the bridge |
| `static/settings.js` | content of the tile under System → Plugins |
| `tests/test_hue_plugin.py` | tests against a rebuilt bridge, without ComfyOS |
