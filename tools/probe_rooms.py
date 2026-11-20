"""Probe: how does the Magenta app (SMHM plugin) manage rooms?

Run on the machine/Pi ComfyOS runs on (as its user),
the Magenta sync must be set up:

    .venv/bin/python tools/probe_rooms.py            read-only: room list of the app vs. Home Base
    .venv/bin/python tools/probe_rooms.py --probe    additionally test possible room commands WITHOUT parameters

The result is on the console and in probe_rooms.txt (in the program folder).
Gateway id and Telekom data are not printed.

--probe: every candidate is called without parameters. Without parameters a
command can normally change nothing – but the error message reveals whether it
exists and which parameters it expects. If one reports "ok" against expectation,
that is marked clearly in the result (then please check in the app).
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import main, plugins  # noqa: E402,F401  (main loads the plugins – the Home Base has been the plugin qivicon since 0.19)

HB = plugins.get("qivicon").plugin
if HB is None:
    sys.exit("Das Plugin „Home Base“ ist ausgeschaltet oder lädt nicht (System → Plugins).")
TelekomCloud = sys.modules["comfyos_plugin_qivicon.telekom"].TelekomCloud

CANDIDATES = [
    # cross-check: certainly does not exist
    "comfyosProbeUnknownCommand",
    # lesen
    "listDevices", "getDevices", "getRoom", "getRoomInfo", "getRoomList", "listCommands", "help",
    # create / rename / delete a room (name patterns like createNewHeatingMode, changeNameOfHeatingMode)
    "createNewRoom", "createRoom", "addRoom",
    "changeNameOfRoom", "renameRoom", "setRoomName", "updateRoom", "editRoom",
    "deleteRoom", "removeRoom",
    # assign a device to a room
    "changeRoomOfDevice", "moveDeviceToRoom", "setRoomOfDevice", "setDeviceRoom", "assignDeviceToRoom",
    "addDeviceToRoom", "removeDeviceFromRoom", "setDevicesOfRoom", "setRoomDevices", "updateRoomDevices",
    # order (name known from the app)
    "setDeviceOrderForRoom", "setRoomOrder",
    # round 2: names from the app or in the style of the plugin
    "getDevicesPerRoomStructure", "getRoomsAndNotifications", "listRoomsAndDevices",
    "addNewRoom", "createRoomWithName", "newRoom", "saveRoom", "setRoom", "changeRoom",
    "changeRoomName", "changeNameOfDevice", "renameDevice", "setDeviceName",
    "changeDeviceRoom", "moveDevice", "moveDevices", "moveDevicesToRoom", "setRoomForDevice",
    "setDeviceToRoom", "addDevicesToRoom", "changeRoomOfDevices", "changeLocationOfDevice",
    "setDeviceLocation", "setLocation", "changeLocation", "updateDevice", "editDevice",
    "deleteRoomById", "removeRoomById",
]

out_lines: list[str] = []


def say(*parts):
    """Print a line and remember it for the report."""
    line = " ".join(str(p) for p in parts)
    print(line)
    out_lines.append(line)


def short(obj, n=400):
    """Value as text, shortened to `n` characters."""
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + " …"


def local_connector():
    """Connection to the Home Base from the settings of the plugin (None = not set up)."""
    return HB.build_connector()


async def local_rest(c, path):
    """Ask read-only at the local interface of the Home Base; an error comes back as text."""
    try:
        return await c._rpc_get(path)
    except Exception as exc:
        return f"FEHLER: {exc}"


async def main() -> int:
    """Put the rooms of the Home Base and of the Magenta account side by side; with --probe additionally probe room
    commands without parameters (changes nothing). Writes a report."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="Raum-Befehle ohne Parameter sondieren")
    args = ap.parse_args()

    ctx = HB.context
    user = str(ctx.settings.get("telekom_username") or "").strip()
    pw = ctx.secrets.get("telekom_password") if user else None
    if not user or not pw:
        print("Magenta-Sync ist nicht eingerichtet (System → Plugins → Home Base) – oder falscher Benutzer.")
        return 1
    cloud = TelekomCloud(user, pw, lambda **options: ctx.http("magenta", **options))
    gw = None
    try:
        await cloud.login()
        gw = ctx.settings.get("gateway_id")
        if not gw:
            await cloud.customer_self()
            ids = cloud.gateway_ids()
            gw = ids[0] if ids else None
        if not gw:
            print("Keine Home Base im Telekom-Konto gefunden.")
            return 1

        say("== 1) Raumliste der Magenta-App (listRooms)")
        rooms = (await cloud.plugin_call("listRooms", gw) or {}).get("result", {})
        app_dev_room = {}
        for rid, val in (rooms or {}).items():
            if not isinstance(val, dict):
                continue
            devs = val.get("devices")
            say(f"  {rid}: {val.get('name')!r}  Felder={sorted(val.keys())}")
            if isinstance(devs, dict):
                for k, v in list(devs.items())[:30]:
                    app_dev_room[k] = rid
                    say(f"      Gerät {k}: {short(v, 200)}")
            elif devs is not None:
                say(f"      devices = {short(devs, 300)}")

        say("\n== 2) Vergleich mit der Home Base (Raum je Gerät)")
        app_room_by_thing = {}
        for rid, val in (rooms or {}).items():
            for v in ((val or {}).get("devices") or {}).values() if isinstance(val, dict) else []:
                if isinstance(v, dict) and v.get("setupConfigUid"):
                    app_room_by_thing.setdefault(v["setupConfigUid"], set()).add(rid)
        c = local_connector()
        if c is None:
            say("  Home Base nicht eingerichtet – übersprungen")
        else:
            try:
                devs = await c.list_devices(max_age=0)
                for d in sorted(devs, key=lambda x: str(x.name)):
                    app_r = sorted(app_room_by_thing.get(d.id, []))
                    same = "gleich" if app_r == [d.room_id] else f"App: {app_r or '–'}"
                    say(f"  {d.name!r}: Home Base {d.room_id} | {same}   ({d.id})")

                say("\n== 3) Räume und Geräte in der Home-Base-REST (nur lesen)")
                for rid in [r for r in rooms if r not in ("SYSTEM",)][:3] + ["SYSTEM"]:
                    say(f"  /rest/items/{rid}:", short(await local_rest(c, f"/rest/items/{rid}"), 500))
                for rid, val in list(rooms.items())[:3]:
                    item = next(iter(((val or {}).get("devices") or {})), None) if isinstance(val, dict) else None
                    if item:
                        say(f"  /rest/items/{item}:", short(await local_rest(c, f"/rest/items/{item}"), 500))
                        thing = val["devices"][item].get("setupConfigUid")
                        if thing:
                            t = await local_rest(c, f"/rest/things/{thing}")
                            say(f"  /rest/things/{thing} location:", t.get("location") if isinstance(t, dict) else short(t, 200))
                say("  /rest/qivicon/rooms:", short(await local_rest(c, "/rest/qivicon/rooms"), 600))
                grp = await local_rest(c, "/rest/items?type=Group")
                if isinstance(grp, list):
                    say(f"  Group-Items: {len(grp)}, Beispiele:",
                        short([{k: g.get(k) for k in ("name", "label", "tags", "groupNames", "category")} for g in grp[:12]], 1500))
            except Exception as exc:
                say("  Home Base nicht lesbar:", exc)
            finally:
                await c.close()

        if args.probe:
            say("\n== 4) Befehle ohne Parameter")
            for cmd in CANDIDATES:
                try:
                    res = await cloud.plugin_call(cmd, gw, {})
                    say(f"  !! {cmd}: OK  {short(res, 300)}")
                    if cmd == CANDIDATES[0]:
                        say("  Das Plugin meldet auch für unbekannte Befehle OK – Sondierung abgebrochen, keine weiteren Befehle gesendet.")
                        break
                except Exception as exc:
                    say(f"  -- {cmd}: {short(str(exc), 300)}")
        else:
            say("\n(Befehls-Sondierung übersprungen – mit --probe starten)")
    finally:
        await cloud.close()

    text = "\n".join(out_lines).replace(gw, "<GATEWAY>") if gw else "\n".join(out_lines)
    (ROOT / "probe_rooms.txt").write_text(text + "\n", encoding="utf-8")
    print(f"\nGespeichert: {ROOT / 'probe_rooms.txt'}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(asyncio.run(main()))
