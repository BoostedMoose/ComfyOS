"""Home Base plugin: the QIVICON / Telekom Magenta Home Base 2 in the home network.

What it does
    * Signs in to the web UI of the Home Base (password only) and talks to the openHAB inside
      through the Home Base's own REST proxy: read devices, switch them, manage rooms.
    * Pairs new devices, reconnects lost ones and deletes devices – the same calls the web UI makes.
    * Optional Magenta sync: keeps heating routines and home/away of ComfyOS in step with the
      Magenta app through the Telekom cloud (needs the Telekom login; off until set up).

Roles towards ComfyOS
    * ``manages_rooms``: the Home Base keeps the room list. ComfyOS writes rooms and room
      assignments of its floor plan there, so the Magenta app shows the same rooms.
    * ``syncs_heating``: the Magenta sync.
    * Device ids are the thing UIDs of the Home Base ("dect:…", "hue:…", "zigbee:…") – the
      manifest therefore declares ``"id_prefixes": ["*"]``. Things of a Hue Bridge are relayed:
      they carry the link key of the Hue plugin's device, so a lamp stays one device.

Files
    connector.py   session, REST proxy, devices, rooms, pairing calls, commands
    model.py       openHAB data -> devices (pure functions)
    pairing.py     prepare the pairing catalog (pure functions)
    telekom.py     Telekom cloud client (OAuth, heating plugin through the relay)
    magenta.py     the sync rounds between ComfyOS' heating routines and the Magenta app
    routes.py      endpoints for the settings tile and the pairing assistant
"""
from __future__ import annotations

from typing import Any, Mapping

from plugin_api import Device, Plugin, PluginContext, PluginError, Room

from . import model
from .connector import HomeBaseConnector, QiviconError, normalize_origin
from .magenta import MagentaSync
from .routes import build_router

EGRESS_HOME_BASE = "homebase"            # see manifest.json -> egress
EGRESS_CATALOG = "catalog"
EGRESS_MAGENTA = "magenta"
DEFAULT_ADDRESS = "https://qivicon"
NOT_CONNECTED = "Keine Home Base verbunden. Bitte unter System einrichten."

# Settings and secrets this plugin stores
SETTING_ADDRESS = "address"              # "https://<host>[:port]"
SETTING_VERIFY_TLS = "verify_tls"
SETTING_GATEWAY_ID = "gateway_id"        # serial number, needed by the cloud relay of the Magenta sync
SECRET_PASSWORD = "password"


class QiviconPlugin(Plugin):
    """Connects one Home Base to ComfyOS."""

    manages_rooms = True
    syncs_heating = True

    def __init__(self, context: PluginContext) -> None:
        """Start unconnected; ``start()`` connects with the stored settings and begins the Magenta sync."""
        super().__init__(context)
        self.connector: HomeBaseConnector | None = None
        self.sync = MagentaSync(context, self.gateway_id)
        self.scan_known: set[str] | None = None        # device ids when the last search started
        self.scan_reported: set[str] = set()
        self.catalog_cache: tuple[float, dict[str, Any]] | None = None
        self._router = build_router(self)

    # -- connection ----------------------------------------------------------

    def gateway_id(self) -> str | None:
        """Serial number of the connected Home Base, if known."""
        return self.connector.gateway_id if self.connector is not None else None

    def build_connector(self) -> HomeBaseConnector | None:
        """Create the connector from the stored settings.

        Returns None without address or password – and when the address is outside the home
        network: the password of the Home Base never goes anywhere else, not even if the
        address came in through a restored backup or an edited settings file.
        """
        address = str(self.context.settings.get(SETTING_ADDRESS) or "").strip()
        password = self.context.secrets.get(SECRET_PASSWORD)
        if not address or not password:
            return None
        try:
            _, host, _ = normalize_origin(address)
        except QiviconError:
            return None
        if not self.context.is_local_host(host):
            self.context.event("Adresse der Home Base liegt nicht im Heimnetz – Verbindung nicht aufgebaut")
            return None
        connector = HomeBaseConnector(
            address, str(password), verify_tls=bool(self.context.settings.get(SETTING_VERIFY_TLS)),
            open_client=lambda **options: self.context.http(EGRESS_HOME_BASE, **options),
            open_catalog_client=lambda **options: self.context.http(EGRESS_CATALOG, **options))
        gateway_id = self.context.settings.get(SETTING_GATEWAY_ID)
        if gateway_id:
            connector.gateway_id = str(gateway_id)
        return connector

    async def use_connector(self, connector: HomeBaseConnector | None) -> None:
        """Replace the connection (after new settings; tests hand in a prepared connector)."""
        if self.connector is not None and self.connector is not connector:
            await self.connector.close()
        self.connector = connector
        self.catalog_cache = None
        self.scan_known = None

    def need_connector(self) -> HomeBaseConnector:
        """Return the connector or raise the "not connected" message."""
        if self.connector is None:
            raise PluginError(NOT_CONNECTED)
        return self.connector

    # -- life cycle ----------------------------------------------------------

    async def start(self) -> None:
        """Connect to the Home Base from the stored settings (unless a test already supplied a connector) and start
        the Magenta sync loop."""
        if self.connector is None:
            await self.use_connector(self.build_connector())
        self.sync.start()

    async def stop(self) -> None:
        """Stop the Magenta sync and close the connection to the Home Base."""
        await self.sync.stop()
        await self.use_connector(None)

    def status(self) -> dict[str, Any]:
        """Report whether the Home Base is configured and answering, and which internet destinations are in use
        (device catalogue only while pairing, Magenta only if set up)."""
        connector = self.connector
        synced = self.sync.configured()
        return {"configured": connector is not None,
                "connected": connector is not None and connector.last_ok is not False,
                # ComfyOS reads the Home Base every few seconds while a page is open: changes show up "live"
                "live": connector is not None and connector.last_ok is not False,
                "egress": {EGRESS_CATALOG: ["on_demand", "nur beim Anlernen"],
                           EGRESS_MAGENTA: ["on", "eingerichtet"] if synced else ["off", "nicht eingerichtet"]}}

    def routes(self) -> Any:
        """Return the plugin's own routes (connection, Magenta account, pairing)."""
        return self._router

    def hints(self) -> list[dict[str, Any]]:
        """The Home Base knows the addresses of its Hue Bridges – the Hue plugin suggests them for pairing – and
        which Sonos speakers it has, so the Sonos plugin can offer to look for them."""
        bridges = self.connector.hue_bridges if self.connector is not None else []
        speakers = getattr(self.connector, "sonos_speakers", None) or []
        return ([{"kind": "hue-bridge", "host": bridge["host"]} for bridge in bridges if bridge.get("host")]
                + [{"kind": "sonos", "uuid": speaker["uuid"], "host": speaker.get("host")} for speaker in speakers])

    # -- devices -------------------------------------------------------------

    async def devices(self) -> list[Device]:
        """Return all devices the Home Base knows; ``max_device_age`` decides whether a cached list is fresh enough."""
        if self.connector is None:
            return []
        return [model.to_device(device) for device in await self.connector.list_devices(self.max_device_age)]

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
        """Send a state change to a device of the Home Base."""
        return await self.need_connector().set_device_state(device_id, dict(state))

    # -- rooms ---------------------------------------------------------------

    async def rooms(self) -> list[Room]:
        """Return the rooms of the Home Base."""
        return [Room(id=room["name"], name=room.get("label") or room["name"])
                for room in await self.need_connector().list_rooms()]

    async def create_room(self, name: str) -> str:
        """Create a room on the Home Base and return its id."""
        return await self.need_connector().create_room(name)

    async def rename_room(self, room_id: str, name: str) -> None:
        """Rename a room on the Home Base."""
        await self.need_connector().rename_room(room_id, name)

    async def delete_room(self, room_id: str) -> None:
        """Delete a room on the Home Base."""
        await self.need_connector().delete_room(room_id)

    async def move_device(self, device_id: str, room_id: str) -> None:
        """Move a device into another room on the Home Base."""
        await self.need_connector().move_device(device_id, room_id)

    # -- heating plans (Magenta sync) ----------------------------------------

    def heating_status(self) -> dict[str, Any]:
        """Return the state of the heating sync for the core (configured, last run, last error)."""
        return self.sync.public_status()

    async def sync_heating(self, pull: bool = True, force: bool = False) -> dict[str, Any]:
        """Run the heating sync with the Magenta account now: push pending changes and, with ``pull``, read the remote
        state."""
        return await self.sync.sync(pull=pull, force=force)


def setup(context: PluginContext) -> Plugin:
    """Entry point named in manifest.json."""
    return QiviconPlugin(context)
