"""Minimal X (Twitter) API v2 client: OAuth 2.0 (authorization code + PKCE) and posting.

Stdlib only. Tokens are handled by the gateway; nothing here logs or returns
credential material except from the token calls themselves.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
REVOKE_URL = "https://api.x.com/2/oauth2/revoke"
ME_URL = "https://api.x.com/2/users/me"
TWEETS_URL = "https://api.x.com/2/tweets"

# tweet.write posts; offline.access gives the refresh token (access tokens last 2h).
SCOPES = ("tweet.read", "tweet.write", "users.read", "offline.access")

MAX_POST_LENGTH = 280
# X counts every URL as 23 characters regardless of its length.
URL_WEIGHT = 23
_URL_RE = re.compile(r"https?://\S+")


class XApiError(RuntimeError):
    """An X API call failed. `status` is the HTTP status (0 = network error)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def client_id() -> str:
    return (os.environ.get("X_CLIENT_ID") or "").strip()


def client_secret() -> str:
    return (os.environ.get("X_CLIENT_SECRET") or "").strip()


def redirect_uri() -> str:
    return (
        os.environ.get("X_REDIRECT_URI") or ""
    ).strip() or "http://127.0.0.1:8080/api/v1/social/x/callback"


def configured() -> bool:
    return bool(client_id())


def post_length(text: str) -> int:
    """Length as X counts it (URLs are a fixed 23). Close enough to gate on; X is the final judge."""
    return len(_URL_RE.sub("x" * URL_WEIGHT, text))


# --- OAuth 2.0 PKCE ------------------------------------------------------


def new_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) using S256."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _request(
    url: str,
    *,
    method: str = "GET",
    form: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    bearer: str | None = None,
    basic: bool = False,
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "HomeFront/0.1"}
    data: bytes | None = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif basic and client_secret():
        # Confidential app: client credentials go in Basic auth, not the body.
        cred = base64.b64encode(f"{client_id()}:{client_secret()}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise XApiError(_error_text(exc), exc.code) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise XApiError(f"Couldn't reach X: {getattr(exc, 'reason', exc)}") from None
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise XApiError("X returned a non-JSON response") from None


def _error_text(exc: urllib.error.HTTPError) -> str:
    """A short message from X's error body; never includes request headers."""
    try:
        body = json.loads(exc.read(20_000).decode("utf-8", errors="replace"))
    except Exception:
        return f"HTTP {exc.code}"
    parts = [body.get("title"), body.get("detail"), body.get("error_description"), body.get("error")]
    for err in body.get("errors") or []:
        if isinstance(err, dict):
            parts.append(err.get("message"))
    text = "; ".join(str(p) for p in parts if p)
    return f"HTTP {exc.code}: {text}"[:300] if text else f"HTTP {exc.code}"


def _tokens(res: dict[str, Any]) -> dict[str, Any]:
    if not res.get("access_token"):
        raise XApiError("X didn't return an access token")
    # Whole seconds: DynamoDB's resource API rejects floats.
    return {
        "access_token": res["access_token"],
        "refresh_token": res.get("refresh_token") or "",
        "expires_at": int(time.time()) + int(res.get("expires_in") or 7200),
        "scope": res.get("scope") or "",
    }


def exchange_code(code: str, code_verifier: str) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "code_verifier": code_verifier,
        "client_id": client_id(),
    }
    return _tokens(_request(TOKEN_URL, method="POST", form=form, basic=True))


def refresh(refresh_token: str) -> dict[str, Any]:
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id()}
    tokens = _tokens(_request(TOKEN_URL, method="POST", form=form, basic=True))
    # X rotates refresh tokens; keep the old one if a new one wasn't issued.
    tokens["refresh_token"] = tokens["refresh_token"] or refresh_token
    return tokens


def revoke(token: str) -> None:
    """Best effort: the user can also remove the app from their X settings."""
    try:
        _request(
            REVOKE_URL,
            method="POST",
            form={"token": token, "token_type_hint": "access_token", "client_id": client_id()},
            basic=True,
        )
    except XApiError:
        pass


# --- API calls -----------------------------------------------------------


def me(access_token: str) -> dict[str, Any]:
    """{id, username, name} of the account that authorized the app."""
    data = _request(ME_URL, bearer=access_token).get("data") or {}
    if not data.get("id"):
        raise XApiError("X didn't return the account")
    return {"id": data["id"], "username": data.get("username") or "", "name": data.get("name") or ""}


def post_tweet(access_token: str, text: str) -> dict[str, Any]:
    """Create a post. Returns {id, url} (url only when the handle is known to the caller)."""
    data = _request(TWEETS_URL, method="POST", payload={"text": text}, bearer=access_token).get("data") or {}
    if not data.get("id"):
        raise XApiError("X didn't confirm the post")
    return {"id": data["id"]}
