"""ComfyOS – weather for the address (Open-Meteo) against a mock weather service.

    python tests/core/test_weather.py [path-to-comfyos]

Checks: place from the address (never street/house number in the request), cache, outage without an error,
unknown place, switch in the floor plan, the endpoint /api/weather, the weather effects (fx) and the official
weather warnings of the DWD (only rounded coordinates, selection, order, cache, outage, can be switched off).
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("wx")

import httpx  # noqa: E402

from app import auth, floorplan_store, main, weather  # noqa: E402

# ------------------------------------------------------------------ Place from the address
pq = weather.place_query
assert pq("Musterstraße 12, 10115 Berlin") == {"postcode": "10115", "name": "Berlin"}
assert pq("Hauptstr. 3a, 80331 München, Deutschland") == {"postcode": "80331", "name": "München"}
assert pq("Bahnhofstraße 5, Wien") == {"postcode": None, "name": "Wien"}
assert pq("Hamburg") == {"postcode": None, "name": "Hamburg"}
assert pq("Musterstraße 1") is None            # only a street: no place -> no weather, no request
assert pq("") is None and pq(None) is None
assert pq("Weg 1, 1010 Wien, Österreich") == {"postcode": "1010", "name": "Wien"}
print("1 place from the address: postcode/town recognised, a street alone without a town -> no weather")

# ------------------------------------------------------------------ Mock-Wetterdienst
calls = []
state = {"down": False, "warn": []}


def handler(req: httpx.Request):
    """Responses of weather service, place search and DWD; records every request, on request everything fails."""
    calls.append(req)
    if state["down"]:
        raise httpx.ConnectError("offline", request=req)
    if req.url.host == "maps.dwd.de":
        return httpx.Response(200, json={"type": "FeatureCollection", "features": state["warn"], "totalFeatures": len(state["warn"])})
    if req.url.host.startswith("geocoding"):
        name = req.url.params.get("name")
        if name == "Nirgendwo":
            return httpx.Response(200, json={"generationtime_ms": 0.1})
        if name == "Wien":
            return httpx.Response(200, json={"results": [{"name": "Wien", "latitude": 48.20849, "longitude": 16.37208,
                                                          "country_code": "AT", "postcodes": ["1010"]}]})
        return httpx.Response(200, json={"results": [
            {"name": "Berlin", "latitude": 52.52437, "longitude": 13.41053, "country_code": "US", "postcodes": ["03570"]},
            {"name": "Berlin", "latitude": 52.52437, "longitude": 13.41053, "country_code": "DE", "postcodes": ["10115", "10117"]}]})
    return httpx.Response(200, json={
        "current": {"temperature_2m": 7.4, "apparent_temperature": 4.9, "relative_humidity_2m": 81, "weather_code": 3,
                    "is_day": 0, "wind_speed_10m": 14.2},
        "daily": {"temperature_2m_max": [9.1], "temperature_2m_min": [2.3]}})


transport = httpx.MockTransport(handler)
weather._client = lambda timeout=6.0: httpx.AsyncClient(transport=transport)
weather._warn_client = lambda timeout=6.0: httpx.AsyncClient(transport=transport)   # DWD: derselbe Mock, nie ins Internet
run = asyncio.run

w = run(weather.current("Musterstraße 12, 10115 Berlin"))
assert w["available"] and w["temperature"] == 7.4 and w["label"] == "Bedeckt" and w["icon"] == "cloud", w
assert w["place"] == "Berlin" and w["min"] == 2.3 and w["max"] == 9.1
geo = [c for c in calls if c.url.host.startswith("geocoding")][0]
fc = [c for c in calls if c.url.host.startswith("api.")][0]
for c in calls:
    assert "Muster" not in str(c.url) and "12" not in c.url.params.get("name", ""), c.url   # street/house number never leaves the house
assert geo.url.params["name"] == "Berlin"
assert fc.url.params["latitude"] == "52.52" and fc.url.params["longitude"] == "13.41"   # auf ca. 1 km gerundet
for field in ("cloud_cover", "precipitation", "rain", "showers", "snowfall", "wind_direction_10m", "wind_gusts_10m"):
    assert field in fc.url.params["current"], field          # weather effects: the same service, no new egress
for field in ("temperature_2m", "precipitation_probability", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m"):
    assert field in fc.url.params["hourly"], field           # hourly forecast and wind: the same query, no second request
assert fc.url.params["forecast_days"] == "2" and len([c for c in calls if c.url.host.startswith("api.")]) == 1
assert w["wind_report"] == {"speed": 14, "gusts": None, "direction": None, "compass": None, "beaufort": 3, "label": "schwache Brise"}, w["wind_report"]
assert w["today"] == [], "without hourly values from the service: empty forecast, no error"
assert w["fx"]["kind"] == "overcast" and w["fx"]["is_day"] is False and w["fx"]["rain"] == 0, w["fx"]
print("2 weather: overcast 7.4 °C; the request only contains the town, coordinates rounded; fx delivered along")

n = len(calls)
run(weather.current("Musterstraße 12, 10115 Berlin"))
assert len(calls) == n, "the second call must come from the cache"
weather.reset()
run(weather.current("Musterstraße 12, 10115 Berlin"))
assert len(calls) == n + 1 and calls[-1].url.host.startswith("api."), "the place is remembered from data/weather.json, only the weather is fetched anew"
print("3 cache: weather 15 min, place permanently")

assert weather.describe(0, False) == ("Klar", "moon") and weather.describe(2, True) == ("Teilweise bewölkt", "partly")
assert weather.describe(95)[1] == "storm" and weather.describe(None) == ("", "cloud")

weather.reset()
state["down"] = True
w = run(weather.current("Musterstraße 12, 10115 Berlin"))
assert w == {"available": False, "reason": "Wetterdienst gerade nicht erreichbar."}, w
state["down"] = False
w = run(weather.current("Nirgendwo"))
assert not w["available"] and "nicht gefunden" in w["reason"], w
w = run(weather.current("Musterstraße 1"))
assert not w["available"] and "Ort" in w["reason"]
print("4 outage/unknown place: no error, only available=False with a reason")

# ------------------------------------------------------------------ Endpunkt + Schalter im Grundriss
from fastapi.testclient import TestClient  # noqa: E402

with TestClient(main.app) as client:
    client.cookies.set(auth.COOKIE, auth.create_session("test"))
    weather.reset()
    r = client.get("/api/weather").json()
    assert r == {"enabled": True, "available": False, "reason": "Keine Adresse im Grundriss eingetragen."}, r
    plan = floorplan_store.save({"address": "Musterstraße 12, 10115 Berlin"})
    assert plan["weather"] is True, "the weather is on by default"
    r = client.get("/api/weather").json()
    assert r["enabled"] and r["available"] and r["temperature"] == 7.4, r
    p = client.put("/api/floorplan", json={**plan, "weather": False}).json()
    assert p["weather"] is False
    n = len(calls)
    r = client.get("/api/weather").json()
    assert r == {"enabled": False, "available": False} and len(calls) == n, "switched off: no request"
    assert client.get("/api/floorplan").json()["weather"] is False
    print("5 endpoint /api/weather: on by default, can be switched off in the floor plan (then no request)")

# ------------------------------------------------------------------ Weather effects: fx from WMO code and readings
fx = weather.fx


def cur(code, **kw):
    """Aktuelles Wetter mit diesem Wettercode."""
    return {"weather_code": code, "is_day": 1, **kw}


assert fx(cur(0, cloud_cover=5))["kind"] == "clear"
assert fx(cur(1))["kind"] == "partly" and fx(cur(0, cloud_cover=50))["kind"] == "partly"     # code 1–2 or 20–80 %
assert fx(cur(3))["kind"] == "overcast" and fx(cur(2, cloud_cover=85))["kind"] == "overcast"  # code 3 or ≥ 80 %
assert fx(cur(45))["kind"] == "fog" and fx(cur(48))["kind"] == "fog"
r = fx(cur(63, rain=2.0, cloud_cover=100, wind_speed_10m=20, wind_direction_10m=250))
assert r["kind"] == "rain" and r["rain"] == 0.5 and r["cloud"] == 1.0 and r["wind"] == 0.25 and r["wind_dir"] == 250, r
assert r["wind_kmh"] == 20 and fx({})["wind_kmh"] == 0, "measured wind in km/h for the wind arrow (since 0.20.1)"
assert fx(cur(51, rain=0))["rain"] == 0.15, "drizzle without a reading stays visible"
assert fx(cur(65, rain=9))["rain"] == 1.0, "intensity capped at 0..1"
assert fx(cur(81))["kind"] == "rain" and fx(cur(81))["rain"] >= 0.5
s = fx(cur(73, snowfall=1.0))
assert s["kind"] == "snow" and s["snow"] == 0.5 and s["rain"] == 0, s
assert fx(cur(86))["kind"] == "snow" and fx(cur(86))["snow"] >= 0.25
t = fx(cur(95, rain=1.0))
assert t["kind"] == "thunder" and t["thunder"] is True and t["rain"] == 0.25, t                # Gewitter mit Regen
assert fx(cur(95))["rain"] == 0 and fx(cur(99))["thunder"]
st = fx(cur(2, wind_gusts_10m=75, wind_speed_10m=40, wind_direction_10m=270))
assert st["kind"] == "storm" and st["storm"] and st["wind"] >= 0.75 and st["rain"] == 0, st    # Sturm bei trockenem Wetter
assert st["wind_kmh"] == 40, "the wind arrow names the measured wind, not the value raised in a storm"
sr = fx(cur(63, rain=2.0, wind_gusts_10m=70, wind_speed_10m=45))
assert sr["kind"] == "rain" and sr["storm"] and sr["wind"] >= 0.75 and sr["rain"] == 0.5, sr     # storm with rain: wind as an overlay
assert fx(cur(95, rain=2, wind_gusts_10m=90))["kind"] == "thunder"
assert fx(cur(0, wind_gusts_10m=59))["storm"] is False
n = fx({"weather_code": 0, "is_day": 0})
assert n["is_day"] is False and n["kind"] == "clear" and n["cloud"] == 0.05, n
assert fx({})["kind"] == "clear" and fx({"weather_code": "x", "rain": float("nan")})["rain"] == 0   # broken values: no error
for v in (fx(cur(c, rain=99, snowfall=99, wind_speed_10m=999, wind_gusts_10m=999, cloud_cover=500)) for c in (0, 3, 45, 63, 73, 95)):
    assert all(0 <= v[k] <= 1 for k in ("cloud", "rain", "snow", "wind", "gust")) and 0 <= v["wind_dir"] < 360, v
# Gusts (since 0.19.1): same scale as wind, never smaller than the mean wind; without a reading = mean wind
assert fx(cur(2, wind_speed_10m=20, wind_gusts_10m=46))["gust"] == 0.57 and fx(cur(2, wind_speed_10m=20))["gust"] == 0.25
assert fx(cur(2, wind_speed_10m=20, wind_gusts_10m=5))["gust"] == 0.25 and fx({})["gust"] == 0
# overlays: hail, sleet, frost, heat
assert fx(cur(96))["hail"] and fx(cur(99, rain=3))["hail"] and not fx(cur(95))["hail"] and not fx(cur(63))["hail"]
mix = fx(cur(63, rain=1.0, snowfall=0.5))
assert mix["kind"] == "rain" and mix["rain"] > 0 and mix["snow"] == 0.25, mix                  # sleet: both fall
mix = fx(cur(73, snowfall=1.0, rain=0.8))
assert mix["kind"] == "snow" and mix["snow"] == 0.5 and mix["rain"] == 0.2, mix
assert fx(cur(0, temperature_2m=-3.5))["frost"] is True and fx(cur(0, temperature_2m=0.0))["frost"] is True
assert fx(cur(0, temperature_2m=4.1))["frost"] is False and fx(cur(0))["frost"] is False         # without a temperature: no frost
# since 0.27 hoarfrost shows from 4 °C; frost_level says how much: a hint at 4 °C (since 0.27.4), full from 0 °C, 0 without frost
assert fx(cur(0, temperature_2m=4))["frost"] is True and fx(cur(0, temperature_2m=4))["frost_level"] == 0.05
assert fx(cur(0, temperature_2m=2))["frost_level"] == 0.53 and fx(cur(0, temperature_2m=0))["frost_level"] == 1
assert fx(cur(0, temperature_2m=-8))["frost_level"] == 1 and fx(cur(0, temperature_2m=9))["frost_level"] == 0
assert fx(cur(73, snowfall=1, temperature_2m=-5))["frost"] is False, "frost only if nothing falls"
assert fx(cur(0, temperature_2m=33))["heat"] == 0.5 and fx(cur(0, temperature_2m=45))["heat"] == 1.0
assert fx(cur(0, temperature_2m=27))["heat"] == 0 and fx({"weather_code": 0, "is_day": 0, "temperature_2m": 33})["heat"] == 0
assert fx(cur(63, rain=2, temperature_2m=33))["heat"] == 0, "heat only in sunshine"
assert fx(cur(0, temperature_2m=float("nan")))["frost"] is False and fx(cur(0, temperature_2m="kalt"))["heat"] == 0
print("6 weather effects: WMO code + readings -> fx (rain, snow, fog, clouds, thunderstorm with rain, storm with rain), 0..1;"
      "hail, sleet, frost, heat")

# ------------------------------------------------------------------ Amtliche Wetterwarnungen (DWD)
NOW = time.time()


def iso(ts):
    """Zeitpunkt als ISO-Text in UTC."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def feat(event, severity, group, onset, expires, **kw):
    """A warning of the DWD with start and end relative to NOW."""
    return {"type": "Feature", "geometry": None, "properties": {
        "EVENT": event, "HEADLINE": f"Amtliche WARNUNG vor {event}", "SEVERITY": severity, "EC_GROUP": group,
        "ONSET": iso(NOW + onset), "EXPIRES": iso(NOW + expires), "STATUS": "Actual", "MSGTYPE": "Alert",
        "IDENTIFIER": f"2.49.0.0.276.0.DWD.TEST.{event}", "DESCRIPTION": "Es treten  Sturmböen\nauf.",
        "INSTRUCTION": "Achten Sie auf herabfallende Gegenstände.", **kw}}


FEATS = [
    feat("FROST", "Minor", "FROST", -3600, 7200),
    feat("STURMBÖEN", "Moderate", "WIND", -1800, 7200),
    feat("STURMBÖEN", "Moderate", "WIND", -1800, 7200, IDENTIFIER="zweiter-gemeindeteil"),        # dieselbe Warnung noch einmal
    feat("ORKANBÖEN", "Extreme", "WIND", 3600, 9000),                                            # beginnt erst
    feat("DAUERREGEN", "Severe", "RAIN", -7200, -60),                                            # abgelaufen
    feat("GEWITTER", "Moderate", "THUNDERSTORM;WIND", -600, 3600, MSGTYPE="Cancel"),             # aufgehoben
    feat("HAGEL", "Severe", "HAIL", -600, 3600, STATUS="Test"),                                  # Probemeldung
    feat("STARKREGEN", "Severe", "", -900, 5400),                                                # without a group: word in the event
]
ws = weather.parse_warnings({"features": FEATS})
assert [w["event"] for w in ws] == ["STARKREGEN", "STURMBÖEN", "FROST", "ORKANBÖEN"], [w["event"] for w in ws]
assert [w["level"] for w in ws] == [3, 2, 1, 4] and [w["active"] for w in ws] == [True, True, True, False], ws
assert [w["windows"] for w in ws] == [True, True, False, True], "window hint for wind/rain, not for frost"
assert ws[1]["headline"] == "Amtliche WARNUNG vor STURMBÖEN" and ws[1]["description"] == "Es treten Sturmböen auf.", ws[1]
assert ws[1]["id"].endswith("STURMBÖEN") and abs(ws[1]["expires"] - (NOW + 7200)) < 2 and abs(ws[1]["onset"] - (NOW - 1800)) < 2
for broken in (None, [], {}, {"features": "x"}, {"features": [None, {"properties": None}, {"properties": {"EVENT": ""}}]},
               {"features": [{"properties": {"EVENT": "WIND", "ONSET": "gestern", "EXPIRES": 12, "SEVERITY": 7}}]}):
    out = weather.parse_warnings(broken)
    assert isinstance(out, list) and all(w["level"] == 1 and w["onset"] is None for w in out), broken   # broken data: no error
long = weather.parse_warnings({"features": [feat("WINDBÖEN", "Minor", "WIND", -60, 600, DESCRIPTION="x" * 5000, HEADLINE="h" * 900)]})[0]
assert len(long["description"]) == 700 and len(long["headline"]) == 140, "texts are limited"
print("7 reading warnings: only valid ones (not expired/cancelled/test), without duplicates, running ones first by level, window hint")

weather.reset()
state["warn"] = FEATS
calls.clear()
got = run(weather.alerts("Musterstraße 12, 10115 Berlin"))
assert [w["event"] for w in got] == ["STARKREGEN", "STURMBÖEN", "FROST", "ORKANBÖEN"], got
dwd = [c for c in calls if c.url.host == "maps.dwd.de"]
assert len(dwd) == 1 and dwd[0].url.path == "/geoserver/dwd/ows" and dwd[0].url.params["typeName"] == "dwd:Warnungen_Gemeinden"
assert dwd[0].url.params["CQL_FILTER"] == "INTERSECTS(THE_GEOM, POINT(52.52 13.41))", dwd[0].url.params["CQL_FILTER"]   # latitude before longitude, about 1 km
assert "THE_GEOM" not in dwd[0].url.params["propertyName"], "municipal borders are not loaded along"
for c in calls:
    assert "Muster" not in str(c.url) and "10115" not in str(c.url), c.url          # neither street nor postcode goes to the DWD
assert "Berlin" not in str(dwd[0].url), "only coordinates go to the DWD"
n = len(calls)
assert run(weather.alerts("Musterstraße 12, 10115 Berlin")) == got and len(calls) == n, "10 minutes of cache"
# service gone: the warnings loaded last stay until they expire, never an error
weather._warn["at"] = 0.0
state["down"] = True
kept = run(weather.alerts("Musterstraße 12, 10115 Berlin"))
assert [w["event"] for w in kept] == ["STARKREGEN", "STURMBÖEN", "FROST", "ORKANBÖEN"], kept
weather._warn["items"][0]["expires"] = NOW - 5
assert [w["event"] for w in run(weather.alerts("Musterstraße 12, 10115 Berlin"))] == ["STURMBÖEN", "FROST", "ORKANBÖEN"], "expired ones drop out"
weather.reset()
assert run(weather.alerts("Musterstraße 12, 10115 Berlin")) == [], "outage without prior knowledge: empty"
state["down"] = False
assert run(weather.alerts("Musterstraße 1")) == [] and run(weather.alerts("")) == []           # without a place: no query
# place outside Germany: the DWD is not asked at all
weather.reset()
calls.clear()
assert run(weather.alerts("Ring 1, 1010 Wien")) == [] and not [c for c in calls if c.url.host == "maps.dwd.de"], calls
print("8 loading warnings: only rounded coordinates to the DWD, 10 min cache, an outage keeps what is known, abroad without a query")

with TestClient(main.app) as client:
    client.cookies.set(auth.COOKIE, auth.create_session("test"))
    weather.reset()
    plan = floorplan_store.save({"address": "Musterstraße 12, 10115 Berlin"})
    assert plan["weather"] is True and plan["warnings"] is True, "warnings are on by default together with the weather"
    r = client.get("/api/weather").json()
    assert r["available"] and [w["event"] for w in r["warnings"]][:2] == ["STARKREGEN", "STURMBÖEN"], r.get("warnings")
    assert r["warnings_source"] == "Deutscher Wetterdienst"
    egr = {c["id"]: c for c in client.get("/api/system/egress").json()["connections"]}
    assert egr["weather_warnings"]["state"] == "on" and egr["weather_warnings"]["hosts"] == ["maps.dwd.de"], egr["weather_warnings"]
    # switched off: the weather stays, but no connection to the DWD
    plan = client.put("/api/floorplan", json={**plan, "warnings": False}).json()
    assert plan["warnings"] is False and plan["weather"] is True
    weather.reset()
    calls.clear()
    r = client.get("/api/weather").json()
    assert r["available"] and "warnings" not in r and not [c for c in calls if c.url.host == "maps.dwd.de"], r
    egr = {c["id"]: c for c in client.get("/api/system/egress").json()["connections"]}
    assert egr["weather_warnings"]["state"] == "off" and egr["weather"]["state"] == "on"
    # weather off: no warnings either
    client.put("/api/floorplan", json={**plan, "warnings": True, "weather": False})
    calls.clear()
    assert client.get("/api/weather").json() == {"enabled": False, "available": False} and calls == []
    print("9 endpoint: /api/weather returns warnings; can be switched off in the floor plan (then no connection to the DWD); the overview of the connections is right")

# ------------------------------------------------------------------ Wind report and hourly forecast
assert [weather.beaufort(v)[0] for v in (0, 0.9, 1, 5.9, 20, 28.9, 62, 117.9, 118, 250)] == [0, 0, 1, 1, 4, 4, 8, 11, 12, 12]
assert weather.beaufort(70) == (8, "stürmischer Wind") and weather.beaufort(130) == (12, "Orkan")
assert [weather.compass(d) for d in (0, 11, 12, 90, 180, 250, 270, 348, 359, 360, 725)] == \
    ["N", "N", "NNO", "O", "S", "WSW", "W", "NNW", "N", "N", "N"]
rep = weather.wind_report({"wind_speed_10m": 20.4, "wind_gusts_10m": 45.5, "wind_direction_10m": 250})
assert rep == {"speed": 20, "gusts": 46, "direction": 250, "compass": "WSW", "beaufort": 4, "label": "mäßige Brise"}, rep
assert weather.wind_report({}) is None and weather.wind_report({"wind_speed_10m": "stark"}) is None
assert weather.wind_report({"wind_speed_10m": float("nan")}) is None
hours = [f"2026-11-{18 + h // 24:02d}T{h % 24:02d}:00" for h in range(48)]
hourly = {"time": hours, "temperature_2m": [h / 2 for h in range(48)], "precipitation_probability": [h % 101 for h in range(48)],
          "precipitation": [0.1] * 48, "weather_code": [61] * 24 + [0] * 24, "is_day": [1 if 8 <= h % 24 < 17 else 0 for h in range(48)],
          "wind_speed_10m": [10 + h for h in range(48)], "wind_gusts_10m": [20 + h for h in range(48)], "wind_direction_10m": [250] * 47}
fc = weather.hourly_forecast(hourly, "2026-11-18T14:20")
assert len(fc) == 24 and fc[0]["time"] == "14:00" and fc[0]["day"] == "2026-11-18" and fc[-1]["time"] == "13:00" and fc[-1]["day"] == "2026-11-19"
assert fc[0] == {"time": "14:00", "day": "2026-11-18", "temperature": 7.0, "label": "Regen", "icon": "rain", "pop": 14.0, "precip": 0.1,
                 "wind": 24.0, "gusts": 34.0, "compass": "WSW"}, fc[0]
assert fc[10]["icon"] == "moon" and fc[10]["label"] == "Klar", fc[10]                # 0 Uhr morgen: klar, Nacht
assert weather.hourly_forecast(hourly, "2026-11-19T23:59")[0]["time"] == "23:00" and len(weather.hourly_forecast(hourly, "2026-11-19T23:59")) == 1
assert weather.hourly_forecast(hourly, "2026-11-20T00:00") == [] and len(weather.hourly_forecast(hourly, None)) == 24
for broken in (None, {}, {"time": "x"}, {"time": hours, "temperature_2m": "x", "wind_speed_10m": [None, "x"]}):
    out = weather.hourly_forecast(broken, "2026-11-18T14:20")
    assert isinstance(out, list) and all(h["temperature"] is None for h in out), broken   # broken data: no error
print("10 wind: Beaufort, compass direction, report; hourly forecast: 24 hours from now across midnight, broken data without an error")

from app import entitlements  # noqa: E402

with TestClient(main.app) as client:
    client.cookies.set(auth.COOKIE, auth.create_session("test"))
    weather.reset()
    floorplan_store.save({"address": "Musterstraße 12, 10115 Berlin"})
    r = client.get("/api/weather").json()
    assert "wind_report" in r and "today" in r, sorted(r)
    entitlements.ENFORCE = True
    entitlements.use_plan("basic")
    try:
        r = client.get("/api/weather").json()
        assert r["available"] and r["temperature"] == 7.4 and "wind_report" not in r and "today" not in r, sorted(r)
        entitlements.use_plan("pro")
        assert "wind_report" in client.get("/api/weather").json()
    finally:
        entitlements.ENFORCE = False
        entitlements.use_plan("basic")
    print("11 wind report and hourly forecast belong to the weather effects (Pro); the weather itself stays for everyone")

print("ALL WEATHER TESTS OK")
