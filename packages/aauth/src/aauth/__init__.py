"""AAuth agent identity and ID-JAG shaped tokens for HomeFront."""

from .httpsig import sign_request, verify_request_signature
from .keys import AgentKey, generate_agent_key, load_agent_key
from .tokens import (
    AA_AGENT_TYP,
    AA_AUTH_TYP,
    ID_JAG_TYP,
    AuthProtocols,
    auth_protocols_enabled,
    jwk_thumbprint,
    mint_aa_agent_jwt,
    mint_aa_auth_jwt,
    mint_id_jag,
    public_jwk,
    ras_secret,
    verify_aa_agent_jwt,
    verify_aa_auth_jwt,
    verify_id_jag,
)

__all__ = [
    "AA_AGENT_TYP",
    "AA_AUTH_TYP",
    "ID_JAG_TYP",
    "AgentKey",
    "AuthProtocols",
    "auth_protocols_enabled",
    "generate_agent_key",
    "jwk_thumbprint",
    "load_agent_key",
    "mint_aa_agent_jwt",
    "mint_aa_auth_jwt",
    "mint_id_jag",
    "public_jwk",
    "ras_secret",
    "sign_request",
    "verify_aa_agent_jwt",
    "verify_aa_auth_jwt",
    "verify_id_jag",
    "verify_request_signature",
]
