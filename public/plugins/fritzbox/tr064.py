"""Presence through the network of a FRITZ!Box (TR-064, service "Hosts"). No network access.

The box knows every device of its network and whether it is active right now (in the Wi-Fi or at a cable).
Two actions of the service ``urn:dslforum-org:service:Hosts:1`` on ``http://<box>:49000/upnp/control/hosts``:

* ``X_AVM-DE_GetHostListPath`` names a file with the whole list (``/devicehostlist.lua?sid=…``); the plugin reads
  it to offer the devices in its tile.
* ``GetSpecificHostEntry`` with ``NewMACAddress`` says for one device whether it is active (``NewActive``).

TR-064 signs in with HTTP digest authentication – the password is never sent as it is. It works only when
"Zugriff für Anwendungen zulassen" is switched on in the box (Heimnetz → Netzwerk → Netzwerkeinstellungen).
Answers are foreign input and read with the same limits as the device list (aha.parse_xml).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from xml.sax.saxutils import escape

from .aha import AhaError, parse_xml

PORT = 49000
CONTROL_PATH = "/upnp/control/hosts"
SERVICE = "urn:dslforum-org:service:Hosts:1"
MAX_HOSTS = 500
_MAC = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_LIST_PATH = re.compile(r"^/[A-Za-z0-9_./-]{1,80}\.lua\?sid=[0-9a-f]{16}$")


@dataclass(frozen=True)
class Host:
    """One device of the network the box knows."""

    mac: str                                    # upper case with colons, e.g. "AA:BB:CC:DD:EE:FF"
    name: str
    ip: str | None
    active: bool


def clean_mac(value: object) -> str | None:
    """'aa-bb-cc-dd-ee-ff' or 'aa:bb:…' -> 'AA:BB:CC:DD:EE:FF'; None for anything else."""
    text = str(value or "").strip().replace("-", ":").upper()
    return text if _MAC.match(text) else None


def soap_body(action: str, arguments: dict[str, str] | None = None) -> str:
    """The SOAP envelope for one action of the Hosts service."""
    inner = "".join(f"<{name}>{escape(value)}</{name}>" for name, value in (arguments or {}).items())
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{SERVICE}">{inner}</u:{action}></s:Body></s:Envelope>')


def soap_headers(action: str) -> dict[str, str]:
    """HTTP headers of a SOAP request."""
    return {"Content-Type": 'text/xml; charset="utf-8"', "SoapAction": f"{SERVICE}#{action}"}


def _value(text: str, name: str) -> str | None:
    """Text of the first element with this local name in a SOAP answer."""
    root = parse_xml(text)
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name:
            return (element.text or "").strip()
    return None


def host_list_path(text: str) -> str:
    """Path of the host list from the answer of ``X_AVM-DE_GetHostListPath``. Raises ``AhaError`` for anything
    else – the path is used for a request, so only the known form is accepted."""
    path = _value(text, "NewX_AVM-DE_HostListPath") or ""
    if not _LIST_PATH.match(path):
        raise AhaError("unexpected host list path")
    return path


def is_active(text: str) -> bool | None:
    """``NewActive`` of the answer of ``GetSpecificHostEntry``: True/False, None when it is missing."""
    value = _value(text, "NewActive")
    return None if value not in ("0", "1") else value == "1"


def parse_hosts(text: str) -> list[Host]:
    """Read the host list (``<List><Item>…</Item></List>``). Devices without a MAC address are left out."""
    root = parse_xml(text)
    hosts: dict[str, Host] = {}
    for item in root.iter("Item"):
        mac = clean_mac(item.findtext("MACAddress"))
        if mac is None or len(hosts) >= MAX_HOSTS:
            continue
        name = " ".join(str(item.findtext("HostName") or "").split())[:60] or mac
        ip = str(item.findtext("IPAddress") or "").strip()[:45] or None
        hosts[mac] = Host(mac=mac, name=name, ip=ip, active=(item.findtext("Active") or "").strip() == "1")
    return sorted(hosts.values(), key=lambda host: host.name.casefold())
