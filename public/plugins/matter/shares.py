"""Which devices the bridge shows, and under which endpoint number.

The user decides per device whether Matter controllers see it ("für Matter sichtbar", off by
default). Two things are kept in the plugin's settings (and so in the backup):

* ``shared``     the share keys the user switched on – a ComfyOS device id or a key of ``home.py``
* ``endpoints``  endpoint key -> endpoint number. A number is assigned once and stays with its
                 key for good, also while the device is not shared: a controller knows a device
                 by its endpoint, and a device that comes back under another number would be a
                 new device there (lost name, lost room, broken routines).

A device can need two endpoints (a sensor with temperature and humidity). Since 0.21.1 there is
no fixed limit for the user: :data:`ADVISED_ENDPOINTS` is only a hint in the tile (controllers such
as Alexa get slow with very many bridged devices). :data:`MAX_SHARED_ENDPOINTS` is a technical
bound that keeps the plugin's settings small.
"""
from __future__ import annotations

from typing import Any, Iterable

from plugin_api import KeyValueStore, PluginError

from .devices import DEVICE_ID_PREFIX, HUMIDITY_SUFFIX, SharedEndpoint

ADVISED_ENDPOINTS = 50                      # above this the tile warns: Alexa may get slow or lose devices
MAX_SHARED_ENDPOINTS = 1000                 # technical bound, not a choice for the user (size of the settings)
MAX_SPARE_NUMBERS = 200                     # numbers kept for keys that are not shared right now
FIRST_ENDPOINT_NUMBER = 10                  # 0 = root node, 1 = aggregator; a few numbers stay free
LAST_ENDPOINT_NUMBER = 65000
MAX_KEY_LENGTH = 170
SETTING_SHARED = "shared"
SETTING_ENDPOINTS = "endpoints"
SETTING_NEXT_NUMBER = "next_endpoint"


def share_key_of(endpoint_key: str) -> str:
    """Return the share key an endpoint belongs to (the second endpoint of a sensor belongs to its device)."""
    return endpoint_key[:-len(HUMIDITY_SUFFIX)] if endpoint_key.endswith(HUMIDITY_SUFFIX) else endpoint_key


class ShareList:
    """The user's choice of shared devices and the permanent endpoint numbers."""

    def __init__(self, settings: KeyValueStore) -> None:
        """Load choice and numbers from the plugin's settings; unusable entries are dropped."""
        self._settings = settings
        shared = settings.get(SETTING_SHARED)
        numbers = settings.get(SETTING_ENDPOINTS)
        next_number = settings.get(SETTING_NEXT_NUMBER)
        self._shared: list[str] = [key for key in shared if isinstance(key, str) and 0 < len(key) <= MAX_KEY_LENGTH] \
            if isinstance(shared, list) else []
        self._numbers: dict[str, int] = {}
        for key, number in (numbers.items() if isinstance(numbers, dict) else []):
            valid_number = isinstance(number, int) and not isinstance(number, bool) and FIRST_ENDPOINT_NUMBER <= number <= LAST_ENDPOINT_NUMBER
            if isinstance(key, str) and valid_number and number not in self._numbers.values():
                self._numbers[key] = number
        highest = max(self._numbers.values(), default=FIRST_ENDPOINT_NUMBER - 1)
        is_number = isinstance(next_number, int) and not isinstance(next_number, bool)
        self._next_number = max(highest + 1, next_number if is_number else FIRST_ENDPOINT_NUMBER)

    # -- the user's choice --------------------------------------------------

    @property
    def shared_keys(self) -> list[str]:
        """The share keys the user switched on."""
        return list(self._shared)

    def is_shared(self, share_key: str) -> bool:
        """Return whether the user shares this device."""
        return share_key in self._shared

    def set_shared(self, share_key: str, is_shared: bool, endpoints_in_use: int, endpoints_needed: int = 1) -> None:
        """Switch sharing of one device on or off.

        ``endpoints_in_use`` is how many endpoints the bridge shows right now, ``endpoints_needed``
        how many this device adds. Raises PluginError for a device of the plugin's own controller
        and when the technical bound would be exceeded.
        """
        self.set_many([share_key], is_shared, endpoints_in_use, endpoints_needed)

    def set_many(self, share_keys: Iterable[str], is_shared: bool, endpoints_in_use: int, endpoints_needed: int) -> int:
        """Switch sharing of several devices at once (all, a room, a group); stored with one write.

        ``endpoints_needed`` is how many endpoints the newly shared devices add. Returns how many
        devices changed. Raises PluginError like :meth:`set_shared`; then nothing changes.
        """
        keys = list(dict.fromkeys(share_keys))
        if not keys or any(not key or len(key) > MAX_KEY_LENGTH for key in keys):
            raise PluginError("Dieses Gerät gibt es nicht.")
        if not is_shared:
            before = len(self._shared)
            self._shared = [key for key in self._shared if key not in keys]
            changed = before - len(self._shared)
        else:
            if any(key.startswith(DEVICE_ID_PREFIX) for key in keys):
                raise PluginError("Geräte, die über Matter eingebunden sind, lassen sich nicht wieder freigeben – "
                                  "sie stehen schon in dem System, aus dem sie geteilt wurden.")
            new = [key for key in keys if key not in self._shared]
            if new and endpoints_in_use + endpoints_needed > MAX_SHARED_ENDPOINTS:
                raise PluginError(f"Mehr als {MAX_SHARED_ENDPOINTS} Geräte kann die Bridge nicht zeigen.")
            self._shared += new
            changed = len(new)
        if changed:
            self._settings.set(SETTING_SHARED, self._shared)
        return changed

    # -- endpoint numbers ---------------------------------------------------

    def number_of(self, endpoint_key: str, store: bool = True) -> int:
        """Return the endpoint number of a key; a key seen for the first time gets the next free number, for good.

        ``store=False`` leaves writing the numbers to the caller (:meth:`select` writes once for many).
        """
        number = self._numbers.get(endpoint_key)
        if number is not None:
            return number
        if self._next_number > LAST_ENDPOINT_NUMBER:
            raise PluginError("Es sind keine Endpunkte mehr frei.")
        number = self._next_number
        self._next_number += 1
        self._numbers[endpoint_key] = number
        if store:
            self._store_numbers()
        return number

    def _store_numbers(self) -> None:
        """Write the endpoint numbers to the settings (after dropping the oldest spare ones)."""
        self._forget_oldest()
        self._settings.set(SETTING_ENDPOINTS, self._numbers)
        self._settings.set(SETTING_NEXT_NUMBER, self._next_number)

    def key_of(self, number: int) -> str | None:
        """Return the endpoint key a number belongs to, or None."""
        return next((key for key, known in self._numbers.items() if known == number), None)

    def _forget_oldest(self) -> None:
        """Keep the list of remembered numbers bounded: drop the oldest keys that are not shared any more."""
        surplus = len(self._numbers) - 2 * len(self._shared) - MAX_SPARE_NUMBERS
        for key in list(self._numbers):
            if surplus <= 0:
                break
            if share_key_of(key) not in self._shared:
                del self._numbers[key]
                surplus -= 1

    # -- putting it together ------------------------------------------------

    def select(self, offered: Iterable[SharedEndpoint]) -> list[tuple[int, SharedEndpoint]]:
        """Return the offered endpoints the user shares, each with its endpoint number, at most the technical bound.

        The plugin enforces the share list here – the helper only ever learns about these endpoints.
        """
        selected: list[tuple[int, SharedEndpoint]] = []
        known = len(self._numbers)
        shared = set(self._shared)
        for endpoint in offered:
            if share_key_of(endpoint.key) in shared and len(selected) < MAX_SHARED_ENDPOINTS:
                selected.append((self.number_of(endpoint.key, store=False), endpoint))
        if len(self._numbers) != known:
            self._store_numbers()
        return selected

    def summary(self, endpoints_in_use: int) -> dict[str, Any]:
        """Return the counter for the tile: how many endpoints are shared, and from when on the tile warns."""
        return {"used": endpoints_in_use, "advice": ADVISED_ENDPOINTS}
