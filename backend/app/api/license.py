"""Licence: state, redeeming, removing, the owner's preview and the licence generator.

None of these routes depends on a Pro feature – a licence must be enterable in every plan and the
preview must be endable in every plan.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import core, entitlements, flow_engine, license, security

router = APIRouter()


class RedeemBody(BaseModel):
    """Licence key to redeem."""
    license: str = Field(min_length=1, max_length=8192)


class PreviewBody(BaseModel):
    """Owner's preview: plan and duration in minutes."""
    plan: str | None = Field(default=None, max_length=20)
    minutes: int = Field(default=60, ge=5, le=1440)


class SignKeyBody(BaseModel):
    """Owner's signing key for issuing licences."""
    key: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=200)


class IssueBody(BaseModel):
    """Issue a licence: plan, name, validity and binding to an installation – confirmed with the password."""
    plan: str = Field(max_length=20)
    name: str = Field(default="", max_length=80)
    instance_id: str | None = Field(default=None, max_length=32)
    days: int | None = Field(default=None, ge=1, le=36500)
    password: str = Field(max_length=200)


def _public(st: dict | None = None) -> dict:
    """Licence status for the interface, completed with: is the plan enforced, how many flows are paused and –
    for the owner – the licences issued recently."""
    st = dict(st or license.status())
    st["enforce"] = entitlements.enforcing()
    st["paused_flows"] = len(flow_engine.engine.paused)
    if st["issuer"]["available"]:
        st["issuer"]["issued"] = list(reversed(license.issued()))[:100]
    return st


def _changed(st: dict) -> dict:
    """After a change to the licence: make all open pages reload and return the new status."""
    core.publish({"type": "reload"})
    return _public(st)


def _fail(exc: license.LicenseError) -> HTTPException:
    """Licence error as an HTTP error with its status code and its text for the user."""
    return HTTPException(exc.status, str(exc))


def _step_up(request: Request, password: str) -> None:
    """Sensitive actions ask for the ComfyOS password again – an open session alone is not enough."""
    core.step_up(request, password, "Lizenz-Generator")


@router.get("/api/license")
async def license_status():
    """Current licence status. A state that has changed meanwhile (expired preview or licence) is taken over along
    the way."""
    return _public(license.sync())


@router.post("/api/license")
async def license_redeem(body: RedeemBody, request: Request):
    """Redeem an owner token, a licence file (.comfyos-license) or its short form, or a purchased key."""
    if not security.rate_ok("license:" + core.client_ip(request), 10, 600):
        raise HTTPException(429, "Zu viele Versuche. Bitte in ein paar Minuten erneut probieren.")
    try:
        return _changed(await license.redeem(body.license))
    except license.LicenseError as exc:
        raise _fail(exc)


@router.delete("/api/license")
async def license_remove():
    """Remove licence file and purchased key. The owner file stays (can only be deleted by hand on the device)."""
    return _changed(await license.deactivate())


@router.post("/api/license/check")
async def license_check():
    """Have the licence checked now and report the new status."""
    return _changed(await license.validate())


@router.post("/api/license/preview")
async def license_preview(body: PreviewBody):
    """Start the preview: view the installation as another plan for a limited time (only with an owner licence)."""
    try:
        return _changed(license.set_preview(body.plan, body.minutes))
    except license.LicenseError as exc:
        raise _fail(exc)


@router.delete("/api/license/preview")
async def license_preview_end():
    """Testansicht beenden."""
    return _changed(license.clear_preview())


@router.put("/api/license/issuer/key")
async def license_issuer_key(body: SignKeyBody, request: Request):
    """Store the signing key on this installation (owner token only, with the password)."""
    if not license.issuer_available():
        raise HTTPException(403, "Lizenzen ausstellen kann nur die Installation mit Owner-Token.")
    _step_up(request, body.password)
    try:
        license.install_sign_key(body.key)
    except license.LicenseError as exc:
        raise _fail(exc)
    return _public()


@router.delete("/api/license/issuer/key")
async def license_issuer_key_remove():
    """Remove the signing key from this installation."""
    license.remove_sign_key()
    return _public()


@router.post("/api/license/issue")
async def license_issue(body: IssueBody, request: Request):
    """Issue a licence (owner token only, with the password). Response: file content and short form."""
    if not license.issuer_available():
        raise HTTPException(403, "Lizenzen ausstellen kann nur die Installation mit Owner-Token.")
    _step_up(request, body.password)
    try:
        return license.issue(body.plan, body.name, body.instance_id, body.days)
    except license.LicenseError as exc:
        raise _fail(exc)
