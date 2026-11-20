"""Plugin loader: finds, checks, loads and runs plugins – the device connections of ComfyOS.

A plugin is a folder with `manifest.json` and a Python package whose entry point (`entry`, usually
`__init__:setup`) returns a `plugin_api.Plugin`. The loader is the only place where plugin code touches the
core; plugins themselves import only `plugin_api` (see public/CONTRIBUTING.md).

Where plugins come from:
  * `public/plugins/<id>/`   built in, shipped with ComfyOS (folders starting with `_` are templates)
  * `<DATA>/plugins/<id>/`   added by the user. Foreign code runs with the rights of ComfyOS – therefore
                             such a plugin is only loaded after the user has explicitly enabled it under System
                             (`plugins.trusted`); before that only the manifest is read.

Principles:
  * **A plugin takes nothing down with it:** every exception from manifest, import, `setup`, `start`, `devices`,
    `command` stays with the plugin (state "error" or source unreachable); the core keeps running.
  * **Device lock:** `command()` checks `device_store` before every command – also for commands a plugin
    sends through `context.switch` from routes of its own.
  * **Egress:** `context.http(key)` and `context.open_stream(key, …)` (TCP, since API revision 2) only reach
    what the manifest declares under `egress` (`scope: "lan"` = home network, otherwise fixed hosts that appear in
    the egress register). A plugin without an entry sends nothing. UDP (`context.open_datagram`, `context.discover`,
    since API revision 4) exists only in the home network and only for a LAN target with `"udp": true`; the tile names it.
  * **Credentials:** `context.secrets` knows only the keys named in the manifest and lives in the secret store.
  * **Helper programs (since API revision 3):** `context.start_helper(…)` starts a process of its own only if the
    manifest names `"helper"` – through `helpers.py` with its limits (path, empty environment, JSON lines).
  * **Access to everything ComfyOS controls (since API revision 3):** only with permission in the manifest (`"access"`).
    `context.command` switches through `core.apply_device` – device lock and event log apply as everywhere.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from fastapi import APIRouter, Depends, HTTPException

from . import demo, device_store, egress, events, helpers, live_audio, plugin_api
from .device_merge import Source, plain
from .paths import APP_ROOT, DATA_DIR
from .plugin_api import (ACCESS_DEVICES, ACCESS_HOME, ACCESS_KINDS, ACCESS_ROOMS, ACCESS_SCENES, API_REVISION, API_VERSION,
                         DEVICE_KINDS, Capability, Device, DeviceView, LightCapabilities, LightState, MediaState, Plugin,
                         PluginError, SceneView, SsdpAnswer)
from .settings_store import delete_secret, get_secret, load_settings, save_settings, set_secret

BUILTIN_DIR = APP_ROOT / "public" / "plugins"
USER_DIR = DATA_DIR / "plugins"
PLUGIN_DATA_DIR = DATA_DIR / "plugin_data"
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
ENTRY_RE = re.compile(r"^(__init__|[a-z_][a-z0-9_]{0,40}):([a-z_][a-z0-9_]{0,40})$")
BUTTON_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")     # form of a Button.id (since API revision 7)
RESERVED_IDS = ("demo",)                                   # the plugin of the demo home (demo_world.py) – never a real one
WIDGETS = ("text", "number", "toggle", "select", "password")
MAX_MANIFEST_BYTES = 64_000
MAX_DEVICES = 2000
MAX_SETTING_BYTES = 64_000    # per value; enough for lists such as own MQTT devices or the shares of the Matter plugin
DEVICES_TIMEOUT = 20.0      # s – a hanging plugin does not hold up the device list
# What a plugin may do with a permission from "access" – shown like this in the tile and in the approval dialog
ACCESS_TEXT = {ACCESS_DEVICES: "alle Geräte sehen und schalten", ACCESS_ROOMS: "das Licht ganzer Räume schalten",
               ACCESS_SCENES: "Hausszenen starten und beenden", ACCESS_HOME: "Zuhause/Abwesend lesen und umschalten"}
STATIC_TYPES = {".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
                ".webp": "image/webp", ".json": "application/json"}

log = logging.getLogger("comfyos.plugins")
sys.modules.setdefault("plugin_api", plugin_api)    # Plugins schreiben: from plugin_api import …


class ManifestError(ValueError):
    """The manifest.json of a plugin is unusable – the text names the reason and is shown with the plugin."""
    pass


@dataclass
class Manifest:
    """Checked content of manifest.json: id, name, version, entry point and what the plugin needs (targets in the
    network, settings, secrets, Python packages)."""
    id: str
    name: str
    version: str
    api: int
    entry: str
    description: str = ""
    api_revision: int = 1                    # lowest revision of the API the plugin needs (additions)
    id_prefixes: list[str] = field(default_factory=list)
    egress: list[dict[str, Any]] = field(default_factory=list)
    settings: list[dict[str, Any]] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    static: str = ""
    requires: list[str] = field(default_factory=list)
    helper: str = ""                         # description of the helper program; empty = the plugin starts none
    access: list[str] = field(default_factory=list)   # Erlaubnisse aus plugin_api.ACCESS_KINDS


@dataclass
class Record:
    """A plugin that was found, with its state."""
    id: str
    name: str
    origin: str                              # "builtin" | "user"
    path: Path | None = None
    manifest: Manifest | None = None
    plugin: Plugin | None = None
    router: APIRouter | None = None
    state: str = "off"                       # "active" | "off" (disabled) | "new" (user plugin, not approved) | "error"
    error: str = ""                          # reason for "error", or the last error while querying
    started: bool = False
    started_at: float = 0.0                  # when it was started last (time.monotonic) – a fresh start is still "connecting"
    mounted: bool = False

    @property
    def active(self) -> bool:
        """Enabled and loaded. A disabled plugin keeps its instance but is never asked."""
        return self.state == "active" and self.plugin is not None


_records: dict[str, Record] = {}
# Set by the core (core.startup) – that way the loader does not import core
on_device_changed: Callable[[str, str, dict[str, Any]], None] | None = None
on_devices_changed: Callable[[], None] | None = None
on_device_gone: Callable[[str, str], None] | None = None
on_switch: Callable[[str, str, Mapping[str, Any]], Awaitable[Any]] | None = None
on_button_pressed: Callable[[str, str, str, str], None] | None = None   # (plugin, device id at the plugin, button, press)
# Set by the core: paths for plugins with permission in the manifest ("access") – devices of all plugins, switching,
# room light, scenes, home/away. Keys: devices, command, room_lights, scenes, house_scene, home_state, home.
access_hooks: dict[str, Callable[..., Awaitable[Any]]] = {}
_watchers: dict[str, list[Callable[[str, Mapping[str, Any]], None]]] = {}    # plugin id -> callbacks from context.on_change
_datagrams: dict[str, list[Any]] = {}             # Plugin-ID -> offene UDP-Sockets aus context.open_datagram
MAX_DATAGRAM_SOCKETS = 4                          # a plugin keeps at most this many UDP sockets open at once
mount: Callable[[Record], None] | None = None     # von main gesetzt: Routen eines Plugins einbinden
heating: Any = None                               # set by the core: heating routines for plugins (plugin_api.HeatingPlans)
ANY_ID = "*"                                      # prefix of a hub that lists devices of other systems under their ids


# ---------------------------------------------------------------- Manifest
def _str(v: Any, limit: int, what: str, required: bool = True) -> str:
    """Required or optional text from the manifest: not empty and at most `limit` characters, otherwise ManifestError
    with the name of the field."""
    if v is None and not required:
        return ""
    if not isinstance(v, str) or not v.strip() or len(v) > limit:
        raise ManifestError(f"„{what}“ fehlt oder ist ungültig.")
    return v.strip()


def read_manifest(path: Path) -> Manifest:
    """Read and check manifest.json (schema v1). Raises ManifestError with an understandable reason."""
    file = path / "manifest.json"
    try:
        if file.stat().st_size > MAX_MANIFEST_BYTES:
            raise ManifestError("manifest.json ist zu groß.")
        raw = json.loads(file.read_text(encoding="utf-8"))
    except OSError:
        raise ManifestError("manifest.json fehlt.")
    except ValueError:
        raise ManifestError("manifest.json ist kein gültiges JSON.")
    if not isinstance(raw, dict):
        raise ManifestError("manifest.json muss ein Objekt sein.")
    pid = _str(raw.get("id"), 31, "id")
    if not ID_RE.match(pid):
        raise ManifestError("„id“ darf nur Kleinbuchstaben, Ziffern und _ enthalten (2–31 Zeichen).")
    if pid in RESERVED_IDS:
        raise ManifestError(f"„id“ {pid} ist für ComfyOS selbst reserviert.")
    if pid != path.name:
        raise ManifestError(f"„id“ ({pid}) muss dem Ordnernamen ({path.name}) entsprechen.")
    version = _str(raw.get("version"), 20, "version")
    if not VERSION_RE.match(version):
        raise ManifestError("„version“ muss wie 1.0.0 aussehen.")
    api = raw.get("api")
    if not isinstance(api, int) or isinstance(api, bool):
        raise ManifestError("„api“ fehlt (ganze Zahl).")
    if api != API_VERSION:
        raise ManifestError(f"Plugin ist für Plugin-API {api} gebaut, ComfyOS bietet {API_VERSION}.")
    revision = raw.get("api_revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ManifestError("„api_revision“ muss eine ganze Zahl ab 1 sein.")
    if revision > API_REVISION:
        raise ManifestError(f"Plugin braucht Plugin-API {api} in Revision {revision}, ComfyOS bietet Revision {API_REVISION} – "
                            "bitte ComfyOS aktualisieren.")
    entry = _str(raw.get("entry"), 90, "entry")
    if not ENTRY_RE.match(entry):
        raise ManifestError("„entry“ muss wie __init__:setup aussehen.")

    def strings(key: str, limit: int, pattern: re.Pattern | None = None, count: int = 20) -> list[str]:
        v = raw.get(key, [])
        if not isinstance(v, list) or len(v) > count or not all(isinstance(x, str) and 0 < len(x) <= limit for x in v):
            raise ManifestError(f"„{key}“ muss eine Liste kurzer Texte sein.")
        if pattern and not all(pattern.match(x) for x in v):
            raise ManifestError(f"„{key}“ enthält einen ungültigen Eintrag.")
        return list(v)

    prefixes = strings("id_prefixes", 40, re.compile(r"^([a-z][a-z0-9_.-]*:|\*)$"))
    if not prefixes:
        raise ManifestError("„id_prefixes“ fehlt: mindestens ein Präfix wie \"meins:\".")
    egress_entries = []
    for e in raw.get("egress", []) if isinstance(raw.get("egress", []), list) else [None]:
        if not isinstance(e, dict) or not KEY_RE.match(str(e.get("key") or "")):
            raise ManifestError("„egress“: jeder Eintrag braucht einen „key“.")
        hosts = e.get("hosts", [])
        udp = e.get("udp", False)
        if not isinstance(udp, bool) or (udp and e.get("scope") != "lan"):
            raise ManifestError(f"„egress“ → {e['key']}: \"udp\": true gibt es nur mit \"scope\": \"lan\".")
        if e.get("scope") == "lan":
            hosts = []
        elif not (isinstance(hosts, list) and hosts and len(hosts) <= 10
                  and all(isinstance(h, str) and re.match(r"^(\*\.)?[a-z0-9.-]{3,80}$", h) for h in hosts)):
            raise ManifestError(f"„egress“ → {e['key']}: entweder \"scope\": \"lan\" oder feste „hosts“.")
        egress_entries.append({"key": e["key"], "desc": _str(e.get("desc"), 160, "egress.desc"), "scope": "lan" if not hosts else "internet",
                               "hosts": hosts, "udp": udp, "data": _str(e.get("data"), 200, "egress.data", required=False),
                               "trigger": _str(e.get("trigger"), 200, "egress.trigger", required=False)})
    settings = []
    for s in raw.get("settings", []) if isinstance(raw.get("settings", []), list) else [None]:
        if not isinstance(s, dict) or not KEY_RE.match(str(s.get("key") or "")) or s.get("widget") not in WIDGETS:
            raise ManifestError("„settings“: jeder Eintrag braucht „key“ und ein „widget“ (" + ", ".join(WIDGETS) + ").")
        entry_s = {"key": s["key"], "widget": s["widget"], "label": _str(s.get("label"), 80, "settings.label"),
                   "hint": _str(s.get("hint"), 240, "settings.hint", required=False)}
        if s["widget"] == "select":
            opts = s.get("options")
            if not (isinstance(opts, list) and 0 < len(opts) <= 30 and all(isinstance(o, list) and len(o) == 2 for o in opts)):
                raise ManifestError(f"„settings“ → {s['key']}: „options“ als Liste von [Wert, Text].")
            entry_s["options"] = [[str(o[0])[:80], str(o[1])[:80]] for o in opts]
        if s["widget"] == "number":
            for k in ("min", "max", "step"):
                if isinstance(s.get(k), (int, float)) and not isinstance(s.get(k), bool):
                    entry_s[k] = s[k]
        if "default" in s and isinstance(s["default"], (str, int, float, bool)):
            entry_s["default"] = s["default"]
        settings.append(entry_s)
    helper = raw.get("helper")
    if helper is not None and not isinstance(helper, dict):
        raise ManifestError("„helper“ muss ein Objekt mit „desc“ sein.")
    helper_desc = _str(helper.get("desc"), 200, "helper.desc") if helper is not None else ""
    access = strings("access", 20, count=len(ACCESS_KINDS))
    if any(a not in ACCESS_KINDS for a in access) or len(set(access)) != len(access):
        raise ManifestError("„access“ kennt nur: " + ", ".join(ACCESS_KINDS) + " (jedes höchstens einmal).")
    static = raw.get("static", "") or ""
    if not isinstance(static, str) or not re.match(r"^([a-z0-9_]{1,30}/)?$", static):
        raise ManifestError("„static“ muss ein Unterordner wie \"static/\" sein.")
    return Manifest(id=pid, name=_str(raw.get("name"), 60, "name"), version=version, api=api, entry=entry,
                    api_revision=revision,
                    description=_str(raw.get("description"), 300, "description", required=False), id_prefixes=prefixes,
                    egress=egress_entries, settings=settings, secrets=strings("secrets", 41, KEY_RE),
                    static=static, requires=strings("requires", 60, re.compile(r"^[A-Za-z0-9_.-]+$")),
                    helper=helper_desc, access=access)


# ---------------------------------------------------------------- Settings per plugin (settings.json → plugins)
def _config() -> dict[str, Any]:
    """Section `plugins` of the settings in safe form: disabled and approved plugins and the
    settings per plugin."""
    cfg = load_settings().get("plugins")
    cfg = cfg if isinstance(cfg, dict) else {}
    lst = lambda k: [x for x in cfg.get(k, []) if isinstance(x, str)] if isinstance(cfg.get(k), list) else []   # noqa: E731
    return {"disabled": lst("disabled"), "trusted": lst("trusted"),
            "settings": cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}}


def _save_config(cfg: dict[str, Any]) -> None:
    """Write the section `plugins` back to the settings."""
    data = load_settings()
    data["plugins"] = cfg
    save_settings(data)


class _Settings:
    """Settings of a plugin – only its own section, only simple values."""

    def __init__(self, plugin_id: str):
        """Settings of the plugin with this id."""
        self._id = plugin_id

    def _all(self) -> dict[str, Any]:
        """All stored settings of this plugin."""
        v = _config()["settings"].get(self._id)
        return v if isinstance(v, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        """Read a setting (`default` if it is missing)."""
        return self._all().get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store a setting. The key must match the pattern, the value must be simple and small –
        otherwise ValueError."""
        if not KEY_RE.match(str(key)):
            raise ValueError("Ungültiger Schlüssel.")
        if not isinstance(value, (str, int, float, bool, list, dict)) or len(json.dumps(value)) > MAX_SETTING_BYTES:
            raise ValueError("Wert zu groß oder nicht speicherbar.")
        cfg = _config()
        cfg["settings"].setdefault(self._id, {})[key] = value
        _save_config(cfg)

    def delete(self, key: str) -> None:
        """Delete a setting."""
        cfg = _config()
        if cfg["settings"].get(self._id, {}).pop(key, None) is not None:
            _save_config(cfg)

    def all(self) -> dict[str, Any]:
        """Copy of all settings of this plugin."""
        return dict(self._all())


def error_text(exc: BaseException) -> str:
    """Text for the user about an error from plugin code: the own text of a PluginError (or the `detail` of an
    HTTP error), otherwise only the kind of the exception – its content may contain addresses or credentials."""
    if isinstance(exc, HTTPException):
        return str(exc.detail)[:300]
    return str(exc)[:300] if isinstance(exc, PluginError) else type(exc).__name__


def secret_name(plugin_id: str, key: str) -> str:
    """Name under which a secret of a plugin lives in the secret store: `plugin:<id>:<key>`."""
    return f"plugin:{plugin_id}:{key}"


def is_secret_name(name: str) -> bool:
    """Does the name have the form `plugin:<id>:<key>`? That is how the migration package recognises what belongs to
    a plugin – all other names in the secret store (licence, push keys) belong to the core."""
    parts = name.split(":")
    return len(parts) == 3 and parts[0] == "plugin" and bool(ID_RE.match(parts[1])) and bool(KEY_RE.match(parts[2]))


class _Secrets:
    """Credentials of a plugin in the secret store; only keys named in the manifest."""

    def __init__(self, plugin_id: str, allowed: list[str]):
        """`allowed` are the keys the manifest names under "secrets" – others do not exist for the plugin."""
        self._id, self._allowed = plugin_id, set(allowed)

    def _name(self, key: str) -> str:
        """Name of the secret in the secret store. Raises PluginError for a key that is not in the
        manifest."""
        if key not in self._allowed:
            raise PluginError(f"Geheimnis „{key}“ steht nicht im Manifest unter „secrets“.")
        return secret_name(self._id, key)

    def get(self, key: str, default: Any = None) -> Any:
        """Read a secret (`default` if it is missing or the store is unreadable)."""
        try:
            return get_secret(self._name(key)) or default
        except PluginError:
            raise
        except Exception:
            return default

    def set(self, key: str, value: Any) -> None:
        """Geheimnis speichern."""
        set_secret(self._name(key), str(value))

    def delete(self, key: str) -> None:
        """Delete a secret."""
        delete_secret(self._name(key))

    def has(self, key: str) -> bool:
        """Ist das Geheimnis gesetzt?"""
        return bool(self.get(key))


class _Context:
    """What a plugin may use of the core (plugin_api.PluginContext)."""

    def __init__(self, rec: Record):
        """Context for exactly one plugin: own settings, own secrets, own log."""
        m = rec.manifest
        self._rec = rec
        self.plugin_id = rec.id
        self.api_version = API_VERSION
        self.api_revision = API_REVISION
        self.log = logging.getLogger(f"comfyos.plugin.{rec.id}")
        self.settings = _Settings(rec.id)
        self.secrets = _Secrets(rec.id, m.secrets if m else [])

    @property
    def heating(self) -> Any:
        """Access to the heating routines (plugin_api.HeatingPlans). Raises PluginError as long as the core has not
        provided them yet."""
        if heating is None:
            raise PluginError("Heizroutinen stehen gerade nicht bereit.")
        return heating

    @property
    def data_dir(self) -> Path:
        """The plugin's own data folder (created when needed)."""
        path = PLUGIN_DATA_DIR / self.plugin_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def http(self, egress_key: str, **client_options: Any) -> Any:
        """HTTP client for a target from the manifest. Into the home network it only reaches home network addresses, into
        the internet only the hosts named there and always with certificate verification."""
        entry = self._egress_entry(egress_key)
        if entry["scope"] == "lan":
            return egress.lan_client(**client_options)
        client_options.pop("verify", None)      # into the internet always with certificate verification
        return egress.client(egress_id(self.plugin_id, egress_key), **client_options)

    def _egress_entry(self, egress_key: str) -> dict[str, Any]:
        """Entry of the manifest for a target. Raises PluginError if the plugin has not declared the target."""
        entry = next((e for e in (self._rec.manifest.egress if self._rec.manifest else []) if e["key"] == egress_key), None)
        if entry is None:
            raise PluginError(f"Ziel „{egress_key}“ steht nicht im Manifest unter „egress“.")
        return entry

    async def open_stream(self, egress_key: str, host: str, port: int, *, tls: bool = False, verify: bool = True,
                          timeout: float = 5.0) -> tuple[Any, Any]:
        """Open a TCP connection to a target from the manifest (e.g. MQTT), with TLS on request. Errors come back as
        errors of the plugin API: not allowed, port closed, unreachable."""
        entry = self._egress_entry(egress_key)
        registered = None if entry["scope"] == "lan" else egress.REGISTRY.get(egress_id(self.plugin_id, egress_key))
        if entry["scope"] != "lan" and registered is None:
            raise PluginError(f"Ziel „{egress_key}“ ist nicht freigegeben.")
        try:
            return await egress.open_stream(host, port, registered, tls=bool(tls), verify=bool(verify), timeout=timeout)
        except egress.StreamBlocked as exc:
            raise PluginError(str(exc))
        except ConnectionRefusedError:
            raise plugin_api.ConnectionRefused(f"{host}: Port {port} ist geschlossen.")
        except (OSError, asyncio.TimeoutError) as exc:      # TLS errors too (ssl.SSLError is an OSError)
            raise plugin_api.DeviceUnreachable(f"{host} antwortet nicht ({type(exc).__name__}).")

    def _udp_entry(self, egress_key: str) -> None:
        """Check whether the manifest declares UDP in the home network for this target. Raises PluginError if not or if
        the plugin is not enabled."""
        entry = self._egress_entry(egress_key)
        if entry["scope"] != "lan" or not entry.get("udp"):
            raise PluginError(f"Ziel „{egress_key}“ erlaubt kein UDP (Manifest: \"scope\": \"lan\" und \"udp\": true).")
        if not self._rec.active:
            raise PluginError("Das Plugin ist nicht eingeschaltet.")

    async def open_datagram(self, egress_key: str, port: int = 0, *, multicast: str | None = None) -> Any:
        """Open a UDP socket in the home network (egress.open_datagram) – only for a LAN target with `"udp": true` in the
        manifest. The socket closes at the latest when the plugin stops."""
        self._udp_entry(egress_key)
        mine = _datagrams.setdefault(self.plugin_id, [])
        mine[:] = [sock for sock in mine if not sock.closed]
        if len(mine) >= MAX_DATAGRAM_SOCKETS:
            raise PluginError("Zu viele offene UDP-Sockets.")
        try:
            channel = await egress.open_datagram(port, multicast)
        except egress.DatagramBlocked as exc:
            raise PluginError(str(exc))
        except OSError as exc:
            raise PluginError(f"UDP-Port {port} lässt sich nicht öffnen ({type(exc).__name__}).")
        sock = _Datagram(channel)
        mine.append(sock)
        return sock

    async def discover(self, egress_key: str, protocol: str, search_target: str, timeout: float = 3.0) -> list[SsdpAnswer]:
        """Search for devices in the home network (only SSDP so far) – only for a LAN target with `"udp": true` in the
        manifest. egress discards answers from outside the home network."""
        self._udp_entry(egress_key)
        if protocol != "ssdp":
            raise PluginError("Gesucht wird bisher nur per SSDP (protocol=\"ssdp\").")
        try:
            answers = await egress.ssdp_search(search_target, timeout)
        except egress.DatagramBlocked as exc:
            raise PluginError(str(exc))
        except OSError as exc:
            raise PluginError(f"Die Suche im Heimnetz ist gescheitert ({type(exc).__name__}).")
        return [SsdpAnswer(host=a["host"], location=a["location"], usn=a["usn"], search_target=a["search_target"],
                           server=a["server"], headers=dict(a["headers"])) for a in answers]

    def is_local_host(self, host: str) -> bool:
        """Does the host point into the home network? A plugin uses this to check addresses the user enters."""
        return egress.is_local_host(host)

    def device_changed(self, device_id: str, changes: Mapping[str, Any]) -> None:
        """A device reports new values on its own: at once to the open pages and the flows. An error in that
        stays with the plugin in the log."""
        if on_device_changed and self._rec.active:
            try:
                on_device_changed(self.plugin_id, str(device_id), plain(dict(changes), self.plugin_id))
            except Exception as exc:
                self.log.warning("device_changed: %s", type(exc).__name__)

    def button_pressed(self, device_id: str, button: str, press: str = plugin_api.PRESS_SHORT) -> None:
        """A button of a device was pressed (since API revision 7): at once to the flows and the open pages. An unknown
        press kind or button id is dropped with a note in the log – a press must never break the plugin."""
        button = str(button)
        if press not in plugin_api.PRESS_KINDS or not BUTTON_ID_RE.match(button):
            self.log.warning("button_pressed: Taste oder Art des Drucks ungültig")
            return
        if on_button_pressed and self._rec.active:
            try:
                on_button_pressed(self.plugin_id, str(device_id)[:device_store.MAX_ID], button, press)
            except Exception as exc:
                self.log.warning("button_pressed: %s", type(exc).__name__)

    def devices_changed(self) -> None:
        """The device list of the plugin has changed: open pages reload."""
        if on_devices_changed:
            on_devices_changed()

    def device_gone(self, device_id: str) -> None:
        """A device no longer exists at the plugin."""
        if on_device_gone and self._rec.active:
            on_device_gone(self.plugin_id, str(device_id)[:device_store.MAX_ID])

    def event(self, message: str) -> None:
        """Write a line to the event log (shortened), with the plugin as its kind."""
        events.log(self.plugin_id, str(message)[:300])

    def hints(self, kind: str) -> list[dict[str, Any]]:
        """Hints of the other running plugins for a kind (e.g. addresses found). A plugin that fails in that
        is skipped."""
        out = []
        for rec in _records.values():
            if rec.id == self.plugin_id or not rec.active:
                continue
            try:
                out += [h for h in rec.plugin.hints() if isinstance(h, dict) and h.get("kind") == kind]
            except Exception:
                continue   # a plugin that fails here is skipped
        return out

    async def switch(self, device_id: str, state: Mapping[str, Any]) -> Any:
        """Switch a device through the core – so lock and event log also apply when a plugin switches."""
        if on_switch is None:
            raise PluginError("Schalten ist gerade nicht möglich.")
        return await on_switch(self.plugin_id, str(device_id), state)

    # -- Helper program (manifest "helper") --------------------------------------
    def start_helper(self, program: str | Path, args: Any = (), *, env: Mapping[str, str] | None = None) -> Any:
        """Start the helper program of the plugin (helpers.py). Only with "helper" in the manifest and only for an enabled
        plugin; the program must be in the plugin's folder or in its data folder."""
        manifest = self._rec.manifest
        if manifest is None or not manifest.helper:
            raise PluginError("Das Manifest erklärt kein Beiprogramm („helper“).")
        if not self._rec.active or self._rec.path is None:
            raise PluginError("Das Plugin ist nicht eingeschaltet.")
        data_dir = self.data_dir
        try:
            return helpers.start(self.plugin_id, [self._rec.path, data_dir], data_dir, program, args, env)
        except helpers.HelperError as exc:
            raise PluginError(str(exc))

    # -- Access to everything ComfyOS controls (manifest "access") ----------------
    def _hook(self, permission: str, name: str) -> Callable[..., Awaitable[Any]]:
        """Path of the core for a call that needs a permission. Raises PluginError without the permission in the manifest,
        for a disabled plugin or as long as the core has not provided the path yet."""
        manifest = self._rec.manifest
        if manifest is None or permission not in manifest.access:
            raise PluginError(f"Das Manifest erlaubt das nicht („access“: {permission}).")
        hook = access_hooks.get(name)
        if hook is None or not self._rec.active:
            raise PluginError("Das ist gerade nicht möglich.")
        return hook

    async def all_devices(self) -> list[DeviceView]:
        """All devices of ComfyOS as a view for the plugin (permission "devices"): name, room, kind, state,
        capabilities – no details of the plugin behind them."""
        return [device_view(row) for row in await _as_plugin_error(self._hook(ACCESS_DEVICES, "devices")())]

    def on_change(self, callback: Callable[[str, Mapping[str, Any]], None]) -> None:
        """Register a callback that learns of every state change of a device (permission "devices"). As long as a plugin
        listens, the core keeps comparing the device state – even without an open page."""
        self._hook(ACCESS_DEVICES, "devices")
        if not callable(callback):
            raise PluginError("on_change braucht eine Funktion.")
        _watchers.setdefault(self.plugin_id, []).append(callback)

    async def command(self, device_id: str, state: Mapping[str, Any]) -> Any:
        """Switch any device through the core (permission "devices") – with device lock and event log. Errors
        come back as errors of the plugin API: locked, unreachable, not possible."""
        hook = self._hook(ACCESS_DEVICES, "command")
        return await _as_plugin_error(hook(self.plugin_id, str(device_id)[:device_store.MAX_ID], plain(dict(state))))

    async def room_lights(self, room_id: str | None, on: bool, brightness: float | None = None) -> None:
        """Switch or dim all lamps of a room (or with None: of the home) (permission "rooms")."""
        hook = self._hook(ACCESS_ROOMS, "room_lights")
        level = None if brightness is None else max(1.0, min(100.0, float(brightness)))
        await _as_plugin_error(hook(self.plugin_id, None if room_id is None else str(room_id)[:120], bool(on), level))

    async def scenes(self) -> list[SceneView]:
        """Light scenes of ComfyOS (permission "scenes")."""
        return [SceneView(id=str(s["id"]), name=str(s["name"]), rooms=tuple(str(r) for r in s["rooms"]), active=bool(s["active"]))
                for s in await self._hook(ACCESS_SCENES, "scenes")()]

    async def house_scene(self, scene_id: str, active: bool = True) -> None:
        """Start or end a house scene (permission "scenes")."""
        await _as_plugin_error(self._hook(ACCESS_SCENES, "house_scene")(self.plugin_id, str(scene_id)[:40], bool(active)))

    async def home_state(self) -> str:
        """"PRESENT" or "AWAY" (permission "home")."""
        return str(await self._hook(ACCESS_HOME, "home_state")())

    async def home(self, state: str) -> None:
        """Set at home/away (permission "home")."""
        await _as_plugin_error(self._hook(ACCESS_HOME, "home")(self.plugin_id, str(state)[:20]))

    def live_audio_url(self, host: str) -> str:
        """Address under which a device in the home network fetches the sound a browser sends right now."""
        host = str(host).strip("[]")
        if not egress.is_local_ip(host):
            raise PluginError("Der Ton vom Browser geht nur an Geräte im Heimnetz.")
        url = live_audio.url_for(host)
        if url is None:
            raise PluginError("Gerade wird kein Ton vom Browser übertragen.")
        return url


class _Datagram:
    """UDP socket as a plugin sees it (plugin_api.Datagram): rejections by egress arrive as PluginError."""

    def __init__(self, channel: Any):
        """`channel` is the checked socket from egress.open_datagram."""
        self._channel = channel

    @property
    def closed(self) -> bool:
        """Ist der Socket geschlossen?"""
        return bool(self._channel.closed)

    def send(self, data: bytes, address: tuple[str, int]) -> None:
        """Send a datagram into the home network. Raises PluginError if egress rejects it."""
        try:
            self._channel.send(data, address)
        except egress.DatagramBlocked as exc:
            raise PluginError(str(exc))

    def on_datagram(self, callback: Callable[[bytes, tuple[str, int]], None]) -> None:
        """Register a callback for incoming datagrams."""
        if not callable(callback):
            raise PluginError("on_datagram braucht eine Funktion.")
        self._channel.on_datagram(callback)

    def close(self) -> None:
        """Close the socket."""
        self._channel.close()


def _close_datagrams(plugin_id: str) -> None:
    """Close all UDP sockets of a plugin – they belong to one run of the plugin."""
    for sock in _datagrams.pop(plugin_id, []):
        sock.close()


async def _as_plugin_error(call: Awaitable[Any]) -> Any:
    """Run a call into the core and translate its HTTP errors into errors of the plugin API: 423 = locked,
    502 = unreachable, everything else = not possible (with the text the interface would show too)."""
    try:
        return await call
    except device_store.DeviceLocked as exc:
        raise plugin_api.DeviceLocked(str(exc.detail))
    except HTTPException as exc:
        if exc.status_code == 502:
            raise plugin_api.DeviceUnreachable(str(exc.detail)[:300])
        raise PluginError(str(exc.detail)[:300])


def light_state(light: Any) -> LightState | None:
    """Light state of a device row (form of `device_merge.light_dict`) back into the form of the plugin API."""
    if not isinstance(light, dict):
        return None
    caps = light.get("caps") if isinstance(light.get("caps"), dict) else {}
    white = caps.get("ct")
    white_range = (int(white[0]), int(white[1])) if isinstance(white, (list, tuple)) and len(white) == 2 else None
    return LightState(
        capabilities=LightCapabilities(white_range=white_range, color=bool(caps.get("color")),
                                       gradient_points=int(caps.get("gradient") or 0),
                                       gradient_modes=list(caps.get("gradient_modes") or []),
                                       effects=list(caps.get("effects") or []), effect_parameters=bool(caps.get("effect_params"))),
        mode=light.get("mode"), xy=list(light["xy"]) if light.get("xy") else None, mirek=light.get("mirek"),
        gradient=[list(p) for p in light.get("points") or []], gradient_mode=light.get("gmode"), effect=light.get("effect"))


def media_state(media: Any) -> MediaState | None:
    """Playback of a device row (form of `device_merge.media_dict`) back into the form of the plugin API. The
    address of the cover picture stays with the core – a foreign plugin loads no pictures."""
    if not isinstance(media, dict):
        return None
    return MediaState(title=media.get("title"), artist=media.get("artist"), album=media.get("album"),
                      source=media.get("source"), position=media.get("position"), duration=media.get("duration"),
                      group=list(media.get("group") or []), coordinator=media.get("coordinator"),
                      favorites=list(media.get("favorites") or []))


def device_view(row: Mapping[str, Any]) -> DeviceView:
    """View of a plugin on a device – from the selection the core puts together for it (`core.view_row`)."""
    caps = [Capability(key=str(c["key"]), type=str(c.get("type") or "bool"), unit=c.get("unit"), minimum=c.get("minimum"),
                       maximum=c.get("maximum"), step=c.get("step"), options=tuple(c.get("options") or ()),
                       readable=bool(c.get("readable", True)), writable=bool(c.get("writable")), label=c.get("label"))
            for c in row.get("caps") or [] if isinstance(c, dict) and c.get("key")]
    return DeviceView(id=str(row["id"]), name=str(row.get("name") or ""), kind=str(row.get("kind") or "unknown"),
                      plugin=str(row.get("plugin") or ""), room_id=row.get("room_id"), room=row.get("room"),
                      online=bool(row.get("online")), locked=bool(row.get("locked")), controllable=bool(row.get("controllable")),
                      state=dict(row.get("state") or {}), capabilities=caps, light=light_state(row.get("light")),
                      media=media_state(row.get("media")))


def watching() -> bool:
    """Is an enabled plugin listening for device changes right now (`context.on_change`)? Then the core keeps
    comparing the device state even without an open page."""
    for plugin_id, callbacks in _watchers.items():
        rec = _records.get(plugin_id)
        if callbacks and rec is not None and rec.active:
            return True
    return False


def notify(message: Mapping[str, Any]) -> None:
    """Pass a live message of the core on to the plugins that listen with `context.on_change`: changes of a device
    with its id, everything else that touches their view (device list, scenes, home state) as "read again" (empty id).
    An error in a callback stays with the plugin."""
    if not _watchers:
        return
    kind = message.get("type")
    if kind == "device" and message.get("id"):
        device_id = str(message["id"])
        changes = {k: (light_state(v) if k == "light" else media_state(v) if k == "media" else v)
                   for k, v in message.items() if k not in ("type", "id")}
    elif kind in ("reload", "scenes", "home"):
        device_id, changes = "", {}
    else:
        return
    for plugin_id, callbacks in list(_watchers.items()):
        rec = _records.get(plugin_id)
        if rec is None or not rec.active:
            continue
        for callback in list(callbacks):
            try:
                callback(device_id, dict(changes))
            except Exception as exc:
                log.warning("Plugin %s: on_change (%s)", plugin_id, type(exc).__name__)


def egress_id(plugin_id: str, key: str) -> str:
    """Id of a plugin target in the egress register: `plugin.<id>.<key>`."""
    return f"plugin.{plugin_id}.{key}"


# ---------------------------------------------------------------- Finding and loading
def _scan(folder: Path, origin: str, cfg: dict[str, Any]) -> list[Record]:
    """Scan a plugin folder: one plugin per subfolder, its manifest is checked. Ids that are already known
    stay as they are – a user plugin cannot replace a built-in one. Returns the newly found
    ones."""
    try:
        entries = sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return []
    found = []
    for path in entries:
        if path.name.startswith(("_", ".")) or path.name == "__pycache__":
            continue
        known = _records.get(path.name)
        if known is not None:           # already known; built-in wins – a user plugin cannot replace one
            if known.path != path:
                log.warning("Plugin %s (%s) übersprungen: die ID gibt es schon", path.name, origin)
            continue
        rec = Record(id=path.name, name=path.name, origin=origin, path=path)
        try:
            rec.manifest = read_manifest(path)
            rec.name = rec.manifest.name
            if origin == "user" and rec.id not in cfg["trusted"]:
                rec.state = "new"
            else:
                rec.state = "off" if rec.id in cfg["disabled"] else "active"
        except ManifestError as exc:
            rec.state, rec.error = "error", str(exc)
        _records[rec.id] = rec
        found.append(rec)
    return found


def discover() -> list[Record]:
    """Scan the plugin folders and check the manifests – does not import any plugin code yet.
    Known plugins stay as they are; only the newly found ones come back (at run time too: "Neu suchen")."""
    cfg = _config()
    return _scan(BUILTIN_DIR, "builtin", cfg) + _scan(USER_DIR, "user", cfg)


def _import(rec: Record) -> Callable[[Any], Plugin]:
    """Load the code of a plugin as a package of its own and return its entry function. Raises PluginError if
    Python packages, `__init__.py` or the entry point are missing."""
    m = rec.manifest
    missing = [r for r in m.requires if importlib.util.find_spec(r.replace("-", "_")) is None]
    if missing:
        raise PluginError("Python-Pakete fehlen: " + ", ".join(missing))
    package = f"comfyos_plugin_{rec.id}"
    module_name, func = m.entry.split(":")
    for name in [n for n in sys.modules if n == package or n.startswith(package + ".")]:
        del sys.modules[name]       # load fresh after a failed attempt
    spec = importlib.util.spec_from_file_location(package, rec.path / "__init__.py", submodule_search_locations=[str(rec.path)])
    if spec is None or spec.loader is None:
        raise PluginError("__init__.py fehlt.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package] = module
    spec.loader.exec_module(module)
    if module_name != "__init__":
        module = importlib.import_module(f"{package}.{module_name}")
    setup = getattr(module, func, None)
    if not callable(setup):
        raise PluginError(f"Einstieg {m.entry} nicht gefunden.")
    return setup


def _fail(rec: Record, where: str, exc: BaseException) -> None:
    """Put a plugin into the state "error". The user only sees an own text or the kind of the error –
    never the text of a foreign exception."""
    rec.state = "error"
    rec.error = str(exc)[:300] if isinstance(exc, (PluginError, ManifestError)) else f"{where}: {type(exc).__name__}"
    rec.started = False
    if not rec.mounted:     # mounted routes belong to exactly this instance – it stays for the next attempt
        rec.plugin = None
    log.warning("Plugin %s: %s (%s)", rec.id, where, type(exc).__name__, exc_info=not isinstance(exc, PluginError))
    events.log("system", f"Plugin „{rec.name}“ läuft nicht: {rec.error}")


def _egress_title(desc: str) -> tuple[str, str]:
    """Split the description of an egress target into a short title and its purpose (tile "Datenschutz"):
    "Magenta-Sync: Heizroutinen abgleichen" -> ("Magenta-Sync", "Heizroutinen abgleichen"),
    "Node.js laden (Laufzeit des Beiprogramms)" -> ("Node.js laden", "Laufzeit des Beiprogramms"). Without either
    mark the whole description is both."""
    head, sep, rest = desc.partition(": ")
    if sep and head and rest:
        return head, rest
    head, sep, rest = desc.partition(" (")
    if sep and head and rest.endswith(")"):
        return head, rest[:-1]
    return desc, desc


def _register_egress(rec: Record) -> None:
    """Internet-Ziele eines Plugins ins Egress-Register eintragen (Heimnetz-Ziele brauchen keinen Eintrag)."""
    for e in rec.manifest.egress:
        if e["scope"] == "internet":
            title, purpose = _egress_title(e["desc"])
            short_name = rec.name.split(" (")[0]     # "Matter (Alexa, Apple Home, Google Home)" -> "Matter"
            egress.register(egress.Egress(egress_id(rec.id, e["key"]), f"{short_name}: {title}", tuple(e["hosts"]),
                                          purpose, e["data"] or "was das Plugin für diese Funktion braucht",
                                          e["trigger"] or f"nur wenn das Plugin „{rec.name}“ eingeschaltet ist"))


def load(rec: Record) -> None:
    """Import the plugin code, call `setup(context)`, fetch the routes. Errors stay with the plugin."""
    if rec.state != "active" or rec.plugin is not None:
        return
    try:
        setup = _import(rec)
        plugin = setup(_Context(rec))
        if not isinstance(plugin, Plugin):
            raise PluginError("setup() muss ein plugin_api.Plugin zurückgeben.")
        rec.plugin, rec.error = plugin, ""
        _register_egress(rec)
        router = plugin.routes()
        if router is not None and not isinstance(router, APIRouter):
            raise PluginError("routes() muss einen fastapi.APIRouter zurückgeben.")
        rec.router = router
        if router is not None and mount is not None and not rec.mounted:
            mount(rec)
            rec.mounted = True
    except Exception as exc:
        _fail(rec, "Laden", exc)


def load_all() -> None:
    """Alle eingeschalteten Plugins laden."""
    for rec in list(_records.values()):
        load(rec)


async def _start(rec: Record) -> None:
    """Start a plugin (`start()`) if it is enabled and not running yet. An error puts it into "error"."""
    if not rec.active or rec.started:
        return
    try:
        await rec.plugin.start()
        rec.started = True
        rec.started_at = time.monotonic()
    except Exception as exc:
        _fail(rec, "Start", exc)
        _watchers.pop(rec.id, None)     # whatever the failed start already registered or started ends with it
        _close_datagrams(rec.id)
        await helpers.stop(rec.id)


async def _stop(rec: Record) -> None:
    """Stop a plugin (`stop()`). An error in that is only logged."""
    if rec.plugin is None or not rec.started:
        return
    rec.started = False
    try:
        await rec.plugin.stop()
    except Exception as exc:
        log.warning("Plugin %s: Stop (%s)", rec.id, type(exc).__name__)
    _watchers.pop(rec.id, None)         # callbacks, UDP sockets and helper program belong to this run of the plugin
    _close_datagrams(rec.id)
    await helpers.stop(rec.id)


async def restart(rec: Record) -> None:
    """Restart a running plugin (after its settings changed)."""
    if rec.active:
        await _stop(rec)
        await _start(rec)


async def start_all() -> None:
    """Alle geladenen Plugins starten."""
    for rec in list(_records.values()):
        await _start(rec)


async def stop_all() -> None:
    """Alle laufenden Plugins anhalten."""
    for rec in list(_records.values()):
        await _stop(rec)


async def set_enabled(plugin_id: str, enabled: bool) -> Record:
    """Enable or disable a plugin (at run time). A user plugin counts as approved once it is enabled."""
    rec = get(plugin_id)
    cfg = _config()
    cfg["disabled"] = [x for x in cfg["disabled"] if x != rec.id] + ([] if enabled else [rec.id])
    if rec.origin == "user" and enabled and rec.id not in cfg["trusted"]:
        cfg["trusted"].append(rec.id)
    _save_config(cfg)
    if not enabled:
        await _stop(rec)        # the instance stays (its routes are mounted), it only rests
        if rec.manifest is not None:
            rec.state, rec.error = "off", ""
    elif rec.manifest is None:
        raise HTTPException(400, f"Das Plugin lässt sich nicht einschalten: {rec.error}")
    else:
        rec.state, rec.error = "active", ""
        load(rec)
        await _start(rec)
    if on_devices_changed:
        on_devices_changed()
    return rec


# ---------------------------------------------------------------- Zugriff
_demo_records: dict[str, Record] | None = None      # the one plugin a session in the demo sees (demo_world.py)


def _registry() -> dict[str, Record]:
    """The plugins of the running request: the real ones – or, for a session in the demo (demo.py), only the plugin
    "Demo-Wohnung". A request in the demo can therefore never reach, list or switch a real plugin."""
    global _demo_records
    if not demo.active():
        return _records
    if _demo_records is None:
        from . import demo_world
        rec = Record(id=demo_world.PLUGIN_ID, name=demo_world.NAME, origin="builtin", state="active", started=True)
        rec.manifest = Manifest(id=demo_world.PLUGIN_ID, name=demo_world.NAME, version="1.0.0", api=API_VERSION,
                                entry="__init__:setup", description=demo_world.DESCRIPTION, api_revision=API_REVISION,
                                id_prefixes=[demo_world.ID_PREFIX])
        rec.plugin = demo_world.DemoPlugin(None)
        _demo_records = {rec.id: rec}
    return _demo_records


def get(plugin_id: str) -> Record:
    """Plugin by its id, or 404."""
    rec = _registry().get(plugin_id)
    if rec is None:
        raise HTTPException(404, "Plugin nicht gefunden.")
    return rec


def records() -> list[Record]:
    """Alle bekannten Plugins."""
    return list(_registry().values())


def active(plugin_id: str) -> Plugin | None:
    """The instance of an enabled, loaded plugin, or None."""
    rec = _registry().get(plugin_id)
    return rec.plugin if rec is not None and rec.active else None


def require_active(plugin_id: str):
    """FastAPI dependency for plugin routes: disabled or faulty plugins answer with 404."""
    def check() -> None:
        if active(plugin_id) is None:
            raise HTTPException(404, "Dieses Plugin ist nicht eingeschaltet.")
    return Depends(check)


def static_file(plugin_id: str, rel: str) -> tuple[Path, str] | None:
    """File from the static folder of an enabled plugin (known types only, never outside the folder)."""
    rec = _registry().get(plugin_id)
    if rec is None or not rec.active or rec.manifest is None or not rec.manifest.static or rec.path is None:
        return None
    base = (rec.path / rec.manifest.static).resolve()
    if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,120}$", rel) or ".." in rel:
        return None
    file = (base / rel).resolve()
    mime = STATIC_TYPES.get(file.suffix.lower())
    if mime is None or base not in file.parents or not file.is_file():
        return None
    return file, mime


def public(rec: Record) -> dict[str, Any]:
    """What the interface shows about a plugin (no credentials)."""
    m = rec.manifest
    status: dict[str, Any] = {}
    if rec.active:
        try:
            status = dict(rec.plugin.status() or {})
        except Exception as exc:
            status = {"configured": False, "connected": False, "message": f"Status nicht lesbar ({type(exc).__name__})"}
    ctx_settings = _Settings(rec.id).all() if m else {}
    secrets = _Secrets(rec.id, m.secrets) if m else None
    return {"id": rec.id, "name": rec.name, "origin": rec.origin, "state": rec.state, "error": rec.error,
            "description": m.description if m else "", "version": m.version if m else None,
            "settings": m.settings if m else [],
            "values": {s["key"]: (bool(secrets.has(s["key"])) if s["widget"] == "password" else ctx_settings.get(s["key"], s.get("default")))
                       for s in (m.settings if m else [])},
            "egress": [{"key": e["key"], "desc": e["desc"], "scope": e["scope"], "hosts": e["hosts"], "udp": bool(e.get("udp"))}
                       for e in (m.egress if m else [])],
            "helper": (m.helper or None) if m else None,
            "access": [{"key": a, "text": ACCESS_TEXT[a]} for a in (m.access if m else [])],
            "script": bool(m and m.static and rec.active and static_file(rec.id, "settings.js")),
            "rooms": bool(rec.active and getattr(rec.plugin, "manages_rooms", False)),
            "status": {k: status.get(k) for k in ("configured", "connected", "live", "message", "name") if k in status}}


def save_values(rec: Record, values: dict[str, Any]) -> None:
    """Save settings from the generic tile – only keys from the manifest, passwords as a secret."""
    m = rec.manifest
    if m is None:
        raise HTTPException(400, "Dieses Plugin hat keine Einstellungen.")
    known = {s["key"]: s for s in m.settings}
    settings, secrets = _Settings(rec.id), _Secrets(rec.id, m.secrets)
    for key, value in values.items():
        s = known.get(key)
        if s is None:
            raise HTTPException(400, f"Unbekannte Einstellung: {str(key)[:40]}")
        if s["widget"] == "password":
            if key not in m.secrets:
                raise HTTPException(400, f"„{key}“ ist ein Passwortfeld, steht aber nicht unter „secrets“.")
            if value:       # empty = leave unchanged
                secrets.set(key, str(value)[:400])
            continue
        if s["widget"] == "toggle":
            value = bool(value)
        elif s["widget"] == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
                raise HTTPException(400, f"„{s['label']}“ muss eine Zahl sein.")
            value = max(s.get("min", value), min(s.get("max", value), value))
        elif s["widget"] == "select":
            if value not in [o[0] for o in s["options"]]:
                raise HTTPException(400, f"„{s['label']}“: ungültige Auswahl.")
        else:
            value = str(value)[:400]
        settings.set(key, value)


# ---------------------------------------------------------------- Devices and commands
def _checked(rec: Record, devices: Any) -> list[Device]:
    """Check the device list of a plugin: only Device, id with a declared prefix, known kind."""
    out, prefixes = [], tuple(rec.manifest.id_prefixes) if rec.manifest else ()
    any_id = ANY_ID in prefixes
    for d in list(devices or [])[:MAX_DEVICES]:
        if not isinstance(d, Device) or not isinstance(d.id, str) or not d.id or len(d.id) > device_store.MAX_ID:
            continue
        if prefixes and not any_id and not d.id.startswith(prefixes):
            log.warning("Plugin %s: Gerät %r ohne erklärtes ID-Präfix übersprungen", rec.id, d.id[:40])
            continue
        if d.kind not in DEVICE_KINDS:
            d.kind = "unknown"
        out.append(d)
    return out


@dataclass
class _Fetch:
    """A running device query of a plugin and how old its result may be at most."""
    task: asyncio.Task
    max_age: float | None


_fetching: dict[str, _Fetch] = {}     # plugin id -> running (or last) query


def _fresh_enough(running: float | None, wanted: float | None) -> bool:
    """Is a running query good enough for a further caller? `None` means "as fresh as the plugin considers right"
    – that satisfies everyone who asks for nothing themselves; whoever names a limit needs a query with at most
    this limit."""
    return wanted is None or (running is not None and running <= wanted)


async def _devices(rec: Record, max_age: float | None = None) -> Source:
    """Fetch the devices of a plugin – at most one query per plugin runs at a time.

    If a second caller arrives while one is running, it gets that result if it is fresh enough for it; otherwise
    it waits and then asks itself. That way `Plugin.max_device_age` holds exactly for the query that set it. Up to
    0.19, concurrent callers (live comparison, Flow, interface) overwrote each other's value.
    """
    loop = asyncio.get_running_loop()
    while True:
        current = _fetching.get(rec.id)
        if current is None or current.task.done() or current.task.get_loop() is not loop:
            break
        if _fresh_enough(current.max_age, max_age):
            return await asyncio.shield(current.task)
        await asyncio.wait([current.task])
    task = loop.create_task(_fetch(rec, max_age))
    _fetching[rec.id] = _Fetch(task, max_age)
    return await asyncio.shield(task)


async def _fetch(rec: Record, max_age: float | None) -> Source:
    """One query at one plugin. Never raises: if it fails, the source has no devices for this round (`None`)."""
    name, prefixes = rec.name, []
    try:
        rec.plugin.max_device_age = max_age       # how old a state from the cache may be
        devices = _checked(rec, await asyncio.wait_for(rec.plugin.devices(), DEVICES_TIMEOUT))
        prefixes = [str(p) for p in rec.plugin.link_prefixes()]
        rec.error = ""
    except PluginError as exc:
        devices, rec.error = None, str(exc)[:300]
    except Exception as exc:
        devices, rec.error = None, f"Geräte nicht lesbar ({type(exc).__name__})"
        log.warning("Plugin %s: devices() (%s)", rec.id, type(exc).__name__)
    return Source(rec.id, name, devices, prefixes)


async def collect(options: dict[str, Any] | None = None) -> list[Source]:
    """Fetch the devices of all running plugins (concurrently). A source with an error returns `devices=None`.
    `options["max_age"]`: how old a state from a plugin's cache may be at most (0 = fresh)."""
    recs = [r for r in _registry().values() if r.active]
    age = (options or {}).get("max_age")
    return list(await asyncio.gather(*(_devices(r, age) for r in recs)))


def unsettled_prefixes(sources: list[Source]) -> tuple[str, ...]:
    """Id prefixes of the plugins whose device list proves nothing right now: disabled, error, unreachable or
    not configured (yet). What the device store knows about such devices must not be cleaned up."""
    delivered = {s.plugin_id for s in sources if s.devices is not None}
    out: list[str] = []
    for rec in _registry().values():
        if rec.manifest is None:
            continue
        settled = rec.active and rec.id in delivered
        if settled:
            try:
                settled = (rec.plugin.status() or {}).get("configured") is not False
            except Exception:
                settled = False
        if not settled:
            out += ["" if p == ANY_ID else p for p in rec.manifest.id_prefixes]    # "" protects every id (hub)
    return tuple(out)


def owner_by_id(device_id: str) -> str | None:
    """Plugin a device id belongs to by its prefix (an exact prefix before the wildcard of a hub)."""
    hub = None
    for rec in _registry().values():
        if rec.manifest is None:
            continue
        for p in rec.manifest.id_prefixes:
            if p == ANY_ID:
                hub = hub or rec.id
            elif device_id.startswith(p):
                return rec.id
    return hub


def having(flag: str) -> list[tuple[str, Plugin]]:
    """Running plugins with a role (`manages_rooms`, `syncs_heating`), in the order they were registered."""
    return [(rec.id, rec.plugin) for rec in _registry().values() if rec.active and getattr(rec.plugin, flag, False) is True]


async def command(row: dict[str, Any], state: Mapping[str, Any], source: str) -> dict[str, Any] | None:
    """Send a command to the plugin that controls the device. Checks the device lock first.
    Raises HTTPException (400 = the device cannot do that, 502 = unreachable, 423 = locked)."""
    if source != device_store.LOCK_SOURCE and device_store.is_locked(str(row.get("id"))):
        raise device_store.DeviceLocked(row.get("name"))
    ctl = row.get("control") or {}
    plugin = active(str(ctl.get("plugin") or ""))
    if plugin is None:
        raise HTTPException(502, f"{row.get('name') or 'Gerät'}: Anbindung nicht verbunden.")
    try:
        result = await plugin.command(str(ctl.get("device_id")), dict(state), source)
    except plugin_api.DeviceUnreachable as exc:
        raise HTTPException(502, str(exc))
    except PluginError as exc:
        raise HTTPException(400, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Plugin %s: command() (%s)", ctl.get("plugin"), type(exc).__name__)
        raise HTTPException(502, f"Befehl fehlgeschlagen ({type(exc).__name__}).")
    return plain(dict(result), str(ctl.get("plugin") or "")) if isinstance(result, Mapping) else None


def command_interval(row: dict[str, Any]) -> float:
    """Pause in seconds the plugin of a device asks for between two commands (0–2 s; 0 if it asks for
    nothing)."""
    plugin = active(str((row.get("control") or {}).get("plugin") or ""))
    try:
        return max(0.0, min(2.0, float(plugin.command_interval))) if plugin else 0.0
    except (TypeError, ValueError):
        return 0.0
