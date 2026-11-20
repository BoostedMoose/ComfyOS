#!/usr/bin/env python3
"""Diagnosis of the local Home Base connection (only your own Home Base, read-only).

Default (without a password, sends no credentials):
    python probe_homebase.py --host qivicon

    -> checks /system/info, the HTTPS ports, analyses the login page
       (forms, field names, scripts) and searches the scripts of the page for
       login/API endpoints. The login page is saved as probe_loginpage.html.

Optionally a single login attempt with the dashboard connector:
    python probe_homebase.py --host qivicon --login
    (the password is asked for hidden; if it is rejected there is no second attempt)
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import sys
from urllib.parse import urljoin, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import httpx
except ImportError:
    sys.exit("httpx fehlt. Im venv: pip install httpx")
# The Home Base has been the plugin public/plugins/qivicon since 0.19 – its connector is used directly here.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "public", "sdk"))
sys.path.insert(0, os.path.join(_ROOT, "public", "plugins"))
from html.parser import HTMLParser  # noqa: E402

from qivicon.connector import HomeBaseConnector, normalize_origin  # noqa: E402


class _FormParser(HTMLParser):
    """Collect forms, input fields and scripts of an HTML page (only for this diagnosis)."""

    def __init__(self):
        """Collects forms with their fields and the addresses of included scripts."""
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self._cur: dict | None = None
        self.scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        """Remember a form, input field or script."""
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._cur = {"action": a.get("action", ""), "method": (a.get("method") or "get").lower(), "inputs": []}
            self.forms.append(self._cur)
        elif tag in ("input", "select", "textarea") and self._cur is not None:
            self._cur["inputs"].append({"name": a.get("name", ""), "type": (a.get("type") or "text").lower(),
                                        "value": a.get("value", ""), "id": a.get("id", "")})
        elif tag == "script" and a.get("src"):
            self.scripts.append(a["src"])

    def handle_endtag(self, tag):
        """End of a form: following fields no longer belong to it."""
        if tag == "form":
            self._cur = None


def parse_page(text: str) -> tuple[list[dict], list[str]]:
    """Forms and script addresses of a page; an unreadable page gives what was found up to then."""
    parser = _FormParser()
    try:
        parser.feed(text)
    except Exception:
        pass
    return parser.forms, parser.scripts


def password_form(forms: list[dict]) -> dict | None:
    """The form with a password field, or None."""
    return next((f for f in forms if any(i["type"] == "password" for i in f["inputs"])), None)

PORTS = (443, 8444)
INTERESTING = re.compile(
    r"""["'`](/[A-Za-z0-9_\-./{}]*(?:login|logon|auth|oauth|token|session|json-rpc|remote|rest/|system/)[A-Za-z0-9_\-./{}?=&]*)["'`]""",
    re.I)
KEYWORDS = re.compile(r"(data-access-token|access_token|Authorization|password|passwort|x-qivicon[\w-]*)", re.I)


def ok(flag, text):
    """Ergebniszeile ausgeben."""
    print(f"  [{'OK ' if flag else '-- '}] {text}")


def analyse(host: str, report: dict):
    """Examine the Home Base without login: system information, reachable ports, structure of the login page and
    hints at interfaces in its scripts. Sends no credentials."""
    client = httpx.Client(verify=False, timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 ComfyOS-Probe/2.0"})

    print(f"\n1) http://{host}/system/info (unauthentifiziert)")
    try:
        r = client.get(f"http://{host}/system/info")
        ok(r.status_code < 400, f"HTTP {r.status_code}")
        if r.status_code < 400:
            print("     " + r.text[:300].replace("\n", " "))
            report["system_info"] = r.text[:1000]
    except httpx.HTTPError as e:
        ok(False, type(e).__name__)

    print("\n2) HTTPS-Ports / Einstiegsseite")
    pages = {}
    for port in PORTS:
        base = f"https://{host}/" if port == 443 else f"https://{host}:{port}/"
        try:
            r = client.get(base)
            chain = " -> ".join(str(h.url) for h in r.history) + (" -> " if r.history else "")
            ok(r.status_code < 400, f"{base}  HTTP {r.status_code}  {chain}{r.url}")
            report.setdefault("ports", {})[port] = {"status": r.status_code, "final_url": str(r.url),
                                                     "set_cookie": [c for c in r.cookies.keys()]}
            if r.status_code < 400:
                pages[port] = r
        except httpx.HTTPError as e:
            ok(False, f"{base}  {type(e).__name__}")
            report.setdefault("ports", {})[port] = {"error": type(e).__name__}

    if not pages:
        print("\nKeine Seite erreichbar. Hostname/IP prüfen.")
        return
    port = 443 if 443 in pages else next(iter(pages))
    page = pages[port]
    with open("probe_loginpage.html", "w", encoding="utf-8") as fh:
        fh.write(page.text)

    print(f"\n3) Login-Seite {page.url} (gespeichert als probe_loginpage.html)")
    forms, scripts = parse_page(page.text)
    report["forms"] = [{"action": f["action"], "method": f["method"],
                        "inputs": [{"name": i["name"], "type": i["type"], "id": i["id"]} for i in f["inputs"]]}
                       for f in forms]
    pw = password_form(forms)
    ok(bool(forms), f"{len(forms)} HTML-Formular(e)")
    for f in report["forms"]:
        print(f"     form method={f['method']} action={f['action'] or '(gleiche URL)'}")
        for i in f["inputs"]:
            print(f"        - {i['type']:<9} name={i['name'] or '(ohne)'} id={i['id'] or '-'}")
    ok(bool(pw), "Passwortfeld in einem <form> gefunden" if pw else
       "kein <form> mit Passwortfeld -> Login läuft per JavaScript")
    pw_outside = re.search(r"<input[^>]+type=[\"']password", page.text, re.I)
    if pw_outside and not pw:
        print("     (Passwortfeld existiert, aber außerhalb eines <form> -> JS schickt den Login)")

    print(f"\n4) Scripts der Seite ({len(scripts)}) -> Endpunkte")
    endpoints, keywords = set(), set()
    for m in INTERESTING.finditer(page.text):
        endpoints.add(m.group(1))
    for k in KEYWORDS.finditer(page.text):
        keywords.add(k.group(1).lower())
    for src in scripts[:25]:
        url = urljoin(str(page.url), src)
        if urlsplit(url).hostname != urlsplit(str(page.url)).hostname:
            continue
        try:
            js = client.get(url)
        except httpx.HTTPError:
            continue
        print(f"     {src}  ({len(js.content)} B)")
        for m in INTERESTING.finditer(js.text):
            endpoints.add(m.group(1))
        for k in KEYWORDS.finditer(js.text):
            keywords.add(k.group(1).lower())
    report["endpoints"] = sorted(endpoints)
    report["keywords"] = sorted(keywords)
    print("     Gefundene Pfade:")
    for e in sorted(endpoints)[:60]:
        print(f"        {e}")
    print(f"     Schlüsselwörter: {', '.join(sorted(keywords)) or '-'}")


async def login_once(host: str, port: int | None, report: dict):
    """Exactly one login attempt with the connector of the plugin; the password is asked for hidden and never
    stored."""
    password = getpass.getpass("Home-Base-Passwort (versteckt): ")
    base = f"https://{host}" + (f":{port}" if port else "")
    c = HomeBaseConnector(base, password, verify_tls=False, open_client=lambda **options: httpx.AsyncClient(**options))
    print("\n5) Ein Login-Versuch über den Dashboard-Connector")
    try:
        system = await c.connect(force=True)
        ok(True, f"verbunden über {c.base_url} ({'Cookie-Sitzung' if c.session_only else 'Token'})")
        print("     " + json.dumps(system, ensure_ascii=False)[:300])
        report["login"] = {"ok": True, "base": c.base_url, "session_only": c.session_only}
    except Exception as e:
        ok(False, str(e))
        report["login"] = {"ok": False, "error": str(e)}
    finally:
        await c.close()


def main():
    """Evaluate the call, examine, sign in once if wanted and save the report without passwords."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="qivicon", help="Hostname oder IP, auch eine URL wird akzeptiert")
    ap.add_argument("--login", action="store_true", help="zusätzlich EINEN Login-Versuch durchführen")
    args = ap.parse_args()
    _, host, port = normalize_origin(args.host)
    report = {"host": host}
    analyse(host, report)
    if args.login:
        asyncio.run(login_once(host, port, report))
    with open("probe_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\nReport gespeichert: probe_report.json (enthält keine Passwörter)")


if __name__ == "__main__":
    main()
