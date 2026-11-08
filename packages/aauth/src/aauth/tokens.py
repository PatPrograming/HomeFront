"""Mint and verify aa-agent+jwt, oauth-id-jag+jwt, and aa-auth+jwt."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .keys import AgentKey, public_key_from_jwk

AA_AGENT_TYP = "aa-agent+jwt"
ID_JAG_TYP = "oauth-id-jag+jwt"
AA_AUTH_TYP = "aa-auth+jwt"

RAS_ISSUER_DEFAULT = "homefront-ras"
AAUTH_AUD = "homefront-mcp"


@dataclass(frozen=True)
class AuthProtocols:
    legacy: bool = True
    idjag: bool = True
    aauth: bool = True


def auth_protocols_enabled(raw: str | None = None) -> AuthProtocols:
    text = (raw if raw is not None else os.environ.get("HOMEFRONT_AUTH_PROTOCOLS", "legacy,aauth,idjag")).lower()
    parts = {p.strip() for p in text.split(",") if p.strip()}
    if "all" in parts:
        return AuthProtocols(True, True, True)
    return AuthProtocols(
        legacy="legacy" in parts,
        idjag="idjag" in parts,
        aauth="aauth" in parts,
    )


def ras_issuer() -> str:
    return (os.environ.get("AAUTH_ISSUER") or RAS_ISSUER_DEFAULT).strip() or RAS_ISSUER_DEFAULT


def ras_secret() -> str:
    secret = (os.environ.get("AAUTH_SIGNING_KEY") or os.environ.get("MANDATE_SIGNING_SECRET") or "").strip()
    if len(secret) < 32:
        raise RuntimeError("AAUTH_SIGNING_KEY or MANDATE_SIGNING_SECRET must be at least 32 characters")
    return secret


def public_jwk(agent_key: AgentKey) -> dict[str, str]:
    x = base64.urlsafe_b64encode(agent_key.public_raw()).decode("ascii").rstrip("=")
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": agent_key.kid, "alg": "EdDSA"}


def jwk_thumbprint(jwk: dict[str, Any]) -> str:
    """RFC 7638 JWK thumbprint for OKP/Ed25519."""
    required = {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"]}
    digest = hashlib.sha256(json.dumps(required, separators=(",", ":"), sort_keys=True).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def mint_aa_agent_jwt(
    agent_key: AgentKey,
    *,
    issuer: str,
    subject: str,
    ttl_seconds: int = 3600,
    now: int | None = None,
) -> str:
    ts = int(time.time() if now is None else now)
    jwk = public_jwk(agent_key)
    headers = {"alg": "EdDSA", "typ": AA_AGENT_TYP, "kid": agent_key.kid}
    payload = {
        "iss": issuer,
        "sub": subject,
        "iat": ts,
        "exp": ts + min(ttl_seconds, 24 * 3600),
        "jti": str(uuid.uuid4()),
        "cnf": {"jwk": jwk},
    }
    return jwt.encode(payload, agent_key.private_key, algorithm="EdDSA", headers=headers)


def verify_aa_agent_jwt(token: str, *, expected_sub: str | None = None) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    if header.get("typ") != AA_AGENT_TYP:
        raise jwt.InvalidTokenError("not an aa-agent+jwt")
    unverified = jwt.decode(token, options={"verify_signature": False})
    jwk = (unverified.get("cnf") or {}).get("jwk")
    if not isinstance(jwk, dict):
        raise jwt.InvalidTokenError("aa-agent+jwt missing cnf.jwk")
    key = public_key_from_jwk(jwk)
    claims = jwt.decode(token, key, algorithms=["EdDSA"], options={"require": ["exp", "iat", "iss", "sub", "cnf"]})
    if expected_sub and claims.get("sub") != expected_sub:
        raise jwt.InvalidTokenError("agent subject mismatch")
    return claims


def mint_id_jag(
    *,
    staffer: str,
    agent_id: str,
    office: str,
    sid: str,
    mode: str = "official",
    ttl_seconds: int = 300,
    secret: str | None = None,
    now: int | None = None,
) -> str:
    ts = int(time.time() if now is None else now)
    payload = {
        "iss": ras_issuer(),
        "sub": staffer,
        "aud": ras_issuer(),
        "client_id": agent_id,
        "office": office,
        "sid": sid,
        "mode": mode,
        "iat": ts,
        "exp": ts + ttl_seconds,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret or ras_secret(), algorithm="HS256", headers={"typ": ID_JAG_TYP, "alg": "HS256"})


def verify_id_jag(token: str, *, secret: str | None = None) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    if header.get("typ") != ID_JAG_TYP:
        raise jwt.InvalidTokenError("not an oauth-id-jag+jwt")
    return jwt.decode(
        token,
        secret or ras_secret(),
        algorithms=["HS256"],
        audience=ras_issuer(),
        options={"require": ["exp", "iat", "iss", "sub", "aud", "client_id", "sid", "office"]},
    )


def mint_aa_auth_jwt(
    *,
    agent_id: str,
    staffer: str,
    office: str,
    sid: str,
    jkt: str,
    mode: str = "official",
    scope: list[str] | None = None,
    ttl_seconds: int = 900,
    secret: str | None = None,
    now: int | None = None,
    auth_protocol: str = "idjag+aauth",
) -> str:
    ts = int(time.time() if now is None else now)
    payload = {
        "iss": ras_issuer(),
        "sub": agent_id,
        "act": staffer,
        "office": office,
        "mode": mode,
        "sid": sid,
        "scope": scope or ["mandate/tools"],
        "aud": AAUTH_AUD,
        "iat": ts,
        "exp": ts + ttl_seconds,
        "jti": str(uuid.uuid4()),
        "cnf": {"jkt": jkt},
        "auth_protocol": auth_protocol,
        "token_typ": AA_AUTH_TYP,
    }
    return jwt.encode(payload, secret or ras_secret(), algorithm="HS256", headers={"typ": AA_AUTH_TYP, "alg": "HS256"})


def verify_aa_auth_jwt(token: str, *, secret: str | None = None, expected_jkt: str | None = None) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    if header.get("typ") != AA_AUTH_TYP:
        raise jwt.InvalidTokenError("not an aa-auth+jwt")
    claims = jwt.decode(
        token,
        secret or ras_secret(),
        algorithms=["HS256"],
        audience=AAUTH_AUD,
        options={"require": ["exp", "iat", "iss", "sub", "act", "sid", "office", "cnf"]},
    )
    jkt = (claims.get("cnf") or {}).get("jkt")
    if not jkt:
        raise jwt.InvalidTokenError("aa-auth+jwt missing cnf.jkt")
    if expected_jkt and jkt != expected_jkt:
        raise jwt.InvalidTokenError("proof-of-possession key thumbprint mismatch")
    return claims


def public_key_pem_from_claims(agent_claims: dict[str, Any]) -> str:
    jwk = (agent_claims.get("cnf") or {}).get("jwk") or {}
    key = public_key_from_jwk(jwk)
    return key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")
