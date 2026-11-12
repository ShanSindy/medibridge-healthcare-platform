
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from token_service import verify_access_token
from user_service import get_user_by_id
from collections.abc import Callable
from permissions import get_allowed_roles

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
) -> dict:
    """Return the active user associated with a valid access token."""

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    claims = verify_access_token(credentials.credentials)

    if claims is None:
        raise unauthorized

    user = get_user_by_id(int(claims["sub"]))

    if user is None or not user["is_active"]:
        raise unauthorized

    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "is_active": user["is_active"],
    }

def require_roles(*allowed_roles: str) -> Callable:
    """Require an authenticated user with an allowed role."""

    valid_roles = {
        "admin",
        "clinician",
        "scheduler",
        "viewer",
    }

    if not allowed_roles:
        raise ValueError("At least one role must be specified")

    if not set(allowed_roles).issubset(valid_roles):
        raise ValueError("Unknown authorization role")

    def role_checker(
        current_user: dict = Depends(get_current_user),
    ) -> dict:
        if current_user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )

        return current_user

    return role_checker

def require_permission(permission: str) -> Callable:
    """Require an authenticated user with a named permission."""

    allowed_roles = get_allowed_roles(permission)

    return require_roles(*allowed_roles)
