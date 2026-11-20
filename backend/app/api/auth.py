"""Login, sessions and further allowed addresses."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth, core, demo, events, security
from ..settings_store import load_settings, save_settings

router = APIRouter()


class LoginBody(BaseModel):
    """Anmeldung: das Passwort."""
    password: str = Field(max_length=1024)


class SetupBody(BaseModel):
    """First setup: new password and – except on the device itself – the setup code."""
    password: str = Field(max_length=1024)
    code: str | None = Field(default=None, max_length=40)


class PasswordChange(BaseModel):
    """Password change: old and new password."""
    old: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


@router.get("/api/auth/state")
async def auth_state(request: Request):
    """What the login page needs to know: set up? signed in? setup code needed (only from another
    device)?"""
    configured = auth.is_configured()
    return {"configured": configured, "logged_in": auth.check_session(request.cookies.get(auth.COOKIE)),
            "needs_code": (not configured) and not core.is_local(request)}


@router.post("/api/auth/setup")
async def auth_setup(body: SetupBody, request: Request, response: Response):
    """First setup: set the password and sign in right away. Works only once; from another device only with the
    setup code, failed attempts block the address."""
    if auth.is_configured():
        raise HTTPException(409, "ComfyOS ist bereits eingerichtet.")
    ip = core.client_ip(request)
    wait = auth.locked_for(ip)
    if wait:
        raise HTTPException(429, f"Zu viele Versuche. Bitte {wait} s warten.")
    if not core.is_local(request) and not auth.check_setup_code(body.code):
        _login_failed(ip, "Einrichtungscode")
        raise HTTPException(403, "Einrichtungscode stimmt nicht.")
    try:
        auth.set_password(body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    core.set_session_cookie(response, auth.create_session(request.headers.get("user-agent", ""), ip), request.url.scheme == "https")
    events.log("system", "ComfyOS-Passwort eingerichtet")
    return {"ok": True}


@router.post("/api/auth/login")
async def auth_login(body: LoginBody, request: Request, response: Response):
    """Sign in: check the password, create a session, set the cookie. Failed attempts block the address for a growing
    time."""
    ip = core.client_ip(request)
    wait = auth.locked_for(ip)
    if wait:
        raise HTTPException(429, f"Zu viele Versuche. Bitte {wait} s warten.")
    if auth.under_attack():
        await asyncio.sleep(2)   # many failed attempts in total: slow down every login, lock nobody out
    if not auth.verify_password(body.password):
        _login_failed(ip, "Passwort")
        raise HTTPException(401, "Passwort falsch.")
    auth.clear_failures(ip)
    core.set_session_cookie(response, auth.create_session(request.headers.get("user-agent", ""), ip), request.url.scheme == "https")
    return {"ok": True}


def _login_failed(ip: str, what: str) -> None:
    """Count a failed attempt of an address; if it leads to a block, that is written to the event log."""
    seconds = auth.register_failure(ip)
    if seconds:
        events.log("security", f"Anmeldung von {ip[:45]} für {seconds} s gesperrt (zu viele Fehlversuche: {what})")


@router.get("/api/auth/sessions")
async def auth_sessions(request: Request):
    """Signed-in devices for the list under System (without tokens)."""
    return {"sessions": auth.list_sessions(request.cookies.get(auth.COOKIE)), "idle_days": auth.SESSION_DAYS}


@router.delete("/api/auth/sessions/{sid}")
async def auth_session_revoke(sid: str):
    """Sign out a device by its short id."""
    if not auth.revoke_by_id(sid):
        raise HTTPException(404, "Sitzung nicht gefunden.")
    events.log("security", "Ein Gerät wurde abgemeldet")
    return {"ok": True}


class HostsBody(BaseModel):
    """Further addresses under which ComfyOS may be reachable."""
    hosts: list[str] = Field(default_factory=list, max_length=10)


@router.get("/api/security")
async def security_state(request: Request):
    """Allowed further addresses and whether the page runs over HTTPS."""
    return {"allowed_hosts": load_settings().get("allowed_hosts") or [], "https": request.url.scheme == "https"}


@router.put("/api/security/hosts")
async def security_hosts(body: HostsBody):
    """Save further allowed addresses (cleaned) – the host check rejects requests through other names."""
    hosts = security.clean_hosts(body.hosts)
    cfg = load_settings()
    cfg["allowed_hosts"] = hosts
    save_settings(cfg)
    security.reset_cache()
    events.log("security", "Weitere Adressen: " + (", ".join(hosts) if hosts else "keine"))
    return {"allowed_hosts": hosts}


@router.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    """Sign out this device and delete the cookie (a running demo ends with it)."""
    demo.stop(request.cookies.get(auth.COOKIE))
    auth.revoke_session(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.post("/api/auth/logout-all")
async def auth_logout_all(request: Request):
    """Sign out all other devices; the own session stays."""
    n = auth.revoke_all(except_token=request.cookies.get(auth.COOKIE))
    events.log("system", f"{n} andere Geräte abgemeldet")
    return {"ok": True, "revoked": n}


@router.post("/api/auth/password")
async def auth_password(body: PasswordChange, request: Request):
    """Change the password – only with the old password. All other devices are signed out along the way; failed
    attempts count as with the login."""
    ip = core.client_ip(request)
    wait = auth.locked_for(ip)
    if wait:
        raise HTTPException(429, f"Zu viele Versuche. Bitte {wait} s warten.")
    if not auth.verify_password(body.old):
        _login_failed(ip, "altes Passwort")
        raise HTTPException(401, "Altes Passwort falsch.")
    try:
        auth.set_password(body.new)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # whoever knew the old password must not stay signed in with an open session
    revoked = auth.revoke_all(except_token=request.cookies.get(auth.COOKIE))
    events.log("security", "ComfyOS-Passwort geändert" + (f" – {revoked} andere Geräte abgemeldet" if revoked else ""))
    return {"ok": True, "revoked": revoked}
