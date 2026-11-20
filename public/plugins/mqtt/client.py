"""MQTT client: keeps one connection to the broker alive and passes messages in both directions.

The client never opens a socket itself. It asks ComfyOS for the connection
(``context.open_stream``), so the broker has to be declared in manifest.json and
lies in the home network.

One session looks like this:

1. open the connection, send CONNECT, wait for CONNACK
2. announce ``comfyos/status = online`` (the last will sets it to ``offline``)
3. subscribe to every topic filter that was requested with :meth:`MqttClient.subscribe`
4. read packets until the connection ends; a second task sends PINGREQ as keep-alive

When a session ends, :meth:`MqttClient.run` waits (1 s, 2 s, 4 s … up to 60 s) and
starts the next one. Subscriptions are kept and sent again after every reconnect.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from plugin_api import DeviceUnreachable, PluginError

from . import protocol
from .protocol import Message, Packet, ProtocolError

KEEP_ALIVE_SECONDS = 60                     # announced in CONNECT; the broker drops us after 1.5 × this silence
PING_INTERVAL_SECONDS = 30.0
SILENCE_LIMIT_SECONDS = 90.0                # no packet for this long: the connection is dead
CONNECT_TIMEOUT_SECONDS = 8.0
ACK_TIMEOUT_SECONDS = 5.0                   # how long publish() waits for PUBACK (quality of service 1)
FIRST_RETRY_SECONDS = 1.0
LONGEST_RETRY_SECONDS = 60.0
# Larger packets end the session. Device states are far smaller; the device list of a large
# Zigbee2MQTT network (bridge/devices) is the one message that reaches a megabyte.
MAX_PACKET_BYTES = 4 * 1024 * 1024
SUBSCRIBE_BATCH_SIZE = 50                   # topic filters per SUBSCRIBE packet

STATUS_TOPIC = "comfyos/status"
STATUS_ONLINE = b"online"
STATUS_OFFLINE = b"offline"

CONNACK_MESSAGES = {
    1: "Der Broker unterstützt MQTT 3.1.1 nicht.",
    2: "Der Broker lehnt die Client-Kennung ab.",
    3: "Der Broker ist gerade nicht verfügbar.",
    protocol.CONNACK_BAD_CREDENTIALS: "Anmeldung abgelehnt: Benutzer oder Passwort stimmen nicht.",
    protocol.CONNACK_NOT_AUTHORIZED: "Anmeldung abgelehnt: Der Broker verlangt Benutzer und Passwort.",
}

OpenStream = Callable[[], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]
MessageHandler = Callable[[Message], None]
ConnectionHandler = Callable[[bool], None]


@dataclass(frozen=True)
class Credentials:
    """How the client identifies itself to the broker."""

    client_id: str
    user_name: str | None = None
    password: str | None = None


class MqttClient:
    """One connection to an MQTT broker with automatic reconnect."""

    def __init__(self, open_stream: OpenStream, credentials: Credentials, on_message: MessageHandler,
                 on_connection_change: ConnectionHandler, log: logging.Logger) -> None:
        """Prepare a client; nothing is sent until ``run()``. ``on_message`` receives every message,
        ``on_connection_change`` every connect and disconnect."""
        self._open_stream = open_stream
        self._credentials = credentials
        self._on_message = on_message
        self._on_connection_change = on_connection_change
        self._log = log

        self._writer: asyncio.StreamWriter | None = None
        self._topic_filters: list[str] = []                         # everything to subscribe to, in request order
        self._waiting_for_ack: dict[int, asyncio.Future[None]] = {}  # packet id -> publish() waiting for PUBACK
        self._next_packet_id = 1
        self._last_packet_at = 0.0                                  # time.monotonic() of the last packet from the broker
        self._was_connected_this_session = False                    # the last session got past CONNACK
        self.last_error = ""                                        # why the last session ended (shown in the tile)

    # -- state ----------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True while a session is established (CONNACK received, connection open)."""
        return self._writer is not None

    # -- public interface -----------------------------------------------------

    async def run(self) -> None:
        """Keep the connection alive until the task is cancelled."""
        retry_seconds = FIRST_RETRY_SECONDS
        while True:
            try:
                await self._run_session()
                self.last_error = "Der Broker hat die Verbindung beendet."
            except PluginError as error:
                self.last_error = str(error)
            except (ProtocolError, asyncio.IncompleteReadError, OSError, asyncio.TimeoutError) as error:
                self.last_error = f"Verbindung zum Broker unterbrochen ({type(error).__name__})."
            if self._was_connected_this_session:
                retry_seconds = FIRST_RETRY_SECONDS             # it worked a moment ago: try again quickly
            self._log.info("MQTT session ended: %s; next try in %.0f s", self.last_error, retry_seconds)
            await asyncio.sleep(retry_seconds)
            retry_seconds = min(LONGEST_RETRY_SECONDS, retry_seconds * 2)

    def subscribe(self, topic_filters: list[str]) -> None:
        """Remember topic filters and subscribe now if connected. Known filters are skipped."""
        new_filters = [topic_filter for topic_filter in topic_filters if topic_filter not in self._topic_filters]
        if not new_filters:
            return
        self._topic_filters.extend(new_filters)
        if self._writer is not None:
            self._send_subscriptions(self._writer, new_filters)

    async def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        """Publish a message. With ``qos=1`` wait until the broker confirms it.

        Raises DeviceUnreachable when there is no connection or the confirmation does not arrive.
        """
        writer = self._writer
        if writer is None:
            raise DeviceUnreachable("MQTT-Broker ist nicht verbunden.")
        if not protocol.is_publish_topic(topic):
            raise PluginError("Ungültiges MQTT-Topic.")
        if qos == 0:
            writer.write(protocol.build_publish(Message(topic=topic, payload=payload, retain=retain)))
            await writer.drain()
            return
        packet_id = self._take_packet_id()
        acknowledged: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiting_for_ack[packet_id] = acknowledged
        try:
            writer.write(protocol.build_publish(Message(topic=topic, payload=payload, qos=1, retain=retain, packet_id=packet_id)))
            await writer.drain()
            await asyncio.wait_for(acknowledged, ACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise DeviceUnreachable("Der MQTT-Broker hat den Befehl nicht bestätigt.")
        finally:
            self._waiting_for_ack.pop(packet_id, None)

    async def close(self) -> None:
        """Say goodbye to the broker (so it does not publish the last will) and close the connection."""
        writer = self._writer
        if writer is None:
            return
        try:
            writer.write(protocol.build_publish(Message(topic=STATUS_TOPIC, payload=STATUS_OFFLINE, retain=True)))
            writer.write(protocol.build_disconnect())
            await writer.drain()
        except OSError:
            pass                                                # the connection is already gone
        writer.close()

    # -- one session ------------------------------------------------------------

    async def _run_session(self) -> None:
        """Connect, subscribe and read until the connection ends. Raises the reason."""
        self._was_connected_this_session = False
        reader, writer = await self._open_stream()
        keep_alive_task: asyncio.Task[None] | None = None
        try:
            await self._handshake(reader, writer)
            self._writer = writer
            self._was_connected_this_session = True
            self._last_packet_at = time.monotonic()
            writer.write(protocol.build_publish(Message(topic=STATUS_TOPIC, payload=STATUS_ONLINE, retain=True)))
            self._send_subscriptions(writer, self._topic_filters)
            await writer.drain()
            self._on_connection_change(True)
            keep_alive_task = asyncio.create_task(self._keep_alive(writer))
            while True:
                packet = await protocol.read_packet(reader, MAX_PACKET_BYTES)
                self._last_packet_at = time.monotonic()
                self._handle_packet(packet, writer)
        finally:
            if keep_alive_task is not None:
                keep_alive_task.cancel()
            was_connected = self._writer is not None
            self._writer = None
            writer.close()
            for acknowledged in self._waiting_for_ack.values():
                if not acknowledged.done():
                    acknowledged.set_exception(DeviceUnreachable("MQTT-Broker ist nicht verbunden."))
            if was_connected:
                self._on_connection_change(False)

    async def _handshake(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Send CONNECT and wait for the broker's verdict. Raises PluginError when it refuses."""
        last_will = Message(topic=STATUS_TOPIC, payload=STATUS_OFFLINE, retain=True)
        writer.write(protocol.build_connect(self._credentials.client_id, KEEP_ALIVE_SECONDS, self._credentials.user_name,
                                            self._credentials.password, last_will))
        await writer.drain()
        answer = await asyncio.wait_for(protocol.read_packet(reader, MAX_PACKET_BYTES), CONNECT_TIMEOUT_SECONDS)
        return_code = protocol.parse_connack(answer)
        if return_code != protocol.CONNACK_ACCEPTED:
            raise PluginError(CONNACK_MESSAGES.get(return_code, f"Der Broker lehnt die Verbindung ab (Code {return_code})."))

    def _send_subscriptions(self, writer: asyncio.StreamWriter, topic_filters: list[str]) -> None:
        """Send the subscriptions in batches, so no single packet grows too large."""
        for start in range(0, len(topic_filters), SUBSCRIBE_BATCH_SIZE):
            batch = topic_filters[start:start + SUBSCRIBE_BATCH_SIZE]
            writer.write(protocol.build_subscribe(self._take_packet_id(), batch))

    def _handle_packet(self, packet: Packet, writer: asyncio.StreamWriter) -> None:
        """Act on one packet from the broker: deliver a message (and acknowledge it), resolve a waiting publish, or
        note an answer to a ping."""
        if packet.packet_type == protocol.PUBLISH:
            message = protocol.parse_publish(packet)
            if message.qos == 1 and message.packet_id is not None:
                writer.write(protocol.build_puback(message.packet_id))
            try:
                self._on_message(message)
            except Exception as error:                           # one odd message must not end the session
                self._log.warning("MQTT message on a topic could not be handled (%s)", type(error).__name__)
        elif packet.packet_type == protocol.PUBACK:
            acknowledged = self._waiting_for_ack.get(protocol.parse_packet_id(packet))
            if acknowledged is not None and not acknowledged.done():
                acknowledged.set_result(None)
        elif packet.packet_type == protocol.SUBACK:
            _, return_codes = protocol.parse_suback(packet)
            if protocol.SUBACK_FAILURE in return_codes:
                self._log.warning("The MQTT broker refused %d subscription(s)", return_codes.count(protocol.SUBACK_FAILURE))
        elif packet.packet_type != protocol.PINGRESP:
            raise ProtocolError("Unexpected packet from the broker.")

    async def _keep_alive(self, writer: asyncio.StreamWriter) -> None:
        """Ask the broker regularly whether it is still there; close the connection when it stays silent."""
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            if time.monotonic() - self._last_packet_at > SILENCE_LIMIT_SECONDS:
                writer.close()                                  # ends read_packet in _run_session
                return
            writer.write(protocol.build_pingreq())

    def _take_packet_id(self) -> int:
        """Return the next packet id; ids wrap around at the protocol's maximum and are never zero."""
        packet_id = self._next_packet_id
        self._next_packet_id = packet_id % protocol.MAX_PACKET_ID + 1
        return packet_id
