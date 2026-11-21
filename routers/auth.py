"""Authentication endpoints: login, current user and the administrator check."""

from fastapi import APIRouter, Depends, Form, HTTPException, status

from auth_dependencies import get_current_user, require_roles
from auth_service import authenticate_user
from token_service import JWT_ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token

router = APIRouter()


@router.get("/auth/me", tags=["Authentication"])
def read_current_user(
    current_user: dict = Depends(get_current_user),
):
    """Return the currently authenticated user's information."""
    return current_user


@router.post("/auth/login", tags=["Authentication"])
def login(
    username: str = Form(...),
    password: str = Form(...),
):
    """Authenticate a user and issue a JWT access token."""

    user = authenticate_user(username, password)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(user["id"])

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.get("/auth/admin-check", tags=["Authentication"])
def admin_check(
    current_user: dict = Depends(require_roles("admin")),
):
    """Verify that the authenticated user has administrator access."""

    return {
        "message": "Administrator access granted",
        "username": current_user["username"],
        "role": current_user["role"],
    }
