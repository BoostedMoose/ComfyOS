"""ComfyOS – MQTT plugin against a REAL broker (Mosquitto) on this machine.

    python tests/plugins/test_mqtt_broker.py [path-to-comfyos]

The other tests run against the test broker of the plugin. Here the plugin talks to a foreign broker and
a foreign peer: devices announce themselves through `mosquitto_pub` (Home Assistant discovery), commands of
ComfyOS are read along with `mosquitto_sub`. The whole path is checked: connecting through `egress.open_stream`
(real TCP), retained discovery messages, adopting, switching (text and JSON command), state and
reachability, sign of life `comfyos/status` with the last will.

Prerequisite: a broker on 127.0.0.1:1883 without login and the programs `mosquitto_pub`/`mosquitto_sub`
(Debian: `sudo apt install mosquitto mosquitto-clients` – only listens locally in the default configuration).
If any of this is missing, the test is skipped (return value 0). All topics lie under a random prefix and
are deleted again at the end.
"""
import json
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

HOST, PORT = "127.0.0.1", 1883


def broker_missing() -> str | None:
    """Reason why the test cannot run (tools or broker missing) – or None."""
    if not shutil.which("mosquitto_pub") or not shutil.which("mosquitto_sub"):
        return "mosquitto_pub/mosquitto_sub nicht installiert"
    try:
        socket.create_connection((HOST, PORT), timeout=1.5).close()
    except OSError:
        return f"kein Broker auf {HOST}:{PORT}"
    return None


reason = broker_missing()
if reason:
    print(f"SKIPPED: {reason} – the test against a real broker only runs where one is installed")
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("broker")

from app import auth, main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PW = "comfyos-" + "test"
PREFIX = "comfyos-test-" + uuid.uuid4().hex[:8]          # eigener Bereich auf dem Broker
DISCOVERY = PREFIX + "/discovery"
mqtt_module = sys.modules["comfyos_plugin_mqtt"]
mqtt_module.SETTLE_SECONDS = 0.4
retained: list[str] = []                                  # retained topics that are deleted at the end


def pub(topic: str, payload, retain: bool = False) -> None:
    """Publish a message with mosquitto_pub; retained topics are remembered for cleaning up."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    subprocess.run(["mosquitto_pub", "-h", HOST, "-p", str(PORT), "-t", topic, "-m", text] + (["-r"] if retain else []),
                   check=True, timeout=10)
    if retain:
        retained.append(topic)


def listen(topic: str) -> subprocess.Popen:
    """Wait for the next message of a topic (reads exactly one, gives up after 8 s)."""
    proc = subprocess.Popen(["mosquitto_sub", "-h", HOST, "-p", str(PORT), "-t", topic, "-C", "1", "-W", "8"],
                            stdout=subprocess.PIPE, text=True)
    time.sleep(0.4)        # until the subscription stands
    return proc


def heard(proc: subprocess.Popen) -> str:
    """What a mosquitto_sub has received."""
    out, _ = proc.communicate(timeout=12)
    return out.strip()


def current(topic: str) -> str:
    """Retained message of a topic ('' = none)."""
    out = subprocess.run(["mosquitto_sub", "-h", HOST, "-p", str(PORT), "-t", topic, "-C", "1", "-W", "2"],
                         stdout=subprocess.PIPE, text=True, timeout=10).stdout
    return out.strip()


PLUG = {"dev": {"ids": [PREFIX + "-plug"], "name": "Echte Steckdose", "mf": "Test", "mdl": "P1"}, "uniq_id": PREFIX + "-plug",
        "stat_t": PREFIX + "/plug/state", "cmd_t": PREFIX + "/plug/set", "avty_t": PREFIX + "/plug/availability"}
LAMP = {"schema": "json", "dev": {"ids": [PREFIX + "-lamp"], "name": "Echte Lampe"}, "uniq_id": PREFIX + "-lamp",
        "stat_t": PREFIX + "/lamp", "cmd_t": PREFIX + "/lamp/set", "brightness": True}
TEMP = {"dev": {"ids": [PREFIX + "-temp"], "name": "Echter Fühler"}, "uniq_id": PREFIX + "-temp", "dev_cla": "temperature",
        "stat_t": PREFIX + "/temp", "unit_of_meas": "°C", "val_tpl": "{{ value_json.temperature }}"}

try:
    # devices announce themselves before ComfyOS already (retained messages, as with Zigbee2MQTT after a restart)
    pub(f"{DISCOVERY}/switch/plug/config", PLUG, retain=True)
    pub(f"{DISCOVERY}/light/lamp/config", LAMP, retain=True)
    pub(f"{DISCOVERY}/sensor/temp/config", TEMP, retain=True)
    pub(PREFIX + "/plug/availability", "online", retain=True)
    pub(PREFIX + "/plug/state", "OFF", retain=True)
    pub(PREFIX + "/temp", {"temperature": 21.4}, retain=True)

    with TestClient(main.app) as client:
        auth.set_password(PW)
        client.cookies.set(auth.COOKIE, auth.create_session("test"))

        def ok(r, code=200):
            assert r.status_code == code, (r.status_code, r.text[:400])
            return r.json()

        def tile():
            return next(p for p in ok(client.get("/api/plugins"))["plugins"] if p["id"] == "mqtt")

        def devs():
            return {d["name"]: d for d in ok(client.get("/api/devices"))}

        def wait(condition, what, seconds=10.0):
            deadline = time.time() + seconds
            while not condition():
                assert time.time() < deadline, f"timed out: {what}"
                time.sleep(0.1)

        # 1) Connect and find
        ok(client.put("/api/plugins/mqtt/settings", json={"values": {"host": HOST, "port": PORT, "discovery_prefix": DISCOVERY}}))
        wait(lambda: tile()["status"].get("connected"), "connection to the broker")
        wait(lambda: len(ok(client.get("/api/plugins/mqtt/devices"))["new"]) == 3, "three found devices")
        found = {c["name"]: c for c in ok(client.get("/api/plugins/mqtt/devices"))["new"]}
        assert set(found) == {"Echte Steckdose", "Echte Lampe", "Echter Fühler"}, found
        assert current("comfyos/status") == "online", "sign of life missing"
        print(f"1 real broker ({HOST}:{PORT}): connected, three retained devices found, sign of life comfyos/status = online")

        # 2) Adopting: state from retained messages
        assert ok(client.post("/api/plugins/mqtt/adopt_all"))["adopted"] == 3
        wait(lambda: len(devs()) >= 3 and devs()["Echter Fühler"].get("temperature") == 21.4, "temperature from a retained message")
        plug = devs()["Echte Steckdose"]
        assert plug["kind"] == "switch" and plug["on"] is False and plug["online"] is True and plug["vendor"] == "Test", plug
        print("2 adopted: socket off, sensor 21.4 °C – state from the retained messages of the broker")

        # 3) Switching: the command arrives at a foreign peer (text and JSON)
        ear = listen(PREFIX + "/plug/set")
        ok(client.post(f"/api/devices/{plug['id']}/command", json={"state": {"on": True}}))
        assert heard(ear) == "ON", "the command to the socket did not arrive"
        lamp = devs()["Echte Lampe"]
        ear = listen(PREFIX + "/lamp/set")
        ok(client.post(f"/api/devices/{lamp['id']}/command", json={"state": {"on": True, "brightness": 50}}))
        sent = json.loads(heard(ear))
        assert sent["state"] == "ON" and 120 <= sent["brightness"] <= 135, sent
        print("3 switching: ON to the socket, JSON with brightness to the lamp – read along with mosquitto_sub")

        # 4) State and reachability from the device
        pub(PREFIX + "/plug/state", "ON")
        wait(lambda: devs()["Echte Steckdose"]["on"] is True, "state ON")
        pub(PREFIX + "/lamp", {"state": "ON", "brightness": 255})
        wait(lambda: devs()["Echte Lampe"]["brightness"] == 100, "brightness 100 %")
        pub(PREFIX + "/temp", {"temperature": 19.0})
        wait(lambda: devs()["Echter Fühler"]["temperature"] == 19.0, "new temperature")
        pub(PREFIX + "/plug/availability", "offline", retain=True)
        wait(lambda: devs()["Echte Steckdose"]["online"] is False, "device offline")
        print("4 the device reports: state, brightness, temperature and 'offline' arrive in ComfyOS")

        # 5) A device disappears (empty discovery message); disabling signs ComfyOS off properly
        pub(f"{DISCOVERY}/sensor/temp/config", "", retain=True)
        wait(lambda: "Echter Fühler" not in devs() or devs()["Echter Fühler"]["online"] is False, "sensor signed off")
        ok(client.put("/api/plugins/mqtt/enabled", json={"enabled": False}))
        wait(lambda: current("comfyos/status") == "offline", "sign of life offline after disabling")
        print("5 a device that signed off disappears or is offline; after disabling comfyos/status = offline")
finally:
    for topic in set(retained) | {"comfyos/status"}:      # eigenen Bereich auf dem Broker wieder leeren
        subprocess.run(["mosquitto_pub", "-h", HOST, "-p", str(PORT), "-t", topic, "-r", "-n"], timeout=10)

print("ALL TESTS AGAINST THE REAL BROKER OK")
