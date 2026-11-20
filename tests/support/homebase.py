"""Mock Home Base for the tests of the core.

The Home Base has been the plugin `qivicon` since 0.19. Tests that need devices of a Home Base derive their
mock class from `QiviconConnector` and put it into the plugin with `install_homebase(...)`.

Order: first `from app import main` (loads the plugins), then import this module.
"""
from __future__ import annotations

from app import plugins  # noqa: E402

from comfyos_plugin_qivicon import connector as _connector  # noqa: E402
from comfyos_plugin_qivicon import model as _model  # noqa: E402

QiviconError = _connector.QiviconError
HomeBaseConnector = _connector.HomeBaseConnector      # the real class of the plugin
PLUGIN_ID = "qivicon"
_STATE_KEYS = ("temperature", "target", "on", "brightness", "open", "motion", "position", "alarm", "valve", "humidity")


class QiviconConnector(_connector.HomeBaseConnector):
    """Base for mocks: needs no login; `list_devices` may return finished device rows (dict)."""

    room_names_provider = None      # earlier mocks pass it on to build_devices; the core owns room names now

    def __init__(self, base_url: str = "https://qivicon", *_unused, **options):
        """Create a mock without credentials; the catalogue goes through the egress entry of the plugin as in operation."""
        # catalogue as in operation through the egress entry of the plugin (tests set egress._transport)
        options.setdefault("open_catalog_client", lambda **o: homebase_plugin().context.http("catalog", **o))
        super().__init__(base_url, "x", **options)

    def _needs_login(self) -> bool:
        """A mock is always "signed in" – no password ever goes out."""
        return False


def build_devices(things, items, qdevices=None, _names=None):
    """Devices from openHAB data – the way the plugin builds them (list of HomeBaseDevice)."""
    return _model.build_devices(things, items, qdevices or [])


def as_device(row):
    """Device row of a mock (dict) -> HomeBaseDevice of the plugin."""
    if not isinstance(row, dict):
        return row
    return _model.HomeBaseDevice(
        id=row["id"], name=row.get("name") or row["id"], kind=row.get("kind") or "unknown", online=bool(row.get("online", True)),
        type_id=row.get("raw_type") or "", room_id=row.get("room_id"), room_name=row.get("room"),
        status=row.get("hb_status") or ("ONLINE" if row.get("online", True) else "OFFLINE"), status_code=row.get("hb_detail"),
        status_text=row.get("status_detail"), vendor=row.get("vendor"), battery=row.get("battery"),
        low_battery=row.get("low_battery"), last_seen=row.get("last_seen"), control_item=row.get("control_item"),
        state={k: row[k] for k in _STATE_KEYS if k in row}, power_w=row.get("power_w"), energy_kwh=row.get("energy_kwh"),
        problems=list(row.get("errors") or []), details=dict(row.get("props") or {}))


def homebase_plugin():
    """Die laufende Instanz des Plugins `qivicon`."""
    return plugins.get(PLUGIN_ID).plugin


def install_homebase(hb):
    """Put a mock Home Base into the plugin (None = no Home Base connected)."""
    plugin = homebase_plugin()
    if hb is not None:
        original = hb.list_devices

        async def list_devices(max_age=None):
            return [as_device(row) for row in await original(max_age)]

        hb.list_devices = list_devices
    plugin.connector = hb
    plugin.catalog_cache = None
    plugin.scan_known = None
    return hb
