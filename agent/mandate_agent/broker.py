"""Getting a mandate token: the agent's own Cognito identity, then lane B's broker.

1. The agent proves who it is with Cognito client_credentials (its own client id and
   secret, scope `mandate/tools`). That gives an access token for the agent workload.
2. It sends that token to `POST /api/v1/sessions/{sid}/mandate`. The broker only answers
   for a session a logged-in staffer started and whose staffer is still active. The answer
   is a 15-minute mandate token that says "agent-1 acting for maya in rep-x".
"""

from __future__ import annotations

import os

import httpx

AGENT_SCOPE = "mandate/tools"


class BrokerError(RuntimeError):
    """The agent could not get a mandate token (bad credentials, unknown or revoked session)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status  # the broker's HTTP status, when the broker itself refused


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
    http = http or httpx.Client(timeout=10)
    client_token = fetch_agent_client_token(http)
    response = http.post(
        f"{api_url}/api/v1/sessions/{sid}/mandate",
        json={},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    if response.status_code != 200:
        raise BrokerError(
            f"The broker refused a mandate for session {sid} (HTTP {response.status_code}).", response.status_code
        )
    return response.json()["mandateToken"]
