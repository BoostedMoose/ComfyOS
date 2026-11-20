"""Live audio: the sound of a browser, passed on as one endless WAV stream to speakers in the home network.

How the sound travels
    1. A page of ComfyOS records the sound of the computer it runs on (the browser asks the user) and sends
       it in small pieces to `POST /api/audio/live/chunk` – with login, like every other request.
    2. This module keeps nothing but hands every piece to whoever listens right now.
    3. A speaker fetches `GET /stream/live.wav` (`PATH`) and plays what arrives. A plugin tells its speaker
       that address (`PluginContext.live_audio_url`).

The address without login
    A speaker cannot log in, so `PATH` is one of the few addresses that answer without a session
    (decision of 19.11.2026: a fixed address, see `Roadmap.md`). It is kept narrow:
      * it only answers while a transmission runs – otherwise 404;
      * it only answers to addresses in the home network – otherwise 403;
      * it only ever sends sound, never takes anything, and at most `MAX_LISTENERS` listen at once.
    Whoever is in the home network can listen in while a transmission runs. That is the price of the fixed
    address and is stated in the README of the Sonos plugin.

The format
    WAV with 16 bit, two channels, 44.1 or 48 kHz – what every speaker plays without a decoder and what a
    browser can produce without a library. The header names the largest possible length, the stream simply
    ends when the transmission does.

With HTTPS switched on the HTTP port only redirects – except for `PATH`, which `tls.redirect_app` answers
through `asgi()`: speakers do not accept a self-signed certificate.
"""
from __future__ import annotations

import asyncio
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from starlette.responses import Response, StreamingResponse

from . import egress

PATH = "/stream/live.wav"
RATES = (44100, 48000)                  # sample rates a transmission may have
CHANNELS = 2
BYTES_PER_FRAME = CHANNELS * 2          # 16 bit per channel
MAX_CHUNK_BYTES = 512 * 1024            # one piece from the browser (about 2.7 s at 48 kHz)
MAX_LISTENERS = 6                       # speakers (group leaders) that fetch the stream at once
IDLE_SECONDS = 8.0                      # no piece for this long: the transmission is over (tab closed, network gone)
QUEUE_CHUNKS = 40                       # pieces waiting per listener; a listener that falls behind loses the oldest
MAX_DEVICES = 20                        # devices remembered as playing the transmission
WAV_ENDLESS = 0xFFFFFFFF                # length field of a stream whose end is not known


@dataclass
class Session:
    """One running transmission."""

    rate: int
    started: float = field(default_factory=time.time)
    last_chunk: float = field(default_factory=time.monotonic)
    listeners: list["asyncio.Queue[bytes | None]"] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)       # ids of the devices that were told to play it


_session: Session | None = None


def _current() -> Session | None:
    """Return the running transmission. One that got no piece for `IDLE_SECONDS` is over and is closed here."""
    if _session is not None and time.monotonic() - _session.last_chunk > IDLE_SECONDS:
        stop()
    return _session


def active() -> bool:
    """Is a transmission running right now?"""
    return _current() is not None


def start(rate: int) -> None:
    """Begin a transmission with this sample rate. A running one with the same rate simply goes on; one with
    another rate is ended first (its listeners would play at the wrong speed). Raises ``ValueError`` for a rate
    that is not in `RATES`."""
    global _session
    if rate not in RATES:
        raise ValueError("Die Abtastrate muss 44100 oder 48000 Hz sein.")
    session = _current()
    if session is not None and session.rate == rate:
        session.last_chunk = time.monotonic()
        return
    if session is not None:
        stop()
    _session = Session(rate=rate)


def feed(data: bytes) -> None:
    """Hand one piece of sound (16 bit, little endian, left and right in turn) to everyone who listens.

    Raises ``LookupError`` when no transmission runs and ``ValueError`` for a piece that is empty, too large
    or does not consist of whole frames.
    """
    session = _current()
    if session is None:
        raise LookupError("Gerade läuft keine Übertragung.")
    if not data or len(data) > MAX_CHUNK_BYTES or len(data) % BYTES_PER_FRAME:
        raise ValueError("Das Tonstück hat keine gültige Länge.")
    session.last_chunk = time.monotonic()
    for queue in session.listeners:
        if queue.full():
            queue.get_nowait()          # the listener is too slow: skip ahead instead of drifting ever further behind
        queue.put_nowait(data)


def stop() -> list[str]:
    """End the transmission: every listener's stream ends. Returns the ids of the devices that played it."""
    global _session
    session, _session = _session, None
    if session is None:
        return []
    for queue in session.listeners:
        while queue.full():
            queue.get_nowait()
        queue.put_nowait(None)          # None = the end
    return list(session.devices)


def note_device(device_id: str) -> None:
    """Remember that a device was told to play the transmission, so that stopping can tell it as well."""
    session = _current()
    if session is not None and device_id not in session.devices and len(session.devices) < MAX_DEVICES:
        session.devices.append(device_id)


def status() -> dict[str, Any]:
    """State for the interface: whether a transmission runs, its rate, who plays it and how many fetch it."""
    session = _current()
    if session is None:
        return {"active": False, "rate": None, "devices": [], "listeners": 0, "path": PATH}
    return {"active": True, "rate": session.rate, "devices": list(session.devices), "listeners": len(session.listeners),
            "path": PATH}


def http_port() -> int:
    """The port ComfyOS answers plain HTTP on (set by `backend/run.py`; with HTTPS this is the redirecting port)."""
    try:
        return int(os.getenv("COMFYOS_HTTP_PORT") or os.getenv("COMFYOS_PORT") or 8000)
    except ValueError:
        return 8000


def url_for(host: str) -> str | None:
    """Return the address under which the device at `host` reaches the stream, or None when no transmission runs
    or ComfyOS has no address in the network of that device."""
    if not active():
        return None
    own = egress.own_address_towards(host)
    if own is None:
        return None
    port = http_port()
    return f"http://{own}{'' if port == 80 else f':{port}'}{PATH}"


def wav_header(rate: int) -> bytes:
    """Header of a WAV stream without a known end: 16 bit PCM, `CHANNELS` channels, `rate` samples per second."""
    return (b"RIFF" + struct.pack("<I", WAV_ENDLESS) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, CHANNELS, rate, rate * BYTES_PER_FRAME, BYTES_PER_FRAME, 16)
            + b"data" + struct.pack("<I", WAV_ENDLESS))


async def listen() -> AsyncIterator[bytes]:
    """Yield the stream for one listener: the header, then every piece as it arrives. Ends with the transmission,
    after `IDLE_SECONDS` without a piece, or at once when nothing runs or `MAX_LISTENERS` already listen."""
    session = _current()
    if session is None or len(session.listeners) >= MAX_LISTENERS:
        return
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=QUEUE_CHUNKS)
    session.listeners.append(queue)
    try:
        yield wav_header(session.rate)
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), IDLE_SECONDS)
            except asyncio.TimeoutError:
                return                  # the browser stopped sending: end the stream, the speaker falls silent
            if chunk is None:
                return
            yield chunk
    finally:
        if queue in session.listeners:
            session.listeners.remove(queue)


def refusal(client_host: str | None) -> int | None:
    """Why a request for the stream is turned away, as an HTTP status – or None when it may listen."""
    if not client_host or not egress.is_local_ip(client_host):
        return 403                      # only the home network
    session = _current()
    if session is None:
        return 404                      # nothing runs: the address does not exist right now
    if len(session.listeners) >= MAX_LISTENERS:
        return 503
    return None


def response(client_host: str | None, method: str = "GET") -> Response:
    """Answer a request for the stream: the endless WAV, only its headers for HEAD, or the refusal."""
    status_code = refusal(client_host)
    headers = {"Cache-Control": "no-store", "Accept-Ranges": "none"}
    if status_code is not None:
        return Response(status_code=status_code, headers=headers)
    if method == "HEAD":
        return Response(status_code=200, media_type="audio/wav", headers=headers)
    return StreamingResponse(listen(), media_type="audio/wav", headers=headers)


async def asgi(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Answer a request for the stream as a bare ASGI app – for the HTTP port that otherwise only redirects to HTTPS."""
    client = scope.get("client") or (None, 0)
    method = str(scope.get("method") or "GET").upper()
    answer = response(client[0], method) if method in ("GET", "HEAD") else Response(status_code=405)
    await answer(scope, receive, send)
