"""Test world "pairing at the Home Base": a mock Home Base with device catalogue, search, inbox and the procedures
for including, reconnecting and deleting. Shared by tests/plugins/test_pairing.py and the UI test ui_pairing.

On import the whole world is created: isolated environment, app, mocks. Whoever uses it imports the names they
need – only the contents (lists, dictionaries) are changed, never the names themselves.
"""
import json
import sys
from pathlib import Path

from . import boot  # noqa: E402

ROOT, tmp = boot.isolate("pair")

import httpx  # noqa: E402

from app import auth, core, device_store, egress, main  # noqa: E402
from support.homebase import QiviconConnector, homebase_plugin, install_homebase  # noqa: E402

TP = "text/plain; charset=UTF-8"
JS = "application/json; charset=UTF-8"
THERMO = "dect:eurotronic_heatingThermostat_v1:02e9e06dda"
GHOST = "hue:0210:ecb5faaabbcc:12"
PLUG = "dect:sercomm_indoorSmartPlug_v1:02c3c7accd"
NEW_THERMO = "dect:eurotronic_heatingThermostat_v1:0abcdef012"
CAMERA = "dlink-camera:DCS-T8000:cam1"
ROOM = "room2221228110212499"
BACKEND = "https://backend-prod.cloud.qivicon.com/services"
BLOB = BACKEND + "/pis/blob/localized/"
IMG = "/api/plugins/qivicon/pairing/image?p=localized/"   # images go through ComfyOS, never directly from the browser
PNG = b"\x89PNG-fake"

# ------------------------------------------------------------------ Mock Home Base (Proxy-Ebene)
QDEV = {
    THERMO: {"id": THERMO, "label": "Heizkörper", "thingTypeId": "dect:eurotronic_heatingThermostat_v1", "room": ROOM,
             "deviceStatus": {"status": "OFFLINE", "statusDetail": "GONE", "statusDescription": None}, "condition": {}},
    GHOST: {"id": GHOST, "label": "Ensis Tisch", "thingTypeId": "hue:0210", "room": ROOM,
            "deviceStatus": {"status": "OFFLINE", "statusDetail": "CONFIGURATION_ERROR",
                             "statusDescription": "@text/offline.conf-error-wrong-light-id"}, "condition": {}},
    PLUG: {"id": PLUG, "label": "TV", "thingTypeId": "dect:sercomm_indoorSmartPlug_v1", "room": ROOM,
           "deviceStatus": {"status": "ONLINE", "statusDetail": "NONE"}, "condition": {}},
}
PAIRABLE = [
    {"thingTypeUID": "dect:eurotronic_heatingThermostat_v1", "label": "Heating Valve", "bridge": False,
     "setupManual": None, "setupInbox": "dect-pairing-flow", "setupInboxHasSteps": False},
    {"thingTypeUID": "dect:sercomm_indoorSmartPlug_v1", "label": "Smart Plug indoor", "bridge": False,
     "setupManual": None, "setupInbox": "dect-pairing-flow", "setupInboxHasSteps": False},
    {"thingTypeUID": "dect:bridge", "label": "DECT (internal)", "bridge": True,
     "setupManual": None, "setupInbox": None, "setupInboxHasSteps": False},
    {"thingTypeUID": "hue:0210", "label": "Extended Color Light", "bridge": False,
     "setupManual": None, "setupInbox": "hue-thing-flow", "setupInboxHasSteps": False},
    {"thingTypeUID": "wiz-msh:0010-PM", "label": "WiZ plug", "bridge": False,
     "setupManual": "wiz-msh-device-bluetooth-pairing-flow", "setupInbox": None, "setupInboxHasSteps": False},
    {"thingTypeUID": "dlink-camera:DCS-T8000", "label": "D-Link DCS-T8000 Camera", "bridge": False,
     "setupManual": "dlink-camera-pairing-flow", "setupInbox": "dlink-camera-pairing-flow", "setupInboxHasSteps": True},
]
RECONNECT_FLOWS = [{"id": "dect-reconnecting-flow", "label": "Neu verbinden", "mode": "GATEWAY", "configParameters": [],
                    "steps": [{"id": "reconnecting-step", "label": "Bitte setzen Sie das Gerät auf den Werkzustand zurück",
                               "context": "product-instructions", "properties": {"instructions": "reset"},
                               "timeout": 180}], "sources": []}]
INBOX = []
calls = []   # (method, path, payload, content_type) – everything except plain read access to lists


def q(uid):
    """Device id encoded the way it appears in a path of the Home Base."""
    return uid.replace(":", "%3A")


class MockHB(QiviconConnector):
    """Home Base of the pairing world: answers the calls of the pairing procedure and records them in calls."""
    def _needs_login(self):
        """The session is always valid."""
        return False

    async def status(self):
        """Immer verbunden."""
        return {"mode": "homebase", "connected": True, "gateway": "Mock", "endpoint": "mock", "message": "ok"}

    async def _rpc_raw(self, base, path, method="GET", payload=None, content_type=None):
        """Answer a call of the Home Base: devices, rooms, pairable devices, procedure of the pairing, deleting."""
        if method != "GET" or "/flows/" in path:
            calls.append((method, path, payload, content_type))
        if method == "GET":
            if path == "/rest/qivicon/devices":
                return [dict(d) for d in QDEV.values()]
            if path == "/rest/things":
                return [{"UID": d["id"], "thingTypeUID": d["thingTypeId"], "label": d["label"], "location": d.get("room"),
                         "statusInfo": {"status": d["deviceStatus"]["status"], "statusDetail": d["deviceStatus"]["statusDetail"]},
                         "channels": []} for d in QDEV.values()]
            if path == "/rest/items":
                return [{"name": ROOM, "label": "Arbeitszimmer", "type": "Group"}]
            if path == "/rest/qivicon/pairing/devices":
                return PAIRABLE
            if path == "/rest/inbox":
                return list(INBOX)
            if path == f"/rest/qivicon/flows/thing/{q(THERMO)}/contexts/setup-reconnect":
                return RECONNECT_FLOWS
            if path == f"/rest/qivicon/flows/thing/{q(GHOST)}/contexts/setup-reconnect":
                return []
            if path == f"/rest/qivicon/flows/thing/{q(NEW_THERMO)}/contexts/setup-inbox":
                return [{"id": "dect-pairing-flow", "label": "@text/dect_pairing.label", "mode": "GATEWAY",
                         "configParameters": [], "steps": [], "sources": []}]
            if path == f"/rest/qivicon/flows/thing/{q(CAMERA)}/contexts/setup-inbox":
                return [{"id": "dlink-camera-pairing-flow", "steps": [{"id": "wifi", "label": "WLAN"}]}]
            if path.startswith("/rest/qivicon/flows/thing/") and path.endswith("/contexts/setup-inbox"):
                return []
            if path == "/rest/qivicon/rooms":
                return [{"name": "room2221228110212499", "label": "Arbeitszimmer"}]
        if method == "POST" and path == f"/rest/qivicon/flows/thing/{q(NEW_THERMO)}/contexts/setup-inbox/dect-pairing-flow":
            label = json.loads(payload)["label"]
            QDEV[NEW_THERMO] = {"id": NEW_THERMO, "bridge": "dect:bridge:internal", "isBridge": False, "label": label,
                                "room": None, "thingTypeId": "dect:eurotronic_heatingThermostat_v1",
                                "deviceStatus": {"status": "UNKNOWN", "statusDetail": "NONE"}, "condition": {}}
            return {"id": f"dect-pairing-flow:{NEW_THERMO}", "flowDescriptionId": "dect-pairing-flow", "entityId": NEW_THERMO,
                    "entityType": "thing", "state": "FINISHED", "context": "setup-inbox", "nextStep": None, "prevStep": None}
        if method == "DELETE" and path == f"/rest/qivicon/flows/{q('dect-pairing-flow:' + NEW_THERMO)}":
            return ""
        if method == "PUT" and path.startswith("/rest/qivicon/devices/"):
            body = json.loads(payload)
            QDEV[body["id"]] = body
            return None
        if method == "POST" and path == "/rest/discovery/bindings/dect/scan":
            INBOX.extend([{"thingUID": NEW_THERMO, "thingTypeUID": "dect:eurotronic_heatingThermostat_v1",
                           "label": "Heating Valve", "flag": "NEW"},
                          {"thingUID": "dect:sercomm_indoorSmartPlug_v1:ignored", "thingTypeUID": "dect:sercomm_indoorSmartPlug_v1",
                           "label": "Smart Plug", "flag": "IGNORED"}])
            return 120
        if method == "POST" and path == f"/rest/qivicon/flows/thing/{q(THERMO)}/contexts/setup-reconnect/dect-reconnecting-flow":
            return {"id": f"dect-reconnecting-flow:{THERMO}", "flowDescriptionId": "dect-reconnecting-flow",
                    "entityId": THERMO, "entityType": "thing", "state": "INITIALIZED", "context": "setup-reconnect",
                    "nextStep": "reconnecting-step", "prevStep": None}
        if method == "POST" and path == f"/rest/qivicon/flows/{q('dect-reconnecting-flow:' + THERMO)}/steps/reconnecting-step":
            return {"flowId": f"dect-reconnecting-flow:{THERMO}", "stepId": "reconnecting-step:1794692441010",
                    "prevStep": None, "nextStep": None}
        if method == "POST" and path.startswith("/rest/inbox/") and path.endswith("/approve"):
            return None
        if method == "DELETE" and path.startswith("/rest/qivicon/devices/"):
            uid = path.rsplit("/", 1)[1].replace("%3A", ":")
            QDEV.pop(uid, None)
            return None
        raise AssertionError(f"unerwarteter Aufruf {method} {path}")


# ------------------------------------------------------------------ Mock Produktkatalog (Telekom-Backend)
PIS = [
    {"hardwareId": "HIDTHERMO", "vendor": "Magenta SmartHome", "name": "Heizkörperthermostat",
     "pairingDisplayName": "Heizkörperthermostat", "models": ["DT_HKR_461_03_RF_460_03", "SmartHome Heizkörperthermostat"],
     "image": BLOB + "aaa", "hidden": False, "lifecycleStatus": "Available", "beta": False, "licenseRestricted": False,
     "thingTypeIds": ["dect:eurotronic_heatingThermostat_v1"]},
    {"hardwareId": "HIDPLUG", "vendor": "Magenta SmartHome", "name": "Zwischenstecker innen", "models": [],
     "image": "https://evil.example.com/x.png", "hidden": False, "lifecycleStatus": "Available", "beta": True,
     "licenseRestricted": False, "thingTypeIds": ["dect:sercomm_indoorSmartPlug_v1"]},
    {"hardwareId": "HIDOLD", "vendor": "Magenta SmartHome", "name": "Altes Gerät", "hidden": False,
     "lifecycleStatus": "EndOfLife", "thingTypeIds": ["dect:sercomm_indoorSmartPlug_v1"]},
    {"hardwareId": "HIDHIDDEN", "vendor": "Magenta SmartHome", "name": "Verstecktes Gerät", "hidden": True,
     "lifecycleStatus": "Available", "thingTypeIds": ["dect:sercomm_indoorSmartPlug_v1"]},
    {"hardwareId": "HIDUNKNOWN", "vendor": "Osram", "name": "LIGHTIFY", "hidden": False, "lifecycleStatus": "Available",
     "thingTypeIds": []},
    {"hardwareId": "HIDWIZ", "vendor": "Magenta SmartHome", "name": "WLAN Zwischenstecker mit Verbrauchsmessung",
     "hidden": False, "lifecycleStatus": "Available", "thingTypeIds": ["wiz-msh:0010-PM"]},
    {"hardwareId": "HIDCAM", "vendor": "D-Link", "name": "Kamera Innen Basic", "hidden": False,
     "lifecycleStatus": "Available", "thingTypeIds": ["dlink-camera:DCS-T8000"]},
    {"hardwareId": "HIDHUE", "vendor": "Philips Hue", "name": "Alle Hue Lampen und Leuchten", "hidden": False,
     "lifecycleStatus": "Available", "thingTypeIds": ["hue:0210", "hue:0100"]},
]
DETAIL_THERMO = {**PIS[0], "protocols": ["DECT_ULE"],
                 "preparationSteps": [{"text": "Entferne die Abdeckung.\r\n\r\n", "image": BLOB + "s1"},
                                      {"text": "Lege die Batterien ein. Starte die Suche mit Jetzt Suchen.", "image": BLOB + "s2"}],
                 "resetSteps": [{"text": "Taste neben den Batterien 10 Sekunden halten.\r\n", "image": BLOB + "r1"}],
                 "installationHints": [{"title": "Batterie", "text": "2x LR6 AA, 1.5V"}]}
cloud = {"mode": "ok", "requests": []}


def cloud_handler(req: httpx.Request) -> httpx.Response:
    """Responses of the device catalogue: list only with a valid token, images without a token; outage and expired token
    on request."""
    cloud["requests"].append(req)
    assert req.url.host == "backend-prod.cloud.qivicon.com", req.url
    if req.url.path.startswith("/services/pis/blob/"):
        assert "authorization" not in req.headers, "Bildabruf ohne Katalog-Token"
        if req.url.path.endswith("/evil"):
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<script>x</script>")
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)
    if cloud["mode"] == "down":
        raise httpx.ConnectError("offline", request=req)
    if cloud["mode"] == "expired" or req.headers.get("authorization") != "Bearer BT-TEST":
        return httpx.Response(401, json={"message": "expired"})
    assert req.headers.get("accept-language") == "de"
    if req.url.path == "/services/pis/hardware/devices":
        return httpx.Response(200, json=PIS)
    if req.url.path == "/services/pis/hardware/devices/HIDTHERMO":
        return httpx.Response(200, json=DETAIL_THERMO)
    return httpx.Response(404, json={"message": "not found"})


egress._transport = httpx.MockTransport(cloud_handler)      # the catalogue goes through the egress entry of the plugin
