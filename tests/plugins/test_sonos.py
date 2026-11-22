"""ComfyOS – Home Base + Sonos plugin together: against a mock Home Base and a Sonos household in memory.

Checks the path through the plugin system (plugin API revision 4): search by SSDP only from the home network, linking
with the Sonos things of the Home Base (one room = one device, surround speakers are devices of their own), adopting,
switching with device lock and event log, playback in the device row, cover pictures through the core, UPnP switched
off and the limits of UDP in the home network. The translation of the UPnP interface has its own tests in public/plugins/sonos/tests/.

    python tests/plugins/test_sonos.py [path-to-comfyos]
    # without an argument: runs from the project root
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support.world_sonos import (  # noqa: E402 – mocks, app and helpers of the shared test world
    auth, core, egress, HB_CELLAR, HB_LIVING, HB_REAR_LEFT, HB_REAR_RIGHT, install_homebase, KITCHEN, LIVING, main, MockHB,
    plugins, PNG, REAR_LEFT, REAR_RIGHT, ROOM_ID, SONOS, sonos, sonos_plugin, wires, writes)
from app import events, live_audio, plugin_api  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

captured = []
_orig_publish = core.publish
core.publish = lambda m: (captured.append(m), _orig_publish(m))


def cmd_path(device_id):
    """Address of the command route of a device."""
    return f"/api/devices/{device_id}/command"


with TestClient(main.app) as client:
    install_homebase(MockHB("https://qivicon", "", "x"))
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    def ok(r, code=200):
        assert r.status_code == code, (r.status_code, r.text)
        return r.json()

    def devs():
        return {d["id"]: d for d in ok(client.get("/api/devices"))}

    def fresh():
        """Query the speakers now (otherwise the state of the last query holds for up to 15 s)."""
        client.portal.call(sonos_plugin().refresh, 0, True)
        return devs()

    # 1) without Sonos: the things of the Home Base stand for themselves, the plugin offers the search
    rows = devs()
    assert {HB_LIVING, HB_REAR_LEFT, HB_REAR_RIGHT, HB_CELLAR} <= set(rows)
    assert rows[HB_LIVING]["kind"] == "media" and not rows[HB_LIVING].get("link_missing") and rows[HB_LIVING].get("playing") is None
    assert not rows[HB_LIVING].get("control"), "a speaker of the Home Base alone cannot be controlled"
    status = ok(client.get(SONOS + "/status"))
    assert status["configured"] is False and status["suggested"] == {"count": 4, "hosts": []}, status
    listed = {p["id"]: p for p in ok(client.get("/api/plugins"))["plugins"]}
    assert listed["sonos"]["state"] == "active" and listed["sonos"]["status"]["configured"] is False
    assert listed["sonos"]["egress"] == [{"key": "speakers", "desc": "Sonos-Lautsprecher im Heimnetz", "scope": "lan", "hosts": [], "udp": True}]
    assert not wires, "no search without the push of a button"
    print("1 without Sonos: list of the Home Base unchanged, the plugin knows the number of its speakers, no search by itself")

    # 2) Search: only the speaker from the home network counts; it names the whole household
    found = ok(client.post(SONOS + "/search"))
    assert found["found"] == 2 and [r["name"] for r in found["rooms"]] == ["Küche", "Wohnzimmer"], found
    assert sonos["searches"] == 2 and wires[-1].closed, "two search requests, after that the socket is closed again"
    assert all(addr == (egress.SSDP_GROUP, egress.SSDP_PORT) for _, addr in wires[-1].sent)
    asked = {u for u, _, _ in sonos["calls"]}
    assert asked <= {LIVING, KITCHEN}, "only speakers of the household are addressed"
    rows = devs()
    assert len(rows) == len(ok(client.get("/api/devices"))), "duplicate devices"
    living = rows[HB_LIVING]
    assert (living["name"], living["room"], living["kind"], living["plugin"]) == ("Fernseher-Box", "Wohnzimmer", "media", "qivicon"), living
    assert living["control"] == {"plugin": "sonos", "device_id": ROOM_ID(LIVING)} and living["online"]
    assert (living["playing"], living["volume"], living["muted"]) == (True, 26, False)
    media = living["media"]
    assert (media["title"], media["artist"], media["duration"], media["position"]) == ("Lied <b>eins</b>", "Die Band", 210, 65), media
    assert media["favorites"] == ["Radio Eins"] and media["group"] == [] and media["coordinator"] is None
    assert media["image"].startswith("/api/media/image/sonos/") and len(media["image"].rsplit("/", 1)[1]) == 24
    caps = {c["key"]: c for c in living["caps"]}
    assert caps["playing"]["writable"] and caps["volume"]["maximum"] == 100 and caps["favorite"]["options"] == ["Radio Eins"]
    assert caps["next"] == {"key": "next", "type": "action", "readable": False, "writable": True}, caps["next"]
    # the surround speakers stay devices of their own (to stand in their room and on the floor plan): name and room
    # from the Home Base, they show what their room plays, are not doubled, not orphaned and cannot be switched
    assert ROOM_ID(LIVING) not in rows and ROOM_ID(REAR_LEFT) not in rows and ROOM_ID(REAR_RIGHT) not in rows
    for box_id, name in ((HB_REAR_LEFT, "Surround links"), (HB_REAR_RIGHT, "Surround rechts")):
        box = rows[box_id]
        assert (box["name"], box["room"], box["kind"], box["plugin"]) == (name, "Wohnzimmer", "media", "qivicon"), box
        assert box["online"] and box.get("playing") is None and box["media"]["title"] == "Lied <b>eins</b>" and not box.get("link_missing")
        assert not box.get("control_item") and not any(c["key"] == "playing" for c in box["caps"]), "a box is not a speaker tile"
        assert box["media"]["favorites"] == []
        assert box["part_of"] == HB_LIVING, "a box points to the row of its room – the id the Home Base lends it"
    assert "part_of" not in living
    # a speaker that no longer exists at Sonos is orphaned
    assert rows[HB_CELLAR]["link_missing"] and rows[HB_CELLAR]["missing_plugin"] == "sonos" and "Sonos" in rows[HB_CELLAR]["status_detail"]
    # a room the Home Base does not know waits to be adopted
    assert ROOM_ID(KITCHEN) not in rows
    tile = ok(client.get("/api/plugins/sonos/devices"))
    assert [c["id"] for c in tile["new"]] == [ROOM_ID(KITCHEN)] and tile["new"][0]["room_hint"] == "Küche"
    # the boxes are not controlled by Sonos: they stay in the list of the plugin that names them (the Home Base)
    assert sorted(d["id"] for d in tile["devices"]) == sorted([HB_LIVING, HB_CELLAR])
    ok(client.post("/api/plugins/sonos/adopt", json={"device_id": ROOM_ID(KITCHEN), "room": None}))
    kitchen = devs()[ROOM_ID(KITCHEN)]
    assert kitchen["adopted"] and kitchen["plugin"] == "sonos" and kitchen["playing"] is False and kitchen["volume"] == 12
    view = plugins.device_view(core.view_row(living))
    assert view.state == {"playing": True, "volume": 26, "muted": False} and view.media.title == "Lied <b>eins</b>"
    print("2 search only in the home network; one room = one device (name and room from the Home Base, state and control directly),"
          "surround speakers are devices of their own without control, what is missing is orphaned, what is unknown is adopted")

    # 3) Switching through the shared path: command at the right speaker, text in the event log, live message
    sonos["calls"].clear()
    captured.clear()
    r = ok(client.post(cmd_path(HB_LIVING), json={"state": {"playing": False}}))
    assert writes(LIVING) == ["Pause"] and writes(KITCHEN) == [] and r["command"] == "Pause" and r["via"] == "sonos", r
    live = next(m for m in captured if m.get("type") == "device" and m.get("id") == HB_LIVING)
    assert live["playing"] is False and live["volume"] == 26 and live["media"]["title"] == "Lied <b>eins</b>", live
    assert "erfunden" not in live and set(live) <= {"type", "id"} | set(core.LIVE_FIELDS)
    assert any("Fernseher-Box: Pause" in e["message"] for e in events.recent(10)), events.recent(3)
    r = ok(client.post(cmd_path(HB_LIVING), json={"state": {"volume": 40, "muted": True}}))
    assert writes(LIVING)[-2:] == ["SetVolume", "SetMute"] and r["command"] == "Lautstärke 40 % · stumm"
    r = ok(client.post(cmd_path(ROOM_ID(KITCHEN)), json={"state": {"favorite": "Radio Eins"}}))
    assert writes(KITCHEN) == ["SetAVTransportURI", "Play"] and r["command"] == "Favorit „Radio Eins“"
    r = ok(client.post(cmd_path(ROOM_ID(KITCHEN)), json={"state": {"next": True}}))
    assert writes(KITCHEN)[-1] == "Next" and r["command"] == "nächster Titel"
    for bad in ({"volume": 500}, {"volume": "laut"}, {"playing": "ja"}, {"favorite": "Gibt es nicht"}):
        ok(client.post(cmd_path(HB_LIVING), json={"state": bad}), 400)
    ok(client.post(cmd_path(HB_CELLAR), json={"state": {"playing": True}}), 400)        # orphaned: not switchable
    ok(client.post(cmd_path(HB_REAR_LEFT), json={"state": {"playing": True}}), 400)     # a further box: controlled through its room
    print("3 switching: playback, volume, mute, favourite and track change at the right speaker; text in the event log,"
          "live message with playback; nonsense and orphaned devices rejected")

    # 4) The device lock applies to speakers too – also to grouping from the tile
    ok(client.post(f"/api/devices/{HB_LIVING}/lock"))
    sonos["calls"].clear()
    ok(client.post(cmd_path(HB_LIVING), json={"state": {"playing": True}}), 423)
    group = {"device_id": ROOM_ID(LIVING), "target_id": ROOM_ID(KITCHEN)}
    ok(client.post(SONOS + "/group", json=group), 423)
    ok(client.post(SONOS + "/ungroup", json={"device_id": ROOM_ID(LIVING)}), 423)
    assert writes(LIVING) == [], "a locked speaker gets no command"
    ok(client.post(f"/api/devices/{HB_LIVING}/unlock"))
    ok(client.post(SONOS + "/group", json=group))
    joined = next(args for u, action, args in sonos["calls"] if u == LIVING and action == "SetAVTransportURI")
    assert joined["CurrentURI"] == f"x-rincon:{KITCHEN}"
    ok(client.post(SONOS + "/ungroup", json={"device_id": ROOM_ID(LIVING)}))
    assert writes(LIVING)[-1] == "BecomeCoordinatorOfStandaloneGroup"
    for bad in ({"device_id": "sonosroom:../x", "target_id": ROOM_ID(KITCHEN)}, {"device_id": HB_LIVING, "target_id": ROOM_ID(KITCHEN)}):
        ok(client.post(SONOS + "/group", json=bad), 422)
    ok(client.post(SONOS + "/group", json={"device_id": "sonosroom:RINCON_FFFFFFFFFFFF01400", "target_id": ROOM_ID(KITCHEN)}), 404)
    print("4 device lock: locked = no command, not through grouping either; grouping goes through the same switching path")

    # 4b) The sound of a browser: the speaker is told the address of the stream – through the shared switching path
    egress.own_address_towards = lambda host: "192.0.2.1" if host.startswith("192.0.2.") else None
    live_audio.http_port = lambda: 8000
    stream_url = "http://192.0.2.1:8000/stream/live.wav"
    caps = {c["key"]: c for c in fresh()[HB_LIVING]["caps"]}
    assert caps["live_audio"] == {"key": "live_audio", "type": "bool", "readable": False, "writable": True}, caps["live_audio"]
    assert not any(c["key"] == "live_audio" for c in fresh()[HB_REAR_LEFT]["caps"]), "a further box cannot play it"
    ok(client.post(cmd_path(HB_LIVING), json={"state": {"live_audio": True}}), 400)     # no transmission runs
    ok(client.post("/api/audio/live/play", json={"device_id": HB_LIVING}), 409)
    ok(client.post("/api/audio/live/start", json={"rate": 48000}))
    ok(client.post("/api/audio/live/play", json={"device_id": HB_REAR_LEFT}), 400)
    ok(client.post(f"/api/devices/{HB_LIVING}/lock"))
    ok(client.post("/api/audio/live/play", json={"device_id": HB_LIVING}), 423)
    ok(client.post(f"/api/devices/{HB_LIVING}/unlock"))
    sonos["calls"].clear()
    r = ok(client.post("/api/audio/live/play", json={"device_id": HB_LIVING}))
    assert r["command"] == "Ton vom Browser" and writes(LIVING) == ["SetAVTransportURI", "Play"], (r, writes(LIVING))
    told = next(args for u, action, args in sonos["calls"] if u == LIVING and action == "SetAVTransportURI")
    assert told["CurrentURI"] == stream_url and "audio/wav" in told["CurrentURIMetaData"], told
    assert "Ton vom Browser" in events.recent(1)[0]["message"]
    assert ok(client.get("/api/audio/live"))["devices"] == [HB_LIVING]
    media = fresh()[HB_LIVING]["media"]
    assert (media["title"], media["artist"], media["source"]) == ("Ton vom Browser", None, "Rechner"), media
    sonos["calls"].clear()
    assert ok(client.post("/api/audio/live/stop"))["active"] is False
    assert writes(LIVING) == ["Stop"] and fresh()[HB_LIVING]["playing"] is False
    sonos["calls"].clear()
    ok(client.post(cmd_path(HB_LIVING), json={"state": {"live_audio": False}}))
    assert writes(LIVING) == ["Stop"], "the speaker still names the stream as its source: stopping again is harmless"
    sonos["source"].clear()
    fresh()                             # the plugin sees that the speaker plays something else now
    sonos["calls"].clear()
    ok(client.post(cmd_path(HB_LIVING), json={"state": {"live_audio": False}}))
    assert writes(LIVING) == [], "a speaker that plays something else is left alone"
    sonos["playing"][LIVING] = True
    print("4b sound of a browser: capability only on rooms, address of the stream to the speaker through the shared path "
          "(lock, event log), shown as source, stopping only stops what plays the stream")

    # 5) Cover picture: the core fetches it from the plugin, the plugin only from the speaker
    image = fresh()[HB_LIVING]["media"]["image"]
    r = client.get(image)
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    for path in ("/api/media/image/sonos/" + "0" * 24, "/api/media/image/hue/" + image.rsplit("/", 1)[1],
                 "/api/media/image/gibtsnicht/abc", "/api/media/image/sonos/..%2fx", "/api/media/image/sonos/" + "a" * 200):
        assert client.get(path).status_code == 404, path
    fresh_client = TestClient(main.app)
    assert fresh_client.get(image).status_code == 401, "cover pictures only with login"
    print("5 cover picture through /api/media/image: only known ids, only from the plugin of the device, only with login")

    # 6) UPnP switched off: nothing disappears, nothing is marked as orphaned, the tile says what the reason is
    sonos["mode"] = "upnp_off"
    rows = fresh()
    assert rows[HB_LIVING]["online"] is False and "UPnP" in rows[HB_LIVING]["status_detail"] and not rows[HB_LIVING].get("link_missing")
    assert rows[ROOM_ID(KITCHEN)]["online"] is False and rows[ROOM_ID(KITCHEN)]["adopted"]
    assert not rows[HB_CELLAR].get("link_missing"), "silent speakers prove nothing"
    status = ok(client.get(SONOS + "/status"))
    assert status["silent"] and not status["connected"] and "UPnP" in status["problem"] and len(status["rooms"]) == 2
    assert {p["id"]: p for p in ok(client.get("/api/plugins"))["plugins"]}["sonos"]["status"]["connected"] is False
    ok(client.post(cmd_path(HB_LIVING), json={"state": {"playing": True}}), 502)
    sonos["mode"] = "ok"
    rows = fresh()
    assert rows[HB_LIVING]["online"] and rows[HB_CELLAR]["link_missing"] and ok(client.get(SONOS + "/status"))["connected"]
    print("6 UPnP off: devices stay (offline), nothing orphaned, the tile names the reason; back afterwards")

    # 7) UDP in the home network (plugin API revision 4): only with a declaration in the manifest, only to and from home network addresses, limited
    assert plugin_api.API_REVISION >= 4 and "action" in plugin_api.CAPABILITY_TYPES
    ctx = sonos_plugin().context

    async def udp():
        sock = await ctx.open_datagram("speakers", 0)
        wire, got, outcome = wires[-1], [], []
        sock.on_datagram(lambda data, sender: got.append((data, sender[0])))
        for data, addr in ((b"x", ("192.0.2.31", 1900)), (b"x", ("8.8.8.8", 53)), (b"x", ("lautsprecher.fritz.box", 1900)),
                           (b"x" * (egress.MAX_DATAGRAM + 1), ("192.0.2.31", 1900)), (b"", ("192.0.2.31", 1900)),
                           (b"x", ("192.0.2.31", 0)), (b"x", ("fe80::1%eth0", 1900)), (b"x", (egress.SSDP_GROUP, 1900))):
            try:
                sock.send(data, addr)
                outcome.append("ok")
            except plugin_api.PluginError:
                outcome.append("blockiert")
        wire.deliver(b"a", ("192.0.2.40", 5))
        wire.deliver(b"b", ("8.8.8.8", 5))
        wire.deliver(b"c" * (egress.MAX_DATAGRAM + 1), ("192.0.2.40", 5))
        wire.deliver(b"d", ("fe80::2%eth0", 5, 0, 2))
        flood = 0
        try:
            for _ in range(200):
                sock.send(b"x", ("192.0.2.31", 1900))
                flood += 1
        except plugin_api.PluginError:
            pass
        sock.close()
        try:
            sock.send(b"x", ("192.0.2.31", 1900))
            outcome.append("nach close gesendet")
        except plugin_api.PluginError:
            pass
        return outcome, got, flood, len(wire.sent), wire.closed

    outcome, got, flood, sent, closed = client.portal.call(udp)
    assert outcome == ["ok", "blockiert", "blockiert", "blockiert", "blockiert", "blockiert", "ok", "ok"], outcome
    assert got == [(b"a", "192.0.2.40"), (b"d", "fe80::2%eth0")], got
    assert flood < 200 and sent == egress.DATAGRAM_SEND_PER_SECOND and closed, (flood, sent)

    async def refused():
        out = []
        for call in (lambda: ctx.open_datagram("speakers", 1900, multicast="239.1.2.3"), lambda: ctx.open_datagram("speakers", 70000),
                     lambda: ctx.open_datagram("unbekannt", 0), lambda: plugins.active("hue").context.open_datagram("bridge", 0),
                     lambda: plugins.active("hue").context.discover("bridge", "ssdp", "upnp:rootdevice"),
                     lambda: ctx.discover("speakers", "mdns", "_sonos._tcp"), lambda: ctx.discover("speakers", "ssdp", "zeile\r\numbruch")):
            try:
                await call()
                out.append("ok")
            except plugin_api.PluginError:
                out.append("blockiert")
        opened = []
        try:
            for _ in range(plugins.MAX_DATAGRAM_SOCKETS + 1):
                opened.append(await ctx.open_datagram("speakers", 1900, multicast=egress.SSDP_GROUP))
            out.append("zu viele Sockets")
        except plugin_api.PluginError:
            pass
        return out, len(opened)

    out, opened = client.portal.call(refused)
    assert out == ["blockiert"] * 7 and opened == plugins.MAX_DATAGRAM_SOCKETS, (out, opened)
    before = len(wires)
    ok(client.put("/api/plugins/sonos/enabled", json={"enabled": False}))
    assert all(w.closed for w in wires[:before]), "disabled: all UDP sockets of the plugin are closed"
    assert ROOM_ID(KITCHEN) not in devs() and not devs()[HB_LIVING].get("control"), "disabled: the Home Base stands for itself again"
    ok(client.post(SONOS + "/search"), 404)
    # the answer to a search is read strictly
    target = "urn:schemas-upnp-org:device:ZonePlayer:1"
    good = (f"HTTP/1.1 200 OK\r\nLOCATION: http://192.0.2.31:1400/xml/device_description.xml\r\nST: {target}\r\n"
            "USN: uuid:RINCON_X::x\r\n\r\n").encode()
    hit = egress.parse_ssdp(good, "192.0.2.31", target)
    assert hit["host"] == "192.0.2.31" and hit["usn"] == "uuid:RINCON_X::x" and hit["headers"]["st"] == target
    assert egress.parse_ssdp(good, "192.0.2.99", target) is None, "the description is not at the sender"
    assert egress.parse_ssdp(good, "192.0.2.31", "upnp:rootdevice") is None
    assert egress.parse_ssdp(good.replace(b"200 OK", b"404 Not Found"), "192.0.2.31", target) is None
    assert egress.parse_ssdp(good.replace(b"http://", b"file://"), "192.0.2.31", target) is None
    assert egress.parse_ssdp(b"NOTIFY * HTTP/1.1\r\n\r\n", "192.0.2.31", target) is None and egress.parse_ssdp(b"\xff\xfe", "192.0.2.31", target) is None
    assert egress.is_local_ip("fe80::1%eth0") and not egress.is_local_ip("2001:4860:4860::8888%eth0")
    print("7 UDP: only with 'udp' in the manifest, only to and from home network addresses, size, rate and number of sockets limited,"
          "only SSDP/mDNS groups; disabling closes everything; search answers read strictly")

core.publish = _orig_publish
print("ALL SONOS TESTS OK")
