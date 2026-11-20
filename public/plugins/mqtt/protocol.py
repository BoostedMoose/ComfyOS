"""MQTT 3.1.1 wire format: build and read control packets. Pure translation, no network access.

Only the part of the protocol ComfyOS needs is implemented:

* CONNECT / CONNACK with user name, password and a last will
* SUBSCRIBE / SUBACK
* PUBLISH with quality of service 0 and 1, PUBACK
* PINGREQ / PINGRESP, DISCONNECT

Quality of service 2 and MQTT 5 are left out on purpose: Home Assistant discovery,
Zigbee2MQTT and ESPHome work without them.

Every packet starts with a fixed header: one byte with the packet type (upper four bits)
and flags (lower four bits), followed by the "remaining length" in a variable-length
encoding of one to four bytes. Texts are UTF-8 with a two-byte length in front.

The module also reads the packets a *broker* receives (``parse_connect``,
``parse_subscribe``). ComfyOS never acts as a broker; the test broker of this plugin uses them.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

PROTOCOL_NAME = "MQTT"
PROTOCOL_LEVEL = 4                          # the protocol level byte of MQTT 3.1.1
MAX_REMAINING_LENGTH = 268_435_455          # largest number the length field can carry
MAX_PACKET_ID = 65_535
MAX_TOPIC_BYTES = 65_535

# Control packet types (upper four bits of the first byte)
CONNECT = 1
CONNACK = 2
PUBLISH = 3
PUBACK = 4
SUBSCRIBE = 8
SUBACK = 9
PINGREQ = 12
PINGRESP = 13
DISCONNECT = 14

CONNACK_ACCEPTED = 0
CONNACK_BAD_CREDENTIALS = 4
CONNACK_NOT_AUTHORIZED = 5
SUBACK_FAILURE = 0x80                       # return code of a subscription the broker refused

# Flag bits of the CONNECT packet
_CONNECT_CLEAN_SESSION = 0x02
_CONNECT_WILL = 0x04
_CONNECT_WILL_RETAIN = 0x20
_CONNECT_PASSWORD = 0x40
_CONNECT_USER_NAME = 0x80

# Flag bits of the PUBLISH packet
_PUBLISH_RETAIN = 0x01
_PUBLISH_DUPLICATE = 0x08


class ProtocolError(Exception):
    """The peer sent something that is not valid MQTT 3.1.1, or a packet that is too large."""


@dataclass(frozen=True)
class Packet:
    """One control packet as it came off the wire: type, flags and the undecoded rest."""

    packet_type: int
    flags: int
    body: bytes


@dataclass(frozen=True)
class Message:
    """An application message: what PUBLISH carries."""

    topic: str
    payload: bytes
    qos: int = 0
    retain: bool = False
    packet_id: int | None = None            # only set for quality of service 1
    duplicate: bool = False                 # the sender repeats a message it got no PUBACK for


@dataclass(frozen=True)
class ConnectRequest:
    """What a client sends in CONNECT (read by the test broker)."""

    client_id: str
    keep_alive_seconds: int
    user_name: str | None
    password: str | None
    will: Message | None


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def encode_remaining_length(length: int) -> bytes:
    """Encode a length in MQTT's variable-length format: seven bits per byte, top bit = "more follows"."""
    if not 0 <= length <= MAX_REMAINING_LENGTH:
        raise ProtocolError("Packet is too large for MQTT.")
    encoded = bytearray()
    while True:
        seven_bits = length % 128
        length //= 128
        encoded.append(seven_bits | 0x80 if length else seven_bits)
        if not length:
            return bytes(encoded)


def encode_string(text: str) -> bytes:
    """Encode a text as UTF-8 with its two-byte length in front."""
    data = text.encode("utf-8")
    if len(data) > MAX_TOPIC_BYTES:
        raise ProtocolError("Text is too long for MQTT.")
    return len(data).to_bytes(2, "big") + data


def _packet(packet_type: int, flags: int, body: bytes) -> bytes:
    """Assemble a packet from its type, flags and body."""
    return bytes([packet_type << 4 | flags]) + encode_remaining_length(len(body)) + body


class _BodyReader:
    """Walks through the body of a packet and raises ProtocolError when it ends too early."""

    def __init__(self, body: bytes) -> None:
        """Read the body from its first byte."""
        self._body = body
        self._position = 0

    def take(self, count: int) -> bytes:
        """Return the next ``count`` bytes."""
        end = self._position + count
        if end > len(self._body):
            raise ProtocolError("Packet ends too early.")
        chunk = self._body[self._position:end]
        self._position = end
        return chunk

    def byte(self) -> int:
        """Return the next byte as a number."""
        return self.take(1)[0]

    def number(self) -> int:
        """Return the next two bytes as a number (big endian)."""
        return int.from_bytes(self.take(2), "big")

    def data(self) -> bytes:
        """Return the next length-prefixed block of bytes."""
        return self.take(self.number())

    def string(self) -> str:
        """Return the next length-prefixed UTF-8 text."""
        try:
            return self.data().decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("Text is not valid UTF-8.")

    def rest(self) -> bytes:
        """Return everything that is left."""
        return self.take(len(self._body) - self._position)

    @property
    def at_end(self) -> bool:
        """True when the whole body has been read."""
        return self._position >= len(self._body)


# ---------------------------------------------------------------------------
# Packets a client sends
# ---------------------------------------------------------------------------

def build_connect(client_id: str, keep_alive_seconds: int, user_name: str | None = None,
                  password: str | None = None, will: Message | None = None) -> bytes:
    """Build CONNECT for a clean session.

    ``will`` is the message the broker publishes for us when the connection breaks.
    A password without a user name is not allowed by MQTT 3.1.1 and is left out.
    """
    flags = _CONNECT_CLEAN_SESSION
    payload = encode_string(client_id)
    if will is not None:
        flags |= _CONNECT_WILL | (will.qos << 3)
        if will.retain:
            flags |= _CONNECT_WILL_RETAIN
        payload += encode_string(will.topic) + len(will.payload).to_bytes(2, "big") + will.payload
    if user_name:
        flags |= _CONNECT_USER_NAME
        payload += encode_string(user_name)
        if password:
            flags |= _CONNECT_PASSWORD
            payload += encode_string(password)
    header = encode_string(PROTOCOL_NAME) + bytes([PROTOCOL_LEVEL, flags]) + keep_alive_seconds.to_bytes(2, "big")
    return _packet(CONNECT, 0, header + payload)


def build_subscribe(packet_id: int, topic_filters: list[str], qos: int = 0) -> bytes:
    """Build SUBSCRIBE for one or more topic filters, all with the same quality of service."""
    body = packet_id.to_bytes(2, "big")
    for topic_filter in topic_filters:
        body += encode_string(topic_filter) + bytes([qos])
    return _packet(SUBSCRIBE, 0x02, body)       # the flags 0010 are fixed by the specification


def build_publish(message: Message) -> bytes:
    """Build PUBLISH. Quality of service 1 needs ``message.packet_id``."""
    if message.qos not in (0, 1):
        raise ProtocolError("Only quality of service 0 and 1 are supported.")
    flags = message.qos << 1
    if message.retain:
        flags |= _PUBLISH_RETAIN
    if message.duplicate:
        flags |= _PUBLISH_DUPLICATE
    body = encode_string(message.topic)
    if message.qos == 1:
        if message.packet_id is None:
            raise ProtocolError("Quality of service 1 needs a packet id.")
        body += message.packet_id.to_bytes(2, "big")
    return _packet(PUBLISH, flags, body + message.payload)


def build_puback(packet_id: int) -> bytes:
    """Build PUBACK, the receipt for a message with quality of service 1."""
    return _packet(PUBACK, 0, packet_id.to_bytes(2, "big"))


def build_pingreq() -> bytes:
    """Build PINGREQ, the keep-alive question."""
    return _packet(PINGREQ, 0, b"")


def build_disconnect() -> bytes:
    """Build DISCONNECT. After it the broker does not publish the last will."""
    return _packet(DISCONNECT, 0, b"")


# ---------------------------------------------------------------------------
# Packets a broker sends (built by the test broker)
# ---------------------------------------------------------------------------

def build_connack(return_code: int, session_present: bool = False) -> bytes:
    """Build CONNACK, the broker's answer to CONNECT."""
    return _packet(CONNACK, 0, bytes([1 if session_present else 0, return_code]))


def build_suback(packet_id: int, return_codes: list[int]) -> bytes:
    """Build SUBACK with one return code per requested topic filter."""
    return _packet(SUBACK, 0, packet_id.to_bytes(2, "big") + bytes(return_codes))


def build_pingresp() -> bytes:
    """Build PINGRESP, the keep-alive answer."""
    return _packet(PINGRESP, 0, b"")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

async def read_packet(reader: asyncio.StreamReader, max_packet_bytes: int) -> Packet:
    """Read exactly one control packet from ``reader``.

    Raises ProtocolError for a malformed length or a packet larger than ``max_packet_bytes``,
    and ``asyncio.IncompleteReadError`` when the connection ends.
    """
    first_byte = (await reader.readexactly(1))[0]
    remaining_length = 0
    for shift in (0, 7, 14, 21):
        length_byte = (await reader.readexactly(1))[0]
        remaining_length |= (length_byte & 0x7F) << shift
        if not length_byte & 0x80:
            break
    else:
        raise ProtocolError("Length field is longer than four bytes.")
    if remaining_length > max_packet_bytes:
        raise ProtocolError("Packet is larger than the limit.")
    body = await reader.readexactly(remaining_length) if remaining_length else b""
    return Packet(packet_type=first_byte >> 4, flags=first_byte & 0x0F, body=body)


def parse_connack(packet: Packet) -> int:
    """Return the return code of CONNACK (0 = accepted)."""
    if packet.packet_type != CONNACK or len(packet.body) != 2:
        raise ProtocolError("Expected CONNACK.")
    return packet.body[1]


def parse_publish(packet: Packet) -> Message:
    """Read topic, payload and flags of a PUBLISH packet."""
    if packet.packet_type != PUBLISH:
        raise ProtocolError("Expected PUBLISH.")
    qos = (packet.flags >> 1) & 0x03
    if qos == 3:
        raise ProtocolError("Invalid quality of service.")
    body = _BodyReader(packet.body)
    topic = body.string()
    packet_id = body.number() if qos else None
    return Message(topic=topic, payload=body.rest(), qos=qos, retain=bool(packet.flags & _PUBLISH_RETAIN),
                   packet_id=packet_id, duplicate=bool(packet.flags & _PUBLISH_DUPLICATE))


def parse_packet_id(packet: Packet) -> int:
    """Return the packet id of PUBACK or SUBACK."""
    if len(packet.body) < 2:
        raise ProtocolError("Packet id is missing.")
    return int.from_bytes(packet.body[:2], "big")


def parse_suback(packet: Packet) -> tuple[int, list[int]]:
    """Return ``(packet id, return codes)`` of SUBACK; a code of 0x80 means "refused"."""
    if packet.packet_type != SUBACK:
        raise ProtocolError("Expected SUBACK.")
    return parse_packet_id(packet), list(packet.body[2:])


def parse_connect(packet: Packet) -> ConnectRequest:
    """Read a CONNECT packet (the broker's side; used by the test broker)."""
    if packet.packet_type != CONNECT:
        raise ProtocolError("Expected CONNECT.")
    body = _BodyReader(packet.body)
    if body.string() != PROTOCOL_NAME or body.byte() != PROTOCOL_LEVEL:
        raise ProtocolError("Not MQTT 3.1.1.")
    flags = body.byte()
    keep_alive_seconds = body.number()
    client_id = body.string()
    will = None
    if flags & _CONNECT_WILL:
        will_topic = body.string()
        will = Message(topic=will_topic, payload=body.data(), qos=(flags >> 3) & 0x03, retain=bool(flags & _CONNECT_WILL_RETAIN))
    user_name = body.string() if flags & _CONNECT_USER_NAME else None
    password = body.string() if flags & _CONNECT_PASSWORD else None
    return ConnectRequest(client_id=client_id, keep_alive_seconds=keep_alive_seconds, user_name=user_name,
                          password=password, will=will)


def parse_subscribe(packet: Packet) -> tuple[int, list[str]]:
    """Return ``(packet id, topic filters)`` of SUBSCRIBE (the broker's side; used by the test broker)."""
    if packet.packet_type != SUBSCRIBE:
        raise ProtocolError("Expected SUBSCRIBE.")
    body = _BodyReader(packet.body)
    packet_id = body.number()
    topic_filters = []
    while not body.at_end:
        topic_filters.append(body.string())
        body.byte()                             # requested quality of service
    return packet_id, topic_filters


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

def is_publish_topic(topic: str) -> bool:
    """True if a message may be published to ``topic``: not empty, no wildcards, no NUL character."""
    if not topic or len(topic.encode("utf-8")) > MAX_TOPIC_BYTES:
        return False
    return not any(character in topic for character in ("+", "#", "\x00"))


def topic_matches(topic_filter: str, topic: str) -> bool:
    """True if ``topic`` matches ``topic_filter``. ``+`` stands for one level, ``#`` for all remaining levels."""
    filter_levels = topic_filter.split("/")
    topic_levels = topic.split("/")
    for index, filter_level in enumerate(filter_levels):
        if filter_level == "#":
            return True
        if index >= len(topic_levels):
            return False
        if filter_level != "+" and filter_level != topic_levels[index]:
            return False
    return len(filter_levels) == len(topic_levels)
