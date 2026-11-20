"""Messages between the Matter plugin and its helper program (bridge/bridge.mjs).

Plugin and helper exchange JSON objects, one per line. This module is the only place that knows
their shape:

* ``*_message`` functions build what the plugin sends.
* :func:`parse` turns a message of the helper into a typed object – or raises
  :class:`ProtocolError`. **Everything the helper sends is foreign input**: a bug or a
  vulnerability in matter.js must not reach the rest of the plugin, so every field is checked
  for type, range and length, and unknown fields are dropped.

Values on this channel are in Matter units (level 1–254, mireds, hundredths of a degree,
hundredths of a per cent). ``devices.py`` converts between them and ComfyOS.

No network and no files here – pure functions, tested in ``tests/test_matter_protocol.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

PROTOCOL_VERSION = 1

NAMESPACES = ("bridge", "controller")       # the two matter.js nodes that keep storage
MAX_STORAGE_OPS = 5000                      # operations in one `storage` message
MAX_CONTEXT_LENGTH = 400
MAX_KEY_LENGTH = 200
MAX_VALUE_LENGTH = 512 * 1024               # one stored value (JSON text)
STORAGE_CHUNK_BYTES = 200 * 1024            # stored values per `storage` message to the helper
MAX_ENDPOINT_NUMBER = 65534
MAX_FABRICS = 32
MAX_REMOTE_ENDPOINTS = 200
MAX_QR_SIZE = 64                            # modules per side
MAX_ID = 2_000_000_000
MAX_TEXT = 64
REPLY_STATUSES = ("ok", "locked", "error")
COVER_COMMANDS = ("up", "down", "stop")
COLOR_MODES = ("xy", "ct")

_NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_ERROR_CODE = re.compile(r"^[A-Za-z0-9_:-]{1,80}$")
_MANUAL_CODE = re.compile(r"^\d{11}$|^\d{21}$")
_QR_CODE = re.compile(r"^MT:[A-Z0-9.\-]{10,250}$")
_QR_ROW = re.compile(r"^[01]+$")

# State keys and the range each may have (Matter units). None = boolean, a tuple of strings = one of them.
_STATE_RANGES: dict[str, Any] = {
    "on": None, "contact": None, "occupied": None, "smoke": None,
    "level": (0, 254), "mireds": (1, 65279), "min_mireds": (1, 65279), "max_mireds": (1, 65279),
    "x": (0, 65535), "y": (0, 65535),
    "local_temperature": (-27315, 32767), "temperature": (-27315, 32767),
    "heating_setpoint": (-27315, 32767), "min_heat": (-27315, 32767), "max_heat": (-27315, 32767),
    "position": (0, 10000), "humidity": (0, 10000),
    "cover": COVER_COMMANDS, "color_mode": COLOR_MODES,
}


class ProtocolError(Exception):
    """A message of the helper does not have the agreed shape. The text names the field, never its content."""


# ---------------------------------------------------------------------------
# What the helper reports
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ready:
    """The helper has started and waits for ``storage`` and ``start``."""

    protocol: int


@dataclass(frozen=True)
class StorageOp:
    """One change matter.js made to its storage."""

    namespace: str                          # "bridge" or "controller"
    action: str                             # "set", "delete" or "clear"
    context: str                            # storage context, e.g. "root.parts.aggregator"
    key: str | None                         # None for "clear"
    value: str | None                       # JSON text as matter.js wrote it; only for "set"


@dataclass(frozen=True)
class StorageOps:
    """Storage changes to persist, in order."""

    ops: list[StorageOp]


@dataclass(frozen=True)
class Started:
    """Both Matter nodes are online."""


@dataclass(frozen=True)
class Pairing:
    """How a controller commissions the bridge."""

    manual: str                             # the eleven-digit code
    qr: str                                 # payload of the QR code, "MT:…"
    rows: list[str]                         # the QR code as rows of "0" and "1" (1 = dark module)
    commissioned: bool                      # at least one controller has commissioned the bridge


@dataclass(frozen=True)
class Fabric:
    """One controller (fabric) the bridge belongs to."""

    index: int
    label: str
    vendor: int


@dataclass(frozen=True)
class Fabrics:
    """All controllers the bridge belongs to."""

    fabrics: list[Fabric]


@dataclass(frozen=True)
class Command:
    """A controller wants a shared device switched. The plugin answers with ``reply``."""

    id: int
    number: int                             # endpoint number of the shared device
    state: dict[str, Any]                   # wanted values in Matter units
    fabric: int | None                      # index of the controller that asked, if known


@dataclass(frozen=True)
class RemoteEndpoint:
    """One endpoint of a device the controller commissioned."""

    endpoint: int
    types: list[int]                        # Matter device type ids
    name: str
    reachable: bool
    state: dict[str, Any]


@dataclass(frozen=True)
class RemoteNode:
    """A device the controller commissioned, with all its endpoints."""

    node: str                               # local id of the node in matter.js
    online: bool
    name: str
    vendor: str
    product: str
    serial: str
    endpoints: list[RemoteEndpoint] = field(default_factory=list)


@dataclass(frozen=True)
class NodeRemoved:
    """The controller no longer knows this device."""

    node: str


@dataclass(frozen=True)
class Result:
    """Outcome of ``pair``, ``unpair`` or ``invoke``."""

    id: int
    ok: bool
    error: str = ""                         # short code, never free text
    node: str = ""                          # for a successful ``pair``
    attestation: list[str] = field(default_factory=list)    # findings of the device attestation ("level:check")
    clean: bool = True                      # for ``unpair``: the device confirmed that it left


@dataclass(frozen=True)
class HelperProblem:
    """The helper reports a problem as a code."""

    code: str


HelperMessage = (Ready | StorageOps | Started | Pairing | Fabrics | Command | RemoteNode | NodeRemoved | Result
                 | HelperProblem)


# ---------------------------------------------------------------------------
# Checking foreign values
# ---------------------------------------------------------------------------

def _integer(value: Any, minimum: int, maximum: int, name: str) -> int:
    """Return ``value`` if it is an integer in range, else raise ProtocolError naming the field."""
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ProtocolError(f"{name}: number out of range")
    return value


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    """Return ``value`` as a short single-line text; anything that is not text becomes empty."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _flag(value: Any, name: str) -> bool:
    """Return ``value`` if it is a boolean, else raise ProtocolError naming the field."""
    if not isinstance(value, bool):
        raise ProtocolError(f"{name}: not a boolean")
    return value


def checked_state(raw: Any, name: str = "state") -> dict[str, Any]:
    """Return the known keys of a state object with checked values. Unknown keys and nulls are dropped.

    Raises ProtocolError when the object is not a mapping or a known key has a wrong type or range.
    """
    if not isinstance(raw, Mapping):
        raise ProtocolError(f"{name}: not an object")
    state: dict[str, Any] = {}
    for key, allowed in _STATE_RANGES.items():
        value = raw.get(key)
        if value is None:
            continue
        if allowed is None:
            state[key] = _flag(value, f"{name}.{key}")
        elif isinstance(allowed[0], str):
            if value not in allowed:
                raise ProtocolError(f"{name}.{key}: unknown word")
            state[key] = value
        else:
            state[key] = _integer(value, allowed[0], allowed[1], f"{name}.{key}")
    return state


def _storage_op(raw: Any) -> StorageOp:
    """Check one storage operation ``[namespace, action, context, key, value]``."""
    if not isinstance(raw, list) or len(raw) != 5:
        raise ProtocolError("storage: malformed operation")
    namespace, action, context, key, value = raw
    if namespace not in NAMESPACES or action not in ("set", "delete", "clear"):
        raise ProtocolError("storage: unknown namespace or action")
    if not isinstance(context, str) or len(context) > MAX_CONTEXT_LENGTH:
        raise ProtocolError("storage: context too long")
    if action == "clear":
        return StorageOp(namespace, action, context, None, None)
    if not isinstance(key, str) or not key or len(key) > MAX_KEY_LENGTH:
        raise ProtocolError("storage: key missing or too long")
    if action == "delete":
        return StorageOp(namespace, action, context, key, None)
    if not isinstance(value, str) or len(value) > MAX_VALUE_LENGTH:
        raise ProtocolError("storage: value missing or too long")
    return StorageOp(namespace, action, context, key, value)


def _pairing(raw: Mapping[str, Any]) -> Pairing:
    """Check the pairing codes and the QR raster."""
    manual, qr, rows = raw.get("manual"), raw.get("qr"), raw.get("rows")
    if not isinstance(manual, str) or not _MANUAL_CODE.match(manual):
        raise ProtocolError("pairing.manual: not a pairing code")
    if not isinstance(qr, str) or not _QR_CODE.match(qr):
        raise ProtocolError("pairing.qr: not a QR payload")
    if not isinstance(rows, list) or len(rows) > MAX_QR_SIZE:
        raise ProtocolError("pairing.rows: too many rows")
    for row in rows:
        if not isinstance(row, str) or len(row) != len(rows) or not _QR_ROW.match(row):
            raise ProtocolError("pairing.rows: not a square raster")
    return Pairing(manual, qr, list(rows), _flag(raw.get("commissioned"), "pairing.commissioned"))


def _fabrics(raw: Mapping[str, Any]) -> Fabrics:
    """Check the list of controllers."""
    entries = raw.get("fabrics")
    if not isinstance(entries, list) or len(entries) > MAX_FABRICS:
        raise ProtocolError("fabrics: not a short list")
    fabrics = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ProtocolError("fabrics: malformed entry")
        fabrics.append(Fabric(index=_integer(entry.get("index"), 1, 254, "fabrics.index"), label=_text(entry.get("label"), 32),
                              vendor=_integer(entry.get("vendor"), 0, 65535, "fabrics.vendor")))
    return Fabrics(fabrics)


def _node_id(value: Any, name: str) -> str:
    """Return ``value`` if it looks like a node id of matter.js, else raise ProtocolError."""
    if not isinstance(value, str) or not _NODE_ID.match(value):
        raise ProtocolError(f"{name}: not a node id")
    return value


def _remote_node(raw: Mapping[str, Any]) -> RemoteNode:
    """Check the description of a device the controller commissioned."""
    entries = raw.get("endpoints")
    if not isinstance(entries, list) or len(entries) > MAX_REMOTE_ENDPOINTS:
        raise ProtocolError("node.endpoints: not a list")
    endpoints = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ProtocolError("node.endpoints: malformed entry")
        types = entry.get("types")
        if not isinstance(types, list) or len(types) > 8:
            raise ProtocolError("node.endpoints.types: not a short list")
        endpoints.append(RemoteEndpoint(
            endpoint=_integer(entry.get("endpoint"), 0, MAX_ENDPOINT_NUMBER, "node.endpoints.endpoint"),
            types=[_integer(type_id, 0, 0xFFFFFFFF, "node.endpoints.types") for type_id in types],
            name=_text(entry.get("name")),
            reachable=_flag(entry.get("reachable"), "node.endpoints.reachable"),
            state=checked_state(entry.get("state"), "node.endpoints.state")))
    return RemoteNode(node=_node_id(raw.get("node"), "node.node"), online=_flag(raw.get("online"), "node.online"),
                      name=_text(raw.get("name")), vendor=_text(raw.get("vendor")), product=_text(raw.get("product")),
                      serial=_text(raw.get("serial")), endpoints=endpoints)


def _result(raw: Mapping[str, Any]) -> Result:
    """Check the outcome of a request."""
    error = raw.get("error", "")
    if not isinstance(error, str) or (error and not _ERROR_CODE.match(error)):
        raise ProtocolError("result.error: not a code")
    findings = raw.get("attestation", [])
    if not isinstance(findings, list) or len(findings) > 10 or not all(isinstance(finding, str) and _ERROR_CODE.match(finding)
                                                                        for finding in findings):
        raise ProtocolError("result.attestation: not a list of codes")
    node = raw.get("node", "")
    return Result(id=_integer(raw.get("id"), 0, MAX_ID, "result.id"), ok=_flag(raw.get("ok"), "result.ok"), error=error,
                  node=_node_id(node, "result.node") if node else "", attestation=list(findings),
                  clean=raw.get("clean") is not False)


def parse(raw: Any) -> HelperMessage:
    """Turn one message of the helper into a typed object.

    Raises ProtocolError for an unknown type or a field that is missing, of the wrong type, out of
    range or too long. The error text never contains what the helper sent.
    """
    if not isinstance(raw, Mapping):
        raise ProtocolError("message: not an object")
    kind = raw.get("type")
    if kind == "ready":
        return Ready(_integer(raw.get("protocol"), 0, 1000, "ready.protocol"))
    if kind == "storage":
        ops = raw.get("ops")
        if not isinstance(ops, list) or len(ops) > MAX_STORAGE_OPS:
            raise ProtocolError("storage: not a list")
        return StorageOps([_storage_op(op) for op in ops])
    if kind == "started":
        return Started()
    if kind == "pairing":
        return _pairing(raw)
    if kind == "fabrics":
        return _fabrics(raw)
    if kind == "command":
        fabric = raw.get("fabric")
        return Command(id=_integer(raw.get("id"), 0, MAX_ID, "command.id"),
                       number=_integer(raw.get("number"), 1, MAX_ENDPOINT_NUMBER, "command.number"),
                       state=checked_state(raw.get("state"), "command.state"),
                       fabric=None if fabric is None else _integer(fabric, 0, 254, "command.fabric"))
    if kind == "node":
        return _remote_node(raw)
    if kind == "node_removed":
        return NodeRemoved(_node_id(raw.get("node"), "node_removed.node"))
    if kind == "result":
        return _result(raw)
    if kind == "error":
        code = raw.get("code")
        if not isinstance(code, str) or not _ERROR_CODE.match(code):
            raise ProtocolError("error.code: not a code")
        return HelperProblem(code)
    raise ProtocolError("message: unknown type")


# ---------------------------------------------------------------------------
# What the plugin sends
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BridgeOptions:
    """Identity of the bridge node."""

    port: int
    passcode: int
    discriminator: int
    name: str
    serial: str
    vendor_id: int
    product_id: int


@dataclass(frozen=True)
class ControllerOptions:
    """Identity of the controller node."""

    port: int
    serial: str
    vendor_id: int
    product_id: int
    label: str = "ComfyOS"
    enabled: bool = True


@dataclass(frozen=True)
class EndpointEntry:
    """One shared device as the helper needs it: endpoint number, Matter kind and current values."""

    number: int
    kind: str
    name: str
    serial: str
    reachable: bool
    locked: bool
    state: dict[str, Any]


def storage_messages(stored: Mapping[str, Mapping[str, Mapping[str, str]]]) -> list[dict[str, Any]]:
    """Build the ``storage`` messages that hand the persisted values back to a freshly started helper.

    ``stored`` is ``{namespace: {context: {key: json text}}}``. Large stores are split so that no
    line comes near the limit of one line.
    """
    messages: list[dict[str, Any]] = []
    for namespace, contexts in stored.items():
        chunk: dict[str, dict[str, str]] = {}
        size = 0
        for context, values in contexts.items():
            for key, value in values.items():
                if size and size + len(value) > STORAGE_CHUNK_BYTES:
                    messages.append({"type": "storage", "namespace": namespace, "values": chunk})
                    chunk, size = {}, 0
                chunk.setdefault(context, {})[key] = value
                size += len(value) + len(context) + len(key)
        if chunk:
            messages.append({"type": "storage", "namespace": namespace, "values": chunk})
    return messages


def endpoint_dict(entry: EndpointEntry) -> dict[str, Any]:
    """Return a shared device in wire form."""
    return {"number": entry.number, "kind": entry.kind, "name": entry.name, "serial": entry.serial,
            "reachable": entry.reachable, "locked": entry.locked, "state": dict(entry.state)}


def start_message(bridge: BridgeOptions, controller: ControllerOptions, endpoints: Iterable[EndpointEntry]) -> dict[str, Any]:
    """Build ``start``: identity of both nodes and the devices shared right now."""
    return {
        "type": "start",
        "bridge": {"port": bridge.port, "passcode": bridge.passcode, "discriminator": bridge.discriminator,
                   "name": bridge.name, "serial": bridge.serial, "vendor_id": bridge.vendor_id, "product_id": bridge.product_id},
        "controller": {"enabled": controller.enabled, "port": controller.port, "label": controller.label,
                       "serial": controller.serial, "vendor_id": controller.vendor_id, "product_id": controller.product_id},
        "endpoints": [endpoint_dict(entry) for entry in endpoints],
    }


def endpoints_message(endpoints: Iterable[EndpointEntry]) -> dict[str, Any]:
    """Build ``endpoints``: the complete list of shared devices; the helper adds and removes to match."""
    return {"type": "endpoints", "endpoints": [endpoint_dict(entry) for entry in endpoints]}


def update_message(number: int, state: Mapping[str, Any], reachable: bool, locked: bool) -> dict[str, Any]:
    """Build ``update``: new values of one shared device."""
    return {"type": "update", "number": number, "state": dict(state), "reachable": reachable, "locked": locked}


def commissioning_message(is_open: bool) -> dict[str, Any]:
    """Build ``commissioning``: open or close the commissioning window of the bridge."""
    return {"type": "commissioning", "open": bool(is_open)}


def remove_fabric_message(index: int) -> dict[str, Any]:
    """Build ``remove_fabric``: remove one controller from the bridge."""
    return {"type": "remove_fabric", "index": int(index)}


def reply_message(command_id: int, status: str) -> dict[str, Any]:
    """Build ``reply``: the answer to a controller's command – "ok", "locked" or "error"."""
    if status not in REPLY_STATUSES:
        raise ValueError("unknown reply status")
    return {"type": "reply", "id": command_id, "status": status}


def clean_pairing_code(text: str) -> str:
    """Return a pairing code a user typed in canonical form, or raise ProtocolError.

    Accepted: the eleven- or 21-digit manual code (spaces and dashes are ignored) and the payload
    of a QR code ("MT:…").
    """
    raw = str(text or "").strip()
    if raw.upper().startswith("MT:"):
        code = raw.upper()
        if not _QR_CODE.match(code):
            raise ProtocolError("not a QR payload")
        return code
    digits = re.sub(r"[\s-]", "", raw)
    if not _MANUAL_CODE.match(digits):
        raise ProtocolError("not a pairing code")
    return digits


def pair_message(request_id: int, code: str) -> dict[str, Any]:
    """Build ``pair``: commission a device with a pairing code (see :func:`clean_pairing_code`)."""
    return {"type": "pair", "id": request_id, "code": clean_pairing_code(code)}


def unpair_message(request_id: int, node: str) -> dict[str, Any]:
    """Build ``unpair``: leave the fabric of a device and forget it."""
    return {"type": "unpair", "id": request_id, "node": node}


def invoke_message(request_id: int, node: str, endpoint: int, state: Mapping[str, Any]) -> dict[str, Any]:
    """Build ``invoke``: switch an endpoint of a commissioned device (values in Matter units)."""
    return {"type": "invoke", "id": request_id, "node": node, "endpoint": endpoint, "state": checked_state(state)}
