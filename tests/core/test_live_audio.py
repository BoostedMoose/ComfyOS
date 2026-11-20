"""ComfyOS – live audio (live_audio.py, api/audio.py): the sound of a browser as a WAV stream for speakers.

    1  relay: header, pieces reach every listener in order, a slow listener loses the oldest, the end closes the stream,
       invalid pieces and rates are refused, at most MAX_LISTENERS, a transmission without pieces ends by itself
    2  the address for speakers: only with a transmission, only for devices in the home network, HTTP port
    3  the stream without login: 404 while nothing runs, 403 from outside the home network, headers only for HEAD,
       nothing but GET and HEAD; the same answers on the port that otherwise redirects to HTTPS
    4  the routes for the browser: only with login, limits, a device needs the capability, stopping ends everything

    python tests/core/test_live_audio.py [path-to-comfyos]
"""
import asyncio
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("live_audio")

from app import auth, egress, live_audio, main, tls  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FRAME = b"\x01\x00\x02\x00"             # one frame: left 1, right 2
HOME = ("192.0.2.40", 40000)            # a device in the home network
OUTSIDE = ("8.8.8.8", 40000)            # an address in the internet


async def take(stream, count: int) -> list[bytes]:
    """Read `count` parts from a listener's stream."""
    return [await stream.__anext__() for _ in range(count)]


async def ended(stream) -> bool:
    """Has the stream ended?"""
    try:
        await stream.__anext__()
    except StopAsyncIteration:
        return True
    return False


async def relay() -> None:
    """Section 1 in one event loop."""
    assert not live_audio.active() and live_audio.status() == {"active": False, "rate": None, "devices": [], "listeners": 0,
                                                               "path": "/stream/live.wav"}
    assert await ended(live_audio.listen()), "without a transmission there is nothing to listen to"
    for bad in (8000, 96000, 0):
        try:
            live_audio.start(bad)
            raise AssertionError(f"rate {bad} accepted")
        except ValueError:
            pass
    try:
        live_audio.feed(FRAME)
        raise AssertionError("a piece without a transmission accepted")
    except LookupError:
        pass

    live_audio.start(48000)
    first, second = live_audio.listen(), live_audio.listen()
    header = (await take(first, 1))[0]
    assert (await take(second, 1))[0] == header and len(header) == 44
    assert header[:4] == b"RIFF" and header[8:16] == b"WAVEfmt " and header[36:40] == b"data"
    assert struct.unpack("<IHHIIHH", header[16:36]) == (16, 1, 2, 48000, 192000, 4, 16)
    assert struct.unpack("<I", header[4:8])[0] == struct.unpack("<I", header[40:44])[0] == 0xFFFFFFFF, "length unknown"
    assert live_audio.status()["listeners"] == 2 and live_audio.status()["rate"] == 48000
    live_audio.feed(FRAME * 10)
    live_audio.feed(FRAME * 20)
    assert await take(first, 2) == [FRAME * 10, FRAME * 20] and await take(second, 2) == [FRAME * 10, FRAME * 20]
    for bad in (b"", FRAME[:3], FRAME + b"\x00", b"\x00" * (live_audio.MAX_CHUNK_BYTES + 4)):
        try:
            live_audio.feed(bad)
            raise AssertionError(f"piece of {len(bad)} bytes accepted")
        except ValueError:
            pass
    # a listener that does not read loses the oldest pieces instead of holding up the others
    for number in range(live_audio.QUEUE_CHUNKS + 5):
        live_audio.feed(struct.pack("<hh", number, number))
    assert (await take(first, 1))[0] == struct.pack("<hh", 5, 5)
    # the same rate again keeps the transmission, another rate begins a new one and ends the old listeners
    live_audio.start(48000)
    assert live_audio.status()["listeners"] == 2
    live_audio.note_device("a:1")
    live_audio.note_device("a:1")
    assert live_audio.status()["devices"] == ["a:1"]
    live_audio.start(44100)
    assert live_audio.status() == {"active": True, "rate": 44100, "devices": [], "listeners": 0, "path": "/stream/live.wav"}
    while not await ended(first):
        pass                            # the pieces still waiting come first, then the end
    # at most MAX_LISTENERS
    streams = [live_audio.listen() for _ in range(live_audio.MAX_LISTENERS)]
    for stream in streams:
        await take(stream, 1)
    assert await ended(live_audio.listen()) and live_audio.refusal(HOME[0]) == 503
    # stop: every stream ends, the devices that played are named
    live_audio.note_device("b:2")
    assert live_audio.stop() == ["b:2"] and not live_audio.active() and live_audio.stop() == []
    assert all([await ended(stream) for stream in streams])
    # no piece for IDLE_SECONDS: the transmission is over (the tab was closed)
    live_audio.IDLE_SECONDS = 0.05
    live_audio.start(48000)
    waiting = live_audio.listen()
    await take(waiting, 1)
    assert await ended(waiting), "a listener must not wait forever"
    await asyncio.sleep(0.06)
    assert not live_audio.active()
    live_audio.IDLE_SECONDS = 8.0
    print("1 relay: header, pieces in order to every listener, slow listeners skip ahead, limits, end and idle end")


asyncio.run(relay())

# 2) the address a speaker is told
assert egress.own_address_towards("8.8.8.8") is None and egress.own_address_towards("kein-name") is None, "home network only"
egress.own_address_towards = lambda host: "192.0.2.1" if host.startswith("192.0.2.") else None
assert live_audio.url_for("192.0.2.40") is None, "no transmission: no address"
live_audio.start(48000)
live_audio.http_port = lambda: 8000
assert live_audio.url_for("192.0.2.40") == "http://192.0.2.1:8000/stream/live.wav"
live_audio.http_port = lambda: 80
assert live_audio.url_for("192.0.2.40") == "http://192.0.2.1/stream/live.wav"
assert live_audio.url_for("198.51.100.7") is None, "ComfyOS has no address in that network"
live_audio.stop()
print("2 address for speakers: only with a transmission, own address in the network of the device, HTTP port")

# 3) the stream itself – the one address that answers without login
home, outside = TestClient(main.app, client=HOME), TestClient(main.app, client=OUTSIDE)
assert home.get(live_audio.PATH).status_code == 404, "nothing runs: the address does not exist"
assert outside.get(live_audio.PATH).status_code == 403
live_audio.start(44100)
assert outside.get(live_audio.PATH).status_code == 403 and outside.head(live_audio.PATH).status_code == 403
head = home.head(live_audio.PATH)
assert head.status_code == 200 and head.headers["content-type"].startswith("audio/wav") and head.headers["cache-control"] == "no-store"
assert live_audio.status()["listeners"] == 0, "HEAD does not listen"
for method in ("POST", "PUT", "DELETE"):
    assert home.request(method, live_audio.PATH).status_code in (403, 405), method
assert home.get(live_audio.PATH + "/x").status_code == 404 and home.get("/stream/").status_code == 404
live_audio.stop()


async def redirecting_port() -> None:
    """The HTTP port that redirects to HTTPS answers the stream itself and redirects everything else."""
    app = tls.redirect_app(443)

    async def ask(path: str, client: tuple[str, int], method: str = "GET") -> tuple[int, dict[bytes, bytes], bytes]:
        sent = []
        scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
                 "headers": [(b"host", b"comfy.local")], "client": client, "http_version": "1.1", "scheme": "http",
                 "server": ("192.0.2.1", 80)}
        delivered = asyncio.Event()

        async def receive():
            await delivered.wait()      # the client hangs up once the test has what it needs
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and (message.get("body") or not message.get("more_body")):
                delivered.set()
        await app(scope, receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        return start["status"], dict(start["headers"]), b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")

    status, headers, _ = await ask("/", HOME)
    assert status == 308 and headers[b"location"] == b"https://comfy.local/"
    assert (await ask(live_audio.PATH, HOME))[0] == 404 and (await ask(live_audio.PATH, OUTSIDE))[0] == 403
    live_audio.start(48000)
    assert (await ask(live_audio.PATH, OUTSIDE))[0] == 403 and (await ask(live_audio.PATH, HOME, "POST"))[0] == 405
    status, headers, body = await ask(live_audio.PATH, HOME)
    assert status == 200 and headers[b"content-type"].startswith(b"audio/wav") and body[:4] == b"RIFF", (status, body[:8])
    assert live_audio.status()["listeners"] == 0, "the listener is gone when the speaker hangs up"
    live_audio.stop()


asyncio.run(redirecting_port())
print("3 stream without login: 404 without a transmission, 403 from outside, HEAD, no writing; also on the redirecting port")

# 4) the routes for the browser
with TestClient(main.app, client=HOME) as client:
    for method, path in (("GET", "/api/audio/live"), ("POST", "/api/audio/live/start"), ("POST", "/api/audio/live/chunk"),
                         ("POST", "/api/audio/live/play"), ("POST", "/api/audio/live/stop")):
        assert client.request(method, path).status_code == 401, path
    client.cookies.set(auth.COOKIE, auth.create_session("test"))
    raw = {"Content-Type": "application/octet-stream"}
    assert client.get("/api/audio/live").json()["active"] is False
    assert client.post("/api/audio/live/chunk", content=FRAME, headers=raw).status_code == 409, "no transmission yet"
    assert client.post("/api/audio/live/play", json={"device_id": "x:1"}).status_code == 409
    for bad in ({"rate": 22050}, {"rate": 96000}):
        assert client.post("/api/audio/live/start", json=bad).status_code == 400, bad
    for bad in ({}, {"rate": "48000x"}, {"rate": 1}, {"rate": 10 ** 9}):
        assert client.post("/api/audio/live/start", json=bad).status_code == 422, bad
    started = client.post("/api/audio/live/start", json={"rate": 48000})
    assert started.status_code == 200 and started.json()["active"] and started.json()["rate"] == 48000
    assert client.post("/api/audio/live/chunk", content=FRAME * 100, headers=raw).json() == {"ok": True}
    for bad in (b"", FRAME[:2], b"\x00" * (live_audio.MAX_CHUNK_BYTES + 4)):
        assert client.post("/api/audio/live/chunk", content=bad, headers=raw).status_code == 400, len(bad)
    assert client.post("/api/audio/live/play", json={"device_id": "gibtsnicht:1"}).status_code == 404
    assert client.post("/api/audio/live/play", json={"device_id": ""}).status_code == 422
    assert client.post("/api/audio/live/play", json={"device_id": "x" * 500}).status_code == 422
    stopped = client.post("/api/audio/live/stop")
    assert stopped.status_code == 200 and stopped.json()["active"] is False and not live_audio.active()
    assert client.post("/api/audio/live/stop").status_code == 200, "stopping twice is harmless"
    assert client.get(live_audio.PATH).status_code == 404
print("4 routes for the browser: only with login, rate and size limited, unknown device refused, stop ends everything")

print("ALL LIVE AUDIO TESTS OK")
