# FRITZ!Box plugin

Brings the smart home devices of a FRITZ!Box into ComfyOS and, if wanted, the presence of phones in its home network. Everything stays in the home network.

## What it can do

- **FRITZ!DECT sockets** (200, 210 …): switch, power and energy.
- **Radiator thermostats** (301, 302, Comet DECT …): room temperature, set point, battery; an open window detected by the thermostat is named in the device sheet.
- **Lamps** (FRITZ!DECT 500): on/off, brightness, white tone and colour.
- **Blinds** (via the box's HAN-FUN blind actuators): position, open, close.
- **Window contacts** (HAN-FUN alarm sensors) as contacts, **temperature and humidity** (FRITZ!DECT 440 and others) as sensors.
- **Buttons** (FRITZ!DECT 400, 440): every press goes to the flows – trigger **"Taster"**. Each button the box lists is a button in ComfyOS; the DECT 400 lists its short and long press as two buttons.
- **Presence** (optional): pick devices of the home network (phones) in the tile – they become presence devices, *present* while they are in the network of the box.

## Setting up

1. In the FRITZ!Box create a user of its own with the right **Smart Home** (System → FRITZ!Box-Benutzer). For presence additionally switch on **"Zugriff für Anwendungen zulassen"** (Heimnetz → Netzwerk → Netzwerkeinstellungen); depending on FRITZ!OS the user then also needs the right **FRITZ!Box Einstellungen**.
2. Under *System → Plugins → FRITZ!Box* enter address (default `fritz.box`), user and password.
3. The devices appear by themselves. With presence switched on: **Geräte im Heimnetz laden**, tick the phones, **Auswahl speichern**.

## How it works

| What | How |
|---|---|
| Login | `login_sid.lua` with challenge: PBKDF2 (FRITZ!OS 7.24 and later) or MD5 – the password never travels in plain text |
| Devices | `getdevicelistinfos` every *n* seconds (setting, default 10) |
| Commands | `setswitchon/off`, `setsimpleonoff`, `setlevelpercentage`, `sethkrtsoll`, `setcolortemperature`, `setunmappedcolor`, `setblind` |
| Buttons | the box notes the time of the last press per button; a newer time is a press. Delay: at most one query interval |
| Presence | TR-064 (port 49000, digest authentication): `GetSpecificHostEntry` per picked device; *away* only after the set time outside the network |

## Safety

- The address must lie in the home network; the password only ever goes there.
- A refused login is never repeated in a loop: the plugin waits at least 60 seconds and honours the block time the box names. Only one login runs at a time.
- Answers of the box are foreign input: XML is limited in size, documents with a DOCTYPE are refused, every value is checked.

## Limits

- Built after AVM's documentation of the AHA-HTTP-Interface and TR-064. **Not checked on a real box yet:** colour lamps, blinds (direction of the position), HAN-FUN contacts and buttons, presence via TR-064.
- A thermostat switched "off" in the box shows 8 °C, "on" (fully open) 28 °C.
- Groups of the box are left out – their members are listed one by one.
- Smoke detectors over HAN-FUN appear as contacts.

## Files

| File | Content |
|---|---|
| `__init__.py` | plugin: login, query rhythm, devices, commands, presence |
| `aha.py` | login, device list and commands of the smart home interface (pure functions) |
| `tr064.py` | presence through the host list (pure functions) |
| `routes.py`, `static/settings.js` | tile |
| `tests/test_fritzbox_plugin.py` | tests against a rebuilt box, without ComfyOS |
