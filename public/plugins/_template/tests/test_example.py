"""Tests for the example plugin. They run without ComfyOS, using the fake context of the SDK.

    python public/plugins/_template/tests/test_example.py [path-to-comfyos]
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "public" / "sdk"))

from plugin_api import Plugin, PluginError  # noqa: E402
from testing import FakeContext  # noqa: E402

# The template folder starts with "_", so it is loaded by path instead of by name.
spec = importlib.util.spec_from_file_location("example_plugin", PLUGIN_DIR / "__init__.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


async def main() -> None:
    """Run every check of the example plugin against a fake context: one lamp named from the settings, commands that
    report back, a state that survives a restart."""
    context = FakeContext("example")
    plugin = example.setup(context)
    assert isinstance(plugin, Plugin)
    await plugin.start()

    # 1) one lamp, off, dimmable by default, named from the settings
    lamp = (await plugin.devices())[0]
    assert lamp.id == "example:lamp" and lamp.kind == "light" and lamp.name == "Beispiel-Lampe"
    assert lamp.state == {"on": False, "brightness": 100}
    assert [capability.key for capability in lamp.capabilities] == ["on", "brightness"]
    context.settings.set("lamp_name", "Schreibtisch")
    context.settings.set("dimmable", False)
    lamp = (await plugin.devices())[0]
    assert lamp.name == "Schreibtisch" and lamp.state == {"on": False}
    print("1 devices(): one lamp, name and capabilities follow the settings")

    # 2) commands change the state and report it back
    assert await plugin.command("example:lamp", {"on": True}, "command") == {"on": True, "brightness": 100}
    assert await plugin.command("example:lamp", {"brightness": 40}, "flow") == {"on": True, "brightness": 40}
    assert await plugin.command("example:lamp", {"on": False}, "command") == {"on": False, "brightness": 40}
    for nonsense in ({}, {"brightness": "hell"}, {"brightness": 0}, {"brightness": 500}):
        try:
            await plugin.command("example:lamp", nonsense, "command")
            raise AssertionError(f"accepted: {nonsense}")
        except PluginError:
            pass
    try:
        await plugin.command("other:device", {"on": True}, "command")
        raise AssertionError("accepted a device of another plugin")
    except PluginError:
        pass
    assert plugin.status()["connected"] and "3 Befehle" in plugin.status()["message"]
    print("2 command(): on, brightness, rejects nonsense and foreign devices")

    # 3) the state survives a restart (kept in the plugin's data folder)
    await plugin.command("example:lamp", {"brightness": 70}, "command")
    await plugin.stop()
    restarted = example.setup(context)
    await restarted.start()
    context.settings.set("dimmable", True)
    assert (await restarted.devices())[0].state == {"on": True, "brightness": 70}
    print("3 state is restored after a restart")


asyncio.run(main())
print("ALL EXAMPLE PLUGIN TESTS OK")
