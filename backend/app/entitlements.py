"""Plans, features and limits of ComfyOS (Basic / Pro / Owner) – the one place for everything that is unlocked.

Enforced in three places: routes (`require_feature`), content when saving (`check_floorplan`,
`check_flow`, `check_enable`, `cap`) and the flow engine at run time (`paused_flows`).

Armed since 0.26: ENFORCE is True, the plan of the installation applies. Licence errors never mean a lock-out:
without a valid licence Basic applies, data is never changed.

Shadow mode (ENFORCE False, only the test suite still switches it on): everything counts as unlocked, every gate
only remembers what Basic would block (`shadow`, once per reason on the console).

The effective plan is set by the licence client (`license.apply`). The owner's preview sets
`enforce=True` along with it: the chosen plan is then enforced on this installation even in shadow mode. That only restricts, never unlocks – there is no switch that grants rights without a licence.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from . import floorplan_store, flow_catalog

ENFORCE = True
BUY_URL = ""

# id -> (Titel, Beschreibung, Status)
FEATURES: dict[str, tuple[str, str, str]] = {
    "ai_import": ("KI-Import von Heizplänen", "Heizpläne aus einer KI-Antwort prüfen und übernehmen.", "available"),
    "flow_ai_import": ("KI-Import von Flows", "Automatisierungen aus einer KI-Antwort prüfen und übernehmen.", "available"),
    "floorplan_elements": ("Fenster und Heizkörper", "Fenster mit Kontaktsensor und Heizkörper im Grundriss.", "available"),
    "floorplan_heatmap": ("Wärme-Heatmap", "Raumtemperaturen als Farbverlauf im Grundriss.", "available"),
    "floorplan_weather": ("Wettereffekte", "Regen, Schnee, Wind, Gewitter und Sonnenlicht rund um den Grundriss.", "available"),
    "custom_appearance": ("Gerätedarstellung", "Eigene Symbole für Geräte im Grundriss, frei skalierbar.", "available"),
    "flow_pro_nodes": ("Pro-Bausteine", "Timing, Zustände, Zufall, Webhook, HTTP und Diagnose in ComfyOS Flow.", "available"),
    "multi_floorplan": ("Mehrere Stockwerke", "Weitere Stockwerke im Grundriss – Keller, Obergeschoss, Dach.", "available"),
    "advanced_dashboard": ("Mehrere Widget-Seiten", "Weitere Seiten in der Widget-Ansicht der Startseite.", "available"),
    "energy_analytics": ("Energieauswertung", "Verbrauchsverlauf als Graph und CSV-Export.", "available"),
    "custom_icons": ("Eigene Gerätesymbole", "Hochgeladene Symbole für Geräte.", "planned"),
    "custom_accent": ("Eigene Akzentfarbe", "Farbschema von ComfyOS individuell anpassen.", "available"),
}

LIMITS: dict[str, str] = {
    "floorplan_rooms": "Räume im Grundriss",
    "flows_active": "Aktive Automatisierungen",
    "history_hours": "Verlauf (Stunden)",
}

# None = unlimited. "*" = all features, future ones too.
PLANS: dict[str, dict[str, Any]] = {
    "basic": {"title": "Basic", "features": [],
              "limits": {"floorplan_rooms": 6, "flows_active": 5, "history_hours": 168}},
    "pro": {"title": "Pro",
            "features": ["ai_import", "flow_ai_import", "floorplan_elements", "floorplan_heatmap", "floorplan_weather",
                         "custom_appearance", "flow_pro_nodes", "energy_analytics", "custom_accent",
                         "advanced_dashboard", "multi_floorplan"],
            "limits": {"floorplan_rooms": None, "flows_active": None, "history_hours": 744}},
    "owner": {"title": "Owner", "features": ["*"],
              "limits": {"floorplan_rooms": None, "flows_active": None, "history_hours": None}},
}

PRO_NODE_TYPES: frozenset[str] = flow_catalog.PRO_TYPES

_plan = "basic"
_force = False   # owner's preview: enforce the plan although ENFORCE is off
shadow_hits: dict[str, str] = {}


# ---------------------------------------------------------------- Plan and rights
def current_plan() -> str:
    """The plan in effect (basic if nothing or something unknown is set)."""
    return _plan if _plan in PLANS else "basic"


def use_plan(name: str, enforce: bool = False) -> None:
    """Set the effective plan (licence client; tests). Unknown plans count as Basic.
    enforce=True (only the owner's preview) enforces the plan in shadow mode too."""
    global _plan, _force
    _plan = name if name in PLANS else "basic"
    _force = bool(enforce)


def enforcing() -> bool:
    """Is the plan enforced? Otherwise shadow mode runs: violations are only remembered."""
    return ENFORCE or _force


def plan_rank(name: str | None) -> int:
    """Order of the plans (Basic < Pro < Owner); unknown = Basic."""
    return list(PLANS).index(name) if name in PLANS else 0


def granted(plan: str | None = None) -> frozenset[str]:
    """Features a plan unlocks (without an argument: the one in effect)."""
    feats = PLANS.get(plan or current_plan(), PLANS["basic"])["features"]
    return frozenset(FEATURES) if "*" in feats else frozenset(f for f in feats if f in FEATURES)


def plan_limit(key: str, plan: str | None = None) -> int | None:
    """Limit of a plan for a counter such as `flows_active` (None = unlimited)."""
    return PLANS.get(plan or current_plan(), PLANS["basic"])["limits"].get(key, PLANS["basic"]["limits"][key])


class ProRequired(HTTPException):
    """Error 402 "Pro required" in the form the interface shows as a hint with a purchase link."""
    def __init__(self, key: str, message: str):
        """`key` is the feature or limit, `message` the sentence for the user."""
        title = FEATURES[key][0] if key in FEATURES else LIMITS.get(key, key)
        super().__init__(402, {"code": "pro_required", "feature": key, "title": title, "message": message})


def shadow(key: str, message: str) -> None:
    """Shadow mode: remember what Basic would block (once per item on the console too)."""
    if key not in shadow_hits:
        print(f"  [Basic würde blockieren] {message}")
    shadow_hits[key] = message


def deny(key: str, message: str) -> None:
    """Basic does not allow this: in shadow mode only remember, otherwise 402."""
    if not enforcing():
        shadow(key, message)
        return
    raise ProRequired(key, message)


def gate(feature: str) -> None:
    """Require a feature: if the plan lacks it, 402 – in shadow mode only a note."""
    if feature not in granted():
        deny(feature, f"{FEATURES[feature][0]} ist eine Pro-Funktion.")


def require_feature(feature: str):
    """Route dependency: `dependencies=[require_feature("…")]`. The test reads `.feature`."""
    if feature not in FEATURES:
        raise ValueError(feature)

    async def dep() -> None:
        gate(feature)
    dep.feature = feature   # type: ignore[attr-defined]
    return Depends(dep)


def cap(key: str, value: int) -> int:
    """Cap a value at the limit of the plan (history)."""
    lim = plan_limit(key)
    if lim is None or value <= lim:
        return value
    if not enforcing():
        shadow(key, f"{LIMITS[key]}: {value} statt höchstens {lim}")
        return value
    return lim


# ---------------------------------------------------------------- Content (monotonic: never take anything away)
def _appearance(plan: dict) -> int:
    """Counts what in the floor plan belongs to the Pro feature "Darstellung": devices shown as a plan symbol (only
    those have a size of their own) and – since 0.29 – loads shown as one; a load as a bubble is Basic."""
    return sum(1 for f in floorplan_store.floors(plan)
               for d in list((f.get("devices") or {}).values()) + list(f.get("loads") or []) if d.get("furniture"))


def _elements(plan: dict) -> int:
    """Counts windows and radiators in the floor plan."""
    return sum(1 for f in floorplan_store.floors(plan) for o in f.get("openings") or []
               if o.get("type") in ("window", "radiator"))


def check_floorplan(old: dict, new: dict) -> None:
    """Basic only rejects what increases the Pro usage. Existing things can still be moved and deleted."""
    ents = granted()
    lim = plan_limit("floorplan_rooms")
    rooms_new, rooms_old = len(floorplan_store.rooms(new)), len(floorplan_store.rooms(old))
    if lim is not None and rooms_new > lim and rooms_new > rooms_old:
        deny("floorplan_rooms", f"Basic erlaubt bis zu {lim} Räume im Grundriss.")
    if "multi_floorplan" not in ents and len(new.get("floors") or []) > len(old.get("floors") or []):
        deny("multi_floorplan", "Mehrere Stockwerke im Grundriss sind eine Pro-Funktion.")
    if "floorplan_elements" not in ents and _elements(new) > _elements(old):
        deny("floorplan_elements", "Fenster und Heizkörper im Grundriss sind Pro-Funktionen.")
    if "floorplan_heatmap" not in ents and new.get("heatmap") and not old.get("heatmap"):
        deny("floorplan_heatmap", "Die Wärme-Heatmap ist eine Pro-Funktion.")
    if "custom_appearance" not in ents and _appearance(new) > _appearance(old):
        deny("custom_appearance", "Eigene Symbole für Geräte im Grundriss sind eine Pro-Funktion.")


def pro_nodes(nodes: list) -> int:
    """Pro nodes of a flow – also Basic nodes in which a Pro setting is used."""
    return sum(1 for n in nodes or [] if flow_catalog.is_pro(n))


def check_flow(old_nodes: list, new_nodes: list) -> None:
    """Check whether a flow gets new Pro nodes. Existing ones may stay; without Pro none are added."""
    if "flow_pro_nodes" not in granted() and pro_nodes(new_nodes) > pro_nodes(old_nodes):
        deny("flow_pro_nodes", "Dieser Flow enthält neue Pro-Bausteine.")


def check_enable(flow: dict, flows: list[dict]) -> None:
    """Switching on: no Pro nodes without Pro, at most `flows_active` active flows."""
    if "flow_pro_nodes" not in granted() and pro_nodes(flow.get("nodes")):
        deny("flow_pro_nodes", "Flows mit Pro-Bausteinen laufen nur mit Pro.")
    lim = plan_limit("flows_active")
    active = sum(1 for f in flows if f.get("enabled") and f.get("id") != flow.get("id")) + 1
    if lim is not None and active > lim:
        deny("flows_active", f"Basic erlaubt bis zu {lim} aktive Automatisierungen.")


# Widget view (since 0.24): the view itself is Basic. A widget is Pro where its function already is – a setting of a
# widget type that shows a Pro feature: (type, setting) -> feature. Further pages are the feature "advanced_dashboard".
WIDGET_FEATURES: dict[tuple[str, str], str] = {
    ("weather", "forecast"): "floorplan_weather",      # wind report and hourly forecast
    ("power", "chart"): "energy_analytics",            # consumption history as a graph
    ("floorplan", "heat"): "floorplan_heatmap",        # heat map in the floor plan widget
    ("floorplan", "fx"): "floorplan_weather",          # weather effects in the floor plan widget
}
WIDGET_PAGES_BASIC = 1


def widget_usage(doc: dict) -> dict[str, int]:
    """Counts per Pro feature how many widgets of a widget view use it."""
    out: dict[str, int] = {}
    for page in doc.get("pages") or []:
        for widget in page.get("widgets") or []:
            for (kind, option), feature in WIDGET_FEATURES.items():
                if widget.get("type") == kind and (widget.get("opts") or {}).get(option) is True:
                    out[feature] = out.get(feature, 0) + 1
    return out


def check_widgets(old: dict, new: dict) -> None:
    """Basic only rejects what increases the Pro usage of the widget view: one more page beyond the first, one more
    widget with a Pro setting. Existing pages and widgets can still be moved, changed and deleted."""
    ents = granted()
    pages_new, pages_old = len(new.get("pages") or []), len(old.get("pages") or [])
    if "advanced_dashboard" not in ents and pages_new > WIDGET_PAGES_BASIC and pages_new > pages_old:
        deny("advanced_dashboard", "Weitere Widget-Seiten gehören zu ComfyOS Pro.")
    before, after = widget_usage(old), widget_usage(new)
    for feature, count in after.items():
        if feature not in ents and count > before.get(feature, 0):
            deny(feature, f"{FEATURES[feature][0]} im Widget ist eine Pro-Funktion.")


def paused_flows(flows: list[dict]) -> dict[str, str]:
    """Enabled flows the plan does not run: 'pro' (Pro nodes) or 'limit' (above the limit,
    the oldest ones run). Also holds for a hand-edited flows.json – the engine asks here at run time."""
    out: dict[str, str] = {}
    pro_ok = "flow_pro_nodes" in granted()
    lim = plan_limit("flows_active")
    running = 0
    for f in sorted((f for f in flows if f.get("enabled")), key=lambda f: (f.get("created") or 0, f.get("id") or "")):
        if not pro_ok and pro_nodes(f.get("nodes")):
            out[f["id"]] = "pro"
        elif lim is not None and running >= lim:
            out[f["id"]] = "limit"
        else:
            running += 1
    if out and not enforcing():
        shadow("flows_paused", f"{len(out)} aktive Flows würden in Basic pausieren")
        return {}
    return out


# ---------------------------------------------------------------- For the interface
def status() -> dict[str, Any]:
    """Plan, features with their release, limits and notes of the shadow mode – for the interface."""
    plan = current_plan()
    ents = granted(plan)
    return {"enforce": enforcing(), "plan": plan, "plan_title": PLANS[plan]["title"], "buy_url": BUY_URL,
            "features": {k: {"title": t, "desc": d, "status": s, "granted": k in ents}
                         for k, (t, d, s) in FEATURES.items()},
            "limits": {k: plan_limit(k, plan) for k in LIMITS},
            "pro_nodes": sorted(PRO_NODE_TYPES), "shadow": dict(shadow_hits)}
