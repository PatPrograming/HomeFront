"""Dev-only mandate token, used until the broker (WP2) exists.

Signed HS256 with MANDATE_DEV_SECRET from the environment, never a committed
secret. Usage:

    MANDATE_DEV_SECRET=... python -m mandate_contracts.dev
"""

from __future__ import annotations

import os
import time

import jwt

from .token import TTL_SECONDS, MandateClaims

DEV_SID = "00000000-0000-4000-8000-000000000001"


def dev_claims(now: int | None = None) -> MandateClaims:
    now = int(time.time()) if now is None else now
    return MandateClaims(
        sub="agent-1",
        act="maya",
        office="rep-x",
        mode="official",
        sid=DEV_SID,
        iat=now,
        exp=now + TTL_SECONDS,
    )


def mint_dev_token(secret: str, claims: MandateClaims | None = None) -> str:
    claims = claims or dev_claims()
    return jwt.encode(claims.model_dump(), secret, algorithm="HS256")


def verify_dev_token(token: str, secret: str) -> MandateClaims:
    """Raises jwt.PyJWTError on a bad signature, expiry or issuer."""
    payload = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        issuer="mandate-broker",
        options={"require": ["exp", "iat", "sub", "sid"]},
    )
    return MandateClaims.model_validate(payload)


if __name__ == "__main__":
    secret = os.environ.get("MANDATE_DEV_SECRET")
    if not secret:
        raise SystemExit("Set MANDATE_DEV_SECRET first.")
    print(mint_dev_token(secret))
