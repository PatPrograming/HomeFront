"""Minimal request binding: Ed25519 over a canonical request string (RFC 9421-inspired).

Canonical string:
  METHOD\\n
  PATH_WITH_QUERY\\n
  content-digest:sha-256=<b64>\\n
  authorization:<aa-auth token>\\n
"""

from __future__ import annotations

import base64
import hashlib
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .keys import AgentKey, public_key_from_jwk
from .tokens import jwk_thumbprint, verify_aa_agent_jwt


def content_digest(body: bytes | str) -> str:
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return "sha-256=" + base64.b64encode(digest).decode("ascii")


def canonical_request(method: str, path: str, body: bytes | str, auth_token: str) -> bytes:
    return (
        f"{method.upper()}\n"
        f"{path}\n"
        f"content-digest:{content_digest(body)}\n"
        f"authorization:Bearer {auth_token}\n"
    ).encode("utf-8")


def sign_request(
    agent_key: AgentKey,
    *,
    method: str,
    path: str,
    body: bytes | str = b"",
    auth_token: str,
    agent_jwt: str,
) -> dict[str, str]:
    """Return headers to attach to the MCP/HTTP call."""
    message = canonical_request(method, path, body, auth_token)
    sig = agent_key.private_key.sign(message)
    return {
        "Authorization": f"Bearer {auth_token}",
        "Signature-Key": f"jwt={agent_jwt}",
        "Signature": "sig1=:" + base64.b64encode(sig).decode("ascii") + ":",
        "Content-Digest": content_digest(body),
        "X-Auth-Protocol": "idjag+aauth",
    }


def _parse_signature(header: str) -> bytes:
    # sig1=:base64:
    value = header.strip()
    if ":" in value:
        # take innermost base64 between colons of the first label
        parts = value.split(":", 2)
        if len(parts) >= 3:
            return base64.b64decode(parts[1])
    return base64.b64decode(value)


def verify_request_signature(
    headers: Mapping[str, str],
    *,
    method: str,
    path: str,
    body: bytes | str = b"",
    auth_token: str,
) -> str:
    """Verify Signature-Key + Signature; return the agent JWK thumbprint (jkt)."""
    # Headers may arrive lower-cased (ASGI) or mixed.
    def get(name: str) -> str:
        for k, v in headers.items():
            if k.lower() == name.lower():
                return v
        return ""

    key_hdr = get("Signature-Key")
    sig_hdr = get("Signature")
    if not key_hdr.startswith("jwt=") or not sig_hdr:
        raise ValueError("missing Signature-Key jwt or Signature")
    agent_jwt = key_hdr[4:].strip()
    agent_claims = verify_aa_agent_jwt(agent_jwt)
    jwk = (agent_claims.get("cnf") or {}).get("jwk")
    if not isinstance(jwk, dict):
        raise ValueError("agent token missing public key")
    pub: Ed25519PublicKey = public_key_from_jwk(jwk)
    message = canonical_request(method, path, body, auth_token)
    try:
        pub.verify(_parse_signature(sig_hdr), message)
    except InvalidSignature as exc:
        raise ValueError("invalid request signature") from exc
    return jwk_thumbprint(jwk)
