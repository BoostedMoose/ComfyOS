"""A small MQTT broker that lives in memory, for tests of the MQTT plugin.

It speaks just enough MQTT 3.1.1 to stand in for a real broker: it accepts connections,
checks user name and password if asked to, stores retained messages, delivers messages to
matching subscriptions and answers keep-alive questions. Tests use it like this::

    broker = FakeBroker()
    context = FakeContext("mqtt", stream_handler=broker.connect, secrets=["password"])
    ...
    broker.publish("zigbee2mqtt/lamp", '{"state": "ON"}')          # as if a device had sent it
    assert broker.received[-1].topic == "zigbee2mqtt/lamp/set"     # what the plugin published

It is not a test itself (the file name does not start with ``test_``).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from testing import memory_stream_pair

from mqtt import protocol
from mqtt.protocol import Message

MAX_PACKET_BYTES = 256 * 1024


@dataclass
class Session:
    """One connected client as the broker sees it."""

    client_id: str
    writer: Any
    will: Message | None
    topic_filters: list[str] = field(default_factory=list)
    said_goodbye: bool = False              # the client sent DISCONNECT, so its last will is not published


class FakeBroker:
    """An in-memory MQTT broker. ``connect`` is the stream handler for ``FakeContext``."""

    def __init__(self, user_name: str | None = None, password: str | None = None) -> None:
        """Create a broker; with a user name and password it accepts only clients that send exactly these."""
        self._user_name = user_name
        self._password = password
        self.sessions: list[Session] = []
        self.retained: dict[str, bytes] = {}
        self.received: list[Message] = []           # every message a client published
        self.connects: list[protocol.ConnectRequest] = []
        self.is_down = False                        # True: new connections are refused

    # -- what tests call ----------------------------------------------------

    async def connect(self, host: str, port: int) -> tuple[Any, Any]:
        """Open a connection to the broker and return the client's end of it."""
        if self.is_down:
            raise ConnectionRefusedError("broker is down")
        client_end, broker_end = memory_stream_pair()
        asyncio.create_task(self._serve(*broker_end))
        return client_end

    def publish(self, topic: str, payload: str | bytes, retain: bool = False) -> None:
        """Publish a message as if a device had sent it."""
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        self._distribute(Message(topic=topic, payload=data, retain=retain))

    def drop_connections(self) -> None:
        """Cut every connection without a goodbye, like a broker that crashes."""
        for session in list(self.sessions):
            session.writer.close()

    def published_to(self, topic: str) -> list[str]:
        """Return the payloads clients published to ``topic``, as text."""
        return [message.payload.decode("utf-8") for message in self.received if message.topic == topic]

    # -- the broker itself --------------------------------------------------

    def _distribute(self, message: Message) -> None:
        """Store or delete the retained message and forward the message to every session whose subscription matches."""
        if message.retain:
            if message.payload:
                self.retained[message.topic] = message.payload
            else:
                self.retained.pop(message.topic, None)      # an empty retained message deletes the stored one
        for session in self.sessions:
            if any(protocol.topic_matches(topic_filter, message.topic) for topic_filter in session.topic_filters):
                session.writer.write(protocol.build_publish(Message(topic=message.topic, payload=message.payload)))

    async def _serve(self, reader: asyncio.StreamReader, writer: Any) -> None:
        """Serve one client connection: check the login, then handle packets until the client leaves (its last will is
        sent if it vanishes)."""
        session: Session | None = None
        try:
            request = protocol.parse_connect(await protocol.read_packet(reader, MAX_PACKET_BYTES))
            self.connects.append(request)
            if self._user_name is not None and (request.user_name, request.password) != (self._user_name, self._password):
                writer.write(protocol.build_connack(protocol.CONNACK_BAD_CREDENTIALS))
                return
            writer.write(protocol.build_connack(protocol.CONNACK_ACCEPTED))
            session = Session(client_id=request.client_id, writer=writer, will=request.will)
            self.sessions.append(session)
            while True:
                packet = await protocol.read_packet(reader, MAX_PACKET_BYTES)
                self._handle(session, packet)
                if session.said_goodbye:
                    return
        except (asyncio.IncompleteReadError, protocol.ProtocolError):
            pass
        finally:
            writer.close()
            if session is not None:
                self.sessions.remove(session)
                if session.will is not None and not session.said_goodbye:
                    self._distribute(session.will)

    def _handle(self, session: Session, packet: protocol.Packet) -> None:
        """React to one packet of a client: subscribe (with the retained messages), publish, ping or disconnect."""
        if packet.packet_type == protocol.SUBSCRIBE:
            packet_id, topic_filters = protocol.parse_subscribe(packet)
            session.topic_filters.extend(topic_filters)
            session.writer.write(protocol.build_suback(packet_id, [0] * len(topic_filters)))
            for topic, payload in list(self.retained.items()):
                if any(protocol.topic_matches(topic_filter, topic) for topic_filter in topic_filters):
                    session.writer.write(protocol.build_publish(Message(topic=topic, payload=payload, retain=True)))
        elif packet.packet_type == protocol.PUBLISH:
            message = protocol.parse_publish(packet)
            self.received.append(message)
            if message.qos == 1 and message.packet_id is not None:
                session.writer.write(protocol.build_puback(message.packet_id))
            self._distribute(message)
        elif packet.packet_type == protocol.PINGREQ:
            session.writer.write(protocol.build_pingresp())
        elif packet.packet_type == protocol.DISCONNECT:
            session.said_goodbye = True
