"""ComfyOS – buttons and ambient light (plugin API revision 7, since 0.23).

    1  device rows: kind "button" with its buttons, ambient light ("illuminance") as a value, capability and property for
       flows; plugins with "devices" see it too
    2  a press (context.button_pressed): to the open pages and the event log, never to listening plugins; the trigger
       "Taster" starts its flow only for the right device, button and kind of press – once
    3  what a plugin cannot do: unknown kinds of press and button ids are dropped, devices ComfyOS does not show do nothing

    python tests/core/test_buttons.py [path-to-comfyos]
"""
import asyncio
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("buttons")

from app import auth, core, events, flow_catalog, flow_engine, main, plugin_api, plugins  # noqa: E402
from app.plugin_api import Button, Capability, Device, Plugin  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class KnopfPlugin(Plugin):
    """A plugin of this test: a wall switch with two buttons, a lamp and a motion detector with ambient light."""

    def __init__(self, context: Any) -> None:
        """Commands are recorded in `commands`."""
        super().__init__(context)
        self.commands: list[tuple[str, dict, str]] = []

    async def devices(self) -> list[Device]:
        """Three devices."""
        return [Device(id="knopf:taster", name="Taster Flur", kind="button", buttons=[Button("1", "An"), Button("2", "Aus")],
                       battery=80),
                Device(id="knopf:lampe", name="Lampe Flur", kind="switch", state={"on": False},
                       capabilities=[Capability("on", writable=True)]),
                Device(id="knopf:bewegung", name="Bewegung Flur", kind="motion", state={"motion": False, "illuminance": 12.5},
                       capabilities=[Capability("motion"), Capability("illuminance", "number", unit="lx")])]

    async def command(self, device_id: str, state: Mapping[str, Any], source: str) -> Mapping[str, Any]:
        """Record and confirm."""
        self.commands.append((device_id, dict(state), source))
        return dict(state)


record = plugins.Record(id="knopf", name="Knopf", origin="builtin", state="active", started=True)
record.manifest = plugins.Manifest(id="knopf", name="Knopf", version="1.0.0", api=1, entry="__init__:setup",
                                   api_revision=plugin_api.API_REVISION, id_prefixes=["knopf:"])
context = plugins._Context(record)
record.plugin = KnopfPlugin(context)
plugins._records["knopf"] = record
knopf: KnopfPlugin = record.plugin

assert plugin_api.API_REVISION == 7 and "button" in plugin_api.DEVICE_KINDS
assert plugin_api.PRESS_KINDS == ("short", "long", "double") and plugin_api.STATE_ILLUMINANCE == "illuminance"
assert "illuminance" in flow_catalog.PROPS and flow_catalog.PROPS["illuminance"]["unit"] == "lx"
assert "illuminance" in core.LIVE_FIELDS and "illuminance" in core.VIEW_STATE and flow_engine.check_specs() == []

with TestClient(main.app) as client:
    client.cookies.set(auth.COOKIE, auth.create_session("test"))

    # ------------------------------------------------------------------ 1) device rows
    rows = {d["id"]: d for d in client.get("/api/devices").json()}
    taster, lampe, bewegung = rows["knopf:taster"], rows["knopf:lampe"], rows["knopf:bewegung"]
    assert taster["kind"] == "button" and taster["buttons"] == [{"id": "1", "name": "An"}, {"id": "2", "name": "Aus"}]
    assert "buttons" not in lampe and taster["battery"] == 80
    assert bewegung["illuminance"] == 12.5 and any(c["key"] == "illuminance" and c.get("unit") == "lx" for c in bewegung["caps"])
    assert core.view_row(bewegung)["state"]["illuminance"] == 12.5, "plugins with access see the ambient light"
    print("1 rows: kind button with its buttons, ambient light as value, capability and flow property")

    # a flow: button "An" short -> lamp on; button "Aus" long -> lamp off
    one = lambda did: {"devices": [did], "groups": [], "rooms": [], "all": False}   # noqa: E731
    nodes = [{"id": "a", "type": "button", "x": 0, "y": 0, "config": {"role": "trigger", "target": one("knopf:taster"),
                                                                    "button": "1", "press": "short"}},
             {"id": "b", "type": "set_device", "x": 300, "y": 0, "config": {"do": "switch", "target": one("knopf:lampe"), "to": "on"}},
             {"id": "c", "type": "button", "x": 0, "y": 200, "config": {"role": "trigger", "target": one("knopf:taster"),
                                                                      "button": "2", "press": "long"}},
             {"id": "d", "type": "set_device", "x": 300, "y": 200, "config": {"do": "switch", "target": one("knopf:lampe"), "to": "off"}}]
    edges = [{"id": "e1", "from": "a", "fromPort": "out", "to": "b", "toPort": "in"},
             {"id": "e2", "from": "c", "fromPort": "out", "to": "d", "toPort": "in"}]
    made = client.post("/api/flows", json={"name": "Taster Flur", "nodes": nodes, "edges": edges})
    assert made.status_code == 200, made.text
    assert client.put(f"/api/flows/{made.json()['id']}/enabled", json={"enabled": True}).status_code == 200

    # ------------------------------------------------------------------ 2) presses
    async def presses() -> None:
        """Press buttons the way a plugin reports it and watch pages, plugins, log and flow."""
        page: asyncio.Queue = asyncio.Queue()
        core.listen(page)
        heard: list = []
        notify = plugins.notify
        plugins.notify = heard.append

        async def settle() -> None:
            """Give the flow engine the time to run."""
            for _ in range(50):
                await asyncio.sleep(0.02)

        try:
            context.button_pressed("knopf:taster", "1", "short")
            await settle()
            message = page.get_nowait()
            assert message == {"type": "button", "id": "knopf:taster", "button": "1", "name": "An", "press": "short"}, message
            assert knopf.commands == [("knopf:lampe", {"on": True}, "flow")], knopf.commands
            assert any(e["message"] == "Taster Flur: An kurz gedrückt" for e in events.recent(20))

            context.button_pressed("knopf:taster", "1", "long")          # the flow wants a short press
            context.button_pressed("knopf:taster", "2", "short")         # … and a long one for "Aus"
            await settle()
            assert len(knopf.commands) == 1, knopf.commands
            context.button_pressed("knopf:taster", "2", "long")
            await settle()
            assert knopf.commands[-1] == ("knopf:lampe", {"on": False}, "flow") and len(knopf.commands) == 2
            assert not [m for m in heard if m.get("type") == "button"], "a press is no change of a device for plugins"

            # ------------------------------------------------------------------ 3) what a plugin cannot do
            log_before = len(events.recent(200))
            context.button_pressed("knopf:taster", "1", "triple")         # unknown kind of press
            context.button_pressed("knopf:taster", "bad id!", "short")    # not a button id
            context.button_pressed("knopf:gibtsnicht", "1", "short")      # a device ComfyOS does not show
            await settle()
            assert len(knopf.commands) == 2 and len(events.recent(200)) == log_before
        finally:
            plugins.notify = notify
            core._listeners.discard(page)
    client.portal.call(presses)
    print("2 presses: pages and log hear them, plugins do not; the trigger fires for the right button and kind of press, once")
    print("3 dropped: unknown kinds of press, invalid button ids, devices ComfyOS does not show")

print("ALL BUTTON TESTS OK")
