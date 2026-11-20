"""Test world "demo home": the world "light and scenes" (world_scenes), completed with radiators, window contacts,
a motion detector, a smart plug with consumption, a floor plan with three rooms and a weather mock.
Sample data only, no internet. Shared by tools/readme_screenshot.py (images of the README) and the
image comparison tests/ui/ui_visual.py.

On import the whole world is created: isolated environment, app, mocks, floor plan.
"""
from __future__ import annotations

import time

from support.world_scenes import BR, ITEMS, THINGS, MockHB, auth, ch, core, install_homebase, main  # noqa: F401

from app import floorplan_store, flow_engine, weather  # noqa: E402
import httpx  # noqa: E402


def demo_weather(request):
    """Weather for the image (instead of Open-Meteo): light rain in a fresh wind from west-southwest – easy to recognise
    but not a storm. The place of the sample address is looked up here as well."""
    if request.url.host.startswith("geocoding"):
        return httpx.Response(200, json={"results": [{"name": "Berlin", "latitude": 52.52, "longitude": 13.41,
                                                      "country_code": "DE", "postcodes": ["10115"]}]})
    return httpx.Response(200, json={
        "current": {"time": time.strftime("%Y-%m-%dT%H:%M"), "temperature_2m": 9.4, "apparent_temperature": 6.8,
                    "relative_humidity_2m": 84, "weather_code": 61, "is_day": 1, "rain": 1.4, "precipitation": 1.4,
                    "cloud_cover": 85, "wind_speed_10m": 26, "wind_gusts_10m": 42, "wind_direction_10m": 245},
        "daily": {"temperature_2m_max": [11.2], "temperature_2m_min": [5.1]}})


def no_warnings(request):
    """Response of the DWD without warnings."""
    return httpx.Response(200, json={"type": "FeatureCollection", "features": []})


weather._client = lambda timeout=6.0: httpx.AsyncClient(transport=httpx.MockTransport(demo_weather))
weather._warn_client = lambda timeout=6.0: httpx.AsyncClient(transport=httpx.MockTransport(no_warnings))


def thing(uid, tt, label, room, chans, online=True):
    """A device the way the Home Base reports it under /rest/things."""
    return {"UID": uid, "thingTypeUID": tt, "label": label, "location": room,
            "statusInfo": {"status": "ONLINE" if online else "OFFLINE"}, "channels": chans}


THINGS += [
    thing("dect:thermostat:tw", "dect:thermostat", "Heizung Wohnzimmer", "wohnen",
          [ch("temperature", "Number", "TW_t"), ch("heatingTemperature", "Number", "TW_s")]),
    thing("dect:thermostat:tk", "dect:thermostat", "Heizung Küche", "kueche",
          [ch("temperature", "Number", "TK_t"), ch("heatingTemperature", "Number", "TK_s")]),
    thing("dect:thermostat:ts", "dect:thermostat", "Heizung Schlafzimmer", "schlafen",   # reports device fault E1
          [ch("temperature", "Number", "TS_t"), ch("heatingTemperature", "Number", "TS_s"), ch("alertState-E1", "Switch", "TS_e1")]),
    thing("zigbee:contact:cw", "zigbee:contact", "Fenster Wohnzimmer", "wohnen", [ch("contact", "Contact", "CW")]),
    thing("zigbee:contact:ck", "zigbee:contact", "Fenster Küche", "kueche", [ch("contact", "Contact", "CK")]),
    thing("zigbee:contact:cs", "zigbee:contact", "Fenster Schlafzimmer", "schlafen", [ch("contact", "Contact", "CS")]),
    thing("zigbee:motion:mk", "zigbee:motion", "Bewegung Küche", "kueche", [ch("presence", "Switch", "MK")], online=False),
    thing("dect:plug:pw", "dect:indoorSmartPlug", "Stecker TV", "wohnen",
          [ch("switchState", "Switch", "PW_s"), ch("powerConsumption", "Number", "PW_p"),
           ch("energyConsumption", "Number", "PW_e")]),
]
ITEMS += [{"name": "TW_t", "state": "21.5"}, {"name": "TW_s", "state": "21"}, {"name": "TK_t", "state": "18.5"},
          {"name": "TK_s", "state": "19"}, {"name": "TS_t", "state": "17"}, {"name": "TS_s", "state": "17"},
          {"name": "CW", "state": "CLOSED"}, {"name": "CK", "state": "OPEN"}, {"name": "CS", "state": "CLOSED"},
          {"name": "MK", "state": "OFF"}, {"name": "TS_e1", "state": "ON"},
          {"name": "PW_s", "state": "ON"}, {"name": "PW_p", "state": "84.0 W"}, {"name": "PW_e", "state": "31.4 kWh"},
          {"name": "kueche", "label": "Küche", "type": "Group"}, {"name": "schlafen", "label": "Schlafzimmer", "type": "Group"}]

STRIP, DECKE, WEISS = f"hue:0210:{BR}:1", f"hue:0210:{BR}:2", f"hue:0210:{BR}:4"
floorplan_store.save({
    "width": 22, "height": 13, "heatmap": True, "shadow": True, "north": 0, "weather": True, "address": "Musterstraße 1, 10115 Berlin",
    "home_name": "Wohnung", "show_names": True,
    "walls": [{"id": "w1", "pts": [[1, 1], [21, 1], [21, 12], [1, 12], [1, 1]]},
              {"id": "w2", "pts": [[12, 1], [12, 12]]}, {"id": "w3", "pts": [[12, 6.5], [21, 6.5]]}],
    "rooms": [{"id": "r1", "name": "Wohnzimmer", "room_id": "wohnen", "x": 6, "y": 6},
              {"id": "r2", "name": "Küche", "room_id": "kueche", "x": 16.5, "y": 3.5},
              {"id": "r3", "name": "Schlafzimmer", "room_id": "schlafen", "x": 16.5, "y": 9}],
    "openings": [
        {"id": "ow1", "type": "window", "a": [4, 1], "b": [8, 1], "side": 1, "device_id": "zigbee:contact:cw"},
        {"id": "or1", "type": "radiator", "a": [4.4, 1], "b": [7.6, 1], "side": 1, "device_id": "dect:thermostat:tw"},
        {"id": "ow2", "type": "window", "a": [15, 1], "b": [18, 1], "side": 1, "device_id": "zigbee:contact:ck"},
        {"id": "or2", "type": "radiator", "a": [21, 2.5], "b": [21, 5], "side": 1, "device_id": "dect:thermostat:tk"},
        {"id": "ow3", "type": "window", "a": [15, 12], "b": [18, 12], "side": 1, "device_id": "zigbee:contact:cs"},
        {"id": "or3", "type": "radiator", "a": [15.3, 12], "b": [17.7, 12], "side": -1, "device_id": "dect:thermostat:ts"},
        {"id": "ow4", "type": "window", "a": [1, 5], "b": [1, 8], "side": 1, "device_id": None},
        {"id": "od1", "type": "door", "a": [12, 3], "b": [12, 4.5], "side": 1, "device_id": None},
        {"id": "od2", "type": "door", "a": [12, 8.5], "b": [12, 10], "side": -1, "device_id": None}],
    "devices": {"zigbee:contact:cw": {"x": 6, "y": 1.5, "t": 0.5}, "dect:thermostat:tw": {"x": 6, "y": 1.5, "t": 0.5},
                "zigbee:contact:ck": {"x": 16.5, "y": 1.5, "t": 0.5}, "dect:thermostat:tk": {"x": 20.5, "y": 3.7, "t": 0.5},
                "zigbee:contact:cs": {"x": 16.5, "y": 11.5, "t": 0.5}, "dect:thermostat:ts": {"x": 16.5, "y": 11.5, "t": 0.5},
                "zigbee:motion:mk": {"x": 19.5, "y": 5.3}, WEISS: {"x": 9.5, "y": 9.5}, "zigbee:bulb:z1": {"x": 2.6, "y": 10.4}},
    "groups": [{"id": "g2", "name": "Licht Sofa", "x": 6, "y": 6.5, "members": [STRIP, DECKE]}]})


async def _no_step(now=None):
    """Replacement for the step of the effects: does nothing (calm image)."""
    return 0
core.scene_step = _no_step


def demo_flow():
    """A sample flow as (nodes, connections): light on motion – in the evening and only at home; plus heating
    down as long as the kitchen window is open."""
    MK, CK, TK = "zigbee:motion:mk", "zigbee:contact:ck", "dect:thermostat:tk"
    one = lambda dev: {"devices": [dev], "groups": [], "rooms": [], "all": False}   # noqa: E731
    N = lambda nid, typ, x, y, **cfg: {"id": nid, "type": typ, "x": x * 290, "y": y * 150, "config": cfg}   # noqa: E731
    nodes = [
        N("a", "device_state", 0, 0, role="trigger", target=one(MK), prop="motion", **{"is": "yes"}, hold_off=120),
        N("b", "time", 0, 1.25, role="condition", **{"from": "sunset", "to": "sunrise"}),
        N("c", "presence", 0, 2.35, role="condition", state="PRESENT"),
        N("d", "when", 1, 0.35, match="all"),
        N("e", "set_device", 2, 0, do="switch", target=one(DECKE), to="on"),
        N("g", "notify", 3, 0, text="Licht an – Bewegung in der Küche", toast=True, push=False),
        N("h", "timer", 1, 1.95, wait=300),
        N("i", "set_device", 2, 1.95, do="switch", target=one(DECKE), to="off"),
        N("j", "device_state", 0, 3.75, role="trigger", target=one(CK), prop="open", **{"is": "yes"}, hold_on=300),
        N("k", "set_device", 1, 3.45, do="temp", target=one(TK), temp_mode="abs", temp=12),
        N("l", "set_device", 1, 4.45, do="temp", target=one(TK), temp_mode="abs", temp=20),
    ]
    E = lambda i, a, ap, z, zp: {"id": f"e{i}", "from": a, "fromPort": ap, "to": z, "toPort": zp}   # noqa: E731
    edges = [E(1, "a", "out", "d", "in"), E(2, "b", "val", "d", "c1"), E(3, "c", "val", "d", "c2"), E(4, "d", "yes", "e", "in"),
             E(5, "e", "out", "g", "in"), E(6, "a", "end", "h", "in"), E(7, "h", "out", "i", "in"),
             E(8, "j", "out", "k", "in"), E(9, "j", "end", "l", "in")]
    return nodes, edges


def calm_weather(request):
    """Fixed weather without today's time of day: fair, little wind (for comparisons in which nothing should move)."""
    if request.url.host.startswith("geocoding"):
        return httpx.Response(200, json={"results": [{"name": "Berlin", "latitude": 52.52, "longitude": 13.41,
                                                      "country_code": "DE", "postcodes": ["10115"]}]})
    return httpx.Response(200, json={
        "current": {"time": "2026-11-18T12:00", "temperature_2m": 9.4, "apparent_temperature": 8.1,
                    "relative_humidity_2m": 70, "weather_code": 2, "is_day": 1, "cloud_cover": 30,
                    "wind_speed_10m": 6, "wind_gusts_10m": 9, "wind_direction_10m": 245},
        "daily": {"temperature_2m_max": [11.2], "temperature_2m_min": [5.1]}})


def steady(api, effects: bool = False) -> str:
    """Bring the demo home into a constant state: calm weather, a scene in the living room, one
    lamp off, a heating schedule, a flow. `api` is a signed-in httpx client against the running app; the Hue Bridge
    is paired along the way. `effects`: weather effects on the floor plan on (moving) or off. Returns the id of the flow."""
    from app import routines_store

    weather._client = lambda timeout=6.0: httpx.AsyncClient(transport=httpx.MockTransport(calm_weather))
    floorplan_store.save({**floorplan_store.get(), "fx": bool(effects)})
    assert api.post("/api/plugins/hue/pair", json={"host": "192.0.2.10"}).json().get("connected")
    api.post("/api/scenes/abendrot/apply", json={"room_ids": ["wohnen"]})
    api.post(f"/api/devices/{WEISS}/command", json={"state": {"on": False}})
    routine = routines_store.create("Winter")
    routines_store.update_schedule(routine["key"], [
        {"room": room, "day": day, "points": [{"time": "06:30", "temp": 21}, {"time": "22:00", "temp": 17}]}
        for room in ("wohnen", "kueche") for day in range(7)])
    routines_store.set_active(routine["key"])
    nodes, edges = demo_flow()
    flow = api.post("/api/flows", json={"name": "Licht bei Bewegung", "description": "Beispiel", "nodes": nodes, "edges": edges})
    assert flow.status_code == 200, flow.text
    return flow.json()["id"]
