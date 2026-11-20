"""Start ComfyOS – with or without HTTPS.

    python backend/run.py            (COMFYOS_HOST, COMFYOS_PORT from the environment, default 0.0.0.0:8000)

If HTTPS is switched on under System → Erreichbarkeit, ComfyOS runs encrypted
on port 443 (or 8443 if HTTP does not run on port 80) and the HTTP port
only redirects there. Exit code 3 = restart after an update or a change of this setting.
"""
import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND.parent)
os.environ["COMFYOS_LAUNCHER"] = "1"

import uvicorn  # noqa: E402

from app import tls  # noqa: E402

APP = os.getenv("COMFYOS_APP", "app.main:app")   # only different for tests


def main() -> None:
    """Start ComfyOS: without HTTPS one server on COMFYOS_PORT, with HTTPS two – the encrypted one and one that
    redirects to it. If no certificate can be created, ComfyOS starts without HTTPS."""
    host = os.getenv("COMFYOS_HOST", "0.0.0.0")
    port = int(os.getenv("COMFYOS_PORT", "8000"))
    if not tls.enabled():
        os.environ["COMFYOS_HTTP_PORT"] = str(port)
        uvicorn.run(APP, host=host, port=port)
        return
    try:
        cert, key = tls.ensure_cert()
    except Exception as exc:
        print(f"  HTTPS nicht möglich ({exc}) – starte ohne HTTPS.")
        uvicorn.run(APP, host=host, port=port)
        return
    sport = tls.https_port(port)
    os.environ["COMFYOS_HTTP_PORT"], os.environ["COMFYOS_HTTPS_PORT"] = str(port), str(sport)
    print(f"\n  HTTPS aktiv: https://<adresse>{'' if sport == 443 else f':{sport}'}/  (HTTP-Port {port} leitet um)")
    https = uvicorn.Server(uvicorn.Config(APP, host=host, port=sport, ssl_certfile=cert, ssl_keyfile=key))
    redirect = uvicorn.Server(uvicorn.Config(tls.redirect_app(sport), host=host, port=port, lifespan="off", log_level="warning"))

    async def both():
        await asyncio.gather(https.serve(), redirect.serve())
    asyncio.run(both())


if __name__ == "__main__":
    main()
