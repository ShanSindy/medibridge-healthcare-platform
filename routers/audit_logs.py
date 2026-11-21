"""Audit log retrieval for authorized administrators."""

from fastapi import APIRouter, Depends, Query

from audit_service import get_audit_logs
from auth_dependencies import require_permission

router = APIRouter()


@router.get("/audit-logs")
def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(require_permission("audit:read")),
):
    """Retrieve audit history for authorized administrators."""

    return {
        "limit": limit,
        "offset": offset,
        "items": get_audit_logs(
            limit=limit,
            offset=offset,
        ),
    }
