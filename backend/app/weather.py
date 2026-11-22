"""Outside temperature and weather for the home address (Open-Meteo: free, without account/key)
and official weather warnings for the same place (Deutscher Wetterdienst, open data, without account).

Privacy: only postcode or town goes to the internet - never street or house number.
The coordinates are rounded to about 1 km and remembered in data/weather.json, so that the
place is only looked up once. The weather itself is cached for 15 minutes,
warnings for 10 minutes. The DWD only gets the rounded coordinates.
Without internet or on errors there simply is no weather display - nothing else depends on it.
Can be switched off in the floor plan editor (floorplan.weather = false, floorplan.warnings = false).
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any

import httpx

from . import demo, jsonfile
from . import egress
from .paths import DATA_DIR, ensure_dirs

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WX_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_FILE = DATA_DIR / "weather.json"
TTL = 900          # Sekunden, gueltiges Wetter
FAIL_TTL = 300     # seconds not to ask again right after an error

# Official warnings: WFS of the DWD (verified 11/2026). Queried is the municipality the point lies in;
# with WFS 2.0 and EPSG:4326 the latitude comes before the longitude. propertyName saves the municipal borders (geometry).
WARN_URL = "https://maps.dwd.de/geoserver/dwd/ows"
WARN_TTL = 600
WARN_FIELDS = "EVENT,HEADLINE,DESCRIPTION,INSTRUCTION,SEVERITY,ONSET,EXPIRES,EC_GROUP,MSGTYPE,STATUS,IDENTIFIER"
WARN_MAX = 12
WARN_SOURCE = "Deutscher Wetterdienst"
_LEVELS = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
# Weather in which open windows are a problem (wind, thunderstorm, rain, hail, snow) – not frost, black ice, fog, heat
_WINDOW_GROUPS = frozenset({"WIND", "THUNDERSTORM", "RAIN", "HAIL", "SNOWFALL", "SNOWDRIFT", "TORNADO"})
_WINDOW_WORDS = ("BÖEN", "STURM", "ORKAN", "GEWITTER", "REGEN", "HAGEL", "SCHNEEFALL", "SCHNEEVERWEHUNG", "TORNADO")

# WMO-Wettercode -> (Text, Symbol). Symbole: sun, partly, cloud, fog, drizzle, rain, snow, storm
_CODES = [
    ((0,), "Klar", "sun"), ((1,), "Überwiegend klar", "sun"), ((2,), "Teilweise bewölkt", "partly"),
    ((3,), "Bedeckt", "cloud"), ((45, 48), "Nebel", "fog"), ((51, 53, 55, 56, 57), "Nieselregen", "drizzle"),
    ((61, 63, 65, 66, 67), "Regen", "rain"), ((71, 73, 75, 77), "Schnee", "snow"),
    ((80, 81, 82), "Regenschauer", "rain"), ((85, 86), "Schneeschauer", "snow"), ((95, 96, 99), "Gewitter", "storm"),
]

_mem: dict[str, Any] = {"key": None, "at": 0.0, "data": None}
_lock = asyncio.Lock()
_warn: dict[str, Any] = {"key": None, "at": 0.0, "ok": False, "items": []}
_warn_lock = asyncio.Lock()


def _client(timeout: float = 6.0) -> httpx.AsyncClient:
    """HTTP client for the weather service (egress entry `weather`)."""
    return egress.client("weather", timeout=httpx.Timeout(timeout, connect=4.0), headers={"User-Agent": "ComfyOS"})


def _warn_client(timeout: float = 6.0) -> httpx.AsyncClient:
    """HTTP client for the weather warnings (egress entry `weather_warnings`)."""
    return egress.client("weather_warnings", timeout=httpx.Timeout(timeout, connect=4.0), headers={"User-Agent": "ComfyOS"})


def describe(code: Any, is_day: bool = True) -> tuple[str, str]:
    """Translate a weather code into text and icon; at night sun and "heiter" become moon icons. Unknown codes
    give a cloud without text."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "", "cloud"
    for codes, label, icon in _CODES:
        if c in codes:
            if not is_day and icon in ("sun", "partly"):
                icon = "moon" if icon == "sun" else "partly-night"
            return label, icon
    return "", "cloud"


def place_query(address: Any) -> dict | None:
    """Only the town from the address: '..., 12345 Berlin' -> postcode + town; otherwise the last part after the comma.

    Street and house number are never used."""
    a = " ".join(str(address or "").split())
    if not a:
        return None
    m = re.search(r"(?<!\d)(\d{4,5})\s+([^\d,]+)", a)
    if m:
        return {"postcode": m.group(1), "name": m.group(2).strip()}
    parts = [p.strip() for p in a.split(",") if p.strip()]
    if len(parts) >= 2:
        name = parts[-1]
        if name.lower() in ("deutschland", "germany", "österreich", "austria", "schweiz", "switzerland") and len(parts) >= 3:
            name = parts[-2]
    elif not re.search(r"\d", a):
        name = a                       # only a town name
    else:
        return None                    # only street + number: no town given
    name = re.sub(r"\d+", " ", name).strip()
    return {"postcode": None, "name": " ".join(name.split())} if name else None


def _read_cache() -> dict:
    """Cached weather data; empty if the file is missing or unreadable (it refills)."""
    return jsonfile.read(CACHE_FILE, {}, keep=False)


def _write_cache(data: dict) -> None:
    """Zwischenspeicher atomar schreiben."""
    ensure_dirs()
    jsonfile.write(CACHE_FILE, data, indent=None)


async def _geocode(q: dict, key: str) -> dict:
    """Coordinates for the configured place – from the cache, otherwise from the geo service. For places of the same
    name the postcode decides, otherwise a German-speaking country. Raises LookupError if the place does not exist."""
    cached = _read_cache()
    if cached.get("query") == key and isinstance(cached.get("lat"), (int, float)):
        return cached
    async with _client() as c:
        r = await c.get(GEO_URL, params={"name": q["name"][:80], "count": 10, "language": "de", "format": "json"})
        r.raise_for_status()
        results = (r.json() or {}).get("results") or []
    if not results:
        raise LookupError("Ort nicht gefunden")
    best = None
    if q.get("postcode"):
        best = next((x for x in results if q["postcode"] in (x.get("postcodes") or [])), None)
    if best is None:   # places of the same name: prefer German-speaking countries, otherwise the first (best-known) hit
        best = next((x for x in results if x.get("country_code") in ("DE", "AT", "CH")), results[0])
    geo = {"query": key, "lat": round(float(best["latitude"]), 2), "lon": round(float(best["longitude"]), 2),
           "place": str(best.get("name") or q["name"])[:60], "cc": str(best.get("country_code") or "")[:2].upper() or None}
    _write_cache(geo)
    return geo


def _f(v: Any) -> float:
    """Number from a response of the weather service (0.0 for everything that is not one)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else 0.0


def _unit(v: float) -> float:
    """Value limited to 0…1 and rounded to two places (strength of an effect)."""
    return round(max(0.0, min(1.0, v)), 2)


RAIN_MAX_MM = 4.0      # mm/h  -> rain = 1
SNOW_MAX_CM = 2.0      # cm/h  -> snow = 1
WIND_MAX_KMH = 80.0    # km/h  -> wind = 1
STORM_GUSTS_KMH = 60.0
FROST_C = 4.0          # °C   -> hoarfrost if nothing falls (ground frost begins above freezing; since 0.27, before 0 °C)
FROST_FULL_C = 0.0     # °C   -> frost_level = 1; at FROST_C it is FROST_MIN
FROST_MIN = 0.05
HEAT_C = 28.0          # °C   -> heat > 0 (only by day in sunshine)
HEAT_MAX_C = 38.0      # °C   -> heat = 1


# Wind force on the Beaufort scale: (upper limit in km/h, name); above that hurricane
BEAUFORT = ((1, "Windstille"), (6, "leiser Zug"), (12, "leichte Brise"), (20, "schwache Brise"), (29, "mäßige Brise"),
            (39, "frische Brise"), (50, "starker Wind"), (62, "steifer Wind"), (75, "stürmischer Wind"), (89, "Sturm"),
            (103, "schwerer Sturm"), (118, "orkanartiger Sturm"))
_COMPASS = ("N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
HOURS_AHEAD = 24       # Stundenprognose: so viele Stunden ab jetzt


def _number(v: Any) -> float | None:
    """Number from a response or None – also for NaN and nonsensically large values."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e6 else None


def beaufort(kmh: float) -> tuple[int, str]:
    """Wind force on the Beaufort scale with its name for a speed in km/h."""
    for strength, (limit, name) in enumerate(BEAUFORT):
        if kmh < limit:
            return strength, name
    return 12, "Orkan"


def compass(degrees: float) -> str:
    """Windrichtung in Grad (woher der Wind kommt) -> Himmelsrichtung, z. B. 250 -> WSW."""
    return _COMPASS[int((degrees % 360) / 22.5 + 0.5) % 16]


def wind_report(cur: dict) -> dict | None:
    """Wind report from the readings: speed, gusts, direction, force on the Beaufort scale. None without wind data."""
    speed = _number(cur.get("wind_speed_10m"))
    if speed is None:
        return None
    gusts, direction = _number(cur.get("wind_gusts_10m")), _number(cur.get("wind_direction_10m"))
    strength, name = beaufort(speed)
    return {"speed": round(speed), "gusts": round(gusts) if gusts is not None else None,
            "direction": int(direction) % 360 if direction is not None else None,
            "compass": compass(direction) if direction is not None else None, "beaufort": strength, "label": name}


def hourly_forecast(hourly: Any, now_iso: Any) -> list[dict]:
    """Hourly values of Open-Meteo -> the next HOURS_AHEAD hours from the running hour (local time)."""
    times = hourly.get("time") if isinstance(hourly, dict) else None
    if not isinstance(times, list):
        return []
    now = str(now_iso or "")[:13]
    start = next((i for i, t in enumerate(times) if str(t)[:13] >= now), len(times)) if now else 0

    def at(key: str, i: int) -> float | None:
        col = hourly.get(key)
        return _number(col[i]) if isinstance(col, list) and i < len(col) else None

    out = []
    for i in range(start, min(len(times), start + HOURS_AHEAD)):
        t = str(times[i])
        code, day = at("weather_code", i), at("is_day", i)
        label, icon = describe(code, day != 0)
        direction = at("wind_direction_10m", i)
        out.append({"time": t[11:16], "day": t[:10], "temperature": at("temperature_2m", i), "label": label, "icon": icon,
                    "pop": at("precipitation_probability", i), "precip": at("precipitation", i),
                    "wind": at("wind_speed_10m", i), "gusts": at("wind_gusts_10m", i),
                    "compass": compass(direction) if direction is not None else None})
    return out


def fx(cur: dict) -> dict:
    """Weather effects on the floor plan – computed in exactly this place from WMO code and readings.

    kind: clear | partly | overcast | fog | rain | snow | storm | thunder; intensities 0..1.
    Wind is an overlay: gusts from STORM_GUSTS_KMH set storm=True (in dry weather also kind=storm),
    rain/snow/thunderstorm keep their kind. gust = measured gusts on the same scale as wind (never smaller
    than the mean wind) – from it the interface derives how strongly the wind swells and subsides. wind_kmh =
    measured mean wind in km/h (wind arrow on the floor plan; `wind` is capped and raised in a storm).
    Further overlays: hail (thunderstorm with hail),
    sleet (rain and snow at once), frost (dry and at most FROST_C; frost_level 0..1 = how much of it, full from FROST_FULL_C),
    heat (0..1 from HEAT_C, by day in sunshine)."""
    try:
        code = int(cur.get("weather_code"))
    except (TypeError, ValueError):
        code = -1
    raw_cloud = cur.get("cloud_cover")
    cloud_pct = _f(raw_cloud) if raw_cloud is not None else None
    rain_mm = max(_f(cur.get("rain")) + _f(cur.get("showers")), _f(cur.get("precipitation")) - _f(cur.get("snowfall")) * 0.7)
    snow_cm = _f(cur.get("snowfall"))
    gusts, wind = _f(cur.get("wind_gusts_10m")), _f(cur.get("wind_speed_10m"))
    if 95 <= code <= 99:
        kind = "thunder"
    elif 71 <= code <= 77 or code in (85, 86):
        kind = "snow"
    elif 51 <= code <= 67 or 80 <= code <= 82:
        kind = "rain"
    elif code in (45, 48):
        kind = "fog"
    elif code == 3 or (cloud_pct is not None and cloud_pct >= 80):
        kind = "overcast"
    elif code in (1, 2) or (cloud_pct is not None and cloud_pct >= 20):
        kind = "partly"
    else:
        kind = "clear"
    storm = gusts >= STORM_GUSTS_KMH
    if storm and kind in ("clear", "partly", "overcast", "fog"):
        kind = "storm"
    if cloud_pct is None:
        cloud_pct = {"clear": 5, "partly": 50, "overcast": 90, "fog": 100, "storm": 70}.get(kind, 95)
    rain = 0.0
    if kind == "rain":   # Nieseln bleibt sichtbar, Schauer/starker Regen mindestens mittel
        rain = max(rain_mm / RAIN_MAX_MM, 0.5 if 80 <= code <= 82 or code in (63, 65, 66, 67) else 0.15)
    elif kind in ("thunder", "storm") and rain_mm > 0:
        rain = max(rain_mm / RAIN_MAX_MM, 0.15)
    snow = max(snow_cm / SNOW_MAX_CM, 0.25) if kind == "snow" else 0.0
    if kind == "rain" and snow_cm > 0:        # sleet: both fall
        snow = max(snow_cm / SNOW_MAX_CM, 0.15)
    elif kind == "snow" and rain_mm > 0:
        rain = max(rain_mm / RAIN_MAX_MM, 0.15)
    raw_temp = cur.get("temperature_2m")
    temp = float(raw_temp) if isinstance(raw_temp, (int, float)) and not isinstance(raw_temp, bool) and raw_temp == raw_temp else None
    is_day = bool(cur.get("is_day", 1))
    frost = temp is not None and temp <= FROST_C and kind in ("clear", "partly", "overcast", "fog")
    frost_level = FROST_MIN + (1 - FROST_MIN) * min(1.0, (FROST_C - temp) / (FROST_C - FROST_FULL_C)) if frost else 0.0
    heat = (temp - HEAT_C) / (HEAT_MAX_C - HEAT_C) if temp is not None and temp >= HEAT_C and is_day and kind in ("clear", "partly") else 0.0
    wind_n = max(wind, gusts * 0.75 if storm else 0.0) / WIND_MAX_KMH
    return {"kind": kind, "cloud": _unit(cloud_pct / 100), "rain": _unit(rain), "snow": _unit(snow),
            "wind": _unit(max(wind_n, 0.75) if storm else wind_n), "gust": _unit(max(gusts, wind) / WIND_MAX_KMH),
            "wind_kmh": int(round(min(max(wind, 0.0), 400.0))), "wind_dir": int(_f(cur.get("wind_direction_10m"))) % 360,
            "thunder": kind == "thunder", "storm": storm, "hail": code in (96, 99), "frost": frost, "frost_level": _unit(frost_level),
            "heat": _unit(heat), "is_day": is_day}


async def _forecast(geo: dict) -> dict:
    # One query for everything: current weather (also wind and gusts for effects and wind report), daily values and the
    # hours until tomorrow – the same service, the same data sent (only the rounded coordinates).
    """Current weather, daily values and the hours until tomorrow – one query, only the rounded
    coordinates are sent."""
    params = {"latitude": geo["lat"], "longitude": geo["lon"], "timezone": "auto", "forecast_days": 2,
              "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,wind_speed_10m,"
                         "cloud_cover,precipitation,rain,showers,snowfall,wind_direction_10m,wind_gusts_10m",
              "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,is_day,wind_speed_10m,"
                        "wind_gusts_10m,wind_direction_10m",
              "daily": "temperature_2m_max,temperature_2m_min"}
    async with _client() as c:
        r = await c.get(WX_URL, params=params)
        r.raise_for_status()
        data = r.json() or {}
    cur = data.get("current") or {}
    if cur.get("temperature_2m") is None:
        raise ValueError("keine Wetterdaten")
    day = data.get("daily") or {}
    first = lambda k: (day.get(k) or [None])[0]   # noqa: E731
    is_day = bool(cur.get("is_day", 1))
    label, icon = describe(cur.get("weather_code"), is_day)
    return {"available": True, "place": geo["place"], "temperature": cur.get("temperature_2m"),
            "apparent": cur.get("apparent_temperature"), "humidity": cur.get("relative_humidity_2m"),
            "wind": cur.get("wind_speed_10m"), "code": cur.get("weather_code"), "is_day": is_day,
            "label": label, "icon": icon, "max": first("temperature_2m_max"), "min": first("temperature_2m_min"),
            "fx": fx(cur), "wind_report": wind_report(cur), "today": hourly_forecast(data.get("hourly"), cur.get("time")),
            "updated": int(time.time())}


# Weather of the demo home (demo.py): light rain in a fresh wind from west-southwest – fixed values, no query.
DEMO_CURRENT = {"temperature_2m": 9.4, "apparent_temperature": 6.8, "relative_humidity_2m": 84, "weather_code": 61,
                "rain": 1.4, "precipitation": 1.4, "cloud_cover": 85, "wind_speed_10m": 26, "wind_gusts_10m": 42,
                "wind_direction_10m": 245}


def demo_weather() -> dict:
    """Weather of the demo home: the sample values with today's time of day. The demo sends nothing to the internet."""
    hour = time.localtime().tm_hour
    cur = {**DEMO_CURRENT, "time": time.strftime("%Y-%m-%dT%H:%M"), "is_day": 1 if 7 <= hour < 18 else 0}
    label, icon = describe(cur["weather_code"], bool(cur["is_day"]))
    return {"available": True, "place": "Musterstadt", "temperature": cur["temperature_2m"],
            "apparent": cur["apparent_temperature"], "humidity": cur["relative_humidity_2m"], "wind": cur["wind_speed_10m"],
            "code": cur["weather_code"], "is_day": bool(cur["is_day"]), "label": label, "icon": icon, "max": 11.2, "min": 5.1,
            "fx": fx(cur), "wind_report": wind_report(cur), "today": [], "updated": int(time.time())}


async def current(address: Any) -> dict:
    """Current weather for the address; never an exception - on problems available=False with the reason. In the
    demo the sample weather (no query)."""
    if demo.active():
        return demo_weather()
    q = place_query(address)
    if not q:
        return {"available": False, "reason": "Für das Wetter fehlt der Ort (PLZ oder Ort) in der Adresse."}
    key = f"{q['postcode'] or ''}|{q['name'].lower()}"

    def fresh() -> dict | None:
        d = _mem["data"]
        if _mem["key"] == key and d is not None and time.time() - _mem["at"] < (TTL if d.get("available") else FAIL_TTL):
            return d
        return None

    if (hit := fresh()) is not None:
        return hit
    async with _lock:
        if (hit := fresh()) is not None:
            return hit
        try:
            data = await _forecast(await _geocode(q, key))
        except LookupError:
            data = {"available": False, "reason": f"Ort „{q['name']}“ nicht gefunden."}
        except Exception:
            data = {"available": False, "reason": "Wetterdienst gerade nicht erreichbar."}
        _mem.update(key=key, at=time.time(), data=data)
        return data


# ---------------------------------------------------------------- Amtliche Wetterwarnungen (DWD)
def _ts(v: Any) -> int | None:
    """ISO time as Unix time, or None."""
    try:
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def _text(v: Any, limit: int) -> str:
    """Text from a foreign response: whitespace cleaned and shortened to `limit` characters."""
    return " ".join(str(v or "").split())[:limit]


def parse_warnings(data: Any, now: float | None = None) -> list[dict]:
    """GeoJSON of the DWD -> list of the valid warnings, most important first (already running, then level, then start).

    Only actual messages (STATUS Actual, no Cancel), nothing expired; the same warning for several
    parts of a municipality counts once. `windows` = in this weather windows should be closed."""
    now = time.time() if now is None else now
    out: list[dict] = []
    seen: set[tuple] = set()
    feats = data.get("features") if isinstance(data, dict) else None
    for f in feats if isinstance(feats, list) else []:
        p = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(p, dict):
            continue
        if str(p.get("STATUS") or "Actual").lower() != "actual" or str(p.get("MSGTYPE") or "").lower() == "cancel":
            continue
        onset, expires = _ts(p.get("ONSET")), _ts(p.get("EXPIRES"))
        event = _text(p.get("EVENT"), 60)
        if not event or (expires is not None and expires <= now):
            continue
        key = (event, onset, expires)
        if key in seen:
            continue
        seen.add(key)
        groups = {g.strip().upper() for g in str(p.get("EC_GROUP") or "").split(";")}
        out.append({"id": _text(p.get("IDENTIFIER"), 140) or "|".join(str(x) for x in key), "event": event,
                    "headline": _text(p.get("HEADLINE"), 140) or event,
                    "level": _LEVELS.get(str(p.get("SEVERITY") or "").lower(), 1),
                    "onset": onset, "expires": expires, "active": onset is None or onset <= now,
                    "description": _text(p.get("DESCRIPTION"), 700), "instruction": _text(p.get("INSTRUCTION"), 700),
                    "windows": bool(groups & _WINDOW_GROUPS) or any(w in event.upper() for w in _WINDOW_WORDS)})
    out.sort(key=lambda w: (not w["active"], -w["level"], w["onset"] or 0))
    return out[:WARN_MAX]


def _valid(items: list[dict]) -> list[dict]:
    """Warnings that have not expired yet, each with the mark whether it already applies (`active`) or is still ahead."""
    now = time.time()
    return [{**w, "active": w["onset"] is None or w["onset"] <= now} for w in items if w["expires"] is None or w["expires"] > now]


async def _fetch_warnings(geo: dict) -> list[dict]:
    """Fetch official weather warnings for the place – only the rounded coordinates are sent."""
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeName": "dwd:Warnungen_Gemeinden",
              "outputFormat": "application/json", "count": "40", "propertyName": WARN_FIELDS,
              "CQL_FILTER": f"INTERSECTS(THE_GEOM, POINT({float(geo['lat']):.2f} {float(geo['lon']):.2f}))"}
    async with _warn_client() as c:
        r = await c.get(WARN_URL, params=params)
        r.raise_for_status()
        return parse_warnings(r.json())


async def alerts(address: Any) -> list[dict]:
    """Official warnings of the DWD for the place of the address; never an exception.

    The DWD only warns for Germany: if the place found is elsewhere, it is not asked at all.
    If the service is unreachable, the warnings loaded last stay until they expire. The demo has no warnings."""
    if demo.active():
        return []
    q = place_query(address)
    if not q:
        return []
    key = f"{q['postcode'] or ''}|{q['name'].lower()}"

    def fresh() -> list[dict] | None:
        if _warn["key"] == key and time.time() - _warn["at"] < (WARN_TTL if _warn["ok"] else FAIL_TTL):
            return _valid(_warn["items"])
        return None

    if (hit := fresh()) is not None:
        return hit
    async with _warn_lock:
        if (hit := fresh()) is not None:
            return hit
        try:
            geo = await _geocode(q, key)
            items = await _fetch_warnings(geo) if geo.get("cc") in (None, "DE") else []
            ok = True
        except Exception:
            items, ok = (_warn["items"] if _warn["key"] == key else []), False
        _warn.update(key=key, at=time.time(), ok=ok, items=items)
        return _valid(items)


def reset() -> None:
    """Clear the cache (after an address change, in tests)."""
    _mem.update(key=None, at=0.0, data=None)
    _warn.update(key=None, at=0.0, ok=False, items=[])
