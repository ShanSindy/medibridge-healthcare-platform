
"""Centralized role permissions for MediBridge."""

PERMISSIONS = {
    "patients:read": (
        "admin",
        "clinician",
        "scheduler",
    ),
    "patients:write": (
        "admin",
        "clinician",
    ),
    "providers:read": (
        "admin",
        "clinician",
        "scheduler",
        "viewer",
    ),
    "providers:write": (
        "admin",
    ),
    "appointments:read": (
        "admin",
        "clinician",
        "scheduler",
    ),
    "appointments:write": (
        "admin",
        "scheduler",
    ),

    "users:manage": (
        "admin",
    ),
    "audit:read": (
        "admin",
    ),
}



def get_allowed_roles(permission: str) -> tuple[str, ...]:
    """Return roles authorized for a named permission."""

    if permission not in PERMISSIONS:
        raise ValueError(
            f"Unknown permission: {permission}"
        )

    return PERMISSIONS[permission]
