"""Heating sync: bridge between the heating routines of ComfyOS and the plugin that syncs them with another system
(so far: the Magenta app through the plugin of the Home Base).

The core does not know this plugin by name: the running plugin with `syncs_heating = True` is asked
(`plugin_api.Plugin.heating_status` / `sync_heating`). In turn the plugin sees the routines only through `Plans`
(`context.heating`, protocol `plugin_api.HeatingPlans`) – never the stores themselves.

Without such a plugin the marks in `routines_store` simply stay and are worked off on the first
sync.
"""
from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from . import home_store, plugins, routines_store, tasks
from .plugin_api import HeatingChanges, HomeMode, RemoteRoutine

DEFAULT_INTERVAL = 300
SYNC_WAIT = 20.0        # s – a request waits at most this long for a sync
NOT_ACTIVE = "Abgleich nicht aktiv"      # by this the interface recognises "no sync configured" (HO.syncToast)
_DONE_FLAGS = {"deleted": "delete", "updated": "dirty", "renamed": "pending_rename", "activate": "activate"}


class Plans:
    """The heating routines as a syncing plugin sees them (plugin_api.HeatingPlans)."""

    def changes(self) -> HeatingChanges:
        """What still has to be transferred: deleted, new, changed, renamed routines and the switch."""
        work = routines_store.pending_work()
        return HeatingChanges(deleted=work["deletes"], created=work["creates"], updated=work["updates"],
                              renamed=work["renames"], activate=work["activate"])

    def routine_name(self, key: str) -> str | None:
        """Name of a routine, or None."""
        r = routines_store.get(key)
        return r.get("name") if r else None

    def schedule(self, key: str) -> dict[str, Any] | None:
        """Weekly schedule of a routine in write form, or None."""
        return routines_store.write_payload(key)

    def is_local(self, key: str) -> bool:
        """Does the routine exist only in ComfyOS, not transferred yet?"""
        return routines_store.is_local_key(key)

    def done(self, key: str, change: str) -> None:
        """Tick off a change as transferred."""
        flag = _DONE_FLAGS.get(change)
        if flag is None:
            raise ValueError("Unbekannte Änderung.")
        routines_store.clear_flag(key, flag)

    def created(self, local_key: str, remote_key: str) -> None:
        """A newly created routine has got its final key – replace it everywhere."""
        routines_store.rekey(local_key, remote_key)
        home_store.rekey(local_key, remote_key)

    def merge(self, routines: Sequence[RemoteRoutine], active_key: str | None, levels: Mapping[str, float],
              room_names: Mapping[str, str]) -> dict[str, int]:
        """Take over the state of the synced system; returns how much has changed."""
        return routines_store.merge_remote(list(routines), active_key, dict(levels), dict(room_names))

    def touch(self) -> None:
        """Zeitpunkt des letzten Abgleichs auf jetzt setzen."""
        routines_store.mark_synced_now()

    def home(self) -> HomeMode:
        """Home/away with the assignment of the routines and the flags for what of it still has to be transferred."""
        h = home_store.get()
        return HomeMode(state=h.get("state") or "PRESENT", state_changed=bool(h.get("pending")),
                        present_routine=h.get("present_routine"), away_routine=h.get("away_routine"),
                        mapping_changed=bool(h.get("mapping_pending")))

    def home_done(self, state: bool = True, mapping: bool = False) -> None:
        """Tick off state and/or assignment as transferred."""
        home_store.mark_synced(state=bool(state), mapping=bool(mapping))

    def merge_home(self, state: str | None, present_routine: str | None, away_routine: str | None) -> None:
        """Take over home/away and the assignment from the synced system."""
        home_store.apply_remote(state, present_routine, away_routine)


def _plugin():
    """The plugin that syncs: the first configured one; if none is configured, the first at all."""
    found = [plugin for _, plugin in plugins.having("syncs_heating")]
    for plugin in found:
        if _status(plugin).get("configured"):
            return plugin
    return found[0] if found else None


def _status(plugin) -> dict[str, Any]:
    """State of the heating sync as the plugin reports it – simple values only; {} without a plugin. If it cannot
    be read, it only holds the kind of the error."""
    if plugin is None:
        return {}
    try:
        st = plugin.heating_status() or {}
    except Exception as exc:
        return {"configured": True, "ok": False, "last_error": f"Stand nicht lesbar ({type(exc).__name__})"}
    return {str(k)[:40]: v for k, v in st.items() if isinstance(v, (str, int, float, bool)) or v is None}


def configured() -> bool:
    """Is a plugin connected that syncs heating routines?"""
    return bool(_status(_plugin()).get("configured"))


def synced_plugin() -> str | None:
    """Id of the plugin whose system runs the heating schedule itself (so far the Home Base with the Magenta sync
    configured) – its thermostats are left to it by the scheduler of ComfyOS (heating_scheduler). None without sync."""
    for plugin_id, plugin in plugins.having("syncs_heating"):
        if _status(plugin).get("configured"):
            return plugin_id
    return None


def public_status() -> dict[str, Any]:
    """State of the sync for the interface (heating schedule, home page, System)."""
    st = {"configured": False, "ok": None, "last_sync": None, "last_error": None, "running": False,
          "interval": DEFAULT_INTERVAL}
    st.update(_status(_plugin()))
    st["configured"] = bool(st.get("configured"))
    st["pending"] = routines_store.state()["pending"] + (1 if home_store.get().get("pending") else 0)
    return st


async def sync(force: bool = False) -> dict[str, Any]:
    """Sync: push local changes, then fetch the state of the other system."""
    plugin = _plugin()
    if plugin is not None and _status(plugin).get("configured"):
        task = tasks.fire(plugin.sync_heating(pull=True, force=force), "heiz-abgleich", quiet=True)
        try:
            # The request only waits for a limited time: otherwise a sluggish server on the other side keeps connections of
            # the browser busy (a phone only has a few). The sync itself finishes in the background.
            await asyncio.wait_for(asyncio.shield(task), timeout=SYNC_WAIT)
        except asyncio.TimeoutError:
            pass   # takes longer: the sync continues in the background
        except Exception:
            pass      # the state names the error; a sync never takes a request down with it
    return public_status()


async def push_now(wait: float = 25.0) -> dict[str, Any]:
    """Push pending changes at once. Keeps running even if we no longer wait
    (no abort in the middle of a creation -> no duplicates)."""
    plugin = _plugin()
    if plugin is None or not _status(plugin).get("configured"):
        return {"synced": False, "reason": NOT_ACTIVE}
    task = tasks.fire(plugin.sync_heating(pull=False, force=True), "heiz-abgleich", quiet=True)
    try:
        st = await asyncio.wait_for(asyncio.shield(task), timeout=wait)
    except asyncio.TimeoutError:
        return {"synced": False, "reason": "Abgleich läuft noch im Hintergrund"}
    except Exception as exc:
        return {"synced": False, "reason": f"Abgleich fehlgeschlagen ({type(exc).__name__})"}
    st = st if isinstance(st, dict) else {}
    return {"synced": bool(st.get("ok")), "reason": st.get("last_error")}


plugins.heating = Plans()
