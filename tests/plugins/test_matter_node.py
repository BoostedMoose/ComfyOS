"""ComfyOS – Matter plugin with the REAL helper program (matter.js under Node) on this machine.

    python tests/plugins/test_matter_node.py [path-to-comfyos]

The other tests of the plugin run without Node against a `FakeHelper`. Here two runs of the plugin each start a
real helper program – through `helpers.py`, confined with `node --permission` as in operation – and the controller of
the one pairs the bridge of the other:

    1  both helper programs start and go online
    2  pairing: open pairing, pairing code, the controller takes in the bridge (through mDNS in the own network)
    3  reading: the shared devices appear at the controller as matter:<node>-<endpoint>
    4  switching: commands arrive through context.command; a locked device is rejected
    5  subscribing: a change in the bridge appears at the controller
    6  storage: encrypted file; after a restart the bridge still knows its controller
    7  removing: the controller signs off
    8  confined: under `--permission` a program reads nothing from the data folder, writes nothing, starts nothing

Prerequisite: Node.js from version 24 (the version the helper program runs with in operation) – as `node` in the path,
under `public/plugins/matter/runtime/` or through the environment variable COMFYOS_TEST_NODE. If Node is missing, the
test is skipped (return value 0). The Matter ports of this test lie apart from the usual ones (5540/5541).
"""
import asyncio
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else Path(__file__).resolve().parents[2]
MIN_NODE_MAJOR = 24


def find_node() -> tuple[Path | None, str]:
    """(path to Node from version 24, reason) – the reason says why the test cannot run if the path is missing."""
    shipped = REPO / "public" / "plugins" / "matter" / "runtime" / ("node.exe" if sys.platform == "win32" else "bin/node")
    candidates = [os.environ.get("COMFYOS_TEST_NODE"), str(shipped) if shipped.is_file() else None, shutil.which("node")]
    found = ""
    for candidate in [c for c in candidates if c]:
        try:
            version = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        found = found or version
        if version.startswith("v") and version[1:].split(".")[0].isdigit() and int(version[1:].split(".")[0]) >= MIN_NODE_MAJOR:
            return Path(candidate).resolve(), version
    return None, f"Node.js from version {MIN_NODE_MAJOR} is missing" + (f" (found: {found})" if found else "")


NODE, NODE_INFO = find_node()
if NODE is None:
    print(f"SKIPPED: {NODE_INFO} – the test with real matter.js only runs where Node is installed"
          "(or set COMFYOS_TEST_NODE=<path to node>)")
    sys.exit(0)
BUNDLE = REPO / "public" / "plugins" / "matter" / "bridge" / "dist" / "bridge.mjs"
if not BUNDLE.is_file():
    print("SKIPPED: public/plugins/matter/bridge/dist/bridge.mjs is missing – build it with dev/build_matter.sh first")
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("matter-node")
for extra in ("public/sdk", "public/plugins"):
    sys.path.insert(0, str(ROOT / extra))

from app import helpers, tasks  # noqa: E402
from plugin_api import DeviceUnreachable, DeviceView, LightState, PluginError  # noqa: E402
from testing import FakeContext  # noqa: E402

import matter  # noqa: E402
from matter import runtime, storage  # noqa: E402

matter.SYNC_DELAY_SECONDS = 0.1
matter.SAVE_DELAY_SECONDS = 0.1
tasks.FIRST_PAUSE = 0.2
PORT = random.randint(20000, 40000)          # own ports: disturb neither a running ComfyOS nor a second test run
ACCESS = ("devices", "rooms", "scenes", "home")


class NodeContext(FakeContext):
    """Context as in the SDK but with a real helper program: `start_helper` goes to `helpers.py` of the core."""

    def start_helper(self, program, args=(), *, env=None):
        """Really start the helper program. Node lies outside the data folder here (inside it in operation) – therefore
        its folder counts as allowed in this test."""
        real = Path(program).resolve()
        self.real_helper = helpers.start(self.plugin_id, [real.parent, self.data_dir], self.data_dir, real, args, env)
        return self.real_helper


def new_plugin(name: str, offset: int, views: list[DeviceView], shared: list[str]) -> tuple[matter.MatterPlugin, NodeContext]:
    """One run of the plugin with its own data folder, own ports and (as a link) the Node of this machine."""
    context = NodeContext(name, secrets=["storage_key", "passcode"], helper=True, access=ACCESS)
    context.device_views = views
    context.settings.set("bridge_port", PORT + offset)
    context.settings.set("controller_port", PORT + offset + 1)
    context.settings.set("shared", shared)
    binary = runtime.binary_in(context.data_dir)
    binary.parent.mkdir(parents=True)
    try:
        binary.symlink_to(NODE)
    except OSError:                          # Windows without the right for links: copy
        shutil.copy2(NODE, binary)
    (context.data_dir / runtime.RUNTIME_FOLDER / runtime.VERSION_FILE).write_text(runtime.NODE_VERSION + "\n", encoding="ascii")
    return matter.setup(context), context


async def until(condition, what: str, seconds: float = 40.0) -> None:
    """Wait until a condition holds – otherwise fail with a message."""
    for _ in range(int(seconds / 0.1)):
        if condition():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"did not happen: {what}")


def alive(pid: int) -> bool:
    """Is the process with this number still running?"""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


PROBE = """
import fs from 'node:fs'
import { spawnSync } from 'node:child_process'
const result = {}
const attempt = (name, run) => { try { run(); result[name] = 'allowed' } catch (error) { result[name] = String(error.code || error.name) } }
attempt('read', () => fs.readFileSync(process.argv[2]))
attempt('list', () => fs.readdirSync(process.argv[3]))
attempt('write', () => fs.writeFileSync(process.argv[3] + '/written-by-helper.txt', 'x'))
attempt('spawn', () => { const child = spawnSync(process.execPath, ['--version']); if (child.error) throw child.error })
process.stdout.write(JSON.stringify(result) + '\\n')
process.stdin.on('data', () => {})
process.stdin.on('end', () => process.exit(0))
"""


async def main() -> None:
    """All sections in one event loop; at the end both helper programs have ended."""
    lamp = DeviceView(id="hue:1", name="Stehlampe", kind="light", plugin="hue", room_id="wohnen", room="Wohnzimmer", controllable=True,
                      state={"on": True, "brightness": 50}, light=LightState())
    plug = DeviceView(id="hb:plug", name="Gesperrte Dose", kind="switch", plugin="qivicon", controllable=True, locked=True, state={"on": False})
    heating = DeviceView(id="hb:therm", name="Heizung", kind="thermostat", plugin="qivicon", controllable=True,
                         state={"target": 21.0, "temperature": 20.0})
    hidden = DeviceView(id="hb:geheim", name="Nicht freigegeben", kind="switch", plugin="qivicon", controllable=True, state={"on": False})
    bridge, bridge_context = new_plugin("matter_a", 0, [lamp, plug, heating, hidden], ["hue:1", "hb:plug", "hb:therm"])
    controller, controller_context = new_plugin("matter_b", 10, [], [])
    try:
        # ------------------------------------------------------------------ 1) starten
        await bridge.start()
        await controller.start()
        await until(lambda: bridge.status()["connected"] and controller.status()["connected"], "both helper programs online", 60)
        command_line = [str(bridge_context.real_helper._program)] + bridge_context.real_helper._args
        assert command_line[1:] == ["--permission", f"--allow-fs-read={matter.BUNDLE}", str(matter.BUNDLE)], command_line
        assert bridge.overview()["shares"]["used"] == 3 and await controller.devices() == []
        print(f"1 two helper programs online ({NODE_INFO}, confined with --permission), three devices shared")

        # ------------------------------------------------------------------ 2) koppeln
        assert bridge.overview()["pairing"] == {"commissioned": False, "open_until": 0.0}
        await bridge.open_window(True)
        code = bridge.overview()["pairing"]["manual"]
        assert len(code) == 11 and code.isdigit() and len(bridge.overview()["pairing"]["rows"]) >= 21
        paired = await controller.pair(code)
        node_id = paired["node"]
        await until(lambda: bridge.overview()["controllers"], "the bridge knows its controller")
        assert bridge.window_open_until == 0.0 and any("gekoppelt" in event for event in bridge_context.events)
        assert code not in " ".join(bridge_context.events + controller_context.events), "pairing code in the event log"
        print("2 pairing: window opened, the controller takes in the bridge with the pairing code, the window closes")

        # ------------------------------------------------------------------ 3) lesen
        await until(lambda: len(controller._devices_of(controller._nodes.get(node_id))) == 3, "devices of the bridge at the controller")
        found = {device.name: device for device in await controller.devices()}
        assert set(found) == {"Stehlampe", "Gesperrte Dose", "Heizung"}, set(found)
        assert "Nicht freigegeben" not in found, "a device that is not shared is visible through Matter"
        assert all(device.id.startswith(f"matter:{node_id}-") and device.discovered for device in found.values())
        assert found["Stehlampe"].kind == "light" and found["Stehlampe"].state == {"on": True, "brightness": 50}
        assert found["Gesperrte Dose"].kind == "switch" and found["Heizung"].state == {"target": 21.0, "temperature": 20.0}
        print("3 reading: exactly the shared devices appear as matter:<node>-<endpoint> with kind and state")

        # ------------------------------------------------------------------ 4) schalten
        assert await controller.command(found["Stehlampe"].id, {"on": False}, "command") == {"on": False}
        await until(lambda: bridge_context.commands == [("hue:1", {"on": False})], "the command arrives at the bridge")
        try:
            await controller.command(found["Gesperrte Dose"].id, {"on": True}, "command")
            raise AssertionError("a locked device could be switched through Matter")
        except DeviceUnreachable:
            pass
        assert len(bridge_context.commands) == 1, bridge_context.commands
        await controller.command(found["Stehlampe"].id, {"brightness": 80}, "command")
        await until(lambda: bridge_context.commands[-1] == ("hue:1", {"on": True, "brightness": 80}), "brightness arrives")
        await controller.command(found["Heizung"].id, {"target": 22.5}, "command")
        await until(lambda: bridge_context.commands[-1] == ("hb:therm", {"target": 22.5}), "target temperature arrives")
        print("4 switching: on/off, brightness and target temperature through context.command; the locked device is rejected")

        # ------------------------------------------------------------------ 5) abonnieren
        controller_context.changes.clear()
        bridge_context.change("hue:1", {"on": True, "brightness": 30})
        await until(lambda: any(device_id == found["Stehlampe"].id and changes.get("brightness") == 30
                                for device_id, changes in controller_context.changes), "the change appears at the controller")
        bridge_context.change("hb:therm", {"temperature": 19.0})
        await until(lambda: any(changes.get("temperature") == 19.0 for _, changes in controller_context.changes), "the reading appears")
        print("5 subscribing: changes in the bridge appear at the controller")

        # ------------------------------------------------------------------ 6) Storage and restart
        store_file = bridge_context.data_dir / "matter.bin"
        await until(store_file.exists, "storage written")
        raw = store_file.read_bytes()
        assert raw.startswith(storage.MAGIC) and b"fabric" not in raw.lower() and b"Stehlampe" not in raw, "storage not encrypted"
        files = sorted(p.name for p in bridge_context.data_dir.iterdir() if not p.name.endswith(".tmp"))
        assert files == ["matter.bin", "runtime"], f"the helper program created files itself: {files}"
        passcode = bridge_context.secrets.get("passcode")
        pid = bridge_context.real_helper._process.pid
        await bridge.stop()
        await helpers.stop("matter_a")
        assert not alive(pid), "the helper program keeps running after stopping"
        bridge._fabrics, bridge._pairing = [], None          # what follows now must come from the stored state
        await bridge.start()
        await until(lambda: bridge.status()["connected"], "bridge online again", 60)
        await until(lambda: len(bridge.overview()["controllers"]) == 1, "the bridge knows its controller after the restart")
        assert bridge.overview()["pairing"]["commissioned"] is True and bridge_context.secrets.get("passcode") == passcode
        bridge_context.commands.clear()

        async def reaches_bridge() -> bool:
            """A command of the controller arrives again after the restart (the session is rebuilt)."""
            for _ in range(10):
                try:
                    await controller.command(found["Stehlampe"].id, {"on": False}, "command")
                    return True
                except PluginError:
                    await asyncio.sleep(2)
            return False

        assert await reaches_bridge() and bridge_context.commands[-1] == ("hue:1", {"on": False})
        print("6 storage: one encrypted file, nothing else; after the restart the pairing still holds")

        # ------------------------------------------------------------------ 7) entfernen
        assert await controller.unpair(node_id) is True
        await until(lambda: await_nothing(controller, node_id), "the controller forgot the device")
        await until(lambda: not bridge.overview()["controllers"], "the bridge has no controller any more")
        assert controller_context.gone and all(identifier.startswith(f"matter:{node_id}-") for identifier in controller_context.gone)
        print("7 removing: the controller signs off, the devices disappear, the bridge is free again")
    finally:
        pids = [context.real_helper._process.pid for context in (bridge_context, controller_context)
                if getattr(context, "real_helper", None) and context.real_helper._process]
        for plugin, name in ((bridge, "matter_a"), (controller, "matter_b")):
            await plugin.stop()
            await helpers.stop(name)
    assert not any(alive(pid) for pid in pids), "a helper program survived the end"

    # ------------------------------------------------------------------ 8) eingesperrt
    probe_dir = tmp / "probe"
    data_dir = tmp / "probe-data"
    probe_dir.mkdir()
    data_dir.mkdir()
    secret = data_dir / "secrets.json"
    secret.write_text('{"passwort": "geheim"}', encoding="utf-8")
    probe = probe_dir / "probe.mjs"
    probe.write_text(PROBE, encoding="utf-8")
    locked_in = helpers.start("probe", [NODE.parent, probe_dir, data_dir], data_dir, NODE,
                              ["--permission", f"--allow-fs-read={probe}", str(probe), str(secret), str(data_dir)])
    try:
        told = await asyncio.wait_for(locked_in.messages().__anext__(), 30)
    finally:
        await locked_in.stop()
    assert told == {"read": "ERR_ACCESS_DENIED", "list": "ERR_ACCESS_DENIED", "write": "ERR_ACCESS_DENIED", "spawn": "ERR_ACCESS_DENIED"}, told
    assert not (data_dir / "written-by-helper.txt").exists()
    print("8 confined: under --permission no reading and no writing in the data folder, no process of its own")


def await_nothing(plugin: matter.MatterPlugin, node_id: str) -> bool:
    """Has the plugin forgotten the node?"""
    return node_id not in plugin._nodes


asyncio.run(main())
print("ALL MATTER TESTS WITH NODE OK")
