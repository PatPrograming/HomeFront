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


ENV_FILE_HINT = "agent/.env on the server"


class BrokerError(RuntimeError):
    """The agent could not get a mandate token.

    `kind` says why, so the chat can name the real cause instead of "the session ended":
    not_set_up (a Cognito setting is missing), credentials_refused (Cognito said no),
    unreachable (Cognito or the broker could not be reached), session_refused (the broker said no:
    the session is unknown, revoked or expired).
    """

    def __init__(self, message: str, status: int | None = None, kind: str = "session_refused"):
        super().__init__(message)
        self.status = status  # the broker's HTTP status, when the broker itself refused
        self.kind = kind


def _setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BrokerError(f"{name} is missing or empty: set it in {ENV_FILE_HINT}, then restart.", kind="not_set_up")
    return value


def fetch_agent_client_token(http: httpx.Client | None = None) -> str:
    """Cognito client_credentials grant with COGNITO_DOMAIN / COGNITO_AGENT_CLIENT_ID / _SECRET."""
    domain = _setting("COGNITO_DOMAIN")
    auth = (_setting("COGNITO_AGENT_CLIENT_ID"), _setting("COGNITO_AGENT_CLIENT_SECRET"))
    base = domain if "://" in domain else f"https://{domain}"
    http = http or httpx.Client(timeout=10)
    try:
        response = http.post(
            f"{base.rstrip('/')}/oauth2/token",
            data={"grant_type": "client_credentials", "scope": AGENT_SCOPE},
            auth=auth,
        )
    except httpx.HTTPError as error:
        raise BrokerError(f"Cognito could not be reached ({type(error).__name__}).", kind="unreachable") from error
    if response.status_code != 200:
        raise BrokerError(
            f"Cognito refused the agent's client credentials (HTTP {response.status_code}): check "
            f"COGNITO_AGENT_CLIENT_ID and COGNITO_AGENT_CLIENT_SECRET in {ENV_FILE_HINT}.",
            kind="credentials_refused",
        )
    return response.json()["access_token"]


def fetch_mandate_token(api_url: str, sid: str, http: httpx.Client | None = None) -> str:
    http = http or httpx.Client(timeout=10)
    client_token = fetch_agent_client_token(http)
    try:
        response = http.post(
            f"{api_url}/api/v1/sessions/{sid}/mandate",
            json={},
            headers={"Authorization": f"Bearer {client_token}"},
        )
    except httpx.HTTPError as error:
        raise BrokerError(f"The broker at {api_url} could not be reached ({type(error).__name__}).", kind="unreachable") from error
    if response.status_code >= 500:
        raise BrokerError(f"The broker failed (HTTP {response.status_code}).", response.status_code, kind="unreachable")
    if response.status_code != 200:
        raise BrokerError(
            f"The broker refused a mandate for session {sid} (HTTP {response.status_code}).", response.status_code
        )
    return response.json()["mandateToken"]
