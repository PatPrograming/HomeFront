"""Ed25519 agent keypairs (private key never enters model context)."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


@dataclass
class AgentKey:
    private_key: Ed25519PrivateKey
    kid: str

    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    def public_raw(self) -> bytes:
        return self.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def private_pem(self) -> str:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")

    def public_pem(self) -> str:
        return self.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def to_env_blob(self) -> str:
        """Compact env form: kid.base64url(32-byte seed)."""
        seed = self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return f"{self.kid}.{base64.urlsafe_b64encode(seed).decode('ascii').rstrip('=')}"


def generate_agent_key(kid: str | None = None) -> AgentKey:
    key = Ed25519PrivateKey.generate()
    return AgentKey(private_key=key, kid=kid or base64.urlsafe_b64encode(os.urandom(8)).decode("ascii").rstrip("="))


def load_agent_key(blob: str | None = None) -> AgentKey:
    """Load from AGENT_AAUTH_KEY env (kid.seed) or generate an ephemeral key."""
    raw = (blob if blob is not None else os.environ.get("AGENT_AAUTH_KEY", "")).strip()
    if not raw:
        return generate_agent_key()
    if raw.startswith("-----BEGIN"):
        private = serialization.load_pem_private_key(raw.encode("ascii"), password=None)
        if not isinstance(private, Ed25519PrivateKey):
            raise ValueError("AGENT_AAUTH_KEY PEM must be Ed25519")
        return AgentKey(private_key=private, kid="pem")
    kid, _, seed_b64 = raw.partition(".")
    if not kid or not seed_b64:
        raise ValueError("AGENT_AAUTH_KEY must be kid.base64url_seed or a PEM private key")
    pad = "=" * (-len(seed_b64) % 4)
    seed = base64.urlsafe_b64decode(seed_b64 + pad)
    if len(seed) != 32:
        raise ValueError("AGENT_AAUTH_KEY seed must be 32 bytes")
    return AgentKey(private_key=Ed25519PrivateKey.from_private_bytes(seed), kid=kid)


def public_key_from_jwk(jwk: dict) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise ValueError("unsupported JWK")
    x = jwk["x"]
    pad = "=" * (-len(x) % 4)
    return Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(x + pad))


def dump_discovery(agent_key: AgentKey, issuer: str) -> dict:
    from .tokens import public_jwk

    return {
        "issuer": issuer,
        "jwks": {"keys": [public_jwk(agent_key)]},
    }
