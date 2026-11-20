"""Example plugin: a virtual lamp that lives only inside ComfyOS.

Use this folder as the starting point for your own plugin:

1. Copy the folder to ``<data>/plugins/<your_id>/`` and set ``"id"`` in manifest.json
   to the folder name (lower case letters, digits and ``_``).
2. Choose your own ``id_prefixes`` – every device id must start with one of them.
3. Replace the simulated lamp below with calls to your real device or hub.
4. Switch the plugin on under System → Plugins.

What this example shows
    * ``setup(context)`` as the entry point named in manifest.json
    * settings from the manifest (``lamp_name``, ``dimmable``) read through ``context.settings``
    * ``devices()`` reporting one device with its state and capabilities
    * ``command()`` validating and applying a command, then reporting the new state
    * keeping a small file in ``context.data_dir`` so the lamp survives a restart
    * ``status()`` for the tile and one own endpoint in ``routes()``

The whole plugin is one file on purpose. Larger plugins split it the way the Hue
plugin does: one module per concern (client, translation, routes).
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from fastapi import APIRouter

from plugin_api import (
    STATE_BRIGHTNESS,
    STATE_ON,
    Capability,
    Device,
    LightState,
    Plugin,
    PluginContext,
    PluginError,
)

DEVICE_ID = "example:lamp"              # must start with an entry of "id_prefixes" in manifest.json
STATE_FILE_NAME = "lamp.json"
DEFAULT_LAMP_NAME = "Beispiel-Lampe"

# Settings declared in manifest.json
SETTING_LAMP_NAME = "lamp_name"
SETTING_DIMMABLE = "dimmable"


class ExamplePlugin(Plugin):
    """Provides one virtual lamp. Its state is kept in memory and in a small file."""

    def __init__(self, context: PluginContext) -> None:
        """Start with the lamp off at full brightness; ``start()`` loads the saved state."""
        super().__init__(context)
        self._is_on = False
        self._brightness = 100          # per cent
        self._commands_handled = 0

    # -- life cycle ---------------------------------------------------------

    async def start(self) -> None:
        """Restore the lamp's last state. A real plugin would connect to its hub here."""
        self._load_state()

    async def stop(self) -> None:
        """Nothing to close. A real plugin would close its connections here."""

    def status(self) -> dict[str, Any]:
        """Report the plugin as connected - the example lamp is always there - and how many commands it handled."""
        return {"configured": True, "connected": True, "live": True,
                "message": f"{self._commands_handled} Befehle seit dem Start"}

    # -- devices ------------------------------------------------------------

    async def devices(self) -> list[Device]:
        """Return the one example lamp with its current state; brightness is only offered if the setting allows
        dimming."""
        is_dimmable = bool(self.context.settings.get(SETTING_DIMMABLE, True))

        state: dict[str, Any] = {STATE_ON: self._is_on}
        capabilities = [Capability(STATE_ON, "bool", writable=True)]
        if is_dimmable:
            state[STATE_BRIGHTNESS] = self._brightness
            capabilities.append(Capability(STATE_BRIGHTNESS, "number", unit="%", minimum=1, maximum=100, writable=True))

        lamp = Device(
            id=DEVICE_ID,
            name=self.context.settings.get(SETTING_LAMP_NAME) or DEFAULT_LAMP_NAME,
            kind="light",
            online=True,
            state=state,
            capabilities=capabilities,
            light=LightState() if is_dimmable else None,    # a LightState marks the lamp as dimmable
            vendor="ComfyOS",
            model="Virtuelle Lampe",
            details={"Art": "Beispiel – existiert nur in ComfyOS"},
        )
        return [lamp]

    # -- commands -----------------------------------------------------------

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
        """Apply ``{"on": bool}`` and/or ``{"brightness": 1–100}``. ComfyOS has already checked the device lock."""
        if device_id != DEVICE_ID:
            raise PluginError("Dieses Gerät gehört nicht zum Beispiel-Plugin.")
        if STATE_ON not in state and state.get(STATE_BRIGHTNESS) is None:
            raise PluginError("Bitte on oder brightness angeben.")

        if state.get(STATE_BRIGHTNESS) is not None:
            self._brightness = self._checked_brightness(state[STATE_BRIGHTNESS])
            self._is_on = True          # setting a brightness switches the lamp on, like a real dimmer
        if STATE_ON in state:
            self._is_on = bool(state[STATE_ON])

        self._commands_handled += 1
        self._save_state()
        return {STATE_ON: self._is_on, STATE_BRIGHTNESS: self._brightness}

    @staticmethod
    def _checked_brightness(value: Any) -> int:
        """Return the brightness as a whole number from 1 to 100. Raises ``PluginError`` with a text for the user
        otherwise."""
        try:
            brightness = int(round(float(value)))
        except (TypeError, ValueError):
            raise PluginError("Helligkeit muss eine Zahl sein.")
        if not 1 <= brightness <= 100:
            raise PluginError("Helligkeit muss zwischen 1 und 100 liegen.")
        return brightness

    # -- persistence --------------------------------------------------------

    def _load_state(self) -> None:
        """Load the lamp's state from the plugin's data folder; a missing or unreadable file keeps the defaults."""
        try:
            saved = json.loads((self.context.data_dir / STATE_FILE_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return                      # first start or unreadable file: keep the defaults
        if isinstance(saved, dict):
            self._is_on = bool(saved.get("on"))
            if isinstance(saved.get("brightness"), int) and 1 <= saved["brightness"] <= 100:
                self._brightness = saved["brightness"]

    def _save_state(self) -> None:
        """Save the lamp's state to the plugin's data folder so it survives a restart."""
        content = json.dumps({"on": self._is_on, "brightness": self._brightness})
        (self.context.data_dir / STATE_FILE_NAME).write_text(content, encoding="utf-8")

    # -- endpoints ----------------------------------------------------------

    def routes(self) -> APIRouter:
        """Own endpoints, reachable under ``/api/plugins/<id>/`` for signed-in users."""
        router = APIRouter()

        @router.get("/info")
        async def info() -> dict[str, Any]:
            return {"lamp_on": self._is_on, "brightness": self._brightness, "commands": self._commands_handled}

        return router


def setup(context: PluginContext) -> Plugin:
    """Entry point named in manifest.json. Create the plugin; connect later in ``start()``."""
    return ExamplePlugin(context)
