"""HTTPS for ComfyOS: self-signed certificate and redirect from HTTP.

The certificate lives with the settings (~/.config/ComfyOS/tls/cert.pem and
key.pem, the key readable only for the ComfyOS user). It is valid for the name
of the machine (comfyos, comfyos.local, comfyos.fritz.box), localhost and the
IP address in the home network. Whoever has an own certificate simply places it under
the same names – ComfyOS only creates a new one if none is there.

Browsers warn about self-signed certificates once per device; after that
the connection is encrypted.
"""
from __future__ import annotations

import datetime
import ipaddress
import os
import socket
from typing import Any

from . import live_audio
from .settings_store import APP_DIR, load_settings

TLS_DIR = APP_DIR / "tls"
CERT_FILE = TLS_DIR / "cert.pem"
KEY_FILE = TLS_DIR / "key.pem"


def enabled() -> bool:
    """Is HTTPS switched on in the settings?"""
    return bool(load_settings().get("https"))


def https_port(http_port: int) -> int:
    """Port for HTTPS: COMFYOS_HTTPS_PORT, otherwise 443 next to port 80 and 8443 next to any other."""
    env = os.getenv("COMFYOS_HTTPS_PORT")
    if env:
        return int(env)
    return 443 if http_port == 80 else 8443


def _names() -> tuple[list[str], list[str]]:
    """Names and addresses the own certificate is valid for: host name in the usual forms, comfyos, localhost
    and the address in the home network."""
    host = socket.gethostname().split(".")[0].lower() or "comfyos"
    dns = sorted({host, f"{host}.local", f"{host}.fritz.box", "comfyos", "comfyos.local", "comfyos.fritz.box", "localhost"})
    ips = {"127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))   # no packet is sent – only determine the own address
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass   # no network: then only 127.0.0.1
    return dns, sorted(ips)


def ensure_cert(force: bool = False) -> tuple[str, str]:
    """Create a certificate if none is there (or force)."""
    if CERT_FILE.exists() and KEY_FILE.exists() and not force:
        return str(CERT_FILE), str(KEY_FILE)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    dns, ips = _names()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns[0] if dns else "comfyos"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ComfyOS (selbst signiert)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass   # file permissions do not exist here (Windows)
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(CERT_FILE), str(KEY_FILE)


def cert_info() -> dict[str, Any] | None:
    """Details of the certificate for display (valid until, names, fingerprint), or None if there is none or it
    is unreadable."""
    if not CERT_FILE.exists():
        return None
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        cert = x509.load_pem_x509_certificate(CERT_FILE.read_bytes())
        try:
            names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            san = [str(n.value) for n in names]
        except x509.ExtensionNotFound:
            san = []
        fp = cert.fingerprint(hashes.SHA256()).hex(":").upper()
        return {"not_after": cert.not_valid_after_utc.date().isoformat(), "names": san, "sha256": fp,
                "self_signed": cert.issuer == cert.subject}
    except Exception as exc:  # damaged / foreign format
        return {"error": str(exc)}


def redirect_app(port: int):
    """Minimal ASGI app: redirect every HTTP request to HTTPS with a 308. The one exception is the live audio
    stream for speakers (live_audio.PATH): a speaker does not accept a self-signed certificate."""
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        if scope.get("path") == live_audio.PATH:
            await live_audio.asgi(scope, receive, send)
            return
        host = dict(scope.get("headers") or []).get(b"host", b"comfyos").decode("latin-1").split(":")[0]
        path = scope.get("raw_path") or scope.get("path", "/").encode()
        qs = scope.get("query_string") or b""
        target = f"https://{host}{'' if port == 443 else f':{port}'}{path.decode('latin-1')}" + (f"?{qs.decode('latin-1')}" if qs else "")
        await send({"type": "http.response.start", "status": 308,
                    "headers": [(b"location", target.encode("latin-1")), (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})
    return app
