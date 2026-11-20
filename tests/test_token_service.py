from datetime import datetime, timedelta, timezone

import jwt

from token_service import (
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    create_access_token,
    verify_access_token,
)


def test_create_and_verify_access_token():
    token = create_access_token(1)

    payload = verify_access_token(token)

    assert payload is not None
    assert payload["sub"] == "1"
    assert payload["type"] == "access"


def test_reject_invalid_signature():
    token = jwt.encode(
        {
            "sub": "1",
            "iat": datetime.now(timezone.utc),
            "nbf": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
            "type": "access",
        },
        "another-independent-test-signing-key-with-sufficient-length",
        algorithm=JWT_ALGORITHM,
    )

    assert verify_access_token(token) is None


def test_reject_expired_token():
    now = datetime.now(timezone.utc)

    token = jwt.encode(
        {
            "sub": "1",
            "iat": now - timedelta(hours=2),
            "nbf": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "type": "access",
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )

    assert verify_access_token(token) is None


def test_reject_malformed_token():
    assert verify_access_token("not-a-valid-jwt") is None


def test_reject_wrong_token_type():
    now = datetime.now(timezone.utc)

    token = jwt.encode(
        {
            "sub": "1",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=30),
            "type": "refresh",
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )

    assert verify_access_token(token) is None


def test_reject_invalid_user_id():
    now = datetime.now(timezone.utc)

    token = jwt.encode(
        {
            "sub": "invalid-user-id",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=30),
            "type": "access",
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )

    assert verify_access_token(token) is None


def test_reject_missing_required_claim():
    now = datetime.now(timezone.utc)

    token = jwt.encode(
        {
            "sub": "1",
            "iat": now,
            "nbf": now,
            "type": "access",
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )

    assert verify_access_token(token) is None
