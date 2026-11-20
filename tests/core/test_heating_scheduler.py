"""ComfyOS – the heating scheduler of the core (heating_scheduler.py, since 0.23.1).

    1  the switching point in force: last point of today up to now, otherwise of the days before (also a week back);
       unresolved levels and broken times are skipped
    2  without a sync: every thermostat of a room gets the value in force – on the switching path of the interface
       (event log " (Heizplan)"); locked ones are left alone, an offline one is tried again; nothing twice
    3  a value set by hand stays until the next switching point; a changed value in force and a thermostat that comes
       back online are set at once
    4  with a sync: the thermostats of the syncing plugin are left to it (its system runs the schedule itself),
       all others still get the value of the newly activated routine
    5  the real home and the demo keep their state apart; the loop runs under supervision

    python tests/core/test_heating_scheduler.py [path-to-comfyos]
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("heating_scheduler")

from app import core, device_store, events, heating_scheduler, heating_sync, plugin_api, plugins, routines_store  # noqa: E402
from app.plugin_api import Plugin  # noqa: E402


class HeizPlugin(Plugin):
    """A plugin of this test whose thermostats ComfyOS runs; commands are recorded in `commands`."""

    def __init__(self, context: Any) -> None:
        """Start without commands."""
        super().__init__(context)
        self.commands: list[tuple[str, dict, str]] = []

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any]:
        """Record and confirm."""
        self.commands.append((device_id, dict(state), source))
        return dict(state)


class SyncPlugin(HeizPlugin):
    """A plugin that syncs the heating with its own system (like the Home Base with the Magenta sync)."""
    syncs_heating = True
    on = False

    def heating_status(self) -> dict[str, Any]:
        """Configured only while `on` is set."""
        return {"configured": self.on, "ok": True}

    async def sync_heating(self, pull: bool = True, force: bool = False) -> dict[str, Any]:
        """Nothing to transfer in this test."""
        return self.heating_status()


def register(pid: str, cls: type) -> Any:
    """Put a running plugin of this test into the registry."""
    record = plugins.Record(id=pid, name=pid.capitalize(), origin="builtin", state="active", started=True)
    record.manifest = plugins.Manifest(id=pid, name=pid.capitalize(), version="1.0.0", api=1, entry="__init__:setup",
                                       api_revision=plugin_api.API_REVISION, id_prefixes=[pid + ":"])
    record.plugin = cls(plugins._Context(record))
    plugins._records[pid] = record
    return record.plugin


heiz: HeizPlugin = register("heiz", HeizPlugin)
sync: SyncPlugin = register("sync", SyncPlugin)


def thermostat(pid: str, key: str, room: str, target: float, online: bool = True) -> dict:
    """A device row of a thermostat as core.effective returns it."""
    did = f"{pid}:{key}"
    return {"id": did, "name": f"Heizung {key}", "kind": "thermostat", "room_id": room, "online": online,
            "target": target, "plugin": pid, "control": {"plugin": pid, "device_id": did}, "caps": []}


ROWS = {r["id"]: r for r in (thermostat("heiz", "bad", "bad", 18.0), thermostat("heiz", "bad2", "bad", 18.0),
                             thermostat("heiz", "kueche", "kueche", 17.0, online=False),
                             thermostat("sync", "bad", "bad", 18.0))}
ROWS["heiz:bad2"]["locked"] = True
device_store.lock("heiz:bad2", "user")


async def fake_merged(max_age: float | None = None, prune: bool = False) -> list[dict]:
    """The device list of this test (copies, as every query returns fresh rows)."""
    return [dict(r) for r in ROWS.values()]


core.merged_devices = fake_merged
core.effective = lambda rows: rows


def every_day(room: str, temp: float, time: str = "00:00") -> list[dict]:
    """Schedule changes: one switching point per day, so the test does not depend on the time of day."""
    return [{"room": room, "day": d, "points": [{"time": time, "temp": temp}]} for d in range(7)]


def sent(plugin: HeizPlugin) -> list[tuple[str, float]]:
    """Commands of a plugin since the last call as (device, target); clears them."""
    out = [(did, state["target"]) for did, state, source in plugin.commands if source == heating_scheduler.SOURCE]
    assert all(source == heating_scheduler.SOURCE for _, _, source in plugin.commands), plugin.commands
    plugin.commands.clear()
    return out


# ------------------------------------------------------------------ 1) the switching point in force
monday = {"0": [{"time": "06:00", "temp": 21.0}, {"time": "22:00", "temp": 17.0}]}
pif = heating_scheduler.point_in_force
assert pif(monday, datetime(2026, 11, 16, 7, 0)) == (0, "06:00", 21.0)          # Monday morning
assert pif(monday, datetime(2026, 11, 16, 6, 0)) == (0, "06:00", 21.0)          # exactly at the point
assert pif(monday, datetime(2026, 11, 16, 5, 59)) == (0, "22:00", 17.0)         # before it: last Monday evening
assert pif(monday, datetime(2026, 11, 18, 10, 0)) == (0, "22:00", 17.0)         # Wednesday: from Monday
assert pif({}, datetime(2026, 11, 16, 7, 0)) is None
odd = {"0": [{"time": "25:00", "temp": 30}, {"time": "07:00", "temp": None}, {"time": "x", "temp": 20}, "kaputt",
             {"time": "05:00", "temp": True}, {"time": "04:00", "temp": 19.5}]}
assert pif(odd, datetime(2026, 11, 16, 8, 0)) == (0, "04:00", 19.5), "broken points are skipped"
assert core.source_tag(heating_scheduler.SOURCE) == " (Heizplan)"
print("1 switching point in force: today up to now, otherwise the days before; broken points skipped")

# ------------------------------------------------------------------ 2) without a sync
winter = routines_store.create("Winter")["key"]
routines_store.update_schedule(winter, every_day("bad", 20.0) + every_day("kueche", 19.0))
routines_store.set_active(winter, pending=False)
assert heating_sync.synced_plugin() is None and set(heating_scheduler.plan()) == {"bad", "kueche"}


async def run() -> None:
    """Sections 2–4 – one event loop, like the running ComfyOS."""
    assert await heating_scheduler.tick() == 2
    assert sorted(sent(heiz)) == [("heiz:bad", 20.0)] and sent(sync) == [("sync:bad", 20.0)], "without a sync ComfyOS runs every thermostat"
    assert any(e["message"].endswith("(Heizplan)") for e in events.recent(20)), events.recent(5)
    for did in ("heiz:bad", "sync:bad"):
        ROWS[did]["target"] = 20.0
    assert await heating_scheduler.tick() == 0 and not heiz.commands and not sync.commands, "nothing twice"
    assert "bad" in heating_scheduler._applied[False] and "kueche" not in heating_scheduler._applied[False]
    print("2 without a sync: every thermostat of the room gets the value in force (event log ' (Heizplan)'); locked and "
          "offline ones not, nothing twice")

    # ------------------------------------------------------------------ 3) by hand, changed value, back online
    ROWS["heiz:bad"]["target"] = 23.0                                       # turned up by hand
    assert await heating_scheduler.tick() == 0 and not heiz.commands, "a value set by hand stays until the next point"
    routines_store.update_schedule(winter, every_day("bad", 21.0))         # the value in force changes
    assert await heating_scheduler.tick() == 2 and sent(heiz) == [("heiz:bad", 21.0)] and sent(sync) == [("sync:bad", 21.0)]
    ROWS["heiz:kueche"]["online"] = True
    assert await heating_scheduler.tick() == 1 and sent(heiz) == [("heiz:kueche", 19.0)], "back online: set at once"
    assert "kueche" in heating_scheduler._applied[False]
    print("3 set by hand stays until the next point; a changed value in force and a thermostat back online are set at once")

    # ------------------------------------------------------------------ 4) with a sync
    sync.on = True
    assert heating_sync.synced_plugin() == "sync"
    sommer = routines_store.create("Sommer")["key"]
    routines_store.update_schedule(sommer, every_day("bad", 17.0))
    routines_store.set_active(sommer, pending=False)
    assert await heating_scheduler.tick() == 1 and sent(heiz) == [("heiz:bad", 17.0)]
    assert not sync.commands, "the thermostats of the syncing plugin are left to its own system"
    assert set(heating_scheduler._applied[False]) == {"bad"}, "rooms without a point in the routine are forgotten"
    sync.on = False
    print("4 with a sync: its thermostats are left to it, all others follow the newly activated routine")

    # two at once: the second asks for one more round instead of running alongside
    routines_store.update_schedule(sommer, every_day("bad", 16.0))
    first, second = await asyncio.gather(heating_scheduler.tick(), heating_scheduler.tick())
    assert sorted(sent(heiz)) == [("heiz:bad", 16.0)] and sorted(sent(sync)) == [("sync:bad", 16.0)], "switched once"
    assert first + second == 2, (first, second)


asyncio.run(run())

# ------------------------------------------------------------------ 5) homes apart, supervised
assert heating_scheduler._applied[True] == {}, "the real home never writes the state of the demo"
src = (ROOT / "backend" / "app" / "core.py").read_text(encoding="utf-8")
assert "tasks.spawn(heating_scheduler.TASK, heating_scheduler.loop)" in src and "await heating_scheduler.tick()" in src
print("5 the real home and the demo keep their state apart; the loop runs under supervision, the demo runs it in its "
      "live comparison")

print("ALL HEATING SCHEDULER TESTS OK")
