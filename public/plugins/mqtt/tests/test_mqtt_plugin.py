"""Tests for the MQTT plugin. They run without ComfyOS, against a broker that lives in memory.

    python public/plugins/mqtt/tests/test_mqtt_plugin.py [path-to-comfyos]

Covered: the wire format (protocol.py), Home Assistant discovery (discovery.py), the translation
of lights, switches, sensors, thermostats, covers, fans, locks, selects, numbers and buttons
(translate.py), grouping entities into devices (registry.py), devices described by hand
(manual.py), the device list of a Zigbee2MQTT bridge (zigbee2mqtt.py), Tasmota's own discovery
(tasmota.py) and the plugin itself against the fake broker: device list, live updates, commands,
state requests, reconnect, wrong password.
The interplay with ComfyOS (adopting, device lock, tile) is tested in tests/plugins/test_integrations.py of ComfyOS.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "public" / "sdk"))
sys.path.insert(0, str(ROOT / "public" / "plugins"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from plugin_api import DeviceUnreachable, PluginError  # noqa: E402
from testing import FakeContext  # noqa: E402

import mqtt  # noqa: E402
from fake_broker import FakeBroker  # noqa: E402
from mqtt import client as mqtt_client, discovery, manual, protocol, tasmota, translate, zigbee2mqtt  # noqa: E402
from mqtt.discovery import DeviceInfo, Entity  # noqa: E402
from mqtt.protocol import Message  # noqa: E402
from mqtt.registry import DeviceRegistry  # noqa: E402

# The plugin waits for quiet before it rebuilds its devices and backs off before it reconnects.
# Tests shorten both, so a run takes seconds instead of minutes.
mqtt.SETTLE_SECONDS = 0.05
mqtt.STATE_REQUEST_DELAY_SECONDS = 0.2
mqtt.STATE_REQUEST_GAP_SECONDS = 0.0
mqtt_client.FIRST_RETRY_SECONDS = 0.05


def entity(platform: str, config: dict, device_name: str = "Testgerät", identifier: str = "dev1", name: str = "") -> Entity:
    """Build an entity the way discovery would, from an already expanded configuration."""
    unique_id = config.get("unique_id") or f"{identifier}_{platform}_{name or 'main'}"
    return Entity(unique_id=unique_id, platform=platform, name=name, device=DeviceInfo(identifier=identifier, name=device_name),
                  config=config)


def unsupported_reason(platform: str, config: dict) -> str:
    """Return why an entity is not supported; fail if it is."""
    try:
        translate.translator_for(entity(platform, config))
    except translate.Unsupported as reason:
        return str(reason)
    raise AssertionError(f"{platform} {config} was accepted")


# ---------------------------------------------------------------------------
# 1) protocol.py: the wire format
# ---------------------------------------------------------------------------
for length, encoded in ((0, b"\x00"), (127, b"\x7f"), (128, b"\x80\x01"), (16_383, b"\xff\x7f"), (16_384, b"\x80\x80\x01"),
                        (2_097_152, b"\x80\x80\x80\x01"), (protocol.MAX_REMAINING_LENGTH, b"\xff\xff\xff\x7f")):
    assert protocol.encode_remaining_length(length) == encoded, length
try:
    protocol.encode_remaining_length(protocol.MAX_REMAINING_LENGTH + 1)
    raise AssertionError("an oversized packet was encoded")
except protocol.ProtocolError:
    pass


async def read_back(data: bytes, limit: int = 1024 * 1024) -> protocol.Packet:
    """Feed bytes into a reader and read one packet from it."""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return await protocol.read_packet(reader, limit)


async def check_protocol() -> None:
    """Packets survive the round trip through build and parse: connect with and without login, publish, subscribe,
    limits."""
    will = Message(topic="comfyos/status", payload=b"offline", retain=True)
    request = protocol.parse_connect(await read_back(protocol.build_connect("comfyos-1", 60, "user", "pass" + "word", will)))
    assert (request.client_id, request.keep_alive_seconds, request.user_name) == ("comfyos-1", 60, "user")
    assert request.password == "pass" + "word" and request.will == will
    anonymous = protocol.parse_connect(await read_back(protocol.build_connect("comfyos-2", 30, None, "ignored")))
    assert anonymous.user_name is None and anonymous.password is None and anonymous.will is None   # no password without a user

    plain = Message(topic="a/b", payload=b'{"x": 1}')
    assert protocol.parse_publish(await read_back(protocol.build_publish(plain))) == plain
    confirmed = Message(topic="a/ä", payload=b"", qos=1, retain=True, packet_id=77)
    assert protocol.parse_publish(await read_back(protocol.build_publish(confirmed))) == confirmed
    large = Message(topic="big", payload=b"x" * 20_000)
    assert protocol.parse_publish(await read_back(protocol.build_publish(large))).payload == large.payload
    for broken in (Message(topic="a", payload=b"", qos=2), Message(topic="a", payload=b"", qos=1)):
        try:
            protocol.build_publish(broken)
            raise AssertionError("built an invalid PUBLISH")
        except protocol.ProtocolError:
            pass

    assert protocol.parse_subscribe(await read_back(protocol.build_subscribe(5, ["a/+", "b/#"]))) == (5, ["a/+", "b/#"])
    assert protocol.parse_suback(await read_back(protocol.build_suback(5, [0, 0x80]))) == (5, [0, 0x80])
    assert protocol.parse_connack(await read_back(protocol.build_connack(4))) == 4
    assert protocol.parse_packet_id(await read_back(protocol.build_puback(513))) == 513
    assert (await read_back(protocol.build_pingreq())).packet_type == protocol.PINGREQ
    assert (await read_back(protocol.build_disconnect())).packet_type == protocol.DISCONNECT

    for data, limit in ((protocol.build_publish(large), 1000), (b"\x30\xff\xff\xff\xff\x01", 1 << 30)):
        try:
            await read_back(data, limit)
            raise AssertionError("an oversized or malformed packet was read")
        except protocol.ProtocolError:
            pass
    try:
        await read_back(protocol.build_publish(plain)[:-2])
        raise AssertionError("a cut-off packet was read")
    except asyncio.IncompleteReadError:
        pass
    try:
        protocol.parse_publish(protocol.Packet(protocol.PUBLISH, 0, b"\x00\x09ab"))        # topic length beyond the body
        raise AssertionError("a PUBLISH with a broken topic was read")
    except protocol.ProtocolError:
        pass


asyncio.run(check_protocol())
for topic_filter, topic, expected in (("a/b", "a/b", True), ("a/+", "a/b", True), ("a/+", "a/b/c", False), ("a/#", "a/b/c", True),
                                      ("a/#", "a", True), ("+/+/config", "ha/light/config", True), ("a/b", "a", False),
                                      ("#", "anything/at/all", True), ("a/+/c", "a/b/c", True), ("a/+/c", "a/b/d", False)):
    assert protocol.topic_matches(topic_filter, topic) is expected, (topic_filter, topic)
assert protocol.is_publish_topic("zigbee2mqtt/lamp/set")
assert not any(protocol.is_publish_topic(bad) for bad in ("", "a/+/b", "a/#", "a\x00b"))
print("1 protocol: length field, every packet there and back, size limit, broken packets, topic filters")

# ---------------------------------------------------------------------------
# 2) discovery.py: config messages -> entities
# ---------------------------------------------------------------------------
Z2M_LIGHT = {
    "~": "zigbee2mqtt/Stehlampe", "name": None, "schema": "json", "stat_t": "~", "cmd_t": "~/set", "brightness": True,
    "bri_scl": 254, "sup_clrm": ["xy", "color_temp"], "min_mirs": 150, "max_mirs": 500,
    "uniq_id": "0x00158d0001a2b3c4_light_zigbee2mqtt",
    "avty": [{"t": "zigbee2mqtt/bridge/state", "val_tpl": "{{ value_json.state }}"}],
    "dev": {"ids": ["zigbee2mqtt_0x00158d0001a2b3c4"], "name": "Stehlampe", "mf": "Philips", "mdl": "Hue White and Color", "sa": "Wohnzimmer"},
}
found = discovery.parse_announcement("homeassistant", "homeassistant/light/0x00158d0001a2b3c4/light/config", json.dumps(Z2M_LIGHT).encode())
light_entity = found.entities[0]
assert found.source.endswith("/light/config") and len(found.entities) == 1
assert (light_entity.platform, light_entity.unique_id, light_entity.name) == ("light", "0x00158d0001a2b3c4_light_zigbee2mqtt", "")
assert light_entity.config["state_topic"] == "zigbee2mqtt/Stehlampe" and light_entity.config["command_topic"] == "zigbee2mqtt/Stehlampe/set"
assert light_entity.config["brightness_scale"] == 254 and light_entity.config["supported_color_modes"] == ["xy", "color_temp"]
assert light_entity.config["availability"] == [{"topic": "zigbee2mqtt/bridge/state", "value_template": "{{ value_json.state }}"}]
assert light_entity.device == DeviceInfo("zigbee2mqtt_0x00158d0001a2b3c4", "Stehlampe", "Philips", "Hue White and Color", "Wohnzimmer")
assert "~" not in light_entity.config

# the device form: one message, shared options, one entry removed (only its platform is left)
DEVICE_FORM = {
    "dev": {"ids": "esp-kueche", "name": "Küchensensor"}, "o": {"name": "esphome"}, "stat_t": "kueche/state",
    "cmps": {
        "temp": {"p": "sensor", "dev_cla": "temperature", "unit_of_meas": "°C", "val_tpl": "{{ value_json.t }}", "uniq_id": "kueche_t"},
        "hum": {"p": "sensor", "dev_cla": "humidity", "val_tpl": "{{ value_json.h }}", "uniq_id": "kueche_h"},
        "old": {"p": "sensor"},
    },
}
found = discovery.parse_announcement("homeassistant", "homeassistant/device/esp-kueche/config", json.dumps(DEVICE_FORM).encode())
assert [(item.platform, item.unique_id, item.config["state_topic"]) for item in found.entities] == [
    ("sensor", "kueche_t", "kueche/state"), ("sensor", "kueche_h", "kueche/state")]
assert all(item.device.identifier == "esp-kueche" and item.device.name == "Küchensensor" for item in found.entities)

assert discovery.parse_announcement("homeassistant", "homeassistant/switch/node/plug/config", b"").entities == ()     # removed
assert discovery.parse_announcement("homeassistant", "zigbee2mqtt/Stehlampe", b"{}") is None
assert discovery.parse_announcement("homeassistant", "homeassistant/light/x/config", b"not json") is None
assert discovery.parse_announcement("homeassistant", "homeassistant/light/x/config", b"[1, 2]") is None
assert discovery.split_config_topic("home/ha", "home/ha/light/node/lamp/config") == ("light", "node/lamp")
assert discovery.split_config_topic("homeassistant", "homeassistant/light/config") is None
no_ids = discovery.parse_announcement("homeassistant", "homeassistant/switch/plug/config", b'{"cmd_t": "plug/set"}').entities[0]
assert no_ids.unique_id == "switch/plug" and no_ids.device.identifier == "switch/plug"       # nothing given: the topic names it
assert discovery.discovery_filters("ha") == ["ha/+/+/config", "ha/+/+/+/config"]
print("2 discovery: short keys, base topic, device form, removal, node ids, missing ids, things that are no config")

# ---------------------------------------------------------------------------
# 3) translate.py: templates and payloads
# ---------------------------------------------------------------------------
assert translate.parse_value_template(None) == () and translate.parse_value_template("{{ value }}") == ()
assert translate.parse_value_template("{{ value_json.state }}") == ("state",)
assert translate.parse_value_template("{{value_json.a.b}}") == ("a", "b")
assert translate.parse_value_template("{{ value_json['update']['state'] }}") == ("update", "state")
assert translate.parse_value_template('{{ value_json["list"][1] }}') == ("list", 1)
for unreadable in ("{{ value_json.a | float }}", "{% if value_json.a %}1{% endif %}", "{{ value_json.a + 1 }}", "{{ states('x') }}",
                   "{{ value_json.__class__ }}x", 42):
    try:
        translate.parse_value_template(unreadable)
        raise AssertionError(f"read the template {unreadable!r}")
    except translate.Unsupported:
        pass
assert translate.extract_value('{"a": {"b": [10, 20]}}', ("a", "b", 1)) == 20
assert translate.extract_value('{"a": 1}', ("missing",)) is None and translate.extract_value("no json", ("a",)) is None
assert translate.extract_value("ON", ()) == "ON"
assert translate.render_command_template(None, "21.5") == "21.5"
assert translate.render_command_template('{ "position": {{ position }} }', "40") == '{ "position": 40 }'
assert translate.render_command_template("{{ value }}", "7") == "7"
assert translate.render_command_template("{{ value | int }}", "7") is None and translate.render_command_template("{% set x = 1 %}", "7") is None
assert translate.same_payload(True, "true") and translate.same_payload("ON", "on") and translate.same_payload(False, False)
assert not translate.same_payload("ON", "OFF") and translate.to_number("21.5") == 21.5
assert translate.to_number(True) is None and translate.to_number("nan") is None and translate.to_number("abc") is None
print("3 templates: only a path into the JSON message is read – filters, logic and function calls are refused, never evaluated")

# ---------------------------------------------------------------------------
# 4) translate.py: lights
# ---------------------------------------------------------------------------
lamp = translate.translator_for(light_entity)
assert lamp.kind == "light" and lamp.is_dimmable and lamp.has_color and lamp.white_range == (150, 500)
assert lamp.topics == ["zigbee2mqtt/Stehlampe"] and lamp.state_keys == ("on", "brightness")
reading = lamp.read("zigbee2mqtt/Stehlampe", '{"state": "ON", "brightness": 127, "color_mode": "color_temp", "color_temp": 370}')
assert reading.state == {"on": True, "brightness": 50} and reading.mirek == 370 and reading.color_mode == "ct"
reading = lamp.read("zigbee2mqtt/Stehlampe", '{"state": "OFF", "brightness": 1, "color": {"x": 0.7006, "y": 0.2993}, "color_mode": "xy"}')
assert reading.state == {"on": False, "brightness": 1} and reading.xy == (0.7006, 0.2993) and reading.color_mode == "xy"
assert lamp.read("zigbee2mqtt/Stehlampe", '{"linkquality": 80}').state == {} and lamp.read("zigbee2mqtt/Stehlampe", "garbage").state == {}


def light_command(translator: translate.Translator, state: dict) -> dict:
    """Return the JSON body a light command publishes."""
    messages = translator.commands(state)
    assert len(messages) == 1 and messages[0].topic == "zigbee2mqtt/Stehlampe/set", messages
    return json.loads(messages[0].payload)


assert light_command(lamp, {"on": True}) == {"state": "ON"}
assert light_command(lamp, {"on": False}) == {"state": "OFF"}
assert light_command(lamp, {"brightness": 50}) == {"state": "ON", "brightness": 127}
assert light_command(lamp, {"brightness": 0}) == {"state": "OFF"}
assert light_command(lamp, {"kelvin": 2700}) == {"state": "ON", "color_temp": 370}
assert light_command(lamp, {"mirek": 100}) == {"state": "ON", "color_temp": 150}                    # clamped to the lamp's range
assert light_command(lamp, {"on": True, "color": "#ff0000", "duration": 400}) == {
    "state": "ON", "color": {"x": 0.7006, "y": 0.2993}, "transition": 0.4}
assert light_command(lamp, {"xy": [0.3, 0.3], "brightness": 100}) == {"state": "ON", "brightness": 254, "color": {"x": 0.3, "y": 0.3}}
assert lamp.commands({"effect": "candle"}) == []                                                    # nothing this lamp can do
try:
    lamp.commands({"color": "red"})
    raise AssertionError("accepted a colour that is not #rrggbb")
except PluginError:
    pass

white_lamp = translate.translator_for(entity("light", {**light_entity.config, "supported_color_modes": ["color_temp"]}))
assert not white_lamp.has_color and light_command(white_lamp, {"color": "#ff0000"}) == {"state": "ON", "color_temp": 500}   # nearest white
rgb_lamp = translate.translator_for(entity("light", {**light_entity.config, "supported_color_modes": ["rgb"]}))
assert light_command(rgb_lamp, {"color": "#ff0000"})["color"] == {"r": 255, "g": 0, "b": 0}
assert rgb_lamp.read("zigbee2mqtt/Stehlampe", '{"color": {"r": 255, "g": 0, "b": 0}}').xy == (0.7006, 0.2993)
hs_lamp = translate.translator_for(entity("light", {**light_entity.config, "supported_color_modes": ["hs"]}))
assert light_command(hs_lamp, {"color": "#00ff00"})["color"] == {"h": 120.0, "s": 100.0}
plug_light = translate.translator_for(entity("light", {"schema": "json", "state_topic": "x", "command_topic": "x/set", "supported_color_modes": ["onoff"]}))
assert not plug_light.is_dimmable and plug_light.state_keys == ("on",)

# the basic schema: one topic per property, on/off and brightness are used
BASIC = {"state_topic": "tasmota/stat/POWER", "command_topic": "tasmota/cmnd/POWER", "brightness_state_topic": "tasmota/stat/RESULT",
         "brightness_value_template": "{{ value_json.Dimmer }}", "brightness_command_topic": "tasmota/cmnd/Dimmer", "brightness_scale": 100}
basic_lamp = translate.translator_for(entity("light", BASIC))
assert basic_lamp.is_dimmable and not basic_lamp.has_color and basic_lamp.white_range is None
assert basic_lamp.read("tasmota/stat/POWER", "ON").state == {"on": True}
assert basic_lamp.read("tasmota/stat/RESULT", '{"Dimmer": 40}').state == {"brightness": 40}
assert [(message.topic, message.payload) for message in basic_lamp.commands({"brightness": 60})] == [
    ("tasmota/cmnd/Dimmer", "60"), ("tasmota/cmnd/POWER", "ON")]
assert [(message.topic, message.payload) for message in basic_lamp.commands({"on": False})] == [("tasmota/cmnd/POWER", "OFF")]
assert unsupported_reason("light", {"schema": "template", "state_topic": "x", "command_topic": "y"}) == "Lampe mit Vorlagen-Schema"
print("4 lights: JSON schema (brightness scale, white tone, colour as xy/rgb/hs, fade), white-only lamp, basic schema")

# ---------------------------------------------------------------------------
# 5) translate.py: switch, sensors, thermostat, cover
# ---------------------------------------------------------------------------
plug = translate.translator_for(entity("switch", {"state_topic": "z/plug", "command_topic": "z/plug/set", "value_template": "{{ value_json.state }}",
                                                  "payload_on": "ON", "payload_off": "OFF", "qos": 2, "retain": True}))
assert plug.read("z/plug", '{"state": "ON", "power": 12}').state == {"on": True} and plug.read("z/plug", '{"power": 12}').state == {}
sent = plug.commands({"on": False})[0]
assert (sent.topic, sent.payload, sent.qos, sent.retain) == ("z/plug/set", "OFF", 1, True)          # quality of service 2 is sent as 1
assert plug.commands({"brightness": 5}) == [] and plug.capabilities()[0].writable
read_only = translate.translator_for(entity("switch", {"state_topic": "z/relay"}))
assert not read_only.capabilities()[0].writable and read_only.commands({"on": True}) == []

# Zigbee2MQTT reports a closed window as contact=true: "on" (open) is the payload false
window = translate.translator_for(entity("binary_sensor", {"state_topic": "z/fenster", "device_class": "window",
                                                           "value_template": "{{ value_json.contact }}", "payload_on": False, "payload_off": True}))
assert window.kind == "contact" and window.read("z/fenster", '{"contact": false}').state == {"open": True}
assert window.read("z/fenster", '{"contact": true}').state == {"open": False} and window.read("z/fenster", '{"battery": 90}').state == {}
for device_class, kind, key in (("occupancy", "motion", "motion"), ("smoke", "smoke", "alarm"), ("presence", "presence", "present")):
    sensor = translate.translator_for(entity("binary_sensor", {"state_topic": "t", "device_class": device_class}))
    assert sensor.kind == kind and sensor.read("t", "ON").state == {key: True} and sensor.read("t", "OFF").state == {key: False}
assert "moisture" in unsupported_reason("binary_sensor", {"state_topic": "t", "device_class": "moisture"})
assert unsupported_reason("binary_sensor", {"state_topic": "t", "device_class": "door", "value_template": "{{ value_json.a | int }}"}) == "Vorlage nicht lesbar"
assert unsupported_reason("vacuum", {"state_topic": "t"}) == "Art „vacuum“"


def sensor_reading(device_class: str, unit: str, text: str, name: str = "") -> translate.Reading:
    """Read one message of a sensor with this class and unit."""
    config = {"state_topic": "t", "device_class": device_class, "unit_of_measurement": unit}
    return translate.translator_for(entity("sensor", config, name=name)).read("t", text)


assert sensor_reading("temperature", "°C", "21.46").state == {"temperature": 21.5}
assert sensor_reading("temperature", "°F", "68").state == {"temperature": 20.0}
assert sensor_reading("humidity", "%", "55.44").state == {"humidity": 55.4}
assert sensor_reading("battery", "%", "130").battery == 100 and sensor_reading("battery", "%", "abc").battery is None
assert sensor_reading("power", "kW", "1.5").power_w == 1500.0 and sensor_reading("power", "W", "12.34").power_w == 12.3
assert sensor_reading("energy", "Wh", "2500").energy_kwh == 2.5 and sensor_reading("energy", "kWh", "3.1").energy_kwh == 3.1
assert sensor_reading("illuminance", "lx", "120.0", name="Helligkeit").state == {"illuminance": 120.0}   # a value since 1.2.0
assert sensor_reading("pressure", "hPa", "1013.0", name="Luftdruck").detail == ("Luftdruck", "1013 hPa")
assert sensor_reading("", "", "fast", name="Lüfterstufe").detail == ("Lüfterstufe", "fast")

TRV = {"current_temperature_topic": "z/trv", "current_temperature_template": "{{ value_json.local_temperature }}",
       "temperature_state_topic": "z/trv", "temperature_state_template": "{{ value_json.occupied_heating_setpoint }}",
       "temperature_command_topic": "z/trv/set/occupied_heating_setpoint", "min_temp": 5, "max_temp": 30, "temp_step": 0.5}
thermostat = translate.translator_for(entity("climate", TRV))
assert thermostat.kind == "thermostat" and thermostat.topics == ["z/trv", "z/trv"]
assert thermostat.read("z/trv", '{"local_temperature": 20.3, "occupied_heating_setpoint": 21}').state == {"temperature": 20.3, "target": 21.0}
target = next(capability for capability in thermostat.capabilities() if capability.key == "target")
assert (target.minimum, target.maximum, target.step, target.writable) == (5, 30, 0.5, True)
assert [(message.topic, message.payload) for message in thermostat.commands({"target": 22.5})] == [("z/trv/set/occupied_heating_setpoint", "22.5")]
assert thermostat.commands({"target": 22})[0].payload == "22"
for bad_target in (4, 31, "warm"):
    try:
        thermostat.commands({"target": bad_target})
        raise AssertionError(f"accepted the target {bad_target!r}")
    except PluginError:
        pass
templated = translate.translator_for(entity("climate", {**TRV, "temperature_command_template": '{"t": {{ value }}}'}))
assert templated.commands({"target": 21})[0].payload == '{"t": 21}'
needs_logic = translate.translator_for(entity("climate", {**TRV, "temperature_command_template": "{{ value | round }}"}))
assert needs_logic.commands({"target": 21}) == [] and not needs_logic.capabilities()[1].writable
assert unsupported_reason("climate", {**TRV, "temperature_unit": "F"}) == "Thermostat in Fahrenheit"

# covers: Home Assistant counts 100 as open, ComfyOS counts 100 as closed
SHUTTER = {"position_topic": "z/rollo", "position_template": "{{ value_json.position }}", "state_topic": "z/rollo",
           "value_template": "{{ value_json.state }}", "command_topic": "z/rollo/set", "set_position_topic": "z/rollo/set",
           "set_position_template": '{ "position": {{ position }} }'}
shutter = translate.translator_for(entity("cover", SHUTTER))
assert shutter.read("z/rollo", '{"position": 100, "state": "OPEN"}').state == {"position": 0}
assert shutter.read("z/rollo", '{"position": 25}').state == {"position": 75}
assert [(message.topic, message.payload) for message in shutter.commands({"position": 75})] == [("z/rollo/set", '{ "position": 25 }')]
assert [shutter.commands({"cover": direction})[0].payload for direction in ("up", "down", "stop")] == ["OPEN", "CLOSE", "STOP"]
turned_around = translate.translator_for(entity("cover", {**SHUTTER, "position_open": 0, "position_closed": 100}))
assert turned_around.read("z/rollo", '{"position": 25}').state == {"position": 25}
simple_shutter = translate.translator_for(entity("cover", {"state_topic": "r/state", "command_topic": "r/cmd", "payload_stop": None}))
assert simple_shutter.read("r/state", "closed").state == {"position": 100} and simple_shutter.read("r/state", "open").state == {"position": 0}
assert simple_shutter.commands({"position": 100})[0].payload == "CLOSE"
for impossible in ({"position": 40}, {"cover": "stop"}, {"cover": "sideways"}):
    try:
        simple_shutter.commands(impossible)
        raise AssertionError(f"accepted {impossible}")
    except PluginError:
        pass
print("5 switch (JSON value, read-only), contact with true/false payloads, sensors with unit conversion, thermostat, cover with turned scale")

# ---------------------------------------------------------------------------
# 6) registry.py: entities -> devices
# ---------------------------------------------------------------------------


def announce(registry: DeviceRegistry, platform: str, object_id: str, config: dict) -> None:
    """Announce one entity to the registry the way a retained config message would."""
    topic = f"homeassistant/{platform}/{object_id}/config"
    registry.announce(discovery.parse_announcement("homeassistant", topic, json.dumps(config).encode() if config else b""))


BRIDGE_AVAILABILITY = {"avty": [{"t": "zigbee2mqtt/bridge/state", "val_tpl": "{{ value_json.state }}"}]}
PLUG_DEVICE = {"dev": {"ids": ["zigbee2mqtt_0x00124b0000000001"], "name": "Waschmaschine", "mf": "Nous", "mdl": "A1Z"}}
registry = DeviceRegistry()
announce(registry, "light", "lamp", Z2M_LIGHT)
announce(registry, "switch", "plug", {**PLUG_DEVICE, **BRIDGE_AVAILABILITY, "stat_t": "z/wm", "cmd_t": "z/wm/set", "val_tpl": "{{ value_json.state }}", "uniq_id": "wm_switch"})
announce(registry, "sensor", "plug_power", {**PLUG_DEVICE, "stat_t": "z/wm", "dev_cla": "power", "unit_of_meas": "W", "val_tpl": "{{ value_json.power }}", "uniq_id": "wm_power"})
announce(registry, "sensor", "plug_energy", {**PLUG_DEVICE, "stat_t": "z/wm", "dev_cla": "energy", "unit_of_meas": "kWh", "val_tpl": "{{ value_json.energy }}", "uniq_id": "wm_energy"})
announce(registry, "sensor", "plug_lqi", {**PLUG_DEVICE, "stat_t": "z/wm", "ent_cat": "diagnostic", "val_tpl": "{{ value_json.linkquality }}", "name": "Linkquality", "uniq_id": "wm_lqi"})
announce(registry, "update", "plug_update", {**PLUG_DEVICE, "stat_t": "z/wm", "uniq_id": "wm_update"})
assert registry.rebuild() is True and registry.rebuild() is False
by_name = {device.name: device for device in registry.devices(broker_connected=True)}
assert set(by_name) == {"Stehlampe", "Waschmaschine"}, set(by_name)
washer = by_name["Waschmaschine"]
assert (washer.id, washer.kind, washer.vendor, washer.model) == ("mqtt:zigbee2mqtt_0x00124b0000000001", "switch", "Nous", "A1Z")
assert washer.discovered and washer.link_keys == ["zigbee:00124b0000000001"] and washer.light is None
assert [capability.key for capability in washer.capabilities] == ["on"] and washer.details == {}      # diagnostics are skipped
assert by_name["Stehlampe"].room_hint == "Wohnzimmer" and by_name["Stehlampe"].light.capabilities.white_range == (150, 500)
assert registry.notes == [] and "z/wm" in registry.topics() and "zigbee2mqtt/bridge/state" in registry.topics()

changes = registry.handle_message("z/wm", '{"state": "ON", "power": 812.4, "energy": 3.25, "linkquality": 90}')
assert changes == {washer.id: {"on": True, "power_w": 812.4}}, changes
assert registry.handle_message("z/wm", '{"state": "ON", "power": 812.4}') == {}                    # nothing new
washer = next(device for device in registry.devices(broker_connected=True) if device.name == "Waschmaschine")
assert washer.state == {"on": True} and washer.power_w == 812.4 and washer.energy_kwh == 3.25 and washer.online is True
changes = registry.handle_message("zigbee2mqtt/Stehlampe", '{"state": "ON", "brightness": 254, "color_temp": 250, "color_mode": "color_temp"}')
lamp_id = by_name["Stehlampe"].id
assert changes[lamp_id]["on"] is True and changes[lamp_id]["brightness"] == 100 and changes[lamp_id]["light"].mirek == 250
assert changes[lamp_id]["light"].mode == "ct"

# availability: the bridge goes away -> both devices are unreachable; without the broker everything is offline
assert registry.handle_message("zigbee2mqtt/bridge/state", '{"state": "online"}') == {}             # known as reachable before
changes = registry.handle_message("zigbee2mqtt/bridge/state", '{"state": "offline"}')
assert changes == {lamp_id: {"online": False}, washer.id: {"online": False}}, changes
offline = {device.name: device for device in registry.devices(broker_connected=True)}
assert offline["Waschmaschine"].online is False and "nicht erreichbar" in offline["Waschmaschine"].status_detail
assert registry.handle_message("zigbee2mqtt/bridge/state", "online") == {}                          # not the announced JSON form: ignored
registry.handle_message("zigbee2mqtt/bridge/state", '{"state": "online"}')
assert all(device.online for device in registry.devices(broker_connected=True))
without_broker = registry.devices(broker_connected=False)
assert not any(device.online for device in without_broker) and without_broker[0].status_detail == "MQTT-Broker nicht verbunden"

# values survive a rebuild; a removed config topic removes the device
announce(registry, "sensor", "plug_voltage", {**PLUG_DEVICE, "stat_t": "z/wm", "dev_cla": "voltage", "unit_of_meas": "V", "val_tpl": "{{ value_json.voltage }}", "name": "Spannung", "uniq_id": "wm_voltage"})
assert registry.rebuild() is False                                                                 # same devices, one more entity
registry.handle_message("z/wm", '{"voltage": 231}')
washer = next(device for device in registry.devices(broker_connected=True) if device.name == "Waschmaschine")
assert washer.state == {"on": True} and washer.power_w == 812.4 and washer.details == {"Spannung": "231 V"}
announce(registry, "light", "lamp", {})
assert registry.rebuild() is True and [device.name for device in registry.devices(True)] == ["Waschmaschine"]

# a thermostat that also announces its temperature as a sensor, a battery and a child lock stays one device
registry = DeviceRegistry()
TRV_DEVICE = {"dev": {"ids": ["trv-bad"], "name": "Heizung Bad"}}
announce(registry, "climate", "trv", {**TRV_DEVICE, "uniq_id": "trv_climate", "curr_temp_t": "z/trv", "curr_temp_tpl": "{{ value_json.local_temperature }}",
                                      "temp_stat_t": "z/trv", "temp_stat_tpl": "{{ value_json.occupied_heating_setpoint }}",
                                      "temp_cmd_t": "z/trv/set/occupied_heating_setpoint"})
announce(registry, "sensor", "trv_t", {**TRV_DEVICE, "uniq_id": "trv_temp", "stat_t": "z/trv", "dev_cla": "temperature", "val_tpl": "{{ value_json.local_temperature }}"})
announce(registry, "sensor", "trv_b", {**TRV_DEVICE, "uniq_id": "trv_battery", "stat_t": "z/trv", "dev_cla": "battery", "ent_cat": "diagnostic", "val_tpl": "{{ value_json.battery }}"})
announce(registry, "switch", "trv_lock", {**TRV_DEVICE, "uniq_id": "trv_lock", "stat_t": "z/trv", "cmd_t": "z/trv/set", "ent_cat": "config", "name": "Child lock"})
registry.rebuild()
registry.handle_message("z/trv", '{"local_temperature": 19.5, "occupied_heating_setpoint": 21, "battery": 64, "child_lock": "LOCK"}')
(heater,) = registry.devices(broker_connected=True)
assert (heater.name, heater.kind, heater.battery) == ("Heizung Bad", "thermostat", 64)
assert heater.state == {"temperature": 19.5, "target": 21.0} and [capability.key for capability in heater.capabilities] == ["temperature", "target"]
assert "on" not in heater.state, "a setting of the device (child lock) became its main switch"

# a double socket: the second switch becomes its own device; what cannot be read is noted, a lone battery is not a device
registry = DeviceRegistry()
STRIP = {"dev": {"ids": ["strip1"], "name": "Steckerleiste"}}
announce(registry, "switch", "strip_l1", {**STRIP, "uniq_id": "strip_l1", "stat_t": "z/strip", "cmd_t": "z/strip/l1/set", "val_tpl": "{{ value_json.state_l1 }}", "name": "L1"})
announce(registry, "switch", "strip_l2", {**STRIP, "uniq_id": "strip_l2", "stat_t": "z/strip", "cmd_t": "z/strip/l2/set", "val_tpl": "{{ value_json.state_l2 }}", "name": "L2"})
announce(registry, "sensor", "strip_power", {**STRIP, "uniq_id": "strip_power", "stat_t": "z/strip", "dev_cla": "power", "unit_of_meas": "W", "val_tpl": "{{ value_json.power }}"})
announce(registry, "vacuum", "fan1", {"dev": {"ids": ["fan1"], "name": "Saugroboter"}, "uniq_id": "fan1", "stat_t": "z/fan", "cmd_t": "z/fan/set"})
announce(registry, "sensor", "odd", {"dev": {"ids": ["odd"], "name": "Wetterstation"}, "uniq_id": "odd_t", "stat_t": "z/odd", "dev_cla": "temperature", "name": "Außen",
                                     "val_tpl": "{{ (value_json.t | float) / 10 }}"})
announce(registry, "sensor", "remote_b", {"dev": {"ids": ["remote"], "name": "Fernbedienung"}, "uniq_id": "remote_b", "stat_t": "z/remote", "dev_cla": "battery"})
registry.rebuild()
strip = {device.id: device for device in registry.devices(broker_connected=True)}
assert set(strip) == {"mqtt:strip1", "mqtt:strip1:strip_l2"}, set(strip)
assert strip["mqtt:strip1"].name == "Steckerleiste" and strip["mqtt:strip1:strip_l2"].name == "Steckerleiste L2"
changes = registry.handle_message("z/strip", '{"state_l1": "ON", "state_l2": "OFF", "power": 40}')
assert changes == {"mqtt:strip1": {"on": True, "power_w": 40.0}, "mqtt:strip1:strip_l2": {"on": False}}, changes
second = registry.find("mqtt:strip1:strip_l2")
assert [(message.topic, message.payload) for message in registry.commands(second, {"on": True})] == [("z/strip/l2/set", "ON")]
assert registry.assume(second, {"on": True}) == {"on": True}
try:
    registry.commands(second, {"target": 21})
    raise AssertionError("a socket accepted a temperature")
except PluginError:
    pass
assert registry.notes == ["Saugroboter: vacuum – Art „vacuum“", "Wetterstation: Außen – Vorlage nicht lesbar"], registry.notes
print("6 registry: one device per physical device, extra values (power, battery, readings), second switch = own device, "
      "availability, values survive a rebuild, notes for what is not supported")

# ---------------------------------------------------------------------------
# 7) manual.py: devices described by hand
# ---------------------------------------------------------------------------
shelly = manual.checked("abcd1234", {"name": "  Shelly   Küche ", "kind": "switch", "state_topic": "shellies/kueche/relay/0",
                                     "command_topic": "shellies/kueche/relay/0/command", "payload_on": "on", "payload_off": "off"})
assert shelly.name == "Shelly Küche" and manual.load(manual.dump([shelly])) == [shelly]
shelly_switch = translate.translator_for(manual.to_entity(shelly))
assert shelly_switch.read("shellies/kueche/relay/0", "on").state == {"on": True}
assert shelly_switch.commands({"on": False})[0].payload == "off"
thermometer = manual.checked("ffff0000", {"name": "Keller", "kind": "temperature", "state_topic": "keller/sensor", "json_key": "env.temperature",
                                          "command_topic": "ignored/for/sensors"})
assert thermometer.command_topic == "" and manual.to_entity(thermometer).config["value_template"] == "{{ value_json.env.temperature }}"
assert translate.translator_for(manual.to_entity(thermometer)).read("keller/sensor", '{"env": {"temperature": 12.34}}').state == {"temperature": 12.3}
door = translate.translator_for(manual.to_entity(manual.checked("0000aaaa", {"name": "Tür", "kind": "contact", "state_topic": "tuer", "payload_on": "open", "payload_off": "closed"})))
assert door.kind == "contact" and door.read("tuer", "open").state == {"open": True}
for bad_input, word in (({"name": "", "kind": "switch", "state_topic": "a"}, "Namen"), ({"name": "X", "kind": "rocket", "state_topic": "a"}, "Geräteart"),
                        ({"name": "X", "kind": "switch"}, "Topic"), ({"name": "X", "kind": "temperature", "command_topic": "a"}, "Zustands-Topic"),
                        ({"name": "X", "kind": "switch", "state_topic": "a/#"}, "Topic"), ({"name": "X", "kind": "switch", "state_topic": "a/+/b"}, "Topic"),
                        ({"name": "X", "kind": "temperature", "state_topic": "a", "json_key": "a | float"}, "JSON-Schlüssel"),
                        ({"name": "X", "kind": "temperature", "state_topic": "a", "json_key": "__class__.__init__ }}{{ x"}, "JSON-Schlüssel")):
    try:
        manual.checked("abcd1234", bad_input)
        raise AssertionError(f"accepted {bad_input}")
    except PluginError as error:
        assert word in str(error), (bad_input, str(error))
assert manual.load([{"key": "x", "name": "bad key", "kind": "switch", "state_topic": "a"}, "junk", None]) == []
print("7 manual devices: topics and payloads checked, JSON key becomes a path (nothing else fits), stored and loaded")


# ---------------------------------------------------------------------------
# 8) translate.py: fans, locks, selects, numbers and buttons
# ---------------------------------------------------------------------------
def sent(messages: list) -> list[tuple[str, str]]:
    """Return the messages of a command as ``(topic, payload)`` pairs."""
    return [(message.topic, message.payload) for message in messages]


def refuses(translator: translate.Translator, state: dict) -> bool:
    """True if the translator rejects the command with a message for the user."""
    try:
        translator.commands(state)
    except PluginError:
        return True
    return False


fan = translate.translator_for(entity("fan", {
    "state_topic": "fan/state", "command_topic": "fan/set", "percentage_state_topic": "fan/speed",
    "percentage_command_topic": "fan/speed/set", "speed_range_max": 4, "preset_modes": ["auto", "sleep"],
    "preset_mode_state_topic": "fan/mode", "preset_mode_command_topic": "fan/mode/set"}))
speed_key, mode_key = fan.state_keys[1], fan.state_keys[2]
assert fan.kind == "switch" and fan.state_keys[0] == "on" and speed_key.startswith("speed_") and mode_key.startswith("mode_")
assert fan.topics == ["fan/state", "fan/speed", "fan/mode"]
assert fan.read("fan/state", "ON").state == {"on": True} and fan.read("fan/state", "OFF").state == {"on": False}
assert fan.read("fan/speed", "2").state == {speed_key: 50} and fan.read("fan/speed", "0").state == {speed_key: 0}
assert fan.read("fan/mode", "sleep").state == {mode_key: "sleep"} and fan.read("fan/mode", "turbo").state == {}
assert [(capability.key, capability.type, capability.label, capability.writable) for capability in fan.capabilities()] == [
    ("on", "bool", None, True), (speed_key, "number", "Stufe", True), (mode_key, "enum", "Modus", True)]
assert fan.capabilities()[2].options == ("auto", "sleep")
assert sent(fan.commands({"on": False})) == [("fan/set", "OFF")]
assert sent(fan.commands({speed_key: 75})) == [("fan/speed/set", "3")] and sent(fan.commands({speed_key: 0})) == [("fan/speed/set", "0")]
assert sent(fan.commands({speed_key: 1})) == [("fan/speed/set", "1")]                 # 1 % already is the slowest speed
assert sent(fan.commands({mode_key: "auto"})) == [("fan/mode/set", "auto")]
assert refuses(fan, {mode_key: "turbo"}) and refuses(fan, {speed_key: 120})
assert translate.translator_for(entity("fan", {"command_topic": "f/set"})).state_keys == ("on",)

lock = translate.translator_for(entity("lock", {"state_topic": "door", "command_topic": "door/set", "value_template": "{{ value_json.state }}"}))
lock_key = lock.state_keys[0]
assert lock.kind == "unknown" and lock_key.startswith("locked_")
assert lock.read("door", '{"state": "LOCKED"}').state == {lock_key: True}
assert lock.read("door", '{"state": "UNLOCKED"}').state == {lock_key: False} and lock.read("door", '{"state": "JAMMED"}').state == {}
assert sent(lock.commands({lock_key: False})) == [("door/set", "UNLOCK")] and sent(lock.commands({lock_key: True})) == [("door/set", "LOCK")]
assert lock.commands({"on": False}) == [], "switching things off must never reach a lock"
assert [(capability.type, capability.label, capability.writable) for capability in lock.capabilities()] == [("bool", "Verriegelt", True)]
assert unsupported_reason("lock", {"command_topic": "door/set", "code_format": "^\\d{4}$"}) == "Schloss mit Code"

select = translate.translator_for(entity("select", {"state_topic": "v", "value_template": "{{ value_json.mode }}", "command_topic": "v/set",
                                                    "options": ["eco", "boost", "eco", 5]}, name="Betriebsart"))
select_key = select.state_keys[0]
assert select_key.startswith("betriebsart_") and select.capabilities()[0].options == ("eco", "boost", "5")
assert select.capabilities()[0].label == "Betriebsart" and select.capabilities()[0].type == "enum"
assert select.read("v", '{"mode": "boost"}').state == {select_key: "boost"} and select.read("v", '{"mode": "x"}').state == {}
assert sent(select.commands({select_key: "eco"})) == [("v/set", "eco")] and refuses(select, {select_key: "x"})
assert unsupported_reason("select", {"command_topic": "v/set"}) == "Auswahl ohne Werte"

number = translate.translator_for(entity("number", {"state_topic": "n", "command_topic": "n/set", "min": 0, "max": 30, "step": 0.5,
                                                    "unit_of_measurement": "s", "command_template": '{"delay": {{ value }}}'}, name="Nachlauf"))
number_key = number.state_keys[0]
number_capability = number.capabilities()[0]
assert (number_capability.minimum, number_capability.maximum, number_capability.step, number_capability.unit) == (0.0, 30.0, 0.5, "s")
assert number_capability.label == "Nachlauf" and number_capability.writable
assert number.read("n", "12.0").state == {number_key: 12} and number.read("n", "soon").state == {}
assert sent(number.commands({number_key: 7.5})) == [("n/set", '{"delay": 7.5}')] and refuses(number, {number_key: 31})
read_only_number = translate.translator_for(entity("number", {"state_topic": "n", "command_topic": "n/set", "command_template": "{{ value | int }}"}))
assert read_only_number.capabilities()[0].writable is False and read_only_number.commands({read_only_number.state_keys[0]: 5}) == []
assert unsupported_reason("number", {"state_topic": "n", "min": 5, "max": 5}) == "Zahl ohne Wertebereich"

button = translate.translator_for(entity("button", {"command_topic": "b/set", "payload_press": "identify"}, name="Blinken"))
button_capability = button.capabilities()[0]
assert (button_capability.type, button_capability.readable, button_capability.writable, button_capability.label) == ("action", False, True, "Blinken")
assert button.state_keys == () and sent(button.commands({button_capability.key: True})) == [("b/set", "identify")]
assert button.commands({button_capability.key: False}) == [] and unsupported_reason("button", {"state_topic": "b"}) == "ohne Topic"

first, second = entity("select", {"unique_id": "a"}), entity("select", {"unique_id": "b"})
assert translate.value_key(first, "Mode") == translate.value_key(first, "Mode") != translate.value_key(second, "Mode")
assert translate.value_key(first, "§§").startswith("value_")

registry = DeviceRegistry()
AIR = {"dev": {"ids": ["air1"], "name": "Luftreiniger"}}
announce(registry, "lock", "door", {"dev": {"ids": ["nuki1"], "name": "Haustür"}, "uniq_id": "nuki_lock", "stat_t": "nuki/state", "cmd_t": "nuki/set"})
announce(registry, "switch", "purifier", {**AIR, "uniq_id": "air_switch", "stat_t": "air/state", "cmd_t": "air/set"})
announce(registry, "select", "purifier_mode", {**AIR, "uniq_id": "air_mode", "name": "Modus", "stat_t": "air/mode", "cmd_t": "air/mode/set", "ops": ["auto", "night"]})
announce(registry, "button", "purifier_reset", {**AIR, "uniq_id": "air_reset", "name": "Filter zurücksetzen", "cmd_t": "air/reset", "ent_cat": "config"})
registry.rebuild()
by_name = {device.name: device for device in registry.devices(broker_connected=True)}
assert by_name["Haustür"].kind == "unknown" and by_name["Luftreiniger"].kind == "switch" and registry.notes == []
purifier = by_name["Luftreiniger"]
assert [capability.label for capability in purifier.capabilities] == ["Modus", None]       # the settings button is left out
purifier_mode_key = purifier.capabilities[0].key
assert registry.handle_message("air/mode", "night") == {purifier.id: {purifier_mode_key: "night"}}
tracked_purifier = registry.find(purifier.id)
assert sent(registry.commands(tracked_purifier, {purifier_mode_key: "auto"})) == [("air/mode/set", "auto")]
assert registry.assume(tracked_purifier, {purifier_mode_key: "auto"}) == {purifier_mode_key: "auto"}
print("8 fans, locks, selects, numbers, buttons: labelled capabilities with stable keys, ranges and options checked, "
      "a lock ignores on/off, a device of only such functions is shown")


# ---------------------------------------------------------------------------
# 9) zigbee2mqtt.py: the device list of a bridge
# ---------------------------------------------------------------------------
Z2M_DEVICES = [
    {"ieee_address": "0x00124b0000000000", "type": "Coordinator", "friendly_name": "Coordinator"},
    {"ieee_address": "0x00158d0001a2b3c4", "type": "Router", "friendly_name": "Stehlampe",
     "definition": {"vendor": "Philips", "model": "9290022166", "exposes": [
         {"type": "light", "features": [
             {"type": "binary", "name": "state", "property": "state", "access": 7, "value_on": "ON", "value_off": "OFF"},
             {"type": "numeric", "name": "brightness", "property": "brightness", "access": 7, "value_min": 0, "value_max": 254},
             {"type": "numeric", "name": "color_temp", "property": "color_temp", "access": 7, "value_min": 153, "value_max": 454},
             {"type": "composite", "name": "color_xy", "property": "color", "access": 7}]},
         {"type": "numeric", "name": "linkquality", "property": "linkquality", "access": 1, "category": "diagnostic", "unit": "lqi"}]}},
    {"ieee_address": "0x00124b0000000002", "type": "EndDevice", "friendly_name": "Küche/Fenster",
     "definition": {"vendor": "Aqara", "model": "MCCGQ11LM", "exposes": [
         {"type": "binary", "name": "contact", "property": "contact", "access": 1, "value_on": False, "value_off": True},
         {"type": "numeric", "name": "battery", "property": "battery", "access": 1, "unit": "%"},
         {"type": "numeric", "name": "device_temperature", "property": "device_temperature", "access": 1, "unit": "°C", "label": "Device temperature"},
         {"type": "enum", "name": "action", "property": "action", "access": 1, "values": ["single"]}]}},
    {"ieee_address": "0x00124b0000000003", "type": "Router", "friendly_name": "Bad Thermostat",
     "definition": {"vendor": "Danfoss", "model": "014G2461", "exposes": [
         {"type": "climate", "features": [
             {"type": "numeric", "name": "occupied_heating_setpoint", "property": "occupied_heating_setpoint", "access": 7,
              "value_min": 5, "value_max": 35, "value_step": 0.5},
             {"type": "numeric", "name": "local_temperature", "property": "local_temperature", "access": 5}]},
         {"type": "enum", "name": "preset", "property": "preset", "access": 7, "values": ["manual", "schedule"], "label": "Preset"},
         {"type": "binary", "name": "child_lock", "property": "child_lock", "access": 7, "category": "config",
          "value_on": "LOCK", "value_off": "UNLOCK"}]}},
    {"ieee_address": "0x00124b0000000004", "type": "Router", "friendly_name": "Abgeschaltet", "disabled": True,
     "definition": {"exposes": [{"type": "switch", "features": [{"type": "binary", "name": "state", "property": "state", "access": 7}]}]}},
    {"ieee_address": "not-an-address", "type": "Router", "friendly_name": "Kaputt", "definition": {"exposes": []}},
    {"ieee_address": "0x00124b0000000005", "type": "Router", "friendly_name": "Unbekannt", "definition": None},
    "junk",
]
listed = zigbee2mqtt.parse_devices("zigbee2mqtt", json.dumps(Z2M_DEVICES).encode())
assert listed.is_fallback and listed.source == "zigbee2mqtt/bridge/devices"
assert {found.device.name for found in listed.entities} == {"Stehlampe", "Küche/Fenster", "Bad Thermostat"}
assert [(request.topic, json.loads(request.payload)) for request in listed.state_requests] == [
    ("zigbee2mqtt/Stehlampe/get", {"state": "", "brightness": "", "color_temp": "", "color": ""}),
    ("zigbee2mqtt/Bad Thermostat/get", {"occupied_heating_setpoint": "", "local_temperature": "", "preset": "", "child_lock": ""})]
assert zigbee2mqtt.parse_devices("zigbee2mqtt", b"{}") is None and zigbee2mqtt.parse_devices("zigbee2mqtt", b"not json") is None
assert zigbee2mqtt.parse_devices("zigbee2mqtt", b"").entities == ()

registry = DeviceRegistry()
registry.announce(listed)
registry.rebuild()
by_name = {device.name: device for device in registry.devices(broker_connected=True)}
assert set(by_name) == {"Stehlampe", "Küche/Fenster", "Bad Thermostat"} and registry.notes == []
lamp, window, thermostat = by_name["Stehlampe"], by_name["Küche/Fenster"], by_name["Bad Thermostat"]
assert (lamp.id, lamp.kind, lamp.vendor, lamp.link_keys) == ("mqtt:zigbee2mqtt_0x00158d0001a2b3c4", "light", "Philips", ["zigbee:00158d0001a2b3c4"])
assert lamp.light.capabilities.white_range == (153, 454) and lamp.light.capabilities.color
assert window.kind == "contact" and thermostat.kind == "thermostat"
# devices that have said nothing yet are asked for their state, each one once; the window cannot be asked
assert [request.topic for request in registry.due_state_requests()] == ["zigbee2mqtt/Stehlampe/get", "zigbee2mqtt/Bad Thermostat/get"]
assert registry.due_state_requests() == []

changes = registry.handle_message("zigbee2mqtt/Stehlampe", '{"state": "ON", "brightness": 127, "color_mode": "xy", "color": {"x": 0.4, "y": 0.4}}')
assert changes[lamp.id]["on"] is True and changes[lamp.id]["brightness"] == 50 and changes[lamp.id]["light"].mode == "xy"
lamp_command = registry.commands(registry.find(lamp.id), {"on": True, "brightness": 50})
assert [message.topic for message in lamp_command] == ["zigbee2mqtt/Stehlampe/set"] and json.loads(lamp_command[0].payload) == {"state": "ON", "brightness": 127}
assert registry.handle_message("zigbee2mqtt/Küche/Fenster", '{"contact": false, "battery": 87, "device_temperature": 23}') == {window.id: {"open": True}}
window = next(device for device in registry.devices(broker_connected=True) if device.id == window.id)
assert window.battery == 87 and window.details == {"Device temperature": "23 °C"}
changes = registry.handle_message("zigbee2mqtt/Bad Thermostat", '{"local_temperature": 20.5, "occupied_heating_setpoint": 21, "preset": "manual", "child_lock": "UNLOCK"}')
preset_key = next(capability.key for capability in thermostat.capabilities if capability.label == "Preset")
assert changes == {thermostat.id: {"temperature": 20.5, "target": 21.0, preset_key: "manual"}}, changes      # the child lock is a setting
tracked_thermostat = registry.find(thermostat.id)
assert sent(registry.commands(tracked_thermostat, {"target": 22.5})) == [("zigbee2mqtt/Bad Thermostat/set/occupied_heating_setpoint", "22.5")]
assert sent(registry.commands(tracked_thermostat, {preset_key: "schedule"})) == [("zigbee2mqtt/Bad Thermostat/set/preset", "schedule")]
# the bridge reports its state as JSON or as plain text, depending on its version
assert registry.handle_message("zigbee2mqtt/bridge/state", '{"state": "offline"}')[lamp.id] == {"online": False}
assert registry.handle_message("zigbee2mqtt/bridge/state", "online")[lamp.id] == {"online": True}
assert registry.handle_message("zigbee2mqtt/Stehlampe/availability", '{"state": "offline"}') == {lamp.id: {"online": False}}
registry.forget_state_requests()
assert [request.topic for request in registry.due_state_requests()] == [], "devices with a known state are not asked"

# the same lamp through Home Assistant discovery: that description wins, nothing appears twice
announce(registry, "light", "lamp", Z2M_LIGHT)
registry.rebuild()
lamps = [device for device in registry.devices(broker_connected=True) if device.id == lamp.id]
assert len(lamps) == 1 and lamps[0].light.capabilities.white_range == (150, 500) and len(registry.devices(broker_connected=True)) == 3
assert lamps[0].state["on"] is True, "values survive the change of the source"
print("9 Zigbee2MQTT device list: exposes become devices (light, contact, thermostat, select), settings and events left out, "
      "state requests once per silent device, availability as JSON or text, Home Assistant discovery wins")

# ---------------------------------------------------------------------------
# 9b) remotes and ambient light (plugin 1.2.0, API revision 7)
# ---------------------------------------------------------------------------
for action, expected in (("1_single", ("1", "short")), ("button_2_double", ("button_2", "double")), ("on_hold", ("on", "long")),
                         ("brightness_up_click", ("brightness_up", "short")), ("single", ("taste", "short")),
                         ("on", ("on", "short")), ("on_press_release", None), ("brightness_stop", None), ("1_triple", None),
                         ("", None), (None, None), ("x" * 61, None)):
    assert translate.split_action(action) == expected, (action, translate.split_action(action))
assert [translate.button_name(b) for b in ("1", "button_3", "taste", "brightness_down", "arrow_left", "scene_a")] == [
    "Taste 1", "Taste 3", "Taste", "Dunkler", "Pfeil links", "Scene a"]
REMOTE_AND_SENSOR = [
    {"ieee_address": "0x00124b0000000009", "type": "EndDevice", "friendly_name": "Fernbedienung",
     "definition": {"vendor": "IKEA", "model": "E1743", "exposes": [
         {"type": "enum", "name": "action", "property": "action", "access": 1,
          "values": ["on", "off", "brightness_move_up", "brightness_stop", "1_double", "1_hold", "1_hold_release"]},
         {"type": "numeric", "name": "battery", "property": "battery", "access": 1, "unit": "%"}]}},
    {"ieee_address": "0x00124b000000000a", "type": "EndDevice", "friendly_name": "Bewegung Flur",
     "definition": {"vendor": "Philips", "model": "9290012607", "exposes": [
         {"type": "binary", "name": "occupancy", "property": "occupancy", "access": 1, "value_on": True, "value_off": False},
         {"type": "numeric", "name": "illuminance", "property": "illuminance", "access": 1},
         {"type": "numeric", "name": "illuminance_lux", "property": "illuminance_lux", "access": 1, "unit": "lx"}]}}]
remotes = DeviceRegistry()
remotes.announce(zigbee2mqtt.parse_devices("zigbee2mqtt", json.dumps(REMOTE_AND_SENSOR).encode()))
remotes.rebuild()
by_name = {device.name: device for device in remotes.devices(broker_connected=True)}
remote, motion_sensor = by_name["Fernbedienung"], by_name["Bewegung Flur"]
assert remote.kind == "button" and [(b.id, b.name) for b in remote.buttons] == [
    ("on", "An"), ("off", "Aus"), ("brightness_move_up", "Brightness move up"), ("1", "Taste 1")], remote.buttons
assert motion_sensor.kind == "motion" and any(c.key == "illuminance" and c.unit == "lx" for c in motion_sensor.capabilities)
remotes.handle_message("zigbee2mqtt/Fernbedienung", '{"action": "1_double", "battery": 80}')
assert remotes.take_presses() == [(remote.id, "1", "double")] and remotes.take_presses() == []
remotes.handle_message("zigbee2mqtt/Fernbedienung", '{"action": "on"}', retained=True)          # an old state the broker kept
remotes.handle_message("zigbee2mqtt/Fernbedienung", '{"action": ""}')
remotes.handle_message("zigbee2mqtt/Fernbedienung", '{"battery": 79}')
assert remotes.take_presses() == [], "a retained message, an empty action or another value is no press"
for _ in range(3):                                                                                 # a held button repeats
    remotes.handle_message("zigbee2mqtt/Fernbedienung", '{"action": "1_hold"}')
remotes.handle_message("zigbee2mqtt/Fernbedienung", '{"action": "1_hold_release"}')
assert remotes.take_presses() == [(remote.id, "1", "long")]
changes = remotes.handle_message("zigbee2mqtt/Bewegung Flur", '{"occupancy": true, "illuminance": 21000, "illuminance_lux": 125}')
assert changes == {motion_sensor.id: {"motion": True, "illuminance": 125.0}}, changes             # the raw value is no lux
# Home Assistant's "event" platform: the message carries the event type; the native list adds no second remote
announce(remotes, "event", "taster", {"dev": {"ids": ["zigbee2mqtt_0x00124b000000000b"], "name": "Taster Küche"},
                                       "stat_t": "zigbee2mqtt/Taster Küche/action_event", "evt_typ": ["press", "double_press"],
                                       "uniq_id": "taster_event"})
remotes.rebuild()
taster = next(device for device in remotes.devices(broker_connected=True) if device.name == "Taster Küche")
assert taster.kind == "button" and [(b.id, b.name) for b in taster.buttons] == [("taste", "Taste")]
remotes.handle_message("zigbee2mqtt/Taster Küche/action_event", '{"event_type": "double_press"}')
assert remotes.take_presses() == [(taster.id, "taste", "double")]
announce(remotes, "event", "kaputt", {"dev": {"ids": ["x1"], "name": "Ohne Topic"}, "uniq_id": "no_topic"})
remotes.rebuild()
assert any("Ohne Topic" in note for note in remotes.notes), remotes.notes
print("9b remotes: actions become presses of named buttons (short, long once while held, double), never from retained "
      "messages; Home Assistant events; ambient light in lux from the lux value only")


# ---------------------------------------------------------------------------
# 10) tasmota.py: Tasmota's own discovery
# ---------------------------------------------------------------------------
TASMOTA_CONFIG = {"ip": "192.0.2.31", "dn": "Tasmota Küche", "fn": ["Licht", "Abzug", None], "hn": "tasmota-kueche", "mac": "AABBCCDDEEFF",
                  "md": "Sonoff Dual R3", "ofln": "Offline", "onln": "Online", "state": ["OFF", "ON", "TOGGLE", "HOLD"], "t": "kueche",
                  "ft": "%prefix%/%topic%/", "tp": ["cmnd", "stat", "tele"], "rl": [2, 1, 0], "so": {"68": 0}, "lt_st": 5, "ver": 1}
TASMOTA_SENSORS = {"sn": {"Time": "2026-01-01T00:00:00", "AM2301": {"Temperature": 21.5, "Humidity": 48.0},
                          "ENERGY": {"Total": 3.2, "Power": 12}, "TempUnit": "C"}, "ver": 1}
TASMOTA_CONFIG_TOPIC = "tasmota/discovery/AABBCCDDEEFF/config"
TASMOTA_SENSORS_TOPIC = "tasmota/discovery/AABBCCDDEEFF/sensors"
found_devices = tasmota.TasmotaDiscovery()
assert found_devices.is_discovery_topic(TASMOTA_CONFIG_TOPIC) and not found_devices.is_discovery_topic("tasmota/discovery/x/config")
assert found_devices.handle_message(TASMOTA_SENSORS_TOPIC, json.dumps(TASMOTA_SENSORS).encode()).entities == ()    # sensors alone say nothing
announced = found_devices.handle_message(TASMOTA_CONFIG_TOPIC, json.dumps(TASMOTA_CONFIG).encode())
assert [(found.platform, found.name) for found in announced.entities] == [
    ("tasmota_light", ""), ("tasmota_relay", "Abzug"), ("sensor", "AM2301 Temperature"), ("sensor", "AM2301 Humidity"),
    ("sensor", "ENERGY Power"), ("sensor", "ENERGY Total")]
assert announced.state_requests == (discovery.StateRequest("tasmota_AABBCCDDEEFF", "cmnd/kueche/STATE"),) and not announced.is_fallback
assert found_devices.handle_message("homeassistant/switch/x/config", b"{}") is None
assert found_devices.handle_message(TASMOTA_CONFIG_TOPIC, b"[1]") is None and found_devices.handle_message(TASMOTA_CONFIG_TOPIC, b"{nope") is None
unusable = tasmota.TasmotaDiscovery().handle_message(TASMOTA_CONFIG_TOPIC, json.dumps({**TASMOTA_CONFIG, "t": "kueche/#"}).encode())
assert unusable.entities == (), "a topic with a wildcard is never used"

registry = DeviceRegistry()
registry.announce(announced)
registry.rebuild()
by_name = {device.name: device for device in registry.devices(broker_connected=True)}
assert set(by_name) == {"Tasmota Küche", "Tasmota Küche Abzug"} and registry.notes == []
kitchen, hood = by_name["Tasmota Küche"], by_name["Tasmota Küche Abzug"]
assert (kitchen.id, kitchen.kind, kitchen.vendor, kitchen.model) == ("mqtt:tasmota_AABBCCDDEEFF", "light", "Tasmota", "Sonoff Dual R3")
assert kitchen.light.capabilities.white_range == (153, 500) and kitchen.light.capabilities.color and hood.kind == "switch"
changes = registry.handle_message("stat/kueche/RESULT", '{"POWER1": "ON", "Dimmer": 60, "HSBColor": "120,100,60", "White": 0, "CT": 300}')
assert set(changes) == {kitchen.id} and changes[kitchen.id]["on"] is True and changes[kitchen.id]["brightness"] == 60
assert changes[kitchen.id]["light"].mode == "xy"
assert registry.handle_message("tele/kueche/STATE", '{"POWER2": "ON"}') == {hood.id: {"on": True}}
assert registry.handle_message("tele/kueche/SENSOR", '{"AM2301": {"Temperature": 22.4, "Humidity": 51}, "ENERGY": {"Total": 3.25, "Power": 14}}') == {
    kitchen.id: {"temperature": 22.4, "humidity": 51.0, "power_w": 14.0}}
assert next(device for device in registry.devices(broker_connected=True) if device.id == kitchen.id).energy_kwh == 3.25
tracked_kitchen, tracked_hood = registry.find(kitchen.id), registry.find(hood.id)
assert sent(registry.commands(tracked_kitchen, {"on": True, "brightness": 40})) == [("cmnd/kueche/Dimmer", "40")]
assert sent(registry.commands(tracked_kitchen, {"on": False})) == [("cmnd/kueche/POWER1", "OFF")]
assert sent(registry.commands(tracked_kitchen, {"brightness": 0})) == [("cmnd/kueche/POWER1", "OFF")]
assert sent(registry.commands(tracked_kitchen, {"on": True})) == [("cmnd/kueche/POWER1", "ON")]
assert sent(registry.commands(tracked_kitchen, {"mirek": 370})) == [("cmnd/kueche/CT", "370")]
assert [message.topic for message in registry.commands(tracked_kitchen, {"color": "#ff0000"})] == ["cmnd/kueche/HSBColor1", "cmnd/kueche/HSBColor2"]
assert sent(registry.commands(tracked_hood, {"on": True})) == [("cmnd/kueche/POWER2", "ON")]
offline = registry.handle_message("tele/kueche/LWT", "Offline")
assert offline == {kitchen.id: {"online": False}, hood.id: {"online": False}}, offline

single = tasmota.TasmotaDiscovery().handle_message(TASMOTA_CONFIG_TOPIC, json.dumps({**TASMOTA_CONFIG, "rl": [1], "so": {}}).encode())
relay = translate.translator_for(single.entities[0], tasmota.TRANSLATORS)
assert relay.read("stat/kueche/RESULT", '{"POWER": "OFF"}').state == {"on": False}, "a single relay reports POWER without a number"
assert relay.read("stat/kueche/RESULT", '{"POWER1": "ON"}').state == {"on": True} and relay.read("stat/kueche/RESULT", "junk").state == {}
plain = tasmota.TasmotaDiscovery().handle_message(TASMOTA_CONFIG_TOPIC, json.dumps({**TASMOTA_CONFIG, "so": {"68": 1}}).encode())
assert translate.translator_for(plain.entities[0], tasmota.TRANSLATORS).is_dimmable is False       # separate channels: on/off only
shutter_config = {**TASMOTA_CONFIG, "t": "rollo", "rl": [3, 3], "dn": "Rollo"}
shutters = tasmota.TasmotaDiscovery().handle_message(TASMOTA_CONFIG_TOPIC, json.dumps(shutter_config).encode())
assert [found.platform for found in shutters.entities] == ["tasmota_shutter"]                       # two relays drive one shutter
shutter = translate.translator_for(shutters.entities[0], tasmota.TRANSLATORS)
assert shutter.kind == "cover" and shutter.read("stat/rollo/RESULT", '{"Shutter1": {"Position": 30, "Direction": 0, "Target": 30}}').state == {"position": 70}
assert sent(shutter.commands({"cover": "up"})) == [("cmnd/rollo/ShutterOpen1", "")] and sent(shutter.commands({"cover": "stop"})) == [("cmnd/rollo/ShutterStop1", "")]
assert sent(shutter.commands({"position": 25})) == [("cmnd/rollo/ShutterPosition1", "75")] and refuses(shutter, {"position": 140})
assert found_devices.handle_message(TASMOTA_CONFIG_TOPIC, b"").entities == (), "an empty config message removes the device"
print("10 Tasmota discovery: relays, light (dimmer, white tone, colour), shutter, sensors, availability, state request, "
      "single relay without a number, unusable topics refused, removal")


# ---------------------------------------------------------------------------
# 11) the plugin against the fake broker
# ---------------------------------------------------------------------------
async def wait_until(condition, seconds: float = 4.0) -> None:
    """Wait until ``condition()`` is true; fail after ``seconds``."""
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting for the plugin"
        await asyncio.sleep(0.01)


def new_plugin(broker: FakeBroker, **settings: object) -> tuple[mqtt.MqttPlugin, FakeContext]:
    """Create a plugin whose broker is the fake one, with ``host`` set unless overridden."""
    context = FakeContext("mqtt", stream_handler=broker.connect, secrets=["password"])
    context.settings.set("host", "192.0.2.20")
    for key, value in settings.items():
        context.settings.set(key, value)
    return mqtt.setup(context), context


async def check_plugin() -> None:
    """Walk the plugin through its life against the fake broker: discovery, state messages, commands, devices coming
    and going, outage and reconnect, manual devices, login, settings."""
    broker = FakeBroker()
    broker.publish("homeassistant/light/0x00158d0001a2b3c4/light/config", json.dumps(Z2M_LIGHT), retain=True)
    broker.publish("homeassistant/switch/plug/config", json.dumps({**PLUG_DEVICE, "stat_t": "z/wm", "cmd_t": "z/wm/set",
                                                                    "val_tpl": "{{ value_json.state }}", "uniq_id": "wm_switch", "qos": 1}), retain=True)
    broker.publish("zigbee2mqtt/bridge/state", '{"state": "online"}', retain=True)
    plugin, context = new_plugin(broker)
    assert plugin.status()["configured"] is True

    # while the retained discovery messages are still arriving, the list counts as "not known yet"
    await plugin.start()
    try:
        await plugin.devices()
        raise AssertionError("reported devices before the list was complete")
    except DeviceUnreachable:
        pass
    await wait_until(lambda: context.reloads >= 2)                  # connected, then: list complete
    devices = {device.name: device for device in await plugin.devices()}
    assert set(devices) == {"Stehlampe", "Waschmaschine"} and all(device.online and device.discovered for device in devices.values())
    assert plugin.status() == {"configured": True, "connected": True, "live": True, "name": "192.0.2.20"}
    assert context.streams[0][:2] == ("192.0.2.20", 1883) and context.streams[0][2]["tls"] is False and context.streams[0][2]["verify"] is True
    assert broker.connects[0].client_id == context.settings.get("client_id") and broker.connects[0].client_id.startswith("comfyos-")
    assert broker.connects[0].will == Message(topic="comfyos/status", payload=b"offline", retain=True)
    assert broker.retained["comfyos/status"] == b"online"
    assert not broker.published_to("homeassistant/status"), "ComfyOS must not pose as Home Assistant"
    assert any("verbunden" in line for line in context.events)

    # a state message arrives -> ComfyOS hears about it at once
    lamp_id, washer_id = devices["Stehlampe"].id, devices["Waschmaschine"].id
    broker.publish("zigbee2mqtt/Stehlampe", '{"state": "ON", "brightness": 127}')
    await wait_until(lambda: any(device_id == lamp_id for device_id, _ in context.changes))
    assert context.changes[-1] == (lamp_id, {"on": True, "brightness": 50})

    # a device that talks a lot is reported at most twice a second: the first change at once, the rest together
    await asyncio.sleep(0.6)                                        # let the report above age, so the next one is due at once
    context.changes.clear()
    for power in range(1, 9):
        broker.publish("zigbee2mqtt/Stehlampe", json.dumps({"brightness": power * 25}))
    await asyncio.sleep(0.1)
    assert len(context.changes) == 1, context.changes
    await asyncio.sleep(0.6)
    assert len(context.changes) == 2 and context.changes[-1] == (lamp_id, {"brightness": 79}), context.changes

    # commands are published to the device's command topic; the expected state comes back
    result = await plugin.command(lamp_id, {"on": True, "brightness": 40, "kelvin": 2700}, "command")
    assert json.loads(broker.published_to("zigbee2mqtt/Stehlampe/set")[-1]) == {"state": "ON", "brightness": 102, "color_temp": 370}
    assert result["on"] is True and result["brightness"] == 40 and result["light"].mirek == 370 and result["light"].mode == "ct"
    assert await plugin.command(washer_id, {"on": True}, "flow") == {"on": True}
    confirmed = [message for message in broker.received if message.topic == "z/wm/set"]
    assert confirmed[-1].payload == b"ON" and confirmed[-1].qos == 1                 # this device asked for confirmed delivery
    for bad_id, bad_state in ((lamp_id, {"target": 21}), ("mqtt:gibtsnicht", {"on": True})):
        try:
            await plugin.command(bad_id, bad_state, "command")
            raise AssertionError("accepted a command that cannot work")
        except PluginError:
            pass

    # a new device announces itself while ComfyOS is running; an old one is removed
    reloads_before = context.reloads
    broker.publish("homeassistant/binary_sensor/fenster/config", json.dumps({
        "dev": {"ids": ["fenster-kueche"], "name": "Fenster Küche"}, "uniq_id": "fenster_contact", "stat_t": "z/fenster",
        "dev_cla": "window", "val_tpl": "{{ value_json.contact }}", "pl_on": False, "pl_off": True}), retain=True)
    await wait_until(lambda: context.reloads > reloads_before)
    assert "Fenster Küche" in [device.name for device in await plugin.devices()]
    broker.publish("z/fenster", '{"contact": false}')
    await wait_until(lambda: context.changes[-1] == ("mqtt:fenster-kueche", {"open": True}))
    reloads_before = context.reloads
    broker.publish("homeassistant/binary_sensor/fenster/config", b"", retain=True)
    await wait_until(lambda: context.reloads > reloads_before)
    assert "Fenster Küche" not in [device.name for device in await plugin.devices()]

    # the broker crashes: devices are shown as offline (not forgotten), the last will is published, commands fail clearly
    reloads_before = context.reloads
    broker.is_down = True
    broker.drop_connections()
    await wait_until(lambda: not plugin.is_connected)
    offline = await plugin.devices()
    assert {device.name for device in offline} == {"Stehlampe", "Waschmaschine"} and not any(device.online for device in offline)
    assert plugin.status()["connected"] is False and plugin.status()["message"]
    assert broker.retained["comfyos/status"] == b"offline"
    try:
        await plugin.command(lamp_id, {"on": False}, "command")
        raise AssertionError("published without a connection")
    except DeviceUnreachable:
        pass

    # ... and comes back: the plugin reconnects on its own and subscribes again
    broker.is_down = False
    await wait_until(lambda: plugin.is_connected)
    assert all(device.online for device in await plugin.devices()) and broker.retained["comfyos/status"] == b"online"
    context.changes.clear()
    broker.publish("z/wm", '{"state": "OFF"}')
    await wait_until(lambda: context.changes == [(washer_id, {"on": False})])

    # a packet larger than the limit ends the session instead of filling the memory; the plugin recovers
    connects_before = len(broker.connects)
    broker.publish("z/wm", "x" * (mqtt_client.MAX_PACKET_BYTES + 10))
    await wait_until(lambda: len(broker.connects) > connects_before and plugin.is_connected)

    # devices described by hand
    try:
        plugin.add_manual_device({"name": "Kaputt", "kind": "switch", "state_topic": "a/#"})
        raise AssertionError("accepted a manual device with a wildcard topic")
    except PluginError:
        pass
    created = plugin.add_manual_device({"name": "Shelly Küche", "kind": "switch", "state_topic": "shellies/kueche/relay/0",
                                        "command_topic": "shellies/kueche/relay/0/command", "payload_on": "on", "payload_off": "off"})
    manual_id = "mqtt:manual_" + created.key
    assert manual_id in [device.id for device in await plugin.devices()] and len(context.settings.get("manual_devices")) == 1
    await plugin.command(manual_id, {"on": True}, "command")
    assert broker.published_to("shellies/kueche/relay/0/command") == ["on"]
    context.changes.clear()
    broker.publish("shellies/kueche/relay/0", "off")
    await wait_until(lambda: context.changes == [(manual_id, {"on": False})])
    status = await call_route(plugin, "GET", "/status")
    assert status["connected"] and [item["device_id"] for item in status["manual"]] == [manual_id] and status["not_supported"] == []
    assert ["switch", "Steckdose / Schalter"] in status["manual_kinds"]

    # stopping says goodbye (no last will), a restart keeps the manual device and the client id
    client_id = context.settings.get("client_id")
    await plugin.stop()
    await wait_until(lambda: not broker.sessions)
    assert broker.retained["comfyos/status"] == b"offline" and not plugin.is_connected
    await plugin.start()
    await wait_until(lambda: plugin.is_connected)
    assert broker.connects[-1].client_id == client_id
    await wait_until(lambda: context.reloads and plugin.status()["connected"])
    await asyncio.sleep(0.2)
    assert manual_id in [device.id for device in await plugin.devices()]
    assert plugin.remove_manual_device(created.key) and not plugin.remove_manual_device(created.key)
    assert manual_id not in [device.id for device in await plugin.devices()]
    await plugin.stop()

    # user name and password: sent to the broker, a wrong password is reported without repeating it
    secret = "broker-" + "geheim"
    guarded = FakeBroker(user_name="comfyos", password=secret)
    plugin, context = new_plugin(guarded, username="comfyos", port=8883, tls=True, verify_tls=False)
    context.secrets.set("password", secret)
    await plugin.start()
    await wait_until(lambda: plugin.is_connected)
    assert (guarded.connects[0].user_name, guarded.connects[0].password) == ("comfyos", secret)
    assert context.streams[0][:2] == ("192.0.2.20", 8883) and context.streams[0][2]["tls"] is True and context.streams[0][2]["verify"] is False
    await plugin.stop()
    context.secrets.set("password", "falsch")
    await plugin.start()
    await wait_until(lambda: "Anmeldung abgelehnt" in plugin.status().get("message", ""))
    assert plugin.status()["connected"] is False and "falsch" not in json.dumps(plugin.status()) and secret not in json.dumps(plugin.status())
    try:
        await plugin.devices()
        raise AssertionError("reported devices without ever having been connected")
    except DeviceUnreachable as error:
        assert "Anmeldung" in str(error)
    await plugin.stop()

    # no broker configured: nothing is connected; a broker outside the home network is never contacted
    plugin, context = new_plugin(FakeBroker(), host="")
    await plugin.start()
    assert plugin.status()["configured"] is False and await plugin.devices() == [] and context.streams == []
    await plugin.stop()
    outside = FakeBroker()
    plugin, context = new_plugin(outside, host="8.8.8.8")
    context.secrets.set("password", secret)
    await plugin.start()
    await wait_until(lambda: "Heimnetz" in plugin.status().get("message", ""))
    assert outside.connects == [] and not plugin.is_connected
    await plugin.stop()

    # Zigbee2MQTT without Home Assistant discovery and a Tasmota device: both are found, silent devices are asked once
    native = FakeBroker()
    native.publish("zigbee2mqtt/bridge/devices", json.dumps(Z2M_DEVICES), retain=True)
    native.publish("zigbee2mqtt/Bad Thermostat", '{"local_temperature": 20.5, "occupied_heating_setpoint": 21}', retain=True)
    native.publish(TASMOTA_CONFIG_TOPIC, json.dumps(TASMOTA_CONFIG), retain=True)
    native.publish(TASMOTA_SENSORS_TOPIC, json.dumps(TASMOTA_SENSORS), retain=True)
    plugin, context = new_plugin(native)
    await plugin.start()
    await wait_until(lambda: context.reloads >= 2)
    devices = {device.name: device for device in await plugin.devices()}
    assert set(devices) == {"Stehlampe", "Küche/Fenster", "Bad Thermostat", "Tasmota Küche", "Tasmota Küche Abzug"}, set(devices)
    await wait_until(lambda: native.published_to("zigbee2mqtt/Stehlampe/get") and native.published_to("cmnd/kueche/STATE"))
    assert json.loads(native.published_to("zigbee2mqtt/Stehlampe/get")[0])["state"] == "" and native.published_to("cmnd/kueche/STATE") == [""]
    assert not native.published_to("zigbee2mqtt/Bad Thermostat/get"), "a device whose state is retained is not asked"
    await plugin.command(devices["Tasmota Küche Abzug"].id, {"on": True}, "test")
    await wait_until(lambda: native.published_to("cmnd/kueche/POWER2") == ["ON"])
    await plugin.stop()
    renamed = FakeBroker()
    renamed.publish("z2m/bridge/devices", json.dumps(Z2M_DEVICES), retain=True)
    plugin, context = new_plugin(renamed, zigbee2mqtt_topic="z2m/")
    await plugin.start()
    await wait_until(lambda: context.reloads >= 2)
    assert {device.name for device in await plugin.devices()} == {"Stehlampe", "Küche/Fenster", "Bad Thermostat"}
    await plugin.stop()

    # another discovery prefix
    other = FakeBroker()
    other.publish("smarthome/ha/switch/plug/config", json.dumps({"cmd_t": "plug/set", "name": "Stecker"}), retain=True)
    other.publish("homeassistant/switch/plug2/config", json.dumps({"cmd_t": "plug2/set", "name": "Falscher Präfix"}), retain=True)
    plugin, context = new_plugin(other, discovery_prefix="/smarthome/ha/")
    await plugin.start()
    await wait_until(lambda: context.reloads >= 2)
    assert [device.name for device in await plugin.devices()] == ["Stecker"]
    await plugin.stop()


async def call_route(plugin: mqtt.MqttPlugin, method: str, path: str) -> dict:
    """Call one of the plugin's endpoints directly (without HTTP)."""
    route = next(route for route in plugin.routes().routes if route.path == path and method in route.methods)
    return await route.endpoint()


asyncio.run(check_plugin())
print("11 plugin: waits for the full list, live updates (held back to two a second), commands, new and removed devices, "
      "broker outage and reconnect, oversized packet, manual devices, password, prefix, Zigbee2MQTT device list, Tasmota, "
      "state requests, nothing outside the home network")

print("ALL MQTT PLUGIN TESTS OK")
