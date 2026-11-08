"""Mandate token minting and verification (HS256, fail closed)."""

from __future__ import annotations

import time

import jwt
import pytest
from identity_gateway.tokens import (
    InvalidToken,
    mint_mandate_token,
    signing_secret,
    verify_mandate_token,
)
from mandate_contracts import ISSUER, SCOPE, MandateClaims

SECRET = "unit-test-secret-unit-test-secret-1234"


def _payload(**over):
    now = int(time.time())
    p = {
        "iss": ISSUER,
        "sub": "agent-1",
        "act": "maya",
        "office": "rep-x",
        "mode": "official",
        "sid": "sid-1",
        "scope": [SCOPE],
        "iat": now,
        "exp": now + 900,
    }
    p.update(over)
    return {k: v for k, v in p.items() if v is not None}


def _raw(payload, secret=SECRET, alg="HS256"):
    return jwt.encode(payload, secret, algorithm=alg)


def _claims(**over):
    now = int(time.time())
    base = dict(sub="agent-1", act="maya", office="rep-x", sid="sid-1", iat=now, exp=now + 900)
    base.update(over)
    return MandateClaims(**base)


def test_minted_token_round_trips_all_claims():
    claims = _claims(mode="campaign")
    got = verify_mandate_token(mint_mandate_token(claims, SECRET), SECRET)
    assert isinstance(got, MandateClaims)
    assert got == claims


def test_minted_token_is_hs256():
    header = jwt.get_unverified_header(mint_mandate_token(_claims(), SECRET))
    assert header["alg"] == "HS256"


def test_rejects_a_token_signed_with_another_secret():
    token = _raw(_payload(), secret="another-secret-another-secret-12345")
    with pytest.raises(InvalidToken):
        verify_mandate_token(token, SECRET)


def test_rejects_a_token_whose_payload_was_edited():
    head, payload, sig = mint_mandate_token(_claims(), SECRET).split(".")
    forged = _raw(_payload(act="chiefofstaff"), secret="x" * 40).split(".")[1]
    with pytest.raises(InvalidToken):
        verify_mandate_token(f"{head}.{forged}.{sig}", SECRET)


def test_rejects_an_expired_token():
    now = int(time.time())
    token = _raw(_payload(iat=now - 2000, exp=now - 1000))
    with pytest.raises(InvalidToken):
        verify_mandate_token(token, SECRET)


def test_rejects_the_wrong_issuer():
    with pytest.raises(InvalidToken):
        verify_mandate_token(_raw(_payload(iss="evil-broker")), SECRET)


@pytest.mark.parametrize("claim", ["sub", "act", "office", "sid", "exp", "iat"])
def test_rejects_a_token_missing_a_required_claim(claim):
    payload = _payload()
    del payload[claim]
    with pytest.raises(InvalidToken):
        verify_mandate_token(_raw(payload), SECRET)


@pytest.mark.parametrize("scope", [[], ["other/scope"], ["mandate/read"]])
def test_rejects_a_token_without_the_mandate_tools_scope(scope):
    with pytest.raises(InvalidToken):
        verify_mandate_token(_raw(_payload(scope=scope)), SECRET)


def test_rejects_alg_none():
    token = jwt.encode(_payload(), None, algorithm="none")
    with pytest.raises(InvalidToken):
        verify_mandate_token(token, SECRET)


@pytest.mark.parametrize("alg", ["HS384", "HS512"])
def test_rejects_other_hmac_algorithms_even_with_the_right_secret(alg):
    with pytest.raises(InvalidToken):
        verify_mandate_token(_raw(_payload(), alg=alg), SECRET)


@pytest.mark.parametrize("junk", ["", "not-a-jwt", "a.b.c", "   "])
def test_rejects_garbage(junk):
    with pytest.raises(InvalidToken):
        verify_mandate_token(junk, SECRET)


def test_signing_secret_reads_the_environment(monkeypatch):
    monkeypatch.setenv("MANDATE_SIGNING_SECRET", "s" * 32)
    assert signing_secret() == "s" * 32


def test_signing_secret_unset_is_a_runtime_error(monkeypatch):
    monkeypatch.delenv("MANDATE_SIGNING_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        signing_secret()


def test_signing_secret_shorter_than_32_chars_is_a_runtime_error(monkeypatch):
    monkeypatch.setenv("MANDATE_SIGNING_SECRET", "s" * 31)
    with pytest.raises(RuntimeError):
        signing_secret()
