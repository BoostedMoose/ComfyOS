"""Tests for the Matter plugin itself and for installing Node.js. They run without ComfyOS and without Node:
the helper program is a ``FakeHelper`` of the SDK, the download a handler function.

    python public/plugins/matter/tests/test_matter_plugin.py [path-to-comfyos]

Covered:
    1  Node.js: one fixed version per system, wrong checksum refused, only the binary leaves the archive
       (paths that point outside the target are never written), download only from nodejs.org
    2  without Node the plugin waits; with Node it starts the helper locked in and hands it identity and storage
    3  sharing: nothing by default, share list and changes reach the helper, permanent endpoint numbers
    4  commands of a controller: carried out through ComfyOS; locked device -> "locked"; not shared -> "error"
    5  commissioning window, pairing code only while it is open and never in events, controllers
    6  storage of matter.js is written encrypted and handed back after a restart of the helper
    7  controller: commissioned devices appear, change, are switched, paired and removed
    8  everything the helper sends is checked: junk is dropped and changes nothing
"""
import asyncio
import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "public" / "sdk"))
sys.path.insert(0, str(ROOT / "public" / "plugins"))

import httpx  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from plugin_api import DeviceUnreachable, DeviceView, LightState, PluginError, SceneView  # noqa: E402
from testing import FakeContext  # noqa: E402

import matter  # noqa: E402
from matter import runtime, storage  # noqa: E402

NODE_BYTES = b"#!/bin/false\nthis stands for the node binary\n"
SECRETS = ["storage_key", "passcode"]
ACCESS = ("devices", "rooms", "scenes", "home")
matter.SYNC_DELAY_SECONDS = 0.01
matter.SAVE_DELAY_SECONDS = 0.01


def tar_archive(members: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    """Build a gzip-compressed tar archive in memory: regular files and, optionally, symbolic links."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755
            bundle.addfile(info, io.BytesIO(content))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            bundle.addfile(info)
    return buffer.getvalue()


def described(content: bytes, binary: str, file_name: str = "node-test.tar.xz") -> runtime.Archive:
    """Describe an archive built by the test, with its real checksum."""
    return runtime.Archive(file_name=file_name, sha256=hashlib.sha256(content).hexdigest(), binary=binary, megabytes=1)


def all_files(folder: Path) -> list[str]:
    """Every file below a folder, as relative paths."""
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())


# ---------------------------------------------------------------------------
# 1) Node.js
# ---------------------------------------------------------------------------
assert set(runtime.ARCHIVES) == {("linux", "arm64"), ("linux", "x64"), ("darwin", "arm64"), ("darwin", "x64"), ("win32", "x64"), ("win32", "arm64")}
for (system, processor), archive in runtime.ARCHIVES.items():
    assert runtime.NODE_VERSION in archive.file_name and len(archive.sha256) == 64 and int(archive.sha256, 16) >= 0
    assert archive.binary.endswith("node.exe" if system == "win32" else "/bin/node") and archive.binary.startswith("node-v")
    assert runtime.download_url(archive) == f"https://nodejs.org/dist/v{runtime.NODE_VERSION}/{archive.file_name}"
assert runtime.archive_for(("linux", "arm32")) is None and runtime.archive_for(("linux", "armv7l")) is None, "no build for 32-bit ARM"
assert "64-Bit" in runtime.unsupported_text()

BINARY = "node-test/bin/node"
good = tar_archive({"node-test/README.md": b"readme", BINARY: NODE_BYTES, "node-test/bin/npm": b"npm", "node-test/lib/x.js": b"x"})
base = FakeContext("matter").data_dir
target = runtime.unpack(good, described(good, BINARY), base, system="linux")
assert target == base / "runtime" / "bin" / "node" and target.read_bytes() == NODE_BYTES
assert all_files(base) == ["runtime/VERSION", "runtime/bin/node"], "only the binary and the version note are written"
assert (base / "runtime" / "VERSION").read_text().strip() == runtime.NODE_VERSION
if sys.platform != "win32":
    assert target.stat().st_mode & 0o111, "binary is not executable"
    assert runtime.installed_in(base) == target and runtime.find(Path("/nonexistent"), base) == target
(base / "runtime" / "VERSION").write_text("1.0.0\n")
assert runtime.installed_in(base) is None, "another version counts as not installed"

evil_base = FakeContext("matter").data_dir
evil = tar_archive({"../evil.txt": b"x", "/tmp/comfyos-evil.txt": b"x", "node-test/bin/../../../evil2.txt": b"x", BINARY: NODE_BYTES,
                    "node-test/bin/node/../../../../evil3.txt": b"x"}, links={"node-test/link": "/etc/passwd"})
runtime.unpack(evil, described(evil, BINARY), evil_base, system="linux")
assert all_files(evil_base) == ["runtime/VERSION", "runtime/bin/node"], all_files(evil_base)
assert not list(evil_base.parent.glob("evil*")) and not Path("/tmp/comfyos-evil.txt").exists(), "a path in the archive left the target"
for broken, why in ((tar_archive({"node-test/bin/other": b"x"}), "binary missing"),
                    (tar_archive({}, links={BINARY: "/bin/sh"}), "binary is a link")):
    try:
        runtime.unpack(broken, described(broken, BINARY), FakeContext("matter").data_dir, system="linux")
        raise AssertionError(f"unpacked although: {why}")
    except runtime.RuntimeProblem:
        pass
zipped = io.BytesIO()
with zipfile.ZipFile(zipped, "w") as package:
    package.writestr("node-test/node.exe", NODE_BYTES)
    package.writestr("../evil.exe", b"x")
windows_base = FakeContext("matter").data_dir
windows_target = runtime.unpack(zipped.getvalue(), described(zipped.getvalue(), "node-test/node.exe", "node-test.zip"), windows_base, system="win32")
assert windows_target == windows_base / "runtime" / "node.exe" and all_files(windows_base) == ["runtime/VERSION", "runtime/node.exe"]
try:
    runtime.verify(good + b"x", described(good, BINARY))
    raise AssertionError("wrong checksum accepted")
except runtime.RuntimeProblem as error:
    assert "Prüfsumme" in str(error)


async def check_install() -> None:
    """Installing through the plugin's declared connection: right archive, wrong checksum, server error."""
    answer = {"status": 200, "content": good}

    def node_site(request: httpx.Request) -> httpx.Response:
        return httpx.Response(answer["status"], content=answer["content"])

    key = runtime.system_key()
    original = runtime.ARCHIVES.get(key)
    runtime.ARCHIVES[key] = described(good, BINARY)
    try:
        context = FakeContext("matter", http_handler=node_site)
        installed = await runtime.install(context, context.data_dir)
        assert installed.read_bytes() == NODE_BYTES and len(context.requests) == 1
        assert context.requests[0].url.host == "nodejs.org" and context.requests[0].url.scheme == "https"
        for change, word in (({"content": good + b"manipulated"}, "Prüfsumme"), ({"status": 404}, "404"), ({"status": 302}, "302")):
            answer.update({"status": 200, "content": good})
            answer.update(change)
            fresh = FakeContext("matter", http_handler=node_site)
            try:
                await runtime.install(fresh, fresh.data_dir)
                raise AssertionError(f"installed although: {change}")
            except runtime.RuntimeProblem as error:
                assert word in str(error), str(error)
            assert all_files(fresh.data_dir) == [], "a refused download left files behind"
        del runtime.ARCHIVES[key]
        try:
            await runtime.install(context, context.data_dir)
            raise AssertionError("installed on a system without a build")
        except runtime.RuntimeProblem as error:
            assert "64-Bit" in str(error)
    finally:
        if original is not None:
            runtime.ARCHIVES[key] = original


asyncio.run(check_install())
print("1 Node.js: fixed version and checksum per system, wrong checksum refused, only the binary is unpacked, download from nodejs.org only")


# ---------------------------------------------------------------------------
# helpers for the plugin tests
# ---------------------------------------------------------------------------
def lamp_view(identifier: str = "hue:1", **fields: object) -> DeviceView:
    """A dimmable lamp of ComfyOS."""
    values = {"name": "Stehlampe", "kind": "light", "plugin": "hue", "room_id": "wohnen", "room": "Wohnzimmer", "controllable": True,
              "state": {"on": True, "brightness": 50}, "light": LightState()}
    values.update(fields)
    return DeviceView(id=identifier, **values)


def install_fake_node(context: FakeContext) -> Path:
    """Put a stand-in for the Node binary where the plugin looks for it."""
    return runtime.unpack(good, described(good, BINARY), context.data_dir)


async def until(condition: object, what: str, seconds: float = 3.0) -> None:
    """Wait until ``condition()`` is true, or fail with a message."""
    for _ in range(int(seconds / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"did not happen: {what}")


async def call_route(plugin: matter.MatterPlugin, method: str, path: str, **arguments: object) -> dict:
    """Call one of the plugin's endpoints directly (without HTTP)."""
    route = next(route for route in plugin.routes().routes if route.path == path and method in route.methods)
    return await route.endpoint(**arguments)


def new_context() -> FakeContext:
    """A context with the manifest's secrets, helper and permissions, and a few devices of ComfyOS."""
    context = FakeContext("matter", secrets=SECRETS, helper=True, access=ACCESS)
    context.device_views = [
        lamp_view(),
        DeviceView(id="hb:plug", name="Kaffeemaschine", kind="switch", plugin="qivicon", room_id="kueche", room="Küche",
                   controllable=True, locked=True, state={"on": False}),
        DeviceView(id="hb:therm", name="Heizung", kind="thermostat", plugin="qivicon", room_id="wohnen", room="Wohnzimmer",
                   controllable=True, state={"target": 21.0, "temperature": 20.0}),
        DeviceView(id="matter:peer9-1", name="Fremd", kind="light", plugin="matter", controllable=True, state={"on": True}),
    ]
    context.scene_views = [SceneView("abendrot", "Abendrot", ("wohnen",), False)]
    return context


async def started_plugin(context: FakeContext) -> matter.MatterPlugin:
    """A plugin whose helper has reported ready and started."""
    install_fake_node(context)
    plugin = matter.setup(context)
    await plugin.start()
    context.helper.feed({"type": "ready", "protocol": 1})
    await until(lambda: context.helper.sent_of("start"), "start sent")
    context.helper.feed({"type": "started"})
    await until(lambda: plugin.status()["connected"], "plugin online")
    return plugin


async def check_plugin() -> None:
    """Sections 2 to 8 in one event loop."""
    # ----------------------------------------------------------------------- 2) without and with Node
    bare = FakeContext("matter", secrets=SECRETS, helper=True, access=ACCESS)
    waiting = matter.setup(bare)
    await waiting.start()
    status = waiting.status()
    assert status["configured"] is False and status["connected"] is False and "Node.js" in status["message"], status
    assert status["egress"] == {"runtime": ["on_demand", status["egress"]["runtime"][1]]}, "the download target must never count as in use"
    assert bare.helper is None and await waiting.devices() == [] and bare.requests == [], "without Node nothing starts and nothing is fetched"
    overview = waiting.overview()
    assert overview["runtime"]["installed"] is False and overview["runtime"]["host"] == "nodejs.org" and overview["pairing"] is None
    await waiting.stop()

    context = new_context()
    node_path = install_fake_node(context)
    plugin = matter.setup(context)
    await plugin.start()
    helper = context.helper
    bundle = str(matter.BUNDLE)
    assert helper.program == str(node_path) and helper.args == ["--permission", f"--allow-fs-read={bundle}", bundle] and helper.env == {}
    assert plugin.status()["configured"] and not plugin.status()["connected"]
    try:
        await plugin.devices()
        raise AssertionError("reported devices before the helper started")
    except DeviceUnreachable:
        pass                                                    # ComfyOS keeps what it stored instead of treating devices as gone
    helper.feed({"type": "ready", "protocol": 1})
    await until(lambda: helper.sent_of("start"), "start sent")
    start = helper.sent_of("start")[0]
    passcode = start["bridge"]["passcode"]
    assert 0 < passcode < 99999999 and passcode not in matter.INVALID_PASSCODES and 0 <= start["bridge"]["discriminator"] <= 4095
    assert (start["bridge"]["port"], start["controller"]["port"]) == (5540, 5541) and start["bridge"]["vendor_id"] == 0xFFF1
    assert start["endpoints"] == [] and not helper.sent_of("storage"), "nothing is shared by default; a fresh store sends nothing"
    assert context.secrets.get("passcode") == str(passcode) and len(context.secrets.get("storage_key")) > 40
    assert "passcode" not in context.settings.values and str(passcode) not in json.dumps(context.settings.values)
    helper.feed({"type": "started"})
    await until(lambda: plugin.status()["connected"], "plugin online")
    assert await plugin.devices() == [] and context.reloads >= 1
    print("2 without Node the plugin waits; with Node the helper starts locked in and gets passcode, ports and an empty share list")

    # ----------------------------------------------------------------------- 3) sharing
    offered = {item["key"]: item for item in plugin.overview()["offered"]}
    assert set(offered) == {"hue:1", "hb:plug", "hb:therm", "room:wohnen", "all-lights", "scene:abendrot", "home"}, set(offered)
    assert not any(item["shared"] for item in offered.values()) and offered["hb:plug"]["locked"] and offered["room:wohnen"]["group"] == "home"
    assert plugin.overview()["shares"] == {"used": 0, "advice": 50}
    plugin.set_shared("hue:1", True)
    await until(lambda: helper.sent_of("endpoints"), "share list sent")
    entry = helper.sent_of("endpoints")[-1]["endpoints"]
    assert entry == [{"number": 10, "kind": "dimmable_light", "name": "Stehlampe", "serial": "comfyos-10", "reachable": True, "locked": False,
                      "state": {"on": True, "level": 127}}], entry
    context.change("hue:1", {"on": False, "brightness": 20})
    await until(lambda: helper.sent_of("update"), "change sent")
    assert helper.sent_of("update")[-1] == {"type": "update", "number": 10, "state": {"on": False, "level": 51}, "reachable": True, "locked": False}
    context.change("hue:1", {"locked": True, "online": False})
    await until(lambda: len(helper.sent_of("update")) == 2, "lock and reachability sent")
    assert helper.sent_of("update")[-1]["locked"] is True and helper.sent_of("update")[-1]["reachable"] is False
    context.change("hue:1", {"locked": False, "online": True})
    await until(lambda: len(helper.sent_of("update")) == 3, "unlock sent")
    for refused_key in ("matter:peer9-1", "gibts:nicht"):
        try:
            plugin.set_shared(refused_key, True)
            raise AssertionError(f"shared {refused_key}")
        except PluginError:
            pass
    plugin.set_shared("hb:plug", True)
    plugin.set_shared("room:wohnen", True)
    await until(lambda: len(helper.sent_of("endpoints")[-1]["endpoints"]) == 3, "three endpoints shared")
    numbers = {item["kind"] + ":" + item["name"]: item["number"] for item in helper.sent_of("endpoints")[-1]["endpoints"]}
    assert numbers == {"dimmable_light:Stehlampe": 10, "on_off_plug:Kaffeemaschine": 11, "dimmable_light:Wohnzimmer Licht": 12}, numbers
    assert plugin.overview()["shares"]["used"] == 3 and plugin.status()["message"].startswith("3 Geräte freigegeben")
    # several at once (all, a room, a group): unknown and stale keys are skipped, own controller devices never shared
    assert plugin.set_shared_many(["hb:therm", "gibts:nicht", "matter:peer9-1", "all-lights"], True) == 2
    await until(lambda: len(helper.sent_of("endpoints")[-1]["endpoints"]) == 5, "two more endpoints shared")
    assert not plugin.shares.is_shared("matter:peer9-1") and not plugin.shares.is_shared("gibts:nicht")
    assert plugin.set_shared_many(["hb:therm", "all-lights"], False) == 2
    await until(lambda: len(helper.sent_of("endpoints")[-1]["endpoints"]) == 3, "bulk hide reached the helper")
    assert (await call_route(plugin, "PUT", "/shares", request=matter.routes.ShareRequest(keys=["hb:therm"], shared=True)))["shares"]["used"] == 4
    plugin.set_shared("hb:therm", False)
    await until(lambda: len(helper.sent_of("endpoints")[-1]["endpoints"]) == 3, "back to three endpoints")
    context.device_views.append(lamp_view("hue:2", name="Neue Lampe"))
    context.change("")                                          # the device list of ComfyOS changed: read again
    await until(lambda: "hue:2" in {item["key"] for item in plugin.overview()["offered"]}, "new device offered")
    assert len(helper.sent_of("endpoints")[-1]["endpoints"]) == 3, "a new device is not shared by itself"
    print("3 sharing: nothing by default; share list, values, lock and reachability reach the helper; own controller devices refused")

    # ----------------------------------------------------------------------- 4) commands of a controller
    helper.feed({"type": "command", "id": 1, "number": 10, "state": {"level": 203, "on": True}, "fabric": 1})
    await until(lambda: helper.sent_of("reply"), "reply sent")
    assert helper.sent_of("reply")[-1] == {"type": "reply", "id": 1, "status": "ok"}
    assert context.commands == [("hue:1", {"on": True, "brightness": 80})], context.commands
    helper.feed({"type": "command", "id": 2, "number": 11, "state": {"on": True}, "fabric": 1})          # the locked socket
    await until(lambda: len(helper.sent_of("reply")) == 2, "reply for the locked device")
    assert helper.sent_of("reply")[-1] == {"type": "reply", "id": 2, "status": "locked"} and len(context.commands) == 1
    helper.feed({"type": "command", "id": 3, "number": 99, "state": {"on": True}, "fabric": 1})          # never shared
    helper.feed({"type": "command", "id": 4, "number": 12, "state": {"on": False}, "fabric": 2})         # the room light
    helper.feed({"type": "command", "id": 5, "number": 10, "state": {"contact": True}, "fabric": 1})     # nothing a lamp can do
    await until(lambda: len(helper.sent_of("reply")) == 5, "three more replies")
    replies = {reply["id"]: reply["status"] for reply in helper.sent_of("reply")}
    assert replies == {1: "ok", 2: "locked", 3: "error", 4: "ok", 5: "error"}, replies
    assert context.room_light_calls == [("wohnen", False, None)] and len(context.commands) == 1
    context.command_error = DeviceUnreachable("Bridge antwortet nicht")
    helper.feed({"type": "command", "id": 6, "number": 10, "state": {"on": True}, "fabric": 1})
    await until(lambda: len(helper.sent_of("reply")) == 6, "reply for an unreachable device")
    assert helper.sent_of("reply")[-1]["status"] == "error"
    context.command_error = None
    plugin.set_shared("hue:1", False)
    await until(lambda: len(helper.sent_of("endpoints")[-1]["endpoints"]) == 2, "lamp no longer shared")
    helper.feed({"type": "command", "id": 7, "number": 10, "state": {"on": True}, "fabric": 1})          # shared a moment ago
    await until(lambda: len(helper.sent_of("reply")) == 7, "reply for the unshared device")
    assert helper.sent_of("reply")[-1]["status"] == "error" and len(context.commands) == 1, "a device that is not shared was switched"
    plugin.set_shared("hue:1", True)
    await until(lambda: len(helper.sent_of("endpoints")[-1]["endpoints"]) == 3, "lamp shared again")
    assert {item["number"] for item in helper.sent_of("endpoints")[-1]["endpoints"]} == {10, 11, 12}, "a device keeps its endpoint for good"
    print("4 commands: through ComfyOS only; locked -> locked, not shared -> error, unreachable -> error; endpoint numbers are permanent")

    # ----------------------------------------------------------------------- 5) commissioning, pairing code, controllers
    rows = ["1" * 21] * 21
    helper.feed({"type": "pairing", "manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00", "rows": rows, "commissioned": False})
    helper.feed({"type": "fabrics", "fabrics": []})
    await until(lambda: plugin.overview()["pairing"] is not None, "pairing known")
    assert plugin.overview()["pairing"] == {"commissioned": False, "open_until": 0.0}, "the code is not given out while the window is closed"
    now = {"time": 1_000_000.0}
    plugin.clock = lambda: now["time"]
    assert (await call_route(plugin, "POST", "/commissioning", request=matter.routes.WindowRequest(open=True)))["pairing"]["manual"] == "34970112332"
    assert helper.sent_of("commissioning")[-1] == {"type": "commissioning", "open": True}
    pairing = plugin.overview()["pairing"]
    assert pairing["qr"].startswith("MT:") and pairing["rows"] == rows and pairing["open_until"] == now["time"] + 900
    now["time"] += 901                                          # 15 minutes later the window closes by itself
    plugin.sync_soon()
    await until(lambda: helper.sent_of("commissioning")[-1] == {"type": "commissioning", "open": False}, "window closed after 15 minutes")
    assert "manual" not in plugin.overview()["pairing"]
    await plugin.open_window(True)
    helper.feed({"type": "fabrics", "fabrics": [{"index": 1, "label": "", "vendor": 4631}, {"index": 2, "label": "Wohnung", "vendor": 4937}]})
    await until(lambda: len(plugin.overview()["controllers"]) == 2, "controllers known")
    assert plugin.overview()["controllers"] == [{"index": 1, "number": 1, "name": "Amazon Alexa"}, {"index": 2, "number": 2, "name": "Wohnung"}]
    # the user's own name for a controller: shown instead, cut and cleaned, empty brings the original back
    assert plugin.rename_controller(1, "  Echo   im Flur\n ") == "Echo im Flur" and plugin.overview()["controllers"][0]["name"] == "Echo im Flur"
    assert len(plugin.rename_controller(2, "x" * 200)) == 40 and plugin.rename_controller(2, "") == "Wohnung"
    assert context.settings.get("controller_names") == {"1": "Echo im Flur"}
    assert (await call_route(plugin, "PUT", "/controllers/{index}/name", index=1, request=matter.routes.NameRequest(name="Alexa")))["name"] == "Alexa"
    assert plugin.rename_controller(1, "Amazon Alexa") == "Amazon Alexa" and context.settings.get("controller_names") == {}
    try:
        plugin.rename_controller(9, "Gibt es nicht")
        raise AssertionError("renamed a controller that does not exist")
    except PluginError:
        pass
    plugin.rename_controller(2, "Küchen-Tablet")
    assert plugin.window_open_until == 0.0, "a new controller closes the window"
    assert any("Amazon Alexa" in event for event in context.events) and "2 Controller" in plugin.status()["message"]
    told = json.dumps(context.events) + json.dumps(context.settings.values)
    assert "34970112332" not in told and str(passcode) not in told and "MT:Y" not in told, "pairing code in events or settings"
    await plugin.remove_controller(2)
    assert helper.sent_of("remove_fabric")[-1] == {"type": "remove_fabric", "index": 2}
    try:
        await plugin.remove_controller(7)
        raise AssertionError("removed a controller that does not exist")
    except PluginError:
        pass
    # Apple Home joins twice (home and keychain): one controller. Numbers follow the position, not the fabric index –
    # after two removed controllers the next one is "Controller 2" again, not "Controller 4".
    events_before = len(context.events)
    helper.feed({"type": "fabrics", "fabrics": [{"index": 1, "label": "", "vendor": 4631}, {"index": 4, "label": "", "vendor": 4937}]})
    await until(lambda: [entry["index"] for entry in plugin.overview()["controllers"]] == [1, 4], "Apple Home known")
    helper.feed({"type": "fabrics", "fabrics": [{"index": 1, "label": "", "vendor": 4631}, {"index": 4, "label": "", "vendor": 4937},
                                                {"index": 5, "label": "", "vendor": 4996}]})
    await until(lambda: len(plugin._fabrics) == 3, "Apple's second fabric known")
    assert plugin.overview()["controllers"] == [{"index": 1, "number": 1, "name": "Amazon Alexa"}, {"index": 4, "number": 2, "name": "Apple Home"}]
    assert [event for event in context.events[events_before:] if "gekoppelt" in event] == ["Matter: Controller „Apple Home“ ist jetzt gekoppelt"]
    assert "2 Controller" in plugin.status()["message"]
    await plugin.remove_controller(4)
    assert helper.sent_of("remove_fabric")[-2:] == [{"type": "remove_fabric", "index": 4}, {"type": "remove_fabric", "index": 5}]
    unnamed = matter.group_controllers([matter.protocol.Fabric(7, "", 1), matter.protocol.Fabric(9, "", 4996), matter.protocol.Fabric(12, "", 4937),
                                        matter.protocol.Fabric(13, "", 4937)])
    assert [(entry.number, entry.index, entry.name) for entry in unnamed] == [(1, 7, "Controller 1"), (2, 9, "Apple Home"), (3, 13, "Apple Home")]
    helper.feed({"type": "fabrics", "fabrics": [{"index": 1, "label": "", "vendor": 4631}, {"index": 2, "label": "Wohnung", "vendor": 4937}]})
    await until(lambda: [entry["index"] for entry in plugin.overview()["controllers"]] == [1, 2], "controllers as before")
    print("5 commissioning: window for 15 minutes, pairing code only while it is open and never in events; controllers named and removable")

    # ----------------------------------------------------------------------- 6) storage
    helper.feed({"type": "storage", "ops": [["bridge", "set", "fabrics", "keys", '{"k":"FABRIC-SECRET"}'], ["controller", "set", "nodes", "peer2", '"x"']]})
    store_file = context.data_dir / "matter.bin"
    await until(store_file.exists, "storage written")
    assert b"FABRIC-SECRET" not in store_file.read_bytes() and store_file.read_bytes().startswith(storage.MAGIC)
    helper.feed({"type": "ready", "protocol": 1})               # the helper crashed and was restarted by ComfyOS
    await until(lambda: len(helper.sent_of("start")) == 2, "start sent again")
    assert not plugin.status()["connected"], "after a restart the plugin is offline until the helper has started"
    handed_back = [message for message in helper.sent_of("storage")]
    assert {message["namespace"] for message in handed_back} == {"bridge", "controller"}
    assert handed_back[0]["values"] == {"fabrics": {"keys": '{"k":"FABRIC-SECRET"}'}}
    assert helper.sent.index(handed_back[-1]) < helper.sent.index(helper.sent_of("start")[1]), "storage must arrive before start"
    assert len(helper.sent_of("start")[1]["endpoints"]) == 3 and helper.sent_of("start")[1]["bridge"]["passcode"] == passcode
    helper.feed({"type": "started"})
    await until(lambda: plugin.status()["connected"], "online again")
    print("6 storage: written encrypted, handed back before start after a restart of the helper; passcode and share list stay")

    # ----------------------------------------------------------------------- 7) controller
    node = {"type": "node", "node": "peer2", "online": True, "name": "Hue Bridge", "vendor": "Signify", "product": "BSB002", "serial": "x",
            "endpoints": [{"endpoint": 0, "types": [22], "name": "", "reachable": True, "state": {}},
                          {"endpoint": 3, "types": [257, 19], "name": "Leselampe", "reachable": True, "state": {"on": True, "level": 127}}]}
    reloads = context.reloads
    helper.feed(node)
    await until(lambda: context.reloads > reloads, "device list announced")
    found = await plugin.devices()
    assert [(device.id, device.name, device.kind, device.discovered) for device in found] == [("matter:peer2-3", "Leselampe", "light", True)]
    node["endpoints"][1]["state"] = {"on": False, "level": 127}
    helper.feed(node)
    await until(lambda: ("matter:peer2-3", {"on": False}) in context.changes, "change of the Matter device reported")
    node["online"] = False
    helper.feed(node)
    await until(lambda: ("matter:peer2-3", {"online": False}) in context.changes, "device reported unreachable")
    node["online"] = True
    helper.feed(node)

    async def answer(message_type: str, **result: object) -> dict:
        """Wait for the next request of this type and answer it like the helper would."""
        count = len(helper.sent_of(message_type))
        await until(lambda: len(helper.sent_of(message_type)) > count, f"{message_type} sent")
        request = helper.sent_of(message_type)[-1]
        helper.feed({"type": "result", "id": request["id"], **result})
        return request

    switching = asyncio.create_task(plugin.command("matter:peer2-3", {"on": True, "brightness": 40}, "command"))
    request = await answer("invoke", ok=True)
    assert (request["node"], request["endpoint"], request["state"]) == ("peer2", 3, {"level": 102})
    assert await switching == {"on": True, "brightness": 40}
    failing = asyncio.create_task(plugin.command("matter:peer2-3", {"on": False}, "flow"))
    await answer("invoke", ok=False, error="ReceivedFailure")
    try:
        await failing
        raise AssertionError("a refused command was reported as done")
    except DeviceUnreachable:
        pass
    for bad_id, bad_state in (("matter:peer7-3", {"on": True}), ("hue:1", {"on": True}), ("matter:peer2-3", {"effect": "candle"})):
        try:
            await plugin.command(bad_id, bad_state, "command")
            raise AssertionError(f"accepted {bad_id} {bad_state}")
        except PluginError:
            pass
    pairing_task = asyncio.create_task(plugin.pair(" 3497-011-2332 "))
    request = await answer("pair", ok=True, node="peer4", attestation=["warning:DclServiceUnavailable"])
    assert request["code"] == "34970112332" and (await pairing_task) == {"node": "peer4", "attestation": ["warning:DclServiceUnavailable"]}
    refused_pairing = asyncio.create_task(plugin.pair("MT:Y.K90AFN00KA0648G00"))
    await answer("pair", ok=False, error="DiscoveryError")
    try:
        await refused_pairing
        raise AssertionError("a failed pairing was reported as done")
    except PluginError as error:
        assert "DiscoveryError" in str(error)
    sent_pairs = len(helper.sent_of("pair"))
    try:
        await plugin.pair("kein code")
        raise AssertionError("a malformed code was sent to the helper")
    except PluginError:
        assert len(helper.sent_of("pair")) == sent_pairs
    assert [entry["attestation"] for entry in plugin.overview()["nodes"]] == [[]] and context.settings.get("attestation") == {
        "peer4": ["warning:DclServiceUnavailable"]}
    removing = asyncio.create_task(plugin.unpair("peer2"))
    await answer("unpair", ok=True, clean=False)
    assert await removing is False
    helper.feed({"type": "node_removed", "node": "peer2"})
    await until(lambda: context.gone == ["matter:peer2-3"], "device forgotten")
    assert await plugin.devices() == []
    try:
        await plugin.unpair("peer2")
        raise AssertionError("removed a device that is gone")
    except PluginError:
        pass
    print("7 controller: devices appear as matter:<node>-<endpoint>, changes arrive, commands, pairing and removal go through the helper")

    # ----------------------------------------------------------------------- 8) foreign input
    before = (len(helper.sent), len(context.commands), len(context.changes), plugin.overview()["controllers"])
    for junk in ("text", 7, {}, {"type": "gibtsnicht"}, {"type": "command", "id": "x", "number": 10, "state": {"on": True}},
                 {"type": "command", "id": 9, "number": 10, "state": {"level": 9999}}, {"type": "command", "id": 9, "number": 10, "state": "on"},
                 {"type": "fabrics", "fabrics": "alle"}, {"type": "node", "node": "../x", "online": True, "endpoints": []},
                 {"type": "storage", "ops": [["fremd", "set", "a", "b", "c"]]}, {"type": "pairing", "manual": "<script>", "qr": "x", "rows": [], "commissioned": True},
                 {"type": "result", "id": 1, "ok": "ja"}, {"type": "error", "code": "mit text und <html>"}):
        helper.feed(junk) if isinstance(junk, dict) else helper._incoming.put_nowait(junk)
    helper.feed({"type": "error", "code": "node_report_failed"})
    helper.feed({"type": "command", "id": 10, "number": 11, "state": {"on": False}, "fabric": 1})        # still works afterwards
    await until(lambda: len(helper.sent) == before[0] + 1, "the plugin keeps working after junk")
    assert helper.sent[-1] == {"type": "reply", "id": 10, "status": "locked"}
    assert (len(context.commands), len(context.changes), plugin.overview()["controllers"]) == before[1:], "junk changed something"
    helper.feed({"type": "ready", "protocol": 99})              # a helper of another version
    await until(lambda: plugin.problem, "version mismatch noticed")
    assert len(helper.sent_of("start")) == 2 and not plugin.status()["connected"], "started a helper that speaks another protocol"

    # routes: errors of the plugin become HTTP errors with the text for the user
    try:
        await call_route(plugin, "POST", "/commissioning", request=matter.routes.WindowRequest(open=True))
        raise AssertionError("opened the window while Matter is not running")
    except HTTPException as error:
        assert error.status_code == 502
    try:
        await call_route(plugin, "PUT", "/shares", request=matter.routes.ShareRequest(key="matter:peer9-1", shared=True))
        raise AssertionError("shared a device of the own controller through the route")
    except HTTPException as error:
        assert error.status_code == 400 and "Matter" in error.detail
    try:
        await call_route(plugin, "DELETE", "/controllers/{index}", index=0)
        raise AssertionError("accepted controller index 0")
    except HTTPException as error:
        assert error.status_code == 404
    assert (await call_route(plugin, "GET", "/status"))["shares"]["used"] == 3
    await plugin.stop()
    assert helper.sent[-1]["type"] != "start" and plugin.status()["configured"] is False
    print("8 foreign input: malformed messages are dropped and change nothing; another protocol version is not started; routes map errors")

    # an unreadable store is set aside, never overwritten silently
    again = new_context()
    (again.data_dir / "matter.bin").write_bytes(b"CMS1" + b"\x00" * 64)
    restarted = await started_plugin(again)
    assert (again.data_dir / "matter.bin.unreadable").exists() and any("neu koppeln" in event for event in again.events)
    await restarted.stop()

    # a missing bundle is reported instead of starting anything
    missing = new_context()
    install_fake_node(missing)
    original_bundle, matter.BUNDLE = matter.BUNDLE, Path("/nonexistent/bridge.mjs")
    try:
        broken = matter.setup(missing)
        await broken.start()
        assert missing.helper is None and "Beiprogramm" in broken.problem and broken.status()["configured"] is False
    finally:
        matter.BUNDLE = original_bundle


asyncio.run(check_plugin())
print("ALL MATTER PLUGIN TESTS OK")
