"""Tests for the network plugin. They run without ComfyOS; no real connection is opened.

    python public/plugins/network/tests/test_network_plugin.py [path-to-comfyos]

Covered: user input (name, address, port), which answers count as "present", the waiting time
before "away", the unknown state after a start, addresses outside the home network,
the endpoints of the tile and the stored targets.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "public" / "sdk"))
sys.path.insert(0, str(ROOT / "public" / "plugins"))

from plugin_api import PluginError  # noqa: E402
from testing import FakeContext, memory_stream_pair  # noqa: E402

import network  # noqa: E402

# How each fake host answers a connection attempt: "open", "refused" or "silent".
answers: dict[str, str] = {}
attempts: list[tuple[str, int]] = []


async def fake_network(host: str, port: int) -> tuple:
    """Stand in for the home network: answer the way ``answers`` says."""
    attempts.append((host, port))
    answer = answers.get(host, "silent")
    if answer == "refused":
        raise ConnectionRefusedError("port closed")
    if answer == "silent":
        raise asyncio.TimeoutError()
    plugin_end, _ = memory_stream_pair()
    return plugin_end


class FakeClock:
    """A clock tests can move forward."""

    def __init__(self) -> None:
        """Start at a fixed point in time."""
        self.now = 1_000_000.0

    def __call__(self) -> float:
        """Return the current fake time in seconds."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Let ``seconds`` pass."""
        self.now += seconds


# ---------------------------------------------------------------------------
# 1) user input
# ---------------------------------------------------------------------------
phone = network.checked_target("abcd1234", {"name": "  Handy   Anna ", "host": " 192.0.2.40 ", "port": 62078})
assert (phone.name, phone.host, phone.port) == ("Handy Anna", "192.0.2.40", 62078) and network.device_id_of(phone) == "network:abcd1234"
assert network.checked_target("abcd1234", {"name": "Drucker", "host": "drucker.fritz.box"}).port == 80
for bad_input, word in (({"name": "", "host": "192.0.2.1"}, "Namen"), ({"name": "X", "host": ""}, "Adresse"),
                        ({"name": "X", "host": "http://192.0.2.1/"}, "Adresse"), ({"name": "X", "host": "a b"}, "Adresse"),
                        ({"name": "X", "host": "192.0.2.1", "port": 0}, "Port"), ({"name": "X", "host": "192.0.2.1", "port": 70000}, "Port"),
                        ({"name": "X", "host": "192.0.2.1", "port": "80"}, "Port"), ({"name": "X", "host": "192.0.2.1", "port": True}, "Port")):
    try:
        network.checked_target("abcd1234", bad_input)
        raise AssertionError(f"accepted {bad_input}")
    except PluginError as error:
        assert word in str(error), (bad_input, str(error))
print("1 input: name, address and port are checked; nothing that looks like a URL or contains spaces")


# ---------------------------------------------------------------------------
# 2) present, away, unknown
# ---------------------------------------------------------------------------
async def call_route(plugin: network.NetworkPlugin, method: str, path: str, **arguments: object) -> dict:
    """Call one of the plugin's endpoints directly (without HTTP)."""
    route = next(route for route in plugin.routes().routes if route.path == path and method in route.methods)
    return await route.endpoint(**arguments)


async def check_plugin() -> None:
    """Walk the plugin through its life against a fake network: targets only in the home network, presence with a
    waiting time, the tile's endpoints, restart, limits."""
    context = FakeContext("network", stream_handler=fake_network)
    plugin = network.setup(context)
    clock = FakeClock()
    plugin.clock = clock
    assert plugin.status()["configured"] is False and await plugin.devices() == []
    assert plugin.interval_seconds == 30 and plugin.away_after_seconds == 600
    context.settings.set("away_after", 5)
    context.settings.set("interval", 9999)
    assert plugin.away_after_seconds == 300 and plugin.interval_seconds == 600        # limited to sensible values

    # an address outside the home network is refused before anything is sent
    try:
        plugin.add_target({"name": "Fremd", "host": "8.8.8.8", "port": 80})
        raise AssertionError("accepted an address outside the home network")
    except PluginError as error:
        assert "Heimnetz" in str(error)
    assert attempts == [] and plugin.targets == []

    answers.update({"192.0.2.40": "open", "192.0.2.41": "refused", "192.0.2.42": "silent"})
    phone = plugin.add_target({"name": "Handy Anna", "host": "192.0.2.40", "port": 62078})
    laptop = plugin.add_target({"name": "Laptop", "host": "192.0.2.41", "port": 80})
    tablet = plugin.add_target({"name": "Tablet", "host": "192.0.2.42", "port": 80})
    assert context.reloads == 3 and len(context.settings.get("targets")) == 3
    plugin._started_at = clock()

    # an open port and a refused connection both prove the device is there; silence does not make it "away" at once
    await plugin.check_all()
    assert sorted(attempts) == [("192.0.2.40", 62078), ("192.0.2.41", 80), ("192.0.2.42", 80)]
    assert context.streams[0][2]["egress_key"] == "hosts"
    devices = {device.name: device for device in await plugin.devices()}
    assert devices["Handy Anna"].state == {"present": True} and devices["Laptop"].state == {"present": True}
    assert devices["Tablet"].state == {}, "a device that has not answered yet is unknown, not away"
    assert all(device.kind == "presence" and device.online and device.discovered for device in devices.values())
    assert devices["Handy Anna"].details["Adresse"] == "192.0.2.40:62078" and devices["Tablet"].details["Zuletzt erreichbar"] == "noch nie"
    assert sorted(context.changes) == sorted([(network.device_id_of(laptop), {"present": True}), (network.device_id_of(phone), {"present": True})])
    assert plugin.status() == {"configured": True, "connected": True, "live": True, "message": "2 von 3 anwesend"}

    # the phone falls asleep: still present until the waiting time is over, then away – reported once
    context.changes.clear()
    answers["192.0.2.40"] = "silent"
    clock.advance(120)
    await plugin.check_all()
    assert plugin.state_of(phone).is_present is True and context.changes == []
    clock.advance(200)
    await plugin.check_all()
    assert plugin.state_of(phone).is_present is False and plugin.state_of(tablet).is_present is False
    assert sorted(context.changes) == sorted([(network.device_id_of(phone), {"present": False}), (network.device_id_of(tablet), {"present": False})])
    await plugin.check_all()
    assert len(context.changes) == 2, "the same state was reported twice"

    # it answers again: present at the first answer
    context.changes.clear()
    answers["192.0.2.40"] = "refused"
    await plugin.check_all()
    assert context.changes == [(network.device_id_of(phone), {"present": True})]

    # the tile's endpoints
    listed = await call_route(plugin, "GET", "/targets")
    assert [(item["name"], item["present"]) for item in listed["targets"]] == [("Handy Anna", True), ("Laptop", True), ("Tablet", False)]
    assert listed["away_after_minutes"] == 5 and listed["targets"][0]["device_id"] == network.device_id_of(phone)
    checked = await call_route(plugin, "POST", "/targets/{key}/check", key=tablet.key)
    assert checked["answered"] is False and checked["present"] is False
    answers["192.0.2.42"] = "open"
    assert (await call_route(plugin, "POST", "/targets/{key}/check", key=tablet.key))["present"] is True
    assert (await call_route(plugin, "DELETE", "/targets/{key}", key=laptop.key)) == {"ok": True}
    assert [target.name for target in plugin.targets] == ["Handy Anna", "Tablet"]
    assert not plugin.remove_target("gibtsnicht")

    # a stored address that ComfyOS refuses is shown as a problem, never as "away"
    context.settings.set("targets", context.settings.get("targets") + [{"key": "ffff0000", "name": "Fremd", "host": "8.8.8.8", "port": 80},
                                                                       {"key": "x", "name": "kaputt"}, "junk"])
    await plugin.start()
    assert [target.name for target in plugin.targets] == ["Handy Anna", "Tablet", "Fremd"]      # invalid entries are skipped
    await asyncio.sleep(0.05)                                                                   # the first round of checks runs at once
    foreign = next(device for device in await plugin.devices() if device.name == "Fremd")
    assert foreign.online is False and "Heimnetz" in foreign.status_detail and foreign.state == {}
    restarted = {device.name: device for device in await plugin.devices()}
    assert restarted["Handy Anna"].state == {"present": True} and restarted["Tablet"].state == {"present": True}

    # after a restart a silent device is unknown first, so a restart never looks like someone leaving
    await plugin.stop()
    answers["192.0.2.40"] = "silent"
    context.changes.clear()
    await plugin.start()
    await asyncio.sleep(0.05)
    assert plugin.state_of(phone).is_present is None and not any(device_id == network.device_id_of(phone) for device_id, _ in context.changes)
    await plugin.stop()

    # the number of targets is limited
    for number in range(network.MAX_TARGETS - len(plugin.targets)):
        plugin.add_target({"name": f"Gerät {number}", "host": "192.0.2.50", "port": 80})
    try:
        plugin.add_target({"name": "Zu viel", "host": "192.0.2.50", "port": 80})
        raise AssertionError("accepted more targets than allowed")
    except PluginError:
        pass


asyncio.run(check_plugin())
print("2 presence: open and refused count as present, away only after the waiting time, unknown after a start, "
      "nothing outside the home network, endpoints, stored targets, limit")

print("ALL NETWORK PLUGIN TESTS OK")
