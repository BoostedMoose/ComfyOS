"""Telekom cloud client for the Magenta sync: sign in with the app's OAuth flow and call the
heating plugin (SMHM) of the Home Base through the cloud relay.

Verified against a capture of the Magenta SmartHome app (iOS 6.7.12):

    POST https://global.telekom.com/gcp-web-api/oauth
         Content-Type: application/x-www-form-urlencoded
         username=<Telekom e-mail>&password=<…>&client_id=<app>&scope=<scope>&grant_type=password
    ->   {"access_token": <JWT>, "token_type": "bearer", "expires_in": 28800, "refresh_token": "…"}

    The JWT carries the application id of the app. The local web login yields a different
    application id, and the heating plugin rejects that one with HTTP 403 – that is why the
    heating plans are reached through the cloud relay and not locally.

    GET https://backend-prod.cloud.qivicon.com/services/customer/self?mode=detail
    ->  customer record with "gateways": [{"id": "<serial number>", "type": "Homebase20"}]

    POST https://ris-prod.cloud.qivicon.com/gateway/json-rpc   method "SMHM/executeCommand"
         header "QIVICON-ServiceGatewayId": <serial number>    (the relay forwards to the Home Base)

Safety: after the Telekom rejected the password it is not sent again for LOGIN_BACKOFF_SECONDS.
Otherwise it would go out with every sync round, and repeated failures can lock the account.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable

import httpx

from plugin_api import PluginError

OAUTH_URL = "https://global.telekom.com/gcp-web-api/oauth"
BACKEND_URL = "https://backend-prod.cloud.qivicon.com"
RELAY_RPC_URL = "https://ris-prod.cloud.qivicon.com/gateway/json-rpc"    # from the app's configuration (endpoint.rpc, prod)
CLIENT_ID = "m2Zm6rGPAc"
SCOPE = "TB4DAJ7S"
LOGIN_BACKOFF_SECONDS = 1800             # after a rejected password login
DEFAULT_TOKEN_SECONDS = 28800
RENEW_BEFORE_SECONDS = 600
READ_ONLY_COMMANDS = ("getHeatingModes", "getHeatingTriggers", "listRooms", "listAutomations")

ClientFactory = Callable[..., Any]       # returns an httpx.AsyncClient (PluginContext.http with the egress key bound)


class TelekomError(PluginError):
    """The Telekom cloud or the heating plugin refused something (message for the user)."""


def token_claims(token: str) -> dict[str, Any]:
    """Read the payload of a JWT (no signature check, only for display and expiry)."""
    try:
        segment = token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def application_id(token: str) -> str | None:
    """Return the application id a token was issued for."""
    for key, value in token_claims(token).items():
        if key.endswith("/applicationid") and isinstance(value, str):
            return value
    return None


class TelekomCloud:
    """One Telekom account: token handling and calls to the heating plugin."""

    def __init__(self, username: str, password: str, open_client: ClientFactory) -> None:
        """Describe a Telekom account; nothing is sent until the first request. ``open_client`` creates the HTTP
        client."""
        self.username = username.strip()
        self.password = password
        self._open_client = open_client
        self.client: Any = None
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at = 0.0
        self.customer: dict[str, Any] = {}
        self._login_blocked_until = 0.0
        self._login_error = ""

    async def close(self) -> None:
        """Close the HTTP client."""
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception:
                pass
            self.client = None

    def _ensure_client(self) -> None:
        """Open the HTTP client on first use."""
        if self.client is None:
            self.client = self._open_client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True,
                                            headers={"User-Agent": "ComfyOS", "Accept": "*/*", "Accept-Language": "de"})

    async def _send(self, method: str, url: str, **options: Any) -> Any:
        """Send one request; network failures become a TelekomError the user understands."""
        self._ensure_client()
        try:
            return await self.client.request(method, url, **options)
        except httpx.HTTPError as error:
            raise TelekomError(f"Telekom-Server nicht erreichbar ({type(error).__name__}).")

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        """Send a token request and return the answer. Raises ``TelekomError`` with a text for the user if the login
        is refused or the answer is unusable."""
        response = await self._send("POST", OAUTH_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if response.status_code in (400, 401, 403):
            detail = ""
            try:
                detail = str(response.json().get("error_description") or response.json().get("error") or "")
            except Exception:
                pass
            raise TelekomError(f"Telekom-Anmeldung abgelehnt (HTTP {response.status_code})"
                               + (f": {detail}" if detail else ". Zugangsdaten prüfen."))
        if response.status_code >= 400:
            raise TelekomError(f"Telekom-Anmeldung antwortet mit HTTP {response.status_code}.")
        try:
            body = response.json()
        except ValueError:
            body = {}
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise TelekomError("Antwort der Telekom-Anmeldung enthält kein access_token.")
        self.access_token = token
        self.refresh_token = body.get("refresh_token") or self.refresh_token
        self.expires_at = time.time() + float(body.get("expires_in") or DEFAULT_TOKEN_SECONDS)
        return body

    async def login(self) -> dict[str, Any]:
        """Sign in with the password – at most once per LOGIN_BACKOFF_SECONDS after a rejection.

        New credentials mean a new object, so a corrected password is tried at once.
        """
        if not self.username or not self.password:
            raise TelekomError("Telekom-Benutzername und -Passwort erforderlich.")
        wait_seconds = self._login_blocked_until - time.time()
        if wait_seconds > 0:
            raise TelekomError(f"{self._login_error} Nächster Anmeldeversuch in {int(wait_seconds // 60) + 1} Min. "
                               "(Schutz vor Sperre des Telekom-Kontos) – Zugangsdaten unter System prüfen.")
        try:
            return await self._token_request({"username": self.username, "password": self.password,
                                              "client_id": CLIENT_ID, "scope": SCOPE, "grant_type": "password"})
        except TelekomError as error:
            self._login_error = str(error)
            self._login_blocked_until = time.time() + LOGIN_BACKOFF_SECONDS
            raise

    async def refresh(self) -> dict[str, Any]:
        """Renew the token with the refresh token; fall back to the password login."""
        if not self.refresh_token:
            return await self.login()
        try:
            return await self._token_request({"refresh_token": self.refresh_token, "client_id": CLIENT_ID,
                                              "scope": SCOPE, "grant_type": "refresh_token"})
        except TelekomError:
            return await self.login()

    async def token(self) -> str:
        """Return a valid app token, renewed when it is about to expire."""
        if not self.access_token or time.time() > self.expires_at - RENEW_BEFORE_SECONDS:
            if self.access_token:
                await self.refresh()
            else:
                await self.login()
        return str(self.access_token)

    async def customer_self(self) -> dict[str, Any]:
        """Read the customer record (it lists the gateways of the account)."""
        token = await self.token()
        response = await self._send("GET", f"{BACKEND_URL}/services/customer/self?mode=detail",
                                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if response.status_code in (401, 403):
            raise TelekomError(f"Cloud-Backend weist das Token ab (HTTP {response.status_code}).")
        if response.status_code >= 400:
            raise TelekomError(f"Cloud-Backend antwortet mit HTTP {response.status_code}.")
        record = response.json()
        self.customer = record if isinstance(record, dict) else {}
        return self.customer

    def gateway_ids(self) -> list[str]:
        """Serial numbers of the Home Bases in the customer record."""
        found: list[str] = []
        for key in ("gateways", "homebases"):
            for entry in self.customer.get(key) or []:
                gateway_id = entry.get("id") if isinstance(entry, dict) else None
                if gateway_id and gateway_id not in found:
                    found.append(gateway_id)
        return found

    async def plugin_call(self, command: str, gateway_id: str, params: dict[str, Any] | None = None,
                          retry: bool = True) -> Any:
        """Call the heating plugin through the cloud relay (app token + gateway id).

        Returns the plugin's answer ``{"result": …, "state": "ok", "command": …}``.
        """
        token = await self.token()
        arguments = {"command": command, "locale": "de", **(params or {})}
        body = [{"jsonrpc": "2.0", "method": "SMHM/executeCommand", "id": f"comfyos-{int(time.time() * 1000)}",
                 "params": [arguments]}]
        response = await self._send("POST", RELAY_RPC_URL, json=body,
                                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                             "Accept": "application/json, text/plain, */*",
                                             "QIVICON-ServiceGatewayId": gateway_id})
        if response.status_code in (401, 403):
            if retry:                     # the server may have dropped the token: sign in once more
                self.access_token = None
                await self.login()
                return await self.plugin_call(command, gateway_id, params, retry=False)
            raise TelekomError(f"Relay weist den App-Token ab (HTTP {response.status_code}).")
        if response.status_code == 404:
            raise TelekomError("Relay kennt das Gateway nicht (404) – Gateway-ID prüfen.")
        if response.status_code >= 400:
            raise TelekomError(f"Relay antwortet mit HTTP {response.status_code}.")
        data = response.json()
        entry = data[0] if isinstance(data, list) and data else data
        if isinstance(entry, dict) and entry.get("error"):
            error = entry["error"]
            raise TelekomError(f"Plugin-Fehler {error.get('code', '')}: {error.get('message', error)}")
        result = entry.get("result", entry) if isinstance(entry, dict) else entry
        if isinstance(result, dict) and result.get("state") not in (None, "ok"):
            detail = result.get("result")
            if isinstance(detail, list):
                detail = "; ".join(str(part) for part in detail)
            raise TelekomError(f"Plugin meldet '{result.get('state')}' bei {command}: {detail}")
        return result

    # -- heating plans through the plugin ------------------------------------

    async def get_heating(self, gateway_id: str) -> dict[str, Any]:
        """Read everything a sync needs: active mode, modes, levels (dashboard) and the schedule per mode."""
        active = (await self.plugin_call("getHeatingMode", gateway_id) or {}).get("result", {})
        modes_answer = (await self.plugin_call("getHeatingModes", gateway_id) or {}).get("result", {})
        dashboard = await self.plugin_call("getDashboardInfo", gateway_id)
        modes = modes_answer.get("heatingModes") or []
        triggers: dict[str, Any] = {}
        for mode in modes:
            key = mode.get("key") if isinstance(mode, dict) else None
            if key:
                triggers[key] = (await self.plugin_call("getHeatingTriggers", gateway_id, {"heatingMode": key}) or {}).get("result", {})
        return {"active": active.get("heatingMode"), "modes": modes, "dashboard": dashboard, "triggersByMode": triggers}

    async def set_heating_triggers(self, gateway_id: str, mode_key: str, schedule: dict[str, Any]) -> Any:
        """Write the weekly schedule of a heating mode."""
        return await self.plugin_call("setHeatingTriggers", gateway_id, {"heatingMode": mode_key, "heatingTriggers": schedule})

    async def set_heating_mode(self, gateway_id: str, mode_key: str) -> Any:
        """Activate a heating mode."""
        return await self.plugin_call("setHeatingMode", gateway_id, {"heatingMode": mode_key})

    async def create_heating_mode(self, gateway_id: str, name: str, schedule: dict[str, Any]) -> str | None:
        """Create a heating mode and return the key the Home Base gave it."""
        answer = await self.plugin_call("createNewHeatingMode", gateway_id, {"heatingModeName": name, "heatingTriggers": schedule})
        result = answer.get("result", answer) if isinstance(answer, dict) else answer
        return result.get("key") if isinstance(result, dict) else None

    async def delete_heating_mode(self, gateway_id: str, mode_key: str) -> Any:
        """Delete a heating mode."""
        return await self.plugin_call("deleteHeatingMode", gateway_id, {"heatingMode": mode_key})

    async def rename_heating_mode(self, gateway_id: str, mode_key: str, name: str) -> Any:
        """Rename a heating mode."""
        return await self.plugin_call("changeNameOfHeatingMode", gateway_id, {"heatingMode": mode_key, "heatingModeName": name})

    async def explore_plugin(self, gateway_id: str) -> list[dict[str, Any]]:
        """Run the read-only commands of the heating plugin, for diagnostics."""
        report = []
        for command in READ_ONLY_COMMANDS:
            entry: dict[str, Any] = {"command": command}
            try:
                entry.update(ok=True, sample=await self.plugin_call(command, gateway_id))
            except Exception as error:
                entry.update(ok=False, error=str(error) if isinstance(error, PluginError) else type(error).__name__)
            report.append(entry)
        return report

    def summary(self) -> dict[str, Any]:
        """Technical facts only – no names, addresses or customer numbers."""
        claims = token_claims(self.access_token or "")
        return {"application_id": application_id(self.access_token or ""), "audience": claims.get("aud"),
                "expires_in_h": round(max(0.0, self.expires_at - time.time()) / 3600, 1), "gateways": self.gateway_ids()}
