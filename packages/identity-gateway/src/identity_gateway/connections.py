"""Connected accounts: a staffer links their own Gmail or X so the assistant can send as them.

Tokens never leave this module in plain text. They are encrypted with KMS (bound to the
staffer and provider) before they touch DynamoDB, and nothing here logs a token, code,
verifier or ciphertext. Every failure to get a usable token means `NeedsConnection`:
the assistant then asks the person to connect, and nothing is sent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Callable, Protocol

import httpx
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

from .storage import MandateStore

log = logging.getLogger("identity_gateway.connections")
# The AWS SDK's DEBUG logs print whole request/response bodies: the PKCE verifier going into
# DynamoDB and the decrypted tokens coming out of KMS. Keep them out of any log, even in debug runs.
for _sdk_logger in ("boto3", "botocore"):
    _sdk = logging.getLogger(_sdk_logger)
    _sdk.setLevel(max(_sdk.getEffectiveLevel(), logging.INFO))  # raise the floor, never lower a stricter level

CONNECTABLE = ("gmail", "x")
PROVIDERS = {"gmail": "Gmail", "x": "X", "linkedin": "LinkedIn", "facebook": "Facebook", "bluesky": "Bluesky"}
SCOPES = {
    "gmail": "openid email https://www.googleapis.com/auth/gmail.send",
    "x": "tweet.read tweet.write users.read offline.access",
}
REQUIRED_SCOPE = {"gmail": "https://www.googleapis.com/auth/gmail.send", "x": "tweet.write"}
AUTH_URL = {"gmail": "https://accounts.google.com/o/oauth2/v2/auth", "x": "https://x.com/i/oauth2/authorize"}
TOKEN_URL = {"gmail": "https://oauth2.googleapis.com/token", "x": "https://api.x.com/2/oauth2/token"}
X_HOSTS = ("x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com")
REVOKE_URL = {"gmail": "https://oauth2.googleapis.com/revoke", "x": "https://api.x.com/2/oauth2/revoke"}
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
X_ME_URL = "https://api.x.com/2/users/me"
X_TWEETS_URL = "https://api.x.com/2/tweets"
GOOGLE_EXTRA_AUTH_PARAMS = {"access_type": "offline", "prompt": "consent"}  # offline = we get a refresh token

STATE_TTL_SECONDS = 600
REFRESH_WINDOW_SECONDS = 60  # refresh an access token this close to its expiry
REFRESH_LEASE_SECONDS = 30  # how long one refresh may hold the right to delete a row on a 400
DEFAULT_REDIRECT_URI = "http://localhost:3000/account/connected"


class NeedsConnection(Exception):
    """No usable grant for this provider. `str(e)` is just the provider id, never a token."""

    def __init__(self, provider: str) -> None:
        super().__init__(provider)
        self.provider = provider


class ProviderError(Exception):
    """The provider's API failed for a reason that is not 'sign in again'."""


class MissingPermission(ProviderError):
    """The person signed in but did not tick the permission we need (send mail, post)."""


class OAuthStateError(Exception):
    """Missing, unknown, expired, replayed or another user's OAuth state."""


class NotConfigured(Exception):
    """No KMS key, or no client id/secret, for that provider."""


@dataclass(frozen=True)
class Connection:
    staffer: str
    provider: str
    handle: str
    connected_at: str
    scopes: str


class Cipher(Protocol):
    def encrypt(self, plaintext: bytes, context: dict[str, str]) -> bytes: ...
    def decrypt(self, ciphertext: bytes, context: dict[str, str]) -> bytes: ...


class KmsCipher:
    """KMS Encrypt/Decrypt. The context ties a ciphertext to one staffer and one provider."""

    def __init__(self, kms_client: Any, key_id: str) -> None:
        self.kms = kms_client
        self.key_id = key_id

    def encrypt(self, plaintext: bytes, context: dict[str, str]) -> bytes:
        return self.kms.encrypt(KeyId=self.key_id, Plaintext=plaintext, EncryptionContext=context)["CiphertextBlob"]

    def decrypt(self, ciphertext: bytes, context: dict[str, str]) -> bytes:
        return self.kms.decrypt(KeyId=self.key_id, CiphertextBlob=ciphertext, EncryptionContext=context)["Plaintext"]


@dataclass(frozen=True)
class OAuthConfig:
    kms_key_id: str
    redirect_uri: str
    clients: dict[str, tuple[str, str]]  # provider -> (client id, client secret)

    @classmethod
    def from_env(cls) -> "OAuthConfig":
        env = os.environ.get
        return cls(
            kms_key_id=env("CONNECTIONS_KMS_KEY_ID", ""),
            redirect_uri=env("CONNECTIONS_REDIRECT_URI") or DEFAULT_REDIRECT_URI,
            clients={
                "gmail": (env("GOOGLE_OAUTH_CLIENT_ID", ""), env("GOOGLE_OAUTH_CLIENT_SECRET", "")),
                "x": (env("X_OAUTH_CLIENT_ID", ""), env("X_OAUTH_CLIENT_SECRET", "")),
            },
        )

    def configured(self, provider: str) -> bool:
        return provider in CONNECTABLE and bool(self.kms_key_id) and all(self.clients[provider])


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _tweet_id(reply_to: str) -> str:
    """X wants the post's numeric id; the agent usually has the post's link (x.com/<user>/status/<id>)."""
    if reply_to.isdigit():
        return reply_to
    link = urllib.parse.urlparse(reply_to)
    match = re.fullmatch(r"/[^/]+/status/(\d+)/?", link.path)
    if link.scheme != "https" or link.hostname not in X_HOSTS or not match:
        raise ProviderError("reply_to is not an X post")
    return match.group(1)


def _key(staffer: str, provider: str) -> dict[str, str]:
    return {"pk": f"CONNECTION#{staffer}", "sk": f"PROVIDER#{provider}"}


def _state_key(state: str) -> dict[str, str]:
    return {"pk": f"OAUTHSTATE#{state}", "sk": "STATE"}


class ConnectionService:
    def __init__(
        self,
        store: MandateStore,
        cipher: Cipher | None,
        http: httpx.Client,
        config: OAuthConfig,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.cipher = cipher
        self.http = http
        self.config = config
        self.clock = clock

    # --- what the Account page shows ------------------------------------

    def list(self, staffer: str) -> list[dict]:
        rows = self.store.table.query(
            KeyConditionExpression=Key("pk").eq(f"CONNECTION#{staffer}"), ConsistentRead=True
        )["Items"]
        # A row without a handle is a half-written leftover (e.g. a refresh racing a disconnect); not a connection.
        by_provider = {row["sk"].removeprefix("PROVIDER#"): row for row in rows if "handle" in row}
        entries = []
        for provider, label in PROVIDERS.items():
            row = by_provider.get(provider)
            entries.append({
                "provider": provider,
                "label": label,
                "supported": provider in CONNECTABLE,
                "configured": self.config.configured(provider),
                "connected": row is not None,
                "handle": row["handle"] if row else None,
                "connectedAt": row["connected_at"] if row else None,
                "scopes": row["scopes"] if row else None,
            })
        return entries

    # --- connecting ------------------------------------------------------

    def start(self, staffer: str, provider: str) -> str:
        """Save a one-time state + PKCE verifier and return the provider's sign-in URL."""
        if not self.config.configured(provider):
            raise NotConfigured(provider)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        self.store.table.put_item(Item={
            **_state_key(state),
            "staffer": staffer,
            "provider": provider,
            "verifier": verifier,
            "expires": int(self.clock()) + STATE_TTL_SECONDS,
        })
        query = {
            "response_type": "code",
            "client_id": self.config.clients[provider][0],
            "redirect_uri": self.config.redirect_uri,
            "scope": SCOPES[provider],
            "state": state,
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
        }
        if provider == "gmail":
            query.update(GOOGLE_EXTRA_AUTH_PARAMS)
        return f"{AUTH_URL[provider]}?{urllib.parse.urlencode(query)}"

    def finish(self, staffer: str, code: str, state: str) -> Connection:
        # Deleting the state is the first thing we do, so it works once even if every check below fails.
        consumed = self.store.table.delete_item(Key=_state_key(state), ReturnValues="ALL_OLD").get("Attributes")
        if not consumed or consumed["staffer"] != staffer or self.clock() > consumed["expires"]:
            raise OAuthStateError("invalid state")
        provider = consumed["provider"]
        if not self.config.configured(provider) or self.cipher is None:
            raise NotConfigured(provider)

        response = self._token_request(
            provider,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "code_verifier": consumed["verifier"],
            },
        )
        if response.status_code != 200:
            log.warning("token exchange failed provider=%s staffer=%s status=%s", provider, staffer, response.status_code)
            raise ProviderError(f"{provider} token exchange failed")
        body = response.json()
        required = ("access_token", "expires_in") + (("id_token",) if provider == "gmail" else ())
        if any(field not in body for field in required):
            log.warning("token response incomplete provider=%s staffer=%s", provider, staffer)
            self._revoke_quietly(provider, body)
            raise ProviderError(f"{provider} token response was incomplete")
        # RFC 6749 5.1: a server may omit `scope` when it equals what we asked for.
        scopes = body.get("scope") or SCOPES[provider]
        if REQUIRED_SCOPE[provider] not in scopes.split():
            log.warning("required permission not granted provider=%s staffer=%s", provider, staffer)
            self._revoke_quietly(provider, body)
            raise MissingPermission(f"{provider} permission was not granted")
        try:
            handle = self._gmail_handle(body["id_token"]) if provider == "gmail" else self._x_handle(body["access_token"])
        except Exception:
            self._revoke_quietly(provider, body)  # the grant exists at the provider but we will not store it
            raise
        tokens = {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token"),
            "expires_at": self.clock() + body["expires_in"],
        }
        connected_at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
        self.store.table.put_item(Item={
            **_key(staffer, provider),
            "handle": handle,
            "connected_at": connected_at,
            "scopes": scopes,
            "ciphertext": self._encrypt(staffer, provider, tokens),
        })
        log.info("connected provider=%s staffer=%s", provider, staffer)
        return Connection(staffer, provider, handle, connected_at, scopes)

    def disconnect(self, staffer: str, provider: str) -> bool:
        """Revoke at the provider (best effort) and delete our copy. True if there was a connection."""
        item = self.store.table.get_item(Key=_key(staffer, provider), ConsistentRead=True).get("Item")
        if item is None:
            return False
        try:
            self._revoke(provider, self._decrypt(staffer, provider, item))
        except Exception:  # KMS down, bad ciphertext, provider down: our copy must still go
            log.warning("provider revoke skipped provider=%s staffer=%s", provider, staffer)
        self._forget(staffer, provider)
        log.info("disconnected provider=%s staffer=%s", provider, staffer)
        return True

    def revoke_all(self, staffer: str) -> int:
        """Offboarding: drop every connection this staffer has."""
        rows = self.store.table.query(
            KeyConditionExpression=Key("pk").eq(f"CONNECTION#{staffer}"), ConsistentRead=True
        )["Items"]
        removed = 0
        for row in rows:
            provider = row["sk"].removeprefix("PROVIDER#")
            try:
                removed += self.disconnect(staffer, provider)
            except Exception:  # one provider failing must not leave the others connected
                log.exception("disconnect failed provider=%s staffer=%s", provider, staffer)
        return removed

    # --- using a connection ----------------------------------------------

    def require(self, staffer: str, provider: str) -> Connection:
        item, _ = self._live_tokens(staffer, provider)
        return Connection(staffer, provider, item["handle"], item["connected_at"], item["scopes"])

    def send_gmail(self, staffer: str, to: str, subject: str, body: str, in_reply_to: str | None = None) -> dict:
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)
        sent = self._call(staffer, "gmail", GMAIL_SEND_URL, {"raw": _b64url(message.as_bytes())})
        return {"status": "sent", "messageId": sent["id"]}

    def post_x(self, staffer: str, text: str, reply_to: str | None = None) -> dict:
        payload: dict[str, Any] = {"text": text}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": _tweet_id(reply_to)}
        posted = self._call(staffer, "x", X_TWEETS_URL, payload)
        return {"status": "posted", "id": posted["data"]["id"]}

    # --- internals -------------------------------------------------------

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return self.http.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            raise ProviderError("provider unreachable") from e

    def _revoke(self, provider: str, tokens: dict) -> None:
        # Revoking the refresh token ends the whole grant (its access tokens too) at Google and X.
        token, hint = (
            (tokens["refresh_token"], "refresh_token") if tokens.get("refresh_token")
            else (tokens["access_token"], "access_token")
        )
        self._send("POST", REVOKE_URL[provider], **self._client_auth(provider, {"token": token, "token_type_hint": hint}))

    def _revoke_quietly(self, provider: str, tokens: dict) -> None:
        """Hand back a grant we will not keep. Failure only means the grant lives until the person revokes it."""
        if not (tokens.get("refresh_token") or tokens.get("access_token")):
            return
        try:
            self._revoke(provider, tokens)
        except ProviderError:
            log.warning("provider revoke failed provider=%s", provider)

    def _client_auth(self, provider: str, form: dict[str, str]) -> dict[str, Any]:
        """Request kwargs for a token/revoke call: Google wants the secret in the form, X wants HTTP Basic."""
        client_id, client_secret = self.config.clients[provider]
        if provider == "x":
            return {"data": {**form, "client_id": client_id}, "auth": httpx.BasicAuth(client_id, client_secret)}
        return {"data": {**form, "client_id": client_id, "client_secret": client_secret}}

    def _token_request(self, provider: str, form: dict[str, str]) -> httpx.Response:
        return self._send("POST", TOKEN_URL[provider], **self._client_auth(provider, form))

    def _context(self, staffer: str, provider: str) -> dict[str, str]:
        return {"staffer": staffer, "provider": provider}

    def _encrypt(self, staffer: str, provider: str, tokens: dict) -> str:
        blob = self.cipher.encrypt(json.dumps(tokens).encode(), self._context(staffer, provider))
        # Stored as base64 text, not DynamoDB Binary: a boto3 Binary cannot be json-dumped (its str() is bytes).
        return base64.b64encode(blob).decode()

    def _decrypt(self, staffer: str, provider: str, item: dict) -> dict:
        if self.cipher is None:
            raise NeedsConnection(provider)
        return json.loads(self.cipher.decrypt(base64.b64decode(item["ciphertext"]),self._context(staffer, provider)))

    def _forget(self, staffer: str, provider: str) -> None:
        self.store.table.delete_item(Key=_key(staffer, provider))

    def _live_tokens(self, staffer: str, provider: str) -> tuple[dict, dict]:
        """The stored row and a non-expired token set, or NeedsConnection."""
        member = self.store.get_staffer(staffer)
        if provider not in CONNECTABLE or member is None or not member.active:
            raise NeedsConnection(provider)
        item = self.store.table.get_item(Key=_key(staffer, provider), ConsistentRead=True).get("Item")
        if item is None:
            raise NeedsConnection(provider)
        try:
            tokens = self._decrypt(staffer, provider, item)
        except ClientError as e:  # wrong staffer/provider context, or the key is gone
            log.warning("decrypt failed provider=%s staffer=%s", provider, staffer)
            raise NeedsConnection(provider) from e
        if tokens["expires_at"] - REFRESH_WINDOW_SECONDS <= self.clock():
            tokens, ciphertext = self._refresh(staffer, provider, item, tokens)
            item = {**item, "ciphertext": ciphertext}  # a 401 on this call must compare against the row as refreshed
        return item, tokens

    def _refresh(self, staffer: str, provider: str, item: dict, tokens: dict) -> tuple[dict, str]:
        """New tokens and the ciphertext now stored. Only the refresh holding the lease may delete on a 400."""
        holds_lease = self._claim_refresh(staffer, provider, item["ciphertext"])
        response = self._token_request(provider, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        if response.status_code in (400, 401):  # the provider says the grant is dead
            log.warning("refresh rejected provider=%s staffer=%s status=%s", provider, staffer, response.status_code)
            # Another refresh is in flight and may be about to save a good row: leave it, it decides.
            if not holds_lease or not self._forget_if_unchanged(staffer, provider, item["ciphertext"]):
                raise ProviderError(f"{provider} was refreshed or removed at the same time; try again")
            self._revoke_quietly(provider, tokens)
            raise NeedsConnection(provider)
        if response.status_code != 200:  # outage or rate limit: the grant may be fine, keep it
            log.warning("refresh failed provider=%s staffer=%s status=%s", provider, staffer, response.status_code)
            raise ProviderError(f"{provider} refresh failed")
        body = response.json()
        if "access_token" not in body or "expires_in" not in body:
            log.warning("refresh response incomplete provider=%s staffer=%s", provider, staffer)
            raise ProviderError(f"{provider} refresh response was incomplete")
        refreshed = {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", tokens["refresh_token"]),  # Google only sends one at consent
            "expires_at": self.clock() + body["expires_in"],
        }
        ciphertext = self._encrypt(staffer, provider, refreshed)
        try:
            self.store.table.update_item(
                Key=_key(staffer, provider),
                UpdateExpression="SET ciphertext = :new REMOVE refreshing_until",
                # Still the row we read: a disconnect stays disconnected, a reconnect is not overwritten.
                ConditionExpression="ciphertext = :old",
                ExpressionAttributeValues={":new": ciphertext, ":old": item["ciphertext"]},
            )
        except self.store.table.meta.client.exceptions.ConditionalCheckFailedException as e:
            if self.store.table.get_item(Key=_key(staffer, provider), ConsistentRead=True).get("Item") is None:
                raise NeedsConnection(provider) from e
            raise ProviderError(f"{provider} was refreshed or reconnected at the same time; try again") from e
        return refreshed, ciphertext

    def _claim_refresh(self, staffer: str, provider: str, ciphertext: str) -> bool:
        """Take the short lease that lets this refresh delete the row if the provider rejects it. False if another holds it."""
        now = int(self.clock())
        try:
            self.store.table.update_item(
                Key=_key(staffer, provider),
                UpdateExpression="SET refreshing_until = :until",
                ConditionExpression="ciphertext = :old AND (attribute_not_exists(refreshing_until) OR refreshing_until < :now)",
                ExpressionAttributeValues={":until": now + REFRESH_LEASE_SECONDS, ":old": ciphertext, ":now": now},
            )
        except self.store.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def _forget_if_unchanged(self, staffer: str, provider: str, ciphertext: str) -> bool:
        """Delete the row only if it still holds what we read. False means it changed (a refresh or reconnect won)."""
        try:
            self.store.table.delete_item(
                Key=_key(staffer, provider),
                ConditionExpression="ciphertext = :old",
                ExpressionAttributeValues={":old": ciphertext},
            )
        except self.store.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def _call(self, staffer: str, provider: str, url: str, payload: dict) -> dict:
        item, tokens = self._live_tokens(staffer, provider)
        response = self._send("POST", url, json=payload, headers={"Authorization": f"Bearer {tokens['access_token']}"})
        if response.status_code == 401:  # revoked or expired at the provider: our copy is useless
            log.warning("provider rejected token provider=%s staffer=%s", provider, staffer)
            self._forget_if_unchanged(staffer, provider, item["ciphertext"])  # keep a row a reconnect replaced
            raise NeedsConnection(provider)
        if response.status_code >= 400:
            log.warning("provider call failed provider=%s staffer=%s status=%s", provider, staffer, response.status_code)
            raise ProviderError(f"{provider} returned {response.status_code}")
        return response.json()

    @staticmethod
    def _gmail_handle(id_token: str) -> str:
        # Payload decode only, no signature check: Google's docs allow this for an id_token
        # received straight from the token endpoint over TLS.
        try:
            payload = id_token.split(".")[1]
            return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["email"]
        except (IndexError, ValueError, KeyError, TypeError) as e:
            raise ProviderError("gmail id_token was malformed") from e

    def _x_handle(self, access_token: str) -> str:
        response = self._send("GET", X_ME_URL, headers={"Authorization": f"Bearer {access_token}"})
        if response.status_code != 200:
            raise ProviderError("x did not return the account name")
        try:
            return "@" + response.json()["data"]["username"]
        except (ValueError, KeyError, TypeError) as e:
            raise ProviderError("x account name was missing from the reply") from e


# --- singleton, same pattern as get_gateway -------------------------------------------------

_connections: ConnectionService | None = None


def get_connections() -> ConnectionService:
    """The process-wide service, built from the environment on first use."""
    global _connections
    if _connections is None:
        import boto3

        # The same table the gateway uses, without building the gateway (that also needs the policy store).
        config = OAuthConfig.from_env()
        cipher = KmsCipher(boto3.client("kms", region_name="us-east-1"), config.kms_key_id) if config.kms_key_id else None
        _connections = ConnectionService(MandateStore.from_env(), cipher, httpx.Client(timeout=10), config)
    return _connections


def set_connections(service: ConnectionService | None) -> None:
    """Install a service (tests) or None to rebuild from the environment."""
    global _connections
    _connections = service


# --- helpers the gateway's tool handlers call -----------------------------------------------


def require_connection(staffer_id: str, provider: str) -> Connection:
    return get_connections().require(staffer_id, provider)


def send_gmail(staffer_id: str, to: str, subject: str, body: str, in_reply_to: str | None = None) -> dict:
    return get_connections().send_gmail(staffer_id, to, subject, body, in_reply_to)


def post_x(staffer_id: str, text: str, reply_to: str | None = None) -> dict:
    return get_connections().post_x(staffer_id, text, reply_to)


def needs_connection_result(e: NeedsConnection) -> dict:
    label = PROVIDERS.get(e.provider, e.provider)
    return {
        "status": "needs_connection",
        "provider": e.provider,
        "label": label,
        "connectUrl": f"/account?connect={e.provider}",
        "note": f"NOT sent. The staffer has not connected {label}. Ask them to connect it on the Account page, then try again.",
    }


def provider_for_tool(tool: str, args: dict) -> str | None:
    """Which connected account a tool needs, or None when it needs none (or is handled elsewhere)."""
    if tool in ("email_send", "email_send_list"):
        return "gmail"
    if tool in ("social_post", "social_reply") and args.get("platform") in (None, "", "x"):
        return "x"
    return None
