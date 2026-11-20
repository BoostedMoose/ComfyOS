"""ComfyOS – security: helper programs of the plugins (helpers.py, plugin API revision 3).

A plugin may start a process of its own – but only through `context.start_helper` and only within the limits the
core sets. A small Python script serves as the helper program here; this test does not need Node.

    1  path: only a file in the folder of the plugin or in its data folder – not beyond through a symlink either
    2  arguments and environment are limited (number, length, names)
    3  environment of the helper program: empty except for HOME (= data folder) and LANG, working directory = data folder;
       no variable of the core arrives
    4  exchange as JSON lines; unreadable input is counted and discarded
    5  stderr is foreign text: its content is neither in the log nor in the event log
    6  a line that is too long and a flood of lines end the helper program; it restarts under supervision
    7  crash: restart with a pause
    8  stopping cleans up: process gone, `messages()` ends; stdin closed → the helper program ends by itself
    9  plugin: without "helper" in the manifest no start; disabling the plugin ends the helper program
    10 in the core only helpers.py starts processes (and the known tools), plugins never themselves

    python tests/security/test_helpers.py [path-to-comfyos]
"""
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("helpers")
os.environ["GEHEIM_IM_CORE"] = "darf-nie-ankommen"

from app import events, helpers, plugin_api, plugins, settings_store, tasks  # noqa: E402

tasks.FIRST_PAUSE = 0.05
SECRET_TEXT = "GEHEIMER-TEXT-AUF-STDERR"
PLUGIN_DIR = tmp / "plugin"
DATA_DIR = tmp / "daten"
OUTSIDE = tmp / "draussen"
for folder in (PLUGIN_DIR, DATA_DIR, OUTSIDE):
    folder.mkdir()

SCRIPT = f'''#!{sys.executable}
"""Beiprogramm des Tests: liest JSON-Zeilen und tut, was „do“ verlangt."""
import json
import os
import sys

for line in sys.stdin:
    message = json.loads(line)
    do = message.get("do")
    if do == "env":
        print(json.dumps({{"env": dict(os.environ), "cwd": os.getcwd()}}), flush=True)
    elif do == "echo":
        print(json.dumps({{"echo": message.get("value")}}), flush=True)
    elif do == "junk":
        print("kein json", flush=True)
        print("[1, 2]", flush=True)
        print(json.dumps({{"after": "junk"}}), flush=True)
    elif do == "stderr":
        print("{SECRET_TEXT}", file=sys.stderr, flush=True)
        print(json.dumps({{"done": True}}), flush=True)
    elif do == "long":
        print("x" * (2 * 1024 * 1024), flush=True)
    elif do == "flood":
        sys.stdout.write("{{}}\\n" * 5000)
        sys.stdout.flush()
    elif do == "exit":
        sys.exit(3)
with open("ended", "w") as marker:          # stdin ist zu: von selbst geendet
    marker.write("ok")
'''


def write_script(folder: Path, name: str = "helper.py") -> Path:
    """Write the test helper program into a folder and make it executable."""
    path = folder / name
    path.write_text(SCRIPT, encoding="utf-8")
    path.chmod(0o755)
    return path


def refused(call) -> str:
    """Run a call that must fail at the limits; returns the text of the HelperError."""
    try:
        call()
    except helpers.HelperError as exc:
        return str(exc)
    raise AssertionError("not rejected")


async def next_message(stream, timeout: float = 10.0) -> dict:
    """The next message of a helper program, with a time limit."""
    return await asyncio.wait_for(stream.__anext__(), timeout)


def alive(pid: int) -> bool:
    """Is the process with this number still running?"""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def wait_for(condition, what: str, timeout: float = 10.0) -> None:
    """Wait until a condition holds – otherwise fail with a message."""
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"did not happen: {what}")


class Capture(logging.Handler):
    """Records every log line of the core."""

    def __init__(self):
        """Empty list of the lines."""
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        """Zeile samt eingesetzten Werten merken."""
        self.lines.append(record.getMessage())


async def main() -> None:
    """All sections in one event loop."""
    script = write_script(PLUGIN_DIR)
    roots = [PLUGIN_DIR, DATA_DIR]

    # ------------------------------------------------------------------ 1) Pfad
    outside = write_script(OUTSIDE)
    assert "außerhalb" in refused(lambda: helpers.checked_program(outside, roots))
    assert "außerhalb" in refused(lambda: helpers.checked_program(PLUGIN_DIR / ".." / "draussen" / "helper.py", roots))
    assert "außerhalb" in refused(lambda: helpers.checked_program(sys.executable, roots))
    assert "außerhalb" in refused(lambda: helpers.checked_program(PLUGIN_DIR, roots)), "the folder itself is not a program"
    link = PLUGIN_DIR / "link.py"
    link.symlink_to(outside)                                      # lies in the folder but points outside
    assert "außerhalb" in refused(lambda: helpers.checked_program(link, roots))
    folder_link = DATA_DIR / "ordner"
    folder_link.symlink_to(OUTSIDE, target_is_directory=True)
    assert "außerhalb" in refused(lambda: helpers.checked_program(folder_link / "helper.py", roots))
    assert "fehlt" in refused(lambda: helpers.checked_program(PLUGIN_DIR / "gibtsnicht", roots))
    (PLUGIN_DIR / "unterordner").mkdir()
    assert "fehlt" in refused(lambda: helpers.checked_program(PLUGIN_DIR / "unterordner", roots))
    assert helpers.checked_program("helper.py", roots) == script.resolve(), "relative counts from the folder of the plugin"
    inside = write_script(DATA_DIR, "eigenes.py")
    assert helpers.checked_program(inside, roots) == inside.resolve()
    print("1 path: only files in the plugin or data folder; outside, by .. and through symlinks rejected")

    # ------------------------------------------------------------------ 2) Arguments and environment
    assert helpers.checked_args(["--a", "b"]) == ["--a", "b"]
    for bad in (["x"] * (helpers.MAX_ARGS + 1), ["x" * (helpers.MAX_ARG_LENGTH + 1)], [1], ["a\x00b"], "ein-text"):
        refused(lambda bad=bad: helpers.checked_args(bad))
    env = helpers.build_env(DATA_DIR, {"MATTER_PORT": "5540"})
    assert env["HOME"] == str(DATA_DIR) and env["LANG"] and env["MATTER_PORT"] == "5540"
    assert set(env) - {"SystemRoot"} == {"HOME", "LANG", "MATTER_PORT"}, env
    for bad in ({"klein": "x"}, {"1A": "x"}, {"A-B": "x"}, {"HOME": "/"}, {"LANG": "x"}, {"A": 1}, {"A": "x" * 5000}, {"A": "a\x00b"},
                {f"V{n}": "x" for n in range(helpers.MAX_ENV + 1)}):
        refused(lambda bad=bad: helpers.build_env(DATA_DIR, bad))
    print("2 arguments and environment: number, length and names limited; HOME and LANG are only set by the core")

    # ------------------------------------------------------------------ 3) Environment and working directory in the process
    capture = Capture()
    logging.getLogger("comfyos").addHandler(capture)
    helper = helpers.start("probe", roots, DATA_DIR, script, [], {"EIGENE": "ja"})
    assert "schon" in refused(lambda: helpers.start("probe", roots, DATA_DIR, script)), "second helper program of the same plugin"
    stream = helper.messages()
    await wait_for(lambda: helper.running, "helper program running")
    await helper.send({"do": "env"})
    told = await next_message(stream)
    assert told["env"].get("HOME") == str(DATA_DIR) and told["env"].get("EIGENE") == "ja" and told["cwd"] == str(DATA_DIR.resolve()), told
    leaked = set(told["env"]) - {"HOME", "LANG", "EIGENE", "LC_CTYPE"}       # Python sets LC_CTYPE itself at the start
    assert not leaked and "GEHEIM_IM_CORE" not in told["env"] and "COMFYOS_HOME" not in told["env"] and "PATH" not in told["env"], leaked
    print("3 environment: only HOME, LANG and what was declared; the working directory is the data folder; nothing of the core arrives")

    # ------------------------------------------------------------------ 4) JSON-Zeilen
    await helper.send({"do": "echo", "value": {"ä": [1, 2.5, None]}})
    assert await next_message(stream) == {"echo": {"ä": [1, 2.5, None]}}
    before = helper.errors
    await helper.send({"do": "junk"})
    assert await next_message(stream) == {"after": "junk"} and helper.errors == before + 2, helper.errors
    try:
        await helper.send({"value": "x" * (helpers.MAX_LINE_BYTES + 10)})
        raise AssertionError("a message that is too long was sent")
    except helpers.HelperError:
        pass
    print("4 JSON lines: objects arrive, unreadable input and non-objects are counted and discarded")

    # ------------------------------------------------------------------ 5) stderr
    await helper.send({"do": "stderr"})
    assert await next_message(stream) == {"done": True}
    await wait_for(lambda: helper.errors == before + 3, "stderr line counted")
    logged = "\n".join(capture.lines) + "\n".join(e["message"] for e in events.recent(50))
    assert SECRET_TEXT not in logged, "content of stderr in the log or event log"
    assert "Beiprogramm meldet Fehler" in logged, logged[-400:]
    print("5 stderr: counted and reported, the content is nowhere")

    # ------------------------------------------------------------------ 6) line too long, flood
    for what in ("long", "flood"):
        pid, starts = helper._process.pid, helper.starts
        await helper.send({"do": what})
        await wait_for(lambda: helper.starts == starts + 1 and helper.running, f"restart after {what}")
        assert not alive(pid), f"process not ended after {what}"
        while not helper._queue.empty():                          # lines up to the limit have arrived – irrelevant here
            helper._queue.get_nowait()
        await helper.send({"do": "echo", "value": what})
        assert await next_message(stream) == {"echo": what}
    assert any("HelperError" in m for m in (e["message"] for e in events.recent(50))), "termination not reported"
    print("6 line too long and flood: helper program ended, restarted under supervision, usable again afterwards")

    # ------------------------------------------------------------------ 7) Absturz
    pid, starts = helper._process.pid, helper.starts
    await helper.send({"do": "exit"})
    await wait_for(lambda: helper.starts == starts + 1 and helper.running, "restart after a crash")
    assert not alive(pid)
    try:
        dead = helpers.Helper("x", script, [], {}, DATA_DIR)
        await dead.send({"do": "echo"})
        raise AssertionError("sent to a helper program that is not running")
    except helpers.HelperError:
        pass
    print("7 crash: restart with a pause; sending without a running process fails cleanly")

    # ------------------------------------------------------------------ 8) Stoppen
    marker = DATA_DIR / "ended"
    marker.unlink(missing_ok=True)
    pid = helper._process.pid
    await helper.stop()
    assert not alive(pid) and not helper.running and helper.stopped and helpers.get("probe") is None
    assert marker.read_text() == "ok", "the helper program did not end by itself when stdin closed"
    assert helpers.task_name("probe") not in tasks.running()
    try:
        await next_message(stream, 2.0)
        raise AssertionError("messages() does not end after stop()")
    except StopAsyncIteration:
        pass
    await helper.stop()                                           # doppelt: harmlos
    second = helpers.start("probe", roots, DATA_DIR, script)      # after stopping a new one may start
    await wait_for(lambda: second.running, "second helper program running")
    pid = second._process.pid
    await tasks.cancel_all()                                      # Herunterfahren
    assert not alive(pid), "the helper program survives the shutdown"
    print("8 stopping and shutdown: process gone, messages() ends; stdin closed → the helper program ends by itself")

    # ------------------------------------------------------------------ 9) through a plugin: the manifest decides
    def write_plugin(pid_: str, manifest: dict) -> None:
        """Create a user-added plugin with the test helper program."""
        folder = plugins.USER_DIR / pid_
        folder.mkdir(parents=True)
        base = {"id": pid_, "name": pid_.capitalize(), "version": "1.0.0", "api": plugin_api.API_VERSION, "api_revision": 3,
                "entry": "__init__:setup", "id_prefixes": [pid_ + ":"]}
        (folder / "manifest.json").write_text(json.dumps({**base, **manifest}), encoding="utf-8")
        (folder / "__init__.py").write_text(
            "from plugin_api import Plugin\n\n\nclass P(Plugin):\n    pass\n\n\ndef setup(context):\n    return P(context)\n", encoding="utf-8")
        write_script(folder)

    write_plugin("ohne", {})
    write_plugin("mit", {"helper": {"desc": "Test-Beiprogramm"}})
    plugins.discover()
    for pid_ in ("ohne", "mit"):
        await plugins.set_enabled(pid_, True)
    assert plugins.public(plugins.get("mit"))["helper"] == "Test-Beiprogramm" and plugins.public(plugins.get("ohne"))["helper"] is None
    try:
        plugins.active("ohne").context.start_helper("helper.py")
        raise AssertionError("helper program started without 'helper' in the manifest")
    except plugin_api.PluginError as exc:
        assert "Manifest" in str(exc)
    context = plugins.active("mit").context
    for bad in (str(outside), sys.executable, "../ohne/helper.py"):
        try:
            context.start_helper(bad)
            raise AssertionError(f"a foreign program was started: {bad}")
        except plugin_api.PluginError:
            pass
    started = context.start_helper("helper.py", [], env={"VOM_PLUGIN": "1"})
    await wait_for(lambda: started.running, "helper program of the plugin running")
    await started.send({"do": "env"})
    told = await next_message(started.messages())
    assert told["env"]["HOME"] == str(context.data_dir) and told["cwd"] == str(context.data_dir.resolve()), told
    pid = started._process.pid
    await plugins.set_enabled("mit", False)
    assert not alive(pid) and helpers.get("mit") is None, "disabling the plugin did not end the helper program"
    try:
        context.start_helper("helper.py")
        raise AssertionError("a disabled plugin starts a helper program")
    except plugin_api.PluginError:
        pass
    assert "trusted" in settings_store.load_settings()["plugins"]
    print("9 plugin: without 'helper' in the manifest no start, only programs from its own area; disabling ends it")


asyncio.run(main())

# ------------------------------------------------------------------ 10) statisch: wer Prozesse startet
APP = ROOT / "backend" / "app"
STARTS = re.compile(r"\bsubprocess\b|create_subprocess_|os\.(system|popen|exec[lv]p?e?|spawn[lv]p?e?|posix_spawn|fork)\(|\bmultiprocessing\b")
core_users = {p.relative_to(APP).as_posix() for p in APP.rglob("*.py") if "__pycache__" not in p.parts
              and STARTS.search(p.read_text(encoding="utf-8"))}
# helpers.py starts helper programs; serversetup.py and updater.py call fixed system tools of the core;
# plugin_api.py only names subprocess in its description (as forbidden).
assert core_users == {"helpers.py", "serversetup.py", "updater.py", "plugin_api.py"}, core_users
api_src = (APP / "plugin_api.py").read_text(encoding="utf-8")
assert "import subprocess" not in api_src and "create_subprocess" not in api_src
assert "create_subprocess_exec" in (APP / "helpers.py").read_text(encoding="utf-8")
assert "shell=True" not in (APP / "helpers.py").read_text(encoding="utf-8") and "create_subprocess_shell" not in (APP / "helpers.py").read_text(encoding="utf-8")
own = [p.relative_to(ROOT).as_posix() for p in (ROOT / "public" / "plugins").rglob("*.py")
       if "tests" not in p.parts and "node_modules" not in p.parts and STARTS.search(p.read_text(encoding="utf-8"))]
assert not own, f"a plugin starts a process itself instead of through context.start_helper(): {own}"
for probe in ("import subprocess", "await asyncio.create_subprocess_exec('x')", "os.system('ls')", "os.execv(path, args)", "import multiprocessing"):
    assert STARTS.search(probe), f"the check does not recognise: {probe}"
assert not STARTS.search("self.context.start_helper(program, args)") and not STARTS.search("os.path.join(a, b)")
print("10 processes: in the core only helpers.py starts helper programs (without a shell); plugins never start a process themselves")

print("ALL HELPER PROGRAM TESTS OK")
