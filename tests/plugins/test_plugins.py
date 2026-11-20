"""ComfyOS – plugin system: manifest, loader, approval, devices and commands through plugins, isolation of errors.

    python tests/plugins/test_plugins.py [path-to-comfyos]

Checks: manifest schema; built-in plugins load, templates (`_…`) do not; a copied `_template` appears as a
tile but only runs after approval with the password; its devices are in the device list and can be switched,
renamed, removed; settings from the manifest; broken plugins (manifest, setup, start, devices, command)
paralyse neither the core nor other plugins; disabled plugins; egress and secrets only as declared –
for TCP connections too (`context.open_stream`, API revision 2); a plugin cannot overwrite fields of the device row
that the core owns; "Alle übernehmen"; access to devices of other plugins, room light, scenes and
home/away only with permission in the manifest (API revision 3) – with device lock and event log; static files
without path tricks; SDK copy and style rules of the built-in plugins. Helper programs: tests/security/test_helpers.py.
"""
import ast
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("plugins")

import httpx  # noqa: E402

from app import settings_store  # noqa: E402
from app import auth, core, device_store, egress, events, home_store, main, plugin_api, plugins  # noqa: E402
from support.homebase import QiviconConnector, install_homebase  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PUBLIC = ROOT / "public"
USER = plugins.USER_DIR
PW = "comfyos-" + "test"
outbound: list[str] = []


def _net(req: httpx.Request) -> httpx.Response:
    """Replacement for the network: records the target of every request."""
    outbound.append(req.url.host)
    return httpx.Response(200, json={"ok": True})


egress._transport = httpx.MockTransport(_net)
egress._lan_transport = httpx.MockTransport(_net)

# ------------------------------------------------------------------ 1) Manifest
good = plugins.read_manifest(PUBLIC / "plugins" / "hue")
assert good.id == "hue" and good.api == plugin_api.API_VERSION and good.id_prefixes == ["huebridge:"] and good.secrets == ["app_key"]
assert good.egress == [{"key": "bridge", "desc": "Hue Bridge im Heimnetz", "scope": "lan", "hosts": [], "udp": False, "data": "",
                        "trigger": ""}]
BASE = {"id": "probe", "name": "Probe", "version": "1.0.0", "api": plugin_api.API_VERSION, "entry": "__init__:setup",
        "id_prefixes": ["probe:"]}


def manifest_error(change: dict, folder: str = "probe") -> str:
    """Have a manifest with this change written and read; returns the error message (empty if it is valid)."""
    d = tmp / "manifeste" / folder
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    data = {k: v for k, v in {**BASE, **change}.items() if v is not None}
    (d / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    try:
        plugins.read_manifest(d)
    except plugins.ManifestError as exc:
        return str(exc)
    return ""


assert manifest_error({}) == "" and manifest_error({"api_revision": plugin_api.API_REVISION}) == ""
assert "aktualisieren" in manifest_error({"api_revision": plugin_api.API_REVISION + 1})      # braucht ein neueres ComfyOS
assert good.api_revision == 7 and plugins.read_manifest(PUBLIC / "plugins" / "mqtt").api_revision == 7   # buttons (0.23)
assert "reserviert" in manifest_error({"id": "demo", "id_prefixes": ["demo:"]}, folder="demo"), "the demo plugin is the core's own"
fritz_manifest = plugins.read_manifest(PUBLIC / "plugins" / "fritzbox")
assert fritz_manifest.api_revision == 7 and fritz_manifest.secrets == ["password"] and fritz_manifest.id_prefixes == ["fritzbox:"]
assert [(e["key"], e["scope"], e["udp"]) for e in fritz_manifest.egress] == [("box", "lan", False)], "the FRITZ!Box only in the home network"
for change, word in (({"id": None}, "id"), ({"id": "Probe!"}, "id"), ({"id": "anders"}, "Ordnernamen"), ({"name": ""}, "name"),
                     ({"version": "eins"}, "version"), ({"api": plugin_api.API_VERSION + 1}, "Plugin-API"), ({"api": "1"}, "api"),
                     ({"api_revision": 0}, "api_revision"), ({"api_revision": "2"}, "api_revision"), ({"api_revision": True}, "api_revision"),
                     ({"entry": "../x:setup"}, "entry"), ({"id_prefixes": []}, "id_prefixes"),
                     ({"id_prefixes": ["kein-doppelpunkt"]}, "id_prefixes"), ({"egress": [{"key": "x", "desc": "ohne Ziel"}]}, "egress"),
                     ({"egress": [{"key": "x", "desc": "d", "hosts": ["http://evil.example.com/pfad"]}]}, "egress"),
                     ({"egress": [{"key": "x", "desc": "d", "hosts": ["api.example.com"], "udp": True}]}, "udp"),
                     ({"egress": [{"key": "x", "desc": "d", "scope": "lan", "udp": "ja"}]}, "udp"),
                     ({"settings": [{"key": "a", "widget": "html", "label": "A"}]}, "settings"),
                     ({"settings": [{"key": "a", "widget": "select", "label": "A"}]}, "options"),
                     ({"secrets": ["../x"]}, "secrets"), ({"static": "../../etc/"}, "static"), ({"requires": ["os; rm -rf /"]}, "requires"),
                     ({"helper": "ein Text"}, "helper"), ({"helper": {}}, "helper.desc"), ({"helper": {"desc": "x" * 300}}, "helper.desc"),
                     ({"access": ["alles"]}, "access"), ({"access": ["devices", "devices"]}, "access"), ({"access": "devices"}, "access"),
                     ({"access": [1]}, "access")):
    msg = manifest_error(change)
    assert msg and word in msg, (change, msg)
assert manifest_error({"entry": "os:system"}) == "", "a module name alone is not an error – loading only happens from the plugin folder"
(tmp / "manifeste" / "probe" / "manifest.json").write_text("{kaputt", encoding="utf-8")
try:
    plugins.read_manifest(tmp / "manifeste" / "probe")
    raise AssertionError("broken JSON was accepted")
except plugins.ManifestError as exc:
    assert "JSON" in str(exc)
assert manifest_error({"helper": {"desc": "Hilfsprozess"}, "access": ["devices", "home"]}) == ""
matter_manifest = plugins.read_manifest(PUBLIC / "plugins" / "matter")
assert matter_manifest.api_revision == 3 and matter_manifest.helper and matter_manifest.access == ["devices", "rooms", "scenes", "home"]
assert matter_manifest.secrets == ["storage_key", "passcode"] and matter_manifest.requires == ["cryptography"]
assert [(e["key"], e["hosts"], e["scope"]) for e in matter_manifest.egress] == [("runtime", ["nodejs.org"], "internet")]
assert good.helper == "" and good.access == [], "without a declaration: no helper program, no access"
# UDP in the home network (revision 4): only on a LAN target, only with an explicit "udp": true
sonos_manifest = plugins.read_manifest(PUBLIC / "plugins" / "sonos")
assert sonos_manifest.api_revision == 6 and sonos_manifest.id_prefixes == ["sonosroom:"] and not sonos_manifest.secrets
assert [(e["key"], e["scope"], e["udp"]) for e in sonos_manifest.egress] == [("speakers", "lan", True)]
assert manifest_error({"egress": [{"key": "x", "desc": "d", "scope": "lan", "udp": True}]}) == ""
print("1 manifest: required fields, id = folder, API version and revision, entry point, prefixes, egress, settings, secrets,"
      "helper program and access rights checked")

# ------------------------------------------------------------------ 2) Built-in ones loaded, the template not
ids = {r.id: r for r in plugins.records()}
assert ids["hue"].origin == "builtin" and ids["hue"].active and ids["qivicon"].origin == "builtin", ids
assert all(ids[pid].origin == "builtin" and ids[pid].active for pid in ("mqtt", "network", "matter", "fritzbox")), ids
assert "_template" not in ids and "example" not in ids, "the template must not be loaded"
assert not any(n.startswith("comfyos_plugin_example") for n in sys.modules)
print("2 built-in plugins load at the start; folders starting with _ are templates")


# ------------------------------------------------------------------ Mock-Home-Base (ein Thermostat)
class MockHB(QiviconConnector):
    """Home Base of this test: one thermostat in the bathroom, commands are recorded in writes."""
    writes: list = []

    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}

    async def list_devices(self, max_age=None):
        """Ein Thermostat."""
        return [{"id": "hb:therm", "name": "Heizung", "kind": "thermostat", "room": "Bad", "room_id": "bad", "online": True,
                 "temperature": 20.5, "target": 21.0, "control_item": "T1", "errors": [], "props": {}, "values": []}]

    async def list_rooms(self):
        """Ein Raum."""
        return [{"name": "bad", "label": "Bad"}]

    async def set_device_state(self, device_id, state):
        """Record a command and confirm the target temperature."""
        self.writes.append((device_id, state))
        return {"target": state.get("target")}


def write_plugin(pid: str, body: str, manifest: dict | None = None) -> Path:
    """Write a user-added plugin with this source code into the folder of the own plugins."""
    d = USER / pid
    d.mkdir(parents=True, exist_ok=True)
    m = {"id": pid, "name": pid.capitalize(), "version": "1.0.0", "api": plugin_api.API_VERSION, "entry": "__init__:setup",
         "id_prefixes": [pid + ":"], **(manifest or {})}
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    (d / "__init__.py").write_text("from plugin_api import Capability, Device, Plugin, PluginError\n" + body, encoding="utf-8")
    return d


SIMPLE = '''
class P(Plugin):
    async def devices(self):
        return [Device(id="{pid}:a", name="{name}", kind="switch", state={{"on": False}}, capabilities=[Capability("on", writable=True)])]
    async def command(self, device_id, state, source):
        return {{"on": bool(state.get("on"))}}
def setup(context):
    return P(context)
'''

with TestClient(main.app) as client:
    install_homebase(MockHB("https://qivicon", "", "x"))
    auth.set_password(PW)
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def ok(r, code=200):
        assert r.status_code == code, (r.status_code, r.text[:400])
        return r.json()

    def listed():
        return {p["id"]: p for p in ok(client.get("/api/plugins"))["plugins"]}

    def devs():
        return {d["id"]: d for d in ok(client.get("/api/devices"))}

    def enable(pid, on=True, password=None, code=200):
        return ok(client.put(f"/api/plugins/{pid}/enabled", json={"enabled": on, **({"password": password} if password else {})}), code)

    # ------------------------------------------------------------------ 3) Copied _template: tile, approval, devices
    shutil.copytree(PUBLIC / "plugins" / "_template", USER / "example")
    found = ok(client.post("/api/plugins/rescan"))
    ex = listed()["example"]
    assert ex["state"] == "new" and ex["origin"] == "user" and ex["name"] == "Beispiel-Plugin", ex
    assert [s["key"] for s in ex["settings"]] == ["lamp_name", "dimmable"] and ex["values"] == {"lamp_name": "Beispiel-Lampe", "dimmable": True}
    assert not any(n.startswith("comfyos_plugin_example") for n in sys.modules), "code of a plugin that is not approved was loaded"
    assert "example:lamp" not in devs()
    enable("example", code=401)                                  # foreign code: no approval without the password
    enable("example", password="falsch", code=401)
    assert listed()["example"]["state"] == "new"
    r = enable("example", password=PW)
    assert r["state"] == "active" and r["status"]["connected"], r
    assert any("freigegeben" in e["message"] for e in events.recent(10) if e["type"] == "security")
    assert settings_store.load_settings()["plugins"]["trusted"] == ["example"]
    lamp = devs()["example:lamp"]
    assert lamp["kind"] == "light" and lamp["name"] == "Beispiel-Lampe" and lamp["on"] is False and lamp["plugin"] == "example", lamp
    assert lamp["control"] == {"plugin": "example", "device_id": "example:lamp"} and lamp["room"] == "Nicht zugeordnet"
    assert devs()["hb:therm"]["plugin"] == "qivicon"             # the Home Base is a plugin like any other
    print("3 copied _template: appears as a tile, only loads after approval with the password, delivers its device")

    # ------------------------------------------------------------------ 4) Switching, settings, own route, name, removing
    r = ok(client.post("/api/devices/example:lamp/command", json={"state": {"on": True, "brightness": 30}}))
    assert r["via"] == "example" and devs()["example:lamp"]["on"] is True and devs()["example:lamp"]["brightness"] == 30
    ok(client.post("/api/devices/example:lamp/command", json={"state": {"brightness": 900}}), 400)
    assert any("Beispiel-Lampe" in e["message"] and "(Beispiel-Plugin)" in e["message"] for e in events.recent(10))
    assert ok(client.get("/api/plugins/example/info"))["brightness"] == 30                       # eigene Route des Plugins
    assert json.loads((plugins.PLUGIN_DATA_DIR / "example" / "lamp.json").read_text())["on"] is True
    ok(client.post("/api/devices/hb:therm/command", json={"state": {"target": 22}}))
    assert MockHB.writes[-1] == ("hb:therm", {"target": 22})
    r = ok(client.put("/api/plugins/example/settings", json={"values": {"lamp_name": "Schreibtisch", "dimmable": False}}))
    assert r["values"] == {"lamp_name": "Schreibtisch", "dimmable": False}
    assert devs()["example:lamp"]["name"] == "Schreibtisch" and devs()["example:lamp"]["brightness"] is None
    ok(client.put("/api/plugins/example/settings", json={"values": {"gibtsnicht": 1}}), 400)
    ok(client.put("/api/devices/example:lamp/name", json={"name": "Meine Lampe"}))
    assert devs()["example:lamp"]["name"] == "Meine Lampe" and device_store.get()["names"] == {"example:lamp": "Meine Lampe"}
    ok(client.post("/api/devices/remove", json={"device_id": "example:lamp"}))
    assert "example:lamp" not in devs() and [x["name"] for x in ok(client.get("/api/plugins/example/devices"))["removed"]] == ["Meine Lampe"]
    ok(client.post("/api/devices/remove", json={"device_id": "example:lamp", "removed": False}))
    assert "example:lamp" in devs()
    assert [x["id"] for x in ok(client.get("/api/plugins/example/devices"))["devices"]] == ["example:lamp"]
    print("4 plugin device: switch, settings from the manifest, own route, own name, remove/restore")

    # ------------------------------------------------------------------ 5) The device lock applies to plugins too
    ok(client.post("/api/devices/example:lamp/lock"))
    ok(client.post("/api/devices/example:lamp/command", json={"state": {"on": False}}), 423)
    ctx = plugins.active("example").context
    try:
        client.portal.call(ctx.switch, "example:lamp", {"on": False})       # this is how a plugin switches from a route of its own
        raise AssertionError("a plugin switched a locked device")
    except device_store.DeviceLocked:
        pass
    ok(client.post("/api/devices/example:lamp/unlock"))
    assert client.portal.call(ctx.switch, "example:lamp", {"on": False})["via"] == "example" and devs()["example:lamp"]["on"] is False
    print("5 device lock: commands to plugin devices and context.switch from plugin routes are rejected (423)")

    # ------------------------------------------------------------------ 6) Broken plugins stay contained
    (USER / "kaputt1").mkdir(parents=True)
    (USER / "kaputt1" / "manifest.json").write_text("{", encoding="utf-8")
    write_plugin("kaputt2", "def setup(context):\n    raise RuntimeError('geheim-im-fehlertext')\n")
    write_plugin("kaputt3", "class P(Plugin):\n    async def start(self):\n        raise OSError('x')\ndef setup(context):\n    return P(context)\n")
    write_plugin("kaputt4", '''
class P(Plugin):
    async def devices(self):
        raise ValueError("boom")
    async def command(self, device_id, state, source):
        raise KeyError("boom")
def setup(context):
    return P(context)
''')
    write_plugin("kaputt5", "def setup(context):\n    return 42\n")
    write_plugin("fremd", SIMPLE.format(pid="andere", name="Falsches Präfix"))           # reports devices with a foreign id
    write_plugin("gut", SIMPLE.format(pid="gut", name="Gute Steckdose"))
    (USER / "hue").mkdir()                                                                # will ein eingebautes ersetzen
    (USER / "hue" / "manifest.json").write_text(json.dumps({**BASE, "id": "hue", "id_prefixes": ["hue2:"]}), encoding="utf-8")
    ok(client.post("/api/plugins/rescan"))
    for pid in ("kaputt2", "kaputt3", "kaputt4", "kaputt5", "fremd", "gut"):
        enable(pid, password=PW)
    st = listed()
    assert st["kaputt1"]["state"] == "error" and "JSON" in st["kaputt1"]["error"], st["kaputt1"]
    assert st["kaputt2"]["state"] == "error" and "geheim" not in st["kaputt2"]["error"] and "RuntimeError" in st["kaputt2"]["error"]
    assert st["kaputt3"]["state"] == "error" and st["kaputt5"]["state"] == "error" and "Plugin" in st["kaputt5"]["error"], st
    assert st["kaputt4"]["state"] == "active" and st["gut"]["state"] == "active" and st["hue"]["origin"] == "builtin", st
    d = devs()
    assert {"hb:therm", "example:lamp", "gut:a"} <= set(d) and not any(k.startswith("andere:") for k in d), list(d)
    assert "ValueError" in listed()["kaputt4"]["error"]                                    # devices() raises: the source fails, the rest stays
    r = ok(client.post("/api/devices/gut:a/command", json={"state": {"on": True}}))
    assert r["via"] == "gut" and ok(client.get("/api/health"))
    try:
        client.portal.call(plugins.command, {"id": "x", "name": "X", "control": {"plugin": "kaputt4", "device_id": "kaputt4:a"}},
                           {"on": True}, "command")
        raise AssertionError("an exception in command() was not reported")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 502 and "KeyError" in exc.detail, exc
    assert any("läuft nicht" in e["message"] for e in events.recent(40))
    print("6 broken plugins (manifest, setup, start, devices, command, wrong prefix, duplicate id): core and others keep running")

    # ------------------------------------------------------------------ 7) Ausschalten
    assert enable("gut", False)["state"] == "off" and "gut:a" not in devs()
    ok(client.get("/api/plugins/example/info"))
    enable("example", False)
    ok(client.get("/api/plugins/example/info"), 404)
    assert "example:lamp" not in devs() and settings_store.load_settings()["plugins"]["disabled"] == ["example", "gut"]
    assert enable("example")["state"] == "active" and devs()["example:lamp"]["name"] == "Meine Lampe"   # already approved: without the password
    # the Home Base can be disabled too: ComfyOS keeps running without it (since 0.19)
    assert enable("qivicon", False)["state"] == "off" and "hb:therm" not in devs() and "example:lamp" in devs()
    assert ok(client.get("/api/overview"))["devices_error"] is None
    enable("qivicon")
    install_homebase(MockHB("https://qivicon"))
    assert devs()["hb:therm"]["name"] == "Heizung"
    ok(client.put("/api/plugins/gibtsnicht/enabled", json={"enabled": True}), 404)
    print("7 disabling: no devices, own routes do not answer; enabling brings everything back; ComfyOS keeps running without a Home Base")

    # ------------------------------------------------------------------ 8) Egress and secrets only as declared in the manifest
    write_plugin("netz", '''
class P(Plugin):
    pass
def setup(context):
    return P(context)
''', {"egress": [{"key": "lan", "desc": "Gerät im Heimnetz", "scope": "lan"},
                 {"key": "cloud", "desc": "Hersteller-Cloud", "hosts": ["api.example.com"], "data": "Gerätezustand"}],
      "secrets": ["token"], "settings": [{"key": "token", "widget": "password", "label": "Zugangstoken"}]})
    ok(client.post("/api/plugins/rescan"))
    enable("netz", password=PW)
    nctx = plugins.active("netz").context

    async def calls():
        got = []
        for key, url in (("lan", "https://192.168.1.20/x"), ("lan", "https://evil.example.com/x"), ("cloud", "https://api.example.com/v1"),
                         ("cloud", "https://evil.example.com/x"), ("cloud", "https://192.168.1.20/x")):
            try:
                async with nctx.http(key) as c:
                    await c.get(url)
                got.append("ok")
            except egress.EgressBlocked:
                got.append("blockiert")
        return got

    outbound.clear()
    assert client.portal.call(calls) == ["ok", "blockiert", "ok", "blockiert", "blockiert"]
    assert outbound == ["192.168.1.20", "api.example.com"], outbound
    try:
        nctx.http("unbekannt")
        raise AssertionError("a target that was not declared was allowed")
    except plugin_api.PluginError:
        pass
    conn = {c["id"]: c for c in ok(client.get("/api/system/egress"))["connections"]}
    assert conn["plugin.netz.cloud"]["hosts"] == ["api.example.com"] and conn["plugin.netz.cloud"]["state"] == "on", conn
    assert "plugin.netz.lan" not in conn, "the home network is not in the egress register"
    ok(client.put("/api/plugins/netz/settings", json={"values": {"token": "Geheim-" + "Token-99"}}))
    assert settings_store.get_secret("plugin:netz:token") == "Geheim-" + "Token-99" and nctx.secrets.get("token")
    assert "Token-99" not in json.dumps(settings_store.load_settings()) and "Token-99" not in client.get("/api/plugins").text
    assert listed()["netz"]["values"] == {"token": True}                                  # the interface only learns: set
    try:
        nctx.secrets.set("anderes", "x")
        raise AssertionError("a secret that was not declared was stored")
    except plugin_api.PluginError:
        pass
    # the same limits apply to TCP connections (context.open_stream, API revision 2) – through DNS names too
    opened: list[tuple] = []
    stream_answer = {"mode": "ok"}

    async def fake_stream(host, port, tls):
        opened.append((host, port, tls))
        if stream_answer["mode"] == "refused":
            raise ConnectionRefusedError()
        if stream_answer["mode"] == "silent":
            raise asyncio.TimeoutError()
        return ("reader", "writer")

    async def fake_resolve(host, port):
        return {"geraet.fritz.box": ["192.168.1.30"], "umgebogen.fritz.box": ["8.8.4.4"]}.get(host, [])

    egress._stream_factory, egress.resolve = fake_stream, fake_resolve

    async def streams():
        got = []
        for key, host, port in (("lan", "192.168.1.20", 1883), ("lan", "8.8.8.8", 1883), ("lan", "geraet.fritz.box", 1883),
                                ("lan", "umgebogen.fritz.box", 1883), ("lan", "unbekannt.fritz.box", 1883),
                                ("cloud", "api.example.com", 8883), ("cloud", "evil.example.com", 8883), ("cloud", "192.168.1.20", 8883),
                                ("unbekannt", "192.168.1.20", 1883), ("lan", "192.168.1.20", 0), ("lan", "192.168.1.20", 70000),
                                ("lan", "192.168.1.20/pfad", 80), ("lan", "nutzer@192.168.1.20", 80)):
            try:
                await nctx.open_stream(key, host, port, tls=key == "cloud")
                got.append("ok")
            except plugin_api.DeviceUnreachable:
                got.append("stumm")
            except plugin_api.PluginError:
                got.append("blockiert")
        return got

    assert client.portal.call(streams) == ["ok", "blockiert", "ok", "blockiert", "blockiert", "ok", "blockiert", "blockiert",
                                           "blockiert", "blockiert", "blockiert", "blockiert", "blockiert"]
    assert opened == [("192.168.1.20", 1883, False), ("geraet.fritz.box", 1883, False), ("api.example.com", 8883, True)], opened

    async def stream_error(mode):
        stream_answer["mode"] = mode
        try:
            await nctx.open_stream("lan", "192.168.1.20", 80)
        except plugin_api.PluginError as exc:
            return type(exc).__name__
        finally:
            stream_answer["mode"] = "ok"

    # "port closed" is an answer (the device is there), silence is not – the network plugin distinguishes by that
    assert client.portal.call(stream_error, "refused") == "ConnectionRefused" and client.portal.call(stream_error, "silent") == "DeviceUnreachable"
    assert nctx.api_revision == plugin_api.API_REVISION == 7
    enable("netz", False)
    assert {c["id"]: c for c in ok(client.get("/api/system/egress"))["connections"]}["plugin.netz.cloud"]["state"] == "off"
    print("8 egress: LAN target only into the home network, internet target only to declared hosts and in the register – for HTTP and"
          "TCP (open_stream), through DNS names too; secrets only as declared")

    # ------------------------------------------------------------------ 8b) The core owns its fields; adopt all
    write_plugin("frech", '''
class P(Plugin):
    async def devices(self):
        fake = {"on": True, "id": "hb:therm", "name": "Umbenannt", "room": "Tresor", "room_id": "bad", "kind": "thermostat",
                "control": {"plugin": "qivicon", "device_id": "hb:therm"}, "control_item": "T1", "locked": True, "plugin": "qivicon",
                "online": False, "_intern": 1, 7: "keine Zeichenkette", "humidity": 55}
        found = [Device(id=f"frech:neu{n}", name=f"Gefunden {n}", kind="switch", state={"on": False}, discovered=True) for n in (1, 2, 3)]
        return [Device(id="frech:a", name="Freches Gerät", kind="switch", state=fake, capabilities=[Capability("on", writable=True)])] + found
    async def command(self, device_id, state, source):
        return {"on": bool(state.get("on"))}
def setup(context):
    return P(context)
''')
    ok(client.post("/api/plugins/rescan"))
    enable("frech", password=PW)
    row = devs()["frech:a"]
    assert (row["id"], row["name"], row["room"], row["kind"], row["plugin"], row["online"]) == \
        ("frech:a", "Freches Gerät", "Nicht zugeordnet", "switch", "frech", True), row
    assert row["control"] == {"plugin": "frech", "device_id": "frech:a"} and not row.get("locked") and "_intern" not in row and 7 not in row
    assert row["on"] is True and row["humidity"] == 55, "the state values themselves arrive"
    assert devs()["hb:therm"]["name"] == "Heizung" and devs()["hb:therm"]["plugin"] == "qivicon"
    sent: list[dict] = []
    original_publish, core.publish = core.publish, sent.append
    try:
        fctx = plugins.active("frech").context
        fctx.device_changed("frech:a", {"on": False, "type": "reload", "id": "hb:therm", "locked": True, "humidity": 60, "erfunden": 1})
        fctx.device_changed("frech:a", {"type": "toast", "text": "Gruß vom Plugin"})
    finally:
        core.publish = original_publish
    assert sent == [{"type": "device", "id": "frech:a", "on": False, "humidity": 60}], sent
    assert [c["id"] for c in ok(client.get("/api/plugins/frech/devices"))["new"]] == ["frech:neu1", "frech:neu2", "frech:neu3"]
    assert ok(client.post("/api/plugins/frech/adopt_all")) == {"ok": True, "adopted": 3}
    assert {"frech:neu1", "frech:neu2", "frech:neu3"} <= set(devs()) and ok(client.post("/api/plugins/frech/adopt_all"))["adopted"] == 0
    enable("frech", False)
    print("8b a plugin overwrites neither id, name, room, control nor the lock of a device row; live messages"
          "only carry state fields; 'Alle übernehmen'")

    # ------------------------------------------------------------------ 8d) Access to everything ComfyOS controls – only with permission
    WATCHER = '''
class P(Plugin):
    def __init__(self, context):
        super().__init__(context)
        self.seen = []
    async def start(self):
        try:
            self.context.on_change(lambda device_id, changes: self.seen.append((device_id, dict(changes))))
        except PluginError:
            pass                                    # ohne Erlaubnis: das Plugin läuft trotzdem
def setup(context):
    return P(context)
'''
    write_plugin("zugriff", WATCHER, {"api_revision": 3, "access": ["devices", "rooms", "scenes", "home"]})
    write_plugin("ohnerecht", WATCHER, {"api_revision": 3})
    write_plugin("nurheim", WATCHER, {"api_revision": 3, "access": ["home"]})
    ok(client.post("/api/plugins/rescan"))
    for pid in ("zugriff", "ohnerecht", "nurheim"):
        enable(pid, password=PW)
    shown = listed()
    assert [a["key"] for a in shown["zugriff"]["access"]] == ["devices", "rooms", "scenes", "home"] and shown["ohnerecht"]["access"] == []
    assert all(a["text"] for a in shown["zugriff"]["access"]) and shown["zugriff"]["helper"] is None, "the tile names the rights in plain words"
    assert listed()["matter"]["helper"] and len(listed()["matter"]["access"]) == 4
    actx, octx, hctx = (plugins.active(pid).context for pid in ("zugriff", "ohnerecht", "nurheim"))

    def denied(call, *args):
        """Call of a plugin that must fail because of a missing permission or at ComfyOS; returns the kind of the error."""
        try:
            client.portal.call(call, *args)
        except plugin_api.PluginError as exc:
            return type(exc).__name__
        raise AssertionError(f"not rejected: {getattr(call, '__name__', call)}")

    for call, args in ((octx.all_devices, ()), (octx.command, ("hb:therm", {"target": 19})), (octx.room_lights, (None, False)),
                       (octx.scenes, ()), (octx.house_scene, ("abendrot",)), (octx.home_state, ()), (octx.home, ("AWAY",)),
                       (hctx.all_devices, ()), (hctx.command, ("hb:therm", {"target": 19})), (hctx.scenes, ()), (hctx.room_lights, (None, True))):
        assert denied(call, *args) == "PluginError", call
    try:
        octx.on_change(print)
        raise AssertionError("on_change was accepted without permission")
    except plugin_api.PluginError:
        pass
    assert MockHB.writes[-1] == ("hb:therm", {"target": 22}), "switching happened without permission"
    views = {v.id: v for v in client.portal.call(actx.all_devices)}
    therm = views["hb:therm"]
    assert isinstance(therm, plugin_api.DeviceView) and (therm.name, therm.kind, therm.room, therm.plugin) == ("Heizung", "thermostat", "Bad", "qivicon")
    assert "target" in therm.state and therm.state["temperature"] == 20.5 and therm.controllable and not therm.locked and therm.online
    assert any(c.key == "target" for c in therm.capabilities), therm.capabilities
    assert {f for f in vars(therm)} == {"id", "name", "kind", "plugin", "room_id", "room", "online", "locked", "controllable", "state",
                                        "capabilities", "light", "media"}, "the view only carries what the user sees too"
    assert "T1" not in repr(views) and "control" not in therm.state, "control channel or source of a foreign plugin in the view"
    assert "example:lamp" in views and views["example:lamp"].plugin == "example"
    seen = plugins.active("zugriff").seen
    seen.clear()
    r = client.portal.call(actx.command, "hb:therm", {"target": 19.5})
    assert MockHB.writes[-1] == ("hb:therm", {"target": 19.5}) and r["via"] == "qivicon"
    assert any("Heizung: 19.5" in e["message"] and "(durch Zugriff)" in e["message"] for e in events.recent(10)), \
        [e["message"] for e in events.recent(5)]                       # im Protokoll steht, wer geschaltet hat
    assert ("hb:therm", {"target": 19.5}) in seen, seen                # the change arrives at listening plugins
    assert not plugins.active("ohnerecht").seen and not plugins.active("nurheim").seen, "changes were overheard without permission"
    assert plugins.watching(), "a listening plugin keeps the live comparison awake"
    ok(client.post("/api/devices/hb:therm/lock"))
    assert ("hb:therm", {"locked": True}) in seen
    assert denied(actx.command, "hb:therm", {"target": 25}) == "DeviceLocked" and MockHB.writes[-1] == ("hb:therm", {"target": 19.5})
    ok(client.post("/api/devices/hb:therm/unlock"))
    assert denied(actx.command, "gibts:nicht", {"on": True}) == "PluginError"
    client.portal.call(actx.room_lights, None, True, 40)
    assert devs()["example:lamp"]["on"] is True
    assert any("(durch Zugriff)" in e["message"] and "Lampe" in e["message"] for e in events.recent(10))
    client.portal.call(actx.room_lights, None, False)
    assert devs()["example:lamp"]["on"] is False
    scene_list = client.portal.call(actx.scenes)
    assert scene_list and all(isinstance(s, plugin_api.SceneView) and s.id and s.name and not s.active for s in scene_list)
    assert denied(actx.house_scene, "gibtsnicht") == "PluginError"
    assert client.portal.call(actx.home_state) in ("PRESENT", "AWAY")
    seen.clear()
    client.portal.call(actx.home, "AWAY")
    assert home_store.get()["state"] == "AWAY" and client.portal.call(hctx.home_state) == "AWAY" and ("", {}) in seen
    assert any(e["type"] == "home" and "(durch Zugriff)" in e["message"] for e in events.recent(10))
    client.portal.call(hctx.home, "PRESENT")
    assert home_store.get()["state"] == "PRESENT" and denied(actx.home, "URLAUB") == "PluginError"
    seen.clear()
    core.publish({"type": "reload"})
    core.publish({"type": "toast", "text": "nichts für Plugins"})
    # only "read again" arrives – the live comparison may report device changes next to it, the message "toast" never
    assert [entry for entry in seen if entry[0] == ""] == [("", {})] and not any("text" in changes for _, changes in seen), seen
    enable("zugriff", False)
    assert denied(actx.all_devices) == "PluginError" and denied(actx.command, "hb:therm", {"target": 18}) == "PluginError"
    assert not plugins.watching(), "a disabled plugin keeps listening"
    for pid in ("ohnerecht", "nurheim"):
        enable(pid, False)
    print("8d access with permission: view of all devices without internals, switching through the core (lock, event log 'durch …'),"
          "room light, scenes, home state; without permission and disabled none of it")

    # ------------------------------------------------------------------ 8c) Heizroutinen abgleichen (context.heating)
    from app import routines_store

    SYNCER = '''
from plugin_api import RemoteRoutine
class P(Plugin):
    syncs_heating = True
    def __init__(self, context):
        super().__init__(context)
        self.rounds, self.seen, self.remote = [], None, {}      # remote: was die Gegenseite an Routinen führt
    def heating_status(self):
        return {"configured": True, "ok": True, "interval": 60, "name": "Testabgleich", "last_error": None, "intern": {"x": 1}}
    async def sync_heating(self, pull=True, force=False):
        plans = self.context.heating
        self.rounds.append((pull, force))
        self.seen = plans.changes()
        for key in list(self.seen.created):
            assert plans.is_local(key) and plans.routine_name(key) and plans.schedule(key) is not None
            self.remote["FERN-" + key[-4:]] = RemoteRoutine("FERN-" + key[-4:], plans.routine_name(key), plans.schedule(key))
            plans.created(key, "FERN-" + key[-4:])
        changes = plans.changes()
        for key in changes.updated:
            plans.done(key, "updated")
        for key in changes.renamed:
            plans.done(key, "renamed")
        for key in changes.deleted:
            self.remote.pop(key, None)
            plans.done(key, "deleted")
        if changes.activate:
            plans.done(changes.activate, "activate")
        if plans.home().state_changed:
            plans.home_done(state=True)
        if pull:
            auto = RemoteRoutine("AUTO", "Komfort", {"0": {"hz_1": {"0600": "21", "2200": "ECO"}, "SYSTEM": {"0000": "OFF"}}}, 5)
            self.merged = plans.merge([auto] + list(self.remote.values()), "AUTO", {"ECO": 16, "UNSINN": 99}, {"hz_1": "Bad", "SYSTEM": "System"})
            plans.merge_home("AWAY", "AUTO", None)
        return self.heating_status()
def setup(context):
    return P(context)
'''
    assert ok(client.get("/api/sync/status"))["configured"] is False                  # the Home Base syncs nothing (yet)
    made = ok(client.post("/api/routines", json={"name": "Urlaub"}))
    assert made["sync"] == {"synced": False, "reason": "Abgleich nicht aktiv"} and routines_store.is_local_key(made["routine"]["key"])
    write_plugin("abgleich", SYNCER)
    ok(client.post("/api/plugins/rescan"))
    enable("abgleich", password=PW)
    syncer = plugins.active("abgleich")
    st = ok(client.get("/api/sync/status"))
    assert st["configured"] and st["name"] == "Testabgleich" and st["interval"] == 60 and st["pending"] >= 1 and "intern" not in st, st
    st = ok(client.post("/api/sync?force=true"))
    assert syncer.rounds == [(True, True)] and syncer.seen.created == [made["routine"]["key"]], (syncer.rounds, syncer.seen)
    keys = {r["key"]: r for r in ok(client.get("/api/routines"))["routines"]}
    remote = "FERN-" + made["routine"]["key"][-4:]
    assert remote in keys and made["routine"]["key"] not in keys, keys                 # the local routine now carries the key of the other side
    assert keys["AUTO"]["name"] == "Komfort" and keys["AUTO"]["active"] and syncer.merged["added"] == 1, (keys, syncer.merged)
    auto = ok(client.get("/api/routines/AUTO"))
    assert auto["levels"]["ECO"] == 16.0 and "UNSINN" not in auto["levels"] and auto["room_names"] == {"hz_1": "Bad"}, auto
    assert [p["temp"] for p in auto["schedule"]["hz_1"]["0"]] == [21.0, 16.0] and "SYSTEM" not in auto["schedule"], auto["schedule"]
    assert home_store.get()["state"] == "AWAY" and home_store.get()["present_routine"] == "AUTO"
    assert st["pending"] == 0, st
    # changes afterwards: rename, change the schedule, activate, home state -> each pushed at once (without fetching) and confirmed
    r = ok(client.put("/api/routines/AUTO/name", json={"name": "Wohlfühlen"}))
    assert r["sync"] == {"synced": True, "reason": None} and syncer.rounds[-1] == (False, True) and syncer.seen.renamed == ["AUTO"]
    ok(client.put("/api/routines/AUTO/schedule", json={"changes": [{"room": "hz_1", "day": 1, "points": [{"time": "07:00", "temp": 20.5}]}]}))
    assert syncer.seen.updated == ["AUTO"]
    ok(client.post(f"/api/routines/{remote}/activate"))
    assert syncer.seen.activate == remote
    ok(client.post("/api/home", json={"state": "PRESENT"}))
    assert home_store.get()["pending"] is False and ok(client.get("/api/sync/status"))["pending"] == 0
    ok(client.delete(f"/api/routines/{remote}"))
    assert syncer.seen.deleted == [remote]
    enable("abgleich", False)
    assert ok(client.get("/api/sync/status"))["configured"] is False
    print("8c heating sync: local changes go to the plugin as a list and only count after confirmation; routines, levels,"
          "room names and home/away of the other side are taken over; without a plugin everything stays local")

    # ------------------------------------------------------------------ 9) Static files
    r = client.get("/plugins/hue/static/settings.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"] and "HO.plugins" in r.text, r.status_code
    for bad in ("../manifest.json", "..%2f__init__.py", "settings.js/../../bridge.py", "/etc/passwd", "settings.py", "x" * 300, ".hidden.js"):
        assert client.get("/plugins/hue/static/" + bad).status_code == 404, bad
    assert client.get("/plugins/gut/static/settings.js").status_code == 404           # disabled or without static
    assert client.get("/plugins/gibtsnicht/static/settings.js").status_code == 404
    print("9 static files: only from the static folder of enabled plugins, no path tricks")

# ------------------------------------------------------------------ 10) SDK and style rules of the built-in plugins
assert (PUBLIC / "sdk" / "plugin_api.py").read_bytes() == (ROOT / "backend" / "app" / "plugin_api.py").read_bytes(), \
    "public/sdk/plugin_api.py differs from backend/app/plugin_api.py – renew the copy"
api_src = (ROOT / "backend" / "app" / "plugin_api.py").read_text(encoding="utf-8")
assert "from ." not in api_src and "import app" not in api_src, "plugin_api must not import anything from the core"
ALLOWED = {"plugin_api", "fastapi", "pydantic", "httpx", "testing"} | set(sys.stdlib_module_names)
# Plus, per plugin, what its manifest names under "requires" (e.g. cryptography in the Matter plugin).
# test helpers a plugin brings next to its tests (e.g. the in-memory broker of the MQTT plugin)
TEST_HELPERS = {"fake_broker"}
checked = 0
for folder in sorted(p for p in (PUBLIC / "plugins").iterdir() if p.is_dir()):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert (folder / "README.md").is_file() and list((folder / "tests").glob("test_*.py")), f"{folder.name}: README.md and tests/ are part of it"
    allowed_here = ALLOWED | {name.replace("-", "_") for name in manifest.get("requires", [])}
    for f in sorted(folder.rglob("*.py")):
        if "node_modules" in f.parts:
            continue                    # foreign code of the helper program (only present on the development machine)
        tree = ast.parse(f.read_text(encoding="utf-8"))
        rel = f.relative_to(ROOT)
        assert ast.get_docstring(tree), f"{rel}: module docstring missing"
        # overridden methods need no docstring of their own if the base class in the same file has one
        documented_names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(n)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [] if node.level else [(node.module or "").split(".")[0]]      # relative Importe = eigenes Paket
            else:
                continue
            foreign = [n for n in names if n not in allowed_here and n != folder.name and not ("tests" in f.parts and n in TEST_HELPERS)]
            assert not foreign, f"{rel}: imports {foreign} – allowed are the standard library, plugin_api, fastapi, pydantic, httpx, 'requires'"
        if "tests" in f.parts:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                nested = any(isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and node in ast.walk(parent) and parent is not node
                             for parent in ast.walk(tree))
                documented = ast.get_docstring(node) or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                                         and (node.name in dir(plugin_api.Plugin) or node.name in documented_names))
                assert documented or nested, f"{rel}: {node.name} has no docstring"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                untyped = [a.arg for a in args if a.annotation is None and a.arg not in ("self", "cls")]
                assert not untyped and node.returns is not None, f"{rel}: {node.name} without type hints ({untyped or 'return'})"
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                assert len(node.id) > 1 or node.id in ("x", "y", "z", "n", "_"), f"{rel}: one-letter name '{node.id}' (line {node.lineno})"
        checked += 1
    assert manifest["entry"] == "__init__:setup"
# The release package ships the built-in plugins, the template and the SDK – without their tests and docs
from app import updater  # noqa: E402

files = updater.release_files(ROOT)
for must in ("public/plugins/hue/manifest.json", "public/plugins/hue/__init__.py", "public/plugins/hue/bridge.py",
             "public/plugins/hue/static/settings.js", "public/plugins/_template/__init__.py", "public/plugins/_template/manifest.json",
             "public/sdk/plugin_api.py", "backend/app/plugins.py", "backend/app/plugin_api.py",
             "public/plugins/mqtt/manifest.json", "public/plugins/mqtt/__init__.py", "public/plugins/mqtt/protocol.py",
             "public/plugins/mqtt/client.py", "public/plugins/mqtt/discovery.py", "public/plugins/mqtt/translate.py",
             "public/plugins/mqtt/registry.py", "public/plugins/mqtt/manual.py", "public/plugins/mqtt/routes.py",
             "public/plugins/mqtt/static/settings.js", "public/plugins/network/manifest.json",
             "public/plugins/network/__init__.py", "public/plugins/network/routes.py", "public/plugins/network/static/settings.js",
             "public/plugins/qivicon/manifest.json", "public/plugins/qivicon/__init__.py", "public/plugins/qivicon/connector.py",
             "public/plugins/qivicon/model.py", "public/plugins/qivicon/pairing.py", "public/plugins/qivicon/telekom.py",
             "public/plugins/qivicon/magenta.py", "public/plugins/qivicon/routes.py", "public/plugins/qivicon/static/settings.js",
             "public/plugins/qivicon/static/pairing.js", "backend/app/heating_sync.py", "backend/app/heating_model.py",
             "backend/app/helpers.py", "public/sdk/testing.py", "public/plugins/matter/manifest.json", "public/plugins/matter/__init__.py",
             "public/plugins/matter/protocol.py", "public/plugins/matter/storage.py", "public/plugins/matter/devices.py",
             "public/plugins/matter/home.py", "public/plugins/matter/shares.py", "public/plugins/matter/runtime.py",
             "public/plugins/matter/routes.py", "public/plugins/matter/static/settings.js",
             "public/plugins/matter/bridge/dist/bridge.mjs", "public/plugins/matter/bridge/bridge.mjs",
             "public/plugins/matter/bridge/package-lock.json", "public/plugins/sonos/manifest.json",
             "public/plugins/sonos/__init__.py", "public/plugins/sonos/soap.py", "public/plugins/sonos/player.py",
             "public/plugins/sonos/household.py", "public/plugins/sonos/routes.py", "public/plugins/sonos/static/settings.js",
             "public/plugins/fritzbox/manifest.json", "public/plugins/fritzbox/__init__.py", "public/plugins/fritzbox/aha.py",
             "public/plugins/fritzbox/tr064.py", "public/plugins/fritzbox/routes.py",
             "public/plugins/fritzbox/static/settings.js", "backend/app/demo.py", "backend/app/demo_world.py"):
    assert must in files, f"missing in the release package: {must}"
assert not [f for f in files if "node_modules" in f], "node_modules in the release package"
bundle = (PUBLIC / "plugins" / "matter" / "bridge" / "dist" / "bridge.mjs").read_text(encoding="utf-8")
assert "node_modules" not in bundle.split("\n", 1)[0] and "from \"@matter" not in bundle and "require(\"@matter" not in bundle, \
    "the helper program must be bundled in one file – at run time nothing may be loaded from node_modules"
assert not [f for f in files if f.startswith("public/") and ("/tests/" in f or f.endswith(".md"))], "tests/docs of the plugins in the package"
print(f"10 SDK copy identical; {checked} plugin files: only allowed imports, docstrings, type hints, descriptive names;"
      "the release package contains plugins, template and SDK")

print("ALL PLUGIN TESTS OK")
