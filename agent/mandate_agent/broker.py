"""Getting agent credentials: Cognito client identity, then ID-JAG + AAuth (or legacy mandate).

Real AAuth flow:
1. Cognito client_credentials → agent access token (mandate/tools).
2. POST /api/v1/oauth/token token-exchange with staff subject token + sid → ID-JAG.
3. POST /api/v1/oauth/token jwt-bearer with ID-JAG + agent public JWK → aa-auth+jwt.
4. MCP calls carry Bearer aa-auth+jwt plus Signature-Key / Signature (PoP).

Legacy flow (still supported):
2'. POST /api/v1/sessions/{sid}/mandate → HS256 mandateToken.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "aauth" / "src"))

from aauth import generate_agent_key, load_agent_key, mint_aa_agent_jwt, public_jwk, sign_request  # noqa: E402
from aauth.keys import AgentKey  # noqa: E402
from aauth.tokens import auth_protocols_enabled, jwk_thumbprint  # noqa: E402

AGENT_SCOPE = "mandate/tools"
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ID_JAG_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id-jag"


class BrokerError(RuntimeError):
    """The agent could not get a mandate / AAuth token (bad credentials, unknown or revoked session)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class AgentCredentials:
    """What the MCP client needs on every request."""

    access_token: str
    auth_protocol: str = "legacy_mandate"
    agent_jwt: str | None = None
    agent_key: AgentKey | None = None

    def mcp_headers(self, method: str, path: str, body: bytes | str = b"") -> dict[str, str]:
        if self.agent_key is not None and self.agent_jwt:
            return sign_request(
                self.agent_key,
                method=method,
                path=path,
                body=body,
                auth_token=self.access_token,
                agent_jwt=self.agent_jwt,
            )
        return {"Authorization": f"Bearer {self.access_token}", "X-Auth-Protocol": self.auth_protocol}


def fetch_agent_client_token(http: httpx.Client | None = None) -> str:
    """Cognito client_credentials grant with COGNITO_DOMAIN / COGNITO_AGENT_CLIENT_ID / _SECRET."""
    domain = os.environ["COGNITO_DOMAIN"]
    base = domain if "://" in domain else f"https://{domain}"
    http = http or httpx.Client(timeout=10)
    response = http.post(
        f"{base.rstrip('/')}/oauth2/token",
        data={"grant_type": "client_credentials", "scope": AGENT_SCOPE},
        auth=(os.environ["COGNITO_AGENT_CLIENT_ID"], os.environ["COGNITO_AGENT_CLIENT_SECRET"]),
    )
    if response.status_code != 200:
        raise BrokerError(f"Cognito refused the agent's client credentials (HTTP {response.status_code}).")
    return response.json()["access_token"]


def fetch_mandate_token(api_url: str, sid: str, http: httpx.Client | None = None) -> str:
    """Legacy HS256 mandate only (string). Prefer fetch_agent_credentials for AAuth."""
    return fetch_agent_credentials(api_url, sid, http=http, prefer_aauth=False).access_token


def _agent_key() -> AgentKey:
    try:
        return load_agent_key()
    except ValueError:
        return generate_agent_key()


def fetch_agent_credentials(
    api_url: str,
    sid: str,
    *,
    staff_subject_token: str | None = None,
    http: httpx.Client | None = None,
    prefer_aauth: bool | None = None,
) -> AgentCredentials:
    """ID-JAG + aa-auth when enabled and a staff subject token is available; else legacy mandate."""
    http = http or httpx.Client(timeout=10)
    protocols = auth_protocols_enabled()
    staff_token = (
        staff_subject_token
        or os.environ.get("STAFF_SUBJECT_TOKEN")
        or os.environ.get("STAFF_ACCESS_TOKEN")
        or ""
    ).strip()
    use_aauth = prefer_aauth if prefer_aauth is not None else (protocols.idjag and protocols.aauth and bool(staff_token))

    client_token = fetch_agent_client_token(http)
    headers = {"Authorization": f"Bearer {client_token}"}

    if use_aauth:
        key = _agent_key()
        exchange = http.post(
            f"{api_url.rstrip('/')}/api/v1/oauth/token",
            data={
                "grant_type": TOKEN_EXCHANGE,
                "requested_token_type": ID_JAG_TOKEN_TYPE,
                "subject_token": staff_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "sid": sid,
            },
            headers=headers,
        )
        if exchange.status_code != 200:
            raise BrokerError(
                f"ID-JAG token exchange failed for session {sid} (HTTP {exchange.status_code}).",
                exchange.status_code,
            )
        id_jag = exchange.json()["access_token"]
        bearer = http.post(
            f"{api_url.rstrip('/')}/api/v1/oauth/token",
            data={
                "grant_type": JWT_BEARER,
                "assertion": id_jag,
                "client_jwk": json.dumps(public_jwk(key)),
            },
            headers=headers,
        )
        if bearer.status_code != 200:
            raise BrokerError(
                f"aa-auth jwt-bearer failed for session {sid} (HTTP {bearer.status_code}).",
                bearer.status_code,
            )
        payload = bearer.json()
        issuer = os.environ.get("AAUTH_AGENT_ISSUER") or f"{api_url.rstrip('/')}/agent"
        agent_id = os.environ.get("MANDATE_AGENT_ID", "agent-1")
        agent_jwt = mint_aa_agent_jwt(key, issuer=issuer, subject=agent_id)
        # Sanity: jkt from server matches our key
        _ = jwk_thumbprint(public_jwk(key))
        return AgentCredentials(
            access_token=payload["access_token"],
            auth_protocol="idjag+aauth",
            agent_jwt=agent_jwt,
            agent_key=key,
        )

    response = http.post(
        f"{api_url.rstrip('/')}/api/v1/sessions/{sid}/mandate",
        json={},
        headers=headers,
    )
    if response.status_code != 200:
        raise BrokerError(
            f"The broker refused a mandate for session {sid} (HTTP {response.status_code}).",
            response.status_code,
        )
    return AgentCredentials(
        access_token=response.json()["mandateToken"],
        auth_protocol="legacy_mandate",
    )
