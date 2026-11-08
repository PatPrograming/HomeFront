"""Mandate tokens: minted by the broker, checked by the gateway on every call.

HS256 with `MANDATE_SIGNING_SECRET`. Only HS256 is accepted, so a token that
says `alg: none` or switches algorithm is rejected.
"""

from __future__ import annotations

import os

import jwt
from pydantic import ValidationError

from mandate_contracts import ISSUER, SCOPE, MandateClaims

REQUIRED_CLAIMS = ["iss", "sub", "act", "office", "sid", "scope", "iat", "exp"]
MIN_SECRET_LENGTH = 32


class InvalidToken(Exception):
    """The mandate token is missing, forged, expired or malformed (HTTP 401)."""


def signing_secret() -> str:
    secret = os.environ.get("MANDATE_SIGNING_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(f"MANDATE_SIGNING_SECRET must be set to at least {MIN_SECRET_LENGTH} characters")
    return secret


def mint_mandate_token(claims: MandateClaims, secret: str) -> str:
    return jwt.encode(claims.model_dump(), secret, algorithm="HS256")


def verify_mandate_token(token: str, secret: str) -> MandateClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=ISSUER,
            options={"require": REQUIRED_CLAIMS},
        )
        claims = MandateClaims.model_validate(payload)
    except (jwt.PyJWTError, ValidationError, TypeError, ValueError) as exc:
        raise InvalidToken(str(exc)) from exc
    if SCOPE not in claims.scope:
        raise InvalidToken("token lacks the mandate/tools scope")
    return claims
