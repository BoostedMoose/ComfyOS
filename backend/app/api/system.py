"""Systemzustand, Live-Stream (SSE), Netzwerk, HTTPS, Dienst-Einrichtung, Neustart."""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import (core, egress, entitlements, events, floorplan_store, flow_engine, flows_store, license, network, plugins,
                push, serversetup, tls)
from ..backup_store import BACKUP_DIR
from ..paths import APP_ROOT, DATA_DIR, LOGS_DIR, app_version
from ..settings_store import load_settings, save_settings

router = APIRouter()


STREAM_CHECK = 5       # s – this often the live stream checks whether the page is still there
STREAM_PING = 20       # s – sign of life when there is nothing to report


@router.get("/api/stream")
async def api_stream(request: Request):
    """Server-sent events: device states live (Hue at once, commands from ComfyOS), plus 'reload' hints."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    core.listen(q)      # a page in the demo gets only the messages of the demo (demo.py)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            quiet = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # wait briefly so that a closed page is noticed quickly (otherwise listeners would
                    # hang for up to 20 s and the live comparison would keep running for nobody)
                    msg = await asyncio.wait_for(q.get(), timeout=STREAM_CHECK)
                except asyncio.TimeoutError:
                    quiet += 1
                    if quiet * STREAM_CHECK >= STREAM_PING:
                        quiet = 0
                        yield ": ping\n\n"      # keeps proxies and mobile networks open
                    continue
                quiet = 0
                if msg.get("type") == "bye":
                    break
                yield "data: " + json.dumps(msg, ensure_ascii=False) + "\n\n"
        finally:
            core._listeners.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- System
@router.get("/api/health")
async def health():
    """Sign of life without login: name and version – nothing else."""
    return {"ok": True, "app": "ComfyOS", "version": app_version()}


# Share of ComfyOS in the load (since 0.20.1): CPU time and memory of the own process, plus the
# disk space of the own data, logs and backups. None of it leaves the machine.
OWN_SMOOTH = 60.0        # s – utilisation and own CPU share are smoothed like the load next to them (mean over one minute)
OWN_DISK_EVERY = 60.0    # s – this often the own folders are counted anew
_own_disk_state: dict = {"at": 0.0, "bytes": None}


def _cpu_ticks() -> tuple[int, int] | None:
    """Busy and total ticks of all cores since boot from /proc/stat (Linux only); None if unreadable."""
    if not sys.platform.startswith("linux"):
        return None    # /proc only exists there
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        values = [int(v) for v in parts[1:9]]     # user nice system idle iowait irq softirq steal (guest is in user)
    except (OSError, ValueError):
        return None    # no /proc (Windows, macOS) or an unexpected line: no utilisation
    if not parts or parts[0] != "cpu" or len(values) < 5:
        return None
    return sum(values) - values[3] - values[4], sum(values)


# Since 0.27.5 the CPU bar shows the real utilisation (/proc/stat) instead of the load average: the load counts waiting
# processes, not computing time – on an idle machine ComfyOS's share then filled the whole bar. Both values are taken
# over the same intervals from a common start (import = start of the service) and smoothed alike, so ComfyOS's share
# can never exceed the utilisation.
_cpu_state: dict = {"at": time.monotonic(), "used": time.process_time(), "ticks": _cpu_ticks(), "share": None, "busy": None}


def _cpu(cores: int) -> tuple[float | None, float]:
    """Utilisation of all cores and the share of ComfyOS in it, in per cent, smoothed over about one minute.

    Measured are the busy ticks of all cores (None without /proc/stat) and the CPU time of the own process between
    two queries – the first query measures since the service started. Queries less than a second apart get the last
    values."""
    now, used, state = time.monotonic(), time.process_time(), _cpu_state
    span = now - state["at"]
    if state["share"] is None or span >= 1.0:
        ticks, before = _cpu_ticks(), state["ticks"]
        own = (used - state["used"]) / max(span, 0.001) / cores * 100
        busy = (ticks[0] - before[0]) / (ticks[1] - before[1]) * 100 if ticks and before and ticks[1] > before[1] else None
        weight = 1.0 if state["share"] is None else 1 - math.exp(-span / OWN_SMOOTH)
        state["share"] = own if state["share"] is None else state["share"] + weight * (own - state["share"])
        if busy is not None:
            state["busy"] = busy if state["busy"] is None else state["busy"] + weight * (busy - state["busy"])
        state["at"], state["used"], state["ticks"] = now, used, ticks or before
    busy = None if state["busy"] is None else max(0.0, min(100.0, state["busy"]))
    share = max(0.0, min(state["share"], 100.0 if busy is None else busy))
    return (None if busy is None else round(busy, 1)), round(share, 1)


def _own_ram() -> int | None:
    """Memory of the own process in bytes (resident pages from /proc/self/statm); None if unreadable."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _tree_bytes(roots: list[str]) -> int:
    """Disk space used by the folders including subfolders in bytes. Links are not followed; what cannot be
    read or is vanishing right now does not count."""
    total, stack = 0, list(roots)
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        st = entry.stat(follow_symlinks=False)
                        total += getattr(st, "st_blocks", 0) * 512 or st.st_size
                    except OSError:
                        continue   # the file was just removed or is unreadable: it does not count
        except OSError:
            continue   # the folder is missing (yet) or unreadable: it does not count
    return total


async def _own_disk() -> int | None:
    """Disk space ComfyOS uses with data, logs and backups, in bytes – counted anew at most every OWN_DISK_EVERY
    seconds (in a thread of its own, the folders are small)."""
    now, state = time.monotonic(), _own_disk_state
    if state["bytes"] is None or now - state["at"] >= OWN_DISK_EVERY:
        state["at"] = now
        state["bytes"] = await asyncio.to_thread(_tree_bytes, [str(DATA_DIR), str(LOGS_DIR), str(BACKUP_DIR)])
    return state["bytes"]


@router.get("/api/system/info")
async def system_info():
    """Load of the machine for the page System: utilisation (cpu, per cent of all cores), load, memory, disk space,
    uptime, temperature – and under `own` which share ComfyOS itself has in it (cpu = per cent of all cores, never more
    than the utilisation; ram and disk in bytes). What cannot be read (e.g. on Windows) stays empty."""
    import shutil, platform
    linux = platform.system() == "Linux"   # /proc and /sys only exist there (Windows: possibly shared folders of the same name)
    bad = (OSError, ValueError, IndexError, KeyError)
    cores = os.cpu_count() or 1
    busy, own_cpu = _cpu(cores)
    info: dict = {"platform": platform.system(), "machine": platform.machine(), "cpu_count": cores,
                  "cpu": busy if linux else None, "load": None, "ram": None, "disk": None, "uptime": None, "cpu_temp": None,
                  "own": {"cpu": own_cpu, "ram": _own_ram() if linux else None, "disk": await _own_disk()}}
    if linux:
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
            info["load"] = [float(parts[0]), float(parts[1]), float(parts[2])]
        except bad:
            pass   # unreadable: the value stays empty
        try:
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, _, v = line.partition(":")
                    if v.strip():
                        mem[k.strip()] = int(v.strip().split()[0]) * 1024
            total, avail = mem["MemTotal"], mem["MemAvailable"]
            info["ram"] = {"total": total, "used": total - avail, "available": avail}
        except bad:
            pass   # unreadable: the value stays empty
        try:
            with open("/proc/uptime") as f:
                info["uptime"] = float(f.read().split()[0])
        except bad:
            pass   # unreadable: the value stays empty
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                info["cpu_temp"] = int(f.read().strip()) / 1000
        except bad:
            pass   # unreadable: the value stays empty
    try:
        usage = shutil.disk_usage(str(APP_ROOT))
        info["disk"] = {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        pass   # unreadable: the value stays empty
    return info


@router.get("/api/system/devices")
async def system_devices():
    """Device health: last_seen, online status and last state change from the flow engine."""
    eng = flow_engine.engine
    devs: list[dict] = []
    try:
        raw = await core.all_devices()
    except HTTPException:
        raw = []
    now = time.time()
    for d in raw:
        did = str(d.get("id", ""))
        changed = eng.changed_at.get(did)
        info: dict = {"id": did, "name": d.get("name"), "kind": d.get("kind"), "room": d.get("room"),
                      "online": d.get("online"), "last_seen": d.get("last_seen"),
                      "last_change": changed}
        if d.get("last_seen"):
            ts = flow_engine.parse_ts(d["last_seen"])
            if ts and now - ts > 86400:
                info["warning"] = "Letzte Meldung vor über 24 Stunden"
        if d.get("online") is False:
            info["warning"] = info.get("warning", "Gerät offline")
        devs.append(info)
    devs.sort(key=lambda x: (not x.get("warning"), x.get("name") or ""))
    return {"devices": devs, "motion_note": "Bewegungsmelder der Home Base werden etwa alle 3 Sekunden abgefragt. "
            "Sehr kurze Bewegungen können verpasst werden, wenn der Sensor zwischen zwei Abfragen zurückgesetzt wird. "
            "Hue-Bewegungsmelder (über die Bridge) melden sich dagegen sofort."}


@router.get("/api/entitlements")
async def api_entitlements():
    """Plan, features and limits – for display only; enforcement happens in the backend."""
    return entitlements.status()


def _license_egress() -> tuple[str, str]:
    """State of the connection to the licence provider for the egress register: off, only when redeeming or regularly."""
    if license.provider_host() is None:
        return "off", "kein Anbieter eingerichtet"
    if license.DEV:
        return "off", "Entwicklungsmodus"
    return ("on", "Schlüssel eingelöst") if license.load_cache()[0] else ("on_demand", "nur beim Einlösen")


@router.get("/api/system/egress")
async def system_egress():
    """All connections into the internet with purpose, data, trigger and current state."""
    plan = floorplan_store.get()
    http_flows = sum(1 for f in flows_store.all_flows()
                     if f.get("enabled") and not flows_store.outdated(f)
                     and any(n.get("type") == "http" for n in f.get("nodes") or []))
    subs = push.count()
    state = {
        "weather": ("on", "eingeschaltet") if plan.get("weather", True) and plan.get("address") else ("off", "aus"),
        "weather_warnings": (("on", "eingeschaltet") if plan.get("weather", True) and plan.get("warnings", True)
                             and plan.get("address") else ("off", "aus")),
        "updates": ("on_demand", "nur auf Knopfdruck"),
        "flow_http": ("on", f"{http_flows} aktive Flows") if http_flows else ("off", "kein aktiver Flow"),
        "push": ("on", f"{subs} Gerät{'' if subs == 1 else 'e'} angemeldet") if subs else ("off", "auf keinem Gerät eingeschaltet"),
        "license_provider": _license_egress(),
    }
    def of(eid: str) -> tuple[str, str]:
        if eid in state:
            return state[eid]
        # Targets from plugin manifests (plugin.<id>.<key>): off as long as the plugin is disabled. When enabled, the
        # plugin itself says whether it uses the target right now (status()["egress"]), otherwise "enabled" applies.
        parts = eid.split(".")
        plugin = plugins.active(parts[1]) if len(parts) == 3 and parts[0] == "plugin" else None
        if plugin is None:
            return "off", "Plugin ausgeschaltet"
        try:
            told = ((plugin.status() or {}).get("egress") or {}).get(parts[2])
        except Exception:
            told = None
        if isinstance(told, (list, tuple)) and len(told) == 2 and told[0] in ("on", "off", "on_demand"):
            return told[0], str(told[1])[:80]
        return "on", "Plugin eingeschaltet"

    return {"connections": [{**e, "state": of(e["id"])[0], "state_text": of(e["id"])[1]} for e in egress.status()]}


@router.get("/api/network")
async def api_network(request: Request):
    """Addresses under which ComfyOS is reachable in the home network."""
    https = request.url.scheme == "https"
    return network.info(request.url.port or (443 if https else 80), "https" if https else "http")


@router.get("/api/events")
async def api_events(limit: int = 150):
    """The last entries of the event log (at most 1000)."""
    return events.recent(limit=max(1, min(limit, 1000)))


class ServerSetupBody(BaseModel):
    """Set up or remove the autostart as a service, with host name."""
    action: str = Field(max_length=20)
    hostname: str = Field("comfyos", max_length=64)


@router.get("/api/serversetup")
async def serversetup_status():
    """State of the autostart as a service."""
    return await asyncio.to_thread(serversetup.status)


@router.post("/api/serversetup")
async def serversetup_run(body: ServerSetupBody):
    """Set up or remove the autostart as a service."""
    try:
        st = await asyncio.to_thread(serversetup.start, body.action, body.hostname)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    events.log("system", "Serversetup: " + ("Dienst wird eingerichtet" if body.action == "install" else "Dienst wird entfernt"))
    return st


class HttpsBody(BaseModel):
    """Switch HTTPS on or off; `regenerate` issues a new certificate."""
    enabled: bool
    regenerate: bool = False


@router.get("/api/https")
async def api_https(request: Request):
    """State of HTTPS: switched on, active right now, port and details of the certificate."""
    http_port = int(os.getenv("COMFYOS_HTTP_PORT") or (request.url.port or 80))
    return {"enabled": tls.enabled(), "active": request.url.scheme == "https",
            "launcher": os.getenv("COMFYOS_LAUNCHER") == "1", "https_port": tls.https_port(http_port),
            "cert": await asyncio.to_thread(tls.cert_info)}


@router.post("/api/https")
async def api_https_set(body: HttpsBody, request: Request):
    """HTTPS on/off (takes effect after a restart). Restarts ComfyOS if it runs through backend/run.py."""
    if body.enabled:
        try:
            await asyncio.to_thread(tls.ensure_cert, body.regenerate)
        except Exception as exc:
            raise HTTPException(500, f"Zertifikat konnte nicht erstellt werden: {exc}")
    cfg = load_settings()
    cfg["https"] = bool(body.enabled)
    save_settings(cfg)
    launcher = os.getenv("COMFYOS_LAUNCHER") == "1"
    host = (request.headers.get("host") or "comfyos").split(":")[0]
    http_port = int(os.getenv("COMFYOS_HTTP_PORT") or (request.url.port or 80))
    if body.enabled:
        sp = tls.https_port(http_port)
        url = f"https://{host}{'' if sp == 443 else f':{sp}'}/"
    else:
        url = f"http://{host}{'' if http_port == 80 else f':{http_port}'}/"
    events.log("system", "HTTPS " + ("eingeschaltet" if body.enabled else "ausgeschaltet"))
    if launcher:
        core.restart_soon(1.5)
    return {"ok": True, "restarting": launcher, "url": url}


@router.post("/api/restart")
async def restart():
    """ComfyOS neu starten."""
    events.log("system", "Neustart angefordert")
    core.restart_soon()
    return {"ok": True}
