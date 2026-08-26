from datetime import UTC, datetime, timedelta

import pytest
from jwt import encode

from app.core.config import Settings
from app.services.audit import _safe_metadata_value
from app.services.auth import (
    AuthenticationError,
    AuthService,
    hash_password,
    verify_password,
)


def test_scrypt_password_hash_is_salted_and_verifies() -> None:
    first = hash_password("a-strong-test-password")
    second = hash_password("a-strong-test-password")

    assert first.startswith("scrypt$")
    assert first != second
    assert verify_password("a-strong-test-password", first)
    assert not verify_password("wrong-password", first)
    assert not verify_password("a-strong-test-password", "not-a-password-hash")


def test_access_token_requires_signature_claims_and_type() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret_key="unit-test-secret-that-is-long-enough",
    )
    service = AuthService(settings)
    user_id = "8f3d4c1e-4d1b-4c8e-93e1-7c12adf3b0f1"
    session_id = "b8f3f2f4-9c6a-4f3d-9633-c4cf7dd7d4b8"
    now = datetime.now(UTC)
    token = encode(
        {
            "sub": user_id,
            "sid": session_id,
            "typ": "access",
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )

    principal = service.decode_access_token(token)
    assert str(principal.user_id) == user_id
    assert str(principal.session_id) == session_id

    with pytest.raises(AuthenticationError):
        service.decode_access_token(f"{token}tampered")

    expired = encode(
        {
            "sub": user_id,
            "sid": session_id,
            "typ": "access",
            "iss": settings.jwt_issuer,
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError):
        service.decode_access_token(expired)


def test_audit_metadata_filters_secret_named_fields() -> None:
    safe = _safe_metadata_value(
        {
            "session_id": "session-id",
            "password": "must-not-persist",
            "nested": {"access_token": "must-not-persist"},
            "count": 2,
        }
    )

    assert safe == {"session_id": "session-id", "nested": {}, "count": 2}
