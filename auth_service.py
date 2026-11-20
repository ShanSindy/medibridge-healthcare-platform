from argon2 import PasswordHasher

from security import verify_password
from user_service import get_user_by_username

# Used when an account does not exist, so password verification
# still performs an Argon2 operation.
_dummy_hasher = PasswordHasher()
DUMMY_PASSWORD_HASH = _dummy_hasher.hash("MediBridgeDummyPasswordForTimingProtection")


def authenticate_user(
    username: str,
    password: str,
) -> dict | None:
    """Authenticate a user with username and password."""

    user = get_user_by_username(username)

    if user is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None

    password_valid = verify_password(
        password,
        user["password_hash"],
    )

    if not password_valid or not user["is_active"]:
        return None

    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "is_active": user["is_active"],
    }
