"""ComfyOS – supervised background tasks (tasks.py) and the device query of the plugins.

    1  long runner: restarts after a crash and after it ends; a second start under the same name changes nothing
    2  one-off task: an error is written to the event log (only the type), `quiet` reports nothing
    3  cancel_all cancels everything
    4  device query: one query at a time per plugin; whoever needs it fresher gets one of their own – with their age
    5  the core starts its loops under supervision and cancels them on shutdown
    6  the shutdown saves even with a full live stream; errors while saving and recording are reported

    python tests/core/test_tasks.py [path-to-comfyos]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("tasks")

from app import core, events, flow_engine, history, main, plugins, power_store, tasks  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

tasks.FIRST_PAUSE = 0.01


def messages() -> list[str]:
    """Texts of the last entries in the event log."""
    return [e["message"] for e in events.recent(50)]


async def supervised():
    """Sections 1 to 3 in one event loop."""
    runs = []

    async def flaky():
        runs.append(len(runs))
        if len(runs) == 1:
            raise RuntimeError("geheimer Inhalt")     # the text must never end up in the event log
        if len(runs) == 2:
            return                                    # a long runner that simply ends
        await asyncio.sleep(3600)

    first = tasks.spawn("probe", flaky)
    assert tasks.spawn("probe", flaky) is first, "a second start created a new task"
    for _ in range(200):
        if len(runs) >= 3:
            break
        await asyncio.sleep(0.01)
    assert len(runs) == 3 and not first.done(), (runs, first.done())
    assert "probe" in tasks.running()
    log = messages()
    assert any("„probe“" in m and "RuntimeError" in m for m in log) and any("„probe“" in m and "beendet" in m for m in log), log
    assert not any("geheimer Inhalt" in m for m in log), "content of an exception in the event log"
    print("1 long runner: restarted after a crash and after it ended, the event log only names the error type")

    async def boom():
        raise ValueError("auch geheim")

    async def fine():
        return 7

    loud, silent, good = tasks.fire(boom(), "einmal"), tasks.fire(boom(), "still", quiet=True), tasks.fire(fine(), "gut")
    await asyncio.gather(loud, silent, good, return_exceptions=True)
    await asyncio.sleep(0.01)
    log = messages()
    assert any("„einmal“" in m and "ValueError" in m for m in log), log
    assert not any("„still“" in m or "„gut“" in m or "auch geheim" in m for m in log), log
    assert good.result() == 7
    print("2 one-off task: error in the event log, with quiet no report, the result can still be fetched")

    waiting = tasks.fire(asyncio.sleep(3600), "wartet")
    await tasks.cancel_all()
    assert first.cancelled() and waiting.cancelled() and tasks.running() == [], (first, waiting, tasks.running())
    print("3 cancel_all: long runners and one-off tasks cancelled")


class Slow:
    """Plugin whose device query waits until the test releases it – and remembers which age applied."""

    max_device_age = None

    def __init__(self):
        """Plugin whose device query only answers after gate was opened."""
        self.ages, self.gate = [], asyncio.Event()

    async def devices(self):
        """Record the maximum age asked for and wait until gate is open."""
        self.ages.append(self.max_device_age)
        await self.gate.wait()
        return []

    def link_prefixes(self):
        """No links."""
        return []


async def fetching():
    """Section 4: concurrent queries at one plugin."""
    plugin = Slow()
    rec = plugins.Record(id="probe", name="Probe", origin="builtin", plugin=plugin, state="active")
    a = asyncio.ensure_future(plugins._devices(rec, None))        # "as fresh as the plugin thinks"
    await asyncio.sleep(0.01)
    b = asyncio.ensure_future(plugins._devices(rec, None))        # the same: shares the query
    c = asyncio.ensure_future(plugins._devices(rec, 0))           # will es frisch: eigener Abruf danach
    await asyncio.sleep(0.01)
    assert plugin.ages == [None], plugin.ages
    plugin.gate.set()
    ra, rb = await a, await b
    assert ra is rb, "concurrent callers got different results"
    plugin.gate.clear()
    await asyncio.sleep(0.01)
    assert plugin.ages == [None, 0], f"the fresh query did not run with its own age: {plugin.ages}"
    d = asyncio.ensure_future(plugins._devices(rec, 5))           # 5 s are enough: shares the fresh query
    e = asyncio.ensure_future(plugins._devices(rec, None))
    await asyncio.sleep(0.01)
    plugin.gate.set()
    rc, rd, re_ = await c, await d, await e
    assert rc is rd is re_ and plugin.ages == [None, 0], plugin.ages
    assert (await plugins._devices(rec, 2)).devices == [] and plugin.ages == [None, 0, 2], plugin.ages
    print("4 device query: one at a time per plugin, shared if fresh enough, otherwise an own query with its own age")


asyncio.run(supervised())
asyncio.run(fetching())

with TestClient(main.app):
    names = tasks.running()
    assert names == ["dynamische-szenen", "flow", "geräteverlauf", "heizplan", "live-abgleich", "lizenzprüfung"], names
assert tasks.running() == [], f"still running after the shutdown: {tasks.running()}"
# The only exception: the loader holds the running device query per plugin itself (plugins._fetching).
SELF_KEPT = ("tasks.py", "plugins.py")
core_src = (ROOT / "backend" / "app").rglob("*.py")
loose = [f"{p.name}" for p in core_src if p.name not in SELF_KEPT
         and ("create_task(" in p.read_text(encoding="utf-8") or "ensure_future(" in p.read_text(encoding="utf-8"))]
assert not loose, f"background work without supervision (use tasks.spawn/fire): {loose}"
print("5 core: six loops under supervision, cancelled on shutdown; no loose create_task in the core")



async def closing():
    """Section 6: shutdown and recording with disturbances."""
    flushed = []
    full: asyncio.Queue = asyncio.Queue(maxsize=1)
    full.put_nowait({"type": "x"})                    # langsamer Client: sein Live-Stream ist voll
    core._listeners.add(full)

    def broken_flush():
        raise OSError("geheimer Pfad")                # the text must never end up in the event log

    keep = power_store.flush, flow_engine.engine.flush, history.record
    power_store.flush, flow_engine.engine.flush = broken_flush, lambda: flushed.append("flow")
    try:
        await core.shutdown()
    finally:
        core._listeners.discard(full)
        power_store.flush, flow_engine.engine.flush = keep[0], keep[1]
    assert flushed == ["flow"], "a full live stream or a failed save holds up the shutdown"
    said = [m for m in messages() if "beenden" in m]
    assert said and "OSError" in said[0] and "Stromverbrauch" in said[0], said
    assert not any("geheimer Pfad" in m for m in messages()), "error text in the event log"

    def broken_record(items):
        raise KeyError("geheimer Inhalt")

    history.record = broken_record
    try:
        core._observe([{"id": "x", "name": "Lampe"}])     # must never raise: a query does not fail because of the recording
    finally:
        history.record = keep[2]
    said = [m for m in messages() if "verlauf" in m]
    assert said and "KeyError" in said[0] and not any("geheimer Inhalt" in m for m in messages()), said
    print("6 the shutdown saves despite a full live stream; a failed save/recording is written to the event log (only the type)")


asyncio.run(closing())

print("ALL TASK TESTS OK")
