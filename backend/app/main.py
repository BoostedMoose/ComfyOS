"""ComfyOS – local smart home hub.

Device connections are plugins (public/plugins): the QIVICON / Magenta Home Base 2 with the optional Magenta sync,
Philips Hue, MQTT, presence in the network. Without a configured plugin ComfyOS runs empty, but complete.

Only the app, middleware, life cycle and frontend live here. State and helpers: core.py, routes: api/.
"""
from __future__ import annotations

import math
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, core, demo, events, live_audio, plugins, security
from .api import (audio, auth as auth_api, backup, demo as demo_api, devices, floorplan, flows, heating, history,
                  license as license_api, plugins as plugins_api, push as push_api, rooms, routines, scenes, settings,
                  system, update, widgets)
from .paths import app_version

# Windows serves .js as text/plain depending on the registry – pin it down.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/manifest+json", ".webmanifest")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start and end of the app: start background tasks and plugins, and shut them down cleanly."""
    await core.startup()
    yield
    await core.shutdown()


def _finite(value):
    """Replace NaN and Infinity in a response by null (nested too) – neither is valid JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


class SafeJSONResponse(JSONResponse):
    """JSON response that does not fail on NaN/Infinity: such values become null (valid for browsers)."""

    def render(self, content) -> bytes:
        """Response as JSON; if it contains NaN or Infinity, it is cleaned first."""
        try:
            return super().render(content)
        except ValueError:
            return super().render(_finite(content))


# The automatic API description (/openapi.json, /docs, /redoc) stays off: it would lie outside /api/ and thus be
# readable without login, and /docs loads its scripts from a foreign server.
app = FastAPI(title="ComfyOS API", version=app_version(), lifespan=lifespan, default_response_class=SafeJSONResponse,
              openapi_url=None, docs_url=None, redoc_url=None)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError):
    """Invalid input: report only place and reason. The input itself is never echoed back –
    it can be huge, contain script text or (Infinity/NaN) make the response unreadable."""
    errors = [{"loc": [str(x)[:60] for x in e.get("loc", ())][:6], "msg": str(e.get("msg", ""))[:200],
               "type": str(e.get("type", ""))[:60]} for e in exc.errors()[:20]]
    return JSONResponse({"detail": errors}, status_code=422)


# ---------------------------------------------------------------- Login
PUBLIC_API = {"/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/setup"}
HOOK_PREFIX = "/api/flows/hook/"   # flow webhooks: without login, but with a token per trigger


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Every address under /api/ requires a valid session – except the few in PUBLIC_API and the flow webhooks
    (they carry their own token). With a valid session the cookie is renewed now and then."""
    path = request.url.path
    token = request.cookies.get(auth.COOKIE)
    if path.startswith("/api/") and path not in PUBLIC_API and not path.startswith(HOOK_PREFIX):
        if not auth.check_session(token):
            return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
        if demo.in_demo(token):
            # a session in the demo (demo.py): only what touches nothing real, and everything it does lands in the demo
            if not demo.allowed(request.method, path):
                return JSONResponse({"detail": demo.REFUSED}, status_code=403)
            mark = demo.enter()
            try:
                response = await call_next(request)
            finally:
                demo.leave(mark)
        else:
            response = await call_next(request)
        # sliding expiry: now and then set the cookie again with current attributes
        if auth.needs_cookie_refresh(token) and "set-cookie" not in response.headers:
            core.set_session_cookie(response, token, request.url.scheme == "https")
        return response
    return await call_next(request)


# Defined after require_login = outermost layer: runs before everything else.
@app.middleware("http")
async def security_gate(request: Request, call_next):
    """Outermost check of every request: allowed host (against DNS rebinding), valid path, limited size,
    write access only from our own page. Every response gets the security headers."""
    path = request.url.path
    host = request.headers.get("host", "")
    if not security.host_allowed(host):
        if security.should_log_blocked(host):
            events.log("security", f"Zugriff über unbekannte Adresse abgelehnt: {security.split_host(host)[0][:60]}")
        return security.apply_headers(security.blocked_host_page(host, path.startswith("/api/")))
    if "\x00" in path or "\\" in path:   # not a valid path of ComfyOS; never pass it on to file access
        return security.apply_headers(JSONResponse({"detail": "Ungültige Adresse."}, status_code=400))
    if request.method in security.WRITE_METHODS and path not in security.UNLIMITED_PATHS:
        if "chunked" in request.headers.get("transfer-encoding", "").lower():
            return security.apply_headers(JSONResponse({"detail": "Größenangabe fehlt."}, status_code=411))
        try:
            size = int(request.headers.get("content-length") or 0)
        except ValueError:
            size = security.MAX_BODY + 1
        if size > security.MAX_BODY:
            return security.apply_headers(JSONResponse({"detail": "Anfrage zu groß."}, status_code=413))
    if request.method in security.WRITE_METHODS and not path.startswith(HOOK_PREFIX) and not security.same_origin(request):
        return security.apply_headers(JSONResponse({"detail": "Anfrage von fremder Seite abgelehnt."}, status_code=403))
    return security.apply_headers(await call_next(request))


# ---------------------------------------------------------------- Routes (order matters for overlapping paths)
ROUTERS = (auth_api, system, license_api, settings, devices, plugins_api, scenes, rooms, floorplan, routines,
           heating, flows, push_api, history, update, backup, audio, demo_api, widgets)
for _module in ROUTERS:
    app.include_router(_module.router)

# Plugins: scan the folders, check the manifests, load the enabled ones – their routes hang under /api/plugins/<id>/
# (that is, behind the login). They are only started in core.startup. A broken plugin stays contained.
plugins.mount = plugins_api.mount(app)
plugins.discover()
plugins.load_all()


def routes():
    """All routes flat in matching order (FastAPI mounts routers as a whole, app.routes does not show them one by one)."""
    for r in app.routes:
        orig = getattr(r, "original_router", None)
        if orig is not None:
            yield from orig.routes
        else:
            yield r


# ---------------------------------------------------------------- Live audio for speakers
@app.api_route(live_audio.PATH, methods=["GET", "HEAD"], include_in_schema=False)
async def live_audio_stream(request: Request):
    """The sound a browser sends right now, as an endless WAV stream for speakers. The one address outside the
    page itself that answers without login – a speaker cannot log in. Only while a transmission runs and only
    to the home network (live_audio.py)."""
    return live_audio.response(request.client.host if request.client else None, request.method)


# ---------------------------------------------------------------- Frontend
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def frontend():
    # Write the version into the asset URLs: after an update the browser reloads everything
    """The page of the app. The version is part of the addresses of scripts and styles, so that the browser reloads
    everything after an update."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("__V__", app_version())
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    """Web app manifest (for installing on the home screen), with the version as well."""
    content = (STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8").replace("__V__", app_version())
    return Response(content, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Service worker for push – must live at the root so that it applies to the whole app."""
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Icon for the browser tab."""
    return FileResponse(STATIC_DIR / "img" / "favicon-32.png", media_type="image/png")


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_icon():
    """Icon for the home screen of iPhone and iPad."""
    return FileResponse(STATIC_DIR / "img" / "apple-touch-icon.png", media_type="image/png")
