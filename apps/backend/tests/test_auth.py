import json
from datetime import datetime, timedelta, timezone

import pytest
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core.auth import AzureADTokenValidator, TokenValidationError


def _generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    return private_pem, public_jwk


def _encode_token(private_pem, issuer, audience, kid="test-kid", azp="client-id", exp_delta=timedelta(minutes=5)):
    now = datetime.now(timezone.utc)
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": "user-123",
        "azp": azp,
        "iat": int(now.timestamp()),
        "exp": int((now + exp_delta).timestamp()),
    }
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": kid})


def test_validator_accepts_valid_token():
    private_pem, public_jwk = _generate_rsa_keypair()
    public_jwk["kid"] = "kid-valid"

    tenant_id = "test-tenant"
    audience = "api://unit-test"
    validator = AzureADTokenValidator(
        tenant_id=tenant_id,
        audience=audience,
        allowed_client_ids=["client-id"],
    )

    validator._cache["jwks"] = {"keys": [public_jwk]}  # type: ignore[attr-defined]

    token = _encode_token(private_pem, validator.issuer, audience, kid="kid-valid")

    claims = validator.validate(token)

    assert claims["aud"] == audience
    assert claims["sub"] == "user-123"


def test_validator_rejects_unapproved_client():
    private_pem, public_jwk = _generate_rsa_keypair()
    public_jwk["kid"] = "kid-client"

    validator = AzureADTokenValidator(
        tenant_id="tenant",
        audience="api://aud",
        allowed_client_ids=["allowed-client"],
    )
    validator._cache["jwks"] = {"keys": [public_jwk]}  # type: ignore[attr-defined]

    token = _encode_token(private_pem, validator.issuer, "api://aud", kid="kid-client", azp="blocked-client")

    with pytest.raises(TokenValidationError) as exc:
        validator.validate(token)

    assert "Client application not allowed" in str(exc.value)


def test_validator_rejects_expired_token():
    private_pem, public_jwk = _generate_rsa_keypair()
    public_jwk["kid"] = "kid-expired"

    validator = AzureADTokenValidator(tenant_id="tenant", audience="api://aud")
    validator._cache["jwks"] = {"keys": [public_jwk]}  # type: ignore[attr-defined]

    token = _encode_token(
        private_pem,
        validator.issuer,
        "api://aud",
        kid="kid-expired",
        exp_delta=timedelta(minutes=-1),
    )

    with pytest.raises(TokenValidationError):
        validator.validate(token)
