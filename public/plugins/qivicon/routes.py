"""Endpoints of the Home Base plugin, mounted under ``/api/plugins/qivicon/``.

They serve the plugin's settings tile and its pairing assistant (static/settings.js,
static/pairing.js): connect, test and diagnose the Home Base, set up the Magenta sync,
list and add found devices, pair, reconnect and delete devices.
Every route requires a signed-in user – the core enforces that for all plugin routes.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from plugin_api import DeviceUnreachable, PluginError

from . import pairing
from .connector import HomeBaseConnector, QiviconError, normalize_origin

if TYPE_CHECKING:
    from . import QiviconPlugin

CATALOG_TTL_SECONDS = 6 * 3600
CATALOG_IMAGE_MAX_BYTES = 3 * 1024 * 1024
CATALOG_CACHE_MAX_BYTES = 50 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
               "image/svg+xml": ".svg"}
ROOM_RETRIES = 3                          # a freshly added device may take a moment to appear in the list
ROOM_RETRY_SECONDS = 1.5
MAX_UID = 160

# Names of the settings and secrets (the same as in __init__.py and magenta.py)
SETTING_ADDRESS = "address"
SETTING_VERIFY_TLS = "verify_tls"
SETTING_GATEWAY_ID = "gateway_id"
SETTING_TELEKOM_USERNAME = "telekom_username"
SECRET_PASSWORD = "password"
SECRET_TELEKOM_PASSWORD = "telekom_password"


class ConnectRequest(BaseModel):
    """Body of ``POST /connect``: address and password of the Home Base as the user typed them."""

    base_url: str = Field("https://qivicon", max_length=200)
    password: str | None = Field(None, max_length=200)     # None = keep the stored password
    verify_tls: bool = False


class MagentaRequest(BaseModel):
    """Body of ``POST /magenta``: the Telekom login for the Magenta sync."""

    username: str = Field(max_length=200)
    password: str | None = Field(None, max_length=200)     # None = keep the stored password


class ScanRequest(BaseModel):
    """Body of ``POST /pairing/scan``: the binding to search with, e.g. "dect"."""

    binding: str = Field(max_length=40)


class ApproveRequest(BaseModel):
    """Body of ``POST /inbox/<uid>/approve``: name and optional room for the new device."""

    label: str = Field("", max_length=80)
    room: str | None = Field(None, max_length=80)          # room of the Home Base to assign right away


def build_router(plugin: "QiviconPlugin") -> APIRouter:
    """Create the router for one plugin instance. Called once from ``QiviconPlugin.__init__``."""
    router = APIRouter()
    context = plugin.context

    def connector() -> HomeBaseConnector:
        """The connected Home Base, or HTTP 400 with the "not connected" message."""
        if plugin.connector is None:
            raise HTTPException(400, "Keine Home Base verbunden. Bitte unter System einrichten.")
        return plugin.connector

    def as_http(error: PluginError) -> HTTPException:
        """Our own errors become HTTP answers: 502 when the Home Base did not answer, else 400."""
        return HTTPException(502 if isinstance(error, DeviceUnreachable) else 400, str(error))

    def text_of(error: BaseException) -> str:
        return str(error) if isinstance(error, PluginError) else f"Fehler ({type(error).__name__})."

    # -- connection ----------------------------------------------------------

    @router.get("/status")
    async def get_status() -> dict[str, Any]:
        """Connection details for the tile. Asks the Home Base, so ``connected`` is current."""
        address = str(context.settings.get(SETTING_ADDRESS) or "")
        status: dict[str, Any] = {"configured": plugin.connector is not None, "connected": False,
                                  "address": address, "verify_tls": bool(context.settings.get(SETTING_VERIFY_TLS)),
                                  "password_saved": bool(context.secrets.get(SECRET_PASSWORD)),
                                  "telekom_username": plugin.sync.username(),
                                  "telekom_saved": bool(context.secrets.get(SECRET_TELEKOM_PASSWORD)),
                                  "sync": plugin.sync.public_status()}
        if plugin.connector is not None:
            status.update(await plugin.connector.status())
        return status

    @router.post("/connect")
    async def connect(request: ConnectRequest) -> dict[str, Any]:
        """Store address and password, then sign in. The password only ever goes to the home network."""
        try:
            _, host, port = normalize_origin(request.base_url)
        except QiviconError as error:
            raise HTTPException(400, str(error))
        address = f"https://{host}" + (f":{port}" if port else "")
        if not context.is_local_host(host):
            raise HTTPException(400, "Die Home Base muss im Heimnetz liegen (IP-Adresse oder Name im Heimnetz, z. B. qivicon).")
        same_address = str(context.settings.get(SETTING_ADDRESS) or "") == address
        if request.password:
            context.secrets.set(SECRET_PASSWORD, request.password)
        elif not context.secrets.get(SECRET_PASSWORD):
            raise HTTPException(400, "Passwort fehlt.")
        elif not same_address:                    # never send a stored password to a different address
            raise HTTPException(400, "Für eine andere Adresse bitte das Passwort der Home Base erneut eingeben.")
        context.settings.set(SETTING_ADDRESS, address)
        context.settings.set(SETTING_VERIFY_TLS, bool(request.verify_tls))
        if not same_address:
            context.settings.delete(SETTING_GATEWAY_ID)     # the serial number belonged to the old address
        await plugin.use_connector(plugin.build_connector())
        if plugin.connector is None:
            return {"ok": False, "connected": False, "message": "Verbindung konnte nicht eingerichtet werden."}
        try:
            system = await plugin.connector.connect(force=True)
        except Exception as error:
            context.devices_changed()
            return {"ok": False, "connected": False, "message": text_of(error)}
        if plugin.connector.gateway_id:
            context.settings.set(SETTING_GATEWAY_ID, plugin.connector.gateway_id)
        context.event("Verbindung zur Home Base hergestellt")
        context.devices_changed()
        return {"ok": True, "connected": True, "system": system, "message": "Home Base verbunden."}

    @router.post("/test")
    async def test_connection() -> dict[str, Any]:
        """Sign in again and read the system record ("Verbindung testen")."""
        try:
            return {"ok": True, "connected": True, "system": await connector().connect(force=True)}
        except HTTPException:
            raise
        except Exception as error:
            return {"ok": False, "connected": False, "message": text_of(error)}

    @router.post("/forget")
    async def forget() -> dict[str, Any]:
        """Disconnect: delete the stored password and address. Devices, rooms and plans stay in ComfyOS."""
        context.secrets.delete(SECRET_PASSWORD)
        for key in (SETTING_ADDRESS, SETTING_VERIFY_TLS, SETTING_GATEWAY_ID):
            context.settings.delete(key)
        await plugin.use_connector(None)
        context.event("Verbindung zur Home Base entfernt")
        context.devices_changed()
        return {"ok": True}

    @router.get("/diagnose")
    async def diagnose() -> dict[str, Any]:
        """Ask the Home Base for its system info without login (is it reachable at all?)."""
        address = str(context.settings.get(SETTING_ADDRESS) or "")
        try:
            _, host, _ = normalize_origin(address)
        except QiviconError:
            host = ""
        if not address or not context.is_local_host(host):
            return {"ok": False, "message": "Keine Home Base konfiguriert."}
        probe = HomeBaseConnector(address, "", verify_tls=bool(context.settings.get(SETTING_VERIFY_TLS)),
                                  open_client=lambda **options: context.http("homebase", **options))
        try:
            info = await probe.probe_system_info()
            return {"ok": bool(info), "system_info": info,
                    "message": "Home Base erreichbar." if info else "Keine Antwort auf /system/info."}
        finally:
            await probe.close()

    @router.get("/explore")
    async def explore() -> dict[str, Any]:
        """Read-only tour of the Home Base's REST API (what may this session read?)."""
        hub = connector()
        return {"endpoint": hub.base_url, "results": await hub.explore()}

    # -- Magenta sync --------------------------------------------------------

    @router.post("/magenta")
    async def set_magenta(request: MagentaRequest) -> dict[str, Any]:
        """Store the Telekom login and run a first sync at once."""
        username = request.username.strip()
        if not username:
            raise HTTPException(400, "Telekom-Benutzername fehlt.")
        if request.password:
            context.secrets.set(SECRET_TELEKOM_PASSWORD, request.password)
        elif not context.secrets.get(SECRET_TELEKOM_PASSWORD) or username != plugin.sync.username():
            raise HTTPException(400, "Telekom-Passwort fehlt.")      # a stored password belongs to its account
        context.settings.set(SETTING_TELEKOM_USERNAME, username)
        await plugin.sync.reset()
        context.event("Magenta-Sync aktiviert")
        status = await plugin.sync.sync(pull=True, force=True)
        return {"ok": True, "status": status, "error": status.get("last_error")}

    @router.post("/magenta/forget")
    async def forget_magenta() -> dict[str, Any]:
        """Remove the Telekom login. The routines stay in ComfyOS."""
        context.secrets.delete(SECRET_TELEKOM_PASSWORD)
        context.settings.delete(SETTING_TELEKOM_USERNAME)
        await plugin.sync.reset()
        context.event("Magenta-Sync entfernt")
        return {"ok": True}

    # -- found devices (inbox) -----------------------------------------------

    @router.get("/inbox")
    async def get_inbox() -> dict[str, Any]:
        """Devices the Home Base discovered but has not added. ``inbox`` is None when it cannot be read."""
        try:
            found = [{"uid": entry["thingUID"], "label": entry.get("label") or entry["thingUID"],
                      "flag": entry.get("flag"), "type": entry.get("thingTypeUID")} for entry in await connector().inbox()]
        except HTTPException:
            raise
        except Exception:
            return {"inbox": None}                # unknown (e.g. the path is not released on this Home Base)
        return {"inbox": found}

    @router.post("/inbox/{thing_uid:path}/approve")
    async def approve(thing_uid: str, request: ApproveRequest) -> dict[str, Any]:
        """Add a found device like the web UI does, then optionally put it into a room."""
        hub = connector()
        if len(thing_uid) > MAX_UID:
            raise HTTPException(400, "Unbekanntes Gerät.")
        try:
            await hub.pair_from_inbox(thing_uid, request.label or thing_uid)
        except Exception as error:
            raise HTTPException(400, text_of(error))
        context.event(f"Gerät eingebunden: {request.label or thing_uid}")
        room_error = None
        if request.room:
            for _ in range(ROOM_RETRIES):
                try:
                    await hub.move_device(thing_uid, request.room)
                    room_error = None
                    break
                except PluginError as error:
                    room_error = str(error)
                    await asyncio.sleep(ROOM_RETRY_SECONDS)
        context.devices_changed()
        return {"ok": True, "room_error": room_error}

    # -- pairing assistant ---------------------------------------------------

    async def read_catalog(hub: HomeBaseConnector) -> dict[str, Any]:
        """Vendors -> devices like in the assistant of the web UI; without the product catalog the type list."""
        cached = plugin.catalog_cache
        if cached and time.time() - cached[0] < CATALOG_TTL_SECONDS:
            return cached[1]
        try:
            pairable = await hub.pairable_types()
        except PluginError as error:
            raise HTTPException(502, str(error))
        try:
            entries = await hub.catalog()
            vendors = pairing.build_catalog(entries if isinstance(entries, list) else [], pairable, hub.backend_url)
            if not vendors:
                raise QiviconError("Der Gerätekatalog enthält keine Geräte für diese Home Base.")
        except PluginError as error:
            return {"source": "homebase", "vendors": pairing.catalog_from_types(pairable),
                    "notice": f"{error} Angezeigt wird die technische Typenliste der Home Base – ohne Bilder und Anleitungen."}
        catalog = {"source": "cloud", "vendors": vendors, "notice": None}
        plugin.catalog_cache = (time.time(), catalog)
        return catalog

    async def thing_status(hub: HomeBaseConnector, thing_uid: str) -> dict[str, Any]:
        try:
            status = await hub.device_status(thing_uid)
        except PluginError as error:
            raise HTTPException(502, str(error))
        if status is None:
            raise HTTPException(404, "Gerät ist der Home Base unbekannt.")
        return status

    @router.get("/pairing/catalog")
    async def pairing_catalog() -> dict[str, Any]:
        return await read_catalog(connector())

    @router.get("/pairing/image")
    async def pairing_image(p: str) -> Response:
        """A picture of the catalog, served by ComfyOS: the browser never connects to Telekom servers itself."""
        if not pairing.valid_blob_path(p):
            raise HTTPException(404, "Bild nicht gefunden.")
        folder = context.data_dir / "catalog"
        name = hashlib.sha256(p.encode("utf-8")).hexdigest()
        cached = next((file for file in folder.glob(name + ".*") if file.suffix in IMAGE_TYPES.values()), None) \
            if folder.is_dir() else None
        if cached is None:
            try:
                image = await connector().catalog_image(p)
            except PluginError:
                raise HTTPException(502, "Gerätekatalog nicht erreichbar.")
            if image is None or image[1] not in IMAGE_TYPES or len(image[0]) > CATALOG_IMAGE_MAX_BYTES:
                raise HTTPException(404, "Bild nicht gefunden.")
            folder.mkdir(parents=True, exist_ok=True)
            if sum(file.stat().st_size for file in folder.iterdir()) > CATALOG_CACHE_MAX_BYTES:
                for file in folder.iterdir():
                    file.unlink(missing_ok=True)
            cached = folder / (name + IMAGE_TYPES[image[1]])
            partial = cached.with_suffix(".part")
            partial.write_bytes(image[0])
            partial.replace(cached)
        content_type = next((mime for mime, suffix in IMAGE_TYPES.items() if suffix == cached.suffix), "application/octet-stream")
        headers = {"Cache-Control": "private, max-age=604800"}
        if cached.suffix == ".svg":               # never let an SVG run scripts as a document
            headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        return Response(cached.read_bytes(), media_type=content_type, headers=headers)

    @router.get("/pairing/devices/{device_id}")
    async def pairing_device(device_id: str) -> dict[str, Any]:
        """Instructions for pairing one device of the catalog."""
        hub = connector()
        catalog = await read_catalog(hub)
        entry = pairing.find_entry(catalog["vendors"], device_id)
        if entry is None:
            raise HTTPException(404, "Gerät nicht im Katalog.")
        detail = None
        if catalog["source"] == "cloud":
            try:
                detail = await hub.catalog(device_id)
            except PluginError:
                detail = None                     # the general instruction instead of an error
        return pairing.device_details(entry, detail, hub.backend_url)

    @router.post("/pairing/scan")
    async def pairing_scan(request: ScanRequest) -> dict[str, Any]:
        """Start the device search for one binding ("Jetzt suchen")."""
        hub = connector()
        try:
            known_bindings = {pairing.binding_of(thing_type) for thing_type in pairing.pairable_index(await hub.pairable_types())}
            if request.binding not in known_bindings:
                raise HTTPException(400, "Diese Geräteart kann die Home Base nicht suchen.")
            plugin.scan_known = {device.id for device in await hub.list_devices(max_age=0)}
            timeout = await hub.start_discovery(request.binding)
        except PluginError as error:
            raise HTTPException(502, str(error))
        context.event(f"Gerätesuche gestartet ({request.binding}, {timeout} s)")
        return {"ok": True, "timeout": timeout}

    @router.get("/pairing/found")
    async def pairing_found(types: str = "") -> list[dict[str, Any]]:
        """Found, not yet added devices of the searched types (inbox of the Home Base)."""
        try:
            inbox = await connector().inbox()
        except PluginError as error:
            raise HTTPException(502, str(error))
        return pairing.found_in_inbox(inbox, [thing_type for thing_type in types[:2000].split(",") if thing_type])

    @router.get("/pairing/new")
    async def pairing_new() -> list[dict[str, Any]]:
        """Devices that appeared on the Home Base since "Jetzt suchen" – also those it adds without the inbox."""
        if plugin.scan_known is None:
            return []
        try:
            devices = await connector().list_devices(max_age=0)
        except PluginError as error:
            raise HTTPException(502, str(error))
        new = [{"id": device.id, "name": device.name or device.id, "kind": device.kind, "room_id": device.room_id}
               for device in devices if device.id not in plugin.scan_known]
        if any(entry["id"] not in plugin.scan_reported for entry in new):     # let open pages reload once
            plugin.scan_reported.update(entry["id"] for entry in new)
            context.devices_changed()
        return new

    # -- reconnect, delete ---------------------------------------------------

    @router.get("/things/{thing_uid}/reconnect")
    async def reconnect_info(thing_uid: str) -> dict[str, Any]:
        """Does the Home Base offer "reconnect" for this device? With the instructions for resetting it."""
        hub = connector()
        status = await thing_status(hub, thing_uid)
        try:
            info = pairing.reconnect_info(await hub.flow_descriptions(thing_uid, "setup-reconnect"))
        except PluginError as error:
            raise HTTPException(502, str(error))
        answer = {"available": info is not None, "status": status, **(info or {}), "reset": []}
        if info is not None:
            try:
                catalog = await read_catalog(hub)
                entry = next((device for vendor in catalog["vendors"] for device in vendor["devices"]
                              if status.get("type") in device["types"]), None)
                if entry and catalog["source"] == "cloud":
                    answer["reset"] = pairing.device_details(entry, await hub.catalog(entry["id"]), hub.backend_url)["reset"]
                    answer["image"] = entry.get("image")
            except (HTTPException, PluginError):
                pass                              # without the catalog the text of the Home Base's flow is enough
        return answer

    @router.post("/things/{thing_uid}/reconnect")
    async def reconnect(thing_uid: str) -> dict[str, Any]:
        """Start reconnecting: create the flow and run its waiting step – like "Weiter" in the web UI."""
        hub = connector()
        status = await thing_status(hub, thing_uid)
        try:
            info = pairing.reconnect_info(await hub.flow_descriptions(thing_uid, "setup-reconnect"))
            if info is None:
                raise HTTPException(400, "Für dieses Gerät bietet die Home Base kein Wiederverbinden an.")
            flow = await hub.start_flow(thing_uid, "setup-reconnect", info["flow"])
            step = flow.get("nextStep") or (info["steps"][0]["id"] if info["steps"] else None)
            if step:
                await hub.run_flow_step(flow["id"], step)
        except PluginError as error:
            raise HTTPException(502, str(error))
        timeout = next((entry["timeout"] for entry in info["steps"] if entry["id"] == step), pairing.DEFAULT_STEP_TIMEOUT)
        context.event(f"Wiederverbinden gestartet: {status.get('label') or thing_uid}")
        return {"ok": True, "flow_id": flow["id"], "timeout": timeout}

    @router.get("/things/{thing_uid}/state")
    async def thing_state(thing_uid: str) -> dict[str, Any]:
        """Current status of one device while the assistant waits for it to come back."""
        hub = connector()
        status = await thing_status(hub, thing_uid)
        connected = pairing.reconnected(status)
        if connected:
            hub.invalidate_devices()
            context.devices_changed()
        return {**status, "connected": connected}

    @router.delete("/things/{thing_uid}")
    async def delete_thing(thing_uid: str) -> dict[str, Any]:
        """Delete a device from the Home Base for good. It only comes back by pairing it again."""
        hub = connector()
        status = await thing_status(hub, thing_uid)
        try:
            await hub.delete_device(thing_uid)
        except PluginError as error:
            raise as_http(error)
        context.event(f"Von der Home Base gelöscht: {status.get('label') or thing_uid}")
        context.device_gone(thing_uid)            # ComfyOS forgets the user's name and the "removed" mark
        context.devices_changed()
        return {"ok": True}

    return router
