# MQTT plugin

Connects devices that hang on an MQTT broker in the home network. ComfyOS signs in there as one more client, listens to the states and switches through the command topics of the devices.

## What it can do

- **Finding devices by itself**, in three ways:
  - **Home Assistant MQTT discovery** – Zigbee2MQTT, ESPHome and many other bridges. Both forms are read: one message per function and the device form with `components`.
  - **Zigbee2MQTT's own device list** (`<base topic>/bridge/devices`) – works without the Home Assistant integration of Zigbee2MQTT. A device that shows up both ways appears once and keeps its name and room.
  - **Tasmota's own discovery** (`tasmota/discovery/…`, the default of Tasmota with `SetOption19 0`).
- **Own devices:** for everything without discovery (Shelly, own scripts) enter the topics by hand.
- **Device kinds:** lamps (on/off, brightness, white tone, colour), sockets and switches, fans, temperature and humidity, window/door contacts, motion, smoke and presence detectors, radiator thermostats (temperature, target temperature), covers.
- **Further functions:** door locks, choices (e.g. an operating mode), settable numbers, buttons and the speed and mode of a fan appear in the device sheet under "Weitere Funktionen" – each with a fitting control. A lock always asks for confirmation.
- **Extra values:** battery, power and meter reading are attached to their device; ambient light in lux is a value of its own (flows can compare it, since 1.2.0); further readings (voltage …) are in the device sheet.
- **Remotes and wall buttons** (since 1.2.0): the `action` of a Zigbee2MQTT device and Home Assistant event entities become presses of named buttons ("1_single" → button 1, short; "on_hold" → button "An", long – once while held; "…_double" → double). The end of a press ("…_release") is left out, and a retained message is never a press. Flows react with the trigger "Taster".
- **Live:** state changes appear at once. Very chatty devices report to the interface at most twice per second.
- **State after connecting:** Zigbee2MQTT and Tasmota devices that say nothing after connecting are asked once for their state.

## What it deliberately does not do

- **Templates are never evaluated.** Discovery messages contain Jinja2 templates. Only the form `{{ value_json.key }}` is read – as a path into the JSON message. Everything else is listed in the tile under "Nicht unterstützt".
- ComfyOS does not pose as Home Assistant (`homeassistant/status` stays untouched).
- Publishing only happens on command topics a device announced or the user entered, and on the topics that ask a Zigbee2MQTT or Tasmota device for its state (`…/get`, `cmnd/…/STATE`) – never on topics with wildcards.
- The broker must be in the home network; ComfyOS blocks addresses outside.
- Locks that need a code are not supported.

## Setting up

1. Enter address and port of the broker under *System → Plugins → MQTT* (usually `1883`, with TLS `8883`), user and password if needed.
2. **Zigbee2MQTT** needs nothing further: ComfyOS reads its device list. If its base topic is not `zigbee2mqtt`, enter it under "Zigbee2MQTT-Topic". **Tasmota** and **ESPHome** announce themselves once MQTT is set up on the device.
3. Found devices appear under **Neu gefunden** and are adopted one by one or all at once.

Devices without discovery: **Eigenes Gerät anlegen** – name, kind, state topic, for switchable devices the command topic, the values for on/off and for JSON messages the key under which the value is found.

## Settings

| Field | Default | Meaning |
|---|---|---|
| Adresse des Brokers | – | name or IP in the home network; empty = not connected |
| Port | 1883 | |
| Benutzer / Passwort | – | the password lives in the secret store |
| Verschlüsselt verbinden (TLS) | off | |
| Zertifikat des Brokers prüfen | on | only switch off for self-signed certificates |
| Discovery-Präfix | `homeassistant` | topic under which devices announce themselves (Home Assistant discovery) |
| Zigbee2MQTT-Topic | `zigbee2mqtt` | base topic of Zigbee2MQTT; ComfyOS reads the device list there |

## Good to know

- **Flows, scenes and voice assistants** only know the well-known values of a device (on, brightness, temperature …). "Weitere Funktionen" are operated in the device sheet.
- **Multi-gang sockets:** the first switch belongs to the device, every further one appears as a device of its own with the name of the channel.
- **Settings of a device** (child lock, signal strength, firmware update) are not shown.
- **Tasmota:** relays, the light (dimmer, white tone, colour), shutters and the sensors temperature, humidity, power and energy are read. Lights with separate channels (`SetOption68 1`) can only be switched on and off. A device that counts energy offers "Zählerstand zurücksetzen" under "Weitere Funktionen": that publishes `EnergyTotal 0` and the device itself counts from zero again (since 1.3.0).
- **Broker unreachable:** known devices stay visible and show "Kein Kontakt"; ComfyOS reconnects by itself (1 s … 60 s).
- ComfyOS publishes `comfyos/status` = `online` / `offline` (as its last will), so that others see whether it is connected.

## Technology

```
protocol.py     MQTT 3.1.1: building and reading packets (QoS 0 and 1; no QoS 2, no MQTT 5)
client.py       connection: sign in, subscribe, keep alive, reconnect
discovery.py    Home Assistant discovery message -> functions of a device (abbreviations, ~, device form)
zigbee2mqtt.py  device list of a Zigbee2MQTT bridge -> the same functions
tasmota.py      Tasmota's own discovery -> functions, and the translators for Tasmota's messages
translate.py    function + message <-> values and commands of ComfyOS
registry.py     grouping functions into devices, keeping current values, state requests
manual.py       devices described by hand
routes.py       endpoints of the tile
```

The connection is not opened by the plugin but by ComfyOS (`context.open_stream`) – therefore the limits from the manifest apply (`"scope": "lan"`).

## Tests

```
python public/plugins/mqtt/tests/test_mqtt_plugin.py
```

The tests run without ComfyOS against a broker in memory (`tests/fake_broker.py`).
