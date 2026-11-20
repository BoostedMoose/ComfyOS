# Home Base (QIVICON)

Connects a Telekom Magenta Home Base 2 (QIVICON) in the home network to ComfyOS. A private community plugin, not
affiliated with Deutsche Telekom.

## What it can do

- **Reading and switching devices:** everything the Home Base manages – thermostats, smart plugs, lamps, covers,
  window and motion detectors, smoke detectors, meters. Only what has a control channel is switched (never blindly).
- **Rooms:** the Home Base keeps the room list. ComfyOS writes rooms and assignments from the floor plan there –
  the Magenta app shows the same rooms.
- **Pairing, reconnecting and deleting devices:** the same procedures as in the web interface of the Home Base.
- **Magenta sync (optional):** keeps heating routines and home/away in step with the Magenta app. Needs the
  Telekom login and stays off until it is set up.

## Setting up

System → Plugins → **Home Base**: enter the address (e.g. `https://qivicon` or the IP address) and the password of
the QIVICON web interface, "Verbinden & speichern". The Magenta sync is in the same tile.

## Connections

| Target | What for | What is sent | When |
|---|---|---|---|
| Home Base in the home network | devices, rooms, pairing | password of the Home Base (only to addresses in the home network) | permanently once connected |
| `*.qivicon.com` | device catalogue: makers, images, instructions | catalogue access of the Home Base, id of the chosen device | only in the pairing assistant |
| `telekom.com`, `*.telekom.com`, `*.qivicon.com` | Magenta sync | Telekom login, heating schedules, home/away | only when set up, then every 5 minutes and after changes |

ComfyOS loads images from the catalogue and passes them on – the browser never talks to Telekom servers itself.

## Security

- The password of the Home Base only goes to addresses in the home network, even if the address comes from a backup. A
  stored password is never sent to a changed address – it has to be entered again then.
- Logins never run in parallel. After a rejected login the plugin pauses (Home Base 5 minutes,
  Telekom 30 minutes), so that neither the Home Base nor the Telekom account gets locked.
- A command is not lost after an expired session: sign in once more, then repeat.

## Files

| File | Content |
|---|---|
| `connector.py` | session, REST proxy of the Home Base, devices, rooms, pairing calls, commands |
| `model.py` | openHAB data → devices (pure functions) |
| `pairing.py` | preparing the pairing catalogue (pure functions) |
| `telekom.py` | Telekom cloud (login, heating plugin through the relay) |
| `magenta.py` | sync rounds between the heating routines of ComfyOS and the Magenta app |
| `routes.py` | routes of the tile and of the pairing assistant (`/api/plugins/qivicon/…`) |
| `static/settings.js`, `static/pairing.js` | tile under System, pairing assistant, actions in the device menu |

## Tests

`python public/plugins/qivicon/tests/test_qivicon_plugin.py` – runs without ComfyOS and without a Home Base. The
interplay with the core is checked by `tests/core/test_devices.py`, `tests/plugins/test_pairing.py` and `tests/plugins/test_hue.py`.

## Limits

- Lamps of the Home Base can only be switched on and off (there is no verified capture for dimmer and colour
  values). Hue lamps run through the Hue plugin with colour and brightness.
- For the cover commands (up/down/stop/position) there is no capture of a real Home Base yet; the
  words come from the openHAB specification.
- Humidity and ambient light (since 1.1.0) are read from channels of these names only – read, never sent.
- Wall buttons of the Home Base report no presses yet: there is no capture of a press. They stay devices without a switch.
