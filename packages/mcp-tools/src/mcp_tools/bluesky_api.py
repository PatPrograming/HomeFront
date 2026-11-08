"""Minimal Bluesky (AT Protocol) client: app-password sessions and posting.

Stdlib only. An app password is exchanged for a session once; only the session
tokens are kept, never the password. Nothing here logs credentials.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

DEFAULT_SERVICE = "https://bsky.social"
SIGNUP_URL = "https://bsky.app"
APP_PASSWORDS_URL = "https://bsky.app/settings/app-passwords"

# Posts are limited to 300 graphemes. Code points are never fewer than graphemes,
# so counting them is a safe (slightly conservative) gate.
MAX_POST_LENGTH = 300

# App passwords look like abcd-efgh-ijkl-mnop. Anything else (notably the real
# account password) is refused before it leaves the browser's request.
_APP_PASSWORD_RE = re.compile(r"^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3}$")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_PUNCT = ".,;:!?)]}'\""


class BlueskyError(RuntimeError):
    """A Bluesky call failed. `status` is the HTTP status (0 = network error)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def looks_like_app_password(value: str) -> bool:
    return bool(_APP_PASSWORD_RE.match((value or "").strip()))


def normalize_identifier(identifier: str) -> str:
    """A handle without the leading @ (emails and DIDs pass through unchanged)."""
    return (identifier or "").strip().lstrip("@").lower()


def post_length(text: str) -> int:
    return len(text)


def _request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    bearer: str | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "HomeFront/0.1"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise BlueskyError(_error_text(exc), exc.code) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BlueskyError(f"Couldn't reach Bluesky: {getattr(exc, 'reason', exc)}") from None
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise BlueskyError("Bluesky returned a non-JSON response") from None


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read(20_000).decode("utf-8", errors="replace"))
    except Exception:
        return f"HTTP {exc.code}"
    text = "; ".join(str(p) for p in (body.get("error"), body.get("message")) if p)
    return f"HTTP {exc.code}: {text}"[:300] if text else f"HTTP {exc.code}"


def _jwt_expiry(token: str, default_ttl: int = 3000) -> int:
    """`exp` from a JWT payload (whole seconds); a short default if it can't be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return int(time.time()) + default_ttl


def _service_of(did_doc: Any) -> str:
    """The account's PDS from its DID document, so accounts on other servers work."""
    for svc in (did_doc or {}).get("service") or []:
        if isinstance(svc, dict) and str(svc.get("id", "")).endswith("#atproto_pds"):
            endpoint = str(svc.get("serviceEndpoint") or "")
            if endpoint.startswith("https://"):
                return endpoint.rstrip("/")
    return DEFAULT_SERVICE


def _session(res: dict[str, Any], service: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if not res.get("accessJwt") or not res.get("refreshJwt"):
        raise BlueskyError("Bluesky didn't return a session")
    prev = previous or {}
    return {
        "access_token": res["accessJwt"],
        "refresh_token": res["refreshJwt"],
        "expires_at": _jwt_expiry(res["accessJwt"]),
        "did": res.get("did") or prev.get("did") or "",
        "handle": res.get("handle") or prev.get("handle") or "",
        "service": service,
    }


# --- sessions ----------------------------------------------------------------


def create_session(identifier: str, app_password: str) -> dict[str, Any]:
    """Log in once with an app password. The password is not returned or kept."""
    res = _request(
        f"{DEFAULT_SERVICE}/xrpc/com.atproto.server.createSession",
        method="POST",
        payload={"identifier": identifier, "password": app_password},
    )
    return _session(res, _service_of(res.get("didDoc")))


def refresh_session(connection: dict[str, Any]) -> dict[str, Any]:
    """Rotate the session. Returns the connection's token fields, updated."""
    service = connection.get("service") or DEFAULT_SERVICE
    res = _request(
        f"{service}/xrpc/com.atproto.server.refreshSession",
        method="POST",
        bearer=connection["refresh_token"],
    )
    return _session(res, service, connection)


def delete_session(connection: dict[str, Any]) -> None:
    """Best effort: ends the session. The app password itself stays valid until
    the user deletes it in Bluesky's settings."""
    try:
        _request(
            f"{connection.get('service') or DEFAULT_SERVICE}/xrpc/com.atproto.server.deleteSession",
            method="POST",
            bearer=connection.get("refresh_token"),
        )
    except BlueskyError:
        pass


# --- posting -----------------------------------------------------------------


def link_facets(text: str) -> list[dict[str, Any]]:
    """Clickable-link annotations. Bluesky indexes by UTF-8 byte offsets."""
    facets = []
    for m in _URL_RE.finditer(text):
        url = m.group(0)
        while url and url[-1] in _TRAILING_PUNCT:
            url = url[:-1]
        if not url:
            continue
        start = len(text[: m.start()].encode("utf-8"))
        facets.append(
            {
                "index": {"byteStart": start, "byteEnd": start + len(url.encode("utf-8"))},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
            }
        )
    return facets


def post_url(handle: str, uri: str) -> str:
    return f"https://bsky.app/profile/{handle or 'did'}/post/{uri.rsplit('/', 1)[-1]}"


def create_post(connection: dict[str, Any], text: str) -> dict[str, Any]:
    """Create a post as the connected account. Returns {uri, cid, url}."""
    record: dict[str, Any] = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    facets = link_facets(text)
    if facets:
        record["facets"] = facets
    res = _request(
        f"{connection.get('service') or DEFAULT_SERVICE}/xrpc/com.atproto.repo.createRecord",
        method="POST",
        payload={"repo": connection["did"], "collection": "app.bsky.feed.post", "record": record},
        bearer=connection["access_token"],
    )
    if not res.get("uri"):
        raise BlueskyError("Bluesky didn't confirm the post")
    return {"uri": res["uri"], "cid": res.get("cid"), "url": post_url(connection.get("handle") or "", res["uri"])}
