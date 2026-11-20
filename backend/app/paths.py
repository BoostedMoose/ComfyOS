"""Central paths of ComfyOS.

APP_ROOT   = program folder (code). Only this one is replaced by the updater.
DATA_ROOT  = place for user data and logs (default: APP_ROOT, changeable with COMFYOS_HOME)

    <DATA_ROOT>/data/        routines, floor plan, room names, home/away
    <DATA_ROOT>/logs/
        heating/             heating history (measured/target), JSONL per month
        power/               power consumption (watts per device), JSONL per month
        events/              event log, JSONL per month
        messages/            system and update messages, JSONL per month

Credentials, login and connection settings deliberately live outside the
program folder in the user profile (see settings_store.APP_DIR), so that they
are neither lost during an update nor copied along when the folder is
passed on.
"""
from __future__ import annotations

from pathlib import Path

from .brand import env

APP_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(env("HOME") or APP_ROOT)

DATA_DIR = DATA_ROOT / "data"
LOGS_DIR = DATA_ROOT / "logs"
HEATING_LOG_DIR = LOGS_DIR / "heating"
POWER_LOG_DIR = LOGS_DIR / "power"
EVENTS_LOG_DIR = LOGS_DIR / "events"
MESSAGES_LOG_DIR = LOGS_DIR / "messages"

ROUTINES_FILE = DATA_DIR / "routines.json"
FLOORPLAN_FILE = DATA_DIR / "floorplan.json"
ROOMS_FILE = DATA_DIR / "rooms.json"
HOME_FILE = DATA_DIR / "home.json"

VERSION_FILE = APP_ROOT / "VERSION"
MANIFEST_FILE = APP_ROOT / "MANIFEST"

# Directories in the program folder that the updater never touches.
PRESERVED_DIRS = ("data", "logs", ".venv", "venv")


def ensure_dirs() -> None:
    """Create the folders for data and logs if they are missing."""
    for d in (DATA_DIR, HEATING_LOG_DIR, POWER_LOG_DIR, EVENTS_LOG_DIR, MESSAGES_LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def app_version() -> str:
    """Version from the file VERSION (0.0.0 if it is missing)."""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"
