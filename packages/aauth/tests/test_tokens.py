"""Unit tests for AAuth mint/verify and request signatures."""

from aauth import (
    generate_agent_key,
    jwk_thumbprint,
    mint_aa_agent_jwt,
    mint_aa_auth_jwt,
    mint_id_jag,
    public_jwk,
    sign_request,
    verify_aa_agent_jwt,
    verify_aa_auth_jwt,
    verify_id_jag,
    verify_request_signature,
)


def test_agent_jwt_round_trip():
    key = generate_agent_key("k1")
    token = mint_aa_agent_jwt(key, issuer="https://agent.example", subject="agent-1")
    claims = verify_aa_agent_jwt(token, expected_sub="agent-1")
    assert claims["iss"] == "https://agent.example"
    assert jwk_thumbprint(claims["cnf"]["jwk"]) == jwk_thumbprint(public_jwk(key))


def test_id_jag_and_aa_auth_and_pop(monkeypatch):
    secret = "x" * 32
    monkeypatch.setenv("AAUTH_SIGNING_KEY", secret)
    monkeypatch.setenv("AAUTH_ISSUER", "homefront-ras")
    jag = mint_id_jag(staffer="maya", agent_id="agent-1", office="rep-x", sid="sid-1", secret=secret)
    assert verify_id_jag(jag, secret=secret)["sub"] == "maya"
    key = generate_agent_key()
    jkt = jwk_thumbprint(public_jwk(key))
    auth = mint_aa_auth_jwt(
        agent_id="agent-1", staffer="maya", office="rep-x", sid="sid-1", jkt=jkt, secret=secret
    )
    verify_aa_auth_jwt(auth, secret=secret, expected_jkt=jkt)
    agent_jwt = mint_aa_agent_jwt(key, issuer="https://agent.example", subject="agent-1")
    headers = sign_request(key, method="POST", path="/mcp", body=b"{}", auth_token=auth, agent_jwt=agent_jwt)
    assert verify_request_signature(headers, method="POST", path="/mcp", body=b"{}", auth_token=auth) == jkt
