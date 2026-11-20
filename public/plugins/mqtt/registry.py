"""Device registry: groups announced entities into ComfyOS devices and keeps their current values.

Home Assistant discovery announces *entities* (the light, its battery, its signal strength),
ComfyOS shows *devices*. The registry

* collects the announcements (``announce``) – from Home Assistant discovery, from Tasmota's own
  discovery and from the device list of a Zigbee2MQTT bridge – and the devices the user created by hand,
* groups the entities by the device they belong to (``rebuild``),
* routes every state message to the entities that listen to its topic (``handle_message``),
* and builds the ``plugin_api.Device`` list (``devices``).

One device, several switches
    A device may have several switchable entities that claim the same state key – a double
    socket has two switches, and both mean "on". The first one (by unique id) stays with the
    device, every further one becomes its own ComfyOS device with the entity's name appended.
    Values that are only read (a thermostat that also announces its temperature as a sensor)
    never split a device.

What is left out
    Settings and diagnostics of a device (child lock, signal strength, firmware update) are
    skipped silently. Functions this plugin cannot handle are listed in ``notes`` and shown
    in the tile as "not supported".

The registry does no network access; ``__init__.py`` feeds it and publishes what it returns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from plugin_api import STATE_BRIGHTNESS, STATE_ON, Button, Capability, Device, LightCapabilities, LightState, PluginError

from . import tasmota
from .discovery import Announcement, DeviceInfo, Entity, StateRequest
from .translate import KIND_ORDER, SWITCHABLE_KINDS, TRANSLATORS, EventTranslator, LampTranslator, LightValues, Outgoing
from .translate import Reading, Translator
from .translate import Unsupported, extract_value, parse_value_template, same_payload, to_number, translator_for, wanted_light

DEVICE_ID_PREFIX = "mqtt:"                  # see "id_prefixes" in manifest.json
MAX_ANNOUNCEMENTS = 5000                    # config topics kept in memory; a home has far fewer
MAX_NOTES = 60
OFFLINE_BROKER_TEXT = "MQTT-Broker nicht verbunden"
OFFLINE_DEVICE_TEXT = "Gerät meldet sich als nicht erreichbar"

# Platforms that never become part of a device tile; they are skipped without a note. (Presses arrive through the
# platform "event" since plugin 1.2.0; the older "device_automation" triggers are left out.)
SILENT_PLATFORMS = ("update", "device_automation", "tag", "scene")
EVENT_PLATFORM = "event"
MAX_PRESSES = 50                            # presses kept between two messages (the plugin takes them at once)
# Every platform the plugin can translate: Home Assistant discovery plus Tasmota's own discovery.
PLATFORM_TRANSLATORS = {**TRANSLATORS, **tasmota.TRANSLATORS}
# Zigbee2MQTT reports "online" either as plain text or as {"state": "online"}, depending on its version.
_AVAILABILITY_JSON_PATH = ("state",)
# Zigbee2MQTT names its devices "zigbee2mqtt_0x<16 hex digits>"; the digits are the Zigbee address.
_ZIGBEE2MQTT_IDENTIFIER = re.compile(r"^zigbee2mqtt_0x([0-9a-fA-F]{16})$")
_UNSAFE_ID_CHARACTERS = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass(frozen=True)
class AvailabilityRule:
    """One topic that says whether a device is reachable."""

    topic: str
    value_path: tuple[Any, ...]
    payload_available: Any = "online"
    payload_not_available: Any = "offline"


@dataclass
class TrackedDevice:
    """One ComfyOS device with the entities it is made of and everything their messages told us."""

    device_id: str
    name: str
    info: DeviceInfo
    translators: list[Translator] = field(default_factory=list)
    availability_rules: list[AvailabilityRule] = field(default_factory=list)
    any_rule_is_enough: bool = False                        # availability_mode "any"; otherwise every rule must agree

    state: dict[str, Any] = field(default_factory=dict)     # current values by ComfyOS state key
    light: LightValues = field(default_factory=LightValues)
    battery: int | None = None
    power_w: float | None = None
    energy_kwh: float | None = None
    details: dict[str, str] = field(default_factory=dict)   # further readings for the device sheet
    availability: dict[str, bool] = field(default_factory=dict)   # availability topic -> last answer

    @property
    def kind(self) -> str:
        """What the device is: the best kind its entities suggest."""
        suggested = {translator.kind for translator in self.translators if translator.kind}
        return next((kind for kind in KIND_ORDER if kind in suggested), "unknown")

    @property
    def reachable(self) -> bool | None:
        """True/False as the availability topics say, None when none of them has spoken yet."""
        answers = [self.availability[rule.topic] for rule in self.availability_rules if rule.topic in self.availability]
        if not answers:
            return None
        return any(answers) if self.any_rule_is_enough else all(answers)

    @property
    def light_translator(self) -> LampTranslator | None:
        """The entity that makes this device a lamp, if any."""
        return next((translator for translator in self.translators if isinstance(translator, LampTranslator)), None)


def safe_id_part(text: str, limit: int) -> str:
    """Make a text usable inside a device id: letters, digits, ``_``, ``.`` and ``-`` only."""
    return _UNSAFE_ID_CHARACTERS.sub("_", text)[:limit]


def light_state(tracked: TrackedDevice) -> LightState | None:
    """Build the LightState of a dimmable lamp; None for plugs and lamps that only switch."""
    translator = tracked.light_translator
    if translator is None or not translator.is_dimmable:
        return None
    values = tracked.light
    mode = values.color_mode
    if mode is None and values.xy is not None and translator.has_color:
        mode = "xy"
    if mode is None and values.mirek is not None:
        mode = "ct"
    return LightState(
        capabilities=LightCapabilities(white_range=translator.white_range, color=translator.has_color),
        mode=mode,
        xy=list(values.xy) if values.xy else None,
        mirek=values.mirek,
    )


def _availability_rules(entity: Entity) -> list[AvailabilityRule]:
    """Read the availability settings of an entity: a list of topics or one single topic."""
    config = entity.config
    entries: list[Mapping[str, Any]] = []
    if isinstance(config.get("availability"), list):
        entries = [entry for entry in config["availability"] if isinstance(entry, Mapping)]
    elif isinstance(config.get("availability_topic"), str):
        entries = [{"topic": config["availability_topic"], "value_template": config.get("availability_template"),
                    "payload_available": config.get("payload_available"),
                    "payload_not_available": config.get("payload_not_available")}]
    rules = []
    for entry in entries:
        topic = entry.get("topic")
        if not isinstance(topic, str) or not topic:
            continue
        try:
            value_path = parse_value_template(entry.get("value_template"))
        except Unsupported:
            continue                                        # unreadable template: this rule cannot be evaluated
        rules.append(AvailabilityRule(topic=topic, value_path=value_path,
                                      payload_available=entry.get("payload_available") or "online",
                                      payload_not_available=entry.get("payload_not_available") or "offline"))
    return rules


class DeviceRegistry:
    """All MQTT devices ComfyOS knows, built from discovery announcements and hand-made entities."""

    def __init__(self) -> None:
        """Start empty; announcements and manual entities are added as they arrive."""
        self._announcements: dict[str, Announcement] = {}                   # by config topic
        self._manual_entities: list[Entity] = []
        self._devices: dict[str, TrackedDevice] = {}                        # by device id
        self._state_listeners: dict[str, list[tuple[TrackedDevice, Translator]]] = {}
        self._availability_listeners: dict[str, list[tuple[TrackedDevice, AvailabilityRule]]] = {}
        self._needs_rebuild = False
        self._asked_for_state: set[str] = set()                             # device ids that got a state request
        self.notes: list[str] = []                                          # what is not supported, for the tile
        self.presses: list[tuple[str, str, str]] = []                       # (device id, button, press) not taken yet

    # -- input ----------------------------------------------------------------

    def announce(self, announcement: Announcement) -> None:
        """Take in what a config topic announces now; an announcement without entities removes the topic."""
        if announcement.entities:
            if announcement.source in self._announcements or len(self._announcements) < MAX_ANNOUNCEMENTS:
                self._announcements[announcement.source] = announcement
        else:
            self._announcements.pop(announcement.source, None)
        self._needs_rebuild = True

    def set_manual_entities(self, entities: list[Entity]) -> None:
        """Replace the entities the user created by hand."""
        self._manual_entities = list(entities)
        self._needs_rebuild = True

    def forget_announcements(self) -> None:
        """Drop everything discovery announced (after the discovery prefix changed)."""
        self._announcements.clear()
        self._needs_rebuild = True

    # -- grouping -------------------------------------------------------------

    def rebuild(self) -> bool:
        """Group all entities into devices again. Returns True when the set of devices changed.

        Values of devices that are still there are kept.
        """
        if not self._needs_rebuild:
            return False
        self._needs_rebuild = False
        previous = self._devices
        announcements = list(self._announcements.values())
        entities = [entity for announcement in announcements if not announcement.is_fallback for entity in announcement.entities]
        described_devices = {entity.device.identifier for entity in entities}
        with_events = {entity.device.identifier for entity in entities if entity.platform == EVENT_PLATFORM}
        # A fallback source only fills in devices no other source describes (see Announcement.is_fallback) – and the
        # presses of a remote whose other source announces none (Home Assistant discovery of Zigbee2MQTT without events).
        entities += [entity for announcement in announcements if announcement.is_fallback
                     for entity in announcement.entities if entity.device.identifier not in described_devices
                     or (entity.platform == EVENT_PLATFORM and entity.device.identifier not in with_events)]
        entities += self._manual_entities
        entities.sort(key=lambda entity: entity.unique_id)             # a stable order keeps device ids stable

        self.notes = []
        slots_by_device: dict[str, list[TrackedDevice]] = {}
        for entity in entities:
            translator = self._usable_translator(entity)
            if translator is not None:
                self._place(translator, slots_by_device.setdefault(entity.device.identifier, []))

        self._devices = {}
        for slots in slots_by_device.values():
            for tracked in slots:
                if not any(translator.kind for translator in tracked.translators):
                    continue                                # only a battery level or a power reading: nothing to show
                self._keep_values(tracked, previous.get(tracked.device_id))
                self._devices[tracked.device_id] = tracked
        self._index_topics()
        return set(self._devices) != set(previous)

    def _usable_translator(self, entity: Entity) -> Translator | None:
        """Return the translator for an entity, or None (with a note where the user should know)."""
        is_battery = entity.platform == "sensor" and entity.device_class == "battery"
        if entity.platform in SILENT_PLATFORMS or (entity.is_secondary and not is_battery):
            return None
        try:
            return translator_for(entity, PLATFORM_TRANSLATORS)
        except Unsupported as reason:
            if len(self.notes) < MAX_NOTES:
                self.notes.append(f"{entity.device.name}: {entity.label} – {reason}")
            return None

    def _place(self, translator: Translator, slots: list[TrackedDevice]) -> None:
        """Put a translator into the first device of its group that has no switchable entity for its state keys yet."""
        info = translator.entity.device
        is_channel = translator.kind in SWITCHABLE_KINDS
        for tracked in slots:
            taken_keys = {key for other in tracked.translators if other.kind in SWITCHABLE_KINDS for key in other.state_keys}
            if not is_channel or not taken_keys.intersection(translator.state_keys):
                self._attach(tracked, translator)
                return
        base_id = DEVICE_ID_PREFIX + safe_id_part(info.identifier, 80)
        if not slots:
            tracked = TrackedDevice(device_id=base_id, name=info.name, info=info)
        else:                                               # a further switch of the same device becomes its own device
            suffix = safe_id_part(translator.entity.unique_id, 60)
            extra_name = translator.entity.name or str(len(slots) + 1)
            tracked = TrackedDevice(device_id=f"{base_id}:{suffix}", name=f"{info.name} {extra_name}", info=info)
        slots.append(tracked)
        self._attach(tracked, translator)

    @staticmethod
    def _attach(tracked: TrackedDevice, translator: Translator) -> None:
        """Add a translator to its device and take over the entity's availability rules."""
        tracked.translators.append(translator)
        known_topics = {rule.topic for rule in tracked.availability_rules}
        for rule in _availability_rules(translator.entity):
            if rule.topic not in known_topics:
                tracked.availability_rules.append(rule)
                known_topics.add(rule.topic)
        if translator.config.get("availability_mode") == "any":
            tracked.any_rule_is_enough = True

    @staticmethod
    def _keep_values(tracked: TrackedDevice, before: TrackedDevice | None) -> None:
        """Carry the last known values over to a rebuilt device, so a rebuild does not blank its state."""
        if before is None:
            return
        tracked.state, tracked.light, tracked.details = before.state, before.light, before.details
        tracked.battery, tracked.power_w, tracked.energy_kwh = before.battery, before.power_w, before.energy_kwh
        tracked.availability = before.availability

    def _index_topics(self) -> None:
        """Rebuild the lookup from topic to the devices that listen to it."""
        self._state_listeners, self._availability_listeners = {}, {}
        for tracked in self._devices.values():
            for translator in tracked.translators:
                for topic in translator.topics:
                    self._state_listeners.setdefault(topic, []).append((tracked, translator))
            for rule in tracked.availability_rules:
                self._availability_listeners.setdefault(rule.topic, []).append((tracked, rule))

    # -- messages ---------------------------------------------------------------

    def topics(self) -> list[str]:
        """Return every topic the registry wants to hear (states and availability)."""
        return sorted(set(self._state_listeners) | set(self._availability_listeners))

    def handle_message(self, topic: str, text: str, retained: bool = False) -> dict[str, dict[str, Any]]:
        """Take in a state or availability message.

        Returns what changed, by device id, in the form ``context.device_changed`` expects. Button presses are kept
        in ``presses`` – never from a retained message: that is an old state the broker keeps, not a new press.
        """
        changes: dict[str, dict[str, Any]] = {}
        for tracked, rule in self._availability_listeners.get(topic, []):
            was_shown_online = tracked.reachable is not False      # a device that has not said anything counts as online
            is_available = self._read_availability(rule, text)
            if is_available is not None:
                tracked.availability[topic] = is_available
            is_shown_online = tracked.reachable is not False
            if is_shown_online != was_shown_online:
                changes.setdefault(tracked.device_id, {})["online"] = is_shown_online
        for tracked, translator in self._state_listeners.get(topic, []):
            reading = translator.read(topic, text)
            if reading.press is not None and not retained and len(self.presses) < MAX_PRESSES:
                self.presses.append((tracked.device_id, *reading.press))
            changed = self._apply(tracked, reading)
            if changed:
                changes.setdefault(tracked.device_id, {}).update(changed)
        return changes

    def take_presses(self) -> list[tuple[str, str, str]]:
        """Return the presses since the last call and forget them."""
        presses, self.presses = self.presses, []
        return presses

    @staticmethod
    def _read_availability(rule: AvailabilityRule, text: str) -> bool | None:
        """Return what an availability message says: reachable, not reachable, or None when it says neither.

        A rule without a template also accepts the JSON form ``{"state": "online"}``.
        """
        values = [extract_value(text, rule.value_path)]
        if not rule.value_path:
            values.append(extract_value(text, _AVAILABILITY_JSON_PATH))
        for value in values:
            if value is not None and same_payload(value, rule.payload_available):
                return True
            if value is not None and same_payload(value, rule.payload_not_available):
                return False
        return None

    # -- asking for states --------------------------------------------------------

    def forget_state_requests(self) -> None:
        """Allow every device to be asked for its state again (after the connection was made anew)."""
        self._asked_for_state.clear()

    def due_state_requests(self) -> list[StateRequest]:
        """Return the requests for devices that have not reported any value yet. Each device is asked once."""
        due = []
        for announcement in self._announcements.values():
            for request in announcement.state_requests:
                device_id = DEVICE_ID_PREFIX + safe_id_part(request.device_identifier, 80)
                tracked = self._devices.get(device_id)
                if tracked is None or device_id in self._asked_for_state:
                    continue
                self._asked_for_state.add(device_id)
                if not tracked.state:
                    due.append(request)
        return due

    @staticmethod
    def _apply(tracked: TrackedDevice, reading: Reading) -> dict[str, Any]:
        """Store a reading and return the values that are new."""
        changed: dict[str, Any] = {}
        for key, value in reading.state.items():
            if tracked.state.get(key) != value:
                tracked.state[key] = value
                changed[key] = value
        if reading.power_w is not None and reading.power_w != tracked.power_w:
            tracked.power_w = reading.power_w
            changed["power_w"] = reading.power_w
        if reading.battery is not None:
            tracked.battery = reading.battery
        if reading.energy_kwh is not None:
            tracked.energy_kwh = reading.energy_kwh
        if reading.detail is not None:
            tracked.details[reading.detail[0]] = reading.detail[1]

        light_before = (tracked.light.mirek, tracked.light.xy, tracked.light.color_mode)
        if reading.mirek is not None:
            tracked.light.mirek = reading.mirek
        if reading.xy is not None:
            tracked.light.xy = reading.xy
        if reading.color_mode is not None:
            tracked.light.color_mode = reading.color_mode
        if light_before != (tracked.light.mirek, tracked.light.xy, tracked.light.color_mode):
            current_light = light_state(tracked)
            if current_light is not None:
                changed["light"] = current_light
        return changed

    # -- output -----------------------------------------------------------------

    def find(self, device_id: str) -> TrackedDevice | None:
        """Return the tracked device with this id, or None."""
        return self._devices.get(device_id)

    def devices(self, broker_connected: bool) -> list[Device]:
        """Build the device list for ComfyOS. Without the broker every device is shown as offline."""
        return [self._device(tracked, broker_connected) for tracked in self._devices.values()]

    @staticmethod
    def _device(tracked: TrackedDevice, broker_connected: bool) -> Device:
        """Convert a tracked device to a ComfyOS device: merged capabilities, online state with a reason, and the key
        that links Zigbee devices to other hubs."""
        capabilities: list[Capability] = []
        for translator in tracked.translators:
            known_keys = {capability.key for capability in capabilities}
            capabilities.extend(capability for capability in translator.capabilities() if capability.key not in known_keys)

        is_online, status_detail = True, None
        if not broker_connected:
            is_online, status_detail = False, OFFLINE_BROKER_TEXT
        elif tracked.reachable is False:
            is_online, status_detail = False, OFFLINE_DEVICE_TEXT

        buttons: dict[str, str] = {}
        for translator in tracked.translators:
            if isinstance(translator, EventTranslator):
                for button, name in translator.buttons():
                    buttons.setdefault(button, name)

        link_keys = []
        zigbee_address = _ZIGBEE2MQTT_IDENTIFIER.match(tracked.info.identifier)
        if zigbee_address and tracked.device_id.count(":") == 1:        # only the device itself, not its extra switches
            link_keys.append("zigbee:" + zigbee_address.group(1).lower())

        return Device(
            id=tracked.device_id,
            name=tracked.name,
            kind=tracked.kind,
            online=is_online,
            state=dict(tracked.state),
            capabilities=capabilities,
            light=light_state(tracked),
            buttons=[Button(button, name) for button, name in buttons.items()],
            room_hint=tracked.info.suggested_area,
            vendor=tracked.info.manufacturer,
            model=tracked.info.model,
            battery=tracked.battery,
            power_w=tracked.power_w,
            energy_kwh=tracked.energy_kwh,
            status_detail=status_detail,
            link_keys=link_keys,
            discovered=True,                    # the user decides which MQTT devices ComfyOS shows
            details=dict(tracked.details),
        )

    # -- commands ---------------------------------------------------------------

    def commands(self, tracked: TrackedDevice, state: Mapping[str, Any]) -> list[Outgoing]:
        """Return the messages to publish for a ComfyOS command. Raises PluginError when nothing fits."""
        messages: list[Outgoing] = []
        for translator in tracked.translators:
            messages.extend(translator.commands(state))
        if not messages:
            raise PluginError(f"{tracked.name} kann das nicht.")
        return messages

    def assume(self, tracked: TrackedDevice, state: Mapping[str, Any]) -> dict[str, Any]:
        """Store what a command just asked for, before the device confirms it, and return the device's values.

        Devices answer within a moment; until then the interface shows the requested state
        instead of jumping back to the old one.
        """
        owned_keys = {key for translator in tracked.translators for key in translator.state_keys}
        for key, value in state.items():
            if key in owned_keys and value is not None:
                tracked.state[key] = value
        brightness = to_number(state.get(STATE_BRIGHTNESS))
        if brightness is not None and STATE_ON in owned_keys:
            tracked.state[STATE_ON] = brightness > 0        # a brightness switches the lamp on, 0 switches it off
            if brightness <= 0:
                tracked.state.pop(STATE_BRIGHTNESS, None)
        lamp = tracked.light_translator
        if lamp is not None:
            wanted_xy, wanted_mirek = wanted_light(state)
            if wanted_xy is not None and lamp.has_color:
                tracked.light.xy, tracked.light.color_mode = wanted_xy, "xy"
            elif wanted_mirek is not None and lamp.white_range is not None:
                tracked.light.mirek, tracked.light.color_mode = wanted_mirek, "ct"
        result = {key: tracked.state[key] for key in owned_keys if key in tracked.state}
        current_light = light_state(tracked)
        if current_light is not None:
            result["light"] = current_light
        return result
