"""Resolve MCP credentials: AAuth (PoP) or legacy HS256 mandate → MandateClaims."""

from __future__ import annotations

from typing import Any, Mapping

from mandate_contracts import MandateClaims, SCOPE

from aauth import (
    auth_protocols_enabled,
    verify_aa_auth_jwt,
    verify_request_signature,
)

from .tokens import InvalidToken, verify_mandate_token


def claims_from_aa_auth(payload: dict[str, Any]) -> MandateClaims:
    return MandateClaims(
        sub=str(payload["sub"]),
        act=str(payload["act"]),
        office=str(payload["office"]),
        mode=payload.get("mode") or "official",
        sid=str(payload["sid"]),
        scope=list(payload.get("scope") or [SCOPE]),
        iat=int(payload["iat"]),
        exp=int(payload["exp"]),
    )


def resolve_call_identity(
    *,
    bearer_token: str,
    secret: str,
    headers: Mapping[str, str] | None = None,
    method: str = "POST",
    path: str = "/mcp",
    body: bytes | str = b"",
) -> tuple[MandateClaims, str]:
    """Return (claims, auth_protocol). Raises InvalidToken on failure."""
    protocols = auth_protocols_enabled()
    headers = headers or {}

    # Prefer AAuth when Signature-Key is present and aauth is enabled.
    has_sig = any(k.lower() == "signature-key" for k in headers)
    if protocols.aauth and has_sig and bearer_token:
        try:
            jkt = verify_request_signature(
                headers, method=method, path=path, body=body, auth_token=bearer_token
            )
            payload = verify_aa_auth_jwt(bearer_token, secret=secret, expected_jkt=jkt)
            return claims_from_aa_auth(payload), str(payload.get("auth_protocol") or "idjag+aauth")
        except Exception as exc:
            raise InvalidToken(f"aauth verification failed: {exc}") from exc

    if protocols.aauth and bearer_token:
        # Allow aa-auth bearer without HTTPSig only when signature headers are absent
        # and legacy is disabled — otherwise require PoP when aauth is the sole path.
        try:
            import jwt

            header = jwt.get_unverified_header(bearer_token)
            if header.get("typ") == "aa-auth+jwt":
                if not protocols.legacy:
                    raise InvalidToken("aa-auth+jwt requires request signature (proof of possession)")
                # Fall through: dual-stack clients should still sign; reject bare aa-auth.
                raise InvalidToken("aa-auth+jwt presented without Signature-Key")
        except InvalidToken:
            raise
        except Exception:
            pass

    if protocols.legacy and bearer_token:
        return verify_mandate_token(bearer_token, secret), "legacy_mandate"

    raise InvalidToken("no acceptable agent authentication")
