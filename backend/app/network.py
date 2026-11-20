"""Reachability in the home network: host name, IP and addresses for phone/browser.

The name "comfyos" comes from the host name of the device ComfyOS runs on:
  * FRITZ!Box: http://comfyos.fritz.box/ – works on every device in the home network.
    The short name http://comfyos/ only works where the system itself appends ".fritz.box"
    (Windows/Mac/Linux); phones do not do that, Android ignores the
    search domain altogether.
  * mDNS/Bonjour: http://comfyos.local/ (iPhone, Mac, Windows, Linux, Android from 12)
  Private DNS (Android) or iCloud Private Relay bypass the FRITZ!Box – then
  only .local or the IP helps.
On the Raspberry Pi install_pi.sh sets the host name and port 80.
On Windows the name can be assigned in the FRITZ!Box (Heimnetz → Netzwerk →
Gerät bearbeiten → Name "comfyos").
"""
from __future__ import annotations

import socket


def _lan_ip() -> str | None:
    """Own address in the home network, or None. Opens a UDP socket for that without sending anything – that way the
    operating system names the interface through which it would reach the network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # sends nothing, only determines the route
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


def info(port: int, scheme: str = "http") -> dict:
    """Under which addresses ComfyOS is reachable in the home network (display under System): host name, IP, port and
    a list of ready-made addresses."""
    host = socket.gethostname().split(".")[0]
    ip = _lan_ip()
    suffix = "" if port == (443 if scheme == "https" else 80) else f":{port}"
    urls = []
    named = host.lower()
    urls.append({"label": "Handy & PC (FRITZ!Box) – empfohlen", "url": f"{scheme}://{named}.fritz.box{suffix}/"})
    urls.append({"label": "Bonjour / mDNS (iPhone, Mac, Android ab 12)", "url": f"{scheme}://{named}.local{suffix}/"})
    urls.append({"label": "Kurzname – nur am PC, nicht auf dem Handy", "url": f"{scheme}://{named}{suffix}/"})
    if ip:
        urls.append({"label": "IP-Adresse (funktioniert immer)", "url": f"{scheme}://{ip}{suffix}/"})
    return {"hostname": host, "ip": ip, "port": port, "is_own_name": named == "comfyos",
            "port80": port in (80, 443), "scheme": scheme, "urls": urls}
