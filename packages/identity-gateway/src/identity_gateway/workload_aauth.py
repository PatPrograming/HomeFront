"""AAuth binding for the Agents / Monitoring workload-token pathway.

Opaque ``gr_*`` tokens remain the revoke handle (token_version). When
``HOMEFRONT_AUTH_PROTOCOLS`` includes ``aauth`` and a signing secret is
configured, each issued token also gets an Ed25519 agent key, ``aa-agent+jwt``,
and PoP-bound ``aa-auth+jwt``. The private key stays in the gateway process;
tool calls re-prove possession server-side so Monitoring needs no browser crypto.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

WORKLOAD_AAUTH = "aauth"
LEGACY_WORKLOAD = "legacy_workload"


def bind_workload_aauth(
    rec: Any,
    *,
    agent_id: str,
    acting_for: str,
    office: str,
    ttl_seconds: int,
) -> Any:
    """Mutate ``rec`` with AAuth fields when enabled; otherwise leave legacy."""
    try:
        from aauth import (
            auth_protocols_enabled,
            generate_agent_key,
            jwk_thumbprint,
            mint_aa_agent_jwt,
            mint_aa_auth_jwt,
            public_jwk,
        )
        from aauth.tokens import ras_secret
    except ImportError:
        rec.auth_protocol = LEGACY_WORKLOAD
        return rec

    if not auth_protocols_enabled().aauth:
        rec.auth_protocol = LEGACY_WORKLOAD
        return rec

    try:
        secret = ras_secret()
    except RuntimeError:
        log.debug("AAuth signing secret unset; issuing legacy workload token")
        rec.auth_protocol = LEGACY_WORKLOAD
        return rec

    key = generate_agent_key()
    issuer = (os.environ.get("AAUTH_AGENT_ISSUER") or "http://127.0.0.1:8080/agent").rstrip("/")
    agent_jwt = mint_aa_agent_jwt(
        key, issuer=issuer, subject=agent_id, ttl_seconds=ttl_seconds
    )
    jkt = jwk_thumbprint(public_jwk(key))
    aa_auth = mint_aa_auth_jwt(
        agent_id=agent_id,
        staffer=acting_for,
        office=(office or "homefront")[:120],
        sid=rec.session_id,
        jkt=jkt,
        scope=["agent/tools"],
        ttl_seconds=min(ttl_seconds, 900),
        secret=secret,
        auth_protocol=WORKLOAD_AAUTH,
    )
    rec.auth_protocol = WORKLOAD_AAUTH
    rec.jkt = jkt
    rec.key_blob = key.to_env_blob()
    rec.agent_jwt = agent_jwt
    rec.aa_auth_jwt = aa_auth
    rec.acting_for = acting_for
    return rec


def prove_workload_aauth(tok: Any, *, method: str, path: str, body: bytes | str) -> str:
    """Return the auth_protocol after proving PoP for an AAuth-bound token.

    Tokens without a key stay ``legacy_workload``. AAuth-bound tokens are signed
    with the gateway-held agent key and verified before the tool runs.
    """
    protocol = getattr(tok, "auth_protocol", None) or LEGACY_WORKLOAD
    key_blob = getattr(tok, "key_blob", None)
    agent_jwt = getattr(tok, "agent_jwt", None)
    aa_auth = getattr(tok, "aa_auth_jwt", None)
    if protocol != WORKLOAD_AAUTH or not key_blob or not agent_jwt or not aa_auth:
        return LEGACY_WORKLOAD if protocol != WORKLOAD_AAUTH else protocol

    from aauth import load_agent_key, sign_request, verify_request_signature

    key = load_agent_key(key_blob)
    headers = sign_request(
        key,
        method=method,
        path=path,
        body=body,
        auth_token=aa_auth,
        agent_jwt=agent_jwt,
    )
    jkt = verify_request_signature(
        headers, method=method, path=path, body=body, auth_token=aa_auth
    )
    expected = getattr(tok, "jkt", None)
    if expected and jkt != expected:
        raise ValueError("workload token jkt mismatch")
    return WORKLOAD_AAUTH


def tool_call_proof_body(tool_name: str, arguments: dict[str, Any]) -> bytes:
    return json.dumps(
        {"tool": tool_name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
