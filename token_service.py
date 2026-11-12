
import os
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv


load_dotenv()

JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
)

if len(JWT_SECRET_KEY) < 48:
    raise ValueError("JWT_SECRET_KEY must be at least 48 characters")

if JWT_ACCESS_TOKEN_EXPIRE_MINUTES <= 0:
    raise ValueError("JWT token lifetime must be positive")


def create_access_token(user_id: int) -> str:
    """Create a signed JWT access token for a user."""

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(
        minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = {
        "sub": str(user_id),
        "iat": now,
        "nbf": now,
        "exp": expires_at,
        "type": "access",
    }

    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def verify_access_token(token: str) -> dict | None:
    """Validate a JWT access token and return its claims."""

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={
                "require": ["sub", "iat", "nbf", "exp", "type"],
            },
            leeway=5,
        )

        if payload.get("type") != "access":
            return None

        subject = payload.get("sub")

        if not isinstance(subject, str) or not subject.isdecimal():
            return None

        if int(subject) <= 0:
            return None

        return payload

    except jwt.PyJWTError:
        return None
