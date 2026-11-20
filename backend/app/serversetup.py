"""Server setup for Raspberry Pi / Linux: set up or remove ComfyOS as a system service.

Runs install_pi.sh through `sudo -n systemd-run`. The script thereby runs as a
short-lived unit of its own (comfyos-setup) and also survives a restart or the
stopping of ComfyOS itself. Prerequisite: the user may use sudo without a password
(the default for the user of Raspberry Pi OS). Output: logs/serversetup.log
"""
from __future__ import annotations

import getpass
import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from .paths import APP_ROOT, LOGS_DIR, ensure_dirs

UNIT_FILE = Path("/etc/systemd/system/comfyos.service")
JOB = "comfyos-setup"
LOG = LOGS_DIR / "serversetup.log"
SCRIPT = APP_ROOT / "install_pi.sh"
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


def _run(cmd: list[str], timeout: float = 8) -> tuple[int, str]:
    """Run a command → (return code, output). If the call itself fails, code 1 comes back with the error text."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _model() -> str:
    """Name of the device (e.g. the model of the Raspberry Pi), otherwise the platform."""
    try:
        return Path("/proc/device-tree/model").read_text(errors="ignore").strip("\x00 \n")
    except OSError:
        return platform.platform()


def status() -> dict[str, Any]:
    """State of the autostart: is it supported here, is the service set up and active, is a setup
    running right now, plus the end of its log."""
    supported = platform.system() == "Linux" and shutil.which("systemctl") is not None and Path("/run/systemd/system").is_dir()
    st: dict[str, Any] = {"supported": supported, "device": _model(), "hostname": socket.gethostname(),
                          "installed": False, "active": "", "enabled": "", "job_running": False,
                          "sudo": False, "as_service": bool(os.environ.get("INVOCATION_ID")), "log": ""}
    if not supported:
        return st
    st["installed"] = UNIT_FILE.exists()
    st["active"] = _run(["systemctl", "is-active", "comfyos.service"])[1]
    st["enabled"] = _run(["systemctl", "is-enabled", "comfyos.service"])[1]
    st["job_running"] = _run(["systemctl", "is-active", JOB])[1] in ("active", "activating")
    st["sudo"] = _run(["sudo", "-n", "true"])[0] == 0
    try:
        st["log"] = "\n".join(LOG.read_text(errors="ignore").splitlines()[-40:])
    except OSError:
        pass   # no log yet
    return st


def start(action: str, hostname: str = "comfyos") -> dict[str, Any]:
    """Set up or remove the service. Runs as a systemd job of its own, so that it survives a restart of
    ComfyOS. Raises RuntimeError with a text for the user if it does not work here."""
    st = status()
    if not st["supported"]:
        raise RuntimeError("Serversetup geht nur auf dem Raspberry Pi bzw. Linux mit systemd.")
    if st["job_running"]:
        raise RuntimeError("Die Einrichtung läuft bereits.")
    if not st["sudo"]:
        raise RuntimeError("Keine Administratorrechte: Der Benutzer darf sudo nicht ohne Passwort ausführen. "
                           "Dann einmalig im Terminal: sudo ./install_pi.sh")
    hostname = (hostname or "comfyos").strip().lower()
    if not NAME_RE.match(hostname):
        raise RuntimeError("Ungültiger Name: nur a–z, 0–9 und Bindestrich, max. 32 Zeichen.")
    if action not in ("install", "uninstall"):
        raise RuntimeError("Unbekannte Aktion.")
    ensure_dirs()
    LOG.write_text("", encoding="utf-8")
    arg = " --uninstall" if action == "uninstall" else ""
    _run(["sudo", "-n", "systemctl", "reset-failed", JOB])
    cmd = ["sudo", "-n", "systemd-run", f"--unit={JOB}", "--collect", "--quiet",
           f"--setenv=COMFYOS_USER={getpass.getuser()}", f"--setenv=COMFYOS_HOSTNAME={hostname}",
           "/bin/bash", "-c", f"/bin/bash '{SCRIPT}'{arg} >> '{LOG}' 2>&1"]
    code, out = _run(cmd, timeout=20)
    if code != 0:
        raise RuntimeError(f"Start fehlgeschlagen: {out}")
    return status()
