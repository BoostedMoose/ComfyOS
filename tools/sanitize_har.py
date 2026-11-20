"""Clean a browser capture (HAR) before it is shared or evaluated.

    python tools/sanitize_har.py captures/qivicon.har      ->  captures/qivicon.clean.har

Replaced by ***: Cookie/Set-Cookie/Authorization and all headers, parameters and
JSON fields with token/password/secret/session/serial in the name, JWTs and the
gateway id of the Home Base. Images, fonts and other binary content are dropped.
Only pass on the .clean.har – delete the raw file afterwards.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SECRET_KEY = re.compile(r"pass|token|secret|session|serial|auth|cookie|credential|csrf|jwt|refresh|apikey|api_key", re.I)
JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*")
GATEWAY_ID = re.compile(r"\b[A-Z]{2}\d{5,}-\d{3,}\b")
FORM_SECRET = re.compile(r"((?:[\w.-]*(?:pass|token|secret|session|auth)[\w.-]*)=)[^&\s\"']+", re.I)
DROP_MIME = ("image/", "font/", "audio/", "video/", "application/octet-stream", "application/font")


def scrub_str(s: str) -> str:
    """Make secrets in a text unrecognisable: form fields with passwords, tokens and the gateway id."""
    return GATEWAY_ID.sub("GW-***", JWT.sub("***JWT***", FORM_SECRET.sub(r"\1***", s)))


def scrub(obj):
    """Clean arbitrarily nested data: values under suspicious keys are replaced, texts are searched."""
    if isinstance(obj, dict):
        return {k: ("***" if SECRET_KEY.search(k) and isinstance(v, (str, int)) and v != "" else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, str):
        return scrub_str(obj)
    return obj


def scrub_text(text: str) -> str:
    """Clean a text – as JSON if it is one, otherwise as ordinary text."""
    try:
        return json.dumps(scrub(json.loads(text)), ensure_ascii=False)
    except (ValueError, TypeError):
        return scrub_str(text)


def scrub_pairs(pairs: list) -> list:
    """Liste von Name/Wert-Paaren (Kopfzeilen, Parameter) bereinigen."""
    out = []
    for p in pairs or []:
        name = str(p.get("name", ""))
        out.append({**p, "value": "***" if SECRET_KEY.search(name) else scrub_str(str(p.get("value", "")))})
    return out


def clean(har: dict) -> dict:
    """Clean a HAR capture: headers, cookies, addresses, sent and received content."""
    for e in har.get("log", {}).get("entries", []):
        req, res = e.get("request", {}), e.get("response", {})
        for part in (req, res):
            part["headers"] = scrub_pairs(part.get("headers"))
            part["cookies"] = [{**c, "value": "***"} for c in part.get("cookies") or []]
        req["url"] = scrub_str(req.get("url", ""))
        req["queryString"] = scrub_pairs(req.get("queryString"))
        pd = req.get("postData")
        if pd:
            if "text" in pd:
                pd["text"] = scrub_text(pd["text"])
            pd["params"] = scrub_pairs(pd.get("params"))
        content = res.get("content") or {}
        mime = str(content.get("mimeType", "")).lower()
        if mime.startswith(DROP_MIME) or content.get("encoding") == "base64":
            content.pop("text", None)
            content.pop("encoding", None)
        elif "text" in content:
            content["text"] = scrub_text(content["text"])
        if res.get("redirectURL"):
            res["redirectURL"] = scrub_str(res["redirectURL"])
    return har


def main() -> int:
    """Clean a file (HAR or JSON) and store it next to it as a `.clean` version."""
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    data = json.loads(src.read_text(encoding="utf-8-sig"))
    is_har = isinstance(data, dict) and isinstance(data.get("log"), dict)
    dst = src.with_name(src.stem + (".clean.har" if is_har else ".clean.json"))
    dst.write_text(json.dumps(clean(data) if is_har else scrub(data), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(data['log'].get('entries', [])) if is_har else 'JSON'} bereinigt -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
