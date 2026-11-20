"""Scaffolding of the security tests (tests/security): outside world cut off, raw requests, collecting findings.

The security tests attack ComfyOS like three attackers: without login in the home network, through a foreign web
page in the owner's browser and with a valid session. This module provides what all of them need:

- **Safeguard:** the test must not touch anything real. Connections to the outside end up in `outbound`, the Home
  Base accepts no login, restart, server setup and update are cut off (`restarts`, `setups`, `prog`).
- **Raw requests** (`raw_for`): directly to the ASGI app, the way uvicorn hands them over. An HTTP client would
  normalise paths such as /a/../b beforehand – exactly the tricks this is about.
- **Collecting findings** (`check`, `finish`): one run shows all gaps instead of only the first.

A security test starts with `from support.attack import *` and ends with `finish("…")`.
"""
from __future__ import annotations

import functools
import io
import json
import os
import sys
import zipfile
from urllib.parse import unquote

from . import boot

ROOT, tmp = boot.isolate("sec")
os.environ["COMFYOS_LICENSE_URL"] = "https://license.example.com/v1"
os.environ.pop("COMFYOS_ALLOWED_HOSTS", None)
os.environ.pop("COMFYOS_DEV", None)

import httpx  # noqa: E402

from app import (auth, backup_store, core, egress, entitlements as ents, events, flow_engine as fe, flows_store,  # noqa: E402
                 license as lic, license_keys, main, security, serversetup, settings_store, tls, updater)
from app.api import update as update_api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from support.homebase import HomeBaseConnector, QiviconError, homebase_plugin, install_homebase  # noqa: E402

__all__ = [
    "ROOT", "tmp", "httpx", "TestClient",
    "auth", "backup_store", "core", "egress", "ents", "events", "fe", "flows_store", "lic", "license_keys", "main",
    "security", "serversetup", "settings_store", "tls", "updater", "update_api",
    "HomeBaseConnector", "QiviconError", "homebase_plugin", "install_homebase",
    "outbound", "prog", "restarts", "setups", "APP_SRC", "PW", "SECRETS", "XSS",
    "raw_for", "login", "jbody", "zip_bytes", "findings", "check", "strict_json", "body_fields", "finish",
]

# ------------------------------------------------------------------ Safeguard: the test must not touch anything real
outbound: list[str] = []          # every connection ComfyOS wanted to open to the outside


def _net(req: httpx.Request) -> httpx.Response:
    """Replacement for every outgoing connection: remember where it was meant to go, and refuse."""
    outbound.append(req.url.host)
    return httpx.Response(404, json={"valid": False})


egress._transport = httpx.MockTransport(_net)
egress._sync_transport = httpx.MockTransport(_net)
egress._lan_transport = httpx.MockTransport(_net)      # the LAN clients of the plugins too


async def _no_connect(self, force=False):
    """The Home Base accepts no login in the test – no password ever goes out."""
    outbound.append(self.host)
    raise QiviconError("im Test gesperrt")
HomeBaseConnector.connect = _no_connect
prog = tmp / "prog" / "comfyos"
prog.mkdir(parents=True)
update_api.APP_ROOT = updater.APP_ROOT = prog          # Uploads/Updates landen im Testordner
restarts: list[float] = []
core.restart_soon = lambda delay=1.0: restarts.append(delay)
setups: list[tuple] = []


def _no_setup(action, hostname="comfyos"):
    """The server setup (systemd, host name) is only noted in the test, never executed."""
    setups.append((action, hostname))
    raise RuntimeError("im Test gesperrt")
serversetup.start = _no_setup
tls.ensure_cert = lambda regenerate=False: ("cert.pem", "key.pem")
lic._raw_hw = lambda: "serial:PENTEST"

APP_SRC = ROOT / "backend" / "app"
PW = "richtig-" + "geheim-9"
SECRETS = {"hb": "HB-" + "Passwort!23", "hue": "hueappkey" + "A1b2C3d4E5f6G7h8", "tk": "Telekom-" + "Pw#77"}
XSS = "<svg onload=alert(1)>"


# ------------------------------------------------------------------ raw ASGI request (as handed over by uvicorn)
async def _raw(method, raw_path, headers=None, body=b"", client=("192.168.1.66", 40000), scheme="http"):
    """Hand a request to the app without an HTTP client. Response as a dict: status, headers, body, text, crashed if so."""
    path, _, query = raw_path.partition("?")
    hdrs = {"host": "192.168.1.5", **{k.lower(): v for k, v in (headers or {}).items()}}
    if body and "content-length" not in hdrs:
        hdrs["content-length"] = str(len(body))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": scheme,
             "path": unquote(path), "raw_path": path.encode("latin-1", "replace"), "query_string": query.encode("latin-1", "replace"),
             "root_path": "", "headers": [(k.encode("latin-1"), v.encode("latin-1", "replace")) for k, v in hdrs.items()],
             "client": client, "server": ("192.168.1.5", 443 if scheme == "https" else 80)}
    sent = [{"type": "http.request", "body": body, "more_body": False}]
    out = {"status": 0, "headers": {}, "body": b""}

    async def receive():
        return sent.pop(0) if sent else {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            for k, v in msg.get("headers", []):
                out["headers"].setdefault(k.decode("latin-1").lower(), []).append(v.decode("latin-1"))
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")
    try:
        await main.app(scope, receive, send)
    except Exception as exc:      # unhandled exception = server error (uvicorn then answers with 500)
        out.update(status=500, body=f"UNBEHANDELT {type(exc).__name__}: {exc}"[:300].encode())
        out["headers"].setdefault("x-content-type-options", ["nosniff"])
        out["crashed"] = True
    out["text"] = out["body"].decode("utf-8", "replace")
    return out


def raw_for(client):
    """`raw(method, path, …)` for a running TestClient: the request runs in the event loop of the app
    (its background tasks live there)."""
    def raw(method, path, **kw):
        return client.portal.call(functools.partial(_raw, method, path, **kw))
    return raw


def login(client):
    """Set-up installation with a signed-in owner. Returns (session token, headers of the owner)."""
    auth.set_password(PW)
    tok = auth.create_session("pentest", "192.168.1.20")
    client.cookies.set(auth.COOKIE, tok)
    return tok, {"cookie": f"{auth.COOKIE}={tok}", "host": "comfyos.local"}


def jbody(obj):
    """JSON body with the fitting header for `raw(...)`."""
    return {"body": json.dumps(obj).encode(), "headers": {"content-type": "application/json"}}


def zip_bytes(entries, compress=zipfile.ZIP_DEFLATED):
    """ZIP-Archiv im Speicher aus {Name: Inhalt}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


findings: list[str] = []          # collected, so that one run shows all gaps instead of only the first


def check(cond, what):
    """Check a condition; a violation is noted as a finding, the test keeps running."""
    if not cond:
        findings.append(what)


def strict_json(text):
    """Read JSON like a browser: NaN/Infinity are invalid there."""
    def bad(c):
        raise ValueError(c)
    return json.loads(text, parse_constant=bad)


def body_fields(route) -> list[str]:
    """Names of the fields in the input model of a route (empty if it has none)."""
    for p in getattr(getattr(route, "dependant", None), "body_params", None) or []:
        ann = getattr(p, "type_", None) or getattr(getattr(p, "field_info", None), "annotation", None)
        if hasattr(ann, "model_fields"):
            return list(ann.model_fields)
    return []


def finish(done: str) -> None:
    """At the end of the test: print all findings (return value 1) or the success line."""
    if findings:
        print(f"\n{len(findings)} FINDINGS:")
        for x in findings:
            print("  - " + x)
        sys.exit(1)
    print(done)
