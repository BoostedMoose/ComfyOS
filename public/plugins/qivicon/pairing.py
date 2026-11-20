"""Pairing assistant: prepare the device catalog like the web UI of the Home Base (pure functions).

Sources (verified against a capture of the web UI)
    * Home Base, GET /rest/qivicon/pairing/devices: device types this Home Base can pair –
      thingTypeUID, setupInbox (a flow after the search) or setupManual (manual only).
    * Telekom product catalog, GET <backend>/pis/hardware/devices[/<id>]: vendors, names,
      pictures and step-by-step instructions. Only what the Home Base knows is shown.

Without the catalog (offline, no access) the assistant falls back to the technical type
list of the Home Base: grouped by radio/binding, no pictures, a general instruction.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

SETUP_AUTO = "auto"       # search, then add the found device in ComfyOS
SETUP_STEPS = "steps"     # search; afterwards the device needs more input (web UI of the Home Base)
SETUP_APP = "app"         # no search through the Home Base (e.g. Bluetooth through the Magenta app)

BINDING_NAMES = {
    "dect": "Magenta SmartHome (DECT)", "hue": "Philips Hue", "hmip": "Homematic IP", "homematic": "HomeMatic",
    "zigbee": "ZigBee", "tradfri": "IKEA", "sonos": "Sonos", "miele": "Miele", "homeconnect": "Home Connect",
    "lifx": "LIFX", "netatmo": "Netatmo", "netatmo-security": "Netatmo", "netatmo-energy": "Netatmo",
    "gardena": "Gardena", "wiz": "WiZ", "wiz-msh": "WiZ (Magenta SmartHome)", "bosch": "Bosch", "nest": "Nest",
    "nuki": "NUKI", "tado": "tado", "somfy": "Somfy", "schellenberg": "Schellenberg", "harmony": "Logitech",
    "bosesoundtouch": "Bose", "dlink-camera": "D-Link", "dlink-smartplug": "D-Link", "dlink-cloud": "D-Link",
    "ip-camera": "IP-Kamera", "smappee": "Smappee", "grohe": "Grohe", "doorbird-device": "DoorBird",
    "iotty": "iotty", "bitronvideo": "Bitron Video", "bitronvideoCgi": "Bitron Video",
}

GENERIC_STEP = ("Bringe das Gerät laut Anleitung des Herstellers in den Verbindungsmodus "
                "(oft: Batterien einlegen, Taste gedrückt halten oder Strom anschließen) und starte dann die Suche.")

# Pictures of the catalog are served by the plugin itself: the browser never loads from Telekom.
IMAGE_ROUTE = "/api/plugins/qivicon/pairing/image?p="
BLOB_PATH = re.compile(r"[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+){0,7}")
OFFLINE_STATES = (None, "OFFLINE", "REMOVED", "REMOVING")
DEFAULT_STEP_TIMEOUT = 180


def binding_of(thing_type: str) -> str:
    """'dect:thermostat' -> 'dect'."""
    return str(thing_type).split(":", 1)[0]


def setup_kind(pairable: dict[str, Any]) -> str:
    """How a device type is set up: SETUP_AUTO, SETUP_STEPS or SETUP_APP."""
    if pairable.get("setupInbox"):
        return SETUP_STEPS if pairable.get("setupInboxHasSteps") else SETUP_AUTO
    return SETUP_APP


def pairable_index(pairable: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index the types by thing type UID; internal types without a pairing flow (dect:bridge …) drop out."""
    return {entry["thingTypeUID"]: entry for entry in pairable
            if isinstance(entry, dict) and entry.get("thingTypeUID") and (entry.get("setupInbox") or entry.get("setupManual"))}


def valid_blob_path(path: str) -> bool:
    """True for a picture path of the catalog that is safe to request (no '..', limited length and depth)."""
    return len(path) <= 300 and bool(BLOB_PATH.fullmatch(path)) and ".." not in path.split("/")


def _image(url: Any, backend_url: str | None) -> str | None:
    """Turn a picture URL of the catalog into the plugin's own route; anything else is dropped."""
    prefix = (backend_url or "") + "/pis/blob/"
    if isinstance(url, str) and backend_url and url.startswith(prefix) and valid_blob_path(url[len(prefix):]):
        return IMAGE_ROUTE + quote(url[len(prefix):], safe="/")
    return None


def _sorted_vendors(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return the catalogue as a list of vendors with their devices, both sorted by name."""
    return [{"name": vendor, "devices": sorted(devices, key=lambda device: device["name"].casefold())}
            for vendor, devices in sorted(groups.items(), key=lambda group: group[0].casefold())]


def build_catalog(catalog: list[dict[str, Any]], pairable: list[dict[str, Any]], backend_url: str | None) -> list[dict[str, Any]]:
    """Catalog entries whose device type the Home Base can pair – grouped by vendor."""
    index = pairable_index(pairable)
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in catalog:
        if not isinstance(entry, dict) or not entry.get("hardwareId") or entry.get("hidden") or entry.get("licenseRestricted"):
            continue
        if entry.get("lifecycleStatus") not in (None, "Available"):
            continue
        types = [thing_type for thing_type in (entry.get("thingTypeIds") or []) if thing_type in index]
        if not types:
            continue
        name = entry.get("pairingDisplayName") or entry.get("name") or entry.get("model") or types[0]
        vendor = str(entry.get("vendor") or "").strip() or BINDING_NAMES.get(binding_of(types[0]), binding_of(types[0]))
        models = [model_name for model_name in (entry.get("models") or [])
                  if isinstance(model_name, str) and model_name.strip() and model_name != name]
        groups.setdefault(vendor, []).append({
            "id": entry["hardwareId"], "name": name, "subtitle": ", ".join(models[:2]),
            "image": _image(entry.get("image"), backend_url), "binding": binding_of(types[0]), "types": types,
            "setup": setup_kind(index[types[0]]), "beta": bool(entry.get("beta"))})
    return _sorted_vendors(groups)


def catalog_from_types(pairable: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback without the product catalog: the technical types of the Home Base, grouped by binding."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for thing_type, entry in pairable_index(pairable).items():
        binding = binding_of(thing_type)
        groups.setdefault(BINDING_NAMES.get(binding, binding), []).append({
            "id": thing_type, "name": entry.get("label") or thing_type, "subtitle": thing_type, "image": None,
            "binding": binding, "types": [thing_type], "setup": setup_kind(entry), "beta": False})
    return _sorted_vendors(groups)


def find_entry(vendors: list[dict[str, Any]], device_id: str) -> dict[str, Any] | None:
    """Find a device of the prepared catalog by its id."""
    for vendor in vendors:
        for device in vendor["devices"]:
            if device["id"] == device_id:
                return {**device, "vendor": vendor["name"]}
    return None


def _steps(rows: Any, backend_url: str | None) -> list[dict[str, Any]]:
    """Return the pairing steps of a device with a text; image addresses are resolved against the catalogue's backend."""
    steps = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get("text") or "").strip():
            steps.append({"title": str(row.get("title") or "").strip() or None, "text": str(row["text"]).strip(),
                          "image": _image(row.get("image"), backend_url)})
    return steps


def device_details(entry: dict[str, Any], detail: dict[str, Any] | None, backend_url: str | None) -> dict[str, Any]:
    """Instructions for pairing one device. ``detail`` = catalog details (None = the general instruction)."""
    detail = detail if isinstance(detail, dict) else {}
    steps = _steps(detail.get("preparationSteps"), backend_url) or [{"title": None, "text": GENERIC_STEP, "image": None}]
    return {**entry, "image": _image(detail.get("image"), backend_url) or entry.get("image"),
            "steps": steps, "reset": _steps(detail.get("resetSteps"), backend_url),
            "hints": _steps(detail.get("installationHints"), backend_url), "protocols": detail.get("protocols") or []}


def found_in_inbox(inbox: list[dict[str, Any]], types: list[str]) -> list[dict[str, Any]]:
    """Found, not yet added devices of the searched types."""
    wanted = set(types)
    return [{"uid": entry["thingUID"], "label": entry.get("label") or entry["thingUID"], "type": entry.get("thingTypeUID")}
            for entry in inbox if entry.get("thingUID") and entry.get("thingTypeUID") in wanted and entry.get("flag") != "IGNORED"]


def reconnect_info(flows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first reconnect flow the Home Base offers, reduced to what the assistant needs."""
    flow = next((entry for entry in flows if isinstance(entry, dict) and entry.get("id")), None)
    if flow is None:
        return None
    steps = [{"id": step["id"], "label": str(step.get("label") or "").strip(),
              "timeout": int(step.get("timeout") or DEFAULT_STEP_TIMEOUT)}
             for step in flow.get("steps") or [] if isinstance(step, dict) and step.get("id")]
    return {"flow": flow["id"], "label": flow.get("label") or "Neu verbinden", "steps": steps}


def reconnected(status: dict[str, Any] | None) -> bool:
    """True once the Home Base no longer lists the device as offline.

    Right after reconnecting it often reports "unknown" first – that counts as connected.
    """
    return bool(status) and status.get("status") not in OFFLINE_STATES
