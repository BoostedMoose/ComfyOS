"""Light as pure computation (without I/O): colour spaces and scene distribution – the light model of the core.

ComfyOS stores colours as "#rrggbb" (scenes, flows), white tones as mirek (1 000 000 / kelvin).
The light state of a lamp (`light` in the device row) has the form
    {caps: {ct: [min, max] | None, color, gradient, gradient_modes, effects, effect_params},
     mode: ct | xy | gradient, xy, mirek, points, gmode, effect}
and comes from the plugin that controls the lamp (plugin_api.LightState -> device_merge.light_dict). How it becomes
a command for a certain bridge only the plugin knows (Hue: public/plugins/hue/clip.py).

The colour conversions live in plugin_api (public, so that plugins use the same formulas) and are only
passed through here.
"""
from __future__ import annotations

from typing import Any

from .plugin_api import (DEFAULT_WHITE_RANGE as DEFAULT_CT, HEX_COLOR as HEX_RE, WARM_WHITE, hex_to_rgb, hex_to_xy,  # noqa: F401
                         kelvin_to_mirek, mirek_to_hex, rgb_to_hex, xy_to_hex, xy_to_mirek)

MAX_DURATION_MS = 60_000


def restore_state(d: dict[str, Any]) -> dict[str, Any]:
    """Current state of a device as a command for restoring it later (ending a scene)."""
    if not d.get("on"):
        return {"on": False}
    out: dict[str, Any] = {"on": True}
    st = d.get("light")
    if st is None:
        return out
    if d.get("brightness"):
        out["brightness"] = d["brightness"]
    if st.get("mode") == "gradient" and len(st.get("points") or []) >= 2:
        out["gradient_xy"] = st["points"]
    elif st.get("mode") == "ct" and st.get("mirek"):
        out["mirek"] = st["mirek"]
    elif st.get("xy"):
        out["xy"] = st["xy"]
    if st.get("effect"):
        out["effect"] = st["effect"]
    return out


# ---------------------------------------------------------------- Szenen
def palette_from_lamps(devices: list[dict[str, Any]], limit: int = 9) -> list[dict[str, Any]]:
    """Palette from the current light (scene "Als neue Szene"): per lamp that is on its colour(s),
    without duplicates, at most `limit`. Without a lamp that is on: warm white."""
    pal: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for d in devices:
        if not d.get("on"):
            continue
        b = int(max(1, min(100, round(float(d.get("brightness") or 100)))))
        st = d.get("light") or {}
        if st.get("mode") == "gradient" and len(st.get("points") or []) >= 2:
            entries = [{"c": xy_to_hex(*p), "b": b} for p in st["points"]]
        elif st.get("mode") == "ct" and st.get("mirek"):
            entries = [{"ct": int(max(153, min(500, round(st["mirek"])))), "b": b}]
        elif st.get("xy"):
            entries = [{"c": xy_to_hex(*st["xy"]), "b": b}]
        else:
            entries = [{"ct": WARM_WHITE, "b": b}]
        for e in entries:
            key = e.get("c") or ("ct", e.get("ct"))
            if key in seen:
                continue
            seen.add(key)
            pal.append(e)
            if len(pal) >= limit:
                return pal
    return pal or [{"ct": WARM_WHITE, "b": 100}]


def entry_hex(e: dict[str, Any]) -> str:
    """Palette entry as a hex colour for display – a white tone is converted into a colour for that."""
    return e["c"] if e.get("c") else mirek_to_hex(e.get("ct") or WARM_WHITE)


def scene_states(palette: list[dict[str, Any]], devices: list[dict[str, Any]], offset: int = 0,
                 brightness: float | None = None, duration: int = 400) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Distribute a palette over lamps (like Hue: in turn, gradient lamps get several colours).

    devices: devices with kind 'light' (passed sorted = stable assignment). offset shifts the
    assignment (dynamic scenes). brightness overrides the brightness of all colours."""
    pal = [e for e in palette if isinstance(e, dict) and (e.get("c") or e.get("ct"))]
    if not pal:
        return []
    out = []
    for i, d in enumerate(devices):
        e = pal[(i + offset) % len(pal)]
        bri = brightness if brightness is not None else e.get("b", 100)
        st: dict[str, Any] = {"on": True, "brightness": bri, "duration": duration}
        info = d.get("light")
        caps = (info or {}).get("caps") or {}
        colours = [entry_hex(pal[(i + offset + k) % len(pal)]) for k in range(len(pal))]
        if caps.get("gradient") and len({c for c in colours}) >= 2:
            st["gradient"] = colours[:max(2, min(caps["gradient"], len(colours)))]
            if brightness is None:
                st["brightness"] = round(sum(x.get("b", 100) for x in pal) / len(pal))
        elif e.get("c"):
            st["color"] = e["c"]
        else:
            st["mirek"] = e["ct"]
        if info is None:   # lamp without colour control (e.g. through the Home Base): only switch on
            st = {"on": True}
        out.append((d, st))
    return out
